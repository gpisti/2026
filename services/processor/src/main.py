import time
import logging
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from shared.connections import get_redis_connection, get_db_session
from shared.models.db_models import Raw_Articles, Processed_Articles

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

log.info("--- Processor Service Indul (DB-alapú dinamikus kulcsszavak) ---")

# ---------------------------------------------------------------------------
# Kulcsszó-cache: DB-ből töltjük be egyszer induláskor
# Struktúra: [ { "keyword": "orbán viktor", "entity_id": <uuid>, "entity_name": "Orbán Viktor" }, ... ]
# ---------------------------------------------------------------------------
KEYWORD_CACHE: list[dict] = []


def ensure_mentions_table(db):
    """Létrehozza az article_entity_mentions kapcsolótáblát, ha még nem létezik."""
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS article_entity_mentions (
            id          SERIAL PRIMARY KEY,
            article_id  UUID NOT NULL REFERENCES raw_articles(article_id) ON DELETE CASCADE,
            entity_id   INTEGER NOT NULL REFERENCES political_entities(id) ON DELETE CASCADE,
            found_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            UNIQUE (article_id, entity_id)
        )
    """))
    db.commit()
    log.info("✓ article_entity_mentions tábla ellenőrizve/létrehozva.")


def load_keywords_from_db(db) -> list[dict]:
    """Betölti a keywords táblát és a hozzá tartozó political_entities neveket."""
    try:
        rows = db.execute(text("""
            SELECT k.keyword, k.entity_id, pe.name AS entity_name
            FROM keywords k
            JOIN political_entities pe ON pe.id = k.entity_id
        """)).fetchall()

        cache = [
            {"keyword": row.keyword.lower(), "entity_id": row.entity_id, "entity_name": row.entity_name}
            for row in rows
        ]
        log.info(f"✓ {len(cache)} kulcsszó betöltve {len({r['entity_id'] for r in cache})} entitáshoz.")
        return cache
    except Exception as e:
        log.error(f"Kritikus hiba a kulcsszavak betöltésekor: {e}")
        return []


# ---------------------------------------------------------------------------
# Szűrőfüggvény — gyors string-matching (ThinkPad-barát, spaCy nélkül)
# ---------------------------------------------------------------------------
def find_matching_entities(text_lower: str, keyword_cache: list[dict]) -> dict:
    """
    Visszaadja az egyedi entity_id → entity_name mapping-et azokhoz az
    entitásokhoz, amelyek kulcsszava szerepel a szövegben.
    """
    found: dict = {}
    for item in keyword_cache:
        if item["keyword"] in text_lower:
            entity_id = item["entity_id"]
            if entity_id not in found:
                found[entity_id] = item["entity_name"]
    return found


# ---------------------------------------------------------------------------
# Fő feldolgozó pipeline
# ---------------------------------------------------------------------------
def process_article(db, article_id: str, keyword_cache: list[dict]):
    """Betölti a cikket, kulcsszó-szűrést futtat, majd menti a találatokat."""
    article = db.query(Raw_Articles).filter(Raw_Articles.article_id == article_id).first()
    if not article:
        log.error(f"Cikk nem található: {article_id}")
        return

    text_content = article.raw_article_text
    if not text_content:
        log.info(f"Skipped (nincs szöveg): {article_id}")
        article.status = 'skipped'
        db.commit()
        return

    # --- Gyors előszűrés ---
    text_lower = text_content.lower()
    matched_entities = find_matching_entities(text_lower, keyword_cache)

    if not matched_entities:
        log.info(f"Skipped (nem releváns): {article.title[:70]}")
        article.status = 'skipped'
        db.commit()
        return

    # --- 1. Processed_Articles alap rekord mentése ---
    try:
        db.add(Processed_Articles(
            article_id=article.article_id,
            word_count=len(text_content.split()),
        ))
        article.status = 'processed'
        db.flush()  # ID generáláshoz, de még nem commit
    except IntegrityError:
        db.rollback()
        log.warning(f"Már feldolgozva (IntegrityError): {article_id}")
        return
    except Exception as e:
        db.rollback()
        log.error(f"Processed_Articles mentési hiba ({article_id}): {e}")
        return

    # --- 2. Entitás-hivatkozások mentése a kapcsolótáblába ---
    saved_entity_ids = []
    for entity_id, entity_name in matched_entities.items():
        try:
            db.execute(text("""
                INSERT INTO article_entity_mentions (article_id, entity_id)
                VALUES (:article_id, :entity_id)
                ON CONFLICT (article_id, entity_id) DO NOTHING
            """), {"article_id": str(article.article_id), "entity_id": entity_id})
            saved_entity_ids.append(entity_id)
            log.info(f"  → '{entity_name}' (entity_id: {entity_id})")
        except Exception as e:
            log.error(f"  ✗ Entitás mentési hiba (entity_id={entity_id}): {e}")

    db.commit()
    log.info(
        f"Elmentve | {len(saved_entity_ids)} entitás | "
        f"entity_ids: {saved_entity_ids} | article_id: {article_id}"
    )


# ---------------------------------------------------------------------------
# Startup: séma + kulcsszavak betöltése DB-ből
# ---------------------------------------------------------------------------
startup_db = get_db_session("Processor-startup")
if startup_db:
    try:
        ensure_mentions_table(startup_db)
        KEYWORD_CACHE = load_keywords_from_db(startup_db)
    finally:
        startup_db.close()
else:
    log.error("Nem sikerült csatlakozni a DB-hez induláskor. Leállás.")
    exit(1)

if not KEYWORD_CACHE:
    log.error("A kulcsszó-cache üres — a keywords tábla üres vagy nem elérhető. Leállás.")
    exit(1)


# ---------------------------------------------------------------------------
# Fő Redis event loop
# ---------------------------------------------------------------------------
r_conn = get_redis_connection("Processor")
log.info("Processor várja a feladatokat a 'process_queue'-ban...")

while True:
    try:
        task = r_conn.brpop("process_queue", 0)
        if not task:
            continue

        article_id = task[1].decode("utf-8") if isinstance(task[1], bytes) else task[1]
        log.info(f"*** FELADAT MEGKAPVA: {article_id} ***")

        db = get_db_session("Processor")
        if not db:
            log.error("DB kapcsolat sikertelen, visszarakjuk a sorba...")
            r_conn.lpush("process_queue", article_id)
            time.sleep(10)
            continue

        try:
            process_article(db, article_id, KEYWORD_CACHE)
        except Exception as e:
            log.error(f"Váratlan hiba ({article_id}): {e}")
            db.rollback()
        finally:
            db.close()

    except Exception as e:
        log.error(f"Redis loop hiba: {e}")
        time.sleep(5)