import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import { Mode, useCurrentModel, useToast, View } from "./store";
import { ChatView } from "./components/ChatView";
import { RightBar } from "./components/RightBar";
import { SettingsPage } from "./components/SettingsPage";
import { TasksPage } from "./components/TasksPage";
import { MemoriesPage } from "./components/MemoriesPage";
import { FilesPage } from "./components/FilesPage";
import { ToolLogsPage } from "./components/ToolLogsPage";

const MODE_LABEL: Record<Mode, string> = {
  companion: "陪伴",
  thinking: "思考",
  executing: "执行",
  resting: "休息",
};

const NAV: { view: View; ico: string; label: string }[] = [
  { view: "chat", ico: "◈", label: "陪伴 · 对话" },
  { view: "tasks", ico: "◷", label: "今日任务" },
  { view: "memories", ico: "❋", label: "记忆与关系" },
  { view: "files", ico: "▤", label: "文件与知识" },
  { view: "tools", ico: "⚙", label: "工具记录" },
  { view: "settings", ico: "✦", label: "设置" },
];

export default function App() {
  const [view, setView] = useState<View>("chat");
  const [mode, setMode] = useState<Mode>("companion");
  const [sessions, setSessions] = useState<api.Session[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const { model, refresh: refreshModel } = useCurrentModel();
  const toastMsg = useToast();

  const creatingRef = useRef(false);

  const refreshSessions = useCallback(async () => {
    const list = await api.listSessions();
    setSessions(list);
    setActiveSession((cur) => cur ?? (list[0]?.id || null));
    return list;
  }, []);

  // 挂载时加载会话；仅当确认列表为空时才自动建一个（带并发守卫）。
  // 不能用 sessions.length===0 触发——初始 state 就是 []，会早于 listSessions 返回，
  // 导致每次启动都多建一个空会话并短暂覆盖已有列表。
  useEffect(() => {
    (async () => {
      const list = await refreshSessions();
      if (list.length === 0 && !creatingRef.current) {
        creatingRef.current = true;
        const s = await api.createSession();
        setSessions([s]);
        setActiveSession(s.id);
        creatingRef.current = false;
      }
    })();
  }, [refreshSessions]);

  const newChat = async () => {
    const s = await api.createSession();
    setActiveSession(s.id);
    setView("chat");
    refreshSessions();
  };

  const openSession = (id: string) => {
    setActiveSession(id);
    setView("chat");
  };

  const removeSession = async (id: string) => {
    await api.deleteSession(id);
    if (activeSession === id) setActiveSession(null);
    const list = await refreshSessions();
    // 删空了就补一个，避免聊天区停在禁用空态
    if (list.length === 0 && !creatingRef.current) {
      creatingRef.current = true;
      const s = await api.createSession();
      setSessions([s]);
      setActiveSession(s.id);
      creatingRef.current = false;
    }
  };

  return (
    <div className="app">
      {/* 顶部状态栏 */}
      <div className="topbar">
        <span className="brand">遐蝶</span>
        <span className="status-pill">
          <span className="status-dot" />
          {MODE_LABEL[mode]}中
        </span>
        <div className="mode-tabs no-drag">
          {(Object.keys(MODE_LABEL) as Mode[]).map((m) => (
            <button
              key={m}
              className={mode === m ? "active" : ""}
              onClick={() => setMode(m)}
            >
              {MODE_LABEL[m]}
            </button>
          ))}
        </div>
        <div className="top-spacer" />
        <span className="model-chip no-drag">
          {model ? `${model.provider_name} · ${model.model}` : "未连接模型"}
        </span>
        <button className="win-btn no-drag" title="设置" onClick={() => setView("settings")}>
          ✦
        </button>
        <button
          className="win-btn no-drag"
          title="最小化"
          onClick={() => api.desktop?.minimizeMain?.()}
        >
          —
        </button>
        <button
          className="win-btn no-drag"
          title="隐藏到托盘"
          onClick={() => api.desktop?.hideMain?.()}
        >
          ✕
        </button>
      </div>

      {/* 三栏 */}
      <div className="body">
        {/* 左侧栏 */}
        <div className="sidebar glass">
          <button className="new-chat" onClick={newChat}>
            ＋ 新建对话
          </button>
          <div className="nav">
            {NAV.map((n) => (
              <button
                key={n.view}
                className={view === n.view ? "active" : ""}
                onClick={() => setView(n.view)}
              >
                <span className="ico">{n.ico}</span>
                {n.label}
              </button>
            ))}
          </div>
          <div className="section-label">最近会话</div>
          <div className="session-list">
            {sessions.length === 0 && <div className="empty">还没有对话</div>}
            {sessions.map((s) => (
              <div
                key={s.id}
                className={
                  "session-item" +
                  (view === "chat" && activeSession === s.id ? " active" : "")
                }
                onClick={() => openSession(s.id)}
              >
                <span className="title">{s.title}</span>
                <span
                  className="del"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeSession(s.id);
                  }}
                >
                  ✕
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* 中央 */}
        <div className="chat glass">
          {view === "chat" && (
            <ChatView
              key={activeSession ?? "none"}
              sessionId={activeSession}
              onMode={setMode}
              onSessionsChanged={refreshSessions}
            />
          )}
          {view === "settings" && <SettingsPage onModelChanged={refreshModel} />}
          {view === "tasks" && <TasksPage />}
          {view === "memories" && <MemoriesPage />}
          {view === "files" && <FilesPage />}
          {view === "tools" && <ToolLogsPage />}
        </div>

        {/* 右侧遐蝶状态栏 */}
        <RightBar
          className="rightbar glass"
          mode={mode}
          model={model}
          onGo={setView}
        />
      </div>

      {toastMsg && <div className="toast">{toastMsg}</div>}
    </div>
  );
}
