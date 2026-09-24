"""Migrate legacy dataset indexes into branch metadata and subset lists."""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import shutil

from modules.json import MetadataJson, RunListJson


DATA_PATH = Path(__file__).parent.parent / "data"


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def write_json(path: Path, value: dict) -> None:
    with path.open("w", encoding="utf-8") as output:
        json.dump(value, output, indent=2, separators=(",", ": "))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate_legacy(root: Path) -> None:
    metadata_by_branch: dict[Path, MetadataJson] = defaultdict(MetadataJson)
    lists: dict[Path, RunListJson] = {}
    subset_indexes: dict[Path, dict] = {}
    branch_indexes: dict[Path, dict] = {}
    reports: dict[Path, Path] = {}
    old_branches: list[Path] = []

    for target in ("test", "nightly", "weekly"):
        target_path = root / target
        old_index = read_json(target_path / "branch.json")
        branches = []
        for branch in old_index["branches"]:
            old_branch = target_path / branch
            if not old_branch.is_dir():
                raise ValueError(f"Missing legacy branch: {old_branch}")
            old_branches.append(old_branch)
            old_config = (
                {"default": "ipc", "subsets": ["ipc"]}
                if target == "test"
                else read_json(old_branch / "subset.json")
            )
            old_subsets = old_config["subsets"]
            old_default = old_config["default"]
            imported_ceiling: dict[str, int] = defaultdict(int)
            for old_subset in old_subsets:
                repo = "xs" if target == "test" else old_subset.split("-", 1)[0]
                source = old_branch if target == "test" else old_branch / old_subset
                for run_id in read_json(source / "data.json")["data"]:
                    if run_id.startswith("imported-"):
                        imported_ceiling[repo] = max(
                            imported_ceiling[repo],
                            int(run_id.removeprefix("imported-")),
                        )
            for old_subset in old_subsets:
                repo = "xs" if target == "test" else old_subset.split("-", 1)[0]
                subset = "ipc" if target == "test" else old_subset[len(repo) + 1 :]
                branch_path = target_path / f"{repo}-{branch}"
                subset_path = branch_path / subset
                old_subset_path = (
                    old_branch if target == "test" else old_branch / old_subset
                )
                if branch_path not in branches:
                    branches.append(branch_path)
                subset_index = subset_indexes.setdefault(
                    branch_path, {"default": subset, "subsets": []}
                )
                subset_index["subsets"].append(subset)
                if old_subset == old_default:
                    subset_index["default"] = subset

                old_data = read_json(old_subset_path / "data.json")
                if old_data.get("version") != 1:
                    raise ValueError(f"Unsupported legacy index: {old_subset_path}")
                metadata = metadata_by_branch[branch_path]
                run_list = RunListJson()
                lists[subset_path] = run_list
                for old_run_id, entry in old_data["data"].items():
                    report = old_subset_path / f"{entry['hash']}.json"
                    if not report.is_file():
                        raise ValueError(f"Missing report: {report}")
                    run_id = str(old_run_id)
                    core = (entry["hash"], entry["title"], entry["date"])
                    existing = metadata.data.get(run_id)
                    if existing and (
                        existing.hash, existing.title, existing.date
                    ) != core:
                        if not run_id.startswith("imported-"):
                            raise ValueError(
                                f"Conflicting run ID: {run_id} in {branch_path}"
                            )
                        next_id = imported_ceiling[repo] + int(
                            run_id.removeprefix("imported-")
                        )
                        while f"imported-{next_id}" in metadata.data:
                            next_id += imported_ceiling[repo]
                        run_id = f"imported-{next_id}"
                    metadata.add(run_id, *core)
                    run_list.add(run_id, entry.get("note"))

                for report in old_subset_path.glob("*.json"):
                    if report.name == "data.json":
                        continue
                    destination = subset_path / report.name
                    if destination in reports and reports[destination] != report:
                        raise ValueError(f"Duplicate destination: {destination}")
                    reports[destination] = report

        names = [path.name for path in branches]
        preferred = f"xs-{old_index['default']}"
        branch_indexes[target_path] = {
            "default": preferred if preferred in names else names[0],
            "branches": names,
        }

    destinations = (
        list(reports)
        + [path / "metadata.json" for path in metadata_by_branch]
        + [path / "subset.json" for path in subset_indexes]
        + [path / "list.json" for path in lists]
    )
    if any(path.exists() for path in destinations):
        raise ValueError("Migration destination already exists")

    for destination, source in reports.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if digest(source) != digest(destination):
            raise ValueError(f"Report copy failed: {source}")
    for path, metadata in metadata_by_branch.items():
        metadata.to_json(path / "metadata.json")
        write_json(path / "subset.json", subset_indexes[path])
    for path, run_list in lists.items():
        run_list.to_json(path / "list.json")

    for path, metadata in metadata_by_branch.items():
        if MetadataJson.from_json(path / "metadata.json") != metadata:
            raise ValueError(f"Metadata verification failed: {path}")
    for path, run_list in lists.items():
        saved = RunListJson.from_json(path / "list.json")
        if set(saved.runs) != set(run_list.runs) or saved.notes != run_list.notes:
            raise ValueError(f"Run list verification failed: {path}")
        metadata = metadata_by_branch[path.parent]
        if any(run_id not in metadata.data for run_id in saved.runs):
            raise ValueError(f"Missing metadata for {path}")

    for old_branch in old_branches:
        shutil.rmtree(old_branch)
    for target_path, index in branch_indexes.items():
        write_json(target_path / "branch.json", index)
    print(f"Migrated {len(reports)} reports into {len(metadata_by_branch)} branches")


def migrate_layout(root: Path) -> None:
    moves: list[tuple[Path, Path, dict, list[tuple[str, str]]]] = []
    branch_indexes: dict[Path, dict] = {}
    for target in ("test", "nightly", "weekly"):
        target_path = root / target
        current = read_json(target_path / "branch.json")
        if all("/" in branch for branch in current["branches"]):
            continue
        if target == "test" and all(
            not branch.startswith("xs-")
            and (target_path / branch / "metadata.json").is_file()
            for branch in current["branches"]
        ):
            continue
        branch_names = []
        for old_name in current["branches"]:
            repo = next(
                (name for name in ("xs", "gem5") if old_name.startswith(f"{name}-")),
                None,
            )
            if repo is None:
                raise ValueError(f"Unknown repository in branch: {old_name}")
            branch = old_name[len(repo) + 1 :]
            source = target_path / old_name
            destination = (
                target_path / branch if target == "test" else target_path / repo / branch
            )
            if not source.is_dir() or destination.exists():
                raise ValueError(f"Invalid branch migration: {source} -> {destination}")
            current_subsets = read_json(source / "subset.json")
            subset_names = []
            subset_moves = []
            for old_subset in current_subsets["subsets"]:
                if old_subset == "ipc":
                    new_subset = "ipc"
                else:
                    compiler, spec = old_subset.rsplit("-", 1)
                    new_subset = f"{spec}/{compiler}"
                if not (source / old_subset).is_dir():
                    raise ValueError(f"Missing subset: {source / old_subset}")
                subset_names.append(new_subset)
                subset_moves.append((old_subset, new_subset))
            default_subset = dict(subset_moves)[current_subsets["default"]]
            moves.append(
                (source, destination, {"default": default_subset, "subsets": subset_names}, subset_moves)
            )
            branch_names.append(branch if target == "test" else f"{repo}/{branch}")
        branch_indexes[target_path] = {
            "default": dict(zip(current["branches"], branch_names))[current["default"]],
            "branches": branch_names,
        }

    for source, destination, subset_index, subset_moves in moves:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        for old_subset, new_subset in subset_moves:
            if old_subset != new_subset:
                new_path = destination / new_subset
                new_path.parent.mkdir(parents=True, exist_ok=True)
                (destination / old_subset).rename(new_path)
        write_json(destination / "subset.json", subset_index)
    for target_path, branch_index in branch_indexes.items():
        write_json(target_path / "branch.json", branch_index)
    print(f"Split {len(moves)} branches into repository and version directories")


def migrate_test_layout(root: Path) -> None:
    test_path = root / "test"
    current = read_json(test_path / "branch.json")
    if not any(branch.startswith("xs/") for branch in current["branches"]):
        return
    if not all(branch.startswith("xs/") for branch in current["branches"]):
        raise ValueError("Mixed test branch layouts")
    for old_branch in current["branches"]:
        branch = old_branch.removeprefix("xs/")
        source = test_path / old_branch
        destination = test_path / branch
        if not (source / "metadata.json").is_file() or destination.exists():
            raise ValueError(f"Invalid test branch migration: {source} -> {destination}")
    for old_branch in current["branches"]:
        source = test_path / old_branch
        source.rename(test_path / old_branch.removeprefix("xs/"))
    repo_path = test_path / "xs"
    if not any(repo_path.iterdir()):
        repo_path.rmdir()
    write_json(
        test_path / "branch.json",
        {
            "default": current["default"].removeprefix("xs/"),
            "branches": [branch.removeprefix("xs/") for branch in current["branches"]],
        },
    )
    print("Removed redundant repository directory from test branches")


def migrate(root: Path) -> None:
    branch = read_json(root / "test" / "branch.json")["default"]
    if (root / "test" / branch / "data.json").exists():
        migrate_legacy(root)
    migrate_layout(root)
    migrate_test_layout(root)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_PATH)
    args = parser.parse_args()
    migrate(args.data_root)


if __name__ == "__main__":
    main()
