"""page_configs.row_actions 的保存时校验。

前端配错（绑了不存在的规则、引用了不存在的字段）在保存时就要拦下来，
否则用户点按钮才报错，排查成本高得多。
"""

# 这些控件要落库才有语义，放在「点击时弹的参数表单」里没有意义
_FORBIDDEN_PARAM_CONTROLS = ('relation', 'reference', 'quoteSelect', 'autoSequence')


def validate_row_actions(row_actions, fields, cur):
    """校验行动作配置。通过返回 None，否则返回中文错误信息。

    Args:
        row_actions: 配置数组
        fields: 该页面的字段配置数组（用于校验字段名引用）
        cur: 数据库游标（用于校验绑定的执行器是否存在）
    """
    if row_actions is None:
        return None
    if not isinstance(row_actions, list):
        return '行操作配置必须是数组'

    known_fields = {
        f.get('fieldName') for f in (fields or []) if isinstance(f, dict)
    }
    seen_ids = set()

    for idx, a in enumerate(row_actions):
        where = f'第 {idx + 1} 个行操作'
        if not isinstance(a, dict):
            return f'{where}配置格式不正确'

        aid = a.get('id')
        if not aid:
            return f'{where}缺少 id'
        if aid in seen_ids:
            return f'{where}的 id「{aid}」重复，行操作 id 必须页面内唯一'
        seen_ids.add(aid)

        if not (a.get('label') or '').strip():
            return f'{where}的名称不能为空'

        atype = a.get('actionType')
        if atype not in ('webhook', 'aiTask'):
            return f'{where}的类型「{atype}」不合法，只能是 webhook 或 aiTask'

        if atype == 'webhook':
            rule_id = a.get('webhookRuleId')
            if not rule_id:
                return f'{where}未选择 Webhook 规则'
            cur.execute('SELECT id FROM webhook_rules WHERE id = %s', (rule_id,))
            if not cur.fetchone():
                return f'{where}绑定的 Webhook 规则「{rule_id}」不存在'
        else:
            task_id = a.get('scanTaskId')
            if not task_id:
                return f'{where}未选择 AI 扫描任务'
            cur.execute('SELECT id FROM ai_scan_tasks WHERE id = %s', (task_id,))
            if not cur.fetchone():
                return f'{where}绑定的 AI 扫描任务「{task_id}」不存在'

        # 字段名引用校验
        sf = a.get('statusField')
        if sf and sf not in known_fields:
            return f'{where}的状态字段「{sf}」不是本页面的字段'

        vw = a.get('visibleWhen') or {}
        vf = vw.get('field')
        if vf and vf not in known_fields:
            return f'{where}的显示条件字段「{vf}」不是本页面的字段'

        for m in (a.get('responseMapping') or []):
            col = (m or {}).get('column')
            if col and col not in known_fields:
                return f'{where}的响应映射目标字段「{col}」不是本页面的字段'

        for p in (a.get('paramFields') or []):
            ct = (p or {}).get('controlType')
            if ct in _FORBIDDEN_PARAM_CONTROLS:
                return f'{where}的参数表单不支持 {ct} 类型控件'

    return None
