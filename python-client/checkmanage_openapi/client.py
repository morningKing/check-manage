"""check-manage Open API 的 Python 客户端实现。

接口行为对照 docs/user-guide/integration/open-api.md。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Optional, Union

import requests

from .exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    OpenApiError,
    ValidationError,
    VersionConflictError,
    WriteNotAllowedError,
)

DEFAULT_BASE_URL = "http://localhost:7001/api/v1"
DEFAULT_TIMEOUT = 30.0

PathLike = Union[str, "os.PathLike[str]"]
FileInput = Union[PathLike, BinaryIO]


class OpenApiClient:
    """check-manage Open API 客户端。

    用法::

        from checkmanage_openapi import OpenApiClient

        with OpenApiClient(api_key="cm_xxx", base_url="https://host/api/v1") as client:
            for record in client.iter_records("inspection-cases"):
                print(record["id"])

            client.attach_files(
                "devices", "附件", "./report.pdf",
                {"名称": "外部记录1"},
            )
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        session: Optional[requests.Session] = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()
        self._owns_session = session is None

    def __enter__(self) -> "OpenApiClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        """关闭客户端持有的底层连接池（外部传入的 session 不会被关闭）。"""
        if self._owns_session:
            self._session.close()

    # ---------------------------------------------------------------
    # internal
    # ---------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        files: Optional[dict] = None,
        data: Optional[dict] = None,
        stream: bool = False,
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        headers = {"X-API-Key": self.api_key}
        resp = self._session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            files=files,
            data=data,
            timeout=self.timeout,
            stream=stream,
        )
        if resp.status_code >= 400:
            self._raise_for_error(resp)
        return resp

    @staticmethod
    def _raise_for_error(resp: requests.Response) -> None:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        message = body.get("error") or resp.text or f"HTTP {resp.status_code}"
        details = body.get("details")
        code = body.get("code")
        status = resp.status_code

        if status == 401:
            raise AuthenticationError(message, status_code=status)
        if status == 403:
            raise WriteNotAllowedError(message, status_code=status)
        if status == 404:
            raise NotFoundError(message, status_code=status)
        if status == 400:
            raise ValidationError(message, status_code=status, details=details)
        if status == 409:
            if code == "VERSION_CONFLICT":
                raise VersionConflictError(message, status_code=status, code=code)
            raise ConflictError(message, status_code=status, code=code)
        raise OpenApiError(message, status_code=status, details=details, code=code)

    # ---------------------------------------------------------------
    # 5.1 / 5.2 — 集合与分支
    # ---------------------------------------------------------------

    def list_collections(self) -> list:
        """返回所有已开放 Open API 访问的数据集合。"""
        return self._request("GET", "/collections").json()["data"]

    def list_branches(self) -> list:
        """返回所有可用分支；main 分支始终存在。"""
        return self._request("GET", "/branches").json()["data"]

    # ---------------------------------------------------------------
    # 5.3 / 5.4 / 5.5 — 记录查询
    # ---------------------------------------------------------------

    def list_records(
        self,
        collection: str,
        *,
        page: int = 1,
        page_size: int = 20,
        branch_id: str = "main",
    ) -> dict:
        """获取单页数据，返回含 data / pagination / branchId 的完整响应体。"""
        resp = self._request(
            "GET",
            f"/collections/{collection}",
            params={"page": page, "pageSize": page_size, "branchId": branch_id},
        )
        return resp.json()

    def iter_records(
        self,
        collection: str,
        *,
        branch_id: str = "main",
        page_size: int = 100,
    ) -> Iterator[dict]:
        """自动翻页，逐条 yield 记录（page_size 最大 100，超出会被服务端截断）。"""
        page = 1
        while True:
            body = self.list_records(
                collection, page=page, page_size=page_size, branch_id=branch_id
            )
            for record in body["data"]:
                yield record
            if page >= body["pagination"]["totalPages"]:
                return
            page += 1

    def get_record(self, collection: str, record_id: str, *, branch_id: str = "main") -> dict:
        resp = self._request(
            "GET", f"/collections/{collection}/{record_id}", params={"branchId": branch_id}
        )
        return resp.json()["data"]

    def get_schema(self, collection: str) -> dict:
        resp = self._request("GET", f"/collections/{collection}/schema")
        return resp.json()["data"]

    # ---------------------------------------------------------------
    # 5.6 / 5.7 — 记录写入（需目标集合开启「允许写入」）
    # ---------------------------------------------------------------

    def create_record(self, collection: str, data: dict, *, branch_id: str = "main") -> dict:
        resp = self._request(
            "POST", f"/collections/{collection}", json_body=data, params={"branchId": branch_id}
        )
        return resp.json()["data"]

    def update_record(
        self,
        collection: str,
        record_id: str,
        data: dict,
        *,
        branch_id: str = "main",
        version: Optional[int] = None,
    ) -> dict:
        """部分更新：data 只需包含要修改的字段。传 version 可触发乐观锁检测。"""
        payload = dict(data)
        if version is not None:
            payload["_version"] = version
        resp = self._request(
            "PUT",
            f"/collections/{collection}/{record_id}",
            json_body=payload,
            params={"branchId": branch_id},
        )
        return resp.json()["data"]

    # ---------------------------------------------------------------
    # 5.8 / 5.9 — 文件上传、下载、写入 file / image 字段
    # ---------------------------------------------------------------

    def upload_file(
        self,
        collection: str,
        file: FileInput,
        *,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> dict:
        """上传单个文件，返回 {uid, name, size, mimeType, downloadUrl}。

        file 可以是文件路径（str / Path）或已打开的二进制文件对象；后者时
        建议显式传 filename，否则会尝试读取 file.name 或退化为 "upload.bin"。
        """
        close_after = False
        if isinstance(file, (str, os.PathLike)):
            fh: BinaryIO = open(file, "rb")  # noqa: SIM115 - closed in finally
            close_after = True
            filename = filename or Path(file).name
        else:
            fh = file
            filename = filename or getattr(file, "name", None) or "upload.bin"

        try:
            upload_tuple = (filename, fh, content_type) if content_type else (filename, fh)
            resp = self._request(
                "POST",
                "/files",
                data={"collection": collection},
                files={"file": upload_tuple},
            )
            return resp.json()["data"]
        finally:
            if close_after:
                fh.close()

    def download_file(self, file_id: str, dest: Optional[PathLike] = None) -> bytes:
        """下载文件二进制内容；传 dest 时同时写入本地磁盘路径。"""
        resp = self._request("GET", f"/files/{file_id}/download", stream=True)
        content = resp.content
        if dest is not None:
            Path(dest).write_bytes(content)
        return content

    def get_file_metadata(self, file_id: str) -> dict:
        resp = self._request("GET", f"/files/{file_id}")
        return resp.json()["data"]

    @staticmethod
    def to_file_field(uploaded: dict) -> dict:
        """把 upload_file() 的返回值转换成可直接写入 file/image 字段数组的对象。"""
        return {
            "uid": uploaded["uid"],
            "name": uploaded["name"],
            "size": uploaded["size"],
            "type": uploaded.get("mimeType"),
        }

    def attach_files(
        self,
        collection: str,
        field_name: str,
        files: Union[FileInput, list],
        data: dict,
        *,
        record_id: Optional[str] = None,
        branch_id: str = "main",
        version: Optional[int] = None,
    ) -> dict:
        """一步完成「上传文件 + 写入 file/image 字段」的完整流程。

        files 可以是单个文件路径，也可以是路径列表（多文件字段）。
        不传 record_id 则新增记录，传了则修改该条已有记录。
        """
        if isinstance(files, (str, os.PathLike)) or hasattr(files, "read"):
            files = [files]
        file_field = [self.to_file_field(self.upload_file(collection, f)) for f in files]

        payload = dict(data)
        payload[field_name] = file_field

        if record_id is None:
            return self.create_record(collection, payload, branch_id=branch_id)
        return self.update_record(
            collection, record_id, payload, branch_id=branch_id, version=version
        )
