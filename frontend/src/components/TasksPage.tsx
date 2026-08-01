import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import * as api from "./../api";
import { toast } from "./../store";
import { Icon } from "./Icon";

function SourceChip({ source }: { source: string }) {
  const isChat = source === "chat";
  return <span className={`task-source${isChat ? " from-chat" : ""}`}>{isChat ? "来自对话" : "手动创建"}</span>;
}

export function TasksPage() {
  const [tasks, setTasks] = useState<api.Task[]>([]);
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () => {
    setLoading(true);
    api.listTasks()
      .then((list) => {
        setTasks(list.filter((task) => task.status !== "archived"));
        setError(null);
      })
      .catch((reason) => setError(reason?.message || "加载任务失败"))
      .finally(() => setLoading(false));
  };

  useEffect(refresh, []);

  const add = async () => {
    const nextTitle = title.trim();
    if (!nextTitle || busy) return;
    setBusy(true);
    try {
      await api.createTask(nextTitle);
      setTitle("");
      toast("已新建任务");
      refresh();
    } catch (reason: any) {
      toast(reason?.message || "新建失败");
    } finally {
      setBusy(false);
    }
  };

  const changeStatus = async (id: string, status: api.Task["status"]) => {
    try {
      await api.updateTask(id, { status });
      refresh();
    } catch (reason: any) {
      toast(reason?.message || "更新失败");
    }
  };

  const remove = async (id: string) => {
    try {
      await api.deleteTask(id);
      toast("已删除任务");
      refresh();
    } catch (reason: any) {
      toast(reason?.message || "删除失败");
    }
  };

  const groups: { key: api.Task["status"]; label: string; items: api.Task[] }[] = [
    { key: "doing", label: "进行中", items: tasks.filter((task) => task.status === "doing") },
    { key: "todo", label: "待办", items: tasks.filter((task) => task.status === "todo") },
    { key: "done", label: "已完成", items: tasks.filter((task) => task.status === "done") },
  ];
  const doneCount = groups[2].items.length;
  const openCount = tasks.length - doneCount;
  const completion = tasks.length ? Math.round((doneCount / tasks.length) * 100) : 0;

  return (
    <section className="page tasks-page" aria-labelledby="tasks-title">
      <header className="page-header compact-page-header">
        <div>
          <p className="page-eyebrow">TASKS</p>
          <h1 id="tasks-title">今日任务</h1>
          <p>随手记录，遐蝶帮你盯着。</p>
        </div>
        <span className="header-meta">{openCount} 项未完成</span>
      </header>

      <div className="task-create">
        <Icon name="plus" />
        <input
          placeholder="写点要做的事，回车即可新建…"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") void add(); }}
        />
        <button onClick={() => void add()} disabled={busy || !title.trim()}>新增任务</button>
      </div>

      <div className="task-summary" aria-label="任务概览">
        <div><strong>{groups[1].items.length}</strong><span>待办</span></div>
        <div><strong>{groups[0].items.length}</strong><span>进行中</span></div>
        <div><strong>{doneCount}</strong><span>已完成</span></div>
        <div className="task-progress" style={{ "--progress": `${completion * 3.6}deg` } as CSSProperties}><span>{completion}%</span></div>
      </div>

      {error && <div className="empty">加载出错了：{error}</div>}
      {!error && loading && <div className="empty">正在整理任务…</div>}
      {!error && !loading && tasks.length === 0 && <div className="empty">今天还没有任务，要我先帮你记一个吗？</div>}

      {!error && !loading && groups.map((group) => group.items.length > 0 && (
        <section className={`task-group ${group.key}`} key={group.key}>
          <header><span>{group.label}</span><b>{group.items.length}</b></header>
          {group.items.map((task) => {
            const done = task.status === "done";
            return (
              <article className="task-item" key={task.id}>
                <button
                  className={`task-check${done ? " checked" : ""}`}
                  aria-label={done ? "标记为待办" : "标记为完成"}
                  onClick={() => void changeStatus(task.id, done ? "todo" : "done")}
                >{done ? "✓" : ""}</button>
                <div className="task-copy">
                  <strong>{task.title}</strong>
                  <div><SourceChip source={task.source} />{task.due_date && <span>{task.due_date}</span>}</div>
                </div>
                <div className="task-actions">
                  {task.status === "todo" && <button onClick={() => void changeStatus(task.id, "doing")}>开始</button>}
                  {task.status === "doing" && <button onClick={() => void changeStatus(task.id, "todo")}>暂停</button>}
                  {done && <button onClick={() => void changeStatus(task.id, "todo")}>重开</button>}
                  <button className="danger" onClick={() => void remove(task.id)}>删除</button>
                </div>
              </article>
            );
          })}
        </section>
      ))}
    </section>
  );
}
