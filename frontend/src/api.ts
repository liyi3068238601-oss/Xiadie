import { dispatchChatSseEvent } from "./chatSseProtocol";

// 后端 API 客户端。dev 期指向本地 FastAPI，可被 Electron 注入的全局覆盖。
export const API_BASE: string =
  (window as any).__XIADIE_API__ || "http://127.0.0.1:8756";

function requestHeaders(init?: RequestInit): Headers {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const token = (window as any).xiadie?.getApiToken?.();
  if (token) headers.set("X-Xiadie-Token", token);
  return headers;
}

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(API_BASE + path, {
    ...init,
    headers: requestHeaders(init),
  });
  if (!r.ok) {
    let detail: string | { code?: string; message?: string } = r.statusText;
    try {
      detail = (await r.json()).detail || detail;
    } catch {
      /* ignore */
    }
    if (typeof detail === "object") {
      throw new ApiError(r.status, detail.message || r.statusText, detail.code);
    }
    throw new ApiError(r.status, detail);
  }
  return r.status === 204 ? (undefined as T) : r.json();
}

// ---- 类型 ----
export interface Session {
  id: string;
  title: string;
  archived: number;
  temporary: number;
  message_count?: number;
  updated_at: number;
}
export interface LifeScheduleSegment {
  id: string;
  ordinal: number;
  start_minute: number;
  end_minute: number;
  activity_code: string;
  label: string;
  detail_status: "coarse" | "detailed" | "cancelled";
  detail_revision: number;
}
export interface LifeSchedule {
  id: string;
  local_date: string;
  timezone_id: string;
  revision: number;
  status: "active" | "replaced" | "disabled";
  algorithm_version: string;
  segments: LifeScheduleSegment[];
}
export function getLifeSchedule(localDate: string, timezoneId = "Asia/Shanghai") {
  return j<{ item: LifeSchedule | null }>(
    `/api/life/schedules/${encodeURIComponent(localDate)}?timezone_id=${encodeURIComponent(timezoneId)}`,
  );
}
export type LifeContinuityMode = "continuous_simulated" | "paused" | "disabled";
export interface LifeSettings {
  mode: LifeContinuityMode;
  offline_continuity_default: LifeContinuityMode;
  short_memo: {
    enabled: boolean;
    rollout_mode: "off" | "shadow" | "active";
    rollout_epoch: number;
    remote_extraction_enabled: boolean;
    default_ttl_seconds: number;
    max_active: number;
    max_recall: number;
  };
}
export interface ShortMemoItem {
  id: string;
  content: string;
  topic_keys: string[];
  source_session_id: string;
  source_message_id: string;
  source_session_title?: string;
  sensitivity: "normal" | "sensitive_minimized";
  revision: number;
  created_at: number;
  updated_at: number;
  expires_at: number;
}
export interface LifeState {
  initialized: boolean;
  algorithm_version: string;
  revision?: number;
  timezone_id?: string;
  current_activity?: string;
  energy?: number;
  focus?: number;
  rest_need?: number;
  social_openness?: number;
  conservative_mode?: boolean;
  anomaly_code?: string | null;
}
export interface LifeDiaryEntry {
  id: string;
  entry_date: string;
  status: "active" | "revoked" | "rebuilding";
  sensitivity: "normal" | "sensitive";
  share_policy: "private" | "ask" | "natural" | "never";
  revision: number;
  title: string;
  body: string;
}
export interface LifeImportantDate {
  id: string;
  label: string;
  status: "candidate" | "active" | "revoked";
  recurrence: "once" | "yearly_solar";
  date_year: number | null;
  date_month: number | null;
  date_day: number | null;
  timezone_id: string;
  celebration_policy: "natural" | "day_only" | "none";
  revision: number;
}
export interface LifeGoal {
  id: string;
  title: string;
  status: "candidate" | "active" | "paused" | "completed" | "revoked";
  priority: number;
  revision: number;
}
export interface LifeDiagnostics {
  schema_version: string;
  state_revision: number | null;
  state_algorithm: string;
  anomaly_code: string | null;
  counts: Record<string, number>;
  sources: Array<{
    source_type: string;
    source_id: string;
    source_revision: string;
    source_status: string;
  }>;
}
export const getLifeSettings = () => j<LifeSettings>("/api/life/settings");
export const updateLifeSettings = (mode: LifeContinuityMode) =>
  j<LifeSettings>("/api/life/settings", {
    method: "PATCH", body: JSON.stringify({ mode }),
  });
export const updateShortMemoSettings = (body: {
  short_memo_enabled?: boolean;
  short_memo_remote_extraction_enabled?: boolean;
  short_memo_default_ttl_seconds?: number;
}) => j<LifeSettings>("/api/life/settings", { method: "PATCH", body: JSON.stringify(body) });
export const listShortMemos = () => j<{ items: ShortMemoItem[] }>("/api/life/short-memos");
export const updateShortMemo = (id: string, body: { expected_revision: number; expires_at: number }) =>
  j<ShortMemoItem>(`/api/life/short-memos/${encodeURIComponent(id)}`, {
    method: "PATCH", body: JSON.stringify(body),
  });
export const deleteShortMemo = (id: string) =>
  j<{ deleted: boolean }>(`/api/life/short-memos/${encodeURIComponent(id)}`, { method: "DELETE" });
export const clearShortMemos = (privacy = false) =>
  j<{ deleted_count: number }>("/api/life/short-memos", {
    method: "DELETE", body: JSON.stringify({ privacy, clear_events: privacy }),
  });
export const getLifeState = () => j<LifeState>("/api/life/state");
export const listLifeDiary = () => j<{ items: LifeDiaryEntry[] }>("/api/life/diary");
export const updateLifeDiary = (id: string, body: { expected_revision: number; title: string; body: string }) =>
  j<LifeDiaryEntry>(`/api/life/diary/${encodeURIComponent(id)}`, {
    method: "PATCH", body: JSON.stringify(body),
  });
export const deleteLifeDiary = (id: string, revision: number) =>
  j<LifeDiaryEntry>(`/api/life/diary/${encodeURIComponent(id)}?expected_revision=${revision}`, {
    method: "DELETE",
  });
export const listLifeDates = () => j<{ items: LifeImportantDate[] }>("/api/life/dates");
export const createLifeDate = (body: {
  label: string; recurrence: "once" | "yearly_solar"; date_year?: number | null;
  date_month: number; date_day: number; timezone_id?: string;
  celebration_policy?: LifeImportantDate["celebration_policy"];
}) => j<LifeImportantDate>("/api/life/dates", { method: "POST", body: JSON.stringify(body) });
export const updateLifeDate = (id: string, body: {
  expected_revision: number; label: string; celebration_policy: LifeImportantDate["celebration_policy"];
}) => j<LifeImportantDate>(`/api/life/dates/${encodeURIComponent(id)}`, {
  method: "PATCH", body: JSON.stringify(body),
});
export const deleteLifeDate = (id: string, revision: number) =>
  j<LifeImportantDate>(`/api/life/dates/${encodeURIComponent(id)}?expected_revision=${revision}`, {
    method: "DELETE",
  });
export const listLifeGoals = () => j<{ items: LifeGoal[] }>("/api/life/goals");
export const createLifeGoal = (title: string, priority = 3) =>
  j<LifeGoal>("/api/life/goals", { method: "POST", body: JSON.stringify({ title, priority }) });
export const updateLifeGoal = (id: string, body: {
  expected_revision: number; title?: string; status?: LifeGoal["status"];
}) => j<LifeGoal>(`/api/life/goals/${encodeURIComponent(id)}`, {
  method: "PATCH", body: JSON.stringify(body),
});
export const deleteLifeGoal = (id: string, revision: number) =>
  j<LifeGoal>(`/api/life/goals/${encodeURIComponent(id)}?expected_revision=${revision}`, {
    method: "DELETE",
  });
export const rebuildLifeViews = () =>
  j<{ affected_diaries: number; timeline_entries: number }>("/api/life/rebuild", { method: "POST" });
export const exportLifeData = () => j<Record<string, unknown>>("/api/life/export");
export const getLifeDiagnostics = () => j<LifeDiagnostics>("/api/life/diagnostics");
export interface Message {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  model?: string;
  favorite: boolean;
  created_at: number;
  knowledge_citations?: KnowledgeCitation[];
  evidence_links?: EvidenceLink[];
  attachments?: ChatAttachmentResult[];
}
export interface KnowledgeCitation {
  id: string;
  citation_key: string;
  document_id: string;
  chunk_id: string;
  original_name: string;
  heading_path: string[];
  ordinal: number;
  paragraph_start: number;
  paragraph_end: number;
  line_start: number;
  line_end: number;
  char_start: number;
  char_end: number;
  page_start: number | null;
  page_end: number | null;
  content_fingerprint: string;
  content?: string;
}
export interface EvidenceLink {
  id: string;
  citation_key: string;
  source_kind: "message" | "memory_fragment" | "life_event" | "tool_run" | "lore_section";
  source_id: string;
  relation: "direct_support" | "partial_support" | "background" | "contradiction" | "example" | "definition";
  locator_snapshot: string;
  validation_status: "active" | "stale" | "missing" | "revoked" | "inaccessible" | "unsupported";
  content_fingerprint: string;
  source_label: string;
  available: boolean;
  content?: string;
  unavailable_reason?: string;
}
export interface ObserverModelConfig {
  mode: "current" | "dedicated";
  provider_id: string | null;
  model: string | null;
}

export interface ContextControls {
  reference_chat_history: boolean;
  summary_injection_enabled: boolean;
  summary_automatic: boolean;
  history_mode: "off" | "explicit_only" | "shadow" | "on";
  ordinary_history_recall: "shadow";
  memory_enabled: boolean;
}

export interface ContextPackageEvent {
  id: string;
  session_id: string;
  user_message_id?: string | null;
  package_protocol_version: string;
  budget_protocol_version: string;
  context_window_tokens: number;
  output_reserve_tokens: number;
  trimmed_messages: number;
  trimmed_rounds: number;
  trim_reason: "none" | "budget";
  summary_revision?: number | null;
  source_type_counts: Record<string, number>;
  component_tokens: Record<string, number>;
  created_at: number;
}

export interface ContextDiagnostics {
  controls: ContextControls;
  component_priority: string[];
  package_events: ContextPackageEvent[];
  history_events: Array<Record<string, unknown>>;
  summary_runs: Array<Record<string, unknown>>;
  summary_revisions: Array<Record<string, unknown>>;
  context_contributors: ContextContributorDiagnostics;
}

export interface ContextContributor {
  contributor_id: string;
  version: string;
  enabled: boolean;
  allowed_kinds: string[];
  allowed_privacy: string[];
  timeout_ms: number;
}

export interface ContextContributorRun {
  contributor_id: string;
  status: "ok" | "disabled" | "timeout" | "error";
  elapsed_ms: number;
  candidate_count: number;
  reason_code?: string | null;
}

export interface ContextContributorDiagnostics {
  protocol_version: "context-contribution-v1";
  contributors: ContextContributor[];
  recent_collections: Array<{
    request_id: string;
    created_at: number;
    candidate_count: number;
    accepted_count?: number | null;
    rejected_count?: number | null;
    rejected_reason_counts?: Record<string, number>;
    runs: ContextContributorRun[];
  }>;
}
export type { EmotionCluster } from "./affectPresentation.mjs";
export interface AffectState {
  contact_need: number;
  guardedness: number;
  guardedness_transient: number;
  valence: number;
  arousal: number;
  immersion: number;
  activity_type: string | null;
  activity_label: string | null;
  activity_started_at: number | null;
  last_user_message_at: number | null;
  last_tick_at: number;
  updated_at: number;
}
export interface RelationshipState {
  bond: number;
  trust: number;
  interaction_count: number;
  updated_at: number;
}
export interface DerivedCompanionState {
  cluster: import("./affectPresentation.mjs").EmotionCluster;
  label: string;
  guardedness: number;
  guardedness_band: string;
  guardedness_baseline: number;
  style_guidance: string;
}
export interface CompanionSignal {
  action: "observation" | "find_activity" | "consider_contact" | "contact" | string;
  urgency?: number;
  reason?: string;
}
export interface CompanionState {
  affect: AffectState;
  relationship: RelationshipState;
  derived: DerivedCompanionState;
  signals: CompanionSignal[];
  algorithm_version: string;
}
export interface CompanionStateEvent {
  id: string;
  event_type: string;
  source: string;
  reason: string;
  source_session_id?: string | null;
  source_message_id?: string | null;
  algorithm_version: string;
  before: {
    affect: Omit<AffectState, "guardedness" | "updated_at">;
    relationship: Omit<RelationshipState, "updated_at">;
  };
  delta: Record<string, unknown>;
  after: {
    affect: Omit<AffectState, "guardedness" | "updated_at">;
    relationship: Omit<RelationshipState, "updated_at">;
  };
  created_at: number;
}
export interface Memory {
  id: string;
  layer: "L0" | "L1" | "L2";
  content: string;
  tags: string;
  source: string;
  source_session_id?: string | null;
  source_message_id?: string | null;
  source_session_title?: string | null;
  source_available?: boolean;
  confidence?: number;
  sensitivity?: "normal" | "sensitive";
  status?: "active" | "cooling" | "frozen" | "tombstone";
  scope?: "user" | "self" | "relationship" | "world";
  kind?: "fact" | "preference" | "plan" | "experience" | "relationship" | "observation" | "correction";
  importance?: number;
  emotion?: string;
  inner_reason?: string;
  observer_version?: string;
  evidence_message_ids?: string[];
  source_assistant_message_id?: string | null;
  enabled: boolean;
  cooling_since?: number | null;
  frozen_at?: number | null;
  last_recalled_at?: number | null;
  recall_count?: number;
  lifecycle_revision?: number;
  last_archivist_evaluated_at?: number | null;
  created_at: number;
  updated_at: number;
}
export interface MemoryLifecycleEvent {
  id: string;
  from_status: string;
  to_status: string;
  reason_code: string;
  source: string;
  policy_version: string;
  created_at: number;
}
export interface MemoryRelation {
  id: string;
  source_fragment_id: string;
  target_fragment_id: string;
  source_content: string;
  target_content: string;
  entity_name: string;
  relation_type: "superseded" | "possible_conflict";
  status: "active" | "resolved" | "dismissed";
  confidence: number;
  rule_code: string;
  detector_version: string;
  model_version?: string | null;
  events: Array<{ id: string; action: string; source: string; reason_code: string; created_at: number }>;
}
export interface MemoryLifecycleDetail {
  fragment: Memory;
  evaluation: null | {
    fragment_id: string;
    policy_version: string;
    score: number;
    components: Record<string, number>;
    contributions: Record<string, number>;
    protection_reasons: string[];
    dependency_flags: Record<string, boolean>;
  };
  events: MemoryLifecycleEvent[];
  relations: MemoryRelation[];
}
export interface ArchivistRun {
  id: string;
  status: string;
  trigger: string;
  scanned_count: number;
  transitioned_count: number;
  conflict_count: number;
  relation_count: number;
  reason_code?: string | null;
  created_at: number;
  finished_at?: number | null;
}
export interface KnowledgeDocument {
  id: string;
  collection_id: string;
  original_name: string;
  extension: ".txt" | ".md" | string;
  mime_type: string;
  size_bytes: number;
  content_sha256: string;
  status: "staged" | "queued" | "parsing" | "indexed" | "failed" | "cancelled" |
    "delete_pending" | "delete_failed";
  sensitivity: "normal" | "sensitive";
  transmission_policy: "remote_allowed" | "ask_each_time" | "local_only";
  policy_revision: number;
  policy_updated_at?: number | null;
  embedding_mode: "none" | "local" | "remote";
  embedding_version?: string | null;
  embedding_indexed_at?: number | null;
  embedding_dimension?: number | null;
  embedding_error_code?: string | null;
  error_code?: string | null;
  parser_version?: string | null;
  parsed_at?: number | null;
  parse_char_count?: number;
  parse_line_count?: number;
  parse_heading_count?: number;
  chunker_version?: string | null;
  chunked_at?: number | null;
  chunk_count: number;
  tags: string[];
  index_version?: string | null;
  indexed_at?: number | null;
  latest_run?: KnowledgeImportRun | null;
  latest_deletion?: KnowledgeDeletionRun | null;
  latest_embedding?: KnowledgeEmbeddingRun | null;
  recall_count?: number;
  last_recalled_at?: number | null;
  citation_count?: number;
  created_at: number;
  updated_at: number;
}
export interface PWMEntity {
  id: string;
  entity_type: string;
  canonical_name: string;
  description: string;
  reality_scope: "reality" | "lore";
  confidence: number;
  status: "candidate" | "active" | "merged" | "split" | "archived" | "revoked";
  extraction_mode: "shadow";
  updated_at: number;
}
export interface PWMWorldEvent {
  id: string;
  event_type: string;
  title: string;
  summary: string;
  start_at: number | null;
  event_layer: string;
  execution_state: "planned" | "materialized" | "performed" | "inferred";
  status: string;
  created_at: number;
}
export interface KIGMaintenanceCandidate {
  id: string;
  candidate_type: string;
  object_kind: string;
  object_id: string;
  status: "proposed" | "confirmed" | "rejected" | "resolved" | "expired";
  requires_confirmation: 1;
  updated_at: number;
}
export interface PWMOverview {
  protocol_version: string;
  mode: "shadow";
  counts: Record<string, number>;
  settings: {
    enabled: boolean;
    shadow_extraction_enabled: boolean;
    maintenance_frequency: "off" | "daily" | "weekly";
    budget_policy: Record<string, number>;
  };
}
export interface KnowledgeEmbeddingRun {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "skipped";
  attempt_count: number;
  max_attempts: number;
  vector_count: number;
  error_code?: string | null;
  created_at: number;
  updated_at: number;
}
export interface KnowledgeEmbeddingStatus {
  available: boolean;
  provider_id: string;
  model: string;
  embedding_version: string;
  model_sha256: string;
  dimension: number;
  local_only: boolean;
  model_path_configured: boolean;
  dependencies_available: boolean;
  remote_requires_per_request_consent: boolean;
}
export interface KnowledgeCollection {
  id: string;
  name: string;
  description: string;
  status: "active" | "disabled";
  default_transmission_policy: KnowledgeDocument["transmission_policy"];
  policy_revision: number;
  policy_updated_at?: number | null;
  created_at: number;
  updated_at: number;
}
export interface KnowledgeDeletionEvent {
  id: string;
  action: string;
  before_status?: string | null;
  after_status: string;
  error_code?: string | null;
  created_at: number;
}
export interface KnowledgeDeletionRun {
  id: string;
  document_id: string;
  status: "queued" | "running" | "completed" | "failed";
  attempt_count: number;
  error_code?: string | null;
  events?: KnowledgeDeletionEvent[];
  created_at: number;
  updated_at: number;
}
export interface KnowledgeImpactPreview {
  document_id: string;
  action: "reindex" | "archive" | "restore" | "delete";
  chunk_count: number;
  embedding_count: number;
  citation_count: number;
  derived_dependency_count: number;
  removes_from_retrieval: boolean;
  preserves_original_file: boolean;
}
export interface KnowledgeRetrievalAudit {
  id: string;
  session_id: string;
  user_message_id?: string | null;
  assistant_message_id?: string | null;
  trigger_reason: string;
  query_fingerprint: string;
  candidate_count: number;
  injected_count: number;
  knowledge_tokens: number;
  knowledge_token_budget: number;
  lore_tokens: number;
  memory_tokens: number;
  status: "no_results" | "injected" | "completed" | "failed";
  search_protocol_version: string;
  audit_state: "active" | "minimized";
  minimized_at?: number | null;
  session_available: boolean;
  created_at: number;
  finished_at?: number | null;
}
export interface KnowledgeAuditLifecycle {
  policy_version: string;
  recall_decisions_days: number;
  terminal_grants_days: number;
  retrieval_metadata_days: number;
  citations: "message_lifetime";
  document_bodies_in_audit: false;
  expired_cited_retrieval_behavior: string;
  counts: Record<string, number>;
}
export interface KnowledgeExportManifest {
  manifest_version: string;
  contains_knowledge_body: false;
  contains_tokens: false;
  contains_vectors: false;
  collections: Array<Record<string, unknown>>;
  documents: Array<Record<string, unknown>>;
}
export interface KnowledgeRecallDecision {
  id: string;
  protocol_version: string;
  threshold_version: string;
  recall_mode: "explicit" | "smart";
  shadow: boolean;
  action: "skip" | "retrieve" | "ask";
  reason_code: string;
  confidence_band: "low" | "medium" | "high";
  query_fingerprint: string;
  policy_fingerprint: string;
  candidate_count: number;
  eligible_count: number;
  injected_count: number;
  retrieval_mode: "none" | "fts" | "vector" | "hybrid" | "fts_unavailable";
  vector_available: boolean;
  vector_error_code?: string | null;
  provider_location: "local" | "remote" | "unknown";
  provider_location_revision: number;
  latency_ms: number;
  status: "queued" | "completed" | "failed" | "timed_out";
  created_at: number;
  finished_at?: number | null;
}
export interface KnowledgeRecallStats {
  sample_count: number;
  scope: "global" | "session";
  action_counts: Record<"skip" | "retrieve" | "ask", number>;
  action_rates: Record<"skip" | "retrieve" | "ask", number>;
  reason_counts: Record<string, number>;
  latency_ms: { average: number; p50: number; p90: number; p99: number };
  vector_available_rate: number;
  timeout_rate: number;
}
export interface KnowledgeRecallSettings {
  mode: "off" | "explicit" | "smart";
  shadow_enabled: boolean;
  protocol_version: string;
  threshold_version: string;
  natural_token_budget: number;
  automatic_injection_enabled: boolean;
  answer_behavior: "disabled" | "explicit_unchanged" | "smart_high_confidence";
  stores_query_or_content: false;
}
export interface KnowledgeGrantDocument {
  id: string;
  name: string;
  policy: "remote_allowed" | "ask_each_time" | "local_only";
  sensitivity: "normal" | "sensitive";
  chunk_count: number;
  token_estimate: number;
}
export interface KnowledgeGrantPreflight {
  id: string | null;
  status: "not_needed" | "pending" | "issued" | "consumed" | "denied" | "expired" | "revoked";
  protocol_version?: string;
  recall_mode: "off" | "explicit" | "smart";
  provider: {
    id: string | null;
    model: string;
    location: "local" | "remote" | "unknown";
    location_revision: number;
  };
  documents: KnowledgeGrantDocument[];
  document_count: number;
  chunk_count: number;
  token_range: { min: number; max: number };
  single_use: boolean;
  can_allow_once: boolean;
  can_always_allow: boolean;
  stores_content: false;
}
export interface KnowledgeGrantResolution {
  id: string;
  status: "issued" | "policy_updated";
  token: string | null;
  expires_at?: number;
  single_use?: boolean;
  transmission_policy?: "remote_allowed" | "local_only";
}
export interface KnowledgeImportEvent {
  id: string;
  action: string;
  before_status?: string | null;
  after_status: string;
  stage: string;
  error_code?: string | null;
  metadata: Record<string, number | string>;
  created_at: number;
}
export interface KnowledgeImportRun {
  id: string;
  document_id?: string;
  status: "queued" | "running" | "cancel_requested" | "cancelled" | "completed" |
    "failed" | "recovery_pending";
  current_stage: "validation" | "copy" | "parsing" | "chunking" | "indexing" | "finalizing";
  progress: number;
  attempt_count?: number;
  max_attempts?: number;
  error_code?: string | null;
  next_attempt_at?: number | null;
  events?: KnowledgeImportEvent[];
}
export interface KnowledgeImportResult {
  document: KnowledgeDocument;
  run: KnowledgeImportRun | null;
  already_exists: boolean;
}
export interface KnowledgeSearchResult {
  chunk_id: string;
  document_id: string;
  collection_id: string;
  original_name: string;
  ordinal: number;
  content: string;
  content_sha256: string;
  tags: string[];
  heading_path: string[];
  paragraph_start: number;
  paragraph_end: number;
  line_start: number;
  line_end: number;
  char_start: number;
  char_end: number;
  page_start?: number | null;
  page_end?: number | null;
  match_type: "primary" | "context" | "vector" | "hybrid";
  context_of?: string | null;
  rank?: number | null;
  vector_score?: number | null;
  fusion_score?: number | null;
}
export interface KnowledgeSearchResponse {
  query: string;
  results: KnowledgeSearchResult[];
  result_count: number;
  used_chars: number;
  context_window: number;
  retrieval_mode?: "fts" | "vector" | "hybrid" | "fts_unavailable";
  vector_available?: boolean;
  vector_error_code?: string | null;
}
export interface MemoryCandidate {
  id: string;
  content: string;
  proposed_layer: "L0" | "L1" | "L2";
  tags: string;
  source_session_id?: string | null;
  source_message_id?: string | null;
  source_session_title?: string | null;
  source_available: boolean;
  confidence: number;
  sensitivity: "normal" | "sensitive";
  status: "pending" | "accepted" | "rejected";
  resolution_note: string;
  created_at: number;
}
export interface EntityFragment extends Memory {
  relation: string;
  confidence: number;
}
export interface MemoryEntity {
  id: string;
  name: string;
  entity_type: string;
  summary: string;
  aliases: string[];
  tags: string[];
  current_status: string;
  status_since: string;
  status: string;
  source: string;
  fragment_count: number;
  fragments?: EntityFragment[];
  updated_at: number;
}
export interface EpisodeFragment extends Memory {
  position: number;
}
export interface MemoryEpisode {
  id: string;
  title: string;
  summary: string;
  start_at: number;
  end_at: number;
  significance: number;
  confidence: number;
  status: "active" | "completed" | "archived" | "tombstone";
  source: "consolidator_auto" | "candidate_confirmed" | string;
  candidate_id?: string | null;
  grouping_fingerprint?: string | null;
  policy_version: string;
  source_fragment_ids: string[];
  source_hash: string;
  summary_status: "legacy_rule" | "extractive_fallback" | "model_validated" | "user_edited";
  summary_protocol_version: string;
  summary_provider_id?: string | null;
  summary_model?: string | null;
  summary_evidence_fragment_ids: string[];
  application_version: string;
  correction_note: string;
  corrected_at?: number | null;
  completed_at?: number | null;
  archived_at?: number | null;
  tombstoned_at?: number | null;
  lifecycle_policy_version?: string;
  lifecycle_revision: number;
  last_lifecycle_evaluated_at?: number | null;
  lifecycle_events?: Array<{
    id: string;
    revision: number;
    from_status: MemoryEpisode["status"];
    to_status: MemoryEpisode["status"];
    reason_code: string;
    source: string;
    policy_version: string;
    created_at: number;
  }>;
  fragment_count: number;
  fragments?: EpisodeFragment[];
  entities?: Array<{ id: string; name: string; entity_type: string }>;
  updated_at: number;
}
export interface EpisodeConsolidatorRun {
  id: string;
  trigger: "startup" | "idle" | "manual" | "fragment";
  status: "queued" | "running" | "cancel_requested" | "cancelled" | "applied" |
    "recovery_pending" | "exhausted" | "skipped";
  group_count: number;
  input_fragment_ids: string[];
  result_episode_ids: string[];
}
export type SagaStatus = "active" | "completed" | "archived" | "tombstone";
export interface SagaEpisodeSource {
  id: string;
  title: string;
  summary: string;
  start_at: number;
  end_at: number;
  status: "active" | "completed" | string;
  summary_status: MemoryEpisode["summary_status"];
  source_hash: string;
  fragments?: EpisodeFragment[];
}
export interface SagaTimelineItem {
  episode_id: string;
  position: number;
  role: "anchor" | "development" | "resolution" | string;
  added_at: number;
  removed_at: number | null;
  episode: SagaEpisodeSource | null;
}
export interface SagaEvent {
  id: string;
  action: string;
  reason_code: string | null;
  source: string;
  policy_version: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  metadata: Record<string, unknown>;
  created_at: number;
}
export interface MemorySaga {
  id: string;
  title: string;
  summary: string;
  theme: string;
  current_stage: string;
  start_at: number;
  end_at: number;
  significance: number;
  confidence: number;
  status: SagaStatus;
  source: string;
  grouping_fingerprint: string | null;
  policy_version: string;
  source_episode_ids: string[];
  source_hash: string;
  summary_status: MemoryEpisode["summary_status"];
  summary_protocol_version: string;
  summary_provider_id?: string | null;
  summary_model?: string | null;
  summary_evidence_episode_ids: string[];
  completion_evidence_episode_ids: string[];
  completion_reason: string;
  correction_note: string;
  corrected_at?: number | null;
  completed_at?: number | null;
  archived_at?: number | null;
  tombstoned_at?: number | null;
  revision: number;
  timeline?: SagaTimelineItem[];
  entities?: Array<{
    entity_id: string;
    name: string;
    entity_type: string;
    entity_status: string;
    relation: string;
  }>;
  events?: SagaEvent[];
}
export interface SagaConsolidatorRun {
  id: string;
  trigger: "startup" | "idle" | "weekly" | "manual" | "episode";
  status: "queued" | "running" | "cancel_requested" | "cancelled" | "applied" |
    "recovery_pending" | "exhausted" | "skipped";
  result_saga_ids: string[];
}
export interface Task {
  id: string;
  title: string;
  status: "todo" | "doing" | "done" | "archived";
  due_date?: string;
  source: string;
  source_session_id?: string;
  updated_at: number;
}
export interface Provider {
  id: string;
  name: string;
  base_url: string;
  models: string[];
  enabled: boolean;
  has_key: boolean;
  sort: number;
  execution_location: "local" | "remote" | "unknown";
  location_revision: number;
  location_confirmed_at?: number | null;
}
export interface CurrentModel {
  provider_id: string;
  provider_name: string;
  model: string;
  capabilities: string[];
}
export type CognitionMode = "off" | "shadow" | "advisory" | "active";
export type CognitionModelRole = "fast" | "reasoning" | "creative";
export interface CognitionModelBinding {
  provider_id: string;
  model: string;
}
export interface CognitionSettings {
  settings_version: string;
  enabled: boolean;
  diagnostics_visible: boolean;
  decision_modes: Record<string, CognitionMode>;
  mode_ceilings: Record<string, CognitionMode>;
  model_bindings: Partial<Record<CognitionModelRole, CognitionModelBinding>>;
  roles: CognitionModelRole[];
  natural_capabilities: string[];
  privacy: {
    raw_output_persisted: boolean;
    body_in_diagnostics: boolean;
    remote_body_bearing_requires_authorization: boolean;
  };
}
export interface CognitionDiagnosticSummary {
  decision_kind: string;
  run_count: number;
  fallback_count: number;
  latency_ms_median: number | null;
  latency_ms_max: number | null;
  error_codes: Record<string, number>;
}
export interface CognitionDiagnostics {
  diagnostic_version: string;
  protocol_version: string;
  registry_version: string;
  settings: CognitionSettings;
  summaries: CognitionDiagnosticSummary[];
  privacy: {
    body_persisted: boolean;
    prompt_persisted: boolean;
    raw_output_persisted: boolean;
    candidate_ids_exposed: boolean;
  };
}
export interface ToolLog {
  id: string;
  tool: string;
  risk_level: string;
  status: string;
  summary: string;
  created_at: number;
}

// ---- 会话 ----
export const listSessions = () => j<Session[]>("/api/sessions");
export const createSession = (temporary = false) =>
  j<Session>("/api/sessions", { method: "POST", body: JSON.stringify({ temporary }) });
export const renameSession = (id: string, title: string) =>
  j<Session>(`/api/sessions/${id}`, { method: "PATCH", body: JSON.stringify({ title }) });
export const deleteSession = (id: string) =>
  j<{ ok: boolean }>(`/api/sessions/${id}`, { method: "DELETE" });
export const listMessages = (id: string) =>
  j<Message[]>(`/api/sessions/${id}/messages`);
export const toggleFavorite = (mid: string) =>
  j<{ favorite: boolean }>(`/api/messages/${mid}/favorite`, { method: "POST" });
export const getKnowledgeCitation = (id: string) =>
  j<KnowledgeCitation>(`/api/knowledge/citations/${id}`);
export const getEvidenceLink = (id: string) =>
  j<EvidenceLink>(`/api/kig/evidence-links/${id}`);

// ---- 记忆 ----
export const listMemories = () => j<Memory[]>("/api/memories");
export const getMemoryStats = () =>
  j<{ L0: number; L1: number; L2: number }>("/api/memory/stats");
export const addMemory = (layer: string, content: string, tags = "") =>
  j<Memory>("/api/memories", { method: "POST", body: JSON.stringify({ layer, content, tags }) });
export const updateMemory = (id: string, body: Partial<Memory>) =>
  j<Memory>(`/api/memories/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const correctMemory = (id: string, content: string, note = "") =>
  j<Memory>(`/api/memories/${id}/correct`, {
    method: "POST",
    body: JSON.stringify({ content, note }),
  });
export const deleteMemory = (id: string) =>
  j<{ ok: boolean }>(`/api/memories/${id}`, { method: "DELETE" });
export const privacyDeleteMemory = (id: string) =>
  j<{ ok: boolean; privacy_cleared: boolean }>(`/api/memories/${id}?privacy=true`, {
    method: "DELETE",
  });
export const getMemoryLifecycle = (id: string) =>
  j<MemoryLifecycleDetail>(`/api/memories/${id}/lifecycle`);
export const restoreMemory = (id: string, expected_revision?: number, reason = "用户手动恢复") =>
  j<Memory>(`/api/memories/${id}/lifecycle`, {
    method: "POST",
    body: JSON.stringify({ target_status: "active", expected_revision, reason }),
  });
export const listMemoryRelations = (status = "active") =>
  j<MemoryRelation[]>(`/api/memory-relations?status=${encodeURIComponent(status)}`);
export const scanMemoryRelations = () =>
  j<{ created_count: number; superseded_count: number; possible_conflict_count: number }>(
    "/api/memory-relations/scan", { method: "POST" }
  );
export const setMemoryRelationStatus = (
  id: string, status: "resolved" | "dismissed", reason: string
) => j<MemoryRelation>(`/api/memory-relations/${id}/status`, {
  method: "POST",
  body: JSON.stringify({ status, reason }),
});
export const listArchivistRuns = (limit = 10) =>
  j<ArchivistRun[]>(`/api/archivist/runs?limit=${limit}`);

// ---- 用户文件知识库 ----
export const listKnowledgeDocuments = (options: {
  collection_id?: string; status?: string; query?: string;
} = {}) => {
  const params = new URLSearchParams();
  if (options.collection_id) params.set("collection_id", options.collection_id);
  if (options.status) params.set("status", options.status);
  if (options.query) params.set("query", options.query);
  return j<KnowledgeDocument[]>(`/api/knowledge/documents${params.size ? `?${params}` : ""}`);
};
export const listKnowledgeCollections = () =>
  j<KnowledgeCollection[]>("/api/knowledge/collections");
export const getPWMOverview = () =>
  j<PWMOverview>("/api/knowledge/world-model/summary");
export const listPWMEntities = (query = "", scope: "reality" | "lore" = "reality") =>
  j<{ items: PWMEntity[] }>(`/api/knowledge/world-model/entities?query=${encodeURIComponent(query)}&scope=${scope}`);
export const listPWMTimeline = () =>
  j<{ items: PWMWorldEvent[] }>("/api/knowledge/world-model/timeline");
export const listKIGMaintenance = () =>
  j<{ items: KIGMaintenanceCandidate[] }>("/api/knowledge/world-model/maintenance");
export const scanKIGMaintenance = () =>
  j<Record<string, number>>("/api/knowledge/world-model/maintenance/scan", { method: "POST" });
export const decideKIGMaintenance = (id: string, accepted: boolean) =>
  j<KIGMaintenanceCandidate>(`/api/knowledge/world-model/maintenance/${encodeURIComponent(id)}/decision`, {
    method: "POST", body: JSON.stringify({ accepted }),
  });
export const updatePWMSettings = (body: {
  enabled?: boolean; shadow_extraction_enabled?: boolean;
  maintenance_frequency?: "off" | "daily" | "weekly";
}) => j<PWMOverview["settings"]>("/api/knowledge/world-model/settings", {
  method: "PATCH", body: JSON.stringify(body),
});
export const updateKnowledgeCollectionPolicy = (
  id: string, default_transmission_policy: KnowledgeDocument["transmission_policy"],
  apply_existing: boolean,
) => j<KnowledgeCollection & { updated_document_count: number; revoked_grant_count: number }>(
  `/api/knowledge/collections/${encodeURIComponent(id)}/transmission-policy`, {
    method: "PATCH", body: JSON.stringify({ default_transmission_policy, apply_existing }),
  },
);
export const updateKnowledgeTags = (id: string, tags: string[]) =>
  j<KnowledgeDocument>(`/api/knowledge/documents/${id}/tags`, {
    method: "PATCH", body: JSON.stringify({ tags }),
  });
export const updateKnowledgeTransmissionPolicy = (
  id: string, transmission_policy: KnowledgeDocument["transmission_policy"],
) => j<KnowledgeDocument>(`/api/knowledge/documents/${id}/transmission-policy`, {
  method: "PATCH", body: JSON.stringify({ transmission_policy }),
});
export const reindexKnowledgeDocument = (id: string) =>
  j<KnowledgeImportRun>(`/api/knowledge/documents/${id}/reindex`, { method: "POST" });
export const deleteKnowledgeDocument = (id: string) =>
  j<KnowledgeDeletionRun>(`/api/knowledge/documents/${id}`, { method: "DELETE" });
export const getKnowledgeImpactPreview = (id: string, action: KnowledgeImpactPreview["action"]) =>
  j<KnowledgeImpactPreview>(
    `/api/knowledge/documents/${encodeURIComponent(id)}/impact-preview?action=${encodeURIComponent(action)}`,
  );
export const retryKnowledgeDeletion = (id: string) =>
  j<KnowledgeDeletionRun>(`/api/knowledge/deletion-runs/${id}/retry`, { method: "POST" });
export const getKnowledgeDeletionRun = (id: string) =>
  j<KnowledgeDeletionRun>(`/api/knowledge/deletion-runs/${id}`);
export const listKnowledgeRetrievals = (limit = 30) =>
  j<KnowledgeRetrievalAudit[]>(`/api/knowledge/retrievals?limit=${limit}`);
export const getKnowledgeAuditLifecycle = () =>
  j<KnowledgeAuditLifecycle>("/api/knowledge/audit-lifecycle");
export const getKnowledgeExportManifest = () =>
  j<KnowledgeExportManifest>("/api/knowledge/export-manifest");
export const clearAllKnowledge = (confirmation: string) =>
  j<{ status: string; queued_document_count: number }>("/api/knowledge/clear-all", {
    method: "POST", body: JSON.stringify({ confirmation }),
  });
export const listKnowledgeRecallDecisions = (limit = 30) =>
  j<KnowledgeRecallDecision[]>(`/api/knowledge/recall-decisions?limit=${limit}`);
export const getKnowledgeRecallStats = (sessionId?: string) =>
  j<KnowledgeRecallStats>(`/api/knowledge/recall-decisions/stats${
    sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""
  }`);
export const getKnowledgeRecallSettings = () =>
  j<KnowledgeRecallSettings>("/api/knowledge/recall/settings");
export const updateKnowledgeRecallSettings = (body: {
  mode?: KnowledgeRecallSettings["mode"];
  shadow_enabled?: boolean;
}) => j<KnowledgeRecallSettings>("/api/knowledge/recall/settings", {
  method: "PATCH", body: JSON.stringify(body),
});
export const preflightKnowledgeTransmission = (
  session_id: string, request_nonce: string, content: string,
  attachment_ids?: string[],
) => j<KnowledgeGrantPreflight>("/api/knowledge/recall/preflight", {
  method: "POST", body: JSON.stringify({
    session_id, request_nonce, content,
    attachment_ids: attachment_ids ?? [],
  }),
});
export const resolveKnowledgeTransmissionGrant = (body: {
  grant_id: string;
  action: "allow_once" | "always_allow" | "local_only";
  session_id: string;
  request_nonce: string;
  content: string;
}) => j<KnowledgeGrantResolution>("/api/knowledge/transmission-grants", {
  method: "POST", body: JSON.stringify(body),
});
export const denyKnowledgeTransmissionGrant = (grantId: string) =>
  j<{ id: string; status: "denied" }>(
    `/api/knowledge/transmission-grants/${encodeURIComponent(grantId)}/deny`,
    { method: "POST" },
  );
export async function importKnowledgeFile(
  file: File, sensitivity: "normal" | "sensitive" = "normal",
): Promise<KnowledgeImportResult> {
  const lower = file.name.toLowerCase();
  const fallbackMime = lower.endsWith(".md") ? "text/markdown" : lower.endsWith(".pdf")
    ? "application/pdf" : lower.endsWith(".docx")
      ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document" : "text/plain";
  const response = await fetch(API_BASE + "/api/knowledge/documents/import", {
    method: "POST",
    headers: requestHeaders({ headers: {
      "Content-Type": file.type || fallbackMime,
      "X-Xiadie-Filename": encodeURIComponent(file.name),
      "X-Xiadie-Collection": "default",
      "X-Xiadie-Sensitivity": sensitivity,
    }}),
    body: file,
  });
  if (!response.ok) {
    let detail = response.statusText;
    let code: string | undefined;
    try {
      const payload = await response.json();
      const structured = payload?.detail;
      if (typeof structured === "string") detail = structured;
      else if (structured?.message) detail = structured.message;
      code = structured?.code;
    } catch { /* ignore */ }
    throw new ApiError(response.status, detail, code);
  }
  return response.json();
}
export const getKnowledgeImportRun = (id: string) =>
  j<KnowledgeImportRun>(`/api/knowledge/import-runs/${id}`);
export const cancelKnowledgeImportRun = (id: string) =>
  j<KnowledgeImportRun>(`/api/knowledge/import-runs/${id}/cancel`, { method: "POST" });
export const getKnowledgeEmbeddingStatus = () =>
  j<KnowledgeEmbeddingStatus>("/api/knowledge/embedding/status");
export const buildKnowledgeEmbedding = (id: string) =>
  j<KnowledgeEmbeddingRun>(`/api/knowledge/documents/${id}/embedding`, { method: "POST" });
export const searchKnowledge = (query: string, options: Record<string, unknown> = {}) =>
  j<KnowledgeSearchResponse>("/api/knowledge/search", {
    method: "POST", body: JSON.stringify({ query, ...options }),
  });
export const listMemoryCandidates = () =>
  j<MemoryCandidate[]>("/api/memory-candidates?status=pending");
export const acceptMemoryCandidate = (
  id: string,
  body: { content?: string; layer?: string; tags?: string }
) =>
  j<{ candidate: MemoryCandidate; memory: Memory }>(`/api/memory-candidates/${id}/accept`, {
    method: "POST",
    body: JSON.stringify(body),
  });
export const rejectMemoryCandidate = (id: string, note = "") =>
  j<MemoryCandidate>(`/api/memory-candidates/${id}/reject`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });

// ---- 记忆实体 ----
export const listEntities = () => j<MemoryEntity[]>("/api/entities");
export const getEntity = (id: string) => j<MemoryEntity>(`/api/entities/${id}`);
export const addEntity = (body: {
  name: string;
  entity_type: string;
  aliases?: string[];
  summary?: string;
  tags?: string[];
}) => j<MemoryEntity>("/api/entities", { method: "POST", body: JSON.stringify(body) });
export const updateEntity = (id: string, body: Partial<MemoryEntity>) =>
  j<MemoryEntity>(`/api/entities/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteEntity = (id: string) =>
  j<{ ok: boolean }>(`/api/entities/${id}`, { method: "DELETE" });
export const linkEntityFragment = (id: string, fragment_id: string, relation = "mentions") =>
  j<MemoryEntity>(`/api/entities/${id}/links`, {
    method: "POST",
    body: JSON.stringify({ fragment_id, relation }),
  });
export const unlinkEntityFragment = (id: string, fragmentId: string) =>
  j<MemoryEntity>(`/api/entities/${id}/links/${fragmentId}`, { method: "DELETE" });
export const mergeEntity = (targetId: string, source_entity_id: string) =>
  j<MemoryEntity>(`/api/entities/${targetId}/merge`, {
    method: "POST",
    body: JSON.stringify({ source_entity_id }),
  });

// ---- Episode ----
export const generateEpisodeCandidates = () =>
  j<{ queued: boolean; run: EpisodeConsolidatorRun }>("/api/episode-candidates/generate", {
    method: "POST",
  });
export const listEpisodes = () => j<MemoryEpisode[]>("/api/episodes");
export const getEpisode = (id: string) => j<MemoryEpisode>(`/api/episodes/${id}`);
export const correctEpisode = (
  id: string,
  body: { title?: string; summary?: string; significance?: number; note?: string; expected_revision?: number }
) => j<MemoryEpisode>(`/api/episodes/${id}/correct`, {
  method: "POST",
  body: JSON.stringify(body),
});
export const transitionEpisode = (
  id: string, target_status: MemoryEpisode["status"], expected_revision: number, reason: string
) => j<MemoryEpisode>(`/api/episodes/${id}/lifecycle`, {
  method: "POST",
  body: JSON.stringify({ target_status, expected_revision, reason }),
});

// ---- Saga ----
export const listSagas = (status?: SagaStatus, limit = 100) => {
  const query = new URLSearchParams({ limit: String(limit) });
  if (status) query.set("status", status);
  return j<MemorySaga[]>(`/api/sagas?${query}`);
};
export const getSaga = (id: string) =>
  j<MemorySaga>(`/api/sagas/${encodeURIComponent(id)}`);
export const enqueueSagaConsolidator = (request_key?: string) =>
  j<SagaConsolidatorRun>("/api/saga-consolidator/runs", {
    method: "POST",
    body: JSON.stringify({ trigger: "manual", request_key }),
  });
export const correctSaga = (
  id: string,
  body: {
    title?: string;
    summary?: string;
    theme?: string;
    current_stage?: string;
    significance?: number;
    note?: string;
    expected_revision: number;
  }
) => j<MemorySaga>(`/api/sagas/${encodeURIComponent(id)}/correct`, {
  method: "POST",
  body: JSON.stringify(body),
});
export const correctSagaSources = (
  id: string,
  episode_ids: string[],
  note: string,
  expected_revision: number
) => j<MemorySaga>(`/api/sagas/${encodeURIComponent(id)}/correct-sources`, {
  method: "POST",
  body: JSON.stringify({ episode_ids, note, expected_revision }),
});
export const transitionSaga = (
  id: string,
  target_status: SagaStatus,
  reason: string,
  expected_revision: number
) => j<MemorySaga>(`/api/sagas/${encodeURIComponent(id)}/lifecycle`, {
  method: "POST",
  body: JSON.stringify({ target_status, reason, expected_revision }),
});

// ---- 任务 ----
export const listTasks = (today = false) =>
  j<Task[]>(`/api/tasks${today ? "?today=true" : ""}`);
export const createTask = (title: string, source_session_id?: string) =>
  j<Task>("/api/tasks", { method: "POST", body: JSON.stringify({ title, source_session_id }) });
export const updateTask = (id: string, body: Partial<Task>) =>
  j<Task>(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteTask = (id: string) =>
  j<{ ok: boolean }>(`/api/tasks/${id}`, { method: "DELETE" });

// ---- 模型 / 供应商 ----
export const listProviders = () => j<Provider[]>("/api/providers");
export const updateProvider = (id: string, body: any) =>
  j<Provider>(`/api/providers/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const testProvider = (provider_id: string, model: string) =>
  j<{ ok: boolean; message: string }>("/api/providers/test", {
    method: "POST",
    body: JSON.stringify({ provider_id, model }),
  });
export const discoverProviderModels = (provider_id: string, base_url: string, api_key = "") =>
  j<{ ok: boolean; models: string[]; message: string }>("/api/providers/discover-models", {
    method: "POST",
    body: JSON.stringify({ provider_id, base_url, api_key }),
  });
export const getCurrentModel = () => j<CurrentModel>("/api/current-model");
export const setCurrentModel = (provider_id: string, model: string) =>
  j<CurrentModel>("/api/current-model", {
    method: "POST",
    body: JSON.stringify({ provider_id, model }),
  });
export const getCognitionSettings = () =>
  j<CognitionSettings>("/api/cognition/settings");
export const updateCognitionSettings = (body: {
  enabled?: boolean;
  diagnostics_visible?: boolean;
  decision_modes?: Record<string, CognitionMode>;
  model_bindings?: Partial<Record<CognitionModelRole, CognitionModelBinding | null>>;
}) =>
  j<CognitionSettings>("/api/cognition/settings", {
    method: "PUT",
    body: JSON.stringify(body),
  });
export const rollbackCognitionSettings = () =>
  j<CognitionSettings>("/api/cognition/settings/rollback", { method: "POST", body: "{}" });
export const getCognitionDiagnostics = (limit = 100) =>
  j<CognitionDiagnostics>(`/api/cognition/diagnostics/v2?limit=${limit}`);
export const getObserverModel = () =>
  j<ObserverModelConfig>("/api/companion-state/observer-model");
export const setObserverModel = (body: ObserverModelConfig) =>
  j<ObserverModelConfig>("/api/companion-state/observer-model", {
    method: "PUT",
    body: JSON.stringify(body),
  });
export const getMemoryObserverModel = () =>
  j<ObserverModelConfig>("/api/memory-observer/model");
export const setMemoryObserverModel = (body: ObserverModelConfig) =>
  j<ObserverModelConfig>("/api/memory-observer/model", {
    method: "PUT",
    body: JSON.stringify(body),
  });
export interface MemoryObserverResult {
  id: string;
  status: "queued" | "running" | "validated" | "applied" | "recovery_pending" | "exhausted" | "skipped";
  error_code: string | null;
  created_count: number;
  remembered_count: number;
}
export const getMemoryObserverResult = (id: string) =>
  j<MemoryObserverResult>(`/api/memory-observer/runs/${encodeURIComponent(id)}/result`);
export const getCompanionState = () => j<CompanionState>("/api/companion-state");
export const listCompanionStateEvents = (limit = 10) =>
  j<CompanionStateEvent[]>(`/api/companion-state/events?limit=${encodeURIComponent(limit)}`);

// ---- 对话连续性（与长期记忆相互独立） ----
export const getContextControls = () => j<ContextControls>("/api/context/controls");

// ---- 通用 settings（走封装，自动带 token）----
export const getSetting = (key: string) =>
  j<{ key: string; value: string }>(`/api/settings/${encodeURIComponent(key)}`);
export const setSetting = (key: string, value: string) =>
  j<{ key: string; value: string }>(`/api/settings/${encodeURIComponent(key)}`, {
    method: "PUT",
    body: JSON.stringify({ value }),
  });

export interface ProactiveFeedback {
  id: string;
  delivery_id: string;
  feedback_kind: string;
  source: "explicit" | "natural_language";
  status: "pending" | "applied" | "rejected" | "revoked";
  evidence_quote?: string | null;
  created_at: number;
}

export interface ProactiveHistoryItem {
  id: string;
  level: number;
  channel: string;
  status: string;
  error_code?: string | null;
  candidate_kind: string;
  topic?: string | null;
  natural_reason: string;
  created_at: number;
  feedback: ProactiveFeedback[];
}

export const listProactiveHistory = (limit = 50) =>
  j<ProactiveHistoryItem[]>(`/api/proactive/history?limit=${limit}`);
export const listPendingProactiveFeedback = (limit = 50) =>
  j<ProactiveFeedback[]>(`/api/proactive/feedback/pending?limit=${limit}`);
export const submitProactiveFeedback = (deliveryId: string, feedbackKind: string) =>
  j<ProactiveFeedback>(`/api/proactive/deliveries/${encodeURIComponent(deliveryId)}/feedback`, {
    method: "POST",
    body: JSON.stringify({ feedback_kind: feedbackKind, request_nonce: crypto.randomUUID() }),
  });
export const resolveProactiveFeedback = (feedbackId: string, accept: boolean) =>
  j<ProactiveFeedback>(`/api/proactive/feedback/${encodeURIComponent(feedbackId)}/resolve`, {
    method: "POST", body: JSON.stringify({ accept }),
  });
export const getProactiveDiagnostics = (limit = 100) =>
  j<Record<string, unknown>>(`/api/proactive/diagnostics?limit=${limit}`);
export const clearProactiveData = () =>
  j<Record<string, unknown>>("/api/proactive/data", { method: "DELETE" });
export const resetProactiveSettings = () =>
  j<{ settings: Record<string, string>; revision: number }>(
    "/api/proactive/settings/reset", { method: "POST" },
  );
export const setContextControls = (body: Partial<Pick<ContextControls,
  "reference_chat_history" | "summary_injection_enabled">>) =>
  j<ContextControls>("/api/context/controls", {
    method: "PUT",
    body: JSON.stringify(body),
  });
export const getContextDiagnostics = (sessionId?: string | null, limit = 20) => {
  const query = new URLSearchParams({ limit: String(limit) });
  if (sessionId) query.set("session_id", sessionId);
  return j<ContextDiagnostics>(`/api/context/diagnostics?${query.toString()}`);
};
export const rebuildHistoryIndex = () =>
  j<{ sessions: number; messages: number }>("/api/history-recall/rebuild", { method: "POST" });
export const rebuildConversationSummary = (sessionId: string) =>
  j<{ run: Record<string, unknown> }>(
    `/api/sessions/${encodeURIComponent(sessionId)}/conversation-summary-rebuild`,
    { method: "POST" },
  );
export const deleteConversationSummaryDerived = (sessionId: string) =>
  j<{ ok: boolean; raw_messages_preserved: number }>(
    `/api/sessions/${encodeURIComponent(sessionId)}/conversation-summary-derived`,
    { method: "DELETE" },
  );

// ---- 工具日志 ----
export const listToolLogs = () => j<ToolLog[]>("/api/tool-logs");

// ---- 聊天（SSE 流式）----
export interface ChatCallbacks {
  onMeta?: (m: {
    model: string;
    memory_used: boolean;
    memory_count: number;
    memory_refs: Array<{
      id: string;
      layer: string;
      source_session_id?: string | null;
      source_message_id?: string | null;
    }>;
    knowledge_used: boolean;
    knowledge_count: number;
    knowledge_source: "none" | "explicit" | "smart" | "confirmed";
    knowledge_recall_mode: "off" | "explicit" | "smart";
  }) => void;
  onDelta?: (text: string) => void;
  onFinal?: (d: {
    message_id: string;
    content: string;
    knowledge_citations: KnowledgeCitation[];
    evidence_links: EvidenceLink[];
  }) => void;
  onError?: (message: string, hint: string) => void;
  onDone?: (d: {
    message_id: string;
    auto_memory: Memory | null;
    memory_candidate?: { id: string; content: string; status: string } | null;
    companion_state: CompanionState | null;
    affect_observation?: { id: string; status: string } | null;
    memory_observation?: { id: string; status: string } | null;
    content: string;
    knowledge_citations: KnowledgeCitation[];
    evidence_links: EvidenceLink[];
  }) => void;
  onPhase?: (phase: "retrieval" | "generation" | "persistence" | "completed") => void;
  onCancelled?: (d: { phase: string; persisted: boolean }) => void;
  onAbort?: () => void;
}

export interface ChatRequestOptions {
  regenerate?: boolean;
  request_nonce?: string;
  knowledge_grant_token?: string;
  knowledge_skip_restricted?: boolean;
  attachment_ids?: string[];
  ingress_messages?: TurnIngressMessage[];
  chat_nonce?: string;
  cancel_token?: string;
  image_transmission_consent?: boolean;
  image_provider_id?: string;
  image_model?: string;
  image_location_revision?: number;
  persona_mode?: "companionship" | "focused_work";
  persona_style?: {
    address_style: "natural" | "ge_xia_low" | "name_if_known" | "none";
    detail_level: "concise" | "balanced" | "detailed";
    poetic_level: "low" | "balanced" | "high";
    proactivity_level: "reserved" | "balanced" | "engaged";
  };
  signal?: AbortSignal;
}

export interface TurnIngressMessage {
  client_message_id: string;
  window_id: string;
  content: string;
  attachment_ids: string[];
  authorization_scope: "local_text_only" | "local_image" | "remote_image_once";
  queued_at_ms: number;
  boundary: "idle_timeout" | "explicit_send" | "voice_end" | "stop";
}

export interface CieSettings {
  protocol_version: string;
  setting_key: "cie_enabled";
  enabled: boolean;
  default_enabled: boolean;
  window_ms: number;
  window_min_ms: number;
  window_max_ms: number;
  max_messages: number;
  ingress_protocol_version: string;
}

export const getCieSettings = () => j<CieSettings>("/api/cie/settings");
export const getContextContributors = () =>
  j<ContextContributorDiagnostics>("/api/cie/context-contributors");
export const setContextContributorEnabled = (contributorId: string, enabled: boolean) =>
  j<ContextContributor>(`/api/cie/context-contributors/${encodeURIComponent(contributorId)}`, {
    method: "PUT", body: JSON.stringify({ enabled }),
  });
export interface VisionCapability {
  protocol_version: string;
  provider_id: string;
  model: string;
  status: "unknown" | "supported" | "unsupported";
  provider_location: "local" | "remote" | "unknown" | string;
  provider_location_revision: number;
  checked_at: number | null;
  error_code: string | null;
}
export const getVisionCapability = () => j<VisionCapability>("/api/cie/vision-capability");
export const probeVisionCapability = () => j<VisionCapability>("/api/cie/vision-capability/probe", {
  method: "POST",
});
export const cancelChat = (cancelToken: string) =>
  j<{ found: boolean; accepted: boolean; phase: string | null }>("/api/chat/cancel", {
    method: "POST", body: JSON.stringify({ cancel_token: cancelToken }),
  });

// ---- 聊天附件上传 ----
export interface ChatAttachmentResult {
  id: string;
  filename: string;
  mime_type: string;
  attachment_kind: "text" | "image";
  char_count: number;
  byte_count?: number;
  pixel_width?: number;
  pixel_height?: number;
  expires_at?: number;
  content_preview: string;
  vision_capability?: VisionCapability;
}

export async function uploadChatAttachment(
  file: File,
): Promise<ChatAttachmentResult> {
  const lower = file.name.toLowerCase();
  const fallbackMime = lower.endsWith(".md")
    ? "text/markdown"
    : lower.endsWith(".pdf")
      ? "application/pdf"
      : lower.endsWith(".docx")
        ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        : lower.endsWith(".png")
          ? "image/png"
          : lower.endsWith(".jpg") || lower.endsWith(".jpeg")
            ? "image/jpeg"
        : "text/plain";
  const response = await fetch(API_BASE + "/api/chat/attachments", {
    method: "POST",
    headers: requestHeaders({ headers: {
      "Content-Type": file.type || fallbackMime,
      "X-Xiadie-Filename": encodeURIComponent(file.name),
    }}),
    body: file,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch { /* ignore */ }
    throw new ApiError(response.status, detail);
  }
  return response.json();
}

// 删除未绑定的附件（message_id IS NULL），用于用户点 × 移除 ready 附件时清理后端记录
export async function deleteChatAttachment(attachmentId: string): Promise<void> {
  await j<void>(`/api/chat/attachments/${encodeURIComponent(attachmentId)}`, {
    method: "DELETE",
  });
}

export async function getMessageAttachmentContent(
  messageId: string, attachmentId: string,
): Promise<{
  id: string;
  filename: string;
  mime_type: string;
  char_count: number;
  content: string;
}> {
  const r = await fetch(
    API_BASE + `/api/messages/${encodeURIComponent(messageId)}/attachments/${encodeURIComponent(attachmentId)}/content`,
    { headers: requestHeaders() },
  );
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch { /* ignore */ }
    throw new ApiError(r.status, detail);
  }
  return r.json();
}

// 用 fetch+ReadableStream 解析 SSE（EventSource 不支持 POST）
export async function streamChat(
  session_id: string,
  content: string,
  cb: ChatCallbacks,
  options: ChatRequestOptions = {},
): Promise<void> {
  // 整体 try/catch：fetch 连接被拒或流读取中断都会 reject，必须保证 onError 触发，
  // 否则调用方（ChatView）的 busy 状态永不复位、输入框卡死。
  try {
    const { signal, ...bodyOptions } = options;
    const r = await fetch(API_BASE + "/api/chat", {
      method: "POST",
      headers: requestHeaders(),
      body: JSON.stringify({ session_id, content, ...bodyOptions }),
      signal,
    });
    if (!r.ok || !r.body) {
      let message = "请求失败";
      let hint = "后端拒绝了本次请求，请检查资料授权后重试。";
      let code: string | undefined;
      try {
        const payload = await r.json();
        const detail = payload?.detail;
        if (typeof detail === "string") message = detail;
        else if (detail?.message) message = detail.message;
        code = detail?.code;
        if (detail?.code === "knowledge_grant_required") {
          hint = "资料需要重新确认后才能发送给当前模型。";
        }
      } catch {
        if (r.status >= 500) hint = "后端服务暂时不可用，请稍后重试。";
      }
      const error = new ApiError(r.status, message, code);
      cb.onError?.(message, hint);
      throw error;
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    const protocolState = { finalSeen: false };
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const blocks = buf.split("\n\n");
      buf = blocks.pop() || "";
      for (const block of blocks) {
        const evLine = block.split("\n").find((l) => l.startsWith("event:"));
        const dataLine = block.split("\n").find((l) => l.startsWith("data:"));
        if (!evLine || !dataLine) continue;
        const ev = evLine.slice(6).trim();
        const data = JSON.parse(dataLine.slice(5).trim());
        dispatchChatSseEvent(ev, data, cb, protocolState);
      }
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      cb.onAbort?.();
      return;
    }
    cb.onError?.("连接中断", "无法连接到后端或数据流已中断，请确认后端已启动后重试。");
  }
}

// ---- 桌面壳桥接（Electron preload 注入；浏览器里为 undefined）----
export const desktop = (window as any).xiadie as
  | {
      openMain: () => void;
      hideMain: () => void;
      minimizeMain: () => void;
      maximizeMain: () => void;
      onWindowMaximized: (cb: (maximized: boolean) => void) => void;
      hidePet: () => void;
      resetPet: () => void;
      quit: () => void;
      showPetMenu: () => void;
      dragPet: (dx: number, dy: number) => void;
      setPetState: (s: string, bubble?: string, cluster?: string) => void;
      onPetState: (cb: (p: { state: string; bubble?: string; cluster?: string }) => void) => void;
      onProactiveDelivery: (cb: (p: {
        id: string;
        channel: "live2d" | "bubble";
        payload: Record<string, any>;
      }) => void) => () => void;
      confirmProactiveDelivery: (id: string, success: boolean) => void;
      onProactiveChatMessage: (cb: (p: {
        session_id: string;
        delivery_id: string;
        message_id: string;
      }) => void) => () => void;
      getApiToken: () => string;
    }
  | undefined;
