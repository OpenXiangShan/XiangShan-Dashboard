"""Update dashboard data from GitHub artifacts or local results."""

import calendar
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from zipfile import ZipFile

from modules.config import UpdateConfig
from modules.github import GitHub
from modules.json import MetadataJson, RunListJson, ReportRegressionJson, ReportTestJson


DATA_PATH = Path(__file__).parent.parent.parent / "data"


def workflow_runs_for_commit(
    gh: GitHub, config: UpdateConfig, sha: str, *, all_workflows: bool = False
) -> list[dict]:
    runs = []
    for page in count(1):
        result = gh.actions.list_workflow_runs(
            config.owner,
            config.repo_name,
            branch=config.upstream_branch,
            event=config.event,
            status="completed",
            head_sha=sha,
            per_page=100,
            page=page,
        )["workflow_runs"]
        runs.extend(result)
        if len(result) < 100:
            break
    return (
        runs
        if all_workflows
        else [run for run in runs if run["name"] == config.workflow]
    )


def append_commit(
    metadata: MetadataJson,
    run_list: RunListJson,
    run_id: int,
    sha: str,
    commit: dict,
    note: str | None = None,
) -> None:
    metadata.add(
        run_id,
        sha,
        commit["commit"]["message"].splitlines()[0],
        int(
            calendar.timegm(
                time.strptime(
                    commit["commit"]["committer"]["date"], "%Y-%m-%dT%H:%M:%SZ"
                )
            )
        ),
    )
    run_list.add(run_id, note)


def add_to_index(path: Path, key: str, value: str) -> None:
    if path.exists():
        with path.open("r", encoding="utf-8") as source:
            index = json.load(source)
    else:
        index = {"default": value, key: []}
    if value in index[key]:
        return
    index[key].append(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        json.dump(index, output, indent=2, separators=(",", ": "))


def register_dataset(config: UpdateConfig, root: Path) -> None:
    branch_path = config.branch_path(root)
    branch = branch_path.relative_to(root / config.type_).as_posix()
    subset = config.data_path(root).relative_to(branch_path).as_posix()
    add_to_index(root / config.type_ / "branch.json", "branches", branch)
    add_to_index(branch_path / "subset.json", "subsets", subset)


@dataclass
class UpdateTarget:
    config: UpdateConfig
    metadata: MetadataJson
    run_list: RunListJson


class GithubUpdater:
    """Fetch and store results from completed GitHub workflow runs."""

    def __init__(
        self,
        gh: GitHub,
        config: UpdateConfig | Sequence[UpdateConfig],
        page_limit: int = 3,
        data_root: Path = DATA_PATH,
    ) -> None:
        self.gh = gh
        self.configs = (config,) if isinstance(config, UpdateConfig) else tuple(config)
        if not self.configs:
            raise ValueError("At least one update config is required")
        self.config = self.configs[0]
        if any(config.batch_key != self.config.batch_key for config in self.configs):
            raise ValueError(
                "Batch configs must share repository, branch, event, and discovery"
            )
        self.page_limit = page_limit
        self.data_root = data_root

    def run(self) -> None:
        metadata_by_path: dict[Path, MetadataJson] = {}
        targets: list[UpdateTarget] = []
        for config in self.configs:
            metadata_path = config.branch_path(self.data_root) / "metadata.json"
            if metadata_path not in metadata_by_path:
                metadata_by_path[metadata_path] = MetadataJson.from_json(metadata_path)
            targets.append(
                UpdateTarget(
                    config,
                    metadata_by_path[metadata_path],
                    RunListJson.from_json(
                        config.data_path(self.data_root) / "list.json"
                    ),
                )
            )
        if self.config.discovery == "commits":
            self._update_by_commits(targets)
        else:
            self._update_by_runs(targets)
        saved_metadata: set[Path] = set()
        for target in targets:
            if not target.run_list.runs:
                continue
            config = target.config
            metadata_path = config.branch_path(self.data_root) / "metadata.json"
            if metadata_path not in saved_metadata:
                target.metadata.to_json(metadata_path)
                saved_metadata.add(metadata_path)
            target.run_list.to_json(
                config.data_path(self.data_root) / "list.json"
            )
            register_dataset(config, self.data_root)

    def _get_artifacts(self, run_id: int) -> list[dict]:
        artifacts = []
        for page in count(1):
            result = self.gh.actions.list_workflow_run_artifacts(
                self.config.owner,
                self.config.repo_name,
                run_id,
                page=page,
            )["artifacts"]
            if not result:
                break
            artifacts.extend(result)
        return artifacts

    def _update_by_commits(self, targets: list[UpdateTarget]) -> None:
        pending = targets.copy()
        for page in count(1):
            commits = self.gh.commits.list_commits(
                self.config.owner,
                self.config.repo_name,
                sha=self.config.upstream_branch,
                page=page,
                per_page=10,
            )
            if not commits:
                break

            for commit in commits:
                sha = commit["sha"]
                logging.info("Checking commit %s", sha)
                pending = [
                    target
                    for target in pending
                    if not target.run_list.exists(target.metadata, sha)
                ]
                if not pending:
                    break

                runs = workflow_runs_for_commit(
                    self.gh, self.config, sha, all_workflows=True
                )
                if not runs:
                    logging.info("  -> No workflow related, skip")
                    continue
                artifacts_by_run: dict[int, list[dict]] = {}
                for target in pending:
                    matching = [
                        run for run in runs if run["name"] == target.config.workflow
                    ]
                    if not matching:
                        continue
                    if len(matching) > 1:
                        logging.warning(
                            "  -> Multiple workflow runs found, using the first one"
                        )
                    run = matching[0]
                    if run["conclusion"] != "success":
                        logging.warning("  -> Workflow run failed, skip")
                        continue
                    if run["id"] not in artifacts_by_run:
                        artifacts_by_run[run["id"]] = self._get_artifacts(run["id"])
                    artifacts = artifacts_by_run[run["id"]]
                    self._store_run(target, run, sha, commit, artifacts)

            if not pending or len(commits) < 10 or page >= self.page_limit:
                break

    def _update_by_runs(self, targets: list[UpdateTarget]) -> None:
        pending = targets.copy()
        commits_by_sha: dict[str, dict] = {}
        for page in count(1):
            runs = self.gh.actions.list_workflow_runs(
                self.config.owner,
                self.config.repo_name,
                branch=self.config.upstream_branch,
                event=self.config.event,
                status="completed",
                page=page,
                per_page=10,
            )["workflow_runs"]
            if not runs:
                break

            for run in runs:
                logging.info("Checking workflow run %s", run["id"])
                matching = [
                    target for target in pending if target.config.workflow == run["name"]
                ]
                if not matching:
                    continue
                if run["conclusion"] != "success":
                    logging.warning("  -> Workflow run failed, skip")
                    continue
                sha = run["head_sha"]
                pending = [
                    target
                    for target in pending
                    if target not in matching
                    or not target.run_list.exists(target.metadata, sha)
                ]
                matching = [target for target in matching if target in pending]
                if not pending:
                    break
                if not matching:
                    continue

                if sha not in commits_by_sha:
                    commits_by_sha[sha] = self.gh.commits.get_commit(
                        self.config.owner, self.config.repo_name, sha
                    )
                artifacts = self._get_artifacts(run["id"])
                for target in matching:
                    self._store_run(target, run, sha, commits_by_sha[sha], artifacts)

            if not pending or len(runs) < 10 or page >= self.page_limit:
                break

    def _store_run(
        self,
        target: UpdateTarget,
        run: dict,
        sha: str,
        commit: dict,
        available_artifacts: list[dict],
    ) -> None:
        config = target.config
        if config.type_ == "test":
            artifacts = [
                artifact
                for artifact in available_artifacts
                if artifact["name"].startswith(config.artifact_name)
            ]
        else:
            artifacts = [
                artifact
                for artifact in available_artifacts
                if artifact["name"] == config.artifact_name
            ]
        if not artifacts:
            logging.info("  -> No artifact, skip")
            return
        logging.info("  -> Found %d artifacts", len(artifacts))

        if config.type_ == "test":
            report = ReportTestJson()
            for artifact in artifacts:
                logging.info("  -> Download %s ...", artifact["name"])
                body = self.gh.actions.download_artifact(
                    config.owner, config.repo_name, artifact["id"]
                )
                if isinstance(body, bytes):
                    testcase = artifact["name"][len(config.artifact_name) :]
                    report.append(testcase, float(body.decode("utf-8").strip()))
                elif isinstance(body, ZipFile):
                    extra = [
                        name
                        for name in body.namelist()
                        if not name.startswith(config.artifact_name)
                    ]
                    if extra:
                        logging.warning(
                            "  -> Artifact %s contains non-ipc files: %s, ignore",
                            artifact["name"],
                            extra,
                        )
                    report.append_artifact_zip(body)
                else:
                    logging.warning("    -> unknown file type, ignore")
            note = None
        else:
            report = ReportRegressionJson()
            note = None
            for artifact in artifacts:
                logging.info("  -> Download %s ...", artifact["name"])
                body = self.gh.actions.download_artifact(
                    config.owner, config.repo_name, artifact["id"]
                )
                if isinstance(body, bytes):
                    note = report.append_score_txt(body.decode("utf-8").strip())
                elif isinstance(body, ZipFile):
                    extra = [
                        name
                        for name in body.namelist()
                        if not (name.startswith("score") and name.endswith(".txt"))
                    ]
                    if extra:
                        logging.warning(
                            "  -> Artifact %s contains score files: %s, ignore",
                            artifact["name"],
                            extra,
                        )
                    note = report.append_artifact_zip(body)
                else:
                    logging.warning("    -> unknown file type, ignore")

        append_commit(target.metadata, target.run_list, run["id"], sha, commit, note)
        report.to_json(config.data_path(self.data_root) / f"{sha}.json")


class LocalUpdater:
    """Import local results and fetch their metadata from GitHub."""

    def __init__(
        self,
        gh: GitHub,
        config: UpdateConfig,
        local_path: Path,
        data_root: Path = DATA_PATH,
    ) -> None:
        self.gh = gh
        self.config = config
        self.local_path = local_path
        self.data_root = data_root
        self.data_path = config.data_path(data_root)
        self.branch_path = config.branch_path(data_root)

    def run(self) -> None:
        if self.config.type_ == "test":
            if not self.local_path.is_dir():
                raise ValueError(f"Invalid local data dir: {self.local_path}")
        else:
            if not self.local_path.is_file():
                raise ValueError(f"Invalid local data file: {self.local_path}")
            if self.config.repo == "gem5":
                raise NotImplementedError(
                    "Local update for gem5 regression is not implemented yet"
                )

        metadata = MetadataJson.from_json(self.branch_path / "metadata.json")
        run_list = RunListJson.from_json(self.data_path / "list.json")
        sha = input("Please input the commit hash for this data: ")
        commit = self.gh.commits.get_commit(
            self.config.owner, self.config.repo_name, sha
        )
        runs = workflow_runs_for_commit(self.gh, self.config, sha)
        if not runs:
            logging.info(
                "No workflow run found for this commit; enter the run id manually"
            )
            run_id_text = input(
                "Please input the workflow run id for this data, enter to abort: "
            )
            if not run_id_text:
                logging.info("No workflow run id provided, abort")
                return
            run_id = int(run_id_text)
        else:
            if len(runs) > 1:
                logging.warning("Multiple workflow runs found, using the first one")
            run_id = runs[0]["id"]

        if self.config.type_ == "test":
            report = ReportTestJson()
            for file in self.local_path.iterdir():
                if file.is_file() and file.name.startswith(self.config.artifact_name):
                    logging.info("  -> Processing %s ...", file.name)
                    testcase = file.name[len(self.config.artifact_name) :]
                    with file.open("r", encoding="utf-8") as result:
                        report.append(testcase, float(result.read().strip()))
            note = None
        else:
            report = ReportRegressionJson()
            with self.local_path.open("r", encoding="utf-8") as result:
                note = report.append_score_txt(result.read().strip())

        append_commit(metadata, run_list, run_id, sha, commit, note)
        report.to_json(self.data_path / f"{sha}.json")
        metadata.to_json(self.branch_path / "metadata.json")
        run_list.to_json(self.data_path / "list.json")
        register_dataset(self.config, self.data_root)
