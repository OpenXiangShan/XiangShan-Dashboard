"""Supported dashboard update configurations."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_BRANCH = "kunminghu-v3"


@dataclass(frozen=True)
class UpdateConfig:
    """Map a dashboard dataset to its upstream workflow and artifact."""

    type_: Literal["test", "nightly", "weekly"]
    repo: str
    repo_name: str
    workflow: str
    artifact_name: str
    discovery: Literal["commits", "runs"] = "runs"
    event: Literal["push", "schedule"] = "schedule"
    branch: str = DEFAULT_BRANCH
    upstream_branch: str = DEFAULT_BRANCH
    owner: str = "OpenXiangShan"
    compiler: str | None = None
    spec: str | None = None

    def __post_init__(self) -> None:
        if self.type_ not in ("test", "nightly", "weekly"):
            raise ValueError(f"Unsupported update type: {self.type_}")
        if self.discovery not in ("commits", "runs"):
            raise ValueError(f"Unsupported discovery: {self.discovery}")
        if self.event not in ("push", "schedule"):
            raise ValueError(f"Unsupported workflow event: {self.event}")
        if self.type_ == "test" and (
            self.compiler is not None or self.spec is not None
        ):
            raise ValueError("Test updates do not have a compiler or spec")
        if self.type_ != "test" and (self.compiler is None or self.spec is None):
            raise ValueError("Regression updates require a compiler and spec")

    @property
    def id(self) -> str:
        parts = [self.type_, self.repo]
        if self.compiler is not None:
            parts.append(self.compiler)
        if self.spec is not None:
            parts.append(self.spec)
        if self.branch != DEFAULT_BRANCH:
            parts.append(self.branch)
        return "-".join(parts)

    def data_path(self, root: Path) -> Path:
        if self.type_ == "test":
            return self.branch_path(root) / "ipc"
        assert self.spec is not None and self.compiler is not None
        return self.branch_path(root) / self.spec / self.compiler

    def branch_path(self, root: Path) -> Path:
        if self.type_ == "test":
            return root / self.type_ / self.branch
        return root / self.type_ / self.repo / self.branch


CONFIGS = (
    UpdateConfig(
        "test",
        "xs",
        "XiangShan",
        "EMU Performance Test",
        "ipc-",
        discovery="commits",
        event="push",
    ),
    UpdateConfig(
        "nightly",
        "xs",
        "XiangShan",
        "Nightly Regression",
        "score",
        compiler="gcc",
        spec="spec06",
    ),
    UpdateConfig(
        "nightly",
        "gem5",
        "GEM5",
        "gem5 Align BTB Performance Test(0.3c)",
        "score-spec06-rva23-novec-gcc16-0.3c",
        discovery="commits",
        event="push",
        upstream_branch="xs-dev",
        compiler="gcc",
        spec="spec06",
    ),
    UpdateConfig(
        "nightly",
        "gem5",
        "GEM5",
        "gem5 Ideal BTB Performance Test",
        "score-spec06-rva23-novec-gcc16-0.3c",
        discovery="commits",
        event="push",
        branch=f"{DEFAULT_BRANCH}-ideal",
        upstream_branch="xs-dev",
        compiler="gcc",
        spec="spec06",
    ),
    UpdateConfig(
        "weekly",
        "xs",
        "XiangShan",
        "Weekly Regression",
        "score",
        compiler="gcc",
        spec="spec06",
    ),
    UpdateConfig(
        "weekly",
        "xs",
        "XiangShan",
        "Weekly Regression",
        "score-spec17",
        compiler="gcc",
        spec="spec17",
    ),
    UpdateConfig(
        "weekly",
        "xs",
        "XiangShan",
        "Weekly Regression",
        "score-xscc",
        compiler="xscc",
        spec="spec06",
    ),
    UpdateConfig(
        "weekly",
        "gem5",
        "GEM5",
        "gem5 Ideal BTB Weekly Performance Test",
        "score-spec06-rva23-novec-gcc16-1.0c",
        upstream_branch="xs-dev",
        compiler="gcc",
        spec="spec06",
    ),
    UpdateConfig(
        "weekly",
        "gem5",
        "GEM5",
        "gem5 Ideal BTB Weekly Performance Test",
        "score-spec17-1.0c",
        upstream_branch="xs-dev",
        compiler="gcc",
        spec="spec17",
    ),
    UpdateConfig(
        "weekly",
        "gem5",
        "GEM5",
        "gem5 Ideal BTB Weekly Performance Test",
        "score-gcc15-spec26-1.0c",
        upstream_branch="xs-dev",
        compiler="gcc",
        spec="spec26",
    ),
    UpdateConfig(
        "weekly",
        "gem5",
        "GEM5",
        "gem5 Ideal BTB Weekly Performance Test",
        "score-ideal-spec06-rva23-novec-gcc16-1.0c",
        branch=f"{DEFAULT_BRANCH}-ideal",
        upstream_branch="xs-dev",
        compiler="gcc",
        spec="spec06",
    ),
    UpdateConfig(
        "weekly",
        "gem5",
        "GEM5",
        "gem5 Ideal BTB Weekly Performance Test",
        "score-ideal-spec17-1.0c",
        branch=f"{DEFAULT_BRANCH}-ideal",
        upstream_branch="xs-dev",
        compiler="gcc",
        spec="spec17",
    ),
    UpdateConfig(
        "weekly",
        "gem5",
        "GEM5",
        "gem5 Ideal BTB Weekly Performance Test",
        "score-ideal-gcc15-spec26-1.0c",
        branch=f"{DEFAULT_BRANCH}-ideal",
        upstream_branch="xs-dev",
        compiler="gcc",
        spec="spec26",
    ),
    UpdateConfig(
        "weekly",
        "gem5",
        "GEM5",
        "gem5 Ideal BTB Weekly Performance Test",
        "score-smt-ideal-gcc12-spec06-smt-1.0c",
        branch=f"{DEFAULT_BRANCH}-smt",
        upstream_branch="xs-dev",
        compiler="gcc",
        spec="spec06",
    ),
    UpdateConfig(
        "weekly",
        "gem5",
        "GEM5",
        "gem5 Ideal BTB Weekly Performance Test",
        "score-ideal-gcc12-spec06-1.0c",
        branch=f"{DEFAULT_BRANCH}-smt-base",
        upstream_branch="xs-dev",
        compiler="gcc",
        spec="spec06",
    ),
)

CONFIG_BY_ID = {config.id: config for config in CONFIGS}
if len(CONFIG_BY_ID) != len(CONFIGS):
    raise ValueError("Duplicate update configuration ID")
