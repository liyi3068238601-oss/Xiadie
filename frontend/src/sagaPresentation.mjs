import { episodeSummaryPresentation } from "./episodePresentation.mjs";

const STATUS = {
  active: {
    label: "进行中",
    detail: "这个长期故事仍在发展，新经历可能继续加入。",
    tone: "active",
  },
  completed: {
    label: "已完成",
    detail: "来源中有明确的结束证据；出现可信新发展时可以恢复。",
    tone: "completed",
  },
  archived: {
    label: "已归档",
    detail: "故事暂时退出活跃整理，仍保留完整来源和审计记录。",
    tone: "archived",
  },
  tombstone: {
    label: "已删除",
    detail: "这是不可恢复的删除状态，不会被后台自动重建。",
    tone: "tombstone",
  },
};

const EVENT_LABELS = {
  created: "创建长期故事",
  episode_appended: "加入新经历",
  completed: "标记完成",
  reactivated: "恢复进行中",
  archived: "归档",
  tombstoned: "删除",
  content_corrected: "纠正故事内容",
  sources_corrected: "纠正来源归组",
  episodes_removed: "移除错误来源",
  episodes_added: "加入正确来源",
};

export function sagaStatusPresentation(status) {
  return STATUS[status] || {
    label: "状态未知",
    detail: "当前客户端尚不认识这个 Saga 状态，请刷新或升级后再操作。",
    tone: "unknown",
  };
}

export function sagaSummaryPresentation(status) {
  return episodeSummaryPresentation(status);
}

export function sagaEventLabel(action) {
  return EVENT_LABELS[action] || "系统记录";
}

export function sagaRoleLabel(role) {
  if (role === "anchor") return "故事起点";
  if (role === "resolution") return "收束经历";
  return "后续发展";
}

export function allowedSagaTransitions(status) {
  if (status === "active") return ["completed", "tombstone"];
  if (status === "completed") return ["active", "archived", "tombstone"];
  if (status === "archived") return ["active", "tombstone"];
  return [];
}

