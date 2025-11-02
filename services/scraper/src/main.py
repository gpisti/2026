import redis
import time
import os
import psycopg2

print("--- Scraper Service Indul (v2) ---")

REDIS_HOST = os.environ.get("REDIS_HOST", "queue")
REDIS_PORT = 6379
r = None
while r is None:
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
        r.ping()
        print("Scraper sikeresen csatlakozva a Redis-hez!")
    except redis.exceptions.ConnectionError:
        print("Redis nem elérhető, újrapróbálkozás 5mp múlva...")
        time.sleep(5)

DB_HOST = os.environ.get("DB_HOST", "db")
DB_PORT = os.environ.get("DB_PORT", 5432)
DB_NAME = os.environ.get("DB_NAME", "2026_db")
DB_USER = os.environ.get("DB_USER", "admin")
DB_PASS = os.environ.get("DB_PASS", "admin")

def get_db_connection():
    """Creates a new database connection."""
    conn = None
    while conn is None:
        try:
            conn = psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASS
            )
            print("Scraper sikeresen csatlakozva a PostgreSQL-hez!")
            return conn
        except psycopg2.OperationalError as e:
            print(f"PostgreSQL nem elérhető ({e}), újrapróbálkozás 5mp múlva...")
            time.sleep(5)

def fetch_portals_to_scrape():
    """Fetches all active portals from the database."""
    conn = get_db_connection()
    if conn is None:
        return []

    portals = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT portal_id, name, rss_feed_url FROM portals WHERE is_active = true;")
            portals = cur.fetchall()
    except Exception as e:
        print(f"Hiba a 'portals' tábla olvasásakor: {e}")
    finally:
        if conn:
            conn.close()
            print("Adatbázis-kapcsolat lezárva.")
    
    return portals

print("Scraper vár a feladatokra...")
while True:
    try:
        task = r.brpop("task_queue", 0) 
        
        if task:
            task_name = task[1]
            print(f"[{time.strftime('%H:%M:%S')}] *** FELADAT MEGKAPVA: {task_name} ***")
            
            print("Adatbázis-lekérdezés indítása...")
            portals_to_scrape = fetch_portals_to_scrape()
            
            print(f"=== Találat: {len(portals_to_scrape)} aktív portál. ===")
            
            if portals_to_scrape:
                for portal in portals_to_scrape:
                    portal_id, name, url = portal
                    print(f"  -> Scrapelendő: {name} ({url})")
            
    except Exception as e:
        print(f"Hiba a fő ciklusban: {e}")
        time.sleep(5)