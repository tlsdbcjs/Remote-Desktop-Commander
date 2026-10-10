param([string]$StateDir)
. (Join-Path $PSScriptRoot 'Invoke-RacpHost.ps1')
$forward = @()
if ($StateDir) { $forward += @('--state-dir', $StateDir) }
Invoke-RacpHost -Action status -PythonArguments $forward
