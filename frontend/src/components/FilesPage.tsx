import { useRef, useState } from "react";
import { toast } from "./../store";

// 需求 6.6：文件与知识页。后端索引/检索尚未落地，这里是阶段占位页，
// 但需要体现最终形态与隐私原则，不能是空白。

const DEV_HINT = "文件导入功能开发中：将支持摘要、标签、引用来源";

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
  const inputRef = useRef<HTMLInputElement>(null);

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    toast(DEV_HINT);
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
    if (f) toast(`已选择「${f.name}」——${DEV_HINT}`);
    // 清空以便再次选择同一文件也能触发
    e.target.value = "";
  }

  return (
    <div className="page">
      <h1>文件与知识</h1>
      <div className="sub">
        把资料交给遐蝶，让她记住你的上下文。索引与检索能力属于后续阶段，当前为占位预览。
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
          支持文档、笔记等；导入后将自动生成摘要与标签（开发中）
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
          onChange={onPick}
          style={{ display: "none" }}
        />
      </div>

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
      <div className="empty">
        还没有知识条目
        <div style={{ marginTop: 6, fontSize: 12 }}>
          文件索引、语义检索与引用来源属于后续阶段能力，敬请期待。
        </div>
      </div>
    </div>
  );
}
