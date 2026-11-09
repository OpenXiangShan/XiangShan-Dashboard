"""Rolling database validation, archive extraction and upstream tool execution."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import tempfile


def inspect_database(path: Path, hart: int = 0) -> dict:
    path = path.resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        names = [r[0] for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        tables = [n for n in names if re.fullmatch(r'\w+_rolling_\d+', n)]
        ipc = f'ipc_rolling_{hart}'
        if ipc not in tables:
            raise ValueError(f'missing {ipc}; use a simulator rolling DB, not the trend DB')
        samples = connection.execute(
            f'SELECT XAXISPT,YAXISPT FROM "{ipc}" ORDER BY ID'
        ).fetchall()
        if len(samples) < 2:
            raise ValueError('IPC needs at least two samples')
        if any(not isinstance(x, int) or not isinstance(y, int) or x < 0 or y < 0 for x, y in samples):
            raise ValueError('IPC coordinates/counts must be nonnegative integers')
        gaps = [b[0] - a[0] for a, b in zip(samples, samples[1:])]
        if min(gaps) <= 0:
            raise ValueError('IPC X axis resets or is not strictly increasing')
        # The initial coordinate need not be the start of a complete window.
        fixed = len(set(gaps)) == 1 and samples[0][0] == gaps[0]
        return {
            'path': str(path), 'bytes': path.stat().st_size,
            'mtime_ns': path.stat().st_mtime_ns, 'hart': hart,
            'rolling_tables': tables, 'samples': len(samples),
            'x_min': samples[0][0], 'x_max': samples[-1][0],
            'gap_min': min(gaps), 'gap_max': max(gaps),
            'fixed_cycle_windows_from_zero': fixed,
            'instructions': sum(y for _, y in samples),
            'ipc': sum(y for _, y in samples) / samples[-1][0] if fixed else None,
        }


def extract_database(archive: Path, member: str, output: Path) -> None:
    relative = PurePosixPath(member)
    if relative.is_absolute() or '..' in relative.parts or not member.endswith('.db'):
        raise ValueError('member must be a relative .db archive member without ..')
    if output.exists():
        raise ValueError(f'output already exists: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            # Stream only the named member; never restore archive paths or links.
            subprocess.run(
                ['tar', '-xOf', str(archive.resolve(strict=True)), '--occurrence=1', '--', member],
                stdout=stream, stderr=subprocess.PIPE, check=True,
            )
        with temporary.open('rb') as stream:
            if stream.read(16) != b'SQLite format 3\x00':
                raise ValueError('archive member is not SQLite')
        temporary.rename(output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _analysis_jobs(database: dict, args: argparse.Namespace, output: Path) -> tuple[list, list]:
    """Plan upstream commands after validating table and cycle compatibility."""
    db = database['path']
    tables = set(database['rolling_tables'])
    suffix = f'_rolling_{args.hart}'
    selected = args.perf_name or ['ipc']
    for name in selected:
        if not re.fullmatch(r'\w+', name) or name + suffix not in tables:
            raise ValueError(f'counter is missing for this hart: {name}')
    jobs = [('list', ['list', db, '--hart', str(args.hart)], None)]
    skips = []
    cycle_names = [t[:-len(suffix)] for t in tables if t.startswith('td_cycle_') and t.endswith(suffix)]
    if database['fixed_cycle_windows_from_zero']:
        # Raw-count correlation is a rate correlation only for equal windows.
        with closing(sqlite3.connect(Path(db).as_uri() + '?mode=ro', uri=True)) as connection:
            ipc_x = [r[0] for r in connection.execute(f'SELECT XAXISPT FROM "ipc{suffix}" ORDER BY ID')]
            aligned = []
            for name in sorted(cycle_names):
                x = [r[0] for r in connection.execute(f'SELECT XAXISPT FROM "{name}{suffix}" ORDER BY ID')]
                if x == ipc_x:
                    aligned += ['--perf-name', name]
                else:
                    skips.append({'counter': name, 'reason': 'cycle_coordinates_differ'})
            for name in selected:
                x = [r[0] for r in connection.execute(f'SELECT XAXISPT FROM "{name}{suffix}" ORDER BY ID')]
                if x != ipc_x:
                    raise ValueError(f'plot counter does not share IPC cycle windows: {name}')
        if aligned:
            jobs.append(('cycle-correlation', ['corr', db, '--hart', str(args.hart), '--align', 'xaxis', *aligned,
                         '--csv', str(output / 'correlation-cycle.csv')], 'correlation-cycle.csv'))
    else:
        skips.append({'step': 'cycle-correlation', 'reason': 'variable_windows_or_unknown_origin'})
    if args.prefetch_progress:
        prefixes = [prefix for prefix in ('L1Prefetch', 'L2Prefetch')
                    if any(t.startswith(prefix) and t.endswith(suffix) for t in tables)]
        if prefixes:
            selection = [arg for prefix in prefixes for arg in ('--include-prefix', prefix)]
            jobs.append(('prefetch-progress', ['corr', db, '--hart', str(args.hart), '--align', 'progress',
                         '--progress-points', '5000', *selection, '--csv',
                         str(output / 'correlation-prefetch-progress.csv')], 'correlation-prefetch-progress.csv'))
        else:
            skips.append({'step': 'prefetch-progress', 'reason': 'no_prefetch_tables'})
    if database['fixed_cycle_windows_from_zero']:
        if args.aggregate > database['samples']:
            raise ValueError('aggregate exceeds IPC sample count')
        jobs.append(('plot', ['plot', db, '--hart', str(args.hart), '--aggregate', str(args.aggregate),
                     *[arg for name in selected for arg in ('--perf-name', name)],
                     '--output', str(output / 'rolling.png')], 'rolling.png'))
    else:
        skips.append({'step': 'plot', 'reason': 'variable_windows_or_unknown_origin'})
    return jobs, skips


def analyze(args: argparse.Namespace) -> int:
    database = inspect_database(args.db, args.hart)
    interpreter = shutil.which(args.python)
    if interpreter is None:
        raise ValueError(f'Python executable not found: {args.python}')
    interpreter = os.path.abspath(interpreter)
    script = (args.xiangshan / 'scripts/rolling.py').resolve(strict=True)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'analysis.json').exists():
        raise ValueError('analysis.json already exists; choose a new output directory')
    jobs, skips = _analysis_jobs(database, args, output)
    record = {
        'schema_version': 'rolling-analysis/v1', 'status': 'running',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source_run': args.source_run, 'database': database,
        'analyzer': str(script), 'analyzer_sha256': hashlib.sha256(script.read_bytes()).hexdigest(),
        'python': interpreter, 'aggregate': args.aggregate,
        'warnings': ['single_slice_not_workload', 'correlation_is_not_causation',
                     'rolling_window_may_include_warmup'],
        'skipped': skips, 'commands': [],
    }
    env = dict(os.environ, MPLBACKEND='Agg', MPLCONFIGDIR=str(output / 'mpl-cache'))
    destination = output / 'analysis.json'
    def save():
        destination.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    save()
    for name, arguments, artifact in jobs:
        command = [interpreter, str(script), *arguments]
        try:
            completed = subprocess.run(command, cwd=output, env=env, capture_output=True, text=True, timeout=args.timeout)
            stdout, stderr, code = completed.stdout, completed.stderr, completed.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            stdout, stderr, code = '', str(error), -1
        (output / f'{name}.stdout.txt').write_text(stdout, encoding='utf-8')
        (output / f'{name}.stderr.txt').write_text(stderr, encoding='utf-8')
        optional_empty = name.endswith(('correlation', 'progress')) and code == 1 and stdout.strip() == 'no comparable rolling counters found'
        valid = code == 0 and (artifact is None or (output / artifact).is_file() and (output / artifact).stat().st_size > 0)
        record['commands'].append({'name': name, 'command': command, 'returncode': code,
                                   'status': 'completed' if valid else 'skipped' if optional_empty else 'failed'})
        if optional_empty:
            record['skipped'].append({'step': name, 'reason': 'no_nonconstant_aligned_series'})
        save()
        if not valid and not optional_empty:
            record['status'] = 'failed'
            save()
            return 1
    stat = Path(database['path']).stat()
    if stat.st_size != database['bytes'] or stat.st_mtime_ns != database['mtime_ns']:
        record['status'] = 'failed'
        record['warnings'].append('source_database_changed_during_analysis')
    else:
        record['status'] = 'partial' if record['skipped'] else 'completed'
    save()
    print(destination)
    return 1 if record['status'] == 'failed' else 0
