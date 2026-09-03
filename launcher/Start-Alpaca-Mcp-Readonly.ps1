param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentPath = Join-Path $projectRoot '.env.local'
$requiredNames = @('ALPACA_API_KEY', 'ALPACA_SECRET_KEY')
$toolsets = 'assets,stock-data,options-data,news'
$fastMcpVersion = 'fastmcp==3.1.0'

if (-not (Test-Path -LiteralPath $environmentPath -PathType Leaf)) {
    throw 'The ignored local environment file is unavailable.'
}

$values = @{}
foreach ($line in Get-Content -LiteralPath $environmentPath) {
    if ($line -notmatch '^\s*(?:export\s+)?(?<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?<value>.*)$') {
        continue
    }
    $name = $Matches.name
    if ($name -notin $requiredNames) {
        continue
    }
    if ($values.ContainsKey($name)) {
        throw "Duplicate required setting: $name"
    }
    $value = $Matches.value.Trim()
    if ($value.Length -ge 2) {
        $first = $value[0]
        $last = $value[$value.Length - 1]
        if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
            $value = $value.Substring(1, $value.Length - 2)
        }
    }
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Required local setting is empty: $name"
    }
    $values[$name] = $value
}

foreach ($name in $requiredNames) {
    if (-not $values.ContainsKey($name)) {
        throw "Required local setting is missing: $name"
    }
}

$env:ALPACA_API_KEY = $values.ALPACA_API_KEY
$env:ALPACA_SECRET_KEY = $values.ALPACA_SECRET_KEY
$env:ALPACA_PAPER_TRADE = 'true'
$env:ALPACA_TOOLSETS = $toolsets

$uvx = Get-Command uvx -ErrorAction Stop
& $uvx.Source '--with' $fastMcpVersion 'alpaca-mcp-server==2.3.1'
exit $LASTEXITCODE
