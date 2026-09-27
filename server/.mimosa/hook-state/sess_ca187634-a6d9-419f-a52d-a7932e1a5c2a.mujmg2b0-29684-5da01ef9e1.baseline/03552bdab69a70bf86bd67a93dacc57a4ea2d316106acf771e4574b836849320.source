import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

FIXTURE = Path(__file__).parent / 'fixtures' / 'row_action_conditions.json'
CASES = json.loads(FIXTURE.read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', CASES, ids=[c['name'] for c in CASES])
def test_evaluate_matches_fixture(case):
    from utils.row_action_condition import evaluate
    assert evaluate(case['condition'], case['data']) is case['expected']


def test_empty_dict_condition_passes():
    from utils.row_action_condition import evaluate
    assert evaluate({}, {'status': 'x'}) is True
