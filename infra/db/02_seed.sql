-- =================================================================
-- Projekt 2026: Entitás és Kulcsszó Seed
-- =================================================================

CREATE TABLE IF NOT EXISTS political_entities (
    id   SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS keywords (
    id        SERIAL PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES political_entities(id) ON DELETE CASCADE,
    keyword   VARCHAR(255) NOT NULL,
    aliases   JSONB DEFAULT '[]'::jsonb,
    UNIQUE (entity_id, keyword)
);

INSERT INTO political_entities (name) VALUES
    ('Orbán Viktor'),
    ('Magyar Péter')
ON CONFLICT (name) DO NOTHING;

INSERT INTO keywords (entity_id, keyword, aliases) VALUES
    ((SELECT id FROM political_entities WHERE name = 'Orbán Viktor'), 'Orbán', '["Orbán", "OV", "Orbán Viktor", Fidesz, "KDNP"]'::jsonb)
ON CONFLICT (entity_id, keyword) DO NOTHING;

INSERT INTO keywords (entity_id, keyword, aliases) VALUES
    ((SELECT id FROM political_entities WHERE name = 'Magyar Péter'), 'Magyar Péter', '["Magyar Péter", "MP", "Tisza Párt", "Tisza", kormánypárt, Tisza Párt]'::jsonb)
ON CONFLICT (entity_id, keyword) DO NOTHING;
