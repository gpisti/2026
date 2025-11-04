import time
import logging
import requests
from bs4 import BeautifulSoup

from shared.connections import get_redis_connection, get_db_session
from shared.models.db_models import Raw_Articles, Processed_Articles

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

log.info("--- Processor Service Indul (v2 - Scraper & Parser) ---")

def fetch_and_clean_article_text(url: str) -> str | None:
    """Downloads the full HTML of an article and extracts the clean text."""
    headers = {'User-Agent': '2026-Scraper-Bot/1.0'}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        paragraphs = soup.find_all('p')
        
        if not paragraphs:
            log.warning(f"Nem található <p> tag a cikkben: {url}")
            return None

        full_text = "\n".join([p.get_text(strip=True) for p in paragraphs])
        
        log.info(f"Sikeres szöveg-kinyerés, hossza: {len(full_text)} karakter ({url})")
        return full_text

    except requests.exceptions.RequestException as e:
        log.error(f"Hiba a cikk letöltésekor ({url}): {e}")
        return None
    except Exception as e:
        log.error(f"Váratlan hiba a HTML tisztításakor ({url}): {e}")
        return None

r_conn = get_redis_connection("Processor")
log.info("Processor vár a 'process_queue' feladatokra...")

while True:
    try:
        task = r_conn.brpop("process_queue", 0) 
        
        if task:
            article_id = task[1]
            log.info(f"*** FELADAT MEGKAPVA: Cikk feldolgozása (ID: {article_id}) ***")
            
            db = get_db_session("Processor")
            if not db:
                log.error("Nem sikerült DB kapcsolatot szerezni, a feladat visszakerül a sorba...")
                r_conn.lpush("process_queue", article_id)
                time.sleep(10)
                continue

            try:
                article_to_process = db.query(Raw_Articles).filter(Raw_Articles.article_id == article_id).first()
                
                if not article_to_process:
                    log.error(f"Nem található cikk ezzel az ID-val: {article_id}. A feladat törölve.")
                    continue

                log.info(f"HTML letöltés indul: {article_to_process.url}")
                cleaned_text = fetch_and_clean_article_text(article_to_process.url)
                
                if cleaned_text:
                    article_to_process.raw_article_text = cleaned_text
                    
                    new_processed_entry = Processed_Articles(
                        article_id=article_to_process.article_id
                    )
                    
                    db.add(new_processed_entry)
                    db.commit()
                    log.info(f"--- Cikk feldolgozva és elmentve (ID: {article_id}) ---")
                else:
                    log.warning(f"Nem sikerült szöveget kinyerni, a cikk kihagyva (ID: {article_id})")

            except Exception as e:
                log.error(f"Hiba a cikk feldolgozása közben (ID: {article_id}): {e}")
                db.rollback()
            finally:
                db.close()

    except Exception as e:
        log.error(f"Hiba a fő ciklusban: {e}")
        time.sleep(5)