import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commit_ipc_trend.dashboard_export import export_history
from commit_ipc_trend.parsers import load_registry
from commit_ipc_trend.store import Store
from tools.import_weekly_spec06 import import_counters, import_weekly
from tools.update_dashboard import SCORE_NAME, collect_evidence, main, parse_args, update_lock


class FakeGitHubApi:
    def __init__(self, run, job_log, score):
        self.run = run
        self.job_log = job_log
        self.score = score

    def workflow_runs(self, workflow, branch, page_limit):
        return [self.run]

    def jobs(self, run_id, page_limit):
        return [{
            "id": 13,
            "name": "Run SPEC06 GCC16 Performance Test / Checkpoints",
            "conclusion": "success",
            "started_at": "2026-11-05T10:00:00Z",
        }]

    def job_logs(self, job_id):
        return self.job_log

    def artifacts(self, run_id, page_limit):
        return [{
            "id": 14,
            "name": "score",
            "created_at": self.run["created_at"],
        }]

    def artifact_zip(self, artifact_id):
        content = io.BytesIO()
        with ZipFile(content, "w") as archive:
            archive.writestr(SCORE_NAME, self.score)
        return content.getvalue()


class PipelineTest(unittest.TestCase):
    def test_shared_config_resolves_paths_and_allows_cli_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text(json.dumps({
                "data_dir": "shared", "report_root": "reports", "git_repo": "XiangShan",
                "gcc16_profile": "profile.json", "update_host": socket.gethostname(),
            }))
            args = parse_args(["--config", str(config)])
            self.assertEqual(args.db, root / "shared/performance.sqlite")
            self.assertEqual(args.evidence, root / "shared/evidence")
            self.assertEqual(args.output, root / "shared/weekly-spec06-gcc16")
            self.assertEqual(args.dashboard_output, root / "shared/mainline-history")
            self.assertEqual(args.git_repo, root / "XiangShan")
            overridden = parse_args(["--config", str(config), "--db", str(root / "other.sqlite")])
            self.assertEqual(overridden.db, root / "other.sqlite")

    def test_wrong_update_host_does_not_create_shared_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text(json.dumps({
                "data_dir": "shared", "report_root": "reports", "git_repo": "XiangShan",
                "gcc16_profile": "profile.json", "update_host": "another-update-host",
            }))
            with redirect_stdout(io.StringIO()), patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as error:
                    main(["--config", str(config)])
            self.assertEqual(error.exception.code, 2)
            self.assertFalse((root / "shared").exists())

    def test_second_process_cannot_update_until_lock_is_released(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "shared/performance.sqlite"
            command = [sys.executable, "-c", (
                "from pathlib import Path; from tools.update_dashboard import update_lock; "
                "import sys; "
                "\nwith update_lock(Path(sys.argv[1])): print('acquired')"
            ), str(database)]
            try:
                with update_lock(database):
                    blocked = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
                    self.assertNotEqual(blocked.returncode, 0)
                    self.assertIn("another dashboard update is running", blocked.stderr)
                    raise RuntimeError("failed import")
            except RuntimeError:
                pass
            released = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(released.returncode, 0, released.stderr)
            self.assertEqual(released.stdout.strip(), "acquired")

    def test_update_entry_passes_local_inputs_through_export_stages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = root / "reports"
            reports.mkdir()
            git_repo = root / "XiangShan"
            git_repo.mkdir()
            profile = root / "checkpoints_all.json"
            profile.write_text("{}")
            evidence = root / "evidence"
            evidence.mkdir()
            database = root / "history.sqlite"
            with Store(database) as store:
                store.initialize()

            commands = []

            def record_stage(command):
                commands.append(command)
                if "prepare" in command:
                    output = Path(command[command.index("--output") + 1])
                    output.mkdir(parents=True, exist_ok=True)
                    (output / "plan.json").write_text('{"runs": []}')

            with redirect_stdout(io.StringIO()):
                with patch("tools.update_dashboard.run_stage", side_effect=record_stage):
                    self.assertEqual(main([
                        "--report-root", str(reports),
                        "--git-repo", str(git_repo),
                        "--gcc16-profile", str(profile),
                        "--db", str(database),
                        "--evidence", str(evidence),
                        "--output", str(root / "weekly"),
                        "--dashboard-output", str(root / "dashboard"),
                        "--import-only", "--skip-receipts",
                    ]), 0)

            weekly_imports = [command for command in commands
                              if command[-1] == "--skip-invalid"]
            self.assertEqual(len(weekly_imports), 1)
            self.assertIn(str(profile), weekly_imports[0])
            recovery = next(i for i, command in enumerate(commands) if "prepare" in command)
            applied = next(i for i, command in enumerate(commands) if "apply" in command)
            exported = next(i for i, command in enumerate(commands)
                            if "export_dashboard_history.py" in command[1])
            self.assertLess(recovery, applied)
            self.assertLess(applied, exported)
            prepare = commands[recovery]
            self.assertIn(str(root / "weekly" / "source"), prepare)

    def test_ci_evidence_imports_once_and_exports_dashboard_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git_repo = root / "XiangShan"
            git_repo.mkdir()
            for command in (
                ["init", "-q"],
                ["config", "user.name", "test"],
                ["config", "user.email", "test@example.com"],
            ):
                subprocess.run(["git", "-C", str(git_repo), *command], check=True)
            (git_repo / "source.txt").write_text("fixture\n")
            subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(git_repo), "commit", "-qm", "fixture"], check=True
            )
            sha = subprocess.check_output(
                ["git", "-C", str(git_repo), "rev-parse", "HEAD"], text=True
            ).strip()

            profile_root = root / "gcc16_rva23_novec_260820"
            checkpoint = profile_root / "checkpoint"
            checkpoint.mkdir(parents=True)
            profile = profile_root / "json" / "checkpoints_all.json"
            profile.parent.mkdir()
            profile.write_text(json.dumps({
                "mcf": {"insts": 100, "points": {"1": 1.0}},
            }))

            reports = root / "reports"
            report = reports / f"cr261105-{sha[:9]}-DefaultConfig"
            slice_dir = report / "mcf_1_1.0"
            slice_dir.mkdir(parents=True)
            (slice_dir / "simulator_out.txt").write_text(
                f"emu-gsim compiled at Nov 5 2026\n"
                f"The image is {checkpoint}/mcf/1/image.zstd\n"
                "DRAMSIM3 config: configs/XiangShan.ini\n"
                "CPU_FREQ: 3000 DRAM_FREQ: 1600\n"
                "instrCnt = 100, cycleCnt = 50, IPC = 2.000000\n"
            )
            registry = load_registry()
            perf_lines = []
            for timestamp in (100, 200):
                for metric in registry["metrics"]:
                    perf_lines.append(
                        f"[PERF ][time= {timestamp}] {metric['source_path']}: "
                        f"{metric['source_name']}, 20000000"
                    )
                perf_lines.append(
                    f"[PERF ][time= {timestamp}] {registry['metrics'][0]['source_path']}: "
                    "clock_cycle, 50000000"
                )
            (slice_dir / "simulator_err.txt").write_text("\n".join(perf_lines) + "\n")

            score_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            score = (
                "429.mcf 120 9120 25.333 1.000\n"
                "SPEC2006 nan nan 20.541 nan\n"
                f"Data Directory : /ci/perf-report/{report.name}\n"
                "=============== Failed Checkpoints ===============\n[]\n"
            )
            run = {
                "id": 12,
                "run_number": 38,
                "conclusion": "success",
                "head_sha": "f" * 40,
                "created_at": score_time,
                "html_url": "https://github.com/OpenXiangShan/XiangShan/actions/runs/12",
            }
            job_log = (
                "[command]/usr/bin/git log -1 --format=%H\n"
                f"{sha}\n"
                f"SPEC_DIR: /ci/perf-report/{report.name}\n"
                "CKPT_JSON_PATH: /ci/checkpoints/checkpoints_all.json\n"
                f"CKPT_HOME: {checkpoint}\n"
            )
            evidence = root / "evidence"
            collected = collect_evidence(
                FakeGitHubApi(run, job_log, score),
                workflow="weekly.yml",
                branch="kunminghu-v3",
                evidence_root=evidence,
                report_root=reports,
                page_limit=2,
                max_runs=10,
            )
            self.assertEqual(collected["collected"][0]["run_id"], 12)
            self.assertEqual((evidence / "12").stat().st_mode & 0o2770, 0o2770)

            args = argparse.Namespace(
                evidence=evidence,
                report_root=reports,
                archive_root=root / "archive",
                git_repo=git_repo,
                gcc16_profile=profile,
                db=root / "history.sqlite",
                output=root / "intermediate",
                skip_invalid=True,
            )
            with redirect_stdout(io.StringIO()):
                import_weekly(args)
                relocated = root / "shared-reports"
                reports.rename(relocated)
                reports.symlink_to(relocated, target_is_directory=True)
                args.report_root = relocated
                import_weekly(args)
                import_counters(argparse.Namespace(db=args.db, output=args.output, jobs=1))

            with Store(args.db, read_only=True) as store:
                self.assertEqual(store.counts()["runs"], 1)
                self.assertEqual(store.counts()["counter_values"], 20)
                run_row = store.connection.execute(
                    "SELECT status,experiment_json FROM runs"
                ).fetchone()
                self.assertEqual(run_row["status"], "published")
                instructions = store.connection.execute(
                    "SELECT value,availability FROM counter_values "
                    "WHERE metric_id='rob_committed_instructions'"
                ).fetchone()
                self.assertEqual(tuple(instructions), (20000000.0, "available"))
                receipt = json.loads(
                    (args.output / "receipts" / "12.json").read_text()
                )
                self.assertEqual(receipt["checkpoint_list"], str(profile.resolve()))

            dashboard = root / "dashboard-json"
            export_history(args.db, dashboard)
            catalog = json.loads((dashboard / "manifest.json").read_text())
            self.assertEqual(catalog["run_count"], 1)
            dataset = json.loads(
                (dashboard / f"{catalog['default_dataset']}.json").read_text()
            )
            self.assertEqual(dataset["runs"][0]["status"], "published")
            scores = {score["object_id"]: score["value"]
                      for score in dataset["runs"][0]["scores"]}
            self.assertEqual(scores["SPEC2006"], 20.541)


if __name__ == "__main__":
    unittest.main()
