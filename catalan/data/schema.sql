-- CATALAN local store. SQLite; lives at catalan/data/catalan.db (gitignored).
--
-- This is the ONLY database CATALAN writes to. Turso is read-only from here
-- (write quota exhausted repo-wide 2026-08-05) and promotion of final tables
-- to Turso is an explicit, separate export step.
--
-- DuckDB can ATTACH this file READ_ONLY for columnar analysis later without
-- migrating anything, which is why the ingest path gets to stay plain sqlite3.

PRAGMA journal_mode = WAL;

-- ── Raw corpus ───────────────────────────────────────────────────────────────
-- Deliberately unfiltered. Category exclusions and near-duplicate dedup are
-- applied at QUERY time, never at ingest, so the corpus stays auditable and a
-- change to the exclusion list does not require a refetch.
CREATE TABLE IF NOT EXISTS announcements (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    isin          TEXT,
    company       TEXT,
    announced_at  TEXT NOT NULL,     -- ISO 8601, second precision, IST
    headline      TEXT NOT NULL,     -- NSE `attchmntText`
    category      TEXT,              -- NSE `desc` — the vendor topic taxonomy
    industry      TEXT,              -- NSE `smIndustry`
    seq_id        TEXT NOT NULL,     -- NSE row identity, for idempotent re-runs
    source        TEXT NOT NULL,     -- 'nse_announcements' | 'pulse'
    ingested_at   TEXT NOT NULL,
    UNIQUE(symbol, seq_id)
);

CREATE INDEX IF NOT EXISTS idx_ann_announced_at ON announcements(announced_at);
CREATE INDEX IF NOT EXISTS idx_ann_symbol_date  ON announcements(symbol, announced_at);
CREATE INDEX IF NOT EXISTS idx_ann_category     ON announcements(category);

-- ── Scores ───────────────────────────────────────────────────────────────────
-- Append-only. Re-scoring under a changed prompt or model writes NEW rows; it
-- never overwrites. The primary key is the provenance tuple, so a re-run under
-- identical provenance is idempotent and a re-run under changed provenance is
-- a new, separately-analysable arm.
CREATE TABLE IF NOT EXISTS scores (
    announcement_id INTEGER NOT NULL REFERENCES announcements(id),
    model_id        TEXT NOT NULL,
    prompt_hash     TEXT NOT NULL,
    effort          TEXT,
    score           INTEGER,          -- +1 YES / 0 UNKNOWN / -1 NO
    rationale       TEXT,
    run_id          TEXT NOT NULL,
    scored_at       TEXT NOT NULL,
    PRIMARY KEY (announcement_id, model_id, prompt_hash)
);

CREATE INDEX IF NOT EXISTS idx_scores_run ON scores(run_id);

-- Message Batches API submissions, so a 250k-row historical scoring job can be
-- resumed after a crash without re-submitting (and re-paying for) work that is
-- already queued at Anthropic. The daily forward run uses the sync transport
-- instead and never touches this table.
CREATE TABLE IF NOT EXISTS score_batches (
    batch_id        TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    prompt_hash     TEXT NOT NULL,
    announcement_ids TEXT NOT NULL,   -- JSON array, custom_id → announcement id
    n_requests      INTEGER NOT NULL,
    status          TEXT NOT NULL,    -- 'submitted' | 'collected' | 'error'
    submitted_at    TEXT NOT NULL,
    collected_at    TEXT
);

-- ── Returns panel ────────────────────────────────────────────────────────────
-- Built from Turso price_history (read-only) joined against the PIT roster.
-- init_ret is the untradable comprehension leg; drift_ret is the tradable one.
CREATE TABLE IF NOT EXISTS panel (
    symbol           TEXT NOT NULL,
    date             TEXT NOT NULL,
    prev_close       REAL,
    open             REAL,
    close            REAL,
    init_ret         REAL,            -- close(t-1) → open(t).  NOT tradable.
    drift_ret        REAL,            -- open(t)    → close(t). The tradable leg.
    in_universe      INTEGER NOT NULL DEFAULT 0,
    adv_20d          REAL,
    liquidity_bucket INTEGER,
    shortable        INTEGER,         -- 0 when T2T / ASM / GSM bans intraday shorts
    PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_panel_date ON panel(date);

-- ── Ingest bookkeeping ───────────────────────────────────────────────────────
-- A silently-dead forward collector is the main failure mode of this study:
-- the forward log accrues exactly one day per calendar day and a missed day is
-- permanently lost. Every collector run logs here so the UI can show gaps.
CREATE TABLE IF NOT EXISTS ingest_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    window_from   TEXT NOT NULL,
    window_to     TEXT NOT NULL,
    rows_fetched  INTEGER NOT NULL DEFAULT 0,
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,      -- 'ok' | 'error'
    detail        TEXT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_window ON ingest_runs(window_from, window_to);
