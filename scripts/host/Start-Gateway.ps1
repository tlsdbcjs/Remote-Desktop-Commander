param(
    [string]$BindAddress,
    [ValidateRange(1, 65535)][int]$Port,
    [string]$StateDir,
    [string]$OAuthConfig
)
. (Join-Path $PSScriptRoot 'Invoke-RacpHost.ps1')
$forward = @()
if ($BindAddress) { $forward += @('--host', $BindAddress) }
if ($PSBoundParameters.ContainsKey('Port')) { $forward += @('--port', "$Port") }
if ($StateDir) { $forward += @('--state-dir', $StateDir) }
if ($OAuthConfig) { $forward += @('--oauth-config', $OAuthConfig) }
Invoke-RacpHost -Action start -PythonArguments $forward
