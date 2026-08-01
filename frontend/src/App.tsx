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
import { LifePage } from "./components/LifePage";
import { Icon, type IconName } from "./components/Icon";

const NAV: { view: View; ico: IconName; label: string }[] = [
  { view: "chat", ico: "chat", label: "陪伴 · 对话" },
  { view: "tasks", ico: "task", label: "今日任务" },
  { view: "life", ico: "life", label: "今日生活" },
  { view: "memories", ico: "memory", label: "记忆与关系" },
  { view: "files", ico: "folder", label: "文件与知识" },
  { view: "tools", ico: "tool", label: "运行日志" },
  { view: "settings", ico: "settings", label: "设置" },
];

export default function App() {
  const [view, setView] = useState<View>("chat");
  const [mode, setMode] = useState<Mode>("companion");
  const [sessions, setSessions] = useState<api.Session[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [focusMessageId, setFocusMessageId] = useState<string | null>(null);
  const [companionState, setCompanionState] = useState<api.CompanionState | null>(null);
  const [stateReason, setStateReason] = useState<string | null>(null);
  const [isMaximized, setIsMaximized] = useState(false);
  const { model, refresh: refreshModel } = useCurrentModel();
  const toastMsg = useToast();

  const creatingRef = useRef(false);
  const latestReasonAtRef = useRef(0);

  const acceptStateSnapshot = useCallback((next: api.CompanionState) => {
    setCompanionState((current) =>
      current && current.affect.updated_at > next.affect.updated_at ? current : next
    );
  }, []);

  const acceptReasonEvent = useCallback((event?: api.CompanionStateEvent) => {
    if (!event || event.created_at < latestReasonAtRef.current) return;
    latestReasonAtRef.current = event.created_at;
    setStateReason(event.reason || null);
  }, []);

  const refreshCompanionState = useCallback(async () => {
    try {
      acceptStateSnapshot(await api.getCompanionState());
    } catch {
      // 短暂断线时保留最后一份有效快照，避免 UI 和桌宠闪回默认状态。
    }
    try {
      const events = await api.listCompanionStateEvents(1);
      acceptReasonEvent(events[0]);
    } catch {
      // 原因是辅助审计信息；读取失败不覆盖已有状态或原因。
    }
  }, [acceptReasonEvent, acceptStateSnapshot]);

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

  useEffect(() => {
    refreshCompanionState();
    const timer = setInterval(refreshCompanionState, 10_000);
    return () => clearInterval(timer);
  }, [refreshCompanionState]);

  useEffect(() => {
    api.desktop?.onWindowMaximized?.(setIsMaximized);
  }, []);

  useEffect(() => {
    const cluster = companionState?.derived.cluster;
    if (!cluster) return;
    const petState = mode === "thinking" ? "thinking" : mode === "executing" ? "executing" : mode === "resting" ? "resting" : "idle";
    api.desktop?.setPetState?.(petState, undefined, cluster);
  }, [companionState?.derived.cluster, mode]);

  const acceptChatState = useCallback((state: api.CompanionState | null) => {
    if (state) acceptStateSnapshot(state);
    api.desktop?.setPetState?.("done", "回复好了~", state?.derived.cluster || companionState?.derived.cluster);
    api.listCompanionStateEvents(1).then((events) => acceptReasonEvent(events[0])).catch(() => {});
  }, [acceptReasonEvent, acceptStateSnapshot, companionState?.derived.cluster]);

  const newChat = async () => {
    const s = await api.createSession();
    setActiveSession(s.id);
    setView("chat");
    refreshSessions();
  };

  const openSession = (id: string) => {
    setFocusMessageId(null);
    setActiveSession(id);
    setView("chat");
  };

  const openMemorySource = (sessionId: string, messageId: string) => {
    setActiveSession(sessionId);
    setFocusMessageId(messageId);
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
        <span className="brand-mark" aria-label="遐蝶"><span aria-hidden="true">◇</span></span>
        <div className="top-spacer" />
        <span className={`model-chip no-drag${model ? " is-online" : " is-offline"}`}>
          <i aria-hidden="true" />
          <span>{model ? `${model.provider_name} · ${model.model}` : "未连接模型"}</span>
        </span>
        <button className="win-btn no-drag" title="设置" onClick={() => setView("settings")}>
          <Icon name="settings" />
        </button>
        {/* 窗口控制按钮组 */}
        <span className="win-controls no-drag">
          <button
            className="win-btn"
            title="最小化"
            onClick={() => api.desktop?.minimizeMain?.()}
            aria-label="最小化"
          >
            <svg width="12" height="12" viewBox="0 0 12 12"><rect y="5" width="12" height="1.5" rx="0.75" fill="currentColor"/></svg>
          </button>
          <button
            className="win-btn"
            title={isMaximized ? "还原" : "最大化"}
            onClick={() => api.desktop?.maximizeMain?.()}
            aria-label={isMaximized ? "还原" : "最大化"}
          >
            {isMaximized ? (
              <svg width="12" height="12" viewBox="0 0 12 12"><rect x="1.5" y="3" width="7" height="7" rx="1" fill="none" stroke="currentColor" strokeWidth="1.3"/><rect x="3.5" y="1" width="7" height="7" rx="1" fill="var(--glass-strong, rgba(28,16,54,0.9))" stroke="currentColor" strokeWidth="1.3"/></svg>
            ) : (
              <svg width="12" height="12" viewBox="0 0 12 12"><rect x="0.75" y="0.75" width="10.5" height="10.5" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.5"/></svg>
            )}
          </button>
          <button
            className="win-btn win-btn-close"
            title="隐藏到托盘"
            onClick={() => api.desktop?.hideMain?.()}
            aria-label="关闭"
          >
            <svg width="12" height="12" viewBox="0 0 12 12"><line x1="1" y1="1" x2="11" y2="11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/><line x1="11" y1="1" x2="1" y2="11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
          </button>
        </span>
      </div>

      {/* 三栏 */}
      <div className="body">
        {/* 左侧栏 */}
        <div className="sidebar glass">
          <button className="new-chat" onClick={newChat}>
            <Icon name="plus" /> <span>新建对话</span>
          </button>
          <div className="nav">
            {NAV.map((n) => (
              <button
                key={n.view}
                className={view === n.view ? "active" : ""}
                onClick={() => setView(n.view)}
              >
                <span className="ico"><Icon name={n.ico} /></span>
                <span className="nav-label">{n.label}</span>
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
              focusMessageId={focusMessageId}
              onMode={setMode}
              companionCluster={companionState?.derived.cluster}
              onCompanionState={acceptChatState}
              onSessionsChanged={refreshSessions}
              onOpenTasks={() => setView("tasks")}
            />
          )}
          {view === "settings" && (
            <SettingsPage onModelChanged={refreshModel} currentSessionId={activeSession} />
          )}
          {view === "tasks" && <TasksPage />}
          {view === "life" && <LifePage />}
          {view === "memories" && <MemoriesPage onOpenSource={openMemorySource} />}
          {view === "files" && <FilesPage />}
          {view === "tools" && <ToolLogsPage />}
        </div>

        {/* 右侧遐蝶状态栏 */}
        <RightBar
          className="rightbar glass"
          companionState={companionState}
          stateReason={stateReason}
          model={model}
          onGo={setView}
        />
      </div>

      {toastMsg && <div className="toast">{toastMsg}</div>}
    </div>
  );
}
