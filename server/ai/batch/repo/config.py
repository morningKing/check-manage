from psycopg2.extras import Json

from db import get_db

from .queries import get_batch_detail


MAX_FILES_PER_BATCH = 50


def get_max_files_per_batch() -> int:
    """批任务子会话个数上限（可配置）。

    每个输入文件对应一个子会话，上限存于 ai_settings.max_batch_sessions，
    管理员在 AI 配置页调整。读取失败、未配置或非法值回落到默认
    MAX_FILES_PER_BATCH；最小 1。
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT max_batch_sessions FROM ai_settings WHERE id = 1')
            row = cur.fetchone()
        val = int(row[0]) if row and row[0] is not None else MAX_FILES_PER_BATCH
    except Exception:
        return MAX_FILES_PER_BATCH
    return max(1, val)


MAX_SUBAGENT_REUSE = 10


def validate_subagent_reuse(value) -> list:
    """校验批级子代理会话复用名单：agent 名字符串数组（去重、剥空白）。

    空/None 合法（= 不启用）。接受逗号分隔字符串容错。非法抛 ValueError
    （路由回 400）。"""
    if value in (None, []):
        return []
    if isinstance(value, str):
        value = value.split(',')
    if not isinstance(value, list):
        raise ValueError('subagent_reuse 必须是 agent 名数组')
    names = []
    for v in value:
        if not isinstance(v, str) or not v.strip():
            raise ValueError('subagent_reuse 的每项必须是非空 agent 名')
        name = v.strip()
        if len(name) > 200:
            raise ValueError('subagent_reuse 的 agent 名过长（>200）')
        if name not in names:
            names.append(name)
    if len(names) > MAX_SUBAGENT_REUSE:
        raise ValueError(f'subagent_reuse 最多 {MAX_SUBAGENT_REUSE} 个 agent')
    return names


MAX_BATCH_CONCURRENCY = 100


def validate_max_concurrency(value) -> int | None:
    """校验批级并发上限（新建/编辑对话框入口，2026-10-27）：落
    ai_execution_budgets(scope_type='batch').max_concurrency，调度 claim
    前现读（concurrency_blocked_batches）。None/缺省 = 不限（跟随全局并发
    AI_BATCH_CONCURRENCY）。bool 是 int 子类，先排除（True→1 静默放行太隐晦）。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError('max_concurrency 必须是 1-100 的整数')
    if not 1 <= value <= MAX_BATCH_CONCURRENCY:
        raise ValueError(f'max_concurrency 必须是 1-{MAX_BATCH_CONCURRENCY} 的整数')
    return value


MAX_BATCH_MODELS = 10


def validate_models(value) -> list | None:
    """校验批多模型列表（多模型分配，2026-10-27）：模型名字符串数组，去空/
    去重保序；空/None 合法（= 单模型批，走既有 model 字段）。≥2 个才轮询
    分配进子会话 model 列。非法抛 ValueError（路由回 400）。"""
    if value in (None, []):
        return None
    if not isinstance(value, list):
        raise ValueError('models 必须是模型名数组')
    names = []
    for v in value:
        if not isinstance(v, str) or not v.strip():
            raise ValueError('models 的每项必须是非空模型名')
        name = v.strip()
        if len(name) > 200:
            raise ValueError('models 的模型名过长（>200）')
        if name not in names:
            names.append(name)
    if len(names) > MAX_BATCH_MODELS:
        raise ValueError(f'models 最多 {MAX_BATCH_MODELS} 个模型')
    return names


def assign_child_models(cur, batch_id: str, models: list | None,
                        seqs: list[int]) -> None:
    """多模型轮询分配（写后调用方自行 commit）：对指定 batch_seq 的子会话
    把 models[seq % len(models)] 写入 ai_chat_sessions.model。models 有效
    长度 <2 时反向清 NULL——派发按「子会话 model 优先，批级回退」，NULL 即
    跟随批级。编辑改模型列表时只重写 pending 子会话（seqs 由调用方圈定），
    运行中/已完成子会话的历史归属不变。"""
    if models and len(models) >= 2:
        for seq in seqs:
            cur.execute(
                "UPDATE ai_chat_sessions SET model = %s "
                "WHERE batch_id = %s AND batch_seq = %s",
                (models[seq % len(models)], batch_id, seq))
    else:
        for seq in seqs:
            cur.execute(
                "UPDATE ai_chat_sessions SET model = NULL "
                "WHERE batch_id = %s AND batch_seq = %s", (batch_id, seq))


_UNSET = object()


def update_batch_config(user_id: str, batch_id: str, *,
                        agent: str | None, model: str | None,
                        provision_repo: str | None = None,
                        provision_ref: str | None = None,
                        action_checks= _UNSET,
                        gate_policy_id= _UNSET,
                        gate_retry= _UNSET,
                        subagent_reuse= _UNSET,
                        name= _UNSET,
                        prompt= _UNSET,
                        models= _UNSET,
                        api_key_id: str | None = None,
                        callback_url: str | None = None,
                        callback_secret: str | None = None) -> dict | None:
    """Update a batch's agent/model/provision repo/callback (owner-only). This
    is a full-replace, not a partial patch: every column here is set to
    whatever was passed, so an omitted field (default None) is cleared to the
    default — callers must resend a field to keep it. Returns updated detail,
    or None if not found / not owned. Takes effect on the next run the worker
    claims (retry / reexecute / pending), since the worker reads these fresh
    per run via _fetch_batch_context.

    `api_key_id` non-None additionally scopes the update to that source key.

    `name`/`prompt`（编辑对话框，2026-09-29）为 _UNSET 哨兵语义：显式传入才
    更新，未传保持原值；prompt 对未执行子任务生效（worker 派发实时读）。

    `gate_policy_id`（门禁策略引用，spec §8.2）同哨兵语义：显式传入才更新，
    `None` 合法 = 解绑；引用不存在/已停用抛 ValueError。
    """
    # 归属预检：action_checks/gate_retry 的子更新与主更新同口径——api_key_id
    # 非 None 时必须命中该密钥名下的批次，否则什么也不写（防跨密钥越权改门禁）。
    scope_sql = "SELECT id FROM ai_chat_batches WHERE id = %s AND user_id = %s"
    scope_params = [batch_id, user_id]
    if api_key_id is not None:
        scope_sql += " AND api_key_id = %s"
        scope_params.append(api_key_id)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(scope_sql, tuple(scope_params))
            if not cur.fetchone():
                return None
    if action_checks is not _UNSET:
        # 动作门禁期望(编辑入口,设计 §5.2 入口 A):显式传入才更新,
        # 未传保持原值——既有调用方(旧 UI/开放 API)不受影响。
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_batches SET action_checks = %s "
                    "WHERE id = %s AND user_id = %s",
                    (Json(action_checks) if action_checks else None,
                     batch_id, user_id),
                )
            conn.commit()
    if gate_policy_id is not _UNSET:
        # 门禁策略引用（spec §8.2）：显式传入才更新（哨兵语义同 action_checks，
        # 既有调用方不受影响）；None 合法 = 解绑。换策略/解绑后路由层会用
        # 合并集同步期望——见 routes/ai_chat_batches.update_config。
        if gate_policy_id:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT enabled FROM gate_policies WHERE id = %s",
                                (gate_policy_id,))
                    prow = cur.fetchone()
            if prow is None:
                raise ValueError('引用的门禁策略不存在')
            if not prow[0]:
                raise ValueError('引用的门禁策略已停用')
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_batches SET gate_policy_id = %s "
                    "WHERE id = %s AND user_id = %s",
                    (gate_policy_id, batch_id, user_id),
                )
            conn.commit()
    if gate_retry is not _UNSET:
        # 修正开关(设计 §5.4):批级覆盖;None=清除覆盖,跟随全局环境变量
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_batches SET gate_retry = %s "
                    "WHERE id = %s AND user_id = %s",
                    (gate_retry, batch_id, user_id),
                )
            conn.commit()
    if subagent_reuse is not _UNSET:
        # 子代理会话复用名单:显式传入才更新,未传保持原值(与 action_checks 同语义)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_batches SET subagent_reuse = %s "
                    "WHERE id = %s AND user_id = %s",
                    (Json(subagent_reuse) if subagent_reuse else None,
                     batch_id, user_id),
                )
            conn.commit()
    if name is not _UNSET or prompt is not _UNSET:
        # 名称/提示词(编辑对话框,2026-09-29):显式传入才更新。提示词对
        # 「待运行 / 重试 / 重新执行」的子任务生效——worker 派发时经
        # _fetch_batch_context 实时读 batch.prompt;运行中/已完成的子任务
        # 不受影响。name/prompt 不允许清空(校验在路由层,这里再兜底跳过空值)。
        sets, params = [], []
        if name is not _UNSET and (name or '').strip():
            sets.append("name = %s")
            params.append(name.strip())
        if prompt is not _UNSET and (prompt or '').strip():
            sets.append("prompt = %s")
            params.append(prompt.strip())
        if sets:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE ai_chat_batches SET {', '.join(sets)} "
                        "WHERE id = %s AND user_id = %s",
                        (*params, batch_id, user_id),
                    )
                conn.commit()

    sql = ("UPDATE ai_chat_batches SET agent = %s, model = %s, "
           "  provision_repo = %s, provision_ref = %s, "
           "  callback_url = %s, callback_secret = %s "
           "WHERE id = %s AND user_id = %s")
    params = [agent, model, provision_repo, provision_ref,
             callback_url, callback_secret, batch_id, user_id]
    if api_key_id is not None:
        sql += " AND api_key_id = %s"
        params.append(api_key_id)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            updated = cur.rowcount > 0
            # 多模型列表（2026-10-27）：哨兵语义，显式传入才更新。批行写
            # models 全量 + model=首模型（上面主 UPDATE 的 model 来自调用方，
            # 放在其后覆盖保证两者一致）。pending 子会话按 batch_seq 轮询
            # 重分配到新列表；运行中/已完成的子会话历史归属不变；<2 个反向
            # 清 NULL（派发回退批级 model）。
            if updated and models is not _UNSET:
                cur.execute(
                    "UPDATE ai_chat_batches SET models = %s WHERE id = %s",
                    (Json(models) if models else None, batch_id))
                # model 列只在列表非空时对齐首模型；models 清空时保留主
                # UPDATE 写入的 model（回到单模型语义由调用方的 model 定）
                if models:
                    cur.execute(
                        "UPDATE ai_chat_batches SET model = %s WHERE id = %s",
                        (models[0], batch_id))
                if models and len(models) >= 2:
                    cur.execute(
                        "SELECT batch_seq FROM ai_chat_sessions "
                        "WHERE batch_id = %s AND status = 'pending' ORDER BY batch_seq",
                        (batch_id,))
                    assign_child_models(cur, batch_id, models,
                                        [r[0] for r in cur.fetchall()])
                else:
                    cur.execute(
                        "UPDATE ai_chat_sessions SET model = NULL "
                        "WHERE batch_id = %s AND status = 'pending'",
                        (batch_id,))
        conn.commit()
    if not updated:
        return None
    return get_batch_detail(user_id, batch_id)



__all__ = [
    'MAX_BATCH_CONCURRENCY',
    'MAX_BATCH_MODELS',
    'MAX_FILES_PER_BATCH',
    'MAX_SUBAGENT_REUSE',
    '_UNSET',
    'assign_child_models',
    'get_batch_detail',
    'get_db',
    'get_max_files_per_batch',
    'Json',
    'update_batch_config',
    'validate_max_concurrency',
    'validate_models',
    'validate_subagent_reuse',
]
