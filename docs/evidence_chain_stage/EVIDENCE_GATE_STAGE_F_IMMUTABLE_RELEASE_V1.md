# Stage F Immutable Release Publication V1

## 1. 阶段结论

Stage F V1 已完成下游正式发布闭环：

```text
Stage E Document Hard Gate
→ Stage E Release Credential Revalidation
→ Immutable Released Document
→ Immutable Eligibility Proof
→ Immutable Release Manifest
→ Post-write Readback Verification
→ Checkpoint / Application Summary Projection
```

验收结果：

```text
Focused JUnit       82 tests / 0 failures / 0 errors / 0 skipped
Regression JUnit   545 tests / 0 failures / 0 errors / 0 skipped
Total              627 tests / 0 failures / 0 errors / 0 skipped
Runtime Hard Failures  10 total / 0 true
Active Index            unchanged
```

Stage F 没有修改 Stage D Gate 语义，也没有降低 Stage E 的正文 Hash、Claim Audit、身份/血缘和正式交付重算要求。

## 2. Stage F 解决的问题

Stage E 完成后，系统已经能够判断一个 Draft 是否具备正式交付资格，但原有 `persist_deliverable()` 只把对象写入 SharedState 和 Capture：

```text
没有独立 Release Manifest
没有内容寻址的正式发布对象
没有稳定幂等键
没有同版本冲突拒绝
没有发布后回读验证
没有对 Stage E 凭证再次验证
```

Stage F V1 将“可交付结果”升级为“可验证的不可变发布凭证”。

## 3. 发布契约

新增：

```text
ReleaseEligibilityProofSchema
ReleaseManifestSchema
PublishedReleaseSchema
```

### 3.1 ReleaseEligibilityProofSchema

该证明绑定：

```text
task_id
run_id
document_id
draft_id
document_version
draft_sha256
hard_gate_sha256
generation_report_sha256
version_decision_sha256
每个 Section Artifact SHA-256
每个 WriterGateBinding SHA-256
每个 ClaimAudit SHA-256
```

并冻结：

```text
hard_gate_passed = true
all_writer_artifacts_eligible = true
human_review_required = false
```

### 3.2 ReleaseManifestSchema

Manifest 包含：

```text
release_id
idempotency_key
task/run/document/draft/version 身份
released document artifact reference
eligibility proof artifact reference
release payload SHA-256
eligibility proof SHA-256
manifest payload SHA-256
publisher
published_at
immutable = true
```

### 3.3 PublishedReleaseSchema

最终发布凭证包含：

```text
ReleaseManifest
Manifest Artifact Reference
verified_after_write = true
idempotent_replay
```

## 4. Stage E 发布资格重新验证

Stage F 不直接信任：

```text
hard_gate.passed
formal_deliverable_eligible
claim_audit.passed
```

发布前要求 Document Hard Gate 中以下检查全部为 `true`：

```text
writer_gate_bound
writer_binding_identity_fields_present
writer_binding_section_identity_valid
writer_execution_status_consistent
writer_generation_allowed
writer_human_review_clear
writer_claim_audit_present
writer_claim_audit_schema_valid
writer_audit_section_identity_valid
writer_audit_content_hash_valid
writer_audit_binding_hash_valid
writer_evidence_lineage_fields_present
writer_audit_evidence_lineage_valid
writer_audit_claim_boundary_valid
writer_audit_claim_spans_valid
writer_classifier_policy_valid
writer_claim_audit_recomputed_valid
writer_claim_audits_passed
writer_required_qualifiers_applied
writer_section_status_deliverable
writer_formal_eligibility_recomputed
writer_formal_deliverable_eligible
```

随后再次对每个当前 Section 调用：

```text
validate_writer_section_artifact()
```

只有 `recomputed_formal_deliverable_eligible=true` 的 Section 才能进入 Release Proof。

## 5. 不可变 Artifact Store

Stage F 复用仓库已有：

```text
FileArtifactStore
```

没有建立第二套存储实现。

Store 提供：

```text
原子临时文件写入 + os.replace
Artifact Reference
SHA-256 Content Hash
大小校验
任务/Run/Version 身份校验
相同 Ref 不同内容拒绝
读取时 Hash 重算
```

每次正式发布写入三类 Artifact：

```text
release://<idempotency_key>/document
release://<idempotency_key>/eligibility
release://<idempotency_key>/manifest
```

## 6. 幂等与冲突策略

稳定幂等身份：

```text
task_id
document_id
document_version
```

计算：

```text
idempotency_key = SHA256(canonical JSON identity)
release_id = release_<idempotency_key prefix>
```

行为：

```text
同一身份 + 完全相同 Artifact
→ idempotent_replay = true
→ 返回同一 Manifest 和 Artifact Reference

同一身份 + 不同正文/Audit/Proof/Run
→ ReleaseConflictError
→ 不覆盖已有正式版本

新 document_version
→ 新 idempotency_key
→ 独立不可变 Release
```

跨 Run 对同一 Task/Document/Version 的重放会因为正式 Artifact 身份不同而拒绝，避免另一条 Run 覆盖已经发布的版本。

## 7. 发布后回读验证

发布完成后立即重新读取：

```text
Manifest Artifact
Released Document Artifact
Eligibility Proof Artifact
```

重新验证：

```text
Artifact Store Envelope Hash
Document Payload SHA-256
Eligibility Proof SHA-256
Manifest Self Hash
Task/Run/Document/Version Identity
```

任意失败抛出：

```text
ReleaseVerificationError
```

只有验证完成后才产生：

```text
verified_after_write = true
```

## 8. 主链接入

`persist_deliverable()` 现在要求：

```text
project_input
assembly
hard_gate
generation_report
version_decision
document_version
output_status
```

执行顺序：

```text
ReleasePublicationService.publish()
→ 生成 Release Credential
→ 嵌入 DeliverableFinalResultSchema
→ 写入 SchemeWriterOutput
→ 写入 SharedState
→ 写入 Checkpoint Payload
→ 暴露到 Application Summary
```

`DeliverableFinalResultSchema` 新增：

```text
release_publication
```

`SchemeGenerationGraphPayloadSchema` 新增：

```text
release_publication
```

最终 AgentResult Metadata 新增：

```text
release_id
release_manifest_ref
release_verified_after_write
```

成功发布后：

```text
need_human_review = false
```

## 9. Checkpoint Hash 规范化

Release Manifest 引入带时区 `published_at` 后，所有进入 SharedState 和终态 AgentResult 的 Pydantic 对象统一使用：

```text
model_dump(mode="json")
```

这保证：

```text
Delta Hash 计算值
Checkpoint JSON 值
Checkpoint 恢复值
```

完全一致，避免 `+00:00` 与 `Z` 的时区序列化差异导致 Graph Delta Hash 校验失败。

没有放宽 Graph Delta Hash 验证。

## 10. 拒绝路径

`persist_rejected()` 不调用 ReleasePublicationService。

Stage F 主链负向结果：

```text
Hard Gate / Writer Gate blocked
→ rejected_artifact 存在
→ deliverable_final_result = null
→ release_publication = null
→ Release Store 文件数 = 0
```

诊断性草稿不能生成正式 Release Manifest。

## 11. Runtime Acceptance

文件：

```text
E:\rag-agent-runtime\stage_f_release_runtime_acceptance_v1.json
```

SHA-256：

```text
ab30bfb168e85a02fca89b1c7d24afd3a58680bd6acdc9f31a5dd07ad67adc28
```

Release Store：

```text
E:\rag-agent-runtime\stage_f_release_store_v1
```

正式版本：

```text
v1.0 release_id = release_b243bdfebe03f2f68445b38f
v1.1 release_id = release_3e30d56bd87c9e2cde4b9ddd
```

正式 Store 共 6 个 Artifact 文件：两个版本各自的 Document、Eligibility Proof 和 Manifest。

### 11.1 Runtime Hard Failures

共 10 项，全部为 `false`：

```text
valid_release_not_verified
exact_replay_not_idempotent
same_version_different_content_not_rejected
cross_run_same_version_replay_not_rejected
new_version_release_failed
failed_hard_gate_release_not_rejected
incomplete_stage_e_credential_not_rejected
tampered_release_not_detected
release_artifacts_not_immutable
active_pointer_or_manifest_changed
```

### 11.2 Runtime 场景

```text
合法发布                     verified
相同 Artifact 精确重放       idempotent
同版本不同正文               rejected
跨 Run 同版本重放            rejected
v1.1 新版本                   published independently
Hard Gate 失败               rejected
Stage E 凭证不完整           rejected
Store Envelope 篡改          detected
```

## 12. JUnit 证据

### 12.1 Focused

```text
E:\rag-agent-runtime\stage_f_focused_junit_v1.xml
```

```text
tests      82
failures    0
errors      0
skipped     0
```

SHA-256：

```text
2eb7a72f921c5894df08ba719a05bf623ace340a05d29f979a11113acecd5358
```

### 12.2 Regression

```text
E:\rag-agent-runtime\stage_f_regression_junit_v1.xml
```

```text
tests      545
failures     0
errors       0
skipped      0
```

构成：

```text
Stage A-F / RAG / Section / Document Core  420
Agent-RAG V1                                  80
Top-level / Section / Recovery Mainline       45
```

SHA-256：

```text
e3233a741bb2b9205630ab9e97fd7441b0049f61f4241a010d346574c5c6e4f3
```

总计：

```text
627 tests
0 failures
0 errors
0 skipped
```

## 13. 可复制命令证据

Focused：

```text
scripts/stage_f_focused_commands_v1.ps1
```

SHA-256：

```text
c02a9d2fd1766226fa88a8eac4028d4c2aaf7024d12c115b594d93c9fffcbf3a
```

Regression：

```text
scripts/stage_f_regression_commands_v1.ps1
```

SHA-256：

```text
7d8a43bc03dc0225e39c774ee5a6f55c373e7e28290701e1aec03117c7a330c7
```

命令文件记录完整 Python 路径、环境变量、每个测试文件、E 盘 basetemp、JUnit 输出和合并命令。

## 14. Active Index

```text
Index Version:
idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e

Pointer SHA-256:
0e77cc066d3dfc47875e7991e22d41828bc5ad531cb2cbb134e6096c6e6772ce

Manifest SHA-256:
18d1aedb6bdd1fe6e6093e5879e0f205808482010ff8d48b8a9215b55287fc02
```

Stage F 未修改索引、Pointer、Manifest 或 Milvus Collection。

## 15. 主要新增文件

```text
backend/apps/enterprise_document/schemas/release_publication.py
backend/apps/enterprise_document/services/release_publication.py
backend/tests/test_stage_f_release_publication.py
backend/tests/test_stage_f_mainline_release.py
scripts/run_stage_f_release_acceptance.py
scripts/finalize_stage_f_acceptance_v1.py
scripts/stage_f_focused_commands_v1.ps1
scripts/stage_f_regression_commands_v1.ps1
docs/EVIDENCE_GATE_STAGE_F_IMMUTABLE_RELEASE_V1.md
```

## 16. 阶段边界

Stage F V1 已完成：

```text
Stage E Credential Revalidation
Immutable Release Document
Immutable Eligibility Proof
Immutable Release Manifest
Exact Replay Idempotency
Same-version Conflict Rejection
Cross-run Replay Rejection
Version-bump Publication
Post-write Verification
Rejected-path Publication Suppression
Checkpoint/Application Release Projection
```

本阶段不包含：

```text
Human Review Web UI
外部对象存储适配器
数字签名/PKI 公证
Release Revocation Workflow
```

当前系统已能证明：

> 一个正式发布版本不仅通过了 Stage E 的正文、Claim、身份和 Evidence 血缘校验，而且发布内容、发布资格证明和 Release Manifest 都被写入不可变内容存储；相同版本不能被另一份内容或另一条 Run 覆盖，发布后任何存储篡改都会在回读校验中被发现。
