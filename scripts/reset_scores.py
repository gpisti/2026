from sqlalchemy import create_engine, text
engine = create_engine("postgresql://admin:admin@db:5432/2026_db")
with engine.connect() as conn:
    conn.execute(text("UPDATE article_entity_mentions SET sentiment_score = NULL;"))
    conn.commit()
    print("Scores reset to NULL.")
