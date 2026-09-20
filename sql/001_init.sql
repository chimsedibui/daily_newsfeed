-- Schema cho article store + trace/logging. Idempotent: chạy lại an toàn.
CREATE SCHEMA IF NOT EXISTS news;
SET search_path TO news, public;

-- ========== 1. Article store ==========
CREATE TABLE IF NOT EXISTS article (
    id              BIGSERIAL PRIMARY KEY,
    url_canonical   TEXT        NOT NULL UNIQUE,
    url_original    TEXT        NOT NULL,
    source_id       TEXT        NOT NULL,
    publisher       TEXT        NOT NULL,
    category        TEXT,
    title           TEXT        NOT NULL,
    lead            TEXT,
    body            TEXT,
    author          TEXT,
    lang            TEXT        DEFAULT 'vi',
    published_at    TIMESTAMPTZ,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash    TEXT        NOT NULL,
    simhash         BIGINT      NOT NULL,
    image_url       TEXT,
    raw             JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_article_published  ON article (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_article_source     ON article (source_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_article_simhash    ON article (simhash);

-- ========== 2. Orchestration / trace ==========
CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id          UUID        PRIMARY KEY,
    logical_date    DATE        NOT NULL,
    trigger         TEXT        NOT NULL DEFAULT 'manual',   -- airflow | manual | backfill
    dag_run_id      TEXT,
    status          TEXT        NOT NULL DEFAULT 'running',  -- running | success | failed | partial
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    duration_ms     INTEGER,
    config_snapshot JSONB       NOT NULL DEFAULT '{}'::jsonb,
    metrics         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_date ON pipeline_run (logical_date DESC, started_at DESC);

-- 1 span = 1 node LangGraph (hoặc 1 sub-step). parent_span_id cho phép dựng cây.
CREATE TABLE IF NOT EXISTS node_span (
    span_id         UUID        PRIMARY KEY,
    run_id          UUID        NOT NULL REFERENCES pipeline_run(run_id) ON DELETE CASCADE,
    parent_span_id  UUID        REFERENCES node_span(span_id) ON DELETE CASCADE,
    name            TEXT        NOT NULL,
    kind            TEXT        NOT NULL DEFAULT 'node',     -- node | llm | http | db
    status          TEXT        NOT NULL DEFAULT 'running',
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    duration_ms     INTEGER,
    attributes      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    input_preview   JSONB,
    output_preview  JSONB,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_span_run ON node_span (run_id, started_at);

CREATE TABLE IF NOT EXISTS llm_call (
    id                BIGSERIAL PRIMARY KEY,
    run_id            UUID      NOT NULL REFERENCES pipeline_run(run_id) ON DELETE CASCADE,
    span_id           UUID      REFERENCES node_span(span_id) ON DELETE SET NULL,
    provider          TEXT      NOT NULL DEFAULT 'anthropic',
    model             TEXT      NOT NULL,
    purpose           TEXT,                                   -- summarize | rank | compose
    input_tokens      INTEGER   DEFAULT 0,
    output_tokens     INTEGER   DEFAULT 0,
    cache_read_tokens INTEGER   DEFAULT 0,
    cost_usd          NUMERIC(12,6) DEFAULT 0,
    latency_ms        INTEGER,
    prompt_preview    TEXT,
    output_preview    TEXT,
    error             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_llm_run ON llm_call (run_id, created_at);

-- Log có cấu trúc, ghi cùng transaction với pipeline (structlog -> đây).
CREATE TABLE IF NOT EXISTS app_log (
    id         BIGSERIAL PRIMARY KEY,
    run_id     UUID REFERENCES pipeline_run(run_id) ON DELETE CASCADE,
    span_id    UUID,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    level      TEXT NOT NULL,
    logger     TEXT,
    event      TEXT NOT NULL,
    context    JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_log_run ON app_log (run_id, ts);

-- Sức khoẻ từng nguồn theo từng run -> phát hiện feed chết/đổi URL.
CREATE TABLE IF NOT EXISTS source_health (
    id          BIGSERIAL PRIMARY KEY,
    run_id      UUID NOT NULL REFERENCES pipeline_run(run_id) ON DELETE CASCADE,
    source_id   TEXT NOT NULL,
    ok          BOOLEAN NOT NULL,
    http_status INTEGER,
    items_found INTEGER DEFAULT 0,
    items_new   INTEGER DEFAULT 0,
    latency_ms  INTEGER,
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_health_source ON source_health (source_id, created_at DESC);

-- ========== 3. Output ==========
CREATE TABLE IF NOT EXISTS article_summary (
    id          BIGSERIAL PRIMARY KEY,
    article_id  BIGINT NOT NULL REFERENCES article(id) ON DELETE CASCADE,
    run_id      UUID   NOT NULL REFERENCES pipeline_run(run_id) ON DELETE CASCADE,
    model       TEXT   NOT NULL,
    summary     TEXT   NOT NULL,
    bullets     JSONB  NOT NULL DEFAULT '[]'::jsonb,
    topics      JSONB  NOT NULL DEFAULT '[]'::jsonb,
    importance  SMALLINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (article_id, run_id)
);

CREATE TABLE IF NOT EXISTS digest (
    id            BIGSERIAL PRIMARY KEY,
    run_id        UUID NOT NULL REFERENCES pipeline_run(run_id) ON DELETE CASCADE,
    digest_date   DATE NOT NULL,
    channel       TEXT NOT NULL DEFAULT 'google_chat',
    headline      TEXT,
    overview      TEXT,
    payload       JSONB NOT NULL,          -- body gửi Google Chat (cardsV2)
    article_ids   JSONB NOT NULL DEFAULT '[]'::jsonb,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | sent | failed | skipped
    sent_at       TIMESTAMPTZ,
    message_name  TEXT,                    -- spaces/.../messages/... do Google trả về
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_digest_date ON digest (digest_date DESC);

-- ========== 4. View tiện cho quan sát ==========
CREATE OR REPLACE VIEW v_run_overview AS
SELECT r.run_id, r.logical_date, r.status, r.started_at, r.duration_ms,
       (r.metrics->>'articles_collected')::int  AS articles_collected,
       (r.metrics->>'articles_selected')::int   AS articles_selected,
       (SELECT count(*) FROM node_span s WHERE s.run_id = r.run_id) AS spans,
       (SELECT coalesce(sum(cost_usd),0) FROM llm_call l WHERE l.run_id = r.run_id) AS llm_cost_usd,
       (SELECT coalesce(sum(input_tokens+output_tokens),0) FROM llm_call l WHERE l.run_id = r.run_id) AS llm_tokens,
       (SELECT count(*) FROM source_health h WHERE h.run_id = r.run_id AND NOT h.ok) AS failed_sources
FROM pipeline_run r;
