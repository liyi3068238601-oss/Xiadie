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
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = () => api.listKnowledgeDocuments().then(setDocuments);
  useEffect(() => { refresh().catch(() => toast("知识文档列表加载失败")); }, []);

  function choose(file: File) {
    const extension = file.name.toLowerCase().split(".").pop();
    if (!extension || !["txt", "md"].includes(extension)) {
      toast("目前只支持 UTF-8 的 TXT 和 Markdown 文件");
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

  return (
    <div className="page">
      <h1>文件与知识</h1>
      <div className="sub">
        把外部资料交给遐蝶作为可引用知识。当前支持安全接收 TXT/Markdown；解析、索引和对话引用仍在施工。
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
          支持 UTF-8 TXT、Markdown · 单文件不超过 10 MiB
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
          accept=".txt,.md,text/plain,text/markdown"
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
            数据流向：仅复制到遐蝶本地应用数据目录；本阶段不调用远程模型、不生成 Embedding、不扫描原目录。
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
      <div className="section-label">已导入知识条目</div>
      {documents.length === 0 ? (
        <div className="empty">还没有知识条目</div>
      ) : documents.map((document) => (
        <div className="list-row" key={document.id}>
          <span className="chip">{document.extension.toUpperCase().replace(".", "")}</span>
          <div style={{ flex: 1 }}>
            <strong>{document.original_name}</strong>
            <div className="sub">{formatBytes(document.size_bytes)} · {documentStatus(document.status)} ·
              指纹 {document.content_sha256.slice(0, 10)}</div>
          </div>
          {document.sensitivity === "sensitive" && <span className="chip danger">敏感 · 仅本地</span>}
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

function documentStatus(status: api.KnowledgeDocument["status"]): string {
  return ({
    staged: "等待入队", queued: "已安全保存 · 等待解析", parsing: "解析中",
    indexed: "可检索", failed: "处理失败", cancelled: "已取消",
    delete_pending: "删除中", delete_failed: "删除待重试",
  })[status];
}
