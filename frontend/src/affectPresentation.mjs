// 后端情绪簇的唯一前端展示映射。Live2D 和右栏共同读取这里，未知值回退 neutral。
export const CLUSTER_PRESENTATION = Object.freeze({
  bright: { icon: "✦", expression: 6, summary: "心绪明快，反应比平时轻盈一些" },
  serene: { icon: "❀", expression: 3, summary: "心绪安宁，愿意安静地陪伴" },
  agitated: { icon: "◈", expression: 7, summary: "有些不安，但仍会保持分寸" },
  melancholic: { icon: "☾", expression: 7, summary: "心绪低缓，更需要安静和克制" },
  focused: { icon: "◇", expression: 10, summary: "正在专注地衔接眼前的问题" },
  contemplative: { icon: "☁", expression: 0, summary: "安静沉思，话语会更简洁" },
  pleased: { icon: "✧", expression: 3, summary: "带着一点自然的愉快和温柔" },
  subdued: { icon: "◌", expression: 0, summary: "心绪沉静，仍会认真回应" },
  neutral: { icon: "🦋", expression: 0, summary: "心绪平和，安静地待在这里" },
});

export function getClusterPresentation(cluster) {
  return CLUSTER_PRESENTATION[cluster] || CLUSTER_PRESENTATION.neutral;
}
