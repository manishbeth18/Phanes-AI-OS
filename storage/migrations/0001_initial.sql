-- Migration 0001: initial Day 3 schema.
-- Applied by storage.database.Database.migrate(), which records this version
-- in schema_migrations. Do not edit after it has been shared; add a new
-- numbered migration instead.
--
-- Conventions: TEXT UUID primary keys, ISO-8601 UTC TEXT timestamps,
-- enum columns constrained with CHECK to the values in shared/enums.py.

CREATE TABLE schema_migrations (
    version    INTEGER PRIMARY KEY CHECK (version > 0),
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE workspaces (
    workspace_id   TEXT PRIMARY KEY,
    root_path      TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    last_opened_at TEXT NOT NULL
);

CREATE TABLE tasks (
    task_id      TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL
                 REFERENCES workspaces (workspace_id) ON DELETE RESTRICT,
    goal         TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN (
                     'pending', 'planning', 'running', 'waiting_for_approval',
                     'completed', 'failed', 'cancelled')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX idx_tasks_status ON tasks (status);
CREATE INDEX idx_tasks_workspace_created ON tasks (workspace_id, created_at);

CREATE TABLE plan_steps (
    step_id           TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL
                      REFERENCES tasks (task_id) ON DELETE CASCADE,
    position          INTEGER NOT NULL CHECK (position >= 0),
    tool              TEXT NOT NULL,
    arguments_json    TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN (
                          'pending', 'running', 'waiting_for_approval',
                          'completed', 'failed', 'skipped', 'cancelled',
                          'unknown')),
    depends_on_json   TEXT NOT NULL DEFAULT '[]',
    requires_approval INTEGER NOT NULL DEFAULT 0
                      CHECK (requires_approval IN (0, 1)),
    UNIQUE (task_id, position),
    -- Lets tool_executions reference (task_id, step_id) as a pair, so an
    -- execution can never point at a step that belongs to another task.
    UNIQUE (task_id, step_id)
);

CREATE INDEX idx_plan_steps_task_status ON plan_steps (task_id, status);

CREATE TABLE tool_executions (
    execution_id         TEXT PRIMARY KEY,
    task_id              TEXT NOT NULL,
    step_id              TEXT NOT NULL,
    attempt_number       INTEGER NOT NULL CHECK (attempt_number >= 1),
    status               TEXT NOT NULL CHECK (status IN (
                             'running', 'completed', 'failed', 'cancelled',
                             'unknown')),
    started_at           TEXT NOT NULL,
    finished_at          TEXT,
    -- NULL means "no outcome recorded". Never defaulted to success.
    success              INTEGER CHECK (success IN (0, 1)),
    verified             INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    verification_details TEXT,
    output               TEXT NOT NULL DEFAULT '',
    exit_code            INTEGER,
    error_code           TEXT,
    error_json           TEXT,
    -- UNIQUE (step_id, attempt_number) also serves as the index on step_id.
    UNIQUE (step_id, attempt_number),
    -- RESTRICT: execution history is never deleted silently.
    FOREIGN KEY (task_id, step_id)
        REFERENCES plan_steps (task_id, step_id) ON DELETE RESTRICT,
    -- verified = 1 requires a description of the check that was performed.
    CHECK (verified = 0
           OR (verification_details IS NOT NULL
               AND length(trim(verification_details)) > 0)),
    -- A running attempt has no outcome yet.
    CHECK (status != 'running'
           OR (finished_at IS NULL AND success IS NULL AND verified = 0))
);

CREATE INDEX idx_tool_executions_task_started
    ON tool_executions (task_id, started_at);
