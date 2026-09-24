"""Branch metadata and subset run lists."""

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path


def run_id_sort_key(run_id: str) -> tuple[int, str]:
    return int(run_id.removeprefix("imported-")), run_id


@dataclass(frozen=True)
class MetadataEntry:
    hash: str
    title: str
    date: int


@dataclass
class MetadataJson:
    data: dict[str, MetadataEntry] = field(default_factory=dict)

    @classmethod
    def from_json(cls, path: Path) -> "MetadataJson":
        if not path.exists():
            return cls()
        with path.open("r", encoding="utf-8") as source:
            entries = json.load(source)
        return cls(
            {str(run_id): MetadataEntry(**entry) for run_id, entry in entries.items()}
        )

    def to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(
            self.data.items(), key=lambda item: run_id_sort_key(item[0]), reverse=True
        )
        with path.open("w", encoding="utf-8") as output:
            json.dump(
                {run_id: asdict(entry) for run_id, entry in ordered}, output,
                indent=2, separators=(",", ": "),
            )

    def add(self, run_id: str | int, commit: str, title: str, date: int) -> None:
        key = str(run_id)
        entry = MetadataEntry(commit, title, date)
        if key in self.data and self.data[key] != entry:
            raise ValueError(f"Conflicting metadata for run {key}")
        self.data[key] = entry


@dataclass
class RunListJson:
    runs: list[str] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_json(cls, path: Path) -> "RunListJson":
        if not path.exists():
            return cls()
        with path.open("r", encoding="utf-8") as source:
            raw = json.load(source)
        return cls([str(run_id) for run_id in raw["runs"]], raw.get("notes", {}))

    def to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.runs.sort(key=run_id_sort_key, reverse=True)
        with path.open("w", encoding="utf-8") as output:
            json.dump(
                {"runs": self.runs, "notes": self.notes}, output,
                indent=2, separators=(",", ": "),
            )

    def exists(self, metadata: MetadataJson, commit: str) -> bool:
        return any(metadata.data[run_id].hash == commit for run_id in self.runs)

    def add(self, run_id: str | int, note: str | None = None) -> None:
        key = str(run_id)
        if key not in self.runs:
            self.runs.append(key)
        if note is None:
            self.notes.pop(key, None)
        else:
            self.notes[key] = note
