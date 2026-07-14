import { useEffect, useState } from "react";
import * as api from "./../api";
import { toast } from "./../store";

// 需求 6.9 分组：模型 API / 外观 / Live2D / 记忆 / 权限 / 数据。
type TabKey = "model" | "appearance" | "live2d" | "memory" | "perms" | "data";

const TABS: { key: TabKey; label: string }[] = [
  { key: "model", label: "模型 API" },
  { key: "appearance", label: "外观" },
  { key: "live2d", label: "Live2D" },
  { key: "memory", label: "记忆" },
  { key: "perms", label: "权限" },
  { key: "data", label: "数据" },
];

// 能力标签说明（stream/tools/vision/reasoning/local）。
const CAP_DESC: { key: string; label: string }[] = [
  { key: "stream", label: "流式输出，逐字返回" },
  { key: "tools", label: "工具调用，可执行动作" },
  { key: "vision", label: "图像理解，可读图片" },
  { key: "reasoning", label: "深度推理，链式思考" },
  { key: "local", label: "本地模型，离线可用" },
];

// 模型列表文本框样式（styles.css 未提供 textarea 类，用内联对齐 .field input 观感）
const textareaStyle: React.CSSProperties = {
  width: "100%",
  padding: "9px 12px",
  borderRadius: 10,
  background: "var(--glass-strong)",
  border: "1px solid var(--glass-border)",
  color: "var(--text)",
  outline: "none",
  resize: "vertical",
  minHeight: 56,
  fontSize: 13,
  lineHeight: 1.5,
};

interface EditForm {
  base_url: string;
  api_key: string;
  models: string;
  enabled: boolean;
}

export function SettingsPage({ onModelChanged }: { onModelChanged: () => void }) {
  const [tab, setTab] = useState<TabKey>("model");

  // ---- 模型 API 状态 ----
  const [providers, setProviders] = useState<api.Provider[]>([]);
  const [current, setCurrent] = useState<api.CurrentModel | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selPid, setSelPid] = useState("");
  const [selModel, setSelModel] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, EditForm>>({});
  const [tests, setTests] = useState<Record<string, { ok: boolean; message: string }>>({});
  const [testing, setTesting] = useState<string | null>(null);

  const loadProviders = () => {
    setLoading(true);
    Promise.all([api.listProviders(), api.getCurrentModel().catch(() => null)])
      .then(([ps, cm]) => {
        setProviders(ps);
        setCurrent(cm);
        setError("");
        const pid = cm?.provider_id || ps[0]?.id || "";
        setSelPid(pid);
        const prov = ps.find((p) => p.id === pid);
        setSelModel(cm?.model || prov?.models[0] || "");
      })
      .catch((e) => setError(e.message || "加载失败"))
      .finally(() => setLoading(false));
  };

  useEffect(loadProviders, []);

  const selProvider = providers.find((p) => p.id === selPid);

  const onSelectProvider = (pid: string) => {
    setSelPid(pid);
    const prov = providers.find((p) => p.id === pid);
    setSelModel(prov?.models[0] || "");
  };

  const applyCurrentModel = () => {
    if (!selPid || !selModel) {
      toast("请先选择供应商与模型");
      return;
    }
    api
      .setCurrentModel(selPid, selModel)
      .then((cm) => {
        setCurrent(cm);
        onModelChanged();
        toast(`已切换到 ${cm.provider_name} · ${cm.model}`);
      })
      .catch((e) => toast(e.message || "切换失败"));
  };

  const toggleConfig = (p: api.Provider) => {
    if (expanded === p.id) {
      setExpanded(null);
      return;
    }
    setExpanded(p.id);
    setEdits((prev) =>
      prev[p.id]
        ? prev
        : {
            ...prev,
            [p.id]: {
              base_url: p.base_url,
              api_key: "",
              models: p.models.join(", "),
              enabled: p.enabled,
            },
          }
    );
  };

  const patchEdit = (id: string, patch: Partial<EditForm>) =>
    setEdits((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));

  const saveProvider = (p: api.Provider) => {
    const f = edits[p.id];
    if (!f) return;
    const models = f.models
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const body: any = {
      base_url: f.base_url.trim(),
      models,
      enabled: f.enabled,
    };
    // 密钥安全：输入框为空时不提交 api_key（后端不回传已保存的 key）。
    if (f.api_key.trim()) body.api_key = f.api_key.trim();
    api
      .updateProvider(p.id, body)
      .then(() => {
        toast(`已保存「${p.name}」`);
        // 清空本地 key 输入，避免残留；写操作后刷新列表。
        setEdits((prev) => ({ ...prev, [p.id]: { ...prev[p.id], api_key: "" } }));
        loadProviders();
      })
      .catch((e) => toast(e.message || "保存失败"));
  };

  const runTest = (p: api.Provider) => {
    const model = (p.id === selPid && selModel) || p.models[0];
    if (!model) {
      toast("该供应商暂无可用模型，请先配置");
      return;
    }
    setTesting(p.id);
    setTests((prev) => {
      const n = { ...prev };
      delete n[p.id];
      return n;
    });
    api
      .testProvider(p.id, model)
      .then((r) => setTests((prev) => ({ ...prev, [p.id]: r })))
      .catch((e) => setTests((prev) => ({ ...prev, [p.id]: { ok: false, message: e.message || "测试失败" } })))
      .finally(() => setTesting(null));
  };

  // ---- 记忆开关（真实读写 settings key: memory_enabled）----
  const [memEnabled, setMemEnabled] = useState<boolean | null>(null);
  const [memErr, setMemErr] = useState("");

  const loadMemory = () => {
    fetch(api.API_BASE + "/api/settings/memory_enabled")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("读取失败"))))
      .then((d) => {
        const v = d && typeof d === "object" ? (d.value ?? d) : d;
        setMemEnabled(String(v) === "1");
        setMemErr("");
      })
      .catch((e) => {
        setMemEnabled(null);
        setMemErr(e.message || "读取失败");
      });
  };

  useEffect(loadMemory, []);

  const setMemory = (on: boolean) => {
    fetch(api.API_BASE + "/api/settings/memory_enabled", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: on ? "1" : "0" }),
    })
      .then((r) => (r.ok ? r : Promise.reject(new Error("保存失败"))))
      .then(() => {
        toast(on ? "已开启长期记忆" : "已关闭长期记忆");
        loadMemory();
      })
      .catch((e) => toast(e.message || "保存失败"));
  };

  return (
    <div className="page">
      <h1>设置</h1>
      <div className="sub">配置模型接口、外观、记忆与权限，让遐蝶更懂你。</div>

      {/* 顶部小标签切换 */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 20 }}>
        {TABS.map((t) => (
          <button
            key={t.key}
            className="btn ghost"
            onClick={() => setTab(t.key)}
            style={
              tab === t.key
                ? { background: "rgba(124, 92, 255, 0.22)", color: "var(--text)", borderColor: "var(--glass-border-lit)" }
                : undefined
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ============ 模型 API ============ */}
      {tab === "model" && (
        <div>
          {loading && <div className="empty">正在加载供应商…</div>}

          {!loading && error && (
            <div className="empty">
              加载失败：{error}
              <div style={{ marginTop: 12 }}>
                <button className="btn ghost" onClick={loadProviders}>
                  重试
                </button>
              </div>
            </div>
          )}

          {!loading && !error && (
            <>
              {/* 当前模型 */}
              <div className="section-label">当前模型</div>
              <div className="card" style={{ marginBottom: 18 }}>
                <div className="row" style={{ flexWrap: "wrap" }}>
                  <select
                    style={{
                      flex: 1,
                      minWidth: 140,
                      padding: "9px 12px",
                      borderRadius: 10,
                      background: "var(--glass-strong)",
                      border: "1px solid var(--glass-border)",
                      color: "var(--text)",
                      outline: "none",
                    }}
                    value={selPid}
                    onChange={(e) => onSelectProvider(e.target.value)}
                  >
                    {providers.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                  <select
                    style={{
                      flex: 1,
                      minWidth: 140,
                      padding: "9px 12px",
                      borderRadius: 10,
                      background: "var(--glass-strong)",
                      border: "1px solid var(--glass-border)",
                      color: "var(--text)",
                      outline: "none",
                    }}
                    value={selModel}
                    onChange={(e) => setSelModel(e.target.value)}
                  >
                    {(selProvider?.models || []).length === 0 && <option value="">该供应商暂无模型</option>}
                    {(selProvider?.models || []).map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                  <button className="btn" onClick={applyCurrentModel}>
                    设为当前
                  </button>
                </div>
                {current && (
                  <div className="card-hint" style={{ marginTop: 10 }}>
                    正在使用：{current.provider_name} · {current.model}
                    {current.capabilities.length > 0 && (
                      <span className="cap-tags" style={{ display: "inline-flex", marginLeft: 8, verticalAlign: "middle" }}>
                        {current.capabilities.map((c) => (
                          <span key={c} className="cap-tag">
                            {c}
                          </span>
                        ))}
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* 供应商列表 */}
              <div className="section-label">供应商</div>
              {providers.length === 0 && <div className="empty">还没有配置任何供应商。</div>}
              {providers.map((p) => {
                const isOpen = expanded === p.id;
                const f = edits[p.id];
                const t = tests[p.id];
                return (
                  <div key={p.id}>
                    <div className="provider-row">
                      <span className="pname">{p.name}</span>
                      {p.has_key ? <span className="tag-ok">已配置密钥</span> : <span className="tag-off">未配置密钥</span>}
                      <span className="chip">{p.enabled ? "已启用" : "已停用"}</span>
                      {testing === p.id && <span className="tag-off">测试中…</span>}
                      {t &&
                        (t.ok ? (
                          <span className="tag-ok">连接正常</span>
                        ) : (
                          <span style={{ color: "var(--danger)", fontSize: 12 }}>{t.message || "连接失败"}</span>
                        ))}
                      <button className="btn ghost" onClick={() => runTest(p)} disabled={testing === p.id}>
                        连接测试
                      </button>
                      <button className="btn ghost" onClick={() => toggleConfig(p)}>
                        {isOpen ? "收起" : "配置"}
                      </button>
                    </div>

                    {isOpen && f && (
                      <div className="card" style={{ margin: "0 0 12px" }}>
                        <div className="field">
                          <label>Base URL</label>
                          <input
                            value={f.base_url}
                            onChange={(e) => patchEdit(p.id, { base_url: e.target.value })}
                            placeholder="https://api.example.com/v1"
                          />
                        </div>
                        <div className="field">
                          <label>API Key</label>
                          <input
                            type="password"
                            value={f.api_key}
                            onChange={(e) => patchEdit(p.id, { api_key: e.target.value })}
                            placeholder={p.has_key ? "已保存密钥（留空则不修改）" : "未配置"}
                          />
                        </div>
                        <div className="field">
                          <label>模型列表（英文逗号分隔）</label>
                          <textarea
                            style={textareaStyle}
                            value={f.models}
                            onChange={(e) => patchEdit(p.id, { models: e.target.value })}
                            placeholder="gpt-4o, gpt-4o-mini"
                          />
                        </div>
                        <div className="field">
                          <label style={{ display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                            <input
                              type="checkbox"
                              style={{ width: "auto" }}
                              checked={f.enabled}
                              onChange={(e) => patchEdit(p.id, { enabled: e.target.checked })}
                            />
                            启用该供应商
                          </label>
                        </div>
                        <div className="row">
                          <button className="btn" onClick={() => saveProvider(p)}>
                            保存
                          </button>
                          <button className="btn ghost" onClick={() => setExpanded(null)}>
                            取消
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}

              {/* 能力标签说明 */}
              <div className="section-label" style={{ marginTop: 18 }}>
                能力标签说明
              </div>
              <div className="card">
                {CAP_DESC.map((c) => (
                  <div key={c.key} className="row" style={{ marginBottom: 6 }}>
                    <span className="cap-tag" style={{ minWidth: 64, textAlign: "center" }}>
                      {c.key}
                    </span>
                    <span className="card-hint">{c.label}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* ============ 外观 ============ */}
      {tab === "appearance" && (
        <PlaceholderSection
          title="外观"
          items={[
            "主题配色（深紫 / 幽蓝切换）",
            "玻璃拟态透明度与背景模糊强度",
            "界面字体大小与气泡样式",
            "浅色 / 深色模式跟随系统",
          ]}
        />
      )}

      {/* ============ Live2D ============ */}
      {tab === "live2d" && (
        <PlaceholderSection
          title="Live2D"
          items={[
            "模型选择与切换",
            "缩放、位置与置顶 / 鼠标穿透",
            "待机动作与点击互动区",
            "口型同步与情绪表情联动",
          ]}
        />
      )}

      {/* ============ 记忆 ============ */}
      {tab === "memory" && (
        <div>
          <div className="section-label">长期记忆</div>
          <div className="list-row">
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600 }}>启用长期记忆</div>
              <div className="card-hint">
                {memErr
                  ? `读取失败：${memErr}`
                  : "开启后，遐蝶会在对话中沉淀并回忆你的偏好与重要信息。"}
              </div>
            </div>
            {memEnabled === null ? (
              <span className="tag-off">{memErr ? "不可用" : "读取中…"}</span>
            ) : (
              <button
                className="btn ghost"
                onClick={() => setMemory(!memEnabled)}
                style={
                  memEnabled
                    ? { color: "var(--ok)", borderColor: "var(--glass-border-lit)" }
                    : undefined
                }
              >
                {memEnabled ? "已开启" : "已关闭"}
              </button>
            )}
          </div>
          <PlaceholderSection
            title="记忆管理（其它）"
            items={[
              "分层记忆 L0 / L1 / L2 的保留策略",
              "自动记忆的置信度阈值",
              "记忆去重与遗忘周期",
            ]}
          />
        </div>
      )}

      {/* ============ 权限 ============ */}
      {tab === "perms" && (
        <div>
          <div className="section-label">工具权限</div>
          <div className="card error" style={{ marginBottom: 12 }}>
            <div className="card-title">高风险工具默认需确认</div>
            <div className="card-hint">
              文件写入、命令执行、网络请求等高风险操作默认逐次弹窗确认，
              <b>不提供「一键全开」</b>，以保护你的设备与数据安全。
            </div>
          </div>
          <PlaceholderSection
            title="权限细则"
            items={[
              "只读工具（文件读取 / 检索）默认放行",
              "高风险工具（写入 / 执行 / 联网）逐次确认",
              "按工具粒度记住本次会话的选择",
              "所有工具调用记录可在「工具记录」查看",
            ]}
          />
        </div>
      )}

      {/* ============ 数据 ============ */}
      {tab === "data" && (
        <div>
          <div className="section-label">数据管理</div>
          <div className="card" style={{ marginBottom: 12 }}>
            <div className="card-title">
              导出与清理 <span className="tag-off">开发中</span>
            </div>
            <div className="card-hint" style={{ marginBottom: 10 }}>
              导出全部会话、记忆与任务，或清理本地缓存数据。
            </div>
            <div className="row">
              <button className="btn ghost" onClick={() => toast("导出功能开发中")}>
                导出全部数据
              </button>
              <button className="btn ghost" onClick={() => toast("清理功能开发中")}>
                清理缓存 / 记忆
              </button>
            </div>
          </div>
          <PlaceholderSection
            title="数据细则"
            items={["导出为 JSON / Markdown", "选择性清理会话或记忆", "本地数据库备份与恢复"]}
          />
        </div>
      )}
    </div>
  );
}

// 尚无对应后端接口的分组，做成清晰的「开发中」占位区块。
function PlaceholderSection({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="card">
      <div className="card-title">
        {title} <span className="tag-off">开发中</span>
      </div>
      <div className="card-hint">该分组将包含：</div>
      <ul style={{ margin: "6px 0 0", paddingLeft: 18, color: "var(--text-dim)", fontSize: 12, lineHeight: 1.8 }}>
        {items.map((i) => (
          <li key={i}>{i}</li>
        ))}
      </ul>
    </div>
  );
}
