import { useEffect, useState } from "react";
import * as api from "./../api";
import { toast } from "./../store";

// 权限等级说明（需求 8.1）：模型可建议工具，但高风险工具必须经确认才能执行。
const RISK_LEVELS: {
  level: string;
  name: string;
  policy: string;
  color: string;
}[] = [
  { level: "S0", name: "安全展示", policy: "允许", color: "var(--ok)" },
  { level: "S1", name: "本地低风险写入", policy: "允许，可撤销", color: "var(--cyan)" },
  { level: "S2", name: "用户数据操作", policy: "需确认", color: "var(--violet-soft)" },
  { level: "S3", name: "外部影响", policy: "必须确认", color: "var(--warn)" },
  { level: "S4", name: "高危系统", policy: "默认禁用·长期后置", color: "var(--danger)" },
];

function riskColor(level: string): string {
  return RISK_LEVELS.find((r) => r.level === level.toUpperCase())?.color ?? "var(--text-dim)";
}

// 状态着色：成功类偏绿，拒绝/失败偏红，等待/确认偏黄，其余取暗色。
function statusColor(status: string): string {
  const s = status.toLowerCase();
  if (/(拒绝|禁用|失败|error|denied|blocked|fail)/.test(s)) return "var(--danger)";
  if (/(待|确认|pending|await|confirm)/.test(s)) return "var(--warn)";
  if (/(完成|成功|允许|ok|done|success|allow|executed)/.test(s)) return "var(--ok)";
  return "var(--text-dim)";
}

function fmtTime(sec: number): string {
  try {
    return new Date(sec * 1000).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

export function ToolLogsPage() {
  const [logs, setLogs] = useState<api.ToolLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    api
      .listToolLogs()
      .then((rows) => setLogs(rows))
      .catch((e) => {
        setError(e?.message || "加载失败");
        toast("加载工具记录失败");
      })
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  return (
    <div className="page">
      <h1>工具记录</h1>
      <div className="sub">只读审计视图 · 每一次影响文件、外部消息或系统的动作都有据可查</div>

      {/* 权限等级说明（需求 8.1） */}
      <div className="card tool" style={{ marginBottom: 18 }}>
        <div className="card-title">权限等级与默认策略</div>
        <div className="card-hint" style={{ marginBottom: 12 }}>
          遐蝶可以<strong>建议</strong>工具，但不会悄悄执行高风险工具。所有影响文件 /
          外部消息 / 系统的动作，都会先弹出确认卡，并留下审计记录。
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {RISK_LEVELS.map((r) => (
            <div className="row" key={r.level} style={{ alignItems: "center" }}>
              <span
                className="chip"
                style={{
                  color: r.color,
                  borderColor: r.color,
                  minWidth: 30,
                  textAlign: "center",
                }}
              >
                {r.level}
              </span>
              <span style={{ flex: 1 }}>{r.name}</span>
              <span style={{ color: r.color, fontSize: 12 }}>{r.policy}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="section-label" style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ flex: 1 }}>调用记录</span>
        <button className="btn ghost" style={{ padding: "4px 12px" }} onClick={load}>
          刷新
        </button>
      </div>

      {loading ? (
        <div className="empty">正在加载工具记录…</div>
      ) : error ? (
        <div className="empty">加载失败：{error}</div>
      ) : logs.length === 0 ? (
        <div className="empty">还没有工具调用记录</div>
      ) : (
        logs.map((log) => (
          <div className="list-row" key={log.id}>
            <span
              className="chip"
              style={{
                color: riskColor(log.risk_level),
                borderColor: riskColor(log.risk_level),
                minWidth: 30,
                textAlign: "center",
              }}
              title={
                RISK_LEVELS.find((r) => r.level === log.risk_level.toUpperCase())?.name || ""
              }
            >
              {log.risk_level}
            </span>
            <span style={{ fontWeight: 600, minWidth: 96 }}>{log.tool}</span>
            <span style={{ color: statusColor(log.status), fontSize: 12, minWidth: 56 }}>
              {log.status}
            </span>
            <span style={{ flex: 1, color: "var(--text-dim)" }}>{log.summary}</span>
            <span style={{ color: "var(--text-faint)", fontSize: 12, whiteSpace: "nowrap" }}>
              {fmtTime(log.created_at)}
            </span>
          </div>
        ))
      )}
    </div>
  );
}
