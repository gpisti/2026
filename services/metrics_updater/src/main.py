import os
import time
import logging
import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

log.info("--- Metrics Updater Service Indul ---")

# ---------------------------------------------------------------------------
# Konfiguráció
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
SHAREDCOUNT_API_KEY = os.getenv("SHAREDCOUNT_API_KEY")
POLL_INTERVAL_SECONDS = int(os.getenv("METRICS_POLL_INTERVAL", "30"))

REDDIT_HEADERS = {
    "User-Agent": "2026-MetricsBot/1.0 (research project; contact: admin@mentor.local)"
}

# ---------------------------------------------------------------------------
# DB kapcsolat
# ---------------------------------------------------------------------------
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def ensure_metrics_table():
    """Létrehozza az article_metrics táblát, ha még nem létezik."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS article_metrics (
                article_id    UUID PRIMARY KEY REFERENCES raw_articles(article_id) ON DELETE CASCADE,
                fb_interactions  INTEGER DEFAULT 0,
                reddit_upvotes   INTEGER DEFAULT 0,
                reddit_comments  INTEGER DEFAULT 0,
                last_updated  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        conn.commit()
    log.info("✓ article_metrics tábla ellenőrizve/létrehozva.")


# ---------------------------------------------------------------------------
# API hívások
# ---------------------------------------------------------------------------
def fetch_sharedcount(url: str) -> int:
    """SharedCount API — Facebook interakciók lekérése."""
    if not SHAREDCOUNT_API_KEY:
        log.warning("SHAREDCOUNT_API_KEY nincs beállítva, FB adat kihagyva.")
        return 0
    try:
        resp = requests.get(
            "https://api.sharedcount.com/v1.0/",
            params={"url": url, "apikey": SHAREDCOUNT_API_KEY},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        fb = data.get("Facebook", {})
        return (
            fb.get("total_count", 0) or
            fb.get("share_count", 0) +
            fb.get("comment_count", 0) +
            fb.get("reaction_count", 0)
        )
    except Exception as e:
        log.warning(f"SharedCount hiba ({url}): {e}")
        return 0


def fetch_reddit(url: str) -> tuple[int, int]:
    """Reddit search JSON — upvote és kommentszám lekérése."""
    try:
        resp = requests.get(
            f"https://www.reddit.com/search.json?q=url:{url}",
            headers=REDDIT_HEADERS,
            timeout=10
        )
        resp.raise_for_status()
        children = resp.json().get("data", {}).get("children", [])
        if not children:
            return 0, 0
        # Legjobb találat adatai
        best = max(children, key=lambda c: c["data"].get("score", 0))["data"]
        return best.get("score", 0), best.get("num_comments", 0)
    except Exception as e:
        log.warning(f"Reddit hiba ({url}): {e}")
        return 0, 0


# ---------------------------------------------------------------------------
# Fő logika
# ---------------------------------------------------------------------------
def fetch_pending_articles(db) -> list:
    """
    Visszaadja azokat a cikkeket, amelyek már szerepelnek az
    article_entity_mentions táblában, de még NINCSENEK az article_metrics-ben.
    """
    rows = db.execute(text("""
        SELECT DISTINCT ra.article_id, ra.url
        FROM raw_articles ra
        INNER JOIN article_entity_mentions aem ON aem.article_id = ra.article_id
        LEFT JOIN article_metrics am ON am.article_id = ra.article_id
        WHERE am.article_id IS NULL
          AND ra.url IS NOT NULL
          AND ra.scraped_at <= NOW() - INTERVAL '3 hours'
        LIMIT 20
    """)).fetchall()
    return rows


def update_metrics_for_article(db, article_id: str, url: str):
    """Lekéri a metrikákat és elmenti az article_metrics táblába."""
    fb_interactions = fetch_sharedcount(url)
    reddit_upvotes, reddit_comments = fetch_reddit(url)

    db.execute(text("""
        INSERT INTO article_metrics (article_id, fb_interactions, reddit_upvotes, reddit_comments, last_updated)
        VALUES (:article_id, :fb, :ru, :rc, NOW())
        ON CONFLICT (article_id) DO UPDATE SET
            fb_interactions = EXCLUDED.fb_interactions,
            reddit_upvotes  = EXCLUDED.reddit_upvotes,
            reddit_comments = EXCLUDED.reddit_comments,
            last_updated    = NOW()
    """), {
        "article_id": str(article_id),
        "fb": fb_interactions,
        "ru": reddit_upvotes,
        "rc": reddit_comments,
    })
    db.commit()
    log.info(f"Metrics frissítve a [{url}] cikkhez — FB: {fb_interactions}, Reddit upvotes: {reddit_upvotes}, Reddit comments: {reddit_comments}")


# ---------------------------------------------------------------------------
# Startup + fő loop
# ---------------------------------------------------------------------------
ensure_metrics_table()

log.info(f"Metrics Updater fut — {POLL_INTERVAL_SECONDS}s ciklusidővel.")

while True:
    try:
        db = SessionLocal()
        try:
            pending = fetch_pending_articles(db)
            if not pending:
                log.debug("Nincs feldolgozandó cikk, várakozás...")
            else:
                log.info(f"{len(pending)} cikk vár metrika-frissítésre.")
                for row in pending:
                    try:
                        update_metrics_for_article(db, row.article_id, row.url)
                        time.sleep(1)  # API rate-limit védelem
                    except Exception as e:
                        log.error(f"Hiba a cikk feldolgozásakor ({row.url}): {e}")
                        db.rollback()
        finally:
            db.close()

    except Exception as e:
        log.error(f"Fő loop hiba: {e}")

    time.sleep(POLL_INTERVAL_SECONDS)
