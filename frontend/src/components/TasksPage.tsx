import { useEffect, useState } from "react";
import * as api from "./../api";
import { toast } from "./../store";

// 状态圆点颜色（进行中=紫，待办=暗银，已完成=绿）。
const DOT: Record<api.Task["status"], string> = {
  doing: "var(--violet-soft)",
  todo: "var(--text-faint)",
  done: "var(--ok)",
  archived: "var(--text-faint)",
};

// 来源标签：手动新建 or 从对话里记下。
function SourceChip({ source }: { source: string }) {
  const isChat = source === "chat";
  return (
    <span className={isChat ? "chip L1" : "chip"}>{isChat ? "对话" : "手动"}</span>
  );
}

export function TasksPage() {
  const [tasks, setTasks] = useState<api.Task[]>([]);
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () => {
    setLoading(true);
    api
      .listTasks()
      .then((list) => {
        setTasks(list.filter((t) => t.status !== "archived"));
        setError(null);
      })
      .catch((e) => setError(e?.message || "加载任务失败"))
      .finally(() => setLoading(false));
  };
  useEffect(refresh, []);

  const add = async () => {
    const t = title.trim();
    if (!t || busy) return;
    setBusy(true);
    try {
      await api.createTask(t);
      setTitle("");
      toast("已新建任务");
      refresh();
    } catch (e: any) {
      toast(e?.message || "新建失败");
    } finally {
      setBusy(false);
    }
  };

  const changeStatus = async (id: string, status: api.Task["status"]) => {
    try {
      await api.updateTask(id, { status });
      refresh();
    } catch (e: any) {
      toast(e?.message || "更新失败");
    }
  };

  const remove = async (id: string) => {
    try {
      await api.deleteTask(id);
      toast("已删除任务");
      refresh();
    } catch (e: any) {
      toast(e?.message || "删除失败");
    }
  };

  const groups: { key: api.Task["status"]; label: string; items: api.Task[] }[] = [
    { key: "doing", label: "进行中", items: tasks.filter((t) => t.status === "doing") },
    { key: "todo", label: "待办", items: tasks.filter((t) => t.status === "todo") },
    { key: "done", label: "已完成", items: tasks.filter((t) => t.status === "done") },
  ];

  const renderRow = (t: api.Task) => {
    const done = t.status === "done";
    return (
      <div className="list-row" key={t.id}>
        {/* 左侧：状态圆点 / 完成勾选，点一下切换完成态 */}
        <button
          title={done ? "标记为待办" : "标记为完成"}
          onClick={() => changeStatus(t.id, done ? "todo" : "done")}
          style={{
            width: 18,
            height: 18,
            borderRadius: "50%",
            flexShrink: 0,
            border: `1.5px solid ${DOT[t.status]}`,
            background: done ? "var(--ok)" : "transparent",
            color: "#0b0713",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 11,
            lineHeight: 1,
          }}
        >
          {done ? "✓" : ""}
        </button>

        <span
          style={{
            flex: 1,
            textDecoration: done ? "line-through" : "none",
            opacity: done ? 0.5 : 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {t.title}
        </span>

        <SourceChip source={t.source} />

        {/* 右侧：状态流转 + 删除 */}
        <div className="row" style={{ gap: 6 }}>
          {t.status === "todo" && (
            <button className="btn ghost" onClick={() => changeStatus(t.id, "doing")}>
              开始
            </button>
          )}
          {t.status === "doing" && (
            <>
              <button className="btn ghost" onClick={() => changeStatus(t.id, "todo")}>
                暂停
              </button>
              <button className="btn ghost" onClick={() => changeStatus(t.id, "done")}>
                完成
              </button>
            </>
          )}
          {done && (
            <button className="btn ghost" onClick={() => changeStatus(t.id, "todo")}>
              重开
            </button>
          )}
          <button
            className="btn ghost"
            onClick={() => remove(t.id)}
            style={{ color: "var(--danger)" }}
          >
            删除
          </button>
        </div>
      </div>
    );
  };

  return (
    <div className="page">
      <h1>任务</h1>
      <div className="sub">
        随手记下要做的事，遐蝶帮你盯着。进行中和待办排在前面。
      </div>

      <div className="field">
        <div className="row">
          <input
            style={{
              flex: 1,
              padding: "9px 12px",
              borderRadius: 10,
              background: "var(--glass-strong)",
              border: "1px solid var(--glass-border)",
              outline: "none",
            }}
            placeholder="写点要做的事，回车即可新建…"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") add();
            }}
          />
          <button className="btn" onClick={add} disabled={busy || !title.trim()}>
            ＋ 新建任务
          </button>
        </div>
      </div>

      {error && <div className="empty">加载出错了：{error}</div>}

      {!error && !loading && tasks.length === 0 && (
        <div className="empty">
          今天还没有任务，从对话里对遐蝶说「记一个任务」也可以
        </div>
      )}

      {!error &&
        groups.map(
          (g) =>
            g.items.length > 0 && (
              <div key={g.key} style={{ marginTop: 8 }}>
                <div className="section-label">
                  {g.label}（{g.items.length}）
                </div>
                {g.items.map(renderRow)}
              </div>
            )
        )}
    </div>
  );
}
