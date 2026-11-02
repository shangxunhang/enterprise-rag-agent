"""Canonical retrieval scope shared by Dense and keyword branches.

KB, file and document identifiers are authorization sets, not ordered routing
preferences.  Stage D normalizes them by stripping, deduplicating and sorting,
then rejects unknown structured-scope fields or any Dense/BM25 disagreement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator


_EQ_PATTERN = re.compile(r'^\s*(tenant_id)\s*==\s*("(?:\\.|[^"\\])*")\s*$')
_IN_PATTERN = re.compile(
    r'^\s*(kb_id|file_id|doc_id)\s+in\s*(\[(?:.|\n)*\])\s*$',
    re.DOTALL,
)
_ALLOWED_FIELDS = {"tenant_id", "kb_id", "file_id", "doc_id"}


def _normalized_set(values: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(value).strip()
                for value in (values or [])
                if str(value or "").strip()
            }
        )
    )


def _quote(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


class StructuredRetrievalScopeSchema(BaseModel):
    """Strict structured projection accepted from the keyword branch."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = None
    kb_ids: list[str] = Field(default_factory=list)
    file_ids: list[str] = Field(default_factory=list)
    doc_ids: list[str] = Field(default_factory=list)
    access_scope_enforced: bool = False

    @model_validator(mode="after")
    def _normalize_sets(self) -> "StructuredRetrievalScopeSchema":
        self.tenant_id = str(self.tenant_id or "").strip() or None
        self.kb_ids = list(_normalized_set(self.kb_ids))
        self.file_ids = list(_normalized_set(self.file_ids))
        self.doc_ids = list(_normalized_set(self.doc_ids))
        return self


@dataclass(frozen=True)
class CanonicalRetrievalScope:
    tenant_id: str | None = None
    kb_ids: tuple[str, ...] = field(default_factory=tuple)
    file_ids: tuple[str, ...] = field(default_factory=tuple)
    doc_ids: tuple[str, ...] = field(default_factory=tuple)
    access_scope_enforced: bool = False

    def to_filter_expr(self) -> str | None:
        parts: list[str] = []
        if self.tenant_id:
            parts.append(f"tenant_id == {_quote(self.tenant_id)}")
        for field_name, values in (
            ("kb_id", self.kb_ids),
            ("file_id", self.file_ids),
            ("doc_id", self.doc_ids),
        ):
            if values:
                parts.append(
                    f"{field_name} in ["
                    + ", ".join(_quote(item) for item in values)
                    + "]"
                )
        return " and ".join(parts) or None

    def to_keyword_scope(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "kb_ids": list(self.kb_ids),
            "file_ids": list(self.file_ids),
            "doc_ids": list(self.doc_ids),
            "access_scope_enforced": self.access_scope_enforced,
        }

    def fingerprint_payload(self) -> dict[str, Any]:
        return self.to_keyword_scope()


def parse_generated_filter_expr(filter_expr: str | None) -> CanonicalRetrievalScope:
    """Parse only the expression grammar emitted by ``RAGRequestMapper``."""
    raw = str(filter_expr or "").strip()
    if not raw:
        return CanonicalRetrievalScope()

    clauses = [item.strip() for item in raw.split(" and ") if item.strip()]
    values: dict[str, Any] = {
        "tenant_id": None,
        "kb_ids": (),
        "file_ids": (),
        "doc_ids": (),
    }
    seen: set[str] = set()
    for clause in clauses:
        eq_match = _EQ_PATTERN.match(clause)
        if eq_match:
            field_name = eq_match.group(1)
            if field_name in seen:
                raise ValueError(f"duplicate retrieval filter field: {field_name}")
            seen.add(field_name)
            values["tenant_id"] = str(json.loads(eq_match.group(2))).strip() or None
            continue

        in_match = _IN_PATTERN.match(clause)
        if not in_match:
            raise ValueError(
                "unsupported retrieval filter expression; only trusted "
                "tenant_id equality and kb/file/doc IN clauses are allowed"
            )
        field_name = in_match.group(1)
        if field_name not in _ALLOWED_FIELDS or field_name in seen:
            raise ValueError(
                f"duplicate or unsupported retrieval filter field: {field_name}"
            )
        seen.add(field_name)
        payload = json.loads(in_match.group(2))
        if not isinstance(payload, list) or not payload:
            raise ValueError(
                f"retrieval filter {field_name} must contain a non-empty list"
            )
        normalized = _normalized_set(payload)
        if not normalized:
            raise ValueError(
                f"retrieval filter {field_name} contains no usable values"
            )
        values[
            {"kb_id": "kb_ids", "file_id": "file_ids", "doc_id": "doc_ids"}[
                field_name
            ]
        ] = normalized

    return CanonicalRetrievalScope(**values)


def _structured_scope(
    keyword_scope: dict[str, Any] | None,
    keyword_doc_ids: Iterable[str] | None,
    *,
    access_scope_enforced: bool,
) -> CanonicalRetrievalScope:
    payload = dict(keyword_scope or {})
    if keyword_doc_ids and not payload.get("doc_ids"):
        payload["doc_ids"] = list(keyword_doc_ids)
    payload.setdefault("access_scope_enforced", access_scope_enforced)
    scope = StructuredRetrievalScopeSchema.model_validate(payload)
    return CanonicalRetrievalScope(
        tenant_id=scope.tenant_id,
        kb_ids=tuple(scope.kb_ids),
        file_ids=tuple(scope.file_ids),
        doc_ids=tuple(scope.doc_ids),
        access_scope_enforced=scope.access_scope_enforced,
    )


def _compare_field(name: str, dense: Any, keyword: Any) -> Any:
    if dense and keyword and dense != keyword:
        raise ValueError(
            "dense and keyword retrieval scopes disagree for "
            f"{name}: dense={dense!r} keyword={keyword!r}"
        )
    return dense or keyword


def resolve_canonical_retrieval_scope(
    *,
    filter_expr: str | None,
    keyword_scope: dict[str, Any] | None,
    keyword_doc_ids: Iterable[str] | None = None,
    access_scope_enforced: bool = False,
) -> CanonicalRetrievalScope:
    """Resolve one fail-closed, order-independent scope for both branches."""
    dense = parse_generated_filter_expr(filter_expr)
    keyword = _structured_scope(
        keyword_scope,
        keyword_doc_ids,
        access_scope_enforced=access_scope_enforced,
    )
    resolved = CanonicalRetrievalScope(
        tenant_id=_compare_field("tenant_id", dense.tenant_id, keyword.tenant_id),
        kb_ids=_compare_field("kb_ids", dense.kb_ids, keyword.kb_ids),
        file_ids=_compare_field("file_ids", dense.file_ids, keyword.file_ids),
        doc_ids=_compare_field("doc_ids", dense.doc_ids, keyword.doc_ids),
        access_scope_enforced=bool(
            dense.access_scope_enforced
            or keyword.access_scope_enforced
            or access_scope_enforced
        ),
    )
    if resolved.access_scope_enforced:
        if not resolved.tenant_id:
            raise ValueError("enforced retrieval scope requires tenant_id")
        if not resolved.kb_ids:
            raise ValueError("enforced retrieval scope requires at least one kb_id")
    return resolved


__all__ = [
    "CanonicalRetrievalScope",
    "StructuredRetrievalScopeSchema",
    "parse_generated_filter_expr",
    "resolve_canonical_retrieval_scope",
]
