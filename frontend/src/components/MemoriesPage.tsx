import { useEffect, useState } from "react";
import * as api from "./../api";
import { toast } from "./../store";
import { EntitiesSection } from "./EntitiesSection";
import { EpisodesSection } from "./EpisodesSection";

type Layer = "L0" | "L1" | "L2";

const LAYERS: { key: Layer; name: string; desc: string }[] = [
  { key: "L0", name: "L0 核心画像", desc: "关于你的长期稳定信息：称呼、身份、重要偏好。" },
  { key: "L1", name: "L1 近期状态", desc: "最近的状态与关注点，会随对话不断更新。" },
  { key: "L2", name: "L2 长期记忆", desc: "值得长期记住的事实、经历与约定。" },
];

// L0/L1 有专属配色，L2 用普通 chip。
function layerChipClass(layer: Layer): string {
  return layer === "L0" ? "chip L0" : layer === "L1" ? "chip L1" : "chip";
}

interface Props {
  onOpenSource: (sessionId: string, messageId: string) => void;
}

export function MemoriesPage({ onOpenSource }: Props) {
  const [memories, setMemories] = useState<api.Memory[]>([]);
  const [candidates, setCandidates] = useState<api.MemoryCandidate[]>([]);
  const [candidateEdits, setCandidateEdits] = useState<
    Record<string, { content: string; layer: Layer }>
  >({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 新增表单
  const [newLayer, setNewLayer] = useState<Layer>("L1");
  const [newContent, setNewContent] = useState("");
  const [adding, setAdding] = useState(false);

  // 行内编辑
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");

  const refresh = () => {
    setLoading(true);
    Promise.all([api.listMemories(), api.listMemoryCandidates()])
      .then(([m, pending]) => {
        setMemories(m);
        setCandidates(pending);
        setCandidateEdits(
          Object.fromEntries(
            pending.map((item) => [
              item.id,
              { content: item.content, layer: item.proposed_layer },
            ])
          )
        );
        setError(null);
      })
      .catch((e) => setError(e.message || "加载失败"))
      .finally(() => setLoading(false));
  };

  useEffect(refresh, []);

  const onAcceptCandidate = async (candidate: api.MemoryCandidate) => {
    const edit = candidateEdits[candidate.id];
    if (!edit?.content.trim()) {
      toast("记忆内容不能为空");
      return;
    }
    try {
      await api.acceptMemoryCandidate(candidate.id, {
        content: edit.content.trim(),
        layer: edit.layer,
      });
      toast("已确认并保存为正式记忆");
      refresh();
    } catch (e: any) {
      toast(e.message || "确认失败");
    }
  };

  const onRejectCandidate = async (candidate: api.MemoryCandidate) => {
    try {
      await api.rejectMemoryCandidate(candidate.id);
      toast("已忽略这条候选");
      refresh();
    } catch (e: any) {
      toast(e.message || "操作失败");
    }
  };

  const onAdd = async () => {
    const content = newContent.trim();
    if (!content) {
      toast("请输入记忆内容");
      return;
    }
    setAdding(true);
    try {
      await api.addMemory(newLayer, content);
      setNewContent("");
      toast("已新增记忆");
      refresh();
    } catch (e: any) {
      toast(e.message || "新增失败");
    } finally {
      setAdding(false);
    }
  };

  const onSaveEdit = async (id: string) => {
    const content = editContent.trim();
    if (!content) {
      toast("内容不能为空");
      return;
    }
    try {
      await api.updateMemory(id, { content });
      setEditingId(null);
      toast("已保存");
      refresh();
    } catch (e: any) {
      toast(e.message || "保存失败");
    }
  };

  const onToggleEnabled = async (m: api.Memory) => {
    try {
      await api.updateMemory(m.id, { enabled: !m.enabled });
      toast(m.enabled ? "已禁用，不再注入对话" : "已启用");
      refresh();
    } catch (e: any) {
      toast(e.message || "操作失败");
    }
  };

  const onDelete = async (m: api.Memory) => {
    if (!window.confirm(`确定删除这条记忆吗？\n\n「${m.content}」`)) return;
    try {
      await api.deleteMemory(m.id);
      toast("已删除");
      refresh();
    } catch (e: any) {
      toast(e.message || "删除失败");
    }
  };

  return (
    <div className="page memory-page">
      <div className="memory-page-hero">
        <div className="memory-page-eyebrow">MEMORY ARCHIVE</div>
        <h1>记忆与关系</h1>
        <div className="sub">
          遐蝶只会参考已启用的正式记忆；对话中识别到的内容会先等待你确认。
        </div>
      </div>

      <EntitiesSection memories={memories} onOpenSource={onOpenSource} />
      <EpisodesSection onOpenSource={onOpenSource} />

      {candidates.length > 0 && (
        <div style={{ marginTop: 20, marginBottom: 24 }}>
          <div className="section-label">待确认记忆</div>
          <div className="sub" style={{ marginBottom: 12 }}>
            这些内容尚未进入正式记忆。你可以先修改内容和层级，再决定是否保存。
          </div>
          {candidates.map((candidate) => {
            const edit = candidateEdits[candidate.id] || {
              content: candidate.content,
              layer: candidate.proposed_layer,
            };
            return (
              <div
                key={candidate.id}
                className="list-row"
                style={{ alignItems: "center", flexWrap: "wrap" }}
              >
                <select
                  value={edit.layer}
                  onChange={(e) =>
                    setCandidateEdits((current) => ({
                      ...current,
                      [candidate.id]: { ...edit, layer: e.target.value as Layer },
                    }))
                  }
                  style={{ width: 82 }}
                >
                  {LAYERS.map((layer) => (
                    <option key={layer.key} value={layer.key}>{layer.key}</option>
                  ))}
                </select>
                <input
                  value={edit.content}
                  onChange={(e) =>
                    setCandidateEdits((current) => ({
                      ...current,
                      [candidate.id]: { ...edit, content: e.target.value },
                    }))
                  }
                  style={{ flex: 1, minWidth: 220 }}
                />
                {candidate.sensitivity === "sensitive" && (
                  <span className="chip" style={{ color: "var(--danger)" }}>可能敏感</span>
                )}
                {candidate.source_available && candidate.source_session_id && candidate.source_message_id ? (
                  <button
                    className="btn ghost"
                    title={candidate.source_session_title || "来源对话"}
                    onClick={() => onOpenSource(candidate.source_session_id!, candidate.source_message_id!)}
                  >
                    来源：{candidate.source_session_title || "原对话"}
                  </button>
                ) : (
                  <span className="chip">来源已不存在</span>
                )}
                <button className="btn" onClick={() => onAcceptCandidate(candidate)}>
                  接受
                </button>
                <button className="btn ghost" onClick={() => onRejectCandidate(candidate)}>
                  拒绝
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* 新增记忆 */}
      <div className="list-row memory-add-card" style={{ alignItems: "flex-end", flexWrap: "wrap" }}>
        <div className="field" style={{ marginBottom: 0, width: 150 }}>
          <label>层级</label>
          <select
            value={newLayer}
            onChange={(e) => setNewLayer(e.target.value as Layer)}
          >
            {LAYERS.map((l) => (
              <option key={l.key} value={l.key}>
                {l.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field" style={{ marginBottom: 0, flex: 1, minWidth: 200 }}>
          <label>内容</label>
          <input
            value={newContent}
            placeholder="记录一件遐蝶应该记住的事……"
            onChange={(e) => setNewContent(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onAdd();
            }}
          />
        </div>
        <button className="btn" disabled={adding} onClick={onAdd}>
          ＋ 新增记忆
        </button>
      </div>

      {error && (
        <div className="empty" style={{ color: "var(--danger)" }}>
          加载记忆失败：{error}
        </div>
      )}

      {!error && loading && memories.length === 0 && (
        <div className="empty">正在读取记忆……</div>
      )}

      {!error && !loading && memories.length === 0 && (
        <div className="empty">
          还没有任何记忆。新增一条，或在对话中让遐蝶自动记录。
        </div>
      )}

      {/* 分层展示 */}
      {!error &&
        LAYERS.map((l) => {
          const group = memories.filter((m) => m.layer === l.key);
          if (group.length === 0) return null;
          return (
            <div key={l.key} style={{ marginTop: 22 }}>
              <div className="section-label">{l.name}</div>
              <div className="sub" style={{ marginBottom: 12 }}>
                {l.desc}
              </div>
              {group.map((m) => {
                const isAuto = m.source === "auto" || m.source === "auto_confirmed";
                const isEditing = editingId === m.id;
                return (
                  <div
                    key={m.id}
                    className="list-row"
                    style={{ opacity: m.enabled ? 1 : 0.45 }}
                  >
                    <span className={layerChipClass(m.layer)}>{m.layer}</span>

                    {isEditing ? (
                      <input
                        autoFocus
                        value={editContent}
                        onChange={(e) => setEditContent(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") onSaveEdit(m.id);
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        style={{
                          flex: 1,
                          padding: "6px 10px",
                          borderRadius: "8px",
                          background: "var(--glass-strong)",
                          border: "1px solid var(--glass-border-lit)",
                          outline: "none",
                        }}
                      />
                    ) : (
                      <div style={{ flex: 1, lineHeight: 1.5 }}>
                        {m.content}
                        {!m.enabled && (
                          <span
                            style={{
                              marginLeft: 8,
                              fontSize: 11,
                              color: "var(--text-faint)",
                            }}
                          >
                            （已禁用 · 不注入对话）
                          </span>
                        )}
                      </div>
                    )}

                    {isAuto && <span className="chip auto">自动</span>}

                    {m.source_message_id && (
                      m.source_available && m.source_session_id ? (
                        <button
                          className="btn ghost"
                          title={m.source_session_title || "来源对话"}
                          onClick={() => onOpenSource(m.source_session_id!, m.source_message_id!)}
                        >
                          来源：{m.source_session_title || "原对话"}
                        </button>
                      ) : (
                        <span className="chip">来源已不存在</span>
                      )
                    )}

                    {isEditing ? (
                      <>
                        <button className="btn" onClick={() => onSaveEdit(m.id)}>
                          保存
                        </button>
                        <button
                          className="btn ghost"
                          onClick={() => setEditingId(null)}
                        >
                          取消
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          className="btn ghost"
                          onClick={() => {
                            setEditingId(m.id);
                            setEditContent(m.content);
                          }}
                        >
                          编辑
                        </button>
                        <button
                          className="btn ghost"
                          onClick={() => onToggleEnabled(m)}
                        >
                          {m.enabled ? "禁用" : "启用"}
                        </button>
                        <button
                          className="btn ghost"
                          style={{ color: "var(--danger)" }}
                          onClick={() => onDelete(m)}
                        >
                          删除
                        </button>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          );
        })}
    </div>
  );
}
