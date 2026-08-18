"""AI 相关端点（批任务 / 单会话 / Row Actions / AI 定时任务 / Prompt 模板 / 记忆）的客户端测试。

独立于 test_client.py（该文件测最基础的集合/文件 API），避免单文件过大。
FakeResponse/make_client 是同一套模式的独立副本，不改动 test_client.py。
"""
import json
from unittest.mock import MagicMock

import pytest

from checkmanage_openapi import (
    ConflictError,
    MemoryUnavailableError,
    NotFoundError,
    OpenApiClient,
    ValidationError,
)


class FakeResponse:
    def __init__(self, status_code, json_data=None, content=b""):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.text = json.dumps(json_data) if json_data is not None else ""

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


def make_client(fake_request):
    session = MagicMock()
    session.request.side_effect = fake_request
    client = OpenApiClient(api_key="cm_test", base_url="http://x/api/v1", session=session)
    return client, session


# ---------------------------------------------------------------------------
# AI 批任务
# ---------------------------------------------------------------------------

def test_create_batch_sends_camel_case_body():
    def fake_request(method, url, **kwargs):
        assert method == "POST"
        assert url == "http://x/api/v1/ai-batches"
        assert kwargs["json"] == {
            "name": "n", "prompt": "p", "files": [{"name": "a", "path": "batch-staging/u/a"}],
            "agent": "build", "callbackUrl": "https://example.com/hook",
            "callbackSecret": "s3cret",
        }
        return FakeResponse(201, {"batchId": "b1", "status": "pending", "total": 1})

    client, _ = make_client(fake_request)
    result = client.create_batch(
        "n", "p", [{"name": "a", "path": "batch-staging/u/a"}],
        agent="build", callback_url="https://example.com/hook", callback_secret="s3cret",
    )
    assert result == {"batchId": "b1", "status": "pending", "total": 1}


def test_create_batch_omits_unset_optional_fields():
    def fake_request(method, url, **kwargs):
        assert kwargs["json"] == {"name": "n", "prompt": "p", "files": []}
        return FakeResponse(201, {"batchId": "b1", "status": "pending", "total": 0})

    client, _ = make_client(fake_request)
    client.create_batch("n", "p", [])


def test_upload_batch_files_sends_repeated_files_field(tmp_path):
    f1 = tmp_path / "a.txt"
    f1.write_bytes(b"aaa")
    f2 = tmp_path / "b.txt"
    f2.write_bytes(b"bbb")

    def fake_request(method, url, **kwargs):
        assert url.endswith("/ai-batches/uploads")
        files = kwargs["files"]
        assert [name for name, _ in files] == ["files", "files"]
        names_seen = {tup[0] for _, tup in files}
        assert names_seen == {"a.txt", "b.txt"}
        return FakeResponse(
            201,
            {"files": [{"name": "a.txt", "path": "batch-staging/u/1/a.txt"},
                      {"name": "b.txt", "path": "batch-staging/u/1/b.txt"}]},
        )

    client, _ = make_client(fake_request)
    result = client.upload_batch_files([f1, f2])
    assert len(result["files"]) == 2


def test_upload_batch_files_closes_opened_handles(tmp_path):
    """路径打开的文件句柄用完要关掉；传入的已打开对象不应被关闭（跟
    upload_file 对单文件的既有约定一致）。"""
    f1 = tmp_path / "a.txt"
    f1.write_bytes(b"aaa")
    already_open = tmp_path / "b.txt"
    already_open.write_bytes(b"bbb")
    fh = open(already_open, "rb")

    def fake_request(method, url, **kwargs):
        return FakeResponse(201, {"files": []})

    client, _ = make_client(fake_request)
    try:
        client.upload_batch_files([f1, (fh, "b.txt")])
        assert not fh.closed
    finally:
        fh.close()


def test_list_batches_passes_pagination_params():
    def fake_request(method, url, **kwargs):
        assert kwargs["params"] == {"page": 2, "pageSize": 10}
        return FakeResponse(200, {"items": [{"batchId": "b1"}], "total": 11})

    client, _ = make_client(fake_request)
    body = client.list_batches(page=2, page_size=10)
    assert body["total"] == 11


def test_iter_batches_stops_using_total_not_total_pages():
    """list_batches() 的信封没有 totalPages，iter_batches 必须靠累计条数
    对比 total 来判断终止，不能照抄 iter_records 比较页码的写法。"""
    pages_requested = []

    def fake_request(method, url, **kwargs):
        page = kwargs["params"]["page"]
        pages_requested.append(page)
        if page == 1:
            return FakeResponse(200, {"items": [{"batchId": "a"}], "total": 2})
        return FakeResponse(200, {"items": [{"batchId": "b"}], "total": 2})

    client, _ = make_client(fake_request)
    items = list(client.iter_batches(page_size=1))
    assert [i["batchId"] for i in items] == ["a", "b"]
    assert pages_requested == [1, 2]


def test_iter_batches_empty_page_stops_immediately():
    def fake_request(method, url, **kwargs):
        return FakeResponse(200, {"items": [], "total": 0})

    client, _ = make_client(fake_request)
    assert list(client.iter_batches()) == []


def test_get_batch():
    def fake_request(method, url, **kwargs):
        assert url.endswith("/ai-batches/b1")
        return FakeResponse(200, {"batchId": "b1", "status": "completed"})

    client, _ = make_client(fake_request)
    assert client.get_batch("b1")["status"] == "completed"


def test_get_batch_results():
    def fake_request(method, url, **kwargs):
        assert url.endswith("/ai-batches/b1/results")
        return FakeResponse(200, {"batchId": "b1", "status": "completed", "results": []})

    client, _ = make_client(fake_request)
    assert client.get_batch_results("b1")["results"] == []


def test_get_batch_not_found_raises():
    def fake_request(method, url, **kwargs):
        return FakeResponse(404, {"error": "批任务不存在"})

    client, _ = make_client(fake_request)
    with pytest.raises(NotFoundError):
        client.get_batch("missing")


def test_import_batch_files_requires_name_or_seq():
    client, _ = make_client(lambda *a, **k: FakeResponse(200, {}))
    with pytest.raises(ValueError):
        client.import_batch_files("b1", ["a.txt"])


def test_import_batch_files_by_seq():
    def fake_request(method, url, **kwargs):
        assert kwargs["json"] == {"paths": ["a.txt"], "seq": 1}
        return FakeResponse(200, {"batchId": "b1", "name": "a.txt", "seq": 1, "results": []})

    client, _ = make_client(fake_request)
    client.import_batch_files("b1", ["a.txt"], seq=1)


def test_delete_batch():
    def fake_request(method, url, **kwargs):
        assert method == "DELETE"
        return FakeResponse(200, {"deleted": True})

    client, _ = make_client(fake_request)
    assert client.delete_batch("b1")["deleted"] is True


def test_retry_failed_batch_sessions():
    def fake_request(method, url, **kwargs):
        assert url.endswith("/ai-batches/b1/retry-failed")
        return FakeResponse(200, {"retried": 3})

    client, _ = make_client(fake_request)
    assert client.retry_failed_batch_sessions("b1")["retried"] == 3


def test_retry_failed_still_running_raises_conflict():
    def fake_request(method, url, **kwargs):
        return FakeResponse(409, {"error": "该批任务仍在执行中"})

    client, _ = make_client(fake_request)
    with pytest.raises(ConflictError):
        client.retry_failed_batch_sessions("b1")


def test_append_batch_files():
    def fake_request(method, url, **kwargs):
        assert kwargs["json"] == {"files": [{"name": "a", "path": "p"}]}
        return FakeResponse(200, {"batchId": "b1", "status": "running", "total": 2, "appended": 1})

    client, _ = make_client(fake_request)
    client.append_batch_files("b1", [{"name": "a", "path": "p"}])


def test_update_batch_config_sends_full_replace_body():
    """整体替换语义：即便调用方只想改 agent，也要发出四个键（未传的是 None），
    这是文档里明确警告过的行为，客户端不能悄悄只发变化的字段。"""
    def fake_request(method, url, **kwargs):
        assert method == "PATCH"
        assert kwargs["json"] == {
            "agent": "plan", "model": None, "callbackUrl": None, "callbackSecret": None,
        }
        return FakeResponse(200, {"batchId": "b1", "agent": "plan"})

    client, _ = make_client(fake_request)
    client.update_batch_config("b1", agent="plan")


def test_download_batch_session_file_writes_dest(tmp_path):
    dest = tmp_path / "out.txt"

    def fake_request(method, url, **kwargs):
        assert url.endswith("/ai-batches/b1/sessions/1/files/download")
        assert kwargs["params"] == {"path": "outputs/report.txt"}
        assert kwargs["stream"] is True
        return FakeResponse(200, content=b"hello")

    client, _ = make_client(fake_request)
    content = client.download_batch_session_file("b1", "1", "outputs/report.txt", dest=dest)
    assert content == b"hello"
    assert dest.read_bytes() == b"hello"


def test_download_batch_session_files_zip_joins_include_list():
    def fake_request(method, url, **kwargs):
        assert url.endswith("/ai-batches/b1/sessions/1/files/download-all")
        assert kwargs["params"] == {"include": "added,modified"}
        return FakeResponse(200, content=b"PK\x03\x04zip")

    client, _ = make_client(fake_request)
    content = client.download_batch_session_files_zip("b1", "1", include=["added", "modified"])
    assert content.startswith(b"PK")


def test_continue_batch_session():
    def fake_request(method, url, **kwargs):
        assert url.endswith("/ai-batches/b1/sessions/1/continue")
        assert kwargs["json"] == {"prompt": "补充一下"}
        return FakeResponse(202, {"batchId": "b1", "status": "running"})

    client, _ = make_client(fake_request)
    assert client.continue_batch_session("b1", "1", "补充一下")["status"] == "running"


def test_reexecute_batch_session():
    def fake_request(method, url, **kwargs):
        assert url.endswith("/ai-batches/b1/sessions/1/reexecute")
        return FakeResponse(200, {"batchId": "b1", "status": "running"})

    client, _ = make_client(fake_request)
    client.reexecute_batch_session("b1", "1")


def test_translate_query_returns_filter():
    def fake_request(method, url, **kwargs):
        assert url.endswith("/ai-batches/query")
        assert kwargs["json"] == {"collection": "orders", "question": "上周失败的记录"}
        return FakeResponse(200, {"filter": {"status": "failed"}})

    client, _ = make_client(fake_request)
    assert client.translate_query("orders", "上周失败的记录") == {"filter": {"status": "failed"}}


def test_list_batch_agents_models_skills():
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        if url.endswith("/agents"):
            return FakeResponse(200, {"agents": [{"name": "build"}], "default": "build"})
        if url.endswith("/models"):
            return FakeResponse(200, {"models": [], "default": ""})
        return FakeResponse(200, {"skills": []})

    client, _ = make_client(fake_request)
    assert client.list_batch_agents()["default"] == "build"
    assert client.list_batch_models()["models"] == []
    assert client.list_batch_skills()["skills"] == []
    assert len(calls) == 3


# ---------------------------------------------------------------------------
# AI 单会话
# ---------------------------------------------------------------------------

def test_create_ai_session_sends_prompt_only_by_default():
    def fake_request(method, url, **kwargs):
        assert method == "POST"
        assert url.endswith("/ai-sessions")
        assert kwargs["json"] == {"prompt": "帮我写一句问候语"}
        return FakeResponse(201, {"sessionId": "sess-1", "status": "pending"})

    client, _ = make_client(fake_request)
    result = client.create_ai_session("帮我写一句问候语")
    assert result == {"sessionId": "sess-1", "status": "pending"}


def test_create_ai_session_includes_optional_fields_when_given():
    def fake_request(method, url, **kwargs):
        assert kwargs["json"] == {
            "prompt": "hi", "agent": "build", "model": "m1", "title": "打招呼",
        }
        return FakeResponse(201, {"sessionId": "sess-1", "status": "pending"})

    client, _ = make_client(fake_request)
    client.create_ai_session("hi", agent="build", model="m1", title="打招呼")


def test_create_ai_session_rejects_empty_prompt():
    def fake_request(method, url, **kwargs):
        return FakeResponse(400, {"error": "prompt 必填"})

    client, _ = make_client(fake_request)
    with pytest.raises(ValidationError):
        client.create_ai_session("")


def test_get_ai_session_pending_has_no_output():
    def fake_request(method, url, **kwargs):
        assert url.endswith("/ai-sessions/sess-1")
        return FakeResponse(200, {
            "sessionId": "sess-1", "status": "pending", "title": "新会话",
            "agent": None, "model": None, "createdAt": "2026-01-01T00:00:00",
            "lastActiveAt": "2026-01-01T00:00:00", "output": None, "error": None,
        })

    client, _ = make_client(fake_request)
    result = client.get_ai_session("sess-1")
    assert result["status"] == "pending"
    assert result["output"] is None


def test_get_ai_session_completed_returns_output():
    def fake_request(method, url, **kwargs):
        return FakeResponse(200, {
            "sessionId": "sess-1", "status": "completed", "output": "你好！",
            "error": None,
        })

    client, _ = make_client(fake_request)
    result = client.get_ai_session("sess-1")
    assert result["output"] == "你好！"


def test_get_ai_session_not_found():
    def fake_request(method, url, **kwargs):
        return FakeResponse(404, {"error": "会话不存在"})

    client, _ = make_client(fake_request)
    with pytest.raises(NotFoundError):
        client.get_ai_session("missing")


# ---------------------------------------------------------------------------
# Row Actions
# ---------------------------------------------------------------------------

def test_run_row_action_success():
    def fake_request(method, url, **kwargs):
        assert method == "POST"
        assert url.endswith("/collections/orders/rec-1/row-actions/act-1/run")
        assert kwargs["json"] == {"params": {"note": "hi"}}
        assert kwargs["params"] == {"branchId": "main"}
        return FakeResponse(
            200, {"ok": True, "status": "running", "statusField": "status", "runningValue": "处理中"}
        )

    client, _ = make_client(fake_request)
    result = client.run_row_action("orders", "rec-1", "act-1", params={"note": "hi"})
    assert result["status"] == "running"


def test_run_row_action_error_message_is_chinese_passthrough():
    """行操作的 error 文案是中文，客户端不做任何改写——只透传状态码到异常类型，
    .message 应该原样是服务端给的中文原文。"""
    def fake_request(method, url, **kwargs):
        return FakeResponse(403, {"error": "角色无权限"})

    client, _ = make_client(fake_request)
    from checkmanage_openapi import WriteNotAllowedError
    with pytest.raises(WriteNotAllowedError) as exc_info:
        client.run_row_action("orders", "rec-1", "act-1")
    assert exc_info.value.message == "角色无权限"


# ---------------------------------------------------------------------------
# AI 定时扫描任务
# ---------------------------------------------------------------------------

def test_list_scan_tasks():
    def fake_request(method, url, **kwargs):
        assert url.endswith("/ai-scan-tasks")
        return FakeResponse(200, {"tasks": [{"id": "scan-1"}]})

    client, _ = make_client(fake_request)
    assert client.list_scan_tasks()["tasks"][0]["id"] == "scan-1"


def test_run_scan_task_now_returns_claimed_count():
    def fake_request(method, url, **kwargs):
        assert url.endswith("/ai-scan-tasks/scan-1/run-now")
        return FakeResponse(200, {"triggered": True, "claimedCount": 3, "lastError": None})

    client, _ = make_client(fake_request)
    result = client.run_scan_task_now("scan-1")
    assert result["claimedCount"] == 3


def test_run_scan_task_now_not_found():
    def fake_request(method, url, **kwargs):
        return FakeResponse(404, {"error": "任务不存在"})

    client, _ = make_client(fake_request)
    with pytest.raises(NotFoundError):
        client.run_scan_task_now("missing")


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

def test_prompt_template_full_crud_cycle():
    state = {}

    def fake_request(method, url, **kwargs):
        if method == "POST" and url.endswith("/prompt-templates"):
            state["tpl"] = {"id": "tpl-1", "name": kwargs["json"]["name"],
                            "content": kwargs["json"]["content"]}
            return FakeResponse(201, state["tpl"])
        if method == "GET" and url.endswith("/prompt-templates"):
            return FakeResponse(200, {"templates": [state["tpl"]]})
        if method == "GET":
            return FakeResponse(200, state["tpl"])
        if method == "PUT":
            state["tpl"] = {"id": "tpl-1", "name": kwargs["json"]["name"],
                            "content": kwargs["json"]["content"]}
            return FakeResponse(200, state["tpl"])
        if method == "DELETE":
            return FakeResponse(204)
        raise AssertionError(f"unexpected {method} {url}")

    client, _ = make_client(fake_request)
    created = client.create_prompt_template("周报", "请总结")
    assert created["id"] == "tpl-1"
    assert client.list_prompt_templates()["templates"][0]["name"] == "周报"
    assert client.get_prompt_template("tpl-1")["name"] == "周报"
    updated = client.update_prompt_template("tpl-1", "月报", "请总结全月")
    assert updated["name"] == "月报"
    assert client.delete_prompt_template("tpl-1") is None


def test_create_prompt_template_duplicate_name_raises_conflict():
    def fake_request(method, url, **kwargs):
        return FakeResponse(409, {"error": "name already in use"})

    client, _ = make_client(fake_request)
    with pytest.raises(ConflictError):
        client.create_prompt_template("dup", "x")


# ---------------------------------------------------------------------------
# 长期记忆
# ---------------------------------------------------------------------------

def test_list_memories():
    def fake_request(method, url, **kwargs):
        assert url.endswith("/memories")
        return FakeResponse(200, {"memories": [{"id": "m1", "memory": "喜欢 Python"}]})

    client, _ = make_client(fake_request)
    assert client.list_memories()["memories"][0]["memory"] == "喜欢 Python"


def test_add_memory_sends_verbatim_flag():
    def fake_request(method, url, **kwargs):
        assert kwargs["json"] == {"text": "偏好简洁代码", "verbatim": True}
        return FakeResponse(200, {"ok": True, "memories": [{"id": "m1"}]})

    client, _ = make_client(fake_request)
    client.add_memory("偏好简洁代码", verbatim=True)


def test_add_memory_unavailable_raises_dedicated_error():
    def fake_request(method, url, **kwargs):
        return FakeResponse(
            409, {"error": "记忆功能未配置（缺少 API Key 或未启用底层）",
                 "code": "MEMORY_UNAVAILABLE"}
        )

    client, _ = make_client(fake_request)
    with pytest.raises(MemoryUnavailableError) as exc_info:
        client.add_memory("test")
    # 必须是专用子类，不是退化成泛化 ConflictError
    assert isinstance(exc_info.value, ConflictError)
    assert exc_info.value.code == "MEMORY_UNAVAILABLE"


def test_delete_memory_not_owned_raises_not_found():
    """服务端会先校验归属再删；不属于这把密钥所属用户的 memory_id 一律 404。"""
    def fake_request(method, url, **kwargs):
        return FakeResponse(404, {"error": "not found"})

    client, _ = make_client(fake_request)
    with pytest.raises(NotFoundError):
        client.delete_memory("someone-elses-id")


def test_delete_memory_success():
    def fake_request(method, url, **kwargs):
        assert url.endswith("/memories/m1")
        assert method == "DELETE"
        return FakeResponse(200, {"ok": True})

    client, _ = make_client(fake_request)
    assert client.delete_memory("m1")["ok"] is True
