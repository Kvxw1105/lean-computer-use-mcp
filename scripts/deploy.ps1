<#
.SYNOPSIS
    Register lean-computer-use-mcp as an MCP server in a local Codex-family client.

.DESCRIPTION
    Detects prerequisites (uv, project, upstream engine), then appends/updates the
    [mcp_servers.lean-computer-use] entry in the target client's config.
    Supported clients:
      - codex    : official Codex CLI/Desktop -> ~/.codex/config.toml
      - codexpp  : StepFun/Codex++ variant     -> C:\AppData\.codex\config.toml (NOTE the non-standard home!)
      - zcode    : Zhipu/CodeGeeX-style client -> ~/.zcode/cli/config.json (mcp.servers)

    The upstream defaults to "auto" (cua-driver/Hermes when present, otherwise
    open-computer-use). A full client restart is required after registration.

.PARAMETER ProjectDir
    Absolute path of the lean-computer-use-mcp checkout. Defaults to the current directory.

.PARAMETER Upstream
    auto | cua-driver | open-computer-use (default: auto).

.PARAMETER Client
    codex | codexpp | zcode (default: codexpp on this machine is common, but keep explicit).

.PARAMETER TimeoutMs
    Optional client timeout for MCP startup (zcode only; default 60000).

.PARAMETER SkipSync
    Skip `uv sync --all-extras` (already installed).

.PARAMETER DryRun
    Print what would be written without modifying any file.

.EXAMPLE
    .\scripts\deploy.ps1 -ProjectDir D:\repo\lean-computer-use-mcp -Client codex -Upstream auto -SkipSync
#>
[CmdletBinding()]
param(
    [string]$ProjectDir = (Get-Location).Path,
    [ValidateSet("auto", "cua-driver", "open-computer-use")]
    [string]$Upstream = "auto",
    [ValidateSet("codex", "codexpp", "zcode")]
    [string]$Client = "codexpp",
    [int]$TimeoutMs = 60000,
    [switch]$SkipSync,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Step { param([string]$Msg) Write-Host "==> $Msg" -ForegroundColor Cyan }
function Write-Ok { param([string]$Msg) Write-Host "    OK: $Msg" -ForegroundColor Green }
function Write-WarnMsg { param([string]$Msg) Write-Host "    WARN: $Msg" -ForegroundColor Yellow }

# ---------------------------------------------------------------- prerequisites
Write-Step "Checking prerequisites"

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    $candidates = @(
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\uv.exe"
    )
    $uv = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $uv) { throw "uv.exe not found. Install it first: https://docs.astral.sh/uv/ (winget install astral-sh.uv)" }
Write-Ok "uv: $uv"

if (-not (Test-Path (Join-Path $ProjectDir "pyproject.toml"))) {
    throw "Project not found at $ProjectDir (no pyproject.toml). Pass -ProjectDir."
}
Write-Ok "project: $ProjectDir"

if (-not $SkipSync) {
    Write-Step "Installing dependencies (uv sync --all-extras)"
    & $uv sync --all-extras --project $ProjectDir
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
}

# ---------------------------------------------------------------- upstream check
Write-Step "Checking upstream engine (mode: $Upstream)"
$cuaExe = $null
$openCuExe = $null

$resolvedCua = (Get-Command cua-driver -ErrorAction SilentlyContinue).Source
if (-not $resolvedCua) {
    $cuaCandidate = Join-Path $env:LOCALAPPDATA "Programs\Cua\cua-driver\bin\cua-driver.exe"
    if (Test-Path $cuaCandidate) { $resolvedCua = $cuaCandidate }
}
if ($resolvedCua) { $cuaExe = $resolvedCua; Write-Ok "cua-driver (Hermes): $resolvedCua" }
else { Write-WarnMsg "cua-driver (Hermes) not found - auto mode will fall back to open-computer-use" }

$openCuExe = (Get-Command open-computer-use -ErrorAction SilentlyContinue).Source
if ($openCuExe) { Write-Ok "open-computer-use: $openCuExe" }
else { Write-WarnMsg "open-computer-use not found on PATH" }

if ($Upstream -eq "cua-driver" -and -not $cuaExe) { throw "Upstream=cua-driver but no cua-driver binary found." }
if ($Upstream -eq "open-computer-use" -and -not $openCuExe) { throw "Upstream=open-computer-use but binary not found." }

# ---------------------------------------------------------------- build command
$uvFs = $uv.Replace("\", "/")
$projectFs = $ProjectDir.Replace("\", "/")
$argsList = @("run", "--project", $projectFs, "lean-computer-use", "serve", "--upstream", $Upstream)
if ($Upstream -eq "auto") { $argsList = @("run", "--project", $projectFs, "lean-computer-use", "serve") }

# ---------------------------------------------------------------- write config
switch ($Client) {
    "codex"   { $cfgPath = Join-Path $env:USERPROFILE ".codex\config.toml" }
    "codexpp" { $cfgPath = "C:\AppData\.codex\config.toml" }
    "zcode"   { $cfgPath = Join-Path $env:USERPROFILE ".zcode\cli\config.json" }
}

if (-not (Test-Path $cfgPath)) { throw "Client config not found: $cfgPath (is $Client installed on this machine?)" }
Write-Step "Registering into $Client config: $cfgPath"

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
if (-not $DryRun) {
    Copy-Item $cfgPath "$cfgPath.bak-$stamp" -Force
    Write-Ok "backup: $cfgPath.bak-$stamp"
}

if ($Client -eq "zcode") {
    $json = Get-Content $cfgPath -Raw | ConvertFrom-Json
    if (-not $json.mcp) { $json | Add-Member -NotePropertyName mcp -NotePropertyValue ([pscustomobject]@{}) }
    if (-not $json.mcp.servers) { $json.mcp | Add-Member -NotePropertyName servers -NotePropertyValue ([pscustomobject]@{}) }
    $entry = [pscustomobject]@{
        command   = $uvFs
        args      = $argsList
        timeoutMs = $TimeoutMs
    }
    $json.mcp.servers | Add-Member -NotePropertyName "lean-computer-use" -NotePropertyValue $entry -Force
    if ($DryRun) {
        Write-Ok "DRY-RUN: would write mcp.servers.lean-computer-use = $($entry | ConvertTo-Json -Compress -Depth 4)"
    } else {
        $json | ConvertTo-Json -Depth 10 | Set-Content $cfgPath -Encoding UTF8
        Write-Ok "registered lean-computer-use (zcode)"
    }
}
else {
    # TOML: escape backslashes, use a literal UTF-8 section.
    $escapedCmd = $uvFs.Replace("\", "\\")
    $escapedArgs = @()
    foreach ($a in $argsList) { $escapedArgs += '"' + $a.Replace("\", "\\") + '"' }
    $section = "`n[mcp_servers.lean-computer-use]`ncommand = `"$escapedCmd`"`nargs = [$($escapedArgs -join ', ')]`n"

    $existing = Get-Content $cfgPath -Raw
    $pattern = '(?s)\[mcp_servers\.lean-computer-use\].*?(?=\r?\n\[|\z)'
    if ($existing -match $pattern) {
        $updated = [regex]::Replace($existing, $pattern, $section.TrimEnd())
        if ($DryRun) { Write-Ok "DRY-RUN: would REPLACE existing [mcp_servers.lean-computer-use] in $cfgPath" }
        else { [System.IO.File]::WriteAllText($cfgPath, $updated, [System.Text.UTF8Encoding]::new($false)); Write-Ok "updated existing entry" }
    }
    else {
        if ($DryRun) { Write-Ok "DRY-RUN: would APPEND [mcp_servers.lean-computer-use] to $cfgPath" }
        else { [System.IO.File]::AppendAllText($cfgPath, $section, [System.Text.UTF8Encoding]::new($false)); Write-Ok "appended new entry" }
    }
}

# ---------------------------------------------------------------- final notes
Write-Step "Done"
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Yellow
Write-Host "  1. FULLY QUIT the client (taskbar right-click -> Quit, or kill the process). Closing the window is NOT enough."
Write-Host "  2. Relaunch, open Settings -> MCP servers (or the plugin/MCP panel), and check 'lean-computer-use' is listed."
Write-Host "  3. Sanity check: lean-computer-use doctor --upstream $Upstream"