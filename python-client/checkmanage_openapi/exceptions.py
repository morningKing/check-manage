"""Open API 客户端异常层级，与 open-api.md §7.2 的错误码表一一对应。"""


class OpenApiError(Exception):
    """所有 Open API 客户端错误的基类。"""

    def __init__(self, message, *, status_code=None, details=None, code=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details
        self.code = code

    def __str__(self):
        parts = [self.message]
        if self.status_code is not None:
            parts.append(f"(HTTP {self.status_code})")
        return " ".join(parts)


class AuthenticationError(OpenApiError):
    """401：Missing / Invalid API key，或密钥已被停用。"""


class WriteNotAllowedError(OpenApiError):
    """403 Collection is read-only：目标集合未开启「允许写入」。"""


class NotFoundError(OpenApiError):
    """404：集合 / 记录 / 分支 / 文件不存在，或未开放 Open API 访问。"""


class ValidationError(OpenApiError):
    """400：请求体为空，或必填字段缺失（校验详情见 .details）。"""


class ConflictError(OpenApiError):
    """409：记录 ID 冲突 / 主键冲突。"""


class VersionConflictError(ConflictError):
    """409 code=VERSION_CONFLICT：PUT 时乐观锁并发冲突，需重新 GET 后携带最新 _version 重试。"""
