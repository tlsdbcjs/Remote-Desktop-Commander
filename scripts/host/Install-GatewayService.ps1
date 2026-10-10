param(
    [string]$DistributionRoot,
    [string]$StateRoot,
    [string]$ConfigFile,
    [string]$Version
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $DistributionRoot) {
    $DistributionRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
}
if (-not $StateRoot) {
    if (-not $env:ProgramData) { throw 'ProgramData is unavailable.' }
    $StateRoot = Join-Path $env:ProgramData 'RACP\Gateway'
}
if (-not $ConfigFile) {
    $ConfigFile = Join-Path $StateRoot 'config\gateway.json'
}
if (-not $Version) {
    $Version = Split-Path -Leaf $DistributionRoot
}

$serviceHost = Join-Path $DistributionRoot 'runtime\pythonservice.exe'
if (-not (Test-Path -LiteralPath $serviceHost -PathType Leaf)) {
    throw 'Gateway pywin32 service host was not found.'
}
if (-not (Test-Path -LiteralPath $ConfigFile -PathType Leaf)) {
    throw 'Gateway service configuration was not found.'
}

$serviceName = 'RACP Gateway'
$updaterName = 'RACP Gateway Updater'
$existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existing) { throw 'RACP Gateway service already exists.' }
$existingUpdater = Get-Service -Name $updaterName -ErrorAction SilentlyContinue
if ($existingUpdater) { throw 'RACP Gateway Updater service already exists.' }

$binaryPath = ('"{0}"' -f $serviceHost)
$updaterBinaryPath = ('"{0}"' -f $serviceHost)
New-Service -Name $serviceName -BinaryPathName $binaryPath -StartupType Automatic | Out-Null
try {
    $gatewayClass = 'HKLM:\SYSTEM\CurrentControlSet\Services\RACP Gateway\PythonClass'
    $gatewayParameters = 'HKLM:\SYSTEM\CurrentControlSet\Services\RACP Gateway\Parameters'
    New-Item -Path $gatewayClass -Force | Out-Null
    Set-Item -Path $gatewayClass -Value 'racp_gateway.windows_service.GatewayService'
    New-Item -Path $gatewayParameters -Force | Out-Null
    New-ItemProperty -Path $gatewayParameters -Name 'ConfigFile' -PropertyType String -Value $ConfigFile -Force | Out-Null
    & sc.exe config $serviceName obj= 'NT SERVICE\RACP Gateway'
    if ($LASTEXITCODE -ne 0) { throw 'Failed to configure the Gateway virtual service account.' }
    & sc.exe sidtype $serviceName unrestricted
    if ($LASTEXITCODE -ne 0) { throw 'Failed to enable the Gateway service SID.' }
    New-Service -Name $updaterName -BinaryPathName $updaterBinaryPath -StartupType Manual | Out-Null
    $updaterClass = 'HKLM:\SYSTEM\CurrentControlSet\Services\RACP Gateway Updater\PythonClass'
    $updaterParameters = 'HKLM:\SYSTEM\CurrentControlSet\Services\RACP Gateway Updater\Parameters'
    New-Item -Path $updaterClass -Force | Out-Null
    Set-Item -Path $updaterClass -Value 'racp_gateway.updater_service.GatewayUpdaterService'
    New-Item -Path $updaterParameters -Force | Out-Null
    New-ItemProperty -Path $updaterParameters -Name 'ConfigFile' -PropertyType String -Value $ConfigFile -Force | Out-Null
    New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
    & icacls.exe $StateRoot /inheritance:r /grant:r 'BUILTIN\Administrators:(OI)(CI)F' 'NT AUTHORITY\SYSTEM:(OI)(CI)F' 'NT SERVICE\RACP Gateway:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to apply Gateway state ACL.' }
    $gatewaySid = (New-Object System.Security.Principal.NTAccount('NT SERVICE\RACP Gateway')).Translate([System.Security.Principal.SecurityIdentifier]).Value
    $updaterSddl = ('D:(A;;CCLCSWRPWPDTLOCRSDRCWDWO;;;SY)(A;;CCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;LCRP;;;{0})' -f $gatewaySid)
    & sc.exe sdset $updaterName $updaterSddl | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to restrict the Gateway updater service ACL.' }
    $updates = Join-Path $StateRoot 'updates'
    New-Item -ItemType Directory -Force -Path $updates | Out-Null
    $activeRelease = @{
        release_id = ('installed-{0}' -f $Version)
        version = $Version
        path = [System.IO.Path]::GetFullPath($DistributionRoot)
        installed_at = [DateTimeOffset]::UtcNow.ToString('o')
    } | ConvertTo-Json
    [System.IO.File]::WriteAllText(
        (Join-Path $updates 'active-release.json'),
        $activeRelease + [Environment]::NewLine,
        (New-Object System.Text.UTF8Encoding($false))
    )
    & sc.exe start $serviceName
    if ($LASTEXITCODE -ne 0) { throw 'Failed to start RACP Gateway service.' }
    $service = Get-Service -Name $serviceName
    $service.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
} catch {
    & sc.exe delete $updaterName | Out-Null
    & sc.exe delete $serviceName | Out-Null
    throw
}
