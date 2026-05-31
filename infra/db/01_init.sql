-- =================================================================
-- Projekt 2026: Adatbázis Inicializáló Szkript
-- Verzió: 1.2 (UUID-ra átállítva)
-- Dialektus: PostgreSQL
-- =================================================================

-- Szükséges kiterjesztés a gen_random_uuid() függvényhez
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Táblák törlése, ha már léteznek
DROP TABLE IF EXISTS Daily_Portal_Aggregates CASCADE;
DROP TABLE IF EXISTS Daily_Aggregates CASCADE;
DROP TABLE IF EXISTS Processed_Articles CASCADE;
DROP TABLE IF EXISTS Raw_Articles CASCADE;
DROP TABLE IF EXISTS Portals CASCADE;
DROP TABLE IF EXISTS Polls CASCADE;

-- =================================================================
-- 1. Tábla: Portals
-- =================================================================
CREATE TABLE Portals (
    portal_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), -- MÓDOSÍTVA
    name VARCHAR(255) NOT NULL UNIQUE,
    rss_feed_url VARCHAR(2048) NOT NULL UNIQUE,
    type VARCHAR(50), 
    is_active BOOLEAN DEFAULT true NOT NULL,
    last_successful_scrape_at TIMESTAMP WITH TIME ZONE,
    language VARCHAR(5) DEFAULT 'hu' NOT NULL,
    cbi_score FLOAT
);

-- =================================================================
-- 2. Tábla: Raw_Articles
-- =================================================================
CREATE TABLE Raw_Articles (
    article_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), -- MÓDOSÍTVA
    portal_id UUID NOT NULL, -- MÓDOSÍTVA
    url VARCHAR(2048) NOT NULL,
    title VARCHAR(1024),
    publish_date TIMESTAMP WITH TIME ZONE,
    scraped_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    http_status_code INT,
    processing_attempts INT DEFAULT 0 NOT NULL,
    status VARCHAR(20) DEFAULT 'new' NOT NULL, 
    raw_article_text TEXT,
    
    CONSTRAINT fk_portal
        FOREIGN KEY(portal_id) 
        REFERENCES Portals(portal_id)
        ON DELETE RESTRICT,
    
    UNIQUE(url)
);

CREATE INDEX idx_rawarticles_status ON Raw_Articles(status);
CREATE INDEX idx_rawarticles_publish_date ON Raw_Articles(publish_date);

-- =================================================================
-- 3. Tábla: Processed_Articles
-- =================================================================
CREATE TABLE Processed_Articles (
    processed_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), -- MÓDOSÍTVA
    article_id UUID NOT NULL UNIQUE, -- MÓDOSÍTVA
    
    mentions_ov BOOLEAN DEFAULT false NOT NULL,
    mentions_mp BOOLEAN DEFAULT false NOT NULL,
    sentiment_ov FLOAT,
    sentiment_mp FLOAT,
    topic VARCHAR(100),
    word_count INT,
    
    sentiment_confidence_score FLOAT, 
    narrative_hash VARCHAR(255), 
    
    model_version VARCHAR(50), 
    processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,

    CONSTRAINT fk_rawarticle
        FOREIGN KEY(article_id)
        REFERENCES Raw_Articles(article_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_processedarticles_narrative_hash ON Processed_Articles(narrative_hash);
CREATE INDEX idx_processedarticles_topic ON Processed_Articles(topic);

-- =================================================================
-- 4. Tábla: Polls
-- =================================================================
CREATE TABLE Polls (
    poll_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), -- MÓDOSÍTVA
    pollster_name VARCHAR(255) NOT NULL, 
    publish_date TIMESTAMP WITH TIME ZONE NOT NULL,
    percent_ov FLOAT NOT NULL,
    percent_mp FLOAT NOT NULL,
    percent_undecided FLOAT NOT NULL
);

CREATE INDEX idx_polls_publish_date ON Polls(publish_date DESC);

-- =================================================================
-- 5. Tábla: Daily_Aggregates
-- (Itt az agg_date marad a PK, ahogy megbeszéltük)
-- =================================================================
CREATE TABLE Daily_Aggregates (
    agg_date DATE PRIMARY KEY,
    
    share_of_voice_ov FLOAT,
    share_of_voice_mp FLOAT,
    avg_sentiment_ov FLOAT,
    avg_sentiment_mp FLOAT,
    sentiment_std_dev_ov FLOAT, 
    sentiment_std_dev_mp FLOAT,
    
    total_articles_ov INT,
    total_articles_mp INT,
    distinct_portals_ov INT,
    distinct_portals_mp INT,
    
    topic_distribution_jsonb JSONB, 
    type_distribution_jsonb JSONB, 
    
    calculation_run_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_final BOOLEAN DEFAULT false NOT NULL
);

-- =================================================================
-- 6. Tábla: Daily_Portal_Aggregates
-- (Itt a PK (agg_date, portal_id) kompozit)
-- =================================================================
CREATE TABLE Daily_Portal_Aggregates (
    agg_date DATE NOT NULL,
    portal_id UUID NOT NULL, -- MÓDOSÍTVA
    
    total_articles INT,
    total_mentions_ov INT,
    total_mentions_mp INT,
    avg_sentiment_ov FLOAT,
    avg_sentiment_mp FLOAT,
    
    PRIMARY KEY (agg_date, portal_id), -- MÓDOSÍTVA
    
    CONSTRAINT fk_portal_agg
        FOREIGN KEY(portal_id)
        REFERENCES Portals(portal_id)
        ON DELETE CASCADE
);

-- =================================================================
-- 7. Alapértelmezett Portálok Beszúrása
-- =================================================================
INSERT INTO Portals (name, rss_feed_url, type)
VALUES 
    ('Telex', 'https://telex.hu/rss', 'fuggetlen'),
    ('444', 'https://444.hu/feed', 'fuggetlen'),
    ('HVG', 'https://hvg.hu/rss', 'ellenzeki_kritikus'),
    ('Index', 'https://index.hu/rss/', 'kormanykozeli'),
    ('Origo', 'https://www.origo.hu/rss/index.xml', 'kormanykozeli'),
    ('Mandiner', 'https://mandiner.hu/rss/', 'kormanykozeli'),
    ('Magyar Nemzet', 'https://magyarnemzet.hu/rss/', 'kormanykozeli')
ON CONFLICT (name) DO NOTHING;