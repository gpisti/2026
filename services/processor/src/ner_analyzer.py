import logging
import nltk
from typing import List, Tuple

log = logging.getLogger(__name__)

try:
    nltk.download('punkt', quiet=True)
    nltk.download('punkt_tab', quiet=True)
except Exception as e:
    log.warning(f"NLTK punkt letöltési figyelmeztetés: {e}")


def _add_mention(person_name: str, entity_word: str, sentence: str, 
                 mentions_list: List[bool], sentences_list: List[str], article_id: str):
    """DRY helper: Log first mention and collect unique sentences."""
    if not mentions_list[0]:
        mentions_list[0] = True
        log.info(f"✓ {person_name} említés: '{entity_word}' (ID: {article_id})")
    
    if sentence not in sentences_list:
        sentences_list.append(sentence)


def analyze_entities_in_article(
    cleaned_text: str,
    ner_pipeline,
    is_ov_context: bool,
    is_mp_context: bool,
    article_id: str
) -> Tuple[bool, bool, List[str], List[str]]:
    """Extracts OV/MP mentions from text using NER. Returns mentions flags and relevant sentences."""
    
    sentences = nltk.sent_tokenize(cleaned_text, language='hungarian')
    if not sentences:
        log.warning(f"Üres cikk (ID: {article_id})")
        return False, False, [], []
    
    log.info(f"NER elemzés {len(sentences)} mondaton (ID: {article_id})")
    
    mentions_ov, mentions_mp = [False], [False]
    ov_sentences, mp_sentences = [], []
    
    try:
        ner_results = ner_pipeline(sentences)
        
        for i, entities in enumerate(ner_results):
            if not entities or len(sentences[i].split()) > 400:
                continue
            
            sentence = sentences[i]
            
            for entity in entities:
                if entity['entity_group'] != 'PER':
                    continue
                
                name = entity['word'].lower().replace('#', '').strip()
                
                if "orbán" in name and "viktor" in name:
                    _add_mention("Orbán Viktor", entity['word'], sentence, mentions_ov, ov_sentences, article_id)
                elif is_ov_context and ("orbán" == name or "viktor" == name):
                    _add_mention("Orbán Viktor (kontextus)", entity['word'], sentence, mentions_ov, ov_sentences, article_id)
                
                if "magyar" in name and "péter" in name:
                    _add_mention("Magyar Péter", entity['word'], sentence, mentions_mp, mp_sentences, article_id)
                elif is_mp_context and name in ("magyar", "péter"):
                    _add_mention("Magyar Péter (kontextus)", entity['word'], sentence, mentions_mp, mp_sentences, article_id)
    
    except Exception as e:
        log.error(f"NER hiba (ID: {article_id}): {e}")
    
    return mentions_ov[0], mentions_mp[0], ov_sentences, mp_sentences
