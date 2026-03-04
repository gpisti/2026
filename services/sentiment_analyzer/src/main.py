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
# Szövegablak (Context Window) kivágása entitás körül
# ---------------------------------------------------------------------------
def extract_context_window(full_text: str, entity_name: str,
                           window: int = CONTEXT_WINDOW_WORDS,
                           fallback: int = FALLBACK_WORDS) -> str:
    """
    Megkeresi az entity_name első előfordulását a full_text-ben (case-insensitive),
    és kivágja a körülötte lévő +/- `window` szavas ablakot.

    Ha nem találja, az első `fallback` szót adja vissza.
    """
    if not full_text or not entity_name:
        return " ".join(full_text.split()[:fallback]) if full_text else ""

    words = full_text.split()
    entity_lower = entity_name.lower()
    text_lower = full_text.lower()

    # Az entitás első karakterpozíciója a szövegben
    char_pos = text_lower.find(entity_lower)
    if char_pos == -1:
        # Fallback: első `fallback` szó
        log.debug(f"Entitás nem található a szövegben: '{entity_name}' — fallback használata.")
        return " ".join(words[:fallback])

    # Karakterpozícióból szópozíció kiszámítása
    # (megszámoljuk a szóközöket a char_pos előtt)
    prefix = full_text[:char_pos]
    word_pos = len(prefix.split())

    start = max(0, word_pos - window)
    end = min(len(words), word_pos + window)

    return " ".join(words[start:end])


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
                        context = extract_context_window(
                            full_text=row.raw_article_text,
                            entity_name=row.entity_name
                        )
                        score = compute_sentiment_score(context)
                        save_mention_sentiment(db, row.mention_id, score)
                        log.info(
                            f"Szentiment kiszámolva: [{row.entity_name}] | "
                            f"[{row.url}] -> Score: {score}"
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
