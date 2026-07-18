"""RAG run-capture port."""
from __future__ import annotations

from typing import Any, Dict, Protocol


class RAGRunCapturePort(Protocol):
    def capture(self, record: Dict[str, Any]) -> Dict[str, Any]: ...
