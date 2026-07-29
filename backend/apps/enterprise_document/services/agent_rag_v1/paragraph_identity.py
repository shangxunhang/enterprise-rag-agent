"""稳定 Paragraph ID 的无状态分配规则。"""

from __future__ import annotations

from typing import Callable, Iterable
from uuid import uuid4


class ParagraphIdentityService:
    """调用方提供现用/退役 ID 集合，服务本身不缓存 Task 状态。"""

    def __init__(self, id_factory: Callable[[], str] | None = None) -> None:
        self.id_factory = id_factory or (lambda: f"paragraph_{uuid4().hex}")

    def new_id(self, reserved_ids: Iterable[str]) -> str:
        reserved = set(reserved_ids)
        for _ in range(100):
            candidate = str(self.id_factory()).strip()
            if candidate and candidate not in reserved:
                return candidate
        raise RuntimeError("unable to allocate a unique paragraph id")

    @staticmethod
    def preserve(paragraph_id: str) -> str:
        if not paragraph_id.strip():
            raise ValueError("paragraph id cannot be empty")
        return paragraph_id

    def merge(
        self,
        paragraph_ids: Iterable[str],
        *,
        reserved_ids: Iterable[str],
    ) -> tuple[str, list[str]]:
        superseded = list(paragraph_ids)
        if len(superseded) < 2:
            raise ValueError("merge requires at least two paragraphs")
        return self.new_id(reserved_ids), superseded

    def split(
        self,
        paragraph_id: str,
        *,
        parts: int,
        reserved_ids: Iterable[str],
    ) -> tuple[list[str], str]:
        if parts < 2:
            raise ValueError("split requires at least two parts")
        reserved = set(reserved_ids)
        new_ids: list[str] = []
        for _ in range(parts):
            new_id = self.new_id(reserved | set(new_ids))
            new_ids.append(new_id)
        return new_ids, paragraph_id
