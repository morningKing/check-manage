import json
from unittest.mock import MagicMock

import pytest

from checkmanage_openapi import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    OpenApiClient,
    ValidationError,
    VersionConflictError,
    WriteNotAllowedError,
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


def test_list_collections_sends_api_key_header():
    def fake_request(method, url, **kwargs):
        assert method == "GET"
        assert url == "http://x/api/v1/collections"
        assert kwargs["headers"]["X-API-Key"] == "cm_test"
        return FakeResponse(200, {"data": [{"collection": "devices", "writable": True}]})

    client, _ = make_client(fake_request)
    assert client.list_collections() == [{"collection": "devices", "writable": True}]


def test_list_branches():
    def fake_request(method, url, **kwargs):
        return FakeResponse(200, {"data": [{"id": "main", "status": "active"}]})

    client, _ = make_client(fake_request)
    assert client.list_branches() == [{"id": "main", "status": "active"}]


def test_list_records_passes_pagination_params():
    def fake_request(method, url, **kwargs):
        assert kwargs["params"] == {"page": 2, "pageSize": 50, "branchId": "pv-1"}
        return FakeResponse(
            200,
            {
                "data": [{"id": "r1"}],
                "pagination": {"page": 2, "pageSize": 50, "total": 51, "totalPages": 2},
                "branchId": "pv-1",
            },
        )

    client, _ = make_client(fake_request)
    body = client.list_records("devices", page=2, page_size=50, branch_id="pv-1")
    assert body["data"] == [{"id": "r1"}]


def test_iter_records_auto_paginates():
    seen_pages = []

    def fake_request(method, url, **kwargs):
        page = kwargs["params"]["page"]
        seen_pages.append(page)
        if page == 1:
            return FakeResponse(
                200,
                {
                    "data": [{"id": "a"}],
                    "pagination": {"page": 1, "pageSize": 1, "total": 2, "totalPages": 2},
                },
            )
        return FakeResponse(
            200,
            {
                "data": [{"id": "b"}],
                "pagination": {"page": 2, "pageSize": 1, "total": 2, "totalPages": 2},
            },
        )

    client, _ = make_client(fake_request)
    records = list(client.iter_records("devices", page_size=1))
    assert [r["id"] for r in records] == ["a", "b"]
    assert seen_pages == [1, 2]


def test_get_record():
    def fake_request(method, url, **kwargs):
        assert url == "http://x/api/v1/collections/devices/r1"
        return FakeResponse(200, {"data": {"id": "r1", "name": "X"}})

    client, _ = make_client(fake_request)
    assert client.get_record("devices", "r1") == {"id": "r1", "name": "X"}


def test_get_schema():
    def fake_request(method, url, **kwargs):
        assert url.endswith("/schema")
        return FakeResponse(200, {"data": {"collection": "devices", "fields": []}})

    client, _ = make_client(fake_request)
    assert client.get_schema("devices")["collection"] == "devices"


def test_create_record_sends_json_body():
    def fake_request(method, url, **kwargs):
        assert method == "POST"
        assert kwargs["json"] == {"name": "x"}
        return FakeResponse(201, {"data": {"id": "api-1", "name": "x"}})

    client, _ = make_client(fake_request)
    assert client.create_record("devices", {"name": "x"})["id"] == "api-1"


def test_batch_create_records_sends_records_and_options():
    def fake_request(method, url, **kwargs):
        assert method == "POST"
        assert url == "http://x/api/v1/collections/devices/batch"
        assert kwargs["json"] == {
            "records": [{"name": "a"}, {"name": "b"}],
            "options": {"continueOnError": True},
        }
        return FakeResponse(201, {"data": [{"id": "api-1", "name": "a"}], "created": 1, "failed": 1,
                                   "errors": [{"index": 1, "error": "..."}]})

    client, _ = make_client(fake_request)
    result = client.batch_create_records(
        "devices", [{"name": "a"}, {"name": "b"}], continue_on_error=True
    )
    assert result["created"] == 1
    assert result["failed"] == 1


def test_batch_create_records_default_continue_on_error_is_false():
    def fake_request(method, url, **kwargs):
        assert kwargs["json"]["options"] == {"continueOnError": False}
        return FakeResponse(201, {"data": [{"id": "api-1"}], "created": 1, "failed": 0})

    client, _ = make_client(fake_request)
    client.batch_create_records("devices", [{"name": "a"}])


def test_batch_create_records_raises_on_400():
    def fake_request(method, url, **kwargs):
        return FakeResponse(400, {"error": "Validation failed for one or more records",
                                   "failed": 1, "errors": [{"index": 0, "error": "..."}]})

    client, _ = make_client(fake_request)
    with pytest.raises(ValidationError):
        client.batch_create_records("devices", [{"name": "a"}])


def test_update_record_merges_version_into_payload():
    def fake_request(method, url, **kwargs):
        assert method == "PUT"
        assert kwargs["json"] == {"status": "inactive", "_version": 3}
        return FakeResponse(200, {"data": {"id": "r1", "status": "inactive", "_version": 4}})

    client, _ = make_client(fake_request)
    result = client.update_record("devices", "r1", {"status": "inactive"}, version=3)
    assert result["_version"] == 4


def test_authentication_error_on_401():
    def fake_request(method, url, **kwargs):
        return FakeResponse(401, {"error": "Invalid API key"})

    client, _ = make_client(fake_request)
    with pytest.raises(AuthenticationError) as exc_info:
        client.list_collections()
    assert exc_info.value.status_code == 401


def test_write_not_allowed_error_on_403():
    def fake_request(method, url, **kwargs):
        return FakeResponse(403, {"error": "Collection is read-only"})

    client, _ = make_client(fake_request)
    with pytest.raises(WriteNotAllowedError):
        client.create_record("devices", {})


def test_not_found_error_on_404():
    def fake_request(method, url, **kwargs):
        return FakeResponse(404, {"error": "Record not found"})

    client, _ = make_client(fake_request)
    with pytest.raises(NotFoundError):
        client.get_record("devices", "missing")


def test_validation_error_carries_details():
    def fake_request(method, url, **kwargs):
        return FakeResponse(400, {"error": "Validation failed", "details": ["名称 is required"]})

    client, _ = make_client(fake_request)
    with pytest.raises(ValidationError) as exc_info:
        client.create_record("devices", {})
    assert exc_info.value.details == ["名称 is required"]


def test_version_conflict_error_when_code_matches():
    def fake_request(method, url, **kwargs):
        return FakeResponse(
            409, {"error": "Record has been modified", "code": "VERSION_CONFLICT"}
        )

    client, _ = make_client(fake_request)
    with pytest.raises(VersionConflictError):
        client.update_record("devices", "r1", {"status": "x"}, version=1)


def test_generic_conflict_error_without_version_code():
    def fake_request(method, url, **kwargs):
        return FakeResponse(409, {"error": "Record ID already exists"})

    client, _ = make_client(fake_request)
    with pytest.raises(ConflictError) as exc_info:
        client.create_record("devices", {"id": "dup"})
    assert not isinstance(exc_info.value, VersionConflictError)


def test_upload_file_from_path_sends_multipart(tmp_path):
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.4 fake content")

    def fake_request(method, url, **kwargs):
        assert method == "POST"
        assert url.endswith("/files")
        assert kwargs["data"] == {"collection": "devices"}
        filename, fh = kwargs["files"]["file"]
        assert filename == "report.pdf"
        assert fh.read() == b"%PDF-1.4 fake content"
        return FakeResponse(
            200,
            {"data": {"uid": "u1", "name": "report.pdf", "size": 21, "mimeType": "application/pdf"}},
        )

    client, _ = make_client(fake_request)
    result = client.upload_file("devices", f)
    assert result["uid"] == "u1"


def test_upload_file_with_field_name_sends_field_name(tmp_path):
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.4 fake content")

    def fake_request(method, url, **kwargs):
        assert kwargs["data"] == {"collection": "devices", "fieldName": "附件"}
        return FakeResponse(
            200,
            {"data": {"uid": "u1", "name": "report.pdf", "size": 21, "mimeType": "application/pdf"}},
        )

    client, _ = make_client(fake_request)
    result = client.upload_file("devices", f, field_name="附件")
    assert result["uid"] == "u1"


def test_upload_file_without_field_name_omits_it(tmp_path):
    """field_name 未传时不带 fieldName 参数，保持向后兼容（后端不做类型限制）。"""
    f = tmp_path / "report.pdf"
    f.write_bytes(b"content")

    def fake_request(method, url, **kwargs):
        assert "fieldName" not in kwargs["data"]
        return FakeResponse(
            200,
            {"data": {"uid": "u1", "name": "report.pdf", "size": 7, "mimeType": "application/pdf"}},
        )

    client, _ = make_client(fake_request)
    client.upload_file("devices", f)


def test_download_file_returns_bytes_and_writes_dest(tmp_path):
    def fake_request(method, url, **kwargs):
        assert url.endswith("/files/u1/download")
        assert kwargs["stream"] is True
        return FakeResponse(200, content=b"binary-content")

    client, _ = make_client(fake_request)
    dest = tmp_path / "out.bin"
    data = client.download_file("u1", dest=dest)
    assert data == b"binary-content"
    assert dest.read_bytes() == b"binary-content"


def test_to_file_field_maps_upload_response():
    uploaded = {"uid": "u1", "name": "a.png", "size": 99, "mimeType": "image/png"}
    assert OpenApiClient.to_file_field(uploaded) == {
        "uid": "u1",
        "name": "a.png",
        "size": 99,
        "type": "image/png",
    }


def test_attach_files_uploads_then_creates_record(tmp_path):
    f = tmp_path / "report.pdf"
    f.write_bytes(b"content")
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        if url.endswith("/files"):
            # attach_files 已知目标字段名，应该原样透传给 upload_file 触发
            # 服务端的类型约束校验，而不是让它退化成"不限制"
            assert kwargs["data"] == {"collection": "devices", "fieldName": "附件"}
            return FakeResponse(
                200,
                {"data": {"uid": "u1", "name": "report.pdf", "size": 7, "mimeType": "application/pdf"}},
            )
        assert kwargs["json"]["附件"] == [
            {"uid": "u1", "name": "report.pdf", "size": 7, "type": "application/pdf"}
        ]
        return FakeResponse(201, {"data": {"id": "api-1"}})

    client, _ = make_client(fake_request)
    result = client.attach_files("devices", "附件", f, {"名称": "外部记录1"})
    assert result["id"] == "api-1"
    assert calls[0][1].endswith("/files")
    assert calls[1][0] == "POST"


def test_attach_files_updates_existing_record_when_record_id_given(tmp_path):
    f = tmp_path / "report.pdf"
    f.write_bytes(b"content")

    def fake_request(method, url, **kwargs):
        if url.endswith("/files"):
            return FakeResponse(
                200, {"data": {"uid": "u1", "name": "report.pdf", "size": 7, "mimeType": "application/pdf"}}
            )
        assert method == "PUT"
        assert url.endswith("/collections/devices/r1")
        return FakeResponse(200, {"data": {"id": "r1"}})

    client, _ = make_client(fake_request)
    result = client.attach_files("devices", "附件", f, {}, record_id="r1")
    assert result["id"] == "r1"


def test_context_manager_closes_owned_session_only():
    external_session = MagicMock()
    client = OpenApiClient(api_key="cm_test", session=external_session)
    with client:
        pass
    external_session.close.assert_not_called()

    owned_client = OpenApiClient(api_key="cm_test")
    owned_client._session.close = MagicMock()
    with owned_client:
        pass
    owned_client._session.close.assert_called_once()


def test_missing_api_key_raises_value_error():
    with pytest.raises(ValueError):
        OpenApiClient(api_key="")
