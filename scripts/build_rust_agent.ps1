[CmdletBinding()]
param([string]$BuildId = (Get-Date -Format 'yyyyMMdd-HHmmss'))
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows -or [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
    throw 'Build the Agent on native Windows x64 using PowerShell 7.'
}
if ($BuildId -notmatch '^[A-Za-z0-9_-]{1,80}$') { throw 'Invalid build identifier.' }
$Repo = Split-Path $PSScriptRoot -Parent
Push-Location $Repo
try {
    $CargoText = Get-Content Cargo.toml -Raw
    $Version = [regex]::Match($CargoText, '(?m)^version = "([0-9]+\.[0-9]+\.[0-9]+)"\r?$').Groups[1].Value
    if (-not $Version) { throw 'Missing workspace version.' }
    $Batch = Join-Path $Repo "dist/rust-agent/$Version/win-x64/$BuildId"
    if (Test-Path $Batch) { throw "Build output already exists: $Batch" }
    New-Item -ItemType Directory -Path $Batch | Out-Null
    $Payload = Join-Path $Batch "racp-agent-$Version-win-x64"
    New-Item -ItemType Directory -Path $Payload | Out-Null
    # Production target only: no test/example compilation and no acceptance execution.
    & cargo build -p racp-agent --release --locked --target x86_64-pc-windows-msvc
    if ($LASTEXITCODE -ne 0) { throw 'Rust Agent compilation failed.' }
    Copy-Item target/x86_64-pc-windows-msvc/release/racp-agent.exe $Payload
    $Downloader = Join-Path $Repo '.tools/rust-agent-playwright'
    & npm install --prefix $Downloader --ignore-scripts --no-package-lock --no-audit --no-fund 'playwright@1.63.0'
    if ($LASTEXITCODE -ne 0) { throw 'Pinned Chromium downloader installation failed.' }
    $BrowserMetadata = Get-Content (Join-Path $Downloader 'node_modules/playwright-core/browsers.json') -Raw | ConvertFrom-Json
    $Chromium = @($BrowserMetadata.browsers | Where-Object { $_.name -eq 'chromium' })
    if ($Chromium.Count -ne 1 -or $Chromium[0].revision -ne '1243') { throw 'Unexpected pinned Chromium revision.' }
    $PreviousBrowserPath = $env:PLAYWRIGHT_BROWSERS_PATH
    try {
        $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Repo '.tools/rust-agent-browsers'
        & node (Join-Path $Downloader 'node_modules/playwright/cli.js') install chromium
        if ($LASTEXITCODE -ne 0) { throw 'Pinned native Chromium download failed.' }
        $ChromeSource = Join-Path $env:PLAYWRIGHT_BROWSERS_PATH 'chromium-1243/chrome-win64'
        if (-not (Test-Path (Join-Path $ChromeSource 'chrome.exe'))) { throw 'Missing native Chromium executable.' }
        Copy-Item $ChromeSource (Join-Path $Payload 'chromium') -Recurse
    } finally { $env:PLAYWRIGHT_BROWSERS_PATH = $PreviousBrowserPath }
    Copy-Item docs/guides/rust-agent-guide.md (Join-Path $Payload 'README.md')
    if (Test-Path LICENSE) { Copy-Item LICENSE $Payload }
    $Commit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Cannot determine source revision.' }
    $Files = @(Get-ChildItem $Payload -File -Recurse | Sort-Object FullName | ForEach-Object {
        @{ path = [IO.Path]::GetRelativePath($Payload, $_.FullName).Replace('\', '/'); size = $_.Length; sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    })
    $Manifest = @{
        version = $Version; platform = 'win'; architecture = 'x64'; source_commit = $Commit
        build_id = $BuildId; rust_toolchain = '1.90.0'; chromium_revision = '1243'; files = $Files
        runtime = 'rust'; python_required = $false; node_required = $false
        tests = 'deferred_by_user'; signed = $false; feature_parity = 'in_progress'
    }
    $Manifest | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $Payload 'build-manifest.json') -Encoding utf8NoBOM
    $Zip = Join-Path $Batch "racp-agent-$Version-win-x64.zip"
    Compress-Archive -Path $Payload -DestinationPath $Zip -CompressionLevel Optimal
    $Checksum = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Checksum  $([IO.Path]::GetFileName($Zip))" | Set-Content (Join-Path $Batch 'SHA256SUMS') -Encoding utf8NoBOM
    Write-Output "Agent package: $Zip"
    Write-Output "SHA-256: $Checksum"
} finally { Pop-Location }
