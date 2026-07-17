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
const CAP_DESC: { key: string; label: string; tone: "ok" | "cyan" | "violet" | "warn" }[] = [
  { key: "stream", label: "支持流式输出，逐字返回生成内容", tone: "ok" },
  { key: "tools", label: "支持函数调用与外部工具集成", tone: "cyan" },
  { key: "vision", label: "支持图像理解与多模态输入", tone: "violet" },
  { key: "reasoning", label: "支持深度推理与思维链过程", tone: "warn" },
  { key: "local", label: "本地模型，离线可用", tone: "violet" },
];

// 权限风险等级配置。
type RiskLevel = {
  code: string;
  label: string;
  tone: "ok" | "cyan" | "warn" | "orange" | "danger";
  policy: string;
  tools: { name: string; state: "on" | "ask" | "off" }[];
};

const RISK_LEVELS: RiskLevel[] = [
  {
    code: "S0",
    label: "无风险",
    tone: "ok",
    policy: "默认放行",
    tools: [
      { name: "对话回复", state: "on" },
      { name: "记忆读取", state: "on" },
      { name: "实体查询", state: "on" },
      { name: "情绪查询", state: "on" },
    ],
  },
  {
    code: "S1",
    label: "低风险",
    tone: "cyan",
    policy: "默认放行",
    tools: [
      { name: "文件读取", state: "on" },
      { name: "目录浏览", state: "on" },
      { name: "搜索检索", state: "on" },
      { name: "网页访问", state: "on" },
    ],
  },
  {
    code: "S2",
    label: "中风险",
    tone: "warn",
    policy: "默认确认",
    tools: [
      { name: "文件写入", state: "ask" },
      { name: "数据导出", state: "ask" },
      { name: "日志分析", state: "ask" },
    ],
  },
  {
    code: "S3",
    label: "高风险",
    tone: "orange",
    policy: "默认确认",
    tools: [
      { name: "命令执行", state: "ask" },
      { name: "网络请求", state: "ask" },
      { name: "进程管理", state: "ask" },
    ],
  },
  {
    code: "S4",
    label: "极高风险",
    tone: "danger",
    policy: "默认禁止",
    tools: [
      { name: "系统修改", state: "off" },
      { name: "环境变量", state: "off" },
      { name: "权限变更", state: "off" },
    ],
  },
];

interface EditForm {
  base_url: string;
  api_key: string;
  models: string;
  enabled: boolean;
  execution_location: api.Provider["execution_location"];
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
  const [drawerPid, setDrawerPid] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, EditForm>>({});
  const [tests, setTests] = useState<Record<string, { ok: boolean; message: string }>>({});
  const [testing, setTesting] = useState<string | null>(null);
  const [discovering, setDiscovering] = useState<string | null>(null);
  const [discoveries, setDiscoveries] = useState<Record<string, { ok: boolean; message: string }>>({});
  const [modelDrafts, setModelDrafts] = useState<Record<string, string>>({});
  const [observerMode, setObserverMode] = useState<"current" | "dedicated">("current");
  const [observerPid, setObserverPid] = useState("");
  const [observerModel, setObserverModel] = useState("");
  const [memoryObserverMode, setMemoryObserverMode] = useState<"current" | "dedicated">("current");
  const [memoryObserverPid, setMemoryObserverPid] = useState("");
  const [memoryObserverModel, setMemoryObserverModel] = useState("");

  const loadProviders = () => {
    setLoading(true);
    Promise.all([
      api.listProviders(),
      api.getCurrentModel().catch(() => null),
      api.getObserverModel().catch(() => null),
      api.getMemoryObserverModel().catch(() => null),
    ])
      .then(([ps, cm, observer, memoryObserver]) => {
        setProviders(ps);
        setCurrent(cm);
        setError("");
        const pid = cm?.provider_id || ps[0]?.id || "";
        setSelPid(pid);
        const prov = ps.find((p) => p.id === pid);
        setSelModel(cm?.model || prov?.models[0] || "");
        const mode = observer?.mode || "current";
        const observerProviderId = observer?.provider_id || pid;
        const observerProvider = ps.find((p) => p.id === observerProviderId);
        setObserverMode(mode);
        setObserverPid(observerProviderId);
        setObserverModel(observer?.model || observerProvider?.models[0] || "");
        const memoryMode = memoryObserver?.mode || "current";
        const memoryPid = memoryObserver?.provider_id || pid;
        const memoryProvider = ps.find((p) => p.id === memoryPid);
        setMemoryObserverMode(memoryMode);
        setMemoryObserverPid(memoryPid);
        setMemoryObserverModel(memoryObserver?.model || memoryProvider?.models[0] || "");
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

  const onSelectObserverProvider = (pid: string) => {
    setObserverPid(pid);
    setObserverModel(providers.find((p) => p.id === pid)?.models[0] || "");
  };

  const applyObserverModel = () => {
    const body: api.ObserverModelConfig = observerMode === "current"
      ? { mode: "current", provider_id: null, model: null }
      : { mode: "dedicated", provider_id: observerPid || null, model: observerModel || null };
    api.setObserverModel(body)
      .then((result) => {
        setObserverMode(result.mode);
        toast(result.mode === "current" ? "观察器将跟随当前聊天模型" : "已保存独立观察模型");
      })
      .catch((e) => toast(e.message || "保存观察模型失败"));
  };

  const onSelectMemoryObserverProvider = (pid: string) => {
    setMemoryObserverPid(pid);
    setMemoryObserverModel(providers.find((p) => p.id === pid)?.models[0] || "");
  };

  const applyMemoryObserverModel = () => {
    const body: api.ObserverModelConfig = memoryObserverMode === "current"
      ? { mode: "current", provider_id: null, model: null }
      : {
          mode: "dedicated",
          provider_id: memoryObserverPid || null,
          model: memoryObserverModel || null,
        };
    api.setMemoryObserverModel(body)
      .then((result) => {
        setMemoryObserverMode(result.mode);
        toast(result.mode === "current" ? "记忆观察器将跟随当前聊天模型" : "已保存独立记忆观察模型");
      })
      .catch((e) => toast(e.message || "保存记忆观察模型失败"));
  };

  const openDrawer = (p: api.Provider) => {
    setDrawerPid(p.id);
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
              execution_location: p.execution_location,
            },
          }
    );
  };

  const closeDrawer = () => setDrawerPid(null);

  const patchEdit = (id: string, patch: Partial<EditForm>) =>
    setEdits((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));

  const editModels = (id: string) =>
    (edits[id]?.models || "")
      .split(",")
      .map((model) => model.trim())
      .filter(Boolean);

  const addModel = (id: string) => {
    const model = (modelDrafts[id] || "").trim();
    if (!model) return;
    const models = editModels(id);
    if (!models.includes(model)) patchEdit(id, { models: [...models, model].join(", ") });
    setModelDrafts((prev) => ({ ...prev, [id]: "" }));
  };

  const removeModel = (id: string, model: string) =>
    patchEdit(id, { models: editModels(id).filter((item) => item !== model).join(", ") });

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
      execution_location: f.execution_location,
    };
    // 密钥安全：输入框为空时不提交 api_key（后端不回传已保存的 key）。
    if (f.api_key.trim()) body.api_key = f.api_key.trim();
    api
      .updateProvider(p.id, body)
      .then(() => {
        toast(`已保存「${p.name}」`);
        // 清空本地 key 输入，避免残留；写操作后刷新列表。
        setEdits((prev) => ({ ...prev, [p.id]: { ...prev[p.id], api_key: "" } }));
        closeDrawer();
        loadProviders();
      })
      .catch((e) => toast(e.message || "保存失败"));
  };

  const discoverModels = (p: api.Provider) => {
    const f = edits[p.id];
    if (!f?.base_url.trim() && p.id !== "mock") {
      toast("请先填写 Base URL");
      return;
    }
    setDiscovering(p.id);
    setDiscoveries((prev) => {
      const next = { ...prev };
      delete next[p.id];
      return next;
    });
    api
      .discoverProviderModels(p.id, f?.base_url.trim() || "", f?.api_key.trim() || "")
      .then((result) => {
        setDiscoveries((prev) => ({ ...prev, [p.id]: { ok: result.ok, message: result.message } }));
        if (!result.ok) return;
        patchEdit(p.id, { models: result.models.join(", ") });
        toast(result.message);
      })
      .catch((e) =>
        setDiscoveries((prev) => ({
          ...prev,
          [p.id]: { ok: false, message: e.message || "获取模型失败" },
        }))
      )
      .finally(() => setDiscovering(null));
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

  // ---- 记忆设置：保留策略 / 敏感度（本地状态，后端尚未支持）----
  const [retentionPolicy, setRetentionPolicy] = useState("count");
  const [l0Max, setL0Max] = useState("10");
  const [l1Max, setL1Max] = useState("50");
  const [l2Max, setL2Max] = useState("200");
  const [sensitivity, setSensitivity] = useState(1);

  // ---- 权限开关（本地状态）----
  const [strictMode, setStrictMode] = useState(true);
  const [permStates, setPermStates] = useState<Record<string, "on" | "ask" | "off">>(() => {
    const states: Record<string, "on" | "ask" | "off"> = {};
    RISK_LEVELS.forEach((lvl) => lvl.tools.forEach((t) => (states[t.name] = t.state)));
    return states;
  });

  const cyclePerm = (name: string) => {
    setPermStates((prev) => {
      const cur = prev[name];
      const next = cur === "on" ? "ask" : cur === "ask" ? "off" : "on";
      return { ...prev, [name]: next };
    });
  };

  const drawerProvider = drawerPid ? providers.find((p) => p.id === drawerPid) : null;

  return (
    <div className="page settings-page">
      {/* 页头：与设计稿一致的 SETTINGS eyebrow + 标题 + 搜索 + 重置 */}
      <header className="settings-hero">
        <div className="settings-hero-text">
          <div className="settings-eyebrow">SETTINGS</div>
          <h1>设置</h1>
          <p>配置模型接口、外观、记忆与权限，让遐蝶更懂你。</p>
        </div>
        <div className="settings-hero-actions">
          <div className="settings-search">
            <span className="settings-search-icon" aria-hidden="true">&#x1F50D;</span>
            <input type="text" placeholder="搜索设置项…" aria-label="搜索设置项" />
          </div>
          <button
            className="settings-reset-btn"
            onClick={() => toast("重置功能开发中")}
          >
            重置为默认
          </button>
        </div>
      </header>

      {/* 分段胶囊式标签栏 */}
      <nav className="settings-tabs" aria-label="设置分类">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={tab === t.key ? "is-active" : ""}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {/* ============ 模型 API ============ */}
      {tab === "model" && (
        <div className="settings-tab-content">
          {loading && <div className="settings-empty">正在加载供应商…</div>}

          {!loading && error && (
            <div className="settings-empty">
              加载失败：{error}
              <div className="settings-empty-actions">
                <button className="btn ghost" onClick={loadProviders}>
                  重试
                </button>
              </div>
            </div>
          )}

          {!loading && !error && (
            <>
              {/* 当前模型 */}
              <section className="settings-card settings-model-current">
                <p className="settings-card-eyebrow">当前模型</p>
                <div className="settings-model-selects">
                  <select
                    className="settings-select"
                    value={selPid}
                    onChange={(e) => onSelectProvider(e.target.value)}
                    aria-label="选择供应商"
                  >
                    {providers.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                  <select
                    className="settings-select settings-select-wide"
                    value={selModel}
                    onChange={(e) => setSelModel(e.target.value)}
                    aria-label="选择模型"
                  >
                    {(selProvider?.models || []).length === 0 && <option value="">该供应商暂无模型</option>}
                    {(selProvider?.models || []).map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                  <button className="btn settings-primary-btn" onClick={applyCurrentModel}>
                    设为当前
                  </button>
                </div>
                {current && (
                  <div className="settings-current-hint">
                    <span className="settings-current-label">
                      正在使用：{current.provider_name} · {current.model}
                    </span>
                    {current.capabilities.length > 0 && (
                      <span className="cap-tags">
                        {current.capabilities.map((c) => {
                          const cap = CAP_DESC.find((d) => d.key === c);
                          return (
                            <span key={c} className={`cap-tag cap-${cap?.tone || "violet"}`}>
                              {c}
                            </span>
                          );
                        })}
                      </span>
                    )}
                  </div>
                )}
              </section>

              {/* 情绪观察模型 */}
              <section className="settings-card settings-observer">
                <p className="settings-card-eyebrow">情绪观察模型</p>
                <p className="settings-card-sub">用于实时分析用户情绪，驱动角色语气与表情的微妙变化。</p>
                <div className="settings-model-selects">
                  <select
                    className="settings-select"
                    value={observerMode}
                    onChange={(e) => setObserverMode(e.target.value as "current" | "dedicated")}
                  >
                    <option value="current">跟随当前</option>
                    <option value="dedicated">独立模型</option>
                  </select>
                  {observerMode === "dedicated" && (
                    <>
                      <select
                        className="settings-select"
                        value={observerPid}
                        onChange={(e) => onSelectObserverProvider(e.target.value)}
                      >
                        {providers.filter((p) => p.id !== "mock" && p.enabled).map((p) => (
                          <option key={p.id} value={p.id}>{p.name}</option>
                        ))}
                      </select>
                      <select
                        className="settings-select settings-select-wide"
                        value={observerModel}
                        onChange={(e) => setObserverModel(e.target.value)}
                      >
                        {(providers.find((p) => p.id === observerPid)?.models || []).map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                    </>
                  )}
                  <button className="btn settings-primary-btn settings-save-btn" onClick={applyObserverModel}>
                    保存
                  </button>
                </div>
              </section>

              {/* 记忆观察模型 */}
              <section className="settings-card settings-observer">
                <p className="settings-card-eyebrow">记忆观察模型</p>
                <p className="settings-card-sub">在对话过程中提取关键记忆片段，写入长期记忆库以持续学习。</p>
                <div className="settings-model-selects">
                  <select
                    className="settings-select"
                    value={memoryObserverMode}
                    onChange={(e) => setMemoryObserverMode(e.target.value as "current" | "dedicated")}
                  >
                    <option value="current">跟随当前</option>
                    <option value="dedicated">独立模型</option>
                  </select>
                  {memoryObserverMode === "dedicated" && (
                    <>
                      <select
                        className="settings-select"
                        value={memoryObserverPid}
                        onChange={(e) => onSelectMemoryObserverProvider(e.target.value)}
                      >
                        {providers.filter((p) => p.id !== "mock" && p.enabled).map((p) => (
                          <option key={p.id} value={p.id}>{p.name}</option>
                        ))}
                      </select>
                      <select
                        className="settings-select settings-select-wide"
                        value={memoryObserverModel}
                        onChange={(e) => setMemoryObserverModel(e.target.value)}
                      >
                        {(providers.find((p) => p.id === memoryObserverPid)?.models || []).map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                    </>
                  )}
                  <button className="btn settings-primary-btn settings-save-btn" onClick={applyMemoryObserverModel}>
                    保存
                  </button>
                </div>
              </section>

              {/* 供应商卡片网格 */}
              <p className="settings-card-eyebrow settings-section-eyebrow">供应商</p>
              {providers.length === 0 && <div className="settings-empty">还没有配置任何供应商。</div>}
              <div className="settings-provider-grid">
                {providers.map((p) => {
                  const isOpen = drawerPid === p.id;
                  const t = tests[p.id];
                  return (
                    <div
                      key={p.id}
                      className={`settings-provider-card${isOpen ? " is-active" : ""}`}
                      onClick={() => openDrawer(p)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          openDrawer(p);
                        }
                      }}
                    >
                      <div className="settings-provider-head">
                        <span
                          className={`settings-provider-dot ${
                            p.has_key ? "is-ok" : p.id === "mock" ? "is-danger" : "is-faint"
                          }`}
                          aria-hidden="true"
                        />
                        <span className="settings-provider-name">{p.name}</span>
                      </div>
                      <div className="settings-provider-meta">
                        <span>{p.models.length} 个模型</span>
                        <span className={p.has_key ? "settings-key-ok" : "settings-key-miss"}>
                          {p.has_key ? "已配置密钥" : "未配置密钥"}
                        </span>
                      </div>
                      {testing === p.id && <div className="settings-provider-testing">测试中…</div>}
                      {t && (
                        <div className={`settings-provider-test ${t.ok ? "is-ok" : "is-fail"}`}>
                          {t.ok ? "连接正常" : t.message || "连接失败"}
                        </div>
                      )}
                      <div className="settings-provider-actions" onClick={(e) => e.stopPropagation()}>
                        <button
                          className="btn ghost settings-mini-btn"
                          onClick={() => runTest(p)}
                          disabled={testing === p.id}
                        >
                          连接测试
                        </button>
                        <button
                          className={`btn settings-mini-btn ${isOpen ? "settings-config-btn-active" : "ghost"}`}
                          onClick={() => openDrawer(p)}
                        >
                          配置 ▸
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* 能力标签说明 */}
              <p className="settings-card-eyebrow settings-section-eyebrow">能力标签说明</p>
              <div className="settings-cap-reference">
                {CAP_DESC.map((c) => (
                  <div key={c.key} className="settings-cap-row">
                    <span className={`cap-tag cap-${c.tone}`}>{c.key}</span>
                    <span className="settings-cap-desc">{c.label}</span>
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
        <div className="settings-tab-content settings-memory-content">
          {/* 长期记忆开关 */}
          <section className="settings-card settings-mem-toggle-card">
            <div className="settings-mem-toggle-row">
              <div className="settings-mem-toggle-text">
                <h2>启用长期记忆</h2>
                <p>
                  {memErr
                    ? `读取失败：${memErr}`
                    : "开启后，遐蝶会在对话中沉淀并回忆你的偏好与重要信息。"}
                </p>
              </div>
              {memEnabled === null ? (
                <span className="settings-status-pill settings-status-off">
                  {memErr ? "不可用" : "读取中…"}
                </span>
              ) : (
                <button
                  className={`toggle-track${memEnabled ? " is-on" : ""}`}
                  role="switch"
                  aria-checked={memEnabled}
                  aria-label="启用长期记忆"
                  onClick={() => setMemory(!memEnabled)}
                >
                  <span className="toggle-thumb" />
                </button>
              )}
            </div>
          </section>

          {/* 记忆层级分布 */}
          <section className="settings-card">
            <h2 className="settings-card-title">记忆层级分布</h2>
            <div className="settings-mem-layer">
              <div className="settings-mem-layer-head">
                <span>L0 核心画像</span>
                <span>5 条</span>
              </div>
              <div className="memory-bar-track">
                <div
                  className="memory-bar-fill memory-bar-l0"
                  style={{ width: "10%" }}
                />
              </div>
            </div>
            <div className="settings-mem-layer">
              <div className="settings-mem-layer-head">
                <span>L1 近期状态</span>
                <span>18 条</span>
              </div>
              <div className="memory-bar-track">
                <div
                  className="memory-bar-fill memory-bar-l1"
                  style={{ width: "38%" }}
                />
              </div>
            </div>
            <div className="settings-mem-layer">
              <div className="settings-mem-layer-head">
                <span>L2 长期记忆</span>
                <span>24 条</span>
              </div>
              <div className="memory-bar-track">
                <div
                  className="memory-bar-fill memory-bar-l2"
                  style={{ width: "52%" }}
                />
              </div>
            </div>
            <p className="settings-card-hint">目标比例: L0 ≤ 10% · L1 30-50% · L2 ≥ 40%</p>
          </section>

          {/* 保留策略 */}
          <section className="settings-card">
            <h2 className="settings-card-title">保留策略</h2>
            <div className="settings-retention-select">
              <select
                className="xd-select"
                value={retentionPolicy}
                onChange={(e) => setRetentionPolicy(e.target.value)}
              >
                <option value="count">按条数上限</option>
                <option value="time">按时间</option>
                <option value="manual">手动管理</option>
              </select>
            </div>
            <div className="settings-retention-rows">
              <div className="settings-retention-row">
                <span>L0 最大条数</span>
                <div className="settings-retention-input">
                  <input
                    type="text"
                    className="xd-input"
                    value={l0Max}
                    onChange={(e) => setL0Max(e.target.value)}
                  />
                  <span className="settings-retention-hint">核心画像条数不宜过多</span>
                </div>
              </div>
              <div className="settings-retention-row">
                <span>L1 最大条数</span>
                <div className="settings-retention-input">
                  <input
                    type="text"
                    className="xd-input"
                    value={l1Max}
                    onChange={(e) => setL1Max(e.target.value)}
                  />
                </div>
              </div>
              <div className="settings-retention-row">
                <span>L2 最大条数</span>
                <div className="settings-retention-input">
                  <input
                    type="text"
                    className="xd-input"
                    value={l2Max}
                    onChange={(e) => setL2Max(e.target.value)}
                  />
                </div>
              </div>
              <p className="settings-retention-warn">超出时自动降级或归档最旧记忆</p>
            </div>
          </section>

          {/* 自主记忆敏感度 */}
          <section className="settings-card">
            <h2 className="settings-card-title">自主记忆敏感度</h2>
            <div className="settings-sensitivity">
              <div className="settings-sensitivity-scale">
                <span>低</span>
                <span>中</span>
                <span>高</span>
              </div>
              <input
                type="range"
                className="xd-range"
                min={0}
                max={2}
                step={1}
                value={sensitivity}
                onChange={(e) => setSensitivity(Number(e.target.value))}
              />
              <div className="settings-sensitivity-detail">
                <p className="settings-sensitivity-label">
                  {sensitivity === 0 ? "低" : sensitivity === 1 ? "中" : "高"}
                </p>
                <p className="settings-sensitivity-desc">
                  {sensitivity === 0
                    ? "仅留下高置信度的重要记忆"
                    : sensitivity === 1
                    ? "平衡记忆频率与噪音，推荐大多数用户使用"
                    : "更多内容会被记住，可能包含日常闲聊"}
                </p>
              </div>
            </div>
          </section>

          {/* 记忆数据 */}
          <section className="settings-card">
            <h2 className="settings-card-title">记忆数据</h2>
            <div className="settings-mem-data-actions">
              <button className="btn ghost settings-mem-data-btn" onClick={() => toast("导出功能开发中")}>
                <span aria-hidden="true">&#x2B07;</span>
                导出记忆数据
              </button>
              <button className="btn ghost settings-mem-data-btn" onClick={() => toast("导入功能开发中")}>
                <span aria-hidden="true">&#x2B06;</span>
                导入记忆数据
              </button>
            </div>
            <p className="settings-card-hint">导出为 JSON 格式，可在记忆与关系页导入恢复</p>
          </section>
        </div>
      )}

      {/* ============ 权限 ============ */}
      {tab === "perms" && (
        <div className="settings-tab-content settings-perms-content">
          {/* 安全策略横幅 */}
          <section className="settings-card settings-perm-banner">
            <div className="settings-perm-banner-text">
              <h2>安全策略</h2>
              <p>高风险工具默认需确认，不提供「一键全开」，以保护你的设备与数据安全。</p>
            </div>
            <div className="settings-perm-banner-toggle">
              <button
                className={`header-toggle${strictMode ? " is-on" : ""}`}
                role="switch"
                aria-checked={strictMode}
                aria-label="全局严格模式"
                onClick={() => setStrictMode(!strictMode)}
              >
                <span className="header-toggle-thumb" />
              </button>
              <span className="settings-perm-banner-label">全局严格模式</span>
            </div>
          </section>

          {/* S0–S4 风险等级卡片 */}
          {RISK_LEVELS.map((lvl) => (
            <section key={lvl.code} className={`settings-card settings-risk-card risk-${lvl.tone}`}>
              <div className="settings-risk-head">
                <span className={`settings-risk-badge risk-badge-${lvl.tone}`}>{lvl.code}</span>
                <span className="settings-risk-label">{lvl.label}</span>
                <span className={`settings-risk-policy risk-policy-${lvl.tone}`}>{lvl.policy}</span>
              </div>
              {lvl.code === "S0" ? (
                /* S0 无风险：默认放行，显示带勾选标记的芯片 */
                <div className="settings-risk-chips">
                  {lvl.tools.map((tool) => (
                    <span key={tool.name} className={`settings-risk-chip risk-chip-${lvl.tone}`}>
                      <span aria-hidden="true">&#10003;</span> {tool.name}
                    </span>
                  ))}
                </div>
              ) : (
                <div className="settings-risk-tools">
                  {lvl.tools.map((tool) => (
                    <div key={tool.name} className="settings-risk-tool">
                      <span>{tool.name}</span>
                      {permStates[tool.name] === "ask" && (
                        <span className="settings-perm-hint">逐次确认</span>
                      )}
                      <button
                        className={`perm-toggle ${permStates[tool.name]}`}
                        role="switch"
                        aria-checked={permStates[tool.name] === "on"}
                        aria-label={tool.name}
                        onClick={() => cyclePerm(tool.name)}
                      />
                    </div>
                  ))}
                </div>
              )}
            </section>
          ))}

          {/* 会话记忆提示 */}
          <div className="settings-perm-note">
            <span>按工具粒度记住本次会话的选择</span>
            <span className="settings-perm-note-dot" aria-hidden="true">·</span>
            <span>所有工具调用记录可在「工具记录」查看</span>
          </div>
        </div>
      )}

      {/* ============ 数据 ============ */}
      {tab === "data" && (
        <div className="settings-tab-content">
          <section className="settings-card">
            <div className="settings-card-title-row">
              <span className="settings-card-title">导出与清理</span>
              <span className="settings-status-pill settings-status-off">开发中</span>
            </div>
            <p className="settings-card-hint">导出全部会话、记忆与任务，或清理本地缓存数据。</p>
            <div className="settings-data-actions">
              <button className="btn ghost" onClick={() => toast("导出功能开发中")}>
                导出全部数据
              </button>
              <button className="btn ghost" onClick={() => toast("清理功能开发中")}>
                清理缓存 / 记忆
              </button>
            </div>
          </section>
          <PlaceholderSection
            title="数据细则"
            items={["导出为 JSON / Markdown", "选择性清理会话或记忆", "本地数据库备份与恢复"]}
          />
        </div>
      )}

      {/* ============ 供应商详情抽屉 ============ */}
      {drawerProvider && (
        <ProviderDrawer
          provider={drawerProvider}
          edit={edits[drawerProvider.id]}
          test={tests[drawerProvider.id]}
          discovery={discoveries[drawerProvider.id]}
          testing={testing === drawerProvider.id}
          discovering={discovering === drawerProvider.id}
          modelDraft={modelDrafts[drawerProvider.id] || ""}
          onPatch={(patch) => patchEdit(drawerProvider.id, patch)}
          onAddModel={() => addModel(drawerProvider.id)}
          onRemoveModel={(m) => removeModel(drawerProvider.id, m)}
          onSetModelDraft={(v) => setModelDrafts((prev) => ({ ...prev, [drawerProvider.id]: v }))}
          onDiscover={() => discoverModels(drawerProvider)}
          onTest={() => runTest(drawerProvider)}
          onSave={() => saveProvider(drawerProvider)}
          onClose={closeDrawer}
        />
      )}
    </div>
  );
}

// 供应商详情抽屉。
function ProviderDrawer({
  provider,
  edit,
  test,
  discovery,
  testing,
  discovering,
  modelDraft,
  onPatch,
  onAddModel,
  onRemoveModel,
  onSetModelDraft,
  onDiscover,
  onTest,
  onSave,
  onClose,
}: {
  provider: api.Provider;
  edit: EditForm | undefined;
  test: { ok: boolean; message: string } | undefined;
  discovery: { ok: boolean; message: string } | undefined;
  testing: boolean;
  discovering: boolean;
  modelDraft: string;
  onPatch: (patch: Partial<EditForm>) => void;
  onAddModel: () => void;
  onRemoveModel: (model: string) => void;
  onSetModelDraft: (value: string) => void;
  onDiscover: () => void;
  onTest: () => void;
  onSave: () => void;
  onClose: () => void;
}) {
  if (!edit) return null;
  const models = (edit.models || "")
    .split(",")
    .map((m) => m.trim())
    .filter(Boolean);

  return (
    <div className="settings-drawer-overlay" onClick={onClose}>
      <div
        className="settings-drawer"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={`${provider.name} 配置`}
      >
        <div className="settings-drawer-header">
          <div>
            <p className="settings-drawer-eyebrow">{provider.id.toUpperCase()}</p>
            <h2>{provider.name}</h2>
          </div>
          <button className="settings-drawer-close" onClick={onClose} aria-label="关闭">
            &#x2715;
          </button>
        </div>

        {/* 连接状态 */}
        <div className="settings-drawer-status">
          <span
            className={`settings-drawer-status-dot ${
              test ? (test.ok ? "is-ok" : "is-fail") : provider.has_key ? "is-ok" : "is-faint"
            }`}
          />
          <span className={`settings-drawer-status-text ${test ? (test.ok ? "is-ok" : "is-fail") : provider.has_key ? "is-ok" : ""}`}>
            {test ? (test.ok ? "连接正常" : test.message || "连接失败") : provider.has_key ? "未测试" : "未配置密钥"}
          </span>
          <button className="btn ghost settings-mini-btn" onClick={onTest} disabled={testing}>
            {testing ? "测试中…" : "重新测试"}
          </button>
        </div>

        {/* 表单 */}
        <div className="settings-drawer-form">
          {/* Base URL */}
          <div className="settings-field">
            <label>Base URL</label>
            <input
              type="text"
              value={edit.base_url}
              onChange={(e) => onPatch({ base_url: e.target.value })}
              placeholder="https://api.example.com/v1"
            />
          </div>

          {/* 模型运行位置 */}
          <div className="settings-field">
            <label>模型运行位置</label>
            <select
              value={edit.execution_location}
              onChange={(e) =>
                onPatch({ execution_location: e.target.value as api.Provider["execution_location"] })
              }
            >
              <option value="unknown">未知（按远程处理）</option>
              <option value="remote">远程服务</option>
              <option value="local">本机服务</option>
            </select>
            <p className="settings-field-hint">
              地址变化会使位置 revision 更新；只有本机回环地址才能确认成"本机服务"。
            </p>
          </div>

          {/* API Key */}
          <div className="settings-field">
            <label>API Key</label>
            <div className="settings-key-row">
              <input
                type="password"
                value={edit.api_key}
                onChange={(e) => onPatch({ api_key: e.target.value })}
                placeholder={provider.has_key ? "已保存密钥（留空则不修改）" : "未配置"}
              />
            </div>
          </div>

          {/* 模型列表 */}
          <div className="settings-field">
            <div className="settings-model-list-head">
              <label>模型列表</label>
              <button
                className="btn ghost settings-mini-btn settings-discover-btn"
                onClick={onDiscover}
                disabled={discovering}
              >
                {discovering ? "正在获取…" : "自动获取模型"}
              </button>
            </div>
            <div className="settings-model-chips">
              {models.map((model) => (
                <span className="settings-model-chip" key={model}>
                  <span>{model}</span>
                  <button
                    type="button"
                    aria-label={`移除 ${model}`}
                    title="移除模型"
                    onClick={() => onRemoveModel(model)}
                  >
                    &#x2715;
                  </button>
                </span>
              ))}
            </div>
            <input
              className="settings-model-add-input"
              value={modelDraft}
              onChange={(e) => onSetModelDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  onAddModel();
                }
              }}
              placeholder={models.length ? "添加模型，按 Enter" : "输入模型名，按 Enter 添加"}
            />
            <p className="settings-field-hint">
              {discovery ? (
                <span style={{ color: discovery.ok ? "var(--ok)" : "var(--danger)" }}>
                  {discovery.message}
                </span>
              ) : (
                "从 Base URL 的 /models 接口读取；也可以手动输入模型名并按 Enter 添加。"
              )}
            </p>
          </div>

          {/* 启用开关 */}
          <label className="settings-enable-row">
            <span
              className={`settings-checkbox ${edit.enabled ? "is-checked" : ""}`}
              aria-hidden="true"
            >
              {edit.enabled && "&#x2713;"}
            </span>
            <input
              type="checkbox"
              checked={edit.enabled}
              onChange={(e) => onPatch({ enabled: e.target.checked })}
            />
            <span>启用该供应商</span>
          </label>
        </div>

        {/* 操作按钮 */}
        <div className="settings-drawer-actions">
          <button className="btn settings-primary-btn" onClick={onSave}>
            保存配置
          </button>
          <button className="btn ghost" onClick={onClose}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

// 尚无对应后端接口的分组，做成清晰的「开发中」占位区块。
function PlaceholderSection({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="settings-tab-content">
      <section className="settings-card">
        <div className="settings-card-title-row">
          <span className="settings-card-title">{title}</span>
          <span className="settings-status-pill settings-status-off">开发中</span>
        </div>
        <p className="settings-card-hint">该分组将包含：</p>
        <ul className="settings-placeholder-list">
          {items.map((i) => (
            <li key={i}>{i}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}
