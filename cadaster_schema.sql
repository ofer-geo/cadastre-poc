-- =============================================================
-- cadaster_schema.sql
-- Cadastral Parcel Management System
-- PostgreSQL 18 + PostGIS
-- =============================================================

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS cadaster;

-- =============================================================
-- TABLES
-- =============================================================

CREATE TABLE cadaster.users (
    user_id    SERIAL PRIMARY KEY,
    username   TEXT        NOT NULL UNIQUE,
    role       TEXT        NOT NULL CHECK (role IN ('editor', 'manager')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cadaster.parcels (
    gid        SERIAL  PRIMARY KEY,
    parcel_id  INTEGER NOT NULL,
    geom       geometry(MultiPolygon, 4326) NOT NULL,
    owner_name TEXT,
    valid_from DATE    NOT NULL DEFAULT CURRENT_DATE,
    valid_to   DATE,
    created_by INTEGER REFERENCES cadaster.users(user_id),
    notes      TEXT,
    CONSTRAINT parcels_valid_period_check
        CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

-- One active version per parcel_id (valid_to IS NULL = currently active)
CREATE UNIQUE INDEX parcels_one_active_per_parcel
    ON cadaster.parcels(parcel_id)
    WHERE valid_to IS NULL;

CREATE INDEX parcels_geom_idx      ON cadaster.parcels USING GIST(geom);
CREATE INDEX parcels_parcel_id_idx ON cadaster.parcels(parcel_id);

CREATE TABLE cadaster.draft_parcels (
    draft_id     SERIAL      PRIMARY KEY,
    parcel_id    INTEGER     NOT NULL,
    action       TEXT        NOT NULL CHECK (action IN ('create', 'modify', 'retire')),
    geom         geometry(MultiPolygon, 4326),
    owner_name   TEXT,
    notes        TEXT,
    submitted_by INTEGER     NOT NULL REFERENCES cadaster.users(user_id),
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status       TEXT        NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_by  INTEGER     REFERENCES cadaster.users(user_id),
    reviewed_at  TIMESTAMPTZ,
    review_notes TEXT
);

CREATE INDEX draft_parcels_status_idx ON cadaster.draft_parcels(status);

-- =============================================================
-- VIEWS
-- =============================================================

CREATE VIEW cadaster.active_parcels AS
    SELECT p.gid, p.parcel_id, p.geom, p.owner_name, p.valid_from,
           p.created_by, u.username AS created_by_username, p.notes
    FROM   cadaster.parcels p
    LEFT JOIN cadaster.users u ON u.user_id = p.created_by
    WHERE  p.valid_to IS NULL;

CREATE VIEW cadaster.parcel_history AS
    SELECT
        p.gid, p.parcel_id, p.geom, p.owner_name,
        p.valid_from, p.valid_to,
        p.created_by, u.username AS created_by_username,
        p.notes,
        CASE WHEN p.valid_to IS NULL THEN 'active' ELSE 'historical' END AS version_status
    FROM  cadaster.parcels p
    LEFT JOIN cadaster.users u ON u.user_id = p.created_by
    ORDER BY p.parcel_id, p.valid_from;

CREATE VIEW cadaster.pending_drafts AS
    SELECT
        d.*,
        u.username AS submitted_by_username
    FROM  cadaster.draft_parcels d
    JOIN  cadaster.users u ON u.user_id = d.submitted_by
    WHERE d.status = 'pending'
    ORDER BY d.submitted_at;

-- =============================================================
-- FUNCTIONS
-- =============================================================

CREATE OR REPLACE FUNCTION cadaster.approve_draft(
    p_draft_id   INTEGER,
    p_manager_id INTEGER
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    v_draft cadaster.draft_parcels%ROWTYPE;
BEGIN
    SELECT * INTO v_draft
    FROM   cadaster.draft_parcels
    WHERE  draft_id = p_draft_id AND status = 'pending'
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Draft % not found or not in pending status', p_draft_id;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM cadaster.users
        WHERE  user_id = p_manager_id AND role = 'manager'
    ) THEN
        RAISE EXCEPTION 'User % does not have manager role', p_manager_id;
    END IF;

    IF v_draft.action = 'create' THEN
        INSERT INTO cadaster.parcels(parcel_id, geom, owner_name, valid_from, created_by, notes)
        VALUES (v_draft.parcel_id, v_draft.geom, v_draft.owner_name,
                CURRENT_DATE, v_draft.submitted_by, v_draft.notes);

    ELSIF v_draft.action = 'modify' THEN
        UPDATE cadaster.parcels
        SET    valid_to = CURRENT_DATE
        WHERE  parcel_id = v_draft.parcel_id AND valid_to IS NULL;

        INSERT INTO cadaster.parcels(parcel_id, geom, owner_name, valid_from, created_by, notes)
        VALUES (v_draft.parcel_id, v_draft.geom, v_draft.owner_name,
                CURRENT_DATE, v_draft.submitted_by, v_draft.notes);

    ELSIF v_draft.action = 'retire' THEN
        UPDATE cadaster.parcels
        SET    valid_to = CURRENT_DATE
        WHERE  parcel_id = v_draft.parcel_id AND valid_to IS NULL;
    END IF;

    UPDATE cadaster.draft_parcels
    SET    status      = 'approved',
           reviewed_by = p_manager_id,
           reviewed_at = now()
    WHERE  draft_id = p_draft_id;
END;
$$;

CREATE OR REPLACE FUNCTION cadaster.reject_draft(
    p_draft_id     INTEGER,
    p_manager_id   INTEGER,
    p_review_notes TEXT DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM cadaster.users
        WHERE  user_id = p_manager_id AND role = 'manager'
    ) THEN
        RAISE EXCEPTION 'User % does not have manager role', p_manager_id;
    END IF;

    UPDATE cadaster.draft_parcels
    SET    status       = 'rejected',
           reviewed_by  = p_manager_id,
           reviewed_at  = now(),
           review_notes = p_review_notes
    WHERE  draft_id = p_draft_id AND status = 'pending';

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Draft % not found or not in pending status', p_draft_id;
    END IF;
END;
$$;

-- =============================================================
-- SEED DATA — Users
-- =============================================================

INSERT INTO cadaster.users (username, role) VALUES
    ('admin_manager', 'manager'),
    ('field_editor',  'editor');

-- =============================================================
-- SEED DATA — Initial 3 Parcels (from initial_cadastre_poc.shp)
-- Source CRS: EPSG:4326 (already correct); converted Polygon → MultiPolygon
-- =============================================================

INSERT INTO cadaster.parcels (parcel_id, geom, owner_name, valid_from, created_by, notes)
VALUES
(
    1,
    ST_Multi(ST_GeomFromText(
        'POLYGON((-13.2840433059763 8.49314053759932,
                  -13.2839187389723 8.4930442868478,
                  -13.2839946469904 8.49294803607213,
                  -13.2841133749161 8.49304813687832,
                  -13.2840433059763 8.49314053759932))',
        4326
    )),
    'Unknown',
    '2026-05-27',
    1,
    'Initial cadastre import'
),
(
    2,
    ST_Multi(ST_GeomFromText(
        'POLYGON((-13.2841192139945 8.49302888672531,
                  -13.2839965933498 8.49293648597742,
                  -13.2840510914141 8.49286333536956,
                  -13.2841834438559 8.49297498629175,
                  -13.2841192139945 8.49302888672531))',
        4326
    )),
    'Unknown',
    '2026-05-27',
    1,
    'Initial cadastre import'
),
(
    3,
    ST_Multi(ST_GeomFromText(
        'POLYGON((-13.2833309384218 8.49291338578697,
                  -13.2832375131688 8.49284408520728,
                  -13.2832881185142 8.49275360943163,
                  -13.2833951682833 8.49282483504403,
                  -13.2833309384218 8.49291338578697))',
        4326
    )),
    'Unknown',
    '2026-05-27',
    1,
    'Initial cadastre import'
);
