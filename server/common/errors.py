"""
自定义异常类
"""


class MergeError(Exception):
    """合并操作异常"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


# 错误码常量
VERSION_NOT_FOUND = 'VERSION_NOT_FOUND'
BRANCH_NOT_FOUND = 'BRANCH_NOT_FOUND'
PERMISSION_DENIED = 'PERMISSION_DENIED'
MERGE_FAILED = 'MERGE_FAILED'
VERSION_ALREADY_MERGED = 'VERSION_ALREADY_MERGED'