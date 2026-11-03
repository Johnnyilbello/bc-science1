param(
    [Parameter(Mandatory=$true)][string]$ExpectedCommit,
    [string]$Repository = 'C:\Users\LNV 83JG00B6IX\Documents\Codex\2026-10-29\voglio-introdurre-cinque-miglioramenti-1-comprensione-3\work\bc-science1',
    [string]$Course = 'C:\Users\LNV 83JG00B6IX\Desktop\SCIENZE MOTORIE 2026\FONDAMENTI DI BIOLOGIA  E CHIMICA',
    [string]$Complete = 'C:\Users\LNV 83JG00B6IX\Documents\BCSciencePreviews\repair-v53-20261102-174434\riassunto-unico.md',
    [string]$Essential = 'C:\Users\LNV 83JG00B6IX\Documents\BCSciencePreviews\compact-fixed-20261102-204446\essenziale-CANDIDATA.md',
    [string]$V9Report = 'C:\Users\LNV 83JG00B6IX\Documents\BCSciencePreviews\spark-v9-20261103-163549\v9-report.json'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Check-Exit([string]$Message) {
    if ($LASTEXITCODE -ne 0) { throw $Message }
}
$branch = 'feat/spark-v10-structural-compression-20261103'
$expected = @(
    '72c1dfc066489d5936ebccbef7aaa06df3a3ca5d171cc329279e8bd71c18c40b',
    '37ba531b68a8fcd5b091985edc84f60a66bdc6582abb0a7df1916937c4bf5a9f',
    '55c36139b772be172384448acfb066a1cccd6140bd15a0f97b69e8124e2c4914'
)
$inputs = @($Complete, $Essential, $V9Report)
for ($i = 0; $i -lt 3; $i++) {
    if ((Get-FileHash -LiteralPath $inputs[$i] -Algorithm SHA256).Hash -ne $expected[$i]) {
        throw "Input diverso dagli allegati verificati: $($inputs[$i])"
    }
}
git -C $Repository fetch origin $branch
Check-Exit 'Fetch V10 fallito'
$actual = (git -C $Repository rev-parse FETCH_HEAD).Trim()
Check-Exit 'Impossibile leggere il commit'
if ($actual -ne $ExpectedCommit) { throw 'Commit remoto diverso dalla V10 verificata' }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$taskHome = [Environment]::GetFolderPath('MyDocuments')
$worktree = Join-Path $taskHome "BCScienceWorktrees\spark-v10-$stamp"
$output = Join-Path $taskHome "BCSciencePreviews\spark-v10-$stamp"
git -C $Repository worktree add --detach $worktree $ExpectedCommit
Check-Exit 'Creazione copia isolata fallita'
$oldPath = $env:PYTHONPATH
$oldEncoding = $env:PYTHONIOENCODING
try {
    $env:PYTHONPATH = Join-Path $worktree 'src'
    $env:PYTHONIOENCODING = 'utf-8'
    foreach ($version in @('3.10', '3.12')) {
        $venv = Join-Path $worktree "work\python-$version"
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            uv venv --python $version $venv
            Check-Exit "Preparazione Python $version fallita"
            $python = Join-Path $venv 'Scripts\python.exe'
            uv pip install --python $python -e "$worktree[dev]"
        } else {
            py "-$version" -m venv $venv
            Check-Exit "Serve Python $version installato oppure uv"
            $python = Join-Path $venv 'Scripts\python.exe'
            & $python -m pip install -e "$worktree[dev]"
        }
        Check-Exit "Installazione dipendenze Python $version fallita"
        & $python -m ruff check --exclude work $worktree
        Check-Exit "Controlli codice Python $version falliti"
        & $python -m pytest -q --basetemp (Join-Path $worktree "work\pytest-$version") (Join-Path $worktree 'tests')
        Check-Exit "Test Python $version falliti"
        & $python -m bc_science.cli courses structural-compression-preview $Course `
            --complete $Complete --essential $Essential --v9-report $V9Report `
            --output-dir "$output-python-$version"
        Check-Exit "Pilot reale Python $version fallito"
        $sums = Get-Content -LiteralPath "$output-python-$version\SHA256SUMS.json" -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($entry in $sums.PSObject.Properties) {
            $artifact = Join-Path "$output-python-$version" $entry.Name
            if ((Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash -ne $entry.Value) {
                throw "Artefatto alterato: $artifact"
            }
        }
    }
    $left = Get-Content -LiteralPath "$output-python-3.10\v10-report.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    $right = Get-Content -LiteralPath "$output-python-3.12\v10-report.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($left.candidate_sha256 -ne $right.candidate_sha256) {
        throw 'Candidate diverse tra Python 3.10 e 3.12'
    }
    Write-Host "DOSSIER: $output-python-3.12\v10-dossier.md"
    Write-Host "DIFF: $output-python-3.12\v10-vs-essenziale.diff"
    Write-Host 'REVISIONE MANUALE OBBLIGATORIA PRIMA DEL MERGE. NESSUNA PUBBLICAZIONE.' -ForegroundColor Yellow
} finally {
    $env:PYTHONPATH = $oldPath
    $env:PYTHONIOENCODING = $oldEncoding
    for ($i = 0; $i -lt 3; $i++) {
        if ((Get-FileHash -LiteralPath $inputs[$i] -Algorithm SHA256).Hash -ne $expected[$i]) {
            throw "Integrita input compromessa: $($inputs[$i])"
        }
    }
}
