from sqlalchemy import create_engine, Column, Integer, String, Boolean, Float, TIMESTAMP, ForeignKey, JSON, BigInteger, TEXT, DATE, text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.dialects.postgresql import JSONB, UUID
import uuid
from shared.config import DATABASE_URL

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Portals(Base):
    __tablename__ = 'portals'
    portal_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), default=uuid.uuid4)
    name = Column(String(255), nullable=False, unique=True)
    rss_feed_url = Column(String(2048), nullable=False, unique=True)
    type = Column(String(50))
    is_active = Column(Boolean, default=True, nullable=False)
    last_successful_scrape_at = Column(TIMESTAMP(timezone=True))
    language = Column(String(5), default='hu', nullable=False)
    cbi_score = Column(Float)
    
    raw_articles = relationship("Raw_Articles", back_populates="portal")
    daily_portal_aggregates = relationship("Daily_Portal_Aggregates", back_populates="portal")

class Raw_Articles(Base):
    __tablename__ = 'raw_articles'
    article_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), default=uuid.uuid4)
    portal_id = Column(UUID(as_uuid=True), ForeignKey('portals.portal_id', ondelete='RESTRICT'), nullable=False)
    url = Column(String(2048), nullable=False, unique=True)
    title = Column(String(1024))
    publish_date = Column(TIMESTAMP(timezone=True))
    scraped_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: 'NOW()')
    http_status_code = Column(Integer)
    processing_attempts = Column(Integer, default=0, nullable=False)
    status = Column(String(20), default='new', nullable=False)
    raw_article_text = Column(TEXT)
    
    portal = relationship("Portals", back_populates="raw_articles")
    processed_article = relationship("Processed_Articles", back_populates="raw_article", uselist=False, cascade="all, delete")

class Processed_Articles(Base):
    __tablename__ = 'processed_articles'
    processed_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), default=uuid.uuid4)
    article_id = Column(UUID(as_uuid=True), ForeignKey('raw_articles.article_id', ondelete='CASCADE'), nullable=False, unique=True)
    mentions_ov = Column(Boolean, default=False, nullable=False)
    mentions_mp = Column(Boolean, default=False, nullable=False)
    sentiment_ov = Column(Float)
    sentiment_mp = Column(Float)
    topic = Column(String(100))
    word_count = Column(Integer)
    sentiment_confidence_score = Column(Float)
    narrative_hash = Column(String(255))
    model_version = Column(String(50))
    processed_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: 'NOW()')
    
    raw_article = relationship("Raw_Articles", back_populates="processed_article")

class Polls(Base):
    __tablename__ = 'polls'
    poll_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), default=uuid.uuid4)
    pollster_name = Column(String(255), nullable=False)
    publish_date = Column(TIMESTAMP(timezone=True), nullable=False)
    percent_ov = Column(Float, nullable=False)
    percent_mp = Column(Float, nullable=False)
    percent_undecided = Column(Float, nullable=False)

class Daily_Aggregates(Base):
    __tablename__ = 'daily_aggregates'
    agg_date = Column(DATE, primary_key=True)
    share_of_voice_ov = Column(Float)
    share_of_voice_mp = Column(Float)
    avg_sentiment_ov = Column(Float)
    avg_sentiment_mp = Column(Float)
    sentiment_std_dev_ov = Column(Float)
    sentiment_std_dev_mp = Column(Float)
    total_articles_ov = Column(Integer)
    total_articles_mp = Column(Integer)
    distinct_portals_ov = Column(Integer)
    distinct_portals_mp = Column(Integer)
    topic_distribution_jsonb = Column(JSONB)
    type_distribution_jsonb = Column(JSONB)
    calculation_run_at = Column(TIMESTAMP(timezone=True), default=lambda: 'NOW()')
    is_final = Column(Boolean, default=False, nullable=False)

class Daily_Portal_Aggregates(Base):
    __tablename__ = 'daily_portal_aggregates'
    agg_date = Column(DATE, primary_key=True)
    portal_id = Column(UUID(as_uuid=True), ForeignKey('portals.portal_id', ondelete='CASCADE'), primary_key=True)
    total_articles = Column(Integer)
    total_mentions_ov = Column(Integer)
    total_mentions_mp = Column(Integer)
    avg_sentiment_ov = Column(Float)
    avg_sentiment_mp = Column(Float)
    
    portal = relationship("Portals", back_populates="daily_portal_aggregates")

def get_db():
    """Helper function to handle the session safely."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()