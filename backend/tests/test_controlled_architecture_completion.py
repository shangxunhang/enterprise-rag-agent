"""Acceptance tests for the controlled architecture completion."""

from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from threading import Lock
import time
from unittest.mock import patch

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.checkpoint.base import empty_checkpoint
import pytest

from agent.runtime.official_checkpoint_saver import (
    LegacyCheckpointDatabaseError,
    _checkpoint_serde,
    create_sqlite_checkpoint_saver,
)
from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.scheme_writer_schema import (
    DocumentPlanSchema,
    SectionPlanSchema,
)
from apps.enterprise_document.services.scheme_writer.stage_service import (
    SchemeGenerationStageService,
)
from apps.enterprise_document.workflows.section_execution_graph import (
    SectionExecutionGraph,
)
from core.config import get_settings
from model_gateway.fake_llm_client import FakeLLMClient
from model_gateway.integrations.langchain.runtime_facade import (
    LangChainModelGatewayFacade,
)
from model_gateway.integrations.langchain.runnable import (
    build_model_gateway_runnable,
)
from model_gateway.model_gateway import ModelGateway
from rag.integrations.langchain.runtime_facade import LangChainRAGServiceFacade
from rag.integrations.langchain.runnable import build_rag_runnable
from rag.services.rag_service import FakeRAGService
from schemas.model import ModelRequestSchema
from schemas.rag import RAGToolInputSchema
from scripts.migrate_legacy_checkpoints import migrate_legacy_database


class _CountingCallback(BaseCallbackHandler):
    def __init__(self) -> None:
        self.starts = 0
        self.ends = 0
        self.errors = 0
        self._lock = Lock()

    def on_chain_start(self, *args, **kwargs) -> None:
        with self._lock:
            self.starts += 1

    def on_chain_end(self, *args, **kwargs) -> None:
        with self._lock:
            self.ends += 1

    def on_chain_error(self, *args, **kwargs) -> None:
        with self._lock:
            self.errors += 1


def _model_request(index: int) -> ModelRequestSchema:
    return ModelRequestSchema(
        model_call_id=f"controlled_model_{index}",
        task_id="task_controlled_facade",
        run_id="run_controlled_facade",
        model_name="fake_llm",
        caller_agent="ControlledArchitectureTest",
        prompt=f"test request {index}",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _rag_request(index: int) -> RAGToolInputSchema:
    return RAGToolInputSchema(
        task_id="task_controlled_facade",
        run_id="run_controlled_facade",
        agent_name="ControlledArchitectureTest",
        query=f"enterprise retrieval {index}",
        need_citation=True,
        max_context_chars=6000,
        max_context_items=3,
        extra={"retrieval_trace_id": f"controlled_rag_{index}"},
    )


def test_langchain_facades_support_sync_async_stream_batch_and_callbacks() -> None:
    gateway = ModelGateway(default_model_name="fake_llm")
    gateway.register_client(FakeLLMClient())
    model = LangChainModelGatewayFacade(
        delegate=gateway,
        runnable=build_model_gateway_runnable(gateway),
    )
    rag_delegate = FakeRAGService()
    rag = LangChainRAGServiceFacade(
        delegate=rag_delegate,
        runnable=build_rag_runnable(rag_delegate),
    )
    callback = _CountingCallback()
    config = {
        "callbacks": [callback],
        "tags": ["controlled-acceptance"],
        "metadata": {"caller_metadata": "preserved"},
        "max_concurrency": 2,
    }

    assert model.invoke(_model_request(1), config=config).success
    assert len(list(model.stream(_model_request(2), config=config))) == 1
    model_batch = model.batch(
        [_model_request(3), _model_request(4)],
        config=config,
    )
    assert [item.model_call_id for item in model_batch] == [
        "controlled_model_3",
        "controlled_model_4",
    ]

    async def exercise_model_async() -> None:
        assert (await model.ainvoke(_model_request(5), config=config)).success
        streamed = [
            item
            async for item in model.astream(_model_request(6), config=config)
        ]
        assert len(streamed) == 1
        batch = await model.abatch(
            [_model_request(7), _model_request(8)],
            config=config,
        )
        assert [item.model_call_id for item in batch] == [
            "controlled_model_7",
            "controlled_model_8",
        ]

    asyncio.run(exercise_model_async())
    assert model.runnable_invocation_count == 8
    assert gateway.usage_snapshot()["logical_calls"] == 8

    assert rag.invoke(_rag_request(1), config=config).success
    assert len(list(rag.stream(_rag_request(2), config=config))) == 1

    async def exercise_rag_async() -> None:
        assert (await rag.ainvoke(_rag_request(3), config=config)).success
        batch = await rag.abatch(
            [_rag_request(4), _rag_request(5)],
            config=config,
        )
        assert [item.query for item in batch] == [
            "enterprise retrieval 4",
            "enterprise retrieval 5",
        ]

    asyncio.run(exercise_rag_async())
    assert rag.runnable_invocation_count == 5
    assert callback.errors == 0
    assert callback.starts == callback.ends
    assert callback.starts >= 13


def test_legacy_checkpoint_database_requires_and_supports_atomic_migration(
    tmp_path,
) -> None:
    database = tmp_path / "legacy.sqlite3"
    serde = _checkpoint_serde()
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"value": 7}
    checkpoint_type, checkpoint_blob = serde.dumps_typed(checkpoint)
    metadata_type, metadata_blob = serde.dumps_typed(
        {"source": "input", "step": 0, "parents": {}}
    )
    value_type, value_blob = serde.dumps_typed({"pending": True})
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(
            """
            CREATE TABLE checkpoints (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL,
                checkpoint_id TEXT NOT NULL,
                parent_checkpoint_id TEXT,
                checkpoint_type TEXT NOT NULL,
                checkpoint_blob BLOB NOT NULL,
                metadata_type TEXT NOT NULL,
                metadata_blob BLOB NOT NULL,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
            );
            CREATE TABLE checkpoint_writes (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL,
                checkpoint_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                write_index INTEGER NOT NULL,
                channel TEXT NOT NULL,
                value_type TEXT NOT NULL,
                value_blob BLOB NOT NULL,
                task_path TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (
                    thread_id, checkpoint_ns, checkpoint_id,
                    task_id, write_index
                )
            );
            """
        )
        connection.execute(
            """
            INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "thread-1",
                "workflow:v1",
                checkpoint["id"],
                None,
                checkpoint_type,
                checkpoint_blob,
                metadata_type,
                metadata_blob,
            ),
        )
        connection.execute(
            """
            INSERT INTO checkpoint_writes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "thread-1",
                "workflow:v1",
                checkpoint["id"],
                "task-1",
                0,
                "custom",
                value_type,
                value_blob,
                "",
            ),
        )
        connection.commit()
    with pytest.raises(LegacyCheckpointDatabaseError):
        create_sqlite_checkpoint_saver(database)

    report = migrate_legacy_database(database)
    assert report["checkpoint_count"] == 1
    assert report["pending_write_count"] == 1
    assert Path(report["legacy_backup"]).is_file()
    saver = create_sqlite_checkpoint_saver(database)
    restored = saver.get_tuple(
        {
            "configurable": {
                "thread_id": "thread-1",
                "checkpoint_ns": "workflow:v1",
            }
        }
    )
    assert restored is not None
    assert restored.checkpoint["channel_values"]["value"] == 7
    assert restored.pending_writes == [
        ("task-1", "custom", {"pending": True})
    ]
    saver.conn.close()


def test_mainline_stream_exposes_stable_events_and_structured_final_result(
    tmp_path,
) -> None:
    settings = replace(
        get_settings(),
        data_root=tmp_path,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=True,
        data_capture_enabled=True,
    )
    environment = {
        "USE_REAL_RAG_TOOL": "false",
        "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }
    with patch.dict(os.environ, environment):
        events = list(
            MainlineApplicationService().stream_run(
                project_root=Path(__file__).resolve().parents[2],
                user_input="Generate an enterprise RAG architecture proposal",
                run_id="controlled_stream",
                output_root=tmp_path,
                clean_existing=True,
                settings=settings,
                allow_demo_defaults=True,
                enable_agent_self_rag=False,
            )
        )

    assert events
    assert [event.sequence for event in events] == list(
        range(1, len(events) + 1)
    )
    assert len({event.event_id for event in events}) == len(events)
    assert all(
        event.schema_version == "workflow_stream_event_v1"
        for event in events
    )
    event_types = [event.event_type for event in events]
    assert event_types[0] == "workflow_started"
    assert "checkpoint_created" in event_types
    assert "section_started" in event_types
    assert "section_completed" in event_types
    assert event_types[-1] == "workflow_completed"
    final = events[-1].payload["result"]
    assert final["task_id"] == "task_controlled_stream"
    assert final["run_id"] == "controlled_stream"
    assert final["status"] in {"success", "partial_success"}
    assert final["deliverable_final_result"] is not None


def test_section_plan_rejects_unsafe_parallelism_and_dependency_cycles() -> None:
    with pytest.raises(ValueError, match="waive global context"):
        SectionPlanSchema(
            section_id="s1",
            section_title="Unsafe",
            section_order=1,
            can_run_in_parallel=True,
        )
    first = SectionPlanSchema(
        section_id="s1",
        section_title="First",
        section_order=1,
        depends_on=["s2"],
    )
    second = SectionPlanSchema(
        section_id="s2",
        section_title="Second",
        section_order=2,
        depends_on=["s1"],
    )
    with pytest.raises(ValueError, match="dependency cycle"):
        DocumentPlanSchema(
            plan_id="cyclic",
            document_id="document",
            document_title="Cyclic",
            sections=[first, second],
            created_at=datetime.now(timezone.utc).isoformat(),
        )


def test_safe_section_parallelism_overlaps_and_fans_in_deterministically(
    tmp_path,
) -> None:
    settings = replace(
        get_settings(),
        data_root=tmp_path,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=True,
        data_capture_enabled=True,
    )
    original_prepare = SchemeGenerationStageService.prepare_document_plan
    original_execute = SectionExecutionGraph.execute

    def parallel_plan(self, graph_state, payload):
        planned = original_prepare(self, graph_state, payload)
        document_plan = planned.document_plan
        assert document_plan is not None
        first_two_ids = [
            item.section_id for item in document_plan.sections[:2]
        ]
        sections = [
            item.model_copy(
                update={
                    "can_run_in_parallel": item.section_order != 3,
                    "requires_global_context": item.section_order == 3,
                    "depends_on": (
                        first_two_ids if item.section_order == 3 else []
                    ),
                    "execution_group": "acceptance",
                }
            )
            for item in document_plan.sections
        ]
        return planned.model_copy(
            update={
                "document_plan": document_plan.model_copy(
                    update={"sections": sections},
                    deep=True,
                )
            },
            deep=True,
        )

    tracker = {
        "active": 0,
        "peak": 0,
        "completed": set(),
        "dependency_violation": False,
    }
    tracker_lock = Lock()

    def monitored_execute(self, *args, **kwargs):
        request = args[0]
        with tracker_lock:
            if not set(request.section_plan.depends_on) <= tracker["completed"]:
                tracker["dependency_violation"] = True
            tracker["active"] += 1
            tracker["peak"] = max(tracker["peak"], tracker["active"])
        try:
            time.sleep(0.04)
            return original_execute(self, *args, **kwargs)
        finally:
            with tracker_lock:
                tracker["active"] -= 1
                tracker["completed"].add(request.section_plan.section_id)

    def execute(run_id: str, concurrency: int):
        tracker["active"] = 0
        tracker["peak"] = 0
        tracker["completed"] = set()
        tracker["dependency_violation"] = False
        environment = {
            "USE_REAL_RAG_TOOL": "false",
            "FAKE_RAG_SCENARIO": "default",
            "FAKE_LLM_SCENARIO": "default",
            "ENABLE_AGENT_SELF_RAG": "false",
            "ENABLE_SEMANTIC_GATE": "false",
            "SECTION_MAX_CONCURRENCY": str(concurrency),
        }
        with patch.dict(os.environ, environment), patch.object(
            SchemeGenerationStageService,
            "prepare_document_plan",
            parallel_plan,
        ), patch.object(
            SectionExecutionGraph,
            "execute",
            monitored_execute,
        ):
            summary = MainlineApplicationService().run(
                project_root=Path(__file__).resolve().parents[2],
                user_input="Generate an enterprise RAG architecture proposal",
                run_id=run_id,
                output_root=tmp_path,
                clean_existing=True,
                settings=settings,
                allow_demo_defaults=True,
                enable_agent_self_rag=False,
            )
        return (
            summary,
            tracker["peak"],
            tracker["dependency_violation"],
        )

    sequential, sequential_peak, sequential_dependency_violation = execute(
        "parallel_baseline",
        1,
    )
    parallel, parallel_peak, parallel_dependency_violation = execute(
        "parallel_enabled",
        2,
    )

    assert sequential["status"] in {"success", "partial_success"}
    assert parallel["status"] in {"success", "partial_success"}
    assert sequential_peak == 1
    assert parallel_peak >= 2
    assert sequential_dependency_violation is False
    assert parallel_dependency_violation is False
    sequential_result = sequential["deliverable_final_result"]
    parallel_result = parallel["deliverable_final_result"]
    assert sequential_result is not None
    assert parallel_result is not None
    assert [
        item["section_order"]
        for item in sequential_result["draft"]["sections"]
    ] == [
        item["section_order"]
        for item in parallel_result["draft"]["sections"]
    ]
    assert [
        (
            item["citation_id"],
            item.get("source_document_id"),
            item.get("quote_text"),
        )
        for item in sequential["scheme_writer_output"]["citations"]
    ] == [
        (
            item["citation_id"],
            item.get("source_document_id"),
            item.get("quote_text"),
        )
        for item in parallel["scheme_writer_output"]["citations"]
    ]
    generated_ids = parallel["supervisor_result"]["result"]["shared_state"][
        "context_bundle"
    ]["generation"]["generated_section_ids"]
    assert len(generated_ids) == len(set(generated_ids)) == 8
    execution_metadata = parallel["supervisor_result"]["result"][
        "workflow_execution"
    ]["metadata"]
    assert execution_metadata["section_parallelism_enabled"] is True
    assert execution_metadata["section_max_concurrency"] == 2
