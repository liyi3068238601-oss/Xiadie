"""EAP v0.2 主动强度阶梯与 Live2D 低干扰行为（spec 第 5.10 节）。

按 spec 第 5.10 节 Level 0~5 六档强度阶梯：
- Level 0 安静无动作：不产生任何可见输出，状态继续推进
- Level 1 Live2D 视线/表情/轻微动作：无文字，仅 Live2D 表达
- Level 2 无通知的小气泡：不触发系统通知，不要求回复
- Level 3 正常聊天主动消息：主窗口内主动消息
- Level 4 桌面系统通知：Windows 系统通知
- Level 5 外部渠道消息：QQ/微信/邮件，必须单独授权

决策原则：在能表达当前接近意愿的前提下，选择最低足够强度。
示例：当 LLM 认为"她想靠近，但不值得打断用户"时，应优先选择 Level 1
或 Level 2，而不是强制发文本。

模块隔离：本模块只导入 db/protocols，不接入 main.py（接入留给 EAP.J）。
本阶段不实际触发 Live2D 或显示气泡：只生成 live2d_action JSON 和 bubble_text，
实际触发由前端/Electron 主进程负责。
"""

import json
from dataclasses import dataclass
from typing import Optional

from .. import db
from .protocols import PROACTIVE_DECISION_V2


# 六档强度阶梯（spec 第 5.10 节）
class IntensityLevel:
    LEVEL_0_SILENT = 0      # 安静无动作
    LEVEL_1_LIVE2D = 1      # Live2D 视线/表情/轻微动作
    LEVEL_2_BUBBLE = 2      # 无通知小气泡
    LEVEL_3_CHAT = 3        # 正常聊天主动消息
    LEVEL_4_DESKTOP = 4     # 桌面系统通知
    LEVEL_5_EXTERNAL = 5    # 外部渠道消息


ALL_LEVELS = (0, 1, 2, 3, 4, 5)


# level → channel 映射（channel 与 level 一一对应）
LEVEL_NAMES = {
    0: "silent",
    1: "live2d",
    2: "bubble",
    3: "chat",
    4: "desktop_notification",
    5: "external",
}

# 同 LEVEL_NAMES（channel 名）
LEVEL_CHANNELS = dict(LEVEL_NAMES)


# level → 中文描述
LEVEL_DESCRIPTIONS = {
    0: "安静无动作",
    1: "Live2D 视线/表情/轻微动作",
    2: "无通知的小气泡",
    3: "正常聊天主动消息",
    4: "桌面系统通知",
    5: "外部渠道消息",
}


# 用户默认授权的级别（spec 第 3.4 节分渠道）
# - Level 0 (silent): True（无需授权）
# - Level 1 (live2d): True（默认开启）
# - Level 2 (bubble): True（默认开启）
# - Level 3 (chat): True（主窗口默认开启）
# - Level 4 (desktop_notification): False（首次使用询问，默认 0）
# - Level 5 (external): False（必须单独授权）
DEFAULT_LEVEL_AUTHORIZATION = {
    0: True,
    1: True,
    2: True,
    3: True,
    4: False,
    5: False,
}


# 每个 ExpressionAct 对应的 Live2D 动作参数模板（Level 1）
# 与 decision.ExpressionAct 6 种值对齐
LIVE2D_ACTION_TEMPLATES = {
    "playful_complaint": {"gaze": "sideways", "expression": "pout", "motion": "head_tilt"},
    "gentle_urge": {"gaze": "direct", "expression": "soft_smile", "motion": "lean_in"},
    "firm_care": {"gaze": "direct", "expression": "concerned", "motion": "nod"},
    "worried_checkin": {"gaze": "up", "expression": "worried", "motion": "hand_reach"},
    "expectant_followup": {"gaze": "direct", "expression": "hopeful", "motion": "lean_in"},
    "quiet_waiting": {"gaze": "down", "expression": "calm", "motion": "idle"},
}


# 每个 ExpressionAct 对应的 Level 2 气泡文本模板
DEFAULT_BUBBLE_TEMPLATES = {
    "playful_complaint": "（小声嘀咕）",
    "gentle_urge": "（期待地看着你）",
    "firm_care": "（静静陪伴）",
    "worried_checkin": "（有些担心）",
    "expectant_followup": "（等你回应）",
    "quiet_waiting": "（在这里）",
}


@dataclass
class IntensityPlan:
    """proactive_intensity_plans 表的记录。"""
    id: str
    decision_id: str
    session_id: str
    level: int
    channel: str
    is_minimum_sufficient: bool
    live2d_action: Optional[dict]
    bubble_text: Optional[str]
    reason: str
    protocol_version: str
    created_at: float


def _load_settings(settings: Optional[dict]) -> dict:
    """加载 settings（如未提供则从 db.get_setting 读取关键键）。"""
    if settings is not None:
        return settings
    return {
        "proactive_enabled": db.get_setting("proactive_enabled", "1"),
        "proactive_desktop_notification_enabled": db.get_setting(
            "proactive_desktop_notification_enabled", "0"
        ),
        "proactive_external_channels_enabled": db.get_setting(
            "proactive_external_channels_enabled", "0"
        ),
    }


def is_level_authorized(level: int, *, settings: Optional[dict] = None) -> bool:
    """检查给定级别是否被用户授权。

    - Level 0：永远 True（无需授权，silent）
    - Level 1-3：proactive_enabled='1' 时授权
    - Level 4：需要 settings['proactive_desktop_notification_enabled'] == '1'
    - Level 5：需要 settings['proactive_external_channels_enabled'] == '1'
    - 关闭主动陪伴（proactive_enabled='0'）时 Level 1-5 全部 False

    settings 参数可选；如未提供则从 db.get_setting 读取
    """
    if not isinstance(level, int) or isinstance(level, bool):
        raise ValueError(f"level must be int, got {type(level).__name__}")
    if level < 0 or level > 5:
        raise ValueError(f"level must be between 0 and 5, got {level}")

    # Level 0 永远授权
    if level == 0:
        return True

    settings = _load_settings(settings)

    # 关闭主动陪伴时 Level 1-5 全部未授权
    if settings.get("proactive_enabled", "1") != "1":
        return False

    if level <= 3:
        return True
    if level == 4:
        return settings.get("proactive_desktop_notification_enabled", "0") == "1"
    if level == 5:
        return settings.get("proactive_external_channels_enabled", "0") == "1"
    return False


def _presence_is_busy_like(presence) -> bool:
    """判断 presence 是否处于"忙碌/勿扰/睡眠"等不适合发文字消息的状态。"""
    if presence is None:
        return False
    user_status = getattr(presence, "user_status", None)
    is_active = getattr(presence, "is_active", False)
    if not is_active:
        return False
    busy_statuses = {
        "away_busy",
        "away_sleep",
        "do_not_disturb",
        "ended_conversation",
        "away_extended",
    }
    return user_status in busy_statuses


def select_minimum_sufficient_level(
    *,
    approach_value: float,
    expression_act: Optional[str] = None,
    llm_advice_intensity: Optional[int] = None,
    presence=None,
    settings: Optional[dict] = None,
) -> int:
    """选择最低足够强度（spec 第 5.10 节决策原则）。

    决策原则：在能表达当前接近意愿的前提下，选择最低足够强度。

    逻辑：
    1. approach_value < 0：Level 0（silent）
    2. approach_value < 0.1（接近意愿很弱）：Level 0 或 1
    3. approach_value < 0.3（想靠近但不值得打断）：
       - presence.away_busy/dnd/sleep：Level 1（Live2D，无文字）
       - 否则：Level 2（无通知气泡）
    4. approach_value < 0.6（值得发消息）：Level 3
    5. approach_value >= 0.6（高重要性）：Level 3 或 4（检查授权）

    LLM 建议优先（llm_advice_intensity）：
    - 如 LLM 提供了 intensity 且该级别被授权，使用 LLM 建议值
    - 但如 LLM 建议 > 必需强度，使用最低足够强度（不强制升级）
    - 如 LLM 建议 < 必需强度，使用 LLM 建议值（允许降级）

    关键：如 LLM 建议 Level 4/5 但未授权，降级到 Level 3
    """
    settings = _load_settings(settings)

    # 第一步：按 approach_value 计算本地必需强度（最低足够强度）
    if approach_value < 0:
        local_required = 0
    elif approach_value < 0.1:
        # 接近意愿很弱：Level 0 或 1（默认 Level 1，除非 presence 表明完全不该出现）
        local_required = 1 if approach_value >= 0 else 0
    elif approach_value < 0.3:
        # 想靠近但不值得打断：presence 忙碌/勿扰/睡眠 → Level 1；否则 Level 2
        if _presence_is_busy_like(presence):
            local_required = 1
        else:
            local_required = 2
    elif approach_value < 0.6:
        # 值得发消息：Level 3
        local_required = 3
    else:
        # 高重要性：Level 3 或 4（检查授权）
        if is_level_authorized(4, settings=settings):
            local_required = 4
        else:
            local_required = 3

    # 第二步：综合 LLM 建议
    if llm_advice_intensity is None:
        # 无 LLM 建议：使用本地必需强度
        chosen = local_required
    else:
        if not isinstance(llm_advice_intensity, int) or isinstance(llm_advice_intensity, bool):
            raise ValueError(
                f"llm_advice_intensity must be int, got {type(llm_advice_intensity).__name__}"
            )
        if llm_advice_intensity < 0 or llm_advice_intensity > 5:
            raise ValueError(
                f"llm_advice_intensity must be between 0 and 5, got {llm_advice_intensity}"
            )

        # 如 LLM 建议级别被授权：
        # - 如 LLM 建议 <= local_required：使用 LLM 建议（允许降级，spec 示例：不值得打断时优先 Level 1/2）
        # - 如 LLM 建议 > local_required：使用 local_required（不强制升级，遵守"最低足够强度"）
        # 如 LLM 建议级别未被授权：降级到 local_required
        if is_level_authorized(llm_advice_intensity, settings=settings):
            if llm_advice_intensity <= local_required:
                chosen = llm_advice_intensity
            else:
                chosen = local_required
        else:
            # LLM 建议级别未授权 → 降级到 local_required
            chosen = local_required

    # 第三步：最终授权检查（local_required 也可能因 proactive_enabled=0 而未授权）
    # 此时降级到 Level 0（与"关闭主动陪伴时 0 次发送"约束一致）
    if not is_level_authorized(chosen, settings=settings):
        chosen = 0

    return chosen


def build_live2d_action(expression_act: Optional[str]) -> Optional[dict]:
    """构建 Live2D 动作参数（spec 第 5.10 节 Level 1）。

    - 如 expression_act 在 LIVE2D_ACTION_TEMPLATES 中，返回对应模板的副本
    - 如 expression_act 为 None 或不在模板中，返回默认 quiet_waiting 模板
    - 用于 Level 1（无文字 Live2D 表达）
    """
    if expression_act is None or expression_act not in LIVE2D_ACTION_TEMPLATES:
        # 默认 quiet_waiting 模板
        template = LIVE2D_ACTION_TEMPLATES["quiet_waiting"]
    else:
        template = LIVE2D_ACTION_TEMPLATES[expression_act]
    # 返回副本避免外部修改
    return dict(template)


def build_bubble_text(expression_act: Optional[str]) -> Optional[str]:
    """构建 Level 2 气泡文本（spec 第 5.10 节 Level 2）。

    - 如 expression_act 在 DEFAULT_BUBBLE_TEMPLATES 中，返回对应文本
    - 如 expression_act 为 None 或不在模板中，返回默认 quiet_waiting 文本
    - 用于 Level 2（无通知小气泡，不要求回复）
    """
    if expression_act is None or expression_act not in DEFAULT_BUBBLE_TEMPLATES:
        return DEFAULT_BUBBLE_TEMPLATES["quiet_waiting"]
    return DEFAULT_BUBBLE_TEMPLATES[expression_act]


def create_intensity_plan(
    decision_id: str,
    session_id: str,
    *,
    level: int,
    expression_act: Optional[str] = None,
    llm_advice_intensity: Optional[int] = None,
    approach_value: float = 0.0,
    presence=None,
    settings: Optional[dict] = None,
    reason: str = "",
    now: Optional[float] = None,
) -> IntensityPlan:
    """创建强度计划并落库。

    - 如果 level 未授权，自动降级到 Level 3 或更低
    - 根据 level 填充 live2d_action（Level 1）或 bubble_text（Level 2）
    - is_minimum_sufficient 标记是否为最低足够强度
    """
    if not isinstance(level, int) or isinstance(level, bool):
        raise ValueError(f"level must be int, got {type(level).__name__}")
    if level < 0 or level > 5:
        raise ValueError(f"level must be between 0 and 5, got {level}")

    settings = _load_settings(settings)
    now = now if now is not None else db.now()

    # 计算最低足够强度（用于 is_minimum_sufficient 判断）
    minimum_sufficient = select_minimum_sufficient_level(
        approach_value=approach_value,
        expression_act=expression_act,
        llm_advice_intensity=llm_advice_intensity,
        presence=presence,
        settings=settings,
    )

    # 如 level 未授权，降级：尝试降级到 minimum_sufficient，否则降到 Level 0
    actual_level = level
    if not is_level_authorized(actual_level, settings=settings):
        # 优先降级到 minimum_sufficient（如已授权）
        if is_level_authorized(minimum_sufficient, settings=settings):
            actual_level = minimum_sufficient
        else:
            # 都未授权，降到 Level 0（与"关闭主动陪伴时 0 次发送"一致）
            actual_level = 0

    # 但如 actual_level > minimum_sufficient，应使用最低足够强度（不强制升级）
    # 这里允许调用方明确传入更高的 level（例如 LLM 强烈建议），但默认遵守最低足够原则
    # 实际通过 select_minimum_sufficient_level 已处理，这里只做授权降级

    channel = LEVEL_CHANNELS[actual_level]
    is_min_sufficient = (actual_level == minimum_sufficient)

    # 根据 level 填充 live2d_action 或 bubble_text
    live2d_action: Optional[dict] = None
    bubble_text: Optional[str] = None
    if actual_level == 1:
        live2d_action = build_live2d_action(expression_act)
    elif actual_level == 2:
        bubble_text = build_bubble_text(expression_act)

    record_id = db.new_id()
    live2d_action_json = (
        json.dumps(live2d_action, ensure_ascii=False, sort_keys=True)
        if live2d_action is not None
        else None
    )

    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO proactive_intensity_plans"
            " (id, decision_id, session_id, level, channel,"
            "  is_minimum_sufficient, live2d_action, bubble_text,"
            "  reason, protocol_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id, decision_id, session_id, actual_level, channel,
                1 if is_min_sufficient else 0, live2d_action_json, bubble_text,
                reason, PROACTIVE_DECISION_V2, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return IntensityPlan(
        id=record_id, decision_id=decision_id, session_id=session_id,
        level=actual_level, channel=channel,
        is_minimum_sufficient=is_min_sufficient,
        live2d_action=live2d_action, bubble_text=bubble_text,
        reason=reason, protocol_version=PROACTIVE_DECISION_V2,
        created_at=now,
    )


def get_intensity_plan(plan_id: str) -> Optional[IntensityPlan]:
    """按 ID 查询强度计划。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM proactive_intensity_plans WHERE id=?",
            (plan_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_plan(row)
    finally:
        conn.close()


def get_intensity_plan_by_decision(decision_id: str) -> Optional[IntensityPlan]:
    """按 decision_id 查询最新一条强度计划。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM proactive_intensity_plans WHERE decision_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (decision_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_plan(row)
    finally:
        conn.close()


def list_intensity_plans_by_session(
    session_id: str, *, limit: int = 50,
) -> list:
    """按 session 查询强度计划列表（按 created_at 倒序）。"""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM proactive_intensity_plans WHERE session_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [_row_to_plan(row) for row in rows]
    finally:
        conn.close()


def _parse_live2d_action(raw: Optional[str]) -> Optional[dict]:
    """从 JSON 字符串解析 live2d_action（None 或空 → None）。"""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        return None


def _row_to_plan(row) -> IntensityPlan:
    """内部：从 sqlite3.Row 构造 IntensityPlan。"""
    return IntensityPlan(
        id=row["id"], decision_id=row["decision_id"], session_id=row["session_id"],
        level=row["level"], channel=row["channel"],
        is_minimum_sufficient=bool(row["is_minimum_sufficient"]),
        live2d_action=_parse_live2d_action(row["live2d_action"]),
        bubble_text=row["bubble_text"],
        reason=row["reason"], protocol_version=row["protocol_version"],
        created_at=row["created_at"],
    )


def plan_intensity_for_decision(
    decision_id: str, *, now: Optional[float] = None,
) -> Optional[IntensityPlan]:
    """完整流程：根据已有决策生成强度计划。

    1. 加载 decision（使用 decision.get_decision）
    2. 如 decision.decision != 'send'，返回 None（不投递的决策不需要强度计划）
    3. 加载 candidate（使用 candidates.get_candidate）
    4. 调用 select_minimum_sufficient_level 选择级别
    5. 调用 create_intensity_plan 落库
    6. 返回 IntensityPlan
    """
    # 延迟导入避免循环依赖
    from .candidates import get_candidate
    from .decision import DecisionAction, get_decision

    decision = get_decision(decision_id)
    if decision is None:
        raise ValueError(f"decision not found: {decision_id}")

    # 不投递的决策不需要强度计划
    if decision.decision != DecisionAction.SEND:
        return None

    candidate = get_candidate(decision.candidate_id)
    if candidate is None:
        raise ValueError(f"candidate not found: {decision.candidate_id}")

    # 选择最低足够强度
    chosen_level = select_minimum_sufficient_level(
        approach_value=decision.approach_value,
        expression_act=decision.expression_act,
        llm_advice_intensity=decision.intensity,
    )

    reason = (
        f"approach_value={decision.approach_value:.3f}; "
        f"expression_act={decision.expression_act or 'none'}; "
        f"llm_advice_intensity={decision.intensity}"
    )

    return create_intensity_plan(
        decision_id=decision.id,
        session_id=decision.session_id,
        level=chosen_level,
        expression_act=decision.expression_act,
        llm_advice_intensity=decision.intensity,
        approach_value=decision.approach_value,
        reason=reason,
        now=now,
    )
