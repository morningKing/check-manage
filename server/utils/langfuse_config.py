"""Configuration and privacy helpers for optional Langfuse export."""

from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping


_REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:pass(?:word|wd)?|token|secret|api[-_ ]?key|authorization|cookie)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(\b(?:pass(?:word|wd)?|token|secret|api[-_ ]?key|authorization|cookie)\b\s*[:=]\s*)"
    r"(Bearer\s+\S+|[^\s,;&]+)",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"\bBearer\s+\S+", re.IGNORECASE)
_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_OBSERVATION_ID = re.compile(r"^[0-9a-f]{16}$")


@dataclass(frozen=True)
class LangfuseSettings:
    enabled: bool
    host: str
    public_key: str
    secret_key: str
    environment: str
    capture_content: bool
    sample_rate: float
    queue_size: int
    flush_interval_seconds: float
    project_id: str = ""


def _env(environ: Mapping[str, str], name: str, default: str = "") -> str:
    return str(environ.get(name, default)).strip()


def _as_bool(value: str, default: bool = False) -> bool:
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _as_float(value: str, default: float) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _as_int(value: str, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def load_langfuse_settings(environ: Mapping[str, str] | None = None) -> LangfuseSettings:
    source = os.environ if environ is None else environ
    public_key = _env(source, "LANGFUSE_PUBLIC_KEY")
    secret_key = _env(source, "LANGFUSE_SECRET_KEY")
    raw_sample_rate = _env(source, "LANGFUSE_SAMPLE_RATE")
    if raw_sample_rate:
        try:
            sample_rate = float(raw_sample_rate)
        except ValueError as exc:
            raise ValueError("LANGFUSE_SAMPLE_RATE must be a finite number") from exc
        if not math.isfinite(sample_rate):
            raise ValueError("LANGFUSE_SAMPLE_RATE must be a finite number")
        # Values outside the probability interval are safely clamped.
        sample_rate = min(1.0, max(0.0, sample_rate))
    else:
        sample_rate = 1.0
    enabled = _as_bool(_env(source, "LANGFUSE_ENABLED")) and bool(public_key and secret_key)

    return LangfuseSettings(
        enabled=enabled,
        host=_env(source, "LANGFUSE_HOST", "https://cloud.langfuse.com").rstrip("/"),
        public_key=public_key,
        secret_key=secret_key,
        environment=_env(source, "LANGFUSE_ENVIRONMENT", "development") or "development",
        capture_content=_as_bool(_env(source, "LANGFUSE_CAPTURE_CONTENT")),
        sample_rate=sample_rate,
        queue_size=_as_int(_env(source, "LANGFUSE_QUEUE_SIZE"), 100),
        flush_interval_seconds=max(
            0.0, _as_float(_env(source, "LANGFUSE_FLUSH_INTERVAL_SECONDS"), 5.0)
        ),
        project_id=_env(source, "LANGFUSE_PROJECT_ID"),
    )


def should_sample(settings: LangfuseSettings, stable_key: str) -> bool:
    """Return a stable sampling decision for an application identifier."""
    if settings.sample_rate <= 0.0:
        return False
    if settings.sample_rate >= 1.0:
        return True
    digest = hashlib.sha256(stable_key.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return bucket < settings.sample_rate


def _is_sensitive_key(key: object) -> bool:
    return _SENSITIVE_KEY.search(str(key)) is not None


def _redact_string(value: str) -> str:
    redacted = _SENSITIVE_VALUE.sub(r"\1[REDACTED]", value)
    return _BEARER_VALUE.sub("Bearer [REDACTED]", redacted)


def redact_content(value: Any, enabled: bool) -> Any:
    """Return content with credential-like mapping values masked when enabled."""
    if not enabled:
        return value
    if isinstance(value, Mapping):
        return {
            key: _REDACTED if _is_sensitive_key(key) else redact_content(item, True)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_content(item, True) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_content(item, True) for item in value)
    if isinstance(value, str):
        return _redact_string(value)
    return value


def observation_id(kind: str, application_id: str) -> str:
    """Create a deterministic 64-bit hexadecimal W3C span identifier."""
    return hashlib.sha256(f"{kind}:{application_id}".encode("utf-8")).hexdigest()[:16]


def trace_id_for(session_id: str, batch_id: str | None, turn_id: str | None) -> str:
    """Return the canonical trace ID shared by export and user-facing links."""
    effective_turn_id = turn_id or ("initial" if batch_id else "anonymous")
    if batch_id:
        identity = f"batch:{batch_id}:session:{session_id}:turn:{effective_turn_id}"
    else:
        identity = f"{session_id}:turn:{effective_turn_id}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def is_valid_trace_id(value: object) -> bool:
    return isinstance(value, str) and _TRACE_ID.fullmatch(value) is not None


def is_valid_observation_id(value: object) -> bool:
    return isinstance(value, str) and _OBSERVATION_ID.fullmatch(value) is not None
