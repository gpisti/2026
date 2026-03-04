import os
import time
import logging
import requests
from urllib.parse import urlparse, urlunparse
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

log.info("--- Metrics Updater Service Indul ---")

# ---------------------------------------------------------------------------
# Konfiguráció
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
SHAREDCOUNT_API_KEY = os.getenv("SHAREDCOUNT_API_KEY")
POLL_INTERVAL_SECONDS = int(os.getenv("METRICS_POLL_INTERVAL", "30"))

REDDIT_HEADERS = {
    "User-Agent": "2026-MetricsBot/1.0 (research project; contact: admin@mentor.local)"
}

# ---------------------------------------------------------------------------
# DB kapcsolat
# ---------------------------------------------------------------------------
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def ensure_metrics_table():
    """Létrehozza az article_metrics táblát (ha kell), és gondoskodik az updated_at oszlopról."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS article_metrics (
                article_id       UUID PRIMARY KEY REFERENCES raw_articles(article_id) ON DELETE CASCADE,
                fb_interactions  INTEGER DEFAULT 0,
                reddit_upvotes   INTEGER DEFAULT 0,
                reddit_comments  INTEGER DEFAULT 0,
                last_updated     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        # Ha a tábla már létezett updated_at nélkül, adjuk hozzá
        conn.execute(text("""
            ALTER TABLE article_metrics
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        """))
        conn.commit()
    log.info("✓ article_metrics tábla ellenőrizve/létrehozva (updated_at oszloppal).")


# ---------------------------------------------------------------------------
# URL tisztítás
# ---------------------------------------------------------------------------
def sanitize_url(url: str) -> str:
    """
    Eltávolítja a query paramétereket és fragmentumot az URL-ről.
    Csak a scheme + netloc + path marad meg — ez megy az API-khoz.
    Pl. 'https://hvg.hu/itthon/cikk?utm_source=rss#top' -> 'https://hvg.hu/itthon/cikk'
    """
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


# ---------------------------------------------------------------------------
# API hívások
# ---------------------------------------------------------------------------
def fetch_sharedcount(url: str) -> int:
    """SharedCount API — Facebook interakciók lekérése (tisztított URL-lel)."""
    if not SHAREDCOUNT_API_KEY:
        log.warning("SHAREDCOUNT_API_KEY nincs beállítva, FB adat kihagyva.")
        return 0
    clean = sanitize_url(url)
    try:
        resp = requests.get(
            "https://api.sharedcount.com/v1.0/",
            params={"url": clean, "apikey": SHAREDCOUNT_API_KEY},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        fb = data.get("Facebook", {})
        return (
            fb.get("total_count", 0) or
            fb.get("share_count", 0) +
            fb.get("comment_count", 0) +
            fb.get("reaction_count", 0)
        )
    except Exception as e:
        log.warning(f"SharedCount hiba ({clean}): {e}")
        return 0


def fetch_reddit(url: str) -> tuple[int, int]:
    """Reddit search JSON — upvote és kommentszám lekérése (tisztított URL-lel)."""
    clean = sanitize_url(url)
    try:
        resp = requests.get(
            f"https://www.reddit.com/search.json?q=url:{clean}",
            headers=REDDIT_HEADERS,
            timeout=10
        )
        resp.raise_for_status()
        children = resp.json().get("data", {}).get("children", [])
        if not children:
            return 0, 0
        # Legjobb találat adatai
        best = max(children, key=lambda c: c["data"].get("score", 0))["data"]
        return best.get("score", 0), best.get("num_comments", 0)
    except Exception as e:
        log.warning(f"Reddit hiba ({clean}): {e}")
        return 0, 0


# ---------------------------------------------------------------------------
# Fő logika
# ---------------------------------------------------------------------------
def fetch_pending_articles(db) -> list:
    """
    Többlépcsős polling:
    - Csak az elmúlt 48 órában bekerült cikkeket nézzük (aktív életciklus).
    - Feldolgozza, ha: még nincs metrikája, VAGY az utolsó frissítés 6+ órája volt.
    - Az érlelési szűrő (3 óra) megmarad: csak scraped_at <= NOW() - 3h cikkeket vesz.
    """
    rows = db.execute(text("""
        SELECT DISTINCT ra.article_id, ra.url
        FROM raw_articles ra
        INNER JOIN article_entity_mentions aem ON aem.article_id = ra.article_id
        LEFT JOIN article_metrics am ON am.article_id = ra.article_id
        WHERE ra.url IS NOT NULL
          AND ra.scraped_at >= NOW() - INTERVAL '48 hours'
          AND ra.scraped_at <= NOW() - INTERVAL '3 hours'
          AND (
            am.article_id IS NULL
            OR am.updated_at <= NOW() - INTERVAL '6 hours'
          )
        LIMIT 20
    """)).fetchall()
    return rows


def update_metrics_for_article(db, article_id: str, url: str):
    """Lekéri a metrikákat és elmenti/frissíti az article_metrics táblában."""
    fb_interactions = fetch_sharedcount(url)   # URL tisztítás a függvényen belül történik
    reddit_upvotes, reddit_comments = fetch_reddit(url)

    db.execute(text("""
        INSERT INTO article_metrics
            (article_id, fb_interactions, reddit_upvotes, reddit_comments, last_updated, updated_at)
        VALUES (:article_id, :fb, :ru, :rc, NOW(), NOW())
        ON CONFLICT (article_id) DO UPDATE SET
            fb_interactions = EXCLUDED.fb_interactions,
            reddit_upvotes  = EXCLUDED.reddit_upvotes,
            reddit_comments = EXCLUDED.reddit_comments,
            last_updated    = NOW(),
            updated_at      = NOW()
    """), {
        "article_id": str(article_id),
        "fb": fb_interactions,
        "ru": reddit_upvotes,
        "rc": reddit_comments,
    })
    db.commit()
    clean = sanitize_url(url)
    log.info(
        f"Metrics frissítve: [{clean}] — "
        f"FB: {fb_interactions}, Reddit upvotes: {reddit_upvotes}, comments: {reddit_comments}"
    )


# ---------------------------------------------------------------------------
# Startup + fő loop
# ---------------------------------------------------------------------------
ensure_metrics_table()

log.info(f"Metrics Updater fut — {POLL_INTERVAL_SECONDS}s ciklusidővel.")

while True:
    try:
        db = SessionLocal()
        try:
            pending = fetch_pending_articles(db)
            if not pending:
                log.debug("Nincs feldolgozandó cikk, várakozás...")
            else:
                log.info(f"{len(pending)} cikk vár metrika-frissítésre.")
                for row in pending:
                    try:
                        update_metrics_for_article(db, row.article_id, row.url)
                        time.sleep(1)  # API rate-limit védelem
                    except Exception as e:
                        log.error(f"Hiba a cikk feldolgozásakor ({row.url}): {e}")
                        db.rollback()
        finally:
            db.close()

    except Exception as e:
        log.error(f"Fő loop hiba: {e}")

    time.sleep(POLL_INTERVAL_SECONDS)
