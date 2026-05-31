import redis
import time
import logging
from sqlalchemy.orm import Session
from sqlalchemy import text
from shared.models.db_models import SessionLocal
from shared.config import REDIS_HOST, REDIS_PORT

logger = logging.getLogger(__name__)


def get_redis_connection(service_name: str = "Service"):
    """Folyamatosan próbál csatlakozni a Redis-hez."""
    r = None
    while r is None:
        try:
            r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
            r.ping()
            logger.info(f"{service_name} sikeresen csatlakozva a Redis-hez!")
            return r
        except redis.exceptions.ConnectionError:
            logger.warning(f"Redis nem elérhető, újrapróbálkozás 5mp múlva...")
            time.sleep(5)


def get_db_session(service_name: str = "Service") -> Session:
    """Folyamatosan próbál adatbázis session-t nyitni."""
    db = None
    while db is None:
        try:
            db = SessionLocal()
            db.execute(text("SELECT 1"))
            logger.info(f"{service_name} sikeresen csatlakozva a PostgreSQL-hez!")
            return db
        except Exception as e:
            logger.warning(f"PostgreSQL nem elérhető ({e}), újrapróbálkozás 5mp múlva...")
            if db:
                db.close()
                db = None
            time.sleep(5)

