import { useEffect, useState } from "react";
import * as api from "./../api";
import { toast } from "./../store";

interface Props {
  onOpenSource: (sessionId: string, messageId: string) => void;
}

type Draft = {
  title: string;
  summary: string;
  significance: number;
  fragmentIds: string[];
};

export function EpisodesSection({ onOpenSource }: Props) {
  const [candidates, setCandidates] = useState<api.EpisodeCandidate[]>([]);
  const [episodes, setEpisodes] = useState<api.MemoryEpisode[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

  const refresh = async () => {
    const [pending, existing] = await Promise.all([
      api.listEpisodeCandidates(),
      api.listEpisodes(),
    ]);
    setCandidates(pending);
    setEpisodes(existing);
    setDrafts(Object.fromEntries(pending.map((candidate) => [candidate.id, {
      title: candidate.title,
      summary: candidate.summary,
      significance: candidate.significance,
      fragmentIds: candidate.fragments.map((fragment) => fragment.id),
    }])));
    return { pending, existing };
  };

  useEffect(() => {
    refresh().catch(() => toast("Episode 加载失败"));
  }, []);

  const generate = async () => {
    setGenerating(true);
    try {
      await api.generateEpisodeCandidates();
      await refresh();
      toast("后台整理已安排，稍后可在这里查看结果");
    } catch (error: any) {
      toast(error.message || "候选生成失败");
    } finally {
      setGenerating(false);
    }
  };

  const updateDraft = (id: string, patch: Partial<Draft>) => {
    setDrafts((current) => ({ ...current, [id]: { ...current[id], ...patch } }));
  };

  const toggleFragment = (candidateId: string, fragmentId: string) => {
    const draft = drafts[candidateId];
    const selected = draft.fragmentIds.includes(fragmentId)
      ? draft.fragmentIds.filter((id) => id !== fragmentId)
      : [...draft.fragmentIds, fragmentId];
    updateDraft(candidateId, { fragmentIds: selected });
  };

  const accept = async (candidate: api.EpisodeCandidate) => {
    const draft = drafts[candidate.id];
    if (!draft?.title.trim() || !draft.summary.trim()) return toast("标题和摘要不能为空");
    if (draft.fragmentIds.length < 2) return toast("Episode 至少需要两条记忆");
    try {
      await api.acceptEpisodeCandidate(candidate.id, {
        title: draft.title.trim(),
        summary: draft.summary.trim(),
        significance: draft.significance,
        fragment_ids: draft.fragmentIds,
      });
      await refresh();
      toast("Episode 已确认");
    } catch (error: any) {
      toast(error.message || "确认失败");
    }
  };

  const reject = async (candidate: api.EpisodeCandidate) => {
    try {
      await api.rejectEpisodeCandidate(candidate.id);
      await refresh();
      toast("已拒绝这组 Episode 候选");
    } catch (error: any) {
      toast(error.message || "拒绝失败");
    }
  };

  const openEpisode = async (id: string) => {
    if (expanded === id) return setExpanded(null);
    try {
      const detail = await api.getEpisode(id);
      setEpisodes((current) => current.map((episode) => episode.id === id ? detail : episode));
      setExpanded(id);
    } catch (error: any) {
      toast(error.message || "Episode 详情加载失败");
    }
  };

  return (
    <section className="memory-section memory-episode-section">
      <div className="episode-heading">
        <div>
          <div className="section-label">经历 · Episode</div>
          <div className="sub">把相关的零散记忆整理成一次完整经历，候选不会自动成为长期 Episode。</div>
        </div>
        <button className="btn ghost" disabled={generating} onClick={generate}>
          {generating ? "安排中…" : "安排后台整理"}
        </button>
      </div>

      {candidates.map((candidate) => {
        const draft = drafts[candidate.id];
        if (!draft) return null;
        return (
          <div className="episode-candidate" key={candidate.id}>
            <div className="episode-candidate-title">待确认 · {formatDate(candidate.start_at)} 至 {formatDate(candidate.end_at)}</div>
            <label className="episode-field">
              <span>经历名称</span>
              <input value={draft.title} onChange={(event) => updateDraft(candidate.id, { title: event.target.value })} />
            </label>
            <label className="episode-field">
              <span>经历摘要</span>
              <textarea rows={3} value={draft.summary} onChange={(event) => updateDraft(candidate.id, { summary: event.target.value })} />
            </label>
            <div className="episode-score">
              <label>重要度</label>
              <input type="range" min={1} max={10} value={draft.significance} onChange={(event) => updateDraft(candidate.id, { significance: Number(event.target.value) })} />
              <span>{draft.significance}/10</span>
              <span className="chip">规则置信度 {Math.round(candidate.confidence * 100)}%</span>
            </div>
            <div className="episode-fragments">
              {candidate.fragments.map((fragment) => (
                <label key={fragment.id} className="episode-fragment-check">
                  <input
                    type="checkbox"
                    checked={draft.fragmentIds.includes(fragment.id)}
                    onChange={() => toggleFragment(candidate.id, fragment.id)}
                  />
                  <span>{fragment.content}</span>
                  {fragment.source_available && fragment.source_session_id && fragment.source_message_id && (
                    <button type="button" onClick={(event) => {
                      event.preventDefault();
                      onOpenSource(fragment.source_session_id!, fragment.source_message_id!);
                    }}>来源</button>
                  )}
                </label>
              ))}
            </div>
            <div className="entity-actions">
              <button className="btn" onClick={() => accept(candidate)}>接受 Episode</button>
              <button className="btn ghost" onClick={() => reject(candidate)}>拒绝</button>
            </div>
          </div>
        );
      })}

      {candidates.length === 0 && episodes.length === 0 && (
        <div className="empty" style={{ padding: 20 }}>还没有可整理的经历。至少需要两条相关正式记忆。</div>
      )}

      {episodes.length > 0 && (
        <div className="episode-list">
          {episodes.map((episode) => (
            <div className="episode-item" key={episode.id}>
              <button className="episode-item-head" onClick={() => openEpisode(episode.id)}>
                <span>{episode.title}</span>
                <small>{formatDate(episode.start_at)} · {episode.fragment_count} 条 · 重要度 {episode.significance}</small>
              </button>
              {expanded === episode.id && (
                <div className="episode-item-detail">
                  <p>{episode.summary}</p>
                  {!!episode.entities?.length && <div className="chip">涉及：{episode.entities.map((entity) => entity.name).join("、")}</div>}
                  {episode.fragments?.map((fragment) => (
                    <div className="entity-fragment" key={fragment.id}>
                      {fragment.content}
                      {fragment.source_available && fragment.source_session_id && fragment.source_message_id && (
                        <div className="msg-meta"><button onClick={() => onOpenSource(fragment.source_session_id!, fragment.source_message_id!)}>查看来源</button></div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function formatDate(value: number): string {
  return new Date(value * 1000).toLocaleDateString("zh-CN");
}
