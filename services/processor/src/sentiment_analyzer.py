import logging
from typing import Optional, Tuple, List
from src.ner_analyzer import analyze_entities_in_article

log = logging.getLogger(__name__)


def analyze_sentiment_for_politicians(
    cleaned_text: str,
    ner_pipeline,
    sentiment_pipeline,
    is_ov_context: bool,
    is_mp_context: bool,
    article_id: str
) -> Tuple[bool, bool, Optional[float], Optional[float]]:
    """Returns OV/MP mentions and their sentiment scores."""
    
    mentions_ov, mentions_mp, ov_sentences, mp_sentences = analyze_entities_in_article(
        cleaned_text, ner_pipeline, is_ov_context, is_mp_context, article_id
    )
    
    def _calc_sentiment(sentences: List[str], name: str) -> Optional[float]:
        if not sentences:
            return None
        
        unique = list(set(sentences))
        log.info(f"Sentiment ({name}): {len(unique)} mondat (ID: {article_id})")
        
        try:
            results = sentiment_pipeline(unique)
            score = sum(r['score'] if r['label'].lower() == 'pozitív' 
                       else -r['score'] if r['label'].lower() == 'negatív' 
                       else 0 for r in results) / len(results)
            
            log.info(f"✓ {name}: {score:.3f}")
            return score
        except Exception as e:
            log.error(f"Sentiment hiba ({name}, ID: {article_id}): {e}")
            return None
    
    return mentions_ov, mentions_mp, _calc_sentiment(ov_sentences, "OV"), _calc_sentiment(mp_sentences, "MP")
