param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseDirectory,
    [Parameter(Mandatory = $true)]
    [string]$ReceiptFile
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$release = [System.IO.Path]::GetFullPath($ReleaseDirectory)
$receipt = [System.IO.Path]::GetFullPath($ReceiptFile)
if (-not (Test-Path -LiteralPath $release -PathType Container)) {
    throw 'Verified staged release directory was not found.'
}
$python = Join-Path $release 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Verified staged release does not contain the Gateway runtime.'
}
$receiptParent = Split-Path -Parent $receipt
if (-not (Test-Path -LiteralPath $receiptParent -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $receiptParent | Out-Null
}
@{
    state = 'delegated'
    release_directory = $release
    updated_at = [DateTimeOffset]::UtcNow.ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath $receipt -Encoding UTF8
Write-Output 'Gateway update handoff recorded. Service switching is performed by the updater state machine.'
