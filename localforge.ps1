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
Push-Location $ProjectRoot
try {
    & $Uv.Source run --project $ProjectRoot poe @Arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
