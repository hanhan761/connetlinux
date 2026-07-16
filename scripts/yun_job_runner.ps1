param([Parameter(Mandatory = $true)][string]$JobDir)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $JobDir -PathType Container)) { exit 65 }
Set-Location -LiteralPath $JobDir
New-Item -ItemType Directory -Force -Path (Join-Path $JobDir 'results') | Out-Null

function Write-File([string]$Name, [string]$Value) {
  [IO.File]::WriteAllText((Join-Path $JobDir $Name), $Value + [Environment]::NewLine)
}

Write-File 'started_at' ([DateTime]::UtcNow.ToString('o'))
Write-File 'status' 'running'
$env:YUN_JOB_ID = Split-Path -Leaf $JobDir
$env:YUN_JOB_DIR = $JobDir
$env:YUN_RESULTS_DIR = Join-Path $JobDir 'results'

try {
  $process = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Join-Path $JobDir 'job.ps1')) -RedirectStandardOutput (Join-Path $JobDir 'stdout.log') -RedirectStandardError (Join-Path $JobDir 'stderr.log') -PassThru
  Write-File 'child_pid' $process.Id
  $process.WaitForExit()
  $code = $process.ExitCode
  Write-File 'exit_code' $code
  Write-File 'status' ($(if (Test-Path (Join-Path $JobDir 'cancel_requested')) { 'cancelled' } elseif ($code -eq 0) { 'succeeded' } else { 'failed' }))
} catch {
  $_ | Out-String | Add-Content -LiteralPath (Join-Path $JobDir 'stderr.log')
  Write-File 'exit_code' '1'
  Write-File 'status' 'failed'
} finally {
  Write-File 'finished_at' ([DateTime]::UtcNow.ToString('o'))
}
