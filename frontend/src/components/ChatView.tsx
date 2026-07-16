import { useEffect, useRef, useState } from "react";
import * as api from "./../api";
import { Mode, toast } from "./../store";
import { memoryNoticeText, shouldShowMemoryNotice } from "../memoryNotice.mjs";
import {
  memoryObserverPollDelay,
  shouldContinueMemoryObserverPolling,
} from "../observerPolling.mjs";

interface Props {
  sessionId: string | null;
  focusMessageId?: string | null;
  onMode: (m: Mode) => void;
  companionCluster?: string;
  onCompanionState: (state: api.CompanionState | null) => void;
  onSessionsChanged: () => void;
}

interface Streaming {
  text: string;
  memoryCount: number;
  knowledgeCount: number;
}

interface PendingGrant {
  preview: api.KnowledgeGrantPreflight;
  content: string;
  requestNonce: string;
  regenerate: boolean;
  locationChanged: boolean;
}

type GrantAction = "allow_once" | "skip" | "always_allow" | "local_only";

export function ChatView({ sessionId, focusMessageId, onMode, companionCluster, onCompanionState, onSessionsChanged }: Props) {
  const [messages, setMessages] = useState<api.Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState<Streaming | null>(null);
  const [errorCard, setErrorCard] = useState<{ msg: string; hint: string } | null>(null);
  const [memoryNotice, setMemoryNotice] = useState<string | null>(null);
  const [pendingGrant, setPendingGrant] = useState<PendingGrant | null>(null);
  const [grantBusy, setGrantBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const memoryWatchId = useRef(0);
  const noticeTimer = useRef<number | null>(null);
  const busy = streaming !== null || grantBusy || pendingGrant !== null;

  useEffect(() => {
    if (!sessionId) {
      setMessages([]);
      return;
    }
    api.listMessages(sessionId).then(setMessages);
    setErrorCard(null);
    setMemoryNotice(null);
    setPendingGrant(null);
    setGrantBusy(false);
    memoryWatchId.current += 1;
    if (noticeTimer.current !== null) window.clearTimeout(noticeTimer.current);
  }, [sessionId]);

  useEffect(() => () => {
    memoryWatchId.current += 1;
    if (noticeTimer.current !== null) window.clearTimeout(noticeTimer.current);
  }, []);

  useEffect(() => {
    if (!focusMessageId || !messages.some((message) => message.id === focusMessageId)) return;
    requestAnimationFrame(() => {
      document.getElementById(`message-${focusMessageId}`)?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    });
  }, [focusMessageId, messages]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, streaming]);

  async function send(regenerate = false) {
    if (!sessionId || busy) return;
    const content = regenerate ? lastUserContent() : input.trim();
    if (!content) return;
    const requestNonce = newRequestNonce();
    memoryWatchId.current += 1;
    setErrorCard(null);
    setMemoryNotice(null);
    setGrantBusy(true);
    try {
      const preview = await api.preflightKnowledgeTransmission(sessionId, requestNonce, content);
      if (preview.status === "pending" && preview.id) {
        setPendingGrant({
          preview,
          content,
          requestNonce,
          regenerate,
          locationChanged: rememberProviderLocation(preview.provider),
        });
        return;
      }
      setGrantBusy(false);
      await runChat({ content, requestNonce, regenerate });
    } catch (error) {
      showRequestError(error, "无法检查资料发送范围，请稍后重试。");
    } finally {
      setGrantBusy(false);
    }
  }

  async function handleGrant(action: GrantAction) {
    if (!sessionId || !pendingGrant || grantBusy || !pendingGrant.preview.id) return;
    const pending = pendingGrant;
    const grantId = pending.preview.id as string;
    const activeSessionId = sessionId;
    setGrantBusy(true);
    setErrorCard(null);
    try {
      let token: string | undefined;
      let skipRestricted = false;
      if (action === "skip") {
        await api.denyKnowledgeTransmissionGrant(grantId);
        skipRestricted = true;
      } else {
        const resolved = await api.resolveKnowledgeTransmissionGrant({
          grant_id: grantId,
          action,
          session_id: activeSessionId,
          request_nonce: pending.requestNonce,
          content: pending.content,
        });
        token = resolved.token || undefined;
        skipRestricted = action === "local_only";
      }
      setPendingGrant(null);
      setGrantBusy(false);
      await runChat({
        content: pending.content,
        requestNonce: pending.requestNonce,
        regenerate: pending.regenerate,
        token,
        skipRestricted,
      });
    } catch (error) {
      showRequestError(error, "授权状态可能已经变化，请关闭提示后重新发送。");
    } finally {
      setGrantBusy(false);
    }
  }

  async function cancelGrant() {
    if (!pendingGrant?.preview.id || grantBusy) return;
    const grantId = pendingGrant.preview.id;
    setGrantBusy(true);
    try {
      await api.denyKnowledgeTransmissionGrant(grantId);
      setPendingGrant(null);
    } catch (error) {
      showRequestError(error, "无法关闭这次授权，请稍后重试。");
    } finally {
      setGrantBusy(false);
    }
  }

  async function runChat(options: {
    content: string;
    requestNonce: string;
    regenerate: boolean;
    token?: string;
    skipRestricted?: boolean;
  }) {
    if (!sessionId) return;
    const activeSessionId = sessionId;
    const { content, requestNonce, regenerate, token, skipRestricted = false } = options;
    if (!regenerate) {
      setMessages((m) => [...m, localMsg("user", content)]);
      setInput("");
    }
    setStreaming({ text: "", memoryCount: 0, knowledgeCount: 0 });
    onMode("thinking");
    api.desktop?.setPetState?.("thinking", "让我想想…", companionCluster);

    await api.streamChat(
      activeSessionId,
      content,
      {
        onMeta: (m) => setStreaming((s) => (s ? {
          ...s, memoryCount: m.memory_count, knowledgeCount: m.knowledge_count,
        } : s)),
        onDelta: (t) => {
          setStreaming((s) => (s ? { ...s, text: s.text + t } : {
            text: t, memoryCount: 0, knowledgeCount: 0,
          }));
        },
        onError: (msg, hint) => {
          setStreaming(null);
          setErrorCard({ msg, hint });
          onMode("companion");
          api.desktop?.setPetState?.("idle", undefined, companionCluster);
        },
        onDone: (d) => {
          setStreaming(null);
          onMode("companion");
          onCompanionState(d.companion_state);
          if (sessionId) api.listMessages(sessionId).then(setMessages);
          onSessionsChanged();
          if (d.memory_observation?.id && d.memory_observation.status === "queued") {
            void watchMemoryResult(d.memory_observation.id);
          }
        },
      },
      {
        regenerate,
        request_nonce: requestNonce,
        knowledge_grant_token: token,
        knowledge_skip_restricted: skipRestricted,
      },
    );
  }

  function showRequestError(error: unknown, fallbackHint: string) {
    const message = error instanceof api.ApiError ? error.message : "请求失败";
    setErrorCard({ msg: message, hint: fallbackHint });
  }

  async function watchMemoryResult(runId: string) {
    const watchId = ++memoryWatchId.current;
    const startedAt = Date.now();
    let consecutiveErrors = 0;
    while (
      memoryWatchId.current === watchId
      && shouldContinueMemoryObserverPolling(Date.now() - startedAt, consecutiveErrors)
    ) {
      try {
        const result = await api.getMemoryObserverResult(runId);
        consecutiveErrors = 0;
        if (result.status === "applied") {
          if (result.remembered_count > 0) showRememberedNotice(result.remembered_count);
          return;
        }
        if (result.status === "exhausted" || result.status === "skipped") return;
      } catch {
        consecutiveErrors += 1;
      }
      if (!shouldContinueMemoryObserverPolling(Date.now() - startedAt, consecutiveErrors)) return;
      const delay = memoryObserverPollDelay(Date.now() - startedAt);
      await new Promise((resolve) => window.setTimeout(resolve, delay));
    }
  }

  function showRememberedNotice(count: number) {
    const storageKey = "xiadie:last-memory-notice-at";
    let lastShownAt = Number.NaN;
    try {
      lastShownAt = Number(window.sessionStorage.getItem(storageKey));
    } catch {
      /* sessionStorage 不可用时仍允许本次轻提示 */
    }
    const now = Date.now();
    if (!shouldShowMemoryNotice(lastShownAt, now)) return;
    try {
      window.sessionStorage.setItem(storageKey, String(now));
    } catch {
      /* ignore */
    }
    setMemoryNotice(memoryNoticeText(count));
    if (noticeTimer.current !== null) window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setMemoryNotice(null), 5200);
  }

  function lastUserContent(): string {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user") return messages[i].content;
    }
    return "";
  }

  async function makeTask() {
    const text = lastUserContent();
    if (!sessionId || !text) return;
    await api.createTask(text.slice(0, 40), sessionId);
    toast("已从本次对话创建任务");
  }

  return (
    <>
      <div className="messages" ref={scrollRef}>
        {messages.length === 0 && !streaming && (
          <div className="empty">
            我是遐蝶，随时在这里。<br />
            聊点什么，或让我帮你记一个任务、存一条记忆都可以。
          </div>
        )}
        {messages.map((m) => (
          <MessageRow
            key={m.id}
            m={m}
            highlighted={m.id === focusMessageId}
            onFavorite={() => favorite(m, setMessages)}
          />
        ))}

        {streaming && (
          <div className="msg assistant">
            <div className="avatar">蝶</div>
            <div>
              <div className="bubble">
                {streaming.text || (
                  <span className="typing-dots">
                    <span>·</span>
                    <span>·</span>
                    <span>·</span>
                  </span>
                )}
              </div>
              {streaming.memoryCount > 0 && (
                <div className="msg-meta">
                  <span className="memory-hint">
                    ✦ 本轮参考了 {streaming.memoryCount} 条相关记忆
                  </span>
                </div>
              )}
              {streaming.knowledgeCount > 0 && (
                <div className="msg-meta">
                  <span className="knowledge-hint">▧ 正在核对 {streaming.knowledgeCount} 条本地资料</span>
                </div>
              )}
            </div>
          </div>
        )}

        {memoryNotice && (
          <div className="memory-notice" role="status" aria-live="polite">
            <span aria-hidden="true">✦</span>
            <div>
              <div>{memoryNotice}</div>
              <small>可以在「记忆与关系」中查看、纠正或删除</small>
            </div>
          </div>
        )}

        {pendingGrant && (
          <KnowledgeGrantCard
            pending={pendingGrant}
            busy={grantBusy}
            onAction={(action) => void handleGrant(action)}
            onCancel={() => void cancelGrant()}
          />
        )}

        {errorCard && (
          <div className="card error">
            <div className="card-title">⚠ {errorCard.msg}</div>
            <div className="card-hint">{errorCard.hint}</div>
            <div style={{ marginTop: 8 }}>
              <button className="btn ghost" onClick={() => send(true)}>
                重试
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="composer">
        <div className="composer-inner">
          <textarea
            rows={1}
            placeholder={sessionId ? "和遐蝶说点什么…" : "正在准备对话…"}
            value={input}
            disabled={!sessionId || busy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button className="send-btn" disabled={busy || !input.trim()} onClick={() => send()}>
            ➤
          </button>
        </div>
        <div className="msg-meta" style={{ marginTop: 8, paddingLeft: 4 }}>
          <button onClick={makeTask} disabled={!lastUserContent()}>
            ＋ 存为任务
          </button>
          <button onClick={() => send(true)} disabled={busy || !lastUserContent()}>
            ↻ 重新生成
          </button>
        </div>
      </div>
    </>
  );
}

function KnowledgeGrantCard({
  pending,
  busy,
  onAction,
  onCancel,
}: {
  pending: PendingGrant;
  busy: boolean;
  onAction: (action: GrantAction) => void;
  onCancel: () => void;
}) {
  const { preview } = pending;
  const remote = preview.provider.location !== "local";
  const dialogRef = useRef<HTMLElement>(null);
  const primaryRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef(onCancel);
  cancelRef.current = onCancel;
  useEffect(() => {
    primaryRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        cancelRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const controls = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        "button:not(:disabled), [href], [tabindex]:not([tabindex='-1'])",
      ));
      if (!controls.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);
  return (
    <section ref={dialogRef} className="knowledge-grant-card" role="dialog" aria-modal="true"
      aria-labelledby="knowledge-grant-title" aria-describedby="knowledge-grant-description">
      <div className="knowledge-grant-head">
        <div className="knowledge-grant-icon" aria-hidden="true">◇</div>
        <div>
          <span className="knowledge-grant-eyebrow">本地资料发送确认</span>
          <h2 id="knowledge-grant-title">遐蝶想参考这些资料回答</h2>
        </div>
        <button className="knowledge-grant-close" onClick={onCancel} disabled={busy} aria-label="取消">×</button>
      </div>

      <div className={`knowledge-grant-route ${remote ? "is-remote" : "is-local"}`}>
        <span>{remote ? "在线模型" : "本地模型"}</span>
        <strong>{preview.provider.id || "未知 Provider"} · {preview.provider.model}</strong>
        <small>
          位置：{locationText(preview.provider.location)} · 配置版本 {preview.provider.location_revision}
        </small>
      </div>
      {pending.locationChanged && (
        <div className="knowledge-grant-warning">模型位置或配置已变化，请重新确认本次发送范围。</div>
      )}
      {remote && (
        <p className="knowledge-grant-explain" id="knowledge-grant-description">
          若允许，下面列出的 {preview.chunk_count} 个片段会随本轮消息发送给当前模型服务商；
          授权仅绑定这条消息、这个模型与当前资料版本。
        </p>
      )}

      <div className="knowledge-grant-documents">
        {preview.documents.map((document) => (
          <div className="knowledge-grant-document" key={document.id}>
            <span className="knowledge-grant-file" aria-hidden="true">▧</span>
            <div>
              <strong>{document.name}</strong>
              <small>{document.chunk_count} 个片段 · 约 {document.token_estimate} tokens</small>
            </div>
            <div className="knowledge-grant-badges">
              {document.sensitivity === "sensitive" && <span className="is-sensitive">敏感</span>}
              <span>{policyText(document.policy)}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="knowledge-grant-summary">
        <span>{preview.document_count} 份文档 · {preview.chunk_count} 个片段</span>
        <span>预计 {preview.token_range.min}–{preview.token_range.max} tokens</span>
        <span>不保存正文或明文授权码</span>
      </div>

      <div className="knowledge-grant-actions">
        <button
          ref={primaryRef}
          className="knowledge-grant-primary"
          disabled={busy || !preview.can_allow_once}
          title={preview.can_allow_once ? "只允许这一次" : "包含仅限本地资料，不能单次放行"}
          onClick={() => onAction("allow_once")}
        >只允许这一次</button>
        <button disabled={busy} onClick={() => onAction("skip")}>本次不使用资料</button>
        <button
          disabled={busy || !preview.can_always_allow}
          title={preview.can_always_allow ? "以后可直接发送这些文档" : "敏感资料不能设为始终允许"}
          onClick={() => onAction("always_allow")}
        >以后始终允许</button>
        <button disabled={busy} onClick={() => onAction("local_only")}>设为仅限本地</button>
      </div>
      <small className="knowledge-grant-footnote">
        “本次不使用资料”会继续发送消息，但从本轮上下文中移除受限片段。
      </small>
      <span className="sr-only" role="status" aria-live="polite">
        {busy ? "正在处理资料授权" : "等待选择资料发送方式"}
      </span>
    </section>
  );
}

function newRequestNonce(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `request-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}

function rememberProviderLocation(provider: api.KnowledgeGrantPreflight["provider"]): boolean {
  const key = `xiadie:provider-location:${provider.id || "unknown"}:${provider.model}`;
  const value = `${provider.location}:${provider.location_revision}`;
  try {
    const previous = window.localStorage.getItem(key);
    window.localStorage.setItem(key, value);
    return previous !== null && previous !== value;
  } catch {
    return false;
  }
}

function locationText(location: api.KnowledgeGrantPreflight["provider"]["location"]): string {
  if (location === "local") return "本机";
  if (location === "remote") return "在线 / 远程";
  return "未知（按在线处理）";
}

function policyText(policy: api.KnowledgeGrantDocument["policy"]): string {
  if (policy === "remote_allowed") return "允许在线";
  if (policy === "local_only") return "仅限本地";
  return "每次询问";
}

function MessageRow({
  m,
  highlighted,
  onFavorite,
}: {
  m: api.Message;
  highlighted?: boolean;
  onFavorite: () => void;
}) {
  const [source, setSource] = useState<api.KnowledgeCitation | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);

  async function openSource(citation: api.KnowledgeCitation) {
    try {
      setSourceError(null);
      setSource(await api.getKnowledgeCitation(citation.id));
    } catch (error) {
      setSource(null);
      setSourceError(error instanceof api.ApiError ? error.message : "无法读取原始资料");
    }
  }

  return (
    <div
      id={`message-${m.id}`}
      className={`msg ${m.role}${highlighted ? " source-highlight" : ""}`}
    >
      <div className="avatar">{m.role === "user" ? "你" : "蝶"}</div>
      <div>
        <div className="bubble">{m.content}</div>
        {!!m.knowledge_citations?.length && (
          <div className="knowledge-citations" aria-label="本回复引用的资料">
            {m.knowledge_citations.map((citation) => (
              <button key={citation.id} onClick={() => void openSource(citation)}>
                {citation.citation_key} · {citation.original_name} · {citation.content_fingerprint}
              </button>
            ))}
          </div>
        )}
        {(source || sourceError) && (
          <div className="knowledge-source" role="region" aria-label="资料原文">
            <button className="knowledge-source-close" onClick={() => {
              setSource(null); setSourceError(null);
            }}>×</button>
            {source ? (
              <>
                <strong>{source.original_name}</strong>
                <small>{sourceLocation(source)}</small>
                <div>{source.content}</div>
              </>
            ) : <span>{sourceError}</span>}
          </div>
        )}
        <div className="msg-meta">
          {m.model && <span>{m.model}</span>}
          <button onClick={() => navigator.clipboard?.writeText(m.content)}>复制</button>
          <button onClick={onFavorite}>{m.favorite ? "★ 已收藏" : "☆ 收藏"}</button>
        </div>
      </div>
    </div>
  );
}

function sourceLocation(source: api.KnowledgeCitation): string {
  const heading = source.heading_path.length ? ` · ${source.heading_path.join(" › ")}` : "";
  const page = source.page_start ? ` · 第 ${source.page_start}${source.page_end !== source.page_start ? `–${source.page_end}` : ""} 页` : "";
  return `段落 ${source.paragraph_start}–${source.paragraph_end} · 行 ${source.line_start}–${source.line_end}${page}${heading} · ${source.content_fingerprint}`;
}

function localMsg(role: "user" | "assistant", content: string): api.Message {
  return {
    id: "local-" + Math.random().toString(36).slice(2),
    session_id: "",
    role,
    content,
    favorite: false,
    created_at: Date.now() / 1000,
  };
}

async function favorite(
  m: api.Message,
  setMessages: React.Dispatch<React.SetStateAction<api.Message[]>>
) {
  if (m.id.startsWith("local-")) return;
  const r = await api.toggleFavorite(m.id);
  setMessages((list) =>
    list.map((x) => (x.id === m.id ? { ...x, favorite: r.favorite } : x))
  );
}
