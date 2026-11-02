"""周期性不可变 Shared State Checkpoint。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from apps.enterprise_document.schemas.agent_rag_v1 import (
    MultiAgentSharedStateSchema,
)
from core.persistence.crash_safe_jsonl import canonical_json_sha256


class CheckpointIntegrityError(ValueError):
    pass


class JsonCheckpointStore:
    """按 event_sequence 保存完整快照，事件日志仍是增量历史来源。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def _run_dir(self, task_id: str, run_id: str) -> Path:
        identity = canonical_json_sha256({"task_id": task_id, "run_id": run_id})
        return self.root / identity

    def save(self, state: MultiAgentSharedStateSchema) -> Path:
        payload = state.model_dump(mode="json")
        envelope = {
            "task_id": state.state_meta.task_id,
            "run_id": state.state_meta.run_id,
            "event_sequence": state.state_meta.event_sequence,
            "state_hash": canonical_json_sha256(payload),
            "state": payload,
        }
        serialized = (
            json.dumps(
                envelope,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        run_dir = self._run_dir(
            state.state_meta.task_id,
            state.state_meta.run_id,
        )
        target = run_dir / f"{state.state_meta.event_sequence:020d}.json"
        with self._lock:
            if target.exists():
                existing = target.read_text(encoding="utf-8")
                if existing != serialized:
                    raise CheckpointIntegrityError(
                        "checkpoint sequence already has different state"
                    )
                return target
            run_dir.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_text(serialized, encoding="utf-8")
                with temporary.open("r+b") as handle:
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return target

    def load_latest(
        self,
        task_id: str,
        run_id: str,
    ) -> MultiAgentSharedStateSchema | None:
        run_dir = self._run_dir(task_id, run_id)
        with self._lock:
            candidates = sorted(run_dir.glob("*.json")) if run_dir.exists() else []
            if not candidates:
                return None
            target = candidates[-1]
            try:
                envelope = json.loads(target.read_text(encoding="utf-8"))
                if (
                    envelope.get("task_id") != task_id
                    or envelope.get("run_id") != run_id
                ):
                    raise CheckpointIntegrityError(
                        "checkpoint identity mismatch"
                    )
                payload = envelope["state"]
                if canonical_json_sha256(payload) != envelope.get("state_hash"):
                    raise CheckpointIntegrityError("checkpoint state hash mismatch")
                state = MultiAgentSharedStateSchema.model_validate(payload)
            except CheckpointIntegrityError:
                raise
            except Exception as exc:
                raise CheckpointIntegrityError(
                    f"checkpoint is invalid: {target}"
                ) from exc
        if state.state_meta.event_sequence != envelope["event_sequence"]:
            raise CheckpointIntegrityError("checkpoint event sequence mismatch")
        return state
