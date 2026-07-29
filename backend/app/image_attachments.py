"""CIE.3 ephemeral image validation and local byte storage."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import struct

from . import db

PROTOCOL_VERSION = "cie-image-attachment-v1"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES_PER_TURN = 4
MAX_DIMENSION = 4096
MAX_TOTAL_PIXELS = 16_000_000
TTL_SECONDS = 3600.0
ALLOWED_MIME_TYPES = frozenset({"image/png", "image/jpeg"})


class ImageAttachmentError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def storage_dir() -> Path:
    path = Path(db.DATA_DIR) / "chat-images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def inspect_image(data: bytes, declared_mime: str) -> dict:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageAttachmentError("image_size_invalid", "图片必须小于等于 5 MiB")
    mime, width, height = _dimensions(data)
    normalized_declared = declared_mime.split(";", 1)[0].strip().lower()
    if normalized_declared not in ALLOWED_MIME_TYPES or normalized_declared != mime:
        raise ImageAttachmentError("image_mime_mismatch", "图片 MIME 与文件内容不一致")
    if width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_TOTAL_PIXELS:
        raise ImageAttachmentError("image_dimensions_exceeded", "图片尺寸超过 4096 或 1600 万像素限制")
    return {
        "mime_type": mime,
        "byte_count": len(data),
        "pixel_width": width,
        "pixel_height": height,
        "content_sha256": hashlib.sha256(data).hexdigest(),
    }


def save(attachment_id: str, data: bytes) -> str:
    if not attachment_id or not attachment_id.isalnum():
        raise ImageAttachmentError("image_id_invalid", "图片标识无效")
    path = _safe_path(f"{attachment_id}.bin")
    temporary = _safe_path(f".{attachment_id}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path.name


def load_data_url(storage_name: str, mime_type: str) -> str:
    path = _safe_path(storage_name)
    data = path.read_bytes()
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def remove(storage_name: str | None) -> None:
    if not storage_name:
        return
    try:
        _safe_path(storage_name).unlink(missing_ok=True)
    except (OSError, ImageAttachmentError):
        pass


def cleanup_expired(now: float | None = None) -> int:
    current = db.now() if now is None else float(now)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id,storage_path FROM message_attachments"
            " WHERE attachment_kind='image' AND storage_path IS NOT NULL AND expires_at<=?",
            (current,),
        ).fetchall()
        for row in rows:
            remove(row["storage_path"])
        conn.executemany(
            "UPDATE message_attachments SET storage_path=NULL WHERE id=?",
            [(row["id"],) for row in rows],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def _safe_path(storage_name: str) -> Path:
    if Path(storage_name).name != storage_name:
        raise ImageAttachmentError("image_storage_path_invalid", "图片临时路径无效")
    root = storage_dir().resolve()
    path = (root / storage_name).resolve()
    if path.parent != root:
        raise ImageAttachmentError("image_storage_path_invalid", "图片临时路径越界")
    return path


def _dimensions(data: bytes) -> tuple[str, int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        if width and height:
            return "image/png", width, height
    if data.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(data):
                break
            length = int.from_bytes(data[index:index + 2], "big")
            if length < 2 or index + length > len(data):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                height = int.from_bytes(data[index + 3:index + 5], "big")
                width = int.from_bytes(data[index + 5:index + 7], "big")
                if width and height:
                    return "image/jpeg", width, height
            index += length
    raise ImageAttachmentError("image_format_unsupported", "仅支持内容有效的 PNG/JPEG 图片")
