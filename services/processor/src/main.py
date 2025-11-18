import time
import logging
from newspaper import Article, ArticleException
from transformers import pipeline
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from shared.connections import get_redis_connection, get_db_session
from shared.models.db_models import Raw_Articles, Processed_Articles
from src.sentiment_analyzer import analyze_sentiment_for_politicians

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

log.info("--- Processor Service Indul (v4 - AI/NER + Sentiment) ---")

try:
    log.info("NLP modellek betöltése... Ez eltarthat néhány percet.")
    
    NER_MODEL_NAME = "NYTK/named-entity-recognition-nerkor-hubert-hungarian"
    ner_pipeline = pipeline("ner", model=NER_MODEL_NAME, aggregation_strategy="simple")
    log.info(f"✓ NER modell betöltve: {NER_MODEL_NAME}")
    
    SENTIMENT_MODEL_NAME = "cmarkea/distilbert-base-hungarian-cased-sentiment-full"
    sentiment_pipeline = pipeline("sentiment-analysis", model=SENTIMENT_MODEL_NAME)
    log.info(f"✓ Sentiment modell betöltve: {SENTIMENT_MODEL_NAME}")
    
except Exception as e:
    log.error(f"Kritikus hiba az NLP modellek betöltésekor: {e}")
    exit(1)


def fetch_and_clean_article_text(url: str) -> str | None:
    """Downloads and extracts clean text from article URL."""
    try:
        article = Article(url)
        article.download()
        article.parse()
        
        if not article.text:
            log.warning(f"Nincs szöveg: {url}")
            return None
            
        log.info(f"Letöltve: {len(article.text)} karakter ({url})")
        return article.text

    except Exception as e:
        log.error(f"Letöltési hiba ({url}): {e}")
        return None

def process_article_pipeline(db: Session, article_id: str):    
    article = db.query(Raw_Articles).filter(Raw_Articles.article_id == article_id).first()
    if not article:
        log.error(f"Cikk nem található: {article_id}")
        return

    cleaned_text = fetch_and_clean_article_text(article.url)
    if not cleaned_text:
        article.status = 'processing_failed'
        db.commit()
        return

    article.raw_article_text = cleaned_text
    text_lower = cleaned_text.lower()
    
    mentions_ov, mentions_mp, sentiment_ov, sentiment_mp = analyze_sentiment_for_politicians(
        cleaned_text, ner_pipeline, sentiment_pipeline,
        "orbán viktor" in text_lower or "viktor orbán" in text_lower,
        "magyar péter" in text_lower or "péter magyar" in text_lower,
        article_id
    )

    db.add(Processed_Articles(
        article_id=article.article_id,
        word_count=len(cleaned_text.split()),
        mentions_ov=mentions_ov,
        mentions_mp=mentions_mp,
        sentiment_ov=sentiment_ov,
        sentiment_mp=sentiment_mp
    ))
    
    article.status = 'processed'
    db.commit()
    log.info(f"Feldolgozva: OV={mentions_ov}, MP={mentions_mp} (ID: {article_id})")


r_conn = get_redis_connection("Processor")
log.info("Processor várja a feladatokat...")

while True:
    try:
        task = r_conn.brpop("process_queue", 0)
        if not task:
            continue
            
        article_id = task[1]
        log.info(f"Feldolgozás: {article_id}")
        
        db = get_db_session("Processor")
        if not db:
            log.error("DB kapcsolat sikertelen, újrapróbálás...")
            r_conn.lpush("process_queue", article_id)
            time.sleep(10)
            continue

        try:
            process_article_pipeline(db, article_id)
        except IntegrityError:
            log.warning(f"Már feldolgozva: {article_id}")
            db.rollback()
        except Exception as e:
            log.error(f"Feldolgozási hiba ({article_id}): {e}")
            db.rollback()
        finally:
            db.close()

    except Exception as e:
        log.error(f"Redis loop hiba: {e}")
        time.sleep(5)