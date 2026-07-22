import { useEffect, useRef, useState } from "react";
import * as api from "./../api";
import { toast } from "./../store";

const MAX_FILE_BYTES = 10 * 1024 * 1024;

// 上传错误按 status code 分类，优先使用后端返回的中文 message
// 后端 import_knowledge_document 已统一返回 {code, message} 结构化格式
const classifyUploadError = (e: { status?: number; message?: string }): string => {
  if (e.status === 401) return "令牌失效，请重启遐蝶";
  const msg = e.message || "";
  if (/Failed to fetch|NetworkError|ERR_CONNECTION/i.test(msg)) {
    return "无法连接到后端，请确认遐蝶已正常启动";
  }
  if (e.status === 413) return msg || "文件超过 10 MiB 限制";
  if (e.status === 415) return msg || "文件类型不支持";
  if (e.status === 409) return msg || "知识库已满或内容冲突";
  if (e.status && e.status >= 500) return `后端异常：${msg || "服务异常"}`;
  return msg || "文件导入失败";
};

// 需求 6.6 的知识库原则
const PRINCIPLES: { title: string; desc: string }[] = [
  {
    title: "用户明确导入",
    desc: "只有你亲手拖入或选择的文件才会进入知识库，不会凭空收录。",
  },
  {
    title: "来源可追溯",
    desc: "每条知识都保留原始文件与出处，回答引用时可回溯到具体来源。",
  },
  {
    title: "结果可删除",
    desc: "导入的条目随时可以删除，删除后不再参与检索与生成。",
  },
  {
    title: "不默认扫描磁盘",
    desc: "遐蝶不会在后台自动扫描你的硬盘或翻找文件目录。",
  },
  {
    title: "不在不知情时上传",
    desc: "不会在你不知情的情况下把文件内容上传到远程模型。",
  },
  {
    title: "敏感文件提示",
    desc: "涉及敏感内容时，会提示相关供应商与数据流向，由你决定是否继续。",
  },
];

type ViewMode = "list" | "grid";

export function FilesPage() {
  const [dragging, setDragging] = useState(false);
  const [pending, setPending] = useState<File | null>(null);
  const [sensitive, setSensitive] = useState(false);
  const [importing, setImporting] = useState(false);
  const [documents, setDocuments] = useState<api.KnowledgeDocument[]>([]);
  const [collections, setCollections] = useState<api.KnowledgeCollection[]>([]);
  const [runDetails, setRunDetails] = useState<Record<string, api.KnowledgeImportRun>>({});
  const [search, setSearch] = useState("");
  const [collectionFilter, setCollectionFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [editingTags, setEditingTags] = useState<string | null>(null);
  const [tagDraft, setTagDraft] = useState("");
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [audits, setAudits] = useState<api.KnowledgeRetrievalAudit[] | null>(null);
  const [recallDecisions, setRecallDecisions] = useState<api.KnowledgeRecallDecision[] | null>(null);
  const [recallStats, setRecallStats] = useState<api.KnowledgeRecallStats | null>(null);
  const [auditLifecycle, setAuditLifecycle] = useState<api.KnowledgeAuditLifecycle | null>(null);
  const [embeddingStatus, setEmbeddingStatus] = useState<api.KnowledgeEmbeddingStatus | null>(null);
  const [recallSettings, setRecallSettings] = useState<api.KnowledgeRecallSettings | null>(null);
  const [privacyOpen, setPrivacyOpen] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("list");
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [activeSource, setActiveSource] = useState<"local" | "bookmark">("local");
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = () => api.listKnowledgeDocuments({
    collection_id: collectionFilter || undefined,
    status: statusFilter || undefined,
    query: search.trim() || undefined,
  }).then(setDocuments);
  useEffect(() => {
    api.listKnowledgeCollections().then(setCollections).catch(() => toast("知识库集合加载失败"));
    api.getKnowledgeEmbeddingStatus().then(setEmbeddingStatus).catch(() => {});
    api.getKnowledgeRecallSettings().then(setRecallSettings).catch(() => {});
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => refresh().catch(() => toast("知识文档列表加载失败")), 220);
    return () => window.clearTimeout(timer);
  }, [search, collectionFilter, statusFilter]);
  const hasActiveProcessing = documents.some((document) =>
    ["queued", "parsing", "delete_pending"].includes(document.status) ||
    ["queued", "running"].includes(document.latest_embedding?.status || "")
  );
  useEffect(() => {
    if (!hasActiveProcessing) return;
    const timer = window.setInterval(() => refresh().catch(() => {}), 1500);
    return () => window.clearInterval(timer);
  }, [hasActiveProcessing, search, collectionFilter, statusFilter]);

  function choose(file: File) {
    const extension = file.name.toLowerCase().split(".").pop();
    if (!extension || !["txt", "md", "pdf", "docx"].includes(extension)) {
      toast("目前支持 UTF-8 TXT、Markdown、PDF 和 DOCX 文件");
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      toast("文件超过 10 MiB 限制");
      return;
    }
    setPending(file);
    setSensitive(false);
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) choose(file);
  }

  function onDragOver(e: React.DragEvent) {
    e.preventDefault();
    if (!dragging) setDragging(true);
  }

  function onDragLeave(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
  }

  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) choose(f);
    // 清空以便再次选择同一文件也能触发
    e.target.value = "";
  }

  async function confirmImport() {
    if (!pending) return;
    setImporting(true);
    try {
      const result = await api.importKnowledgeFile(pending, sensitive ? "sensitive" : "normal");
      toast(result.already_exists ? "相同内容已经在知识库中" : "文件已安全保存，等待后台解析");
      setPending(null);
      setSensitive(false);
      await refresh();
    } catch (error: any) {
      toast(classifyUploadError(error));
    } finally {
      setImporting(false);
    }
  }

  function toggleRun(document: api.KnowledgeDocument) {
    // 已展开 → 收起
    if (runDetails[document.id]) {
      setRunDetails((current) => {
        const next = { ...current };
        delete next[document.id];
        return next;
      });
      return;
    }
    // 未展开 → 加载并展开
    const runId = document.latest_run?.id;
    if (!runId) return;
    api.getKnowledgeImportRun(runId)
      .then((run) => setRunDetails((current) => ({ ...current, [document.id]: run })))
      .catch((error: any) => toast(error.message || "任务详情加载失败"));
  }

  async function cancelRun(document: api.KnowledgeDocument) {
    const runId = document.latest_run?.id;
    if (!runId || !window.confirm(`停止处理「${document.original_name}」吗？原文件副本仍会保留。`)) return;
    try {
      await api.cancelKnowledgeImportRun(runId);
      toast("已请求停止处理");
      await refresh();
    } catch (error: any) {
      toast(error.message || "停止失败");
    }
  }

  async function saveTags(document: api.KnowledgeDocument) {
    const tags = tagDraft.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean);
    setActionBusy(`tags:${document.id}`);
    try {
      await api.updateKnowledgeTags(document.id, tags);
      toast("标签已保存");
      setEditingTags(null);
      await refresh();
    } catch (error: any) {
      toast(error.message || "标签保存失败");
    } finally {
      setActionBusy(null);
    }
  }

  async function reindexDocument(document: api.KnowledgeDocument) {
    if (!window.confirm(`重建「${document.original_name}」的本地索引吗？重建期间会暂时退出检索。`)) return;
    setActionBusy(`reindex:${document.id}`);
    try {
      await api.reindexKnowledgeDocument(document.id);
      toast("已开始重建本地索引");
      await refresh();
    } catch (error: any) {
      toast(error.message || "重建启动失败");
    } finally {
      setActionBusy(null);
    }
  }

  async function buildEmbedding(document: api.KnowledgeDocument) {
    setActionBusy(`embedding:${document.id}`);
    try {
      await api.buildKnowledgeEmbedding(document.id);
      toast("已开始建立本地语义索引，全文检索仍可正常使用");
      await refresh();
    } catch (error: any) {
      toast(error.message || "本地语义索引启动失败");
    } finally {
      setActionBusy(null);
    }
  }

  async function updateTransmissionPolicy(
    document: api.KnowledgeDocument,
    transmissionPolicy: api.KnowledgeDocument["transmission_policy"],
  ) {
    setActionBusy(`policy:${document.id}`);
    try {
      await api.updateKnowledgeTransmissionPolicy(document.id, transmissionPolicy);
      toast("资料偏好已更新");
      await refresh();
    } catch (error: any) {
      toast(error.message || "资料偏好更新失败");
    } finally {
      setActionBusy(null);
    }
  }

  async function deleteDocument(document: api.KnowledgeDocument) {
    const confirmed = window.confirm(
      `确定删除「${document.original_name}」吗？\n\n将清除遐蝶应用内的原文副本、切片、索引和解析产物，立即停止召回。应用外的原文件或备份不会同步删除。`,
    );
    if (!confirmed) return;
    setActionBusy(`delete:${document.id}`);
    try {
      await api.deleteKnowledgeDocument(document.id);
      toast("已退出召回，正在清理应用内资料");
      await refresh();
    } catch (error: any) {
      toast(error.message || "删除启动失败");
    } finally {
      setActionBusy(null);
    }
  }

  async function retryDelete(document: api.KnowledgeDocument) {
    const runId = document.latest_deletion?.id;
    if (!runId || !window.confirm("再次尝试清理这份应用内资料吗？外部原文件和备份不受影响。")) return;
    setActionBusy(`delete:${document.id}`);
    try {
      await api.retryKnowledgeDeletion(runId);
      toast("已重新开始清理");
      await refresh();
    } catch (error: any) {
      toast(error.message || "重试删除失败");
    } finally {
      setActionBusy(null);
    }
  }

  async function toggleAudits() {
    if (audits !== null) {
      setAudits(null);
      setRecallDecisions(null);
      setRecallStats(null);
      setAuditLifecycle(null);
      return;
    }
    try {
      const [retrievals, decisions, stats, lifecycle] = await Promise.all([
        api.listKnowledgeRetrievals(), api.listKnowledgeRecallDecisions(), api.getKnowledgeRecallStats(),
        api.getKnowledgeAuditLifecycle(),
      ]);
      setAudits(retrievals);
      setRecallDecisions(decisions);
      setRecallStats(stats);
      setAuditLifecycle(lifecycle);
    } catch (error: any) {
      toast(error.message || "检索记录加载失败");
    }
  }

  async function changeCollectionPolicy(
    collection: api.KnowledgeCollection,
    policy: api.KnowledgeDocument["transmission_policy"],
  ) {
    if (!window.confirm("修改这个集合以后导入普通文档时采用的默认发送策略吗？")) return;
    const applyExisting = window.confirm(
      "同时把这个策略应用到集合内现有文档吗？\n\n确定：应用到现有文档；取消：只作为以后导入文档的默认策略。",
    );
    setActionBusy(`collection-policy:${collection.id}`);
    try {
      const result = await api.updateKnowledgeCollectionPolicy(collection.id, policy, applyExisting);
      toast(applyExisting
        ? `集合策略已更新，修改 ${result.updated_document_count} 份文档`
        : "集合默认策略已更新，仅影响以后导入的普通文档");
      setCollections(await api.listKnowledgeCollections());
      await refresh();
    } catch (error: any) {
      toast(error.message || "集合策略更新失败");
    } finally {
      setActionBusy(null);
    }
  }

  async function downloadManifest() {
    try {
      const manifest = await api.getKnowledgeExportManifest();
      const url = URL.createObjectURL(new Blob(
        [JSON.stringify(manifest, null, 2)], { type: "application/json" },
      ));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "xiadie-knowledge-manifest.json";
      anchor.click();
      URL.revokeObjectURL(url);
      toast("已导出不含正文、向量和授权 token 的知识清单");
    } catch (error: any) {
      toast(error.message || "知识清单导出失败");
    }
  }

  async function clearAllKnowledge() {
    const confirmation = window.prompt(
      "这会让全部知识立即退出召回，并清除应用内原文副本、切片、索引、授权与派生审计。\n"
      + "应用外原文件和备份不会删除。请输入 CLEAR_ALL_KNOWLEDGE 确认：",
    );
    if (confirmation !== "CLEAR_ALL_KNOWLEDGE") return;
    setActionBusy("clear-all");
    try {
      const result = await api.clearAllKnowledge(confirmation);
      toast(`全部知识已退出召回，正在清理 ${result.queued_document_count} 份应用内资料`);
      await refresh();
    } catch (error: any) {
      toast(error.message || "完整清除启动失败");
    } finally {
      setActionBusy(null);
    }
  }

  async function changeRecallMode(mode: api.KnowledgeRecallSettings["mode"]) {
    if (!recallSettings || mode === recallSettings.mode) return;
    setActionBusy("recall-mode");
    try {
      setRecallSettings(await api.updateKnowledgeRecallSettings({ mode }));
      toast(mode === "off" ? "已关闭知识召回" : mode === "smart"
        ? "已启用高置信智能召回" : "已恢复仅明确请求时召回");
    } catch (error: any) {
      toast(error.message || "召回模式修改失败");
    } finally {
      setActionBusy(null);
    }
  }

  function toggleGroup(groupId: string) {
    setCollapsedGroups((current) => {
      const next = new Set(current);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  }

  const indexedCount = documents.filter((document) => document.status === "indexed").length;
  const failedCount = documents.filter((document) => ["failed", "delete_failed"].includes(document.status)).length;
  const waitingCount = Math.max(0, documents.length - indexedCount - failedCount);
  const groupedDocuments: Array<{
    id: string; name: string; collection: api.KnowledgeCollection | null;
    documents: api.KnowledgeDocument[];
  }> = collections
    .map((collection) => ({
      id: collection.id,
      name: collection.name,
      collection,
      documents: documents.filter((document) => document.collection_id === collection.id),
    }))
    .filter((group) => group.documents.length > 0);
  const ungroupedDocuments = documents.filter(
    (document) => !collections.some((collection) => collection.id === document.collection_id),
  );
  if (ungroupedDocuments.length > 0) {
    groupedDocuments.push({ id: "other", name: "未分类", collection: null, documents: ungroupedDocuments });
  }

  const statusClassOf = (document: api.KnowledgeDocument) => documentStatusClass(document);
  const statusLabelOf = (document: api.KnowledgeDocument) => documentStatus(document);

  return (
    <div className="page knowledge-page">
      {/* 1. 紧凑页眉 */}
      <header className="knowledge-hero">
        <div className="knowledge-hero-text">
          <div className="knowledge-eyebrow">KNOWLEDGE BASE</div>
          <h1>文件与知识</h1>
          <p>管理你的文件和网页书签，让遐蝶理解你提供的知识。</p>
        </div>
        <div className="knowledge-hero-actions">
          <div className="knowledge-view-toggle" role="group" aria-label="视图切换">
            <button className={viewMode === "list" ? "is-active" : ""} onClick={() => setViewMode("list")} title="列表视图" aria-label="列表视图">
              <span aria-hidden="true">▤</span>
            </button>
            <button className={viewMode === "grid" ? "is-active" : ""} onClick={() => setViewMode("grid")} title="网格视图" aria-label="网格视图">
              <span aria-hidden="true">▦</span>
            </button>
          </div>
          <button className="knowledge-history-button" onClick={toggleAudits}>
            <span aria-hidden="true">◇</span>
            {audits === null ? "检索记录" : "收起记录"}
          </button>
          <button className="knowledge-primary-button" onClick={() => inputRef.current?.click()}>
            <span aria-hidden="true">↑</span>导入文件
          </button>
        </div>
      </header>

      {/* 2. 搜索栏 */}
      <div className="knowledge-searchbar">
        <label className="knowledge-search-field">
          <span aria-hidden="true">⌕</span>
          <input value={search} onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索文件名、内容、引用…" maxLength={120} />
        </label>
        <select aria-label="分组筛选" className="knowledge-filter-select" value={collectionFilter}
          onChange={(event) => setCollectionFilter(event.target.value)}>
          <option value="">全部分组</option>
          {collections.map((collection) => (
            <option key={collection.id} value={collection.id}>{collection.name}</option>
          ))}
        </select>
        <select aria-label="状态筛选" className="knowledge-filter-select" value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}>
          <option value="">全部状态</option>
          <option value="indexed">已索引</option>
          <option value="queued">等待处理</option>
          <option value="parsing">处理中</option>
          <option value="failed">处理失败</option>
          <option value="cancelled">已取消</option>
          <option value="delete_pending">删除中</option>
          <option value="delete_failed">删除失败</option>
        </select>
      </div>

      {/* 3. 上传条 + 内联隐私 */}
      <div
        className={`knowledge-upload-strip ${dragging ? "is-dragging" : ""}`}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onClick={() => inputRef.current?.click()}
      >
        <div className="knowledge-upload-icon" aria-hidden="true">＋</div>
        <div className="knowledge-upload-copy">
          <strong>{dragging ? "松开即可准备导入" : "拖入文件，或从本机选择"}</strong>
          <span>支持 TXT、Markdown、PDF、DOCX · 单文件不超过 10 MiB</span>
        </div>
        <button className="knowledge-primary-button knowledge-upload-pick" onClick={(event) => {
          event.stopPropagation();
          inputRef.current?.click();
        }}>选择文件</button>
        <input
          ref={inputRef}
          type="file"
          accept=".txt,.md,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          onChange={onPick}
          style={{ display: "none" }}
        />
        <button
          className="knowledge-privacy-inline"
          onClick={(event) => { event.stopPropagation(); setPrivacyOpen((current) => !current); }}
          aria-expanded={privacyOpen}
        >
          隐私说明 <span className={privacyOpen ? "is-open" : ""} aria-hidden="true">⌄</span>
        </button>
      </div>

      {/* 隐私面板（折叠） */}
      {privacyOpen && (
        <div className="knowledge-privacy-panel glass">
          <p><strong>本地处理：</strong>文件复制、解析、稳定切片和 BGE-M3 索引均在本机完成，不扫描原目录、不把正文发往远程向量服务。</p>
          <p><strong>自然对话：</strong>若你使用在线聊天模型并命中知识库，本轮需要引用的少量片段会随问题发送给模型；整份文件不会因此上传。</p>
          <div className="knowledge-principles">
            {PRINCIPLES.map((principle) => (
              <div key={principle.title}><strong>{principle.title}</strong><span>{principle.desc}</span></div>
            ))}
          </div>
        </div>
      )}

      {/* 召回模式 */}
      {recallSettings && (
        <section className="knowledge-recall-mode glass" aria-labelledby="knowledge-recall-mode-title">
          <div>
            <div className="knowledge-eyebrow">RECALL MODE</div>
            <strong id="knowledge-recall-mode-title">对话中的知识召回</strong>
            <p>{recallModeDescription(recallSettings.mode)}</p>
          </div>
          <div className="knowledge-recall-mode-options" role="radiogroup" aria-label="知识召回模式">
            {(["off", "explicit", "smart"] as const).map((mode) => (
              <button key={mode} role="radio" aria-checked={recallSettings.mode === mode}
                className={recallSettings.mode === mode ? "is-active" : ""}
                disabled={actionBusy === "recall-mode"}
                onClick={() => void changeRecallMode(mode)}>
                {mode === "off" ? "关闭" : mode === "explicit" ? "明确请求" : "智能召回"}
              </button>
            ))}
          </div>
        </section>
      )}

      {/* 导入确认 */}
      {pending && (
        <section className="knowledge-confirm glass">
          <div className="knowledge-confirm-mark" aria-hidden="true">✓</div>
          <div className="knowledge-confirm-copy">
            <div className="knowledge-confirm-title">导入前确认</div>
            <strong>{pending.name}</strong>
            <p>类型：{pending.type || "由后端检测"} · 大小：{formatBytes(pending.size)}</p>
            <p>数据流向：仅复制到遐蝶本地应用数据目录；解析与 BGE-M3 语义索引均在本机完成。</p>
            <label className="knowledge-sensitive-check">
              <input type="checkbox" checked={sensitive}
                onChange={(event) => setSensitive(event.target.checked)} />
              这是敏感资料（将保持禁止远程处理的标记）
            </label>
          </div>
          <div className="knowledge-confirm-actions">
            <button className="knowledge-primary-button" disabled={importing} onClick={confirmImport}>
              {importing ? "安全保存中…" : "确认导入到本地"}
            </button>
            <button className="knowledge-text-button" disabled={importing} onClick={() => setPending(null)}>取消</button>
          </div>
        </section>
      )}

      {/* 4. 统计条 */}
      <div className="knowledge-summary">
        <div className="knowledge-stat">
          <span className="knowledge-stat-icon" aria-hidden="true">▤</span>
          <div>
            <strong>{documents.length}</strong>
            <small>个文件</small>
          </div>
        </div>
        <i className="knowledge-stat-divider" />
        <div className="knowledge-stat">
          <span className="knowledge-stat-dot is-ok" />
          <div>
            <strong className="is-ok">{indexedCount}</strong>
            <small>已索引</small>
          </div>
        </div>
        <i className="knowledge-stat-divider" />
        <div className="knowledge-stat">
          <span className="knowledge-stat-dot is-danger" />
          <div>
            <strong className="is-danger">{failedCount}</strong>
            <small>失败</small>
          </div>
        </div>
        <i className="knowledge-stat-divider" />
        <div className="knowledge-stat">
          <span className="knowledge-stat-dot is-warn" />
          <div>
            <strong className="is-warn">{waitingCount}</strong>
            <small>待索引</small>
          </div>
        </div>
      </div>

      {/* 5. 来源标签 */}
      <div className="knowledge-tabs">
        <div className="knowledge-tabs-list">
          <button className={activeSource === "local" ? "is-active" : ""} onClick={() => setActiveSource("local")}>
            本地文件
          </button>
          <button className="knowledge-tab-disabled" disabled title="即将上线">
            网页书签 <small>即将上线</small>
          </button>
        </div>
        <div className={`knowledge-engine-pill ${embeddingStatus?.available ? "is-ready" : ""}`}>
          <i />
          {embeddingStatus?.available
            ? `本地 BGE-M3 已就绪 · ${embeddingStatus.dimension} 维 · 不上传正文`
            : "本地 BGE-M3 未就绪 · 自动使用全文检索"}
        </div>
      </div>

      {/* 审计区 */}
      {audits !== null && (
        <div className="glass knowledge-audits">
          <div className="card-title">最近检索审计（不保存查询正文）</div>
          {auditLifecycle && <div className="knowledge-shadow-summary">
            <span>判断保留 {auditLifecycle.recall_decisions_days} 天</span>
            <span>终态授权 {auditLifecycle.terminal_grants_days} 天</span>
            <span>检索元数据 {auditLifecycle.retrieval_metadata_days} 天</span>
            <span>引用随消息保留</span>
            <span>已隔离知识候选 {auditLifecycle.counts.knowledge_candidates_isolated || 0} 条</span>
          </div>}
          {audits.length === 0 ? <div className="sub">还没有知识检索记录</div> : audits.map((audit) => (
            <div className="knowledge-audit-row" key={audit.id}>
              <span>{new Date(audit.created_at * 1000).toLocaleString("zh-CN")}</span>
              <span>{audit.candidate_count ? `${audit.injected_count}/${audit.candidate_count} 条注入` : "没有找到资料"}</span>
              <span>知识 {audit.knowledge_tokens}/{audit.knowledge_token_budget} token</span>
              <span>指纹 {audit.query_fingerprint}</span>
              <span>{audit.search_protocol_version}{audit.audit_state === "minimized" ? " · 已最小化" : ""}</span>
              {!audit.session_available && <span className="danger-text">原会话已删除</span>}
            </div>
          ))}
        </div>
      )}

      {recallDecisions !== null && (
        <div className="glass knowledge-audits knowledge-shadow-audits">
          <div className="card-title">
            {recallSettings?.mode === "smart"
              ? "知识召回判断（仅 high 会实际影响回答）"
              : "影子召回判断（不会改变回答或发送资料）"}
          </div>
          {recallStats && (
            <div className="knowledge-shadow-summary">
              <span>{recallStats.sample_count} 条样本</span>
              <span>{Math.round(recallStats.action_rates.skip * 100)}% 跳过</span>
              <span>{Math.round(recallStats.action_rates.retrieve * 100)}% 召回判断</span>
              <span>{Math.round(recallStats.action_rates.ask * 100)}% 需要确认</span>
              <span>P90 {recallStats.latency_ms.p90} ms</span>
              <span>向量可用 {Math.round(recallStats.vector_available_rate * 100)}%</span>
            </div>
          )}
          {recallDecisions.length === 0 ? <div className="sub">还没有影子判断记录</div> : recallDecisions.map((decision) => (
            <div className="knowledge-audit-row knowledge-shadow-row" key={decision.id}>
              <span>{new Date(decision.created_at * 1000).toLocaleString("zh-CN")}</span>
              <span className={`shadow-action action-${decision.action}`}>
                {recallDecisionLabel(decision)}
              </span>
              <span>{decision.reason_code} · {decision.confidence_band}</span>
              <span>{decision.candidate_count}/{decision.eligible_count} 候选 · {decision.retrieval_mode}</span>
              <span>{decision.latency_ms} ms · 指纹 {decision.query_fingerprint}</span>
            </div>
          ))}
        </div>
      )}

      {/* 6. 文件列表 / 网格 */}
      {documents.length === 0 ? (
        <div className="knowledge-empty">
          <span aria-hidden="true">□</span>
          <strong>没有符合当前条件的知识文档</strong>
          <small>导入一份资料后，解析与索引状态会显示在这里。</small>
        </div>
      ) : groupedDocuments.map((group) => {
        const collapsed = collapsedGroups.has(group.id);
        return (
          <section className={`knowledge-group glass ${collapsed ? "is-collapsed" : ""}`} key={group.id}>
            <div className="knowledge-group-heading" onClick={() => toggleGroup(group.id)}>
              <div className="knowledge-group-title">
                <span className={`knowledge-folder-chevron ${collapsed ? "is-collapsed" : ""}`} aria-hidden="true">▾</span>
                <span className="knowledge-folder-icon" aria-hidden="true">◇</span>
                <strong>{group.name}</strong>
                <small>{group.documents.length} 个文件</small>
              </div>
              {group.collection && <label className="knowledge-group-policy" onClick={(event) => event.stopPropagation()}>
                <span>新资料默认</span>
                <select value={group.collection.default_transmission_policy}
                  disabled={actionBusy === `collection-policy:${group.id}`}
                  onChange={(event) => changeCollectionPolicy(
                    group.collection!,
                    event.target.value as api.KnowledgeDocument["transmission_policy"],
                  )}>
                  <option value="ask_each_time">用之前问我</option>
                  <option value="local_only">只在本机用</option>
                  <option value="remote_allowed">可以分享给遐蝶</option>
                </select>
              </label>}
            </div>
            <div className="knowledge-folder-content">
              {viewMode === "list" ? (
                <div className="knowledge-file-list">
                  {group.documents.map((document) => (
                    <FileRow
                      key={document.id}
                      document={document}
                      groupName={group.name}
                      editingTags={editingTags}
                      tagDraft={tagDraft}
                      setTagDraft={setTagDraft}
                      setEditingTags={setEditingTags}
                      actionBusy={actionBusy}
                      runDetails={runDetails}
                      embeddingAvailable={!!embeddingStatus?.available}
                      statusClass={statusClassOf(document)}
                      statusLabel={statusLabelOf(document)}
                      onToggleRun={toggleRun}
                      onCancelRun={cancelRun}
                      onSaveTags={saveTags}
                      onReindex={reindexDocument}
                      onBuildEmbedding={buildEmbedding}
                      onUpdatePolicy={updateTransmissionPolicy}
                      onDelete={deleteDocument}
                      onRetryDelete={retryDelete}
                    />
                  ))}
                </div>
              ) : (
                <div className="knowledge-file-grid">
                  {group.documents.map((document) => (
                    <FileCard
                      key={document.id}
                      document={document}
                      groupName={group.name}
                      editingTags={editingTags}
                      tagDraft={tagDraft}
                      setTagDraft={setTagDraft}
                      setEditingTags={setEditingTags}
                      actionBusy={actionBusy}
                      runDetails={runDetails}
                      embeddingAvailable={!!embeddingStatus?.available}
                      statusClass={statusClassOf(document)}
                      statusLabel={statusLabelOf(document)}
                      onToggleRun={toggleRun}
                      onCancelRun={cancelRun}
                      onSaveTags={saveTags}
                      onReindex={reindexDocument}
                      onBuildEmbedding={buildEmbedding}
                      onUpdatePolicy={updateTransmissionPolicy}
                      onDelete={deleteDocument}
                      onRetryDelete={retryDelete}
                    />
                  ))}
                </div>
              )}
            </div>
          </section>
        );
      })}

      {/* 7. 隐私说明（折叠） */}
      <div className={`knowledge-privacy-notice glass ${privacyOpen ? "is-open" : ""}`}>
        <div className="knowledge-privacy-notice-head" onClick={() => setPrivacyOpen((current) => !current)}>
          <span aria-hidden="true">▣</span>
          隐私与数据流向
          <span className={`knowledge-privacy-chevron ${privacyOpen ? "is-open" : ""}`} aria-hidden="true">⌄</span>
        </div>
        <div className="knowledge-privacy-notice-body">
          <ol>
            <li>你的文件仅在本地处理，不会上传到云端</li>
            <li>遐蝶仅在对话中被提问时才会访问文件内容</li>
            <li>你可以随时删除文件，所有索引数据将同步清除</li>
            <li>文件内容不会被用于模型训练</li>
            <li>删除知识文档只会清理遐蝶应用内副本、切片与索引；应用外的原文件或备份不会同步删除</li>
          </ol>
          <div className="knowledge-row-actions">
            <button className="btn ghost" onClick={downloadManifest}>导出元数据清单</button>
            <button className="btn danger" disabled={actionBusy === "clear-all"}
              onClick={clearAllKnowledge}>完整清除知识库</button>
          </div>
        </div>
      </div>
    </div>
  );
}

interface FileRowProps {
  document: api.KnowledgeDocument;
  groupName: string;
  editingTags: string | null;
  tagDraft: string;
  setTagDraft: (value: string) => void;
  setEditingTags: (value: string | null) => void;
  actionBusy: string | null;
  runDetails: Record<string, api.KnowledgeImportRun>;
  embeddingAvailable: boolean;
  statusClass: string;
  statusLabel: string;
  onToggleRun: (document: api.KnowledgeDocument) => void;
  onCancelRun: (document: api.KnowledgeDocument) => void;
  onSaveTags: (document: api.KnowledgeDocument) => void;
  onReindex: (document: api.KnowledgeDocument) => void;
  onBuildEmbedding: (document: api.KnowledgeDocument) => void;
  onUpdatePolicy: (
    document: api.KnowledgeDocument,
    policy: api.KnowledgeDocument["transmission_policy"],
  ) => void;
  onDelete: (document: api.KnowledgeDocument) => void;
  onRetryDelete: (document: api.KnowledgeDocument) => void;
}

function fileIconLabel(document: api.KnowledgeDocument): string {
  return document.extension.toUpperCase().replace(".", "");
}

function FileRow(props: FileRowProps) {
  const { document, editingTags, tagDraft, setTagDraft, setEditingTags, actionBusy, runDetails, embeddingAvailable, statusClass, statusLabel } = props;
  const ext = document.extension.toLowerCase().replace(".", "");
  return (
    <article className="knowledge-file-row">
      <div className={`knowledge-file-icon type-${ext}`}>{fileIconLabel(document)}</div>
      <div className="knowledge-file-main">
        <div className="knowledge-file-titleline">
          <strong title={document.original_name}>{document.original_name}</strong>
          {document.sensitivity === "sensitive" && <span className="knowledge-sensitive-pill">敏感 · 仅本地</span>}
        </div>
        <div className="knowledge-file-meta">
          <span>{formatBytes(document.size_bytes)}</span>
          <span>·</span>
          <span>{new Date(document.created_at * 1000).toLocaleDateString("zh-CN")}</span>
          <span>·</span>
          <span className="knowledge-file-citation">指纹 {document.content_sha256.slice(0, 10)}</span>
        </div>
        <div className={`knowledge-policy-label policy-${document.transmission_policy}`}>
          {transmissionPolicyLabel(document.transmission_policy)}
        </div>
        {!!document.tags.length && <div className="knowledge-tags">
          {document.tags.map((tag) => <span className="chip" key={tag}>{tag}</span>)}
        </div>}
      </div>
      <span className={`knowledge-status-pill ${statusClass}`}>
        <i />{statusLabel}
      </span>
      <details className="knowledge-file-controls">
        <summary aria-label={`管理 ${document.original_name}`}>•••</summary>
        <div className="knowledge-file-details-body">
          {document.error_code && <div className="danger-text">错误代码：{document.error_code}</div>}
          {document.parsed_at && !document.chunked_at && (
            <p>本地解析：{document.parse_line_count} 行 · {document.parse_heading_count} 个标题 ·
              {document.parse_char_count} 字符；尚未切片或索引</p>
          )}
          {document.chunked_at && (
            <p>稳定切片：{document.chunk_count} 段；保留标题、段落、行号与字符范围；
              {document.indexed_at ? "本地索引已就绪" : "尚未索引"}</p>
          )}
          {document.status === "indexed" && (
            <p>语义索引：{document.embedding_indexed_at
              ? `本地 BGE-M3 已就绪 · ${document.embedding_dimension} 维 · ${document.latest_embedding?.vector_count || document.chunk_count} 条向量`
              : ["queued", "running"].includes(document.latest_embedding?.status || "")
                ? "正在本地建立，期间继续使用全文检索"
                : document.embedding_error_code
                  ? `建立失败（${document.embedding_error_code}），已自动退回全文检索`
                  : "尚未建立，当前使用全文检索"}</p>
          )}
          {editingTags === document.id && (
            <div className="knowledge-tag-editor">
              <input value={tagDraft} maxLength={410} onChange={(event) => setTagDraft(event.target.value)}
                placeholder="用逗号分隔，最多 10 项，每项 40 字符" />
              <button className="btn" disabled={actionBusy === `tags:${document.id}`}
                onClick={() => props.onSaveTags(document)}>保存</button>
              <button className="btn ghost" onClick={() => setEditingTags(null)}>取消</button>
            </div>
          )}
          {runDetails[document.id] && <div className="knowledge-run-events">
            {runDetails[document.id].events?.map((event) => (
              <p key={event.id}>{eventLabel(event.action)} · {stageLabel(event.stage)} ·
                {new Date(event.created_at * 1000).toLocaleString("zh-CN")}</p>
            ))}
          </div>}
          <details className="knowledge-details">
            <summary>来源详情</summary>
            <div>文档 ID：{document.id}</div>
            <div>Collection：{props.groupName}</div>
            <div>完整内容指纹：{document.content_sha256}</div>
            <div>解析器：{document.parser_version || "尚未解析"} · 切片器：{document.chunker_version || "尚未切片"}</div>
            <div>索引版本：{document.index_version || "尚未索引"} · 导入时间：{new Date(document.created_at * 1000).toLocaleString("zh-CN")}</div>
            <div>语义版本：{document.embedding_version || "尚未建立"}</div>
            <div>最近召回：{document.last_recalled_at
              ? new Date(document.last_recalled_at * 1000).toLocaleString("zh-CN") : "尚未召回"}
              · 累计 {document.recall_count || 0} 次 · 引用 {document.citation_count || 0} 条</div>
            <div>遐蝶能用这些资料吗：{transmissionPolicyLabel(document.transmission_policy)} · revision {document.policy_revision}</div>
          </details>
          <div className="knowledge-row-actions">
            <label className="knowledge-policy-control">
              <span>遐蝶能用这些资料吗</span>
              <select value={document.transmission_policy}
                disabled={actionBusy === `policy:${document.id}`}
                onChange={(event) => props.onUpdatePolicy(
                  document,
                  event.target.value as api.KnowledgeDocument["transmission_policy"],
                )}>
                <option value="ask_each_time">用之前问我</option>
                <option value="local_only">只在本机用</option>
                {document.sensitivity !== "sensitive" && <option value="remote_allowed">可以分享给遐蝶</option>}
              </select>
            </label>
            {document.latest_run && <button className="btn ghost" onClick={() => props.onToggleRun(document)}>
              {runDetails[document.id] ? "收起详情" : "进度详情"}
            </button>}
            {document.latest_run && ["queued", "running", "recovery_pending", "cancel_requested"].includes(document.latest_run.status) && (
              <button className="btn ghost" disabled={document.latest_run.status === "cancel_requested"}
                onClick={() => props.onCancelRun(document)}>{document.latest_run.status === "cancel_requested" ? "停止中…" : "停止处理"}</button>
            )}
            {!document.status.startsWith("delete_") && <button className="btn ghost" onClick={() => {
              setEditingTags(document.id); setTagDraft(document.tags.join("，"));
            }}>标签</button>}
            {["indexed", "failed", "cancelled"].includes(document.status) && <button className="btn ghost"
              disabled={actionBusy === `reindex:${document.id}`} onClick={() => props.onReindex(document)}>
              {document.status === "indexed" ? "重建索引" : "重试处理"}</button>}
            {document.status === "indexed" && embeddingAvailable && !document.embedding_indexed_at &&
              !["queued", "running"].includes(document.latest_embedding?.status || "") && <button className="btn ghost"
                disabled={actionBusy === `embedding:${document.id}`} onClick={() => props.onBuildEmbedding(document)}>建立语义索引</button>}
            {!document.status.startsWith("delete_") && <button className="btn danger"
              disabled={actionBusy === `delete:${document.id}`} onClick={() => props.onDelete(document)}>删除</button>}
            {document.status === "delete_failed" && document.latest_deletion && <button className="btn danger"
              disabled={actionBusy === `delete:${document.id}`} onClick={() => props.onRetryDelete(document)}>重试删除</button>}
          </div>
        </div>
      </details>
    </article>
  );
}

function FileCard(props: FileRowProps) {
  const { document, editingTags, tagDraft, setTagDraft, setEditingTags, actionBusy, runDetails, embeddingAvailable, statusClass, statusLabel } = props;
  const ext = document.extension.toLowerCase().replace(".", "");
  return (
    <article className="knowledge-file-card">
      <div className="knowledge-file-card-head">
        <div className={`knowledge-file-icon type-${ext}`}>{fileIconLabel(document)}</div>
        <span className={`knowledge-status-pill ${statusClass}`}><i />{statusLabel}</span>
      </div>
      <strong className="knowledge-file-card-name" title={document.original_name}>{document.original_name}</strong>
      <div className="knowledge-file-card-meta">
        <span>{formatBytes(document.size_bytes)}</span>
        <span>·</span>
        <span>{new Date(document.created_at * 1000).toLocaleDateString("zh-CN")}</span>
      </div>
      <div className={`knowledge-policy-label policy-${document.transmission_policy}`}>
        {transmissionPolicyLabel(document.transmission_policy)}
      </div>
      {!!document.tags.length && <div className="knowledge-tags">
        {document.tags.map((tag) => <span className="chip" key={tag}>{tag}</span>)}
      </div>}
      <details className="knowledge-file-card-controls">
        <summary>管理</summary>
        <div className="knowledge-file-details-body">
          {document.error_code && <div className="danger-text">错误代码：{document.error_code}</div>}
          {document.parsed_at && !document.chunked_at && (
            <p>本地解析：{document.parse_line_count} 行 · {document.parse_heading_count} 个标题 · {document.parse_char_count} 字符</p>
          )}
          {document.chunked_at && (
            <p>稳定切片：{document.chunk_count} 段；{document.indexed_at ? "本地索引已就绪" : "尚未索引"}</p>
          )}
          {document.status === "indexed" && (
            <p>语义索引：{document.embedding_indexed_at
              ? `BGE-M3 ${document.embedding_dimension} 维 · ${document.latest_embedding?.vector_count || document.chunk_count} 向量`
              : "尚未建立，使用全文检索"}</p>
          )}
          {editingTags === document.id && (
            <div className="knowledge-tag-editor">
              <input value={tagDraft} maxLength={410} onChange={(event) => setTagDraft(event.target.value)}
                placeholder="用逗号分隔标签" />
              <button className="btn" disabled={actionBusy === `tags:${document.id}`}
                onClick={() => props.onSaveTags(document)}>保存</button>
              <button className="btn ghost" onClick={() => setEditingTags(null)}>取消</button>
            </div>
          )}
          {runDetails[document.id] && <div className="knowledge-run-events">
            {runDetails[document.id].events?.map((event) => (
              <p key={event.id}>{eventLabel(event.action)} · {new Date(event.created_at * 1000).toLocaleString("zh-CN")}</p>
            ))}
          </div>}
          <details className="knowledge-details">
            <summary>来源详情</summary>
            <div>文档 ID：{document.id}</div>
            <div>指纹：{document.content_sha256}</div>
            <div>导入：{new Date(document.created_at * 1000).toLocaleString("zh-CN")}</div>
            <div>索引：{document.index_version || "尚未索引"}</div>
            <div>召回 {document.recall_count || 0} 次 · 引用 {document.citation_count || 0} 条</div>
          </details>
          <div className="knowledge-row-actions">
            <label className="knowledge-policy-control">
              <span>遐蝶能用这些资料吗</span>
              <select value={document.transmission_policy}
                disabled={actionBusy === `policy:${document.id}`}
                onChange={(event) => props.onUpdatePolicy(
                  document,
                  event.target.value as api.KnowledgeDocument["transmission_policy"],
                )}>
                <option value="ask_each_time">用之前问我</option>
                <option value="local_only">只在本机用</option>
                {document.sensitivity !== "sensitive" && <option value="remote_allowed">可以分享给遐蝶</option>}
              </select>
            </label>
            {document.latest_run && <button className="btn ghost" onClick={() => props.onToggleRun(document)}>
              {runDetails[document.id] ? "收起" : "进度"}
            </button>}
            {document.latest_run && ["queued", "running", "recovery_pending", "cancel_requested"].includes(document.latest_run.status) && (
              <button className="btn ghost" disabled={document.latest_run.status === "cancel_requested"}
                onClick={() => props.onCancelRun(document)}>停止</button>
            )}
            {!document.status.startsWith("delete_") && <button className="btn ghost" onClick={() => {
              setEditingTags(document.id); setTagDraft(document.tags.join("，"));
            }}>标签</button>}
            {["indexed", "failed", "cancelled"].includes(document.status) && <button className="btn ghost"
              disabled={actionBusy === `reindex:${document.id}`} onClick={() => props.onReindex(document)}>
              {document.status === "indexed" ? "重建" : "重试"}</button>}
            {document.status === "indexed" && embeddingAvailable && !document.embedding_indexed_at &&
              !["queued", "running"].includes(document.latest_embedding?.status || "") && <button className="btn ghost"
                disabled={actionBusy === `embedding:${document.id}`} onClick={() => props.onBuildEmbedding(document)}>语义索引</button>}
            {!document.status.startsWith("delete_") && <button className="btn danger"
              disabled={actionBusy === `delete:${document.id}`} onClick={() => props.onDelete(document)}>删除</button>}
            {document.status === "delete_failed" && document.latest_deletion && <button className="btn danger"
              disabled={actionBusy === `delete:${document.id}`} onClick={() => props.onRetryDelete(document)}>重试删除</button>}
          </div>
        </div>
      </details>
    </article>
  );
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}

function recallModeDescription(mode: api.KnowledgeRecallSettings["mode"]): string {
  if (mode === "off") return "完全不查询知识库，即使消息里明确提到文档也不会召回。";
  if (mode === "smart") return "明确请求照常处理；自然对话仅在高置信命中时使用，发送给在线模型仍遵守你设置的偏好。";
  return "默认模式：只有你明确提到知识库、资料或文档时才会查询；后台影子判断不会改变回答。";
}

function recallDecisionLabel(decision: api.KnowledgeRecallDecision): string {
  if (decision.shadow) {
    return decision.action === "retrieve" ? "建议召回" : decision.action === "ask" ? "建议确认" : "跳过";
  }
  if (decision.injected_count > 0) return decision.action === "ask" ? "确认后召回" : "已召回";
  return decision.action === "ask" ? "本轮未使用" : "跳过";
}

function documentStatus(document: api.KnowledgeDocument): string {
  if (document.status === "indexed" && document.indexed_at) return "已索引";
  if (document.latest_run?.status === "running" && document.latest_run.current_stage === "indexing") {
    return `索引中 ${document.latest_run.progress}%`;
  }
  if (document.chunked_at && document.latest_run?.current_stage === "indexing") {
    return "切片完成";
  }
  if (document.latest_run?.status === "running" && document.latest_run.current_stage === "chunking") {
    return `切片中 ${document.latest_run.progress}%`;
  }
  if (document.parsed_at && document.latest_run?.current_stage === "chunking") {
    return "解析完成";
  }
  if (document.latest_run?.status === "running") return `解析中 ${document.latest_run.progress}%`;
  if (document.latest_run?.status === "recovery_pending") return "等待重试";
  return ({
    staged: "等待入队", queued: "待索引", parsing: "解析中",
    indexed: "已索引", failed: "索引失败", cancelled: "已取消",
    delete_pending: "删除中", delete_failed: "删除失败",
  })[document.status];
}

function documentStatusClass(document: api.KnowledgeDocument): string {
  if (document.status === "indexed") return "is-indexed";
  if (["failed", "delete_failed"].includes(document.status)) return "is-failed";
  if (["cancelled"].includes(document.status)) return "is-cancelled";
  return "is-processing";
}

function transmissionPolicyLabel(policy: api.KnowledgeDocument["transmission_policy"]): string {
  return ({
    remote_allowed: "可以分享给遐蝶",
    ask_each_time: "用之前问我",
    local_only: "只在本机用",
  })[policy];
}

function eventLabel(action: string): string {
  return ({ admitted: "安全接收", parsing_started: "开始解析", parsing_completed: "解析完成",
    chunking_started: "开始切片", chunking_completed: "切片完成",
    indexing_started: "开始索引", indexing_completed: "索引完成",
    retry_scheduled: "等待重试", recovery_scheduled: "中断恢复", cancel_requested: "请求停止",
    cancelled: "已停止", failed: "解析失败", reindex_requested: "请求重建索引",
    delete_requested: "请求删除" } as Record<string, string>)[action] || "任务记录";
}

function stageLabel(stage: string): string {
  return ({ validation: "校验", copy: "本地副本", parsing: "解析", chunking: "等待切片",
    indexing: "索引", finalizing: "收尾" } as Record<string, string>)[stage] || stage;
}
