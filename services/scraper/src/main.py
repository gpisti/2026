import time
import logging
from shared.connections import get_redis_connection, get_db_session
from shared.models.db_models import Portals

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

log.info("--- Scraper Service Indul (v3 - SQLAlchemy) ---")

def fetch_portals_to_scrape():
    """Lekérdezi az összes aktív portált az adatbázisból SQLAlchemy segítségével."""
    db = None
    portals = []
    try:
        db = get_db_session("Scraper")
        portals = db.query(Portals).filter(Portals.is_active == True).all()
    except Exception as e:
        log.error(f"Hiba a 'portals' tábla olvasásakor: {e}")
        if db:
            db.rollback()
    finally:
        if db:
            db.close()
            log.debug("Adatbázis-kapcsolat lezárva.")
    
    return portals

r = get_redis_connection("Scraper")
log.info("Scraper vár a feladatokra...")
while True:
    try:
        task = r.brpop("task_queue", 0) 
        
        if task:
            task_name = task[1]
            log.info(f"*** FELADAT MEGKAPVA: {task_name} ***")
            
            log.info("Adatbázis-lekérdezés indítása...")
            portals_to_scrape = fetch_portals_to_scrape()
            
            log.info(f"=== Találat: {len(portals_to_scrape)} aktív portál. ===")
            
            if portals_to_scrape:
                for portal in portals_to_scrape:
                    log.info(f"  -> Scrapelendő: {portal.name} ({portal.rss_feed_url})")
                    # To be continued...
            
    except Exception as e:
        log.error(f"Hiba a fő ciklusban: {e}")
        time.sleep(5)