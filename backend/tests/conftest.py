"""所有测试导入 app 之前统一隔离数据目录和本地 API 令牌。"""
import os
import tempfile

import pytest

os.environ["XIADIE_DATA_DIR"] = tempfile.mkdtemp(prefix="xiadie-test-")
os.environ["XIADIE_API_TOKEN"] = "test-token-with-at-least-thirty-two-bytes"


@pytest.fixture(autouse=True)
def reset_proactive_clock_watermark():
    """Each test owns its simulated clock; production still persists the watermark."""
    from app import db

    db.init_db()
    db.set_setting("proactive_last_reliable_now", "0")
    db.set_setting("proactive_resume_guard_until", "0")
    yield
