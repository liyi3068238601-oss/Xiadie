import { useEffect, useState } from "react";
import * as api from "./../api";
import { Mode, toast, View } from "./../store";

interface Props {
  className: string;
  mode: Mode;
  model: api.CurrentModel | null;
  onGo: (v: View) => void;
}

const MOOD: Record<Mode, { face: string; name: string; sub: string }> = {
  companion: { face: "🦋", name: "陪伴中", sub: "安静地待在你身边" },
  thinking: { face: "💭", name: "思考中", sub: "正在组织回答" },
  executing: { face: "⚡", name: "执行中", sub: "正在处理任务" },
  resting: { face: "🌙", name: "休息中", sub: "随时可以叫我" },
};

export function RightBar({ className, mode, model, onGo }: Props) {
  const [memories, setMemories] = useState<api.Memory[]>([]);
  const [tasks, setTasks] = useState<api.Task[]>([]);

  const refresh = () => {
    api
      .listMemories()
      .then((m) => setMemories(m.filter((x) => x.enabled).slice(0, 3)))
      .catch(() => {});
    api.listTasks(true).then(setTasks).catch(() => {});
  };
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  const mood = MOOD[mode];

  return (
    <div className={className}>
      <div className="state-block">
        <div className="block-title">遐蝶状态</div>
        <div className="mood">
          <span className="face">{mood.face}</span>
          <div>
            <div className="mood-name">{mood.name}</div>
            <div className="mood-sub">{mood.sub}</div>
          </div>
        </div>
      </div>

      <div className="state-block">
        <div className="block-title">最近记忆</div>
        {memories.length === 0 && <div className="mini-item">还没有记忆</div>}
        {memories.map((m) => (
          <div key={m.id} className="mini-item">
            <span className="badge">[{m.layer}] </span>
            {m.content}
          </div>
        ))}
      </div>

      <div className="state-block">
        <div className="block-title">今日任务</div>
        {tasks.length === 0 && <div className="mini-item">今天还没有任务</div>}
        {tasks.map((t) => (
          <div key={t.id} className="mini-item">
            {t.status === "done" ? "✓ " : "○ "}
            {t.title}
          </div>
        ))}
      </div>

      <div className="state-block">
        <div className="block-title">模型能力</div>
        <div className="cap-tags">
          {(model?.capabilities || ["local"]).map((c) => (
            <span key={c} className="cap-tag">
              {c}
            </span>
          ))}
        </div>
      </div>

      <div className="state-block">
        <div className="block-title">快捷操作</div>
        <div className="quick-actions">
          <button onClick={() => onGo("memories")}>记忆库</button>
          <button onClick={() => onGo("tasks")}>任务</button>
          <button onClick={() => onGo("files")}>知识库</button>
          <button onClick={() => onGo("settings")}>设置</button>
          <button onClick={() => onGo("tools")}>工具记录</button>
          <button onClick={() => toast("导出功能开发中")}>导出</button>
        </div>
      </div>
    </div>
  );
}
