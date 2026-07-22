"""EAP v0.2 表达向量、迟滞与 ExpressionPlan（spec 第 5.11 节）。

按 spec 第 5.11 节，本模块承载：
- 7 维连续表达向量（warmth/playfulness/directness/concern/initiative/restraint/energy）
- 3 个迟滞参数（minimum_state_duration / hysteresis_margin / transition_momentum）
- ExpressionPlan 协议（expression-plan-v1）：作用范围 5 项 + 禁区 5 项
- 心境状态转换历史（用于迟滞检查）

核心约束（spec 第 5.11 节）：
- "数值刚越过边界不立即跳变"：阈值附近需要满足 minimum_state_duration + hysteresis_margin
  双重检查才能转换状态，避免心境簇/guardedness 频繁跳变
- ExpressionPlan 严禁修改事实答案、安全结论、工具结果、权限要求、用户边界
- 9 种心境簇与 5 档 guardedness 仍保留用于 UI/调试/Live2D 大类选择，
  但不在阈值边界频繁跳变

模块隔离：本模块只导入 db/protocols/run_ledger，不接入 main.py（接入留给 EAP.J）。
本阶段不修改 affect-v1.2、affect-observer-v1、engine.py、observer.py。
"""

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .. import db
from .protocols import EXPRESSION_PLAN_V1
from .run_ledger import compute_source_hash, make_idempotency_key


# ============================================================================
# 常量：7 维连续表达向量
# ============================================================================

class ExpressionDimension:
    """7 维连续表达向量维度名（spec 第 5.11 节）。"""
    WARMTH = 'warmth'                # 温暖
    PLAYFULNESS = 'playfulness'      # 顽皮
    DIRECTNESS = 'directness'        # 直接
    CONCERN = 'concern'              # 关心
    INITIATIVE = 'initiative'        # 主动
    RESTRAINT = 'restraint'          # 克制
    ENERGY = 'energy'                # 能量


ALL_DIMENSIONS = (
    ExpressionDimension.WARMTH,
    ExpressionDimension.PLAYFULNESS,
    ExpressionDimension.DIRECTNESS,
    ExpressionDimension.CONCERN,
    ExpressionDimension.INITIATIVE,
    ExpressionDimension.RESTRAINT,
    ExpressionDimension.ENERGY,
)


# 每维度的中文描述（用于 UI/调试）
DIMENSION_DESCRIPTIONS = {
    ExpressionDimension.WARMTH: '温暖',
    ExpressionDimension.PLAYFULNESS: '顽皮',
    ExpressionDimension.DIRECTNESS: '直接',
    ExpressionDimension.CONCERN: '关心',
    ExpressionDimension.INITIATIVE: '主动',
    ExpressionDimension.RESTRAINT: '克制',
    ExpressionDimension.ENERGY: '能量',
}


# ============================================================================
# 常量：迟滞参数（3 项）
# ============================================================================

DEFAULT_HYSTERESIS_PARAMS = {
    'minimum_state_duration': 30.0,  # 30 秒：最小状态持续时间
    'hysteresis_margin': 0.1,        # 迟滞余量：需要超过更宽阈值才转换
    'transition_momentum': 0.5,      # 转换动量：影响状态转换速度
}


# ============================================================================
# 常量：6 种 ExpressionAct 的默认 7 维向量
# ============================================================================

# 每个 ExpressionAct 对应的默认 7 维向量（与 decision.ExpressionAct 6 种值对齐）
EXPRESSION_ACT_DEFAULT_VECTORS = {
    'playful_complaint':       {'warmth': 0.6, 'playfulness': 0.8, 'directness': 0.7, 'concern': 0.3, 'initiative': 0.7, 'restraint': 0.4, 'energy': 0.7},
    'gentle_urge':             {'warmth': 0.7, 'playfulness': 0.4, 'directness': 0.6, 'concern': 0.6, 'initiative': 0.7, 'restraint': 0.5, 'energy': 0.6},
    'firm_care':               {'warmth': 0.7, 'playfulness': 0.2, 'directness': 0.7, 'concern': 0.9, 'initiative': 0.6, 'restraint': 0.6, 'energy': 0.5},
    'worried_checkin':         {'warmth': 0.8, 'playfulness': 0.1, 'directness': 0.5, 'concern': 0.9, 'initiative': 0.8, 'restraint': 0.5, 'energy': 0.4},
    'expectant_followup':      {'warmth': 0.6, 'playfulness': 0.3, 'directness': 0.5, 'concern': 0.5, 'initiative': 0.7, 'restraint': 0.4, 'energy': 0.6},
    'quiet_waiting':           {'warmth': 0.6, 'playfulness': 0.2, 'directness': 0.3, 'concern': 0.5, 'initiative': 0.3, 'restraint': 0.8, 'energy': 0.3},
}


# ============================================================================
# 常量：ExpressionPlan 作用范围与禁区（spec 第 5.11 节）
# ============================================================================

# ExpressionPlan 只能调整这 5 项
EXPRESSION_PLAN_ALLOWED_ADJUSTMENTS = frozenset({
    'tone', 'length', 'directness', 'live2d_intensity', 'voice_prosody'
})

# ExpressionPlan 严禁修改这 5 项（禁区）
EXPRESSION_PLAN_FORBIDDEN_MODIFICATIONS = frozenset({
    'facts', 'safety', 'tool_results', 'permissions', 'user_boundary'
})


# ============================================================================
# dataclass 定义
# ============================================================================

@dataclass
class ExpressionVector:
    """7 维连续表达向量（spec 第 5.11 节）。

    每维取值 0.0~1.0：
    - warmth: 温暖
    - playfulness: 顽皮
    - directness: 直接
    - concern: 关心
    - initiative: 主动
    - restraint: 克制
    - energy: 能量

    正式回复消费连续表达向量，可同时表达"有点担心、稍微直接、仍然克制、
    带一点亲近"，而不是单一固定情绪标签。
    """
    warmth: float = 0.5
    playfulness: float = 0.5
    directness: float = 0.5
    concern: float = 0.5
    initiative: float = 0.5
    restraint: float = 0.5
    energy: float = 0.5

    def to_dict(self) -> dict:
        """转为字典（键名与 ALL_DIMENSIONS 一致）。"""
        return {
            'warmth': self.warmth,
            'playfulness': self.playfulness,
            'directness': self.directness,
            'concern': self.concern,
            'initiative': self.initiative,
            'restraint': self.restraint,
            'energy': self.energy,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'ExpressionVector':
        """从字典构造（缺失维度使用默认 0.5）。"""
        if d is None:
            return cls()
        return cls(
            warmth=float(d.get('warmth', 0.5)),
            playfulness=float(d.get('playfulness', 0.5)),
            directness=float(d.get('directness', 0.5)),
            concern=float(d.get('concern', 0.5)),
            initiative=float(d.get('initiative', 0.5)),
            restraint=float(d.get('restraint', 0.5)),
            energy=float(d.get('energy', 0.5)),
        )

    def clamp(self) -> 'ExpressionVector':
        """每维 clamp 到 [0, 1]，返回新向量。"""
        return ExpressionVector(
            warmth=_clamp01(self.warmth),
            playfulness=_clamp01(self.playfulness),
            directness=_clamp01(self.directness),
            concern=_clamp01(self.concern),
            initiative=_clamp01(self.initiative),
            restraint=_clamp01(self.restraint),
            energy=_clamp01(self.energy),
        )


@dataclass
class HysteresisParams:
    """3 个迟滞参数（spec 第 5.11 节）。

    - minimum_state_duration: 最小状态持续时间（秒），
      数值刚越过边界不立即跳变
    - hysteresis_margin: 迟滞余量（0.0~1.0），
      需要超过更宽阈值才转换
    - transition_momentum: 转换动量（0.0~1.0），
      影响状态转换速度（低动量延长所需时间，高动量缩短）
    """
    minimum_state_duration: float = 30.0
    hysteresis_margin: float = 0.1
    transition_momentum: float = 0.5


@dataclass
class ExpressionPlan:
    """expression_plans 表的完整记录。

    承载 7 维表达向量 + 3 个迟滞参数 + 5 项作用范围 + 5 项禁区标记。
    """
    id: str
    session_id: str
    decision_id: Optional[str]
    intensity_plan_id: Optional[str]
    vector: ExpressionVector
    hysteresis: HysteresisParams
    adjusts_tone: bool
    adjusts_length: bool
    adjusts_directness: bool
    adjusts_live2d_intensity: bool
    adjusts_voice_prosody: bool
    modifies_facts: bool
    modifies_safety: bool
    modifies_tool_results: bool
    modifies_permissions: bool
    modifies_user_boundary: bool
    expression_act: Optional[str]
    source_hash: str
    idempotency_key: str
    protocol_version: str
    created_at: float


@dataclass
class StateTransition:
    """expression_state_transitions 表的记录。

    用于迟滞检查：记录心境簇/guardedness/表达向量从一状态到另一状态的转换，
    以及是否因迟滞被拒绝。
    """
    id: str
    session_id: str
    state_kind: str
    from_state: str
    to_state: str
    from_value: Optional[float]
    to_value: Optional[float]
    transition_at: float
    hysteresis_applied: bool
    rejection_reason: Optional[str]
    created_at: float


# ============================================================================
# 内部工具
# ============================================================================

def _clamp01(value: float) -> float:
    """将数值限制在 [0.0, 1.0]。"""
    if value is None:
        return 0.0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def _to_bool(value) -> bool:
    """sqlite INTEGER → bool。"""
    return bool(value)


# ============================================================================
# 表达向量构造
# ============================================================================

def create_expression_vector(
    *,
    warmth: float = 0.5,
    playfulness: float = 0.5,
    directness: float = 0.5,
    concern: float = 0.5,
    initiative: float = 0.5,
    restraint: float = 0.5,
    energy: float = 0.5,
) -> ExpressionVector:
    """创建 7 维表达向量，自动 clamp 到 [0, 1]。"""
    return ExpressionVector(
        warmth=_clamp01(warmth),
        playfulness=_clamp01(playfulness),
        directness=_clamp01(directness),
        concern=_clamp01(concern),
        initiative=_clamp01(initiative),
        restraint=_clamp01(restraint),
        energy=_clamp01(energy),
    )


def create_expression_vector_for_act(expression_act: str) -> ExpressionVector:
    """根据 ExpressionAct 返回默认 7 维向量。

    如 act 不在 EXPRESSION_ACT_DEFAULT_VECTORS 中，返回全 0.5 默认向量。
    """
    if expression_act is None or expression_act not in EXPRESSION_ACT_DEFAULT_VECTORS:
        return ExpressionVector()
    return ExpressionVector.from_dict(EXPRESSION_ACT_DEFAULT_VECTORS[expression_act])


# ============================================================================
# 状态转换迟滞检查
# ============================================================================

def should_transition_state(
    *,
    current_value: float,
    target_value: float,
    threshold: float,
    hysteresis: HysteresisParams,
    last_transition_at: Optional[float] = None,
    now: Optional[float] = None,
) -> tuple:
    """判断心境状态是否应该转换（迟滞检查，spec 第 5.11 节）。

    按 spec："数值刚越过边界不立即跳变"。

    返回 (should_transition: bool, reason: str)：
    - 检查 1：minimum_state_duration - 自上次转换以来的时间是否足够
    - 检查 2：hysteresis_margin - 当前值是否超过 threshold ± margin
    - 检查 3：transition_momentum - 转换动量影响（如 momentum < 0.3，需要更长时间）

    如任一检查失败，返回 (False, reason)；否则 (True, 'ok')
    """
    now = now if now is not None else db.now()

    # 检查 1：minimum_state_duration（结合 transition_momentum 调整）
    # 动量低 → effective_duration 增大；动量高 → effective_duration 减小
    momentum = _clamp01(hysteresis.transition_momentum)
    effective_duration = hysteresis.minimum_state_duration / max(0.1, momentum)

    if last_transition_at is not None:
        elapsed = now - last_transition_at
        if elapsed < effective_duration:
            return (False, 'minimum_state_duration_not_met')

    # 检查 2：hysteresis_margin（值需超过 threshold ± margin）
    margin = max(0.0, hysteresis.hysteresis_margin)
    if target_value > threshold:
        # 向上转换：当前值必须 >= threshold + margin
        if current_value < threshold + margin:
            return (False, 'hysteresis_margin_not_met')
    elif target_value < threshold:
        # 向下转换：当前值必须 <= threshold - margin
        if current_value > threshold - margin:
            return (False, 'hysteresis_margin_not_met')
    # target_value == threshold 时无需 margin 检查

    return (True, 'ok')


def record_state_transition(
    session_id: str,
    *,
    state_kind: str,
    from_state: str,
    to_state: str,
    from_value: Optional[float] = None,
    to_value: Optional[float] = None,
    transition_at: Optional[float] = None,
    hysteresis_applied: bool = False,
    rejection_reason: Optional[str] = None,
    now: Optional[float] = None,
) -> StateTransition:
    """记录状态转换到 expression_state_transitions 表。

    state_kind 必须为 'mood_cluster' / 'guardedness_level' / 'expression_vector'。
    """
    valid_kinds = {'mood_cluster', 'guardedness_level', 'expression_vector'}
    if state_kind not in valid_kinds:
        raise ValueError(f"invalid state_kind: {state_kind!r}")

    now = now if now is not None else db.now()
    transition_at = transition_at if transition_at is not None else now
    record_id = db.new_id()

    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO expression_state_transitions"
            " (id, session_id, state_kind, from_state, to_state,"
            "  from_value, to_value, transition_at, hysteresis_applied,"
            "  rejection_reason, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id, session_id, state_kind, from_state, to_state,
                from_value, to_value, transition_at,
                1 if hysteresis_applied else 0,
                rejection_reason, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return StateTransition(
        id=record_id, session_id=session_id, state_kind=state_kind,
        from_state=from_state, to_state=to_state,
        from_value=from_value, to_value=to_value,
        transition_at=transition_at,
        hysteresis_applied=hysteresis_applied,
        rejection_reason=rejection_reason, created_at=now,
    )


def get_last_transition(
    session_id: str, *, state_kind: str,
) -> Optional[StateTransition]:
    """获取指定 state_kind 的最近一次转换记录（按 transition_at 倒序）。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM expression_state_transitions "
            "WHERE session_id=? AND state_kind=? "
            "ORDER BY transition_at DESC LIMIT 1",
            (session_id, state_kind),
        ).fetchone()
        if not row:
            return None
        return _row_to_transition(row)
    finally:
        conn.close()


# ============================================================================
# ExpressionPlan 禁区验证
# ============================================================================

def validate_expression_plan_scope(
    *,
    modifies_facts: bool = False,
    modifies_safety: bool = False,
    modifies_tool_results: bool = False,
    modifies_permissions: bool = False,
    modifies_user_boundary: bool = False,
) -> tuple:
    """验证 ExpressionPlan 是否触碰禁区（spec 第 5.11 节禁区 5 项）。

    返回 (is_valid: bool, violations: list[str])：
    - 任何 modifies_* 为 True 都视为违规
    - violations 是违规字段名列表
    """
    checks = [
        ('facts', modifies_facts),
        ('safety', modifies_safety),
        ('tool_results', modifies_tool_results),
        ('permissions', modifies_permissions),
        ('user_boundary', modifies_user_boundary),
    ]
    violations = [name for name, flag in checks if flag]
    return (len(violations) == 0, violations)


# ============================================================================
# ExpressionPlan 创建与查询
# ============================================================================

def create_expression_plan(
    session_id: str,
    *,
    decision_id: Optional[str] = None,
    intensity_plan_id: Optional[str] = None,
    vector: Optional[ExpressionVector] = None,
    expression_act: Optional[str] = None,
    hysteresis: Optional[HysteresisParams] = None,
    source_messages: Optional[Iterable[dict]] = None,
    adjusts_tone: bool = True,
    adjusts_length: bool = True,
    adjusts_directness: bool = True,
    adjusts_live2d_intensity: bool = True,
    adjusts_voice_prosody: bool = False,
    modifies_facts: bool = False,
    modifies_safety: bool = False,
    modifies_tool_results: bool = False,
    modifies_permissions: bool = False,
    modifies_user_boundary: bool = False,
    now: Optional[float] = None,
) -> ExpressionPlan:
    """创建 ExpressionPlan 并落库。

    - 如未提供 vector，根据 expression_act 使用默认向量（或全 0.5）
    - 如未提供 hysteresis，使用 DEFAULT_HYSTERESIS_PARAMS
    - 自动调用 validate_expression_plan_scope 验证禁区
      （任何 modifies_* = True 时抛出 ValueError）
    - 计算 source_hash（如提供 source_messages）
    - 生成 idempotency_key = make_idempotency_key(EXPRESSION_PLAN_V1, session_id, decision_id or '')
    - 落库到 expression_plans 表
    - 幂等：相同 (session_id, decision_id) 重复调用返回已有 plan
    """
    # 禁区严格检查：任何 True 都拒绝
    is_valid, violations = validate_expression_plan_scope(
        modifies_facts=modifies_facts,
        modifies_safety=modifies_safety,
        modifies_tool_results=modifies_tool_results,
        modifies_permissions=modifies_permissions,
        modifies_user_boundary=modifies_user_boundary,
    )
    if not is_valid:
        raise ValueError(
            f"ExpressionPlan 禁区违规: {violations}"
        )

    now = now if now is not None else db.now()

    # 7 维向量：显式 > act 默认 > 全 0.5
    if vector is None:
        vector = create_expression_vector_for_act(expression_act or '')
    else:
        vector = vector.clamp()

    # 迟滞参数：默认或自定义
    if hysteresis is None:
        hysteresis = HysteresisParams(
            minimum_state_duration=DEFAULT_HYSTERESIS_PARAMS['minimum_state_duration'],
            hysteresis_margin=DEFAULT_HYSTERESIS_PARAMS['hysteresis_margin'],
            transition_momentum=DEFAULT_HYSTERESIS_PARAMS['transition_momentum'],
        )

    # source_hash
    if source_messages is not None:
        source_hash = compute_source_hash(source_messages)
    else:
        source_hash = ''

    # 幂等检查：相同 (session_id, decision_id) 已有 plan 则返回已有
    idempotency_key = make_idempotency_key(
        EXPRESSION_PLAN_V1, session_id, decision_id or '',
    )
    existing = _get_plan_by_idempotency_key(idempotency_key)
    if existing is not None:
        return existing

    record_id = db.new_id()

    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO expression_plans"
            " (id, session_id, decision_id, intensity_plan_id,"
            "  warmth, playfulness, directness, concern, initiative, restraint, energy,"
            "  minimum_state_duration, hysteresis_margin, transition_momentum,"
            "  adjusts_tone, adjusts_length, adjusts_directness,"
            "  adjusts_live2d_intensity, adjusts_voice_prosody,"
            "  modifies_facts, modifies_safety, modifies_tool_results,"
            "  modifies_permissions, modifies_user_boundary,"
            "  expression_act, source_hash, idempotency_key,"
            "  protocol_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id, session_id, decision_id, intensity_plan_id,
                vector.warmth, vector.playfulness, vector.directness,
                vector.concern, vector.initiative, vector.restraint, vector.energy,
                hysteresis.minimum_state_duration, hysteresis.hysteresis_margin,
                hysteresis.transition_momentum,
                1 if adjusts_tone else 0,
                1 if adjusts_length else 0,
                1 if adjusts_directness else 0,
                1 if adjusts_live2d_intensity else 0,
                1 if adjusts_voice_prosody else 0,
                0, 0, 0, 0, 0,  # modifies_* 永远为 0（已在前面校验）
                expression_act, source_hash, idempotency_key,
                EXPRESSION_PLAN_V1, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return ExpressionPlan(
        id=record_id, session_id=session_id,
        decision_id=decision_id, intensity_plan_id=intensity_plan_id,
        vector=vector, hysteresis=hysteresis,
        adjusts_tone=adjusts_tone, adjusts_length=adjusts_length,
        adjusts_directness=adjusts_directness,
        adjusts_live2d_intensity=adjusts_live2d_intensity,
        adjusts_voice_prosody=adjusts_voice_prosody,
        modifies_facts=False, modifies_safety=False,
        modifies_tool_results=False, modifies_permissions=False,
        modifies_user_boundary=False,
        expression_act=expression_act, source_hash=source_hash,
        idempotency_key=idempotency_key,
        protocol_version=EXPRESSION_PLAN_V1, created_at=now,
    )


def get_expression_plan(plan_id: str) -> Optional[ExpressionPlan]:
    """按 ID 查询 ExpressionPlan。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM expression_plans WHERE id=?",
            (plan_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_plan(row)
    finally:
        conn.close()


def get_expression_plan_by_decision(decision_id: str) -> Optional[ExpressionPlan]:
    """按 decision_id 查询最新一条 ExpressionPlan。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM expression_plans WHERE decision_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (decision_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_plan(row)
    finally:
        conn.close()


def list_expression_plans_by_session(
    session_id: str, *, limit: int = 50,
) -> list:
    """按 session 查询 ExpressionPlan 列表（按 created_at 倒序）。"""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM expression_plans WHERE session_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [_row_to_plan(row) for row in rows]
    finally:
        conn.close()


# ============================================================================
# 迟滞应用到向量
# ============================================================================

def apply_hysteresis_to_vector(
    current_vector: ExpressionVector,
    target_vector: ExpressionVector,
    *,
    hysteresis: HysteresisParams,
    last_transition_at: Optional[float] = None,
    now: Optional[float] = None,
) -> tuple:
    """对 7 维向量应用迟滞检查，返回最终向量和每维的检查结果。

    按 spec："数值刚越过边界不立即跳变"。

    对每一维：
    - 阈值 0.5（中性）
    - 调用 should_transition_state 检查
    - 如通过：使用 target_vector 该维值
    - 如未通过：保留 current_vector 该维值

    返回 (final_vector: ExpressionVector, per_dimension_results: dict)：
    - per_dimension_results: {dim: (should_transition, reason)}
    """
    now = now if now is not None else db.now()
    current_dict = current_vector.to_dict()
    target_dict = target_vector.to_dict()

    final_values = {}
    per_dimension_results = {}
    for dim in ALL_DIMENSIONS:
        current_v = float(current_dict.get(dim, 0.5))
        target_v = float(target_dict.get(dim, 0.5))
        should, reason = should_transition_state(
            current_value=current_v,
            target_value=target_v,
            threshold=0.5,
            hysteresis=hysteresis,
            last_transition_at=last_transition_at,
            now=now,
        )
        per_dimension_results[dim] = (should, reason)
        final_values[dim] = target_v if should else current_v

    final_vector = ExpressionVector(**final_values)
    return (final_vector, per_dimension_results)


# ============================================================================
# 内部：行转换
# ============================================================================

def _get_plan_by_idempotency_key(idempotency_key: str) -> Optional[ExpressionPlan]:
    """按 idempotency_key 查询（幂等检查）。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM expression_plans WHERE idempotency_key=? "
            "ORDER BY created_at DESC LIMIT 1",
            (idempotency_key,),
        ).fetchone()
        if not row:
            return None
        return _row_to_plan(row)
    finally:
        conn.close()


def _row_to_plan(row) -> ExpressionPlan:
    """内部：从 sqlite3.Row 构造 ExpressionPlan。"""
    vector = ExpressionVector(
        warmth=row["warmth"],
        playfulness=row["playfulness"],
        directness=row["directness"],
        concern=row["concern"],
        initiative=row["initiative"],
        restraint=row["restraint"],
        energy=row["energy"],
    )
    hysteresis = HysteresisParams(
        minimum_state_duration=row["minimum_state_duration"],
        hysteresis_margin=row["hysteresis_margin"],
        transition_momentum=row["transition_momentum"],
    )
    return ExpressionPlan(
        id=row["id"], session_id=row["session_id"],
        decision_id=row["decision_id"], intensity_plan_id=row["intensity_plan_id"],
        vector=vector, hysteresis=hysteresis,
        adjusts_tone=_to_bool(row["adjusts_tone"]),
        adjusts_length=_to_bool(row["adjusts_length"]),
        adjusts_directness=_to_bool(row["adjusts_directness"]),
        adjusts_live2d_intensity=_to_bool(row["adjusts_live2d_intensity"]),
        adjusts_voice_prosody=_to_bool(row["adjusts_voice_prosody"]),
        modifies_facts=_to_bool(row["modifies_facts"]),
        modifies_safety=_to_bool(row["modifies_safety"]),
        modifies_tool_results=_to_bool(row["modifies_tool_results"]),
        modifies_permissions=_to_bool(row["modifies_permissions"]),
        modifies_user_boundary=_to_bool(row["modifies_user_boundary"]),
        expression_act=row["expression_act"],
        source_hash=row["source_hash"],
        idempotency_key=row["idempotency_key"],
        protocol_version=row["protocol_version"],
        created_at=row["created_at"],
    )


def _row_to_transition(row) -> StateTransition:
    """内部：从 sqlite3.Row 构造 StateTransition。"""
    return StateTransition(
        id=row["id"], session_id=row["session_id"],
        state_kind=row["state_kind"],
        from_state=row["from_state"], to_state=row["to_state"],
        from_value=row["from_value"], to_value=row["to_value"],
        transition_at=row["transition_at"],
        hysteresis_applied=_to_bool(row["hysteresis_applied"]),
        rejection_reason=row["rejection_reason"],
        created_at=row["created_at"],
    )
