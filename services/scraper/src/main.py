import time
import random
import logging
import feedparser
import trafilatura
import requests
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from shared.connections import get_redis_connection, get_db_session
from shared.models.db_models import Portals, Raw_Articles

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

log.info("--- Scraper Service Indul ---")

# ---------------------------------------------------------------------------
# Fix RSS forráslista — ez az egyetlen igazság
# ---------------------------------------------------------------------------
RSS_SOURCES = [
    {"name": "Telex",          "rss_feed_url": "https://telex.hu/rss",                                                    "type": "fuggetlen"},
    {"name": "24.hu",          "rss_feed_url": "https://24.hu/feed/",                                                     "type": "fuggetlen"},
    {"name": "Index",          "rss_feed_url": "https://index.hu/24ora/rss/",                                             "type": "kormanykozeli"},
    {"name": "HVG",            "rss_feed_url": "https://hvg.hu/rss",                                                      "type": "ellenzeki_kritikus"},
    {"name": "444",            "rss_feed_url": "https://444.hu/feed",                                                     "type": "fuggetlen"},
    {"name": "Origo",          "rss_feed_url": "https://www.origo.hu/publicapi/hu/rss/origo/articles",                    "type": "kormanykozeli"},
    {"name": "Magyar Nemzet",  "rss_feed_url": "https://magyarnemzet.hu/publicapi/hu/rss/magyar_nemzet/articles",         "type": "kormanykozeli"},
    {"name": "Mandiner",       "rss_feed_url": "https://mandiner.hu/publicapi/hu/rss/mandiner/articles",                  "type": "kormanykozeli"},
    {"name": "Portfolio",      "rss_feed_url": "https://www.portfolio.hu/rss/all.xml",                                    "type": "gazdasagi"},
    {"name": "Világgazdaság",  "rss_feed_url": "https://www.vg.hu/publicapi/hu/rss/vilaggazdasag/articles",               "type": "gazdasagi"},
    {"name": "Blikk",          "rss_feed_url": "https://www.blikk.hu/rss-articles.xml",                                   "type": "bulvar"},
    {"name": "Bors",           "rss_feed_url": "https://www.borsonline.hu/publicapi/hu/rss/bors/articles",                "type": "bulvar"},
    {"name": "Népszava",       "rss_feed_url": "https://nepszava.hu/feed",                                                "type": "ellenzeki_kritikus"},
    {"name": "Pesti Srácok",   "rss_feed_url": "https://pestisracok.hu/publicapi/hu/rss/pesti_sracok/articles",           "type": "kormanykozeli"},
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# Portálok sync-elése az adatbázisba induláskor
# ---------------------------------------------------------------------------
def sync_portals_to_db(db) -> dict[str, object]:
    """
    Upsert-eli az RSS_SOURCES listát a portals táblába (name alapján).
    Visszaad egy {name -> portal_id (UUID)} szótárat — csak a nyers ID-t,
    nem az ORM objektumot, hogy elkerüljük a DetachedInstanceError-t.
    """
    portal_map = {}
    for src in RSS_SOURCES:
        existing = db.query(Portals).filter(Portals.name == src["name"]).first()
        if existing:
            # URL frissítése, ha megváltozott
            if existing.rss_feed_url != src["rss_feed_url"]:
                existing.rss_feed_url = src["rss_feed_url"]
                log.info(f"  ↻ RSS URL frissítve: {src['name']}")
            existing.is_active = True
            portal_map[src["name"]] = existing.portal_id   # csak az ID!
        else:
            new_portal = Portals(
                name=src["name"],
                rss_feed_url=src["rss_feed_url"],
                type=src.get("type"),
                is_active=True,
            )
            db.add(new_portal)
            db.flush()   # portal_id generáláshoz commit előtt
            portal_map[src["name"]] = new_portal.portal_id  # csak az ID!
            log.info(f"  + Új portál hozzáadva: {src['name']}")
    db.commit()
    log.info(f"✓ {len(portal_map)} portál szinkronizálva az adatbázissal.")
    return portal_map


# ---------------------------------------------------------------------------
# Feed feldolgozás
# ---------------------------------------------------------------------------
def parse_feed_and_save(db, redis_conn, portal_id, rss_url: str, portal_name: str) -> int:
    """Letölti és feldolgozza egy portál RSS feedjét. portal_id egy nyers UUID."""
    MAX_RETRIES = 2
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(rss_url, headers=HEADERS, timeout=10)
            if response.status_code == 429:
                wait = 30 * attempt
                log.warning(
                    f"HTTP 429 Too Many Requests ({portal_name}) — "
                    f"várakozás {wait}s, majd újrapróbálkozás ({attempt}/{MAX_RETRIES})..."
                )
                time.sleep(wait)
                continue   # újrapróbálja a ciklus következő iterációja
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            break   # sikeres letöltés, kilép a retry loopból
        except requests.exceptions.RequestException as e:
            log.error(f"Feed letöltési hiba ({portal_name}, kísérlet {attempt}): {e}")
            if attempt == MAX_RETRIES:
                return 0
            time.sleep(10)
        except Exception as e:
            log.error(f"Váratlan hiba feed parse-olásakor ({portal_name}): {e}")
            return 0
    else:
        log.error(f"Feed letöltés végleg sikertelen: {portal_name}")
        return 0

    if feed.bozo:
        log.warning(f"Hibás feed formátum: {portal_name} ({feed.bozo_exception}) — folytatás...")

    new_count = 0
    for entry in feed.entries:
        if not hasattr(entry, "link") or not hasattr(entry, "title"):
            log.warning(f"Hiányos cikk-adat ({portal_name}) — kihagyva.")
            continue

        publish_time = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                publish_time = datetime.fromtimestamp(time.mktime(entry.published_parsed))
            except Exception:
                pass

        try:
            # --- Stealth delay: véletlenszerű várakozás a kérések között ---
            delay = random.uniform(3, 7)
            log.debug(f"Stealth delay: {delay:.1f}s ({portal_name})")
            time.sleep(delay)

            article_text = None
            try:
                downloaded = trafilatura.fetch_url(entry.link)
                if downloaded:
                    article_text = trafilatura.extract(downloaded)
            except Exception as fetch_err:
                log.warning(f"Trafilatura hiba ({entry.link}): {fetch_err}")

            new_article = Raw_Articles(
                portal_id=portal_id,
                url=entry.link,
                title=entry.title,
                publish_date=publish_time,
                raw_article_text=article_text,
            )
            db.add(new_article)
            db.commit()
            db.refresh(new_article)

            redis_conn.lpush("process_queue", str(new_article.article_id))
            text_status = f"{len(article_text)} kar." if article_text else "nincs szöveg"
            log.info(f"ÚJ CIKK: {portal_name} - {entry.title} [{text_status}]")
            new_count += 1

        except IntegrityError:
            db.rollback()
            log.debug(f"Ismert cikk (kihagyva): {portal_name} - {entry.title}")
        except Exception as e:
            log.error(f"Váratlan hiba cikk mentésekor ({entry.link}): {e}")
            db.rollback()

    return new_count


# ---------------------------------------------------------------------------
# Startup: portálok sync
# ---------------------------------------------------------------------------
_startup_db = get_db_session("Scraper-startup")
if not _startup_db:
    log.error("Nem sikerült csatlakozni a DB-hez induláskor. Leállás.")
    exit(1)

try:
    PORTAL_MAP = sync_portals_to_db(_startup_db)
finally:
    _startup_db.close()

r_conn = get_redis_connection("Scraper")
log.info(f"Scraper vár a feladatokra... ({len(RSS_SOURCES)} portál konfigurálva)")

# ---------------------------------------------------------------------------
# Fő Redis event loop
# ---------------------------------------------------------------------------
while True:
    try:
        task = r_conn.brpop("task_queue", 0)

        if task:
            log.info("*** FELADAT MEGKAPVA ***")
            db = get_db_session("Scraper")

            try:
                total_new = 0
                log.info(f"=== {len(RSS_SOURCES)} portál scrapelése ===")

                for src in RSS_SOURCES:
                    portal_id = PORTAL_MAP.get(src["name"])
                    if not portal_id:
                        log.warning(f"Portál nem található a mapben: {src['name']} — kihagyva.")
                        continue
                    try:
                        count = parse_feed_and_save(
                            db, r_conn,
                            portal_id=portal_id,
                            rss_url=src["rss_feed_url"],
                            portal_name=src["name"],
                        )
                        total_new += count
                    except Exception as e:
                        log.error(f"Hiba a '{src['name']}' feldolgozásakor: {e}")

                log.info(f"=== Scrapelési kör vége: {total_new} új cikk ===")

            except Exception as e:
                log.error(f"Hiba a scrapelési körben: {e}")
            finally:
                db.close()

    except Exception as e:
        log.error(f"Hiba a fő ciklusban: {e}")
        time.sleep(5)