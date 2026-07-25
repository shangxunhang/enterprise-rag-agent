"""Small durable SQLite checkpointer for the local LangGraph runtime.

The optional ``langgraph-checkpoint-sqlite`` package is not available in the
current environment.  This implementation follows the installed
``BaseCheckpointSaver`` protocol and stores the serializer's typed payloads
without narrowing the graph state to JSON.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
from typing import Any, AsyncIterator, Iterator, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
    get_checkpoint_id,
    get_checkpoint_metadata,
)


class SQLiteCheckpointSaver(BaseCheckpointSaver[Any]):
    """Crash-safe durable saver keyed by thread, namespace and checkpoint id."""

    def __init__(self, path: str | Path, *, serde=None) -> None:
        super().__init__(serde=serde)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(
            self.path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    parent_checkpoint_id TEXT,
                    checkpoint_type TEXT NOT NULL,
                    checkpoint_blob BLOB NOT NULL,
                    metadata_type TEXT NOT NULL,
                    metadata_blob BLOB NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
                );
                CREATE INDEX IF NOT EXISTS idx_checkpoints_latest
                    ON checkpoints(thread_id, checkpoint_ns, checkpoint_id DESC);
                CREATE TABLE IF NOT EXISTS checkpoint_writes (
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
                        thread_id,
                        checkpoint_ns,
                        checkpoint_id,
                        task_id,
                        write_index
                    )
                );
                """
            )

    @staticmethod
    def _identity(config: RunnableConfig) -> tuple[str, str, str | None]:
        configurable = config.get("configurable", {})
        thread_id = str(configurable.get("thread_id") or "")
        if not thread_id:
            raise ValueError("checkpoint config requires thread_id")
        checkpoint_ns = str(configurable.get("checkpoint_ns") or "")
        checkpoint_id = get_checkpoint_id(config)
        return thread_id, checkpoint_ns, checkpoint_id

    def _load_writes(
        self,
        connection: sqlite3.Connection,
        *,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> list[tuple[str, str, Any]]:
        rows = connection.execute(
            """
            SELECT task_id, channel, value_type, value_blob
            FROM checkpoint_writes
            WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?
            ORDER BY task_id, write_index
            """,
            (thread_id, checkpoint_ns, checkpoint_id),
        ).fetchall()
        return [
            (task_id, channel, self.serde.loads_typed((value_type, value_blob)))
            for task_id, channel, value_type, value_blob in rows
        ]

    def _row_to_tuple(
        self,
        connection: sqlite3.Connection,
        row,
    ) -> CheckpointTuple:
        (
            thread_id,
            checkpoint_ns,
            checkpoint_id,
            parent_checkpoint_id,
            checkpoint_type,
            checkpoint_blob,
            metadata_type,
            metadata_blob,
        ) = row
        config: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }
        parent_config = (
            {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": parent_checkpoint_id,
                }
            }
            if parent_checkpoint_id
            else None
        )
        return CheckpointTuple(
            config=config,
            checkpoint=self.serde.loads_typed(
                (checkpoint_type, checkpoint_blob)
            ),
            metadata=self.serde.loads_typed((metadata_type, metadata_blob)),
            parent_config=parent_config,
            pending_writes=self._load_writes(
                connection,
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
                checkpoint_id=checkpoint_id,
            ),
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id, checkpoint_ns, checkpoint_id = self._identity(config)
        with self._lock, self._connection() as connection:
            if checkpoint_id:
                row = connection.execute(
                    """
                    SELECT thread_id, checkpoint_ns, checkpoint_id,
                           parent_checkpoint_id, checkpoint_type, checkpoint_blob,
                           metadata_type, metadata_blob
                    FROM checkpoints
                    WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?
                    """,
                    (thread_id, checkpoint_ns, checkpoint_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT thread_id, checkpoint_ns, checkpoint_id,
                           parent_checkpoint_id, checkpoint_type, checkpoint_blob,
                           metadata_type, metadata_blob
                    FROM checkpoints
                    WHERE thread_id=? AND checkpoint_ns=?
                    ORDER BY checkpoint_id DESC
                    LIMIT 1
                    """,
                    (thread_id, checkpoint_ns),
                ).fetchone()
            return self._row_to_tuple(connection, row) if row else None

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        params: list[Any] = []
        clauses: list[str] = []
        if config is not None:
            thread_id, checkpoint_ns, _ = self._identity(config)
            clauses.extend(["thread_id=?", "checkpoint_ns=?"])
            params.extend([thread_id, checkpoint_ns])
        if before is not None:
            _, _, before_id = self._identity(before)
            if before_id:
                clauses.append("checkpoint_id < ?")
                params.append(before_id)
        sql = (
            "SELECT thread_id, checkpoint_ns, checkpoint_id, "
            "parent_checkpoint_id, checkpoint_type, checkpoint_blob, "
            "metadata_type, metadata_blob FROM checkpoints"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY checkpoint_id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, params).fetchall()
            tuples = [self._row_to_tuple(connection, row) for row in rows]
        for item in tuples:
            if filter and any(item.metadata.get(k) != v for k, v in filter.items()):
                continue
            yield item

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        del new_versions
        thread_id, checkpoint_ns, parent_checkpoint_id = self._identity(config)
        checkpoint_id = str(checkpoint["id"])
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_blob = self.serde.dumps_typed(
            get_checkpoint_metadata(config, metadata)
        )
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO checkpoints (
                        thread_id, checkpoint_ns, checkpoint_id,
                        parent_checkpoint_id, checkpoint_type, checkpoint_blob,
                        metadata_type, metadata_blob
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        thread_id,
                        checkpoint_ns,
                        checkpoint_id,
                        parent_checkpoint_id,
                        checkpoint_type,
                        checkpoint_blob,
                        metadata_type,
                        metadata_blob,
                    ),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id, checkpoint_ns, checkpoint_id = self._identity(config)
        if not checkpoint_id:
            raise ValueError("checkpoint writes require checkpoint_id")
        rows = []
        for index, (channel, value) in enumerate(writes):
            write_index = int(WRITES_IDX_MAP.get(channel, index))
            value_type, value_blob = self.serde.dumps_typed(value)
            rows.append(
                (
                    thread_id,
                    checkpoint_ns,
                    checkpoint_id,
                    task_id,
                    write_index,
                    channel,
                    value_type,
                    value_blob,
                    task_path,
                )
            )
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for row in rows:
                    if row[4] >= 0:
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO checkpoint_writes (
                                thread_id, checkpoint_ns, checkpoint_id,
                                task_id, write_index, channel,
                                value_type, value_blob, task_path
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            row,
                        )
                    else:
                        connection.execute(
                            """
                            INSERT OR REPLACE INTO checkpoint_writes (
                                thread_id, checkpoint_ns, checkpoint_id,
                                task_id, write_index, channel,
                                value_type, value_blob, task_path
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            row,
                        )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def delete_thread(self, thread_id: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM checkpoint_writes WHERE thread_id=?",
                    (thread_id,),
                )
                connection.execute(
                    "DELETE FROM checkpoints WHERE thread_id=?",
                    (thread_id,),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        values = await asyncio.to_thread(
            lambda: list(
                self.list(config, filter=filter, before=before, limit=limit)
            )
        )
        for value in values:
            yield value

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await asyncio.to_thread(
            self.put,
            config,
            checkpoint,
            metadata,
            new_versions,
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(
            self.put_writes,
            config,
            writes,
            task_id,
            task_path,
        )

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)
