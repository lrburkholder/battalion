<#
Run the on-demand CI workflow from PowerShell.

This wrapper selects Git Bash deliberately so ``run_ci.sh`` receives Bash
semantics and the authenticated Windows GitHub CLI remains on PATH.
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$gitBash = Join-Path $env:ProgramFiles 'Git\bin\bash.exe'
if (-not (Test-Path -LiteralPath $gitBash)) {
    throw "Git Bash is required to run CI. Install Git for Windows or run scripts/run_ci.sh from Bash."
}

& $gitBash (Join-Path $PSScriptRoot 'run_ci.sh') @Arguments
exit $LASTEXITCODE
