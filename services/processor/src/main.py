import re
import time
import logging
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from shared.connections import get_redis_connection, get_db_session
from shared.models.db_models import Raw_Articles, Processed_Articles

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

log.info("--- Processor Service Indul (Regex Word-Boundary alapú kulcsszó-illesztés) ---")

# ---------------------------------------------------------------------------
# Kulcsszó-cache struktúrája:
# [
#   {
#     "entity_id": 1,
#     "entity_name": "Fidesz-Kormány",
#     "primary_keyword": "fidesz",
#     "pattern": re.compile(r"\b(fidesz|fidesszel|fidesznek|...)\b", re.IGNORECASE)
#   },
#   ...
# ]
# ---------------------------------------------------------------------------
KEYWORD_CACHE: list[dict] = []


# ---------------------------------------------------------------------------
# DB migráció: új oszlopok hozzáadása
# ---------------------------------------------------------------------------
def run_migrations(db):
    """Eltávolítja a redundans primary_keyword oszlopot (ha még létezne), és hozzáadja az új oszlopokat."""
    # A régi, felesleges oszlop eltávolítása
    db.execute(text("""
        ALTER TABLE keywords DROP COLUMN IF EXISTS primary_keyword
    """))
    # aliases JSONB oszlop — ez tartja a ragozott alakokat / szinonímákat
    db.execute(text("""
        ALTER TABLE keywords
        ADD COLUMN IF NOT EXISTS aliases JSONB DEFAULT '[]'::jsonb
    """))
    # article_entity_mentions: matched_keyword
    db.execute(text("""
        ALTER TABLE article_entity_mentions
        ADD COLUMN IF NOT EXISTS matched_keyword VARCHAR(255)
    """))
    db.commit()
    log.info("✓ DB migráció kész (primary_keyword eldobva, aliases + matched_keyword oszlopok).")


def ensure_mentions_table(db):
    """Létrehozza az article_entity_mentions kapcsolótáblát, ha még nem létezik."""
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS article_entity_mentions (
            id            SERIAL PRIMARY KEY,
            article_id    UUID NOT NULL REFERENCES raw_articles(article_id) ON DELETE CASCADE,
            entity_id     INTEGER NOT NULL REFERENCES political_entities(id) ON DELETE CASCADE,
            matched_keyword VARCHAR(255),
            found_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            UNIQUE (article_id, entity_id)
        )
    """))
    db.commit()
    log.info("✓ article_entity_mentions tábla ellenőrizve/létrehozva.")


# ---------------------------------------------------------------------------
# Kulcsszavak betöltése DB-ből — Regex pattern-ek előfordítása
# ---------------------------------------------------------------------------
def load_keywords_from_db(db) -> list[dict]:
    """
    Betölti a keywords táblát és összeépíti az entitásonkénti Regex pattern-eket.
    Minden entitáshoz egy compiled regex jön létre:
      \b(primary_keyword|alias1|alias2|...)\b  (IGNORECASE)

    A szóhatár (\b) kiszűri a fals pozitív substring találatokat
    (pl. "fideszes" nem illik rá a "fidesz" pattern-re, ha nem adjuk hozzá aliasként).
    """
    try:
        rows = db.execute(text("""
            SELECT
                k.entity_id,
                pe.name     AS entity_name,
                k.keyword
            FROM keywords k
            JOIN political_entities pe ON pe.id = k.entity_id
        """)).fetchall()
    except Exception as e:
        log.error(f"Kulcsszavak betöltési hiba: {e}")
        return []

    # entity_id → {entity_name, primary_keyword, terms[]} csoportosítás
    entity_terms: dict[int, dict] = {}
    for row in rows:
        eid = row.entity_id
        if eid not in entity_terms:
            entity_terms[eid] = {
                "entity_name": row.entity_name,
                "primary_keyword": row.keyword,   # az első talált kulcsszó lesz a primary
                "terms": set(),
            }
        # Az eredeti keyword mindig benne van
        entity_terms[eid]["terms"].add(row.keyword.lower())
        # Aliasok hozzáadása (JSONB lista) — a kulcsszó sorból
        if hasattr(row, 'aliases') and row.aliases:
            for alias in (row.aliases if isinstance(row.aliases, list) else []):
                if alias:
                    entity_terms[eid]["terms"].add(alias.lower())

    cache = []
    for eid, data in entity_terms.items():
        # Hosszabb kifejezések előre (greedy matching elkerülésére)
        sorted_terms = sorted(data["terms"], key=len, reverse=True)
        # Regex speciális karakterek escapelése
        escaped = [re.escape(t) for t in sorted_terms]
        pattern_str = r"\b(" + "|".join(escaped) + r")\b"
        try:
            compiled = re.compile(pattern_str, re.IGNORECASE)
        except re.error as e:
            log.warning(f"Regex hiba ({data['entity_name']}): {e} — kihagyva.")
            continue

        cache.append({
            "entity_id":      eid,
            "entity_name":    data["entity_name"],
            "primary_keyword": data["primary_keyword"],
            "pattern":        compiled,
        })

    log.info(
        f"✓ {len(cache)} entitás betöltve, "
        f"{sum(len(e['terms']) for e in entity_terms.values())} kifejezéssel "
        f"(Regex word-boundary mód)."
    )
    return cache


# ---------------------------------------------------------------------------
# Szöveg-illesztés Regex alapon
# ---------------------------------------------------------------------------
def find_matching_entities(text_content: str, keyword_cache: list[dict]) -> dict:
    """
    Visszaadja az egyedi entity_id → {entity_name, matched_keyword} mapping-et.
    A keresés \b szóhatáros Regex-szel történik (IGNORECASE).
    """
    found: dict = {}
    for item in keyword_cache:
        match = item["pattern"].search(text_content)
        if match:
            eid = item["entity_id"]
            if eid not in found:
                found[eid] = {
                    "entity_name":    item["entity_name"],
                    "matched_keyword": match.group(0),   # a ténylegesen illeszkedett szó
                }
    return found


# ---------------------------------------------------------------------------
# Fő feldolgozó pipeline
# ---------------------------------------------------------------------------
def process_article(db, article_id: str, keyword_cache: list[dict]):
    """Betölti a cikket, regex szűrést futtat, majd menti a találatokat."""
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

    matched_entities = find_matching_entities(text_content, keyword_cache)

    if not matched_entities:
        log.info(f"Skipped (nem releváns): {article.title[:70]}")
        article.status = 'skipped'
        db.commit()
        return

    # Explícit találat log — portfolió neve + entitás + matched keyword + URL
    portal_name = getattr(article.portal, 'name', 'ismeretlen') if hasattr(article, 'portal') and article.portal else 'ismeretlen'
    for entity_id, match_data in matched_entities.items():
        log.info(
            f"[{portal_name}] Tállat: {match_data['entity_name']} "
            f"-> matched_keyword: '{match_data['matched_keyword']}' "
            f"(URL: {article.url})"
        )

    # --- 1. Processed_Articles alap rekord mentése ---
    try:
        db.add(Processed_Articles(
            article_id=article.article_id,
            word_count=len(text_content.split()),
        ))
        article.status = 'processed'
        db.flush()
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
    for entity_id, match_data in matched_entities.items():
        try:
            db.execute(text("""
                INSERT INTO article_entity_mentions (article_id, entity_id, matched_keyword)
                VALUES (:article_id, :entity_id, :matched_keyword)
                ON CONFLICT (article_id, entity_id) DO UPDATE SET
                    matched_keyword = EXCLUDED.matched_keyword
            """), {
                "article_id":      str(article.article_id),
                "entity_id":       entity_id,
                "matched_keyword": match_data["matched_keyword"],
            })
            saved_entity_ids.append(entity_id)
            log.info(
                f"  → '{match_data['entity_name']}' "
                f"(matched: '{match_data['matched_keyword']}')"
            )
        except Exception as e:
            log.error(f"  ✗ Entitás mentési hiba (entity_id={entity_id}): {e}")

    db.commit()
    log.info(
        f"Elmentve | {len(saved_entity_ids)} entitás | "
        f"entity_ids: {saved_entity_ids} | article_id: {article_id}"
    )


# ---------------------------------------------------------------------------
# Startup: séma + migráció + kulcsszavak betöltése
# ---------------------------------------------------------------------------
startup_db = get_db_session("Processor-startup")
if startup_db:
    try:
        ensure_mentions_table(startup_db)
        run_migrations(startup_db)
        KEYWORD_CACHE = load_keywords_from_db(startup_db)
    finally:
        startup_db.close()
else:
    log.error("Nem sikerült csatlakozni a DB-hez induláskor. Leállás.")
    exit(1)

if not KEYWORD_CACHE:
    log.error("A kulcsszó-cache üres — nincs primary_keyword az adatbázisban. Leállás.")
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