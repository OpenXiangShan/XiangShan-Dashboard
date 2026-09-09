"""Update data from OpenXiangShan/XiangShan"""

import argparse
import calendar
from itertools import count, product
import logging
from pathlib import Path
import time
from typing import Callable, Literal
from zipfile import ZipFile

from modules.json import DataJson, ReportTestJson, ReportRegressionJson
from modules.github import GitHub

OWNER = "OpenXiangShan"
DATA_PATH = Path(__file__).parent.parent / "data"

Repo = Literal["xs", "gem5"]
RegressionTarget = Literal["nightly", "weekly"]
RegressionCompiler = Literal["gcc", "xscc"]

REPO = {
    "xs": "XiangShan",
    "gem5": "GEM5",
}

WORKFLOW_NAMES = {
    "xs": {
        "test": "EMU Performance Test",
        "nightly": "Nightly Regression",
        "weekly": "Weekly Regression",
    },
    "gem5": {
        "weekly": "gem5 Ideal BTB Weekly Performance Test",
    },
}

SCORE_ARTIFACT_NAMES = {
    "xs": {
        "nightly": "score",
        "weekly": {
            "gcc": "score",
            "xscc": "score-xscc",
        },
    },
    "gem5": {
        "weekly": {
            "gcc": "score-spec06-rva23-novec-gcc16-1.0c",
        },
    },
}

BRANCH_NAMES = {
    "xs": {
        "kunminghu-v3": "kunminghu-v3",
    },
    "gem5": {
        "kunminghu-v3": "xs-dev",
    },
}


def get_artifacts(
    gh: GitHub,
    run_id: int,
    repo: Repo,
    filter_func: Callable[[dict], bool] | None = None,
) -> list[dict]:
    """A wrapper to get all artifacts for a workflow run, handling pagination"""
    artifacts = []
    for artifact_page in count(1):
        artifacts_page = gh.actions.list_workflow_run_artifacts(
            OWNER,
            REPO[repo],
            run_id,
            page=artifact_page,
        )["artifacts"]
        if not artifacts_page:
            break
        artifacts.extend(artifacts_page)
    if filter_func:
        artifacts = list(filter(filter_func, artifacts))
    return artifacts


def update_test_gh(gh: GitHub, args: argparse.Namespace) -> None:
    """Update data for the Performance Test workflow"""
    workflow = WORKFLOW_NAMES["xs"]["test"]
    branch = BRANCH_NAMES["xs"][args.branch]
    data_path = DATA_PATH / "test" / args.branch

    data = DataJson.from_json(data_path / "data.json")

    # get latest commit hash from OpenXiangShan/XiangShan
    found_existing = False
    for page in count(1):
        commits = gh.commits.list_commits(
            OWNER, REPO["xs"], sha=branch, page=page, per_page=10
        )
        if not commits:
            break

        for commit in commits:
            logging.info("Checking commit %s", commit["sha"])
            if data.exists(commit["sha"]):
                logging.info("  -> Already exists in dataset, finish")
                found_existing = True
                break

            # get workflow run for this commit
            runs = gh.actions.list_workflow_runs(
                OWNER,
                REPO["xs"],
                event="push",
                status="completed",
                head_sha=commit["sha"],
            )["workflow_runs"]

            runs = list(filter(lambda x: x["name"] == workflow, runs))

            if not runs:
                logging.info("  -> No workflow related, skip")
                continue

            if len(runs) > 1:
                logging.warning(
                    "  -> Multiple workflow runs found (%s), using the first one",
                    str(map(lambda x: x["id"], runs)),
                )

            run = runs[0]

            if run["conclusion"] != "success":
                logging.warning("  -> Workflow run failed, skip")
                continue

            # get artifacts for this workflow run
            artifacts = get_artifacts(
                gh, run["id"], "xs", lambda x: x["name"].startswith("ipc-")
            )

            if len(artifacts) == 0:
                logging.info("  -> No artifact, skip")
                continue

            logging.info("  -> Found %d artifacts", len(artifacts))

            report = ReportTestJson()

            for artifact in artifacts:
                logging.info("  -> Download %s ...", artifact["name"])

                artifact_body = gh.actions.download_artifact(
                    OWNER,
                    REPO["xs"],
                    artifact["id"],
                )

                if isinstance(artifact_body, bytes):
                    logging.debug("    -> is a raw file")
                    testcase = artifact["name"][len("ipc-") :]
                    ipc = float(artifact_body.decode("utf-8").strip())
                    report.append(testcase, ipc)
                elif isinstance(artifact_body, ZipFile):
                    logging.debug("    -> is a zipfile")
                    extra = list(
                        filter(
                            lambda x: not x.startswith("ipc-"), artifact_body.namelist()
                        )
                    )
                    if extra:
                        logging.warning(
                            "  -> Artifact %s contains non-ipc files: %s, ignore",
                            artifact["name"],
                            str(extra),
                        )
                    report.append_artifact_zip(artifact_body)
                else:
                    logging.warning("    -> unknown file type, ignore")
                    continue

            report.to_json(data_path / f"{commit["sha"]}.json")

            data.append(
                run["id"],
                commit["sha"],
                commit["commit"]["message"].splitlines()[0],
                int(
                    calendar.timegm(
                        time.strptime(
                            commit["commit"]["committer"]["date"], "%Y-%m-%dT%H:%M:%SZ"
                        )
                    )
                ),
            )

        if found_existing or page >= args.page_limit:
            break

    data.to_json(data_path / "data.json")


def update_test_local(gh: GitHub, args: argparse.Namespace) -> None:
    """Update data for the Performance Test workflow from local files"""
    if not args.local.is_dir():
        raise ValueError(f"Invalid local data dir: {args.local}")

    workflow = WORKFLOW_NAMES["xs"]["test"]
    data_path = DATA_PATH / "test" / args.branch

    data = DataJson.from_json(data_path / "data.json")

    local_path: Path = args.local
    commit_sha = input("Please input the commit hash for this data: ")

    commit = gh.commits.get_commit(OWNER, REPO["xs"], commit_sha)

    runs = gh.actions.list_workflow_runs(
        OWNER,
        REPO["xs"],
        event="push",
        status="completed",
        head_sha=commit_sha,
    )["workflow_runs"]

    runs = list(filter(lambda x: x["name"] == workflow, runs))

    if not runs:
        logging.info(
            "No success workflow run found for this commit, try manually inputting the workflow run id"
        )
        run_id = input(
            "Please input the workflow run id for this data, enter to abort: "
        )
        if not run_id:
            logging.info("No workflow run id provided, abort")
            return
        run_id = int(run_id)
    else:
        if len(runs) > 1:
            logging.warning(
                "Multiple workflow runs found (%s), using the first one",
                str(map(lambda x: x["id"], runs)),
            )
        run_id = runs[0]["id"]

    report = ReportTestJson()

    for file in local_path.iterdir():
        if file.is_file() and file.name.startswith("ipc-"):
            logging.info("  -> Processing %s ...", file.name)
            testcase = file.name[len("ipc-") :]
            with open(file, "r", encoding="utf-8") as f:
                ipc = float(f.read().strip())
                report.append(testcase, ipc)

    report.to_json(data_path / f"{commit_sha}.json")
    data.append(
        run_id,
        commit_sha,
        commit["commit"]["message"].splitlines()[0],
        int(
            calendar.timegm(
                time.strptime(
                    commit["commit"]["committer"]["date"], "%Y-%m-%dT%H:%M:%SZ"
                )
            )
        ),
    )
    data.to_json(data_path / "data.json")


def update_test(gh: GitHub, args: argparse.Namespace) -> None:
    """Update data for the Performance Test workflow"""
    if args.local:
        update_test_local(gh, args)
    else:
        update_test_gh(gh, args)


def update_regression_gh(
    gh: GitHub,
    args: argparse.Namespace,
    repo: Repo,
    target: RegressionTarget,
    compiler: RegressionCompiler,
) -> None:
    """Update data for the Regression workflow"""
    match target:
        case "nightly":
            workflow = WORKFLOW_NAMES[repo]["nightly"]
            data_path = DATA_PATH / target / args.branch
            score_artifact_name = SCORE_ARTIFACT_NAMES[repo]["nightly"]
        case "weekly":
            workflow = WORKFLOW_NAMES[repo]["weekly"]
            data_path = DATA_PATH / target / args.branch / f"{repo}-{compiler}"
            score_artifact_name = SCORE_ARTIFACT_NAMES[repo]["weekly"][compiler]
        case _:
            raise ValueError(f"Invalid target ({target}) for regression update")

    data = DataJson.from_json(data_path / "data.json")
    branch = BRANCH_NAMES[repo][args.branch]

    # get latest action runs for this workflow
    found_existing = False
    for page in count(1):
        runs = gh.actions.list_workflow_runs(
            OWNER,
            REPO[repo],
            branch=branch,
            event="schedule",
            status="completed",
            page=page,
            per_page=10,
        )["workflow_runs"]
        if not runs:
            break

        for run in runs:
            logging.info("Checking workflow run %s", run["id"])

            if run["name"] != workflow:
                logging.info("  -> Workflow name mismatch, skip")
                continue

            if run["conclusion"] != "success":
                logging.warning("  -> Workflow run failed, skip")
                continue

            if data.exists(run["head_sha"]):
                logging.info("  -> Already exists in dataset, finish")
                found_existing = True
                break

            commit = gh.commits.get_commit(OWNER, REPO[repo], run["head_sha"])

            artifacts = get_artifacts(
                gh, run["id"], repo, lambda x: x["name"] == score_artifact_name
            )

            if len(artifacts) == 0:
                logging.info("  -> No artifact, skip")
                continue

            logging.info("  -> Found %d artifacts", len(artifacts))

            report = ReportRegressionJson()
            note = None

            for artifact in artifacts:
                logging.info("  -> Download %s ...", artifact["name"])

                artifact_body = gh.actions.download_artifact(
                    OWNER,
                    REPO[repo],
                    artifact["id"],
                )

                if isinstance(artifact_body, bytes):
                    logging.debug("    -> is a raw file")
                    txt = artifact_body.decode("utf-8").strip()
                    note = report.append_score_txt(txt)
                elif isinstance(artifact_body, ZipFile):
                    logging.debug("    -> is a zipfile")
                    extra = list(
                        filter(
                            lambda x: not (
                                x.startswith("score") and x.endswith(".txt")
                            ),
                            artifact_body.namelist(),
                        )
                    )
                    if extra:
                        logging.warning(
                            "  -> Artifact %s contains score files: %s, ignore",
                            artifact["name"],
                            str(extra),
                        )
                    note = report.append_artifact_zip(artifact_body)
                else:
                    logging.warning("    -> unknown file type, ignore")
                    continue

            report.to_json(data_path / f"{run["head_sha"]}.json")

            data.append(
                run["id"],
                run["head_sha"],
                commit["commit"]["message"].splitlines()[0],
                int(
                    calendar.timegm(
                        time.strptime(
                            commit["commit"]["committer"]["date"], "%Y-%m-%dT%H:%M:%SZ"
                        )
                    )
                ),
                note,
            )

        if found_existing or page >= args.page_limit:
            break

    data.to_json(data_path / "data.json")


def update_regression_local(
    gh: GitHub,
    args: argparse.Namespace,
    repo: Repo,
    target: RegressionTarget,
    compiler: RegressionCompiler,
) -> None:
    """Update data for the Regression workflow from local files"""
    if not args.local.is_file():
        raise ValueError(f"Invalid local data file: {args.local}")

    if not repo == "xs":
        raise NotImplementedError(
            "Local update for gem5 regression is not implemented yet"
        )

    match target:
        case "nightly":
            workflow = WORKFLOW_NAMES[repo]["nightly"]
            data_path = DATA_PATH / target / args.branch
        case "weekly":
            workflow = WORKFLOW_NAMES[repo]["weekly"]
            data_path = DATA_PATH / target / args.branch / f"{repo}-{compiler}"
        case _:
            raise ValueError(f"Invalid target ({target}) for regression update")

    data = DataJson.from_json(data_path / "data.json")

    commit_sha = input("Please input the commit hash for this data: ")

    commit = gh.commits.get_commit(OWNER, REPO[repo], commit_sha)

    runs = gh.actions.list_workflow_runs(
        OWNER,
        REPO[repo],
        event="schedule",
        status="completed",
        head_sha=commit_sha,
    )["workflow_runs"]

    runs = list(filter(lambda x: x["name"] == workflow, runs))

    if not runs:
        logging.info(
            "No success workflow run found for this commit, try manually inputting the workflow run id"
        )
        run_id = input(
            "Please input the workflow run id for this data, enter to abort: "
        )
        if not run_id:
            logging.info("No workflow run id provided, abort")
            return
        run_id = int(run_id)
    else:
        if len(runs) > 1:
            logging.warning(
                "Multiple workflow runs found (%s), using the first one",
                str(map(lambda x: x["id"], runs)),
            )
        run_id = runs[0]["id"]

    report = ReportRegressionJson()

    with args.local.open("r", encoding="utf-8") as f:
        txt = f.read().strip()

    note = report.append_score_txt(txt)
    report.to_json(data_path / f"{commit_sha}.json")
    data.append(
        run_id,
        commit_sha,
        commit["commit"]["message"].splitlines()[0],
        int(
            calendar.timegm(
                time.strptime(
                    commit["commit"]["committer"]["date"], "%Y-%m-%dT%H:%M:%SZ"
                )
            )
        ),
        note,
    )
    data.to_json(data_path / "data.json")


def update_regression(
    gh: GitHub,
    args: argparse.Namespace,
    repo: Repo,
    target: RegressionTarget,
    compiler: RegressionCompiler = "gcc",
) -> None:
    """Update data for the Regression workflow"""
    if args.local:
        update_regression_local(gh, args, repo, target, compiler)
    else:
        update_regression_gh(gh, args, repo, target, compiler)


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Update data from OpenXiangShan/XiangShan"
    )
    parser.add_argument("--token", help="GitHub personal access token", required=True)
    parser.add_argument(
        "--logging-level",
        help="Logging level",
        default="INFO",
    )
    parser.add_argument(
        "--page-limit",
        help="Search commit page limit",
        type=int,
        default=3,
    )
    parser.add_argument(
        "--branch",
        help="Branch to check for commits",
        default="kunminghu-v3",
    )
    parser.add_argument(
        "--repo",
        help="Repository to update [xs/gem5]",
        nargs="+",
        choices=["xs", "gem5"],
        default=["xs", "gem5"],
    )
    parser.add_argument(
        "--compiler",
        help="Weekly regression compiler [gcc/xscc], ignored for test and nightly regression",
        nargs="+",
        choices=["gcc", "xscc"],
        default=["gcc", "xscc"],
    )
    parser.add_argument(
        "--target",
        help="Target workflow to update [test/nightly/weekly]",
        nargs="+",
        choices=["test", "nightly", "weekly"],
        default=["test", "nightly", "weekly"],
    )
    parser.add_argument(
        "--local",
        help="Path to local data dir, if specified, will update data from local files",
        type=Path,
    )
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.logging_level))

    gh = GitHub(args.token)

    if args.local:
        logging.info("Updating data from local files in %s", args.local)
        if len(args.target) != 1:
            raise ValueError(
                "When --local is specified, only one target can be updated"
            )
        if len(args.compiler) != 1 and "weekly" in args.target:
            raise ValueError(
                "When --local is specified, only one compiler can be updated for weekly regression"
            )
    else:
        logging.info("Updating data from GitHub Artifacts")

    if "test" in args.target:
        logging.info("Updating Performance Test workflow")
        update_test(gh, args)

    if "nightly" in args.target:
        for repo in args.repo:
            if repo == "gem5":
                logging.warning(
                    "Skipping %s Nightly Regression workflow, only xs is supported",
                    repo,
                )
                continue
            logging.info("Updating %s Nightly Regression workflow", repo)
            update_regression(gh, args, repo, "nightly")

    if "weekly" in args.target:
        for repo, compiler in product(args.repo, args.compiler):
            if repo == "gem5" and compiler != "gcc":
                logging.warning(
                    "Skipping %s Weekly Regression workflow for compiler %s, only gcc is supported",
                    repo,
                    compiler,
                )
                continue
            logging.info(
                "Updating %s Weekly Regression workflow for compiler %s", repo, compiler
            )
            update_regression(gh, args, repo, "weekly", compiler)


if __name__ == "__main__":
    main()
