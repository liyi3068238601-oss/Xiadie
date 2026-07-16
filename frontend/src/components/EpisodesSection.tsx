import { useEffect, useState } from "react";
import * as api from "./../api";
import { episodeSummaryPresentation, shortSourceHash } from "./../episodePresentation.mjs";
import { toast } from "./../store";

interface Props {
  onOpenSource: (sessionId: string, messageId: string) => void;
}

type CorrectionDraft = {
  title: string;
  summary: string;
  significance: number;
  note: string;
};

export function EpisodesSection({ onOpenSource }: Props) {
  const [episodes, setEpisodes] = useState<api.MemoryEpisode[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<CorrectionDraft | null>(null);
  const [busy, setBusy] = useState<"refresh" | "schedule" | "save" | "lifecycle" | null>(null);

  const refresh = async () => {
    const existing = await api.listEpisodes();
    setEpisodes(existing);
    return existing;
  };

  useEffect(() => {
    refresh().catch(() => toast("经历加载失败"));
  }, []);

  const refreshNow = async () => {
    setBusy("refresh");
    try {
      await refresh();
      setExpanded(null);
      setEditing(null);
      setDraft(null);
      toast("经历列表已刷新");
    } catch (error: any) {
      toast(error.message || "经历刷新失败");
    } finally {
      setBusy(null);
    }
  };

  const schedule = async () => {
    setBusy("schedule");
    try {
      await api.generateEpisodeCandidates();
      toast("后台整理已安排，完成后刷新即可看到新经历");
    } catch (error: any) {
      toast(error.message || "后台整理安排失败");
    } finally {
      setBusy(null);
    }
  };

  const openEpisode = async (id: string) => {
    if (expanded === id) {
      setExpanded(null);
      setEditing(null);
      return;
    }
    try {
      const detail = await api.getEpisode(id);
      setEpisodes((current) => current.map((item) => item.id === id ? detail : item));
      setExpanded(id);
    } catch (error: any) {
      toast(error.message || "经历详情加载失败");
    }
  };

  const beginCorrection = (episode: api.MemoryEpisode) => {
    setEditing(episode.id);
    setDraft({
      title: episode.title,
      summary: episode.summary,
      significance: episode.significance,
      note: "",
    });
  };

  const saveCorrection = async (episodeId: string) => {
    if (!draft?.title.trim() || !draft.summary.trim()) {
      return toast("经历名称和摘要不能为空");
    }
    setBusy("save");
    try {
      const corrected = await api.correctEpisode(episodeId, {
        title: draft.title.trim(),
        summary: draft.summary.trim(),
        significance: draft.significance,
        note: draft.note.trim(),
        expected_revision: episodes.find((item) => item.id === episodeId)?.lifecycle_revision,
      });
      setEpisodes((current) => current.map((item) => item.id === episodeId ? corrected : item));
      setEditing(null);
      setDraft(null);
      toast("经历已纠正，并留下独立审计记录");
    } catch (error: any) {
      toast(error.message || "经历纠正失败");
    } finally {
      setBusy(null);
    }
  };

  const changeLifecycle = async (
    episode: api.MemoryEpisode, target: api.MemoryEpisode["status"], reason: string,
  ) => {
    setBusy("lifecycle");
    try {
      const updated = await api.transitionEpisode(
        episode.id, target, episode.lifecycle_revision, reason,
      );
      setEpisodes((current) => target === "tombstone"
        ? current.filter((item) => item.id !== episode.id)
        : current.map((item) => item.id === episode.id ? updated : item));
      toast(target === "active" ? "经历已恢复" : "经历已删除");
    } catch (error: any) {
      toast(error.message || "生命周期操作失败，请刷新后重试");
    } finally {
      setBusy(null);
    }
  };

  const removeEpisode = async (episode: api.MemoryEpisode) => {
    if (!window.confirm(
      `确定永久删除经历「${episode.title}」吗？\n\n应用不会自动创建备份；应用外已有备份不会被同步清除。`,
    )) return;
    if (window.prompt("这是不可恢复操作。请输入 DELETE 继续：") !== "DELETE") {
      toast("已取消删除");
      return;
    }
    await changeLifecycle(episode, "tombstone", "用户在记忆管理页永久删除");
  };

  const copyHash = async (hash: string) => {
    if (!hash) return;
    try {
      await navigator.clipboard.writeText(hash);
      toast("完整来源校验指纹已复制");
    } catch {
      toast("无法复制，请稍后重试");
    }
  };

  return (
    <section className="memory-section memory-episode-section">
      <div className="episode-heading">
        <div>
          <div className="section-label">共同经历 · Episode</div>
          <div className="sub">遐蝶会在后台把相关正式记忆自主整理成经历；这里用于查看来源与纠错。</div>
        </div>
        <div className="episode-heading-actions">
          <button className="btn ghost" disabled={busy !== null || editing !== null} onClick={refreshNow}>
            {busy === "refresh" ? "刷新中…" : "刷新经历"}
          </button>
          <button className="btn ghost" disabled={busy !== null || editing !== null} onClick={schedule}>
            {busy === "schedule" ? "安排中…" : "检查新经历"}
          </button>
        </div>
      </div>

      {episodes.length === 0 && (
        <div className="empty episode-empty">
          还没有形成共同经历。至少两条具有共同主题的正式记忆通过评分后，系统会在后台自动整理。
        </div>
      )}

      <div className="episode-list">
        {episodes.map((episode) => {
          const presentation = episodeSummaryPresentation(episode.summary_status);
          const isExpanded = expanded === episode.id;
          const isEditing = editing === episode.id && draft;
          return (
            <article className="episode-item" key={episode.id}>
              <button
                className="episode-item-head"
                aria-expanded={isExpanded}
                onClick={() => openEpisode(episode.id)}
              >
                <span className="episode-title-group">
                  <strong>{episode.title}</strong>
                  <span className={`episode-status ${presentation.tone}`}>{presentation.label}</span>
                  <span className="chip">{episodeStatusLabel(episode.status)}</span>
                </span>
                <small>
                  {formatDateRange(episode.start_at, episode.end_at)} · {episode.fragment_count} 条来源 · 重要度 {episode.significance}
                </small>
              </button>

              {isExpanded && (
                <div className="episode-item-detail">
                  {!isEditing && <p className="episode-summary">{episode.summary}</p>}

                  <div className="episode-audit-grid">
                    <div><span>摘要状态</span><strong>{presentation.label}</strong><small>{presentation.detail}</small></div>
                    <div><span>来源校验</span><strong>{shortSourceHash(episode.source_hash)}</strong><small>内容哈希短指纹</small></div>
                    <div><span>整理方式</span><strong>{episode.source === "consolidator_auto" ? "后台自主整理" : "兼容流程"}</strong><small>{episode.application_version}</small></div>
                    <div><span>规则置信度</span><strong>{Math.round(episode.confidence * 100)}%</strong><small>{episode.policy_version}</small></div>
                    <div><span>生命周期</span><strong>{episodeStatusLabel(episode.status)}</strong><small>修订 {episode.lifecycle_revision}</small></div>
                  </div>

                  <div className="episode-detail-actions">
                    {!!episode.source_hash && (
                      <button className="btn ghost" onClick={() => copyHash(episode.source_hash)}>复制完整校验指纹</button>
                    )}
                    {!isEditing && <button className="btn ghost" onClick={() => beginCorrection(episode)}>纠正这段经历</button>}
                    {episode.status !== "active" && (
                      <button className="btn ghost" disabled={busy !== null} onClick={() => changeLifecycle(
                        episode, "active", "用户在经历管理页手动恢复",
                      )}>恢复经历</button>
                    )}
                    <button
                      className="btn ghost" disabled={busy !== null}
                      style={{ color: "var(--danger)" }} onClick={() => removeEpisode(episode)}
                    >永久删除</button>
                  </div>

                  {isEditing && draft && (
                    <div className="episode-correction">
                      <label><span>经历名称</span><input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
                      <label><span>经历摘要</span><textarea rows={4} value={draft.summary} onChange={(event) => setDraft({ ...draft, summary: event.target.value })} /></label>
                      <label><span>重要度 · {draft.significance}/10</span><input type="range" min={1} max={10} value={draft.significance} onChange={(event) => setDraft({ ...draft, significance: Number(event.target.value) })} /></label>
                      <label><span>纠错说明（可选）</span><input value={draft.note} maxLength={240} onChange={(event) => setDraft({ ...draft, note: event.target.value })} /></label>
                      <div className="entity-actions">
                        <button className="btn" disabled={busy === "save"} onClick={() => saveCorrection(episode.id)}>{busy === "save" ? "保存中…" : "保存纠错"}</button>
                        <button className="btn ghost" onClick={() => { setEditing(null); setDraft(null); }}>取消</button>
                      </div>
                    </div>
                  )}

                  {!!episode.entities?.length && (
                    <div className="episode-entities">涉及：{episode.entities.map((entity) => entity.name).join("、")}</div>
                  )}

                  <div className="episode-source-heading">
                    <strong>来源记忆</strong>
                    <span>{episode.fragments?.length ?? 0} / {episode.source_fragment_ids.length} 条可读取</span>
                  </div>
                  <div className="episode-sources">
                    {episode.fragments?.map((fragment, index) => (
                      <div className="episode-source" key={fragment.id}>
                        <div className="episode-source-index">{index + 1}</div>
                        <div className="episode-source-body">
                          <div>{fragment.content}</div>
                          <small>{formatDate(fragment.created_at)} · {fragment.source_available ? "原对话可用" : "仅保留正式记忆来源"}</small>
                        </div>
                        {fragment.source_available && fragment.source_session_id && fragment.source_message_id && (
                          <button onClick={() => onOpenSource(fragment.source_session_id!, fragment.source_message_id!)}>查看原对话</button>
                        )}
                      </div>
                    ))}
                  </div>
                  {episode.corrected_at && (
                    <div className="episode-correction-note">
                      最近纠错：{formatDateTime(episode.corrected_at)}{episode.correction_note ? ` · ${episode.correction_note}` : ""}
                    </div>
                  )}
                  {!!episode.lifecycle_events?.length && (
                    <details className="episode-lifecycle-events">
                      <summary>生命周期记录 · {episode.lifecycle_events.length} 条</summary>
                      {episode.lifecycle_events.map((event) => (
                        <div className="memory-lifecycle-event" key={event.id}>
                          {episodeStatusLabel(event.from_status)} → {episodeStatusLabel(event.to_status)}
                          <small>{formatDateTime(event.created_at)} · {event.reason_code}</small>
                        </div>
                      ))}
                    </details>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function episodeStatusLabel(status: api.MemoryEpisode["status"]): string {
  return ({ active: "活跃", completed: "已成熟", archived: "已归档", tombstone: "已删除" })[status];
}

function formatDate(value: number): string {
  return new Date(value * 1000).toLocaleDateString("zh-CN");
}

function formatDateTime(value: number): string {
  return new Date(value * 1000).toLocaleString("zh-CN");
}

function formatDateRange(start: number, end: number): string {
  const left = formatDate(start);
  const right = formatDate(end);
  return left === right ? left : `${left} 至 ${right}`;
}
