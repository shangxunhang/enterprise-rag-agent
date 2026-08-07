$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "backend"
$env:TEMP = "E:\rag-agent-tmp"
$env:TMP = "E:\rag-agent-tmp"
$python = "D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe"
$root = "E:\rag-agent-runtime\stage_f_regression_parts_final"
New-Item -ItemType Directory -Force -Path $root | Out-Null
New-Item -ItemType Directory -Force -Path "E:\rag-agent-tmp" | Out-Null

& $python -m pytest -q --basetemp=E:\rag-agent-tmp\pytest-stage-f-core-final --junitxml="$root\regression_core.xml" backend/tests/test_stage_a_evidence_gate_contracts.py backend/tests/test_stage_b_document_catalog.py backend/tests/test_stage_c_profile_aware_indexing.py backend/tests/test_stage_c_profile_lineage.py backend/tests/test_stage_c_structured_chunkers.py backend/tests/test_stage_d_candidate_profile_consistency.py backend/tests/test_stage_d_evidence_governance_v2.py backend/tests/test_stage_d_fact_status_policy.py backend/tests/test_stage_d_final_gate_prompt_visibility.py backend/tests/test_stage_d_negative_acceptance.py backend/tests/test_stage_d_online_bundle_integration.py backend/tests/test_stage_d_retrieval_candidate_and_scope.py backend/tests/test_stage_d_structured_evidence_projection.py backend/tests/test_stage_e_writer_governance.py backend/tests/test_stage_e_document_hard_gate.py backend/tests/test_stage_e_claim_audit_integrity.py backend/tests/test_stage_e_claim_classifier_fail_closed.py backend/tests/test_stage_e_cross_section_reuse.py backend/tests/test_stage_e_mandatory_identity_lineage.py backend/tests/test_stage_e_cross_document_run_reuse.py backend/tests/test_stage_e_document_gate_recomputation.py backend/tests/test_stage_e_acceptance_evidence.py backend/tests/test_stage_f_release_publication.py backend/tests/test_stage_f_mainline_release.py backend/tests/test_stage_f_atomic_idempotent_release.py backend/tests/test_stage_f_release_concurrency.py backend/tests/test_scheme_writer_decomposition.py backend/tests/test_rag_config_driven_context_packer.py backend/tests/test_rag_config_driven_quality_plugins.py backend/tests/test_rag_config_driven_query_transformers.py backend/tests/test_rag_config_driven_repair_strategy.py backend/tests/test_rag_config_driven_reranker.py backend/tests/test_rag_config_driven_retrieval_stack.py backend/tests/test_rag_corrective_retrieval_loop.py backend/tests/test_rag_evidence_pipeline.py backend/tests/test_rag_model_gateway_runtime.py backend/tests/test_rag_offline_chunker_index_version.py backend/tests/test_rag_plugin_architecture_closure.py backend/tests/test_rag_strategy_eval_runner.py backend/tests/test_section_aware_evidence_retrieval.py backend/tests/test_section_execution_contracts.py backend/tests/test_section_generation_subservices.py backend/tests/test_document_assembler.py backend/tests/test_p0_real_rag_runtime.py backend/tests/test_p1_high_risk_fact_and_citation_integrity.py backend/tests/test_p1_trust_semantic_payload_closure.py backend/tests/test_production_retrieval_baseline_recovery.py backend/tests/test_self_rag_judge_baseline_recovery.py backend/tests/test_step_12_evidence_contract.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$agentFiles = @(
  "test_agent_rag_v1_agents.py",
  "test_agent_rag_v1_contracts.py",
  "test_agent_rag_v1_document_closure.py",
  "test_agent_rag_v1_infrastructure.py",
  "test_agent_rag_v1_recovery.py",
  "test_agent_rag_v1_recovery_e2e.py",
  "test_agent_rag_v1_runtime_contracts.py",
  "test_agent_rag_v1_standalone_e2e.py",
  "test_agent_rag_v1_workflow.py",
  "test_agent_rag_v1_writer_claims.py"
)
foreach ($file in $agentFiles) {
  $name = [System.IO.Path]::GetFileNameWithoutExtension($file)
  & $python -m pytest -q --basetemp="E:\rag-agent-tmp\pytest-stage-f-$name-final" --junitxml="$root\$name.xml" "backend/tests/$file"
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$mainlineFiles = @(
  "test_phase2_top_level_business_graph.py",
  "test_phase3_section_execution_subgraph.py",
  "test_phase4_section_agents_mainline.py",
  "test_phase6_paragraph_claim_mainline.py",
  "test_phase7_document_closure_mainline.py",
  "test_phase8_recovery_budget_retirement.py"
)
foreach ($file in $mainlineFiles) {
  $name = [System.IO.Path]::GetFileNameWithoutExtension($file)
  & $python -m pytest -q --basetemp="E:\rag-agent-tmp\pytest-stage-f-$name-final" --junitxml="$root\$name.xml" "backend/tests/$file"
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$parts = Get-ChildItem "$root\*.xml" | Sort-Object FullName | ForEach-Object { $_.FullName }
& $python scripts/merge_junit_reports.py --output E:\rag-agent-runtime\stage_f_regression_junit_final.xml $parts
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
