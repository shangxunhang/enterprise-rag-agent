# TASK-003 Offline Index Chain Analysis

## 1. 调查结论

当前可证实的真实离线索引链不是从 Raw Document 开始，而是从版本化 JSONL 中的 `cleaned_text_unit_v1` 对象开始：

```text
scripts/run_step_11_2_acceptance.py::main
  -> OfflineIndexConfigLoader.load + apply_overrides
  -> OfflineIndexBuilder.build
  -> load_jsonl_dicts(cleaned_text_unit_all.jsonl)
  -> migrate_legacy_cleaned_text_unit_payload
  -> CleanedTextUnitSchema.model_validate
  -> registry.build(chunker=fixed_parent_child:v1)
  -> FixedParentChildChunkerPlugin.chunk_records
  -> ChildParentChunker.chunk_records / chunk_units_for_doc
  -> build_parent_chunk_v1 + build_child_chunk_v1
  -> parent_chunks.jsonl + child_chunks.jsonl
  -> encode_texts_with_model
  -> TextEmbedder.encode_texts
  -> child_vectors.npy
  -> build_vector_index_record_v2
  -> vector_index_records.jsonl
  -> MilvusClient(milvus.db)
  -> create_or_reset_child_chunk_collection
  -> build_milvus_child_chunk_record
  -> insert_child_chunk_records
  -> close client
  -> reopen client + get_collection_stats
  -> IndexManifest.write
  -> OfflineIndexVerifier.verify
  -> step_11_2_acceptance_report.json
```

当前 active index 为：

```text
idx_fixed_parent_child_v1_m3e_base_local_m3e_base_v1_869c349c871f
```

该索引的现有运行证据确认：输入 1,417 条 `CleanedTextUnit`，涉及 5 个文档；输出 69 个 Parent Chunk、258 个 Child Chunk、258 个 768 维向量和 258 个 Milvus entity；Step 11.2 验收状态为 `success`，Milvus 自检索 3/3 命中。构建阶段没有激活 active pointer；独立的 Step 11.3 生命周期事件随后完成了激活。

必须保留一个基线差异：2026-07-17 已激活产物没有 `tenant_id`、`kb_id`、`file_id` 字段，而当前 HEAD 的 `OfflineIndexBuilder.build()` 会在严格输入校验后补齐这些字段。因此，“当前可执行代码链”和“当前在线正在使用的历史产物”不能视为完全相同的代码版本输出。

---

## 2. 调查基线

### 2.1 Git 基线

| 项目 | 结果 |
|---|---|
| Project Root | `D:\MyCode\rag-agent` |
| Branch | `main` |
| Commit | `07dda2e80645cac67f8b5f5e30002d21c2ab01eb` |
| Exact Tag | `unknown`；`git describe --tags --exact-match` 返回无精确 tag |
| Workspace | dirty；调查开始前已有大量与本任务无关的修改和未跟踪文件 |
| TASK-003 输出文件初始状态 | Analysis 与 Handoff 均不存在 |
| 本轮业务代码/配置/测试/数据修改 | 无 |
| Milvus 写入/删除/重建 | 无 |

工作区基线不是 clean checkout。本报告中的代码事实对应 `HEAD + 当前工作区内容`；历史 active artifact 没有记录其构建时 Git commit，因此其精确代码基线为 `unknown`。

### 2.2 已读取治理与任务文档

- `docs/team/PROJECT.md`
- `docs/team/ORGANIZATION.md`
- `docs/team/WORKFLOW.md`
- `docs/team/POLICIES.md`
- `docs/team/STATUS.md`
- `docs/team/TASKS/TASK-003-offline-index-chain.md`
- `docs/team/HANDOFFS/TASK-002-online-rag-chain-handoff.md`
- `docs/STEP_11_2_REAL_INDEX_ACCEPTANCE.md`

### 2.3 现有运行与测试证据

#### 历史真实运行证据

文件：

- `data/processed/indexes/active_index.json`
- `data/processed/indexes/idx_fixed_parent_child_v1_m3e_base_local_m3e_base_v1_869c349c871f/index_manifest.json`
- `data/processed/indexes/idx_fixed_parent_child_v1_m3e_base_local_m3e_base_v1_869c349c871f/step_11_2_acceptance_report.json`
- `data/processed/indexes/index_registry.json`
- `data/processed/indexes/index_lifecycle_history.jsonl`

确认值：

| 指标 | 值 |
|---|---:|
| Source records | 1,417 |
| Source documents | 5 |
| Parent chunks | 69 |
| Child chunks | 258 |
| Vector rows | 258 |
| Vector dimension | 768 |
| Milvus rows | 258 |
| Collection | `rag_child_chunks` |
| Metric | `COSINE` |
| Self-retrieval | 3/3，hit rate 1.0 |
| Step 11.2 status | `success` |
| Lifecycle activation | `success`，2026-07-17T07:34:02.878333+00:00 |

#### 本轮只读/临时测试

执行：

```text
backend/tests/test_rag_offline_chunker_index_version.py
backend/tests/test_step_11_2_offline_index_verification.py
```

结果：

```text
28 passed, 3 warnings
```

第一次运行时 10 项通过、18 项因仓库外 `D:\Temp` 父目录不存在而在 pytest `tmp_path` setup 阶段报错；补建仓库外临时目录后原命令复跑，28 项全部通过。告警为依赖弃用告警，不是被测断言失败。

本轮没有运行真实数据重建、真实 Embedding 或 Milvus 写入测试。

---

## 3. 状态判定口径

- **active**：由当前可执行入口可达，并有直接调用、测试或历史运行证据。
- **inactive**：实现存在，但当前入口不可达或未启用。
- **legacy**：有明确 Mini-RAG/旧 schema/旧存储形态等历史证据，当前链不使用。
- **experimental**：仅由 smoke、专项验收、测试或实验 profile 使用，未进入当前 active index。
- **unknown**：现有证据不足。

---

## 4. 入口候选与真实入口

### 4.1 候选矩阵

| 候选 | 直接证据 | 状态 | 结论 |
|---|---|---|---|
| `scripts/run_step_11_2_acceptance.py::main` | 直接调用 `OfflineIndexBuilder.build()` 和 `OfflineIndexVerifier.verify()`；active index 目录中存在对应 `step_11_2_acceptance_report.json` 且 build/status 均为 success | active | 当前 active artifact 的已证实构建/验收入口 |
| `scripts/build_offline_index.py::main` | 直接加载 config，并在非 `--validate-only` 时调用 `OfflineIndexBuilder.build()`；相关 builder 测试通过 | active | 通用离线构建 CLI；没有证据证明当前 active artifact 就是由该脚本直接启动 |
| `scripts/manage_index_lifecycle.py::main` | 调用 `IndexLifecycleManager.activate/rollback`；lifecycle history 有真实 activate 事件 | active | 构建后的独立激活入口，不负责 Chunk/Embedding/Milvus 写入 |
| `backend/rag/init_mini_rag.py::main` | 仅创建 Mini-RAG 脚手架和示例文件 | legacy | 不是当前索引入口 |
| 测试函数中的 builder 调用 | 使用 `tmp_path`、fake embedding/fake Milvus | experimental | 验证行为，不是生产数据入口 |

### 4.2 当前真实入口的上游与输入

文件：`scripts/run_step_11_2_acceptance.py`

- 模块/函数：`main()`
- 上游调用者：命令行进程，`if __name__ == "__main__": raise SystemExit(main())`
- 配置输入：`--index-config`，可由 `--source-path`、`--embedding-model`、`--device`、`--batch-size`、`--output-root`、`--dataset-version` 覆盖
- 下游调用者：`OfflineIndexConfigLoader.load()`、`apply_overrides()`、`OfflineIndexBuilder.build()`、`OfflineIndexVerifier.verify()`
- 输出：stdout JSON、验收报告、进程退出码 0/1
- 异常行为：配置不是 `milvus_lite`、不是真实 model embedding、或试图由 builder 更新 active pointer 时直接抛 `ValueError`

历史 manifest 的有效配置与当前 YAML 默认值不同：

| 项目 | 当前 profile 默认值 | active manifest 有效值 |
|---|---|---|
| source.path | `data/processed/cleaned_text_units.jsonl` | `data/raw/jsonl/cleaned_text_unit_all.jsonl` |
| dataset_version | `cleaned_text_units_real_v1` | `cleaned_text_unit_all_20260717_v1` |

`run_step_11_2_acceptance.py::apply_overrides()`支持这两项覆盖，但现有报告不记录原始 CLI argv。因此有效值已确认，精确命令参数来源为 `unknown`。

---

## 5. 最早可证实输入与清洗边界

### 5.1 最早输入对象

最早可证实对象不是 PDF 或其他 Raw Document，而是：

```text
JSONL record -> cleaned_text_unit_v1
```

实际文件：

```text
data/raw/jsonl/cleaned_text_unit_all.jsonl
```

manifest 记录绝对路径、SHA-256、格式 `jsonl` 和 1,417 条记录。该文件中的 `source_uri` 可追溯到外部 PDF，但当前离线 builder 不读取、解析或清洗 PDF。

Raw Document -> parse -> clean -> `CleanedTextUnit` 的生产链不由当前入口调用，状态为 **unknown（本任务边界之前）**。

### 5.2 输入加载与契约校验

| 上游 | 当前函数 | 直接调用 | 输入 | 输出 | 状态变化 | 异常 |
|---|---|---|---|---|---|---|
| `OfflineIndexBuilder.build` | `rag.store.parent_chunk_store.load_jsonl_dicts` | `source_records = load_jsonl_dicts(source_path)` | JSONL path | `list[dict]` | 文件记录进入内存 | 文件/JSON 读取异常向上抛出 |
| `OfflineIndexBuilder.build` | `migrate_legacy_cleaned_text_unit_payload` | 对每条 source record 调用 | 原始 dict | 已知 legacy 字段迁移后的 dict | `cleaning_rules`、`created_at` 从顶层移入 `extra` | 非 Mapping、非法 `extra` 抛异常 |
| `OfflineIndexBuilder.build` | `CleanedTextUnitSchema.model_validate` | 严格验证迁移后 payload | dict | canonical model/dict | 建立 `cleaned_text_unit_v1` 边界 | strict type 或未知字段失败；不静默丢弃 |
| Chunker | `cleaned_text_unit_chunker.row_to_unit` | `ChildParentChunker.chunk_records()` 内再次调用 | canonical dict | 内部 unit dict | normalize text、重算 `text_length`、unit_id 可从 `extra.source_parse_unit_id` 回填 | ValidationError/TypeError 向上抛出 |

`CleanedTextUnitSchema`关键字段：

- identity/scope：`unit_id`、`doc_id`、`tenant_id`、`kb_id`、`file_id`
- source：`source_type`、`source_uri`、`source_name`、`source_format`、`batch_id`
- structure：`title`、`section`、`section_level`、`page_start`、`page_end`、`unit_type`、`unit_order`
- content/quality：`text`、`text_length`、`language`、`quality_score`、`quality_flags`、`cleaning_version`、`extra`

### 5.3 当前 HEAD 的 scope 补齐

文件：`backend/rag/offline/builder.py`

类/方法：`OfflineIndexBuilder.build()`

严格验证之后，当前代码写入：

```text
tenant_id = record.tenant_id or config.source.tenant_id or "default"
kb_id     = record.kb_id or config.source.kb_id or "default"
file_id   = record.file_id or source_name or doc_id or ""
```

并建立 `doc_id -> (tenant_id, kb_id)` 映射；相同 `doc_id` 出现在不同 scope 时抛 `ValueError`。

对应测试：

- `test_builder_validates_source_before_scope_enrichment`
- scope/activation 相关测试集在本轮通过

### 5.4 active 历史数据的只读统计

| 数据集 | 总数 | tenant_id key | kb_id key | file_id key | doc_id key |
|---|---:|---:|---:|---:|---:|
| source CleanedTextUnit | 1,417 | 0 | 0 | 0 | 1,417 |
| parent_chunks.jsonl | 69 | 0 | 0 | 0 | 69 |
| child_chunks.jsonl | 258 | 0 | 0 | 0 | 258 |
| vector_index_records.jsonl | 258 | 0 | 0 | 0 | 258 |

因此 active artifact 与当前 HEAD scope 补齐行为存在已证实的历史基线差异。

---

## 6. Chunk 主链

### 6.1 Chunker 选择

配置来源：active manifest 的 `chunker`：

```text
name=fixed_parent_child
version=v1
implementation=rag.plugins.chunkers.plugin.FixedParentChildChunkerPlugin
```

注册来源：`backend/rag/registry/default_registrations.py::build_default_component_registry()`。

构建调用：

```text
OfflineIndexBuilder.build
  -> self.registry.build(category="chunker", config=chunker_config, ...)
  -> FixedParentChildChunkerPlugin(...)
```

### 6.2 参数

| 参数 | active 值 | 来源 |
|---|---:|---|
| parent_chunk_size | 1500 | manifest chunker params |
| parent_chunk_overlap | 150 | manifest chunker params |
| child_chunk_size | 500 | manifest chunker params |
| child_chunk_overlap | 50 | manifest chunker params |
| unit | `token` | manifest |
| tokenizer_model_name | `D:/models/huggingface/embedding/m3e-base` | manifest |
| tokenizer_local_files_only | `true` | manifest |
| parent strategy | `fixed` | plugin execution metadata |
| child strategy | `fixed` | plugin execution metadata |
| parent version | `parent_chunk_v1.0` | config/manifest |
| child version | `child_chunk_v1.0` | config/manifest |
| deterministic created_at | `2026-07-17T00:00:00+08:00` | manifest |

### 6.3 调用与数据变化

| 上游调用者 | 文件/类/函数 | 下游 | 输入 | 输出 | 数量/状态变化 | 异常行为 |
|---|---|---|---|---|---|---|
| `OfflineIndexBuilder.build` | `FixedParentChildChunkerPlugin.chunk_records` | `ChildParentChunker.chunk_records` | 1,417 canonical dicts | `ParentChildChunkResult` | plugin 添加 deterministic time 和 plugin metadata | delegate 异常直接上抛 |
| plugin | `ChildParentChunker.chunk_records` | `row_to_unit`、`chunk_units_for_doc` | 多文档 records | 聚合 parents/children | 按 `doc_id` 分组并按 doc_id 排序 | 缺失 doc_id 的 unit 跳过 |
| `chunk_units_for_doc` | `_prepare_units` | `should_skip_unit`、`_expand_long_units` | 单文档 units | 已过滤、排序、长 unit 拆分后的 units | 当前数据跳过 5 条，保留 1,412 条 | tokenizer/split 异常通常上抛；token offset 内部有 fallback |
| `chunk_units_for_doc` | `_build_parent_unit_groups` | `_overlap_tail` | ordered units | parent unit groups | 按 token 预算聚合；overlap 保留尾部完整 units | 参数在构造时校验 |
| 每个 parent group | `_join_units_with_spans` | `_build_children_for_parent` | units | parent text + char spans | unit 文本以换行拼接 | 空 parent 不产出 |
| parent 构造 | `build_parent_chunk_v1` | schema dict | parent fields | `parent_chunk_v1` | 生成 ID/hash/child IDs/metadata | 无内部重试 |
| child 构造 | `_build_children_for_parent` -> `build_child_chunk_v1` | schema dict | parent text/windows | `child_chunk_v1` | 生成 child ID、parent link、offset、metadata | 无 child text 时跳过 |

### 6.4 过滤与边界处理

`should_skip_unit()`实际规则：

1. text 为空或全空白；
2. `unit_type == "image"`；
3. `unit_type == "other"` 且有 `short_text` flag 且文本长度小于 10。

active source 只读统计：

```text
source records: 1417
skipped: 5
skip reason: short_other=5
kept: 1412
```

active 第一批 child 的 `source_unit_ids` 从 `...000003` 直接到 `...000006`，与 `...000004`、`...000005` 被过滤一致。

长 unit：`_expand_long_units()`先按 parent 窗口拆为 pseudo unit，ID 追加 `_part_XXXX`，并增加 `split_from_long_unit` flag 与源内 char offset。

Parent overlap：不是截取固定 token 字符串，而是从上一组尾部按完整 unit 回收，直到累计长度达到 overlap。

Child split：tokenizer 支持 `offset_mapping` 时按 token 窗口切分；step=`chunk_size-overlap`。若 tokenizer 调用抛异常或 offset 不可用，函数捕获异常并静默回退到字符窗口。

### 6.5 ID 与父子关系

| 字段 | 创建者 | 规则/来源 |
|---|---|---|
| `doc_id` | CleanedTextUnit producer | builder 不创建；输入必须提供才能进入分组 |
| `parent_chunk_id` | `ChildParentChunker.chunk_units_for_doc` | `{doc_id}_parent_{parent_index:06d}` |
| `child_chunk_id` | `_build_children_for_parent` | `{parent_chunk_id}_child_{child_index_in_parent:04d}` |
| `chunk_id` | `build_child_chunk_v1` | 等于 `child_chunk_id` |
| `parent.child_chunk_ids` | `chunk_units_for_doc` | 当前 parent 生成的 child ID 列表 |
| `child.parent_chunk_id` | `_build_children_for_parent` | 当前 parent ID |
| `parent_chunk_index` | parent enumerate | 每个 doc 从 1 开始 |
| `child_index_in_parent` | child enumerate | 每个 parent 从 1 开始 |
| `child_chunk_index` | `global_child_index` | 每个 doc 调用 `chunk_units_for_doc` 时从 1 开始，不是全数据集全局唯一 |

active 只读统计：

- 69 个唯一 parent ID；
- 258 个唯一 child ID；
- orphan child = 0；
- `child_chunk_index` 只有 173 个唯一值，范围 1..173；各 doc 均从 1 重新开始；
- 主键唯一性由 `child_chunk_id/chunk_id` 保证，不由 `child_chunk_index` 保证。

### 6.6 Chunk metadata 字段来源

| 输出字段 | 来源/转换 |
|---|---|
| text | CleanedTextUnit text，按 unit 顺序换行聚合，再切 window |
| text_length | `len(text)` |
| token_count | token mode 下 `token_counter.count(text)` |
| text_hash | `sha256(text)` |
| source_unit_ids | parent 全部 source unit；child 按 char span 与 source unit span 交集 |
| title | 对涉及 units 取最后一个非空值 |
| section / section_level | 对涉及 units 取最后一个非空值 |
| page_start / page_end | 涉及 units 的最小/最大页 |
| source_type | first non-empty，默认 offline/source default |
| tenant_id/kb_id/file_id | 当前 HEAD 从已补齐 unit 传递；历史 active artifact 无对应 key |
| char_start_in_parent / char_end_in_parent | tokenizer offset 或字符 window 边界 |
| quality_score | 涉及 units 的非空 score 平均值 |
| quality_flags | 去重合并；短于目标窗口时可添加 `short_chunk` |
| language | 涉及 units 的多数语言 |
| chunk_type | 全 table=`table`；含 table=`mixed`；否则 `text` |
| source_uri/name/format/batch_id | first non-empty unit 字段，写入 `extra` |
| offline_index_version/config_hash/dataset_version | `OfflineIndexBuilder.build` 在 chunk 生成后写入 `extra` |
| chunker_plugin | `FixedParentChildChunkerPlugin._normalize_output` |

### 6.7 Chunk 数量变化

```text
1417 CleanedTextUnit
  - 5 skipped short_other
= 1412 participating units
  -> 69 parent_chunk_v1
  -> 258 child_chunk_v1
```

Chunk 数量是现有产物和验收报告共同确认的运行事实。

---

## 7. 中间产物

当前 active index 目录：

```text
data/processed/indexes/idx_fixed_parent_child_v1_m3e_base_local_m3e_base_v1_869c349c871f/
```

| 产物 | 生产函数 | 记录数/形状 | 主链角色 | 状态 |
|---|---|---:|---|---|
| `parent_chunks.jsonl` | `OfflineIndexBuilder._write_jsonl` | 69 | Parent context 主链持久化节点 | active |
| `child_chunks.jsonl` | `OfflineIndexBuilder._write_jsonl` | 258 | Embedding 输入、验证输入、在线 metadata 节点 | active |
| `child_vectors.npy` | `np.save` in `OfflineIndexBuilder.build` | `(258, 768)`, float32 | Milvus 写入 vector 来源 | active |
| `vector_index_records.jsonl` | `build_vector_index_record_v2` + `_write_jsonl` | 258 | Chunk/Embedding/Index lineage 登记产物 | active |
| `milvus.db/` | `MilvusClient(str(db_path))` | 258 rows | 实际向量数据库 | active |
| `index_manifest.json` | `IndexManifest.write` | 1 | 不可变索引身份、hash、artifact registry | active |
| `step_11_2_acceptance_report.json` | acceptance script | 1 | 构建后验证结果 | active |
| `active_index.json` | `IndexLifecycleManager.activate` | 1 pointer | 构建后独立生命周期指针 | active，但不属于写库链 |

`_write_jsonl()`以 `w` 模式直接写目标文件，不使用临时文件替换；manifest 在所有 artifact 和 Milvus reopen/count 检查完成后最后写入。

---

## 8. Embedding 主链

### 8.1 调用边

| 上游 | 文件/类/函数 | 直接调用 | 输入 | 输出 | 状态变化 | 异常 |
|---|---|---|---|---|---|---|
| `OfflineIndexBuilder.build` | `embedding_service.encode_texts_with_model` | `encode_texts_with_model(texts, model_name, device, batch_size, version)` | 258 child texts | `(np.ndarray, embedding_model, embedding_version)` | 文本转向量 | 未解析模型时 ValueError；加载/推理异常上抛 |
| `encode_texts_with_model` | `TextEmbedder.__init__` | `SentenceTransformer(model_name_or_path, device)` | model path/device | loaded model | 模型进入内存/GPU | 依赖、路径、设备异常上抛 |
| `encode_texts_with_model` | `TextEmbedder.encode_texts` | `self.model.encode(...)` | `list[str]` | float32 ndarray | batch 编码，L2 normalize | empty texts ValueError；模型异常上抛 |
| builder | shape checks | `vectors.ndim/shape` | ndarray | validated ndarray | 确认行数和 dim | 行数或维度不一致 ValueError |
| builder | `np.save` | `np.save(..., allow_pickle=False)` | float32 ndarray | `.npy` | 向量持久化 | IO 异常上抛 |

### 8.2 active 配置与运行值

| 项目 | 值 | 证据 |
|---|---|---|
| mode | `model` | manifest/profile/report |
| model | `D:\models\huggingface\embedding\m3e-base` | manifest |
| name | `m3e_base` | manifest |
| version | `local_m3e_base_v1` | manifest/vector records |
| device | `cuda` | manifest |
| batch_size | 32 | manifest；TextEmbedder 传入 model.encode |
| normalize_embeddings | true | `TextEmbedder.encode_texts` 与 manifest |
| dtype | float32 | builder/save；只读 NPY |
| configured dim | 768 | config/manifest |
| actual shape | `(258, 768)` | NPY 与验收报告 |
| finite | true | 本轮只读和验收报告 |
| L2 norm | min 0.9999999148；max 1.0000000766；mean 1.0000000020 | 验收报告 |

builder 将全部 258 条文本一次性传给 `TextEmbedder.encode_texts`；实际 mini-batch 由 sentence-transformers 的 `batch_size=32` 执行。

### 8.3 非 active Embedding

`encode_texts_with_hash()`及 `HashTextEmbedder`由 smoke/unit test 使用；当前 active manifest 明确为真实 model mode，故 hash 路径为 **experimental**。

---

## 9. Vector Index Record

文件：`backend/rag/schema/VectorIndexRecord_Schema.py`

函数：`build_vector_index_record_v2()`

上游：`OfflineIndexBuilder.build()`逐个 zip `children` 与 vectors 的行索引。

输出不包含向量值；它记录 Chunk、Embedding、Index 的 lineage：

- `vector_id` / `indexed_chunk_id` / `chunk_id` / `child_chunk_id`
- `parent_chunk_id`
- scope 与 doc/source 字段
- `indexed_granularity=child`
- `text_hash`
- `embedding_key`
- embedding model/dim/version
- index name/version/vector_db
- title/section/page/source units
- parent/child chunk version
- active/timestamps

`embedding_key`是以下字段拼接后 SHA-256：

```text
indexed_chunk_id | text_hash | embedding_model | embedding_version | index_name | index_version
```

active `vector_index_records.jsonl`共 258 条，但历史产物没有 `tenant_id/kb_id/file_id` key。

---

## 10. Milvus 实际写入链

### 10.1 连接、database 与 collection

| 项目 | active 值/行为 | 来源 |
|---|---|---|
| Client | `pymilvus.MilvusClient` | builder lazy import |
| Database/URI | `<index_output_dir>/milvus.db` | builder 拼接并传给 `MilvusClient(str(db_path))` |
| Server database name | 未配置；Milvus Lite 本地文件/目录模式 | 当前代码 |
| Collection | `rag_child_chunks` | manifest/config |
| recreate | `true` | manifest |
| vector index | `AUTOINDEX` | `create_or_reset_child_chunk_collection` |
| metric | `COSINE` | manifest/config |
| dimension | 768 | embedding actual/config |
| insert batch | 128 | manifest/config |
| max text chars | 8192 | manifest/config |
| dynamic fields | disabled | schema builder |
| primary key auto_id | false | schema builder |
| primary key | `chunk_id` | schema builder |

### 10.2 Collection 创建行为

文件：`backend/rag/vector_store/milvus_child_chunk_store.py`

函数：`create_or_reset_child_chunk_collection()`

- `client.has_collection(collection_name)`；
- 若存在且 `recreate=true`，调用 `client.drop_collection()`；
- 若存在且 `recreate=false`，打印并直接返回；
- 创建静态 schema；
- `prepare_index_params()`；
- `add_index(field_name="vector", index_type="AUTOINDEX", metric_type=...)`；
- `client.create_collection(...)`。

active config 为 `recreate=true`。但 immutable index_version 目录通常是新目录；同 config/source 且 manifest 已存在时 builder 在打开 Milvus 前就返回 `already_exists`。

### 10.3 物理 schema

`build_milvus_schema_for_child_chunk_v1(dim, max_text_chars)`定义：

| 字段组 | Milvus 字段 |
|---|---|
| Primary/vector | `chunk_id` VARCHAR PK、`vector` FLOAT_VECTOR(768)、`vector_id`、`indexed_chunk_id` |
| Parent-child/scope | `child_chunk_id`、`parent_chunk_id`、`tenant_id`、`kb_id`、`file_id`、`doc_id`、`source_type`、`indexed_granularity` |
| Text/location | `text`、`text_length`、`token_count`、`text_hash`、`source_unit_ids_json`、`title`、`section`、`section_level`、`page_start`、`page_end` |
| Child position | `child_chunk_index`、`child_index_in_parent`、`child_chunk_strategy`、`char_start_in_parent`、`char_end_in_parent` |
| Version/lineage | `cleaning_version`、`parent_chunk_version`、`child_chunk_version`、`chunk_created_at`、`extra_json`、`child_chunk_schema_version` |
| Embedding/index | `embedding_model`、`embedding_dim`、`embedding_version`、`embedding_key`、`index_version`、`is_active` |

### 10.4 字段映射

以下“在线读取线索”仅用于与 TASK-002 核对，不反向证明写入。

| 源字段 | 转换函数 | Milvus 写入字段 | 在线读取线索 |
|---|---|---|---|
| child `chunk_id/child_chunk_id` | `build_milvus_child_chunk_record`，优先 child ID | `chunk_id` PK、`child_chunk_id`、`indexed_chunk_id`、`vector_id` | TASK-002 dense retriever按 child ID 构造候选 |
| child vector row | `vector.astype(float32).tolist()` | `vector` | 在线以同 embedding model 查询 `vector` |
| child `parent_chunk_id` | `safe_str` | `parent_chunk_id` | TASK-002 用它回填 parent context |
| child `tenant_id` | `safe_str(...,"default")` | `tenant_id` | 在线 trusted scope filter 使用；历史 artifact 字段冲突见 Finding |
| child `kb_id` | `safe_str(...,"default")` | `kb_id` | 同上 |
| child `file_id` | `safe_str` | `file_id` | 在线 scope/filter/candidate metadata 线索 |
| child `doc_id` | `safe_str` | `doc_id` | 在线候选和过滤读取 |
| child `source_type` | `safe_str(...,"offline")` | `source_type` | 在线候选 metadata |
| 常量 | `DEFAULT_INDEXED_GRANULARITY` | `indexed_granularity="child"` | 在线以 child 命中、parent 回填 |
| child `text` | `truncate_text(text,8192)` | `text` | dense result text |
| child text | `len/hash` 已在 chunk 创建；安全转换 | `text_length`、`token_count`、`text_hash` | 在线返回 text/hash lineage |
| child `source_unit_ids` | compact JSON | `source_unit_ids_json` | 在线主链当前未确认直接解析该物理字段 |
| child title/section/pages | safe str/int，null int 写为 -1 | 对应字段 | TASK-002候选读取这些字段 |
| child indices/offsets | safe int，null 写为 -1 | 对应字段 | lineage/定位 metadata |
| child versions/time/extra | safe str/compact JSON | 对应字段 | TASK-002读取 chunk/index lineage 子集 |
| model/dim/version | builder实际 embedding 返回与 config | 对应字段 | 在线工厂从 manifest解析 model/dim/version |
| child ID+hash+model+index | `build_embedding_key` | `embedding_key` | 在线 lineage 核对线索 |
| index version | builder计算值 | `index_version` | 在线 manifest lineage |
| child `is_active` | bool，默认 true | `is_active` | 在线过滤/候选线索 |

### 10.5 写入方法和结果处理

调用：

```text
OfflineIndexBuilder.build
  -> records = [build_milvus_child_chunk_record(child, vector, ...)]
  -> insert_child_chunk_records(client, collection, records, batch_size=128)
  -> client.insert(collection_name=..., data=batch)
```

258 条 active records 的批次形态为 128 + 128 + 2。

`insert_child_chunk_records()`：

- 只调用 `insert`；
- 不调用 `upsert`；
- 不逐记录 delete；
- 不做 delete-before-insert，只有 collection 级 drop（当 recreate=true 且已存在）；
- 每批 `client.insert` 抛异常时立即中断；
- `total`按提交 batch 长度累加，不解析 insert result 内的实际成功数；
- builder 检查 `inserted_count == len(children)`；
- 更强的持久化确认是关闭 client 后重新打开，并检查 `get_collection_stats().row_count == len(children)`。

### 10.6 flush/load

- builder 不调用显式 `flush`；代码注释说明 Windows Milvus Lite 上避免该调用，使用 close/reopen 作为持久化边界；
- builder 写入阶段不调用 `load_collection`；
- `OfflineIndexVerifier._verify_milvus()`在检索前尝试 `load_collection`，异常被忽略，然后读取 stats 并执行 self-retrieval；
- active acceptance report 已确认 collection 存在、row_count=258、自检索成功。

---

## 11. 幂等、重复、失败、重试与部分成功

### 11.1 索引身份

`OfflineIndexBuildConfig.index_version()`由以下语义字段决定：

- schema version；
- dataset version；
- chunker name/version/params；
- embedding identity；
- index backend/collection/metric 等身份字段。

`build_id`、notes、output root 等运行字段不改变 index_version。测试：

- `test_operational_fields_do_not_change_index_version`
- `test_index_version_changes_when_chunk_params_change`
- `test_index_version_changes_by_chunker_strategy`

### 11.2 已完成构建重复执行

若目标 `index_manifest.json` 已存在：

1. 读取 manifest；
2. 比较 config hash 与 source hash；
3. 一致则返回 `OfflineIndexBuildResult(status="already_exists")`；
4. 不重新生成 Chunk/Embedding，不打开 Milvus；
5. 不一致则抛 index version collision `ValueError`。

测试：`test_builder_creates_immutable_manifest_and_artifacts`。

### 11.3 未完成目录

若 output dir 非空但没有 manifest，builder 抛 `ValueError`，不会自动清理、续跑或覆盖。

### 11.4 跳过

- unit 层有明确 skip 规则，active 数据跳过 5 条；
- 缺失 `doc_id` 的 unit 在 `ChildParentChunker.chunk_records` 不进入 group；
- 空 parent/child text 不生成对应记录；
- 没有单独 rejected/skipped JSONL 或逐条失败文件；统计只可从产物/验证报告推导。

### 11.5 重试与断点

当前 builder、embedding 和 Milvus insert 链中没有自动 retry、backoff、checkpoint 或 resume。

### 11.6 部分成功

当前 build result 只有正常 `success`、`already_exists`、validate-only success；异常直接上抛，没有 `partial_success` 状态。

写入顺序为：

```text
create output dir
-> parent JSONL
-> child JSONL
-> vectors NPY
-> vector record JSONL
-> Milvus create/insert
-> close/reopen row-count verification
-> manifest last
```

因此在 manifest 写入前任一步骤失败时，已写入的前序文件或部分 Milvus 数据可能留在 non-empty output dir。下一次相同 build 因“non-empty without manifest”被拒绝；当前链不自动恢复该目录。

acceptance script 只有在 builder 成功返回或 `--skip-build` 时才继续写验收报告；builder 抛异常时不产生成功报告。

---

## 12. 配置与实际运行值

| 类别 | active 有效值 | 创建/消费位置 |
|---|---|---|
| build_id | `m3e_milvus_lite_real_v1` | config/manifest |
| dataset_version | `cleaned_text_unit_all_20260717_v1` | override/effective config，manifest |
| source | `data/raw/jsonl/cleaned_text_unit_all.jsonl` | builder |
| chunker | fixed parent-child v1 | registry/plugin |
| parent window | 1500/150 token | ChildParentChunker |
| child window | 500/50 token | ChildParentChunker |
| tokenizer | local m3e-base | token counter |
| embedding | local m3e-base v1 | TextEmbedder |
| device/batch | cuda/32 | TextEmbedder |
| dimension | 768 | builder checks/schema |
| normalization | true | sentence-transformers encode |
| backend | milvus_lite | builder |
| DB path | version dir `/milvus.db` | builder |
| collection | `rag_child_chunks` | store/config |
| index | AUTOINDEX/COSINE | Milvus schema creation |
| recreate | true | collection creation |
| insert batch | 128 | insert helper |
| active pointer update in builder | false；schema禁止 true | OutputConfig |

---

## 13. Candidate 状态分类

| 实现/文件 | 状态 | 证据 |
|---|---|---|
| `scripts/run_step_11_2_acceptance.py` | active | active index 目录中的 success acceptance report 对应其输出结构 |
| `scripts/build_offline_index.py` | active | 当前可执行 CLI 直达 builder；测试覆盖 builder |
| `backend/rag/offline/builder.py::OfflineIndexBuilder` | active | 两个入口直调；manifest/report/测试共同证实 |
| `m3e_milvus_lite_real_v1.yaml` | active | active manifest build_id/profile字段一致；实际 source/dataset有 override |
| `FixedParentChildChunkerPlugin` | active | active manifest implementation + child extra plugin metadata |
| `ChildParentChunker` parent/child path | active | plugin直接调用；产物字段与规则对应；测试通过 |
| `cleaned_text_unit_chunker.row_to_unit/should_skip_unit` | active | ChildParentChunker直接调用；active source skip证据 |
| `TextEmbedder` model embedding | active | manifest/report真实 model、cuda、768维证据 |
| `milvus_child_chunk_store.py` | active | builder直接 import/call；report确认 collection/entity/self retrieval |
| `VectorIndexRecord_Schema.build_vector_index_record_v2` | active | builder直接调用；258条 artifact存在 |
| `OfflineIndexVerifier` | active | acceptance入口直调；report结构和结果存在 |
| `IndexLifecycleManager.activate` | active | lifecycle history真实成功事件；构建后单独激活 |
| recursive/heading/paragraph parent-child plugins | experimental | 有注册与 smoke profiles/测试，但 active manifest未选择 |
| `fixed_parent_child_smoke_v1.yaml`及其他 smoke profiles | experimental | `artifacts_only`、smoke dataset，明确不激活 |
| hash embedding | experimental | smoke/unit test路径；active manifest为 model |
| `cleaned_text_unit_chunker` flat `build_flat_chunk_v1/chunk_partition/chunk_records_for_doc` | inactive | 当前 builder无调用者；搜索仅见定义 |
| `vector_store/milvus_chunk_store.py` flat chunk store | inactive | 当前 builder只 import child-chunk store；未发现当前入口调用 |
| `schema/Chunk_Schema.py::build_chunk` Mini-RAG兼容 builder | legacy | 文件明确保留 Mini-RAG旧 schema兼容；当前主链调用 parent/child v1 builders |
| `rag/Index/indexer.py` FAISS index path | legacy | 文件明确 Mini-RAG/FAISS；当前 active backend为 Milvus Lite |
| `rag/init_mini_rag.py` | legacy | 明确创建 Mini-RAG脚手架与示例数据 |
| Raw PDF parsing/cleaning producer | unknown | active source可追溯PDF URI，但当前索引入口没有调用解析/清洗代码 |
| active artifact 构建时精确 Git commit | unknown | manifest/report没有 Git SHA |
| active run 原始 CLI argv | unknown |只记录有效配置，不记录 argv |

---

## 14. 与 TASK-002 在线读取线索核对

### 14.1 已证实一致

| 项目 | 离线证据 | TASK-002 线索 | 结论 |
|---|---|---|---|
| active index version | pointer/manifest/history | 同一 version | 一致 |
| backend/DB | Milvus Lite version dir | online resolver读取同一 DB | 一致 |
| collection | `rag_child_chunks` | online dense retriever同名 | 一致 |
| metric/dim/model | COSINE/768/m3e-base | online manifest解析同值 | 一致 |
| child indexed, parent回填 | Milvus只写 child，保存 parent ID；parent JSONL独立 | online命中child后补parent | 一致 |
| counts | 69/258/258 | TASK-002记录同值 | 一致 |
| lineage | index version写入 chunk/vector/Milvus schema | online候选读取 lineage | 一致 |

### 14.2 已证实冲突/基线差异

active parent/child/vector artifact 均缺少 `tenant_id/kb_id/file_id` key；TASK-002 在线 trusted scope 需要 scope 字段。当前 HEAD builder会补齐，但这不能改变已激活历史 artifact 的事实。

### 14.3 unknown

- active 历史 Milvus entity 内是否含默认 `tenant_id/kb_id/file_id`：本轮未打开 active DB读取实体样例，现有 acceptance report只确认 collection、row count和自检索字段；不能由当前 HEAD mapping反推历史写入内容。
- source scope字段缺失时，active历史构建代码究竟如何处理：精确历史 commit未知。
- 在线所有 output fields 与 active历史物理 schema是否逐字段完全一致：代码 schema已确认，但历史 DB未做只读 describe/entity dump。

---

## 15. Findings

### FINDING-003-001：当前代码与 active 历史 artifact 的 scope 字段基线不一致

- 范围：当前 builder 与 active parent/child/vector artifacts。
- 已确认事实：当前 HEAD在严格验证后补齐 `tenant_id/kb_id/file_id`；active source和三类 artifact这些 key均为0条。
- 影响判断：不在本任务中评价；交给 TASK-005/TASK-006 核实 Contract/运行一致性。
- 未处理：是。

### FINDING-003-002：active profile 文件的当前默认 source/dataset 与 active manifest 不同

- 当前 YAML：`data/processed/cleaned_text_units.jsonl`、`cleaned_text_units_real_v1`。
- active manifest：`data/raw/jsonl/cleaned_text_unit_all.jsonl`、`cleaned_text_unit_all_20260717_v1`。
- acceptance CLI支持 override，但原始 argv未保存。
- 未处理：是。

### FINDING-003-003：失败构建可能留下无 manifest 的非空版本目录

- builder先写 JSONL/NPY/Milvus，最后写 manifest；没有自动 rollback/cleanup/resume。
- 后续相同 build看到 non-empty dir without manifest 会拒绝继续。
- 未执行故障注入；这是当前控制流的直接事实。
- 未处理：是。

### FINDING-003-004：insert helper的计数是提交 batch 长度，不解析 Milvus insert result

- `insert_child_chunk_records`每批调用 `client.insert`后按 `len(batch)`累计。
- 最终 close/reopen `row_count`检查提供实际持久化数量确认。
- 未处理：是。

### FINDING-003-005：token offset异常会静默回退字符切分

- `_split_text_windows_with_offsets`捕获 tokenizer/offset异常后调用字符窗口。
- manifest记录配置为 token；单条 chunk不会记录“本次是否发生fallback”。active输出记录 tokenizer backend为 huggingface，但不能证明所有窗口都没有fallback。
- 未处理：是。

### FINDING-003-006：`child_chunk_index`按文档重置

- active 258 children只有173个唯一 `child_chunk_index`；每个doc从1开始。
- `chunk_id/child_chunk_id`仍为258个唯一值。
- 未处理：是。

### FINDING-003-007：Step 11.2 note与active pointer不是同一步骤冲突

- manifest note只声明 Step 11.2 builder不激活 pointer；
- lifecycle history证明Step 11.3随后独立激活同一index。
- 结论：两项描述不同生命周期阶段；本轮没有修改任何记录。

---

## 16. 未确认问题与证据缺口

1. 2026-07-17 active artifact对应的精确 Git commit/tag。
2. active run的完整 CLI argv，以及 source/dataset override由谁提供。
3. Raw PDF到 `cleaned_text_unit_all.jsonl` 的真实生产入口、解析器调用链与清洗 Job；当前 index builder只消费其结果。
4. active历史 Milvus entity中scope字段的实际存在性和值。
5. active历史 Milvus collection的只读 `describe_collection`完整输出；本轮为避免触碰 active DB，使用当前物理schema代码与既有验收报告取证。
6. 真实生产失败案例、失败目录和日志；现有代码可确认控制流，但没有现有真实失败运行样本。
7. 是否存在外部调度器、CI或系统任务直接调用通用 `build_offline_index.py`；仓库内未发现调度注册证据。

---

## 17. 交接线索

### TASK-004 Algorithm Lead

需复核的实际算法调用点：

- `should_skip_unit`过滤；
- parent按token预算聚合及whole-unit overlap；
- child固定token window与50 token overlap；
- tokenizer offset失败的字符fallback；
- long unit拆分；
- m3e-base normalized embedding；
- Milvus AUTOINDEX/COSINE；
- self-retrieval验收。

本报告只确认调用和条件，不评价算法质量。

### TASK-005 Architect

需复核的创建/写入责任：

- `CleanedTextUnitSchema`：输入边界；
- `build_parent_chunk_v1/build_child_chunk_v1`：Chunk contract创建者；
- `OfflineIndexBuilder`：scope补齐、lineage注入、artifact顺序、build status；
- `build_vector_index_record_v2`：vector lineage contract；
- `build_milvus_child_chunk_record`：物理字段转换；
- `IndexManifest`：不可变索引身份；
- `IndexLifecycleManager`：唯一 active pointer写入者；
- 当前代码与active历史artifact的scope差异。

### TASK-006 QA / Reviewer

需复核：

- 历史 artifact与当前 HEAD差异；
- active DB只读schema/entity scope字段；
- partial output目录失败语义；
- insert result与reopen row count双层检查；
- 28项测试通过及真实 acceptance report。

---

## 18. 最终状态建议

建议 TASK-003 状态：`review`。

理由：调查范围内的当前可执行链、active历史运行、Chunk/Embedding/Milvus写入和失败语义均已形成可复核证据；仍存在的历史commit、CLI argv、active DB scope字段等已明确标记为 unknown，没有写成事实。