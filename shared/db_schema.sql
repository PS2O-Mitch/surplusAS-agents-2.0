-- surplusAS-agents-2.0 schema bootstrap
--
-- All tables live in the `agents` schema inside the existing `surplusas`
-- Cloud SQL Postgres instance. Cross-schema relationships to `public.partner_keys`,
-- `public.pricing_coefficients`, and `public.reference_prices` are validated in
-- application code (no hard FKs across schemas) so this repo can be deployed
-- independently of `surplusAS-API-2.0` and `surplusAS-pricing-intel`.

CREATE SCHEMA IF NOT EXISTS agents;

-- Required for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- merchant_profiles: written by Onboarding, read by every other agent.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents.merchant_profiles (
    merchant_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id           TEXT NOT NULL,                              -- logical FK -> public.partner_keys
    merchant_name        TEXT NOT NULL,
    region               TEXT NOT NULL,                              -- e.g. US-FL-Hillsborough
    merchant_floor_pct   NUMERIC(4,3) NOT NULL DEFAULT 0.10
                           CHECK (merchant_floor_pct BETWEEN 0 AND 1),
    allowed_categories   TEXT[] NOT NULL,
    timezone             TEXT NOT NULL DEFAULT 'America/New_York',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS merchant_profiles_partner_idx
    ON agents.merchant_profiles (partner_id);

-- ---------------------------------------------------------------------------
-- listings: written by Listing Intake; status reflects whether pricing was
-- computable at intake. Every listing is born with a recommendation attached.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents.listings (
    listing_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id          UUID NOT NULL REFERENCES agents.merchant_profiles(merchant_id),
    partner_id           TEXT NOT NULL,
    title                TEXT NOT NULL,
    description          TEXT,
    category             TEXT NOT NULL,
    units                INT  NOT NULL CHECK (units >= 1),
    retail_value         NUMERIC(10,2) NOT NULL CHECK (retail_value > 0),
    hours_until_expiry   NUMERIC(6,2) NOT NULL,
    image_uri            TEXT,
    status               TEXT NOT NULL CHECK (status IN
                            ('draft','draft_no_price','published','withdrawn','sold')),
    initial_recommendation_id  UUID NOT NULL,
    current_recommendation_id  UUID NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS listings_merchant_idx ON agents.listings (merchant_id);
CREATE INDEX IF NOT EXISTS listings_partner_status_idx
    ON agents.listings (partner_id, status);

-- ---------------------------------------------------------------------------
-- recommendation_log: APPEND-ONLY by convention. The source of truth Dispute
-- Triage reads to recover the original applied_pressures map. Re-derivations
-- write a new row with replay_of set to the original id.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents.recommendation_log (
    recommendation_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id           UUID,
    merchant_id          UUID,
    partner_id           TEXT NOT NULL,
    pricing_input        JSONB NOT NULL,
    recommended_price    NUMERIC(10,2) NOT NULL,
    recommended_discount_pct NUMERIC(5,4) NOT NULL,
    anchor_p50           NUMERIC(10,2) NOT NULL,
    anchor_source        TEXT NOT NULL,
    anchor_region        TEXT NOT NULL,
    applied_pressures    JSONB NOT NULL,
    formula_version      TEXT NOT NULL,
    coefficients_version INT NOT NULL,
    replay_of            UUID,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS recommendation_log_listing_idx
    ON agents.recommendation_log (listing_id, created_at DESC);
CREATE INDEX IF NOT EXISTS recommendation_log_partner_idx
    ON agents.recommendation_log (partner_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- disputes: written by Dispute Triage. Captures both pressure maps + per-key diff.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents.disputes (
    dispute_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id           UUID NOT NULL REFERENCES agents.listings(listing_id),
    merchant_id          UUID NOT NULL REFERENCES agents.merchant_profiles(merchant_id),
    partner_id           TEXT NOT NULL,
    reason_text          TEXT NOT NULL,
    original_recommendation_id UUID NOT NULL,
    new_recommendation_id      UUID NOT NULL,
    pressure_diff        JSONB NOT NULL,
    resolution           TEXT NOT NULL DEFAULT 'pending'
                           CHECK (resolution IN ('pending','accepted','rejected','withdrawn')),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS disputes_listing_idx ON agents.disputes (listing_id);

-- ---------------------------------------------------------------------------
-- webhook_subscriptions + webhook_deliveries: the customer-facing async surface.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents.webhook_subscriptions (
    subscription_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id           TEXT NOT NULL,
    url                  TEXT NOT NULL,
    events               TEXT[] NOT NULL,
    secret_version       INT  NOT NULL DEFAULT 1,
    secret_hash          TEXT NOT NULL,                              -- argon2id of HMAC secret
    active               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS webhook_subs_partner_idx
    ON agents.webhook_subscriptions (partner_id) WHERE active;

CREATE TABLE IF NOT EXISTS agents.webhook_deliveries (
    delivery_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subscription_id      UUID NOT NULL REFERENCES agents.webhook_subscriptions(subscription_id),
    event_type           TEXT NOT NULL,
    payload              JSONB NOT NULL,
    attempt              INT  NOT NULL DEFAULT 0,
    last_status_code     INT,
    last_error           TEXT,
    delivered_at         TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS webhook_deliveries_pending_idx
    ON agents.webhook_deliveries (subscription_id, created_at)
    WHERE delivered_at IS NULL;
