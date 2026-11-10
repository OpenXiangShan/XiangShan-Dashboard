import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rtl_sources import executed_metadata, resolve_rtl

SHA = 'a' * 40


def log_text(report='/report/run', profile='/profiles/points.json'):
    return f'''2026-11-08T00:00:00Z echo "SPEC_DIR: /wrong/script/example"
2026-11-08T00:00:00Z   SPEC_DIR: {report}
2026-11-08T00:00:00Z   CKPT_JSON_PATH: {profile}
2026-11-08T00:00:00Z   SCORE_FILE: {report}/score-spec06-gcc16-0.3c.txt
2026-11-08T00:00:00Z [command]/usr/bin/git log -1 --format=%H
2026-11-08T00:00:00Z {SHA}
'''


class RtlSourcesTests(unittest.TestCase):
    def test_only_executed_env_and_checkout_sha_are_used(self):
        receipt = executed_metadata(log_text())
        self.assertEqual(receipt['SPEC_DIR'], '/report/run')
        self.assertEqual(receipt['commit'], SHA)
        self.assertEqual(receipt['benchmark_type'], 'spec06-gcc16-0.3c')
        self.assertIsNone(executed_metadata('echo SPEC_DIR: /example'))
        with self.assertRaisesRegex(ValueError, 'checkout SHA'):
            executed_metadata(log_text().replace(SHA, 'workflow-head-only'))
        with self.assertRaisesRegex(ValueError, '多个 SPEC_DIR'):
            executed_metadata(log_text() + '  SPEC_DIR: /another/run\n')

    def test_receipts_filter_jobs_cache_evidence_and_enforce_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report, profile = root / 'reports/run', root / 'profiles/points.json'
            report.mkdir(parents=True)
            profile.parent.mkdir()
            profile.write_text('{}')
            run = {'status': 'completed', 'run_attempt': 2, 'html_url': 'https://example/run'}
            jobs = {'jobs': [{'id': i, 'name': name, 'conclusion': status} for i, name, status in [
                (1, 'Run Performance Test / Checkpoints', 'success'),
                (2, 'Run Fuzz Test / Checkpoints', 'success'),
                (3, 'Run Performance Test / Checkpoints', 'failure')]]}
            with patch('rtl_sources.github', side_effect=[run, jobs, log_text(str(report), str(profile))]) as api:
                receipts = resolve_rtl('123', root / 'evidence', [report.parent], [profile.parent])
                self.assertEqual(api.call_count, 3)
            self.assertTrue(receipts[0]['available'])
            self.assertEqual(receipts[0]['run_attempt'], 2)
            with patch('rtl_sources.github', side_effect=AssertionError('cached')):
                self.assertEqual(resolve_rtl('123', root / 'evidence', [report.parent], [profile.parent]), receipts)
            for reports, profiles in [([root / 'elsewhere'], [profile.parent]), ([report.parent], [root / 'elsewhere'])]:
                with self.assertRaisesRegex(ValueError, '路径不在'):
                    resolve_rtl('123', root / 'evidence', reports, profiles)
            profile.unlink()
            self.assertFalse(resolve_rtl('123', root / 'evidence', [report.parent], [profile.parent])[0]['available'])

    def test_active_run_is_not_cached(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch('rtl_sources.github', return_value={'status': 'in_progress'}):
                with self.assertRaisesRegex(ValueError, '尚未结束'):
                    resolve_rtl('123', Path(temporary), [], [])
            self.assertFalse((Path(temporary) / '123/receipts.json').exists())


if __name__ == '__main__':
    unittest.main()
