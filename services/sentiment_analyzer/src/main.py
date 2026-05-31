import os
import re
import time
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from transformers import pipeline

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

log.info("--- Sentiment Analyzer Service Indul (Aspect-Based) ---")

# ---------------------------------------------------------------------------
# Konfiguráció
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
POLL_INTERVAL_SECONDS = int(os.getenv("SENTIMENT_POLL_INTERVAL", "30"))
CONTEXT_WINDOW_WORDS = 50   # entitás körüli ablak: 50 szó előtte + 50 utána = ~100
FALLBACK_WORDS = 100        # ha az entitást nem találja, az első 100 szót veszi
BATCH_SIZE = 10

ENTITY_KEYWORDS_CACHE: dict[int, dict] = {}

# ---------------------------------------------------------------------------
# DB kapcsolat
# ---------------------------------------------------------------------------
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def ensure_sentiment_column():
    """
    Hozzáadja a sentiment_score oszlopot az article_entity_mentions táblához,
    ha még nincs — ez az entitás-szintű (ABSA) megközelítés oszlopa.
    """
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE article_entity_mentions
            ADD COLUMN IF NOT EXISTS sentiment_score FLOAT DEFAULT NULL
        """))
        conn.commit()
    log.info("✓ sentiment_score oszlop ellenőrizve/hozzáadva (article_entity_mentions).")


# ---------------------------------------------------------------------------
# HuggingFace modell betöltése (egyszer, induláskor)
# ---------------------------------------------------------------------------
log.info("Modell betöltése: NYTK/sentiment-ohb3-xlm-roberta-hungarian ...")
sentiment_pipeline = pipeline(
    "text-classification",
    model="NYTK/sentiment-ohb3-xlm-roberta-hungarian",
    top_k=None   # minden osztály valószínűségét visszaadja
)
log.info("✓ Modell betöltve.")


# ---------------------------------------------------------------------------
# Entitás kulcsszó cache betöltése DB-ből
# ---------------------------------------------------------------------------
def load_entity_keywords_cache(db) -> dict[int, dict]:
    rows = db.execute(text("""
        SELECT k.entity_id, pe.name AS entity_name, k.keyword, k.aliases
        FROM keywords k
        JOIN political_entities pe ON pe.id = k.entity_id
    """)).fetchall()

    cache: dict[int, dict] = {}
    for row in rows:
        eid = row.entity_id
        if eid not in cache:
            cache[eid] = {"name": row.entity_name, "keywords": set()}
        cache[eid]["keywords"].add(row.keyword.lower())
        aliases = row.aliases
        if aliases and isinstance(aliases, list):
            for alias in aliases:
                if alias:
                    cache[eid]["keywords"].add(alias.lower())

    log.info(
        "✓ Entitás kulcsszó cache betöltve: %d entitás, %d kifejezés.",
        len(cache),
        sum(len(v["keywords"]) for v in cache.values()),
    )
    return cache


# ---------------------------------------------------------------------------
# Maszkolás: más entitások kulcsszavainak cseréje [MÁSIK_SZEREPLŐ]-re
# ---------------------------------------------------------------------------
def mask_other_entities(text: str, current_entity_id: int, cache: dict[int, dict]) -> str:
    other_keywords: set[str] = set()
    for eid, data in cache.items():
        if eid != current_entity_id:
            other_keywords.update(data["keywords"])

    if not other_keywords:
        return text

    sorted_terms = sorted(other_keywords, key=len, reverse=True)
    escaped = [re.escape(t) for t in sorted_terms]
    pattern = re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)
    return pattern.sub("[MÁSIK_SZEREPLŐ]", text)


# ---------------------------------------------------------------------------
# Kontextusablakok kinyerése re.finditer-rel (az entitás ÖSSZES előfordulása)
# ---------------------------------------------------------------------------
def find_all_context_windows(
    full_text: str,
    entity_keywords: set[str],
    window: int = CONTEXT_WINDOW_WORDS,
    fallback: int = FALLBACK_WORDS,
) -> list[str]:
    if not full_text or not entity_keywords:
        return []

    words = full_text.split()

    sorted_terms = sorted(entity_keywords, key=len, reverse=True)
    escaped = [re.escape(t) for t in sorted_terms]
    pattern = re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)

    seen_word_positions: set[int] = set()
    windows: list[str] = []

    for match in pattern.finditer(full_text):
        word_pos = len(full_text[: match.start()].split())
        if word_pos in seen_word_positions:
            continue
        seen_word_positions.add(word_pos)

        start = max(0, word_pos - window)
        end = min(len(words), word_pos + window)
        context = " ".join(words[start:end])
        if context.strip():
            windows.append(context)

    if not windows:
        windows.append(" ".join(words[:fallback]))

    return windows


# ---------------------------------------------------------------------------
# Szentiment számítás
# ---------------------------------------------------------------------------
def compute_sentiment_score(context_text: str) -> float:
    """
    Futtatja a modellt a kivágott szövegre.
    score = positive_probability (LABEL_2) - negative_probability (LABEL_0)
    Visszaad egy [-1.0, +1.0] közötti float-ot.
    """
    if not context_text.strip():
        return 0.0

    results = sentiment_pipeline(context_text, truncation=True, max_length=512)[0]
    probs = {item["label"].lower(): item["score"] for item in results}

    # NYTK/sentiment-ohb3-xlm-roberta-hungarian label mapping:
    # LABEL_0 = negatív, LABEL_1 = semleges, LABEL_2 = pozitív
    positive = probs.get("label_2", 0.0)
    negative = probs.get("label_0", 0.0)
    return round(positive - negative, 4)


# ---------------------------------------------------------------------------
# Lekérdezési logika — entitás-szintű pending sorok
# ---------------------------------------------------------------------------
def fetch_pending_mentions(db) -> list:
    """
    Visszaadja azokat az (article_id, entity_id, entity_name, raw_article_text, url)
    kombinációkat az article_entity_mentions-ből, ahol a sentiment_score még NULL.
    """
    rows = db.execute(text("""
        SELECT
            aem.id          AS mention_id,
            aem.article_id,
            aem.entity_id,
            pe.name         AS entity_name,
            ra.raw_article_text,
            ra.url
        FROM article_entity_mentions aem
        INNER JOIN raw_articles ra ON ra.article_id = aem.article_id
        INNER JOIN political_entities pe ON pe.id = aem.entity_id
        WHERE aem.sentiment_score IS NULL
          AND ra.raw_article_text IS NOT NULL
          AND ra.raw_article_text != ''
        LIMIT :limit
    """), {"limit": BATCH_SIZE}).fetchall()
    return rows


def save_mention_sentiment(db, mention_id: int, score: float):
    """UPDATE-eli a sentiment_score-t az adott article_entity_mentions sorban."""
    db.execute(text("""
        UPDATE article_entity_mentions
        SET sentiment_score = :score
        WHERE id = :mention_id
    """), {"score": score, "mention_id": mention_id})
    db.commit()


# ---------------------------------------------------------------------------
# Startup + fő loop
# ---------------------------------------------------------------------------
ensure_sentiment_column()

startup_db = SessionLocal()
try:
    ENTITY_KEYWORDS_CACHE = load_entity_keywords_cache(startup_db)
finally:
    startup_db.close()

if not ENTITY_KEYWORDS_CACHE:
    log.error("Az entitás kulcsszó cache üres — nincsenek entitások a DB-ben. Leállás.")
    exit(1)

log.info(f"Sentiment Analyzer fut — {POLL_INTERVAL_SECONDS}s ciklusidővel. (Aspect-Based mód)")

while True:
    try:
        db = SessionLocal()
        try:
            pending = fetch_pending_mentions(db)
            if not pending:
                log.debug("Nincs feldolgozandó entitás-hivatkozás, várakozás...")
            else:
                log.info(f"{len(pending)} entitás-hivatkozás vár szentiment-elemzésre.")
                for row in pending:
                    try:
                        entity_data = ENTITY_KEYWORDS_CACHE.get(row.entity_id)
                        entity_keywords = entity_data["keywords"] if entity_data else set()

                        windows = find_all_context_windows(
                            full_text=row.raw_article_text,
                            entity_keywords=entity_keywords,
                        )

                        scores = []
                        for window_text in windows:
                            masked = mask_other_entities(
                                window_text, row.entity_id, ENTITY_KEYWORDS_CACHE
                            )
                            s = compute_sentiment_score(masked)
                            scores.append(s)

                        avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0
                        save_mention_sentiment(db, row.mention_id, avg_score)

                        log.info(
                            f"Szentiment kiszámolva: [{row.entity_name}] | "
                            f"[{row.url}] -> {len(windows)} ablak, "
                            f"score={avg_score}"
                        )
                    except Exception as e:
                        log.error(
                            f"Hiba a feldolgozáskor "
                            f"(article={row.article_id}, entity={row.entity_name}): {e}"
                        )
                        db.rollback()
        finally:
            db.close()

    except Exception as e:
        log.error(f"Fő loop hiba: {e}")

    time.sleep(POLL_INTERVAL_SECONDS)
