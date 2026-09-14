import importlib
import re
from dataclasses import FrozenInstanceError, fields
from math import inf, nan

import pytest

from utils.langfuse_config import (
    LangfuseSettings,
    load_langfuse_settings,
    observation_id,
    redact_content,
    should_sample,
    trace_id_for,
)


def test_settings_are_frozen_and_disabled_without_credentials():
    settings = load_langfuse_settings({})

    assert not settings.enabled
    assert settings.host == "https://cloud.langfuse.com"
    assert settings.project_id == ""
    assert settings.public_key == ""
    assert settings.secret_key == ""
    assert settings.environment == "development"
    assert not settings.capture_content
    assert settings.sample_rate == 1.0
    assert settings.queue_size == 100
    assert settings.flush_interval_seconds == 5.0
    assert [field.name for field in fields(LangfuseSettings)] == [
        "enabled",
        "host",
        "public_key",
        "secret_key",
        "environment",
        "capture_content",
        "sample_rate",
        "queue_size",
        "flush_interval_seconds",
        "project_id",
    ]

    with pytest.raises(FrozenInstanceError):
        settings.enabled = True


def test_credentials_and_values_are_loaded_and_sample_rate_is_bounded():
    settings = load_langfuse_settings(
        {
            "LANGFUSE_ENABLED": "true",
            "LANGFUSE_HOST": " https://langfuse.example/ ",
            "LANGFUSE_PROJECT_ID": " project/with spaces ",
            "LANGFUSE_PUBLIC_KEY": "pk-test",
            "LANGFUSE_SECRET_KEY": "sk-test",
            "LANGFUSE_ENVIRONMENT": "test",
            "LANGFUSE_CAPTURE_CONTENT": "yes",
            "LANGFUSE_SAMPLE_RATE": "2.5",
            "LANGFUSE_QUEUE_SIZE": "250",
            "LANGFUSE_FLUSH_INTERVAL_SECONDS": "1.25",
        }
    )

    assert settings.enabled
    assert settings.host == "https://langfuse.example"
    assert settings.project_id == "project/with spaces"
    assert settings.public_key == "pk-test"
    assert settings.secret_key == "sk-test"
    assert settings.environment == "test"
    assert settings.capture_content
    assert settings.sample_rate == 1.0
    assert settings.queue_size == 250
    assert settings.flush_interval_seconds == 1.25

    assert load_langfuse_settings({"LANGFUSE_SAMPLE_RATE": "-1"}).sample_rate == 0.0


@pytest.mark.parametrize("value", ["not-a-number", "nan", "inf", "-inf"])
def test_invalid_sample_rates_are_rejected(value):
    with pytest.raises(ValueError, match="LANGFUSE_SAMPLE_RATE"):
        load_langfuse_settings({"LANGFUSE_SAMPLE_RATE": value})


def test_non_finite_numeric_sample_rates_are_rejected():
    for value in [nan, inf, -inf]:
        with pytest.raises(ValueError, match="LANGFUSE_SAMPLE_RATE"):
            load_langfuse_settings({"LANGFUSE_SAMPLE_RATE": str(value)})


def test_invalid_queue_and_flush_values_use_safe_defaults():
    settings = load_langfuse_settings(
        {
            "LANGFUSE_QUEUE_SIZE": "not-an-int",
            "LANGFUSE_FLUSH_INTERVAL_SECONDS": "not-a-float",
        }
    )
    assert settings.queue_size == 100
    assert settings.flush_interval_seconds == 5.0

    bounded = load_langfuse_settings(
        {"LANGFUSE_QUEUE_SIZE": "-5", "LANGFUSE_FLUSH_INTERVAL_SECONDS": "-1"}
    )
    assert bounded.queue_size == 1
    assert bounded.flush_interval_seconds == 0.0


def test_central_config_exposes_langfuse_settings(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-central")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-central")
    monkeypatch.setenv("LANGFUSE_SAMPLE_RATE", "0.25")

    import config

    reloaded = importlib.reload(config)
    assert reloaded.LANGFUSE_SETTINGS.enabled
    assert reloaded.LANGFUSE_SETTINGS.public_key == "pk-central"
    assert reloaded.LANGFUSE_SETTINGS.sample_rate == 0.25
    monkeypatch.undo()
    importlib.reload(config)


def test_missing_either_credential_disables_export():
    base = {"LANGFUSE_ENABLED": "true", "LANGFUSE_PUBLIC_KEY": "pk"}
    assert not load_langfuse_settings(base).enabled
    assert not load_langfuse_settings(
        {"LANGFUSE_ENABLED": "true", "LANGFUSE_SECRET_KEY": "sk"}
    ).enabled
    assert load_langfuse_settings({**base, "LANGFUSE_SECRET_KEY": "sk"}).enabled
    assert load_langfuse_settings(
        {**base, "LANGFUSE_SECRET_KEY": "sk", "LANGFUSE_ENABLED": "false"}
    ).enabled is False


def test_trace_id_format_is_canonical_for_all_session_kinds():
    trace_ids = [
        trace_id_for('session-1', None, 'turn-1'),
        trace_id_for('child-1', 'batch-1', 'child-1:user'),
        trace_id_for('session-1', None, 'continuation-1'),
    ]
    assert all(re.fullmatch(r"[0-9a-f]{32}", trace_id) for trace_id in trace_ids)
    assert trace_ids[0] == trace_id_for('session-1', None, 'turn-1')


def test_sampling_is_deterministic_and_respects_boundaries():
    always = LangfuseSettings(
        enabled=True,
        host="host",
        public_key="pk",
        secret_key="sk",
        environment="test",
        capture_content=False,
        sample_rate=1.0,
        queue_size=1,
        flush_interval_seconds=1.0,
    )
    never = LangfuseSettings(**{**always.__dict__, "sample_rate": 0.0})

    assert should_sample(always, "stable-key")
    assert not should_sample(never, "stable-key")
    assert should_sample(always, "stable-key") == should_sample(always, "stable-key")


def test_redaction_masks_sensitive_keys_recursively_and_can_be_disabled():
    value = {
        "username": "alice",
        "password": "p@ss",
        "nested": [{"access_token": "token", "safe": "value"}],
        "headers": {"Authorization": "Bearer secret", "Cookie": "sid=123"},
        "api-key": "key",
        "message": "password=hunter2; preserve=this",
        "message2": "Authorization: Bearer nested-token",
        "safe_text": "this ordinary text stays visible",
    }

    redacted = redact_content(value, True)

    assert redacted["username"] == "alice"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["nested"][0]["access_token"] == "[REDACTED]"
    assert redacted["headers"]["Authorization"] == "[REDACTED]"
    assert redacted["headers"]["Cookie"] == "[REDACTED]"
    assert redacted["api-key"] == "[REDACTED]"
    assert redacted["message"] == "password=[REDACTED]; preserve=this"
    assert redacted["message2"] == "Authorization: [REDACTED]"
    assert redacted["safe_text"] == "this ordinary text stays visible"
    assert redact_content(value, False) is value
    assert redact_content("plain text", True) == "plain text"
    assert redact_content("Bearer direct-token", True) == "Bearer [REDACTED]"
    assert redact_content(["token=abc", "ordinary text"], True) == [
        "token=[REDACTED]",
        "ordinary text",
    ]


def test_observation_ids_are_deterministic_and_kind_scoped():
    first = observation_id("batch", "application-123")
    second = observation_id("batch", "application-123")

    assert first == second
    assert first != observation_id("session", "application-123")
    assert first != observation_id("batch", "application-456")
    assert len(first) == 16
    assert all(character in "0123456789abcdef" for character in first)
