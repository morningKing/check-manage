"""Test that the AI chat agent directive instructs emission of diagram fences."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_agent_directive_mentions_diagram_fences():
    """Verify the directive instructs mermaid and echarts fence emission."""
    from routes.ai_chat import _AGENT_DIRECTIVE
    assert 'mermaid' in _AGENT_DIRECTIVE
    assert 'echarts' in _AGENT_DIRECTIVE


def test_agent_directive_mentions_query_collection():
    from routes.ai_chat import _AGENT_DIRECTIVE
    assert 'query_collection' in _AGENT_DIRECTIVE
