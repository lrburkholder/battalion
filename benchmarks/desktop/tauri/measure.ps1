param(
    [string]$Executable = (Join-Path $PSScriptRoot "src-tauri\target\release\battalion-tauri-spike.exe"),
    [int]$Samples = 5
)

$ErrorActionPreference = "Stop"
$releaseRoot = Split-Path -Parent $Executable
$measurementRoot = Join-Path $releaseRoot "benchmark-measurements"
New-Item -ItemType Directory -Force -Path $measurementRoot | Out-Null

function Get-ProcessTree([int]$RootProcessId) {
    $all = @(Get-CimInstance Win32_Process)
    $ids = [System.Collections.Generic.List[int]]::new()
    $ids.Add($RootProcessId)
    for ($index = 0; $index -lt $ids.Count; $index++) {
        foreach ($child in $all | Where-Object ParentProcessId -eq $ids[$index]) {
            if (!$ids.Contains([int]$child.ProcessId)) {
                $ids.Add([int]$child.ProcessId)
            }
        }
    }
    return @($all | Where-Object { $ids.Contains([int]$_.ProcessId) })
}

function Get-TreeMetrics($Tree) {
    $running = @($Tree | ForEach-Object {
        Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
    })
    return [pscustomobject]@{
        process_count = $running.Count
        working_set_bytes = ($running | Measure-Object WorkingSet64 -Sum).Sum
        private_bytes = ($running | Measure-Object PrivateMemorySize64 -Sum).Sum
        cpu_seconds = [math]::Round((($running | Measure-Object CPU -Sum).Sum), 4)
        process_names = @($Tree.Name)
    }
}

function Stop-ProcessTree($Tree) {
    $ids = @($Tree.ProcessId | Sort-Object -Descending)
    if ($ids.Count -gt 0) {
        Stop-Process -Id $ids -Force -ErrorAction SilentlyContinue
    }
}

function Wait-MainWindow($Process, $Stopwatch) {
    while ($Stopwatch.Elapsed.TotalSeconds -le 10 -and !$Process.HasExited) {
        $Process.Refresh()
        if ($Process.MainWindowHandle -ne 0) {
            return $true
        }
        Start-Sleep -Milliseconds 5
    }
    return $false
}

$coldStarts = @()
for ($sample = 1; $sample -le $Samples; $sample++) {
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    $process = Start-Process -FilePath $Executable -WindowStyle Hidden -PassThru
    if (!(Wait-MainWindow $process $stopwatch)) {
        throw "Cold start $sample did not expose its native window."
    }
    $stopwatch.Stop()
    $tree = @(Get-ProcessTree $process.Id)
    $coldStarts += [pscustomobject]@{
        sample = $sample
        window_ready_ms = [math]::Round($stopwatch.Elapsed.TotalMilliseconds, 2)
        process_count = $tree.Count
    }
    Stop-ProcessTree $tree
    Start-Sleep -Milliseconds 300
}

$idleSamples = @()
$idleProcessNames = @()
for ($sample = 1; $sample -le $Samples; $sample++) {
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    $process = Start-Process -FilePath $Executable -WindowStyle Hidden -PassThru
    if (!(Wait-MainWindow $process $stopwatch)) {
        throw "Idle sample $sample did not expose its native window."
    }
    Start-Sleep -Milliseconds 1500
    $tree = @(Get-ProcessTree $process.Id)
    $metrics = Get-TreeMetrics $tree
    $idleSamples += [pscustomobject]@{
        sample = $sample
        process_count = $metrics.process_count
        working_set_bytes = $metrics.working_set_bytes
        private_bytes = $metrics.private_bytes
        cpu_seconds = $metrics.cpu_seconds
    }
    if ($sample -eq 1) {
        $idleProcessNames = $metrics.process_names
    }
    Stop-ProcessTree $tree
    Start-Sleep -Milliseconds 300
}

$scenarioSamples = @()
for ($sample = 1; $sample -le $Samples; $sample++) {
    $marker = Join-Path $measurementRoot "scenario-$sample.ready"
    Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    $process = Start-Process -FilePath $Executable `
        -ArgumentList "--benchmark-ready-file=$marker" -WindowStyle Hidden -PassThru
    while (!(Test-Path -LiteralPath $marker)) {
        if ($stopwatch.Elapsed.TotalSeconds -gt 10 -or $process.HasExited) {
            throw "Scenario sample $sample failed before readiness."
        }
        Start-Sleep -Milliseconds 5
    }
    $stopwatch.Stop()
    $tree = @(Get-ProcessTree $process.Id)
    $metrics = Get-TreeMetrics $tree
    $scenarioSamples += [pscustomobject]@{
        sample = $sample
        complete_ms = [math]::Round($stopwatch.Elapsed.TotalMilliseconds, 2)
        process_count = $metrics.process_count
        working_set_bytes = $metrics.working_set_bytes
        private_bytes = $metrics.private_bytes
        cpu_seconds = $metrics.cpu_seconds
    }
    if (!$process.WaitForExit(10000)) {
        Stop-ProcessTree $tree
        throw "Scenario sample $sample did not exit."
    }
    Start-Sleep -Milliseconds 300
}

$permissionMarker = Join-Path $measurementRoot "permission-probe.txt"
Remove-Item -LiteralPath $permissionMarker -Force -ErrorAction SilentlyContinue
$permissionProcess = Start-Process -FilePath $Executable -ArgumentList @(
    "--benchmark-ready-file=$permissionMarker",
    "--benchmark-permission-probe"
) -WindowStyle Hidden -PassThru -Wait
if ($permissionProcess.ExitCode -ne 0 -or !(Test-Path -LiteralPath $permissionMarker)) {
    throw "Permission probe failed."
}

$crashProcess = Start-Process -FilePath $Executable -WindowStyle Hidden -PassThru
$crashStopwatch = [Diagnostics.Stopwatch]::StartNew()
if (!(Wait-MainWindow $crashProcess $crashStopwatch)) {
    throw "Client crash injection did not expose its native window."
}
Start-Sleep -Milliseconds 300
$crashTree = @(Get-ProcessTree $crashProcess.Id)
Stop-Process -Id $crashProcess.Id -Force
Start-Sleep -Milliseconds 1000
$orphanIds = @($crashTree.ProcessId | Where-Object {
    Get-Process -Id $_ -ErrorAction SilentlyContinue
})
if ($orphanIds.Count -gt 0) {
    Stop-Process -Id $orphanIds -Force -ErrorAction SilentlyContinue
}

$restartMarker = Join-Path $measurementRoot "client-restart.ready"
Remove-Item -LiteralPath $restartMarker -Force -ErrorAction SilentlyContinue
$restartStopwatch = [Diagnostics.Stopwatch]::StartNew()
$restartProcess = Start-Process -FilePath $Executable `
    -ArgumentList "--benchmark-ready-file=$restartMarker" -WindowStyle Hidden -PassThru
while (!(Test-Path -LiteralPath $restartMarker)) {
    if ($restartStopwatch.Elapsed.TotalSeconds -gt 10 -or $restartProcess.HasExited) {
        throw "Client restart did not recover the scenario."
    }
    Start-Sleep -Milliseconds 5
}
$restartStopwatch.Stop()
$restartProcess.WaitForExit(10000) | Out-Null

$artifacts = @(
    Get-Item -LiteralPath $Executable
    Get-Item -LiteralPath (Join-Path $releaseRoot "bundle\msi\Battalion Tauri Benchmark_0.1.0_x64_en-US.msi")
    Get-Item -LiteralPath (Join-Path $releaseRoot "bundle\nsis\Battalion Tauri Benchmark_0.1.0_x64-setup.exe")
)

[pscustomobject]@{
    cold_start = $coldStarts
    cold_start_median_ms = ($coldStarts.window_ready_ms | Sort-Object)[$Samples / 2]
    idle = $idleSamples
    idle_working_set_median = ($idleSamples.working_set_bytes | Sort-Object)[$Samples / 2]
    idle_private_median = ($idleSamples.private_bytes | Sort-Object)[$Samples / 2]
    idle_cpu_median = ($idleSamples.cpu_seconds | Sort-Object)[$Samples / 2]
    idle_process_names = $idleProcessNames
    scenario = $scenarioSamples
    scenario_median_ms = ($scenarioSamples.complete_ms | Sort-Object)[$Samples / 2]
    active_working_set_median = ($scenarioSamples.working_set_bytes | Sort-Object)[$Samples / 2]
    active_private_median = ($scenarioSamples.private_bytes | Sort-Object)[$Samples / 2]
    active_cpu_median = ($scenarioSamples.cpu_seconds | Sort-Object)[$Samples / 2]
    permission_probe = @((Get-Content -LiteralPath $permissionMarker))
    crash_tree_process_count = $crashTree.Count
    orphan_processes_after_one_second = $orphanIds.Count
    restart_scenario_ms = [math]::Round($restartStopwatch.Elapsed.TotalMilliseconds, 2)
    artifacts = @($artifacts | ForEach-Object {
        [pscustomobject]@{
            name = $_.Name
            bytes = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
    })
} | ConvertTo-Json -Depth 6
