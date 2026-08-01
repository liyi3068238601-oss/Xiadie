import { useEffect, useState } from "react";
import * as api from "./../api";
import { View } from "./../store";
import { getClusterPresentation } from "./../affectPresentation.mjs";

interface Props {
  className: string;
  companionState: api.CompanionState | null;
  stateReason: string | null;
  model: api.CurrentModel | null;
  onGo: (v: View) => void;
}

const CAPABILITY_LABEL: Record<string, string> = {
  local: "本地运行",
  stream: "流式回复",
  streaming: "流式回复",
  tools: "工具调用",
  tool_calling: "工具调用",
  vision: "识图",
  reasoning: "推理",
};

export function RightBar({ className, companionState, stateReason, model, onGo }: Props) {
  const [memories, setMemories] = useState<api.Memory[]>([]);
  const [tasks, setTasks] = useState<api.Task[]>([]);

  const refresh = () => {
    api
      .listMemories()
      .then((m) => setMemories(m.filter((x) => x.enabled).slice(0, 3)))
      .catch(() => {});
    api.listTasks(true).then((items) => setTasks(items.slice(0, 3))).catch(() => {});
  };
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  const cluster = getClusterPresentation(companionState?.derived.cluster || "neutral");

  return (
    <div className={className}>
      <div className="state-block">
        <div className="block-title">当前心境</div>
        <div className="mood affect-card">
          <span className="face">{cluster.icon}</span>
          <div>
            <div className="mood-name">{companionState?.derived.label || "正在读取"}</div>
            <div className="mood-sub">{companionState ? cluster.summary : "等待后端状态快照"}</div>
          </div>
        </div>
        <div className="state-reason">{stateReason || "状态正在平稳延续"}</div>
        {import.meta.env.DEV && companionState && (
          <details className="state-debug">
            <summary>开发调试数据</summary>
            <div>cluster: {companionState.derived.cluster}</div>
            <div>guardedness: {companionState.derived.guardedness_band} ({companionState.derived.guardedness.toFixed(3)})</div>
            <div>contact_need: {companionState.affect.contact_need.toFixed(3)}</div>
            <div>valence / arousal: {companionState.affect.valence.toFixed(3)} / {companionState.affect.arousal.toFixed(3)}</div>
            <div>immersion: {companionState.affect.immersion.toFixed(3)}</div>
            <div>bond / trust: {companionState.relationship.bond.toFixed(3)} / {companionState.relationship.trust.toFixed(3)}</div>
            <div>algorithm: {companionState.algorithm_version}</div>
          </details>
        )}
      </div>

      <div className="state-block">
        <div className="block-title">最近记忆</div>
        {memories.length === 0 && <div className="mini-item mini-empty">还没有留下记忆，聊聊天就会慢慢积累。</div>}
        {memories.map((m) => (
          <div key={m.id} className="mini-item">
            <span className="badge">[{m.layer}] </span>
            {m.content}
          </div>
        ))}
      </div>

      <div className="state-block">
        <div className="block-title">今日任务</div>
        {tasks.length === 0 && <div className="mini-item mini-empty">今天还没有任务，要我先帮你记一个吗？</div>}
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
              {CAPABILITY_LABEL[c] || c}
            </span>
          ))}
        </div>
      </div>

      <div className="state-block">
        <div className="block-title">快捷操作</div>
        <div className="quick-actions">
          <button onClick={() => onGo("memories")}>记忆库</button>
          <button onClick={() => onGo("settings")}>设置</button>
        </div>
      </div>
    </div>
  );
}
