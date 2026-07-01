"""Tests for tools.save_artifact."""

from unittest.mock import patch
import pytest


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def test_writes_into_outputs(fake_db, mock_cursor, tmp_path):
    mock_cursor.fetchone.return_value = (str(tmp_path),)
    with patch("tools.save_artifact.get_db", fake_db):
        from tools.save_artifact import handle
        res = handle({"filename": "check.py", "content": "print(1)"}, _ctx())
    assert res["saved"] is True
    assert res["path"] == "outputs/check.py"
    assert (tmp_path / "outputs" / "check.py").read_text(encoding="utf-8") == "print(1)"


def test_strips_path_traversal(fake_db, mock_cursor, tmp_path):
    mock_cursor.fetchone.return_value = (str(tmp_path),)
    with patch("tools.save_artifact.get_db", fake_db):
        from tools.save_artifact import handle
        res = handle({"filename": "../../evil.py", "content": "x"}, _ctx())
    assert res["path"] == "outputs/evil.py"
    assert (tmp_path / "outputs" / "evil.py").exists()
    assert not (tmp_path.parent / "evil.py").exists()


def test_guest_blocked(tmp_path):
    from tools.save_artifact import handle, SaveArtifactError
    with pytest.raises(SaveArtifactError):
        handle({"filename": "a.py", "content": "x"}, _ctx("guest"))


def test_kefu_guest_blocked():
    from tools.save_artifact import handle, SaveArtifactError
    with pytest.raises(SaveArtifactError):
        handle({"filename": "a.py", "content": "x"}, _ctx("kefu-guest"))


def test_missing_args(fake_db, mock_cursor):
    from tools.save_artifact import handle, SaveArtifactError
    with patch("tools.save_artifact.get_db", fake_db):
        with pytest.raises(SaveArtifactError):
            handle({"filename": ""}, _ctx())
