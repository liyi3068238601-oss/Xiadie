"""开发期启动入口：python run.py（默认 127.0.0.1:8756）。"""
import os

import uvicorn

if __name__ == "__main__":
    # 只为本机 Vite 开发页开放严格来源兼容；run_frozen/正式包不会设置此项。
    os.environ.setdefault("XIADIE_DEV_MODE", "1")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8756, reload=True)
