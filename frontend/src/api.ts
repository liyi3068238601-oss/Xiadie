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
    throw new Error(detail);
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
export interface Memory {
  id: string;
  layer: "L0" | "L1" | "L2";
  content: string;
  tags: string;
  source: string;
  enabled: boolean;
  updated_at: number;
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
export const deleteMemory = (id: string) =>
  j<{ ok: boolean }>(`/api/memories/${id}`, { method: "DELETE" });

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

// ---- 工具日志 ----
export const listToolLogs = () => j<ToolLog[]>("/api/tool-logs");

// ---- 聊天（SSE 流式）----
export interface ChatCallbacks {
  onMeta?: (m: { model: string; memory_used: boolean }) => void;
  onDelta?: (text: string) => void;
  onError?: (message: string, hint: string) => void;
  onDone?: (d: { message_id: string; auto_memory: Memory | null }) => void;
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
      setPetState: (s: string, bubble?: string, emotion?: string) => void;
      onPetState: (cb: (p: { state: string; bubble?: string; emotion?: string }) => void) => void;
      getApiToken: () => string;
    }
  | undefined;
