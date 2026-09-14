"""Describe data.json structure"""

from dataclasses import dataclass, asdict
import json
from pathlib import Path


@dataclass
class DataJsonEntry:
    """Describe a single entry in data.json"""

    hash: str
    title: str = ""
    date: int = 0
    note: str | None = None


CURRENT_VERSION = 1


@dataclass
class DataJson:
    """Describe the entire data.json structure"""

    data: dict[str, DataJsonEntry]
    version: int = CURRENT_VERSION

    @staticmethod
    def from_json(path: Path) -> "DataJson":
        """Load data from a JSON file"""
        if not path.exists():
            return DataJson(data={}, version=CURRENT_VERSION)

        with open(path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        version = raw_data["version"]

        match version:
            case 1:
                return DataJson(
                    version=version,
                    data={
                        str(k): DataJsonEntry(**v) for k, v in raw_data["data"].items()
                    },
                )
            case _:
                raise ValueError(f"Unsupported data version: {version}")

    def to_json(self, path: Path) -> None:
        """Save data to a JSON file"""
        self.sort()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                asdict(
                    self, dict_factory=lambda x: {k: v for k, v in x if v is not None}
                ),
                f,
                indent=2,
                separators=(",", ": "),
            )

    def exists(self, commit: str) -> bool:
        """Check if a commit hash exists in the dataset"""
        return commit in map(lambda entry: entry.hash, self.data.values())

    def append(
        self,
        run_id: str | int,
        commit: str,
        title: str,
        date: int,
        note: str | None = None,
    ) -> None:
        """Append a single workflow run to dataset"""
        self.data[str(run_id)] = DataJsonEntry(
            hash=commit, title=title, date=date, note=note
        )

    def sort(self) -> None:
        """Sort data by run_id descending"""

        def sort_key(item: tuple[str, DataJsonEntry]) -> tuple[int, str]:
            run_id = item[0]
            if run_id.startswith("imported-"):
                run_id = run_id[len("imported-") :]
            return (int(run_id), item[0])

        self.data = dict(
            sorted(self.data.items(), key=sort_key, reverse=True)
        )
