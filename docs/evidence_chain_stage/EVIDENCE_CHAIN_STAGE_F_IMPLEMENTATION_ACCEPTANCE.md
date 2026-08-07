# Stage F 实施与验收证据：Atomic Commit & True Idempotency

> 记录日期：2026-08-04  
> 阶段状态：**通过，Stage A–F 可以正式冻结**  
> 证据性质：仓库实现审计 + Runtime 复验 + JUnit 复验 + 独立并发/双进程攻击  
> 最终正式 Store：`E:\rag-agent-runtime\stage_f_release_store_final`

---

## 1. Stage F 解决什么问题

Stage A–E 已经能够证明：

```text
文档来源已分类
→ Chunk 与索引保留文档身份和证据能力
→ 在线检索只使用授权范围内的 Evidence
→ Final Gate 决定允许和禁止生成的 Claim
→ Writer 强制执行 Gate
→ Claim Audit 验证最终正文
→ Document Hard Gate 重算正式交付资格
```

但 Stage E 结束时仍然只证明：

> 当前这份文档内容具备正式交付资格。

它还不能证明：

```text
最终落盘的是不是刚刚验收通过的同一份正文
同一个版本是否可能被不同内容覆盖
调用超时后重试是否会重复发布或制造冲突
两个 Worker 同时发布同一版本时是否会混写
Document、Eligibility Proof、Manifest 写到一半崩溃时是否算发布
正式发布后 Artifact 被修改时是否能够发现
```

Stage F 因此承担证据链的最后一步：

```text
Stage E Formal Delivery Credential
→ Immutable Release Request
→ Atomic Commit
→ Idempotent Replay
→ Concurrent Version Exclusion
→ Post-commit Verification
→ Deliverable Final Result
```

Stage F 不重新判断业务语义，不修改 Stage D/E Gate。它解决的是：

> 已通过内容验证的文档，如何安全、唯一、可恢复、不可覆盖地成为正式版本。

---

## 2. 最终实施范围

Stage F 最终收敛到五项能力：

```text
原子提交
真实幂等
并发互斥
崩溃恢复判断
发布后篡改校验
```

明确未扩展：

```text
Human Review Web UI
S3 / OSS 对象存储
PKI / 数字签名
Release Revocation
后台 Blob GC
分布式共识
多区域一致性
数据库式分布式事务
```

复杂度控制结果：

```text
新增生产文件：0
修改生产文件：4
生产代码净增：347 行
新增测试文件：2
测试代码新增：378 行
新增 Schema 类：0
新增正式发布调用点：0
```

生产修改文件：

```text
backend/apps/enterprise_document/schemas/release_publication.py
backend/apps/enterprise_document/services/agent_rag_v1/artifact_store.py
backend/apps/enterprise_document/services/release_publication.py
backend/apps/enterprise_document/services/scheme_writer/stage_service.py
```

新增测试：

```text
backend/tests/test_stage_f_atomic_idempotent_release.py
backend/tests/test_stage_f_release_concurrency.py
```

---

## 3. Release Request Fingerprint

新增稳定发布请求指纹：

```text
release_request_sha256
```

参与 Hash 的字段：

```text
task_id
run_id
document_id
draft_id
document_version
output_format
draft_sha256
hard_gate_sha256
generation_report_sha256
version_decision_sha256
publisher
release_policy
```

发布策略冻结为：

```text
atomic_manifest_commit_v1
```

明确排除：

```text
published_at
verified_at
当前系统时间
retry_count
worker_id
process_id
临时文件路径
日志时间
Checkpoint 时间
```

计算时机：

```text
序列化 Draft / Hard Gate / Generation Report / Version Decision
→ 计算各自 canonical SHA-256
→ 计算 release_request_sha256
→ 获取版本锁
→ 检查已有 Manifest
→ 首次提交时才生成发布时间
```

这保证：

```text
同一稳定业务请求
+ 不同调用时间
+ 不同 Service 实例
+ 不同 Store 实例
+ 进程重启
→ request SHA-256 不变
```

---

## 4. 发布版本身份

版本身份冻结为：

```text
task_id + document_id + document_version
```

由此计算：

```text
idempotency_key
release_id
Canonical Manifest Ref
版本级 Lock Path
```

`run_id` 不进入版本地址，但进入 `release_request_sha256`。

最终语义：

```text
同版本 + 同 Request
→ 幂等返回已有 Release

同版本 + 不同 Run
→ ReleaseConflictError

同版本 + 不同正文 / Gate / Proof / Report / Version Decision
→ ReleaseConflictError

新 document_version
→ 新的独立 Release
```

---

## 5. 原子发布模型

Stage F 不实现复杂多文件回滚，而采用：

```text
不可变 Content Blob
+
Canonical Manifest 唯一 Commit Marker
```

引用规则：

```text
Document Blob:
release-blob://<idempotency_key>/document/<document_sha256>

Eligibility Blob:
release-blob://<idempotency_key>/eligibility/<proof_sha256>

Canonical Manifest:
release://<idempotency_key>/manifest
```

唯一 Commit Point：

```text
Canonical Manifest 的原子 FileArtifactStore.put_json()
```

正式发布定义：

```text
Document / Proof Blob 存在
但 Canonical Manifest 不存在
→ NOT_COMMITTED
→ 未正式发布

Canonical Manifest 存在
且 Manifest / Document / Proof 完整验证通过
→ COMMITTED
→ 正式发布
```

因此 Manifest 提交前中断可以留下孤立 Blob，但不会留下“半个正式版本”。

---

## 6. 跨进程并发互斥

直接复用：

```text
backend/core/persistence/interprocess_lock.py
InterProcessFileLock
```

锁路径：

```text
<release_store_root>/.release-locks/<idempotency_key>.lock
```

锁粒度：

```text
task_id + document_id + document_version
```

锁覆盖完整事务：

```text
读取并验证已有 Canonical Manifest
→ 比较 release_request_sha256
→ 生成首次 Commit 时间
→ 构造 Eligibility Proof
→ 写 Document Blob
→ 写 Eligibility Blob
→ 提交前校验
→ 提交 Canonical Manifest
→ 提交后回读校验
```

`FileArtifactStore` 自身的 `RLock` 仍用于单 Store 实例的单文件操作，但不再被当作版本级并发控制。

---

## 7. publish() 最终行为

生产正式发布入口仍然只有一个：

```text
SchemeGenerationStageService.persist_deliverable()
→ ReleasePublicationService.publish()
```

仓库静态搜索结果：

```text
release_publication_service.publish() 正式调用点：1
```

发布流程：

```text
1. 计算稳定 request SHA-256
2. 计算 release_id / idempotency_key
3. 获取版本级跨进程锁
4. 读取 Canonical Manifest
5. 已存在：完整 verify
6. Request 相同：返回已有 Release，idempotent_replay=true
7. Request 不同：ReleaseConflictError
8. 未存在：生成首次 commit 时间
9. 重新执行 Stage E 发布资格验证
10. 写 Document Blob
11. 写 Eligibility Blob
12. 构造并预验证 Manifest
13. 原子提交 Canonical Manifest
14. 回读 Manifest / Document / Proof
15. 完整验证后返回 PublishedRelease
```

`persist_deliverable()` 只有在 `publish()` 成功返回已验证 Release 后，才构造：

```text
DeliverableFinalResult
```

`persist_rejected()` 不调用发布服务。

发布基础设施异常不会被伪装成业务拒绝。

---

## 8. Stage E 发布资格重新验证

发布前不直接信任历史布尔值。

Stage F 重新要求：

```text
Document Hard Gate passed
output_status 可交付
全部 Stage E Writer Check 为 true
Task / Run / Document / Version 一致
Version Decision 与 document_version 一致
Generation Report Task / Run / Version 一致
每个 Section 当前 Artifact 重新验证通过
无 pending human review
```

Eligibility Proof 绑定：

```text
release_request_sha256
draft_sha256
hard_gate_sha256
generation_report_sha256
version_decision_sha256
section_artifact_sha256
writer_gate_binding_sha256
claim_audit_sha256
```

这保证最终 Release 与 Stage E 验收的是同一份当前 Artifact。

---

## 9. 崩溃恢复判断

没有新增 Recovery Service 或恢复状态机。

恢复判断直接由 `publish()` 开头完成：

### 9.1 Manifest 不存在

```text
NOT_COMMITTED
孤立 Blob 不视为发布
允许安全重试
```

### 9.2 Manifest 有效且 Request 相同

```text
COMMITTED_MATCH
可能是首次提交后响应丢失或 Checkpoint 未更新
返回已有 Release
idempotent_replay=true
```

### 9.3 Manifest 有效但 Request 不同

```text
COMMITTED_CONFLICT
ReleaseConflictError
不允许覆盖
```

### 9.4 Manifest 存在但内容损坏

```text
COMMITTED_CORRUPTED
ReleaseVerificationError
禁止自动覆盖或修复
```

---

## 10. 发布后完整性验证

回读验证覆盖：

```text
Manifest Envelope
Manifest ArtifactReference
Manifest Payload
Manifest Self Hash

Document Blob Envelope
Document ArtifactReference
Document Payload SHA-256

Eligibility Blob Envelope
Eligibility ArtifactReference
Eligibility Proof SHA-256

release_id
idempotency_key
release_request_sha256
Task / Run / Document / Draft / Version
Document / Proof Blob Ref
release_policy
```

此外还会重新验证 Store 中 Draft 的：

```text
Section Artifact Hash
Writer Gate Binding Hash
Claim Audit Hash
当前 Section formal deliverable eligibility
```

任意不一致：

```text
ReleaseVerificationError
```

损坏的正式版本不会被新内容覆盖。

---

## 11. 故障注入验收

### 11.1 Document Blob 写失败

```text
Canonical Manifest 不存在
正式发布不可见
相同请求重试成功
```

### 11.2 Eligibility Blob 写失败

```text
允许留下孤立 Document Blob
Canonical Manifest 不存在
正式发布不可见
相同请求重试成功
```

### 11.3 Manifest Commit 前失败

```text
允许 Document / Proof 成为孤立 Blob
Canonical Manifest 不存在
相同请求重试成功
```

### 11.4 Manifest 已提交但响应丢失

```text
第一次调用收到技术异常
Manifest 实际已提交
第二次读取已有 Manifest
返回已有 Release
idempotent_replay=true
最终正式 Artifact 数量=3
```

---

## 12. 幂等验收

### 12.1 默认时间精确重放

连续两次调用，不传 `published_at`：

```text
release_id 相同
Manifest 完全相同
published_at 相同
Document Artifact 相同
Eligibility Artifact 相同
第二次 idempotent_replay=true
```

### 12.2 不同 Service / Store 实例

两个独立：

```text
ReleasePublicationService
FileArtifactStore
```

共享同一 Root，返回同一个 Manifest。

### 12.3 进程重启恢复

重新创建 Service 和 Store 后：

```text
读取已有 Canonical Manifest
不重新生成时间
不重新生成 Manifest
idempotent_replay=true
```

---

## 13. 并发验收

### 13.1 仓库自动化线程并发

测试使用：

```text
两个独立 ReleasePublicationService
两个独立 FileArtifactStore
同一个 Store Root
```

相同内容并发：

```text
成功数：2
Manifest 相同：true
Manifest ArtifactReference 相同：true
published_at 相同：true
idempotent_replay 数量：1
最终 JSON 数量：3
最终 verify：true
```

不同内容并发：

```text
成功数：1
ReleaseConflictError：1
混合 Artifact：false
成功方重试：幂等成功
失败方重试：稳定 Conflict
版本被毒化：false
最终 JSON 数量：3
```

### 13.2 独立验收压力测试

本次验收额外连续执行：

```text
20 轮 test_stage_f_release_concurrency.py
```

结果：

```text
20 / 20 轮通过
未发现混合 Artifact
未发现版本毒化
```

### 13.3 独立双进程精确重放

两个真正独立的 Python 进程读取同一份已序列化 Artifact，同时发布：

```text
进程 1：成功，idempotent_replay=false
进程 2：成功，idempotent_replay=true
release_id 相同
退出码：0 / 0
最终 JSON 数量：3
```

### 13.4 独立双进程不同内容竞争

两个真正独立的 Python 进程发布同一版本的不同 Artifact：

```text
一个进程成功
另一个进程 ReleaseConflictError
退出码：0 / 非零
最终 JSON 数量：3
```

证明版本级 OS Lock 不仅在单进程线程场景成立，也能约束独立进程。

---

## 14. 篡改验收

实际覆盖：

```text
Manifest Envelope
Manifest Payload
Document Blob
Eligibility Blob
release_request_sha256
```

全部触发：

```text
ReleaseVerificationError
```

已提交的损坏版本不会被重新发布逻辑自动覆盖。

---

## 15. Runtime Acceptance

正式 Runtime：

```text
E:\rag-agent-runtime\stage_f_release_runtime_acceptance_final.json
```

SHA-256：

```text
e58b43c3ffb34a7e6f4da872c32cb8e4c306b5b3ea3f91e98c8ae81b76063c84
```

结果：

```text
passed = true
Hard Failure Count = 27
Hard Failure True Count = 0
```

本次独立复验 Runtime：

```text
E:\rag-agent-runtime\review_stage_f_release_runtime_acceptance_final.json
```

SHA-256：

```text
0689059e5b1f2314a5005dd6909994c773dca0b3421dc54e500d85d354c4e5c7
```

结果同样为：

```text
passed = true
hard_failure_count = 0
```

Runtime 覆盖：

```text
合法发布
默认时间精确重放
不同 Service 重放
同版本内容冲突
跨 Run 冲突
新版本发布
Hard Gate 拒绝
三类 Commit 前故障
故障后重试
Commit 后响应丢失恢复
相同内容并发
不同内容并发
混合 Artifact 检测
版本毒化检测
孤立 Blob 不作为发布
Manifest / Document / Proof 篡改
拒绝路径不发布
Active Index 不变化
```

---

## 16. JUnit 验收

### 16.1 Focused

独立复跑：

```text
95 passed
0 failures
0 errors
```

当前文件：

```text
E:\rag-agent-runtime\stage_f_focused_junit_final.xml
```

当前 SHA-256：

```text
9a760fd3c3a11d3424a4d1894b3c6865beb82042414b32f8eb253c8ec378c1d9
```

### 16.2 Regression

正式回归组成：

```text
Stage A–F / RAG / Section / Document Core  433
Agent-RAG V1                                  80
Phase2–8 Mainline                             45
Total                                        558
```

本次独立复验：

```text
Core       433 passed
Agent-RAG   80 passed
Phase2      17 passed
Phase3       5 passed
Phase4       5 passed
Phase6       4 passed
Phase7       4 passed
Phase8      10 passed
```

合计：

```text
558 passed
0 failures
0 errors
```

独立复验合并文件：

```text
E:\rag-agent-runtime\review_stage_f_regression_junit_final.xml
```

SHA-256：

```text
d473fff811c399e20984b7483004e6b88b382f1701a34573cfc1643accbbf4c0
```

正式回归文件：

```text
E:\rag-agent-runtime\stage_f_regression_junit_final.xml
```

当前 SHA-256：

```text
b4603c0f38af000b9bc04f492184198e5e939c174cf2623b7e7b63697a470f4c
```

### 16.3 Windows SQLite 运行规则

将所有 Phase2–8 文件放在同一个 pytest 进程和同一个 basetemp 中，会因为仍打开的：

```text
langgraph_checkpoints.sqlite3
```

产生 Windows `WinError 32` 清理错误。

这不是业务断言失败。

正式验收方式是：

```text
每个主线测试文件
→ 独立 pytest 进程
→ 独立 basetemp
→ 最后合并 JUnit
```

按正式方式复跑后 45 项全部通过。

---

## 17. 最终报告与证据 Hash

本次独立复验后重新运行 Finalizer，使最终报告绑定当前 JUnit 文件。

仓库报告：

```text
data/processed/indexes/stage_f_release_acceptance_report_final.json
```

Runtime 副本：

```text
E:\rag-agent-runtime\stage_f_release_acceptance_report_final.json
```

两份 SHA-256：

```text
4c425c3119c330d62ce5d0e8802e26e59df199d1d82cce477c92e91dc9b1781c
```

最终报告结果：

```text
passed = true
stage_a_to_f_frozen = true
Tests = 653
Failures = 0
Errors = 0
Runtime Hard Failure True Count = 0
```

技术说明：

```text
docs/EVIDENCE_GATE_STAGE_F_FINAL_RELEASE.md
```

SHA-256：

```text
2bacca582567b8f4e0ab5d67c37e5edb84e0ac99d64bca0e7ebf7b37f20f7e84
```

命令文件：

```text
scripts/stage_f_focused_commands_v1.ps1
SHA-256: 4884c185950c417ae9a965010c2474d14c9cb35e32d17f915b2b7aa1d9e21ded

scripts/stage_f_regression_commands_v1.ps1
SHA-256: 2bd23b0b9f1e4301317259e98b6f34314b3e98c3fc6878434a7cb5b0aa6f74d9
```

---

## 18. 静态检查与 Active Index

```text
python -m compileall -q backend scripts
→ passed

git diff --check
→ passed
```

`git diff --check` 仅输出既有 CRLF/LF 提示，没有 whitespace error。

Active Index：

```text
Index Version:
idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e

Pointer SHA-256:
0e77cc066d3dfc47875e7991e22d41828bc5ad531cb2cbb134e6096c6e6772ce

Manifest SHA-256:
18d1aedb6bdd1fe6e6093e5879e0f205808482010ff8d48b8a9215b55287fc02
```

Stage F 未修改 Stage C 索引、Active Pointer 或 Manifest。

---

## 19. 主要证据路径

仓库内：

```text
backend/apps/enterprise_document/schemas/release_publication.py
backend/apps/enterprise_document/services/agent_rag_v1/artifact_store.py
backend/apps/enterprise_document/services/release_publication.py
backend/apps/enterprise_document/services/scheme_writer/stage_service.py

backend/tests/test_stage_f_release_publication.py
backend/tests/test_stage_f_mainline_release.py
backend/tests/test_stage_f_atomic_idempotent_release.py
backend/tests/test_stage_f_release_concurrency.py

scripts/run_stage_f_release_acceptance.py
scripts/finalize_stage_f_acceptance_v1.py
scripts/stage_f_focused_commands_v1.ps1
scripts/stage_f_regression_commands_v1.ps1

docs/EVIDENCE_GATE_STAGE_F_FINAL_RELEASE.md
data/processed/indexes/stage_f_release_acceptance_report_final.json
```

E 盘：

```text
E:\rag-agent-runtime\stage_f_release_runtime_acceptance_final.json
E:\rag-agent-runtime\stage_f_release_acceptance_report_final.json
E:\rag-agent-runtime\stage_f_focused_junit_final.xml
E:\rag-agent-runtime\stage_f_regression_junit_final.xml
E:\rag-agent-runtime\stage_f_release_store_final

E:\rag-agent-runtime\review_stage_f_release_runtime_acceptance_final.json
E:\rag-agent-runtime\review_stage_f_regression_junit_final.xml
```

---

## 20. 遗留边界

### 20.1 设计内边界

```text
孤立 Blob 不执行 GC
依赖底层文件系统支持 OS 文件锁和原子 replace
未实现对象存储、PKI、撤销和分布式一致性
```

这些均属于明确排除范围，不阻断 Stage F 验收。

### 20.2 历史 Stage F V1 Store

历史试验 Store：

```text
E:\rag-agent-runtime\stage_f_release_store_v1
```

包含 2 个旧 Manifest。只读复核结果：

```text
旧 Manifest 数量：2
当前 Final ReleaseManifestSchema 可读取：0
```

原因是最终 Schema 新增了必填 `release_request_sha256`，而历史 V1 试验 Artifact 没有该字段。

该问题不影响 Final Store，因为最终运行使用独立路径：

```text
E:\rag-agent-runtime\stage_f_release_store_final
```

治理规则必须明确：

```text
stage_f_release_store_v1
→ 历史试验/验收样本
→ 不作为 Final Formal Release Store
→ 不得与 Final Store 混用
```

后续代码审计时可以决定归档或标记该目录，但不需要为本阶段继续增加 V1/V2 双轨读取系统。

---

## 21. 最终验收结论

Stage F 已经能够证明：

> Stage E 批准的文档通过稳定发布请求指纹绑定到唯一文档版本；同一版本发布由版本级跨进程锁串行化；Document 与 Eligibility Proof 作为不可变 Blob 保存，Canonical Manifest 是唯一正式提交点；Manifest 提交前的失败不会产生正式版本；Manifest 提交后的响应丢失可以通过幂等重放恢复；同版本并发冲突不会产生混合 Artifact，也不会毒化版本；Manifest、Document 或 Proof 的发布后篡改能够被完整性验证发现。

验收状态：

```text
原子提交：通过
真实幂等：通过
线程并发互斥：通过
双进程并发互斥：通过
崩溃恢复判断：通过
发布后篡改校验：通过
主线唯一调用点：通过
Stage E 资格重验证：通过
Focused：95 passed
Regression：558 passed
总计：653 passed
Runtime Hard Failure：0 / 27
Active Index：未变化
```

最终决定：

```text
Stage F：通过
Stage A–F：正式冻结
```

证据链不再继续增加新功能。

下一步允许进入：

```text
代码审计
冗余删除
事实源和主链梳理
架构图
项目说明与简历证据整理
```
