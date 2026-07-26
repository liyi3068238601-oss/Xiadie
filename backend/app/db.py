"""SQLite 存储层：会话、消息、记忆、任务、供应商、设置、工具日志。

本地优先原则：所有数据保存在 backend/data/xiadie.db。
"""
import json
import os
import sqlite3
import time
import uuid

DEFAULT_MEMORY_ENABLED = "1"

DATA_DIR = os.environ.get(
    "XIADIE_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)
DB_PATH = os.path.join(DATA_DIR, "xiadie.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '新对话',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT,
    favorite INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo','doing','done','archived')),
    due_date TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    source_session_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL DEFAULT '',
    api_key TEXT NOT NULL DEFAULT '',
    models TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 0,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_logs (
    id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    risk_level TEXT NOT NULL DEFAULT 'S0',
    status TEXT NOT NULL DEFAULT 'done',
    summary TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
"""

MIGRATIONS = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS companion_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            connection REAL NOT NULL CHECK(connection BETWEEN 0 AND 1),
            pride REAL NOT NULL CHECK(pride BETWEEN -1 AND 1),
            valence REAL NOT NULL CHECK(valence BETWEEN -1 AND 1),
            arousal REAL NOT NULL CHECK(arousal BETWEEN -1 AND 1),
            immersion REAL NOT NULL CHECK(immersion BETWEEN 0 AND 1),
            updated_at REAL NOT NULL
        );
        """,
    ),
    (
        2,
        """
        DROP TABLE IF EXISTS memories;

        CREATE TABLE IF NOT EXISTS memory_fragments (
            id TEXT PRIMARY KEY,
            layer TEXT NOT NULL CHECK(layer IN ('L0','L1','L2')),
            content TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            source_session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
            source_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0 AND 1),
            sensitivity TEXT NOT NULL DEFAULT 'normal'
                CHECK(sensitivity IN ('normal','sensitive')),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','cooling','frozen','tombstone')),
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_fragments_active
            ON memory_fragments(status, enabled, layer, updated_at);
        CREATE INDEX IF NOT EXISTS idx_memory_fragments_source
            ON memory_fragments(source_session_id, source_message_id);

        CREATE TABLE IF NOT EXISTS memory_candidates (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            proposed_layer TEXT NOT NULL CHECK(proposed_layer IN ('L0','L1','L2')),
            tags TEXT NOT NULL DEFAULT '',
            source_session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
            source_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
            sensitivity TEXT NOT NULL DEFAULT 'normal'
                CHECK(sensitivity IN ('normal','sensitive')),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','accepted','rejected')),
            resolved_memory_id TEXT REFERENCES memory_fragments(id) ON DELETE SET NULL,
            resolution_note TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            resolved_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_candidates_status
            ON memory_candidates(status, created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_candidates_source_content
            ON memory_candidates(source_message_id, content);

        CREATE TABLE IF NOT EXISTS memory_entities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT 'concept',
            summary TEXT NOT NULL DEFAULT '',
            aliases TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_entities_name_type
            ON memory_entities(name, entity_type);

        CREATE TABLE IF NOT EXISTS memory_fragment_entities (
            fragment_id TEXT NOT NULL REFERENCES memory_fragments(id) ON DELETE CASCADE,
            entity_id TEXT NOT NULL REFERENCES memory_entities(id) ON DELETE CASCADE,
            relation TEXT NOT NULL DEFAULT 'mentions',
            created_at REAL NOT NULL,
            PRIMARY KEY(fragment_id, entity_id, relation)
        );

        CREATE TABLE IF NOT EXISTS memory_events (
            id TEXT PRIMARY KEY,
            object_type TEXT NOT NULL CHECK(object_type IN ('candidate','fragment','entity')),
            object_id TEXT NOT NULL,
            action TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            source TEXT NOT NULL DEFAULT 'system',
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_events_object
            ON memory_events(object_type, object_id, created_at);
        """,
    ),
    (
        3,
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fragments_fts USING fts5(
            content,
            tags,
            content='memory_fragments',
            content_rowid='rowid',
            tokenize='trigram'
        );

        CREATE TRIGGER IF NOT EXISTS memory_fragments_fts_insert
        AFTER INSERT ON memory_fragments BEGIN
            INSERT INTO memory_fragments_fts(rowid, content, tags)
            VALUES (new.rowid, new.content, new.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS memory_fragments_fts_delete
        AFTER DELETE ON memory_fragments BEGIN
            INSERT INTO memory_fragments_fts(memory_fragments_fts, rowid, content, tags)
            VALUES ('delete', old.rowid, old.content, old.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS memory_fragments_fts_update
        AFTER UPDATE OF content, tags ON memory_fragments BEGIN
            INSERT INTO memory_fragments_fts(memory_fragments_fts, rowid, content, tags)
            VALUES ('delete', old.rowid, old.content, old.tags);
            INSERT INTO memory_fragments_fts(rowid, content, tags)
            VALUES (new.rowid, new.content, new.tags);
        END;

        INSERT INTO memory_fragments_fts(memory_fragments_fts) VALUES('rebuild');
        """,
    ),
    (
        4,
        """
        ALTER TABLE memory_entities ADD COLUMN tags TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE memory_entities ADD COLUMN current_status TEXT NOT NULL DEFAULT '';
        ALTER TABLE memory_entities ADD COLUMN status_since TEXT NOT NULL DEFAULT '';
        ALTER TABLE memory_entities ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
        ALTER TABLE memory_entities ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
        ALTER TABLE memory_entities ADD COLUMN merged_into_id TEXT;
        ALTER TABLE memory_fragment_entities ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0;
        CREATE INDEX IF NOT EXISTS idx_memory_entities_status_type
            ON memory_entities(status, entity_type, updated_at);
        """,
    ),
    (
        5,
        """
        CREATE TABLE IF NOT EXISTS memory_episodes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            start_at REAL NOT NULL,
            end_at REAL NOT NULL,
            significance INTEGER NOT NULL DEFAULT 4 CHECK(significance BETWEEN 1 AND 10),
            confidence REAL NOT NULL DEFAULT 0.7 CHECK(confidence BETWEEN 0 AND 1),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','archived','tombstone')),
            source TEXT NOT NULL DEFAULT 'candidate_confirmed',
            candidate_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_episodes_status_time
            ON memory_episodes(status, end_at DESC);

        CREATE TABLE IF NOT EXISTS memory_episode_fragments (
            episode_id TEXT NOT NULL REFERENCES memory_episodes(id) ON DELETE CASCADE,
            fragment_id TEXT NOT NULL REFERENCES memory_fragments(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            PRIMARY KEY(episode_id, fragment_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_episode_fragment_single_active
            ON memory_episode_fragments(fragment_id);

        CREATE TABLE IF NOT EXISTS memory_episode_entities (
            episode_id TEXT NOT NULL REFERENCES memory_episodes(id) ON DELETE CASCADE,
            entity_id TEXT NOT NULL REFERENCES memory_entities(id) ON DELETE CASCADE,
            relation TEXT NOT NULL DEFAULT 'involves',
            created_at REAL NOT NULL,
            PRIMARY KEY(episode_id, entity_id)
        );

        CREATE TABLE IF NOT EXISTS memory_episode_candidates (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            start_at REAL NOT NULL,
            end_at REAL NOT NULL,
            significance INTEGER NOT NULL DEFAULT 4 CHECK(significance BETWEEN 1 AND 10),
            confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','accepted','rejected')),
            grouping_key TEXT NOT NULL UNIQUE,
            resolved_episode_id TEXT REFERENCES memory_episodes(id) ON DELETE SET NULL,
            resolution_note TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            resolved_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_episode_candidates_status
            ON memory_episode_candidates(status, created_at DESC);

        CREATE TABLE IF NOT EXISTS memory_episode_candidate_fragments (
            candidate_id TEXT NOT NULL REFERENCES memory_episode_candidates(id) ON DELETE CASCADE,
            fragment_id TEXT NOT NULL REFERENCES memory_fragments(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(candidate_id, fragment_id)
        );

        DROP INDEX IF EXISTS idx_memory_events_object;
        ALTER TABLE memory_events RENAME TO memory_events_v4;
        CREATE TABLE memory_events (
            id TEXT PRIMARY KEY,
            object_type TEXT NOT NULL CHECK(object_type IN (
                'candidate','fragment','entity','episode_candidate','episode'
            )),
            object_id TEXT NOT NULL,
            action TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            source TEXT NOT NULL DEFAULT 'system',
            created_at REAL NOT NULL
        );
        INSERT INTO memory_events
            (id, object_type, object_id, action, before_json, after_json, source, created_at)
        SELECT id, object_type, object_id, action, before_json, after_json, source, created_at
        FROM memory_events_v4;
        DROP TABLE memory_events_v4;
        CREATE INDEX idx_memory_events_object
            ON memory_events(object_type, object_id, created_at);
        """,
    ),
    (
        6,
        """
        DROP INDEX IF EXISTS idx_memory_entities_name_type;
        CREATE UNIQUE INDEX idx_memory_entities_active_name_type
            ON memory_entities(name, entity_type) WHERE status='active';
        """,
    ),
    (
        7,
        """
        CREATE TABLE IF NOT EXISTS affect_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            contact_need REAL NOT NULL CHECK(contact_need BETWEEN 0 AND 1),
            guardedness_transient REAL NOT NULL
                CHECK(guardedness_transient BETWEEN -0.25 AND 0.25),
            valence REAL NOT NULL CHECK(valence BETWEEN -1 AND 1),
            arousal REAL NOT NULL CHECK(arousal BETWEEN -1 AND 1),
            immersion REAL NOT NULL CHECK(immersion BETWEEN 0 AND 1),
            activity_type TEXT,
            activity_label TEXT,
            activity_started_at REAL,
            last_user_message_at REAL,
            last_tick_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS relationship_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            bond REAL NOT NULL CHECK(bond BETWEEN 0 AND 1),
            trust REAL NOT NULL CHECK(trust BETWEEN 0 AND 1),
            interaction_count INTEGER NOT NULL DEFAULT 0 CHECK(interaction_count >= 0),
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS affect_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            source TEXT NOT NULL,
            source_session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
            source_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            before_json TEXT NOT NULL,
            delta_json TEXT NOT NULL,
            after_json TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            algorithm_version TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_affect_events_created
            ON affect_events(created_at DESC);

        INSERT OR IGNORE INTO affect_state(
            id, contact_need, guardedness_transient, valence, arousal, immersion,
            last_tick_at, updated_at
        )
        SELECT 1, 0.05, 0.0,
               MAX(-1.0, MIN(1.0, valence)),
               MAX(-1.0, MIN(1.0, arousal)),
               MAX(0.0, MIN(1.0, immersion)),
               CAST(strftime('%s','now') AS REAL),
               CAST(strftime('%s','now') AS REAL)
        FROM companion_state WHERE id = 1;

        INSERT OR IGNORE INTO relationship_state(id, bond, trust, interaction_count, updated_at)
        SELECT 1, MAX(0.10, MIN(0.35, connection * 0.5)), 0.25, 0,
               CAST(strftime('%s','now') AS REAL)
        FROM companion_state WHERE id = 1;
        """,
    ),
    (
        8,
        """
        CREATE TABLE IF NOT EXISTS affect_observer_runs (
            id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            source_session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            source_user_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            source_assistant_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            provider_id TEXT,
            model TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK(status IN ('running','candidate','recovery_pending','skipped')),
            candidate_json TEXT,
            warnings_json TEXT NOT NULL DEFAULT '[]',
            error_code TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 1 CHECK(attempt_count BETWEEN 1 AND 3),
            max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 3),
            next_attempt_at REAL,
            input_chars INTEGER NOT NULL DEFAULT 0 CHECK(input_chars >= 0),
            output_chars INTEGER NOT NULL DEFAULT 0 CHECK(output_chars >= 0),
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            protocol_version TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_affect_observer_runs_recovery
            ON affect_observer_runs(status, next_attempt_at, updated_at);
        CREATE INDEX IF NOT EXISTS idx_affect_observer_runs_source
            ON affect_observer_runs(source_session_id, source_assistant_message_id);
        """,
    ),
    (
        9,
        """
        DROP INDEX IF EXISTS idx_affect_observer_runs_recovery;
        DROP INDEX IF EXISTS idx_affect_observer_runs_source;
        ALTER TABLE affect_observer_runs RENAME TO affect_observer_runs_v8;

        CREATE TABLE affect_observer_runs (
            id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            source_session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            source_user_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            source_assistant_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            provider_id TEXT,
            model TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'queued','running','applied','recovery_pending','exhausted','skipped'
            )),
            candidate_json TEXT,
            warnings_json TEXT NOT NULL DEFAULT '[]',
            error_code TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 3),
            max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 3),
            next_attempt_at REAL,
            last_attempt_at REAL,
            applied_event_id TEXT REFERENCES affect_events(id) ON DELETE SET NULL,
            applied_at REAL,
            input_chars INTEGER NOT NULL DEFAULT 0 CHECK(input_chars >= 0),
            output_chars INTEGER NOT NULL DEFAULT 0 CHECK(output_chars >= 0),
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            protocol_version TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        INSERT INTO affect_observer_runs(
            id,idempotency_key,source_session_id,source_user_message_id,
            source_assistant_message_id,provider_id,model,status,candidate_json,warnings_json,
            error_code,attempt_count,max_attempts,next_attempt_at,input_chars,output_chars,
            prompt_tokens,completion_tokens,protocol_version,created_at,updated_at
        )
        SELECT id,idempotency_key,source_session_id,source_user_message_id,
               source_assistant_message_id,provider_id,model,
               CASE status
                   WHEN 'candidate' THEN 'queued'
                   WHEN 'running' THEN 'recovery_pending'
                   ELSE status
               END,
               CASE WHEN status='candidate' THEN NULL ELSE candidate_json END,
               warnings_json,
               CASE WHEN status='running' THEN 'observer_interrupted' ELSE error_code END,
               CASE WHEN status='candidate' THEN 0 ELSE attempt_count END,
               max_attempts,
               CASE WHEN status IN ('candidate','running')
                    THEN CAST(strftime('%s','now') AS REAL) ELSE next_attempt_at END,
               input_chars,output_chars,prompt_tokens,completion_tokens,
               protocol_version,created_at,updated_at
        FROM affect_observer_runs_v8;
        DROP TABLE affect_observer_runs_v8;
        CREATE INDEX idx_affect_observer_runs_recovery
            ON affect_observer_runs(status, next_attempt_at, updated_at);
        CREATE INDEX idx_affect_observer_runs_source
            ON affect_observer_runs(source_session_id, source_assistant_message_id);
        """,
    ),
    (
        10,
        """
        ALTER TABLE memory_fragments ADD COLUMN scope TEXT NOT NULL DEFAULT 'world'
            CHECK(scope IN ('user','self','relationship','world'));
        ALTER TABLE memory_fragments ADD COLUMN kind TEXT NOT NULL DEFAULT 'fact'
            CHECK(kind IN ('fact','preference','plan','experience','relationship','observation','correction'));
        ALTER TABLE memory_fragments ADD COLUMN importance REAL NOT NULL DEFAULT 0.5
            CHECK(importance BETWEEN 0 AND 1);
        ALTER TABLE memory_fragments ADD COLUMN emotion TEXT NOT NULL DEFAULT '';
        ALTER TABLE memory_fragments ADD COLUMN inner_reason TEXT NOT NULL DEFAULT '';
        ALTER TABLE memory_fragments ADD COLUMN observer_version TEXT NOT NULL DEFAULT 'legacy';
        ALTER TABLE memory_fragments ADD COLUMN evidence_message_ids TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE memory_fragments ADD COLUMN source_assistant_message_id TEXT
            REFERENCES messages(id) ON DELETE SET NULL;
        ALTER TABLE memory_fragments ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT '';

        UPDATE memory_fragments
        SET importance = CASE layer WHEN 'L0' THEN 0.90 WHEN 'L1' THEN 0.65 ELSE 0.50 END,
            evidence_message_ids = CASE
                WHEN source_message_id IS NULL THEN '[]'
                ELSE json_array(source_message_id)
            END,
            inner_reason = '迁移自旧版记忆，尚未由自主观察器重新评估';

        CREATE UNIQUE INDEX idx_memory_fragments_observer_idempotency
            ON memory_fragments(idempotency_key) WHERE idempotency_key != '';
        CREATE INDEX idx_memory_fragments_scope_kind
            ON memory_fragments(status, enabled, scope, kind, importance DESC);

        CREATE TABLE memory_observer_runs (
            id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            source_session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            source_user_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            source_assistant_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            provider_id TEXT,
            model TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'queued','running','validated','applied','recovery_pending','exhausted','skipped'
            )),
            candidate_json TEXT,
            warnings_json TEXT NOT NULL DEFAULT '[]',
            error_code TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 3),
            max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 3),
            next_attempt_at REAL,
            last_attempt_at REAL,
            applied_fragment_ids_json TEXT NOT NULL DEFAULT '[]',
            applied_at REAL,
            input_chars INTEGER NOT NULL DEFAULT 0 CHECK(input_chars >= 0),
            output_chars INTEGER NOT NULL DEFAULT 0 CHECK(output_chars >= 0),
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            protocol_version TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_memory_observer_runs_recovery
            ON memory_observer_runs(status, next_attempt_at, updated_at);
        CREATE INDEX idx_memory_observer_runs_source
            ON memory_observer_runs(source_session_id, source_assistant_message_id);
        """,
    ),
    (
        11,
        """
        ALTER TABLE memory_observer_runs ADD COLUMN latency_ms INTEGER
            CHECK(latency_ms IS NULL OR latency_ms >= 0);
        ALTER TABLE memory_observer_runs ADD COLUMN repair_attempted INTEGER NOT NULL DEFAULT 0
            CHECK(repair_attempted IN (0,1));
        """,
    ),
    (
        12,
        """
        ALTER TABLE memory_observer_runs ADD COLUMN created_fragment_ids_json TEXT
            NOT NULL DEFAULT '[]';
        """,
    ),
    (
        13,
        """
        CREATE TABLE episode_consolidator_runs (
            id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            trigger TEXT NOT NULL CHECK(trigger IN ('startup','idle','manual','fragment')),
            status TEXT NOT NULL CHECK(status IN (
                'queued','running','cancel_requested','cancelled','applied',
                'recovery_pending','exhausted','skipped'
            )),
            policy_version TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 3),
            max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 3),
            next_attempt_at REAL,
            started_at REAL,
            finished_at REAL,
            error_code TEXT,
            input_fragment_ids_json TEXT NOT NULL DEFAULT '[]',
            result_episode_ids_json TEXT NOT NULL DEFAULT '[]',
            group_count INTEGER NOT NULL DEFAULT 0 CHECK(group_count >= 0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_episode_consolidator_due
            ON episode_consolidator_runs(status, next_attempt_at, created_at);

        CREATE TABLE episode_consolidator_events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES episode_consolidator_runs(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            before_status TEXT,
            after_status TEXT NOT NULL,
            reason_code TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_episode_consolidator_events_run
            ON episode_consolidator_events(run_id, created_at, id);
        """,
    ),
    (
        14,
        """
        ALTER TABLE memory_episode_candidates ADD COLUMN entity_score REAL NOT NULL DEFAULT 0
            CHECK(entity_score BETWEEN 0 AND 1);
        ALTER TABLE memory_episode_candidates ADD COLUMN text_score REAL NOT NULL DEFAULT 0
            CHECK(text_score BETWEEN 0 AND 1);
        ALTER TABLE memory_episode_candidates ADD COLUMN time_score REAL NOT NULL DEFAULT 0
            CHECK(time_score BETWEEN 0 AND 1);
        ALTER TABLE memory_episode_candidates ADD COLUMN coherence_score REAL NOT NULL DEFAULT 0
            CHECK(coherence_score BETWEEN 0 AND 1);
        ALTER TABLE memory_episode_candidates ADD COLUMN score_details_json TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE memory_episode_candidates ADD COLUMN policy_version TEXT NOT NULL DEFAULT 'legacy';
        ALTER TABLE memory_episode_candidates ADD COLUMN expires_at REAL;
        ALTER TABLE memory_episode_candidates ADD COLUMN last_evaluated_at REAL;

        CREATE TABLE episode_group_candidates (
            id TEXT PRIMARY KEY,
            grouping_fingerprint TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK(status IN ('observing','qualified','superseded','expired')),
            fragment_ids_json TEXT NOT NULL,
            shared_entity_ids_json TEXT NOT NULL,
            entity_score REAL NOT NULL CHECK(entity_score BETWEEN 0 AND 1),
            text_score REAL NOT NULL CHECK(text_score BETWEEN 0 AND 1),
            time_score REAL NOT NULL CHECK(time_score BETWEEN 0 AND 1),
            coherence_score REAL NOT NULL CHECK(coherence_score BETWEEN 0 AND 1),
            total_score REAL NOT NULL CHECK(total_score BETWEEN 0 AND 1),
            evaluation_count INTEGER NOT NULL DEFAULT 1 CHECK(evaluation_count >= 1),
            policy_version TEXT NOT NULL,
            promoted_candidate_id TEXT REFERENCES memory_episode_candidates(id) ON DELETE SET NULL,
            first_seen_at REAL NOT NULL,
            last_evaluated_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE INDEX idx_episode_group_candidates_status_expiry
            ON episode_group_candidates(status, expires_at, last_evaluated_at);
        """,
    ),
    (
        15,
        """
        ALTER TABLE memory_episode_candidates ADD COLUMN summary_status TEXT NOT NULL
            DEFAULT 'legacy_rule' CHECK(summary_status IN (
                'legacy_rule','extractive_fallback','model_validated'
            ));
        ALTER TABLE memory_episode_candidates ADD COLUMN summary_protocol_version TEXT NOT NULL
            DEFAULT 'legacy';
        ALTER TABLE memory_episode_candidates ADD COLUMN summary_provider_id TEXT;
        ALTER TABLE memory_episode_candidates ADD COLUMN summary_model TEXT;
        ALTER TABLE memory_episode_candidates ADD COLUMN summary_evidence_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE memory_episode_candidates ADD COLUMN summary_warnings_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE memory_episode_candidates ADD COLUMN summary_error_code TEXT;
        ALTER TABLE memory_episode_candidates ADD COLUMN summary_source_hash TEXT NOT NULL DEFAULT '';
        ALTER TABLE memory_episode_candidates ADD COLUMN summary_prompt_tokens INTEGER;
        ALTER TABLE memory_episode_candidates ADD COLUMN summary_completion_tokens INTEGER;
        ALTER TABLE memory_episode_candidates ADD COLUMN summary_repair_attempted INTEGER NOT NULL DEFAULT 0
            CHECK(summary_repair_attempted IN (0,1));
        """,
    ),
    (
        16,
        """
        ALTER TABLE memory_episodes ADD COLUMN grouping_fingerprint TEXT;
        ALTER TABLE memory_episodes ADD COLUMN policy_version TEXT NOT NULL DEFAULT 'legacy';
        ALTER TABLE memory_episodes ADD COLUMN source_fragment_ids_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE memory_episodes ADD COLUMN source_hash TEXT NOT NULL DEFAULT '';
        ALTER TABLE memory_episodes ADD COLUMN summary_status TEXT NOT NULL DEFAULT 'legacy_rule'
            CHECK(summary_status IN (
                'legacy_rule','extractive_fallback','model_validated','user_edited'
            ));
        ALTER TABLE memory_episodes ADD COLUMN summary_protocol_version TEXT NOT NULL DEFAULT 'legacy';
        ALTER TABLE memory_episodes ADD COLUMN summary_provider_id TEXT;
        ALTER TABLE memory_episodes ADD COLUMN summary_model TEXT;
        ALTER TABLE memory_episodes ADD COLUMN summary_evidence_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE memory_episodes ADD COLUMN application_version TEXT NOT NULL DEFAULT 'legacy';

        ALTER TABLE memory_episode_candidates ADD COLUMN application_attempt_count INTEGER NOT NULL
            DEFAULT 0 CHECK(application_attempt_count >= 0);
        ALTER TABLE memory_episode_candidates ADD COLUMN application_error_code TEXT;
        ALTER TABLE memory_episode_candidates ADD COLUMN last_application_at REAL;

        CREATE UNIQUE INDEX idx_memory_episodes_candidate_unique
            ON memory_episodes(candidate_id) WHERE candidate_id IS NOT NULL;
        CREATE UNIQUE INDEX idx_memory_episodes_grouping_unique
            ON memory_episodes(grouping_fingerprint) WHERE grouping_fingerprint IS NOT NULL;
        """,
    ),
    (
        17,
        """
        ALTER TABLE memory_episodes ADD COLUMN correction_note TEXT NOT NULL DEFAULT '';
        ALTER TABLE memory_episodes ADD COLUMN corrected_at REAL;
        """,
    ),
    (
        18,
        """
        CREATE TABLE memory_sagas (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 80),
            summary TEXT NOT NULL CHECK(length(trim(summary)) BETWEEN 1 AND 1200),
            theme TEXT NOT NULL DEFAULT '' CHECK(length(theme) <= 80),
            start_at REAL NOT NULL,
            end_at REAL NOT NULL CHECK(end_at >= start_at),
            significance INTEGER NOT NULL DEFAULT 5 CHECK(significance BETWEEN 1 AND 10),
            confidence REAL NOT NULL DEFAULT 0.7 CHECK(confidence BETWEEN 0 AND 1),
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN (
                'active','completed','archived','tombstone'
            )),
            source TEXT NOT NULL DEFAULT 'automatic' CHECK(source IN (
                'automatic','manual','migration'
            )),
            grouping_fingerprint TEXT,
            policy_version TEXT NOT NULL DEFAULT 'saga-v1',
            source_episode_ids_json TEXT NOT NULL DEFAULT '[]',
            source_hash TEXT NOT NULL DEFAULT '',
            summary_status TEXT NOT NULL DEFAULT 'extractive_fallback' CHECK(summary_status IN (
                'legacy_rule','extractive_fallback','model_validated','user_edited'
            )),
            summary_protocol_version TEXT NOT NULL DEFAULT 'saga-summary-v1',
            summary_provider_id TEXT,
            summary_model TEXT,
            summary_evidence_json TEXT NOT NULL DEFAULT '[]',
            completion_reason TEXT NOT NULL DEFAULT '',
            completed_at REAL,
            archived_at REAL,
            tombstoned_at REAL,
            correction_note TEXT NOT NULL DEFAULT '',
            corrected_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_memory_sagas_status_time
            ON memory_sagas(status, end_at DESC);
        CREATE UNIQUE INDEX idx_memory_sagas_grouping_unique
            ON memory_sagas(grouping_fingerprint)
            WHERE grouping_fingerprint IS NOT NULL;

        CREATE TABLE memory_saga_episodes (
            saga_id TEXT NOT NULL REFERENCES memory_sagas(id) ON DELETE CASCADE,
            episode_id TEXT NOT NULL REFERENCES memory_episodes(id) ON DELETE RESTRICT,
            position INTEGER NOT NULL CHECK(position >= 0),
            role TEXT NOT NULL DEFAULT 'development' CHECK(role IN (
                'anchor','development','resolution'
            )),
            added_at REAL NOT NULL,
            removed_at REAL CHECK(removed_at IS NULL OR removed_at >= added_at),
            PRIMARY KEY(saga_id, episode_id)
        );
        CREATE UNIQUE INDEX idx_saga_episode_one_active_saga
            ON memory_saga_episodes(episode_id) WHERE removed_at IS NULL;
        CREATE INDEX idx_memory_saga_episodes_order
            ON memory_saga_episodes(saga_id, position, added_at);

        CREATE TABLE memory_saga_entities (
            saga_id TEXT NOT NULL REFERENCES memory_sagas(id) ON DELETE CASCADE,
            entity_id TEXT NOT NULL REFERENCES memory_entities(id) ON DELETE RESTRICT,
            relation TEXT NOT NULL DEFAULT 'involves',
            confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0 AND 1),
            source TEXT NOT NULL DEFAULT 'episode_derived' CHECK(source IN (
                'episode_derived','manual'
            )),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(saga_id, entity_id)
        );
        CREATE INDEX idx_memory_saga_entities_entity
            ON memory_saga_entities(entity_id, saga_id);

        CREATE TABLE memory_saga_events (
            id TEXT PRIMARY KEY,
            saga_id TEXT NOT NULL REFERENCES memory_sagas(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            reason_code TEXT,
            source TEXT NOT NULL DEFAULT 'system',
            policy_version TEXT NOT NULL DEFAULT 'saga-v1',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_memory_saga_events_saga
            ON memory_saga_events(saga_id, created_at, id);
        """,
    ),
    (
        19,
        """
        CREATE TABLE saga_group_candidates (
            id TEXT PRIMARY KEY,
            grouping_fingerprint TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK(status IN (
                'observing','qualified','conflicted','expired'
            )),
            episode_ids_json TEXT NOT NULL,
            shared_entity_ids_json TEXT NOT NULL DEFAULT '[]',
            entity_score REAL NOT NULL CHECK(entity_score BETWEEN 0 AND 1),
            text_score REAL NOT NULL CHECK(text_score BETWEEN 0 AND 1),
            time_score REAL NOT NULL CHECK(time_score BETWEEN 0 AND 1),
            coherence_score REAL NOT NULL CHECK(coherence_score BETWEEN 0 AND 1),
            total_score REAL NOT NULL CHECK(total_score BETWEEN 0 AND 1),
            score_details_json TEXT NOT NULL DEFAULT '{}',
            policy_version TEXT NOT NULL,
            conflict_reason TEXT,
            evaluation_count INTEGER NOT NULL DEFAULT 1 CHECK(evaluation_count >= 1),
            promoted_saga_id TEXT REFERENCES memory_sagas(id) ON DELETE SET NULL,
            first_seen_at REAL NOT NULL,
            last_evaluated_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE INDEX idx_saga_group_candidates_status_expiry
            ON saga_group_candidates(status, expires_at, last_evaluated_at);
        """,
    ),
    (
        20,
        """
        ALTER TABLE saga_group_candidates ADD COLUMN title TEXT NOT NULL DEFAULT '';
        ALTER TABLE saga_group_candidates ADD COLUMN summary TEXT NOT NULL DEFAULT '';
        ALTER TABLE saga_group_candidates ADD COLUMN theme TEXT NOT NULL DEFAULT '';
        ALTER TABLE saga_group_candidates ADD COLUMN current_stage TEXT NOT NULL DEFAULT '';
        ALTER TABLE saga_group_candidates ADD COLUMN lifecycle_signal TEXT NOT NULL DEFAULT 'active'
            CHECK(lifecycle_signal IN ('active','completed'));
        ALTER TABLE saga_group_candidates ADD COLUMN summary_status TEXT NOT NULL
            DEFAULT 'not_started' CHECK(summary_status IN (
                'not_started','extractive_fallback','model_validated'
            ));
        ALTER TABLE saga_group_candidates ADD COLUMN summary_protocol_version TEXT NOT NULL
            DEFAULT 'saga-summary-v1';
        ALTER TABLE saga_group_candidates ADD COLUMN summary_provider_id TEXT;
        ALTER TABLE saga_group_candidates ADD COLUMN summary_model TEXT;
        ALTER TABLE saga_group_candidates ADD COLUMN summary_evidence_episode_ids_json TEXT
            NOT NULL DEFAULT '[]';
        ALTER TABLE saga_group_candidates ADD COLUMN completion_evidence_episode_ids_json TEXT
            NOT NULL DEFAULT '[]';
        ALTER TABLE saga_group_candidates ADD COLUMN summary_warnings_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE saga_group_candidates ADD COLUMN summary_error_code TEXT;
        ALTER TABLE saga_group_candidates ADD COLUMN summary_source_hash TEXT NOT NULL DEFAULT '';
        ALTER TABLE saga_group_candidates ADD COLUMN summary_prompt_tokens INTEGER;
        ALTER TABLE saga_group_candidates ADD COLUMN summary_completion_tokens INTEGER;
        ALTER TABLE saga_group_candidates ADD COLUMN summary_repair_attempted INTEGER NOT NULL DEFAULT 0
            CHECK(summary_repair_attempted IN (0,1));

        CREATE TABLE saga_candidate_summary_events (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL REFERENCES saga_group_candidates(id) ON DELETE CASCADE,
            action TEXT NOT NULL CHECK(action IN (
                'summary_validated','summary_fallback','summary_rejected'
            )),
            error_code TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_saga_candidate_summary_events_candidate
            ON saga_candidate_summary_events(candidate_id, created_at, id);
        """,
    ),
    (
        21,
        """
        ALTER TABLE memory_sagas ADD COLUMN current_stage TEXT NOT NULL DEFAULT '';
        ALTER TABLE saga_group_candidates ADD COLUMN application_mode TEXT NOT NULL
            DEFAULT 'create' CHECK(application_mode IN ('create','append'));
        ALTER TABLE saga_group_candidates ADD COLUMN target_saga_id TEXT
            REFERENCES memory_sagas(id) ON DELETE SET NULL;
        ALTER TABLE saga_group_candidates ADD COLUMN application_attempt_count INTEGER NOT NULL
            DEFAULT 0 CHECK(application_attempt_count >= 0);
        ALTER TABLE saga_group_candidates ADD COLUMN application_error_code TEXT;
        ALTER TABLE saga_group_candidates ADD COLUMN last_application_at REAL;

        CREATE TABLE saga_consolidator_runs (
            id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            trigger TEXT NOT NULL CHECK(trigger IN ('startup','idle','weekly','manual','episode')),
            status TEXT NOT NULL CHECK(status IN (
                'queued','running','cancel_requested','cancelled','applied',
                'recovery_pending','exhausted','skipped'
            )),
            policy_version TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 3),
            max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 3),
            next_attempt_at REAL,
            started_at REAL,
            finished_at REAL,
            error_code TEXT,
            input_episode_ids_json TEXT NOT NULL DEFAULT '[]',
            result_saga_ids_json TEXT NOT NULL DEFAULT '[]',
            candidate_count INTEGER NOT NULL DEFAULT 0 CHECK(candidate_count >= 0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_saga_consolidator_due
            ON saga_consolidator_runs(status, next_attempt_at, created_at);

        CREATE TABLE saga_consolidator_events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES saga_consolidator_runs(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            before_status TEXT,
            after_status TEXT NOT NULL,
            reason_code TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_saga_consolidator_events_run
            ON saga_consolidator_events(run_id, created_at, id);
        """,
    ),
    (
        22,
        """
        ALTER TABLE memory_sagas ADD COLUMN completion_evidence_episode_ids_json TEXT
            NOT NULL DEFAULT '[]';
        ALTER TABLE memory_sagas ADD COLUMN lifecycle_policy_version TEXT NOT NULL
            DEFAULT 'saga-lifecycle-v1';
        ALTER TABLE memory_sagas ADD COLUMN revision INTEGER NOT NULL DEFAULT 0
            CHECK(revision >= 0);

        CREATE TABLE saga_relationship_delta_suggestions (
            id TEXT PRIMARY KEY,
            saga_id TEXT NOT NULL REFERENCES memory_sagas(id) ON DELETE CASCADE,
            source_event_id TEXT NOT NULL REFERENCES memory_saga_events(id) ON DELETE RESTRICT,
            signal_type TEXT NOT NULL CHECK(signal_type IN ('shared_saga_completed')),
            bond_delta REAL NOT NULL CHECK(bond_delta BETWEEN 0 AND 0.02),
            trust_delta REAL NOT NULL CHECK(trust_delta BETWEEN 0 AND 0.01),
            evidence_episode_ids_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed','revoked')),
            revocation_reason TEXT,
            revoked_at REAL,
            policy_version TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(saga_id, source_event_id, signal_type)
        );
        CREATE INDEX idx_saga_relationship_suggestions_saga
            ON saga_relationship_delta_suggestions(saga_id, created_at, id);
        """,
    ),
    (
        23,
        """
        ALTER TABLE memory_fragments ADD COLUMN last_recalled_at REAL;
        ALTER TABLE memory_fragments ADD COLUMN recall_count INTEGER NOT NULL DEFAULT 0
            CHECK(recall_count >= 0);
        ALTER TABLE memory_fragments ADD COLUMN cooling_since REAL;
        ALTER TABLE memory_fragments ADD COLUMN frozen_at REAL;
        ALTER TABLE memory_fragments ADD COLUMN lifecycle_policy_version TEXT NOT NULL
            DEFAULT 'fragment-retention-v1';
        ALTER TABLE memory_fragments ADD COLUMN lifecycle_revision INTEGER NOT NULL DEFAULT 0
            CHECK(lifecycle_revision >= 0);

        UPDATE memory_fragments SET cooling_since=updated_at
            WHERE status='cooling' AND cooling_since IS NULL;
        UPDATE memory_fragments SET frozen_at=updated_at
            WHERE status='frozen' AND frozen_at IS NULL;

        CREATE INDEX idx_memory_fragments_retention_due
            ON memory_fragments(status, enabled, last_recalled_at, created_at);

        CREATE TABLE memory_recall_events (
            id TEXT PRIMARY KEY,
            fragment_id TEXT NOT NULL REFERENCES memory_fragments(id) ON DELETE RESTRICT,
            context_key TEXT NOT NULL,
            source_session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
            token_estimate INTEGER NOT NULL DEFAULT 0 CHECK(token_estimate >= 0),
            policy_version TEXT NOT NULL,
            injected_at REAL NOT NULL,
            UNIQUE(fragment_id, context_key)
        );
        CREATE INDEX idx_memory_recall_events_fragment
            ON memory_recall_events(fragment_id, injected_at, id);

        CREATE TABLE memory_lifecycle_events (
            id TEXT PRIMARY KEY,
            fragment_id TEXT NOT NULL REFERENCES memory_fragments(id) ON DELETE RESTRICT,
            revision INTEGER NOT NULL CHECK(revision >= 1),
            from_status TEXT NOT NULL
                CHECK(from_status IN ('active','cooling','frozen','tombstone')),
            to_status TEXT NOT NULL
                CHECK(to_status IN ('active','cooling','frozen','tombstone')),
            retention_score REAL CHECK(retention_score BETWEEN 0 AND 1),
            score_components_json TEXT NOT NULL DEFAULT '{}',
            reason_code TEXT NOT NULL,
            source TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(fragment_id, revision)
        );
        CREATE INDEX idx_memory_lifecycle_events_fragment
            ON memory_lifecycle_events(fragment_id, created_at, id);
        """,
    ),
    (
        24,
        """
        ALTER TABLE memory_fragments ADD COLUMN fts_indexed INTEGER NOT NULL DEFAULT 1
            CHECK(fts_indexed IN (0,1));

        DROP TRIGGER IF EXISTS memory_fragments_fts_insert;
        DROP TRIGGER IF EXISTS memory_fragments_fts_delete;
        DROP TRIGGER IF EXISTS memory_fragments_fts_update;

        CREATE TRIGGER memory_fragments_fts_insert
        AFTER INSERT ON memory_fragments WHEN new.fts_indexed=1 BEGIN
            INSERT INTO memory_fragments_fts(rowid,content,tags)
            VALUES(new.rowid,new.content,new.tags);
        END;
        CREATE TRIGGER memory_fragments_fts_delete
        AFTER DELETE ON memory_fragments WHEN old.fts_indexed=1 BEGIN
            INSERT INTO memory_fragments_fts(memory_fragments_fts,rowid,content,tags)
            VALUES('delete',old.rowid,old.content,old.tags);
        END;
        CREATE TRIGGER memory_fragments_fts_update
        AFTER UPDATE OF content,tags ON memory_fragments
        WHEN old.fts_indexed=1 AND new.fts_indexed=1 BEGIN
            INSERT INTO memory_fragments_fts(memory_fragments_fts,rowid,content,tags)
            VALUES('delete',old.rowid,old.content,old.tags);
            INSERT INTO memory_fragments_fts(rowid,content,tags)
            VALUES(new.rowid,new.content,new.tags);
        END;
        """,
    ),
    (
        25,
        """
        ALTER TABLE memory_fragments ADD COLUMN last_archivist_evaluated_at REAL;
        CREATE INDEX idx_memory_fragments_archivist_due
            ON memory_fragments(status, enabled, last_archivist_evaluated_at);

        CREATE TABLE archivist_runs (
            id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            trigger TEXT NOT NULL CHECK(trigger IN ('startup','idle','manual')),
            status TEXT NOT NULL CHECK(status IN (
                'queued','running','cancel_requested','cancelled','completed','skipped',
                'recovery_pending','exhausted'
            )),
            policy_version TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 3),
            max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 3),
            next_attempt_at REAL,
            started_at REAL,
            finished_at REAL,
            error_code TEXT,
            scan_budget INTEGER NOT NULL CHECK(scan_budget BETWEEN 1 AND 200),
            transition_budget INTEGER NOT NULL CHECK(transition_budget BETWEEN 0 AND 100),
            runtime_budget_ms INTEGER NOT NULL CHECK(runtime_budget_ms BETWEEN 100 AND 30000),
            model_call_budget INTEGER NOT NULL DEFAULT 0 CHECK(model_call_budget BETWEEN 0 AND 20),
            scanned_count INTEGER NOT NULL DEFAULT 0 CHECK(scanned_count >= 0),
            transitioned_count INTEGER NOT NULL DEFAULT 0 CHECK(transitioned_count >= 0),
            conflict_count INTEGER NOT NULL DEFAULT 0 CHECK(conflict_count >= 0),
            model_calls_used INTEGER NOT NULL DEFAULT 0 CHECK(model_calls_used >= 0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_archivist_runs_due
            ON archivist_runs(status, next_attempt_at, created_at);

        CREATE TABLE archivist_run_events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES archivist_runs(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            before_status TEXT,
            after_status TEXT NOT NULL,
            reason_code TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_archivist_run_events_run
            ON archivist_run_events(run_id, created_at, id);
        """,
    ),
    (
        26,
        """
        PRAGMA foreign_keys=OFF;

        CREATE TABLE memory_episodes_v26 (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            start_at REAL NOT NULL,
            end_at REAL NOT NULL,
            significance INTEGER NOT NULL DEFAULT 4 CHECK(significance BETWEEN 1 AND 10),
            confidence REAL NOT NULL DEFAULT 0.7 CHECK(confidence BETWEEN 0 AND 1),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','completed','archived','tombstone')),
            source TEXT NOT NULL DEFAULT 'candidate_confirmed',
            candidate_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            grouping_fingerprint TEXT,
            policy_version TEXT NOT NULL DEFAULT 'legacy',
            source_fragment_ids_json TEXT NOT NULL DEFAULT '[]',
            source_hash TEXT NOT NULL DEFAULT '',
            summary_status TEXT NOT NULL DEFAULT 'legacy_rule' CHECK(summary_status IN (
                'legacy_rule','extractive_fallback','model_validated','user_edited'
            )),
            summary_protocol_version TEXT NOT NULL DEFAULT 'legacy',
            summary_provider_id TEXT,
            summary_model TEXT,
            summary_evidence_json TEXT NOT NULL DEFAULT '[]',
            application_version TEXT NOT NULL DEFAULT 'legacy',
            correction_note TEXT NOT NULL DEFAULT '',
            corrected_at REAL,
            completed_at REAL,
            archived_at REAL,
            tombstoned_at REAL,
            lifecycle_policy_version TEXT NOT NULL DEFAULT 'episode-lifecycle-v1',
            lifecycle_revision INTEGER NOT NULL DEFAULT 0 CHECK(lifecycle_revision >= 0),
            last_lifecycle_evaluated_at REAL
        );
        INSERT INTO memory_episodes_v26(
            id,title,summary,start_at,end_at,significance,confidence,status,source,candidate_id,
            created_at,updated_at,grouping_fingerprint,policy_version,source_fragment_ids_json,
            source_hash,summary_status,summary_protocol_version,summary_provider_id,summary_model,
            summary_evidence_json,application_version,correction_note,corrected_at
        ) SELECT
            id,title,summary,start_at,end_at,significance,confidence,status,source,candidate_id,
            created_at,updated_at,grouping_fingerprint,policy_version,source_fragment_ids_json,
            source_hash,summary_status,summary_protocol_version,summary_provider_id,summary_model,
            summary_evidence_json,application_version,correction_note,corrected_at
        FROM memory_episodes;
        DROP TABLE memory_episodes;
        ALTER TABLE memory_episodes_v26 RENAME TO memory_episodes;
        CREATE INDEX idx_memory_episodes_status_time
            ON memory_episodes(status, end_at DESC);
        CREATE UNIQUE INDEX idx_memory_episodes_candidate_unique
            ON memory_episodes(candidate_id) WHERE candidate_id IS NOT NULL;
        CREATE UNIQUE INDEX idx_memory_episodes_grouping_unique
            ON memory_episodes(grouping_fingerprint) WHERE grouping_fingerprint IS NOT NULL;
        CREATE INDEX idx_memory_episodes_lifecycle_due
            ON memory_episodes(status,last_lifecycle_evaluated_at,end_at);

        CREATE TABLE memory_episode_lifecycle_events (
            id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL REFERENCES memory_episodes(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL CHECK(revision >= 1),
            from_status TEXT NOT NULL CHECK(from_status IN (
                'active','completed','archived','tombstone'
            )),
            to_status TEXT NOT NULL CHECK(to_status IN (
                'active','completed','archived','tombstone'
            )),
            reason_code TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('archivist','new_evidence','user','privacy')),
            policy_version TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            UNIQUE(episode_id,revision)
        );
        CREATE INDEX idx_memory_episode_lifecycle_events_episode
            ON memory_episode_lifecycle_events(episode_id,revision);

        ALTER TABLE memory_sagas ADD COLUMN last_lifecycle_evaluated_at REAL;
        ALTER TABLE memory_sagas ADD COLUMN completion_revision INTEGER;
        UPDATE memory_sagas SET completion_revision=revision WHERE status='completed';
        CREATE INDEX idx_memory_sagas_lifecycle_due
            ON memory_sagas(status,last_lifecycle_evaluated_at,completed_at);

        PRAGMA foreign_keys=ON;
        """,
    ),
    (
        27,
        """
        ALTER TABLE archivist_runs ADD COLUMN relation_count INTEGER NOT NULL DEFAULT 0
            CHECK(relation_count >= 0);

        CREATE TABLE memory_fragment_relations (
            id TEXT PRIMARY KEY,
            source_fragment_id TEXT NOT NULL REFERENCES memory_fragments(id) ON DELETE CASCADE,
            target_fragment_id TEXT NOT NULL REFERENCES memory_fragments(id) ON DELETE CASCADE,
            entity_id TEXT NOT NULL REFERENCES memory_entities(id) ON DELETE CASCADE,
            relation_type TEXT NOT NULL CHECK(relation_type IN (
                'superseded','possible_conflict'
            )),
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN (
                'active','resolved','dismissed'
            )),
            confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
            rule_code TEXT NOT NULL,
            detector_version TEXT NOT NULL,
            model_version TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            CHECK(source_fragment_id != target_fragment_id),
            UNIQUE(source_fragment_id,target_fragment_id,entity_id,relation_type)
        );
        CREATE INDEX idx_memory_fragment_relations_status
            ON memory_fragment_relations(status,relation_type,updated_at);
        CREATE INDEX idx_memory_fragment_relations_fragments
            ON memory_fragment_relations(source_fragment_id,target_fragment_id);

        CREATE TABLE memory_fragment_relation_events (
            id TEXT PRIMARY KEY,
            relation_id TEXT NOT NULL REFERENCES memory_fragment_relations(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            before_status TEXT,
            after_status TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('archivist','user')),
            detector_version TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_memory_fragment_relation_events_relation
            ON memory_fragment_relation_events(relation_id,created_at,id);
        """,
    ),
    (
        28,
        """
        CREATE TABLE knowledge_collections (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL COLLATE NOCASE,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(name)
        );
        INSERT INTO knowledge_collections(id,name,description,status,created_at,updated_at)
        VALUES('default','默认知识库','用户明确导入的外部资料','active',
               CAST(strftime('%s','now') AS REAL),CAST(strftime('%s','now') AS REAL));

        CREATE TABLE knowledge_documents (
            id TEXT PRIMARY KEY,
            collection_id TEXT NOT NULL DEFAULT 'default'
                REFERENCES knowledge_collections(id) ON DELETE RESTRICT,
            source_type TEXT NOT NULL DEFAULT 'file' CHECK(source_type='file'),
            original_name TEXT NOT NULL,
            extension TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
            storage_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'staged' CHECK(status IN (
                'staged','queued','parsing','indexed','failed','cancelled',
                'delete_pending','delete_failed'
            )),
            sensitivity TEXT NOT NULL DEFAULT 'normal'
                CHECK(sensitivity IN ('normal','sensitive')),
            embedding_mode TEXT NOT NULL DEFAULT 'none'
                CHECK(embedding_mode IN ('none','local','remote')),
            embedding_provider_id TEXT,
            embedding_model TEXT,
            parser_version TEXT,
            index_version TEXT,
            page_count INTEGER NOT NULL DEFAULT 0 CHECK(page_count >= 0),
            chunk_count INTEGER NOT NULL DEFAULT 0 CHECK(chunk_count >= 0),
            error_code TEXT,
            indexed_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            CHECK(embedding_mode='remote' OR (
                embedding_provider_id IS NULL AND embedding_model IS NULL
            )),
            CHECK(status!='indexed' OR indexed_at IS NOT NULL)
        );
        CREATE INDEX idx_knowledge_documents_collection_status
            ON knowledge_documents(collection_id,status,updated_at);
        CREATE UNIQUE INDEX uq_knowledge_documents_collection_hash
            ON knowledge_documents(collection_id,content_sha256);

        CREATE TABLE knowledge_import_runs (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            idempotency_key TEXT NOT NULL UNIQUE,
            trigger TEXT NOT NULL CHECK(trigger IN ('import','reindex')),
            status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN (
                'queued','running','cancel_requested','cancelled','completed',
                'failed','recovery_pending'
            )),
            current_stage TEXT NOT NULL DEFAULT 'validation' CHECK(current_stage IN (
                'validation','copy','parsing','chunking','indexing','finalizing'
            )),
            progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
            max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 5),
            error_code TEXT,
            cancel_requested_at REAL,
            started_at REAL,
            finished_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_knowledge_import_runs_status
            ON knowledge_import_runs(status,created_at,id);

        CREATE TABLE knowledge_import_events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES knowledge_import_runs(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            before_status TEXT,
            after_status TEXT NOT NULL,
            stage TEXT NOT NULL,
            error_code TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_knowledge_import_events_run
            ON knowledge_import_events(run_id,created_at,id);
        """,
    ),
    (
        29,
        """
        ALTER TABLE knowledge_documents ADD COLUMN parsed_at REAL;
        ALTER TABLE knowledge_documents ADD COLUMN parse_char_count INTEGER NOT NULL DEFAULT 0
            CHECK(parse_char_count >= 0);
        ALTER TABLE knowledge_documents ADD COLUMN parse_line_count INTEGER NOT NULL DEFAULT 0
            CHECK(parse_line_count >= 0);
        ALTER TABLE knowledge_documents ADD COLUMN parse_heading_count INTEGER NOT NULL DEFAULT 0
            CHECK(parse_heading_count >= 0);
        ALTER TABLE knowledge_import_runs ADD COLUMN next_attempt_at REAL;

        CREATE TABLE knowledge_parse_artifacts (
            document_id TEXT PRIMARY KEY REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            artifact_key TEXT NOT NULL UNIQUE,
            parser_version TEXT NOT NULL,
            normalized_sha256 TEXT NOT NULL CHECK(length(normalized_sha256)=64),
            char_count INTEGER NOT NULL CHECK(char_count >= 0),
            line_count INTEGER NOT NULL CHECK(line_count >= 0),
            heading_count INTEGER NOT NULL CHECK(heading_count >= 0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_knowledge_parse_artifacts_version
            ON knowledge_parse_artifacts(parser_version,updated_at);
        CREATE INDEX idx_knowledge_import_runs_due
            ON knowledge_import_runs(status,current_stage,next_attempt_at,created_at);
        """,
    ),
    (
        30,
        """
        ALTER TABLE knowledge_documents ADD COLUMN chunker_version TEXT;
        ALTER TABLE knowledge_documents ADD COLUMN chunked_at REAL;

        CREATE TABLE knowledge_chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
            content TEXT NOT NULL CHECK(length(content) > 0),
            content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
            heading_path_json TEXT NOT NULL DEFAULT '[]',
            paragraph_start INTEGER NOT NULL CHECK(paragraph_start >= 1),
            paragraph_end INTEGER NOT NULL CHECK(paragraph_end >= paragraph_start),
            line_start INTEGER NOT NULL CHECK(line_start >= 1),
            line_end INTEGER NOT NULL CHECK(line_end >= line_start),
            char_start INTEGER NOT NULL CHECK(char_start >= 0),
            char_end INTEGER NOT NULL CHECK(char_end > char_start),
            page_start INTEGER CHECK(page_start IS NULL OR page_start >= 1),
            page_end INTEGER CHECK(page_end IS NULL OR page_end >= page_start),
            chunker_version TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(document_id,ordinal),
            UNIQUE(document_id,char_start,char_end)
        );
        CREATE INDEX idx_knowledge_chunks_document_locator
            ON knowledge_chunks(document_id,ordinal,char_start,char_end);
        CREATE INDEX idx_knowledge_chunks_content_hash
            ON knowledge_chunks(content_sha256);
        """,
    ),
    (
        31,
        """
        ALTER TABLE knowledge_documents ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'
            CHECK(json_valid(tags_json) AND json_type(tags_json)='array');
        CREATE VIRTUAL TABLE knowledge_chunks_fts USING fts5(
            terms,
            content='',
            contentless_delete=1,
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TRIGGER knowledge_chunks_fts_delete
        BEFORE DELETE ON knowledge_chunks
        BEGIN
            DELETE FROM knowledge_chunks_fts WHERE rowid=OLD.rowid;
        END;
        """,
    ),
    (
        32,
        """
        CREATE TABLE knowledge_chat_retrievals (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            user_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            assistant_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            trigger_reason TEXT NOT NULL,
            query_sha256 TEXT NOT NULL CHECK(length(query_sha256)=64),
            candidate_count INTEGER NOT NULL DEFAULT 0 CHECK(candidate_count >= 0),
            injected_count INTEGER NOT NULL DEFAULT 0 CHECK(injected_count >= 0),
            knowledge_tokens INTEGER NOT NULL DEFAULT 0 CHECK(knowledge_tokens >= 0),
            knowledge_token_budget INTEGER NOT NULL CHECK(knowledge_token_budget > 0),
            lore_tokens INTEGER NOT NULL DEFAULT 0 CHECK(lore_tokens >= 0),
            memory_tokens INTEGER NOT NULL DEFAULT 0 CHECK(memory_tokens >= 0),
            status TEXT NOT NULL CHECK(status IN ('no_results','injected','completed','failed')),
            created_at REAL NOT NULL,
            finished_at REAL
        );
        CREATE INDEX idx_knowledge_chat_retrievals_session
            ON knowledge_chat_retrievals(session_id,created_at,id);
        CREATE INDEX idx_knowledge_chat_retrievals_assistant
            ON knowledge_chat_retrievals(assistant_message_id);

        CREATE TABLE knowledge_message_citations (
            id TEXT PRIMARY KEY,
            assistant_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            retrieval_id TEXT NOT NULL REFERENCES knowledge_chat_retrievals(id) ON DELETE CASCADE,
            citation_key TEXT NOT NULL,
            document_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            original_name TEXT NOT NULL,
            heading_path_json TEXT NOT NULL DEFAULT '[]'
                CHECK(json_valid(heading_path_json) AND json_type(heading_path_json)='array'),
            ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
            paragraph_start INTEGER NOT NULL CHECK(paragraph_start >= 1),
            paragraph_end INTEGER NOT NULL CHECK(paragraph_end >= paragraph_start),
            line_start INTEGER NOT NULL CHECK(line_start >= 1),
            line_end INTEGER NOT NULL CHECK(line_end >= line_start),
            char_start INTEGER NOT NULL CHECK(char_start >= 0),
            char_end INTEGER NOT NULL CHECK(char_end > char_start),
            page_start INTEGER,
            page_end INTEGER,
            content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
            created_at REAL NOT NULL,
            UNIQUE(assistant_message_id,citation_key)
        );
        CREATE INDEX idx_knowledge_message_citations_message
            ON knowledge_message_citations(assistant_message_id,citation_key);
        """,
    ),
    (
        33,
        """
        CREATE TABLE knowledge_deletion_runs (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            collection_id TEXT NOT NULL,
            content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
            status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed')),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
            error_code TEXT,
            started_at REAL,
            finished_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_knowledge_deletion_runs_status
            ON knowledge_deletion_runs(status,created_at,id);
        CREATE INDEX idx_knowledge_deletion_runs_document
            ON knowledge_deletion_runs(document_id,created_at,id);

        CREATE TABLE knowledge_deletion_events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES knowledge_deletion_runs(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            before_status TEXT,
            after_status TEXT NOT NULL,
            error_code TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
                CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_knowledge_deletion_events_run
            ON knowledge_deletion_events(run_id,created_at,id);

        CREATE TRIGGER knowledge_retrieval_finished_guard
        BEFORE UPDATE OF status,finished_at ON knowledge_chat_retrievals
        WHEN NEW.status IN ('completed','failed') AND NEW.finished_at IS NULL
        BEGIN
            SELECT RAISE(ABORT,'finished knowledge retrieval requires finished_at');
        END;
        """,
    ),
    (
        34,
        """
        ALTER TABLE knowledge_parse_artifacts ADD COLUMN page_count INTEGER NOT NULL DEFAULT 0
            CHECK(page_count >= 0);
        ALTER TABLE knowledge_documents ADD COLUMN embedding_version TEXT;
        ALTER TABLE knowledge_documents ADD COLUMN embedding_indexed_at REAL;
        ALTER TABLE knowledge_documents ADD COLUMN embedding_dimension INTEGER
            CHECK(embedding_dimension IS NULL OR embedding_dimension > 0);
        ALTER TABLE knowledge_documents ADD COLUMN embedding_error_code TEXT;

        CREATE TABLE knowledge_chunk_embeddings (
            chunk_id TEXT PRIMARY KEY REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
            document_id TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            provider_id TEXT NOT NULL,
            model TEXT NOT NULL,
            embedding_version TEXT NOT NULL,
            dimension INTEGER NOT NULL CHECK(dimension > 0),
            vector_blob BLOB NOT NULL CHECK(length(vector_blob)=dimension*4),
            content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_knowledge_chunk_embeddings_document
            ON knowledge_chunk_embeddings(document_id,embedding_version,chunk_id);

        CREATE TABLE knowledge_embedding_runs (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            provider_id TEXT NOT NULL,
            model TEXT NOT NULL,
            embedding_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','skipped')),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
            max_attempts INTEGER NOT NULL DEFAULT 2 CHECK(max_attempts BETWEEN 1 AND 3),
            vector_count INTEGER NOT NULL DEFAULT 0 CHECK(vector_count >= 0),
            error_code TEXT,
            started_at REAL,
            finished_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_knowledge_embedding_runs_status
            ON knowledge_embedding_runs(status,created_at,id);
        CREATE INDEX idx_knowledge_embedding_runs_document
            ON knowledge_embedding_runs(document_id,created_at,id);

        CREATE TABLE knowledge_embedding_events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES knowledge_embedding_runs(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            before_status TEXT,
            after_status TEXT NOT NULL,
            error_code TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
                CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_knowledge_embedding_events_run
            ON knowledge_embedding_events(run_id,created_at,id);
        """,
    ),
    (
        35,
        """
        ALTER TABLE knowledge_documents ADD COLUMN transmission_policy TEXT NOT NULL
            DEFAULT 'ask_each_time'
            CHECK(transmission_policy IN ('remote_allowed','ask_each_time','local_only'));
        ALTER TABLE knowledge_documents ADD COLUMN policy_revision INTEGER NOT NULL DEFAULT 1
            CHECK(policy_revision >= 1);
        ALTER TABLE knowledge_documents ADD COLUMN policy_updated_at REAL;
        UPDATE knowledge_documents SET policy_updated_at=updated_at
            WHERE policy_updated_at IS NULL;

        ALTER TABLE providers ADD COLUMN execution_location TEXT NOT NULL DEFAULT 'unknown'
            CHECK(execution_location IN ('local','remote','unknown'));
        ALTER TABLE providers ADD COLUMN location_revision INTEGER NOT NULL DEFAULT 1
            CHECK(location_revision >= 1);
        ALTER TABLE providers ADD COLUMN location_confirmed_at REAL;
        UPDATE providers SET execution_location='local' WHERE id='mock';
        UPDATE providers SET execution_location='remote' WHERE id IN (
            'deepseek','openai','glm','qwen','kimi','openrouter','siliconflow'
        );
        UPDATE providers SET execution_location='local'
            WHERE id='ollama' AND (
                base_url LIKE 'http://127.0.0.1:%' OR base_url LIKE 'http://localhost:%'
                OR base_url LIKE 'https://127.0.0.1:%' OR base_url LIKE 'https://localhost:%'
            );

        CREATE TABLE knowledge_document_policy_events (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            before_policy TEXT NOT NULL
                CHECK(before_policy IN ('remote_allowed','ask_each_time','local_only')),
            after_policy TEXT NOT NULL
                CHECK(after_policy IN ('remote_allowed','ask_each_time','local_only')),
            policy_revision INTEGER NOT NULL CHECK(policy_revision >= 1),
            actor TEXT NOT NULL DEFAULT 'user' CHECK(actor IN ('user','migration','system')),
            reason_code TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_knowledge_document_policy_events_document
            ON knowledge_document_policy_events(document_id,created_at,id);

        CREATE TRIGGER knowledge_document_policy_sensitive_insert_guard
        BEFORE INSERT ON knowledge_documents
        WHEN NEW.sensitivity='sensitive' AND NEW.transmission_policy='remote_allowed'
        BEGIN
            SELECT RAISE(ABORT,'sensitive document cannot allow remote transmission');
        END;
        CREATE TRIGGER knowledge_document_policy_sensitive_update_guard
        BEFORE UPDATE OF sensitivity,transmission_policy ON knowledge_documents
        WHEN NEW.sensitivity='sensitive' AND NEW.transmission_policy='remote_allowed'
        BEGIN
            SELECT RAISE(ABORT,'sensitive document cannot allow remote transmission');
        END;
        """,
    ),
    (
        36,
        """
        CREATE TABLE knowledge_recall_decisions (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            user_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            protocol_version TEXT NOT NULL,
            recall_mode TEXT NOT NULL CHECK(recall_mode IN ('explicit','smart')),
            shadow INTEGER NOT NULL DEFAULT 1 CHECK(shadow IN (0,1)),
            action TEXT NOT NULL CHECK(action IN ('skip','retrieve','ask')),
            reason_code TEXT NOT NULL,
            confidence_band TEXT NOT NULL CHECK(confidence_band IN ('low','medium','high')),
            query_sha256 TEXT NOT NULL CHECK(length(query_sha256)=64),
            candidate_count INTEGER NOT NULL DEFAULT 0 CHECK(candidate_count >= 0),
            eligible_count INTEGER NOT NULL DEFAULT 0 CHECK(eligible_count >= 0),
            injected_count INTEGER NOT NULL DEFAULT 0 CHECK(injected_count >= 0),
            retrieval_mode TEXT NOT NULL DEFAULT 'none'
                CHECK(retrieval_mode IN ('none','fts','vector','hybrid','fts_unavailable')),
            vector_available INTEGER NOT NULL DEFAULT 0 CHECK(vector_available IN (0,1)),
            vector_error_code TEXT,
            policy_snapshot_sha256 TEXT NOT NULL CHECK(length(policy_snapshot_sha256)=64),
            provider_id TEXT,
            provider_location TEXT NOT NULL DEFAULT 'unknown'
                CHECK(provider_location IN ('local','remote','unknown')),
            provider_location_revision INTEGER NOT NULL DEFAULT 1
                CHECK(provider_location_revision >= 1),
            latency_ms INTEGER NOT NULL DEFAULT 0 CHECK(latency_ms >= 0),
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK(status IN ('queued','completed','failed','timed_out')),
            created_at REAL NOT NULL,
            finished_at REAL
        );
        CREATE INDEX idx_knowledge_recall_decisions_session
            ON knowledge_recall_decisions(session_id,created_at DESC,id DESC);
        CREATE INDEX idx_knowledge_recall_decisions_status
            ON knowledge_recall_decisions(status,created_at,id);
        """,
    ),
    (
        37,
        """
        ALTER TABLE knowledge_recall_decisions ADD COLUMN threshold_version TEXT NOT NULL
            DEFAULT 'knowledge-recall-thresholds-v1';

        CREATE TABLE knowledge_transmission_grants (
            id TEXT PRIMARY KEY,
            recall_decision_id TEXT REFERENCES knowledge_recall_decisions(id) ON DELETE SET NULL,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            user_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL
                DEFERRABLE INITIALLY DEFERRED,
            request_nonce TEXT NOT NULL CHECK(length(request_nonce) BETWEEN 16 AND 64),
            user_content_sha256 TEXT NOT NULL CHECK(length(user_content_sha256)=64),
            query_sha256 TEXT NOT NULL CHECK(length(query_sha256)=64),
            provider_id TEXT,
            model TEXT NOT NULL,
            provider_location TEXT NOT NULL CHECK(provider_location IN ('local','remote','unknown')),
            provider_location_revision INTEGER NOT NULL CHECK(provider_location_revision >= 1),
            plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256)=64),
            policy_snapshot_sha256 TEXT NOT NULL CHECK(length(policy_snapshot_sha256)=64),
            threshold_version TEXT NOT NULL,
            token_hash TEXT UNIQUE CHECK(token_hash IS NULL OR length(token_hash)=64),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','issued','consumed','denied','expired','revoked')),
            document_count INTEGER NOT NULL CHECK(document_count >= 0),
            chunk_count INTEGER NOT NULL CHECK(chunk_count >= 0),
            token_min INTEGER NOT NULL CHECK(token_min >= 0),
            token_max INTEGER NOT NULL CHECK(token_max >= token_min),
            expires_at REAL NOT NULL,
            issued_at REAL,
            consumed_at REAL,
            denied_at REAL,
            revoked_at REAL,
            error_code TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(session_id,request_nonce)
        );
        CREATE INDEX idx_knowledge_transmission_grants_status
            ON knowledge_transmission_grants(status,expires_at,created_at,id);
        CREATE INDEX idx_knowledge_transmission_grants_decision
            ON knowledge_transmission_grants(recall_decision_id,created_at,id);

        CREATE TABLE knowledge_transmission_grant_items (
            grant_id TEXT NOT NULL REFERENCES knowledge_transmission_grants(id) ON DELETE CASCADE,
            document_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
            transmission_policy TEXT NOT NULL
                CHECK(transmission_policy IN ('remote_allowed','ask_each_time','local_only')),
            policy_revision INTEGER NOT NULL CHECK(policy_revision >= 1),
            sensitivity TEXT NOT NULL CHECK(sensitivity IN ('normal','sensitive')),
            token_estimate INTEGER NOT NULL CHECK(token_estimate >= 0),
            PRIMARY KEY(grant_id,chunk_id)
        );
        CREATE INDEX idx_knowledge_transmission_grant_items_document
            ON knowledge_transmission_grant_items(document_id,grant_id);

        CREATE TABLE knowledge_transmission_grant_events (
            id TEXT PRIMARY KEY,
            grant_id TEXT NOT NULL REFERENCES knowledge_transmission_grants(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            before_status TEXT,
            after_status TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0 CHECK(item_count >= 0),
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_knowledge_transmission_grant_events_grant
            ON knowledge_transmission_grant_events(grant_id,created_at,id);
        """,
    ),
    (
        38,
        """
        ALTER TABLE knowledge_transmission_grants ADD COLUMN recall_mode TEXT NOT NULL
            DEFAULT 'explicit' CHECK(recall_mode IN ('off','explicit','smart'));

        CREATE TABLE knowledge_recall_mode_events (
            id TEXT PRIMARY KEY,
            before_mode TEXT NOT NULL CHECK(before_mode IN ('off','explicit','smart')),
            after_mode TEXT NOT NULL CHECK(after_mode IN ('off','explicit','smart')),
            actor TEXT NOT NULL DEFAULT 'user',
            reason_code TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_knowledge_recall_mode_events_created
            ON knowledge_recall_mode_events(created_at DESC,id DESC);
        """,
    ),
    (
        39,
        """
        ALTER TABLE knowledge_documents ADD COLUMN recall_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE knowledge_documents ADD COLUMN last_recalled_at REAL;

        CREATE INDEX IF NOT EXISTS idx_knowledge_recall_decisions_created
            ON knowledge_recall_decisions(created_at);

        CREATE INDEX IF NOT EXISTS idx_knowledge_chat_retrievals_created
            ON knowledge_chat_retrievals(created_at);
        """,
    ),
    (
        40,
        """
        ALTER TABLE memory_fragments ADD COLUMN observation_source TEXT NOT NULL
            DEFAULT 'conversation' CHECK(observation_source IN (
                'conversation','knowledge_reference','shared_lookup','user_confirmed_fact'
            ));
        ALTER TABLE knowledge_chat_retrievals ADD COLUMN search_protocol_version TEXT NOT NULL
            DEFAULT 'knowledge-search-v1';
        CREATE INDEX idx_memory_fragments_observation_source
            ON memory_fragments(observation_source,status,enabled,created_at);
        """,
    ),
    (
        41,
        """
        ALTER TABLE knowledge_collections ADD COLUMN default_transmission_policy TEXT NOT NULL
            DEFAULT 'ask_each_time' CHECK(default_transmission_policy IN (
                'remote_allowed','ask_each_time','local_only'
            ));
        ALTER TABLE knowledge_collections ADD COLUMN policy_revision INTEGER NOT NULL DEFAULT 1
            CHECK(policy_revision >= 1);
        ALTER TABLE knowledge_collections ADD COLUMN policy_updated_at REAL;
        UPDATE knowledge_collections SET policy_updated_at=updated_at
            WHERE policy_updated_at IS NULL;

        ALTER TABLE knowledge_chat_retrievals ADD COLUMN audit_state TEXT NOT NULL
            DEFAULT 'active' CHECK(audit_state IN ('active','minimized'));
        ALTER TABLE knowledge_chat_retrievals ADD COLUMN minimized_at REAL;
        CREATE INDEX idx_knowledge_chat_retrievals_audit_lifecycle
            ON knowledge_chat_retrievals(audit_state,created_at,id);
        """,
    ),
    (
        42,
        """
        CREATE TABLE conversation_summary_runs (
            id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK(status IN (
                'queued','running','recovery_pending','completed',
                'failed','exhausted','cancelled'
            )),
            protocol_version TEXT NOT NULL,
            source_start_message_id TEXT NOT NULL,
            source_end_message_id TEXT NOT NULL,
            source_message_count INTEGER NOT NULL
                CHECK(source_message_count >= 2 AND source_message_count % 2 = 0),
            source_hash TEXT NOT NULL CHECK(length(source_hash) = 64),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
            max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10),
            lease_token TEXT,
            lease_expires_at REAL,
            heartbeat_at REAL,
            next_attempt_at REAL,
            error_code TEXT,
            result_revision_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL
        );
        CREATE INDEX idx_conversation_summary_runs_due
            ON conversation_summary_runs(status,next_attempt_at,created_at,id);
        CREATE INDEX idx_conversation_summary_runs_session
            ON conversation_summary_runs(session_id,created_at,id);

        CREATE TABLE conversation_summary_revisions (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            run_id TEXT NOT NULL REFERENCES conversation_summary_runs(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL CHECK(revision >= 1),
            status TEXT NOT NULL CHECK(status IN ('active','superseded','invalid','failed')),
            protocol_version TEXT NOT NULL,
            source_start_message_id TEXT NOT NULL,
            source_end_message_id TEXT NOT NULL,
            source_message_count INTEGER NOT NULL
                CHECK(source_message_count >= 2 AND source_message_count % 2 = 0),
            source_hash TEXT NOT NULL CHECK(length(source_hash) = 64),
            summary_text TEXT,
            open_threads_json TEXT NOT NULL DEFAULT '[]',
            decisions_json TEXT NOT NULL DEFAULT '[]',
            corrections_json TEXT NOT NULL DEFAULT '[]',
            entity_refs_json TEXT NOT NULL DEFAULT '[]',
            provider_id TEXT,
            model TEXT,
            prompt_tokens INTEGER CHECK(prompt_tokens IS NULL OR prompt_tokens >= 0),
            completion_tokens INTEGER CHECK(completion_tokens IS NULL OR completion_tokens >= 0),
            error_code TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            activated_at REAL,
            superseded_at REAL,
            invalidated_at REAL,
            UNIQUE(session_id,revision),
            CHECK(status IN ('invalid','failed') OR summary_text IS NOT NULL)
        );
        CREATE UNIQUE INDEX idx_conversation_summary_one_active
            ON conversation_summary_revisions(session_id) WHERE status='active';
        CREATE INDEX idx_conversation_summary_revisions_source
            ON conversation_summary_revisions(session_id,source_hash,status,revision);

        CREATE TABLE conversation_summary_events (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            run_id TEXT REFERENCES conversation_summary_runs(id) ON DELETE CASCADE,
            revision_id TEXT REFERENCES conversation_summary_revisions(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            before_status TEXT,
            after_status TEXT,
            reason_code TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            CHECK(run_id IS NOT NULL OR revision_id IS NOT NULL)
        );
        CREATE INDEX idx_conversation_summary_events_session
            ON conversation_summary_events(session_id,created_at,id);
        CREATE INDEX idx_conversation_summary_events_run
            ON conversation_summary_events(run_id,created_at,id);
        CREATE INDEX idx_conversation_summary_events_revision
            ON conversation_summary_events(revision_id,created_at,id);
        """,
    ),
    (
        43,
        """
        ALTER TABLE conversation_summary_runs ADD COLUMN provider_id TEXT;
        ALTER TABLE conversation_summary_runs ADD COLUMN model TEXT;
        ALTER TABLE conversation_summary_runs ADD COLUMN provider_location TEXT
            CHECK(provider_location IS NULL OR provider_location IN ('local','remote','unknown'));
        ALTER TABLE conversation_summary_runs ADD COLUMN provider_location_revision INTEGER
            CHECK(provider_location_revision IS NULL OR provider_location_revision >= 1);
        ALTER TABLE conversation_summary_runs ADD COLUMN remote_history_allowed INTEGER NOT NULL
            DEFAULT 0 CHECK(remote_history_allowed IN (0,1));
        ALTER TABLE conversation_summary_runs ADD COLUMN generation_mode TEXT NOT NULL
            DEFAULT 'full' CHECK(generation_mode IN ('full','incremental'));
        ALTER TABLE conversation_summary_runs ADD COLUMN base_revision_id TEXT;
        ALTER TABLE conversation_summary_runs ADD COLUMN input_chars INTEGER
            CHECK(input_chars IS NULL OR input_chars >= 0);
        ALTER TABLE conversation_summary_runs ADD COLUMN output_chars INTEGER
            CHECK(output_chars IS NULL OR output_chars >= 0);
        ALTER TABLE conversation_summary_runs ADD COLUMN prompt_tokens INTEGER
            CHECK(prompt_tokens IS NULL OR prompt_tokens >= 0);
        ALTER TABLE conversation_summary_runs ADD COLUMN completion_tokens INTEGER
            CHECK(completion_tokens IS NULL OR completion_tokens >= 0);
        ALTER TABLE conversation_summary_runs ADD COLUMN latency_ms INTEGER
            CHECK(latency_ms IS NULL OR latency_ms >= 0);
        ALTER TABLE conversation_summary_runs ADD COLUMN repair_attempted INTEGER NOT NULL
            DEFAULT 0 CHECK(repair_attempted IN (0,1));
        """,
    ),
    (
        44,
        """
        CREATE VIRTUAL TABLE conversation_history_sessions_fts USING fts5(
            session_id UNINDEXED,
            title,
            summary_text,
            tokenize='trigram'
        );
        CREATE VIRTUAL TABLE conversation_history_messages_fts USING fts5(
            message_id UNINDEXED,
            session_id UNINDEXED,
            content,
            tokenize='trigram'
        );

        INSERT INTO conversation_history_sessions_fts(session_id,title,summary_text)
        SELECT s.id,s.title,COALESCE((
            SELECT r.summary_text FROM conversation_summary_revisions r
            WHERE r.session_id=s.id AND r.status='active' LIMIT 1
        ),'') FROM sessions s;
        INSERT INTO conversation_history_messages_fts(message_id,session_id,content)
        SELECT id,session_id,content FROM messages;

        CREATE TRIGGER conversation_history_session_insert AFTER INSERT ON sessions BEGIN
            INSERT INTO conversation_history_sessions_fts(session_id,title,summary_text)
            VALUES(NEW.id,NEW.title,'');
        END;
        CREATE TRIGGER conversation_history_session_title_update AFTER UPDATE OF title ON sessions BEGIN
            DELETE FROM conversation_history_sessions_fts WHERE session_id=OLD.id;
            INSERT INTO conversation_history_sessions_fts(session_id,title,summary_text)
            SELECT NEW.id,NEW.title,COALESCE((
                SELECT summary_text FROM conversation_summary_revisions
                WHERE session_id=NEW.id AND status='active' LIMIT 1
            ),'');
        END;
        CREATE TRIGGER conversation_history_session_delete AFTER DELETE ON sessions BEGIN
            DELETE FROM conversation_history_sessions_fts WHERE session_id=OLD.id;
            DELETE FROM conversation_history_messages_fts WHERE session_id=OLD.id;
        END;
        CREATE TRIGGER conversation_history_message_insert AFTER INSERT ON messages BEGIN
            INSERT INTO conversation_history_messages_fts(message_id,session_id,content)
            VALUES(NEW.id,NEW.session_id,NEW.content);
        END;
        CREATE TRIGGER conversation_history_message_update AFTER UPDATE OF content ON messages BEGIN
            DELETE FROM conversation_history_messages_fts WHERE message_id=OLD.id;
            INSERT INTO conversation_history_messages_fts(message_id,session_id,content)
            VALUES(NEW.id,NEW.session_id,NEW.content);
        END;
        CREATE TRIGGER conversation_history_message_delete AFTER DELETE ON messages BEGIN
            DELETE FROM conversation_history_messages_fts WHERE message_id=OLD.id;
        END;
        CREATE TRIGGER conversation_history_summary_insert AFTER INSERT ON conversation_summary_revisions
        WHEN NEW.status='active' BEGIN
            DELETE FROM conversation_history_sessions_fts WHERE session_id=NEW.session_id;
            INSERT INTO conversation_history_sessions_fts(session_id,title,summary_text)
            SELECT s.id,s.title,NEW.summary_text FROM sessions s WHERE s.id=NEW.session_id;
        END;
        CREATE TRIGGER conversation_history_summary_status_update
        AFTER UPDATE OF status ON conversation_summary_revisions BEGIN
            DELETE FROM conversation_history_sessions_fts WHERE session_id=NEW.session_id;
            INSERT INTO conversation_history_sessions_fts(session_id,title,summary_text)
            SELECT s.id,s.title,COALESCE((
                SELECT summary_text FROM conversation_summary_revisions
                WHERE session_id=NEW.session_id AND status='active' LIMIT 1
            ),'') FROM sessions s WHERE s.id=NEW.session_id;
        END;

        CREATE TABLE conversation_history_recall_events (
            id TEXT PRIMARY KEY,
            current_session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            query_sha256 TEXT NOT NULL,
            intent TEXT NOT NULL,
            mode TEXT NOT NULL CHECK(mode IN ('off','explicit_only','shadow','on')),
            status TEXT NOT NULL CHECK(status IN ('off','no_candidates','shadow','injected')),
            score_version TEXT NOT NULL,
            candidate_session_count INTEGER NOT NULL DEFAULT 0,
            candidate_turn_count INTEGER NOT NULL DEFAULT 0,
            injected_turn_count INTEGER NOT NULL DEFAULT 0,
            diagnostic_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_conversation_history_recall_events_session
            ON conversation_history_recall_events(current_session_id,created_at,id);
        """,
    ),
    (
        45,
        """
        CREATE TABLE context_package_events (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            user_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            package_protocol_version TEXT NOT NULL,
            budget_protocol_version TEXT NOT NULL,
            context_window_tokens INTEGER NOT NULL,
            output_reserve_tokens INTEGER NOT NULL,
            trimmed_messages INTEGER NOT NULL DEFAULT 0,
            trimmed_rounds INTEGER NOT NULL DEFAULT 0,
            trim_reason TEXT NOT NULL CHECK(trim_reason IN ('none','budget')),
            summary_revision INTEGER,
            source_type_counts_json TEXT NOT NULL DEFAULT '{}',
            component_tokens_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_context_package_events_session
            ON context_package_events(session_id,created_at,id);
        """,
    ),
    (
        46,
        """
        CREATE TABLE IF NOT EXISTS message_attachments (
            id TEXT PRIMARY KEY,
            message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            mime_type TEXT,
            content_text TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            char_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_message_attachments_message
            ON message_attachments(message_id);
        """,
    ),
    (
        47,
        """
        -- 知识库引用策略陪伴化：非敏感文档默认直接引用（remote_allowed），不再每次询问
        -- 敏感文档保持 ask_each_time，由 knowledge_policy.update_document_policy 强制
        UPDATE knowledge_documents
            SET transmission_policy='remote_allowed',
                policy_revision=policy_revision+1,
                policy_updated_at=updated_at
            WHERE sensitivity='normal' AND transmission_policy='ask_each_time';
        UPDATE knowledge_collections
            SET default_transmission_policy='remote_allowed',
                policy_revision=policy_revision+1,
                policy_updated_at=updated_at
            WHERE default_transmission_policy='ask_each_time';
        INSERT INTO knowledge_document_policy_events(
            id, document_id, before_policy, after_policy, policy_revision,
            actor, reason_code, created_at
        )
        SELECT
            lower(hex(randomblob(16))),
            id,
            'ask_each_time',
            'remote_allowed',
            policy_revision,
            'system',
            'migration_47_companion_default',
            updated_at
        FROM knowledge_documents
        WHERE sensitivity='normal'
            AND transmission_policy='remote_allowed'
            AND policy_revision > 1;
        INSERT OR IGNORE INTO settings(key, value)
            VALUES('knowledge_default_policy', 'remote_allowed');
        """,
    ),
    (
        48,
        """
        -- EAP v0.2：为 affect_observer_runs 增加 source_hash 字段
        -- 与 conversation_summary_runs.source_hash 对齐，用于 EAP 各阶段
        -- 引用 affect 观察结果时的来源校验。
        -- DEFAULT '' 兼容已有行（affect-observer-v1 已冻结，不修改写入逻辑）；
        -- 新行由 EAP 各阶段在引用时按需计算并写入。
        ALTER TABLE affect_observer_runs ADD COLUMN source_hash TEXT NOT NULL DEFAULT '';
        """,
    ),
    (
        49,
        """
        -- EAP v0.2 Conversation Presence v2：用户在线状态、离开原因、open_thread
        -- 与 affect-observer-v1 的 user_status 4 值枚举互补，扩展为 8 值
        -- 每个 session 最多一条 active 记录（is_active=1），历史记录保留用于审计
        CREATE TABLE conversation_presence (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            user_status TEXT NOT NULL CHECK(user_status IN (
                'online', 'away_brief', 'away_sleep', 'away_busy',
                'away_extended', 'ended_conversation', 'do_not_disturb', 'unknown'
            )),
            detected_at REAL NOT NULL,
            expires_at REAL,
            expected_return_at REAL,
            open_thread INTEGER NOT NULL DEFAULT 0,
            open_thread_topic TEXT,
            source_message_id TEXT,
            priority INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
            FOREIGN KEY(source_message_id) REFERENCES messages(id) ON DELETE SET NULL
        );
        CREATE INDEX idx_conversation_presence_session_active
            ON conversation_presence(session_id, is_active);
        CREATE INDEX idx_conversation_presence_expires
            ON conversation_presence(expires_at)
            WHERE is_active = 1;
        """,
    ),
    (
        50,
        """
        -- EAP v0.2 关系意义判断：LLM 输出 9 种关系意义标签，程序映射为受限 delta
        -- 与 saga_relationship_delta_suggestions 独立（不修改已冻结的 saga 表）
        -- 幂等：UNIQUE(source_message_id) 确保同一消息只产生一条建议
        CREATE TABLE episode_relationship_delta_suggestions (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            source_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            episode_id TEXT REFERENCES memory_episodes(id) ON DELETE SET NULL,
            relationship_label TEXT NOT NULL CHECK(relationship_label IN (
                'ordinary_exchange', 'shared_appreciation', 'reliable_help',
                'shared_success', 'vulnerable_disclosure', 'boundary_respected',
                'boundary_repair', 'reunion', 'conflict'
            )),
            bond_delta REAL NOT NULL CHECK(bond_delta BETWEEN -0.01 AND 0.005),
            familiarity_delta REAL NOT NULL CHECK(familiarity_delta BETWEEN 0 AND 0.003),
            trust_delta REAL NOT NULL CHECK(trust_delta BETWEEN -0.01 AND 0.005),
            attachment_delta REAL NOT NULL CHECK(attachment_delta BETWEEN 0 AND 0.003),
            rapport_delta REAL NOT NULL CHECK(rapport_delta BETWEEN -0.005 AND 0.003),
            cap_bond_applied REAL NOT NULL DEFAULT 0,
            cap_trust_applied REAL NOT NULL DEFAULT 0,
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed','applied','revoked')),
            applied_at REAL,
            revoked_at REAL,
            revocation_reason TEXT,
            protocol_version TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_episode_rel_delta_session
            ON episode_relationship_delta_suggestions(session_id, created_at);
        CREATE INDEX idx_episode_rel_delta_source
            ON episode_relationship_delta_suggestions(source_message_id);
        """,
    ),
    (
        51,
        """
        -- EAP v0.2 ContactEpisode：同一话题的连续主动管理（spec 第 5.7 节）
        -- 状态机 10 值：proposed/waiting/approached/deferred/quiet_waiting/responded/closed/expired/cancelled/blocked
        -- 承载 unanswered_pressure 累积与衰减（spec 第 5.9 节）
        CREATE TABLE contact_episodes (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            topic TEXT NOT NULL,
            origin_type TEXT NOT NULL CHECK(origin_type IN (
                'expected_return', 'emotional_care', 'milestone', 'life_share'
            )),
            source_refs TEXT NOT NULL DEFAULT '{}',  -- JSON: 来源消息 ID、Episode ID、Saga ID
            open_thread TEXT,  -- 用户回来后可自然衔接的事情
            first_candidate_at REAL,
            last_approach_at REAL,
            approach_count INTEGER NOT NULL DEFAULT 0 CHECK(approach_count >= 0),
            unanswered_pressure REAL NOT NULL DEFAULT 0 CHECK(unanswered_pressure >= 0),
            current_intensity INTEGER NOT NULL DEFAULT 0
                CHECK(current_intensity BETWEEN 0 AND 5),
            status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN (
                'proposed', 'waiting', 'approached', 'deferred',
                'quiet_waiting', 'responded', 'closed', 'expired', 'cancelled', 'blocked'
            )),
            expires_at REAL,  -- 最大生命周期
            outcome TEXT CHECK(outcome IS NULL OR outcome IN (
                'replied', 'ignored', 'rejected', 'expired', 'cancelled'
            )),
            protocol_version TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_contact_episodes_session_status
            ON contact_episodes(session_id, status);
        CREATE INDEX idx_contact_episodes_expiry
            ON contact_episodes(expires_at)
            WHERE status IN ('proposed', 'waiting', 'approached', 'deferred', 'quiet_waiting');
        """,
    ),
    (
        52,
        """
        -- EAP v0.2 Proactive Candidate：本地候选生成（spec 第 6.3 节决策流程第 1 步）
        CREATE TABLE proactive_candidates (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            episode_id TEXT REFERENCES contact_episodes(id) ON DELETE SET NULL,
            candidate_kind TEXT NOT NULL CHECK(candidate_kind IN (
                'chat_continuation', 'return_followup', 'emotional_care',
                'milestone_followup', 'casual_greeting', 'life_share'
            )),
            topic TEXT NOT NULL,
            source_refs TEXT NOT NULL DEFAULT '{}',
            open_thread TEXT,
            source_hash TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
                'pending', 'evaluating', 'approved', 'deferred',
                'suppressed', 'abandoned', 'delivered'
            )),
            expires_at REAL,
            protocol_version TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_proactive_candidates_session_status
            ON proactive_candidates(session_id, status);
        CREATE INDEX idx_proactive_candidates_episode
            ON proactive_candidates(episode_id);

        -- EAP v0.2 Proactive Decision：三层硬门 + LLM 结构化建议 + Shadow 基线（spec 第 6.3 节）
        CREATE TABLE proactive_decisions (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL REFERENCES proactive_candidates(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            decision TEXT NOT NULL CHECK(decision IN ('send', 'defer', 'suppress', 'abandon')),
            intensity INTEGER CHECK(intensity IS NULL OR intensity BETWEEN 0 AND 5),
            expression_act TEXT CHECK(expression_act IS NULL OR expression_act IN (
                'playful_complaint', 'gentle_urge', 'firm_care',
                'worried_checkin', 'expectant_followup', 'quiet_waiting'
            )),
            topic TEXT,
            confidence REAL NOT NULL DEFAULT 0.0 CHECK(confidence BETWEEN 0 AND 1),
            reason_codes TEXT NOT NULL DEFAULT '[]',
            source_refs TEXT NOT NULL DEFAULT '[]',
            layer1_blocked INTEGER NOT NULL DEFAULT 0,
            layer1_block_reasons TEXT NOT NULL DEFAULT '[]',
            layer2_deferred INTEGER NOT NULL DEFAULT 0,
            layer2_defer_reasons TEXT NOT NULL DEFAULT '[]',
            layer3_factors TEXT NOT NULL DEFAULT '{}',
            approach_drive REAL NOT NULL DEFAULT 0.0,
            contact_cost REAL NOT NULL DEFAULT 0.0,
            effective_drive REAL NOT NULL DEFAULT 0.0,
            approach_value REAL NOT NULL DEFAULT 0.0,
            shadow_score REAL,
            is_shadow INTEGER NOT NULL DEFAULT 0,
            llm_raw_response TEXT,
            idempotency_key TEXT NOT NULL UNIQUE,
            protocol_version TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_proactive_decisions_candidate
            ON proactive_decisions(candidate_id);
        CREATE INDEX idx_proactive_decisions_session_created
            ON proactive_decisions(session_id, created_at);
        """,
    ),
    (
        53,
        """
        -- EAP v0.2 主动强度阶梯：记录决策最终选择的强度（spec 第 5.10 节）
        CREATE TABLE proactive_intensity_plans (
            id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL REFERENCES proactive_decisions(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 5),
            channel TEXT NOT NULL CHECK(channel IN (
                'silent', 'live2d', 'bubble', 'chat', 'desktop_notification', 'external'
            )),
            is_minimum_sufficient INTEGER NOT NULL DEFAULT 1,
            live2d_action TEXT,  -- JSON: {gaze: ..., expression: ..., motion: ...}
            bubble_text TEXT,    -- Level 2 的气泡文本（如适用）
            reason TEXT NOT NULL DEFAULT '',
            protocol_version TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_proactive_intensity_plans_decision
            ON proactive_intensity_plans(decision_id);
        CREATE INDEX idx_proactive_intensity_plans_session_created
            ON proactive_intensity_plans(session_id, created_at);
        """,
    ),
    (
        54,
        """
        -- EAP v0.2 ExpressionPlan：7 维连续表达向量 + 迟滞参数（spec 第 5.11 节）
        CREATE TABLE expression_plans (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            decision_id TEXT REFERENCES proactive_decisions(id) ON DELETE SET NULL,
            intensity_plan_id TEXT REFERENCES proactive_intensity_plans(id) ON DELETE SET NULL,
            -- 7 维连续表达向量（每维 0.0~1.0）
            warmth REAL NOT NULL DEFAULT 0.5 CHECK(warmth BETWEEN 0 AND 1),
            playfulness REAL NOT NULL DEFAULT 0.5 CHECK(playfulness BETWEEN 0 AND 1),
            directness REAL NOT NULL DEFAULT 0.5 CHECK(directness BETWEEN 0 AND 1),
            concern REAL NOT NULL DEFAULT 0.5 CHECK(concern BETWEEN 0 AND 1),
            initiative REAL NOT NULL DEFAULT 0.5 CHECK(initiative BETWEEN 0 AND 1),
            restraint REAL NOT NULL DEFAULT 0.5 CHECK(restraint BETWEEN 0 AND 1),
            energy REAL NOT NULL DEFAULT 0.5 CHECK(energy BETWEEN 0 AND 1),
            -- 迟滞参数
            minimum_state_duration REAL NOT NULL DEFAULT 30.0,  -- 秒
            hysteresis_margin REAL NOT NULL DEFAULT 0.1,        -- 0.0~1.0
            transition_momentum REAL NOT NULL DEFAULT 0.5,      -- 0.0~1.0
            -- ExpressionPlan 作用范围标记（5 项可调整）
            adjusts_tone INTEGER NOT NULL DEFAULT 1,
            adjusts_length INTEGER NOT NULL DEFAULT 1,
            adjusts_directness INTEGER NOT NULL DEFAULT 1,
            adjusts_live2d_intensity INTEGER NOT NULL DEFAULT 1,
            adjusts_voice_prosody INTEGER NOT NULL DEFAULT 0,  -- 未来语音韵律，默认关
            -- 禁区标记（5 项不可修改）
            modifies_facts INTEGER NOT NULL DEFAULT 0,
            modifies_safety INTEGER NOT NULL DEFAULT 0,
            modifies_tool_results INTEGER NOT NULL DEFAULT 0,
            modifies_permissions INTEGER NOT NULL DEFAULT 0,
            modifies_user_boundary INTEGER NOT NULL DEFAULT 0,
            -- 元数据
            expression_act TEXT,
            source_hash TEXT NOT NULL DEFAULT '',  -- 输入源的哈希
            idempotency_key TEXT NOT NULL UNIQUE,
            protocol_version TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_expression_plans_session_created
            ON expression_plans(session_id, created_at);
        CREATE INDEX idx_expression_plans_decision
            ON expression_plans(decision_id);

        -- EAP v0.2 心境状态转换历史：用于迟滞检查（spec 第 5.11 节）
        CREATE TABLE expression_state_transitions (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            state_kind TEXT NOT NULL CHECK(state_kind IN (
                'mood_cluster', 'guardedness_level', 'expression_vector'
            )),
            from_state TEXT NOT NULL,
            to_state TEXT NOT NULL,
            from_value REAL,  -- 数值型状态的旧值（如 guardedness 0.0~1.0）
            to_value REAL,    -- 新值
            transition_at REAL NOT NULL,
            hysteresis_applied INTEGER NOT NULL DEFAULT 0,  -- 是否因迟滞被拒绝转换
            rejection_reason TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_expression_state_transitions_session
            ON expression_state_transitions(session_id, created_at);
        CREATE INDEX idx_expression_state_transitions_kind
            ON expression_state_transitions(state_kind, transition_at);
        """,
    ),
    (
        55,
        """
        -- EAP v0.2 LIFE 接入：proactive seed 接收队列（spec 第 8.2 节）
        -- LIFE 专项将生活事件投递到此表，EAP 消费并建立 ContactEpisode
        -- 本阶段只定义接口，LIFE 专项启动后实际写入
        CREATE TABLE life_proactive_seeds (
            id TEXT PRIMARY KEY,
            source_event_type TEXT NOT NULL CHECK(source_event_type IN (
                'life_event', 'personal_goal', 'important_date', 'diary_entry', 'self_timeline'
            )),
            source_event_id TEXT NOT NULL,  -- LIFE 侧的事件 ID
            source_event_summary TEXT NOT NULL,  -- 事件摘要（不超过 200 字符）
            topic TEXT NOT NULL,  -- EAP 用来建立 ContactEpisode 的 topic
            origin_type TEXT NOT NULL CHECK(origin_type IN (
                'expected_return', 'emotional_care', 'milestone', 'life_share'
            )),
            -- 边界约束字段
            seed_kind TEXT NOT NULL DEFAULT 'life_share' CHECK(seed_kind = 'life_share'),
            -- seed 来源版本（用于幂等和审计）
            source_revision TEXT NOT NULL DEFAULT '',
            source_hash TEXT NOT NULL DEFAULT '',
            -- EAP 消费状态
            consumed_at REAL,  -- 如非 NULL，表示已被 EAP 消费
            consumed_episode_id TEXT REFERENCES contact_episodes(id) ON DELETE SET NULL,
            consumed_candidate_id TEXT REFERENCES proactive_candidates(id) ON DELETE SET NULL,
            -- 拒绝标记（如 EAP 判断不适合接近，可标记 rejected）
            rejected_at REAL,
            rejection_reason TEXT,
            -- 元数据
            idempotency_key TEXT NOT NULL UNIQUE,
            protocol_version TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            CHECK(
                (consumed_at IS NULL AND consumed_episode_id IS NULL AND consumed_candidate_id IS NULL)
                OR
                (consumed_at IS NOT NULL AND consumed_episode_id IS NOT NULL)
            )
        );
        CREATE INDEX idx_life_proactive_seeds_consumed
            ON life_proactive_seeds(consumed_at)
            WHERE consumed_at IS NULL;
        CREATE INDEX idx_life_proactive_seeds_source
            ON life_proactive_seeds(source_event_type, source_event_id);
        CREATE UNIQUE INDEX idx_life_proactive_seeds_source_unique
            ON life_proactive_seeds(source_event_type, source_event_id, source_revision);
        """,
    ),
    (
        56,
        """
        CREATE TABLE IF NOT EXISTS decision_runs (
            id TEXT PRIMARY KEY,
            task_kind TEXT NOT NULL,
            protocol_version TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_revision TEXT NOT NULL DEFAULT '',
            source_hash TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN (
                'queued','running','applied','recovery_pending','exhausted','skipped'
            )),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
            max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts > 0),
            next_attempt_at REAL,
            provider_id TEXT,
            model_id TEXT,
            latency_ms INTEGER CHECK(latency_ms IS NULL OR latency_ms >= 0),
            input_tokens INTEGER CHECK(input_tokens IS NULL OR input_tokens >= 0),
            output_tokens INTEGER CHECK(output_tokens IS NULL OR output_tokens >= 0),
            error_code TEXT,
            warnings_json TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            completed_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_decision_runs_recovery
            ON decision_runs(status, next_attempt_at, updated_at);
        CREATE INDEX IF NOT EXISTS idx_decision_runs_source
            ON decision_runs(source_type, source_id, source_revision);
        """,
    ),
    (
        57,
        """
        -- EAP.R2: revision-aware relationship suggestions and cognition result audit.
        ALTER TABLE episode_relationship_delta_suggestions
            RENAME TO episode_relationship_delta_suggestions_v56;
        DROP INDEX IF EXISTS idx_episode_rel_delta_session;
        DROP INDEX IF EXISTS idx_episode_rel_delta_source;

        CREATE TABLE episode_relationship_delta_suggestions (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            source_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            source_assistant_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            episode_id TEXT REFERENCES memory_episodes(id) ON DELETE SET NULL,
            relationship_label TEXT NOT NULL CHECK(relationship_label IN (
                'ordinary_exchange', 'shared_appreciation', 'reliable_help',
                'shared_success', 'vulnerable_disclosure', 'boundary_respected',
                'boundary_repair', 'reunion', 'conflict'
            )),
            bond_delta REAL NOT NULL CHECK(bond_delta BETWEEN -0.01 AND 0.005),
            familiarity_delta REAL NOT NULL CHECK(familiarity_delta BETWEEN 0 AND 0.003),
            trust_delta REAL NOT NULL CHECK(trust_delta BETWEEN -0.01 AND 0.005),
            attachment_delta REAL NOT NULL CHECK(attachment_delta BETWEEN 0 AND 0.003),
            rapport_delta REAL NOT NULL CHECK(rapport_delta BETWEEN -0.005 AND 0.003),
            cap_bond_applied REAL NOT NULL DEFAULT 0,
            cap_trust_applied REAL NOT NULL DEFAULT 0,
            source_revision TEXT NOT NULL DEFAULT '',
            source_hash TEXT NOT NULL DEFAULT '',
            evidence_json TEXT NOT NULL DEFAULT '[]',
            reason TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0 CHECK(confidence BETWEEN 0 AND 1),
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'proposed'
                CHECK(status IN ('proposed','applied','revoked')),
            applied_event_id TEXT REFERENCES affect_events(id) ON DELETE SET NULL,
            revocation_event_id TEXT REFERENCES affect_events(id) ON DELETE SET NULL,
            applied_at REAL,
            revoked_at REAL,
            revocation_reason TEXT,
            protocol_version TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(source_message_id, source_revision)
        );
        INSERT INTO episode_relationship_delta_suggestions(
            id,session_id,source_message_id,episode_id,relationship_label,
            bond_delta,familiarity_delta,trust_delta,attachment_delta,rapport_delta,
            cap_bond_applied,cap_trust_applied,idempotency_key,status,applied_at,
            revoked_at,revocation_reason,protocol_version,created_at,updated_at
        ) SELECT
            id,session_id,source_message_id,episode_id,relationship_label,
            bond_delta,familiarity_delta,trust_delta,attachment_delta,rapport_delta,
            cap_bond_applied,cap_trust_applied,idempotency_key,status,applied_at,
            revoked_at,revocation_reason,protocol_version,created_at,updated_at
        FROM episode_relationship_delta_suggestions_v56;
        DROP TABLE episode_relationship_delta_suggestions_v56;
        CREATE INDEX idx_episode_rel_delta_session
            ON episode_relationship_delta_suggestions(session_id, created_at);
        CREATE INDEX idx_episode_rel_delta_source
            ON episode_relationship_delta_suggestions(source_message_id, source_revision);

        CREATE TABLE companion_cognition_results (
            run_id TEXT PRIMARY KEY REFERENCES decision_runs(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            source_user_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            source_assistant_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            source_revision TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            user_affect_json TEXT NOT NULL,
            relationship_label TEXT NOT NULL,
            relationship_suggestion_id TEXT
                REFERENCES episode_relationship_delta_suggestions(id) ON DELETE SET NULL,
            protocol_version TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_companion_cognition_results_source
            ON companion_cognition_results(session_id, source_assistant_message_id);
        """,
    ),
    (
        58,
        """
        -- EAP.R3: recoverable source queue, candidate leases and shadow orchestration saga.
        ALTER TABLE proactive_candidates ADD COLUMN source_revision TEXT NOT NULL DEFAULT '';
        ALTER TABLE proactive_candidates ADD COLUMN due_at REAL;
        ALTER TABLE proactive_candidates ADD COLUMN runtime_source_id TEXT;
        CREATE INDEX idx_proactive_candidates_due
            ON proactive_candidates(status, due_at, expires_at);
        CREATE UNIQUE INDEX idx_proactive_candidates_runtime_source
            ON proactive_candidates(runtime_source_id) WHERE runtime_source_id IS NOT NULL;

        CREATE TABLE proactive_runtime_sources (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            source_kind TEXT NOT NULL CHECK(source_kind IN (
                'expected_return','emotional_care','episode_milestone',
                'saga_milestone','casual_greeting','life_seed'
            )),
            source_ref_id TEXT NOT NULL,
            source_revision TEXT NOT NULL,
            source_hash TEXT NOT NULL CHECK(length(source_hash) = 64),
            payload_json TEXT NOT NULL DEFAULT '{}',
            due_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN (
                'queued','claimed','processed','skipped','expired'
            )),
            lease_owner TEXT,
            lease_expires_at REAL,
            candidate_id TEXT REFERENCES proactive_candidates(id) ON DELETE SET NULL,
            result_code TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(source_kind, source_ref_id, source_revision)
        );
        CREATE INDEX idx_proactive_runtime_sources_due
            ON proactive_runtime_sources(status, due_at, lease_expires_at);

        CREATE TABLE proactive_candidate_claims (
            candidate_id TEXT PRIMARY KEY REFERENCES proactive_candidates(id) ON DELETE CASCADE,
            source_revision TEXT NOT NULL,
            lease_owner TEXT NOT NULL,
            lease_expires_at REAL NOT NULL,
            claimed_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_proactive_candidate_claims_expiry
            ON proactive_candidate_claims(lease_expires_at);

        CREATE TABLE proactive_runtime_sagas (
            candidate_id TEXT PRIMARY KEY REFERENCES proactive_candidates(id) ON DELETE CASCADE,
            source_revision TEXT NOT NULL,
            decision_id TEXT REFERENCES proactive_decisions(id) ON DELETE SET NULL,
            intensity_plan_id TEXT REFERENCES proactive_intensity_plans(id) ON DELETE SET NULL,
            expression_plan_id TEXT REFERENCES expression_plans(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'claimed' CHECK(status IN (
                'claimed','decided','planned','completed','recovery_pending','skipped'
            )),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
            max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10),
            next_attempt_at REAL,
            error_code TEXT,
            gate_before_json TEXT NOT NULL DEFAULT '{}',
            gate_after_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_proactive_runtime_sagas_recovery
            ON proactive_runtime_sagas(status, next_attempt_at, updated_at);
        """,
    ),
    (
        59,
        """
        -- EAP.R4: auditable at-most-once local delivery ledger.
        ALTER TABLE messages ADD COLUMN proactive_delivery_id TEXT;
        CREATE UNIQUE INDEX idx_messages_proactive_delivery
            ON messages(proactive_delivery_id) WHERE proactive_delivery_id IS NOT NULL;

        CREATE TABLE proactive_deliveries (
            id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL UNIQUE REFERENCES proactive_decisions(id) ON DELETE CASCADE,
            candidate_id TEXT NOT NULL REFERENCES proactive_candidates(id) ON DELETE CASCADE,
            episode_id TEXT REFERENCES contact_episodes(id) ON DELETE SET NULL,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 4),
            channel TEXT NOT NULL CHECK(channel IN (
                'silent','live2d','bubble','chat','desktop_notification'
            )),
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64),
            authorization_revision INTEGER NOT NULL CHECK(authorization_revision >= 0),
            authorization_hash TEXT NOT NULL CHECK(length(authorization_hash) = 64),
            source_revision TEXT NOT NULL,
            source_hash TEXT NOT NULL CHECK(length(source_hash) = 64),
            status TEXT NOT NULL CHECK(status IN (
                'queued','claimed','delivering','delivered','failed',
                'cancelled','suppressed','expired'
            )),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 1),
            lease_owner TEXT,
            lease_token TEXT,
            lease_expires_at REAL,
            error_code TEXT,
            delivered_at REAL,
            acknowledged_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_proactive_deliveries_claim
            ON proactive_deliveries(status, lease_expires_at, created_at);
        CREATE INDEX idx_proactive_deliveries_session
            ON proactive_deliveries(session_id, created_at);

        CREATE TABLE proactive_delivery_attempts (
            id TEXT PRIMARY KEY,
            delivery_id TEXT NOT NULL REFERENCES proactive_deliveries(id) ON DELETE CASCADE,
            attempt_no INTEGER NOT NULL CHECK(attempt_no = 1),
            consumer_id TEXT NOT NULL,
            lease_token TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'claimed','delivering','delivered','failed','uncertain'
            )),
            error_code TEXT,
            claimed_at REAL NOT NULL,
            invocation_started_at REAL,
            confirmed_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(delivery_id, attempt_no)
        );
        CREATE INDEX idx_proactive_delivery_attempts_delivery
            ON proactive_delivery_attempts(delivery_id, created_at);

        CREATE TABLE proactive_delivery_events (
            id TEXT PRIMARY KEY,
            delivery_id TEXT NOT NULL REFERENCES proactive_deliveries(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            reason_code TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_proactive_delivery_events_delivery
            ON proactive_delivery_events(delivery_id, created_at, id);
        """,
    ),
    (
        60,
        """
        -- EAP.R5: grounded proactive feedback and learned local preferences.
        CREATE TABLE proactive_feedback (
            id TEXT PRIMARY KEY,
            delivery_id TEXT NOT NULL REFERENCES proactive_deliveries(id) ON DELETE CASCADE,
            episode_id TEXT REFERENCES contact_episodes(id) ON DELETE SET NULL,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            feedback_kind TEXT NOT NULL CHECK(feedback_kind IN (
                'wrong_timing','too_frequent','wrong_content','reject_topic',
                'reject_tone','allow_more'
            )),
            source TEXT NOT NULL CHECK(source IN ('explicit','natural_language')),
            status TEXT NOT NULL CHECK(status IN ('pending','applied','rejected','revoked')),
            evidence_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            evidence_quote TEXT CHECK(evidence_quote IS NULL OR length(evidence_quote) BETWEEN 1 AND 160),
            target_topic TEXT,
            target_kind TEXT,
            target_expression_act TEXT,
            confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
            policy_effect_json TEXT NOT NULL DEFAULT '{}',
            idempotency_key TEXT NOT NULL UNIQUE,
            protocol_version TEXT NOT NULL,
            resolved_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_proactive_feedback_delivery
            ON proactive_feedback(delivery_id, created_at);
        CREATE INDEX idx_proactive_feedback_pending
            ON proactive_feedback(status, created_at);

        CREATE TABLE proactive_preference_weights (
            id TEXT PRIMARY KEY,
            dimension TEXT NOT NULL CHECK(dimension IN ('topic','kind','expression_act')),
            value TEXT NOT NULL,
            contact_cost_delta REAL NOT NULL DEFAULT 0 CHECK(contact_cost_delta BETWEEN -1 AND 1),
            acceptance_delta REAL NOT NULL DEFAULT 0 CHECK(acceptance_delta BETWEEN -1 AND 1),
            source_feedback_id TEXT REFERENCES proactive_feedback(id) ON DELETE SET NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(dimension, value)
        );

        CREATE TABLE proactive_feedback_events (
            id TEXT PRIMARY KEY,
            feedback_id TEXT NOT NULL REFERENCES proactive_feedback(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_proactive_feedback_events_feedback
            ON proactive_feedback_events(feedback_id, created_at, id);
        """,
    ),
    (
        61,
        """
        -- CDS.1: extend the shared Schema 56 DecisionRun instead of creating a parallel ledger.
        ALTER TABLE decision_runs ADD COLUMN policy_version TEXT NOT NULL DEFAULT '';
        ALTER TABLE decision_runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'legacy'
            CHECK(mode IN ('legacy','shadow','advisory','active'));
        ALTER TABLE decision_runs ADD COLUMN provider_location TEXT;
        ALTER TABLE decision_runs ADD COLUMN source_snapshot_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE decision_runs ADD COLUMN snapshot_hash TEXT NOT NULL DEFAULT '';
        ALTER TABLE decision_runs ADD COLUMN candidate_snapshot_hash TEXT NOT NULL DEFAULT '';
        ALTER TABLE decision_runs ADD COLUMN candidate_count INTEGER NOT NULL DEFAULT 0
            CHECK(candidate_count >= 0);
        ALTER TABLE decision_runs ADD COLUMN selected_count INTEGER NOT NULL DEFAULT 0
            CHECK(selected_count >= 0 AND selected_count <= candidate_count);
        ALTER TABLE decision_runs ADD COLUMN action TEXT;
        ALTER TABLE decision_runs ADD COLUMN confidence_band TEXT;
        ALTER TABLE decision_runs ADD COLUMN reason_codes_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE decision_runs ADD COLUMN fallback_used INTEGER NOT NULL DEFAULT 0
            CHECK(fallback_used IN (0,1));
        ALTER TABLE decision_runs ADD COLUMN prompt_template_hash TEXT NOT NULL DEFAULT '';
        ALTER TABLE decision_runs ADD COLUMN input_schema_hash TEXT NOT NULL DEFAULT '';
        ALTER TABLE decision_runs ADD COLUMN output_schema_hash TEXT NOT NULL DEFAULT '';
        ALTER TABLE decision_runs ADD COLUMN validator_version TEXT NOT NULL DEFAULT '';
        ALTER TABLE decision_runs ADD COLUMN fallback_version TEXT NOT NULL DEFAULT '';
        ALTER TABLE decision_runs ADD COLUMN model_binding_revision TEXT NOT NULL DEFAULT '';
        ALTER TABLE decision_runs ADD COLUMN temperature REAL;
        ALTER TABLE decision_runs ADD COLUMN top_p REAL;
        ALTER TABLE decision_runs ADD COLUMN retention_class TEXT NOT NULL DEFAULT 'operational';
        ALTER TABLE decision_runs ADD COLUMN expires_at REAL;
        ALTER TABLE decision_runs ADD COLUMN privacy_scope TEXT NOT NULL DEFAULT 'body_free';
        ALTER TABLE decision_runs ADD COLUMN aggregate_after_expiry INTEGER NOT NULL DEFAULT 1
            CHECK(aggregate_after_expiry IN (0,1));

        CREATE TABLE decision_run_events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES decision_runs(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            mode TEXT NOT NULL CHECK(mode IN ('legacy','shadow','advisory','active')),
            error_code TEXT,
            warning_codes_json TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_decision_run_events_run
            ON decision_run_events(run_id, created_at, id);
        CREATE INDEX idx_decision_runs_diagnostics
            ON decision_runs(task_kind, mode, created_at);
        CREATE INDEX idx_decision_runs_expiry
            ON decision_runs(expires_at)
            WHERE expires_at IS NOT NULL;
        """,
    ),
    (
        62,
        """
        -- CDS.2: model routing, certification, circuit breakers and body-free budgets.
        ALTER TABLE decision_runs ADD COLUMN logical_role TEXT NOT NULL DEFAULT 'legacy'
            CHECK(logical_role IN ('legacy','fast','reasoning','creative'));
        ALTER TABLE decision_runs ADD COLUMN provider_location_revision INTEGER
            CHECK(provider_location_revision IS NULL OR provider_location_revision >= 1);
        ALTER TABLE decision_runs ADD COLUMN certification_level TEXT NOT NULL DEFAULT 'unverified'
            CHECK(certification_level IN (
                'unverified','structured_capable','decision_verified','local_sensitive_verified'
            ));

        CREATE TABLE cognition_model_certifications (
            id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            provider_location TEXT NOT NULL CHECK(provider_location IN ('local','remote','unknown')),
            provider_location_revision INTEGER NOT NULL CHECK(provider_location_revision >= 1),
            logical_role TEXT NOT NULL CHECK(logical_role IN ('fast','reasoning','creative')),
            decision_kind TEXT NOT NULL,
            protocol_version TEXT NOT NULL,
            model_binding_revision TEXT NOT NULL,
            certification_level TEXT NOT NULL CHECK(certification_level IN (
                'unverified','structured_capable','decision_verified','local_sensitive_verified'
            )),
            probe_version TEXT NOT NULL,
            last_error_code TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(provider_id,model_id,provider_location,provider_location_revision,logical_role,
                   decision_kind,protocol_version,model_binding_revision)
        );

        CREATE TABLE cognition_circuit_breakers (
            id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            decision_kind TEXT NOT NULL,
            protocol_version TEXT NOT NULL,
            model_binding_revision TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('closed','open','half_open')),
            consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_failures >= 0),
            open_until REAL,
            last_error_code TEXT,
            updated_at REAL NOT NULL,
            UNIQUE(provider_id,model_id,decision_kind,protocol_version,model_binding_revision)
        );

        CREATE TABLE cognition_budget_events (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            decision_kind TEXT NOT NULL,
            logical_role TEXT NOT NULL CHECK(logical_role IN ('fast','reasoning','creative')),
            provider_location TEXT NOT NULL CHECK(provider_location IN ('local','remote','unknown')),
            priority TEXT NOT NULL CHECK(priority IN ('foreground','normal','background')),
            status TEXT NOT NULL CHECK(status IN ('authorized','completed','rejected','cancelled')),
            estimated_tokens INTEGER NOT NULL DEFAULT 0 CHECK(estimated_tokens >= 0),
            actual_tokens INTEGER CHECK(actual_tokens IS NULL OR actual_tokens >= 0),
            error_code TEXT,
            created_at REAL NOT NULL,
            completed_at REAL,
            UNIQUE(task_id)
        );
        CREATE INDEX idx_cognition_budget_window
            ON cognition_budget_events(created_at,provider_location,status);
        """,
    ),
    (
        63,
        """
        -- CDS.12: body-free feedback and per-decision calibration audit.
        CREATE TABLE cognition_calibration_profiles (
            decision_kind TEXT PRIMARY KEY,
            feedback_domain TEXT NOT NULL CHECK(feedback_domain IN (
                'recall','proactive','relationship','memory'
            )),
            profile_version TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision >= 0),
            parameters_json TEXT NOT NULL DEFAULT '{}',
            feedback_count INTEGER NOT NULL DEFAULT 0 CHECK(feedback_count >= 0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE cognition_feedback_signals (
            id TEXT PRIMARY KEY,
            decision_kind TEXT NOT NULL,
            feedback_domain TEXT NOT NULL CHECK(feedback_domain IN (
                'recall','proactive','relationship','memory'
            )),
            feedback_kind TEXT NOT NULL CHECK(feedback_kind IN (
                'helpful','not_helpful','missing','wrong_source','quick_reply','later_reply',
                'unanswered','rejected','corrected'
            )),
            source_run_id TEXT REFERENCES decision_runs(id) ON DELETE SET NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            parameter_delta_json TEXT NOT NULL DEFAULT '{}',
            profile_revision INTEGER NOT NULL CHECK(profile_revision >= 1),
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_cognition_feedback_decision
            ON cognition_feedback_signals(decision_kind,created_at);

        CREATE TABLE cognition_calibration_events (
            id TEXT PRIMARY KEY,
            decision_kind TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN ('feedback_applied','profile_rolled_back')),
            from_revision INTEGER NOT NULL CHECK(from_revision >= 0),
            to_revision INTEGER NOT NULL CHECK(to_revision > from_revision),
            changes_json TEXT NOT NULL DEFAULT '{}',
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_cognition_calibration_events_decision
            ON cognition_calibration_events(decision_kind,created_at);
        """,
    ),
    (
        64,
        """
        -- LIFE.2: provenance-aware LifeEvent ledger. Existing tool_logs rows are the
        -- canonical local ToolRun evidence; LIFE does not create another tool ledger.
        CREATE TABLE life_events (
            id TEXT PRIMARY KEY,
            event_kind TEXT NOT NULL CHECK(event_kind IN (
                'state_transition','activity','agent_action','observation','date_marker'
            )),
            world_layer TEXT NOT NULL CHECK(world_layer IN (
                'planned','simulated','observed','performed'
            )),
            lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN (
                'active','superseded','revoked'
            )),
            current_revision INTEGER NOT NULL DEFAULT 1 CHECK(current_revision >= 1),
            tool_run_id TEXT REFERENCES tool_logs(id) ON DELETE RESTRICT,
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX idx_life_events_timeline
            ON life_events(world_layer,lifecycle_status,created_at,id);

        CREATE TABLE life_event_revisions (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES life_events(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL CHECK(revision >= 1),
            event_kind TEXT NOT NULL,
            world_layer TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            attributes_json TEXT NOT NULL DEFAULT '{}',
            change_reason_code TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(event_id,revision)
        );

        CREATE TABLE life_event_sources (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES life_events(id) ON DELETE CASCADE,
            source_kind TEXT NOT NULL CHECK(source_kind IN (
                'life_event','diary_entry','important_date','personal_goal','self_timeline',
                'tool_run','user_statement','system_observation'
            )),
            source_id TEXT NOT NULL,
            source_revision TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            created_at REAL NOT NULL,
            removed_at REAL,
            UNIQUE(event_id,source_kind,source_id,source_revision)
        );
        CREATE INDEX idx_life_event_sources_lookup
            ON life_event_sources(source_kind,source_id,active);

        CREATE TABLE life_event_audit_events (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES life_events(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL CHECK(event_type IN (
                'created','corrected','revoked','source_removed'
            )),
            from_status TEXT,
            to_status TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision >= 1),
            reason_code TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_life_event_audit_event
            ON life_event_audit_events(event_id,created_at,id);
        """,
    ),
]

# 默认供应商：全部 OpenAI-Compatible。api_key 开发期存本地库，
# 正式版迁移到系统安全存储（见需求 MODEL-005）。
DEFAULT_PROVIDERS = [
    ("mock",        "内置演示",    "",                                          ["xiadie-mock"], 1),
    ("deepseek",    "DeepSeek",    "https://api.deepseek.com/v1",               ["deepseek-chat", "deepseek-reasoner"], 0),
    ("openai",      "OpenAI",      "https://api.openai.com/v1",                 ["gpt-4o-mini", "gpt-4o"], 0),
    ("glm",         "智谱 GLM",    "https://open.bigmodel.cn/api/paas/v4",      ["glm-4-flash", "glm-4-plus"], 0),
    ("qwen",        "通义千问",    "https://dashscope.aliyuncs.com/compatible-mode/v1", ["qwen-plus", "qwen-turbo"], 0),
    ("kimi",        "Kimi",        "https://api.moonshot.cn/v1",                ["moonshot-v1-8k"], 0),
    ("openrouter",  "OpenRouter",  "https://openrouter.ai/api/v1",              ["openrouter/auto"], 0),
    ("siliconflow", "硅基流动",    "https://api.siliconflow.cn/v1",             ["Qwen/Qwen2.5-7B-Instruct"], 0),
    ("ollama",      "Ollama 本地", "http://127.0.0.1:11434/v1",                 ["qwen2.5:7b"], 0),
    ("custom",      "自定义接口",  "",                                          [], 0),
]


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _apply_migrations(conn)
        for i, (pid, name, base_url, models, enabled) in enumerate(DEFAULT_PROVIDERS):
            default_location = (
                "local" if pid in {"mock", "ollama"}
                else "unknown" if pid == "custom" else "remote"
            )
            conn.execute(
                "INSERT OR IGNORE INTO providers("
                "id,name,base_url,models,enabled,sort,execution_location) VALUES(?,?,?,?,?,?,?)",
                (pid, name, base_url, json.dumps(models, ensure_ascii=False), enabled, i,
                 default_location),
            )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('current_model', ?)",
            (json.dumps({"provider_id": "mock", "model": "xiadie-mock"}),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('memory_enabled', ?)",
            (DEFAULT_MEMORY_ENABLED,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value)"
            " VALUES('affect_observer_model', '{\"mode\":\"current\"}')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value)"
            " VALUES('memory_observer_model', '{\"mode\":\"current\"}')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value)"
            " VALUES('knowledge_local_embedding_enabled', '1')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value)"
            " VALUES('knowledge_shadow_recall_enabled', '1')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value)"
            " VALUES('knowledge_recall_mode', 'explicit')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value)"
            " VALUES('conversation_history_recall_mode', 'explicit_only')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value)"
            " VALUES('conversation_summary_injection_enabled', '1')"
        )
        # EAP v0.2：本机主动陪伴默认开启（spec 第 3.4 节）
        # - 主窗口内主动消息：默认开启
        # - 桌宠气泡和轻提示：默认开启
        # - Live2D 无文字表达：默认开启
        # - Windows 系统通知：首次使用时询问（默认 0，前端引导用户授权）
        # - QQ、微信、邮件等外部渠道：必须逐渠道明确授权（默认 0）
        from .proactive.settings import DEFAULTS as proactive_defaults
        for key, value in proactive_defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (key, value)
            )
        conn.commit()
    finally:
        conn.close()


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """按版本顺序执行幂等迁移；未版本化的开发库从 0 开始。"""
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    version = int(row["value"]) if row else 0
    for target, sql in MIGRATIONS:
        if target <= version:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(target),),
        )
        version = target


def get_setting(key: str, default: str = "") -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()
