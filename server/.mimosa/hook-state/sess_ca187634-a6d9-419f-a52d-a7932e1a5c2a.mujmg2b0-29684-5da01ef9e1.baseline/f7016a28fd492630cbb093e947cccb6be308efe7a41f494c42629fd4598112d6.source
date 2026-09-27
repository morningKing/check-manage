"""动作门禁期望提炼器(M1.5,设计 §5.2「AI 自动提炼」)。

一次独立的 LLM 调用分析任务文本 + Agent 配置 + Skill 清单/步骤,产出
action_checks 建议(预填入口 A/B 表单)。三道机械校验的后两道在调用方:
① 本模块做 schema/正则/原语校验(复用 agent_ledger.validate_checks);
② 历史记账 dry-run 由前端/接口用 agent_ledger.count_tree_tool_calls 完成;
③ 人工一键确认后登记。红线:提炼只产出建议,执行侧无增删期望的路径。
"""
import json
import logging
import os
import re

import requests

from utils.ai_query import get_ai_settings, get_http_session
from utils.agent_ledger import validate_checks

log = logging.getLogger(__name__)

_MAX_CHECKS = 8
_MAX_SKILL_CHARS = 6000

_SYSTEM_PROMPT = """你是 AI 任务的"动作门禁期望提炼器"。给定任务文本(可附 Agent \
配置与 Skill 文档),提炼出**任务必经的、可用工具调用记录验证的动作**清单。

只输出 JSON 数组,不要多余文字。每项形如:
{"name": "克隆目标仓库", "check_type": "tool", "tool": "bash", "args_pattern": "git clone\\\\s+\\\\S*acme/inspector", "min_count": 1}

规则:
1. 只提炼"必经"动作(任务字面要求或所附 Skill 步骤点名的动作),宁缺勿滥,最多 6 条;
2. check_type="tool" 时:tool 是 OpenCode 工具名(bash/read/write/edit/grep/glob),\
args_pattern 是 POSIX 正则,匹配工具入参拼接文本(形如 "command=git clone …"、\
"filePath=C:\\…\\a.md"),所以要匹配值本身而不是 key;文件名/路径中的点要转义;
3. 产物落文件的任务可改用 check_type="file"(effect_spec:{"path":"outputs/x.xlsx",\
工作区相对 glob})或 check_type="db_record"(effect_spec:{"collection":"slug",\
"filter":{…Mongo 风格}})表达效果级要求;
4. name 用简短中文;不要虚构任务里不存在的仓库/脚本/文件。"""


def _extract_json_array(text: str) -> list:
    """从模型回复里抠出 JSON 数组(容忍 ```json 围栏与前后废话)。"""
    text = (text or '').strip()
    fence = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find('['), text.rfind(']')
        if start == -1 or end <= start:
            raise ValueError('模型回复中未找到 JSON 数组')
        text = text[start:end + 1]
    arr = json.loads(text)
    if not isinstance(arr, list):
        raise ValueError('模型回复不是 JSON 数组')
    return arr


def _load_skill_texts(skill_names) -> list:
    """按名字解析已启用的全局技能并读 SKILL.md 正文(静态可知,派发前可读)。
    skill_names 是名字数组;未启用/不存在的名字静默跳过。
    存储约定同 inject_global_skills:<workspace_root>/global-skills/<name>/SKILL.md。"""
    wanted = [str(n) for n in (skill_names or []) if n]
    if not wanted:
        return []
    from utils.global_skills import global_skills_root, list_global_skills
    from config import AI_WORKSPACE_ROOT
    root = global_skills_root(AI_WORKSPACE_ROOT)
    enabled = {s.get('name') for s in list_global_skills() if s.get('enabled')}
    texts = []
    for name in wanted:
        if name not in enabled:
            continue
        path = os.path.join(root, name, 'SKILL.md')
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                content = f.read(_MAX_SKILL_CHARS)
        except OSError:
            continue
        if content:
            texts.append(f"### Skill: {name}\n{content}")
    return texts


def extract_action_checks(task_text: str, agent: str | None = None,
                          skills: list | None = None) -> list:
    """提炼动作期望建议。返回经 validate_checks 规范化的数组(可能为空)。

    Raises RuntimeError: AI 未启用/调用失败/回复不可解析(路由层转 400/502)。"""
    cfg = get_ai_settings()
    if not cfg['enabled']:
        raise RuntimeError('AI 功能未启用，请在系统配置中开启')
    if not cfg['apiKey']:
        raise RuntimeError('AI 服务未配置 API Key')

    user_parts = [f"# 任务文本\n{(task_text or '').strip()[:8000]}"]
    if agent:
        user_parts.append(f"# Agent\n{agent}")
    for t in _load_skill_texts(skills):
        user_parts.append(t)

    payload = json.dumps({
        'model': cfg['model'],
        'messages': [
            {'role': 'system', 'content': _SYSTEM_PROMPT},
            {'role': 'user', 'content': '\n\n'.join(user_parts)},
        ],
        'temperature': 0.1,
        'max_tokens': max(int(cfg.get('maxTokens') or 1024), 1024),
    }).encode('utf-8')
    headers = {'Content-Type': 'application/json',
               'Authorization': f"Bearer {cfg['apiKey']}"}
    try:
        resp = get_http_session().post(cfg['endpoint'], data=payload,
                                       headers=headers, timeout=cfg['timeout'])
    except requests.RequestException as e:
        raise RuntimeError(f'AI 服务连接失败: {e}')
    if resp.status_code >= 400:
        raise RuntimeError(f'AI 服务请求失败 ({resp.status_code}): {resp.text[:200]}')
    try:
        content = resp.json()['choices'][0]['message']['content']
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f'AI 服务返回格式异常: {e}')

    try:
        raw = _extract_json_array(content)
    except (ValueError, json.JSONDecodeError) as e:
        raise RuntimeError(f'提炼结果不可解析: {e}')

    # 把模型可能输出的 effect_spec 内联字段规整成 validate_checks 认的形状
    cleaned = []
    for c in raw[:_MAX_CHECKS]:
        if not isinstance(c, dict):
            continue
        c = dict(c)
        for k in list(c.keys()):
            if k not in ('name', 'tool', 'args_pattern', 'min_count',
                         'scope', 'check_type', 'effect_spec'):
                c.pop(k, None)
        cleaned.append(c)
    checks = validate_checks(cleaned)
    log.info('action check extraction produced %d checks (raw %d)',
             len(checks), len(raw))
    return checks
