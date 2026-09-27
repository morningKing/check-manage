"""page_configs.row_actions 的保存时校验。

前端配错（绑了不存在的规则、引用了不存在的字段）在保存时就要拦下来，
否则用户点按钮才报错，排查成本高得多。
"""

# 这些控件要落库才有语义，放在「点击时弹的参数表单」里没有意义
_FORBIDDEN_PARAM_CONTROLS = ('relation', 'reference', 'quoteSelect', 'autoSequence')

# 这些控件在 dynamic_data 里存的不是可比较/可回写的标量值——multiSelect/
# checkbox/file/image 存的是数组，relation/reference/quoteSelect 干脆不落在
# 这行的 data 里（走 data_relations 表）。行动作的状态字段/显示条件字段/响应
# 映射目标字段都要求"能读出一个标量、能整体覆盖写回一个标量"，用这些控件
# 配置出来，条件求值器会把它们当 ''（见 utils/row_action_condition.py），
# 写回则会把数组/关联字段静默改写成一个字符串，是不折不扣的脏数据。
_NON_SCALAR_CONTROLS = ('multiSelect', 'checkbox', 'file', 'image',
                        'relation', 'reference', 'quoteSelect')


def validate_row_actions(row_actions, fields, cur, collection=None):
    """校验行动作配置。通过返回 None，否则返回中文错误信息。

    Args:
        row_actions: 配置数组
        fields: 该页面的字段配置数组（用于校验字段名引用）
        cur: 数据库游标（用于校验绑定的执行器是否存在）
        collection: 本页面的集合名（用于校验绑定的 AI 扫描任务是否作用于
            同一个集合——见 I6）。传 None 时跳过这条校验（兼容旧调用点/测试）。
    """
    if row_actions is None:
        return None
    if not isinstance(row_actions, list):
        return '行操作配置必须是数组'

    known_fields = {
        f.get('fieldName') for f in (fields or []) if isinstance(f, dict)
    }
    field_control = {
        f.get('fieldName'): f.get('controlType')
        for f in (fields or []) if isinstance(f, dict)
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
            cur.execute('SELECT trigger_event FROM webhook_rules WHERE id = %s', (rule_id,))
            rule_row = cur.fetchone()
            if not rule_row:
                return f'{where}绑定的 Webhook 规则「{rule_id}」不存在'
            # I6：只有 triggerEvent='manual' 的规则才该被行操作按钮点名调用。
            # 前端下拉只是过滤显示，不挡 API 直接绑定；一条 create/update 事件
            # 规则被绑上按钮后，会既被增删改自动触发、又被按钮手动触发。
            if rule_row[0] != 'manual':
                return (f'{where}绑定的 Webhook 规则「{rule_id}」触发事件不是「手动」'
                        f'（当前是「{rule_row[0]}」），不能被行操作按钮调用')
        else:
            task_id = a.get('scanTaskId')
            if not task_id:
                return f'{where}未选择 AI 扫描任务'
            cur.execute('SELECT collection FROM ai_scan_tasks WHERE id = %s', (task_id,))
            task_row = cur.fetchone()
            if not task_row:
                return f'{where}绑定的 AI 扫描任务「{task_id}」不存在'
            # I6：扫描任务作用的 collection 必须和本页面一致，否则点击时
            # claim_one 按本页面的 record_id 去查任务绑定的另一个 collection，
            # 永远查不到，用户只会看到一句无法定位真正原因的「记录不存在」。
            if collection is not None and task_row[0] != collection:
                return (f'{where}绑定的 AI 扫描任务「{task_id}」作用于集合'
                        f'「{task_row[0]}」，与本页面的集合「{collection}」不一致')

        # 字段名引用校验
        sf = a.get('statusField')
        if sf:
            if sf not in known_fields:
                return f'{where}的状态字段「{sf}」不是本页面的字段'
            if field_control.get(sf) in _NON_SCALAR_CONTROLS:
                return (f'{where}的状态字段「{sf}」是 {field_control.get(sf)} 类型控件，'
                        '其值不是可回写的标量，不能用作状态字段')
            # C1：状态字段配了却缺执行中值/成功值/失败值中任一项，行动作引擎的
            # None -> '' 兜底会把该字段静默清成空串（管理员本意可能只是想
            # "成功就别动这个字段"，但引擎无法区分"留空"和"清空"的意图）。
            for key, cn in (('runningValue', '执行中值'), ('doneValue', '成功值'),
                           ('failedValue', '失败值')):
                val = a.get(key)
                if not (val and str(val).strip()):
                    return (f'{where}配置了状态字段「{sf}」，必须同时填写{cn}，'
                            '否则未配置的值会把该字段清空为空串')

        vw = a.get('visibleWhen') or {}
        vf = vw.get('field')
        if vf:
            if vf not in known_fields:
                return f'{where}的显示条件字段「{vf}」不是本页面的字段'
            if field_control.get(vf) in _NON_SCALAR_CONTROLS:
                return (f'{where}的显示条件字段「{vf}」是 {field_control.get(vf)} 类型控件，'
                        '其值不是可比较的标量，不能用于显示条件')

        for m in (a.get('responseMapping') or []):
            col = (m or {}).get('column')
            if col:
                if col not in known_fields:
                    return f'{where}的响应映射目标字段「{col}」不是本页面的字段'
                if field_control.get(col) in _NON_SCALAR_CONTROLS:
                    return (f'{where}的响应映射目标字段「{col}」是 {field_control.get(col)} '
                            '类型控件，不能作为响应映射目标')

        for p in (a.get('paramFields') or []):
            ct = (p or {}).get('controlType')
            if ct in _FORBIDDEN_PARAM_CONTROLS:
                return f'{where}的参数表单不支持 {ct} 类型控件'

    return None
