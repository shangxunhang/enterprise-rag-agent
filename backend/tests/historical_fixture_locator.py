"""Read-only Attempt4 resource locator using the frozen Baseline Closure records."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path, PureWindowsPath
import stat


DESIGN_RELATIVE_PATH = Path("docs/post_r13_business_improvements_audits/LOCAL_VERIFIER_RECOVERY_DESIGN_20261004_01a10514")
FIXTURE_RELATIVE_PATH = DESIGN_RELATIVE_PATH / "readonly_counterexample_copy"
CLOSURE_RECEIPT_RELATIVE_PATH = Path("docs/git_baseline_closure_20261005/MECHANICAL_RECEIPT.json")
CLOSURE_RECEIPT_SHA256 = "688fb6c058ab7dd59111fb3cc6b7a26def8d340ba793bb07d18db829f7a1359b"
MOVE_RECEIPT_SHA256 = "6aec65078903cc2172751f773f0ea4f7d7cd3642f8650eb3b370f908bf396294"
MANIFEST_SHA256 = "eb1a41a897fb786b4a453cca17437ca7676ddf628c0357aa8ba12420ef3c28b4"
SOURCE_MIRROR_ROOT_ENV = "RAG_AGENT_BASELINE_SOURCE_MIRROR_ROOT"
EXPECTED_FILENAMES = frozenset({
    "candidate_exact_artifact_envelope.json", "EXACT_SOURCE_GUARD_OWNER_COUNTEREXAMPLE.json",
    "input_exact_artifact_envelope.json", "m6_binding_facts_exact_event.jsonl",
    "m6_claim_records_exact_event.jsonl", "request_exact_observed_record.jsonl",
    "response_exact_observed_record.jsonl", "shared_support_exact_event.jsonl",
})


class HistoricalFixtureError(RuntimeError):
    """A required immutable test resource could not be admitted."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def _plain_path(path: Path, *, directory: bool = False) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise HistoricalFixtureError("PATH_UNSAFE", str(path))
    # Check the full chain, including junctions above the configured root.
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise HistoricalFixtureError("PATH_UNSAFE", f"link/reparse point: {component}")
    if (directory and not path.is_dir()) or (not directory and not path.is_file()):
        raise HistoricalFixtureError("RESOURCE_MISSING", str(path))
    return path


def _verified_bytes(path: Path, expected_sha256: str) -> bytes:
    raw = _plain_path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise HistoricalFixtureError("HASH_MISMATCH", str(path))
    return raw


def resolve_attempt4_fixture(repo_root: Path | None = None) -> Path:
    """Return the original archive only after all metadata and eight files verify.

    The optional environment setting relocates the physical mirror; the pinned
    move receipt still binds the original canonical migration. No fallback or
    candidate search is performed and this function never writes any resource.
    """
    try:
        repo = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
        _plain_path(repo, directory=True)
        closure = json.loads(_verified_bytes(repo / CLOSURE_RECEIPT_RELATIVE_PATH, CLOSURE_RECEIPT_SHA256))
        declared = PureWindowsPath(closure["external_evidence"]["source_mirror_archive"])
        if not declared.is_absolute() or ".." in declared.parts:
            raise HistoricalFixtureError("PATH_UNSAFE", "invalid declared mirror root")
        configured = os.environ.get(SOURCE_MIRROR_ROOT_ENV)
        if configured is not None and not configured.strip():
            raise HistoricalFixtureError("RESOURCE_MISSING", f"empty {SOURCE_MIRROR_ROOT_ENV}")
        mirror = _plain_path(Path(configured if configured is not None else str(declared)), directory=True)
        move_raw = _verified_bytes(mirror / "_closure_receipt/move_receipt.csv", MOVE_RECEIPT_SHA256)
        rows = list(csv.DictReader(io.StringIO(move_raw.decode("utf-8-sig"))))
        matches = [row for row in rows if row["RelativeRoot"] == FIXTURE_RELATIVE_PATH.as_posix()]
        if len(matches) != 1:
            code = "AMBIGUOUS_RESOURCE" if matches else "RESOURCE_MISSING"
            raise HistoricalFixtureError(code, f"expected one exact move row, found {len(matches)}")
        row = matches[0]
        source, archive = PureWindowsPath(row["Source"]), PureWindowsPath(row["Archive"])
        relative = PureWindowsPath(FIXTURE_RELATIVE_PATH.as_posix())
        if (not source.is_absolute() or ".." in source.parts or ".." in archive.parts
                or source.parts[-len(relative.parts):] != relative.parts
                or archive != declared / relative or not 0 <= int(row["RobocopyExit"]) < 8):
            raise HistoricalFixtureError("AUTHORITY_INVALID", "move row does not bind the canonical fixture")
        if (repo / FIXTURE_RELATIVE_PATH).exists() or (repo / FIXTURE_RELATIVE_PATH).is_symlink():
            raise HistoricalFixtureError("AMBIGUOUS_RESOURCE", "legacy checkout fixture also exists")
        root = _plain_path(mirror / FIXTURE_RELATIVE_PATH, directory=True)
        if not root.resolve().is_relative_to(mirror.resolve()):
            raise HistoricalFixtureError("PATH_UNSAFE", str(root))
        manifest = json.loads(_verified_bytes(repo / DESIGN_RELATIVE_PATH / "ARTIFACT_MANIFEST.json", MANIFEST_SHA256))
        if (not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list)
                or any(not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                       for entry in manifest["files"])):
            raise HistoricalFixtureError("AUTHORITY_INVALID", "invalid manifest file entries")
        prefix = "readonly_counterexample_copy/"
        entries = [entry for entry in manifest["files"] if entry["path"].startswith(prefix)]
        names = [entry["path"][len(prefix):] for entry in entries]
        if len(names) != len(EXPECTED_FILENAMES) or set(names) != EXPECTED_FILENAMES:
            raise HistoricalFixtureError("AUTHORITY_INVALID", "manifest must bind the exact eight original files")
        if {path.name for path in root.iterdir()} != EXPECTED_FILENAMES:
            raise HistoricalFixtureError("FILE_SET_MISMATCH", "missing or unexpected fixture members")
        for name, entry in zip(names, entries):
            raw = _verified_bytes(root / name, entry["sha256"])
            if len(raw) != entry["size_bytes"]:
                raise HistoricalFixtureError("SIZE_MISMATCH", name)
        return root
    except HistoricalFixtureError:
        raise
    except FileNotFoundError as exc:
        raise HistoricalFixtureError("RESOURCE_MISSING", str(exc.filename)) from exc
    except (OSError, ValueError, TypeError, KeyError, csv.Error) as exc:
        raise HistoricalFixtureError("AUTHORITY_INVALID", str(exc)) from exc
