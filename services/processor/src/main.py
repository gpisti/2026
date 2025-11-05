import time
import logging
from newspaper import Article, ArticleException
from transformers import pipeline
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from shared.connections import get_redis_connection, get_db_session
from shared.models.db_models import Raw_Articles, Processed_Articles

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

log.info("--- Processor Service Indul (v3 - AI/NER) ---")

try:
    log.info("NLP modell betöltése (NYTK NER)... Ez eltarthat egy darabig.")
    ner_pipeline = pipeline("ner", model="NYTK/named-entity-recognition-nerkor-hubert-hungarian", aggregation_strategy="simple")
    log.info("NLP modell (NYTK NER) sikeresen betöltve. F-mérték: 90.18%")
except Exception as e:
    log.error(f"Kritikus hiba az NLP modell betöltésekor: {e}")
    exit(1)


def fetch_and_clean_article_text(url: str) -> str | None:
    """Downloads the full HTML of an article and extracts the clean text."""
    try:
        article = Article(url)
        article.download()
        article.parse()
        
        if not article.text:
            log.warning(f"A newspaper3k nem talált szöveget a cikkben: {url}")
            return None
            
        cleaned_text = article.text
        log.info(f"Sikeres szöveg-kinyerés, hossza: {len(cleaned_text)} karakter ({url})")
        
        return cleaned_text

    except ArticleException as e:
        log.error(f"Hiba a cikk letöltésekor (ArticleException): {e}")
        return None
    except Exception as e:
        log.error(f"Váratlan hiba a HTML tisztításakor ({url}): {e}")
        return None

def process_article_pipeline(db: Session, article_id: str):
    """
    Executes the full processing pipeline for a single article.
    Steps:
        1. Retrieve article metadata from the database.
        2. Download and clean the article HTML to extract text.
        3. Save cleaned text to the Raw_Articles table.
        4. Run Named Entity Recognition (NER) on the cleaned article text.
        5. Detect mentions of key persons and update flags accordingly.
        6. Save extracted information to the Processed_Articles table.
    """
    
    article = db.query(Raw_Articles).filter(Raw_Articles.article_id == article_id).first()
    if not article:
        log.error(f"Nem található cikk: {article_id}")
        return

    log.info(f"HTML letöltés indul: {article.url}")
    cleaned_text = fetch_and_clean_article_text(article.url)
    
    if not cleaned_text:
        log.warning(f"Nem sikerült szöveget kinyerni, a cikk kihagyva (ID: {article_id})")
        article.status = 'processing_failed'
        db.commit()
        return

    article.raw_article_text = cleaned_text
    
    mentions_ov = False
    mentions_mp = False
    word_count = len(cleaned_text.split())
    
    cleaned_text_lower = cleaned_text.lower()
    is_ov_context = "orbán viktor" in cleaned_text_lower or "viktor orbán" in cleaned_text_lower
    is_mp_context = "magyar péter" in cleaned_text_lower or "péter magyar" in cleaned_text_lower
    
    if is_ov_context or is_mp_context:
        log.debug(f"Kontextus: OV={is_ov_context}, MP={is_mp_context} (ID: {article_id})")

    try:
        text_chunks = [chunk.strip() for chunk in cleaned_text.split("\n") if chunk.strip()]
        list_of_entity_lists = ner_pipeline(text_chunks)
        all_entities = [entity for sublist in list_of_entity_lists for entity in sublist]
        
        log.info(f"NER analízis kész, {len(all_entities)} entitás találva {len(text_chunks)} bekezdésben (ID: {article_id})")
        
        for entity in all_entities:
            if entity['entity_group'] == 'PER':
                entity_text = entity['word'].lower().replace('#', '').strip()
                
                if not mentions_ov and ("orbán" in entity_text or "viktor" in entity_text):
                    mentions_ov = True
                    log.info(f"✓ Orbán Viktor említés: '{entity['word']}' (ID: {article_id})")
                
                if not mentions_mp and "magyar" in entity_text and "péter" in entity_text:
                    mentions_mp = True
                    log.info(f"✓ Magyar Péter említés (teljes név): '{entity['word']}' (ID: {article_id})")
                
                elif not mentions_mp and is_mp_context and ("magyar" == entity_text or "péter" == entity_text):
                    mentions_mp = True
                    log.info(f"✓ Magyar Péter említés (kontextus alapján): '{entity['word']}' (ID: {article_id})")
                
                if mentions_ov and mentions_mp:
                    log.debug(f"Mindkét személy megtalálva, early exit (ID: {article_id})")
                    break

    except Exception as e:
        log.error(f"NLP hiba a cikk feldolgozása közben (ID: {article_id}): {e}")

    new_processed_entry = Processed_Articles(
        article_id=article.article_id,
        word_count=word_count,
        mentions_ov=mentions_ov,
        mentions_mp=mentions_mp
    )
    
    article.status = 'processed'
    
    db.add(new_processed_entry)
    db.commit()
    log.info(f"--- Cikk feldolgozva és elmentve (ID: {article_id}) ---")


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
                process_article_pipeline(db, article_id)
            except IntegrityError as e:
                log.warning(f"Integritási hiba (valószínűleg már feldolgozva): {e}")
                db.rollback()
            except Exception as e:
                log.error(f"Váratlan hiba a feldolgozás során (ID: {article_id}): {e}")
                db.rollback()
            finally:
                db.close()

    except Exception as e:
        log.error(f"Hiba a fő Redis ciklusban: {e}")
        time.sleep(5)