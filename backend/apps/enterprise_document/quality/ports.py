"""Contracts for application-side generation checking and repair."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class RepairOutput:
    answer: str
    repaired: bool
    report: dict[str, Any]


@runtime_checkable
class GenerationCheckerPort(Protocol):
    def check(
        self,
        *,
        query: str,
        answer: str | None,
        context: str,
        citations: list[dict[str, Any]],
        citation_bindings: list[dict[str, Any]] | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None: ...

    def execution_metadata(self) -> dict[str, Any]: ...


@runtime_checkable
class RepairStrategyPort(Protocol):
    def repair(
        self,
        *,
        query: str,
        answer: str,
        context: str,
        citations: list[dict[str, Any]],
        citation_bindings: list[dict[str, Any]],
        check_result: dict[str, Any] | None,
        runtime_context: dict[str, Any] | None = None,
    ) -> RepairOutput: ...

    def execution_metadata(self) -> dict[str, Any]: ...
