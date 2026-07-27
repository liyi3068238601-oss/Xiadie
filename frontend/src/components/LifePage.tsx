import { FormEvent, useCallback, useEffect, useState } from "react";
import * as api from "../api";

type Tab = "today" | "diary" | "dates" | "goals" | "settings";

function timeLabel(minutes: number) {
  const hour = Math.floor(minutes / 60).toString().padStart(2, "0");
  const minute = (minutes % 60).toString().padStart(2, "0");
  return `${hour}:${minute}`;
}

function modeCopy(mode: api.LifeContinuityMode) {
  if (mode === "paused") return "已暂停；保留已有生活记录，不继续补算。";
  if (mode === "disabled") return "已关闭；离线期间不会继续推进她的生活。";
  return "已开启；应用退出期间只记录时间，下次启动时进行有界补算。";
}

export function LifePage() {
  const [tab, setTab] = useState<Tab>("today");
  const [schedule, setSchedule] = useState<api.LifeSchedule | null>(null);
  const [settings, setSettings] = useState<api.LifeSettings | null>(null);
  const [lifeState, setLifeState] = useState<api.LifeState | null>(null);
  const [diary, setDiary] = useState<api.LifeDiaryEntry[]>([]);
  const [dates, setDates] = useState<api.LifeImportantDate[]>([]);
  const [goals, setGoals] = useState<api.LifeGoal[]>([]);
  const [diagnostics, setDiagnostics] = useState<api.LifeDiagnostics | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [notice, setNotice] = useState("");
  const [goalTitle, setGoalTitle] = useState("");
  const [dateLabel, setDateLabel] = useState("");
  const [dateValue, setDateValue] = useState("");
  const today = new Date().toLocaleDateString("en-CA");

  const refresh = useCallback(async () => {
    setFailed(false);
    try {
      const [scheduleResult, settingsResult, stateResult, diaryResult, datesResult, goalsResult] = await Promise.all([
        api.getLifeSchedule(today), api.getLifeSettings(), api.getLifeState(),
        api.listLifeDiary(), api.listLifeDates(), api.listLifeGoals(),
      ]);
      setSchedule(scheduleResult.item);
      setSettings(settingsResult);
      setLifeState(stateResult);
      setDiary(diaryResult.items);
      setDates(datesResult.items);
      setGoals(goalsResult.items);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [today]);

  useEffect(() => { void refresh(); }, [refresh]);

  const changeMode = async (mode: api.LifeContinuityMode) => {
    await api.updateLifeSettings(mode);
    setSettings((current) => current ? { ...current, mode } : current);
    setNotice(mode === "continuous_simulated" ? "离线生活已开启" : mode === "paused" ? "生活推进已暂停" : "离线生活已关闭");
  };

  const addGoal = async (event: FormEvent) => {
    event.preventDefault();
    if (!goalTitle.trim()) return;
    await api.createLifeGoal(goalTitle.trim());
    setGoalTitle("");
    await refresh();
  };

  const addDate = async (event: FormEvent) => {
    event.preventDefault();
    if (!dateLabel.trim() || !dateValue) return;
    const [year, month, day] = dateValue.split("-").map(Number);
    await api.createLifeDate({
      label: dateLabel.trim(), recurrence: "once", date_year: year,
      date_month: month, date_day: day,
    });
    setDateLabel("");
    setDateValue("");
    await refresh();
  };

  const downloadExport = async () => {
    const payload = await api.exportLifeData();
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `xiadie-life-${today}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
    setNotice("生活数据已导出");
  };

  const editDiary = async (item: api.LifeDiaryEntry) => {
    const title = window.prompt("日记标题", item.title);
    if (title === null) return;
    const body = window.prompt("日记正文", item.body);
    if (body === null || !title.trim() || !body.trim()) return;
    await api.updateLifeDiary(item.id, { expected_revision: item.revision, title: title.trim(), body: body.trim() });
    await refresh();
  };

  const editDate = async (item: api.LifeImportantDate) => {
    const label = window.prompt("日期名称", item.label);
    if (label === null || !label.trim()) return;
    await api.updateLifeDate(item.id, {
      expected_revision: item.revision, label: label.trim(), celebration_policy: item.celebration_policy,
    });
    await refresh();
  };

  const editGoal = async (item: api.LifeGoal) => {
    const title = window.prompt("目标名称", item.title);
    if (title === null || !title.trim()) return;
    await api.updateLifeGoal(item.id, { expected_revision: item.revision, title: title.trim() });
    await refresh();
  };

  const openDiagnostics = async () => {
    if (!diagnostics) setDiagnostics(await api.getLifeDiagnostics());
  };

  return (
    <section className="page life-page" aria-labelledby="life-title">
      <header className="page-header life-header">
        <div>
          <h2 id="life-title">陪伴与生活</h2>
          <p>看看她今天大致在做什么，也可以随时暂停、关闭或整理生活记录。</p>
        </div>
        <div className={`life-mode-badge ${settings?.mode || "loading"}`}>
          {settings?.mode === "continuous_simulated" ? "生活继续中" : settings?.mode === "paused" ? "已暂停" : settings?.mode === "disabled" ? "已关闭" : "读取中"}
        </div>
      </header>

      <nav className="life-tabs" aria-label="生活页面">
        {([['today', '今天'], ['diary', '日记'], ['dates', '重要日期'], ['goals', '个人目标'], ['settings', '设置']] as [Tab, string][]).map(([value, label]) => (
          <button key={value} className={tab === value ? "active" : ""} onClick={() => setTab(value)}>{label}</button>
        ))}
      </nav>

      {notice && <div className="life-notice" role="status">{notice}</div>}
      {loading && <div className="empty">正在读取她今天的生活……</div>}
      {failed && <div className="empty">暂时无法读取生活记录，聊天仍可正常使用。<button onClick={refresh}>重试</button></div>}

      {!loading && !failed && tab === "today" && (
        <div className="life-grid">
          <article className="life-card life-state-card">
            <h3>此刻</h3>
            {lifeState?.initialized ? (
              <><strong>{lifeState.current_activity || "按自己的节奏生活"}</strong><p>这是模拟生活状态，不代表现实世界中已经执行。</p></>
            ) : <p className="empty-inline">还没有生活状态，第一次启动后会自然建立。</p>}
          </article>
          <article className="life-card life-schedule-card">
            <h3>今天的安排</h3>
            {!schedule ? <p className="empty-inline">今天还没有生成生活安排。</p> : (
              <div className="life-timeline">
                {schedule.segments.map((segment) => (
                  <div className="life-segment" key={segment.id}>
                    <time>{timeLabel(segment.start_minute)}–{timeLabel(segment.end_minute)}</time>
                    <span>{segment.label}</span>
                    <small>{segment.detail_status === "detailed" ? "已细化" : "概览"}</small>
                  </div>
                ))}
              </div>
            )}
          </article>
        </div>
      )}

      {!loading && !failed && tab === "diary" && (
        <div className="life-list">
          {diary.length === 0 && <div className="empty">还没有日记。她会在有足够生活线索时留下记录。</div>}
          {diary.map((item) => (
            <article className="life-card" key={item.id}>
              <div className="life-card-title"><div><time>{item.entry_date}</time><h3>{item.title}</h3></div><span>{item.sensitivity === "sensitive" ? "私密" : "日记"}</span></div>
              <details className="life-private"><summary>展开私人正文</summary><p>{item.body}</p></details>
              <div className="life-actions"><button onClick={() => editDiary(item)}>编辑</button><button className="danger" onClick={async () => { if (window.confirm("删除这篇日记？")) { await api.deleteLifeDiary(item.id, item.revision); await refresh(); } }}>删除</button></div>
            </article>
          ))}
        </div>
      )}

      {!loading && !failed && tab === "dates" && (
        <div className="life-list">
          <form className="life-add-form" onSubmit={addDate}><input aria-label="日期名称" placeholder="添加重要日期" value={dateLabel} onChange={(e) => setDateLabel(e.target.value)} /><input aria-label="日期" type="date" value={dateValue} onChange={(e) => setDateValue(e.target.value)} /><button type="submit">添加</button></form>
          {dates.length === 0 && <div className="empty">还没有重要日期。只有确认过的日期才会参与提醒。</div>}
          {dates.map((item) => (
            <article className="life-card life-row" key={item.id}>
              <div><h3>{item.label}</h3><p>{item.date_year ? `${item.date_year}年` : "每年"}{item.date_month}月{item.date_day}日 · {item.celebration_policy === "none" ? "不主动提及" : item.celebration_policy === "day_only" ? "仅当天" : "自然准备"}</p></div>
              <div className="life-actions"><button onClick={() => editDate(item)}>编辑</button><button className="danger" onClick={async () => { if (window.confirm("删除这个日期？")) { await api.deleteLifeDate(item.id, item.revision); await refresh(); } }}>删除</button></div>
            </article>
          ))}
        </div>
      )}

      {!loading && !failed && tab === "goals" && (
        <div className="life-list">
          <form className="life-add-form" onSubmit={addGoal}><input aria-label="目标名称" placeholder="添加一个她可以持续推进的目标" value={goalTitle} onChange={(e) => setGoalTitle(e.target.value)} /><button type="submit">添加</button></form>
          {goals.length === 0 && <div className="empty">还没有个人目标。目标会保持少量，并且不会授予工具权限。</div>}
          {goals.map((item) => (
            <article className="life-card life-row" key={item.id}>
              <div><h3>{item.title}</h3><p>{item.status === "active" ? "进行中" : item.status === "paused" ? "已暂停" : item.status === "completed" ? "已完成" : "待确认"}</p></div>
              <div className="life-actions">
                <button onClick={() => editGoal(item)}>编辑</button>
                {item.status === "active" && <button onClick={async () => { await api.updateLifeGoal(item.id, { expected_revision: item.revision, status: "paused" }); await refresh(); }}>暂停</button>}
                {item.status === "paused" && <button onClick={async () => { await api.updateLifeGoal(item.id, { expected_revision: item.revision, status: "active" }); await refresh(); }}>继续</button>}
                <button className="danger" onClick={async () => { if (window.confirm("删除这个目标？")) { await api.deleteLifeGoal(item.id, item.revision); await refresh(); } }}>删除</button>
              </div>
            </article>
          ))}
        </div>
      )}

      {!loading && !failed && tab === "settings" && settings && (
        <div className="life-settings-stack">
          <article className="life-card">
            <h3>离线世界继续运转</h3><p>{modeCopy(settings.mode)}</p>
            <div className="life-mode-options" role="group" aria-label="离线生活模式">
              <button className={settings.mode === "continuous_simulated" ? "active" : ""} onClick={() => changeMode("continuous_simulated")}>开启</button>
              <button className={settings.mode === "paused" ? "active" : ""} onClick={() => changeMode("paused")}>暂停</button>
              <button className={settings.mode === "disabled" ? "active" : ""} onClick={() => changeMode("disabled")}>关闭</button>
            </div>
          </article>
          <article className="life-card"><h3>数据管理</h3><p>可以重建本地生活索引，或导出包括私人日记在内的完整本地副本。</p><div className="life-actions"><button onClick={async () => { const result = await api.rebuildLifeViews(); setNotice(`已重建 ${result.timeline_entries} 条生活索引`); await refresh(); }}>重建</button><button onClick={downloadExport}>导出</button></div></article>
          <details className="life-card life-diagnostics" onToggle={(event) => { if (event.currentTarget.open) void openDiagnostics(); }}>
            <summary>开发者诊断</summary>
            {!diagnostics ? <p>正在读取无正文诊断……</p> : <div><p>Schema {diagnostics.schema_version} · 状态修订 {diagnostics.state_revision ?? "未建立"}</p><p>算法 {diagnostics.state_algorithm} · 错误码 {diagnostics.anomaly_code || "无"}</p><ul>{diagnostics.sources.map((source) => <li key={`${source.source_type}:${source.source_id}:${source.source_revision}`}>{source.source_type} · {source.source_id} · rev {source.source_revision} · {source.source_status}</li>)}</ul></div>}
          </details>
        </div>
      )}
    </section>
  );
}
