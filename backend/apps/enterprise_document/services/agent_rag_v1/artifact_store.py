"""不可变、内容哈希校验的本地 Artifact Store。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from apps.enterprise_document.schemas.agent_rag_v1 import ArtifactReferenceSchema


class ArtifactIntegrityError(ValueError):
    pass


class FileArtifactStore:
    """单文件信封原子发布，读取时复核内容与完整身份。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    @staticmethod
    def _content_hash(content: bytes) -> str:
        return "sha256:" + hashlib.sha256(content).hexdigest()

    def _path_for_ref(self, artifact_ref: str) -> Path:
        filename = hashlib.sha256(artifact_ref.encode("utf-8")).hexdigest()
        return self.root / filename[:2] / f"{filename}.json"

    def put_json(
        self,
        value: Any,
        *,
        artifact_type: str,
        schema_version: str,
        created_by: str,
        task_id: str,
        run_id: str | None = None,
        attempt_id: str | None = None,
        input_snapshot_version: str | None = None,
        section_id: str | None = None,
        artifact_ref: str | None = None,
    ) -> ArtifactReferenceSchema:
        content = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self.put_bytes(
            content,
            artifact_type=artifact_type,
            schema_version=schema_version,
            created_by=created_by,
            task_id=task_id,
            run_id=run_id,
            attempt_id=attempt_id,
            input_snapshot_version=input_snapshot_version,
            section_id=section_id,
            artifact_ref=artifact_ref,
            content_encoding="json-utf8",
        )

    def put_bytes(
        self,
        content: bytes,
        *,
        artifact_type: str,
        schema_version: str,
        created_by: str,
        task_id: str,
        run_id: str | None = None,
        attempt_id: str | None = None,
        input_snapshot_version: str | None = None,
        section_id: str | None = None,
        artifact_ref: str | None = None,
        content_encoding: str = "binary",
    ) -> ArtifactReferenceSchema:
        stable_ref = artifact_ref or (
            f"artifact://{task_id}/{artifact_type}/{uuid4().hex}"
        )
        reference = ArtifactReferenceSchema(
            artifact_ref=stable_ref,
            artifact_type=artifact_type,
            content_hash=self._content_hash(content),
            schema_version=schema_version,
            size_bytes=len(content),
            created_by=created_by,
            task_id=task_id,
            run_id=run_id,
            section_id=section_id,
            attempt_id=attempt_id,
            input_snapshot_version=input_snapshot_version,
        )
        envelope = {
            "reference": reference.model_dump(mode="json"),
            "content_encoding": content_encoding,
            "content_base64": base64.b64encode(content).decode("ascii"),
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
        target = self._path_for_ref(stable_ref)
        with self._lock:
            if target.exists():
                existing_ref, existing_content = self._read_envelope(target)
                if existing_ref != reference or existing_content != content:
                    raise ArtifactIntegrityError(
                        "artifact ref already exists with different content"
                    )
                return existing_ref
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_text(serialized, encoding="utf-8")
                with temporary.open("r+b") as handle:
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return reference

    @staticmethod
    def _validate_identity(
        reference: ArtifactReferenceSchema,
        *,
        artifact_ref: str,
        task_id: str,
        run_id: str | None = None,
        section_id: str | None = None,
        attempt_id: str | None = None,
        input_snapshot_version: str | None = None,
    ) -> None:
        if reference.artifact_ref != artifact_ref:
            raise ArtifactIntegrityError("artifact reference identity mismatch")
        if reference.task_id != task_id:
            raise ArtifactIntegrityError("artifact task identity mismatch")
        if run_id is not None and reference.run_id != run_id:
            raise ArtifactIntegrityError("artifact run identity mismatch")
        if section_id is not None and reference.section_id != section_id:
            raise ArtifactIntegrityError("artifact section identity mismatch")
        if attempt_id is not None and reference.attempt_id != attempt_id:
            raise ArtifactIntegrityError("artifact attempt identity mismatch")
        if (
            input_snapshot_version is not None
            and reference.input_snapshot_version != input_snapshot_version
        ):
            raise ArtifactIntegrityError("artifact snapshot identity mismatch")

    def get_bytes_with_reference(
        self,
        artifact_ref: str,
        *,
        task_id: str,
        run_id: str | None = None,
        section_id: str | None = None,
        attempt_id: str | None = None,
        input_snapshot_version: str | None = None,
    ) -> tuple[ArtifactReferenceSchema, bytes]:
        target = self._path_for_ref(artifact_ref)
        if not target.is_file():
            raise FileNotFoundError(f"artifact does not exist: {artifact_ref}")
        with self._lock:
            reference, content = self._read_envelope(target)
        self._validate_identity(
            reference,
            artifact_ref=artifact_ref,
            task_id=task_id,
            run_id=run_id,
            section_id=section_id,
            attempt_id=attempt_id,
            input_snapshot_version=input_snapshot_version,
        )
        return reference, content

    def get_bytes(self, artifact_ref: str, **identity: Any) -> bytes:
        _, content = self.get_bytes_with_reference(artifact_ref, **identity)
        return content

    def get_json_with_reference(
        self,
        artifact_ref: str,
        **identity: Any,
    ) -> tuple[ArtifactReferenceSchema, Any]:
        reference, content = self.get_bytes_with_reference(
            artifact_ref,
            **identity,
        )
        try:
            return reference, json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactIntegrityError("artifact is not valid JSON") from exc

    def get_json(self, artifact_ref: str, **identity: Any) -> Any:
        _, value = self.get_json_with_reference(artifact_ref, **identity)
        return value

    def list_references(self) -> tuple[ArtifactReferenceSchema, ...]:
        """Enumerate immutable envelopes without assigning retention semantics."""

        references: list[ArtifactReferenceSchema] = []
        with self._lock:
            for target in sorted(self.root.rglob("*.json")):
                if not target.is_file() or target.name.startswith("."):
                    continue
                reference, _ = self._read_envelope(target)
                references.append(reference)
        refs = [item.artifact_ref for item in references]
        if len(refs) != len(set(refs)):
            raise ArtifactIntegrityError("artifact store contains duplicate refs")
        return tuple(sorted(references, key=lambda item: item.artifact_ref))

    def delete_exact(
        self,
        artifact_ref: str,
        *,
        expected_content_hash: str,
    ) -> int:
        """Physically delete one exact envelope after integrity revalidation.

        This is a persistence capability only.  Callers must obtain semantic GC
        authorization elsewhere (SIF-13 PLAN -> REVALIDATE -> DELETE).
        """

        target = self._path_for_ref(artifact_ref)
        with self._lock:
            if not target.exists():
                return 0
            reference, _ = self._read_envelope(target)
            if reference.artifact_ref != artifact_ref:
                raise ArtifactIntegrityError("artifact delete ref mismatch")
            if reference.content_hash != str(expected_content_hash):
                raise ArtifactIntegrityError(
                    "artifact changed since semantic GC revalidation"
                )
            bytes_freed = int(target.stat().st_size)
            target.unlink()
            parent = target.parent
            if parent != self.root:
                try:
                    parent.rmdir()
                except OSError:
                    pass
            return bytes_freed

    @classmethod
    def _read_envelope(
        cls,
        target: Path,
    ) -> tuple[ArtifactReferenceSchema, bytes]:
        try:
            envelope = json.loads(target.read_text(encoding="utf-8"))
            reference = ArtifactReferenceSchema.model_validate(
                envelope["reference"]
            )
            content = base64.b64decode(
                envelope["content_base64"],
                validate=True,
            )
        except Exception as exc:
            raise ArtifactIntegrityError(
                f"artifact envelope is invalid: {target}"
            ) from exc
        if reference.content_hash != cls._content_hash(content):
            raise ArtifactIntegrityError("artifact content hash mismatch")
        if reference.size_bytes != len(content):
            raise ArtifactIntegrityError("artifact size mismatch")
        return reference, content
