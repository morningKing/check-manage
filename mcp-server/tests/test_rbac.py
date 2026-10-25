# -*- coding: utf-8 -*-
"""rbac 矩阵语义：行命中/行缺失默认/DB失败分层回落/TTL 缓存。"""
import pytest

import rbac


@pytest.fixture(autouse=True)
def _fresh_cache():
    rbac._reset_cache()
    yield
    rbac._reset_cache()


def test_legacy_predicates():
    assert rbac.is_readonly("guest") and rbac.is_readonly("kefu-guest")
    assert not rbac.is_readonly("developer")
    assert rbac.is_public_kefu("kefu-guest") and not rbac.is_public_kefu("guest")


def test_matrix_row_hit(monkeypatch):
    monkeypatch.setattr(rbac, "_fetch_rules", lambda: {
        ("developer", "run_python"): False,
        ("kefu-guest", "query_collection"): True,
    })
    assert rbac.tool_allowed("run_python", "developer") is False
    assert rbac.tool_allowed("query_collection", "kefu-guest") is True
    # 未播种行 → 代码默认
    assert rbac.tool_allowed("run_python", "admin") is True


def test_missing_row_defaults(monkeypatch):
    monkeypatch.setattr(rbac, "_fetch_rules", lambda: {})
    assert rbac.tool_allowed("run_python", "admin") is True
    assert rbac.tool_allowed("run_python", "developer") is True
    assert rbac.tool_allowed("run_python", "guest") is False        # spec §8 Q1 收紧
    assert rbac.tool_allowed("anything", "kefu-guest") is False
    assert rbac.tool_allowed("whatever", "custom-role") is False    # spec §8 Q2


def test_db_failure_layered_fallback(monkeypatch):
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(rbac, "_fetch_rules", _boom)
    assert rbac.tool_allowed("query_collection", "kefu-guest") is True   # 白名单兜底
    assert rbac.tool_allowed("run_python", "kefu-guest") is False
    assert rbac.tool_allowed("query_collection", "guest") is True        # manifest read 工具
    assert rbac.tool_allowed("run_python", "guest") is False             # write 工具拒绝
    assert rbac.tool_allowed("run_python", "admin") is True              # fail-open
    assert rbac.tool_allowed("run_python", "custom-role") is True        # fail-open


def test_ttl_cache_reuse(monkeypatch):
    calls = {"n": 0}

    def _counting():
        calls["n"] += 1
        return {}
    monkeypatch.setattr(rbac, "_fetch_rules", _counting)
    rbac.tool_allowed("run_python", "admin")
    rbac.tool_allowed("run_python", "admin")
    assert calls["n"] == 1                # TTL 内不重读
