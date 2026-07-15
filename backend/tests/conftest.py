"""所有测试导入 app 之前统一隔离数据目录和本地 API 令牌。"""
import os
import tempfile

os.environ["XIADIE_DATA_DIR"] = tempfile.mkdtemp(prefix="xiadie-test-")
os.environ["XIADIE_API_TOKEN"] = "test-token-with-at-least-thirty-two-bytes"
