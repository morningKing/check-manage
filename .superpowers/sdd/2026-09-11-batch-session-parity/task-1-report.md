# Task 1 Implementation Report

## Changed files

- `server/utils/session_prompt.py`: added `build_session_prompt()` and moved the ordinary-session directive, memory injection, attachment/@-mention expansion, export fallback, and raw stored-part construction into the shared helper.
- `server/routes/ai_chat.py`: routed `send_message()` through the shared helper while preserving message persistence, model/agent overrides, listener ordering, and recovery behavior.
- `server/tests/test_session_prompt.py`: added contract and ordinary/batch parity tests covering text and binary attachments, file mentions, memory, export fallback, and raw stored parts.
- `server/tests/test_ai_chat_directive.py`: verifies the route re-exports the unchanged shared directive.
- `server/tests/test_batch_engine.py`: verifies batch-specific input hints are applied after the shared prompt contract.
- `server/tests/test_routes_ai_chat.py`: moved export fallback patch points to the shared helper module.

## TDD evidence

- Initial focused run: `3 failed, 3 passed`; failures were `ImportError`/`ModuleNotFoundError` because `utils.session_prompt` did not exist.
- Final regression run:
  `set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_session_prompt.py tests/test_ai_chat_directive.py tests/test_routes_ai_chat.py tests/test_batch_engine.py::test_batch_uses_shared_prompt_contract_before_input_hint -v`
  Result: `96 passed, 178 warnings`.
- `git diff --check`: passed with no whitespace errors.

## Commits

- `75cb316` — `refactor: share AI session prompt preparation`
- `cea2d24` — `docs: add Task 1 implementation report`

## Concerns

- Batch worker execution was intentionally not changed in Task 1; later work will consume the shared helper. External `/v1/ai-sessions` behavior was not changed.
- The regression suite emits existing Python 3.12 `datetime.utcnow()` deprecation warnings from `auth.py`; no new warnings were introduced by this task.
