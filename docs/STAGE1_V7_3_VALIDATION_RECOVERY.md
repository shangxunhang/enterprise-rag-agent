# Stage 1 v7.3 Validation Recovery

本版本针对 2026-07-14 真实 Qwen2.5-1.5B + Milvus 运行暴露的三个问题：

1. `project_fact_boundary_respected` 将建设目标和培训描述误判为项目资源事实；
2. “项目概述”串写技术选型、培训、安全和实施内容；
3. “技术方案”完整生成但超长，原有 retry 仍可能自由扩写。

## 修改

- 项目事实边界只重点拦截资源、人员配置、预算、工期、性能指标等高风险确定性承诺。
- “拟建设一套系统”和“为相关人员提供培训”不再按资源事实误判。
- 采购服务器、配置内存、招聘工程师、组建项目团队等无依据承诺继续失败。
- 为不同章节注入运行时 `section_contract`，不修改 ProjectInput/Citation Schema。
- “项目概述”增加高置信章节作用域校验。
- 事实边界或章节作用域失败时，触发一次 `scheme_section_validation_rewrite` 定向修订。
- 完整但超长的章节进入 `scheme_section_compression`，使用更小 token budget 压缩原文，而不是再次自由生成。

## 验证

```text
33 passed
compileall passed
```

真实环境运行时重点观察：

```text
[ValidationRewrite] START/END
[SectionCompression] START/END
[SectionValidation] ... scope_violations=...
```
