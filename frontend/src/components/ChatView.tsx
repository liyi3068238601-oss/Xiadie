import { useEffect, useRef, useState } from "react";
import * as api from "./../api";
import { Mode, toast } from "./../store";
import { inferEmotion } from "./../emotion";

interface Props {
  sessionId: string | null;
  onMode: (m: Mode) => void;
  onSessionsChanged: () => void;
}

interface Streaming {
  text: string;
  memoryUsed: boolean;
}

export function ChatView({ sessionId, onMode, onSessionsChanged }: Props) {
  const [messages, setMessages] = useState<api.Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState<Streaming | null>(null);
  const [errorCard, setErrorCard] = useState<{ msg: string; hint: string } | null>(null);
  const [autoMemory, setAutoMemory] = useState<api.Memory | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const busy = streaming !== null;

  useEffect(() => {
    if (!sessionId) {
      setMessages([]);
      return;
    }
    api.listMessages(sessionId).then(setMessages);
    setErrorCard(null);
    setAutoMemory(null);
  }, [sessionId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, streaming]);

  async function send(regenerate = false) {
    if (!sessionId || busy) return;
    const content = regenerate ? lastUserContent() : input.trim();
    if (!content) return;
    if (!regenerate) {
      setMessages((m) => [...m, localMsg("user", content)]);
      setInput("");
    }
    setErrorCard(null);
    setAutoMemory(null);
    setStreaming({ text: "", memoryUsed: false });
    onMode("thinking");
    api.desktop?.setPetState?.("thinking", "让我想想…", "confused");

    let reply = ""; // 累积回复原文，done 时用于推断遐蝶情绪
    await api.streamChat(
      sessionId,
      content,
      {
        onMeta: (m) => setStreaming((s) => (s ? { ...s, memoryUsed: m.memory_used } : s)),
        onDelta: (t) => {
          reply += t;
          setStreaming((s) => (s ? { ...s, text: s.text + t } : { text: t, memoryUsed: false }));
        },
        onError: (msg, hint) => {
          setStreaming(null);
          setErrorCard({ msg, hint });
          onMode("companion");
          api.desktop?.setPetState?.("idle", undefined, "aggrieved");
        },
        onDone: (d) => {
          setStreaming(null);
          setAutoMemory(d.auto_memory);
          onMode("companion");
          // 根据回复内容切换遐蝶表情
          const emotion = inferEmotion(reply, content);
          api.desktop?.setPetState?.("done", "回复好了~", emotion);
          if (sessionId) api.listMessages(sessionId).then(setMessages);
          onSessionsChanged();
        },
      },
      regenerate
    );
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
          <MessageRow key={m.id} m={m} onFavorite={() => favorite(m, setMessages)} />
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
              {streaming.memoryUsed && (
                <div className="msg-meta">
                  <span className="memory-hint">✦ 已参考近期记忆</span>
                </div>
              )}
            </div>
          </div>
        )}

        {autoMemory && (
          <div className="card memory">
            <div className="card-title">✦ 已记住一条信息</div>
            <div>{autoMemory.content}</div>
            <div className="card-hint">
              自动记忆 · 可在「记忆与关系」中编辑或删除
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

function MessageRow({ m, onFavorite }: { m: api.Message; onFavorite: () => void }) {
  return (
    <div className={"msg " + m.role}>
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
