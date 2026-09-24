"""Update dashboard data from GitHub artifacts or local results."""

import calendar
import json
import logging
import time
from itertools import count
from pathlib import Path
from typing import Callable
from zipfile import ZipFile

from modules.config import UpdateConfig
from modules.github import GitHub
from modules.json import MetadataJson, RunListJson, ReportRegressionJson, ReportTestJson


DATA_PATH = Path(__file__).parent.parent.parent / "data"


def workflow_runs_for_commit(gh: GitHub, config: UpdateConfig, sha: str) -> list[dict]:
    runs = gh.actions.list_workflow_runs(
        config.owner,
        config.repo_name,
        branch=config.upstream_branch,
        event=config.event,
        status="completed",
        head_sha=sha,
    )["workflow_runs"]
    return [run for run in runs if run["name"] == config.workflow]


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


class GithubUpdater:
    """Fetch and store results from completed GitHub workflow runs."""

    def __init__(
        self,
        gh: GitHub,
        config: UpdateConfig,
        page_limit: int = 3,
        data_root: Path = DATA_PATH,
    ) -> None:
        self.gh = gh
        self.config = config
        self.page_limit = page_limit
        self.data_root = data_root
        self.data_path = config.data_path(data_root)
        self.branch_path = config.branch_path(data_root)

    def run(self) -> None:
        metadata = MetadataJson.from_json(self.branch_path / "metadata.json")
        run_list = RunListJson.from_json(self.data_path / "list.json")
        if self.config.discovery == "commits":
            self._update_by_commits(metadata, run_list)
        else:
            self._update_by_runs(metadata, run_list)
        if not run_list.runs:
            return
        metadata.to_json(self.branch_path / "metadata.json")
        run_list.to_json(self.data_path / "list.json")
        register_dataset(self.config, self.data_root)

    def _get_artifacts(
        self, run_id: int, matches: Callable[[dict], bool]
    ) -> list[dict]:
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
            artifacts.extend(artifact for artifact in result if matches(artifact))
        return artifacts

    def _update_by_commits(
        self, metadata: MetadataJson, run_list: RunListJson
    ) -> None:
        found_existing = False
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
                if run_list.exists(metadata, sha):
                    logging.info("  -> Already exists in subset, finish")
                    found_existing = True
                    break

                runs = workflow_runs_for_commit(self.gh, self.config, sha)
                if not runs:
                    logging.info("  -> No workflow related, skip")
                    continue
                if len(runs) > 1:
                    logging.warning(
                        "  -> Multiple workflow runs found, using the first one"
                    )
                run = runs[0]
                if run["conclusion"] != "success":
                    logging.warning("  -> Workflow run failed, skip")
                    continue
                self._store_run(run, sha, commit, metadata, run_list)

            if found_existing or page >= self.page_limit:
                break

    def _update_by_runs(self, metadata: MetadataJson, run_list: RunListJson) -> None:
        found_existing = False
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
                if run["name"] != self.config.workflow:
                    logging.info("  -> Workflow name mismatch, skip")
                    continue
                if run["conclusion"] != "success":
                    logging.warning("  -> Workflow run failed, skip")
                    continue
                sha = run["head_sha"]
                if run_list.exists(metadata, sha):
                    logging.info("  -> Already exists in subset, finish")
                    found_existing = True
                    break

                commit = self.gh.commits.get_commit(
                    self.config.owner, self.config.repo_name, sha
                )
                self._store_run(run, sha, commit, metadata, run_list)

            if found_existing or page >= self.page_limit:
                break

    def _store_run(
        self,
        run: dict,
        sha: str,
        commit: dict,
        metadata: MetadataJson,
        run_list: RunListJson,
    ) -> None:
        if self.config.type_ == "test":
            artifacts = self._get_artifacts(
                run["id"],
                lambda artifact: artifact["name"].startswith(self.config.artifact_name),
            )
        else:
            artifacts = self._get_artifacts(
                run["id"],
                lambda artifact: artifact["name"] == self.config.artifact_name,
            )
        if not artifacts:
            logging.info("  -> No artifact, skip")
            return
        logging.info("  -> Found %d artifacts", len(artifacts))

        if self.config.type_ == "test":
            report = ReportTestJson()
            for artifact in artifacts:
                logging.info("  -> Download %s ...", artifact["name"])
                body = self.gh.actions.download_artifact(
                    self.config.owner, self.config.repo_name, artifact["id"]
                )
                if isinstance(body, bytes):
                    testcase = artifact["name"][len(self.config.artifact_name) :]
                    report.append(testcase, float(body.decode("utf-8").strip()))
                elif isinstance(body, ZipFile):
                    extra = [
                        name
                        for name in body.namelist()
                        if not name.startswith(self.config.artifact_name)
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
                    self.config.owner, self.config.repo_name, artifact["id"]
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

        append_commit(metadata, run_list, run["id"], sha, commit, note)
        report.to_json(self.data_path / f"{sha}.json")


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
