-- Orga v6 — World Model Schema
-- Drei Kern-Tabellen: world_tx, world_datom, world_attribute
-- Append-only: world_datom wird NIE geupdatet oder gelöscht.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- =====================================================================
-- world_tx: Eine fachliche Zustandsänderung (Provenance-Carrier)
-- =====================================================================

CREATE TABLE IF NOT EXISTS world_tx (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    actor        text NOT NULL DEFAULT 'system',
    device_id    text,
    scope_id     uuid,
    source_type  text,
    source_id    text,
    raw_text     text,
    metadata     jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- =====================================================================
-- world_datom: Atomare Fakten [Entity, Attribute, Value|Ref, TX, +/-]
-- =====================================================================

CREATE TABLE IF NOT EXISTS world_datom (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id   uuid NOT NULL,
    attribute   text NOT NULL,
    value       jsonb,
    ref_entity  uuid,
    tx_id       bigint NOT NULL REFERENCES world_tx(id),
    added       boolean NOT NULL,
    CHECK (
        (value IS NOT NULL AND ref_entity IS NULL)
        OR
        (value IS NULL AND ref_entity IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS world_datom_entity_idx  ON world_datom(entity_id, attribute);
CREATE INDEX IF NOT EXISTS world_datom_attribute_idx ON world_datom(attribute);
CREATE INDEX IF NOT EXISTS world_datom_tx_idx      ON world_datom(tx_id);
CREATE INDEX IF NOT EXISTS world_datom_ref_idx     ON world_datom(ref_entity);
CREATE INDEX IF NOT EXISTS world_datom_value_gin_idx ON world_datom USING gin(value);

-- =====================================================================
-- world_attribute: Kontrollierte Attribut-Vokabel
-- value_kind: 'scalar' (Wert) | 'ref' (Entity-Referenz)
-- cardinality: 'one' (max. 1 aktiver Wert) | 'many' (mehrere aktive)
-- =====================================================================

CREATE TABLE IF NOT EXISTS world_attribute (
    name          text PRIMARY KEY,
    value_kind    text NOT NULL CHECK (value_kind IN ('scalar', 'ref')),
    cardinality   text NOT NULL CHECK (cardinality IN ('one', 'many')),
    description   text NOT NULL DEFAULT ''
);

INSERT INTO world_attribute (name, value_kind, cardinality, description) VALUES
    ('sys/type',       'scalar', 'one', 'Entity-Typ: person, group, event, task, reminder, commitment, resource, note, fact, unknown'),
    ('sys/name',       'scalar', 'one', 'Anzeigename der Entity'),
    ('sys/alias',      'scalar', 'many','Weitere Namen/Schreibweisen der Entity'),
    ('org/actor',      'ref',    'one', 'Wer handelt (bei Commitments)'),
    ('org/object',     'ref',    'one', 'Worauf sich die Aussage bezieht'),
    ('org/context',    'ref',    'one', 'Übergeordneter Kontext (Event, Gruppe)'),
    ('org/owner',      'ref',    'one', 'Wem etwas gehört / wer verantwortlich ist'),
    ('org/participant','ref',    'many','Teilnehmende Personen'),
    ('org/member_of',  'ref',    'many','Mitgliedschaft in Gruppe/Event'),
    ('org/related_to', 'ref',    'many','Assoziierte Entity'),
    ('org/action',     'scalar', 'one', 'Aktion des Commitments: bring, attend, own, ...'),
    ('org/status',     'scalar', 'one', 'Status: active, done, cancelled, ...'),
    ('org/location',   'scalar', 'one', 'Ort'),
    ('time/start',     'scalar', 'one', 'Startzeitpunkt (ISO 8601)'),
    ('time/end',       'scalar', 'one', 'Endzeitpunkt (ISO 8601)'),
    ('time/due',       'scalar', 'one', 'Fälligkeitszeitpunkt (ISO 8601)'),
    ('note/body',      'scalar', 'one', 'Freitext-Inhalt (Notes, Facts)')
ON CONFLICT (name) DO NOTHING;

-- =====================================================================
-- world_current_datoms: View — aktueller World State
-- Ein Datom ist "aktiv", wenn seine letzte Operation added=true ist.
-- Partitioniert nach (entity, attribute, value|ref): pro Wertspur
-- gewinnt die neueste Operation.
-- =====================================================================

CREATE OR REPLACE VIEW world_current_datoms AS
SELECT *
FROM (
    SELECT
        d.*,
        row_number() OVER (
            PARTITION BY
                entity_id,
                attribute,
                COALESCE(value::text, ''),
                COALESCE(ref_entity::text, '')
            ORDER BY tx_id DESC, id DESC
        ) AS rn
    FROM world_datom d
) x
WHERE rn = 1
  AND added = true;
