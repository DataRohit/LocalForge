[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$RepositoryRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendProject = Join-Path $RepositoryRoot "backend"
$ProjectRoot = if (Test-Path (Join-Path $BackendProject "pyproject.toml")) {
    $BackendProject
} else {
    $RepositoryRoot
}

$Uv = Get-Command uv -ErrorAction Stop
$env:PYTHONPATH = "$RepositoryRoot;$(Join-Path $RepositoryRoot 'src')"
Push-Location $RepositoryRoot
try {
    & $Uv.Source run --project $ProjectRoot poe -C $ProjectRoot @Arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
