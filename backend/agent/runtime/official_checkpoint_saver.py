"""构造并管理官方 LangGraph SQLite 检查点保存器。

本模块只负责数据库路径、安全序列化配置、旧表结构识别和连接生命周期。检查点表、
历史版本、待提交写入以及恢复协议全部由 ``langgraph-checkpoint-sqlite`` 官方实现，
项目代码不再复制或修改该协议。
"""

from __future__ import annotations

from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
from threading import Lock
from typing import Any, AsyncIterator
import weakref
import zlib

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


class LegacyCheckpointDatabaseError(RuntimeError):
    """表示目标数据库使用旧自定义结构或无法识别的检查点结构。"""


@dataclass
class SQLiteCheckpointResource:
    """显式持有一个官方同步 saver 及其底层 SQLite 连接。

    Attributes:
        saver: 提供官方 LangGraph 检查点读写协议的保存器。
        connection: saver 使用的底层 SQLite 连接，由该资源负责关闭。
        path: 规范化后的数据库绝对路径。

    ``close`` 是正常生命周期的唯一关闭入口；弱引用 finalizer 只处理资源所有权
    异常丢失等非正常退出，不能替代装配层的显式关闭。
    """

    saver: SqliteSaver
    connection: sqlite3.Connection
    path: Path

    # 标记底层连接是否已经成功关闭；不暴露在 repr 中，避免调试输出噪声。
    _closed: bool = field(default=False, init=False, repr=False)
    # 保护 closed 检查和连接关闭，使并发关闭保持幂等。
    _close_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    # 资源被垃圾回收但未显式关闭时使用的兜底 finalizer。
    _finalizer: weakref.finalize = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """在数据类初始化后注册异常退出连接清理器。"""

        # 正常路径始终调用 close；finalizer 只在对象失去所有权时兜底关闭连接。
        self._finalizer = weakref.finalize(self, self.connection.close)

    @property
    def closed(self) -> bool:
        """线程安全地返回资源是否已经成功关闭。"""

        # 读取与 close 使用同一把锁，避免观察到关闭过程中的中间状态。
        with self._close_lock:
            return self._closed

    def close(self) -> None:
        """至多成功关闭一次底层 SQLite 连接。

        如果底层 ``connection.close`` 抛出异常，资源不会提前标记为已关闭，调用方
        仍可重试；finalizer 也保持附着状态，以便异常退出时继续兜底清理。
        """

        # 第一步：串行化并发关闭调用，确保状态判断与连接操作不可交错。
        with self._close_lock:
            # 第二步：已经关闭时直接返回，使资源关闭操作具备幂等性。
            if self._closed:
                return

            # 第三步：先真实关闭连接；失败时不能发布虚假的成功生命周期状态。
            self.connection.close()

            # 第四步：底层关闭成功后才更新标记并解除不再需要的 finalizer。
            self._closed = True
            self._finalizer.detach()


def _validate_existing_database(database_path: Path) -> None:
    """在官方 saver 接触数据库前识别旧结构和未知结构。

    Args:
        database_path: 即将用于检查点持久化的规范数据库路径。

    Raises:
        LegacyCheckpointDatabaseError: 检测到项目旧自定义表结构，或现有
            ``checkpoints`` 表既不符合旧结构也不符合官方结构时抛出。
    """

    # 第一步：不存在或为空的文件尚无表结构，交给官方 saver 正常初始化。
    if not database_path.exists() or database_path.stat().st_size == 0:
        return

    # 第二步：使用独立短生命周期连接只读检查 checkpoints 表及其列名。
    with closing(sqlite3.connect(str(database_path))) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='checkpoints'"
        ).fetchone()

        # 第三步：数据库中没有 checkpoints 表时，不阻止官方实现创建自己的表。
        if table is None:
            return

        # 第四步：读取实际列集合，后续只根据结构特征判断，不依赖用户版本标记。
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(checkpoints)"
            ).fetchall()
        }

    # 第五步：旧项目自定义实现包含两个 blob 列，必须先显式迁移，不能原地覆盖。
    if {"checkpoint_blob", "metadata_blob"} <= columns:
        raise LegacyCheckpointDatabaseError(
            "legacy custom checkpoint schema detected at "
            f"{database_path}; run "
            "`python scripts/migrate_legacy_checkpoints.py "
            f"--database \"{database_path}\"` before starting the runtime"
        )

    # 第六步：定义官方同步 SQLite saver 当前所需的核心列集合。
    official_columns = {
        "thread_id",
        "checkpoint_ns",
        "checkpoint_id",
        "parent_checkpoint_id",
        "type",
        "checkpoint",
        "metadata",
    }

    # 第七步：存在 checkpoints 表但不满足官方核心结构时，按未知结构 fail-closed。
    if not official_columns <= columns:
        raise LegacyCheckpointDatabaseError(
            "unknown checkpoint schema detected at "
            f"{database_path}; preserve the file and inspect it before migration"
        )


CHECKPOINT_COMPRESSION_PREFIX = "zlib+"
DEFAULT_CHECKPOINT_COMPRESSION_MIN_BYTES = 64 * 1024
DEFAULT_CHECKPOINT_COMPRESSION_LEVEL = 6


class CompressedJsonPlusSerializer:
    """安全 JSON+/msgpack 序列化器，按阈值压缩大型检查点载荷。

    类型标签使用 ``zlib+<delegate_type>``，因此同一数据库可以同时读取历史未压缩
    行与新压缩行。压缩只包裹官方 ``JsonPlusSerializer`` 产生的安全字节，不引入
    pickle，也不改变 LangGraph 的检查点、pending writes 或恢复协议。
    """

    def __init__(
        self,
        *,
        min_bytes: int = DEFAULT_CHECKPOINT_COMPRESSION_MIN_BYTES,
        compression_level: int = DEFAULT_CHECKPOINT_COMPRESSION_LEVEL,
    ) -> None:
        if min_bytes < 0:
            raise ValueError("checkpoint compression min_bytes must be non-negative")
        if not 0 <= compression_level <= 9:
            raise ValueError("checkpoint compression level must be between 0 and 9")
        self.min_bytes = int(min_bytes)
        self.compression_level = int(compression_level)
        self.delegate = JsonPlusSerializer(
            pickle_fallback=False,
            allowed_msgpack_modules=(),
        )

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        type_name, payload = self.delegate.dumps_typed(obj)
        if len(payload) < self.min_bytes:
            return type_name, payload
        compressed = zlib.compress(payload, self.compression_level)
        if len(compressed) >= len(payload):
            return type_name, payload
        return f"{CHECKPOINT_COMPRESSION_PREFIX}{type_name}", compressed

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        type_name, payload = data
        if type_name.startswith(CHECKPOINT_COMPRESSION_PREFIX):
            delegate_type = type_name[len(CHECKPOINT_COMPRESSION_PREFIX) :]
            if not delegate_type:
                raise ValueError("compressed checkpoint type is missing delegate type")
            payload = zlib.decompress(payload)
            type_name = delegate_type
        return self.delegate.loads_typed((type_name, payload))


def _checkpoint_serde() -> CompressedJsonPlusSerializer:
    """创建禁止 pickle 回退且压缩大型载荷的检查点序列化器。"""

    return CompressedJsonPlusSerializer()


def compact_official_checkpoint_database(
    path: str | Path,
    *,
    min_bytes: int = DEFAULT_CHECKPOINT_COMPRESSION_MIN_BYTES,
    compression_level: int = DEFAULT_CHECKPOINT_COMPRESSION_LEVEL,
    batch_size: int = 64,
    vacuum: bool = True,
) -> dict[str, int]:
    """原地压缩官方 SQLite saver 中尚未压缩的大型 checkpoint/write BLOB。

    该操作不反序列化业务对象，只压缩官方 serializer 已生成的字节并更新类型标签，
    因此不会改变对象语义。调用前必须确保没有运行时持有该数据库连接。
    """

    database_path = Path(path).expanduser().resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"checkpoint database not found: {database_path}")
    _validate_existing_database(database_path)
    if min_bytes < 0:
        raise ValueError("checkpoint compression min_bytes must be non-negative")
    if not 0 <= compression_level <= 9:
        raise ValueError("checkpoint compression level must be between 0 and 9")
    if batch_size <= 0:
        raise ValueError("checkpoint compaction batch_size must be positive")

    bytes_before = database_path.stat().st_size
    checkpoint_rows_compressed = 0
    write_rows_compressed = 0
    payload_bytes_before = 0
    payload_bytes_after = 0

    connection = sqlite3.connect(str(database_path), check_same_thread=False)
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("BEGIN IMMEDIATE")
        for table_name, blob_column in (
            ("checkpoints", "checkpoint"),
            ("writes", "value"),
        ):
            read_cursor = connection.execute(
                f"SELECT rowid, type, {blob_column} FROM {table_name} "
                "ORDER BY rowid"
            )
            write_cursor = connection.cursor()
            try:
                while rows := read_cursor.fetchmany(batch_size):
                    for rowid, type_name, payload in rows:
                        raw_type = str(type_name or "")
                        raw_payload = bytes(payload or b"")
                        payload_bytes_before += len(raw_payload)
                        if (
                            raw_type.startswith(CHECKPOINT_COMPRESSION_PREFIX)
                            or len(raw_payload) < min_bytes
                        ):
                            payload_bytes_after += len(raw_payload)
                            continue
                        compressed = zlib.compress(raw_payload, compression_level)
                        if len(compressed) >= len(raw_payload):
                            payload_bytes_after += len(raw_payload)
                            continue
                        write_cursor.execute(
                            f"UPDATE {table_name} SET type = ?, {blob_column} = ? "
                            "WHERE rowid = ?",
                            (
                                f"{CHECKPOINT_COMPRESSION_PREFIX}{raw_type}",
                                compressed,
                                rowid,
                            ),
                        )
                        payload_bytes_after += len(compressed)
                        if table_name == "checkpoints":
                            checkpoint_rows_compressed += 1
                        else:
                            write_rows_compressed += 1
            finally:
                read_cursor.close()
                write_cursor.close()
        connection.commit()

        serializer = CompressedJsonPlusSerializer(
            min_bytes=min_bytes,
            compression_level=compression_level,
        )
        for table_name, blob_column in (
            ("checkpoints", "checkpoint"),
            ("writes", "value"),
        ):
            for type_name, payload in connection.execute(
                f"SELECT type, {blob_column} FROM {table_name} "
                "WHERE type LIKE ?",
                (f"{CHECKPOINT_COMPRESSION_PREFIX}%",),
            ):
                serializer.loads_typed((str(type_name), bytes(payload)))

        if vacuum:
            connection.execute("VACUUM")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "checkpoint_rows_compressed": checkpoint_rows_compressed,
        "write_rows_compressed": write_rows_compressed,
        "payload_bytes_before": payload_bytes_before,
        "payload_bytes_after": payload_bytes_after,
        "database_bytes_before": bytes_before,
        "database_bytes_after": database_path.stat().st_size,
    }


def create_sqlite_checkpoint_resource(
    path: str | Path,
) -> SQLiteCheckpointResource:
    """创建由调用方显式持有的官方同步 SQLite 检查点资源。

    Args:
        path: 数据库路径，可包含 ``~`` 或相对路径。

    Returns:
        同时持有 saver、连接和规范路径的资源对象；调用方必须在生命周期结束时
        调用 ``close``。
    """

    # 第一步：展开用户目录并解析绝对路径，使检查和实际连接使用同一文件。
    database_path = Path(path).expanduser().resolve()

    # 第二步：确保父目录存在；官方 saver 只负责表，不负责创建目录。
    database_path.parent.mkdir(parents=True, exist_ok=True)

    # 第三步：在官方 saver 初始化表之前拒绝旧结构或未知结构。
    _validate_existing_database(database_path)

    # 第四步：创建允许跨线程访问的连接；线程安全仍由上层调用约束和 SQLite 负责。
    connection = sqlite3.connect(
        str(database_path),
        check_same_thread=False,
    )

    # 第五步：使用安全序列化器构造官方 saver，不重写其协议。
    saver = SqliteSaver(connection, serde=_checkpoint_serde())

    # 第六步：附加规范路径供运行时检查和诊断使用；该属性不是官方静态类型字段。
    saver.path = database_path  # type: ignore[attr-defined]

    # 第七步：把 saver 与连接封装成具有明确关闭责任的资源。
    return SQLiteCheckpointResource(
        saver=saver,
        connection=connection,
        path=database_path,
    )


def create_sqlite_checkpoint_saver(path: str | Path) -> SqliteSaver:
    """为旧调用方创建只返回 saver 的兼容对象。

    Args:
        path: SQLite 检查点数据库路径。

    Returns:
        官方同步 ``SqliteSaver``。

    生产装配必须改用 ``create_sqlite_checkpoint_resource``，这样连接能被确定性关闭。
    这里把资源附着到 saver，仅用于维持旧返回类型并保留异常退出 finalizer。
    """

    # 第一步：仍通过正式资源工厂完成路径、安全和旧结构校验。
    resource = create_sqlite_checkpoint_resource(path)

    # 第二步：把资源绑定到 saver，防止兼容调用中资源过早被回收。
    resource.saver._rag_agent_checkpoint_resource = resource  # type: ignore[attr-defined]

    # 第三步：维持历史函数的返回类型，只返回 saver。
    return resource.saver


@asynccontextmanager
async def open_async_sqlite_checkpoint_saver(
    path: str | Path,
) -> AsyncIterator[AsyncSqliteSaver]:
    """在异步上下文中打开并自动关闭官方 SQLite saver。

    Args:
        path: 异步检查点数据库路径。

    Yields:
        可用于 ``astream`` 和异步恢复路径的官方 ``AsyncSqliteSaver``。
    """

    # 第一步：规范化路径并确保父目录存在。
    database_path = Path(path).expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    # 第二步：异步 saver 接触文件前同样执行旧结构和未知结构保护。
    _validate_existing_database(database_path)

    # 第三步：让官方异步上下文管理器负责连接创建和关闭。
    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as saver:
        # 第四步：显式覆盖安全序列化器，防止依赖升级后默认配置悄然允许 pickle。
        saver.serde = _checkpoint_serde()

        # 第五步：附加规范路径，供诊断和运行时检查使用。
        saver.path = database_path  # type: ignore[attr-defined]

        # 第六步：把 saver 暴露给调用方；离开上下文后由官方实现关闭连接。
        yield saver
