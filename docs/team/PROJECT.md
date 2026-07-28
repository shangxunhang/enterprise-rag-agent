# Project

# Project

## 项目目标
构建企业级 Agent-RAG 系统，支持基于可信知识证据生成方案文档。

## 当前核心功能

### F1 在线生成链路
Query
→ Agent / Workflow
→ RAG
→ Evidence
→ Generation
→ Quality Gate
→ Final Result

### F2 离线索引链路
Raw Document / CleanedTextUnit
→ Chunk
→ Embedding
→ Vector Database

## 当前阶段
E-001：还原当前真实代码链路和架构。

## 当前非目标
- 不新增 RAG 算法
- 不新增 Agent
- 不进行大规模重构
- 不迁移新的框架
- 不优化代码风格

## 成功标准
所有核心模块、算法、状态和 Contract 都能挂到两条真实功能链上。