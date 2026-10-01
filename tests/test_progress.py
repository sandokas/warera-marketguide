from threading import Event

import pytest

from warera_quant.progress import ProgressReporter


def test_heartbeat_identifies_blocked_stage_and_stops_after_return():
    messages = []
    heartbeat = Event()

    def emit(message):
        messages.append(message)
        if "still working" in message:
            heartbeat.set()

    progress = ProgressReporter(emit, interval=0.01)
    with progress.stage("Market rows"):
        with progress.stage("Steel: transaction history"):
            assert heartbeat.wait(2), "No progress while the operation was blocked"
    assert any("Steel: transaction history: still working" in m for m in messages)
    assert messages[0] == "Market rows: started"
    assert "Market rows: completed" in messages[-1]
    assert not progress.stack


def test_failure_is_reported_and_does_not_leave_active_stage():
    messages = []
    progress = ProgressReporter(messages.append)
    with pytest.raises(ValueError, match="query failed"):
        with progress.stage("Participant rankings"):
            raise ValueError("query failed")
    assert "Participant rankings: failed" in messages[-1]
    assert not progress.stack


def test_quiet_and_verbose_modes_preserve_results():
    value = [1, 2, 3]
    quiet = ProgressReporter()
    assert quiet.call("Query", lambda: value) is value
    assert not quiet.timings
    messages = []
    verbose = ProgressReporter(messages.append, verbose=True)
    assert verbose.call("Query", lambda: value) is value
    verbose.summary()
    assert "Query: 3 results" in messages
    assert messages[-1].startswith("Report preparation timings:")
