param(
    [string]$StateDir,
    [string]$Name = 'Windows PC',
    [switch]$NoOpen
)
. (Join-Path $PSScriptRoot 'Invoke-RacpHost.ps1')
$forward = @('--name', $Name)
if ($StateDir) {
    $flag = if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'scripts/gateway_host.py')) {
        '--state-dir'
    } else { '--lab-dir' }
    $forward += @($flag, $StateDir)
}
if ($NoOpen) { $forward += '--no-open' }
Invoke-RacpHost -Action connection -PythonArguments $forward
