from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from agent.runtime.official_checkpoint_saver import (
    CHECKPOINT_COMPRESSION_PREFIX,
    CompressedJsonPlusSerializer,
    compact_official_checkpoint_database,
    create_sqlite_checkpoint_resource,
)


def _large_payload() -> dict[str, object]:
    repeated = "大件运输项目证据上下文与章节生成状态。" * 20_000
    return {
        "document": repeated,
        "sections": [repeated[:100_000], repeated[100_000:200_000]],
        "metadata": {"run_id": "compression-test", "version": 1},
    }


def _checkpoint(checkpoint_id: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "v": 4,
        "ts": "2026-08-03T18:40:00+08:00",
        "id": checkpoint_id,
        "channel_values": {"payload": payload},
        "channel_versions": {"payload": "1"},
        "versions_seen": {},
        "updated_channels": ["payload"],
    }


def _config(thread_id: str) -> dict[str, dict[str, str]]:
    return {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": "workflow:v1",
        }
    }


def test_compressed_serializer_round_trips_and_reads_legacy_payload() -> None:
    payload = _large_payload()
    serializer = CompressedJsonPlusSerializer(min_bytes=128)

    type_name, encoded = serializer.dumps_typed(payload)
    assert type_name.startswith(CHECKPOINT_COMPRESSION_PREFIX)
    assert serializer.loads_typed((type_name, encoded)) == payload

    legacy = JsonPlusSerializer(
        pickle_fallback=False,
        allowed_msgpack_modules=(),
    )
    legacy_type, legacy_payload = legacy.dumps_typed(payload)
    assert serializer.loads_typed((legacy_type, legacy_payload)) == payload
    assert len(encoded) < len(legacy_payload) // 4


def test_checkpoint_resource_compresses_large_state_and_restores_exactly(
    tmp_path: Path,
) -> None:
    database = tmp_path / "compressed.sqlite3"
    payload = _large_payload()
    resource = create_sqlite_checkpoint_resource(database)
    try:
        resource.saver.put(
            _config("compressed-thread"),
            _checkpoint("checkpoint-0001", payload),
            {"source": "input", "step": 0, "parents": {}},
            {},
        )
        row = resource.connection.execute(
            "SELECT type, length(checkpoint) FROM checkpoints"
        ).fetchone()
        assert row is not None
        assert str(row[0]).startswith(CHECKPOINT_COMPRESSION_PREFIX)

        restored = resource.saver.get_tuple(_config("compressed-thread"))
        assert restored is not None
        assert restored.checkpoint["channel_values"]["payload"] == payload
    finally:
        resource.close()


def test_small_checkpoint_remains_uncompressed(tmp_path: Path) -> None:
    database = tmp_path / "small.sqlite3"
    resource = create_sqlite_checkpoint_resource(database)
    try:
        resource.saver.put(
            _config("small-thread"),
            _checkpoint("checkpoint-0001", {"value": "small"}),
            {"source": "input", "step": 0, "parents": {}},
            {},
        )
        type_name = resource.connection.execute(
            "SELECT type FROM checkpoints"
        ).fetchone()[0]
        assert not str(type_name).startswith(CHECKPOINT_COMPRESSION_PREFIX)
    finally:
        resource.close()


def test_existing_official_database_is_compacted_without_semantic_change(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-uncompressed.sqlite3"
    payload = _large_payload()
    legacy = JsonPlusSerializer(
        pickle_fallback=False,
        allowed_msgpack_modules=(),
    )
    with closing(sqlite3.connect(str(database))) as connection:
        saver = SqliteSaver(connection, serde=legacy)
        saved = saver.put(
            _config("legacy-thread"),
            _checkpoint("checkpoint-0001", payload),
            {"source": "input", "step": 0, "parents": {}},
            {},
        )
        saver.put_writes(
            saved,
            [("payload", payload)],
            task_id="task-1",
        )

    size_before = database.stat().st_size
    report = compact_official_checkpoint_database(
        database,
        min_bytes=128,
        vacuum=True,
    )
    assert report["checkpoint_rows_compressed"] == 1
    assert report["write_rows_compressed"] == 1
    assert report["payload_bytes_after"] < report["payload_bytes_before"] // 4
    assert database.stat().st_size < size_before

    resource = create_sqlite_checkpoint_resource(database)
    try:
        restored = resource.saver.get_tuple(_config("legacy-thread"))
        assert restored is not None
        assert restored.checkpoint["channel_values"]["payload"] == payload
        assert restored.pending_writes[0][2] == payload
    finally:
        resource.close()
