from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from core.config import settings
import logging

logger = logging.getLogger(__name__)

if not settings.DATABASE_URL:
    logger.error("DATABASE_URL is missing from environment variables.")
    raise ValueError("DATABASE_URL must be configured.")

try:
    engine = create_engine(
        settings.DATABASE_URL, 
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10
    )
    logger.info("Successfully configured PostgreSQL database engine.")
except Exception as e:
    logger.error("Failed to configure database engine: %s", str(e))
    raise


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


Base = declarative_base()

def get_db():
    """
    Dependency generator for FastAPI routes.
    Provides a database session for a request and safely closes it afterward.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()