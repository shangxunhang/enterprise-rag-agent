param(
    [string]$ProjectRoot = "D:\MyCode\rag-agent"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    throw "Project directory does not exist: $ProjectRoot"
}

$TeamRoot = Join-Path $ProjectRoot "docs\team"

$SubDirectories = @(
    "REQUIREMENTS",
    "ANALYSIS",
    "DECISIONS",
    "TASKS",
    "HANDOFFS",
    "REVIEWS",
    "MEETINGS",
    "RELEASES",
    "FEEDBACK"
)

$Files = [ordered]@{
    "PROJECT.md"      = "# Project`r`n"
    "ORGANIZATION.md" = "# Organization`r`n"
    "WORKFLOW.md"     = "# Engineering Workflow`r`n"
    "POLICIES.md"     = "# Team Policies`r`n"
    "ARCHITECTURE.md" = "# Current Architecture`r`n"
    "CONTRACTS.md"    = "# Shared Contracts`r`n"
    "STATUS.md"       = "# Project Status`r`n"
}

Write-Host "Project root: $ProjectRoot"
Write-Host "Team docs root: $TeamRoot"
Write-Host ""

if (-not (Test-Path -LiteralPath $TeamRoot)) {
    New-Item -ItemType Directory -Path $TeamRoot -Force | Out-Null
    Write-Host "[CREATED DIR] $TeamRoot"
}
else {
    Write-Host "[EXISTS DIR]  $TeamRoot"
}

foreach ($SubDirectory in $SubDirectories) {
    $DirectoryPath = Join-Path $TeamRoot $SubDirectory

    if (-not (Test-Path -LiteralPath $DirectoryPath)) {
        New-Item -ItemType Directory -Path $DirectoryPath -Force | Out-Null
        Write-Host "[CREATED DIR] $DirectoryPath"
    }
    else {
        Write-Host "[EXISTS DIR]  $DirectoryPath"
    }

    # Git does not track empty directories.
    $GitKeepPath = Join-Path $DirectoryPath ".gitkeep"

    if (-not (Test-Path -LiteralPath $GitKeepPath)) {
        New-Item -ItemType File -Path $GitKeepPath -Force | Out-Null
        Write-Host "[CREATED FILE] $GitKeepPath"
    }
}

foreach ($FileName in $Files.Keys) {
    $FilePath = Join-Path $TeamRoot $FileName

    if (-not (Test-Path -LiteralPath $FilePath)) {
        Set-Content `
            -LiteralPath $FilePath `
            -Value $Files[$FileName] `
            -Encoding UTF8

        Write-Host "[CREATED FILE] $FilePath"
    }
    else {
        Write-Host "[EXISTS FILE]  $FilePath"
    }
}

Write-Host ""
Write-Host "docs/team structure created successfully."