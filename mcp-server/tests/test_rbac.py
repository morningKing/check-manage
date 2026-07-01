from rbac import is_readonly, is_public_kefu, tool_allowed, KEFU_TOOL_ALLOWLIST


def test_is_readonly():
    assert is_readonly("guest") and is_readonly("kefu-guest")
    assert not is_readonly("developer") and not is_readonly("admin")


def test_is_public_kefu():
    assert is_public_kefu("kefu-guest")
    assert not is_public_kefu("guest") and not is_public_kefu("admin")


def test_tool_allowed_public_kefu_restricted():
    assert tool_allowed("query_collection", "kefu-guest")
    assert tool_allowed("read_upload", "kefu-guest")
    assert tool_allowed("list_collections", "kefu-guest")
    assert not tool_allowed("run_python", "kefu-guest")
    assert not tool_allowed("save_artifact", "kefu-guest")
    assert not tool_allowed("read_data_file", "kefu-guest")
    assert not tool_allowed("memory_add", "kefu-guest")


def test_tool_allowed_others_unrestricted():
    for role in ("developer", "admin", "guest"):
        assert tool_allowed("run_python", role)
