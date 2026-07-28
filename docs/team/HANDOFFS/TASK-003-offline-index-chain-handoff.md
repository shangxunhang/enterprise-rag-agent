# TASK-003 Offline Index Chain Handoff

## 1. Handoff Summary

TASK-003 已完成当前离线索引 As-Is 调查。当前可证实主链以 `cleaned_text_unit_v1` JSONL 为起点，不包含 Raw PDF 解析/清洗生产过程；终点为 Milvus Lite child collection 写入、close/reopen row-count 确认和 Step 11.2 artifact 验收。

当前 active index：

```text
idx_fixed_parent_child_v1_m3e_base_local_m3e_base_v1_869c349c871f
```

完整调查：

```text
docs/team/ANALYSIS/TASK-003-offline-index-chain.md
```

---

## 2. 已确认事实

### 2.1 真实入口与边界

- 真实历史构建/验收入口：`scripts/run_step_11_2_acceptance.py::main()`。
- 直接调用：`OfflineIndexBuilder.build()`，随后 `OfflineIndexVerifier.verify()`。
- 通用可执行入口：`scripts/build_offline_index.py::main()`，同样直达 builder，但没有证据证明 active artifact 由它直接启动。
- 最早可证实输入：`data/raw/jsonl/cleaned_text_unit_all.jsonl` 中 1,417 条 `cleaned_text_unit_v1`。
- Raw PDF只通过 `source_uri`可追溯；当前 builder不读取或解析PDF。
- 构建器不更新 active pointer；`IndexLifecycleManager.activate()`在Step 11.3独立激活。

### 2.2 实际数量

| 阶段 | 数量 |
|---|---:|
| CleanedTextUnit | 1,417 |
| Documents | 5 |
| Skipped units | 5，均为 `other + short_text + len<10` |
| Participating units | 1,412 |
| Parent chunks | 69 |
| Child chunks | 258 |
| Embedding rows | 258 |
| Embedding dim | 768 |
| Vector index records | 258 |
| Milvus rows | 258 |
| Self retrieval | 3/3，hit rate 1.0 |

### 2.3 Chunk

- active chunker：`fixed_parent_child:v1`。
- 实现：`FixedParentChildChunkerPlugin -> ChildParentChunker`。
- parent：1500 token，overlap 150；按完整unit聚合/回收尾部unit。
- child：500 token，overlap 50；支持tokenizer offset，失败时静默回退字符窗口。
- `parent_chunk_id={doc_id}_parent_{index:06d}`。
- `child_chunk_id={parent_chunk_id}_child_{index:04d}`。
- child `chunk_id == child_chunk_id`。
- parent-child orphan = 0；parent ID 69个唯一，child ID 258个唯一。
- `child_chunk_index`按doc重置，不是全数据集唯一。

### 2.4 Embedding

- model：`D:\models\huggingface\embedding\m3e-base`。
- version：`local_m3e_base_v1`。
- device：`cuda`。
- batch size：32。
- `SentenceTransformer.encode(... normalize_embeddings=True)`。
- 输出：float32，shape `(258,768)`；finite且L2 norm接近1。

### 2.5 Milvus

- backend：Milvus Lite。
- URI/database path：active version目录下 `milvus.db`。
- collection：`rag_child_chunks`。
- primary key：`chunk_id`，auto_id=false。
- vector：FLOAT_VECTOR(768)。
- index：AUTOINDEX；metric=COSINE。
- dynamic fields：false。
- recreate：true。
- insert batch：128，实际批次为128+128+2。
- 写入方法：`client.insert`；没有upsert、逐记录delete或delete-before-insert。
- builder不显式flush；close/reopen后检查row_count。
- verifier尝试load collection、检查row count并执行self-retrieval。

### 2.6 幂等和失败语义

- manifest存在且config/source hash一致：返回 `already_exists`，不重算、不写Milvus。
- manifest存在但hash不同：抛index version collision。
- non-empty output dir且无manifest：拒绝继续。
- 无自动retry、checkpoint、resume或partial_success状态。
- manifest最后写；前序失败可能留下部分JSONL/NPY/Milvus目录。
- insert helper按batch长度累计，不解析insert result；最终reopen row_count负责确认持久化数量。

### 2.7 运行和测试证据

- active acceptance report：`status=success`。
- lifecycle history：Step 11.3 activate success。
- 本轮仓库外临时目录测试：28 passed，3 dependency warnings。
- 本轮未重建真实index、未生成真实Embedding、未写/删Milvus。

---

## 3. 交给 TASK-004：离线算法候选

以下仅是已确认调用点，不包含优劣判断。

| 算法性处理 | 调用点 | 条件/参数 | 证据 |
|---|---|---|---|
| CleanedTextUnit过滤 | `should_skip_unit` | empty、image、short other | active源数据5条被跳过；child source IDs对应缺口 |
| 长unit拆分 | `ChildParentChunker._expand_long_units` | unit长度超过parent size | pseudo unit ID、source offset、flag |
| Parent聚合 | `_build_parent_unit_groups` | 1500 token | 按unit顺序聚合 |
| Parent overlap | `_overlap_tail` | 150 token | 回收完整尾部units |
| Child固定窗口 | `_split_text_windows_with_offsets` | 500/50 token | active child token_count/offset字段 |
| Tokenizer fallback | 同上 | offset异常/不可用 | catch Exception后字符切分 |
| Source-unit对齐 | `_unit_ids_for_span` | char span overlap | child source_unit_ids |
| Embedding | `TextEmbedder.encode_texts` | m3e-base/cuda/batch32/normalize | manifest+NPY+report |
| Vector index | `create_or_reset_child_chunk_collection` | AUTOINDEX/COSINE | schema code+report |
| Self retrieval | `OfflineIndexVerifier._verify_milvus` | evenly spaced samples/top-k3 | report 3/3 |

TASK-004 不得把以下内容当作已确认：

- tokenizer在active历史构建中从未发生fallback；现有证据不足。
- Raw PDF解析/清洗算法属于本链；当前入口不可达。
- recursive/heading/paragraph策略进入active index；它们仅有smoke/test证据。

---

## 4. 交给 TASK-005：对象、字段和状态责任

### 4.1 对象创建者/写入者

| 对象/状态 | 创建/写入者 | 序列化/落盘 |
|---|---|---|
| CleanedTextUnit contract | `CleanedTextUnitSchema`验证；上游producer创建内容 | source JSONL由本任务边界外产生 |
| scope enrichment | 当前HEAD `OfflineIndexBuilder.build` | 传入Chunk；历史active artifact未体现 |
| Parent Chunk | `build_parent_chunk_v1` | `parent_chunks.jsonl` |
| Child Chunk | `build_child_chunk_v1` | `child_chunks.jsonl` |
| parent-child linkage | `ChildParentChunker` | parent child IDs / child parent ID |
| Embedding matrix | `TextEmbedder.encode_texts` | `child_vectors.npy` |
| VectorIndexRecord v2 | `build_vector_index_record_v2` | `vector_index_records.jsonl` |
| Milvus physical record | `build_milvus_child_chunk_record` | `client.insert` |
| Collection/schema | `create_or_reset_child_chunk_collection` | Milvus Lite DB |
| Build result | `OfflineIndexBuildResult` | stdout/acceptance report嵌套 |
| Index manifest | `IndexManifest` | `index_manifest.json`，最后写 |
| Verification status | `OfflineIndexVerifier` | `step_11_2_acceptance_report.json` |
| Active pointer | `IndexLifecycleManager.activate` | `active_index.json` + lifecycle history |

### 4.2 状态变化

```text
no version dir
-> output dir created
-> parent/child artifacts written
-> vectors/vector records written
-> collection created/reset
-> batches inserted
-> client closed
-> client reopened and row_count verified
-> manifest written (build success marker)
-> verifier success/failed report
-> separate lifecycle activation
```

### 4.3 TASK-005必须关注的基线差异

当前HEAD builder在严格验证后补齐：

```text
tenant_id=default
kb_id=default
file_id=source_name or doc_id
```

但active历史：

- source 1,417/1,417均无 `tenant_id/kb_id/file_id` key；
- parent 69/69无这些key；
- child 258/258无这些key；
- vector records 258/258无这些key。

这不是推测，而是只读字段统计。active历史构建时Git commit未知，不能把当前HEAD行为回写为历史事实。

---

## 5. 与 TASK-002 在线读取核对

### 5.1 已证实一致项

- active index version一致。
- Milvus Lite DB/collection一致。
- model/dim/metric一致：m3e-base/768/COSINE。
- 离线仅索引child，保留`parent_chunk_id`；在线命中child后回填parent。
- parent/child/vector/Milvus数量一致。
- manifest lineage和active pointer一致。

### 5.2 已证实冲突/差异项

- TASK-002 trusted scope读取需要 `tenant_id/kb_id/file_id`。
- active JSONL artifacts明确缺少这些key。
- 当前HEAD builder会补齐，但active artifact没有。

### 5.3 unknown项

- active历史Milvus entity内部是否有默认scope字段和值。
- active历史DB完整describe schema与当前HEAD schema是否逐字段相同。
- 历史构建代码是否已经有scope default mapping。

TASK-002字段需求只能作为核对线索，不得反向证明历史离线写入。

---

## 6. 新发现但未处理的问题

### FINDING-003-001

当前HEAD scope补齐与active历史artifact字段不一致。

### FINDING-003-002

当前 `m3e_milvus_lite_real_v1.yaml` 默认source/dataset与active manifest有效值不同；CLI支持override，但原始argv未知。

### FINDING-003-003

失败构建可留下无manifest的非空目录；当前链无自动cleanup/resume。

### FINDING-003-004

insert helper按提交batch长度累计，不解析insert result；最终row-count复核才确认实际持久化数量。

### FINDING-003-005

Token offset异常静默回退字符切分，单条Chunk无显式fallback标志。

### FINDING-003-006

`child_chunk_index`按doc重置；全局唯一字段是child/chunk ID。

### FINDING-003-007

manifest note“不激活”描述Step 11.2，history显示Step 11.3随后激活；两者是不同阶段，不是同一步骤的相互否定。

以上均未修复、未修改、未重新验证真实写入。

---

## 7. 未确认问题

1. active artifact构建时的精确Git commit/tag。
2. active run完整CLI argv和override来源。
3. Raw PDF -> CleanedTextUnit真实生产链。
4. active历史Milvus entity scope字段的存在性和值。
5. active历史collection完整只读schema/entity样例。
6. 真实生产失败日志和partial output样例。
7. 仓库外调度器是否调用通用build CLI。

下游不得将上述内容写成已确认事实。

---

## 8. 状态分类摘要

### active

- `run_step_11_2_acceptance.py`
- `build_offline_index.py`
- `OfflineIndexBuilder`
- fixed parent-child chunker
- `row_to_unit/should_skip_unit`
- real m3e embedding
- child vector record v2
- child Milvus store
- verifier
- lifecycle activation

### experimental

- recursive/heading/paragraph parent-child smoke profiles
- hash embedding smoke/test
- artifacts-only smoke indexes

### inactive

- flat `chunk_v1` chunking functions
- flat `milvus_chunk_store.py`

### legacy

- Mini-RAG `build_chunk` compatibility
- FAISS `rag/Index/indexer.py`
- `init_mini_rag.py`

### unknown

- Raw PDF parsing/cleaning producer
- historical build commit/argv
- historical Milvus scope fields

---

## 9. 建议交接对象

- Primary：Algorithm Lead / TASK-004
- Secondary：Architect / TASK-005
- Verification：QA / Reviewer / TASK-006

## 10. 建议任务状态

建议 TASK-003：`review`。

调查已覆盖真实入口、输入边界、Chunk、Embedding、Milvus写入、结果确认、幂等/异常及候选状态；剩余证据缺口已显式标记为 unknown。