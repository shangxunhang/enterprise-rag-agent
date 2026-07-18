"""Filesystem paths associated with one runtime execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunWorkspace:
    output_root: Path
    task_id: str
    run_id: str

    @property
    def tasks_dir(self) -> Path:
        return self.output_root / "tasks"

    @property
    def runs_dir(self) -> Path:
        return self.output_root / "runs"

    @property
    def captures_dir(self) -> Path:
        return self.output_root / "captures"

    @property
    def task_state_path(self) -> Path:
        return self.tasks_dir / f"{self.task_id}_state.jsonl"

    @property
    def trace_path(self) -> Path:
        return self.runs_dir / f"{self.run_id}_trace.jsonl"

    @property
    def raw_capture_path(self) -> Path:
        return (
            self.captures_dir
            / "raw_interactions"
            / f"{self.run_id}_raw_interactions.jsonl"
        )

    @property
    def sft_capture_path(self) -> Path:
        return (
            self.captures_dir
            / "sft_candidates"
            / f"{self.run_id}_sft_candidates.jsonl"
        )

    @property
    def eval_capture_path(self) -> Path:
        return (
            self.captures_dir
            / "eval_samples"
            / f"{self.run_id}_eval_samples.jsonl"
        )

    def clean(self) -> None:
        for path in (
            self.trace_path,
            self.raw_capture_path,
            self.sft_capture_path,
            self.eval_capture_path,
            self.task_state_path,
        ):
            if path.exists():
                path.unlink()

    def paths(self) -> dict[str, str]:
        return {
            "trace": str(self.trace_path),
            "task_state": str(self.task_state_path),
            "raw_interactions": str(self.raw_capture_path),
            "sft_candidates": str(self.sft_capture_path),
            "eval_samples": str(self.eval_capture_path),
        }
