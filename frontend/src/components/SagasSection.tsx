import { useEffect, useState } from "react";
import * as api from "./../api";
import { shortSourceHash } from "./../episodePresentation.mjs";
import {
  allowedSagaTransitions,
  sagaEventLabel,
  sagaRoleLabel,
  sagaStatusPresentation,
  sagaSummaryPresentation,
} from "./../sagaPresentation.mjs";
import { toast } from "./../store";

interface Props {
  onOpenSource: (sessionId: string, messageId: string) => void;
}

type ContentDraft = {
  title: string;
  summary: string;
  theme: string;
  currentStage: string;
  significance: number;
  note: string;
};

type LifecycleDraft = {
  target: api.SagaStatus;
  reason: string;
};

export function SagasSection({ onOpenSource }: Props) {
  const [sagas, setSagas] = useState<api.MemorySaga[]>([]);
  const [episodes, setEpisodes] = useState<api.MemoryEpisode[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [editing, setEditing] = useState<"content" | "sources" | null>(null);
  const [contentDraft, setContentDraft] = useState<ContentDraft | null>(null);
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [sourceNote, setSourceNote] = useState("");
  const [lifecycleDraft, setLifecycleDraft] = useState<LifecycleDraft | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = async () => {
    const [storyList, episodeList] = await Promise.all([
      api.listSagas(undefined, 100),
      api.listEpisodes(),
    ]);
    setSagas(storyList);
    setEpisodes(episodeList.slice().sort((a, b) => a.start_at - b.start_at || a.id.localeCompare(b.id)));
    return storyList;
  };

  useEffect(() => {
    refresh().catch(() => toast("长期故事加载失败"));
  }, []);

  const replaceSaga = (detail: api.MemorySaga) => {
    setSagas((current) => current.map((item) => item.id === detail.id ? detail : item));
  };

  const loadDetail = async (id: string) => {
    const detail = await api.getSaga(id);
    replaceSaga(detail);
    return detail;
  };

  const openSaga = async (id: string) => {
    if (expanded === id) {
      setExpanded(null);
      clearEditors();
      return;
    }
    setBusy(`open:${id}`);
    try {
      await loadDetail(id);
      setExpanded(id);
      clearEditors();
    } catch (error: any) {
      toast(error.message || "长期故事详情加载失败");
    } finally {
      setBusy(null);
    }
  };

  const refreshNow = async () => {
    setBusy("refresh");
    try {
      await refresh();
      setExpanded(null);
      clearEditors();
      toast("长期故事列表已刷新");
    } catch (error: any) {
      toast(error.message || "刷新失败");
    } finally {
      setBusy(null);
    }
  };

  const schedule = async () => {
    setBusy("schedule");
    try {
      await api.enqueueSagaConsolidator(`ui-${Date.now()}`);
      toast("长期故事整理已在后台排队，稍后刷新即可查看结果");
    } catch (error: any) {
      toast(error.message || "整理任务安排失败");
    } finally {
      setBusy(null);
    }
  };

  const beginContentEdit = (saga: api.MemorySaga) => {
    setEditing("content");
    setContentDraft({
      title: saga.title,
      summary: saga.summary,
      theme: saga.theme,
      currentStage: saga.current_stage,
      significance: saga.significance,
      note: "",
    });
    setLifecycleDraft(null);
  };

  const saveContent = async (saga: api.MemorySaga) => {
    if (!contentDraft) return;
    if (![contentDraft.title, contentDraft.summary, contentDraft.theme, contentDraft.currentStage]
      .every((value) => value.trim())) {
      toast("名称、摘要、主题和当前阶段不能为空");
      return;
    }
    setBusy(`content:${saga.id}`);
    try {
      const corrected = await api.correctSaga(saga.id, {
        title: contentDraft.title.trim(),
        summary: contentDraft.summary.trim(),
        theme: contentDraft.theme.trim(),
        current_stage: contentDraft.currentStage.trim(),
        significance: contentDraft.significance,
        note: contentDraft.note.trim(),
        expected_revision: saga.revision,
      });
      replaceSaga(corrected);
      clearEditors();
      toast("长期故事已纠正，并保留独立审计记录");
    } catch (error: any) {
      await handleWriteError(error, saga.id, "纠正失败");
    } finally {
      setBusy(null);
    }
  };

  const beginSourceEdit = (saga: api.MemorySaga) => {
    setEditing("sources");
    setSourceIds(saga.source_episode_ids);
    setSourceNote("");
    setLifecycleDraft(null);
  };

  const toggleSource = (episodeId: string) => {
    const selected = new Set(sourceIds);
    if (selected.has(episodeId)) selected.delete(episodeId);
    else selected.add(episodeId);
    setSourceIds(episodes.filter((episode) => selected.has(episode.id)).map((episode) => episode.id));
  };

  const saveSources = async (saga: api.MemorySaga) => {
    if (sourceIds.length < 2) return toast("长期故事至少需要两个正式 Episode");
    if (!sourceNote.trim()) return toast("请说明为什么要纠正来源归组");
    if (!window.confirm("来源纠错会重建基础摘要、实体与来源校验指纹。确定继续吗？")) return;
    setBusy(`sources:${saga.id}`);
    try {
      const corrected = await api.correctSagaSources(
        saga.id, sourceIds, sourceNote.trim(), saga.revision
      );
      replaceSaga(corrected);
      clearEditors();
      toast("来源已纠正；摘要已重置为有来源的基础版本，可继续手动完善");
    } catch (error: any) {
      await handleWriteError(error, saga.id, "来源纠错失败");
    } finally {
      setBusy(null);
    }
  };

  const beginLifecycle = (saga: api.MemorySaga) => {
    const first = allowedSagaTransitions(saga.status)[0] as api.SagaStatus | undefined;
    if (!first) return;
    setLifecycleDraft({ target: first, reason: "" });
    setEditing(null);
    setContentDraft(null);
  };

  const saveLifecycle = async (saga: api.MemorySaga) => {
    if (!lifecycleDraft?.reason.trim()) return toast("请填写状态变化原因");
    const target = lifecycleDraft.target;
    if (target === "tombstone" && !window.confirm(
      "删除长期故事后不可恢复，后台也不会用相同来源自动重建。确定删除吗？"
    )) return;
    setBusy(`lifecycle:${saga.id}`);
    try {
      const changed = await api.transitionSaga(
        saga.id, target, lifecycleDraft.reason.trim(), saga.revision
      );
      replaceSaga(changed);
      clearEditors();
      toast(`长期故事状态已更新为「${sagaStatusPresentation(changed.status).label}」`);
    } catch (error: any) {
      await handleWriteError(error, saga.id, "状态更新失败");
    } finally {
      setBusy(null);
    }
  };

  const handleWriteError = async (error: any, sagaId: string, fallback: string) => {
    if (error instanceof api.ApiError && error.status === 409) {
      try {
        await loadDetail(sagaId);
      } catch {
        // 保留原错误提示；用户仍可手动刷新。
      }
      clearEditors();
      toast("内容已被后台或其他窗口更新，详情已刷新，请确认后重试");
      return;
    }
    toast(error.message || fallback);
  };

  function clearEditors() {
    setEditing(null);
    setContentDraft(null);
    setSourceIds([]);
    setSourceNote("");
    setLifecycleDraft(null);
  }

  return (
    <section className="memory-section saga-section" aria-labelledby="saga-section-title">
      <div className="episode-heading">
        <div>
          <div className="section-label" id="saga-section-title">长期故事 · Saga</div>
          <div className="sub">跨越多个日期的正式经历会形成长期故事；这里展示发展阶段、证据链与状态历史。</div>
        </div>
        <div className="episode-heading-actions">
          <button className="btn ghost" disabled={busy !== null || editing !== null} onClick={refreshNow}>
            {busy === "refresh" ? "刷新中…" : "刷新故事"}
          </button>
          <button className="btn ghost" disabled={busy !== null || editing !== null} onClick={schedule}>
            {busy === "schedule" ? "安排中…" : "检查长期故事"}
          </button>
        </div>
      </div>

      {sagas.length === 0 && (
        <div className="empty saga-empty" role="status">
          还没有形成长期故事。至少两个跨日期、主题连续的正式 Episode 通过评分后，遐蝶会在后台自主整理。
        </div>
      )}

      <div className="saga-list">
        {sagas.map((saga) => {
          const state = sagaStatusPresentation(saga.status);
          const summaryState = sagaSummaryPresentation(saga.summary_status);
          const isExpanded = expanded === saga.id;
          const transitions = allowedSagaTransitions(saga.status) as api.SagaStatus[];
          return (
            <article className={`saga-item saga-${state.tone}`} key={saga.id}>
              <button
                className="saga-item-head"
                aria-expanded={isExpanded}
                aria-controls={`saga-detail-${saga.id}`}
                onClick={() => openSaga(saga.id)}
              >
                <span className="saga-title-group">
                  <span className="saga-state-icon" aria-hidden="true">{statusIcon(saga.status)}</span>
                  <span>
                    <strong>{saga.title}</strong>
                    <small>{saga.theme || "未命名主题"}</small>
                  </span>
                </span>
                <span className="saga-head-meta">
                  <span className={`saga-status ${state.tone}`}>{state.label}</span>
                  <small>{formatDateRange(saga.start_at, saga.end_at)} · {saga.source_episode_ids.length} 段经历</small>
                </span>
              </button>

              {isExpanded && (
                <div className="saga-detail" id={`saga-detail-${saga.id}`}>
                  <div className="saga-state-explanation" role="status">
                    <strong>{state.label}</strong>
                    <span>{state.detail}</span>
                  </div>
                  {!editing && <p className="saga-summary">{saga.summary}</p>}
                  {!editing && (
                    <div className="saga-current-stage">
                      <span>当前阶段</span>
                      <strong>{saga.current_stage || "暂未记录当前阶段"}</strong>
                    </div>
                  )}

                  <div className="episode-audit-grid saga-audit-grid">
                    <div><span>摘要状态</span><strong>{summaryState.label}</strong><small>{summaryState.detail}</small></div>
                    <div><span>来源校验</span><strong>{shortSourceHash(saga.source_hash)}</strong><small>完整 Episode 链指纹</small></div>
                    <div><span>整理协议</span><strong>{saga.summary_protocol_version}</strong><small>{saga.policy_version}</small></div>
                    <div><span>规则置信度</span><strong>{Math.round(saga.confidence * 100)}%</strong><small>revision {saga.revision}</small></div>
                  </div>

                  {saga.status !== "tombstone" && !editing && !lifecycleDraft && (
                    <div className="saga-actions" aria-label="长期故事操作">
                      <button className="btn ghost" onClick={() => beginContentEdit(saga)}>纠正故事内容</button>
                      <button className="btn ghost" onClick={() => beginSourceEdit(saga)}>纠正来源归组</button>
                      {transitions.length > 0 && (
                        <button className="btn ghost" onClick={() => beginLifecycle(saga)}>更改故事状态</button>
                      )}
                    </div>
                  )}

                  {editing === "content" && contentDraft && (
                    <div className="saga-editor" aria-label="纠正长期故事内容">
                      <label><span>故事名称</span><input value={contentDraft.title} maxLength={80} onChange={(e) => setContentDraft({ ...contentDraft, title: e.target.value })} /></label>
                      <label><span>主题</span><input value={contentDraft.theme} maxLength={80} onChange={(e) => setContentDraft({ ...contentDraft, theme: e.target.value })} /></label>
                      <label className="wide"><span>摘要</span><textarea rows={5} value={contentDraft.summary} maxLength={1200} onChange={(e) => setContentDraft({ ...contentDraft, summary: e.target.value })} /></label>
                      <label className="wide"><span>当前阶段</span><textarea rows={2} value={contentDraft.currentStage} maxLength={300} onChange={(e) => setContentDraft({ ...contentDraft, currentStage: e.target.value })} /></label>
                      <label><span>重要度 · {contentDraft.significance}/10</span><input type="range" min={1} max={10} value={contentDraft.significance} onChange={(e) => setContentDraft({ ...contentDraft, significance: Number(e.target.value) })} /></label>
                      <label><span>纠错说明（可选）</span><input value={contentDraft.note} maxLength={240} onChange={(e) => setContentDraft({ ...contentDraft, note: e.target.value })} /></label>
                      <div className="saga-editor-actions wide">
                        <button className="btn" disabled={busy !== null} onClick={() => saveContent(saga)}>保存纠错</button>
                        <button className="btn ghost" onClick={clearEditors}>取消</button>
                      </div>
                    </div>
                  )}

                  {editing === "sources" && (
                    <fieldset className="saga-source-editor">
                      <legend>纠正来源 Episode</legend>
                      <p className="sub">至少选择两个，界面会按经历发生时间提交；保存后摘要会重置为基础抽取版本。</p>
                      <div className="saga-source-options">
                        {episodes.map((episode) => (
                          <label key={episode.id} className={sourceIds.includes(episode.id) ? "selected" : ""}>
                            <input type="checkbox" checked={sourceIds.includes(episode.id)} onChange={() => toggleSource(episode.id)} />
                            <span><strong>{episode.title}</strong><small>{formatDate(episode.start_at)} · {episode.summary.slice(0, 90)}</small></span>
                          </label>
                        ))}
                      </div>
                      <label className="saga-source-note"><span>纠错原因</span><input value={sourceNote} maxLength={240} onChange={(e) => setSourceNote(e.target.value)} /></label>
                      <div className="saga-editor-actions">
                        <button className="btn" disabled={busy !== null || sourceIds.length < 2} onClick={() => saveSources(saga)}>保存来源纠错</button>
                        <button className="btn ghost" onClick={clearEditors}>取消</button>
                      </div>
                    </fieldset>
                  )}

                  {lifecycleDraft && (
                    <div className="saga-lifecycle-editor" aria-label="更改长期故事状态">
                      <label><span>目标状态</span><select value={lifecycleDraft.target} onChange={(e) => setLifecycleDraft({ ...lifecycleDraft, target: e.target.value as api.SagaStatus })}>
                        {transitions.map((target) => <option key={target} value={target}>{sagaStatusPresentation(target).label}</option>)}
                      </select></label>
                      <label><span>变化原因</span><input value={lifecycleDraft.reason} maxLength={240} onChange={(e) => setLifecycleDraft({ ...lifecycleDraft, reason: e.target.value })} /></label>
                      <div className="saga-editor-actions">
                        <button className={lifecycleDraft.target === "tombstone" ? "btn danger" : "btn"} disabled={busy !== null} onClick={() => saveLifecycle(saga)}>确认状态变化</button>
                        <button className="btn ghost" onClick={clearEditors}>取消</button>
                      </div>
                    </div>
                  )}

                  {!!saga.entities?.length && (
                    <div className="saga-entities" aria-label="相关实体">
                      <span>相关实体</span>
                      {saga.entities.map((entity) => <span className="chip" key={entity.entity_id}>{entity.name} · {entity.entity_type}</span>)}
                    </div>
                  )}

                  <div className="saga-subheading">
                    <strong>故事时间线</strong>
                    <span>{saga.timeline?.filter((item) => item.removed_at === null).length ?? 0} 条当前来源</span>
                  </div>
                  <div className="saga-timeline">
                    {saga.timeline?.map((item, index) => (
                      <div className={`saga-timeline-item ${item.removed_at ? "removed" : ""}`} key={`${item.episode_id}-${item.added_at}`}>
                        <div className="saga-timeline-marker"><span>{index + 1}</span></div>
                        <div className="saga-timeline-body">
                          <div className="saga-timeline-title">
                            <strong>{item.episode?.title || "来源 Episode 已不可用"}</strong>
                            <span>{item.removed_at ? "已从当前归组移除" : sagaRoleLabel(item.role)}</span>
                          </div>
                          {item.episode && <p>{item.episode.summary}</p>}
                          <small>{item.episode ? formatDateRange(item.episode.start_at, item.episode.end_at) : formatDate(item.added_at)}</small>
                          {!!item.episode?.fragments?.length && (
                            <details className="saga-fragment-sources">
                              <summary>查看 {item.episode.fragments.length} 条正式记忆来源</summary>
                              {item.episode.fragments.map((fragment) => (
                                <div className="saga-fragment" key={fragment.id}>
                                  <span>{fragment.content}</span>
                                  {fragment.source_available && fragment.source_session_id && fragment.source_message_id ? (
                                    <button onClick={() => onOpenSource(fragment.source_session_id!, fragment.source_message_id!)}>打开原对话</button>
                                  ) : <small>原对话已不可用</small>}
                                </div>
                              ))}
                            </details>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="saga-subheading">
                    <strong>审计记录</strong>
                    <span>最近 {Math.min(saga.events?.length ?? 0, 10)} 条</span>
                  </div>
                  <div className="saga-events">
                    {saga.events?.slice().reverse().slice(0, 10).map((event) => (
                      <div className="saga-event" key={event.id}>
                        <strong>{sagaEventLabel(event.action)}</strong>
                        <span>{formatDateTime(event.created_at)} · {event.source}</span>
                        {event.reason_code && <small>{event.reason_code}</small>}
                      </div>
                    ))}
                  </div>
                  {saga.corrected_at && (
                    <div className="episode-correction-note">
                      最近纠错：{formatDateTime(saga.corrected_at)}{saga.correction_note ? ` · ${saga.correction_note}` : ""}
                    </div>
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

function statusIcon(status: api.SagaStatus): string {
  if (status === "completed") return "✓";
  if (status === "archived") return "□";
  if (status === "tombstone") return "×";
  return "↗";
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
