# -*- coding: utf-8 -*-
"""工具元数据 manifest——分组/读写属性的唯一真相源（spec §5.1）。

每个工具模块在 import 时调用 register_meta 自注册；tools/__init__ 构建完
_TOOLS 后调用 validate_registry 校验「工具集 == META 集」，缺标注/多余标注
都拒绝启动。分组与读写属性同时服务于：
- 管理展示（快照表 → GET /ai/mcp-tools）
- rbac 的 DB 失败分层回落（guest → 仅 read 工具，spec §5.2）
"""
from dataclasses import dataclass

VALID_ACCESS = ('read', 'write')


@dataclass(frozen=True)
class GroupDef:
    id: str
    name: str          # 中文显示名
    order: int         # 组排序（快照表 group_order 来源）


GROUPS = {
    'data-query': GroupDef('data-query', '数据查询', 1),
    'data-write': GroupDef('data-write', '数据写入', 2),
    'memory':     GroupDef('memory', '长期记忆', 3),
    'trace':      GroupDef('trace', '轨迹与会话分析', 4),
    'batch':      GroupDef('batch', '批任务观测', 5),
    'graph':      GroupDef('graph', '图谱查询', 6),
    'exec':       GroupDef('exec', '通用执行与产物', 7),
}

# tool_name -> (group_id, access)
_META: dict = {}


def register_meta(name: str, *, group: str, access: str) -> None:
    if group not in GROUPS:
        raise RuntimeError(
            f'tool {name!r} declares unknown group {group!r}; known: {sorted(GROUPS)}')
    if access not in VALID_ACCESS:
        raise RuntimeError(f'tool {name!r} declares bad access {access!r}; expected read|write')
    if name in _META:
        raise RuntimeError(f'tool {name!r} registered meta twice')
    _META[name] = (group, access)


def meta_of(name: str):
    return _META.get(name)


def validate_registry(tool_names: set) -> None:
    missing = tool_names - set(_META)
    extra = set(_META) - tool_names
    if missing or extra:
        raise RuntimeError(
            f'tool/meta registry mismatch — missing META for {sorted(missing)}, '
            f'meta without tool {sorted(extra)}')
