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

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(API_BASE + path, {
    ...init,
    headers: requestHeaders(init),
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      detail = (await r.json()).detail || detail;
    } catch {
      /* ignore */
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
  message_count?: number;
  updated_at: number;
}
export interface Message {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  model?: string;
  favorite: boolean;
  created_at: number;
}
export interface ObserverModelConfig {
  mode: "current" | "dedicated";
  provider_id: string | null;
  model: string | null;
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
  embedding_mode: "none" | "local" | "remote";
  error_code?: string | null;
  created_at: number;
  updated_at: number;
}
export interface KnowledgeImportResult {
  document: KnowledgeDocument;
  run: null | { id: string; status: string; current_stage: string; progress: number };
  already_exists: boolean;
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
}
export interface CurrentModel {
  provider_id: string;
  provider_name: string;
  model: string;
  capabilities: string[];
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
export const createSession = () =>
  j<Session>("/api/sessions", { method: "POST", body: "{}" });
export const renameSession = (id: string, title: string) =>
  j<Session>(`/api/sessions/${id}`, { method: "PATCH", body: JSON.stringify({ title }) });
export const deleteSession = (id: string) =>
  j<{ ok: boolean }>(`/api/sessions/${id}`, { method: "DELETE" });
export const listMessages = (id: string) =>
  j<Message[]>(`/api/sessions/${id}/messages`);
export const toggleFavorite = (mid: string) =>
  j<{ favorite: boolean }>(`/api/messages/${mid}/favorite`, { method: "POST" });

// ---- 记忆 ----
export const listMemories = () => j<Memory[]>("/api/memories");
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
export const listKnowledgeDocuments = () =>
  j<KnowledgeDocument[]>("/api/knowledge/documents");
export async function importKnowledgeFile(
  file: File, sensitivity: "normal" | "sensitive" = "normal",
): Promise<KnowledgeImportResult> {
  const fallbackMime = file.name.toLowerCase().endsWith(".md") ? "text/markdown" : "text/plain";
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
    try { detail = (await response.json()).detail || detail; } catch { /* ignore */ }
    throw new ApiError(response.status, detail);
  }
  return response.json();
}
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
  }) => void;
  onDelta?: (text: string) => void;
  onError?: (message: string, hint: string) => void;
  onDone?: (d: {
    message_id: string;
    auto_memory: Memory | null;
    memory_candidate?: { id: string; content: string; status: string } | null;
    companion_state: CompanionState | null;
    affect_observation?: { id: string; status: string } | null;
    memory_observation?: { id: string; status: string } | null;
  }) => void;
}

// 用 fetch+ReadableStream 解析 SSE（EventSource 不支持 POST）
export async function streamChat(
  session_id: string,
  content: string,
  cb: ChatCallbacks,
  regenerate = false
): Promise<void> {
  // 整体 try/catch：fetch 连接被拒或流读取中断都会 reject，必须保证 onError 触发，
  // 否则调用方（ChatView）的 busy 状态永不复位、输入框卡死。
  try {
    const r = await fetch(API_BASE + "/api/chat", {
      method: "POST",
      headers: requestHeaders(),
      body: JSON.stringify({ session_id, content, regenerate }),
    });
    if (!r.ok || !r.body) {
      cb.onError?.("请求失败", "无法连接到后端服务，请确认后端已启动。");
      return;
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
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
        if (ev === "meta") cb.onMeta?.(data);
        else if (ev === "delta") cb.onDelta?.(data.text);
        else if (ev === "error") cb.onError?.(data.message, data.hint);
        else if (ev === "done") cb.onDone?.(data);
      }
    }
  } catch {
    cb.onError?.("连接中断", "无法连接到后端或数据流已中断，请确认后端已启动后重试。");
  }
}

// ---- 桌面壳桥接（Electron preload 注入；浏览器里为 undefined）----
export const desktop = (window as any).xiadie as
  | {
      openMain: () => void;
      hideMain: () => void;
      minimizeMain: () => void;
      hidePet: () => void;
      resetPet: () => void;
      quit: () => void;
      showPetMenu: () => void;
      dragPet: (dx: number, dy: number) => void;
      setPetState: (s: string, bubble?: string, cluster?: string) => void;
      onPetState: (cb: (p: { state: string; bubble?: string; cluster?: string }) => void) => void;
      getApiToken: () => string;
    }
  | undefined;
