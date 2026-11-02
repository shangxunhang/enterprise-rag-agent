$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "backend"
$env:TEMP = "E:\rag-agent-tmp"
$env:TMP = "E:\rag-agent-tmp"
$python = "D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe"
New-Item -ItemType Directory -Force -Path "E:\rag-agent-tmp" | Out-Null
New-Item -ItemType Directory -Force -Path "E:\rag-agent-runtime" | Out-Null
& $python -m pytest -q `
  --basetemp=E:\rag-agent-tmp\pytest-stage-f-focused-final `
  --junitxml=E:\rag-agent-runtime\stage_f_focused_junit_final.xml `
  backend/tests/test_stage_f_release_publication.py `
  backend/tests/test_stage_f_mainline_release.py `
  backend/tests/test_stage_f_atomic_idempotent_release.py `
  backend/tests/test_stage_f_release_concurrency.py `
  backend/tests/test_stage_e_writer_governance.py `
  backend/tests/test_stage_e_document_hard_gate.py `
  backend/tests/test_stage_e_claim_audit_integrity.py `
  backend/tests/test_stage_e_claim_classifier_fail_closed.py `
  backend/tests/test_stage_e_cross_section_reuse.py `
  backend/tests/test_stage_e_mandatory_identity_lineage.py `
  backend/tests/test_stage_e_cross_document_run_reuse.py `
  backend/tests/test_stage_e_document_gate_recomputation.py `
  backend/tests/test_stage_e_acceptance_evidence.py `
  backend/tests/test_scheme_writer_decomposition.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
