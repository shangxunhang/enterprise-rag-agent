$ErrorActionPreference = "Stop"

$python = "D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe"
$projectRoot = "D:\MyCode\rag-agent"
$fixture = Join-Path $projectRoot "backend\tests\fixtures\e2e\gov_cloud_framework_project_input.json"

if (-not $env:DEEPSEEK_API_KEY) {
    throw "DEEPSEEK_API_KEY is not set in this PowerShell session."
}

$env:SECTION_GENERATION_MODEL_PROFILE = "deepseek"
$env:DEEPSEEK_PROVIDER_MODEL_NAME = "deepseek-v4-flash"
$env:DEEPSEEK_THINKING_ENABLED = "false"

$payload = Get-Content -Raw -Encoding UTF8 $fixture | ConvertFrom-Json
$query = [string]$payload.user_query
if ([string]::IsNullOrWhiteSpace($query)) {
    throw "Fixture user_query is empty."
}

$runId = "gov_cloud_deepseek_writer_ab_" + (Get-Date -Format "yyyyMMdd_HHmmss")
$root = Join-Path "E:\rag-agent-runtime" $runId
New-Item -ItemType Directory -Force -Path $root | Out-Null

$args = @(
    "scripts\run_demo.py",
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
