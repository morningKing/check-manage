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
    MemoryUnavailableError,
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
            if code == "MEMORY_UNAVAILABLE":
                raise MemoryUnavailableError(message, status_code=status, code=code)
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

    def batch_create_records(
        self,
        collection: str,
        records: list,
        *,
        branch_id: str = "main",
        continue_on_error: bool = False,
    ) -> dict:
        """批量创建记录（单次最多 1000 条）。

        返回 {"data": [...], "created": N, "failed": M, "errors"?: [...]}。
        continue_on_error=False（默认）时，只要有一条记录校验失败就不会写入
        任何记录，整个调用抛 ApiError 子类异常；True 时跳过失败记录，成功的
        会写入，返回值里的 failed/errors 描述哪些没写进去。
        """
        resp = self._request(
            "POST",
            f"/collections/{collection}/batch",
            json_body={"records": records, "options": {"continueOnError": continue_on_error}},
            params={"branchId": branch_id},
        )
        return resp.json()

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

    @staticmethod
    def _open_for_upload(
        file: FileInput,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ):
        """打开一个待上传文件，返回 (upload_tuple, fh, close_after)。

        file 可以是文件路径（str / Path）或已打开的二进制文件对象；后者时
        建议显式传 filename，否则会尝试读取 file.name 或退化为 "upload.bin"。
        调用方负责在 finally 里 `if close_after: fh.close()`——本方法只负责打开，
        不负责关闭，因为 upload_file（单文件）和 upload_batch_files（多文件，
        每个各自可能来自路径或对象）的清理时机不一样，各自在自己的 finally 里处理。
        """
        close_after = False
        if isinstance(file, (str, os.PathLike)):
            fh: BinaryIO = open(file, "rb")  # noqa: SIM115 - closed by caller's finally
            close_after = True
            filename = filename or Path(file).name
        else:
            fh = file
            filename = filename or getattr(file, "name", None) or "upload.bin"
        upload_tuple = (filename, fh, content_type) if content_type else (filename, fh)
        return upload_tuple, fh, close_after

    def upload_file(
        self,
        collection: str,
        file: FileInput,
        *,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
        field_name: Optional[str] = None,
    ) -> dict:
        """上传单个文件，返回 {uid, name, size, mimeType, downloadUrl}。

        field_name 可选：传入目标 file/image 字段名时，服务端会按该字段
        在页面配置里配置的「允许的文件类型」做校验，类型不符会抛
        ValidationError；不传则不做类型限制（向后兼容）。
        """
        upload_tuple, fh, close_after = self._open_for_upload(file, filename, content_type)
        try:
            data = {"collection": collection}
            if field_name:
                data["fieldName"] = field_name
            resp = self._request(
                "POST",
                "/files",
                data=data,
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
        file_field = [
            self.to_file_field(self.upload_file(collection, f, field_name=field_name))
            for f in files
        ]

        payload = dict(data)
        payload[field_name] = file_field

        if record_id is None:
            return self.create_record(collection, payload, branch_id=branch_id)
        return self.update_record(
            collection, record_id, payload, branch_id=branch_id, version=version
        )

    # ---------------------------------------------------------------
    # AI 批任务（/v1/ai-batches，对照 docs/user-guide/integration/ai-batch-api.md）
    #
    # 除文件下载方法（返回 bytes）外，本组全部方法原样返回 resp.json()
    # 解析出的整个响应体 —— 不做任何字段拆包/改形，跟集合类方法历史上对
    # `data` 信封的拆包习惯不是同一套规则，这是有意的：AI 批任务的响应体
    # 本身就没有统一的 `data` 信封，逐个方法各自发明拆包意见只会更难预测。
    # ---------------------------------------------------------------

    def upload_batch_files(self, files: list) -> dict:
        """批量上传待创建批任务用的文件（AI 批任务专属暂存区，路径不透明，
        原样传给 create_batch()/append_batch_files()）。

        files 每项可以是：文件路径；已打开的文件对象；或 (file, filename)
        / (file, filename, content_type) 元组（file 是路径或文件对象，用于
        显式指定文件名/类型）。

        返回 {'files': [{'name', 'path'}, ...]}。
        """
        opened = []
        try:
            request_files = []
            for f in files:
                if isinstance(f, tuple):
                    file_obj = f[0]
                    filename = f[1] if len(f) > 1 else None
                    content_type = f[2] if len(f) > 2 else None
                else:
                    file_obj, filename, content_type = f, None, None
                upload_tuple, fh, close_after = self._open_for_upload(
                    file_obj, filename, content_type
                )
                opened.append((fh, close_after))
                request_files.append(("files", upload_tuple))
            resp = self._request("POST", "/ai-batches/uploads", files=request_files)
            return resp.json()
        finally:
            for fh, close_after in opened:
                if close_after:
                    fh.close()

    def list_batch_agents(self) -> dict:
        """可用于 create_batch()/update_batch_config() 的 agent 名称列表。
        返回 {'agents': [{'name','description'}, ...], 'default': str|None}。"""
        return self._request("GET", "/ai-batches/agents").json()

    def list_batch_models(self) -> dict:
        """可用于 create_batch()/update_batch_config() 的模型列表（已连接的
        provider 下）。返回 {'models': [{'id','label','providerID','modelID'}, ...],
        'default': str}。"""
        return self._request("GET", "/ai-batches/models").json()

    def list_batch_skills(self) -> dict:
        """可用于 create_batch() 的全局 skill 列表。返回 {'skills': [...]}。"""
        return self._request("GET", "/ai-batches/skills").json()

    def create_batch(
        self,
        name: str,
        prompt: str,
        files: list,
        *,
        agent: Optional[str] = None,
        model: Optional[str] = None,
        callback_url: Optional[str] = None,
        callback_secret: Optional[str] = None,
    ) -> dict:
        """创建批任务，每个文件对应一个子任务/AI 会话。

        files 是 upload_batch_files() 返回的 files 数组（或其子集），每项
        需含 name 与 path；最多 50 个。callback_url 非空时批任务进入终态会
        收到一次 HMAC 签名的 HTTP 回调（callback_secret 用于计算签名），
        详见 ai-batch-api.md §4.2b；不传则只能轮询 get_batch()。

        返回 {'batchId', 'status', 'total'}。
        """
        body = {"name": name, "prompt": prompt, "files": files}
        if agent is not None:
            body["agent"] = agent
        if model is not None:
            body["model"] = model
        if callback_url is not None:
            body["callbackUrl"] = callback_url
        if callback_secret is not None:
            body["callbackSecret"] = callback_secret
        return self._request("POST", "/ai-batches", json_body=body).json()

    def list_batches(self, *, page: int = 1, page_size: int = 20) -> dict:
        """分页列出本密钥创建的批任务。返回 {'items': [...], 'total': N}——
        注意这里**没有** `pagination.totalPages` 字段（跟 list_records() 的
        分页信封不是同一套），要自动翻页请用 iter_batches()。"""
        return self._request(
            "GET", "/ai-batches", params={"page": page, "pageSize": page_size}
        ).json()

    def iter_batches(self, *, page_size: int = 20) -> Iterator[dict]:
        """自动翻页，逐个 yield 批任务。list_batches() 的响应体只有 `total`
        没有 `totalPages`，所以翻页终止靠"已取到的条数 >= total"判断，而不是
        像 iter_records() 那样比较页码。"""
        page = 1
        seen = 0
        while True:
            body = self.list_batches(page=page, page_size=page_size)
            items = body["items"]
            if not items:
                return
            for item in items:
                yield item
            seen += len(items)
            if seen >= body["total"]:
                return
            page += 1

    def get_batch(self, batch_id: str) -> dict:
        """查询单个批任务的状态与进度。"""
        return self._request("GET", f"/ai-batches/{batch_id}").json()

    def get_batch_results(self, batch_id: str) -> dict:
        """取回每个文件的处理结果。返回 {'batchId', 'status', 'results': [...]}；
        `results[].output` 只在该子任务 status='completed' 时非 null。"""
        return self._request("GET", f"/ai-batches/{batch_id}/results").json()

    def get_batch_file_records(self, batch_id: str) -> dict:
        """获取每个子会话被自动记录的新增/修改文件。返回
        {'batchId', 'status', 'results': [{'name','seq','status','files'}, ...]}。"""
        return self._request("GET", f"/ai-batches/{batch_id}/file-records").json()

    def import_batch_files(
        self,
        batch_id: str,
        paths: list,
        *,
        name: Optional[str] = None,
        seq: Optional[int] = None,
    ) -> dict:
        """把子会话工作区里被记录过的文件导入系统 data_files（幂等）。
        name 与 seq 二选一用于定位子会话，与 get_batch_results() 里
        results[].name / 序号同口径。"""
        if name is None and seq is None:
            raise ValueError("name 或 seq 必须提供一个")
        body = {"paths": paths}
        if name is not None:
            body["name"] = name
        if seq is not None:
            body["seq"] = seq
        return self._request(
            "POST", f"/ai-batches/{batch_id}/import", json_body=body
        ).json()

    def delete_batch(self, batch_id: str) -> dict:
        """删除批任务（不可逆，会清理 AI 会话工作区）。返回 {'deleted': True}。"""
        return self._request("DELETE", f"/ai-batches/{batch_id}").json()

    def retry_failed_batch_sessions(self, batch_id: str) -> dict:
        """把批任务中失败的子任务重置为待处理，交由后台重跑（保留原有
        prompt/agent/model 配置，不重跑已成功的子任务）。返回 {'retried': N}。"""
        return self._request("POST", f"/ai-batches/{batch_id}/retry-failed").json()

    def append_batch_files(self, batch_id: str, files: list) -> dict:
        """向已有批任务追加文件（任何状态都可追加，沿用原有 prompt/agent/model）。
        返回 {'batchId', 'status', 'total', 'appended'}。"""
        return self._request(
            "POST", f"/ai-batches/{batch_id}/append", json_body={"files": files}
        ).json()

    def update_batch_config(
        self,
        batch_id: str,
        *,
        agent: Optional[str] = None,
        model: Optional[str] = None,
        callback_url: Optional[str] = None,
        callback_secret: Optional[str] = None,
    ) -> dict:
        """修改批任务的 agent/model/回调配置。

        ⚠️ 整体替换语义，不是局部 patch：省略的参数会被清空为默认值，不是
        「保持原样」——每次调用都要把想保留的字段一起传，包括 callback_url/
        callback_secret（只传 agent/model 会把已配置的回调清空）。
        """
        body = {
            "agent": agent,
            "model": model,
            "callbackUrl": callback_url,
            "callbackSecret": callback_secret,
        }
        return self._request("PATCH", f"/ai-batches/{batch_id}", json_body=body).json()

    def list_batch_session_messages(self, batch_id: str, child_id: str) -> dict:
        """获取子会话的完整对话历史（含工具调用）。child_id 可以是 batch_seq
        （数字）或输入文件名，两者等价。"""
        return self._request(
            "GET", f"/ai-batches/{batch_id}/sessions/{child_id}/messages"
        ).json()

    def list_batch_session_files(self, batch_id: str, child_id: str) -> dict:
        """实时扫描子会话工作区，返回当前文件列表。"""
        return self._request(
            "GET", f"/ai-batches/{batch_id}/sessions/{child_id}/files"
        ).json()

    def download_batch_session_file(
        self,
        batch_id: str,
        child_id: str,
        path: str,
        *,
        dest: Optional[PathLike] = None,
    ) -> bytes:
        """下载子会话工作区中的单个文件；传 dest 时同时写入本地磁盘路径。"""
        resp = self._request(
            "GET",
            f"/ai-batches/{batch_id}/sessions/{child_id}/files/download",
            params={"path": path},
            stream=True,
        )
        content = resp.content
        if dest is not None:
            Path(dest).write_bytes(content)
        return content

    def download_batch_session_files_zip(
        self,
        batch_id: str,
        child_id: str,
        *,
        include: Optional[Union[str, list]] = None,
        dest: Optional[PathLike] = None,
    ) -> bytes:
        """将子会话工作区中新增/修改的文件打包为 ZIP 下载（基于系统自动记录
        的变更，不是实时扫描）。include 默认 "added,modified"，可传列表或
        逗号分隔字符串筛选状态子集。"""
        params = {}
        if include is not None:
            params["include"] = ",".join(include) if isinstance(include, list) else include
        resp = self._request(
            "GET",
            f"/ai-batches/{batch_id}/sessions/{child_id}/files/download-all",
            params=params,
            stream=True,
        )
        content = resp.content
        if dest is not None:
            Path(dest).write_bytes(content)
        return content

    def continue_batch_session(self, batch_id: str, child_id: str, prompt: str) -> dict:
        """在已完成/失败的子会话上追加一轮对话，保留历史上下文（与
        reexecute_batch_session 不同，不会清空历史/换新会话）。"""
        return self._request(
            "POST",
            f"/ai-batches/{batch_id}/sessions/{child_id}/continue",
            json_body={"prompt": prompt},
        ).json()

    def reexecute_batch_session(self, batch_id: str, child_id: str) -> dict:
        """重新执行单个子会话：清空历史、新建 AI 会话、用批任务原始 prompt
        重跑。仅限终态（completed/failed）的子会话。"""
        return self._request(
            "POST", f"/ai-batches/{batch_id}/sessions/{child_id}/reexecute"
        ).json()

    def translate_query(self, collection: str, question: str) -> dict:
        """把中文/英文问题翻译成 MongoDB 风格的查询过滤器，不执行查询本身。
        返回的 filter 可直接传给 list_records()/iter_records() 未来若支持
        `q` 参数时使用（当前 list_records() 尚未透出该参数）。

        返回 {'filter': {...}}。
        """
        return self._request(
            "POST",
            "/ai-batches/query",
            json_body={"collection": collection, "question": question},
        ).json()

    # ---------------------------------------------------------------
    # AI 单会话（/v1/ai-sessions，对照
    # docs/user-guide/integration/ai-session-api.md）——批任务要求至少一个
    # 文件、且每个文件拆一个子会话；单会话是"一句 prompt（可选带上几个文件）
    # 要一个综合答案"的轻量版，跟批任务共用同一套执行引擎、并发上限、以及
    # upload_batch_files() 这同一个上传接口（没有专属上传端点），只是所有
    # 文件进同一个会话、不拆分子任务，也没有 list/delete/continue/retry，
    # 只有创建和查状态两个端点。agent/model 的可选值发现复用
    # list_batch_agents()/list_batch_models()，不重复建端点。
    # ---------------------------------------------------------------

    def create_ai_session(
        self,
        prompt: str,
        *,
        agent: Optional[str] = None,
        model: Optional[str] = None,
        title: Optional[str] = None,
        files: Optional[list] = None,
    ) -> dict:
        """创建一个单会话。立即返回 pending，真正执行是异步的（跟批任务同一个
        worker），用 get_ai_session() 轮询直到进入终态。返回 {sessionId, status}。

        files（可选）是 upload_batch_files() 返回的 files 数组（或其子集），
        每项需含 name 与 path；最多 50 个，不要求来自同一次上传调用。留空 =
        纯文本会话；随附的文件全部进入这一个 AI 会话供 prompt 读取。
        """
        body = {"prompt": prompt}
        if agent is not None:
            body["agent"] = agent
        if model is not None:
            body["model"] = model
        if title is not None:
            body["title"] = title
        if files is not None:
            body["files"] = files
        return self._request("POST", "/ai-sessions", json_body=body).json()

    def get_ai_session(self, session_id: str) -> dict:
        """查询单会话状态。`status` 为 pending/running/completed/failed；
        `output` 只在 completed 时非 null，`error` 只在 failed 时非 null。
        没有 SSE——轮询是唯一的完成信号获取方式。"""
        return self._request("GET", f"/ai-sessions/{session_id}").json()

    # ---------------------------------------------------------------
    # Row Actions 对外触发（/v1/collections/.../row-actions/.../run，
    # 对照 docs/user-guide/data/row-actions.md §11）
    # ---------------------------------------------------------------

    def run_row_action(
        self,
        collection: str,
        record_id: str,
        action_id: str,
        *,
        params: Optional[dict] = None,
        branch_id: str = "main",
    ) -> dict:
        """触发已在页面上配置好的行操作（webhook 或 AI 类型），效果等同于
        登录用户在界面「⋯」菜单里点击该按钮。action_id 需要线下从配置该行
        操作的管理员那里获取（本客户端不提供发现/列表方法）。

        ⚠️ 本端点的 error 文案是中文（透传服务端 RowActionError 原文），
        跟本 SDK 其余方法对应的英文 error 惯例不同，属有意为之的例外。

        返回 {'ok', 'status', 'statusField', 'runningValue'}。
        """
        return self._request(
            "POST",
            f"/collections/{collection}/{record_id}/row-actions/{action_id}/run",
            json_body={"params": params or {}},
            params={"branchId": branch_id},
        ).json()

    # ---------------------------------------------------------------
    # AI 定时扫描任务（/v1/ai-scan-tasks，对照
    # docs/user-guide/ai/scan-tasks.md §10）
    # ---------------------------------------------------------------

    def list_scan_tasks(self) -> dict:
        """列出这把密钥所属用户创建的 AI 定时扫描任务。返回 {'tasks': [...]}。"""
        return self._request("GET", "/ai-scan-tasks").json()

    def get_scan_task(self, task_id: str) -> dict:
        """查询单个扫描任务（仅限自己创建的，否则 404）。"""
        return self._request("GET", f"/ai-scan-tasks/{task_id}").json()

    def run_scan_task_now(self, task_id: str) -> dict:
        """立即触发一次扫描：同步完成"认领当前符合条件的待处理记录 + 建一个
        批任务"，真正的 AI 处理仍是异步的（批任务 worker 接手）。

        返回 {'triggered': True, 'claimedCount': N, 'lastError': str|None}。
        """
        return self._request("POST", f"/ai-scan-tasks/{task_id}/run-now").json()

    # ---------------------------------------------------------------
    # Prompt 模板（/v1/prompt-templates，per-user CRUD）
    # ---------------------------------------------------------------

    def list_prompt_templates(self) -> dict:
        """列出这把密钥所属用户的全部 Prompt 模板。返回 {'templates': [...]}。"""
        return self._request("GET", "/prompt-templates").json()

    def create_prompt_template(self, name: str, content: str) -> dict:
        """新建一个 Prompt 模板。name 在同一用户下唯一，重名抛 ConflictError。"""
        return self._request(
            "POST", "/prompt-templates", json_body={"name": name, "content": content}
        ).json()

    def get_prompt_template(self, template_id: str) -> dict:
        return self._request("GET", f"/prompt-templates/{template_id}").json()

    def update_prompt_template(self, template_id: str, name: str, content: str) -> dict:
        """整条替换 name + content（两者都必填，不是局部更新）。"""
        return self._request(
            "PUT",
            f"/prompt-templates/{template_id}",
            json_body={"name": name, "content": content},
        ).json()

    def delete_prompt_template(self, template_id: str) -> None:
        """删除一个 Prompt 模板。成功返回 None（服务端 204 无响应体）。"""
        self._request("DELETE", f"/prompt-templates/{template_id}")
        return None

    # ---------------------------------------------------------------
    # 长期记忆（/v1/memories，对照 docs/user-guide/ai/long-term-memory.md
    # 「对外 API」小节）
    # ---------------------------------------------------------------

    def list_memories(self) -> dict:
        """列出这把密钥所属用户的全部长期记忆。返回 {'memories': [...]}，
        原样透传 mem0 的返回形状（通常每条含 'id'/'memory' 等字段）。"""
        return self._request("GET", "/memories").json()

    def add_memory(self, text: str, *, verbatim: bool = False) -> dict:
        """补写一条长期记忆。verbatim=False（默认）会先经 AI 提炼成简洁事实
        再入库；verbatim=True 原样保存、不提炼。

        若系统未配置/未启用记忆功能，抛 MemoryUnavailableError（409）。
        返回 {'ok': True, 'memories': [...]}（写入后的最新列表）。
        """
        return self._request(
            "POST", "/memories", json_body={"text": text, "verbatim": verbatim}
        ).json()

    def delete_memory(self, memory_id: str) -> dict:
        """删除一条记忆。memory_id 是 mem0 的不透明 UUID；服务端会先校验这条
        记忆确实属于这把密钥所属的用户，不属于则 404（不是 403，不泄漏存在性）。
        """
        return self._request("DELETE", f"/memories/{memory_id}").json()
