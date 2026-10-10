# -*- coding: utf-8 -*-
"""manifest 被动归档接线护栏（SkillOpt 任务拟合页全空第二因）。

ai_skill_def_versions 的被动兜底归档（register_definition_versions，spec §4.3）
专为**无 fit.steps 的定义**登记版本——左栏定义清单（definition-summary =
def_versions ∪ fit_results 并集）靠它在首次任务后出现条目，「生成步骤」入口
才可达。封装入口是 execution_audit.collect_and_save_workspace_manifests
（scan + save + register 三合一）；若启动路径绕开它直接
save_manifests(scan_workspace_manifests(...))，归档静默失效——页面恒空且
UI 生成入口死锁（2026-10-10 生产发现，b3e1256 引入时四个调用点未换装）。

护栏：四条 attempt 启动路径（交互会话/批子会话/客服/轨迹分析）必须经
collect_and_save_workspace_manifests，且不得再直接调 scan_workspace_manifests
（扫描只允许发生在封装内，防止换装后又退回旧形态）。
"""
import os

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# attempt 启动时扫描 workspace manifests 的四个调用方（相对 server/）
_ENTRY_FILES = (
    os.path.join('routes', 'ai_chat.py'),
    os.path.join('routes', 'kefu_public.py'),
    os.path.join('routes', 'ai_session_admin.py'),
    os.path.join('utils', 'batch_engine.py'),
)


def test_attempt_entry_paths_use_passive_archive():
    missing, direct_scan = [], []
    for rel in _ENTRY_FILES:
        path = os.path.join(_SERVER_DIR, rel)
        with open(path, encoding='utf-8') as f:
            src = f.read()
        if 'collect_and_save_workspace_manifests' not in src:
            missing.append(rel)
        if 'scan_workspace_manifests' in src:
            direct_scan.append(rel)
    assert not missing, (
        f'以下 attempt 启动路径未走 collect_and_save_workspace_manifests，'
        f'def_versions 被动归档静默失效（SkillOpt 左栏恒空、生成步骤入口'
        f'死锁）：{missing}。请把 save_manifests(scan_workspace_manifests(ws)) '
        f'换装为 collect_and_save_workspace_manifests(attempt_id, ws)。')
    assert not direct_scan, (
        f'以下文件绕过封装直接调 scan_workspace_manifests（归档只在 '
        f'collect_and_save_workspace_manifests 内做，绕过即漏登记）：'
        f'{direct_scan}。')
