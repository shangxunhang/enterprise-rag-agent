"""验证一个版本化离线 RAG 索引的不可变产物和在线存储一致性。

验证流程只读取 Manifest 和构建产物，不激活索引、不更新 active pointer。普通文件
检查内容哈希、记录数量、向量形状和父子块血缘；Milvus Lite 进一步检查集合存在、
实体数量和抽样向量能否自检索到对应子块。该模块属于构建后的质量门禁，索引切换由
``IndexLifecycleManager`` 单独负责。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from rag.offline.manifest import (
    IndexManifest,
    effective_artifact_integrity_mode,
    milvus_semantic_fingerprint,
    sha256_path,
)
from rag.store.parent_chunk_store import load_jsonl_dicts


# 注入工厂便于测试使用 Fake MilvusClient，也避免模块导入时强依赖 pymilvus。
MilvusClientFactory = Callable[[str], Any]


def _as_int(value: Any, default: int = -1) -> int:
    """尽力把值转换为整数，失败时返回默认值。

    该宽松转换只用于读取第三方统计和历史 Manifest；严格配置仍由 Pydantic 负责。
    """

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_artifact_path(
    manifest_path: Path,
    raw_path: str,
) -> Path:
    """把 Manifest 内产物路径解析为绝对路径。

    相对路径以 Manifest 所在目录为基准，使整个版本目录可以整体移动或复制。
    """

    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (manifest_path.parent / candidate).resolve()


def _pick_evenly_spaced_indices(
    size: int,
    sample_count: int,
) -> list[int]:
    """从 ``[0, size)`` 中选择近似等距且不重复的样本下标。

    Args:
        size: 总记录数。
        sample_count: 目标抽样数量。

    Returns:
        升序下标列表。无记录或非正样本数返回空列表；样本数不少于总数时返回全部。
    """

    # 没有可抽样数据或请求数量无效时不生成索引。
    if size <= 0 or sample_count <= 0:
        return []

    # 请求覆盖全部记录时直接返回完整范围。
    if sample_count >= size:
        return list(range(size))

    # 单样本固定选择首条，保持历史行为。
    if sample_count == 1:
        return [0]

    # 在首尾之间线性插值并四舍五入，集合去重后排序。
    last = size - 1
    return sorted(
        {
            round(index * last / (sample_count - 1))
            for index in range(sample_count)
        }
    )


def _normalize_search_hits(raw: Any) -> list[dict[str, Any]]:
    """把不同 pymilvus 返回形状统一为自检所需字段。

    Args:
        raw: ``[[hit, ...]]``、``[hit, ...]`` 或空值。

    Returns:
        每项包含 child_chunk_id、doc_id、text 和 score 的字典列表。非字典 hit 跳过。
    """

    if not raw:
        return []

    # 单查询 search 常返回外层查询列表；兼容已经展平的测试实现。
    hits = (
        raw[0]
        if isinstance(raw, list)
        and raw
        and isinstance(raw[0], list)
        else raw
    )

    normalized: list[dict[str, Any]] = []
    for item in hits or []:
        if not isinstance(item, dict):
            continue

        # 新客户端可能把输出字段放入 entity，旧适配器可能直接放在 hit 顶层。
        entity = (
            item.get("entity")
            if isinstance(item.get("entity"), dict)
            else {}
        )
        chunk_id = (
            entity.get("child_chunk_id")
            or entity.get("chunk_id")
            or item.get("id")
            or item.get("chunk_id")
        )

        normalized.append(
            {
                "child_chunk_id": str(chunk_id or ""),
                "doc_id": str(
                    entity.get("doc_id")
                    or item.get("doc_id")
                    or ""
                ),
                "text": str(
                    entity.get("text")
                    or item.get("text")
                    or ""
                ),
                "score": item.get(
                    "distance",
                    item.get("score"),
                ),
            }
        )

    return normalized


@dataclass(frozen=True)
class VerificationCheck:
    """描述一个独立验证项及其诊断信息。

    Attributes:
        name: 稳定检查名称。
        status: ``passed`` 或 ``failed``。
        details: 预期值、实际值、错误或样本等诊断数据。
    """

    name: str
    status: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 兼容检查记录。"""

        return {
            "name": self.name,
            "status": self.status,
            "details": self.details,
        }


@dataclass(frozen=True)
class OfflineIndexVerificationResult:
    """汇总一次索引验证的状态、检查、指标和自检检索明细。

    Attributes:
        status: 全部检查通过时为 ``success``，否则为 ``failed``。
        manifest_path: 实际验证的 Manifest 绝对路径。
        index_version: Manifest 成功解析后的索引版本，否则为 ``None``。
        checks: 按执行顺序记录的独立检查。
        metrics: 记录数量、向量统计、Milvus 行数和失败检查等聚合信息。
        self_retrieval: Milvus 抽样自检的逐样本结果。
    """

    status: str
    manifest_path: str
    index_version: str | None
    checks: list[VerificationCheck]
    metrics: dict[str, Any]
    self_retrieval: list[dict[str, Any]]

    @property
    def passed(self) -> bool:
        """返回是否全部验证通过。"""

        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        """返回带稳定报告版本的 JSON 兼容字典。"""

        return {
            "schema_version": (
                "offline_index_verification_report_v1"
            ),
            "status": self.status,
            "manifest_path": self.manifest_path,
            "index_version": self.index_version,
            "checks": [item.to_dict() for item in self.checks],
            "metrics": self.metrics,
            "self_retrieval": self.self_retrieval,
        }

    def write(self, path: str | Path) -> Path:
        """把完整验证报告写为缩进 JSON。

        Args:
            path: 报告目标路径。

        Returns:
            解析后的绝对目标路径。
        """

        # 第一步：解析绝对路径并创建父目录。
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        # 第二步：保留中文、完整检查和自检明细写入报告。
        target.write_text(
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return target


class OfflineIndexVerifier:
    """验证 Manifest、不可变产物、向量协议和 Milvus 在线一致性。

    Attributes:
        _milvus_client_factory: 可选注入的 MilvusClient 工厂。

    验证器不会抛出大多数“验证失败”，而是记录 failed check 并返回报告；只有代码
    调用或未捕获的底层错误才会越过报告边界。
    """

    # 无论后端是什么，这四个不可变产物都必须存在。
    REQUIRED_ARTIFACTS = (
        "parent_chunks",
        "child_chunks",
        "vectors",
        "vector_index_records",
    )

    def __init__(
        self,
        *,
        milvus_client_factory: MilvusClientFactory | None = None,
    ) -> None:
        """保存可选 Milvus 客户端工厂。"""

        self._milvus_client_factory = milvus_client_factory

    def _client_factory(self) -> MilvusClientFactory:
        """返回注入工厂或延迟导入的默认 pymilvus 工厂。

        Raises:
            RuntimeError: 需要验证 Milvus 但当前环境未安装 pymilvus 时抛出。
        """

        if self._milvus_client_factory is not None:
            return self._milvus_client_factory

        try:
            from pymilvus import MilvusClient
        except ImportError as exc:  # pragma: no cover - 取决于本地环境
            raise RuntimeError(
                "pymilvus is required to verify a milvus_lite index; "
                "install the Step 11.2 dependencies first"
            ) from exc

        return lambda uri: MilvusClient(uri)

    @staticmethod
    def _check(
        checks: list[VerificationCheck],
        *,
        name: str,
        condition: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        """把布尔条件追加为统一 passed/failed 检查记录。"""

        checks.append(
            VerificationCheck(
                name=name,
                status="passed" if condition else "failed",
                details=details or {},
            )
        )

    def verify(
        self,
        manifest_path: str | Path,
        *,
        verify_artifact_hashes: bool = True,
        verify_milvus: bool = True,
        self_retrieval_samples: int = 3,
        self_retrieval_top_k: int = 3,
    ) -> OfflineIndexVerificationResult:
        """执行完整离线索引验证。

        Args:
            manifest_path: 待验证 Manifest 路径。
            verify_artifact_hashes: 是否验证普通产物哈希和 Milvus 语义指纹。
            verify_milvus: Milvus 后端是否执行集合、行数和自检检索检查。
            self_retrieval_samples: 等距抽样的子块向量数量。
            self_retrieval_top_k: 每个自检查询返回的结果数。

        Returns:
            无论成功失败都返回 ``OfflineIndexVerificationResult``。
        """

        # 第一步：初始化报告容器并检查 Manifest 文件存在性。
        manifest_file = Path(manifest_path).expanduser().resolve()
        checks: list[VerificationCheck] = []
        metrics: dict[str, Any] = {}
        self_retrieval: list[dict[str, Any]] = []

        if not manifest_file.is_file():
            self._check(
                checks,
                name="manifest_exists",
                condition=False,
                details={"path": str(manifest_file)},
            )
            return OfflineIndexVerificationResult(
                status="failed",
                manifest_path=str(manifest_file),
                index_version=None,
                checks=checks,
                metrics=metrics,
                self_retrieval=self_retrieval,
            )

        self._check(
            checks,
            name="manifest_exists",
            condition=True,
            details={"path": str(manifest_file)},
        )

        # 第二步：严格解析 Manifest；Schema 错误形成报告并提前返回。
        try:
            manifest = IndexManifest.model_validate_json(
                manifest_file.read_text(encoding="utf-8")
            )
        except Exception as exc:
            self._check(
                checks,
                name="manifest_schema",
                condition=False,
                details={
                    "error": f"{type(exc).__name__}: {exc}"
                },
            )
            return OfflineIndexVerificationResult(
                status="failed",
                manifest_path=str(manifest_file),
                index_version=None,
                checks=checks,
                metrics=metrics,
                self_retrieval=self_retrieval,
            )

        self._check(
            checks,
            name="manifest_schema",
            condition=True,
            details={"schema_version": manifest.schema_version},
        )

        # 第三步：把 Manifest 规模和后端信息写入报告指标。
        metrics.update(
            {
                "document_count": manifest.document_count,
                "parent_chunk_count": manifest.parent_chunk_count,
                "child_chunk_count": manifest.child_chunk_count,
                "embedding_dim": manifest.embedding.get("dim"),
                "index_backend": manifest.index.get("backend"),
            }
        )

        # 第四步：检查必需不可变产物；Milvus 后端还要求数据库产物。
        missing = [
            name
            for name in self.REQUIRED_ARTIFACTS
            if name not in manifest.artifacts
        ]
        if (
            manifest.index.get("backend") == "milvus_lite"
            and "milvus_lite" not in manifest.artifacts
        ):
            missing.append("milvus_lite")

        self._check(
            checks,
            name="required_artifacts_declared",
            condition=not missing,
            details={"missing": missing},
        )
        if missing:
            return self._finish(
                manifest_file,
                manifest,
                checks,
                metrics,
                self_retrieval,
            )

        # 第五步：解析全部产物路径并检查文件系统存在性。
        artifact_paths = {
            name: _resolve_artifact_path(
                manifest_file,
                artifact.path,
            )
            for name, artifact in manifest.artifacts.items()
        }
        nonexistent = {
            name: str(path)
            for name, path in artifact_paths.items()
            if not path.exists()
        }
        self._check(
            checks,
            name="artifact_files_exist",
            condition=not nonexistent,
            details={"missing": nonexistent},
        )
        if nonexistent:
            return self._finish(
                manifest_file,
                manifest,
                checks,
                metrics,
                self_retrieval,
            )

        # 第六步：按每个产物的实际完整性模式验证内容或语义指纹。
        if verify_artifact_hashes:
            content_mismatches: dict[str, dict[str, str]] = {}
            semantic_mismatches: dict[str, dict[str, str]] = {}
            semantic_artifacts: list[str] = []
            legacy_semantic_inferred: list[str] = []

            for name, path in artifact_paths.items():
                artifact = manifest.artifacts[name]
                mode = effective_artifact_integrity_mode(
                    name,
                    artifact,
                )

                if mode == "content_sha256":
                    actual = sha256_path(
                        path,
                        metadata_only_paths=set(
                            artifact.metadata_only_paths
                        ),
                    )
                    if artifact.sha256 != actual:
                        content_mismatches[name] = {
                            "expected": artifact.sha256,
                            "actual": actual,
                        }
                    continue

                # Milvus 使用逻辑指纹；旧 Manifest 仅推断模式，不修改其原文件。
                semantic_artifacts.append(name)
                if artifact.integrity_mode != "milvus_semantic_v1":
                    legacy_semantic_inferred.append(name)
                    continue

                actual = milvus_semantic_fingerprint(
                    collection_name=str(
                        manifest.index.get("collection_name") or ""
                    ),
                    record_count=int(
                        artifact.record_count
                        or manifest.child_chunk_count
                    ),
                    embedding_dim=_as_int(
                        manifest.embedding.get("dim"),
                        default=-1,
                    ),
                    metric_type=str(
                        manifest.index.get("metric_type") or ""
                    ),
                    child_chunks_sha256=(
                        manifest.artifacts["child_chunks"].sha256
                    ),
                    vector_records_sha256=(
                        manifest.artifacts[
                            "vector_index_records"
                        ].sha256
                    ),
                )
                if artifact.sha256 != actual:
                    semantic_mismatches[name] = {
                        "expected": artifact.sha256,
                        "actual": actual,
                    }

            mismatches = {
                **content_mismatches,
                **semantic_mismatches,
            }
            self._check(
                checks,
                name="artifact_sha256",
                condition=not mismatches,
                details={
                    "mismatches": mismatches,
                    "semantic_artifacts": semantic_artifacts,
                    "legacy_semantic_inferred": (
                        legacy_semantic_inferred
                    ),
                },
            )

        # 第七步：加载父块、子块、向量索引记录和 NumPy 向量矩阵。
        parents = load_jsonl_dicts(
            artifact_paths["parent_chunks"]
        )
        children = load_jsonl_dicts(
            artifact_paths["child_chunks"]
        )
        vector_records = load_jsonl_dicts(
            artifact_paths["vector_index_records"]
        )
        vectors = np.load(
            artifact_paths["vectors"],
            allow_pickle=False,
        )

        # 第八步：检查所有行数都与 Manifest child/parent 计数一致。
        actual_counts = {
            "parent_chunks": len(parents),
            "child_chunks": len(children),
            "vector_records": len(vector_records),
            "vector_rows": (
                int(vectors.shape[0]) if vectors.ndim >= 1 else 0
            ),
        }
        metrics["actual_counts"] = actual_counts
        expected_counts = {
            "parent_chunks": manifest.parent_chunk_count,
            "child_chunks": manifest.child_chunk_count,
            "vector_records": manifest.child_chunk_count,
            "vector_rows": manifest.child_chunk_count,
        }
        self._check(
            checks,
            name="artifact_record_counts",
            condition=actual_counts == expected_counts,
            details={
                "expected": expected_counts,
                "actual": actual_counts,
            },
        )

        # 第九步：验证二维向量形状和 Embedding 维度。
        expected_dim = _as_int(manifest.embedding.get("dim"))
        shape_ok = (
            vectors.ndim == 2
            and int(vectors.shape[1]) == expected_dim
        )
        self._check(
            checks,
            name="vector_matrix_shape",
            condition=shape_ok,
            details={
                "shape": list(vectors.shape),
                "expected_dim": expected_dim,
            },
        )

        # 第十步：拒绝 NaN/Inf；空向量矩阵也视为失败。
        finite_ok = (
            bool(np.isfinite(vectors).all())
            if vectors.size
            else False
        )
        self._check(
            checks,
            name="vector_values_finite",
            condition=finite_ok,
            details={
                "vector_count": (
                    int(vectors.shape[0])
                    if vectors.ndim == 2
                    else 0
                )
            },
        )

        # 第十一步：非空二维矩阵检查每行是否近似 L2 归一化。
        if vectors.ndim == 2 and vectors.size:
            norms = np.linalg.norm(
                vectors.astype("float64"),
                axis=1,
            )
            metrics["vector_norm"] = {
                "min": float(norms.min()),
                "max": float(norms.max()),
                "mean": float(norms.mean()),
            }
            normalized = bool(
                np.all(np.abs(norms - 1.0) <= 1e-3)
            )
            self._check(
                checks,
                name="vectors_l2_normalized",
                condition=normalized,
                details=metrics["vector_norm"],
            )

        # 第十二步：验证父子块 ID 非空且在各自集合内唯一。
        parent_ids = [
            str(item.get("parent_chunk_id") or "")
            for item in parents
        ]
        child_ids = [
            str(
                item.get("child_chunk_id")
                or item.get("chunk_id")
                or ""
            )
            for item in children
        ]
        unique_ids_ok = (
            all(parent_ids)
            and all(child_ids)
            and len(set(parent_ids)) == len(parent_ids)
            and len(set(child_ids)) == len(child_ids)
        )
        self._check(
            checks,
            name="chunk_ids_unique",
            condition=unique_ids_ok,
            details={
                "parent_unique": len(set(parent_ids)),
                "parent_total": len(parent_ids),
                "child_unique": len(set(child_ids)),
                "child_total": len(child_ids),
            },
        )

        # 第十三步：每个子块必须引用实际存在的父块。
        parent_id_set = set(parent_ids)
        orphan_children = [
            child_ids[index]
            for index, child in enumerate(children)
            if str(child.get("parent_chunk_id") or "")
            not in parent_id_set
        ]
        self._check(
            checks,
            name="child_parent_references",
            condition=not orphan_children,
            details={
                "orphan_count": len(orphan_children),
                "sample": orphan_children[:10],
            },
        )

        # 第十四步：每个子块 extra 中的离线索引版本必须匹配当前 Manifest。
        lineage_mismatches = [
            child_ids[index]
            for index, child in enumerate(children)
            if str(
                (child.get("extra") or {}).get(
                    "offline_index_version"
                )
                or ""
            )
            != manifest.index_version
        ]
        self._check(
            checks,
            name="child_index_lineage",
            condition=not lineage_mismatches,
            details={
                "mismatch_count": len(lineage_mismatches),
                "sample": lineage_mismatches[:10],
            },
        )

        # 第十五步：按后端决定是否执行 Milvus 语义验证或记录不适用。
        if (
            verify_milvus
            and manifest.index.get("backend") == "milvus_lite"
        ):
            self._verify_milvus(
                manifest=manifest,
                artifact_paths=artifact_paths,
                children=children,
                vectors=vectors,
                checks=checks,
                metrics=metrics,
                self_retrieval=self_retrieval,
                sample_count=self_retrieval_samples,
                top_k=self_retrieval_top_k,
            )
        elif manifest.index.get("backend") != "milvus_lite":
            self._check(
                checks,
                name="milvus_verification",
                condition=True,
                details={
                    "status": "not_applicable",
                    "backend": manifest.index.get("backend"),
                },
            )

        # 第十六步：根据所有检查状态生成最终报告。
        return self._finish(
            manifest_file,
            manifest,
            checks,
            metrics,
            self_retrieval,
        )

    def _verify_milvus(
        self,
        *,
        manifest: IndexManifest,
        artifact_paths: dict[str, Path],
        children: list[dict[str, Any]],
        vectors: np.ndarray,
        checks: list[VerificationCheck],
        metrics: dict[str, Any],
        self_retrieval: list[dict[str, Any]],
        sample_count: int,
        top_k: int,
    ) -> None:
        """验证 Milvus 集合、实体数量和抽样向量自检索。

        所有运行时异常都转换为 ``milvus_runtime`` failed check；客户端在 finally 中
        尽力关闭，关闭失败不会覆盖原验证结果。
        """

        # 第一步：定位数据库目录和集合，并延迟创建客户端。
        db_path = artifact_paths["milvus_lite"]
        collection_name = str(
            manifest.index.get("collection_name") or ""
        )
        client = None

        try:
            client = self._client_factory()(str(db_path))

            # 第二步：检查目标集合存在；不存在时无需继续行数和检索检查。
            exists = bool(client.has_collection(collection_name))
            self._check(
                checks,
                name="milvus_collection_exists",
                condition=exists,
                details={
                    "collection_name": collection_name,
                    "db_path": str(db_path),
                },
            )
            if not exists:
                return

            # 第三步：尽力显式加载集合；部分 Milvus Lite 版本可能不需要或不支持。
            try:
                client.load_collection(collection_name)
            except Exception:
                pass

            # 第四步：集合实体数量必须等于 Manifest 子块数量。
            stats = client.get_collection_stats(
                collection_name=collection_name
            )
            row_count = _as_int((stats or {}).get("row_count"))
            metrics["milvus_row_count"] = row_count
            self._check(
                checks,
                name="milvus_entity_count",
                condition=row_count == manifest.child_chunk_count,
                details={
                    "expected": manifest.child_chunk_count,
                    "actual": row_count,
                },
            )

            # 第五步：从头、中、尾等位置等距选择向量做自检索。
            sample_indices = _pick_evenly_spaced_indices(
                len(children),
                sample_count,
            )
            successful = 0

            for index in sample_indices:
                expected_id = str(
                    children[index].get("child_chunk_id")
                    or children[index].get("chunk_id")
                    or ""
                )

                # 使用构建产物中的原向量查询同一集合，要求 top-k 内返回自身。
                raw_hits = client.search(
                    collection_name=collection_name,
                    data=[
                        np.asarray(
                            vectors[index],
                            dtype=np.float32,
                        ).tolist()
                    ],
                    anns_field="vector",
                    limit=max(1, int(top_k)),
                    search_params={
                        "metric_type": str(
                            manifest.index.get("metric_type")
                            or "COSINE"
                        )
                    },
                    output_fields=[
                        "chunk_id",
                        "child_chunk_id",
                        "doc_id",
                        "text",
                    ],
                )
                hits = _normalize_search_hits(raw_hits)
                returned_ids = [
                    item["child_chunk_id"] for item in hits
                ]
                passed = expected_id in returned_ids
                successful += int(passed)

                # 保存每个抽样位置的预期和实际 ID，便于排查向量/行映射错误。
                self_retrieval.append(
                    {
                        "sample_index": index,
                        "expected_child_chunk_id": expected_id,
                        "returned_child_chunk_ids": returned_ids,
                        "passed": passed,
                    }
                )

            # 第六步：当前门禁要求所有抽样向量都能在 top-k 内检索到自身。
            hit_rate = (
                successful / len(sample_indices)
                if sample_indices
                else 0.0
            )
            metrics["self_retrieval_hit_rate"] = hit_rate
            self._check(
                checks,
                name="milvus_self_retrieval",
                condition=(
                    bool(sample_indices)
                    and math.isclose(hit_rate, 1.0)
                ),
                details={
                    "sample_count": len(sample_indices),
                    "successful": successful,
                    "hit_rate": hit_rate,
                    "top_k": top_k,
                },
            )

        except Exception as exc:
            # 客户端创建、统计或检索异常统一记录为运行时验证失败。
            self._check(
                checks,
                name="milvus_runtime",
                condition=False,
                details={
                    "error": f"{type(exc).__name__}: {exc}"
                },
            )
        finally:
            # 尽力关闭客户端；没有 close 或关闭失败都不覆盖主检查结果。
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    @staticmethod
    def _finish(
        manifest_file: Path,
        manifest: IndexManifest,
        checks: Sequence[VerificationCheck],
        metrics: dict[str, Any],
        self_retrieval: list[dict[str, Any]],
    ) -> OfflineIndexVerificationResult:
        """根据检查状态构造最终验证报告。"""

        # 第一步：收集所有非 passed 检查名称，写入指标方便机器筛选。
        failed = [
            item.name
            for item in checks
            if item.status != "passed"
        ]
        metrics["failed_checks"] = failed

        # 第二步：没有失败项才返回 success。
        return OfflineIndexVerificationResult(
            status="success" if not failed else "failed",
            manifest_path=str(manifest_file),
            index_version=manifest.index_version,
            checks=list(checks),
            metrics=metrics,
            self_retrieval=self_retrieval,
        )
