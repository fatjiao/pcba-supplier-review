#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string[]]$CadPath,
    [string]$ProjectRoot,
    [string]$StagingRoot = (Join-Path ([IO.Path]::GetTempPath()) 'pcba-ad-review')
)

$ErrorActionPreference = 'Stop'
$sources = @($CadPath | ForEach-Object { Get-Item -LiteralPath $_ })
$projects = @($sources | Where-Object Extension -IEQ '.PrjPcb')
if ($projects.Count -gt 1) {
    throw 'Stage one Altium project per run.'
}

if (-not $ProjectRoot -and $projects.Count -eq 1) {
    $ProjectRoot = $projects[0].DirectoryName
}
$projectPrefix = ''
if ($ProjectRoot) {
    $root = Get-Item -LiteralPath $ProjectRoot
    if ($root -isnot [IO.DirectoryInfo]) {
        throw "Expected a project directory: $ProjectRoot"
    }
    $projectPrefix = $root.FullName.TrimEnd([char]92) + '\'
}

$relativePaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$files = foreach ($source in $sources) {
    if ($source -isnot [IO.FileInfo]) {
        throw "Expected a design file: $($source.FullName)"
    }
    $relativePath = $source.Name
    if ($projectPrefix) {
        if (-not $source.FullName.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "File is outside the supplied project root: $($source.FullName)"
        }
        $relativePath = $source.FullName.Substring($projectPrefix.Length)
    }
    if (-not $relativePaths.Add($relativePath)) {
        throw "Two supplied files map to the same staging path: $relativePath"
    }
    [pscustomobject]@{ source = $source; relative_path = $relativePath }
}

$runId = [Guid]::NewGuid().ToString('N')
$stagePath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($StagingRoot)
$jobRoot = Join-Path $stagePath $runId
$inputDir = Join-Path $jobRoot 'input'
$outputDir = Join-Path $jobRoot 'evidence'
$null = [IO.Directory]::CreateDirectory($inputDir)
$null = [IO.Directory]::CreateDirectory($outputDir)

$copiedFiles = foreach ($file in $files) {
    $destination = Join-Path $inputDir $file.relative_path
    $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination))
    $file.source.Refresh()
    $originalLength = $file.source.Length
    $originalWriteTime = $file.source.LastWriteTimeUtc
    $readStream = [IO.File]::Open(
        $file.source.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
    )
    try {
        $writeStream = [IO.File]::Open($destination, [IO.FileMode]::CreateNew)
        try { $readStream.CopyTo($writeStream) } finally { $writeStream.Dispose() }
    } finally {
        $readStream.Dispose()
    }
    $file.source.Refresh()
    if ($file.source.Length -ne $originalLength -or
        $file.source.LastWriteTimeUtc -ne $originalWriteTime -or
        (Get-Item -LiteralPath $destination).Length -ne $originalLength) {
        throw "Source changed during staging: $($file.source.FullName)"
    }
    [pscustomobject]@{
        source_path = $file.source.FullName
        staged_path = $file.relative_path
        staged_sha256 = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

$probeFile = Join-Path $jobRoot 'rdp-probe.txt'
[IO.File]::WriteAllText($probeFile, $runId)
$rdpRoot = $null
if ($jobRoot -match '^([A-Za-z]):\\(.*)$') {
    $rdpRoot = '\\tsclient\' + $Matches[1].ToUpperInvariant() + '\' + $Matches[2]
}
$manifestPath = Join-Path $jobRoot 'manifest.json'
$manifest = [pscustomobject]@{
    run_id = $runId
    state = 'staged'
    local_job_root = $jobRoot
    local_cad_input = $inputDir
    local_returned_evidence = $outputDir
    local_probe = $probeFile
    rdp_candidate_job_root = $rdpRoot
    rdp_read_write_verified = $false
    files = @($copiedFiles)
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
$manifest | Add-Member -NotePropertyName manifest_path -NotePropertyValue $manifestPath -PassThru
