import json
import re


def extract_json(text):
    """Return the parsed JSON object from the AI reply, or None.
    Prefers the last ```json fenced block; falls back to the last balanced {...}."""
    if not text:
        return None
    blocks = re.findall(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    for raw in reversed(blocks):
        try:
            return json.loads(raw)
        except ValueError:
            continue
    # fallback: scan for balanced top-level objects, try the last parseable one
    candidates = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(text[start:i + 1])
    for raw in reversed(candidates):
        try:
            return json.loads(raw)
        except ValueError:
            continue
    return None


def message_text(final_msg):
    """Concatenate the text parts of an assistant message dict."""
    if not final_msg:
        return ''
    parts = [p.get('text', '') for p in (final_msg.get('content') or [])
             if p.get('type') == 'text']
    return '\n'.join(t for t in parts if t)


def assemble_prompt(task):
    """[system preamble] + [user prompt_template] + [system JSON output contract]."""
    keys = [m['jsonKey'] for m in (task.get('field_mapping') or [])]
    contract_obj = ', '.join(f'"{k}": ...' for k in keys)
    preamble = ('本任务的数据见工作区 uploads/record.md，附件见 uploads/attachments/ 目录。\n'
                '请阅读这些内容后完成下面的任务。\n\n')
    contract = ('\n\n---\n完成后，请在回复的最后输出一个 JSON 代码块，'
                f'且仅包含以下字段：\n```json\n{{ {contract_obj} }}\n```')
    return preamble + (task.get('prompt_template') or '') + contract


def on_child_finished(session_row, final_msg, ok):
    """Write-back hook fired by the batch worker for scan-task children.
    Full implementation in Phase 2."""
    return None
