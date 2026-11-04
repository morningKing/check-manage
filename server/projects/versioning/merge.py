"""
项目版本管理核心逻辑

职责：
- 项目级快照/分支创建
- 项目分支切换（同步切换所有collection）
- 项目分支合并
- 项目版本差异对比

设计：
- 项目分支覆盖数据分支
- 项目下所有数据菜单共享同一个分支状态
- 参考 Git 的分支管理模式
"""

import uuid
from datetime import datetime, timezone
from db import get_db
import psycopg2.extras
from common.operation_log import log_operation, pick_display_name, get_page_info

from .diff import (compute_collection_diff, get_collection_fields,
                   load_project_version_data,
                   load_project_version_data_by_type)
from .repo import MAIN_BRANCH_ID, get_project_collections, get_user_project_branch


def merge_project_version(version_id, target_branch, strategy, merged_by, user_id, project_menu_id):
    """
    合并项目版本到目标分支（支持多次合并 + webhook 触发 + 回滚）

    Parameters
    ----------
    version_id : str
        源版本 ID
    target_branch : str
        目标分支，'main' 或 'current'
    strategy : str
        合并策略：'theirs'（使用源版本数据）或 'ours'（保留当前）
    merged_by : str
        合并执行者
    user_id : str
        用户 ID
    project_menu_id : str
        项目菜单 ID

    Returns
    -------
    dict
        {
            'success': True,
            'mergeId': 'merge-xxx',
            'collections': [...],
            'canMergeAgain': True
        }
    """
    now = datetime.now(timezone.utc)
    merge_id = f'merge-{uuid.uuid4().hex[:8]}'

    # === Phase 1: 计算合并预览（用于 before webhook） ===
    preview_collections = []
    preview_created = 0
    preview_updated = 0
    preview_deleted = 0

    # 获取版本信息（需要提前获取）
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT name, version_type, status FROM project_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')
        version_name, version_type, status = row

        # 获取目标分支
        if target_branch == 'current':
            target_branch_id = get_user_project_branch(user_id, project_menu_id)
        else:
            target_branch_id = MAIN_BRANCH_ID

        # 获取目标分支名称
        target_branch_name = '主分支' if target_branch_id == MAIN_BRANCH_ID else version_name
        if target_branch_id != MAIN_BRANCH_ID:
            cur.execute(
                'SELECT name FROM project_versions WHERE id = %s',
                (target_branch_id,)
            )
            tb_row = cur.fetchone()
            if tb_row:
                target_branch_name = tb_row[0]

        # 获取项目名称
        cur.execute('SELECT name FROM menus WHERE id = %s', (project_menu_id,))
        proj_row = cur.fetchone()
        project_name = proj_row[0] if proj_row else ''

        # 计算预览数据
        collections = get_project_collections(project_menu_id)
        for coll_info in collections:
            collection = coll_info['collection']
            source_records, source_rels = load_project_version_data_by_type(version_id, collection, version_type)
            target_records, target_rels = load_project_version_data(collection, target_branch_id)
            fields = get_collection_fields(collection)
            field_names = [f['fieldName'] for f in fields]
            relation_fields = [f for f in fields if f.get('controlType') == 'relation']
            diff = compute_collection_diff(
                target_records, source_records, field_names,
                target_rels, source_rels, relation_fields
            )

            preview_created += len(diff['added'])
            preview_updated += len(diff['modified'])
            preview_deleted += len(diff['removed'])
            preview_collections.append({
                'collection': collection,
                'pageName': coll_info['pageName'],
                'previewCreated': len(diff['added']),
                'previewUpdated': len(diff['modified']),
                'previewDeleted': len(diff['removed']),
            })

    # === Phase 2: Before Webhook 触发 ===
    from collab.webhook_engine import fire_webhooks
    preview_payload = {
        'event': 'merge',
        'timing': 'before',
        'mergePreview': {
            'mergeId': merge_id,
            'project': {'menuId': project_menu_id, 'name': project_name},
            'version': {'id': version_id, 'name': version_name},
            'targetBranch': {'id': target_branch_id, 'name': target_branch_name},
            'collections': preview_collections,
            'summary': {
                'previewTotal': preview_created + preview_updated + preview_deleted,
                'previewCreated': preview_created,
                'previewUpdated': preview_updated,
                'previewDeleted': preview_deleted,
            }
        }
    }

    before_result = fire_webhooks('merge', None, merge_id, None, preview_payload, merged_by, timing='before')

    if before_result['beforeBlocked']:
        return {
            'success': False,
            'error': 'Before webhook blocked the merge',
            'webhookErrors': before_result['beforeErrors'],
            'mergeId': None,
        }

    # === Phase 3: 执行合并（事务内）===
    merge_backups_list = []  # 收集备份数据

    with get_db() as conn:
        cur = conn.cursor()

        # 获取版本信息
        cur.execute(
            'SELECT name, version_type, status FROM project_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')
        version_name, version_type, status = row

        # 获取目标分支
        if target_branch == 'current':
            target_branch_id = get_user_project_branch(user_id, project_menu_id)
        else:
            target_branch_id = MAIN_BRANCH_ID

        # 获取目标分支名称
        target_branch_name = '主分支' if target_branch_id == MAIN_BRANCH_ID else version_name
        if target_branch_id != MAIN_BRANCH_ID:
            cur.execute(
                'SELECT name FROM project_versions WHERE id = %s',
                (target_branch_id,)
            )
            tb_row = cur.fetchone()
            if tb_row:
                target_branch_name = tb_row[0]

        # 创建合并记录
        cur.execute(
            'INSERT INTO merge_records '
            '(id, source_version_id, source_version_name, target_branch_id, target_branch_name, '
            'project_menu_id, strategy, merged_by, merged_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (merge_id, version_id, version_name, target_branch_id, target_branch_name,
             project_menu_id, strategy, merged_by, now)
        )

        # 记录合并操作日志
        log_operation(
            action='merge',
            target_type='project_version',
            target_id=version_id,
            target_name=version_name,
            description=f'合并版本 {version_name} 到 {target_branch_name}',
            branch_id=target_branch_id
        )

        # 获取项目下所有 Collection
        collections = get_project_collections(project_menu_id)

        merge_result = {
            'success': True,
            'mergeId': merge_id,
            'collections': [],
            'canMergeAgain': True
        }

        total_created = 0
        total_updated = 0
        total_deleted = 0
        merged_collections = set()  # 收集本次合并涉及的 collection，用于事务末尾重播种序列计数器

        for coll_info in collections:
            collection = coll_info['collection']
            merged_collections.add(collection)

            # 加载源版本数据（根据版本类型选择数据源）
            source_records, source_rels = load_project_version_data_by_type(version_id, collection, version_type)

            # 加载目标分支当前数据
            target_records, target_rels = load_project_version_data(collection, target_branch_id)

            # 获取字段配置
            fields = get_collection_fields(collection)
            field_names = [f['fieldName'] for f in fields]
            relation_fields = [f for f in fields if f.get('controlType') == 'relation']

            # 计算差异
            diff = compute_collection_diff(
                target_records, source_records, field_names,
                target_rels, source_rels, relation_fields
            )

            coll_result = {
                'collection': collection,
                'pageName': coll_info['pageName'],
                'recordsCreated': 0,
                'recordsUpdated': 0,
                'recordsDeleted': 0,
            }

            # 构建来源标记
            merged_from = {
                'mergeId': merge_id,
                'versionId': version_id,
                'versionName': version_name,
                'mergedAt': now.isoformat(),
                'mergedBy': merged_by
            }

            if strategy == 'theirs':
                # 使用源版本数据

                # 1. 删除目标数据中不在源版本中的记录（备份后删除）
                for rec in diff['removed']:
                    # 查询旧数据
                    cur.execute(
                        'SELECT data FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
                        (collection, rec['id'], target_branch_id)
                    )
                    old_data_row = cur.fetchone()
                    old_data = old_data_row[0] if old_data_row else None

                    # 查询旧关联
                    cur.execute(
                        'SELECT field_name, related_collection, related_id '
                        'FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
                        (collection, rec['id'], target_branch_id)
                    )
                    old_rels = [{'field': r[0], 'relatedColl': r[1], 'relatedId': r[2]} for r in cur.fetchall()]

                    # 备份数据（稍后批量插入）
                    merge_backups_list.append({
                        'id': f'backup-{uuid.uuid4().hex[:8]}',
                        'collection': collection,
                        'type': 'deleted',
                        'record_id': rec['id'],
                        'old_data': old_data,
                        'new_data': None,
                        'old_relations': old_rels,
                        'new_relations': None,
                    })

                    # 记录删除日志
                    display_name = pick_display_name(rec, fields) or rec['id']
                    log_operation(
                        action='delete',
                        target_type='dynamic_data',
                        target_id=rec['id'],
                        target_name=display_name,
                        description=f'合并删除：来自版本 {version_name}',
                        branch_id=target_branch_id
                    )
                    cur.execute(
                        'DELETE FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
                        (collection, rec['id'], target_branch_id),
                    )
                    cur.execute(
                        'DELETE FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
                        (collection, rec['id'], target_branch_id),
                    )
                    coll_result['recordsDeleted'] += 1

                # 2. 插入新增记录（添加来源标记 + 备份）
                for rec in diff['added']:
                    data = {k: v for k, v in rec.items() if k != 'id'}
                    data['_mergedFrom'] = merged_from

                    # 查询新关联数据（从源版本）
                    new_rels = []
                    if version_type == 'branch':
                        cur.execute(
                            'SELECT field_name, related_collection, related_id '
                            'FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
                            (collection, rec['id'], version_id)
                        )
                    else:
                        cur.execute(
                            'SELECT field_name, related_collection, related_id '
                            'FROM project_version_relations WHERE version_id = %s AND collection = %s AND record_id = %s',
                            (version_id, collection, rec['id'])
                        )
                    for r in cur.fetchall():
                        new_rels.append({'field': r[0], 'relatedColl': r[1], 'relatedId': r[2]})

                    # 备份数据
                    merge_backups_list.append({
                        'id': f'backup-{uuid.uuid4().hex[:8]}',
                        'collection': collection,
                        'type': 'created',
                        'record_id': rec['id'],
                        'old_data': None,
                        'new_data': data,
                        'old_relations': None,
                        'new_relations': new_rels,
                    })

                    # 记录创建日志
                    display_name = pick_display_name(data, fields) or rec['id']
                    log_operation(
                        action='create',
                        target_type='dynamic_data',
                        target_id=rec['id'],
                        target_name=display_name,
                        description=f'合并创建：来自版本 {version_name}',
                        branch_id=target_branch_id
                    )
                    cur.execute(
                        'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, %s)',
                        (rec['id'], collection, psycopg2.extras.Json(data), target_branch_id),
                    )
                    coll_result['recordsCreated'] += 1

                # 3. 更新修改记录（添加来源标记 + 备份）
                for item in diff['modified']:
                    # 查询旧数据
                    cur.execute(
                        'SELECT data FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
                        (collection, item['id'], target_branch_id)
                    )
                    old_data_row = cur.fetchone()
                    old_data = old_data_row[0] if old_data_row else None

                    # 查询旧关联
                    cur.execute(
                        'SELECT field_name, related_collection, related_id '
                        'FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
                        (collection, item['id'], target_branch_id)
                    )
                    old_rels = [{'field': r[0], 'relatedColl': r[1], 'relatedId': r[2]} for r in cur.fetchall()]

                    # 新数据
                    new_data = {k: v for k, v in item['record'].items() if k != 'id'}
                    new_data['_mergedFrom'] = merged_from

                    # 查询新关联（从源版本）
                    new_rels = []
                    if version_type == 'branch':
                        cur.execute(
                            'SELECT field_name, related_collection, related_id '
                            'FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
                            (collection, item['id'], version_id)
                        )
                    else:
                        cur.execute(
                            'SELECT field_name, related_collection, related_id '
                            'FROM project_version_relations WHERE version_id = %s AND collection = %s AND record_id = %s',
                            (version_id, collection, item['id'])
                        )
                    for r in cur.fetchall():
                        new_rels.append({'field': r[0], 'relatedColl': r[1], 'relatedId': r[2]})

                    # 备份数据
                    merge_backups_list.append({
                        'id': f'backup-{uuid.uuid4().hex[:8]}',
                        'collection': collection,
                        'type': 'updated',
                        'record_id': item['id'],
                        'old_data': old_data,
                        'new_data': new_data,
                        'old_relations': old_rels,
                        'new_relations': new_rels,
                    })

                    # 记录更新日志
                    display_name = pick_display_name(new_data, fields) or item['id']
                    log_operation(
                        action='update',
                        target_type='dynamic_data',
                        target_id=item['id'],
                        target_name=display_name,
                        description=f'合并更新：来自版本 {version_name}',
                        branch_id=target_branch_id
                    )
                    cur.execute(
                        'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = version + 1 '
                        'WHERE collection = %s AND id = %s AND branch_id = %s',
                        (psycopg2.extras.Json(new_data), collection, item['id'], target_branch_id),
                    )
                    coll_result['recordsUpdated'] += 1

                # 4. 恢复关联数据
                cur.execute(
                    'DELETE FROM data_relations WHERE collection = %s AND branch_id = %s',
                    (collection, target_branch_id)
                )

                # 根据版本类型选择关联数据源
                if version_type == 'branch':
                    # 分支类型：从 data_relations 加载当前关联数据
                    cur.execute(
                        'SELECT record_id, field_name, related_collection, related_id '
                        'FROM data_relations WHERE collection = %s AND branch_id = %s',
                        (collection, version_id)
                    )
                else:
                    # 快照类型：从快照表加载
                    cur.execute(
                        'SELECT record_id, field_name, related_collection, related_id '
                        'FROM project_version_relations WHERE version_id = %s AND collection = %s',
                        (version_id, collection)
                    )
                rel_rows = cur.fetchall()
                for record_id, field_name, related_coll, related_id in rel_rows:
                    cur.execute(
                        'INSERT INTO data_relations '
                        '(collection, record_id, field_name, related_collection, related_id, branch_id) '
                        'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                        (collection, record_id, field_name, related_coll, related_id, target_branch_id)
                    )

            merge_result['collections'].append(coll_result)
            total_created += coll_result['recordsCreated']
            total_updated += coll_result['recordsUpdated']
            total_deleted += coll_result['recordsDeleted']

        # 合并把源分支记录（携带其 autoSequence 编号）复制进目标分支，但目标分支计数器
        # 未被推进 —— 后续 create_item 可能重号。此处在提交前、与合并同事务重播种目标
        # 分支这些 collection 的序列计数器（GREATEST 语义，绝不回退）。
        if merged_collections:
            from data.sequences import reseed_sequences
            reseed_sequences(cur, collections=list(merged_collections), branch_id=target_branch_id)

        # 更新合并记录统计
        cur.execute(
            'UPDATE merge_records SET records_created = %s, records_updated = %s, records_deleted = %s WHERE id = %s',
            (total_created, total_updated, total_deleted, merge_id)
        )

        # 插入备份数据到 merge_backups 表
        for backup in merge_backups_list:
            cur.execute(
                'INSERT INTO merge_backups '
                '(id, merge_id, collection, backup_type, record_id, old_data, new_data, '
                'old_relations, new_relations) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (backup['id'], merge_id, backup['collection'], backup['type'],
                 backup['record_id'],
                 psycopg2.extras.Json(backup['old_data']) if backup['old_data'] else None,
                 psycopg2.extras.Json(backup['new_data']) if backup['new_data'] else None,
                 psycopg2.extras.Json(backup['old_relations']) if backup['old_relations'] else None,
                 psycopg2.extras.Json(backup['new_relations']) if backup['new_relations'] else None)
            )

        # 不再更新版本状态为 merged（保持 active，允许多次合并）
        # 仅更新 merged_at 和 merged_by 作为最后一次合并记录
        cur.execute(
            'UPDATE project_versions SET merged_at = %s, merged_by = %s WHERE id = %s',
            (now, merged_by, version_id)
        )

        # 获取项目名称已在前面获取，此处无需重复查询

    # === Phase 4: After Webhook 触发 + 回滚检查 ===
    from collab.webhook_engine import build_merge_webhook_payload
    after_payload = build_merge_webhook_payload(
        merge_id,
        merge_result,
        project_menu_id,
        project_name,
        version_id,
        version_name,
        target_branch_id,
        target_branch_name,
        merged_by,
    )

    after_result = fire_webhooks('merge', None, merge_id, None, after_payload, merged_by, timing='after')

    # 检查是否需要回滚
    if after_result['rollbackNeeded']:
        rollback_success = _rollback_merge(merge_id)

        if rollback_success:
            return {
                'success': False,
                'error': 'Merge rollback triggered by webhook failure',
                'webhookErrors': after_result['afterErrors'],
                'rollbackExecuted': True,
                'mergeId': merge_id,
            }
        else:
            # 回滚失败（罕见情况）
            return {
                'success': True,  # 操作已执行，回滚失败
                'error': 'Webhook failed but rollback also failed',
                'webhookErrors': after_result['afterErrors'],
                'rollbackFailed': True,
                'mergeId': merge_id,
            }

    # 返回 webhook 错误信息（即使成功也返回，供前端展示）
    if after_result['afterErrors']:
        merge_result['webhookErrors'] = after_result['afterErrors']

    return merge_result


def merge_project_version_detailed(version_id, target_branch, collection_decisions, merged_by, user_id, project_menu_id):
    """
    合并项目版本到目标分支（详细决策模式 - 支持按记录/字段选择）

    Parameters
    ----------
    version_id : str
        源版本 ID
    target_branch : str
        目标分支，'main' 或 'current'
    collection_decisions : list
        用户选择的决策列表，每个元素包含：
        {
            'collection': str,
            'added': [record_id, ...],
            'removed': [record_id, ...],
            'modified': [
                {
                    'recordId': str,
                    'fieldDecisions': [
                        {'fieldName': str, 'useSource': bool}
                    ]
                }
            ]
        }
    merged_by : str
        合并执行者
    user_id : str
        用户 ID
    project_menu_id : str
        项目菜单 ID

    Returns
    -------
    dict
        {
            'success': True,
            'mergeId': 'merge-xxx',
            'collections': [...],
        }
    """
    now = datetime.now(timezone.utc)
    merge_id = f'merge-{uuid.uuid4().hex[:8]}'

    with get_db() as conn:
        cur = conn.cursor()

        # 获取版本信息
        cur.execute(
            'SELECT name, version_type, status FROM project_versions WHERE id = %s',
            (version_id,)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('版本不存在')
        version_name, version_type, status = row

        # 获取目标分支
        if target_branch == 'current':
            target_branch_id = get_user_project_branch(user_id, project_menu_id)
        else:
            target_branch_id = MAIN_BRANCH_ID

        # 获取目标分支名称
        target_branch_name = '主分支' if target_branch_id == MAIN_BRANCH_ID else version_name
        if target_branch_id != MAIN_BRANCH_ID:
            cur.execute(
                'SELECT name FROM project_versions WHERE id = %s',
                (target_branch_id,)
            )
            tb_row = cur.fetchone()
            if tb_row:
                target_branch_name = tb_row[0]

        # 创建合并记录
        cur.execute(
            'INSERT INTO merge_records '
            '(id, source_version_id, source_version_name, target_branch_id, target_branch_name, '
            'project_menu_id, strategy, merged_by, merged_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (merge_id, version_id, version_name, target_branch_id, target_branch_name,
             project_menu_id, 'detailed', merged_by, now)
        )

        # 记录合并操作日志
        log_operation(
            action='merge',
            target_type='project_version',
            target_id=version_id,
            target_name=version_name,
            description=f'详细合并版本 {version_name} 到 {target_branch_name}',
            branch_id=target_branch_id
        )

        merge_result = {
            'success': True,
            'mergeId': merge_id,
            'collections': [],
        }

        total_created = 0
        total_updated = 0
        total_deleted = 0
        merged_collections = set()  # 收集本次合并涉及的 collection，用于事务末尾重播种序列计数器

        # 构建来源标记
        merged_from = {
            'mergeId': merge_id,
            'versionId': version_id,
            'versionName': version_name,
            'mergedAt': now.isoformat(),
            'mergedBy': merged_by
        }

        # 处理每个 collection 的决策
        for coll_decision in collection_decisions:
            collection = coll_decision.get('collection')
            if collection:
                merged_collections.add(collection)
            added_ids = coll_decision.get('added', [])
            removed_ids = coll_decision.get('removed', [])
            modified_decisions = coll_decision.get('modified', [])

            # 获取 pageName
            cur.execute(
                'SELECT name FROM menus WHERE page_id = %s AND menu_type = %s',
                (f'page-{collection}', 'data')
            )
            page_row = cur.fetchone()
            page_name = page_row[0] if page_row else collection

            coll_result = {
                'collection': collection,
                'pageName': page_name,
                'recordsCreated': 0,
                'recordsUpdated': 0,
                'recordsDeleted': 0,
            }

            # 加载源版本数据
            source_records, source_rels = load_project_version_data_by_type(version_id, collection, version_type)
            source_records_map = {r['id']: r for r in source_records}

            # 加载目标分支当前数据
            target_records, target_rels = load_project_version_data(collection, target_branch_id)
            target_records_map = {r['id']: r for r in target_records}

            # 获取字段配置
            fields = get_collection_fields(collection)

            # 1. 处理新增记录
            for rec_id in added_ids:
                source_rec = source_records_map.get(rec_id)
                if source_rec:
                    data = {k: v for k, v in source_rec.items() if k != 'id'}
                    data['_mergedFrom'] = merged_from
                    display_name = pick_display_name(data, fields) or rec_id
                    log_operation(
                        action='create',
                        target_type='dynamic_data',
                        target_id=rec_id,
                        target_name=display_name,
                        description=f'详细合并创建：来自版本 {version_name}',
                        branch_id=target_branch_id
                    )
                    cur.execute(
                        'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, %s)',
                        (rec_id, collection, psycopg2.extras.Json(data), target_branch_id),
                    )
                    coll_result['recordsCreated'] += 1

            # 2. 处理删除记录
            for rec_id in removed_ids:
                target_rec = target_records_map.get(rec_id)
                if target_rec:
                    display_name = pick_display_name(target_rec, fields) or rec_id
                    log_operation(
                        action='delete',
                        target_type='dynamic_data',
                        target_id=rec_id,
                        target_name=display_name,
                        description=f'详细合并删除：来自版本 {version_name}',
                        branch_id=target_branch_id
                    )
                    cur.execute(
                        'DELETE FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
                        (collection, rec_id, target_branch_id),
                    )
                    cur.execute(
                        'DELETE FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
                        (collection, rec_id, target_branch_id),
                    )
                    coll_result['recordsDeleted'] += 1

            # 3. 处理修改记录（按字段决策合并）
            for mod_decision in modified_decisions:
                rec_id = mod_decision.get('recordId')
                field_decisions = mod_decision.get('fieldDecisions', [])

                source_rec = source_records_map.get(rec_id)
                target_rec = target_records_map.get(rec_id)

                if target_rec and field_decisions:
                    # 从目标数据开始，按决策更新字段
                    merged_data = {k: v for k, v in target_rec.items() if k != 'id'}
                    merged_data['_mergedFrom'] = merged_from

                    for fd in field_decisions:
                        field_name = fd.get('fieldName')
                        use_source = fd.get('useSource', True)

                        if use_source and source_rec and field_name in source_rec:
                            merged_data[field_name] = source_rec[field_name]
                        # 如果 useSource 为 False，保留目标数据（不修改）

                    display_name = pick_display_name(merged_data, fields) or rec_id
                    log_operation(
                        action='update',
                        target_type='dynamic_data',
                        target_id=rec_id,
                        target_name=display_name,
                        description=f'详细合并更新：来自版本 {version_name}',
                        branch_id=target_branch_id
                    )
                    cur.execute(
                        'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = version + 1 '
                        'WHERE collection = %s AND id = %s AND branch_id = %s',
                        (psycopg2.extras.Json(merged_data), collection, rec_id, target_branch_id),
                    )
                    coll_result['recordsUpdated'] += 1

            merge_result['collections'].append(coll_result)
            total_created += coll_result['recordsCreated']
            total_updated += coll_result['recordsUpdated']
            total_deleted += coll_result['recordsDeleted']

        # 与上面 merge_project_version 同理：在提交前重播种目标分支这些 collection 的
        # 序列计数器，防止详细合并复制进来的 autoSequence 编号被后续 create 重号。
        if merged_collections:
            from data.sequences import reseed_sequences
            reseed_sequences(cur, collections=list(merged_collections), branch_id=target_branch_id)

        # 更新合并记录统计
        cur.execute(
            'UPDATE merge_records SET records_created = %s, records_updated = %s, records_deleted = %s WHERE id = %s',
            (total_created, total_updated, total_deleted, merge_id)
        )

        # 更新版本合并时间
        cur.execute(
            'UPDATE project_versions SET merged_at = %s, merged_by = %s WHERE id = %s',
            (now, merged_by, version_id)
        )

        # 获取项目名称
        cur.execute('SELECT name FROM menus WHERE id = %s', (project_menu_id,))
        proj_row = cur.fetchone()
        project_name = proj_row[0] if proj_row else None

    # 触发 webhook
    import uuid as uuid_module
    from collab.webhook_engine import fire_webhooks, build_merge_webhook_payload
    merge_id = merge_result.get('mergeId') or f'merge-{uuid_module.uuid4().hex[:12]}'
    webhook_payload = build_merge_webhook_payload(
        merge_id,
        merge_result,
        project_menu_id,
        project_name or '',
        version_id,
        version_name,
        target_branch_id,
        target_branch_name,
        merged_by,
    )
    fire_webhooks('merge', None, merge_id, None, webhook_payload, merged_by)

    return merge_result



# ==================== Merge Rollback Support ====================

def _rollback_merge(merge_id: str) -> bool:
    """
    回滚合并操作（从 merge_backups 恢复数据）

    Parameters
    ----------
    merge_id : str
        合并记录 ID

    Returns
    -------
    bool
        回滚是否成功
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()

            # 获取目标分支 ID
            cur.execute(
                'SELECT target_branch_id, source_version_id FROM merge_records WHERE id = %s',
                (merge_id,)
            )
            merge_row = cur.fetchone()
            if not merge_row:
                return False

            target_branch_id, source_version_id = merge_row

            # 获取备份数据
            cur.execute(
                'SELECT id, collection, backup_type, record_id, old_data, new_data, '
                'old_relations, new_relations '
                'FROM merge_backups WHERE merge_id = %s',
                (merge_id,)
            )
            backups = cur.fetchall()

            # 按类型回滚
            affected_collections = set()
            for backup in backups:
                backup_id, collection, backup_type, record_id, old_data, new_data, \
                    old_relations, new_relations = backup
                affected_collections.add(collection)

                if backup_type == 'created':
                    # 回滚创建：删除新记录
                    cur.execute(
                        'DELETE FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
                        (collection, record_id, target_branch_id)
                    )
                    cur.execute(
                        'DELETE FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
                        (collection, record_id, target_branch_id)
                    )

                elif backup_type == 'deleted':
                    # 回滚删除：恢复旧记录
                    if old_data:
                        cur.execute(
                            'INSERT INTO dynamic_data (id, collection, data, branch_id, created_at) '
                            'VALUES (%s, %s, %s, %s, NOW())',
                            (record_id, collection, psycopg2.extras.Json(old_data), target_branch_id)
                        )

                    # 恢复旧关联
                    if old_relations:
                        for rel in old_relations:
                            cur.execute(
                                'INSERT INTO data_relations '
                                '(collection, record_id, field_name, related_collection, related_id, branch_id) '
                                'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                                (collection, record_id, rel.get('field'), rel.get('relatedColl'),
                                 rel.get('relatedId'), target_branch_id)
                            )

                elif backup_type == 'updated':
                    # 回滚更新：恢复旧数据
                    if old_data:
                        cur.execute(
                            'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = version + 1 '
                            'WHERE collection = %s AND id = %s AND branch_id = %s',
                            (psycopg2.extras.Json(old_data), collection, record_id, target_branch_id)
                        )

                    # 恢复旧关联（先删除新关联，再恢复旧关联）
                    cur.execute(
                        'DELETE FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
                        (collection, record_id, target_branch_id)
                    )
                    if old_relations:
                        for rel in old_relations:
                            cur.execute(
                                'INSERT INTO data_relations '
                                '(collection, record_id, field_name, related_collection, related_id, branch_id) '
                                'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                                (collection, record_id, rel.get('field'), rel.get('relatedColl'),
                                 rel.get('relatedId'), target_branch_id)
                            )

            # 闭合 autoSequence 计数器不变式：回滚恢复（deleted/updated）的记录可能携带
            # 超过目标分支计数器的编号，重播种目标分支，避免回滚后 create_item 重号。
            if affected_collections:
                from data.sequences import reseed_sequences
                reseed_sequences(cur, collections=list(affected_collections),
                                 branch_id=target_branch_id)

            # 删除合并记录
            cur.execute('DELETE FROM merge_records WHERE id = %s', (merge_id,))

            # 删除备份数据
            cur.execute('DELETE FROM merge_backups WHERE merge_id = %s', (merge_id,))

            conn.commit()

            # 记录回滚日志
            log_operation('rollback', 'merge', merge_id, '合并回滚',
                         f'Webhook 失败触发回滚，merge_id={merge_id}')

            return True

    except Exception as e:
        log_operation('rollback_failed', 'merge', merge_id, '回滚失败', str(e))
        return False



__all__ = [
    'datetime',
    'timezone',
    'get_db',
    'log_operation',
    'pick_display_name',
    'get_page_info',
    'merge_project_version',
    'merge_project_version_detailed',
    '_rollback_merge',
]
