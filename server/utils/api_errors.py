"""统一的对外 API 错误响应 helper（/v1/* 六大 AI 能力蓝图共用）。

`err()` 是对既有 `jsonify({'error': '...'}), status` 写法的加法扩展：新增一个
兄弟字段 `code`，不改变 `error` 字段本身的形状（现有集成方读 `error` 当字符串，
这是既定契约，不能破坏）。这个 {error, code} 形状在 open_api_memories.py 的
`MEMORY_UNAVAILABLE` 里已经有过一次先例，这里是把它推广成全局约定。
"""

INVALID_ARGUMENT = 'INVALID_ARGUMENT'
NOT_FOUND = 'NOT_FOUND'
UNAUTHORIZED = 'UNAUTHORIZED'
FORBIDDEN = 'FORBIDDEN'
PAYLOAD_TOO_LARGE = 'PAYLOAD_TOO_LARGE'
CONFLICT = 'CONFLICT'
INTERNAL_ERROR = 'INTERNAL_ERROR'
UPSTREAM_UNAVAILABLE = 'UPSTREAM_UNAVAILABLE'


def err(message: str, code: str, status: int):
    """返回可直接 `return` 的 (jsonify(...), status) 二元组。"""
    from flask import jsonify
    return jsonify({'error': message, 'code': code}), status


def register_error_handlers(bp):
    """给一个 open_api_*.py 蓝图挂上兜底异常处理，让它管辖内的路由永远回
    JSON（而不是 Flask/Werkzeug 默认的 HTML 错误页）。

    刻意注册在**蓝图**上而不是 `app.errorhandler`：Flask 按 `request.blueprints`
    做 handler 查找，蓝图级 handler 只对经过该蓝图路由匹配到的请求生效——不会
    影响其余 30+ 个内部蓝图的报错行为（包括 pytest 下依赖异常直接抛出失败的
    测试）。唯一没覆盖到的边界情况是"这个蓝图 url_prefix 下完全没匹配到任何
    路由"（比如 /v1/ai-batches/不存在的路径）——那种 404 在路由匹配阶段就发生，
    还没进入到任何蓝图的请求上下文，只能退回 Flask 默认 404 页；六个蓝图各自
    已知的路由错误、以及路由内部抛出的未预期异常都会被这里接住。

    单个 `Exception` handler 就够了：Flask 按异常的 MRO 查表，HTTPException
    （404/400 等）本身也是 Exception 的子类，同一个 handler 两种都会命中。
    """
    import logging
    from werkzeug.exceptions import HTTPException

    logger = logging.getLogger('open_api')

    @bp.errorhandler(Exception)
    def _handle(e):
        from flask import request
        if isinstance(e, HTTPException):
            code = (e.name or 'ERROR').upper().replace(' ', '_')
            return err(e.description or e.name, code, e.code or 500)
        logger.exception('unhandled exception on %s %s', request.method, request.path)
        return err('服务器内部错误', INTERNAL_ERROR, 500)
