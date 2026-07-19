# `run_demo` 完整业务调用图

本图从 `scripts/run_demo.py::run_demo` 展开，省略 Pydantic 字段校验、序列化和普通 getter。

## 1. 顶层主链

```mermaid
flowchart TD
    A["run_demo.run_demo"] --> B["mainline_runtime.run_mainline"]
    B --> C["MainlineApplicationService.run"]
    C --> C1["Task / Workspace / RuntimeOptions"]
    C --> C2["SupervisorFactory.build"]
    C2 --> C3["RAGServiceFactory + AgentQualityFactory + ModelGatewayFactory"]
    C2 --> C4["AgentRegistry"]
    C --> D["SupervisorAgent.run"]
    D --> D1["WorkflowRouter.route"]
    D --> E["NativeWorkflowEngine.execute"]
    E --> E1["AgentNodeAdapter.execute"]
    E1 --> N["ProjectInputNormalizerAgent.run"]
    E1 --> S["SchemeWriterAgent.run"]
    S --> U["SchemeGenerationUseCase.run"]
    U --> R["RAG / Evidence 子图"]
    U --> G["逐章生成 / Self-RAG 子图"]
    U --> H["evaluate_scheme_draft"]
    U --> I["SchemeCaptureService"]
    E --> J["GraphStateApplier + WorkflowStateController"]
    C --> K["汇总文档、任务状态和产物路径"]
```

Workflow 不再经过 `WorkflowStepDispatcher -> AgentStepHandler`。固定 Agent 节点由 `AgentNodeAdapter` 直接从 `AgentRegistry` 取出并执行，同时保留隔离状态副本和 Delta 提交边界。

## 2. RAG / Evidence 子图

```mermaid
flowchart TD
    A["SchemeEvidenceService._call_rag_tool"] --> B["ObservedRAGService.retrieve"]
    B --> C["RAGService.retrieve"]
    C --> D["RAGRequestMapper.map"]
    D --> E["RetrievalRuntime.retrieve"]
    E --> F{"Engine 已初始化?"}
    F -- "否" --> G["ParentChildRuntimeFactory.build"]
    G --> G1["StaticRetrievalSpecLoader"]
    G --> G2["Intent / Gate Policy Loader"]
    G --> G3["ComponentRegistry.build"]
    G --> H["ParentChildRAGEngine.run"]
    F -- "是" --> H
    H --> I["ParentChildRetrievalPipeline.run"]
    I --> J["AdaptiveRetrievalPlanner.plan"]
    J --> K["QueryTransformSelector.transform"]
    K --> L["identity | multi_query | hyde"]
    L --> M["Dense Child + BM25 Child"]
    M --> N["Source Fusion"]
    N --> O["Parent-Child Enrichment"]
    O --> P["Query Fusion"]
    P --> Q["Reranker.rerank"]
    Q --> R["EvidenceAssessor.assess"]
    R --> S{"CorrectiveRetrievalGate.decide"}
    S -- "充分或无预算" --> T["ContextGate.pack"]
    S -- "不充分且有预算" --> U["CorrectiveQueryPlanner.plan"]
    U --> M
    T --> V["检索评测 + RunCapture"]
    V --> W["RAGResultMapper.map"]
    W --> X["RAGEvidenceContractBuilder.build"]
    X --> Y["EvidenceBundleSchema"]
    Y --> Z["引用去重/重映射"]
```

注意：Planner 不决定是否执行纠错。`EvidenceAssessor` 每次都执行；Gate 只在证据不充分且剩余预算大于零时打开；`CorrectiveQueryPlanner` 只在 Gate 打开后运行。

## 3. 逐章生成 / Self-RAG 子图

```mermaid
flowchart TD
    A["遍历 DocumentPlan.sections"] --> B["选择文档级或章节级证据"]
    B --> C["activate_workflow_budget"]
    C --> D["SectionGenerationService._generate_section"]
    D --> E["LLMContextManager + PromptService"]
    E --> F["SectionModelService -> ModelGateway"]
    F --> G["CitationService 绑定/验证"]
    G --> H["SelfRAGLiteGenerationChecker.check"]
    H --> I{"Self-RAG 决策"}
    I -- "supported" --> J["章节检查"]
    I -- "need_rewrite" --> K["LocalRewriteRepairStrategy"]
    K --> K1["重绑引用 + Self-RAG 复检"]
    K1 --> J
    I -- "need_retrieve_more" --> L["SchemeEvidenceService 补检索"]
    L --> M{"WorkflowBudget 还有轮次?"}
    M -- "是" --> D
    M -- "否" --> N["标记未解决/需人工审阅"]
    J --> O{"Citation 硬约束仍失败?"}
    O -- "是且有预算" --> P["应用层 recovery RAG"]
    P --> D
    O -- "否" --> Q["SectionEvidenceBundle + Section"]
    Q --> R{"next section"}
    R -- "有" --> A
    R -- "无" --> S["SchemeDraft + Document Hard Gate + Capture"]
```

`WorkflowBudget` 按章节统一统计检索轮数、改写轮数、LLM 调用数和预留输出 Token，所有恢复路径共享同一份预算。

## 4. 首次请求与复用

首次真实检索会解析活动索引、读取静态检索规格与两份小策略、构建 Retriever/Reranker/Context Packer 等资源。后续请求复用 Engine。只有活动索引指针改变并显式 reload 时，才会等待在途请求并原子替换 Engine。
