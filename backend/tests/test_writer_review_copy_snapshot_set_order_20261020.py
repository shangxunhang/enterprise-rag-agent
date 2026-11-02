"""Offline RC8-WC-001 regression against the frozen Full8 predecessor.

The native fixture opens the original database read-only and immutable. It pins
one historical checkpoint; it does not establish historical latest scheduling,
run a Graph, construct a SourceOwner, call a provider, or seal historical files.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from langgraph.checkpoint.sqlite import SqliteSaver
import pytest

from agent.runtime.langgraph_state import LangGraphRuntimeStateCodec
from agent.runtime.official_checkpoint_saver import CompressedJsonPlusSerializer
from apps.enterprise_document.schemas.scheme_generation_graph import (
    SchemeGenerationGraphStateCodec,
)
from apps.enterprise_document.services.agent_rag_v1 import writer_review_copy as wc
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)


REPOSITORY = Path(__file__).resolve().parents[2]
FROZEN = (REPOSITORY / "docs/rc1_full8_real_e2e_acceptance_20261020_r01/evidence"
    / "EXACT_VALIDATE_DOCUMENT_ASSEMBLY_PREDECESSOR.json")
CHECKPOINT_ID = "1f1cbec0-3baf-61d0-800a-10e00132bc16"
MISMATCH = "CHECKPOINT_INCOMING_SNAPSHOT_MISMATCH"


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@pytest.fixture(scope="module")
def historical_native():
    """Use the same frozen JSON locator and production serde/codec as the probe."""
    assert FROZEN.is_file(), "required historical frozen predecessor is unavailable"
    frozen_sha256 = _file_sha256(FROZEN)
    frozen = json.loads(FROZEN.read_bytes())
    config = frozen["config"]
    assert config["configurable"]["checkpoint_id"] == CHECKPOINT_ID
    database = Path(frozen["source_db"])
    assert database.is_absolute() and database.is_file()
    database_sha256 = _file_sha256(database)
    connection = sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1",
        uri=True, check_same_thread=False)
    connection.execute("PRAGMA query_only=ON")
    assert connection.execute("PRAGMA query_only").fetchone() == (1,)
    connection.execute("BEGIN")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(checkpoints)")}
    assert {"thread_id", "checkpoint_ns", "checkpoint_id", "parent_checkpoint_id",
        "type", "checkpoint", "metadata"} <= columns

    class PinnedReadOnlySaver(SqliteSaver):
        def __init__(self):
            super().__init__(connection, serde=CompressedJsonPlusSerializer())
            # Existing schema was checked above. Never run the Saver DDL setup.
            self.is_setup = True
            self.reads = []

        def get_tuple(self, requested):
            requested = copy.deepcopy(requested)
            requested.setdefault("configurable", {}).setdefault("checkpoint_id", CHECKPOINT_ID)
            self.reads.append(requested)
            return super().get_tuple(requested)

    engine = EnterpriseDocumentWorkflowEngine.__new__(EnterpriseDocumentWorkflowEngine)
    engine.runtime_state_codec = LangGraphRuntimeStateCodec()
    engine.business_state_codec = SchemeGenerationGraphStateCodec()
    engine.checkpointer = PinnedReadOnlySaver()
    workflow = SimpleNamespace(workflow_id="scheme_generation_v2", workflow_version="v2.0")
    checkpoint = engine.checkpointer.get_tuple(config)
    assert checkpoint is not None and checkpoint.checkpoint["id"] == CHECKPOINT_ID
    values = checkpoint.checkpoint["channel_values"]
    graph, payload = engine._restore(values, workflow)
    exported_graph, exported_payload = engine._restore(frozen["channel_values"], workflow)
    assert graph.model_dump(mode="json") == exported_graph.model_dump(mode="json")
    assert wc._payload_comparable_json(payload) == wc._payload_comparable_json(exported_payload)
    assert payload.assembly is not None and payload.assembly.known_chunk_ids
    initial_values_sha256 = wc._sha(values)
    try:
        yield SimpleNamespace(engine=engine, workflow=workflow, graph=graph,
            payload=payload, values=values, checkpoint=checkpoint)
    finally:
        assert connection.total_changes == 0
        assert wc._sha(values) == initial_values_sha256
        connection.rollback()
        connection.close()
        assert _file_sha256(FROZEN) == frozen_sha256
        assert _file_sha256(database) == database_sha256


def _incoming(historical_native):
    # Match the actual production _run_stage boundary, including both deep copies.
    return (historical_native.graph.model_copy(deep=True),
        historical_native.payload.model_copy(deep=True))


def _resolve(historical_native, before_graph, before_payload):
    return wc.resolve_committed_predecessor(historical_native.engine,
        workflow=historical_native.workflow, before_graph=before_graph,
        before_payload=before_payload)


def test_historical_native_deepcopy_passes_production_resolver(historical_native):
    graph, payload = _incoming(historical_native)
    original_set = historical_native.payload.assembly.known_chunk_ids
    incoming_set = payload.assembly.known_chunk_ids
    assert original_set == incoming_set and original_set is not incoming_set
    before = payload.model_dump(mode="json")
    result = _resolve(historical_native, graph, payload)
    assert result["checkpoint_id"] == CHECKPOINT_ID
    assert result["committed_checkpoint_source_sha256"] == wc._sha(historical_native.values)
    assert result["business_revision"] == payload.business_revision
    assert wc._payload_comparable_json(result["payload"]) == wc._payload_comparable_json(payload)
    assert payload.model_dump(mode="json") == before
    assert payload.assembly.known_chunk_ids is incoming_set
    assert all(row["configurable"]["checkpoint_id"] == CHECKPOINT_ID
        for row in historical_native.engine.checkpointer.reads)


PAYLOAD_DIFFERENCES = [
    "set_add", "set_delete", "set_replace", "body", "sections_order",
    "required_sections_order", "plan_sections_order", "assembly_draft_sections_order",
    "generated_draft_sections_order", "section_execution_results_order",
    "task_id", "run_id", "document_id", "workflow_namespace", "document_version",
    "business_revision", "schema_version",
]


@pytest.mark.parametrize("case", PAYLOAD_DIFFERENCES, ids=PAYLOAD_DIFFERENCES)
def test_historical_native_real_payload_difference_rejected(historical_native, case):
    graph, payload = _incoming(historical_native)
    members = payload.assembly.known_chunk_ids
    first = sorted(members)[0]
    foreign = "__offline_regression_foreign_chunk__"
    while foreign in members:
        foreign += "_"
    if case == "set_add":
        members.add(foreign)
    elif case == "set_delete":
        members.remove(first)
    elif case == "set_replace":
        members.remove(first)
        members.add(foreign)
    elif case == "body":
        payload.sections[0].content += "\nOFFLINE_REGRESSION_CHANGED_BODY"
    elif case == "sections_order":
        assert len(payload.sections) > 1
        payload.sections.reverse()
    elif case == "required_sections_order":
        assert len(payload.required_sections) > 1
        payload.required_sections.reverse()
    elif case == "plan_sections_order":
        assert len(payload.document_plan.sections) > 1
        payload.document_plan.sections.reverse()
    elif case == "assembly_draft_sections_order":
        assert len(payload.assembly.draft.sections) > 1
        payload.assembly.draft.sections.reverse()
    elif case == "generated_draft_sections_order":
        assert len(payload.generated_draft.sections) > 1
        payload.generated_draft.sections.reverse()
    elif case == "section_execution_results_order":
        assert len(payload.section_execution_results) > 1
        payload.section_execution_results.reverse()
    elif case == "business_revision":
        payload.business_revision += 1
    else:
        setattr(payload, case, str(getattr(payload, case)) + "-offline-changed")
    with pytest.raises(wc.WriterReviewCopyUnavailable, match=MISMATCH):
        _resolve(historical_native, graph, payload)


def test_historical_native_graph_difference_still_rejected(historical_native):
    graph, payload = _incoming(historical_native)
    graph.user_input += " OFFLINE_REGRESSION_CHANGED_GRAPH"
    with pytest.raises(wc.WriterReviewCopyUnavailable, match=MISMATCH):
        _resolve(historical_native, graph, payload)


class _ProjectionPayload:
    """Detached malformed serializer seam; never a canonical payload/fixture."""

    def __init__(self, original, data):
        self.assembly = SimpleNamespace(known_chunk_ids=original)
        self.data = data

    def model_dump(self, *, mode):
        assert mode == "json"
        return copy.deepcopy(self.data)


@pytest.mark.parametrize("members", [set(), {"only"}, {"z", "a", "middle"}],
    ids=["empty_set", "singleton_set", "multiple_members"])
def test_comparison_projection_only_sorts_known_set_and_is_pure(members):
    data = {"assembly": {"known_chunk_ids": list(reversed(sorted(members))),
        "ordered_list": ["z", "a"]}, "sections": [{"content": "exact正文\r\n"}],
        "required_sections": ["second", "first"], "identity": "unchanged"}
    payload = _ProjectionPayload(members, data)
    original = copy.deepcopy(data)
    expected = copy.deepcopy(data)
    expected["assembly"]["known_chunk_ids"] = sorted(members)
    actual = wc._payload_comparable_json(payload)
    assert actual == expected
    assert payload.data == original and payload.assembly.known_chunk_ids is members
    assert members == set(original["assembly"]["known_chunk_ids"])
    assert actual is not data and actual["assembly"] is not data["assembly"]
    actual["assembly"]["known_chunk_ids"].append("detached-only")
    assert payload.data == original and "detached-only" not in members


@pytest.mark.parametrize("original", [["a"], None, {"a", 1}, frozenset({"a"})],
    ids=["native_list", "native_none", "native_nonstring_member", "native_frozenset"])
def test_invalid_native_set_type_fails_closed(historical_native, original):
    _, payload = _incoming(historical_native)
    # Deliberately bypass schema admission on this detached instance so the
    # production comparison boundary must reject an invalid native field too.
    object.__setattr__(payload.assembly, "known_chunk_ids", original)
    with pytest.raises(wc.WriterReviewCopyUnavailable, match=MISMATCH):
        wc._payload_comparable_json(payload)


INVALID_JSON_PROJECTIONS = [
    ("missing_assembly", {}),
    ("assembly_none", {"assembly": None}),
    ("assembly_list", {"assembly": []}),
    ("missing_known_chunk_ids", {"assembly": {}}),
    ("ids_none", {"assembly": {"known_chunk_ids": None}}),
    ("ids_tuple", {"assembly": {"known_chunk_ids": ("a",)}}),
    ("ids_dict", {"assembly": {"known_chunk_ids": {"a": True}}}),
    ("ids_string", {"assembly": {"known_chunk_ids": "a"}}),
    ("ids_nonstring", {"assembly": {"known_chunk_ids": [1]}}),
    ("duplicate_ids", {"assembly": {"known_chunk_ids": ["a", "a"]}}),
    ("missing_member", {"assembly": {"known_chunk_ids": []}}),
    ("foreign_member", {"assembly": {"known_chunk_ids": ["b"]}}),
    ("extra_member", {"assembly": {"known_chunk_ids": ["a", "b"]}}),
    ("top_level_none", None),
    ("top_level_list", []),
    ("top_level_tuple", ()),
    ("top_level_scalar", "invalid"),
]


@pytest.mark.parametrize("case,data", INVALID_JSON_PROJECTIONS,
    ids=[case for case, _ in INVALID_JSON_PROJECTIONS])
def test_invalid_serialized_projection_fails_closed(case, data):
    payload = _ProjectionPayload({"a"}, data)
    before = copy.deepcopy(data)
    with pytest.raises(wc.WriterReviewCopyUnavailable, match=MISMATCH):
        wc._payload_comparable_json(payload)
    assert payload.data == before and payload.assembly.known_chunk_ids == {"a"}


def test_serialization_exception_fails_closed():
    class BrokenSerialization:
        def model_dump(self, *, mode):
            raise TypeError("offline-test-unserializable")
    with pytest.raises(TypeError, match="offline-test-unserializable"):
        wc._payload_comparable_json(BrokenSerialization())


def test_missing_assembly_keeps_existing_completeness_rejection(historical_native):
    graph, payload = _incoming(historical_native)
    payload.assembly = None
    assert wc._payload_comparable_json(payload) == payload.model_dump(mode="json")
    with pytest.raises(wc.WriterReviewCopyUnavailable, match=MISMATCH):
        _resolve(historical_native, graph, payload)
    with pytest.raises(wc.WriterReviewCopyUnavailable, match="COMPLETE_ASSEMBLY_REQUIRED"):
        wc.validate_exact_writer_document({"payload": payload},
            section_dependencies=SimpleNamespace())
