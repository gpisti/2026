import os
import time
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

log.info("--- Daily Aggregator Service Indul ---")

# ---------------------------------------------------------------------------
# Konfiguráció
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
RUN_INTERVAL_SECONDS = int(os.getenv("AGGREGATOR_INTERVAL", "600"))   # 10 perc
LOOKBACK_DAYS = 3   # az utolsó 3 napot számolja újra minden körben

# ---------------------------------------------------------------------------
# DB kapcsolat
# ---------------------------------------------------------------------------
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


# ---------------------------------------------------------------------------
# Tábla inicializálás
# ---------------------------------------------------------------------------
def ensure_daily_momentum_table():
    """Létrehozza a daily_momentum táblát, ha még nem létezik, és migrálja az oszlopokat."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS daily_momentum (
                date              DATE    NOT NULL,
                entity_id         INTEGER NOT NULL
                    REFERENCES political_entities(id) ON DELETE CASCADE,
                total_articles    INTEGER DEFAULT 0,
                total_reach       INTEGER DEFAULT 0,
                average_sentiment FLOAT,
                momentum_score    FLOAT,
                baseline_sentiment FLOAT,
                adjusted_momentum  FLOAT,
                calculated_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                PRIMARY KEY (date, entity_id)
            )
        """))
        # Ha a tábla már létezett a régi sémával, adjuk hozzá az új oszlopokat
        conn.execute(text("""
            ALTER TABLE daily_momentum
            ADD COLUMN IF NOT EXISTS baseline_sentiment FLOAT
        """))
        conn.execute(text("""
            ALTER TABLE daily_momentum
            ADD COLUMN IF NOT EXISTS adjusted_momentum FLOAT
        """))
        conn.commit()
    log.info("✓ daily_momentum tábla ellenőrizve/létrehozva (baseline + adjusted_momentum oszlopokkal).")


# ---------------------------------------------------------------------------
# Aggregációs lekérdezés és UPSERT
# ---------------------------------------------------------------------------
def run_aggregation(db):
    """
    Kiszámolja az elmúlt LOOKBACK_DAYS napra az entitásonkénti Média Momentum
    mutatókat Adjusted Sentiment logikával, majd upsert-eli a daily_momentum táblába.

    Adjusted Sentiment = aem.sentiment_score - baseline_sentiment
    ahol baseline_sentiment = az elmúlt 14 napban ugyanazon portál+entitás párnál
    mért átlag-szentiment (a portál politikai beállítottságának kiszűrésére).
    """
    rows = db.execute(text(f"""
        WITH portal_baselines AS (
            -- 14 napos mozgó átlag portál × entitás szinten (a baseline)
            SELECT
                ra_b.portal_id,
                aem_b.entity_id,
                COALESCE(AVG(aem_b.sentiment_score), 0.0) AS baseline_sentiment
            FROM raw_articles ra_b
            INNER JOIN article_entity_mentions aem_b ON aem_b.article_id = ra_b.article_id
            WHERE aem_b.sentiment_score IS NOT NULL
              AND ra_b.scraped_at >= CURRENT_DATE - INTERVAL '14 days'
            GROUP BY ra_b.portal_id, aem_b.entity_id
        )
        SELECT
            DATE(ra.scraped_at)                              AS agg_date,
            aem.entity_id,
            COUNT(DISTINCT ra.article_id)                    AS total_articles,
            SUM(
                COALESCE(am.fb_interactions, 0)
                + COALESCE(am.reddit_upvotes, 0)
                + COALESCE(am.reddit_comments, 0)
            )                                                AS total_reach,
            AVG(aem.sentiment_score)                         AS average_sentiment,
            -- Hagyományos (nyers) momentum
            SUM(
                (
                    COALESCE(am.fb_interactions, 0)
                    + COALESCE(am.reddit_upvotes, 0)
                    + COALESCE(am.reddit_comments, 0)
                ) * aem.sentiment_score
            )                                                AS momentum_score,
            -- A baseline: súlyozott átlag a portálok baseline értékeiből
            AVG(COALESCE(pb.baseline_sentiment, 0.0))        AS baseline_sentiment,
            -- Adjusted momentum: reach × (score - baseline)
            SUM(
                (
                    COALESCE(am.fb_interactions, 0)
                    + COALESCE(am.reddit_upvotes, 0)
                    + COALESCE(am.reddit_comments, 0)
                ) * (
                    aem.sentiment_score
                    - COALESCE(pb.baseline_sentiment, 0.0)
                )
            )                                                AS adjusted_momentum
        FROM raw_articles ra
        INNER JOIN article_entity_mentions aem ON aem.article_id = ra.article_id
        INNER JOIN article_metrics am          ON am.article_id  = ra.article_id
        LEFT JOIN portal_baselines pb
               ON pb.portal_id = ra.portal_id
              AND pb.entity_id = aem.entity_id
        WHERE aem.sentiment_score IS NOT NULL
          AND DATE(ra.scraped_at) >= CURRENT_DATE - INTERVAL '{LOOKBACK_DAYS} days'
        GROUP BY DATE(ra.scraped_at), aem.entity_id
        ORDER BY agg_date DESC, aem.entity_id
    """)).fetchall()

    if not rows:
        log.info("Nincs aggregálható adat az elmúlt %d napban.", LOOKBACK_DAYS)
        return 0

    upserted = 0
    for row in rows:
        db.execute(text("""
            INSERT INTO daily_momentum
                (date, entity_id, total_articles, total_reach, average_sentiment,
                 momentum_score, baseline_sentiment, adjusted_momentum, calculated_at)
            VALUES
                (:date, :entity_id, :total_articles, :total_reach, :average_sentiment,
                 :momentum_score, :baseline_sentiment, :adjusted_momentum, NOW())
            ON CONFLICT (date, entity_id) DO UPDATE SET
                total_articles     = EXCLUDED.total_articles,
                total_reach        = EXCLUDED.total_reach,
                average_sentiment  = EXCLUDED.average_sentiment,
                momentum_score     = EXCLUDED.momentum_score,
                baseline_sentiment = EXCLUDED.baseline_sentiment,
                adjusted_momentum  = EXCLUDED.adjusted_momentum,
                calculated_at      = NOW()
        """), {
            "date":               row.agg_date,
            "entity_id":          row.entity_id,
            "total_articles":     row.total_articles,
            "total_reach":        int(row.total_reach or 0),
            "average_sentiment":  float(row.average_sentiment)  if row.average_sentiment  is not None else None,
            "momentum_score":     float(row.momentum_score)     if row.momentum_score     is not None else None,
            "baseline_sentiment": float(row.baseline_sentiment) if row.baseline_sentiment is not None else 0.0,
            "adjusted_momentum":  float(row.adjusted_momentum)  if row.adjusted_momentum  is not None else None,
        })
        upserted += 1

    db.commit()
    log.info(
        "✓ Aggregáció kész: %d sor upsert-elve "
        "(utolsó %d nap, adjusted sentiment logikával).",
        upserted, LOOKBACK_DAYS
    )
    return upserted


# ---------------------------------------------------------------------------
# Startup + fő loop
# ---------------------------------------------------------------------------
ensure_daily_momentum_table()

log.info("Daily Aggregator fut — %ds ciklusidővel (%d napos visszatekintéssel).",
         RUN_INTERVAL_SECONDS, LOOKBACK_DAYS)

while True:
    try:
        db = SessionLocal()
        try:
            run_aggregation(db)
        except Exception as e:
            log.error("Aggregációs hiba: %s", e)
            db.rollback()
        finally:
            db.close()
    except Exception as e:
        log.error("Fő loop hiba: %s", e)

    time.sleep(RUN_INTERVAL_SECONDS)