import logging
import os
import shutil
import threading
import time
import traceback
from pathlib import Path

from db import get_db
from projects.workspace import (create_session_workspace, _rm_force,
                             batch_workspace_root, legacy_batch_workspace_root,
                             resolve_batch_data_path)

logger = logging.getLogger(__name__)

def _workspace_root() -> str:
    # Unified with the chat/global-skills root (config.AI_WORKSPACE_ROOT);
    # AI_CHAT_WORKSPACE_ROOT still wins when explicitly set. Kept as a function
    # because tests monkeypatch it.
    return batch_workspace_root()


def _prepare_workspace(user_id: str, session_id: str,
                       staged_file_path) -> str:
    """Create the per-session workspace and copy the staged input(s) into uploads/.

    `staged_file_path` accepts either a single string (existing callers:
    batch children pass one file path, scan-task children pass one context
    directory path) or a list of path strings (standalone /v1/ai-sessions
    children with 2+ files in `body.files` — see open_api_ai_sessions.py).
    A single string is internally treated as a one-element list, so existing
    callers' behavior is unchanged byte-for-byte; the list form just repeats
    the same per-path copy for each entry, letting one session pull in files
    staged across multiple separate /uploads calls (no requirement that they
    share a parent staging directory).

    Returns the absolute workspace path.  Side effect beyond the filesystem:
    generates and persists the child's session token (MCP 鉴权需要) — no other
    DB writes. Can be monkeypatched in tests:
        monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: str(tmp_path))

    Raises FileNotFoundError if a staged input is gone (e.g. the staging dir
    was swept after its 24h TTL, or the batch row outlived its files). Silently
    producing an EMPTY uploads/ would be worse: the prompt still tells the agent
    to read uploads/<name> (see _with_input_hint), so the child would "succeed"
    with garbage output. Raising here makes _run_one mark the child failed.

    `staged_file_path` empty/falsy (standalone /v1/ai-sessions children with no
    attachments at all — see open_api_ai_sessions.py) short-circuits to a bare
    empty uploads/. Without this, `Path(root) / ''` evaluates to `root` itself,
    which exists and is a dir, so the code below would recursively copy the
    ENTIRE workspace root (every user's every session) into this session's own
    uploads/ — a real bug, not a graceful no-file case.
    """
    # P2-B4：配额检查（AI_WORKSPACE_QUOTA_MB 仅显式设置时生效，默认 0 不启用；
    # 在后台 worker 线程内执行，不在请求路径上）。超限抛 PermissionError →
    # _run_one 的 except Exception 落 failed，配额自然阻断新派发。
    from config import AI_WORKSPACE_QUOTA_MB
    if AI_WORKSPACE_QUOTA_MB > 0:
        user_ws_dir = os.path.join(_workspace_root(), user_id)
        if os.path.isdir(user_ws_dir):
            try:
                total_mb = sum(
                    os.path.getsize(os.path.join(dp, f))
                    for dp, _, fns in os.walk(user_ws_dir) for f in fns
                ) / (1024 * 1024)
            except OSError:
                total_mb = 0.0  # 遍历竞态（并发清理）fail-open，不阻断派发
            if total_mb > AI_WORKSPACE_QUOTA_MB:
                raise PermissionError(
                    f'工作区配额超限：用户 {user_id} 已占用 '
                    f'{total_mb:.0f}MB > {AI_WORKSPACE_QUOTA_MB}MB')
    ws = create_session_workspace(_workspace_root(), user_id, session_id)
    # 平台 MCP 接线（2026-10-07 缺口修复）：交互会话在 create_session 写
    # workspace opencode.json（per-session token 的 MCP url + model），批子
    # 会话自 2026-09-29 工作区配置重构（去 .opencode 目录）起没有任何 MCP
    # 段——平台 MCP 工具（query_collection/run_python/save_artifact…）在
    # 批子会话整体不可用，模型调用被 OC 记 invalid（当日 R4 实测），账本里
    # 批会话的 check-manage_* 调用自 9-29 后为零。先写 MCP/model，再由
    # ensure_batch_permissions 幂等合并 permission 键（不动 MCP 段）。
    from config import (AI_SESSION_TTL_HOURS, MCP_SERVER_URL,
                        get_default_chat_model)
    from data.mcp_servers import enabled_mcp_config, internal_mcp_enabled, \
        INTERNAL_MCP_NAME as MCP_NAME
    from ai.sessions.session_token import generate_token
    from projects.workspace import write_opencode_config
    _mcp_name = MCP_NAME
    write_opencode_config(
        ws, mcp_name=_mcp_name,
        mcp_url=(f'{MCP_SERVER_URL}/mcp?token='
                 f'{generate_token(session_id, AI_SESSION_TTL_HOURS)}'),
        model=get_default_chat_model(),
        extra_mcp=enabled_mcp_config(reserved_names=[_mcp_name]),
        include_internal=internal_mcp_enabled())
    # 绑定批任务的外部目录读取（2026-09-30）：无人值守会话读工作区外的文件
    # 会触发 OpenCode external_directory=ask 授权询问，无人应答 → 工具挂起
    # （生产实测 read 挂 900s 被看门狗 abort）。派发时写项目级 allow——
    # 项目配置覆盖全局 deny，只对批任务/无人值守子会话生效。
    from projects.workspace import ensure_batch_permissions
    ensure_batch_permissions(ws)
    if not staged_file_path:
        return ws
    paths = staged_file_path if isinstance(staged_file_path, list) else [staged_file_path]
    up = Path(ws) / 'uploads'
    up.mkdir(parents=True, exist_ok=True)
    for rel in paths:
        # Staged paths are stored relative to the batch root. Sessions staged
        # before the root unification still point into the legacy tree, so
        # resolve across both — no data migration needed.
        src = Path(resolve_batch_data_path(
            rel, roots=(_workspace_root(), legacy_batch_workspace_root())))
        # On Windows, copying a just-created staging dir can intermittently raise
        # PermissionError (antivirus / handle-settling contention). Retry a few times.
        last_err = None
        for _attempt in range(3):
            try:
                if src.is_dir():
                    # scan-task context directory: copy its whole contents into uploads/
                    shutil.copytree(str(src), str(up), dirs_exist_ok=True)
                else:
                    dst = up / Path(rel).name
                    shutil.copy2(str(src), str(dst))
                last_err = None
                break
            except (PermissionError, OSError) as e:
                last_err = e
                time.sleep(0.3)
        if last_err is not None:
            raise last_err
    return ws


def _recompute_batch_status(batch_id: str, anchor_sid: str | None = None) -> None:
    """Set ai_chat_batches.status based on its done/failed/total counts.

    Called from pause/reconcile paths — always within a committed state.
    Exported at module level so tests can call it directly.

    paused 子任务不是终态：只要还有 paused 子任务、且未全部终态，批次整体
    显示为 'paused'（暂停可以 resume 回 running，不需要动 done/failed 计数）。

    anchor_sid：本次重算由哪个子会话转移触发（效果账本锚定用）。缺省时
    兜底取批内子会话（created_at DESC, id DESC —— 同事务插入的 created_at
    可并列，id 作 tiebreaker，10 号 §3.2）。
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT done, failed, total, callback_url, callback_secret, "
                "       (SELECT count(*) FROM ai_chat_sessions s "
                "         WHERE s.batch_id = ai_chat_batches.id "
                "           AND s.status = 'paused') AS paused "
                "FROM ai_chat_batches WHERE id = %s",
                (batch_id,),
            )
            row = cur.fetchone()
            if not row:
                return
            done, failed, total, callback_url, callback_secret, paused = row
            terminal = done + failed
            if terminal == 0 and paused == 0:
                new_status = 'pending'
            elif terminal < total and paused > 0:
                new_status = 'paused'
            elif terminal < total:
                new_status = 'running'
            elif failed == total:
                new_status = 'failed'
            elif done == total:
                new_status = 'completed'
            else:
                new_status = 'partial'
            cur.execute(
                "UPDATE ai_chat_batches "
                "SET status = %s, "
                "    completed_at = CASE WHEN %s = total THEN now() ELSE NULL END "
                "WHERE id = %s",
                (new_status, terminal, batch_id),
            )
            # P1 M4：终态时同一事务内入队 outbox（outbox 关闭时回退直发，
            # 见 _notify_callback 内的判断）
            if new_status in ('completed', 'partial', 'failed'):
                if callback_url:
                    from ai.batch import batch_events as batch_events
                    from ai.batch import delivery_outbox as delivery_outbox
                    eid = batch_events.append_event(
                        batch_id, 'batch.status', aggregate_type='batch',
                        aggregate_id=batch_id,
                        payload={'status': new_status, 'done': done,
                                 'failed': failed, 'total': total},
                        conn=conn)
                    if delivery_outbox.outbox_enabled():
                        delivery_outbox.enqueue(
                            batch_id, target_url=callback_url,
                            payload={'event': 'ai_batch_completed',
                                     'batchId': batch_id, 'status': new_status,
                                     'total': total, 'done': done, 'failed': failed},
                            secret=callback_secret or '', event_id=eid, conn=conn)
                        # H7 回修（10 号 §3.2）：effect 锚定本次转移触发的
                        # 子会话（anchor_sid）；仅在此兜底路径无 sid 时退化为
                        # 确定性 tiebreaker 查询。与 outbox 入队同事务登记。
                        if anchor_sid:
                            anchor_sid_use = anchor_sid
                        else:
                            cur.execute(
                                "SELECT id FROM ai_chat_sessions "
                                "WHERE batch_id = %s "
                                "ORDER BY created_at DESC, id DESC LIMIT 1",
                                (batch_id,))
                            arow = cur.fetchone()
                            anchor_sid_use = arow[0] if arow else None
                        if anchor_sid_use:
                            from ai.batch import execution_effect as _eff
                            _eff.record_effect(anchor_sid_use, 'callback',
                                               f'outbox:{eid}', batch_id=batch_id,
                                               request=eid, conn=conn)
        conn.commit()
    if new_status in ('completed', 'partial', 'failed') and callback_url:
        from ai.batch.delivery_outbox import outbox_enabled
        if not outbox_enabled():
            _notify_callback(batch_id, new_status, callback_url, callback_secret,
                             done, failed, total)
    if new_status in ('completed', 'partial', 'failed'):
        _cleanup_batch_oc_sessions(batch_id)


def _cleanup_batch_oc_sessions(batch_id: str) -> None:
    """批整体终态后清理其子会话的 OpenCode 会话（LRU 防淘汰，2026-09-30）。

    OC 会话列表有 100 条的 LRU 保留上限；平台积累的已终态子会话（e2e、
    历史批次）会把**运行中**的会话挤出池——后续 get_messages 404，触发
    代价高昂的「会话失效自愈」（新建+注入有损摘要）。批收敛后子会话对话
    已全部落库（查看对话读平台库，不依赖 OC），OC 侧会话即无用，删除腾位。
    env AI_BATCH_KEEP_OC_SESSIONS=1 可保留（依赖 continue 完整上下文、
    不接受自愈有损摘要的部署可关闭清理；关闭后需自行保障 OC 会话配额）。
    best-effort：单会话删除失败（已淘汰/不可达）跳过，不影响任何状态。"""
    import os
    if os.getenv('AI_BATCH_KEEP_OC_SESSIONS', '') == '1':
        return
    try:
        from ai.opencode.opencode_client import OpenCodeClient
        from config import OPENCODE_BASE_URL
        client = OpenCodeClient(OPENCODE_BASE_URL)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, opencode_session_id FROM ai_chat_sessions "
                    "WHERE batch_id = %s AND opencode_session_id IS NOT NULL",
                    (batch_id,))
                rows = cur.fetchall()
        if not rows:
            return
        removed = 0
        for _sid, oc in rows:
            try:
                client.delete_session(oc)
                removed += 1
            except Exception:
                pass  # 已被 LRU 淘汰 / OC 不可达——best-effort
        logger.info('batch terminal cleanup: removed %d/%d oc sessions batch=%s',
                    removed, len(rows), batch_id)
    except Exception:  # noqa: BLE001 —— 清理失败不影响任何批任务状态
        logger.exception('batch oc session cleanup failed batch=%s', batch_id)


def _notify_callback(batch_id, status, callback_url, callback_secret,
                     done, failed, total):
    """Best-effort open-API completion callback: POST to `callback_url` (if
    set) once the batch reaches a terminal status, HMAC-signed via the
    existing webhook engine. Fires on every terminal transition, not just the
    first — retry-failed/append/continue can push a terminal batch back to
    running and it may reach a terminal status again later; each is a real
    "batch done" event worth notifying about.

    Dispatched on a daemon thread: _fire_single_webhook can block for
    timeout * (retries + 1) (~120s worst case) and batch_engine's worker pool
    only has 3 concurrent slots, so a synchronous call here would stall other
    queued batch children whenever the callback endpoint is slow/down.
    """
    if not callback_url:
        return
    # P1 M4：outbox 启用时回调已入队由投递器负责，直发会造成重复投递
    try:
        from ai.batch.delivery_outbox import outbox_enabled
        if outbox_enabled():
            return
    except Exception:
        pass
    def _fire():
        try:
            from utils.webhook_engine import _fire_single_webhook
            _fire_single_webhook(
                rule_id=f'batch-{batch_id}', rule_name='AI批任务完成回调',
                webhook_url=callback_url, secret=callback_secret or '',
                event_type='ai_batch_completed',
                payload={'event': 'ai_batch_completed', 'batchId': batch_id,
                        'status': status, 'total': total, 'done': done, 'failed': failed},
                timeout=30, retries=3,
            )
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_fire, daemon=True).start()



__all__ = [
    '_workspace_root',
    '_prepare_workspace',
    '_recompute_batch_status',
    '_cleanup_batch_oc_sessions',
    '_notify_callback',
    'logger',
    'get_db',
    'Path',
    'create_session_workspace',
    '_rm_force',
    'batch_workspace_root',
    'legacy_batch_workspace_root',
    'resolve_batch_data_path',
]
