import { useEffect, useRef, useState } from "react";
import * as api from "./../api";
import { toast } from "./../store";

const MAX_FILE_BYTES = 10 * 1024 * 1024;

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
  const [embeddingStatus, setEmbeddingStatus] = useState<api.KnowledgeEmbeddingStatus | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = () => api.listKnowledgeDocuments({
    collection_id: collectionFilter || undefined,
    status: statusFilter || undefined,
    query: search.trim() || undefined,
  }).then(setDocuments);
  useEffect(() => {
    api.listKnowledgeCollections().then(setCollections).catch(() => toast("知识库集合加载失败"));
    api.getKnowledgeEmbeddingStatus().then(setEmbeddingStatus).catch(() => {});
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
      toast(error.message || "文件导入失败");
    } finally {
      setImporting(false);
    }
  }

  async function showRun(document: api.KnowledgeDocument) {
    const runId = document.latest_run?.id;
    if (!runId) return;
    try {
      const run = await api.getKnowledgeImportRun(runId);
      setRunDetails((current) => ({ ...current, [document.id]: run }));
    } catch (error: any) {
      toast(error.message || "任务详情加载失败");
    }
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
      return;
    }
    try {
      setAudits(await api.listKnowledgeRetrievals());
    } catch (error: any) {
      toast(error.message || "检索记录加载失败");
    }
  }

  return (
    <div className="page">
      <h1>文件与知识</h1>
      <div className="sub">
        把外部资料交给遐蝶作为可引用知识。支持 TXT、Markdown、PDF、DOCX 的本地解析、稳定切片与混合检索；PDF 引用保留真实页码，扫描图片暂不做 OCR。
      </div>
      <div className="sub" style={{ marginBottom: 16 }}>
        语义索引：{embeddingStatus?.available
          ? `本地 BGE-M3 已就绪（${embeddingStatus.dimension} 维，不上传正文）`
          : "本地 BGE-M3 未就绪，将自动使用全文检索"}
      </div>

      {/* 拖拽 / 选择区 */}
      <div
        className="glass"
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onClick={() => inputRef.current?.click()}
        style={{
          padding: "40px 24px",
          textAlign: "center",
          cursor: "pointer",
          border: `2px dashed ${
            dragging ? "var(--glass-border-lit)" : "var(--glass-border)"
          }`,
          background: dragging
            ? "rgba(124, 92, 255, 0.12)"
            : "var(--glass)",
          boxShadow: dragging ? "var(--glow)" : undefined,
          transition: "all 0.15s ease",
          marginBottom: 24,
        }}
      >
        <div style={{ fontSize: 30, marginBottom: 10, opacity: 0.85 }}>📄</div>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>
          把文件拖到这里，或点击选择
        </div>
        <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
          支持 UTF-8 TXT、Markdown、PDF、DOCX · 单文件不超过 10 MiB
        </div>
        <div className="row" style={{ justifyContent: "center", marginTop: 16 }}>
          <button
            className="btn ghost"
            onClick={(e) => {
              e.stopPropagation();
              inputRef.current?.click();
            }}
          >
            选择文件
          </button>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".txt,.md,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          onChange={onPick}
          style={{ display: "none" }}
        />
      </div>

      {pending && (
        <div className="glass" style={{ padding: 18, marginBottom: 24 }}>
          <div className="section-label">导入前确认</div>
          <div style={{ margin: "8px 0", fontWeight: 600 }}>{pending.name}</div>
          <div className="sub">
            类型：{pending.type || "由后端检测"} · 大小：{formatBytes(pending.size)}<br />
            数据流向：仅复制到遐蝶本地应用数据目录；解析与 BGE-M3 语义索引均在本机完成，不扫描原目录、不把正文发往远程向量服务。
          </div>
          <label style={{ display: "flex", gap: 8, alignItems: "center", margin: "14px 0" }}>
            <input type="checkbox" checked={sensitive} onChange={(event) => setSensitive(event.target.checked)} />
            这是敏感资料（将保持禁止远程处理的标记）
          </label>
          <div className="row">
            <button className="btn" disabled={importing} onClick={confirmImport}>
              {importing ? "安全保存中…" : "确认导入到本地"}
            </button>
            <button className="btn ghost" disabled={importing} onClick={() => setPending(null)}>取消</button>
          </div>
        </div>
      )}

      {/* 知识库原则 */}
      <div className="section-label">知识库原则（需求 6.6）</div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
          gap: 10,
          marginBottom: 24,
        }}
      >
        {PRINCIPLES.map((p) => (
          <div key={p.title} className="card memory">
            <div className="card-title">{p.title}</div>
            <div className="card-hint">{p.desc}</div>
          </div>
        ))}
      </div>

      {/* 已导入知识条目 */}
      <div className="section-label">知识文档管理</div>
      <div className="glass knowledge-toolbar">
        <input value={search} onChange={(event) => setSearch(event.target.value)}
          placeholder="按文件名搜索（同名文件用指纹区分）" maxLength={120} />
        <select value={collectionFilter} onChange={(event) => setCollectionFilter(event.target.value)}>
          <option value="">全部 collection</option>
          {collections.map((collection) => (
            <option key={collection.id} value={collection.id}>{collection.name}</option>
          ))}
        </select>
        <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
          <option value="">全部状态</option>
          <option value="indexed">已索引</option>
          <option value="queued">等待处理</option>
          <option value="parsing">处理中</option>
          <option value="failed">处理失败</option>
          <option value="cancelled">已取消</option>
          <option value="delete_pending">删除中</option>
          <option value="delete_failed">删除失败</option>
        </select>
        <button className="btn ghost" onClick={toggleAudits}>
          {audits === null ? "查看检索记录" : "收起检索记录"}
        </button>
      </div>
      {audits !== null && (
        <div className="glass knowledge-audits">
          <div className="card-title">最近检索审计（不保存查询正文）</div>
          {audits.length === 0 ? <div className="sub">还没有知识检索记录</div> : audits.map((audit) => (
            <div className="knowledge-audit-row" key={audit.id}>
              <span>{new Date(audit.created_at * 1000).toLocaleString("zh-CN")}</span>
              <span>{audit.candidate_count ? `${audit.injected_count}/${audit.candidate_count} 条注入` : "没有找到资料"}</span>
              <span>知识 {audit.knowledge_tokens}/{audit.knowledge_token_budget} token</span>
              <span>指纹 {audit.query_fingerprint}</span>
              {!audit.session_available && <span className="danger-text">原会话已删除</span>}
            </div>
          ))}
        </div>
      )}
      {documents.length === 0 ? (
        <div className="empty">没有符合当前条件的知识文档</div>
      ) : documents.map((document) => (
        <div className="list-row knowledge-document-row" key={document.id}>
          <span className="chip">{document.extension.toUpperCase().replace(".", "")}</span>
          <div style={{ flex: 1 }}>
            <strong>{document.original_name}</strong>
            <div className="sub">{formatBytes(document.size_bytes)} · {documentStatus(document)} ·
              指纹 {document.content_sha256.slice(0, 10)}</div>
            {document.error_code && <div className="sub danger-text">错误代码：{document.error_code}</div>}
            {!!document.tags.length && <div className="knowledge-tags">
              {document.tags.map((tag) => <span className="chip" key={tag}>{tag}</span>)}
            </div>}
            {editingTags === document.id && (
              <div className="knowledge-tag-editor">
                <input value={tagDraft} maxLength={410} onChange={(event) => setTagDraft(event.target.value)}
                  placeholder="用逗号分隔，最多 10 项，每项 40 字符" />
                <button className="btn" disabled={actionBusy === `tags:${document.id}`}
                  onClick={() => saveTags(document)}>保存</button>
                <button className="btn ghost" onClick={() => setEditingTags(null)}>取消</button>
              </div>
            )}
            {document.parsed_at && !document.chunked_at && (
              <div className="sub">本地解析：{document.parse_line_count} 行 · {document.parse_heading_count} 个标题 ·
                {document.parse_char_count} 字符；尚未切片或索引</div>
            )}
            {document.chunked_at && (
              <div className="sub">稳定切片：{document.chunk_count} 段；保留标题、段落、行号与字符范围；
                {document.indexed_at ? "本地索引已就绪" : "尚未索引"}</div>
            )}
            {document.status === "indexed" && (
              <div className="sub">
                语义索引：{document.embedding_indexed_at
                  ? `本地 BGE-M3 已就绪 · ${document.embedding_dimension} 维 · ${document.latest_embedding?.vector_count || document.chunk_count} 条向量`
                  : ["queued", "running"].includes(document.latest_embedding?.status || "")
                    ? "正在本地建立，期间继续使用全文检索"
                    : document.embedding_error_code
                      ? `建立失败（${document.embedding_error_code}），已自动退回全文检索`
                      : "尚未建立，当前使用全文检索"}
              </div>
            )}
            {runDetails[document.id] && (
              <div style={{ marginTop: 8 }}>
                {runDetails[document.id].events?.map((event) => (
                  <div className="sub" key={event.id}>
                    {eventLabel(event.action)} · {stageLabel(event.stage)} ·
                    {new Date(event.created_at * 1000).toLocaleString("zh-CN")}
                  </div>
                ))}
              </div>
            )}
            <details className="knowledge-details">
              <summary>来源详情</summary>
              <div>文档 ID：{document.id}</div>
              <div>Collection：{collections.find((item) => item.id === document.collection_id)?.name || document.collection_id}</div>
              <div>完整内容指纹：{document.content_sha256}</div>
              <div>解析器：{document.parser_version || "尚未解析"} · 切片器：{document.chunker_version || "尚未切片"}</div>
              <div>索引版本：{document.index_version || "尚未索引"} · 导入时间：{new Date(document.created_at * 1000).toLocaleString("zh-CN")}</div>
              <div>语义版本：{document.embedding_version || "尚未建立"}</div>
            </details>
          </div>
          {document.sensitivity === "sensitive" && <span className="chip danger">敏感 · 仅本地</span>}
          {document.latest_run && <button className="btn ghost" onClick={() => showRun(document)}>进度详情</button>}
          {document.latest_run && ["queued", "running", "recovery_pending", "cancel_requested"].includes(
            document.latest_run.status
          ) && (
            <button className="btn ghost" disabled={document.latest_run.status === "cancel_requested"}
              onClick={() => cancelRun(document)}>
              {document.latest_run.status === "cancel_requested" ? "停止中…" : "停止处理"}
            </button>
          )}
          {!document.status.startsWith("delete_") && (
            <button className="btn ghost" onClick={() => {
              setEditingTags(document.id); setTagDraft(document.tags.join("，"));
            }}>标签</button>
          )}
          {["indexed", "failed", "cancelled"].includes(document.status) && (
            <button className="btn ghost" disabled={actionBusy === `reindex:${document.id}`}
              onClick={() => reindexDocument(document)}>
              {document.status === "indexed" ? "重建索引" : "重试处理"}
            </button>
          )}
          {document.status === "indexed" && embeddingStatus?.available && !document.embedding_indexed_at &&
            !["queued", "running"].includes(document.latest_embedding?.status || "") && (
            <button className="btn ghost" disabled={actionBusy === `embedding:${document.id}`}
              onClick={() => buildEmbedding(document)}>建立语义索引</button>
          )}
          {!document.status.startsWith("delete_") && (
            <button className="btn danger" disabled={actionBusy === `delete:${document.id}`}
              onClick={() => deleteDocument(document)}>删除</button>
          )}
          {document.status === "delete_failed" && document.latest_deletion && (
            <button className="btn danger" disabled={actionBusy === `delete:${document.id}`}
              onClick={() => retryDelete(document)}>重试删除</button>
          )}
        </div>
      ))}
    </div>
  );
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}

function documentStatus(document: api.KnowledgeDocument): string {
  if (document.status === "indexed" && document.indexed_at) return "已索引 · 可检索";
  if (document.latest_run?.status === "running" && document.latest_run.current_stage === "indexing") {
    return `索引中 ${document.latest_run.progress}%`;
  }
  if (document.chunked_at && document.latest_run?.current_stage === "indexing") {
    return "切片完成 · 等待索引";
  }
  if (document.latest_run?.status === "running" && document.latest_run.current_stage === "chunking") {
    return `切片中 ${document.latest_run.progress}%`;
  }
  if (document.parsed_at && document.latest_run?.current_stage === "chunking") {
    return "解析完成 · 等待切片";
  }
  if (document.latest_run?.status === "running") return `解析中 ${document.latest_run.progress}%`;
  if (document.latest_run?.status === "recovery_pending") return "处理暂停 · 等待重试";
  return ({
    staged: "等待入队", queued: "已安全保存 · 等待解析", parsing: "解析中",
    indexed: "已索引 · 可检索", failed: "处理失败", cancelled: "已取消",
    delete_pending: "删除中", delete_failed: "删除待重试",
  })[document.status];
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
