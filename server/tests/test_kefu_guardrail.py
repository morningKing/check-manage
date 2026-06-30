from utils.kefu_guardrail import assemble_system_prompt, GUARDRAIL


def test_guardrail_always_present():
    out = assemble_system_prompt('你是某产品的售前助手')
    assert GUARDRAIL in out
    assert '你是某产品的售前助手' in out
    # 边界声明在实例提示词之前
    assert out.index(GUARDRAIL) < out.index('你是某产品的售前助手')


def test_guardrail_with_empty_instance_prompt():
    out = assemble_system_prompt(None)
    assert GUARDRAIL in out
    assert out.strip().endswith(GUARDRAIL.strip())
