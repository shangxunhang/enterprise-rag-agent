# EXPERIMENT ONLY — DO NOT MERGE INTO PRODUCTION ROUTING.
$ErrorActionPreference = "Stop"

$python = "D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe"
$projectRoot = "D:\MyCode\rag-agent"
$fixture = Join-Path $projectRoot "backend\tests\fixtures\e2e\gov_cloud_framework_project_input.json"

if (-not $env:DEEPSEEK_API_KEY) {
    throw "DEEPSEEK_API_KEY is not set in this PowerShell session."
}

$env:DEEPSEEK_PROVIDER_MODEL_NAME = "deepseek-v4-flash"
$env:DEEPSEEK_THINKING_ENABLED = "false"
$env:GATE_ENFORCEMENT_MODE = "shadow"

if (-not (Test-Path $python)) { throw "Python interpreter not found: $python" }
if (-not (Test-Path $fixture)) { throw "Fixture not found: $fixture" }

$projectInput = Get-Content -Raw -Encoding UTF8 $fixture | ConvertFrom-Json
$query = $projectInput.user_query
if ([string]::IsNullOrWhiteSpace($query)) { throw "fixture.user_query is empty." }

$runId = "gov_cloud_deepseek_all_roles_ab_" + (Get-Date -Format "yyyyMMdd_HHmmss")
$runtimeRoot = "E:\rag-agent-runtime"
$root = Join-Path $runtimeRoot $runId
New-Item -ItemType Directory -Force -Path $root | Out-Null

$args = @(
    "scripts\experiments\run_all_deepseek_e2e.py",
    "--user-input", $query,
    "--project-input-file", "backend\tests\fixtures\e2e\gov_cloud_framework_project_input.json",
    "--authorized-kb-id", "historical_case_kb",
    "--authorized-kb-id", "policy_standard_kb",
    "--authorized-kb-id", "project_fact_kb",
    "--authorized-kb-id", "technical_reference_kb",
    "--authorized-kb-id", "template_style_kb",
    "--run-id", $runId,
    "--output-root", $root,
    "--strict",
    "--e2e-report-path", (Join-Path $root "e2e_report.json")
)

$p = Start-Process `
    -FilePath $python `
    -ArgumentList $args `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput (Join-Path $root "console.stdout.log") `
    -RedirectStandardError (Join-Path $root "console.stderr.log") `
    -PassThru

Write-Output "RUN_ID=$runId"
Write-Output "PID=$($p.Id)"
Write-Output "ROOT=$root"
Write-Output "EXPERIMENT=all_active_llm_roles_deepseek"
Write-Output "GATE_ENFORCEMENT_MODE=shadow"
