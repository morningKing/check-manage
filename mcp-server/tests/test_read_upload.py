"""Tests for tools.read_upload."""

import os
from unittest.mock import patch
import pytest


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def _mk_uploads(tmp_path):
    up = tmp_path / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    return up


def test_lists_uploads_when_no_filename(fake_db, mock_cursor, tmp_path):
    up = _mk_uploads(tmp_path)
    (up / "a.txt").write_text("x", encoding="utf-8")
    (up / "b.csv").write_text("y", encoding="utf-8")
    mock_cursor.fetchone.return_value = (str(tmp_path),)
    with patch("tools.read_upload.get_db", fake_db):
        from tools.read_upload import handle
        res = handle({}, _ctx())
    assert sorted(res["files"]) == ["a.txt", "b.csv"]


def test_reads_text_file(fake_db, mock_cursor, tmp_path):
    up = _mk_uploads(tmp_path)
    (up / "config.txt").write_text("磁盘阈值 75%", encoding="utf-8")
    mock_cursor.fetchone.return_value = (str(tmp_path),)
    with patch("tools.read_upload.get_db", fake_db):
        from tools.read_upload import handle
        res = handle({"filename": "config.txt"}, _ctx())
    assert res["found"] is True
    assert res["content"] == "磁盘阈值 75%"


def test_missing_file_returns_available_list(fake_db, mock_cursor, tmp_path):
    up = _mk_uploads(tmp_path)
    (up / "real.txt").write_text("x", encoding="utf-8")
    mock_cursor.fetchone.return_value = (str(tmp_path),)
    with patch("tools.read_upload.get_db", fake_db):
        from tools.read_upload import handle
        res = handle({"filename": "ghost.txt"}, _ctx())
    assert res["found"] is False
    assert "real.txt" in res["available"]


def test_path_traversal_confined_to_uploads(fake_db, mock_cursor, tmp_path):
    _mk_uploads(tmp_path)
    # a secret outside uploads/ must not be readable
    (tmp_path / "secret.txt").write_text("TOPSECRET", encoding="utf-8")
    mock_cursor.fetchone.return_value = (str(tmp_path),)
    with patch("tools.read_upload.get_db", fake_db):
        from tools.read_upload import handle
        # basename strips the ../, so it looks for uploads/secret.txt (absent)
        res = handle({"filename": "../secret.txt"}, _ctx())
    assert res.get("found") is False
    assert "TOPSECRET" not in str(res)


def test_binary_file_reports_non_text(fake_db, mock_cursor, tmp_path):
    up = _mk_uploads(tmp_path)
    (up / "img.bin").write_bytes(b"\xff\xfe\x00\x01\x02binary")
    mock_cursor.fetchone.return_value = (str(tmp_path),)
    with patch("tools.read_upload.get_db", fake_db):
        from tools.read_upload import handle
        res = handle({"filename": "img.bin"}, _ctx())
    assert res.get("binary") is True
    assert "content" not in res
