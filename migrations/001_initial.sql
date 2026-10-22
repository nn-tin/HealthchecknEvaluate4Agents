CREATE TABLE IF NOT EXISTS scheduled_batches (
    batch_id text PRIMARY KEY,
    environment text NOT NULL,
    scheduled_for timestamptz NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    expected_agents text[] NOT NULL,
    status text NOT NULL DEFAULT 'running',
    UNIQUE(environment, scheduled_for)
);
CREATE TABLE IF NOT EXISTS schedule_settings (
    environment text PRIMARY KEY,
    timezone text NOT NULL,
    slots time[] NOT NULL,
    weekdays integer[] NOT NULL,
    agents text[] NOT NULL,
    grace_minutes integer NOT NULL
);
CREATE TABLE IF NOT EXISTS scheduled_evaluation_batches (
    batch_id text PRIMARY KEY,
    environment text NOT NULL,
    scheduled_for timestamptz NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    expected_cases text[] NOT NULL,
    status text NOT NULL DEFAULT 'running',
    UNIQUE(environment, scheduled_for)
);
CREATE TABLE IF NOT EXISTS evaluation_schedule_settings (
    environment text PRIMARY KEY,
    timezone text NOT NULL,
    slots time[] NOT NULL,
    weekdays integer[] NOT NULL,
    agents text[] NOT NULL,
    cases text[] NOT NULL DEFAULT ARRAY[]::text[],
    grace_minutes integer NOT NULL
);
ALTER TABLE evaluation_schedule_settings
    ADD COLUMN IF NOT EXISTS cases text[] NOT NULL DEFAULT ARRAY[]::text[];
CREATE TABLE IF NOT EXISTS runs (
    run_id text PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('healthcheck','evaluation')),
    agent text NOT NULL,
    environment text NOT NULL,
    case_id text,
    batch_id text,
    trigger text NOT NULL,
    scheduled_for timestamptz,
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    status text NOT NULL,
    verdict text,
    score double precision,
    duration_ms double precision NOT NULL,
    first_event_ms double precision,
    ttft_ms double precision,
    stream_complete boolean,
    error text,
    conversation_id text,
    dify_log_url text,
    result_file text
);
ALTER TABLE runs ADD COLUMN IF NOT EXISTS conversation_id text;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS dify_log_url text;
CREATE INDEX IF NOT EXISTS runs_time ON runs(started_at DESC);
CREATE INDEX IF NOT EXISTS runs_agent_time ON runs(environment, agent, started_at DESC);
CREATE INDEX IF NOT EXISTS runs_batch ON runs(batch_id);
CREATE TABLE IF NOT EXISTS metric_results (
    run_id text REFERENCES runs ON DELETE CASCADE,
    name text NOT NULL,
    score double precision,
    threshold double precision,
    success boolean,
    status text NOT NULL,
    reason text,
    error text,
    PRIMARY KEY(run_id, name)
);
CREATE TABLE IF NOT EXISTS rule_results (
    run_id text REFERENCES runs ON DELETE CASCADE,
    name text NOT NULL,
    success boolean NOT NULL,
    critical boolean NOT NULL,
    reason text,
    PRIMARY KEY(run_id, name)
);
CREATE TABLE IF NOT EXISTS tool_calls (
    run_id text REFERENCES runs ON DELETE CASCADE,
    turn_index integer NOT NULL,
    tool_index integer NOT NULL,
    name text NOT NULL,
    status text NOT NULL,
    started_at timestamptz,
    ended_at timestamptz,
    duration_ms double precision,
    timing_source text NOT NULL,
    PRIMARY KEY(run_id, turn_index, tool_index)
);
CREATE TABLE IF NOT EXISTS transcript_turns (
    run_id text REFERENCES runs ON DELETE CASCADE,
    turn_index integer NOT NULL,
    role text NOT NULL,
    content text NOT NULL,
    latency_ms double precision,
    PRIMARY KEY(run_id, turn_index)
);
CREATE OR REPLACE VIEW batch_progress AS
SELECT b.*, count(DISTINCT r.agent) FILTER (WHERE r.agent = ANY(b.expected_agents))::int AS completed_agents,
       count(DISTINCT r.agent) FILTER (WHERE r.status = 'Error' AND r.agent = ANY(b.expected_agents))::int AS failed_agents,
       cardinality(b.expected_agents) AS expected_count
FROM scheduled_batches b LEFT JOIN runs r ON r.batch_id = b.batch_id AND r.kind='healthcheck'
GROUP BY b.batch_id;
-- Generate expected slots in the configured timezone, including days the scheduler was offline.
CREATE OR REPLACE VIEW expected_batches AS
SELECT s.environment, s.agents, s.grace_minutes,
       (d.day::date + slot.value) AT TIME ZONE s.timezone AS scheduled_for
FROM schedule_settings s
CROSS JOIN LATERAL generate_series(
    (now() AT TIME ZONE s.timezone)::date - 30,
    (now() AT TIME ZONE s.timezone)::date, interval '1 day') AS d(day)
CROSS JOIN LATERAL unnest(s.slots) AS slot(value)
WHERE extract(isodow FROM d.day)::int = ANY(s.weekdays);
CREATE OR REPLACE VIEW evaluation_batch_progress AS
SELECT b.*,
       count(DISTINCT r.case_id) FILTER (WHERE r.case_id = ANY(b.expected_cases))::int AS completed_cases,
       count(DISTINCT r.case_id) FILTER (
           WHERE r.case_id = ANY(b.expected_cases) AND (r.status='error' OR r.verdict='ERROR')
       )::int AS failed_cases,
       cardinality(b.expected_cases) AS expected_count
FROM scheduled_evaluation_batches b
LEFT JOIN runs r ON r.batch_id=b.batch_id AND r.kind='evaluation'
GROUP BY b.batch_id;
CREATE OR REPLACE VIEW expected_evaluation_batches AS
SELECT s.environment, s.agents, s.cases, s.grace_minutes,
       (d.day::date + slot.value) AT TIME ZONE s.timezone AS scheduled_for
FROM evaluation_schedule_settings s
CROSS JOIN LATERAL generate_series(
    (now() AT TIME ZONE s.timezone)::date - 30,
    (now() AT TIME ZONE s.timezone)::date, interval '1 day') AS d(day)
CROSS JOIN LATERAL unnest(s.slots) AS slot(value)
WHERE extract(isodow FROM d.day)::int = ANY(s.weekdays);
