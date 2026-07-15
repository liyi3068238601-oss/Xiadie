import { useEffect, useRef, useState } from "react";
import * as api from "./../api";
import { Mode, toast } from "./../store";
import { memoryNoticeText, shouldShowMemoryNotice } from "../memoryNotice.mjs";

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
}

export function ChatView({ sessionId, focusMessageId, onMode, companionCluster, onCompanionState, onSessionsChanged }: Props) {
  const [messages, setMessages] = useState<api.Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState<Streaming | null>(null);
  const [errorCard, setErrorCard] = useState<{ msg: string; hint: string } | null>(null);
  const [memoryNotice, setMemoryNotice] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const memoryWatchId = useRef(0);
  const noticeTimer = useRef<number | null>(null);
  const busy = streaming !== null;

  useEffect(() => {
    if (!sessionId) {
      setMessages([]);
      return;
    }
    api.listMessages(sessionId).then(setMessages);
    setErrorCard(null);
    setMemoryNotice(null);
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
    memoryWatchId.current += 1;
    if (!regenerate) {
      setMessages((m) => [...m, localMsg("user", content)]);
      setInput("");
    }
    setErrorCard(null);
    setMemoryNotice(null);
    setStreaming({ text: "", memoryCount: 0 });
    onMode("thinking");
    api.desktop?.setPetState?.("thinking", "让我想想…", companionCluster);

    await api.streamChat(
      sessionId,
      content,
      {
        onMeta: (m) => setStreaming((s) => (s ? { ...s, memoryCount: m.memory_count } : s)),
        onDelta: (t) => {
          setStreaming((s) => (s ? { ...s, text: s.text + t } : { text: t, memoryCount: 0 }));
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
      regenerate
    );
  }

  async function watchMemoryResult(runId: string) {
    const watchId = ++memoryWatchId.current;
    for (let attempt = 0; attempt < 60 && memoryWatchId.current === watchId; attempt += 1) {
      try {
        const result = await api.getMemoryObserverResult(runId);
        if (result.status === "applied") {
          if (result.remembered_count > 0) showRememberedNotice(result.remembered_count);
          return;
        }
        if (result.status === "exhausted" || result.status === "skipped") return;
      } catch {
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
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
            disabled={!sessionId}
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

function MessageRow({
  m,
  highlighted,
  onFavorite,
}: {
  m: api.Message;
  highlighted?: boolean;
  onFavorite: () => void;
}) {
  return (
    <div
      id={`message-${m.id}`}
      className={`msg ${m.role}${highlighted ? " source-highlight" : ""}`}
    >
      <div className="avatar">{m.role === "user" ? "你" : "蝶"}</div>
      <div>
        <div className="bubble">{m.content}</div>
        <div className="msg-meta">
          {m.model && <span>{m.model}</span>}
          <button onClick={() => navigator.clipboard?.writeText(m.content)}>复制</button>
          <button onClick={onFavorite}>{m.favorite ? "★ 已收藏" : "☆ 收藏"}</button>
        </div>
      </div>
    </div>
  );
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
