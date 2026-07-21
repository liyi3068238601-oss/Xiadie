"""EAP v0.2 主动陪伴默认设置测试（spec 第 3.4 节）。"""
import pytest

from app import db


@pytest.fixture(autouse=True)
def reset_proactive_settings():
    """每个测试前清除 proactive 设置并重新初始化，确保默认值生效。"""
    db.init_db()
    conn = db.connect()
    try:
        conn.execute(
            "DELETE FROM settings WHERE key IN ("
            "'proactive_enabled', 'proactive_desktop_notification_enabled',"
            " 'proactive_external_channels_enabled')"
        )
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
    # 再次调用 init_db() 不应覆盖已设置的值（INSERT OR IGNORE 语义）
    db.init_db()
    assert db.get_setting("proactive_enabled") == "0"
