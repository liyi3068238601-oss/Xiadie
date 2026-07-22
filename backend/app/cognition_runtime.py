"""CDS.2 model routing and resource controls for registered cognitive decisions.

The runtime stores only binding, counters and error codes. Prompts, model output,
candidate bodies and user text remain outside the control-plane tables.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from . import cognitive_decision as cds
from . import db, llm

PROBE_VERSION = "cognitive-structured-probe-v1"
PROBE_TIMEOUT_SECONDS = {
    "fast": 5.0,
    "reasoning": 30.0,
    "creative": 15.0,
}


class LogicalRole(str, Enum):
    FAST = "fast"
    REASONING = "reasoning"
    CREATIVE = "creative"


class CertificationLevel(str, Enum):
    UNVERIFIED = "unverified"
    STRUCTURED_CAPABLE = "structured_capable"
    DECISION_VERIFIED = "decision_verified"
    LOCAL_SENSITIVE_VERIFIED = "local_sensitive_verified"


class TaskPriority(str, Enum):
    FOREGROUND = "foreground"
    NORMAL = "normal"
    BACKGROUND = "background"


_CERT_RANK = {
    CertificationLevel.UNVERIFIED: 0,
    CertificationLevel.STRUCTURED_CAPABLE: 1,
    CertificationLevel.DECISION_VERIFIED: 2,
    CertificationLevel.LOCAL_SENSITIVE_VERIFIED: 3,
}


@dataclass(frozen=True)
class ModelBinding:
    provider: dict[str, Any]
    model_id: str
    logical_role: LogicalRole
    revision: str

    @property
    def provider_id(self) -> str:
        return str(self.provider["id"])

    @property
    def location(self) -> str:
        return str(self.provider.get("execution_location") or "unknown")

    @property
    def location_revision(self) -> int:
        return max(1, int(self.provider.get("location_revision") or 1))


def _binding_revision(provider_id: str, model_id: str, role: LogicalRole,
                      location: str, location_revision: int) -> str:
    payload = json.dumps(
        [provider_id, model_id, role.value, location, location_revision], separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def resolve_model_binding(role: LogicalRole) -> ModelBinding:
    """Resolve an optional role override, otherwise reuse the current Provider/model."""
    try:
        overrides = json.loads(db.get_setting("cognition_model_bindings", "{}") or "{}")
    except (TypeError, ValueError):
        overrides = {}
    try:
        current = json.loads(db.get_setting("current_model", "{}") or "{}")
    except (TypeError, ValueError):
        current = {}
    selected = overrides.get(role.value) if isinstance(overrides, dict) else None
    selected = selected if isinstance(selected, dict) else current
    provider_id = str(selected.get("provider_id") or current.get("provider_id") or "mock")
    model_id = str(selected.get("model") or current.get("model") or "xiadie-mock")
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM providers WHERE id=? AND enabled=1", (provider_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise cds.DecisionProtocolError("model_binding_unavailable", "configured Provider is unavailable")
    provider = dict(row)
    location = str(provider.get("execution_location") or "unknown")
    location_revision = max(1, int(provider.get("location_revision") or 1))
    return ModelBinding(
        provider=provider, model_id=model_id, logical_role=role,
        revision=_binding_revision(provider_id, model_id, role, location, location_revision),
    )


def get_certification(binding: ModelBinding, decision_kind: str,
                      protocol_version: str = cds.PROTOCOL_VERSION) -> CertificationLevel:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT certification_level FROM cognition_model_certifications WHERE "
            "provider_id=? AND model_id=? AND provider_location=? AND "
            "provider_location_revision=? AND logical_role=? AND decision_kind=? AND "
            "protocol_version=? AND model_binding_revision=?",
            (binding.provider_id, binding.model_id, binding.location,
             binding.location_revision, binding.logical_role.value, decision_kind,
             protocol_version, binding.revision),
        ).fetchone()
    finally:
        conn.close()
    return CertificationLevel(row[0]) if row else CertificationLevel.UNVERIFIED


def _record_certification(binding: ModelBinding, decision_kind: str,
                          level: CertificationLevel, error_code: str | None) -> None:
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO cognition_model_certifications("
            "id,provider_id,model_id,provider_location,provider_location_revision,logical_role,"
            "decision_kind,protocol_version,model_binding_revision,certification_level,"
            "probe_version,last_error_code,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(provider_id,model_id,provider_location,provider_location_revision,"
            "logical_role,decision_kind,protocol_version,model_binding_revision) DO UPDATE SET "
            "certification_level=excluded.certification_level,probe_version=excluded.probe_version,"
            "last_error_code=excluded.last_error_code,updated_at=excluded.updated_at",
            (db.new_id(), binding.provider_id, binding.model_id, binding.location,
             binding.location_revision, binding.logical_role.value, decision_kind,
             cds.PROTOCOL_VERSION, binding.revision, level.value, PROBE_VERSION,
             error_code, now, now),
        )
        conn.commit()
    finally:
        conn.close()


async def run_structured_probe(binding: ModelBinding, decision_kind: str) -> bool:
    """Probe exact structured output using synthetic data only."""
    payload = cds.ProtocolProbeInput(candidate_ids=("synthetic-a",))
    messages = [{
        "role": "user",
        "content": (
            "Synthetic protocol test. Return only JSON with action='select', "
            "selected_ids=['synthetic-a'], reason_codes=['directly_relevant'], "
            "confidence_band='high'. No user data is included."
        ),
    }]
    try:
        completion = await llm.complete_json(
            binding.provider, binding.model_id, messages,
            timeout_seconds=PROBE_TIMEOUT_SECONDS[binding.logical_role.value],
        )
        result, repaired = cds._decode_result_once(  # noqa: SLF001 - exact shared parser probe
            completion["text"], cds.ProtocolProbeResult,
        )
        cds.REGISTRY.get("protocol_probe").validator(payload, result)
        if repaired:
            raise cds.DecisionProtocolError("probe_required_repair", "probe was not exact JSON")
    except Exception as exc:
        code = getattr(exc, "code", None) or "structured_probe_failed"
        _record_certification(binding, decision_kind, CertificationLevel.UNVERIFIED, code)
        return False
    _record_certification(binding, decision_kind, CertificationLevel.STRUCTURED_CAPABLE, None)
    return True


def certification_allows(level: CertificationLevel, mode: cds.DecisionMode) -> bool:
    required = (
        CertificationLevel.STRUCTURED_CAPABLE
        if mode is cds.DecisionMode.SHADOW else CertificationLevel.DECISION_VERIFIED
    )
    return _CERT_RANK[level] >= _CERT_RANK[required]


def privacy_error(binding: ModelBinding, privacy_class: str,
                  level: CertificationLevel) -> str | None:
    """Fail closed for body-bearing cognition until its location has explicit certification."""
    if privacy_class in {"body_free", "synthetic_body_free"}:
        return None
    if binding.location == "unknown":
        return "provider_location_unknown"
    if binding.location == "remote":
        return "remote_cognition_not_authorized"
    if level is not CertificationLevel.LOCAL_SENSITIVE_VERIFIED:
        return "local_sensitive_model_not_certified"
    return None


def _breaker_key(binding: ModelBinding, decision_kind: str) -> tuple[str, ...]:
    return (binding.provider_id, binding.model_id, decision_kind, cds.PROTOCOL_VERSION, binding.revision)


def circuit_allows(binding: ModelBinding, decision_kind: str, *, now: float | None = None) -> bool:
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM cognition_circuit_breakers WHERE provider_id=? AND model_id=? "
            "AND decision_kind=? AND protocol_version=? AND model_binding_revision=?",
            _breaker_key(binding, decision_kind),
        ).fetchone()
        if not row or row["state"] == "closed":
            return True
        if row["state"] == "open" and float(row["open_until"] or 0) <= now:
            conn.execute(
                "UPDATE cognition_circuit_breakers SET state='half_open',updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            conn.commit()
            return True
        return False
    finally:
        conn.close()


def record_circuit_result(binding: ModelBinding, decision_kind: str, *, success: bool,
                          error_code: str | None = None, threshold: int = 3,
                          cooldown_seconds: float = 60, now: float | None = None) -> None:
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM cognition_circuit_breakers WHERE provider_id=? AND model_id=? "
            "AND decision_kind=? AND protocol_version=? AND model_binding_revision=?",
            _breaker_key(binding, decision_kind),
        ).fetchone()
        failures = 0 if success else int(row["consecutive_failures"] if row else 0) + 1
        state = "closed" if success or failures < threshold else "open"
        open_until = now + cooldown_seconds if state == "open" else None
        values = (*_breaker_key(binding, decision_kind), state, failures, open_until,
                  None if success else error_code, now)
        conn.execute(
            "INSERT INTO cognition_circuit_breakers("
            "id,provider_id,model_id,decision_kind,protocol_version,model_binding_revision,state,"
            "consecutive_failures,open_until,last_error_code,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(provider_id,model_id,decision_kind,protocol_version,model_binding_revision) "
            "DO UPDATE SET state=excluded.state,consecutive_failures=excluded.consecutive_failures,"
            "open_until=excluded.open_until,last_error_code=excluded.last_error_code,"
            "updated_at=excluded.updated_at",
            (db.new_id(), *values),
        )
        conn.commit()
    finally:
        conn.close()


@dataclass(frozen=True)
class BudgetPolicy:
    rolling_seconds: float = 3600
    rolling_tokens: int = 20_000
    daily_tokens: int = 100_000
    local_concurrency: int = 2
    remote_concurrency: int = 1
    foreground_latency_ms: int = 2_500


class CognitionBudgetGovernor:
    def __init__(self, policy: BudgetPolicy | None = None):
        self.policy = policy or BudgetPolicy()
        self._started: set[str] = set()

    def authorize(self, *, task_id: str, decision_kind: str, role: LogicalRole,
                  location: str, priority: TaskPriority, estimated_tokens: int,
                  network_online: bool = True, battery_saver: bool = False,
                  foreground_latency_ms: int = 0, now: float | None = None) -> tuple[bool, str | None]:
        now = db.now() if now is None else now
        error: str | None = None
        if location == "remote" and not network_online:
            error = "cognition_network_offline"
        elif priority is TaskPriority.BACKGROUND and battery_saver:
            error = "cognition_battery_saver"
        elif priority is not TaskPriority.FOREGROUND and foreground_latency_ms > self.policy.foreground_latency_ms:
            error = "cognition_foreground_pressure"
        conn = db.connect()
        try:
            active = conn.execute(
                "SELECT COUNT(*) FROM cognition_budget_events WHERE status='authorized' "
                "AND provider_location=?", (location,),
            ).fetchone()[0]
            limit = self.policy.local_concurrency if location == "local" else self.policy.remote_concurrency
            if error is None and active >= limit:
                error = "cognition_concurrency_limit"
            rolling = conn.execute(
                "SELECT COALESCE(SUM(COALESCE(actual_tokens,estimated_tokens)),0) "
                "FROM cognition_budget_events WHERE created_at>=? AND status IN ('authorized','completed')",
                (now - self.policy.rolling_seconds,),
            ).fetchone()[0]
            day_start = now - (now % 86400)
            daily = conn.execute(
                "SELECT COALESCE(SUM(COALESCE(actual_tokens,estimated_tokens)),0) "
                "FROM cognition_budget_events WHERE created_at>=? AND status IN ('authorized','completed')",
                (day_start,),
            ).fetchone()[0]
            if error is None and rolling + estimated_tokens > self.policy.rolling_tokens:
                error = "cognition_rolling_budget"
            if error is None and daily + estimated_tokens > self.policy.daily_tokens:
                error = "cognition_daily_budget"
            status = "authorized" if error is None else "rejected"
            conn.execute(
                "INSERT INTO cognition_budget_events(id,task_id,decision_kind,logical_role,"
                "provider_location,priority,status,estimated_tokens,error_code,created_at,completed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (db.new_id(), task_id, decision_kind, role.value, location, priority.value,
                 status, max(0, int(estimated_tokens)), error, now, now if error else None),
            )
            conn.commit()
        finally:
            conn.close()
        return error is None, error

    def cancel_pending_for_user_message(self, *, now: float | None = None) -> list[str]:
        """Cancel only not-started low-priority diary/PWM/offline refinements."""
        conn = db.connect()
        try:
            rows = conn.execute(
                "SELECT task_id FROM cognition_budget_events WHERE status='authorized' "
                "AND priority='background' AND decision_kind IN ('diary','pwm','offline_refinement')"
            ).fetchall()
            cancelled = [row["task_id"] for row in rows if row["task_id"] not in self._started]
            if cancelled:
                placeholders = ",".join("?" for _ in cancelled)
                conn.execute(
                    f"UPDATE cognition_budget_events SET status='cancelled',error_code=?,completed_at=? "
                    f"WHERE task_id IN ({placeholders})",
                    ("cancelled_for_user_message", db.now() if now is None else now, *cancelled),
                )
                conn.commit()
            return cancelled
        finally:
            conn.close()

    def mark_started(self, task_id: str) -> None:
        self._started.add(task_id)

    def complete(self, task_id: str, *, actual_tokens: int = 0,
                 error_code: str | None = None, now: float | None = None) -> None:
        self._started.discard(task_id)
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE cognition_budget_events SET status='completed',actual_tokens=?,"
                "error_code=?,completed_at=? WHERE task_id=? AND status='authorized'",
                (max(0, int(actual_tokens)), error_code, db.now() if now is None else now, task_id),
            )
            conn.commit()
        finally:
            conn.close()


def recover_control_plane(*, now: float | None = None, stale_after_seconds: float = 3600,
                          retention_seconds: float = 30 * 86400) -> dict[str, int]:
    """Release crashed reservations and prune old terminal body-free events."""
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        recovered = conn.execute(
            "UPDATE cognition_budget_events SET status='cancelled',"
            "error_code='runtime_recovered',completed_at=? "
            "WHERE status='authorized' AND created_at<?",
            (now, now - max(1.0, stale_after_seconds)),
        ).rowcount
        deleted = conn.execute(
            "DELETE FROM cognition_budget_events WHERE status IN "
            "('completed','rejected','cancelled') AND completed_at<?",
            (now - max(1.0, retention_seconds),),
        ).rowcount
        conn.commit()
        return {"recovered": recovered, "deleted": deleted}
    finally:
        conn.close()

DEFAULT_GOVERNOR = CognitionBudgetGovernor()


async def execute_registered_decision(
    header: cds.CommonDecisionHeader, payload: Any, candidates: tuple[cds.CandidateRef, ...],
    *, current_snapshot: tuple[cds.SourceSnapshot, ...], role: LogicalRole,
    priority: TaskPriority = TaskPriority.NORMAL, governor: CognitionBudgetGovernor | None = None,
    estimated_tokens: int = 500, network_online: bool = True, battery_saver: bool = False,
) -> dict[str, Any]:
    """Execute one registered decision; every infrastructure failure returns its fallback."""
    governor = governor or DEFAULT_GOVERNOR
    definition = cds.REGISTRY.get(header.decision_kind)
    try:
        binding = resolve_model_binding(role)
    except cds.DecisionProtocolError:
        run, _ = cds.create_run(header, payload, candidates, logical_role=role.value)
        return cds.evaluate_failure(run.id, header, payload, error_code="model_binding_unavailable")
    level = get_certification(binding, header.decision_kind)
    if level is CertificationLevel.UNVERIFIED:
        await run_structured_probe(binding, header.decision_kind)
        level = get_certification(binding, header.decision_kind)
    run, _ = cds.create_run(
        header, payload, candidates, provider_id=binding.provider_id, model_id=binding.model_id,
        provider_location=binding.location, provider_location_revision=binding.location_revision,
        logical_role=role.value, certification_level=level.value,
        model_binding_revision=binding.revision, temperature=0.0, top_p=1.0,
    )
    if not certification_allows(level, header.mode):
        return cds.evaluate_failure(run.id, header, payload, error_code="model_not_certified")
    location_error = privacy_error(binding, definition.privacy_class, level)
    if location_error:
        return cds.evaluate_failure(run.id, header, payload, error_code=location_error)
    if not circuit_allows(binding, header.decision_kind):
        return cds.evaluate_failure(run.id, header, payload, error_code="circuit_open")
    task_id = run.id
    allowed, budget_error = governor.authorize(
        task_id=task_id, decision_kind=header.decision_kind, role=role,
        location=binding.location, priority=priority, estimated_tokens=estimated_tokens,
        network_online=network_online, battery_saver=battery_saver,
    )
    if not allowed:
        return cds.evaluate_failure(run.id, header, payload, error_code=budget_error or "budget_rejected")
    governor.mark_started(task_id)
    try:
        messages = [{"role": "user", "content": json.dumps(payload.__dict__, ensure_ascii=False)}]
        completion = await asyncio.wait_for(
            llm.complete_json(
                binding.provider, binding.model_id, messages,
                timeout_seconds=definition.timeout_seconds, temperature=0.0, top_p=1.0,
            ),
            timeout=definition.timeout_seconds + 0.1,
        )
        outcome = cds.evaluate_output(
            run.id, header, payload, completion["text"], current_snapshot=current_snapshot,
            latency_ms=completion.get("latency_ms"), input_tokens=completion.get("prompt_tokens"),
            output_tokens=completion.get("completion_tokens"),
        )
        success = not outcome["fallback_used"]
        record_circuit_result(
            binding, header.decision_kind, success=success,
            error_code=outcome.get("error_code"),
        )
        governor.complete(
            task_id, actual_tokens=(completion.get("prompt_tokens") or 0)
            + (completion.get("completion_tokens") or 0), error_code=outcome.get("error_code"),
        )
        return outcome
    except Exception as exc:
        code = getattr(exc, "code", None) or (
            "cognition_timeout" if isinstance(exc, asyncio.TimeoutError) else "cognition_provider_error"
        )
        record_circuit_result(binding, header.decision_kind, success=False, error_code=code)
        governor.complete(task_id, error_code=code)
        return cds.evaluate_failure(run.id, header, payload, error_code=code)
