"""开发期启动入口：python run.py（默认 127.0.0.1:8756）。"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8756, reload=True)
