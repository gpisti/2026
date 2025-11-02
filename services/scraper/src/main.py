import redis
import time
import os
from sqlalchemy import text
from shared.models.db_models import SessionLocal, Portals

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

def get_db_session():
    """Creates a new database session."""
    session = None
    while session is None:
        try:
            session = SessionLocal()
            session.execute(text("SELECT 1"))
            print("Scraper sikeresen csatlakozva a PostgreSQL-hez!")
            return session
        except Exception as e:
            print(f"PostgreSQL nem elérhető ({e}), újrapróbálkozás 5mp múlva...")
            if session:
                session.close()
            time.sleep(5)

def fetch_portals_to_scrape():
    """Fetches all active portals from the database."""
    db = None
    portals = []
    try:
        db = get_db_session()
        portals = db.query(Portals).filter(Portals.is_active == True).all()
    except Exception as e:
        print(f"Hiba a 'portals' tábla olvasásakor: {e}")
    finally:
        if db:
            db.close()
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