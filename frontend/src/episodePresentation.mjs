const SUMMARY_PRESENTATION = Object.freeze({
  model_validated: { label: "来源校验摘要", tone: "verified", detail: "摘要逐条通过来源事实校验" },
  extractive_fallback: { label: "原文整理", tone: "extractive", detail: "模型不可用或未通过校验，使用来源原文整理" },
  user_edited: { label: "人工纠错", tone: "corrected", detail: "标题或摘要由用户纠正" },
  legacy_rule: { label: "旧版经历", tone: "legacy", detail: "创建于来源校验协议启用之前" },
});

export function episodeSummaryPresentation(status) {
  return SUMMARY_PRESENTATION[status] ?? {
    label: "校验状态未知", tone: "legacy", detail: "当前版本无法识别这条经历的摘要状态",
  };
}

export function shortSourceHash(value) {
  const normalized = String(value ?? "").trim().toLowerCase();
  return /^[a-f0-9]{64}$/.test(normalized) ? normalized.slice(0, 12) : "未记录";
}
