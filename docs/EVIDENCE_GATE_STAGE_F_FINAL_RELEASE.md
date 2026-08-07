# Stage F.1 Atomic Commit & True Idempotency — Final Freeze

## 1. Scope and conclusion

Stage F.1 closes the Stage A–F evidence and publication mainline. The implementation is limited to:

1. atomic publication visibility;
2. true idempotency across time and service instances;
3. same-version concurrency serialization;
4. crash/retry publication-state determination;
5. post-publication tamper detection.

No object storage, PKI, revocation, background cleanup, database transaction, distributed consensus, or additional Stage F/G architecture was introduced.

**Final decision: Stage A–F may be frozen.** Subsequent work is limited to code audit, redundancy removal, architecture diagrams, and project documentation.

## 2. Implementation footprint

Modified production files:

- `backend/apps/enterprise_document/schemas/release_publication.py`
- `backend/apps/enterprise_document/services/agent_rag_v1/artifact_store.py`
- `backend/apps/enterprise_document/services/release_publication.py`
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py`

New test files:

- `backend/tests/test_stage_f_atomic_idempotent_release.py`
- `backend/tests/test_stage_f_release_concurrency.py`

Complexity evidence, measured against the pre-F.1 working-tree snapshot:

- production net added lines: **347**;
- new test lines: **378**;
- new production files: **0**;
- new production service layers: **0**;
- new formal publication call sites: **0**.

## 3. Stable release request fingerprint

All new releases bind the same stable business request through:

```text
release_request_sha256
```

Included fields:

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

The release policy is:

```text
atomic_manifest_commit_v1
```

Explicitly excluded fields:

```text
published_at
verified_at
current system time
retry_count
worker_id
process_id
temporary path
log time
checkpoint time
```

The fingerprint is calculated before acquiring a first-commit timestamp. Consequently, the same business request produces the same request hash regardless of retry time, worker, service object, or process restart.

## 4. Version identity and references

Version identity remains:

```text
task_id + document_id + document_version
```

`run_id` does not participate in the version address, but it is included in `release_request_sha256`; therefore a different run attempting the same version is a stable conflict.

Reference rules:

```text
Document Blob:
release-blob://<idempotency_key>/document/<document_sha256>

Eligibility Blob:
release-blob://<idempotency_key>/eligibility/<proof_sha256>

Canonical Manifest:
release://<idempotency_key>/manifest
```

Runtime example:

```text
release_id:
release_b243bdfebe03f2f68445b38f

release_request_sha256:
875abb142cd7b14bd8ce795a6d5ebbc1e907c10d3fec608a344fa26a4c357ecf

canonical_manifest_ref:
release://b243bdfebe03f2f68445b38f0e4825c2a84e907233b1f2c336190bbd246d66cb/manifest
```

## 5. Atomic publication model

The Canonical Manifest is the only commit marker.

```text
Document/Proof Blob exists + Manifest absent
=> NOT_COMMITTED
=> no formal release
=> safe retry

Manifest exists + full verification passes
=> committed release
```

No rollback of orphan blobs is required. Orphan blobs are immutable content only and do not reserve the version identity.

The unique commit point is the atomic `FileArtifactStore.put_json()` of the Canonical Manifest.

## 6. Cross-process lock

Implementation:

```text
InterProcessFileLock
```

Lock path:

```text
<release_store_root>/.release-locks/<idempotency_key>.lock
```

Lock granularity:

```text
task_id + document_id + document_version
```

The single lock covers the complete publication critical section:

```text
read Canonical Manifest
verify existing release
compare release_request_sha256
generate first commit timestamp
build Eligibility Proof
write Document Blob
write Eligibility Blob
build and preverify Manifest
commit Canonical Manifest
postcommit readback verification
```

Two independent `ReleasePublicationService` instances and two independent `FileArtifactStore` instances sharing one root are therefore serialized for the same version, while different versions remain independently publishable.

## 7. True idempotency and recovery

### 7.1 Default-time replay

Two calls without `published_at` returned:

- identical `release_id`;
- identical Manifest;
- identical `published_at`;
- identical Document and Eligibility references;
- second call with `idempotent_replay=true`.

### 7.2 Different service/store instance

A new service and a new store object using the same root returned the existing Manifest with `idempotent_replay=true`.

### 7.3 Process/restart semantics

The four recovery decisions are implemented directly at the start of `publish()`:

```text
Manifest absent
=> NOT_COMMITTED

Manifest valid + request hash matches
=> COMMITTED_MATCH
=> return existing release

Manifest valid + request hash differs
=> COMMITTED_CONFLICT
=> ReleaseConflictError

Manifest present but invalid or referenced Blob invalid/missing
=> COMMITTED_CORRUPTED
=> ReleaseVerificationError
```

No separate recovery service or state machine was introduced.

## 8. Fault-injection evidence

### Document Blob write failure

```text
failure observed: true
Canonical Manifest visible after failure: false
retry succeeded: true
```

### Eligibility Blob write failure

```text
failure observed: true
orphan Document Blob allowed
Canonical Manifest visible after failure: false
retry succeeded: true
```

### Manifest precommit failure

```text
failure observed: true
Document/Proof orphan content allowed
Canonical Manifest visible after failure: false
retry succeeded: true
```

The successful retry retained one orphan Eligibility Blob from the first attempt, producing four JSON artifacts in that isolated probe root. This is intentional and does not make the version published before the retry.

### Manifest committed, response lost

```text
first call observed response-loss exception: true
Manifest and Blobs already complete: true
next call recovered existing release: true
idempotent_replay: true
artifact count: 3
```

## 9. Concurrency evidence

### Same request, same version

```text
both calls succeeded: true
same Manifest: true
same Manifest ArtifactReference: true
same published_at: true
idempotent replay count: 1
canonical release artifact count: 3
final verification: true
```

### Different requests, same version

```text
success count: 1
ReleaseConflictError count: 1
mixed release: false
winner retry: idempotent success
loser retry: stable ReleaseConflictError
artifact count: 3
version poisoned: false
```

Document, Eligibility Proof, and Manifest all carried the winner's same `release_request_sha256`.

## 10. Verification and tamper detection

`verify()` now checks:

- Manifest envelope and stored ArtifactReference;
- Manifest self hash;
- Document envelope and payload hash;
- Eligibility envelope and proof hash;
- Manifest/Proof/Document `release_request_sha256` binding;
- task, run, document, draft, and version identities;
- content-addressed Blob references;
- Canonical Manifest reference;
- recomputed draft hash;
- section artifact hash set;
- writer gate binding hash set;
- claim audit hash set;
- stable release request fingerprint.

Actual tests detected tampering of:

- Manifest envelope;
- Manifest payload;
- Document Blob;
- Eligibility Blob;
- `release_request_sha256`.

Every case raised `ReleaseVerificationError`; no committed release was overwritten or automatically repaired.

## 11. Mainline behavior

The only formal publication entry remains:

```text
SchemeGenerationStageService.persist_deliverable()
```

Changes:

- `payload.started_at` is no longer used as `published_at`;
- first commit time is generated inside `ReleasePublicationService` under the version lock;
- `DeliverableFinalResultSchema` is created only after verified publication returns;
- idempotent recovery creates the normal Final Result from the existing release;
- publication technical failures propagate and do not become business rejection;
- `persist_rejected()` does not call the publication service.

## 12. Runtime Hard Failures

Runtime acceptance produced **27 Hard Failure keys**, all `false`, including:

- default-time exact replay;
- different-service replay;
- all three precommit fault points and retries;
- postcommit response loss recovery;
- same-content and different-content concurrency;
- mixed release and version poisoning;
- orphan Blob publication visibility;
- Manifest/Document/Eligibility tamper detection;
- rejected-path suppression;
- Active Index immutability.

Runtime evidence:

```text
E:\rag-agent-runtime\stage_f_release_runtime_acceptance_final.json
SHA-256: e58b43c3ffb34a7e6f4da872c32cb8e4c306b5b3ea3f91e98c8ae81b76063c84
```

## 13. Test and quality evidence

Focused JUnit:

```text
E:\rag-agent-runtime\stage_f_focused_junit_final.xml
95 tests, 0 failures, 0 errors
SHA-256: 1e0acf836d32d271b6046ef0c22690f52f6ff76bc9cf2fe5f27d0f0366da3b75
```

Regression JUnit:

```text
E:\rag-agent-runtime\stage_f_regression_junit_final.xml
558 tests, 0 failures, 0 errors, 0 skipped
SHA-256: 960e4bb019bc0c5ffe23438acc0fee426e1debf007f8e91ea84fe52beafd9d86
```

Combined:

```text
653 tests
0 failures
0 errors
```

Quality checks:

```text
python -m compileall -q backend scripts
=> passed

git diff --check
=> passed
```

The line-ending warnings emitted by Git are advisory only; the command returned zero and reported no whitespace errors.

## 14. Acceptance reports

Repository report:

```text
data/processed/indexes/stage_f_release_acceptance_report_final.json
SHA-256: 08b60186e6de479983dfe3300cf8e4bae296dce6b246415fd15ba030980dec15
```

Runtime copy:

```text
E:\rag-agent-runtime\stage_f_release_acceptance_report_final.json
SHA-256: 08b60186e6de479983dfe3300cf8e4bae296dce6b246415fd15ba030980dec15
```

## 15. Active Index invariant

The final state exactly preserves:

```text
Index Version:
idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e

Pointer SHA-256:
0e77cc066d3dfc47875e7991e22d41828bc5ad531cb2cbb134e6096c6e6772ce

Manifest SHA-256:
18d1aedb6bdd1fe6e6093e5879e0f205808482010ff8d48b8a9215b55287fc02
```

No index rebuild, pointer modification, Stage D semantic change, or Stage E semantic change was performed.

## 16. Residual boundaries

The remaining limitations are deliberate scope boundaries, not Stage F.1 defects:

1. orphan immutable Blobs are retained; no GC is implemented;
2. lock correctness depends on the underlying local/shared filesystem honoring OS advisory locks and atomic replacement;
3. external object storage, PKI, revocation, distributed consensus, and multi-region publication remain out of scope.

## 17. Freeze statement

The system now proves that a Stage E-approved document is bound to one stable release request and one version identity; same-version publication is serialized across service/store instances; Document and Eligibility are immutable content-addressed Blobs; Canonical Manifest is the sole commit marker; precommit failures do not expose a release; postcommit response loss is recovered by true idempotent replay; concurrent conflicts cannot create mixed artifacts or poison the version; and committed tampering is detected.

**Stage A–F is formally frozen.**
