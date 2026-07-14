// 从聊天文本推断遐蝶情绪（中文关键词启发式）。
// 情绪名 → Live2D 表情索引的映射在 pet.tsx 里（EMO_TO_EXP）。
// 这里只做"文本 → 情绪名"，不依赖模型，便于单测与复用。

export type Emotion =
  | "neutral"
  | "happy"
  | "excited"
  | "shy"
  | "playful"
  | "aggrieved"
  | "confused"
  | "speechless";

// 关键词表：按优先级从上到下匹配（越靠前越优先）。
const RULES: { emo: Emotion; kw: RegExp }[] = [
  // 道歉 / 失败 / 无能为力 → 委屈
  { emo: "aggrieved", kw: /(抱歉|对不起|不好意思|失败|出错|错误|无法|没办法|做不到|不能完成|遇到问题)/ },
  // 疑惑 / 不确定 / 反问 → 疑惑
  { emo: "confused", kw: /(不确定|不太清楚|不清楚|也许|可能是|是不是|要不要|吗？|呢？|\?$|？$)/ },
  // 撒娇 / 调皮 / 玩笑 → 调皮
  { emo: "playful", kw: /(嘿嘿|哈哈|嘻嘻|开玩笑|逗你|调皮|偷偷|~$|~。|啦~|哦~)/ },
  // 被夸 / 喜欢 / 亲密 → 害羞
  { emo: "shy", kw: /(谢谢夸|过奖|害羞|不好意思啦|喜欢你|你真好|抱抱|摸摸)/ },
  // 惊喜 / 强烈正向 / 达成 → 兴奋
  { emo: "excited", kw: /(太好了|太棒|好厉害|好喜欢|超级|真棒|完成啦|搞定啦|成功了|棒极了|哇)/ },
  // 无奈 / 无语 → 无语
  { emo: "speechless", kw: /(无语|无奈|没辙|又来了|唉|哎)/ },
  // 一般正向应答 → 开心
  { emo: "happy", kw: /(好的|没问题|可以的?|当然|乐意|收到|记住了|完成|搞定|帮你|一起)/ },
];

/** 综合参考助手回复（主）与用户消息（辅）判断情绪。 */
export function inferEmotion(assistantText: string, userText = ""): Emotion {
  const t = (assistantText || "").slice(0, 200);
  for (const r of RULES) {
    if (r.kw.test(t)) return r.emo;
  }
  // 助手文本没命中时，看用户是否在道谢/夸奖 → 害羞
  if (/(谢谢|感谢|辛苦|你真|棒|厉害)/.test(userText)) return "shy";
  return "neutral";
}
