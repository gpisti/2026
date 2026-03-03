import time
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

def parse_feed_and_save(db, redis_conn, portal):
    """Processes a single portal's RSS feed."""
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        response = requests.get(portal.rss_feed_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
    except requests.exceptions.RequestException as e:
        log.error(f"Feed letöltési hiba ({portal.name}): {e}")
        return 0
    except Exception as e:
        log.error(f"Váratlan hiba feed parse-olásakor ({portal.name}): {e}")
        return 0

    if feed.bozo:
        log.warning(f"Hibás feed formátum: {portal.name} (Hiba: {feed.bozo_exception}) — folytatás...")

    new_count = 0
    for entry in feed.entries:
        if not hasattr(entry, 'link') or not hasattr(entry, 'title'):
            log.warning(f"Hiányos cikk-adat (cím vagy link hiányzik) a {portal.name} feedben. Kihagyva.")
            continue

        publish_time = None
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            try:
                publish_time = datetime.fromtimestamp(time.mktime(entry.published_parsed))
            except Exception:
                pass

        try:
            # --- Teljes cikk szöveg letöltése trafilatura-val ---
            article_text = None
            try:
                downloaded = trafilatura.fetch_url(entry.link)
                if downloaded:
                    article_text = trafilatura.extract(downloaded)
            except Exception as fetch_err:
                log.warning(f"Trafilatura hiba ({entry.link}): {fetch_err} — szöveg None marad.")

            new_article = Raw_Articles(
                portal_id=portal.portal_id,
                url=entry.link,
                title=entry.title,
                publish_date=publish_time,
                raw_article_text=article_text
            )
            db.add(new_article)
            db.commit()
            db.refresh(new_article)
            
            redis_conn.lpush("process_queue", str(new_article.article_id))
            text_status = f"{len(article_text)} kar." if article_text else "nincs szöveg"
            log.info(f"ÚJ CIKK: {portal.name} - {entry.title} [{text_status}]")
            new_count += 1

        except IntegrityError:
            db.rollback()
            log.debug(f"Ismert cikk (kihagyva): {portal.name} - {entry.title}")
        except Exception as e:
            log.error(f"Váratlan hiba cikk mentésekor ({entry.link}): {e}")
            db.rollback()

    return new_count

r_conn = get_redis_connection("Scraper")
log.info("Scraper vár a feladatokra...")

while True:
    try:
        task = r_conn.brpop("task_queue", 0)
        
        if task:
            log.info("*** FELADAT MEGKAPVA ***")
            db = get_db_session("Scraper")
            
            try:
                portals = db.query(Portals).filter(Portals.is_active == True).all()
                log.info(f"=== {len(portals)} aktív portál ===")
                
                total_new = 0
                for portal in portals:
                    total_new += parse_feed_and_save(db, r_conn, portal)
                
                log.info(f"=== Scrapelési kör vége: {total_new} új cikk ===")
            except Exception as e:
                log.error(f"Hiba a portálok lekérdezésekor: {e}")
            finally:
                db.close()
    except Exception as e:
        log.error(f"Hiba a fő ciklusban: {e}")
        time.sleep(5)