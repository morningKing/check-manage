import uuid
from datetime import datetime, timezone
from db import get_db
from data.page_config_relations import extract_target_collection
from projects.versioning import get_project_collections


# ==================== 关联关系扫描 ====================

def scan_dependency_relations(source_project: str, target_project: str) -> list[dict]:
    """
    扫描源项目与目标项目之间的关联关系

    复用 page_config_relations.py 的 extract_target_collection 逻辑

    Parameters
    ----------
    source_project : str
        源项目菜单ID
    target_project : str
        目标项目菜单ID

    Returns
    -------
    list[dict]
        关联关系列表
    """
    # 获取源项目和目标项目的所有collection
    source_collections = get_project_collections(source_project)
    target_collections = get_project_collections(target_project)
    target_collection_names = {c['collection'] for c in target_collections}

    relations = []
    with get_db() as conn:
        cur = conn.cursor()

        for coll_info in source_collections:
            collection = coll_info['collection']
            page_id = coll_info['pageId']

            # 获取page_config的fields
            cur.execute(
                'SELECT fields FROM page_configs WHERE id = %s',
                (page_id,)
            )
            row = cur.fetchone()
            if not row or not row[0]:
                continue

            fields = row[0]
            for field in fields:
                target_collection = extract_target_collection(field)

                if target_collection and target_collection in target_collection_names:
                    relations.append({
                        'source_collection': collection,
                        'source_field': field.get('fieldName'),
                        'target_collection': target_collection,
                        'control_type': field.get('controlType'),
                    })

    return relations


def save_dependency_relations(dependency_id: str, relations: list[dict]) -> list[dict]:
    """
    保存依赖涉及的关联关系

    Parameters
    ----------
    dependency_id : str
        依赖声明ID
    relations : list[dict]
        关联关系列表

    Returns
    -------
    list[dict]
        保存的关联关系记录
    """
    with get_db() as conn:
        cur = conn.cursor()

        saved_relations = []
        for rel in relations:
            rel_id = f'pdr-{uuid.uuid4().hex[:12]}'

            cur.execute(
                '''INSERT INTO project_dependency_relations
                   (id, dependency_id, source_collection, source_field, target_collection, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id, dependency_id, source_collection, source_field, target_collection, validation_status''',
                (rel_id, dependency_id, rel['source_collection'], rel['source_field'], rel['target_collection'], datetime.now(timezone.utc))
            )
            row = cur.fetchone()

            saved_relations.append({
                'id': row[0],
                'dependencyId': row[1],
                'sourceCollection': row[2],
                'sourceField': row[3],
                'targetCollection': row[4],
                'validationStatus': row[5],
                'controlType': rel.get('control_type'),
            })

        conn.commit()

        return saved_relations


def get_dependency_relations(dependency_id: str) -> list[dict]:
    """
    获取依赖声明涉及的关联关系
    """
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute(
            '''SELECT id, dependency_id, source_collection, source_field, target_collection,
                      estimated_records, validation_status, validation_detail, validated_at
               FROM project_dependency_relations
               WHERE dependency_id = %s
               ORDER BY source_collection, source_field''',
            (dependency_id,)
        )
        rows = cur.fetchall()

        relations = []
        for row in rows:
            relations.append({
                'id': row[0],
                'dependencyId': row[1],
                'sourceCollection': row[2],
                'sourceField': row[3],
                'targetCollection': row[4],
                'estimatedRecords': row[5],
                'validationStatus': row[6],
                'validationDetail': row[7],
                'validatedAt': row[8].isoformat() if row[8] else None,
            })

        return relations



__all__ = [
    'datetime',
    'timezone',
    'get_db',
    'extract_target_collection',
    'get_project_collections',
    'scan_dependency_relations',
    'save_dependency_relations',
    'get_dependency_relations',
]
