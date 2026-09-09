"""Describe report.json structure"""

from dataclasses import dataclass, field, asdict
import json
from pathlib import Path
import re
from zipfile import ZipFile

# report.json: { testcase: { "ipc": ipc } }
# {
#   "testcase1": { "ipc": 1.0 },
#   "testcase2": { "ipc": 0.5 },
#   ...
# }


@dataclass
class ReportJsonEntry:
    """Describe a single entry in report.json"""

    ipc: float | None = None
    score: float | None = None


@dataclass
class ReportJson:
    """Describe the entire report.json structure"""

    _data: dict[str, ReportJsonEntry] = field(default_factory=dict)

    @staticmethod
    def from_json(path: Path) -> "ReportJson":
        """Load data from a JSON file"""
        if not path.exists():
            return ReportJson()

        with open(path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        version = raw_data["version"]

        match version:
            case 1:
                return ReportJson(
                    _data={
                        k: ReportJsonEntry(**v) for k, v in raw_data["data"].items()
                    },
                )
            case _:
                raise ValueError(f"Unsupported data version: {version}")

    def to_json(self, path: Path) -> None:
        """Save data to a JSON file"""
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = {
            testcase: {
                key: value for key, value in asdict(entry).items() if value is not None
            }
            for testcase, entry in self._data.items()
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                serialized,
                f,
                indent=2,
                separators=(",", ": "),
            )


class ReportTestJson(ReportJson):
    """Describe the report.json structure for Performance Test workflow"""

    def append(self, testcase: str, ipc: float) -> None:
        """Append a single testcase to report"""
        self._data[testcase] = ReportJsonEntry(ipc=ipc)

    def append_artifact_zip(self, artifact_zip: ZipFile) -> None:
        """Append multiple testcase from a artifact zip file"""
        for name in artifact_zip.namelist():
            if not name.startswith("ipc-") or name.startswith("ipc-fuzz-"):
                continue
            testcase = name.replace("ipc-", "")
            with artifact_zip.open(name) as f:
                ipc = float(f.read().decode("utf-8").strip())
            self.append(testcase, ipc)


class ReportRegressionJson(ReportJson):
    """Describe the report.json structure for Regression Workflow"""

    def append(self, testcase: str, score: float) -> None:
        """Append a single testcase to report"""
        self._data[testcase] = ReportJsonEntry(score=score)

    def append_score_txt(self, txt: str) -> str | None:
        """Append multiple testcase from a score.txt file"""
        failed = 0
        total = 0
        for line in txt.splitlines():
            # match "id.name time ref_time score coverage"
            m = re.match(
                r"^\s*((?:\d+\.)?\w+)\s+[\d\.NaN]+\s+[\d\.NaN]+\s+([\d\.NaN]+)\s+[\d\.NaN]+",
                line,
            )
            if m:
                testcase = m.group(1)
                score = float(m.group(2))
                self.append(testcase, score)
            # match Checkpoints Number : 545/1094
            m = re.match(r"^\s*Checkpoints Number\s*:\s*(\d+)/(\d+)", line)
            if m:
                success, total = int(m.group(1)), int(m.group(2))
                failed = total - success
        if failed > 0:
            return f"{failed} out of {total} checkpoints failed, score may be inaccurate"
        return None

    def append_artifact_zip(self, artifact_zip: ZipFile) -> str | None:
        """Append multiple testcase from a artifact zip file"""
        results = []
        for name in artifact_zip.namelist():
            if not (name.startswith("score") and name.endswith(".txt")):
                continue
            with artifact_zip.open(name) as f:
                txt = f.read().decode("utf-8").strip()
                if (result := self.append_score_txt(txt)) is not None:
                    results.append(result)
        if results:
            return "\n".join(results)
        return None
