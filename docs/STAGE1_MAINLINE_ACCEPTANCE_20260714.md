# 第一阶段主链路修正验收报告

- 版本：`stage1-mainline-evidence-v7.1-20260714`
- 日期：2026-07-14
- 范围：ProjectInput → Agent/Workflow → RAG Tool/Service → Model Gateway → Section → Citation → HardGate

## 1. 验收结论

第一阶段的**代码修正与自动化验收已完成**。当前代码已经通过单元、回归、主链路契约与 Fake 端到端测试。

尚需在目标 Windows 环境完成一次真实运行验收，因为当前检查环境无法加载用户本机的 Qwen2.5-1.5B、BGE Reranker、CUDA 和 Milvus Lite 数据库。

## 2. 原问题根因

原始清洗数据和 Parent Chunk 中存在安全设计证据，但 RAG 适配阶段存在两次错误处理：

1. `RAGTool` 将完整 Child/Parent 记录压缩为 500 字预览；
2. `LegacyRAGService` 和 Agent 回退逻辑使用 `match_text[:500]` 构造 Citation。

因此 Parent 后半段的 JWT、任务级授权、Schema 验证和输出脱敏没有进入 `CitationSchema.quote_text`。下游 CitationLinker 又允许低置信度弱匹配，最终造成形式上有引用、语义上不成立的假阳性。

## 3. 阶段任务验收矩阵

| 阶段任务 | 结果 | 实现说明 |
|---|---|---|
| 真实 ProjectInput 进入主链路 | 通过 | 新增生产入口 `scripts/run_pipeline.py`；要求显式 ProjectInput；task_id、source_materials、章节和输出要求完整传递 |
| 删除业务硬编码 | 通过 | Demo 默认值仅保留在 `run_demo.py`；生产入口不注入固定标题、章节、检索问题和引用策略；移除 CitationAnchor 与 RAG 查询扩展中的固定业务场景 |
| 统一状态模型 | 通过 | Task、Workflow、Agent、Tool、RAG 输出统一使用 `ExecutionStatus` |
| 修复错误传播 | 通过 | Tool 错误以 `ErrorSchema` 逐层传播；Workflow 遇失败停止；CLI 失败返回非零退出码 |
| 支持分章节生成 | 通过 | 新增 `DocumentPlanSchema`/`SectionPlanSchema`；每章独立保存输入、Prompt、模型输出、Citation、状态、截断和 Eval |
| 截断检测与恢复 | 通过 | 检测 finish_reason、未闭合 JSON、残句和最小长度；支持局部续写及缩短上下文后的整章重生成 |
| Citation 章节绑定 | 通过 | Child evidence 与 Parent context 分离；一个 Parent 下多个命中 Child 展开为独立 Citation；Binding 定位到文档、章节、段落、Claim、Child Chunk |
| 基础上下文 Schema | 通过 | User、Task、Conversation、Business、Evidence、Generation、Runtime Context 已分层 |
| Eval 硬失败规则 | 通过 | 缺章节、截断、缺引用、非法引用、未验证 Grounding、失败章节、结构错误、项目事实越界均不能成功 |
| 主链路集成测试 | 通过（代码环境） | 26 项测试通过；FakeRAG/FakeLLM Demo 与显式 ProjectInput 生产入口均完整通过；生产入口不再导入 Demo 模块 |

## 4. 关键修正

### 4.1 RAG 证据语义

- `match_text`：实际命中的 Child evidence；
- `context_text`：用于生成的完整 Parent context；
- Compact Context 仅用于日志/展示，不再作为 Citation 数据源；
- ContextPacker 的完整 `selected_results` 优先于 500 字预览；
- 同一 Parent 下的 `matched_child_chunks` 全部保留并展开为 Citation。

### 4.2 引用真实性

- 清除模型自行输出的 Citation marker，再由系统重建；
- 低词法支持度的 Claim-Evidence Binding 被拒绝；
- 合法 Binding 写入：
  - `grounding_verified=true`
  - `grounding_score`
  - `grounding_policy=lexical_strict_v2`
- HardGate 拒绝任何未经过 Grounding 验证的 Binding；
- 删除“追加一条知识库摘录以强行过门禁”的 CitationAnchor 兜底。

### 4.3 真实输入范围

- `ProjectInput.task_id` 与 Task runtime ID 保持一致；
- `source_materials.file_ids/doc_ids/kb_ids` 写入 Task；
- `doc_ids` 转为 Dense Milvus filter 和 BM25 多文档过滤条件；
- 生产输入章节列表不一致、重复章节或 Citation 章节越界时直接校验失败。

### 4.4 项目事实边界

对高风险确定性事实进行运行时检查：

- 设备型号、技术名词、服务器/GPU/CPU/NPU 配置；
- 人员数量、团队配置；
- 百分比、并发、周期、金额、容量等具体指标。

如果这些内容既不在 ProjectInput 中，也不在检索证据中，且没有写“待补充/需项目方确认”，章节会被标记失败。

### 4.5 Demo 与生产入口隔离

- 新增 `scripts/mainline_runtime.py` 作为共享运行时；
- `run_pipeline.py` 直接调用 `run_mainline()`，不再导入 `run_demo.py`；
- `run_demo.py` 仅负责 Demo 默认值和展示输出；
- 生产入口执行 `--help` 不会加载 Demo 配置或产生 Demo 日志副作用。

## 5. 自动化验证结果

```text
PYTHONPATH=backend:scripts python -m pytest backend/tests -q
26 passed
```

```text
PYTHONPATH=backend python -m compileall -q backend scripts
COMPILE_OK
```

Fake 全链路：

```text
run_demo_version: stage1-mainline-evidence-v7.1-20260714
[DocumentPlan] sections=8 source=project_input
[HardGate] passed=True failures=[]
Status: success
```

显式 ProjectInput 生产入口：

```text
[DocumentPlan] sections=3 source=project_input
[HardGate] passed=True failures=[]
task_id: task_example
status: success
```

真实项目数据回归确认：

```text
doc_002_single_column_paper_parent_000003_child_0003
包含：JWT、用户仅可访问自身 task/agent、Schema 验证、输出脱敏
```

该 Child 现在可以被构造成独立 Citation，不再因 Parent 前 500 字截断而丢失。

## 6. 本机最终验收命令

### Demo 回归

```powershell
D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe `
  D:\MyCode\rag-agent\scripts\run_demo.py
```

### 显式 ProjectInput 生产入口

```powershell
D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe `
  D:\MyCode\rag-agent\scripts\run_pipeline.py `
  --project-input-file D:\MyCode\rag-agent\data\examples\project_input.example.json
```

本机真实链路应确认：

1. 版本为 `stage1-mainline-evidence-v7.1-20260714`；
2. 日志出现 `[DocumentPlan]`；
3. Citation 数量不再固定等于 Parent 数量；
4. 安全证据对应实际 Child Chunk，而不是 Parent 前 500 字；
5. 低置信错误引用被拒绝或触发 GroundedRegeneration；
6. HardGate 通过时所有 Binding 均带 `grounding_verified=true`；
7. 最终成功返回退出码 0，失败返回非零退出码。

## 7. 当前边界

本阶段没有把每个章节拆成独立微服务或 LangGraph 节点。当前仍是模块化单体，文档规划、章节生成、校验、修复和合并在 SchemeWriterAgent 内部以显式阶段执行并完整留痕。这满足第一阶段主链路修正目标；更细粒度的图工作流、Checkpoint 和章节级动态重检索属于下一阶段。
