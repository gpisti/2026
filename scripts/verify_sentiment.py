from sqlalchemy import create_engine, text
engine = create_engine("postgresql://admin:admin@db:5432/2026_db")
with engine.connect() as conn:
    rows = conn.execute(text("""
        SELECT
            aem.sentiment_score,
            aem.matched_keyword,
            pe.name AS entity_name,
            ra.url
        FROM article_entity_mentions aem
        JOIN raw_articles ra ON ra.article_id = aem.article_id
        LEFT JOIN political_entities pe ON pe.id = aem.entity_id
        WHERE aem.sentiment_score IS NOT NULL
        ORDER BY aem.sentiment_score DESC
        LIMIT 20
    """)).fetchall()
    print(f"Found {len(rows)} entity mentions with sentiment score:")
    for r in rows:
        print(f"[{r[0]:+.4f}] {r[2]} (matched: '{r[1]}') -> {r[3]}")
