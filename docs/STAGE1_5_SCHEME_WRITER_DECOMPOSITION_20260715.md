# SchemeWriterAgent 模块化拆分说明

## 目标

本次仅重构 `SchemeWriterAgent`，不改变 ProjectInput、RAG、Prompt、Citation、状态、错误、Trace 和最终输出语义，也不引入 LangGraph 或新算法。

## 拆分结果

原 `SchemeWriterAgent` 为 2357 行，混合了输入解析、RAG 调用、证据适配、Prompt、模型调用、截断恢复、Citation、章节生成、文档规划、HardGate 和数据捕获。

重构后 Agent 仅保留：

- Agent 身份和依赖配置；
- SemanticSectionJudge 装配；
- 服务集合初始化；
- 工作流协议入口。

业务实现迁移到：

```text
services/scheme_writer/
├── input_service.py                 # SharedState -> ProjectInput/结构化事实
├── evidence_service.py              # RAG Tool 调用与 Evidence/Citation 规范化
├── prompt_service.py                # 章节 Prompt 与动态生成约束
├── model_service.py                 # ModelGateway 调用、重试、压缩和截断恢复
├── citation_service.py              # Citation 绑定、验证、修复和 grounded regeneration
├── advisory_service.py              # 可选项目事实提示逻辑
├── section_generation_service.py    # 单章节生成用例
├── document_planning_service.py     # DocumentPlan/SectionPlan
├── capture_service.py               # DataCapture 适配
├── use_case.py                      # 整篇方案生成应用用例
├── runtime_support.py               # 时间与统一错误构造
├── constants.py                     # Citation/提示策略常量
├── base.py                          # 迁移期运行时兼容委托
└── facade.py                        # 旧私有方法兼容代理
```

## 兼容策略

`SchemeWriterServiceFacade` 暂时保留原私有方法名称，使既有测试和内部子类不需要同时重写。实际业务逻辑已不在 Agent 文件中。后续模块测试完成后，可单独移除这层兼容代理，不影响公开的 `Agent.run()` 协议。

## 验收

- `SchemeWriterAgent`：2357 行降至 55 行；
- 现有 42 项测试全部通过；
- `compileall backend scripts` 通过；
- `run_pipeline.py --help` 正常；
- 主链路输入输出 Schema 和状态语义保持不变。
