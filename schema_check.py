from sqlalchemy import create_engine, text
engine = create_engine("postgresql://admin:admin@db:5432/2026_db")
with engine.connect() as conn:
    for tbl in ['keywords', 'political_entities', 'article_entity_mentions']:
        rows = conn.execute(text(f"""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = '{tbl}' ORDER BY ordinal_position
        """)).fetchall()
        print(f"\n=== {tbl} ===")
        for r in rows: print(f"  {r[0]}: {r[1]}")

    print("\n=== keywords data ===")
    rows = conn.execute(text("SELECT * FROM keywords LIMIT 10")).fetchall()
    for r in rows: print(dict(r._mapping))
