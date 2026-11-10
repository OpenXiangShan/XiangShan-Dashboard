import gzip
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gem5_analysis import AnalysisService, QueryError, change, filter_rows, make_rows, parse_run, run_repository


class QueryTests(unittest.TestCase):
    def test_run_identity_rejects_other_hosts_repositories_and_attempts(self):
        self.assertEqual(parse_run(' 123 '), '123')
        self.assertEqual(parse_run('https://github.com/OpenXiangShan/GEM5/actions/runs/123?check_suite_focus=true'), '123')
        for value in ['0', '../123', 'https://evil.test/OpenXiangShan/GEM5/actions/runs/123',
                      'https://github.com/other/GEM5/actions/runs/123',
                      'https://github.com/OpenXiangShan/GEM5/actions/runs/123/attempts/2']:
            with self.subTest(value=value), self.assertRaises(QueryError):
                parse_run(value)

    def test_explicit_urls_identify_both_backends(self):
        for repo, backend in [('GEM5', 'gem5'), ('XiangShan', 'xs')]:
            url = f'https://github.com/OpenXiangShan/{repo}/actions/runs/123'
            self.assertEqual(parse_run(url), '123')
            self.assertEqual(run_repository(url), backend)
        self.assertIsNone(run_repository('123'))

    def test_zero_missing_and_nonfinite_values_do_not_invent_percentages(self):
        self.assertEqual(change(0, 0), (0, 0))
        self.assertEqual(change(0, 10), (10, None))
        self.assertEqual(change(None, 10), (None, None))
        self.assertEqual(change(float('nan'), 1), (None, None))
        self.assertEqual(change(10, 12), (2, 20))

    def test_profile_mismatch_does_not_pair_simpoint_numbers(self):
        left = {'simpoint': [{'name': 'gcc_1', 'bmk': 'gcc', 'x': 10}]}
        right = {'simpoint': [{'name': 'gcc_1', 'bmk': 'gcc', 'x': 20}]}
        manifests = [{'profile_hash': value, 'points': [{'name': 'gcc_1', 'workload': 'gcc', 'point': 1, 'weight': .5}]} for value in ['a', 'b']]
        row = make_rows(left, right, ['x'], 'simpoint', manifests)[0]
        self.assertEqual(row['status'], 'profile_mismatch')
        self.assertIsNone(row['delta_pct'])
        self.assertEqual((row['a'], row['b']), (10, 20))

    def test_filters_apply_to_same_rows_used_by_export_and_sort(self):
        rows = [{'name': str(i), 'benchmark': b, 'point': i, 'delta': d, 'delta_pct': p}
                for i, b, d, p in [(1, 'gcc', 2, 20), (2, 'gcc', -4, -40), (3, 'mcf', 10, None)]]
        self.assertEqual([r['name'] for r in filter_rows(rows, {'benchmarks': ['gcc'], 'min_change': 30})], ['2'])
        self.assertEqual([r['name'] for r in filter_rows(rows, {})], ['3', '2', '1'])
        self.assertEqual([r['name'] for r in filter_rows(rows, {'simpoints': ['1']})], ['1'])
        for threshold in [-1, float('nan'), float('inf')]:
            with self.assertRaises(QueryError):
                filter_rows(rows, {'min_change': threshold})


    def test_default_sort_prioritizes_absolute_delta_over_relative_change(self):
        rows = [
            {"name": name, "benchmark": "gcc", "point": 1, "delta": delta, "delta_pct": pct}
            for name, delta, pct in [("small", 199, 19900), ("large", 1800, 900),
                                     ("decrease", -2500, -50), ("missing", None, None)]
        ]
        self.assertEqual([r["name"] for r in filter_rows(rows, {})],
                         ["decrease", "large", "small", "missing"])
        self.assertEqual([r["name"] for r in filter_rows(rows, {"sort": "delta"})],
                         ["large", "small", "decrease", "missing"])
        self.assertEqual(filter_rows(rows, {"sort": "abs_pct"})[0]["name"], "small")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.proc = self.root / 'processor'
        self.proc.mkdir()
        for name in ['analysis_pipeline.py', 'analysis_inputs.py', 'batch.py', 'paths.py', 'local_configs.py']:
            (self.proc / name).write_text('# test processor')
        self.archive_root = self.root / 'archive'
        self.archive = self.archive_root / 'spec06-0.3c' / 'one'
        self.archive.mkdir(parents=True)
        (self.archive / 'metadata.txt').write_text('workflow_run_id: 123\ncommit: abc\nbenchmark_type: spec06-0.3c\nconfig_name: kmhv3\n')
        (self.archive / 'cluster.json').write_text('{"gcc": {"insts": 100, "points": {"1": 0.3}}}')
        point = self.archive / 'spec_all' / 'gcc_1' / 'm5out'
        point.mkdir(parents=True)
        (point / 'stats.txt').write_text('stats')
        (point.parent / 'completed').touch()
        self.service = AnalysisService(self.archive_root, self.proc, self.root / 'cache', max_pending=1)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_multiple_archives_are_retained_and_identity_is_not_user_path(self):
        second = self.archive.parent / 'two'
        second.mkdir()
        (second / 'metadata.txt').write_text('workflow_run_id: 123\nconfig_name: ideal\n')
        result = self.service.resolve_run('123')
        self.assertEqual(len(result['candidates']), 2)
        with self.assertRaises(QueryError):
            self.service.archive(str(self.archive))
        with self.assertRaises(QueryError):
            self.service.resolve_run('456', backend='gem5')

    def test_explicit_gem5_url_overrides_dataset_hint_and_never_reads_github(self):
        with patch('rtl_sources.github', side_effect=AssertionError('unexpected GitHub request')):
            result = self.service.resolve_run('https://github.com/OpenXiangShan/GEM5/actions/runs/123', backend='xs')
            self.assertEqual(result['candidates'][0]['backend'], 'gem5')
            with self.assertRaises(QueryError):
                self.service.resolve_run('https://github.com/OpenXiangShan/GEM5/actions/runs/456')

    def test_old_processor_checkout_fails_with_actionable_error(self):
        (self.proc / 'analysis_pipeline.py').unlink()
        with self.assertRaisesRegex(QueryError, 'analysis_pipeline.py'):
            AnalysisService(self.archive_root, self.proc, self.root / 'other-cache')

    def test_cache_identity_tracks_profile_processor_and_completion(self):
        archive = self.service.resolve_run('123')['candidates'][0]
        first = self.service._signature(archive)
        (self.archive / 'cluster.json').write_text('{"changed": true}')
        second = self.service._signature(archive)
        self.assertNotEqual(first, second)
        (self.archive / 'spec_all/gcc_1/completed').unlink()
        self.assertNotEqual(second, self.service._signature(archive))
        old = self.service._processor_hash()
        (self.proc / 'batch.py').write_text('changed')
        self.assertNotEqual(old, self.service._processor_hash())

    def test_queue_deduplicates_running_query_and_enforces_limit(self):
        archive = self.service.resolve_run('123')['candidates'][0]
        query = {'a': archive['id'], 'b': archive['id'], 'metrics': ['cpi']}
        release = threading.Event()
        def execute(job, identity, archives):
            release.wait(5)
            with self.service.lock:
                self.service.pending.pop(identity, None)
                job['status'] = 'complete'
        with patch.object(self.service, '_execute', side_effect=execute):
            try:
                first = self.service.submit(query)
                self.assertEqual(first['id'], self.service.submit(query)['id'])
                with self.assertRaises(QueryError):
                    self.service.submit({**query, 'metrics': ['cycles']})
            finally:
                release.set()

    def test_category_query_accepts_more_than_twelve_metrics(self):
        archive = self.service.resolve_run('123')['candidates'][0]
        query = {'a': archive['id'], 'b': archive['id'], 'metrics': [f'metric{i}' for i in range(80)]}
        release = threading.Event()
        def blocked(*args):
            release.wait(5)
        with patch.object(self.service, '_execute', side_effect=blocked):
            try:
                self.assertEqual(len(self.service.submit(query)['query']['metrics']), 80)
                with self.assertRaises(QueryError):
                    self.service.submit({**query, 'metrics': [f'm{i}' for i in range(513)]})
            finally:
                release.set()

    def test_cancel_running_worker_stops_process_and_frees_queue(self):
        worker = self.root / 'sleep_worker.py'
        worker.write_text('import time,json,sys\nfrom pathlib import Path\n'
            'request=json.loads(Path(sys.argv[2]).read_text())\n'
            'Path(request["output"],"progress.json").write_text(json.dumps({"stage":"测试读取","completed":1,"total":4}))\n'
            'time.sleep(60)\n')
        archive = self.service.resolve_run('123')['candidates'][0]
        with patch('gem5_analysis.WORKER', worker):
            job = self.service.submit({'a': archive['id'], 'b': archive['id']})
            deadline = time.monotonic() + 5
            while job['id'] not in self.service.processes and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertIn(job['id'], self.service.processes)
            process = self.service.processes[job['id']]
            while 'progress' not in self.service.get_job(job['id']) and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(self.service.get_job(job['id'])['progress']['completed'], 1)
            self.service.cancel_job(job['id'])
            while self.service.get_job(job['id'])['status'] in {'queued', 'running'} and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(self.service.get_job(job['id'])['status'], 'cancelled')
            self.assertIsNotNone(process.poll())
            self.assertFalse(self.service.pending)
            self.assertEqual(list((self.service.cache / 'runs').iterdir()), [])

    def test_cancel_queued_job_does_not_execute_it(self):
        self.service.max_pending = 2
        archive = self.service.resolve_run('123')['candidates'][0]
        release = threading.Event()
        def blocked(job, identity, archives):
            release.wait(5)
        with patch.object(self.service, '_execute', side_effect=blocked) as execute:
            try:
                self.service.submit({'a': archive['id'], 'b': archive['id'], 'metrics': ['cpi']})
                second = self.service.submit({'a': archive['id'], 'b': archive['id'], 'metrics': ['cycles']})
                stopped = self.service.cancel_job(second['id'])
                self.assertEqual(stopped['status'], 'cancelled')
                self.assertTrue(self.service.futures[second['id']].cancelled())
            finally:
                release.set()
        self.assertEqual(execute.call_count, 1)

    def test_raw_search_is_not_limited_to_yaml_registry(self):
        snapshot = 'a' * 64
        directory = self.service.cache / 'runs' / snapshot
        directory.mkdir(parents=True)
        (directory / 'catalog.json').write_text(json.dumps({'groups': {'basic': ['cpi']},
            'yaml_metrics': ['cpi'], 'raw_metrics': ['system.cpu.brandNewCounter']}))
        found = self.service.metrics([snapshot], 'brandnew')
        self.assertEqual(found['items'][0]['id'], 'raw:system.cpu.brandNewCounter')
        self.assertEqual(found['group_metrics'], {'basic': ['cpi']})
        self.assertEqual(self.service.metrics([snapshot], 'brandnew', offset=100)['group_metrics']['basic'], ['cpi'])
        self.assertEqual(self.service.metrics([snapshot], 'brandnew', ['basic'])['total'], 0)
        with self.assertRaises(QueryError):
            self.service.metrics(['../../private'])



class ProcessorIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(__import__('os').environ.get('GEM5_DATA_PROC'), 'set GEM5_DATA_PROC to exercise the real processor')
    def test_real_processor_weighting_raw_search_missing_values_and_incomplete_points(self):
        processor = Path(__import__('os').environ['GEM5_DATA_PROC'])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, run, cycles, counters in [('a', 123, [100, 200], [10, 30]), ('b', 456, [125, 200], [20, 40])]:
                archive = root / 'archive/spec06-0.3c' / label
                archive.mkdir(parents=True)
                (archive / 'metadata.txt').write_text(f'workflow_run_id: {run}\nbenchmark_type: spec06-0.3c\nconfig_name: kmhv3\n')
                (archive / 'cluster.json').write_text(json.dumps({'gcc': {'insts': '1000', 'points': {'1': '.2', '2': '.6', '3': '.2'}}}))
                for point in [1, 2, 3]:
                    folder = archive / f'spec_all/gcc_{point}'
                    (folder / 'm5out').mkdir(parents=True)
                    cyc = cycles[min(point - 1, 1)]
                    counter = counters[min(point - 1, 1)]
                    stats = f'''---------- Begin Simulation Statistics ----------
system.cpu.numCycles {cyc}
system.cpu.committedInsts 100
system.cpu.totalIpc {100 / cyc}
system.cpu.brandNewCounter {counter}
'''
                    if label == 'b':
                        stats += 'system.cpu.onlyInNewRun 42\n'
                    stats += '---------- End Simulation Statistics ----------\n'
                    stats = '\n'.join(line + ' # fixture' if line.startswith('system.') else line for line in stats.splitlines()) + '\n'
                    (folder / 'm5out/stats.txt').write_text(stats + stats)
                    if point != 3:
                        (folder / 'completed').touch()
            service = AnalysisService(root / 'archive', processor, root / 'cache',
                                      rtl_report_roots=[root / 'rtl'], profile_roots=[root / 'archive'])
            try:
                a, b = [service.resolve_run(run)['candidates'][0]['id'] for run in ['123', '456']]
                job = service.submit({'a': a, 'b': b, 'metrics': ['cpi', 'raw:system.cpu.brandNewCounter', 'raw:system.cpu.onlyInNewRun']})
                deadline = time.monotonic() + 120
                while job['status'] in {'queued', 'running'} and time.monotonic() < deadline:
                    time.sleep(.1)
                    job = service.get_job(job['id'])
                self.assertEqual(job['status'], 'complete', job.get('error'))
                result = job['result']
                rows = {r['metric']: r for r in result['rows'] if r['benchmark'] == 'gcc'}
                self.assertAlmostEqual(rows['cpi']['a'], 1.75)
                self.assertAlmostEqual(rows['cpi']['b'], 1.8125)
                self.assertAlmostEqual(rows['raw:system.cpu.brandNewCounter']['a'], 25)
                self.assertAlmostEqual(rows['raw:system.cpu.brandNewCounter']['b'], 35)
                self.assertAlmostEqual(rows['raw:system.cpu.brandNewCounter']['delta_pct'], 40)
                self.assertIsNone(rows['raw:system.cpu.onlyInNewRun']['a'])
                self.assertEqual(rows['raw:system.cpu.onlyInNewRun']['status'], 'missing')
                self.assertEqual(result['runs'][0]['completed_points'], 2)
                self.assertEqual(result['runs'][0]['expected_points'], 3)
                self.assertAlmostEqual(rows['cpi']['coverage_a'], .8)
                self.assertTrue(any('部分运行' in warning for warning in result['warnings']))
                found = service.metrics([r['snapshot_id'] for r in result['runs']], 'brandnew')
                self.assertEqual(found['items'][0]['kind'], 'raw')
                # Point raw queries pass their scope to the processor and keep raw values.
                point_job = service.submit({"a": a, "b": b, "level": "simpoint",
                    "benchmarks": ["gcc"], "simpoints": ["gcc_1"],
                    "metrics": ["cpi", "raw:system.cpu.brandNewCounter"]})
                deadline = time.monotonic() + 120
                while point_job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                    time.sleep(.1)
                    point_job = service.get_job(point_job["id"])
                self.assertEqual(point_job["status"], "complete", point_job.get("error"))
                point_rows = point_job["result"]["rows"]
                self.assertEqual({row["name"] for row in point_rows}, {"gcc_1"})
                raw_row = next(row for row in point_rows if row["metric"].startswith("raw:"))
                self.assertEqual((raw_row["a"], raw_row["b"]), (10, 20))
                self.assertIn("gcc", point_job["result"]["benchmarks"])
                # Resolve an RTL receipt through the same service and compare backends.
                report = root / 'rtl/run'
                profile = root / 'archive/spec06-0.3c/a/cluster.json'
                for point, cyc in [(1, 100), (2, 200)]:
                    folder = report / f'gcc_{point}_{float([.2, .6][point - 1]):.6f}'
                    folder.mkdir(parents=True)
                    (folder / 'simulator_err.txt').write_text(
                        '[PERF ][time=1] SimTop.rob: commitInstr, 200\n'
                        '[PERF ][time=2] SimTop.rob: commitInstr, 100\n'
                        f'[PERF ][time=2] SimTop.rob: clock_cycle, {cyc}\n')
                    (folder / 'simulator_out.txt').write_text('EXCEEDING CYCLE/INSTR LIMIT')
                evidence = service.cache / 'rtl-evidence/789'
                evidence.mkdir(parents=True)
                (evidence / 'receipts.json').write_text(json.dumps([{
                    'SPEC_DIR': str(report), 'CKPT_JSON_PATH': str(profile),
                    'SCORE_FILE': str(report / 'score-spec06-0.3c.txt'),
                    'commit': 'a' * 40, 'benchmark_type': 'spec06-0.3c', 'job_id': 1,
                    'actions_url': 'https://github.com/OpenXiangShan/XiangShan/actions/runs/789'}]))
                rtl = service.resolve_run('https://github.com/OpenXiangShan/XiangShan/actions/runs/789')['candidates'][0]
                mixed = service.submit({'a': rtl['id'], 'b': a, 'metrics': ['cpi', 'raw:system.cpu.brandNewCounter']})
                deadline = time.monotonic() + 120
                while mixed['status'] in {'queued', 'running'} and time.monotonic() < deadline:
                    time.sleep(.1)
                    mixed = service.get_job(mixed['id'])
                self.assertEqual(mixed['status'], 'complete', mixed.get('error'))
                rows = {r['metric']: r for r in mixed['result']['rows'] if r['benchmark'] == 'gcc'}
                self.assertEqual(rows['cpi']['a'], rows['cpi']['b'])
                self.assertIsNone(rows['raw:system.cpu.brandNewCounter']['a'])
                self.assertEqual(rows['raw:system.cpu.brandNewCounter']['status'], 'missing')
                self.assertTrue(any('跨 RTL/GEM5' in w for w in mixed['result']['warnings']))
            finally:
                service.close()


class StaticRoutingTests(unittest.TestCase):
    def test_data_route_cannot_escape_into_sources_or_analysis_cache(self):
        import serve_gem5
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build = root / 'dist-internal'
            build.mkdir()
            (root / 'data').mkdir()
            handler = serve_gem5.Handler.__new__(serve_gem5.Handler)
            handler.directory = str(build)
            with patch.object(serve_gem5, 'ROOT', root):
                self.assertEqual(handler.translate_path('/data/list.json'), str(root / 'data/list.json'))
                self.assertEqual(handler.translate_path('/assets/index.js'), str(build / 'assets/index.js'))
                for url in ['/data/%2e%2e/internal/local/secret.json', '/%2e%2e/internal/secret.json']:
                    self.assertEqual(handler.translate_path(url), str(build / '__not_found__'))


if __name__ == '__main__':
    unittest.main()
