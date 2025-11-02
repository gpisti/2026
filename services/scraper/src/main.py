import redis
import time
import os

print("--- Scraper Service Indul ---")

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

print("Scraper vár a feladatokra...")
while True:
    try:
        task = r.brpop("task_queue", 0) 
        
        if task:
            task_name = task[1]
            print(f"[{time.strftime('%H:%M:%S')}] *** FELADAT MEGKAPVA: {task_name} ***")
            
    except Exception as e:
        print(f"Hiba a feladat fogadásakor: {e}")
        time.sleep(5)