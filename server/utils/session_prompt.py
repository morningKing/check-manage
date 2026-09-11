"""Shared prompt preparation for ordinary and batch AI sessions."""

import os

from utils.data_export import (
    ExportError,
    export_collection_to_xlsx,
    is_export_intent,
    resolve_collection_from_text,
)
from utils.mention_files import inline_file_mentions
from utils.memory import render_memory_block, search_memory
from utils.workspace import safe_resolve


_AGENT_DIRECTIVE = (
    "[系统规则] 若需产出脚本/配置/文档，把完整内容放进带语言和文件名的代码块"
    "（如 ```python app.py）。画流程图用 ```mermaid 代码块；画数据图表用 ```echarts 代码块"
    "（块内为 ECharts 的 JSON option，纯 JSON、不要函数）。"
    "回答数据查询类问题时，用 query_collection 工具查询真实数据（必要时先用 list_collections 看字段），"
    "不要臆造数据、不要写直连数据库的脚本；查询结果会自动以表格呈现给用户，"
    "你不要再用文字或 markdown 表格复述查询到的数据，只需简要说明（例如查到多少条）。"
    "拉取远端代码时用 `git clone <url> <子目录>/` 克隆到子目录（保留其 .git），"
    "切勿在工作区根目录执行 git init/clone/reset/checkout 或替换根 .git（会破坏变更跟踪、"
    "导致所有文件被误报删除）。"
    "直接给最终结果，简洁作答，不要复述本规则、不要输出你的思考或计划过程。\n\n"
)


def _safe_workspace_path(workspace_path: str, rel: str):
    try:
        return safe_resolve(workspace_path, rel)
    except Exception:
        return None


def _read_text_attachment(workspace_path: str, rel: str, max_bytes: int = 200_000):
    """Return decoded text of an uploaded file, or None if not inlineable."""
    abs_path = _safe_workspace_path(workspace_path, rel)
    if not abs_path or not os.path.isfile(abs_path):
        return None
    try:
        if os.path.getsize(abs_path) > max_bytes:
            return None
        with open(abs_path, 'rb') as f:
            return f.read().decode('utf-8')
    except (UnicodeDecodeError, OSError):
        return None


def build_session_prompt(*, content: str, workspace_path: str,
                         attachments: list[str], agent_mentions: list[dict],
                         user_id: str, role: str | None) -> tuple[str, list[dict]]:
    """Build the agent prompt while retaining the user's raw message parts.

    The returned stored parts deliberately contain no directive, memory, file
    contents, or export notices. Batch-specific input hints remain the worker's
    responsibility and are intentionally not added here.
    """
    attachments = attachments or []
    agent_mentions = agent_mentions or []
    stored_parts = [{'type': 'text', 'text': content}] if content else []

    mem_block = ''
    if content:
        mem_block = render_memory_block(search_memory(user_id, content, limit=5))
    prompt = _AGENT_DIRECTIVE + mem_block + content

    for rel in attachments:
        name = os.path.basename(rel)
        stored_parts.append({'type': 'file', 'name': name, 'path': rel})
        inlined = _read_text_attachment(workspace_path, rel)
        if inlined is not None:
            prompt += f"\n\n[用户上传的文件 {name}]\n```\n{inlined}\n```"
        else:
            abs_path = _safe_workspace_path(workspace_path, rel)
            prompt += f"\n\n[用户上传的文件 {name}，路径：{abs_path}（如需要可用工具读取）]"

    agent_names = [
        a.get('name') for a in agent_mentions
        if isinstance(a, dict) and a.get('name')
    ]
    prompt += inline_file_mentions(
        workspace_path, content,
        agent_names=agent_names, already_attached=attachments,
    )

    if is_export_intent(content):
        match = resolve_collection_from_text(content)
        if match:
            collection, label = match
            try:
                result = export_collection_to_xlsx(collection, workspace_path, role=role)
                prompt += (
                    f"\n\n[系统已将「{label}」的 {result['rows']} 条数据导出为文件 {result['path']}，"
                    "用户可在「产出文件」处下载。请简要告知用户已导出，不要重复生成脚本。]"
                )
            except ExportError:
                pass

    return prompt, stored_parts
