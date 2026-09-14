from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_langfuse_dependency_and_health_docs_match_v4_contract():
    requirements = (ROOT / 'server' / 'requirements.txt').read_text(encoding='utf-8')
    runbook = (ROOT / 'docs' / 'operations' / 'langfuse-self-hosting.md').read_text(encoding='utf-8')
    guide = (ROOT / 'docs' / 'user-guide' / 'ai' / 'agent-observability.md').read_text(encoding='utf-8')

    assert 'langfuse>=3.63.0,<4.0.0' in requirements
    assert 'http://localhost:3000/api/public/health' in runbook
    assert 'http://localhost:3000/api/public/ready' in runbook
    assert 'http://localhost:3030/api/health' in runbook
    assert 'http://localhost:3000/api/health' not in runbook
    assert 'langfuse>=3.63.0,<4.0.0' in guide
    assert 'Token、费用' not in guide
