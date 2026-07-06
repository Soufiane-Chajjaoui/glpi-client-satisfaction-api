# PowerShell script — run daily GLPI Light pipeline
# Schedule via Task Scheduler: 2:00 AM daily

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
$null = New-Item -ItemType Directory -Path $LogDir -Force
$LogFile = Join-Path $LogDir "pipeline_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

"=== GLPI Light Pipeline $(Get-Date) ===" | Out-File $LogFile

& python "$ProjectRoot\pipeline.py" --all 2>&1 | Out-File $LogFile -Append

"=== Done $(Get-Date) ===" | Out-File $LogFile -Append
