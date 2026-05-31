from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from shared.config import DATABASE_URL

app = FastAPI(title="Projekt 2026 API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = create_engine(DATABASE_URL)


@app.get("/api/momentum/latest")
def get_latest_momentum():
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                dm.date,
                dm.entity_id,
                pe.name AS entity_name,
                dm.total_articles,
                dm.total_reach,
                dm.average_sentiment,
                dm.momentum_score,
                dm.baseline_sentiment,
                dm.adjusted_momentum,
                dm.calculated_at
            FROM daily_momentum dm
            INNER JOIN political_entities pe ON pe.id = dm.entity_id
            ORDER BY dm.date ASC, dm.entity_id ASC
        """)).fetchall()

    if not rows:
        return {"data": [], "message": "No momentum data available yet."}

    result = [
        {
            "date": str(row.date),
            "entity_id": row.entity_id,
            "entity_name": row.entity_name,
            "total_articles": row.total_articles,
            "total_reach": row.total_reach,
            "average_sentiment": float(row.average_sentiment) if row.average_sentiment is not None else None,
            "momentum_score": float(row.momentum_score) if row.momentum_score is not None else None,
            "baseline_sentiment": float(row.baseline_sentiment) if row.baseline_sentiment is not None else None,
            "adjusted_momentum": float(row.adjusted_momentum) if row.adjusted_momentum is not None else None,
            "calculated_at": str(row.calculated_at) if row.calculated_at else None,
        }
        for row in rows
    ]

    return {"data": result}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
