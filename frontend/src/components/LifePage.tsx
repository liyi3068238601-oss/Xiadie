import { useEffect, useState } from "react";
import * as api from "../api";

function timeLabel(minutes: number) {
  const hour = Math.floor(minutes / 60).toString().padStart(2, "0");
  const minute = (minutes % 60).toString().padStart(2, "0");
  return `${hour}:${minute}`;
}

export function LifePage() {
  const [schedule, setSchedule] = useState<api.LifeSchedule | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const today = new Date().toLocaleDateString("en-CA");

  useEffect(() => {
    api.getLifeSchedule(today)
      .then((result) => setSchedule(result.item))
      .catch(() => setFailed(true))
      .finally(() => setLoading(false));
  }, [today]);

  return (
    <section className="page life-page" aria-labelledby="life-title">
      <header className="page-header">
        <div>
          <h2 id="life-title">今日生活</h2>
          <p>这里展示的是计划概览，不代表这些事情已经发生。</p>
        </div>
      </header>
      {loading && <div className="empty">正在读取今天的安排…</div>}
      {failed && <div className="empty">暂时无法读取生活概览，聊天仍可正常使用。</div>}
      {!loading && !failed && !schedule && <div className="empty">今天还没有生成生活安排。</div>}
      {schedule && (
        <div className="life-timeline">
          {schedule.segments.map((segment) => (
            <article className="life-segment" key={segment.id}>
              <time>{timeLabel(segment.start_minute)}–{timeLabel(segment.end_minute)}</time>
              <span>{segment.label}</span>
              <small>{segment.detail_status === "detailed" ? "已细化" : "概览"}</small>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
