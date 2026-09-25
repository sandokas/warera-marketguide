"""Unattended, restartable rollout orchestration; all DB/API logic stays in the app.

Run again with the same arguments/job directory after a hard process termination.
The OS file lock releases on termination; SQLite owns page/cursor atomicity.
"""
import argparse
from collections import Counter
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import sys

from warera_quant import cli
from warera_quant.api_client import WarEraApiClient
from warera_quant.market_store import MarketStore


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')
    temporary.replace(path)


@contextmanager
def exclusive_job(path):
    """An OS lock, not a stale PID-file decision; covers all jobs for this DB."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def invoke_cli(database, flags):
    previous = sys.argv
    try:
        sys.argv = ['warera-marketguide', *flags, '--market-db', str(database)]
        cli.main()
    finally:
        sys.argv = previous


def verified_exhaustion(status):
    return all(status['streams'][stream]['latest_scan_exhausted']
        and status['streams'][stream]['progress']['status'] in ('complete', 'exhausted')
        and not status['streams'][stream]['progress']['last_error']
        and not status['streams'][stream]['progress']['rejected']
        for stream in ('trading', 'itemMarket'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--market-db', default='data/warera_market.sqlite3')
    parser.add_argument('--job-dir', default='output/market-rollout')
    parser.add_argument('--publish-only', action='store_true')
    parser.add_argument('--exhaustion-evidence', type=Path)
    parser.add_argument('--as-of', help='Optional aware ISO report cutoff, frozen in job state')
    args = parser.parse_args()
    database = Path(args.market_db).resolve()
    if not database.exists():
        parser.error('Existing migrated database required')
    if args.publish_only and args.exhaustion_evidence is None:
        parser.error('--publish-only requires --exhaustion-evidence')
    if args.as_of and datetime.fromisoformat(args.as_of.replace('Z', '+00:00')).tzinfo is None:
        parser.error('--as-of requires a timezone')
    job = Path(args.job_dir).resolve()
    job.mkdir(parents=True, exist_ok=True)
    with exclusive_job(database.with_name(database.name + '.rollout.lock')):
        state_path = job / 'job.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {
            'database':str(database), 'stage':'snapshot' if args.publish_only else 'full-import',
            'as_of':args.as_of, 'started':datetime.now(timezone.utc).isoformat()}
        if state['database'] != str(database):
            parser.error('Job belongs to a different database')
        if state['stage'] == 'done':
            print('Already complete; inspect job.json and report. Use a new job directory for a new rollout.')
            return
        if args.publish_only:
            evidence = json.loads(args.exhaustion_evidence.read_text(encoding='utf-8-sig'))
            if not verified_exhaustion(evidence):
                parser.error('Evidence does not show successful exhaustion of both streams')
            atomic_json(job / 'full-status.json', evidence)
        state.update(pid=os.getpid(), status='running', error=None)
        def save_state():
            state['updated'] = datetime.now(timezone.utc).isoformat()
            atomic_json(state_path, state)
        save_state()
        transport_path = job / 'transport.json'
        counts = Counter(json.loads(transport_path.read_text()).get('counts', {}) if transport_path.exists() else {})
        class MeasuredClient(WarEraApiClient):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                if self.base_url != 'https://api2.warera.io/trpc':
                    raise ValueError('Unexpected API host')
                request = self.session.request
                def measured(*a, **kw):
                    stage = state['stage']
                    counts[stage + ':attempts'] += 1
                    def save_transport(in_flight):
                        atomic_json(transport_path, {'counts':dict(counts), 'in_flight':in_flight,
                            'updated':datetime.now(timezone.utc).isoformat()})
                    save_transport(True)
                    try:
                        response = request(*a, **kw)
                        counts[stage + ':http_' + str(response.status_code)] += 1
                        return response
                    except Exception as exc:
                        counts[stage + ':' + type(exc).__name__] += 1
                        raise
                    finally:
                        save_transport(False)
                self.session.request = measured
        cli.WarEraApiClient = MeasuredClient
        with (job / 'stdout.log').open('a', encoding='utf-8', buffering=1) as stdout, \
             (job / 'stderr.log').open('a', encoding='utf-8', buffering=1) as stderr, \
             redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                if state['stage'] == 'full-import':
                    invoke_cli(database, ['--sync','--resync-market','--history-scope','all','--resume-market'])
                    store = MarketStore(database)
                    status = store.market_sync_status()
                    metadata = store.market_sync_metadata()
                    store.close()
                    atomic_json(job / 'full-status.json', status)
                    if not verified_exhaustion(status) or metadata.status != 'complete':
                        raise RuntimeError('Full import incomplete; inspect redacted status and transport counters')
                    state['stage'] = 'catch-up'
                    save_state()
                if state['stage'] == 'catch-up':
                    invoke_cli(database, ['--sync'])
                    store = MarketStore(database)
                    status = store.market_sync_status()
                    metadata = store.market_sync_metadata()
                    store.close()
                    atomic_json(job / 'catchup-status.json', status)
                    if metadata.status != 'complete':
                        raise RuntimeError('Catch-up incomplete')
                    state['stage'] = 'snapshot'
                    save_state()
                if state['stage'] == 'snapshot':
                    store = MarketStore(database)
                    if store.market_sync_metadata().status != 'complete':
                        raise RuntimeError('Source database has incomplete sync metadata')
                    as_of = state.get('as_of') or datetime.now(timezone.utc).isoformat()
                    snapshot = job / ('snapshot-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3')
                    store.backup(snapshot)
                    store.close()
                    # An interrupted backup is retained as an orphan, never overwritten.
                    state.update(stage='report', snapshot=str(snapshot), as_of=as_of)
                    atomic_json(job / 'publication.json', {'database':str(snapshot), 'as_of':as_of,
                        'status':'API exhaustion observed; retained legacy, inventory, lineage and settlement limits remain'})
                    save_state()
                if state['stage'] == 'report':
                    invoke_cli(state['snapshot'], ['--from-db','--as-of',state['as_of'],'--output',str(job / 'report')])
                    state['stage'] = 'verify'
                    save_state()
                if state['stage'] == 'verify':
                    inventory = MarketStore(state['snapshot']).database_inventory()
                    if inventory['integrity'] != ['ok']:
                        raise RuntimeError('Snapshot integrity failed')
                    atomic_json(job / 'database-inventory.json', inventory)
                    previous = sys.argv
                    try:
                        sys.argv = ['verify_market_publication', str(job)]
                        runpy.run_path(str(Path(__file__).with_name('verify_market_publication.py')), run_name='__main__')
                    finally:
                        sys.argv = previous
                    state.update(stage='done', status='complete_pending_visual_review',
                        finished=datetime.now(timezone.utc).isoformat())
                    save_state()
            except BaseException as exc:
                state.update(status='failed', error=type(exc).__name__)
                save_state()
                print('Rollout stopped at ' + state['stage'] + ': ' + type(exc).__name__, flush=True)
                raise SystemExit(1) from None


if __name__ == '__main__':
    main()
