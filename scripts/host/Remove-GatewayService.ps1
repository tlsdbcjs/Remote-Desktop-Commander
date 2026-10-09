param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Remove-RacpService([string]$ServiceName) {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $service) { return }
    if ($service.Status -ne 'Stopped') {
        & sc.exe stop $ServiceName | Out-Null
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1062) {
            throw ('Failed to request {0} service stop.' -f $ServiceName)
        }
        $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    }
    & sc.exe delete $ServiceName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw ('Failed to remove {0} service.' -f $ServiceName) }
}

Remove-RacpService 'RACP Gateway Updater'
Remove-RacpService 'RACP Gateway'
Remove-Item -LiteralPath 'Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\EventLog\Application\RACP Gateway Updater' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath 'Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\EventLog\Application\RACP Gateway' -Recurse -Force -ErrorAction SilentlyContinue
Write-Output 'RACP Gateway service removed. Operational data was preserved.'
