import redis
import time
import os

print("--- Scheduler Service Indul ---")

REDIS_HOST = os.environ.get("REDIS_HOST", "queue")
REDIS_PORT = 6379

r = None
while r is None:
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
        r.ping()
        print("Scheduler sikeresen csatlakozva a Redis-hez!")
    except redis.exceptions.ConnectionError:
        print("Redis nem elérhető, újrapróbálkozás 5mp múlva...")
        time.sleep(5)

while True:
    try:
        task_name = "scrape_task"
        r.lpush("task_queue", task_name) 
        print(f"[{time.strftime('%H:%M:%S')}] Új feladat elküldve: {task_name}")
    except Exception as e:
        print(f"Hiba a feladat küldésekor: {e}")
    
    time.sleep(10)