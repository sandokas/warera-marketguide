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
