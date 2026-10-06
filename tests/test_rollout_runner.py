"""Operational locking is released by real process termination."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import time

import pytest


def runner_module():
    spec = importlib.util.spec_from_file_location('rollout_runner', Path(__file__).parents[1] / 'scripts/market_rollout.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lock_prevents_duplicate_writer_and_releases_after_termination(tmp_path):
    module = runner_module()
    lock = tmp_path / 'writer.lock'
    ready = tmp_path / 'ready'
    terminate = tmp_path / 'terminate'
    script = tmp_path / 'worker.py'
    script.write_text('''import importlib.util,sys,time,os
from pathlib import Path
spec=importlib.util.spec_from_file_location('rollout',sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with module.exclusive_job(Path(sys.argv[2])):
    Path(sys.argv[3]).write_text('ready')
    deadline=time.monotonic()+30
    while not Path(sys.argv[4]).exists() and time.monotonic()<deadline:
        time.sleep(.02)
    os._exit(19)
''')
    process = subprocess.Popen([sys.executable,str(script),module.__file__,str(lock),str(ready),str(terminate)])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        with pytest.raises(OSError):
            with module.exclusive_job(lock):
                pytest.fail('Second writer obtained lock')
    finally:
        terminate.touch()
        process.wait(timeout=5)
    assert process.returncode == 19
    with module.exclusive_job(lock):
        pass


@pytest.mark.parametrize("historical", [False, True])
def test_snapshot_cutoff_metadata_and_resume(tmp_path, monkeypatch, historical):
    import json
    from datetime import datetime, timedelta, timezone
    from warera_quant.market_store import MarketStore
    from warera_quant.market_data import resolve_publication_context, load_participant_report
    from test_report_context import C, trade, ingest

    module = runner_module()
    database = tmp_path / "source.db"
    with MarketStore(database) as store:
        ingest(store, trade("latest", C))
        store.record_market_sync(C + timedelta(days=99), status="complete")
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"streams": {stream: {"latest_scan_exhausted": True,
        "progress": {"status": "exhausted", "last_error": None, "rejected": 0}}
        for stream in ("trading", "itemMarket")}}))
    job = tmp_path / "job"
    flags = ["rollout", "--publish-only", "--exhaustion-evidence", str(evidence),
             "--market-db", str(database), "--job-dir", str(job)]
    if historical:
        flags += ["--as-of", C.isoformat()]
    monkeypatch.setattr(sys, "argv", flags)
    calls = []
    def interrupted(database, flags, *, context=None):
        calls.append(context)
        raise RuntimeError("Simulated report process termination")
    monkeypatch.setattr(module, "invoke_cli", interrupted)
    with pytest.raises(SystemExit):
        module.main()
    state = json.loads((job / "job.json").read_text())
    assert state["stage"] == "report"
    assert state["context"]["analysis_as_of"] == C.isoformat()
    assert state["context"]["boundary_mode"] == ("historical-exclusive" if historical else "database-inclusive")
    # Source progresses after the snapshot. A resumed report still uses snapshot C.
    with MarketStore(database) as store:
        ingest(store, trade("later", C + timedelta(days=10)))
    monkeypatch.setattr(module, "invoke_cli", lambda database, flags, *, context=None: calls.append(context))
    monkeypatch.setattr(module.runpy, "run_path", lambda *a, **k: None)
    module.main()
    publication = json.loads((job / "publication.json").read_text())
    with MarketStore(publication["database"]) as store:
        context = resolve_publication_context(store, publication)
        count = load_participant_report(store, context=context)["coverage"]["market_transaction_count"]
    assert count == (0 if historical else 1)
    assert calls[-1].analysis_as_of == calls[0].analysis_as_of == C
    assert calls[-1].window_end_exclusive == calls[0].window_end_exclusive
    assert publication["context"] == calls[-1].to_dict()
    assert json.loads((job / "job.json").read_text())["stage"] == "done"


def test_legacy_as_of_only_job_remains_end_exclusive(tmp_path):
    from warera_quant.market_store import MarketStore
    from warera_quant.market_data import resolve_publication_context, load_participant_report
    from test_report_context import C, trade, ingest
    with MarketStore(tmp_path / "snapshot") as store:
        ingest(store, trade("latest", C))
        context = resolve_publication_context(store, {"as_of": C.isoformat()})
        assert context.boundary_mode == "historical-exclusive"
        assert load_participant_report(store, context=context)["coverage"]["market_transaction_count"] == 0
