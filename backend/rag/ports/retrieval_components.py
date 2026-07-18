"""Stable ports for configuration-driven retrieval composition."""

from __future__ import annotations

from typing import Protocol

from rag.schema.candidate import CandidateSet, RetrievalRequest


class CandidateRetrieverPort(Protocol):
    def retrieve(self, request: RetrievalRequest) -> CandidateSet: ...


class FusionPort(Protocol):
    def fuse(self, candidate_sets: list[CandidateSet]) -> CandidateSet: ...


class CandidateEnricherPort(Protocol):
    def enrich(self, candidate_set: CandidateSet) -> CandidateSet: ...
