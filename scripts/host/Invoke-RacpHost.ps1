# Shared launcher for repository and self-contained Gateway distributions.
Set-StrictMode -Version Latest

function Invoke-RacpHost {
    param(
        [Parameter(Mandatory = $true)][string]$Action,
        [string[]]$PythonArguments = @()
    )
    $ErrorActionPreference = 'Stop'
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
    try {
        $packaged = Test-Path -LiteralPath (Join-Path $PSScriptRoot 'scripts/gateway_host.py')
        if ($packaged) {
            $root = $PSScriptRoot
        } else {
            $root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
            if (-not (Test-Path -LiteralPath (Join-Path $root 'pyproject.toml'))) {
                throw 'Gateway repository or distribution root was not found.'
            }
        }
        $python = $null
        foreach ($relative in @(
            'runtime/python.exe',
            '.tools/desktop-build-venv/Scripts/python.exe',
            '.venv-acceptance/Scripts/python.exe',
            '.venv/Scripts/python.exe'
        )) {
            $candidate = Join-Path $root $relative
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $python = $candidate
                break
            }
        }
        if (-not $python) { throw 'Host Python runtime was not found.' }
        if ($packaged -or $Action -in @('start', 'status')) {
            $script = Join-Path $root 'scripts/gateway_host.py'
            $forward = @($Action) + $PythonArguments
        } elseif ($Action -eq 'connection') {
            $script = Join-Path $root 'scripts/create_connection_file.py'
            $forward = $PythonArguments
        } else {
            $script = Join-Path $root 'scripts/create_console_login.py'
            $forward = $PythonArguments
        }
        & $python -X utf8 -E -s -B $script @forward
        $result = $LASTEXITCODE
    } catch {
        Write-Error $_ -ErrorAction Continue
        $result = 1
    }
    exit $result
}
