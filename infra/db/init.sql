-- =================================================================
-- Projekt 2026: Adatbázis Inicializáló Szkript
-- Verzió: 1.0
-- Dialektus: PostgreSQL
-- =================================================================

-- Táblák törlése, ha már léteznek
DROP TABLE IF EXISTS DailyPortalAggregates CASCADE;
DROP TABLE IF EXISTS DailyAggregates CASCADE;
DROP TABLE IF EXISTS ProcessedArticles CASCADE;
DROP TABLE IF EXISTS RawArticles CASCADE;
DROP TABLE IF EXISTS Portals CASCADE;
DROP TABLE IF EXISTS Polls CASCADE;

-- =================================================================
-- 1. Tábla: Portals
-- A figyelt hírportálok "címjegyzéke"
-- =================================================================
CREATE TABLE Portals (
    portal_id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    rss_feed_url VARCHAR(2048) NOT NULL UNIQUE,
    type VARCHAR(50), -- pl. 'kormanykozeli', 'fuggetlen', 'ellenzeki'
    is_active BOOLEAN DEFAULT true NOT NULL,
    last_successful_scrape_at TIMESTAMP WITH TIME ZONE,
    language VARCHAR(5) DEFAULT 'hu' NOT NULL,
    cbi_score FLOAT -- A "Számított Torzítási Index", az Aggregator tölti fel
);

-- =================================================================
-- 2. Tábla: RawArticles
-- A beérkező, feldolgozatlan cikkek "piszkos" logja
-- =================================================================
CREATE TABLE RawArticles (
    article_id BIGSERIAL PRIMARY KEY,
    portal_id INT NOT NULL,
    url VARCHAR(2048) NOT NULL,
    title VARCHAR(1024),
    publish_date TIMESTAMP WITH TIME ZONE,
    scraped_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    http_status_code INT,
    processing_attempts INT DEFAULT 0 NOT NULL,
    status VARCHAR(20) DEFAULT 'new' NOT NULL, -- pl. 'new', 'processed', 'error'
    raw_article_text TEXT,
    
    CONSTRAINT fk_portal
        FOREIGN KEY(portal_id) 
        REFERENCES Portals(portal_id)
        ON DELETE RESTRICT, -- Nem törölhetsz portált, amíg cikke van
    
    UNIQUE(url) -- Egy cikket csak egyszer scrapelünk
);

CREATE INDEX idx_rawarticles_status ON RawArticles(status);
CREATE INDEX idx_rawarticles_publish_date ON RawArticles(publish_date);

-- =================================================================
-- 3. Tábla: ProcessedArticles
-- A "dúsított" adatok táblája (NLP kimenete)
-- =================================================================
CREATE TABLE ProcessedArticles (
    processed_id BIGSERIAL PRIMARY KEY,
    article_id BIGINT NOT NULL UNIQUE, -- 1:1 kapcsolat a RawArticles-szal
    
    -- NLP Eredmények
    mentions_ov BOOLEAN DEFAULT false NOT NULL,
    mentions_mp BOOLEAN DEFAULT false NOT NULL,
    sentiment_ov FLOAT,
    sentiment_mp FLOAT,
    topic VARCHAR(100),
    word_count INT,
    
    -- Torzításkezelő Mezők
    sentiment_confidence_score FLOAT, -- Irónia-szűrő (0.0 - 1.0)
    narrative_hash VARCHAR(255), -- Propaganda-szűrő (SimHash)
    
    -- Metaadatok
    model_version VARCHAR(50), -- Melyik NLP modell verzió elemezte?
    processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,

    CONSTRAINT fk_rawarticle
        FOREIGN KEY(article_id)
        REFERENCES RawArticles(article_id)
        ON DELETE CASCADE -- Ha törlődik a nyers cikk, törlődjön ez is
);

CREATE INDEX idx_processedarticles_narrative_hash ON ProcessedArticles(narrative_hash);
CREATE INDEX idx_processedarticles_topic ON ProcessedArticles(topic);

-- =================================================================
-- 4. Tábla: Polls
-- A "Szent Grál": A valós közvéleménykutatási adatok
-- =================================================================
CREATE TABLE Polls (
    poll_id SERIAL PRIMARY KEY,
    pollster_name VARCHAR(255) NOT NULL, -- pl. 'Medián', 'Nézőpont'
    publish_date TIMESTAMP WITH TIME ZONE NOT NULL,
    percent_ov FLOAT NOT NULL,
    percent_mp FLOAT NOT NULL,
    percent_undecided FLOAT NOT NULL
);

CREATE INDEX idx_polls_publish_date ON Polls(publish_date DESC);

-- =================================================================
-- 5. Tábla: DailyAggregates
-- A fő dashboard motorja (OLAP tábla), napi 1 sor
-- =================================================================
CREATE TABLE DailyAggregates (
    agg_date DATE PRIMARY KEY,
    
    -- Predikciós Bemenetek (Súlyozott)
    share_of_voice_ov FLOAT,
    share_of_voice_mp FLOAT,
    avg_sentiment_ov FLOAT,
    avg_sentiment_mp FLOAT,
    sentiment_std_dev_ov FLOAT, -- Polarizáció mérése
    sentiment_std_dev_mp FLOAT,
    
    -- Hangerő
    total_articles_ov INT,
    total_articles_mp INT,
    distinct_portals_ov INT,
    distinct_portals_mp INT,
    
    -- Téma Elemzés
    topic_distribution_jsonb JSONB, -- pl. '{"gazdasag": 50, "kulpol": 20}'
    type_distribution_jsonb JSONB, -- pl. '{"kormanykozeli": 120, "fuggetlen": 80}'
    
    -- Metaadatok
    calculation_run_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_final BOOLEAN DEFAULT false NOT NULL -- 'false' ha napközbeni, 'true' ha a végső éjszakai futás
);

-- =================================================================
-- 6. Tábla: DailyPortalAggregates
-- A "Nagyító" funkció motorja (OLAP tábla)
-- =================================================================
CREATE TABLE DailyPortalAggregates (
    agg_date DATE NOT NULL,
    portal_id INT NOT NULL,
    
    -- Metrikák
    total_articles INT,
    total_mentions_ov INT,
    total_mentions_mp INT,
    avg_sentiment_ov FLOAT,
    avg_sentiment_mp FLOAT,
    
    -- Kompozit elsődleges kulcs
    PRIMARY KEY (agg_date, portal_id),
    
    CONSTRAINT fk_portal_agg
        FOREIGN KEY(portal_id)
        REFERENCES Portals(portal_id)
        ON DELETE CASCADE -- Ha törlünk egy portált, törlődjön az aggregátuma is
);
