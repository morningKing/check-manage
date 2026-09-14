"""Bounded, asynchronous export of mapped observations to Langfuse."""

from __future__ import annotations

import atexit
from dataclasses import asdict
import json
import logging
import queue
import threading
import time
from collections import deque
from enum import Enum
from typing import Any, Callable, Iterable

from utils.langfuse_config import (
    LangfuseSettings,
    is_valid_trace_id,
    observation_id,
    redact_content,
    should_sample,
    trace_id_for,
)
from utils.langfuse_mapping import Observation


logger = logging.getLogger(__name__)


class ExportResult(str, Enum):
    ACCEPTED = "accepted"
    SAMPLED = "sampled"
    DISABLED = "disabled"
    DROPPED = "dropped"


_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "error",
    "cancelled",
    "canceled",
    "timeout",
    "timed_out",
    "aborted",
}
_MAX_RETRIES = 3
_BATCH_SIZE = 50


class LangfuseExporter:
    def __init__(
        self,
        settings: LangfuseSettings,
        client_factory: Callable[[], Any] | None = None,
        batch_size: int = _BATCH_SIZE,
    ):
        self.settings = settings
        self._client_factory = client_factory or self._make_client
        self._batch_size = max(1, batch_size)
        self._queue: queue.PriorityQueue[tuple[int, int, Observation, int, str]] = queue.PriorityQueue(
            maxsize=settings.queue_size
        )
        self._normal_slots = threading.BoundedSemaphore(max(0, settings.queue_size - 1))
        self._terminal_slots = threading.BoundedSemaphore(1)
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._client: Any = None
        self._client_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._last_flush_ok = True
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._retained: deque[tuple[Observation, int, str]] = deque(maxlen=settings.queue_size)
        self._retained_lock = threading.Lock()
        self._retained_overflow_count = 0
        self._dedup_lock = threading.Lock()
        self._sent_observations: dict[str, str] = {}
        self._queued_observations: set[tuple[str, str]] = set()

    @property
    def retained_overflow_count(self) -> int:
        with self._retained_lock:
            return self._retained_overflow_count

    @property
    def retained_count(self) -> int:
        with self._retained_lock:
            return len(self._retained)

    @property
    def last_flush_ok(self) -> bool:
        with self._flush_lock:
            return self._last_flush_ok

    def start(self) -> None:
        if not self.settings.enabled:
            return
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="langfuse-exporter",
                daemon=True,
            )
            self._thread.start()

    def submit(self, observations: Iterable[Observation]) -> ExportResult:
        if not self.settings.enabled:
            return ExportResult.DISABLED

        selected = []
        for observation in observations:
            if should_sample(self.settings, self._trace_id(observation)):
                selected.append(observation)
        if not selected:
            return ExportResult.SAMPLED

        accepted = 0
        for observation in sorted(
            selected,
            key=lambda item: 0 if item.status in _TERMINAL_STATUSES else 1,
        ):
            fingerprint = self._fingerprint(observation)
            key = (observation.id, fingerprint)
            with self._dedup_lock:
                if (self._sent_observations.get(observation.id) == fingerprint
                        or key in self._queued_observations):
                    continue
                self._queued_observations.add(key)
            slot_kind = self._acquire_slot(observation)
            if slot_kind is None:
                self._forget_queued(observation)
                continue
            with self._sequence_lock:
                sequence = self._sequence
                self._sequence += 1
            priority = 0 if observation.status in _TERMINAL_STATUSES else 1
            try:
                self._queue.put_nowait((priority, sequence, observation, 0, slot_kind))
            except queue.Full:
                self._release_slot(slot_kind)
                self._forget_queued(observation)
                continue
            accepted += 1
        if accepted == 0:
            return ExportResult.DROPPED
        self._wake.set()
        return ExportResult.ACCEPTED

    def flush(self, timeout_seconds: float) -> bool:
        if not self.settings.enabled:
            return True
        self._replay_retained()
        self.start()
        self._wake.set()
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                flush_ok = self._flush_client()
                return flush_ok and self._queue.unfinished_tasks == 0 and self.retained_count == 0 and self.retained_overflow_count == 0
            time.sleep(0.005)
        return self._queue.unfinished_tasks == 0 and self.retained_count == 0 and self.retained_overflow_count == 0 and self.last_flush_ok

    def stop(self, timeout_seconds: float) -> bool:
        if not self.settings.enabled:
            return True
        self._replay_retained()
        self._stopping.set()
        self._wake.set()
        thread = self._thread
        if thread is None:
            return self.retained_count == 0 and self.retained_overflow_count == 0 and self._queue.unfinished_tasks == 0 and self.last_flush_ok
        thread.join(timeout=max(0.0, timeout_seconds))
        return (
            not thread.is_alive()
            and self._queue.unfinished_tasks == 0
            and self.retained_count == 0
            and self.retained_overflow_count == 0
            and self.last_flush_ok
        )

    def _make_client(self) -> Any:
        # Keep the SDK import lazy so disabled deployments do not need it loaded.
        from langfuse import Langfuse

        return Langfuse(
            public_key=self.settings.public_key,
            secret_key=self.settings.secret_key,
            host=self.settings.host,
            environment=self.settings.environment,
            flush_at=self._batch_size,
        )

    def _get_client(self) -> Any:
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = self._client_factory()
        return self._client

    def _run(self) -> None:
        while True:
            if self._stopping.is_set() and self._queue.unfinished_tasks == 0:
                self._flush_client()
                return
            batch = self._take_batch()
            if batch:
                self._export_batch(batch)

    def _take_batch(self) -> list[tuple[Observation, int, int, str]]:
        try:
            _, sequence, observation, attempt, slot_kind = self._queue.get(
                timeout=max(0.01, self.settings.flush_interval_seconds)
            )
        except queue.Empty:
            self._wake.clear()
            return []

        batch = [(observation, sequence, attempt, slot_kind)]
        deadline = time.monotonic() + max(0.0, self.settings.flush_interval_seconds)
        while len(batch) < self._batch_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                _, sequence, observation, attempt, slot_kind = self._queue.get(timeout=remaining)
            except queue.Empty:
                break
            batch.append((observation, sequence, attempt, slot_kind))
        return batch

    def _export_batch(self, batch: list[tuple[Observation, int, int, str]]) -> None:
        client = None
        for observation, sequence, attempt, slot_kind in batch:
            try:
                if client is None:
                    client = self._get_client()
                self._send_observation(client, observation)
            except Exception:
                logger.exception(
                    "Langfuse export failed session_id=%s trace_id=%s observation_id=%s",
                    observation.session_id,
                    observation.trace_id,
                    observation.id,
                )
                if attempt + 1 < _MAX_RETRIES:
                    requeued = self._requeue(observation, sequence, attempt + 1, slot_kind)
                else:
                    requeued = False
                    self._retain(observation, attempt + 1, slot_kind)
                    self._forget_queued(observation)
                self._queue.task_done()
                if not requeued:
                    self._release_slot(slot_kind)
                continue
            self._queue.task_done()
            self._release_slot(slot_kind)
            self._mark_sent(observation)
        self._flush_client()

    def _requeue(self, observation: Observation, sequence: int, attempt: int, slot_kind: str) -> bool:
        # A short delay prevents a terminal SDK failure from consuming a CPU core.
        time.sleep(min(0.01 * attempt, 0.05))
        try:
            self._queue.put_nowait((
                0 if observation.status in _TERMINAL_STATUSES else 1,
                sequence,
                observation,
                attempt,
                slot_kind,
            ))
            return True
        except queue.Full:
            self._retain(observation, attempt, slot_kind)
            self._release_slot(slot_kind)
            self._forget_queued(observation)
            return False

    @staticmethod
    def _fingerprint(observation: Observation) -> str:
        return json.dumps(asdict(observation), sort_keys=True, default=str,
                          separators=(",", ":"))

    def _forget_queued(self, observation: Observation) -> None:
        with self._dedup_lock:
            self._queued_observations.discard(
                (observation.id, self._fingerprint(observation)))

    def _mark_sent(self, observation: Observation) -> None:
        with self._dedup_lock:
            fingerprint = self._fingerprint(observation)
            self._queued_observations.discard((observation.id, fingerprint))
            self._sent_observations[observation.id] = fingerprint

    def _acquire_slot(self, observation: Observation) -> str | None:
        terminal = observation.status in _TERMINAL_STATUSES
        if terminal and self._terminal_slots.acquire(blocking=False):
            return "terminal"
        if self._normal_slots.acquire(blocking=False):
            return "normal"
        return None

    def _release_slot(self, slot_kind: str) -> None:
        (self._terminal_slots if slot_kind == "terminal" else self._normal_slots).release()

    def _retain(self, observation: Observation, attempt: int, slot_kind: str) -> bool:
        with self._retained_lock:
            if len(self._retained) >= self.settings.queue_size:
                self._retained_overflow_count += 1
                logger.error(
                    "Langfuse retained buffer overflow session_id=%s trace_id=%s observation_id=%s",
                    observation.session_id,
                    observation.trace_id,
                    observation.id,
                )
                return False
            self._retained.append((observation, attempt, slot_kind))
            return True

    def _replay_retained(self) -> None:
        with self._retained_lock:
            retained = list(self._retained)
            self._retained.clear()
            for observation, attempt, slot_kind in retained:
                acquired = self._acquire_slot(observation)
                if acquired is None:
                    self._retain_locked(observation, attempt, slot_kind)
                    continue
                with self._sequence_lock:
                    sequence = self._sequence
                    self._sequence += 1
                key = (observation.id, self._fingerprint(observation))
                with self._dedup_lock:
                    self._queued_observations.add(key)
                try:
                    self._queue.put_nowait((
                        0 if observation.status in _TERMINAL_STATUSES else 1,
                        sequence,
                        observation,
                        0,
                        acquired,
                    ))
                except queue.Full:
                    self._release_slot(acquired)
                    self._forget_queued(observation)
                    self._retain_locked(observation, attempt, slot_kind)

    def _retain_locked(self, observation: Observation, attempt: int, slot_kind: str) -> bool:
        if len(self._retained) >= self.settings.queue_size:
            self._retained_overflow_count += 1
            logger.error(
                "Langfuse retained buffer overflow session_id=%s trace_id=%s observation_id=%s",
                observation.session_id,
                observation.trace_id,
                observation.id,
            )
            return False
        self._retained.append((observation, attempt, slot_kind))
        return True

    def _send_observation(self, client: Any, observation: Observation) -> None:
        trace_id = self._trace_id(observation)
        sdk_observation_id = observation_id(observation.kind, observation.id)
        metadata = {
            "observation_id": sdk_observation_id,
            "session_id": observation.session_id,
            "environment": self.settings.environment,
        }
        if self.settings.capture_content:
            metadata.update(redact_content(observation.metadata, True))
            input_value = redact_content(observation.input, True)
            output_value = redact_content(observation.output, True)
        else:
            metadata.update(self._safe_metadata(observation.metadata))
            metadata.update({
                "content_captured": False,
                "input_length": self._content_length(observation.input),
                "output_length": self._content_length(observation.output),
            })
            input_value = None
            output_value = None
        if observation.start_time:
            metadata["start_time"] = observation.start_time.isoformat()
        if observation.end_time:
            metadata["end_time"] = observation.end_time.isoformat()
        values = {
            "name": observation.name,
            "as_type": "generation" if observation.kind == "generation" else "span",
            "trace_context": {
                "trace_id": trace_id,
                **({"parent_span_id": observation.parent_id} if observation.parent_id else {}),
            },
            "input": input_value,
            "output": output_value,
            "metadata": metadata,
        }
        if observation.kind == "generation":
            model = observation.metadata.get("model")
            if model:
                values["model"] = model
        start_observation = getattr(client, "start_observation", None)
        if callable(start_observation):
            started = start_observation(**values)
        else:
            legacy_method = getattr(
                client,
                "generation" if observation.kind == "generation" else "span",
            )
            started = legacy_method(
                id=sdk_observation_id,
                trace_id=trace_id,
                parent_observation_id=observation.parent_id,
                name=observation.name,
                input=values["input"],
                output=values["output"],
                metadata=values["metadata"],
                session_id=observation.session_id,
                model=values.get("model"),
                environment=self.settings.environment,
                start_time=observation.start_time,
                end_time=observation.end_time,
            )
        if hasattr(started, "update"):
            started.update(
                output=values["output"],
                level="ERROR" if observation.status in {"failed", "error"} else None,
            )
        if hasattr(started, "end"):
            started.end()

    @staticmethod
    def _content_length(value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, str):
            return len(value)
        return len(json.dumps(value, ensure_ascii=True, default=str, separators=(",", ":")))

    @staticmethod
    def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        allowed = {"message_id", "part_id", "tool", "task_id", "parent_session_id", "model", "agent", "batch_id"}
        return {
            key: value
            for key, value in metadata.items()
            if key in allowed and isinstance(value, (str, int, float, bool))
        }

    @staticmethod
    def _trace_id(observation: Observation) -> str:
        if is_valid_trace_id(observation.trace_id):
            return observation.trace_id
        return trace_id_for(
            observation.session_id or "unknown",
            observation.metadata.get("batch_id"),
            observation.metadata.get("turn_id"),
        )

    def _flush_client(self) -> bool:
        with self._flush_lock:
            client = self._client
            if client is None:
                self._last_flush_ok = True
                return True
            try:
                flush = getattr(client, "flush", None)
                if callable(flush):
                    flush()
                self._last_flush_ok = True
                return True
            except Exception:
                self._last_flush_ok = False
                logger.exception("Langfuse client flush failed")
                return False


_EXPORTER: LangfuseExporter | None = None
_EXPORTER_LOCK = threading.Lock()


def get_langfuse_exporter() -> LangfuseExporter:
    global _EXPORTER
    if _EXPORTER is None:
        with _EXPORTER_LOCK:
            if _EXPORTER is None:
                from config import LANGFUSE_SETTINGS

                _EXPORTER = LangfuseExporter(LANGFUSE_SETTINGS)
    return _EXPORTER


def _stop_at_exit() -> None:
    exporter = _EXPORTER
    if exporter is not None:
        exporter.stop(2.0)


atexit.register(_stop_at_exit)
