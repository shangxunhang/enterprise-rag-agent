"""构建可重复、版本化且不自动激活的父子块离线索引。

Builder 负责：验证源数据和组件配置、把 cleaned-text-unit 迁移到 canonical 协议、
补充检索作用域、调用注册 Chunker、编码子块向量、写出不可变 JSONL/NumPy 产物、
可选创建 Milvus Lite 集合，并最终发布 ``IndexManifest``。相同配置和源哈希的已完成
版本可幂等复用；版本目录冲突或半成品必须显式处理。Builder 永远不修改 active pointer。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from core.diagnostics import capture_event, capture_pool, current_diagnostics

from rag.catalog.document_profile_projection import project_source_record
from rag.catalog.document_profile_registry import DocumentProfileRegistry
from rag.config.static_retrieval import ComponentConfig
from rag.embed.embedding_service import (
    encode_texts_with_hash,
    encode_texts_with_model,
)
from rag.offline.config import (
    OfflineIndexBuildConfig,
    OfflineIndexConfigLoader,
)
from rag.offline.manifest import (
    ArtifactRecord,
    IndexManifest,
    milvus_semantic_fingerprint,
    sha256_file,
)
from rag.registry.default_registrations import (
    build_default_component_registry,
)
from rag.schema.BM25Record_Schema import build_bm25_record_v1
from rag.schema.VectorIndexRecord_Schema import (
    build_vector_index_record_v2,
)
from rag.schema.cleaned_text_unit import (
    CleanedTextUnitSchema,
    migrate_legacy_cleaned_text_unit_payload,
)
from rag.util.jsonl_utils import iter_jsonl_paths, load_jsonl_dicts


def _json_hash(payload: Any) -> str:
    """对任意 JSON 兼容对象的 canonical 表示计算 SHA-256。"""

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _source_dataset_hash(path: Path) -> str:
    """Hash exactly the JSON/JSONL files consumed from a source path."""

    if path.is_file():
        return sha256_file(path)

    candidates = list(iter_jsonl_paths(path))
    if not candidates:
        raise ValueError(
            f"cleaned unit source directory contains no JSON data files: {path}"
        )

    digest = hashlib.sha256()
    for item in candidates:
        relative = item.relative_to(path).as_posix().encode("utf-8")
        content_hash = sha256_file(item).encode("ascii")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(item.stat().st_size.to_bytes(16, "big", signed=False))
        digest.update(content_hash)
    return digest.hexdigest()


def _recursive_assets_hash(path: Path) -> str:
    """Hash all files under a structured-asset root in stable relative order."""

    if not path.is_dir():
        raise FileNotFoundError(f"structured assets root not found: {path}")
    files = sorted(item for item in path.rglob("*") if item.is_file())
    if not files:
        raise ValueError(f"structured assets root contains no files: {path}")
    digest = hashlib.sha256()
    for item in files:
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(item.stat().st_size.to_bytes(16, "big", signed=False))
        digest.update(sha256_file(item).encode("ascii"))
    return digest.hexdigest()


def _profile_aware_source_hash(
    source_path: Path,
    document_profiles_path: Path | None,
    structured_assets_root: Path | None = None,
) -> str:
    """Hash cleaned source, reviewed catalog and structured assets together."""

    dataset_hash = _source_dataset_hash(source_path)
    if document_profiles_path is None and structured_assets_root is None:
        return dataset_hash

    payload: dict[str, Any] = {"cleaned_source_sha256": dataset_hash}
    if document_profiles_path is not None:
        payload["document_profiles_sha256"] = sha256_file(
            document_profiles_path
        )
    if structured_assets_root is not None:
        payload["structured_assets_sha256"] = _recursive_assets_hash(
            structured_assets_root
        )
    return _json_hash(payload)


def _attach_structured_asset(
    record: dict[str, Any],
    *,
    structured_assets_root: Path | None,
) -> dict[str, Any]:
    """Attach a referenced table JSON to one profiled CleanedTextUnit."""

    if structured_assets_root is None or record.get("unit_type") != "table":
        return record
    extra = dict(record.get("extra") or {})
    table_uri = extra.get("table_uri")
    if table_uri in (None, ""):
        producer = extra.get("producer_top_level_extensions")
        if isinstance(producer, dict):
            table_uri = producer.get("table_uri")
    if table_uri in (None, ""):
        raise ValueError(
            "structured table unit is missing table_uri; "
            f"unit_id={record.get('unit_id')!r}"
        )
    relative = Path(str(table_uri))
    # PDF parsers may expose an image URI for a table region.  Only reviewed
    # relative JSON assets participate in deterministic row reconstruction.
    if relative.suffix.lower() != ".json":
        return record
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe structured table JSON URI: {table_uri!r}")
    asset_path = (structured_assets_root / relative).resolve()
    root = structured_assets_root.resolve()
    try:
        asset_path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"structured table URI escapes root: {table_uri!r}") from exc
    if not asset_path.is_file():
        raise FileNotFoundError(
            "structured table asset not found; "
            f"unit_id={record.get('unit_id')!r} path={asset_path}"
        )
    payload = json.loads(asset_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise ValueError(f"invalid structured table asset: {asset_path}")
    extra["structured_table"] = payload
    extra["structured_table_asset"] = {
        "uri": relative.as_posix(),
        "sha256": sha256_file(asset_path),
    }
    projected = dict(record)
    projected["extra"] = extra
    return projected


def _is_explicit_local_model_path(value: str) -> bool:
    """判断模型字符串是否明确表达本地文件系统路径。

    Windows 盘符、Unix 绝对路径、UNC 路径和 ``./``、``../`` 相对路径都视为本地
    路径；普通 ``organization/model`` Hugging Face 标识不视为本地路径。
    """

    normalized = str(value or "").strip()
    return bool(
        re.match(r"^[A-Za-z]:[\\/]", normalized)
        or normalized.startswith(
            ("/", "\\\\", "./", "../", ".\\", "..\\")
        )
    )


def _write_jsonl(
    path: Path,
    records: list[dict[str, Any]],
) -> None:
    """以确定性键顺序覆盖写入紧凑 JSONL 文件。

    Args:
        path: 目标文件。
        records: 已按稳定业务身份排序的记录列表。

    本函数不是追加式或原子发布。版本目录在构建前必须为空，因此覆盖写入只发生在
    尚未发布的私有构建目录中；最终完成边界由 Manifest 发布表示。
    """

    # 第一步：创建产物父目录。
    path.parent.mkdir(parents=True, exist_ok=True)

    # 第二步：每条记录使用排序键和紧凑分隔符写成一行。
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            handle.write("\n")


@dataclass(frozen=True)
class OfflineIndexBuildResult:
    """描述离线索引验证或构建的结构化结果。

    Attributes:
        status: ``success`` 或幂等复用时的 ``already_exists``。
        index_version: 由索引兼容性身份产生的版本。
        config_hash: 完整构建配置哈希。
        source_hash: 源数据文件内容哈希。
        output_dir: 版本化产物目录。
        manifest_path: 完成构建后的 Manifest 路径；仅验证时为 ``None``。
        document_count: 去重文档数量。
        parent_chunk_count: 父块数量。
        child_chunk_count: 子块和向量数量。
        validation_only: 是否只完成了前置验证。
    """

    status: str
    index_version: str
    config_hash: str
    source_hash: str
    output_dir: str
    manifest_path: str | None
    document_count: int
    parent_chunk_count: int
    child_chunk_count: int
    validation_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        """返回 dataclass 字段的浅拷贝字典。"""

        return self.__dict__.copy()


class OfflineIndexBuilder:
    """协调离线数据迁移、Chunk、Embedding、存储和 Manifest 发布。

    Attributes:
        project_root: 相对路径统一解析基准。
        registry: 已注册 Chunker 等组件的事实源。
    """

    def __init__(
        self,
        *,
        registry=None,
        project_root: str | Path | None = None,
    ) -> None:
        """初始化项目根目录和组件注册表。

        Args:
            registry: 可选自定义组件注册表，测试和插件环境可注入。
            project_root: 可选项目根目录；省略时使用配置加载器推导的仓库根。
        """

        # 第一步：固定绝对项目根目录，后续所有配置相对路径都基于该目录。
        self.project_root = (
            Path(project_root).expanduser().resolve()
            if project_root
            else OfflineIndexConfigLoader.default_project_root()
        )

        # 第二步：未注入注册表时构建项目默认插件注册表。
        self.registry = registry or build_default_component_registry()

    def resolve(self, path: str | Path) -> Path:
        """把绝对或项目相对路径解析为规范化绝对路径。"""

        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.project_root / candidate).resolve()

    def validate(
        self,
        config: OfflineIndexBuildConfig,
    ) -> OfflineIndexBuildResult:
        """验证源、模型路径和 Chunker 注册，并推导构建身份。

        Args:
            config: 严格离线索引构建配置。

        Returns:
            ``validation_only=True`` 的结果，不创建版本目录或加载模型。

        Raises:
            FileNotFoundError: 源数据或明确本地模型路径不存在时抛出。
            ValueError: Chunker 未注册时抛出，并列出可用版本。
        """

        # 第一步：解析并检查 cleaned-text-unit 源文件。
        source_path = self.resolve(config.source.path)
        if not source_path.exists():
            raise FileNotFoundError(
                f"cleaned unit source not found: {source_path}"
            )

        # 第二步：Profile-aware 构建必须绑定存在且可严格加载的目录文件。
        document_profiles_path: Path | None = None
        if config.source.document_profiles_path is not None:
            document_profiles_path = self.resolve(
                config.source.document_profiles_path
            )
            if not document_profiles_path.is_file():
                raise FileNotFoundError(
                    "document profile catalog not found: "
                    f"{document_profiles_path}"
                )
            DocumentProfileRegistry.from_jsonl(document_profiles_path)
        elif config.source.require_document_profiles:
            raise ValueError(
                "strict profile-aware build requires document_profiles_path"
            )

        structured_assets_root: Path | None = None
        if config.source.structured_assets_root is not None:
            structured_assets_root = self.resolve(
                config.source.structured_assets_root
            )
            if not structured_assets_root.is_dir():
                raise FileNotFoundError(
                    "structured assets root not found: "
                    f"{structured_assets_root}"
                )

        # 第三步：真实模型模式中，仅对明确本地路径做前置存在性检查。
        if config.embedding.mode == "model":
            model_name = str(
                config.embedding.model_name or ""
            ).strip()
            if (
                _is_explicit_local_model_path(model_name)
                and not Path(model_name).expanduser().exists()
            ):
                raise FileNotFoundError(
                    f"embedding model path not found: {model_name}"
                )

        # 第四步：Chunker 必须以名称和版本精确注册，不能隐式回退其他实现。
        if not self.registry.contains(
            category="chunker",
            name=config.chunker.name,
            version=config.chunker.version,
        ):
            available = [
                f"{item.name}@{item.version}"
                for item in self.registry.list_components(
                    category="chunker"
                )
            ]
            raise ValueError(
                f"unknown chunker "
                f"{config.chunker.name}@{config.chunker.version}; "
                f"available={available}"
            )

        # 第五步：推导索引版本、版本目录和复合源内容哈希，不产生写入副作用。
        index_version = config.index_version()
        output_dir = (
            self.resolve(config.outputs.root_dir) / index_version
        )
        return OfflineIndexBuildResult(
            status="success",
            index_version=index_version,
            config_hash=config.config_hash(),
            source_hash=_profile_aware_source_hash(
                source_path,
                document_profiles_path,
                structured_assets_root,
            ),
            output_dir=str(output_dir),
            manifest_path=None,
            document_count=0,
            parent_chunk_count=0,
            child_chunk_count=0,
            validation_only=True,
        )

    def build(
        self,
        config: OfflineIndexBuildConfig,
    ) -> OfflineIndexBuildResult:
        """构建一个不可变版本化父子块索引。

        Args:
            config: 严格离线索引配置。

        Returns:
            新构建或幂等复用的 ``OfflineIndexBuildResult``。

        Raises:
            ValueError: 版本碰撞、半成品目录、作用域冲突、空子块、向量形状/维度错误
                或 Milvus 持久化行数不一致时抛出。
        """

        # 第一步：执行无副作用前置验证并确定版本目录、源哈希和 Manifest 路径。
        if current_diagnostics().enabled:
            capture_event("I-INDEX", {"phase": "BEGIN", "config": config.model_dump(mode="json")}, unit_id="A07", operation_id="offline_build")
        validated = self.validate(config)
        source_path = self.resolve(config.source.path)
        output_dir = Path(validated.output_dir)
        manifest_path = output_dir / "index_manifest.json"

        # 第二步：完成版本目录若存在 Manifest，只允许相同配置和相同源内容幂等复用。
        if manifest_path.exists():
            existing = IndexManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            if (
                existing.config_hash != config.config_hash()
                or existing.source_hash != validated.source_hash
            ):
                raise ValueError(
                    f"index version collision: {manifest_path}"
                )
            return OfflineIndexBuildResult(
                status="already_exists",
                index_version=existing.index_version,
                config_hash=existing.config_hash,
                source_hash=existing.source_hash,
                output_dir=str(output_dir),
                manifest_path=str(manifest_path),
                document_count=existing.document_count,
                parent_chunk_count=existing.parent_chunk_count,
                child_chunk_count=existing.child_chunk_count,
            )

        # 第三步：没有 Manifest 但目录非空表示未完成或冲突构建，禁止覆盖继续。
        if output_dir.exists() and any(output_dir.iterdir()):
            raise ValueError(
                f"incomplete non-empty index directory exists: "
                f"{output_dir}"
            )
        output_dir.mkdir(parents=True, exist_ok=True)

        # 第四步：读取全部源记录；Stage C 可由 reviewed profile catalog 接管治理与作用域。
        source_records = load_jsonl_dicts(source_path)
        document_profiles_path = (
            self.resolve(config.source.document_profiles_path)
            if config.source.document_profiles_path is not None
            else None
        )
        profile_registry = (
            DocumentProfileRegistry.from_jsonl(document_profiles_path)
            if document_profiles_path is not None
            else None
        )
        profile_catalog_sha256 = (
            sha256_file(document_profiles_path)
            if document_profiles_path is not None
            else None
        )
        structured_assets_root = (
            self.resolve(config.source.structured_assets_root)
            if config.source.structured_assets_root is not None
            else None
        )
        structured_assets_sha256 = (
            _recursive_assets_hash(structured_assets_root)
            if structured_assets_root is not None
            else None
        )

        records: list[dict[str, Any]] = []
        source_document_ids: set[str] = set()
        for source_record in source_records:
            if profile_registry is not None:
                document_id = str(source_record.get("doc_id") or "").strip()
                if not document_id:
                    raise ValueError(
                        "profile-aware index source contains a record without doc_id; "
                        f"unit_id={source_record.get('unit_id')!r}"
                    )
                source_document_ids.add(document_id)
                try:
                    profile = profile_registry.get(document_id)
                except KeyError as exc:
                    raise ValueError(
                        "index source contains an unprofiled document; "
                        f"document_id={document_id!r}"
                    ) from exc
                projected = project_source_record(
                    source_record,
                    profile=profile,
                    catalog_sha256=str(profile_catalog_sha256),
                )
                if projected is not None:
                    records.append(
                        _attach_structured_asset(
                            projected,
                            structured_assets_root=structured_assets_root,
                        )
                    )
                continue

            raw_should_index = source_record.get("should_index", True)
            raw_is_gold = source_record.get("is_gold_reference", False)
            if not isinstance(raw_should_index, bool):
                raise TypeError(
                    "should_index must be a boolean when present; "
                    f"unit_id={source_record.get('unit_id')!r}"
                )
            if not isinstance(raw_is_gold, bool):
                raise TypeError(
                    "is_gold_reference must be a boolean when present; "
                    f"unit_id={source_record.get('unit_id')!r}"
                )
            if raw_is_gold and raw_should_index:
                raise ValueError(
                    "gold reference records must not enter the retrieval index; "
                    f"unit_id={source_record.get('unit_id')!r}"
                )

            migrated = migrate_legacy_cleaned_text_unit_payload(source_record)
            CleanedTextUnitSchema.model_validate(migrated)
            if not raw_should_index:
                continue
            records.append(migrated)

        if profile_registry is not None:
            profile_document_ids = {
                profile.document_id for profile in profile_registry.all()
            }
            missing_source_documents = sorted(
                profile_document_ids - source_document_ids
            )
            if missing_source_documents:
                raise ValueError(
                    "document profile catalog contains documents absent from "
                    "the cleaned source: "
                    + ", ".join(missing_source_documents)
                )

        # Profile-aware CleanedTextUnit is itself an immutable Stage C artifact.
        profiled_units_path: Path | None = None
        if profile_registry is not None:
            profiled_units_path = (
                output_dir / "profiled_cleaned_text_units.jsonl"
            )
            _write_jsonl(profiled_units_path, records)

        # 第五步：补充检索作用域；Profile-aware 记录已由目录值覆盖旧默认值。
        doc_scopes: dict[str, tuple[str, str]] = {}
        for record in records:
            record["tenant_id"] = (
                record.get("tenant_id")
                or config.source.tenant_id
                or "default"
            )
            record["kb_id"] = (
                record.get("kb_id")
                or config.source.kb_id
                or "default"
            )
            record["file_id"] = (
                record.get("file_id")
                or record.get("source_name")
                or record.get("doc_id")
                or ""
            )

            # 同一物理索引中的 doc_id 必须全局唯一，不能跨租户/知识库复用。
            doc_id = str(record.get("doc_id") or "").strip()
            if doc_id:
                scope = (
                    record["tenant_id"],
                    record["kb_id"],
                )
                previous_scope = doc_scopes.setdefault(
                    doc_id,
                    scope,
                )
                if previous_scope != scope:
                    raise ValueError(
                        "doc_id must be globally unique inside one "
                        "physical index; "
                        f"doc_id={doc_id!r} appears in scopes "
                        f"{previous_scope!r} and {scope!r}"
                    )

        # 第六步：把确定性 created_at 注入 Chunker 参数，并通过注册表创建精确版本实现。
        component_params = dict(config.chunker.params)
        component_params.setdefault(
            "deterministic_created_at",
            config.deterministic_created_at,
        )
        chunker_config = ComponentConfig(
            name=config.chunker.name,
            version=config.chunker.version,
            enabled=True,
            params=component_params,
        )
        chunker = self.registry.build(
            category="chunker",
            config=chunker_config,
        )
        chunk_result = chunker.chunk_records(records)
        capture_pool("I-CHUNK", chunk_result.parents, unit_id="A05", operation_id="offline_chunk_parents", phase="PARENTS")
        capture_pool("I-CHUNK", chunk_result.children, unit_id="A05", operation_id="offline_chunk_children", phase="CHILDREN")

        # 第七步：按稳定块 ID 排序，使 JSONL 行、向量行和后续哈希跨运行一致。
        parents = sorted(
            chunk_result.parents,
            key=lambda item: str(
                item.get("parent_chunk_id") or ""
            ),
        )
        children = sorted(
            chunk_result.children,
            key=lambda item: str(
                item.get("child_chunk_id") or ""
            ),
        )

        # 第八步：在所有父子块 extra 中固化索引版本、完整配置哈希和数据集版本。
        index_version = validated.index_version
        current_config_hash = config.config_hash()
        for record in [*parents, *children]:
            extra = dict(record.get("extra") or {})
            if profile_registry is not None:
                profile = extra.get("document_profile")
                if not isinstance(profile, dict):
                    raise ValueError(
                        "all Stage C parent/child chunks require document_profile; "
                        f"chunk_id={record.get('child_chunk_id') or record.get('parent_chunk_id')!r}"
                    )
                if str(profile.get("document_id") or "") != str(record.get("doc_id") or ""):
                    raise ValueError("chunk document identity conflicts with document_profile")
                if str(profile.get("kb_id") or "") != str(record.get("kb_id") or ""):
                    raise ValueError("chunk kb_id conflicts with document_profile")
                if record.get("child_chunk_id") and not isinstance(
                    extra.get("type_aware_chunking"), dict
                ):
                    raise ValueError(
                        "all Stage C child chunks require type_aware_chunking; "
                        f"child_chunk_id={record.get('child_chunk_id')!r}"
                    )
            extra.update(
                {
                    "offline_index_version": index_version,
                    "offline_index_config_hash": current_config_hash,
                    "dataset_version": config.dataset_version,
                }
            )
            record["extra"] = extra

        # 第九步：先发布父块和子块不可变 JSONL 产物。
        parent_path = output_dir / "parent_chunks.jsonl"
        child_path = output_dir / "child_chunks.jsonl"
        _write_jsonl(parent_path, parents)
        _write_jsonl(child_path, children)

        # 第十步：对子块正文编码；零子块表示源或 Chunker 配置无效，禁止生成空索引。
        texts = [str(item.get("text") or "") for item in children]
        if current_diagnostics().enabled:
            capture_event("I-ENCODE", {"phase": "ROW_IDENTITY", "child_ids": [item["child_chunk_id"] for item in children],
                "text_hashes": [hashlib.sha256(text.encode()).hexdigest() for text in texts]}, unit_id="A06", operation_id="offline_encode_rows")
        if not texts:
            raise ValueError("chunker produced zero child chunks")

        if config.embedding.mode == "hash":
            vectors, embedding_model, embedding_version = (
                encode_texts_with_hash(
                    texts,
                    dim=config.embedding.dim,
                )
            )
        else:
            vectors, embedding_model, embedding_version = (
                encode_texts_with_model(
                    texts,
                    model_name=config.embedding.model_name,
                    device=config.embedding.device,
                    batch_size=config.embedding.batch_size,
                    embedding_version=config.embedding.version,
                )
            )

        # 第十一步：验证向量矩阵一行对应一个子块，且实际维度与配置完全一致。
        if (
            vectors.ndim != 2
            or int(vectors.shape[0]) != len(texts)
        ):
            raise ValueError(
                "embedding output must be a 2D matrix with one "
                "row per child chunk; "
                f"got shape={getattr(vectors, 'shape', None)} "
                f"child_count={len(texts)}"
            )
        embedding_dim = int(vectors.shape[1])
        if embedding_dim != int(config.embedding.dim):
            raise ValueError(
                "embedding dimension mismatch: "
                f"configured={config.embedding.dim} "
                f"actual={embedding_dim} model={embedding_model}"
            )

        # 第十二步：以 float32 NumPy 文件保存向量，并禁止 pickle 对象。
        vectors_path = output_dir / "child_vectors.npy"
        np.save(
            vectors_path,
            vectors.astype("float32"),
            allow_pickle=False,
        )

        # 第十三步：构造每个子块的逻辑 VectorIndexRecord，固化模型和索引血缘。
        index_name = f"{index_version}_children"
        vector_records = [
            build_vector_index_record_v2(
                child_chunk=child,
                embedding_model=embedding_model,
                embedding_dim=embedding_dim,
                embedding_version=embedding_version,
                index_name=index_name,
                index_version=index_version,
                vector_db=config.index.backend,
                created_at=config.deterministic_created_at,
                extra={
                    "dataset_version": config.dataset_version,
                    "offline_index_config_hash": current_config_hash,
                    "_require_profile_lineage": profile_registry is not None,
                },
            )
            for child in children
        ]
        vector_records_path = (
            output_dir / "vector_index_records.jsonl"
        )
        _write_jsonl(vector_records_path, vector_records)

        # Dense and BM25 use one metadata contract derived from the same ChildChunk.
        bm25_records: list[dict[str, Any]] = []
        bm25_records_path: Path | None = None
        if profile_registry is not None:
            bm25_records = [
                build_bm25_record_v1(
                    child_chunk=child,
                    dataset_version=config.dataset_version,
                    offline_index_config_hash=current_config_hash,
                )
                for child in children
            ]
            bm25_records_path = output_dir / "bm25_records.jsonl"
            _write_jsonl(bm25_records_path, bm25_records)

        # 第十四步：可选创建 Milvus Lite 在线存储，并在关闭重开后校验持久化行数。
        milvus_path: Path | None = None
        if config.index.backend == "milvus_lite":
            from pymilvus import MilvusClient
            from rag.vector_store.milvus_child_chunk_store import (
                build_milvus_child_chunk_record,
                create_or_reset_child_chunk_collection,
                ensure_milvus_lite_windows_manifest_replace_compatibility,
                insert_child_chunk_records,
            )

            ensure_milvus_lite_windows_manifest_replace_compatibility()
            milvus_path = output_dir / "milvus.db"
            client = MilvusClient(str(milvus_path))
            create_or_reset_child_chunk_collection(
                client=client,
                collection_name=config.index.collection_name,
                dim=embedding_dim,
                metric_type=config.index.metric_type,
                recreate=config.index.recreate,
                max_text_chars=config.index.max_text_chars,
            )

            try:
                # 将每个 canonical 子块与同一行向量映射为物理 Milvus 记录。
                physical_records = [
                    build_milvus_child_chunk_record(
                        child_chunk=child,
                        vector=vector,
                        embedding_model=embedding_model,
                        embedding_dim=embedding_dim,
                        embedding_version=embedding_version,
                        max_text_chars=config.index.max_text_chars,
                        index_name=index_name,
                        index_version=index_version,
                    )
                    for child, vector in zip(children, vectors)
                ]
                inserted_count = insert_child_chunk_records(
                    client=client,
                    collection_name=config.index.collection_name,
                    records=physical_records,
                    batch_size=config.index.insert_batch_size,
                )
                if inserted_count != len(children):
                    raise ValueError(
                        "Milvus insert count mismatch: "
                        f"expected={len(children)} "
                        f"actual={inserted_count}"
                    )
            finally:
                # 不显式 flush：部分 Windows Milvus Lite 版本用 os.rename 替换已存在
                # Manifest 会触发 WinError 183。关闭并重开后核对实体数是更强持久化边界。
                close = getattr(client, "close", None)
                if callable(close):
                    close()

            # 重新打开数据库，确认集合和全部实体已经真正持久化。
            verification_client = MilvusClient(str(milvus_path))
            try:
                has_collection = getattr(
                    verification_client,
                    "has_collection",
                    None,
                )
                if (
                    callable(has_collection)
                    and not has_collection(
                        config.index.collection_name
                    )
                ):
                    raise ValueError(
                        "Milvus collection missing after reopen: "
                        f"{config.index.collection_name}"
                    )

                stats = verification_client.get_collection_stats(
                    collection_name=config.index.collection_name
                )
                persisted_count = int(
                    (stats or {}).get("row_count", -1)
                )
                if persisted_count != len(children):
                    raise ValueError(
                        "Milvus persisted entity count mismatch "
                        "after reopen: "
                        f"expected={len(children)} "
                        f"actual={persisted_count}"
                    )
            finally:
                close = getattr(
                    verification_client,
                    "close",
                    None,
                )
                if callable(close):
                    close()

        # 第十五步：为所有不可变产物计算内容哈希和记录数量。
        artifacts: dict[str, ArtifactRecord] = {
            "parent_chunks": ArtifactRecord(
                path=str(parent_path),
                sha256=sha256_file(parent_path),
                record_count=len(parents),
            ),
            "child_chunks": ArtifactRecord(
                path=str(child_path),
                sha256=sha256_file(child_path),
                record_count=len(children),
            ),
            "vectors": ArtifactRecord(
                path=str(vectors_path),
                sha256=sha256_file(vectors_path),
                record_count=len(children),
            ),
            "vector_index_records": ArtifactRecord(
                path=str(vector_records_path),
                sha256=sha256_file(vector_records_path),
                record_count=len(vector_records),
            ),
        }
        if profiled_units_path is not None:
            artifacts["profiled_cleaned_text_units"] = ArtifactRecord(
                path=str(profiled_units_path),
                sha256=sha256_file(profiled_units_path),
                record_count=len(records),
            )
        if bm25_records_path is not None:
            artifacts["bm25_records"] = ArtifactRecord(
                path=str(bm25_records_path),
                sha256=sha256_file(bm25_records_path),
                record_count=len(bm25_records),
            )

        # 第十六步：Milvus 使用逻辑语义指纹，不依赖会被运行时改写的数据库字节。
        if milvus_path is not None and milvus_path.exists():
            artifacts["milvus_lite"] = ArtifactRecord(
                path=str(milvus_path),
                sha256=milvus_semantic_fingerprint(
                    collection_name=config.index.collection_name,
                    record_count=len(children),
                    embedding_dim=config.embedding.dim,
                    metric_type=config.index.metric_type,
                    child_chunks_sha256=(
                        artifacts["child_chunks"].sha256
                    ),
                    vector_records_sha256=(
                        artifacts[
                            "vector_index_records"
                        ].sha256
                    ),
                ),
                record_count=len(children),
                kind=(
                    "directory" if milvus_path.is_dir() else "file"
                ),
                integrity_mode="milvus_semantic_v1",
            )

        # 第十七步：综合配置、源和全部产物哈希形成构建可重复性指纹。
        reproducibility_payload = {
            "config_hash": current_config_hash,
            "source_hash": validated.source_hash,
            "artifacts": {
                name: item.sha256
                for name, item in sorted(artifacts.items())
            },
        }

        # 第十八步：固化源数据与 Profile Catalog 的独立哈希和过滤计数。
        source_metadata: dict[str, Any] = {
            "path": str(source_path),
            "format": config.source.format,
            "sha256": validated.source_hash,
            "cleaned_source_sha256": _source_dataset_hash(source_path),
            "input_record_count": len(source_records),
            "record_count": len(records),
        }
        if document_profiles_path is not None:
            source_metadata.update(
                {
                    "document_profiles_path": str(document_profiles_path),
                    "document_profiles_sha256": str(
                        profile_catalog_sha256
                    ),
                    "profile_document_count": len(
                        profile_registry.all()
                        if profile_registry is not None
                        else []
                    ),
                    "profiled_source_document_count": len(
                        source_document_ids
                    ),
                }
            )
        if structured_assets_root is not None:
            source_metadata.update(
                {
                    "structured_assets_root": str(structured_assets_root),
                    "structured_assets_sha256": str(
                        structured_assets_sha256
                    ),
                }
            )

        # 第十九步：固化组件实现、执行 metadata、模型协议、计数和产物清单。
        plugin_metadata = getattr(chunker, "plugin_metadata", None)
        index_metadata = {
            **config.index.model_dump(mode="json"),
            "index_name": index_name,
            "output_dir": str(output_dir),
        }
        if bm25_records_path is not None:
            index_metadata["bm25_records_path"] = str(bm25_records_path)
        if profiled_units_path is not None:
            index_metadata["profiled_cleaned_text_units_path"] = str(
                profiled_units_path
            )
        manifest = IndexManifest(
            index_version=index_version,
            build_id=config.build_id,
            dataset_version=config.dataset_version,
            config_hash=current_config_hash,
            source_hash=validated.source_hash,
            source=source_metadata,
            chunker={
                "name": config.chunker.name,
                "version": config.chunker.version,
                "params": component_params,
                "implementation": (
                    plugin_metadata.implementation
                    if plugin_metadata
                    else chunker.__class__.__name__
                ),
                "execution": chunker.execution_metadata(),
            },
            embedding={
                "mode": config.embedding.mode,
                "name": config.embedding.name,
                "model": embedding_model,
                "version": embedding_version,
                "dim": embedding_dim,
                "device": config.embedding.device,
                "batch_size": config.embedding.batch_size,
                "normalize_embeddings": True,
            },
            index=index_metadata,
            document_count=len(
                {
                    str(item.get("doc_id"))
                    for item in records
                    if item.get("doc_id")
                }
            ),
            parent_chunk_count=len(parents),
            child_chunk_count=len(children),
            artifacts=artifacts,
            created_at=config.deterministic_created_at,
            reproducibility_hash=_json_hash(
                reproducibility_payload
            ),
            notes=config.notes,
        )

        # 第二十步：Manifest 原子发布表示该版本构建完成；此前目录都视为未完成私有产物。
        manifest.write(manifest_path)
        capture_event("I-INDEX", {"phase": "RESULT", "manifest_ref": str(manifest_path),
            "index_version": index_version, "config_hash": current_config_hash, "source_hash": validated.source_hash,
            "parent_count": len(parents), "child_count": len(children)}, unit_id="A07", operation_id="offline_build")

        # 第二十一步：返回构建结果，但不更新 active pointer。
        return OfflineIndexBuildResult(
            status="success",
            index_version=index_version,
            config_hash=current_config_hash,
            source_hash=validated.source_hash,
            output_dir=str(output_dir),
            manifest_path=str(manifest_path),
            document_count=manifest.document_count,
            parent_chunk_count=len(parents),
            child_chunk_count=len(children),
        )
