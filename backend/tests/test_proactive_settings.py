"""EAP v0.2 主动陪伴默认设置测试（spec 第 3.4 节）。"""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.proactive import settings

client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"}
)


@pytest.fixture(autouse=True)
def reset_proactive_settings():
    """每个测试前清除 proactive 设置并重新初始化，确保默认值生效。"""
    db.init_db()
    conn = db.connect()
    try:
        conn.execute("DELETE FROM settings WHERE key LIKE 'proactive_%'")
        conn.commit()
    finally:
        conn.close()
    db.init_db()
    yield


def test_proactive_enabled_defaults_to_on():
    assert db.get_setting("proactive_enabled") == "1"


def test_proactive_desktop_notification_defaults_to_off():
    assert db.get_setting("proactive_desktop_notification_enabled") == "0"


def test_proactive_external_channels_defaults_to_off():
    assert db.get_setting("proactive_external_channels_enabled") == "0"


def test_proactive_settings_can_be_overridden():
    # 用户显式关闭
    db.set_setting("proactive_enabled", "0")
    assert db.get_setting("proactive_enabled") == "0"
    # INSERT OR IGNORE must preserve explicit user choice.
    db.init_db()
    assert db.get_setting("proactive_enabled") == "0"


def test_registry_defaults_are_persisted():
    for key, default in settings.DEFAULTS.items():
        assert db.get_setting(key) == default


@pytest.mark.parametrize("key,value", [
    ("proactive_enabled", "yes"),
    ("proactive_quiet_hours_start", "24"),
    ("proactive_frequency_mode", "unlimited"),
    ("proactive_pause_until", "2030-01-01T12:00:00"),
    ("proactive_external_channels_enabled", "1"),
])
def test_invalid_public_values_are_rejected(key, value):
    response = client.put(f"/api/settings/{key}", json={"value": value})
    assert response.status_code == 400


def test_pause_is_normalized_persisted_and_blocks_until_expiry():
    response = client.put(
        "/api/settings/proactive_pause_until",
        json={"value": "2030-01-01T08:00:00+08:00"},
    )
    assert response.status_code == 200
    assert response.json()["value"] == "2030-01-01T00:00:00Z"
    blocked = settings.effective_policy(now=1_800_000_000)
    expired = settings.effective_policy(now=2_000_000_000)
    assert "proactive_paused" in blocked.blocked_reasons
    assert "proactive_paused" not in expired.blocked_reasons


def test_clock_rollback_and_kind_switch_fail_closed():
    policy = settings.effective_policy(
        now=100, last_seen_now=101, candidate_kind="emotional_care",
        overrides={"proactive_kind_emotional_care_enabled": "0"},
    )
    assert policy.allows_non_silent is False
    assert set(policy.blocked_reasons) >= {"clock_rollback", "candidate_kind_disabled"}


def test_corrupt_emergency_and_pause_values_fail_closed():
    emergency = settings.effective_policy(overrides={"proactive_emergency_stop": "corrupt"})
    pause = settings.effective_policy(overrides={"proactive_pause_until": "corrupt"})
    assert "emergency_stop" in emergency.blocked_reasons
    assert "invalid_pause_until" in pause.blocked_reasons


def test_frequency_profiles_are_conservative():
    restrained = settings.effective_policy(overrides={"proactive_frequency_mode": "restrained"})
    standard = settings.effective_policy(overrides={"proactive_frequency_mode": "standard"})
    custom = settings.effective_policy(overrides={"proactive_frequency_mode": "custom"})
    assert restrained.frequency_cost_addition > standard.frequency_cost_addition
    assert custom.frequency_cost_addition > standard.frequency_cost_addition
