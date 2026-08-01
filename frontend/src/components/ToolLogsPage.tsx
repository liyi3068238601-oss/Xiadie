import { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "./../api";
import { toast } from "./../store";

type CategoryFilter = "all" | api.RuntimeLogCategory;
type StatusFilter = "all" | api.RuntimeLogStatus;
type DetailState =
  | { status: "loading" }
  | { status: "loaded"; value: api.RuntimeLogTurnDetail }
  | { status: "error"; message: string };

const CATEGORIES: { value: CategoryFilter; label: string; hint: string }[] = [
  { value: "all", label: "全部", hint: "所有运行事件" },
  { value: "model", label: "模型", hint: "对话与后台模型任务" },
  { value: "reasoning", label: "决策", hint: "可审计的动作与理由摘要" },
  { value: "retrieval", label: "检索", hint: "知识召回与证据选择" },
  { value: "context", label: "上下文", hint: "预算、注入与裁剪" },
  { value: "tool", label: "工具", hint: "工具调用及风险级别" },
  { value: "system", label: "系统", hint: "后台状态变化" },
];

const STATUS_LABELS: Record<api.RuntimeLogStatus, string> = {
  success: "成功",
  warning: "注意",
  error: "异常",
  pending: "进行中",
};

const DETAIL_LABELS: Record<string, string> = {
  model: "模型",
  provider_id: "供应商",
  logical_role: "逻辑角色",
  protocol_version: "协议",
  confidence: "置信度",
  latency_ms: "耗时",
  input_tokens: "输入 Token",
  output_tokens: "输出 Token",
  prompt_tokens: "提示 Token",
  completion_tokens: "生成 Token",
  candidate_count: "候选数",
  eligible_count: "合格候选",
  injected_count: "注入数",
  retrieval_mode: "检索方式",
  vector_available: "向量可用",
  context_window_tokens: "上下文窗口",
  output_reserve_tokens: "输出预留",
  trimmed_messages: "裁剪消息",
  trimmed_rounds: "裁剪轮次",
  input_count: "本轮输入数",
  risk_level: "风险等级",
  error_code: "错误码",
  fallback_used: "使用降级",
  reason_codes: "理由码",
  warning_codes: "警告码",
  component_tokens: "组件预算",
  source_type_counts: "来源计数",
};

function fmtTime(sec: number): string {
  return new Date(sec * 1000).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function fmtDetail(key: string, value: unknown): string {
  if (key === "latency_ms" && typeof value === "number") return `${value} ms`;
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

export function ToolLogsPage() {
  const [feed, setFeed] = useState<api.RuntimeLogFeed | null>(null);
  const [category, setCategory] = useState<CategoryFilter>("all");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, DetailState>>({});
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((quiet = false) => {
    if (!quiet) setLoading(true);
    api.listRuntimeLogs({
      category: category === "all" ? undefined : category,
      status: status === "all" ? undefined : status,
      limit: 300,
    }).then((next) => {
      setFeed(next);
      setError(null);
    }).catch((reason) => {
      setError(reason?.message || "加载失败");
      if (!quiet) toast("加载运行日志失败");
    }).finally(() => {
      if (!quiet) setLoading(false);
    });
  }, [category, status]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => load(true), 5000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load]);

  const items = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    if (!needle) return feed?.items ?? [];
    return (feed?.items ?? []).filter((item) =>
      `${item.title} ${item.summary} ${item.status} ${JSON.stringify(item.details)}`
        .toLocaleLowerCase().includes(needle),
    );
  }, [feed, search]);

  const toggleExpanded = (item: api.RuntimeLogItem) => {
    if (expanded === item.id) {
      setExpanded(null);
      return;
    }
    setExpanded(item.id);
    const currentDetail = details[item.id];
    if (!item.detail_available || currentDetail?.status === "loading" || currentDetail?.status === "loaded") return;
    setDetails((current) => ({ ...current, [item.id]: { status: "loading" } }));
    api.getRuntimeLogDetail(item.id).then((value) => {
      setDetails((current) => ({ ...current, [item.id]: { status: "loaded", value } }));
    }).catch((reason) => {
      const message = reason instanceof api.ApiError && reason.status === 404
        ? "原始对话已删除或不可用"
        : reason?.message || "本轮详情加载失败";
      setDetails((current) => ({ ...current, [item.id]: { status: "error", message } }));
    });
  };

  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast("已复制");
    } catch {
      toast("复制失败");
    }
  };

  return (
    <div className="page runtime-logs-page">
      <header className="page-header compact-page-header runtime-log-header">
        <div>
          <p className="page-eyebrow">RUNTIME OBSERVABILITY</p>
          <h1>运行日志</h1>
          <p>模型、决策、检索、上下文与工具调用的本地只读审计视图。</p>
        </div>
        <div className="runtime-log-actions">
          <label className="runtime-auto-refresh">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(event) => setAutoRefresh(event.target.checked)}
            />
            <span>自动刷新</span>
          </label>
          <button className="btn ghost" onClick={() => load()} disabled={loading}>刷新</button>
        </div>
      </header>

      <div className="runtime-privacy-note">
        <span className="runtime-privacy-icon">i</span>
        <span>{feed?.privacy_notice || "本页会展示本地对话输入与最终回复；不展示系统提示词、隐藏思维链、密钥、知识正文或记忆正文。"}</span>
      </div>
      <div className="runtime-representation-note">
        聊天详情是持久化输入与最终回复，不是逐 chunk 回放；不能单独证明首 Token、展示节奏或取消瞬间行为。
      </div>
      {error && feed ? <div className="runtime-refresh-warning">刷新失败：{error}，已保留上一次结果。</div> : null}

      <section className="runtime-summary-grid">
        {CATEGORIES.slice(1).map((item) => (
          <button
            key={item.value}
            className={`runtime-summary-card ${category === item.value ? "active" : ""}`}
            onClick={() => setCategory(category === item.value ? "all" : item.value)}
            title={item.hint}
          >
            <span className={`runtime-category-dot ${item.value}`} />
            <strong>{feed?.counts[item.value as api.RuntimeLogCategory] ?? 0}</strong>
            <small>{item.label}</small>
          </button>
        ))}
      </section>

      <div className="runtime-log-toolbar">
        <div className="runtime-category-tabs">
          {CATEGORIES.map((item) => (
            <button
              key={item.value}
              className={category === item.value ? "active" : ""}
              onClick={() => setCategory(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <select value={status} onChange={(event) => setStatus(event.target.value as StatusFilter)}>
          <option value="all">全部状态</option>
          <option value="success">成功</option>
          <option value="warning">注意</option>
          <option value="error">异常</option>
          <option value="pending">进行中</option>
        </select>
        <input
          className="runtime-log-search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="搜索模型、错误码或理由…"
        />
      </div>

      {loading && !feed ? (
        <div className="empty">正在汇总运行日志…</div>
      ) : error && !feed ? (
        <div className="empty">加载失败：{error}</div>
      ) : items.length === 0 ? (
        <div className="empty">当前筛选条件下没有日志</div>
      ) : (
        <div className="runtime-log-list">
          {items.map((item) => {
            const isOpen = expanded === item.id;
            const detail = details[item.id];
            return (
              <article className={`runtime-log-row ${isOpen ? "expanded" : ""}`} key={item.id}>
                <button className="runtime-log-main" onClick={() => toggleExpanded(item)}>
                  <span className={`runtime-category-dot ${item.category}`} />
                  <span className="runtime-log-copy">
                    <span className="runtime-log-title-line">
                      <strong>{item.title}</strong>
                      {item.source === "chat" && item.details.model ? (
                        <span className="runtime-model-chip">{String(item.details.model)}</span>
                      ) : null}
                      {item.source === "chat" && typeof item.details.input_count === "number" ? (
                        <span className="runtime-input-count-chip">{item.details.input_count} 条输入</span>
                      ) : null}
                      {item.category === "tool" && item.details.risk_level ? (
                        <span className="runtime-risk-chip">{String(item.details.risk_level)}</span>
                      ) : null}
                    </span>
                    <small>{item.summary || "没有附加摘要"}</small>
                  </span>
                  <span className={`runtime-status ${item.status_group}`}>
                    {STATUS_LABELS[item.status_group]}
                  </span>
                  <time>{fmtTime(item.created_at)}</time>
                  <span className="runtime-expand-mark">{isOpen ? "−" : "+"}</span>
                </button>
                {isOpen ? (
                  <div className="runtime-log-details">
                    {item.detail_available ? (
                      <RuntimeTurnDetail state={detail} onCopy={copyText} />
                    ) : (
                      <RuntimeMetadata item={item} />
                    )}
                    <div className="runtime-log-footnote">
                      来源：{item.source} · 原始状态：{item.status} · 事件：{item.id}
                    </div>
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}

function RuntimeMetadata({ item }: { item: api.RuntimeLogItem }) {
  return (
    <div className="runtime-detail-grid">
      {Object.entries(item.details).map(([key, value]) => (
        <div className="runtime-detail" key={key}>
          <span>{DETAIL_LABELS[key] || key}</span>
          <code>{fmtDetail(key, value)}</code>
        </div>
      ))}
    </div>
  );
}

function RuntimeTurnDetail({ state, onCopy }: {
  state: DetailState | undefined;
  onCopy: (text: string) => void;
}) {
  if (!state || state.status === "loading") {
    return <div className="runtime-detail-state">正在加载本轮详情…</div>;
  }
  if (state.status === "error") {
    return <div className="runtime-detail-state error">{state.message}</div>;
  }
  const detail = state.value;
  return (
    <div className="runtime-turn-detail">
      <section>
        <h2>本轮输入 <span>{detail.inputs.length} 条</span></h2>
        {detail.inputs.length === 0 ? (
          <div className="runtime-detail-state">本轮没有前置用户输入，可能是主动陪伴消息。</div>
        ) : (
          <div className="runtime-turn-inputs">
            {detail.inputs.map((input, index) => (
              <article className="runtime-turn-body" key={input.message_id}>
                <header>
                  <strong>输入 {index + 1}</strong>
                  <time>{fmtTime(input.created_at)}</time>
                  <button onClick={() => onCopy(input.content)}>复制</button>
                </header>
                <pre>{input.content}</pre>
                <small>消息：{input.message_id}</small>
              </article>
            ))}
          </div>
        )}
      </section>
      <section>
        <h2>最终回复 <span>{detail.assistant.model}</span></h2>
        <article className="runtime-turn-body assistant">
          <header>
            <strong>持久化最终正文</strong>
            <time>{fmtTime(detail.assistant.created_at)}</time>
            <button onClick={() => onCopy(detail.assistant.content)}>复制</button>
          </header>
          <pre>{detail.assistant.content}</pre>
          <small>消息：{detail.assistant.message_id} · 表示：{detail.representation}</small>
        </article>
      </section>
    </div>
  );
}
