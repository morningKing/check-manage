# -*- coding: utf-8 -*-
"""智能写作附件：图片 magic bytes、磁盘存储、DB 元数据。"""
import os
import shutil
import uuid
from pathlib import Path

import config
from db import get_db

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ALLOWED_EXT = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/gif': '.gif',
    'image/webp': '.webp',
}


def detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def attachment_dir(doc_id: str) -> Path:
    root = Path(os.path.realpath(os.path.join(config.SE_DATA_DIR, 'attachments')))
    target = Path(os.path.realpath(root / doc_id))
    if target != root / doc_id or root not in target.parents:
        raise ValueError('invalid document id')
    return target


def save_attachment(doc_id: str, owner_id: str, filename: str, data: bytes) -> dict:
    if not data or len(data) > MAX_ATTACHMENT_BYTES:
        raise ValueError('图片大小不合法（上限 10MB）')
    mime = detect_image_mime(data)
    if mime not in ALLOWED_EXT:
        raise ValueError('仅支持 png/jpeg/gif/webp 图片')
    att_id = f'att_{uuid.uuid4().hex}'
    target_dir = attachment_dir(doc_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f'{att_id}{ALLOWED_EXT[mime]}'
    path.write_bytes(data)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO se_attachments "
                "(id, document_id, owner_user_id, filename, mime, size, stored_path) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (att_id, doc_id, owner_id, filename[:255], mime, len(data), str(path)))
        conn.commit()
    return {'id': att_id, 'url': f'/suggest-editor/attachments/{att_id}',
            'filename': filename, 'mime': mime, 'size': len(data),
            'stored_path': str(path)}


def get_attachment(attachment_id: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, document_id, owner_user_id, filename, mime, size, stored_path "
                "FROM se_attachments WHERE id = %s", (attachment_id,))
            row = cur.fetchone()
    if not row:
        return None
    keys = ('id', 'document_id', 'owner_user_id', 'filename', 'mime', 'size', 'stored_path')
    return dict(zip(keys, row))


def delete_document_attachments(doc_id: str) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM se_attachments WHERE document_id = %s", (doc_id,))
        conn.commit()
    target = attachment_dir(doc_id)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
