import { useEffect, useMemo, useState } from "react";
import * as api from "./../api";
import { toast } from "./../store";

interface Props {
  memories: api.Memory[];
  onOpenSource: (sessionId: string, messageId: string) => void;
}

const TYPES = [
  ["person", "人物"], ["pet", "宠物"], ["organization", "组织"],
  ["place", "地点"], ["event", "事件"], ["project", "项目"],
  ["work", "作品"], ["hobby", "爱好"], ["concept", "概念"],
];

type Draft = {
  name: string;
  entity_type: string;
  summary: string;
  aliases: string;
  tags: string;
  current_status: string;
  status_since: string;
};

export function EntitiesSection({ memories, onOpenSource }: Props) {
  const [entities, setEntities] = useState<api.MemoryEntity[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<api.MemoryEntity | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState("person");
  const [mergeSource, setMergeSource] = useState("");
  const [linkFragment, setLinkFragment] = useState("");

  const refreshList = async (keepId?: string | null) => {
    const list = await api.listEntities();
    setEntities(list);
    const wanted = keepId ?? selectedId;
    if (wanted && list.some((entity) => entity.id === wanted)) {
      await selectEntity(wanted);
    } else if (!wanted) {
      setDetail(null);
      setDraft(null);
    }
  };

  const selectEntity = async (id: string) => {
    setSelectedId(id);
    const entity = await api.getEntity(id);
    setDetail(entity);
    setDraft(toDraft(entity));
    setMergeSource("");
    setLinkFragment("");
  };

  useEffect(() => {
    refreshList(null).catch(() => toast("实体列表加载失败"));
  }, []);

  const availableMemories = useMemo(() => {
    const linked = new Set((detail?.fragments || []).map((fragment) => fragment.id));
    return memories.filter((memory) => !linked.has(memory.id));
  }, [detail, memories]);

  const create = async () => {
    if (!newName.trim()) return toast("请输入实体名称");
    try {
      const entity = await api.addEntity({ name: newName.trim(), entity_type: newType });
      setNewName("");
      await refreshList(entity.id);
      toast("实体已创建");
    } catch (error: any) {
      toast(error.message || "创建失败");
    }
  };

  const save = async () => {
    if (!detail || !draft || !draft.name.trim()) return;
    try {
      const entity = await api.updateEntity(detail.id, {
        name: draft.name.trim(),
        entity_type: draft.entity_type,
        summary: draft.summary.trim(),
        aliases: splitList(draft.aliases),
        tags: splitList(draft.tags),
        current_status: draft.current_status.trim(),
        status_since: draft.status_since.trim(),
      });
      setDetail(entity);
      setDraft(toDraft(entity));
      await refreshList(entity.id);
      toast("实体档案已保存");
    } catch (error: any) {
      toast(error.message || "保存失败");
    }
  };

  const archive = async () => {
    if (!detail || !window.confirm(`归档实体「${detail.name}」并解除全部记忆关联吗？`)) return;
    try {
      await api.deleteEntity(detail.id);
      setSelectedId(null);
      setDetail(null);
      setDraft(null);
      await refreshList(null);
      toast("实体已归档");
    } catch (error: any) {
      toast(error.message || "归档失败");
    }
  };

  const merge = async () => {
    if (!detail || !mergeSource) return;
    const source = entities.find((entity) => entity.id === mergeSource);
    if (!source || !window.confirm(`把「${source.name}」合并进「${detail.name}」吗？`)) return;
    try {
      const entity = await api.mergeEntity(detail.id, source.id);
      await refreshList(entity.id);
      toast("实体已合并，原名称已保留为别名");
    } catch (error: any) {
      toast(error.message || "合并失败");
    }
  };

  const link = async () => {
    if (!detail || !linkFragment) return;
    try {
      const entity = await api.linkEntityFragment(detail.id, linkFragment);
      setDetail(entity);
      setDraft(toDraft(entity));
      setLinkFragment("");
      await refreshList(entity.id);
      toast("记忆已关联");
    } catch (error: any) {
      toast(error.message || "关联失败");
    }
  };

  const unlink = async (fragmentId: string) => {
    if (!detail) return;
    try {
      const entity = await api.unlinkEntityFragment(detail.id, fragmentId);
      setDetail(entity);
      setDraft(toDraft(entity));
      await refreshList(entity.id);
      toast("关联已解除");
    } catch (error: any) {
      toast(error.message || "解除关联失败");
    }
  };

  return (
    <section className="memory-section memory-entity-section">
      <div className="section-label">实体档案</div>
      <div className="sub" style={{ marginBottom: 12 }}>
        人物、宠物、地点和项目会把相关记忆聚在一起；自动识别不确定时会保持未关联。
      </div>

      <div className="list-row entity-create-row" style={{ flexWrap: "wrap" }}>
        <input
          value={newName}
          placeholder="新实体名称"
          onChange={(event) => setNewName(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") create(); }}
          style={{ flex: 1, minWidth: 160 }}
        />
        <select value={newType} onChange={(event) => setNewType(event.target.value)}>
          {TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <button className="btn" onClick={create}>＋ 创建实体</button>
      </div>

      {entities.length === 0 ? (
        <div className="empty" style={{ padding: 20 }}>还没有实体。确认包含明确名称的记忆后会自动出现。</div>
      ) : (
        <div className="entity-layout">
          <div className="entity-list">
            {entities.map((entity) => (
              <button
                key={entity.id}
                className={`entity-item${selectedId === entity.id ? " active" : ""}`}
                onClick={() => selectEntity(entity.id)}
              >
                <span>{entity.name}</span>
                <small>{typeLabel(entity.entity_type)} · {entity.fragment_count} 条</small>
              </button>
            ))}
          </div>

          {detail && draft ? (
            <div className="entity-detail">
              <div className="entity-form-grid">
                <label>名称<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
                <label>类型<select value={draft.entity_type} onChange={(e) => setDraft({ ...draft, entity_type: e.target.value })}>{TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                <label className="wide">别名<input value={draft.aliases} placeholder="用逗号分隔" onChange={(e) => setDraft({ ...draft, aliases: e.target.value })} /></label>
                <label className="wide">标签<input value={draft.tags} placeholder="用逗号分隔" onChange={(e) => setDraft({ ...draft, tags: e.target.value })} /></label>
                <label className="wide">概述<textarea rows={2} value={draft.summary} onChange={(e) => setDraft({ ...draft, summary: e.target.value })} /></label>
                <label>当前状态<input value={draft.current_status} onChange={(e) => setDraft({ ...draft, current_status: e.target.value })} /></label>
                <label>状态始于<input value={draft.status_since} placeholder="YYYY-MM" onChange={(e) => setDraft({ ...draft, status_since: e.target.value })} /></label>
              </div>
              <div className="entity-actions">
                <button className="btn" onClick={save}>保存档案</button>
                <select value={mergeSource} onChange={(e) => setMergeSource(e.target.value)}>
                  <option value="">选择要并入的实体</option>
                  {entities.filter((entity) => entity.id !== detail.id).map((entity) => <option key={entity.id} value={entity.id}>{entity.name}</option>)}
                </select>
                <button className="btn ghost" disabled={!mergeSource} onClick={merge}>合并</button>
                <button className="btn ghost" style={{ color: "var(--danger)" }} onClick={archive}>归档</button>
              </div>

              <div className="section-label" style={{ marginTop: 18 }}>关联记忆 · {detail.fragments?.length || 0}</div>
              {availableMemories.length > 0 && (
                <div className="entity-actions">
                  <select value={linkFragment} onChange={(e) => setLinkFragment(e.target.value)}>
                    <option value="">选择一条未关联记忆</option>
                    {availableMemories.map((memory) => <option key={memory.id} value={memory.id}>{memory.content.slice(0, 45)}</option>)}
                  </select>
                  <button className="btn ghost" disabled={!linkFragment} onClick={link}>添加关联</button>
                </div>
              )}
              {(detail.fragments || []).map((fragment) => (
                <div className="entity-fragment" key={fragment.id}>
                  <div>{fragment.content}</div>
                  <div className="msg-meta">
                    <span>{fragment.relation} · {Math.round(fragment.confidence * 100)}%</span>
                    {fragment.source_available && fragment.source_session_id && fragment.source_message_id && (
                      <button onClick={() => onOpenSource(fragment.source_session_id!, fragment.source_message_id!)}>来源：{fragment.source_session_title || "原对话"}</button>
                    )}
                    <button onClick={() => unlink(fragment.id)}>解除关联</button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty" style={{ flex: 1 }}>选择一个实体查看档案</div>
          )}
        </div>
      )}
    </section>
  );
}

function toDraft(entity: api.MemoryEntity): Draft {
  return {
    name: entity.name,
    entity_type: entity.entity_type,
    summary: entity.summary || "",
    aliases: (entity.aliases || []).join("，"),
    tags: (entity.tags || []).join("，"),
    current_status: entity.current_status || "",
    status_since: entity.status_since || "",
  };
}

function splitList(value: string): string[] {
  return value.split(/[,，、\n]/).map((item) => item.trim()).filter(Boolean);
}

function typeLabel(value: string): string {
  return TYPES.find(([key]) => key === value)?.[1] || value;
}
