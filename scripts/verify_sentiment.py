from sqlalchemy import create_engine, text
engine = create_engine("postgresql://admin:admin@db:5432/2026_db")
with engine.connect() as conn:
    rows = conn.execute(text("SELECT a.sentiment_score, b.url FROM article_metrics a JOIN raw_articles b ON a.article_id = b.article_id WHERE a.sentiment_score IS NOT NULL AND a.sentiment_score != 0.0 LIMIT 10")).fetchall()
    print(f"Found {len(rows)} articles with non-zero sentiment:")
    for r in rows:
        print(f"[{r[0]}] {r[1]}")
