import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta
import os

OUTPUT_DIR = "c:/munka/project with gpisti/2026/research/mock_data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 1. media_outlets.csv
portals = [
    "TELEX", "INDEX", "24.HU", "HVG", "444", 
    "ORIGO", "MAGYAR_NEMZET", "MANDINER", "PESTI_SRACOK", "M1",
    "RTL", "ATV", "PORTFOLIO", "NAPI", "PENZCENTRUM"
]

media_data = []
for p in portals:
    reach = random.randint(10000, 1500000)
    media_data.append({"portal_nev": p, "becsult_eleres": reach})

df_media = pd.DataFrame(media_data)
df_media.to_csv(f"{OUTPUT_DIR}/media_outlets.csv", index=False)

# 2. processed_articles.csv
articles_data = []
start_date = datetime.now() - timedelta(days=30)
for _ in range(2000):
    dt = datetime.now() - timedelta(days=random.randint(0, 30), hours=random.randint(0,23), minutes=random.randint(0,59))
    portal = random.choice(portals)
    entity = random.choice(["OV", "MP"])
    sentiment = random.uniform(-1.0, 1.0)
    articles_data.append({
        "datum": dt.isoformat(),
        "portal_nev": portal,
        "emlitett_szemely": entity,
        "sentiment_score": round(sentiment, 4)
    })

# Add some anomalies
for _ in range(50):
    dt = datetime.now() - timedelta(days=5) # focused spike
    portal = "ORIGO"
    articles_data.append({
        "datum": dt.isoformat(),
        "portal_nev": portal,
        "emlitett_szemely": "MP",
        "sentiment_score": random.uniform(-1.0, -0.8) # extreme negative
    })

df_articles = pd.DataFrame(articles_data)
df_articles.sort_values(by="datum", inplace=True)
df_articles.to_csv(f"{OUTPUT_DIR}/processed_articles.csv", index=False)

# 3. polls.csv
polls_data = []
for i in range(4, -1, -1):
    dt = datetime.now() - timedelta(days=i*7)
    ov_pct = random.uniform(35.0, 45.0)
    mp_pct = random.uniform(30.0, 40.0)
    polls_data.append({
        "datum": dt.strftime("%Y-%m-%d"),
        "ov_szazalek": round(ov_pct, 1),
        "mp_szazalek": round(mp_pct, 1)
    })

df_polls = pd.DataFrame(polls_data)
df_polls.to_csv(f"{OUTPUT_DIR}/polls.csv", index=False)

print("Mock data generated successfully in research/mock_data/")
