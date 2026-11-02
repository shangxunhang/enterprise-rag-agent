# Multi-Agent V3 工程阅读入口

本目录是 2026-11-02 整理的公开技术摘要，用于说明当前代码的主链、证据边界和已知限制。它不替代历史冻结合同，也不将离线回归、正文生成或历史 BEST_EFFORT Release 合并成业务质量验收。

| 文档 | 内容 |
| --- | --- |
| [技术概览](TECHNICAL_OVERVIEW.md) | 生产入口、Agent 分工、RAG 与模型边界、持久化及质量控制 |
| [当前状态](CURRENT_STATUS.md) | 五项阶段状态、不同真实 Run 的区别、测试与恢复限制 |
| [问题登记](ISSUE_REGISTER.md) | 本次文档整理问题及当前尚未闭合的工程问题 |
| [发布范围](PUBLICATION_AUTHORITY.md) | 已批准的公开边界与验收条件 |
| [本地归档依赖](LOCAL_ARCHIVE_TESTS.md) | 历史回放、目录数据及平台前置条件 |
| [快照验证](VALIDATION.md) | 在实际公开工作树执行的检查 |
| [仓库维护问题](PACKAGING_ISSUES.md) | 排除、脱敏、依赖与 CI 边界 |

建议先阅读仓库 [README](../../README.md) 完成环境准备，再从 [MainlineApplicationService](../../backend/application/mainline_service.py) 和 [章节执行图](../../backend/apps/enterprise_document/workflows/section_execution_graph.py) 阅读主链。离线测试范围、依赖和配置以发布分支中的实际文件为准。

公开版本保留工程源码、全部正式测试源码、配置示例和必要技术文档。真实业务材料、Provider 原始请求与响应、运行数据库、Checkpoint、预算账本和大型诊断证据留在维护者本地。下文出现的历史文档路径是证据标识，部分原件不随公开快照分发，不能据此承诺在全新克隆中重放历史实验。

仓库整理不包含业务修复、新的真实 E2E 或付费模型调用。当前版本可用于技术阅读和限定范围的离线验证；完整业务质量与正式 Release 验收仍有缺项。
