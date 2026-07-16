"""影子召回后台线程；检索或模型冷启动绝不占用聊天事件循环。"""
from __future__ import annotations

import queue
import threading

from . import knowledge_recall

_queue: queue.Queue[tuple[str, str, dict] | None] = queue.Queue(maxsize=128)
_thread: threading.Thread | None = None
_lock = threading.Lock()


def start_worker() -> None:
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, name="xiadie-knowledge-recall", daemon=True)
        _thread.start()


def stop_worker() -> None:
    global _thread
    thread = _thread
    if thread and thread.is_alive():
        try:
            _queue.put_nowait(None)
        except queue.Full:
            pass
        thread.join(timeout=0.5)
    if not thread or not thread.is_alive():
        _thread = None


def enqueue(decision_id: str, user_text: str, provider: dict) -> None:
    start_worker()
    try:
        _queue.put_nowait((decision_id, user_text, dict(provider)))
    except queue.Full:
        knowledge_recall.fail(decision_id)


def _loop() -> None:
    while True:
        item = _queue.get()
        if item is None:
            return
        decision_id, user_text, provider = item
        try:
            result = knowledge_recall.evaluate(user_text, provider)
            if result["latency_ms"] > knowledge_recall.TIMEOUT_MS:
                knowledge_recall.fail(decision_id, timed_out=True)
            else:
                knowledge_recall.complete(decision_id, result)
        except Exception:  # noqa: BLE001 - 诊断记录不能包含底层敏感异常
            knowledge_recall.fail(decision_id)
