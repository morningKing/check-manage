"""AI 批任务对外接口的请求体大小上限 —— **Flask 与反向代理共用的唯一真源**。

为什么要单独一个模块：这道门必须在**两个进程**里各判一次。

* Flask 侧（`routes/open_api_batches.py` 的 `before_request`）挡住直连后端 /
  经 Vite 开发代理的请求；
* 反向代理侧（`proxy.py`）必须在 `self.rfile.read(content_length)` 之前挡住 ——
  那一行把整个请求体同步读进**代理进程内存**，发生在 Flask 的钩子之前。只在
  Flask 里设限，等于这道门在生产入口 `:8080` 上完全失效。

`proxy.py` 是独立进程、不能 import Flask/db 那一坨，所以本模块**零依赖**（不
import 任何东西，只有数字和路径前缀），两边都从这里读，改一处两边一致。

⚠️ 上限只作用于 AI 批任务对外端点，**绝不能变成全局限制**：备份还原
（`/api/backups/upload-restore`）上传的 ZIP 含 `vector_store/` 与 `data_files/`，
大小本质无上界，任何全局值都会直接打断灾难恢复。`body_limit_for_path` 对不属于
本套接口的路径一律返回 None（= 不限制），就是为了守住这条。
"""

MAX_UPLOAD_TOTAL_BYTES = 100 * 1024 * 1024   # 单次上传所有文件累计 100 MB
# multipart 的分隔符/各分部头部会让整个请求体略大于文件字节之和，留 1 MB 余量，
# 免得一次正好 100 MB 的合法上传被前置门误伤（真正的 100 MB 判定在视图里逐文件累加）。
MAX_UPLOAD_REQUEST_BYTES = MAX_UPLOAD_TOTAL_BYTES + 1024 * 1024
# 其余端点都是 JSON：prompt 上限 20000 字符 + 最多 50 个文件项，1 MB 绰绰有余。
MAX_JSON_BODY_BYTES = 1024 * 1024

# 公网路径（代理看到的）与后端路径（Flask 看到的）。proxy.py 的契约是
# 「/api/X → 后端 /X」，所以同一个端点在两边长得不一样，两个前缀都要认。
AI_BATCH_PUBLIC_PREFIX = '/api/v1/ai-batches'
AI_BATCH_BACKEND_PREFIX = '/v1/ai-batches'

_PREFIXES = (AI_BATCH_PUBLIC_PREFIX, AI_BATCH_BACKEND_PREFIX)


def body_limit_for_path(path):
    """返回该路径允许的请求体字节上限；不属于 AI 批任务对外接口则返回 None。

    `path` 可以是公网路径（`/api/v1/ai-batches/uploads`）或后端路径
    （`/v1/ai-batches/uploads`），可带查询串。None 表示**不限制** —— 其余端点
    （尤其是备份还原）必须保持无上限。
    """
    p = (path or '').split('?', 1)[0].split('#', 1)[0]
    for prefix in _PREFIXES:
        if p == prefix or p.startswith(prefix + '/'):
            rel = p[len(prefix):].rstrip('/')
            return MAX_UPLOAD_REQUEST_BYTES if rel == '/uploads' else MAX_JSON_BODY_BYTES
    return None
