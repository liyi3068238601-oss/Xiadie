"""SQLite 存储层：会话、消息、记忆、任务、供应商、设置、工具日志。

本地优先原则：所有数据保存在 backend/data/xiadie.db。
"""
import json
import os
import sqlite3
import time
import uuid

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
            conn.execute(
                "INSERT OR IGNORE INTO providers(id, name, base_url, models, enabled, sort)"
                " VALUES(?,?,?,?,?,?)",
                (pid, name, base_url, json.dumps(models, ensure_ascii=False), enabled, i),
            )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('current_model', ?)",
            (json.dumps({"provider_id": "mock", "model": "xiadie-mock"}),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('memory_enabled', '1')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value)"
            " VALUES('affect_observer_model', '{\"mode\":\"current\"}')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value)"
            " VALUES('memory_observer_model', '{\"mode\":\"current\"}')"
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
