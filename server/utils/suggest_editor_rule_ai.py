"""自然语言 → 检查规则 的 AI 转换（管理面「AI 生成规则」用）。

一次请求的流程：临时目录建一个 OpenCode 会话 → 发送转换提示词 → 轮询 REST 消息
直到本轮 finish（复用 batch_engine 验证过的完成判据 info.finish + time.completed）
→ 从 assistant 文本里抠出 JSON → 归一化（补 id/name/severity）→ 交给
validate_rules 做与手工保存完全相同的校验。模型输出不可信，一律按数据校验，
不执行任何模型文本。
"""
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid

from config import OPENCODE_BASE_URL, get_default_chat_model
from utils.opencode_client import OpenCodeClient, OpenCodeError
from utils.suggest_editor_rules import RuleValidationError, validate_rules

logger = logging.getLogger(__name__)

CONVERT_TIMEOUT_SEC = 90
MAX_DESCRIPTION_CHARS = 2000


class RuleConversionError(RuntimeError):
    """AI 调用/超时等上游型失败（路由层映射为 502）。"""


class RuleParseError(RuleConversionError):
    """模型输出无法解析为规则（路由层映射为 422）。"""


def build_convert_prompt(description: str) -> str:
    return f"""你是"检查规则生成器"。把用户用自然语言描述的检查要求，转换成下面的规则 JSON。

规则结构：
{{"rules": [
  {{"id": "rule-xxx", "name": "简短中文名", "enabled": true, "severity": "error|warn|info",
    "aiPrompt": "可选：无法用确定性机制表达的语义要求，写给 AI 的核对说明",
    "maps": [{{"wrong": "错误写法", "right": "正确写法", "caseSensitive": false}}],
    "patterns": [{{"extract": "提取候选的正则", "valid": "合规判定的正则", "message": "提示语"}}],
    "whitelists": [{{"anchors": ["锚点词："], "values": ["允许值1", "允许值2"], "message": "提示语"}}],
    "presence": [{{"triggers": ["出现即触发"], "requiresAny": ["必须出现其一1", "必须出现其一2"], "message": "提示语"}}]
  }}
]}}

四类本地判定（优先使用；毫秒级、确定性）：
- maps：错误写法→正确写法，输入即提示替换；
- patterns：用 extract 在文本中提取候选 token，用 valid 判断是否合规，不合规提示；
- whitelists：锚点词之后出现的值必须在 values 白名单内，否则提示；
- presence：文档出现 triggers 任一词、且 requiresAny 一个都没出现时提醒——
  用于"必须写明 A，或明确声明 B（豁免）"这类完整性规则。
- aiPrompt 只用于确实需要语义理解的要求，不要与确定性机制重复表达。

硬性要求：
1. id 以 "rule-" 开头、仅含字母数字与连字符、每条唯一；
2. 每条规则 name 非空、简短；
3. 正则是合法正则（不要用 Python 特有语法）；字符串值不要包含换行符；
4. 每条规则至少包含一种机制或 aiPrompt；
5. 如果要求本质是**语义判断**（如"必须说明是哪个系统/模块"、"语气要正式"、"逻辑要成立"），
   不要硬构造脆弱的 extract/valid 正则，直接写 aiPrompt；只有"错误写法→正确写法"、
   "枚举白名单"、"型号格式"、"出现 A 必须写明 B 或豁免词"这类可机械判定的，才用 maps/
   patterns/whitelists/presence；
6. 只输出 JSON 对象本身：不要 markdown 代码块、不要解释文字。

示例 1：用户说"涉及 9800M12 的设备方案，必须写 9800UPS，或者明确写不涉及拖带"
→ {{"rules": [{{"id": "rule-9800-drag", "name": "拖带完整性", "enabled": true, "severity": "warn",
  "presence": [{{"triggers": ["9800M12"], "requiresAny": ["9800UPS", "不涉及拖带", "无需拖带", "不拖带"],
  "message": "涉及 9800M12 时须写明 9800UPS，或明确声明不涉及拖带"}}],
  "aiPrompt": "涉及 9800M12 时须写明 9800UPS 或声明不涉及拖带；满足其一即合规，已满足不要提建议。"}}]}}

示例 2：用户说"型号写法要统一，S-5700 应写成 S5700"
→ {{"rules": [{{"id": "rule-model-name", "name": "型号写法", "enabled": true, "severity": "error",
  "maps": [{{"wrong": "S-5700", "right": "S5700"}}]}}]}}

示例 3：用户说"设备类型后面只能写 S5700、S6700、S7700 之一"
→ {{"rules": [{{"id": "rule-device-type", "name": "设备类型白名单", "enabled": true, "severity": "warn",
  "whitelists": [{{"anchors": ["设备类型：", "设备类型:"], "values": ["S5700", "S6700", "S7700"],
  "message": "设备类型不在规范清单内"}}]}}]}}

用户描述：
{description}
"""


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_rule_json(text: str) -> list:
    """从模型回复里抠出 {"rules": [...]}。

    容忍：markdown 代码块围栏、JSON 前后夾杂说明文字。找不到 JSON 即抛
    RuleParseError（路由层给用户可操作的提示）。"""
    if not text or not text.strip():
        raise RuleParseError("AI 返回为空")
    cleaned = _FENCE_RE.sub("", text).strip()
    start = cleaned.find("{")
    if start < 0:
        raise RuleParseError("AI 返回里没有 JSON 对象")
    depth = 0
    end = -1
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise RuleParseError("AI 返回的 JSON 不完整")
    try:
        payload = json.loads(cleaned[start:end])
    except ValueError as e:
        raise RuleParseError(f"AI 返回的 JSON 解析失败：{e}")
    if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
        raise RuleParseError("AI 返回结构必须是 {\"rules\": [...]}")
    return payload["rules"]


def normalize_rules(raw_rules: list, description: str) -> list:
    """模型输出不可信：补 id/name/severity/enabled、去重 id，再走统一校验。"""
    normalized = []
    seen = set()
    for index, rule in enumerate(raw_rules):
        if not isinstance(rule, dict):
            raise RuleParseError(f"第 {index + 1} 条规则不是对象")
        item = dict(rule)
        rule_id = str(item.get("id") or "").strip()
        if not rule_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", rule_id):
            rule_id = f"rule-{uuid.uuid4().hex[:12]}"
        while rule_id in seen:
            rule_id = f"rule-{uuid.uuid4().hex[:12]}"
        item["id"] = rule_id
        seen.add(rule_id)
        name = str(item.get("name") or "").strip()
        item["name"] = name or f"规则 {index + 1}"
        if item.get("severity") not in ("info", "warn", "error"):
            item["severity"] = "warn"
        item.setdefault("enabled", True)
        normalized.append(item)
    if not normalized:
        raise RuleParseError("AI 没有生成任何规则，请把描述写得更具体")
    try:
        return validate_rules({"rules": normalized})
    except RuleValidationError as e:
        raise RuleParseError(f"AI 生成的规则未通过校验：{e}")


def _convert_model() -> str:
    """转换用模型：优先 AGENT_MODEL（与编辑器分析同一个模型，已按写作场景验证），
    其次平台「AI 配置」默认模型，最后空串（OpenCode 自身默认）。"""
    return (os.getenv("AGENT_MODEL") or "").strip() or get_default_chat_model()


def _serve_hint(model: str) -> str:
    """502 时给管理员的可操作提示：生产多用 proxy.py 托管，先看 [3/5] 段。"""
    return (
        f"opencode serve={OPENCODE_BASE_URL}，model={model or '默认模型'}。"
        "请检查 proxy.py 启动日志 [3/5] 段（OpenCode reachable/started）、"
        "server/.env 的 OPENCODE_BIN / OPENCODE_BASE_URL、以及 server/oc-serve.log；"
        "服务刚启动时首次生成可能较慢，可直接重试"
    )


def _ensure_serve_reachable() -> None:
    """转换前先探活：serve 没起来时立刻给出可定位的错误，而不是等 90 秒超时。"""
    try:
        OpenCodeClient(OPENCODE_BASE_URL, timeout=3).list_providers()
    except Exception as e:
        raise RuleConversionError(
            f"opencode serve 不可达（{OPENCODE_BASE_URL}）：{e}。{_serve_hint(_convert_model())}"
        )


def _describe_model_error(error) -> str:
    """OpenCode 的 info.error 形如 {name, data:{message}}；转成人能读的一行。"""
    if isinstance(error, dict):
        name = str(error.get("name") or "").strip()
        data = error.get("data")
        detail = ""
        if isinstance(data, dict):
            detail = str(data.get("message") or data.get("error") or "").strip()
        elif data:
            detail = str(data).strip()
        if name or detail:
            return f"{name}：{detail}".strip("：")
        return json.dumps(error, ensure_ascii=False)[:300]
    return str(error)[:300]


def convert_nl_to_rules(description: str) -> list:
    """自然语言 → 已校验的规则列表。上游失败抛 RuleConversionError。"""
    model = _convert_model()
    started = time.time()
    logger.info("rule-convert start model=%s serve=%s desc_len=%d",
                model or "default", OPENCODE_BASE_URL, len(description))
    _ensure_serve_reachable()
    client = OpenCodeClient(OPENCODE_BASE_URL)
    workdir = tempfile.mkdtemp(prefix="se-rule-convert-")
    session_id = ""
    try:
        try:
            session_id = client.create_session(directory=workdir, title="规则生成")
            client.send_prompt_async(
                session_id, build_convert_prompt(description),
                model=model, directory=workdir,
            )
        except OpenCodeError as e:
            raise RuleConversionError(
                f"AI 服务调用失败（create_session，{OPENCODE_BASE_URL}）：{e}。{_serve_hint(model)}"
            )
        except Exception as e:  # requests 层异常等
            raise RuleConversionError(
                f"AI 服务不可用（{OPENCODE_BASE_URL}）：{e}。{_serve_hint(model)}"
            )

        deadline = time.time() + CONVERT_TIMEOUT_SEC
        text = ""
        poll_errors = 0
        while time.time() < deadline:
            time.sleep(1.0)
            try:
                messages = client.get_messages(session_id, directory=workdir)
            except Exception as e:
                poll_errors += 1
                if poll_errors == 1 or poll_errors % 30 == 0:
                    logger.warning("rule-convert poll failed session=%s count=%d: %s",
                                   session_id, poll_errors, e)
                continue
            assistant = [m for m in messages if (m.get("info") or {}).get("role") == "assistant"]
            if not assistant:
                continue
            last = assistant[-1]
            info = last.get("info") or {}
            # 模型/供应商侧错误（鉴权失败、额度、模型不存在…）：立刻报，不再空等 90 秒
            model_error = info.get("error")
            if model_error:
                logger.warning("rule-convert model error session=%s: %s", session_id, model_error)
                raise RuleConversionError(
                    f"模型调用失败（{model or '默认模型'}）：{_describe_model_error(model_error)}。"
                    f"{_serve_hint(model)}"
                )
            if info.get("finish") and (info.get("time") or {}).get("completed"):
                text = "\n".join(
                    p.get("text", "") for p in (last.get("parts") or []) if p.get("type") == "text"
                )
                break
        else:
            elapsed = time.time() - started
            logger.warning("rule-convert timeout session=%s after=%.1fs model=%s poll_errors=%d",
                           session_id, elapsed, model or "default", poll_errors)
            raise RuleConversionError(
                f"AI 生成超时（>{CONVERT_TIMEOUT_SEC}s，实际 {elapsed:.0f}s，"
                f"轮询失败 {poll_errors} 次）：{_serve_hint(model)}"
            )
        rules = normalize_rules(parse_rule_json(text), description)
        logger.info("rule-convert ok session=%s rules=%d %.1fs",
                    session_id, len(rules), time.time() - started)
        return rules
    finally:
        if session_id:
            try:
                client.delete_session(session_id)
            except Exception:
                pass
        shutil.rmtree(workdir, ignore_errors=True)
