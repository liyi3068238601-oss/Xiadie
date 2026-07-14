"""PyInstaller 冻结入口：直接导入 app 对象运行 uvicorn（不用字符串导入/reload，冻结更稳）。"""
import os
import sys

# 无控制台窗口（console=False）冻结时 stdout/stderr 会是 None，
# uvicorn 的日志处理器写入 None 会在启动时崩溃。给它们一个可写兜底目标。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import uvicorn

from app.main import app

if __name__ == "__main__":
    port = int(os.environ.get("XIADIE_PORT", "8756"))
    # 冻结环境不能用 reload；用 app 对象直接跑。
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
