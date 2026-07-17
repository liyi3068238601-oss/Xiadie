"""SecretStore 抽象接口与开发期实现。

API Key 不再直接从 providers 表读写，而是通过 SecretStore 存取。
当前阶段提供 SqliteSecretStore（开发期兼容层），向后兼容现有数据库。
正式包使用 Electron safeStorage 实现，密钥永远不出现在数据库正文中。
"""
from __future__ import annotations

import abc
import os
import sqlite3
from pathlib import Path

from . import db

_DATA_DIR = Path(os.environ.get("XIADIE_DATA_DIR", db.DATA_DIR))


class SecretStore(abc.ABC):
    """Secret 存储的抽象接口。"""

    @abc.abstractmethod
    def store(self, key_id: str, value: str) -> None:
        """写入 secret。key_id 是稳定的引用键（如 provider_id）。"""

    @abc.abstractmethod
    def retrieve(self, key_id: str) -> str | None:
        """读取 secret；不存在时返回 None。"""

    @abc.abstractmethod
    def delete(self, key_id: str) -> None:
        """删除 secret。"""

    @abc.abstractmethod
    def has(self, key_id: str) -> bool:
        """是否存在该 key_id 的 secret。"""


class InMemorySecretStore(SecretStore):
    """仅用于测试的内存实现。"""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def store(self, key_id: str, value: str) -> None:
        self._store[key_id] = value

    def retrieve(self, key_id: str) -> str | None:
        return self._store.get(key_id)

    def delete(self, key_id: str) -> None:
        self._store.pop(key_id, None)

    def has(self, key_id: str) -> bool:
        return key_id in self._store


class SqliteSecretStore(SecretStore):
    """开发期兼容实现：把密钥保存在独立 SQLite 表中。

    表结构和 providers 表隔离，但存储在同一数据库文件中。
    这仍然是不加密的——仅作为正式 safeStorage 就绪前的过渡方案。
    正式包必须使用 ElectronSecretStore。
    """

    TABLE = "secret_store"

    def __init__(self) -> None:
        self._ensure_table()

    def _ensure_table(self) -> None:
        conn = db.connect()
        try:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {self.TABLE} (key_id TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    def store(self, key_id: str, value: str) -> None:
        self._ensure_table()
        conn = db.connect()
        try:
            conn.execute(
                f"INSERT OR REPLACE INTO {self.TABLE}(key_id,value) VALUES(?,?)",
                (key_id, value),
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    def retrieve(self, key_id: str) -> str | None:
        conn = db.connect()
        try:
            row = conn.execute(
                f"SELECT value FROM {self.TABLE} WHERE key_id=?", (key_id,)
            ).fetchone()
            return row["value"] if row else None
        finally:
            conn.close()

    def delete(self, key_id: str) -> None:
        conn = db.connect()
        try:
            conn.execute(f"DELETE FROM {self.TABLE} WHERE key_id=?", (key_id,))
            conn.commit()
        finally:
            conn.close()

    def has(self, key_id: str) -> bool:
        return self.retrieve(key_id) is not None


_store: SecretStore | None = None


def get_store() -> SecretStore:
    global _store
    if _store is None:
        _store = SqliteSecretStore()
    return _store


def _reset_store_for_tests() -> None:
    global _store
    _store = None
