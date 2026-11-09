"""Conservative path-based architecture change labels."""

from __future__ import annotations

from typing import Iterable


LABEL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Prefetch", ("prefetch", "stride", "stream")),
    ("Cache/MSHR", ("cache", "mshr", "missqueue", "miss_queue", "l2", "l3")),
    ("Issue/ROB", ("issue", "issuequeue", "issue_queue", "rob", "reservation")),
    ("LoadQueue/Replay", ("loadqueue", "load_queue", "replay", "storequeue", "store_queue")),
    ("TLB/PTW", ("tlb", "ptw", "pagetable", "page_table", "translation")),
    ("Branch/Frontend", ("bpu", "branch", "frontend", "front_end", "fetch", "ifu")),
    ("MemoryOrdering", ("memoryorder", "memory_order", "lsq", "ordering")),
)


def classify_paths(paths: Iterable[str]) -> dict[str, object]:
    """Return deterministic labels and evidence for changed repository paths."""

    normalized = sorted({str(path).replace("\\", "/") for path in paths})
    label_hits: set[str] = set()
    evidence: dict[str, list[str]] = {}
    for path in normalized:
        lowered = path.lower()
        matched = False
        for label, tokens in LABEL_RULES:
            if any(token in lowered for token in tokens):
                label_hits.add(label)
                evidence.setdefault(label, []).append(path)
                matched = True
        if not matched:
            label_hits.add("Other")
            evidence.setdefault("Other", []).append(path)
    if not label_hits:
        label_hits.add("Other")
        evidence["Other"] = []
    ordered_labels = [label for label, _ in LABEL_RULES if label in label_hits]
    if "Other" in label_hits:
        ordered_labels.append("Other")
    return {
        "labels": ordered_labels,
        "modules": normalized,
        "source": "path_heuristic",
        "confidence": 0.6 if normalized else 0.0,
        "evidence": evidence,
    }
