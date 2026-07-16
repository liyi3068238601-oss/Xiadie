import { useEffect, useState } from "react";
import * as api from "./../api";
import { toast } from "./../store";
import { EntitiesSection } from "./EntitiesSection";
import { EpisodesSection } from "./EpisodesSection";
import { SagasSection } from "./SagasSection";

type Layer = "L0" | "L1" | "L2";

const LAYERS: { key: Layer; name: string; desc: string }[] = [
  { key: "L0", name: "L0 核心画像", desc: "关于你的长期稳定信息：称呼、身份、重要偏好。" },
  { key: "L1", name: "L1 近期状态", desc: "最近的状态与关注点，会随对话不断更新。" },
  { key: "L2", name: "L2 长期记忆", desc: "值得长期记住的事实、经历与约定。" },
];

const SCOPE_LABELS: Record<string, string> = {
  user: "关于用户", self: "遐蝶自身", relationship: "共同关系", world: "外部世界",
};
const KIND_LABELS: Record<string, string> = {
  fact: "事实", preference: "偏好", plan: "计划", experience: "共同经历",
  relationship: "关系", observation: "观察", correction: "纠正",
};

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
  const [lifecycleDetails, setLifecycleDetails] = useState<Record<string, api.MemoryLifecycleDetail>>({});
  const [expandedLifecycle, setExpandedLifecycle] = useState<string | null>(null);
  const [relations, setRelations] = useState<api.MemoryRelation[]>([]);
  const [archivistRuns, setArchivistRuns] = useState<api.ArchivistRun[]>([]);

  // 新增表单
  const [newLayer, setNewLayer] = useState<Layer>("L1");
  const [newContent, setNewContent] = useState("");
  const [adding, setAdding] = useState(false);

  // 行内编辑
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");
  const [correction, setCorrection] = useState<{
    id: string; content: string; note: string;
  } | null>(null);

  const refresh = () => {
    setLoading(true);
    Promise.all([
      api.listMemories(), api.listMemoryCandidates(), api.listMemoryRelations(), api.listArchivistRuns(),
    ])
      .then(([m, pending, activeRelations, runs]) => {
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
        setRelations(activeRelations);
        setArchivistRuns(runs);
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

  const onSaveCorrection = async () => {
    if (!correction?.content.trim()) {
      toast("纠正后的内容不能为空");
      return;
    }
    try {
      await api.correctMemory(correction.id, correction.content.trim(), correction.note.trim());
      setCorrection(null);
      toast("已按纠错语义保存，并保留审计记录");
      refresh();
    } catch (e: any) {
      toast(e.message || "纠正失败");
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

  const openLifecycle = async (m: api.Memory) => {
    if (expandedLifecycle === m.id) {
      setExpandedLifecycle(null);
      return;
    }
    try {
      const detail = await api.getMemoryLifecycle(m.id);
      setLifecycleDetails((current) => ({ ...current, [m.id]: detail }));
      setExpandedLifecycle(m.id);
    } catch (e: any) {
      toast(e.message || "生命周期详情加载失败");
    }
  };

  const restore = async (m: api.Memory) => {
    try {
      await api.restoreMemory(m.id, m.lifecycle_revision, "用户在记忆管理页手动恢复");
      toast("记忆已恢复为活跃状态");
      setExpandedLifecycle(null);
      refresh();
    } catch (e: any) {
      toast(e.message || "恢复失败，请刷新后重试");
    }
  };

  const privacyDelete = async (m: api.Memory) => {
    if (!window.confirm(
      `永久清除这条记忆及其应用内审计信息？\n\n「${m.content}」\n\n应用不会自动创建备份；应用外已有备份不受影响。`,
    )) return;
    if (window.prompt("此操作不可撤销。请输入 DELETE 继续：") !== "DELETE") {
      toast("已取消永久清除");
      return;
    }
    try {
      await api.privacyDeleteMemory(m.id);
      toast("记忆已从本地数据库永久清除");
      setExpandedLifecycle(null);
      refresh();
    } catch (e: any) {
      toast(e.message || "永久清除失败");
    }
  };

  const scanRelations = async () => {
    try {
      const result = await api.scanMemoryRelations();
      toast(`检查完成，新增 ${result.created_count} 条关系`);
      refresh();
    } catch (e: any) {
      toast(e.message || "冲突检查失败");
    }
  };

  const disposeRelation = async (relation: api.MemoryRelation, status: "resolved" | "dismissed") => {
    const reason = window.prompt(status === "resolved" ? "请说明如何解决：" : "请说明为何忽略：");
    if (!reason?.trim()) return;
    try {
      await api.setMemoryRelationStatus(relation.id, status, reason.trim());
      toast(status === "resolved" ? "已标记解决" : "已忽略这条提示");
      refresh();
    } catch (e: any) {
      toast(e.message || "处理失败");
    }
  };

  return (
    <div className="page memory-page">
      <div className="memory-page-hero">
        <div className="memory-page-eyebrow">MEMORY ARCHIVE</div>
        <h1>记忆与关系</h1>
        <div className="sub">
          遐蝶会依据人格自主选择值得留下的事；你仍可以查看来源、编辑、纠正、禁用或删除。
        </div>
      </div>

      <EntitiesSection memories={memories} onOpenSource={onOpenSource} />
      <EpisodesSection onOpenSource={onOpenSource} />
      <SagasSection onOpenSource={onOpenSource} />

      <section className="memory-section memory-conflict-section">
        <div className="episode-heading">
          <div>
            <div className="section-label">记忆关系与维护</div>
            <div className="sub">只提示可能的新旧关系，不会自动改写或删除任何记忆。</div>
          </div>
          <button className="btn ghost" onClick={scanRelations}>检查关系</button>
        </div>
        {archivistRuns[0] && (
          <div className="memory-maintenance-summary">
            最近维护：扫描 {archivistRuns[0].scanned_count} 条 · 状态变化 {archivistRuns[0].transitioned_count} 条 ·
            并发冲突 {archivistRuns[0].conflict_count} 次 · 新关系 {archivistRuns[0].relation_count} 条
          </div>
        )}
        {relations.length === 0 ? <div className="empty">当前没有待处理的记忆关系。</div> : relations.map((relation) => (
          <div className="memory-relation-row" key={relation.id}>
            <div>
              <strong>{relation.relation_type === "superseded" ? "明确的新旧变化" : "可能存在冲突"}</strong>
              <small>{relation.entity_name} · 置信度 {Math.round(relation.confidence * 100)}%</small>
              <div>旧：{relation.source_content}</div><div>新：{relation.target_content}</div>
            </div>
            <div className="row">
              <button className="btn ghost" onClick={() => disposeRelation(relation, "resolved")}>标记已解决</button>
              <button className="btn ghost" onClick={() => disposeRelation(relation, "dismissed")}>忽略提示</button>
            </div>
          </div>
        ))}
      </section>

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
                const isAuto = m.source === "observer" || m.source === "auto" || m.source === "auto_confirmed";
                const isObserver = m.source === "observer";
                const isEditing = editingId === m.id;
                const lifecycle = lifecycleDetails[m.id];
                return (
                  <div
                    key={m.id}
                    className="list-row memory-fragment-card"
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

                    {isAuto && (
                      <span className="chip auto">{isObserver ? "遐蝶自主记忆" : "旧版自动"}</span>
                    )}

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
                          onClick={() => setCorrection({ id: m.id, content: m.content, note: "" })}
                        >
                          纠正
                        </button>
                        <button
                          className="btn ghost"
                          onClick={() => onToggleEnabled(m)}
                        >
                          {m.enabled ? "禁用" : "启用"}
                        </button>
                        <button className="btn ghost" onClick={() => openLifecycle(m)}>
                          {expandedLifecycle === m.id ? "收起生命周期" : "生命周期"}
                        </button>
                        {(m.status === "cooling" || m.status === "frozen") && (
                          <button className="btn ghost" onClick={() => restore(m)}>恢复</button>
                        )}
                        <button
                          className="btn ghost"
                          style={{ color: "var(--danger)" }}
                          onClick={() => onDelete(m)}
                        >
                          删除
                        </button>
                      </>
                    )}

                    {isObserver && (
                      <div className="memory-observer-detail">
                        <div className="memory-detail-tags">
                          <span>{SCOPE_LABELS[m.scope || ""] || m.scope || "未分类视角"}</span>
                          <span>{KIND_LABELS[m.kind || ""] || m.kind || "未分类类型"}</span>
                          <span>重要度 {Math.round((m.importance || 0) * 100)}%</span>
                          {m.emotion && <span>情绪：{m.emotion}</span>}
                        </div>
                        {m.inner_reason && (
                          <div className="memory-reason">
                            <strong>为什么留下</strong>
                            <span>{m.inner_reason}</span>
                          </div>
                        )}
                        <div className="memory-provenance">
                          <span>观察器：{m.observer_version || "未知版本"}</span>
                          <span>证据消息：{m.evidence_message_ids?.length || 0} 条</span>
                          <span>置信度：{Math.round((m.confidence || 0) * 100)}%</span>
                        </div>
                      </div>
                    )}

                    {expandedLifecycle === m.id && lifecycle && (
                      <div className="memory-lifecycle-detail">
                        <div className="memory-detail-tags">
                          <span>状态：{fragmentStatusLabel(m.status)}</span>
                          <span>保留分：{Math.round((lifecycle.evaluation?.score || 0) * 100)}%</span>
                          <span>召回：{m.recall_count || 0} 次</span>
                          <span>修订：{m.lifecycle_revision || 0}</span>
                        </div>
                        <div className="memory-reason">
                          <strong>保护原因</strong>
                          <span>{lifecycle.evaluation?.protection_reasons.length
                            ? lifecycle.evaluation.protection_reasons.join("、") : "无自动保护条件"}</span>
                        </div>
                        <div className="memory-provenance">
                          {Object.entries(lifecycle.evaluation?.components || {}).map(([key, value]) => (
                            <span key={key}>{key}: {Math.round(value * 100)}%</span>
                          ))}
                        </div>
                        <details>
                          <summary>状态事件 {lifecycle.events.length} 条 · 关系 {lifecycle.relations.length} 条</summary>
                          {lifecycle.events.map((event) => (
                            <div className="memory-lifecycle-event" key={event.id}>
                              {fragmentStatusLabel(event.from_status)} → {fragmentStatusLabel(event.to_status)}
                              <small>{new Date(event.created_at * 1000).toLocaleString("zh-CN")} · {event.reason_code}</small>
                            </div>
                          ))}
                        </details>
                        <button className="btn ghost" style={{ color: "var(--danger)" }} onClick={() => privacyDelete(m)}>
                          永久清除隐私数据
                        </button>
                      </div>
                    )}

                    {correction?.id === m.id && (
                      <div className="memory-correction-editor">
                        <div className="memory-correction-title">纠正这条记忆</div>
                        <textarea
                          value={correction.content}
                          onChange={(e) => setCorrection({ ...correction, content: e.target.value })}
                          rows={2}
                        />
                        <input
                          value={correction.note}
                          placeholder="纠正原因（可选，例如：之前记错了时间）"
                          onChange={(e) => setCorrection({ ...correction, note: e.target.value })}
                        />
                        <div className="row">
                          <button className="btn" onClick={onSaveCorrection}>保存纠正</button>
                          <button className="btn ghost" onClick={() => setCorrection(null)}>取消</button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          );
        })}

      <details className="legacy-memory-candidates">
        <summary>
          旧版候选兼容区
          <span>{candidates.length > 0 ? ` · ${candidates.length} 条待处理` : " · 暂无待处理项"}</span>
        </summary>
        <div className="sub">
          这里仅保留模型不可用时产生的旧关键词候选。自主记忆不会进入此处；旧数据处理完之前不会删除该入口。
        </div>
        {candidates.length === 0 && <div className="empty">没有需要兼容处理的旧候选。</div>}
        {candidates.map((candidate) => {
          const edit = candidateEdits[candidate.id] || {
            content: candidate.content,
            layer: candidate.proposed_layer,
          };
          return (
            <div key={candidate.id} className="legacy-candidate-row">
              <select
                value={edit.layer}
                onChange={(e) => setCandidateEdits((current) => ({
                  ...current,
                  [candidate.id]: { ...edit, layer: e.target.value as Layer },
                }))}
              >
                {LAYERS.map((layer) => <option key={layer.key} value={layer.key}>{layer.key}</option>)}
              </select>
              <input
                value={edit.content}
                onChange={(e) => setCandidateEdits((current) => ({
                  ...current,
                  [candidate.id]: { ...edit, content: e.target.value },
                }))}
              />
              {candidate.sensitivity === "sensitive" && <span className="chip danger">可能敏感</span>}
              {candidate.source_available && candidate.source_session_id && candidate.source_message_id ? (
                <button
                  className="btn ghost"
                  onClick={() => onOpenSource(candidate.source_session_id!, candidate.source_message_id!)}
                >
                  查看来源
                </button>
              ) : <span className="chip">来源已不存在</span>}
              <button className="btn" onClick={() => onAcceptCandidate(candidate)}>接受旧候选</button>
              <button className="btn ghost" onClick={() => onRejectCandidate(candidate)}>拒绝</button>
            </div>
          );
        })}
      </details>
    </div>
  );
}

function fragmentStatusLabel(status?: api.Memory["status"] | string): string {
  return ({ active: "活跃", cooling: "冷却", frozen: "冻结", tombstone: "已删除" } as Record<string, string>)[status || "active"] || status || "活跃";
}
