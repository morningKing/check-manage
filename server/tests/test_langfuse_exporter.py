import threading
import time
from dataclasses import replace
import logging
import re

from utils.langfuse_config import LangfuseSettings
from utils.langfuse_config import observation_id, trace_id_for
from utils.langfuse_mapping import Observation
from utils.langfuse_exporter import ExportResult, LangfuseExporter


def settings(**changes):
    base = LangfuseSettings(
        enabled=True,
        host="https://langfuse.test",
        public_key="pk-test",
        secret_key="sk-test",
        environment="test",
        capture_content=True,
        sample_rate=1.0,
        queue_size=4,
        flush_interval_seconds=0.02,
    )
    return replace(base, **changes)


def observation(identifier="obs-1", status="completed"):
    return Observation(
        kind="generation",
        id=identifier,
        parent_id=None,
        trace_id=trace_id_for("session-1", None, "anonymous"),
        session_id="session-1",
        name="generation",
        input={"prompt": "hello"},
        output="world",
        metadata={"model": "test-model"},
        status=status,
        start_time=None,
        end_time=None,
    )


class FakeObservation:
    def __init__(self):
        self.updates = []
        self.ended = False

    def update(self, **values):
        self.updates.append(values)

    def end(self):
        self.ended = True


class FakeClient:
    def __init__(self, fail_times=0, started=None):
        self.fail_times = fail_times
        self.started = started if started is not None else []
        self.flushed = 0

    def start_observation(self, **values):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("temporary client failure")
        self.started.append(values)
        return FakeObservation()

    def flush(self):
        self.flushed += 1


class LegacyFakeClient:
    def __init__(self):
        self.created = []

    def generation(self, **values):
        self.created.append(values)
        return FakeObservation()

    def flush(self):
        pass


class V3FakeClient:
    def __init__(self):
        self.started = []

    def start_observation(self, *, trace_context, name, as_type, input, output, metadata, model=None):
        self.started.append({
            "trace_context": trace_context,
            "name": name,
            "as_type": as_type,
            "input": input,
            "output": output,
            "metadata": metadata,
            "model": model,
        })
        return FakeObservation()

    def flush(self):
        pass


class BlockingFlushClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.release_flush = threading.Event()

    def flush(self):
        self.release_flush.wait(1.0)


class FailingFlushClient(FakeClient):
    def flush(self):
        raise RuntimeError("flush failed")


def wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_disabled_submit_does_not_construct_client_or_import_sdk():
    constructed = []
    exporter = LangfuseExporter(settings=settings(enabled=False), client_factory=lambda: constructed.append(1))

    assert exporter.submit([observation()]) is ExportResult.DISABLED
    exporter.start()
    exporter.stop(0.1)
    assert constructed == []


def test_concurrent_start_creates_one_worker():
    client = FakeClient()
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client)
    starters = [threading.Thread(target=exporter.start) for _ in range(8)]
    for starter in starters:
        starter.start()
    for starter in starters:
        starter.join()

    assert exporter.submit([observation("one-worker")]) is ExportResult.ACCEPTED
    assert exporter.flush(1.0)
    exporter.stop(1.0)
    assert len(client.started) == 1


def test_worker_submits_batch_and_preserves_deterministic_id():
    client = FakeClient()
    exporter = LangfuseExporter(settings=settings(queue_size=10), client_factory=lambda: client, batch_size=2)
    exporter.start()

    assert exporter.submit([observation("obs-a"), observation("obs-b")]) is ExportResult.ACCEPTED
    assert exporter.flush(1.0)
    exporter.stop(1.0)

    assert [item["metadata"]["observation_id"] for item in client.started] == [
        observation_id("generation", "obs-a"),
        observation_id("generation", "obs-b"),
    ]
    assert all(re.fullmatch(r"[0-9a-f]{32}", item["trace_context"]["trace_id"]) for item in client.started)
    assert client.flushed >= 1


def test_repeated_snapshot_with_same_observation_id_is_exported_once():
    client = FakeClient()
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client, batch_size=1)
    exporter.start()

    item = observation("stable-snapshot")
    assert exporter.submit([item]) is ExportResult.ACCEPTED
    assert exporter.flush(1.0)
    assert exporter.submit([item]) is ExportResult.DROPPED
    assert exporter.flush(1.0)
    exporter.stop(1.0)

    assert [entry["metadata"]["observation_id"] for entry in client.started] == [
        observation_id("generation", "stable-snapshot")
    ]


def test_modern_exporter_passes_application_session_id_explicitly():
    client = FakeClient()
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client, batch_size=1)
    exporter.start()
    assert exporter.submit([observation("session-explicit")]) is ExportResult.ACCEPTED
    assert exporter.flush(1.0)
    exporter.stop(1.0)

    assert client.started[0]["metadata"]["session_id"] == "session-1"


def test_v3_sdk_shape_receives_only_supported_start_observation_arguments():
    client = V3FakeClient()
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client, batch_size=1)
    exporter.start()
    assert exporter.submit([observation("v3-api")]) is ExportResult.ACCEPTED
    assert exporter.flush(1.0)
    exporter.stop(1.0)

    assert re.fullmatch(r"[0-9a-f]{32}", client.started[0]["trace_context"]["trace_id"])
    assert client.started[0]["metadata"]["session_id"] == "session-1"


def test_v3_exporter_keeps_historical_start_and_end_times_in_metadata():
    from datetime import datetime, timezone

    client = V3FakeClient()
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client, batch_size=1)
    exporter.start()
    stamped = replace(
        observation("timed-obs"),
        start_time=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        end_time=datetime(2026, 1, 2, 3, 4, 6, tzinfo=timezone.utc),
    )
    assert exporter.submit([stamped]) is ExportResult.ACCEPTED
    assert exporter.flush(1.0)
    exporter.stop(1.0)

    metadata = client.started[0]["metadata"]
    assert metadata["start_time"] == "2026-01-02T03:04:05+00:00"
    assert metadata["end_time"] == "2026-01-02T03:04:06+00:00"


def test_capture_content_false_sends_no_raw_observation_content():
    client = V3FakeClient()
    exporter = LangfuseExporter(
        settings=settings(capture_content=False),
        client_factory=lambda: client,
        batch_size=1,
    )
    exporter.start()
    tool = replace(
        observation("private-tool"),
        kind="span",
        input={"command": "private command"},
        output={"result": "private result"},
    )
    assert exporter.submit([observation("private-content"), tool]) is ExportResult.ACCEPTED
    assert exporter.flush(1.0)
    exporter.stop(1.0)

    payload = client.started[0]
    for payload in client.started:
        assert payload["input"] is None
        assert payload["output"] is None
        assert payload["metadata"]["input_length"] > 0
        assert payload["metadata"]["output_length"] > 0
        assert "hello" not in repr(payload)
        assert "world" not in repr(payload)
        assert "private command" not in repr(payload)
        assert "private result" not in repr(payload)


def test_sampling_decision_is_shared_by_all_observations_in_a_trace():
    client = V3FakeClient()
    trace_settings = settings(sample_rate=0.5)
    exporter = LangfuseExporter(trace_settings, client_factory=lambda: client, batch_size=2)
    exporter.start()
    items = [observation("sample-a"), observation("sample-b")]
    assert exporter.submit(items) is (
        ExportResult.ACCEPTED
        if __import__("utils.langfuse_config", fromlist=["should_sample"]).should_sample(
            trace_settings, items[0].trace_id
        )
        else ExportResult.SAMPLED
    )
    exporter.flush(1.0)
    exporter.stop(1.0)
    assert len(client.started) in {0, 2}


def test_worker_supports_v2_style_generation_client():
    client = LegacyFakeClient()
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client, batch_size=1)
    exporter.start()

    assert exporter.submit([observation("legacy")]) is ExportResult.ACCEPTED
    assert exporter.flush(1.0)
    exporter.stop(1.0)

    assert client.created[0]["id"] == observation_id("generation", "legacy")
    assert client.created[0]["trace_id"] == observation("legacy").trace_id


def test_client_exception_isolated_and_batch_is_retried():
    client = FakeClient(fail_times=1)
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client, batch_size=1)
    exporter.start()

    assert exporter.submit([observation()]) is ExportResult.ACCEPTED
    assert exporter.flush(1.0)
    exporter.stop(1.0)

    assert len(client.started) == 1


def test_terminal_client_error_is_retained_after_bounded_retries():
    client = FakeClient(fail_times=100)
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client, batch_size=1)
    exporter.start()

    assert exporter.submit([observation("terminal", "failed")]) is ExportResult.ACCEPTED
    assert not exporter.flush(1.0)
    assert exporter.stop(1.0) is False

    assert [item[0].id for item in exporter._retained] == ["terminal"]


def test_retained_overflow_is_counted_logged_and_does_not_evict_oldest(caplog):
    exporter = LangfuseExporter(settings=settings(queue_size=1))
    first = observation("retained-first", "failed")
    second = observation("retained-second", "failed")

    assert exporter._retain(first, 1, "terminal")
    with caplog.at_level(logging.ERROR, logger="utils.langfuse_exporter"):
        assert not exporter._retain(second, 1, "terminal")

    assert [item[0].id for item in exporter._retained] == ["retained-first"]
    assert exporter.retained_overflow_count == 1
    message = " ".join(record.getMessage() for record in caplog.records)
    assert "session-1" in message
    assert observation("retained-second").trace_id in message
    assert "retained-second" in message


def test_concurrent_retention_is_bounded_and_counts_every_overflow():
    exporter = LangfuseExporter(settings=settings(queue_size=2))
    observations = [observation(f"retained-{index}", "failed") for index in range(20)]
    threads = [
        threading.Thread(target=exporter._retain, args=(item, 1, "terminal"))
        for item in observations
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(exporter._retained) == 2
    assert exporter.retained_overflow_count == 18


def test_queue_saturation_is_non_blocking_and_terminal_observation_is_prioritized():
    release = threading.Event()
    client = FakeClient()

    def blocked_factory():
        release.wait(1.0)
        return client

    exporter = LangfuseExporter(settings=settings(queue_size=3), client_factory=blocked_factory, batch_size=1)
    exporter.start()
    assert exporter.submit([observation("first", "running")]) is ExportResult.ACCEPTED
    assert exporter.submit([observation("second", "running")]) is ExportResult.ACCEPTED
    assert exporter.submit([observation("terminal", "failed")]) is ExportResult.ACCEPTED
    assert exporter.submit([observation("overflow", "running")]) is ExportResult.DROPPED
    release.set()
    assert exporter.flush(1.0)
    exporter.stop(1.0)
    assert [item["metadata"]["observation_id"] for item in client.started] == [
        observation_id("generation", "terminal"),
        observation_id("generation", "first"),
        observation_id("generation", "second"),
    ]


def test_terminal_observation_admitted_when_normal_capacity_is_full():
    release = threading.Event()
    client = FakeClient()

    def blocked_factory():
        release.wait(1.0)
        return client

    exporter = LangfuseExporter(settings=settings(queue_size=2), client_factory=blocked_factory, batch_size=1)
    exporter.start()
    assert exporter.submit([observation("normal-1", "running")]) is ExportResult.ACCEPTED
    assert exporter.submit([observation("terminal", "failed")]) is ExportResult.ACCEPTED
    release.set()
    assert exporter.flush(1.0)
    exporter.stop(1.0)
    assert observation_id("generation", "terminal") in [
        item["metadata"]["observation_id"] for item in client.started
    ]


def test_retained_item_is_replayed_by_flush_after_client_recovers():
    client = FakeClient(fail_times=3)
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client, batch_size=1)
    exporter.start()
    assert exporter.submit([observation("replay")]) is ExportResult.ACCEPTED
    assert not exporter.flush(1.0)
    assert [item[0].id for item in exporter._retained] == ["replay"]

    client.fail_times = 0
    assert exporter.flush(1.0)
    exporter.stop(1.0)
    assert len(client.started) == 1
    assert not exporter._retained


def test_mid_batch_failure_does_not_duplicate_successful_observations():
    client = FakeClient()
    failed_once = {"obs-b"}

    def send(**values):
        identifier = values["metadata"]["observation_id"]
        if identifier in failed_once:
            failed_once.remove(identifier)
            raise RuntimeError("one item failed")
        client.started.append(values)
        return FakeObservation()

    client.start_observation = send
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client, batch_size=3)
    exporter.start()
    assert exporter.submit([observation("obs-a"), observation("obs-b"), observation("obs-c")]) is ExportResult.ACCEPTED
    assert exporter.flush(1.0)
    exporter.stop(1.0)
    ids = [item["metadata"]["observation_id"] for item in client.started]
    assert ids.count(observation_id("generation", "obs-a")) == 1
    assert ids.count(observation_id("generation", "obs-b")) == 1
    assert ids.count(observation_id("generation", "obs-c")) == 1


def test_batch_waits_for_time_window_before_sending_partial_batch():
    client = FakeClient()
    exporter = LangfuseExporter(
        settings=settings(flush_interval_seconds=0.1),
        client_factory=lambda: client,
        batch_size=3,
    )
    exporter.start()
    assert exporter.submit([observation("timed")]) is ExportResult.ACCEPTED
    time.sleep(0.03)
    assert not client.started
    assert exporter.flush(1.0)
    exporter.stop(1.0)
    assert len(client.started) == 1


def test_shutdown_is_bounded_when_client_flush_blocks():
    client = BlockingFlushClient()
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client)
    exporter.start()
    exporter.submit([observation()])
    started = time.monotonic()
    exporter.stop(0.05)
    assert time.monotonic() - started < 0.5
    client.release_flush.set()


def test_flush_exception_makes_flush_and_stop_fail(caplog):
    client = FailingFlushClient()
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client)
    exporter.start()
    exporter.submit([observation("flush-error")])

    with caplog.at_level(logging.ERROR, logger="utils.langfuse_exporter"):
        assert exporter.flush(1.0) is False
        assert exporter.stop(1.0) is False

    assert "Langfuse client flush failed" in " ".join(record.getMessage() for record in caplog.records)


def test_zero_timeout_flush_includes_recorded_flush_failure():
    client = FailingFlushClient()
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client)
    exporter._client = client
    assert exporter._flush_client() is False

    assert exporter.flush(0) is False


def test_no_thread_stop_includes_recorded_flush_failure():
    client = FailingFlushClient()
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client)
    exporter._client = client
    assert exporter._flush_client() is False

    assert exporter.stop(0) is False


def test_stop_reports_failure_when_retained_replay_cannot_enter_full_queue():
    client = FakeClient(fail_times=100)
    exporter = LangfuseExporter(
        settings=settings(queue_size=1),
        client_factory=lambda: client,
        batch_size=1,
    )
    exporter.start()
    assert exporter.submit([observation("retained", "failed")]) is ExportResult.ACCEPTED
    assert not exporter.flush(1.0)
    assert [item[0].id for item in exporter._retained] == ["retained"]

    exporter.submit([observation("queued", "running")])
    started = time.monotonic()
    assert exporter.stop(0.05) is False
    assert time.monotonic() - started < 0.5
    assert [item[0].id for item in exporter._retained] == ["retained"]


def test_failure_log_contains_safe_observation_diagnostics(caplog):
    client = FakeClient(fail_times=100)
    exporter = LangfuseExporter(settings=settings(), client_factory=lambda: client, batch_size=1)
    exporter.start()
    with caplog.at_level(logging.ERROR, logger="utils.langfuse_exporter"):
        exporter.submit([observation("logged", "failed")])
        assert not exporter.flush(1.0)
    exporter.stop(1.0)
    message = " ".join(record.getMessage() for record in caplog.records)
    assert "session-1" in message
    assert observation("logged").trace_id in message
    assert "logged" in message
    assert "sk-test" not in message


def test_app_lifecycle_seam_injects_fake_exporter():
    from app import start_langfuse_exporter

    class FakeExporter:
        def __init__(self):
            self.started = 0
            self.stopped = []

        def start(self):
            self.started += 1

        def stop(self, timeout):
            self.stopped.append(timeout)

    fake = FakeExporter()
    shutdown_hooks = []
    assert start_langfuse_exporter(lambda: fake, shutdown_hooks.append) is fake
    assert fake.started == 1
    shutdown_hooks[0]()
    assert fake.stopped == [2.0]
