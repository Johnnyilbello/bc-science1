param(
    [switch]$SkipOCR
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$InstallRoot = Join-Path $env:LOCALAPPDATA "BCScience"
$AppDir = Join-Path $InstallRoot "app"
$VenvDir = Join-Path $InstallRoot "venv"
$BinDir = Join-Path $InstallRoot "bin"
$Archive = Join-Path $env:TEMP "bc-science-main.zip"
$ExtractDir = Join-Path $env:TEMP "bc-science-main"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Reload-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Resolve-Python {
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        try {
            & $python.Source -c "import sys; print(sys.executable)" *> $null
            if ($LASTEXITCODE -eq 0) { return $python.Source }
        } catch {}
    }

    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $resolved = & $py.Source -3 -c "import sys; print(sys.executable)"
            if ($LASTEXITCODE -eq 0 -and $resolved) { return $resolved.Trim() }
        } catch {}
    }
    return $null
}

function Resolve-Tesseract {
    $cmd = Get-Command tesseract.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        (Join-Path $env:ProgramFiles "Tesseract-OCR\\tesseract.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Tesseract-OCR\\tesseract.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    return $null
}

function Resolve-Ollama {
    $cmd = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
        (Join-Path $env:ProgramFiles "Ollama\ollama.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Test-OllamaApi {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

Write-Host "BC Science - installazione locale" -ForegroundColor Green
Write-Host "Scienze Motorie eCampus 2026/2027"

New-Item -ItemType Directory -Force -Path $InstallRoot, $BinDir | Out-Null

Write-Step "Verifica Python"
$PythonExe = Resolve-Python
if (-not $PythonExe) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python non è installato e winget non è disponibile. Installa Python 3.12+ e rilancia il comando."
    }

    Write-Host "Python non trovato: installo Python 3.12..."
    & $winget.Source install --id Python.Python.3.12 --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Installazione Python fallita." }
    Reload-Path
    $PythonExe = Resolve-Python
    if (-not $PythonExe) { throw "Python installato ma non ancora rilevabile. Riapri PowerShell e rilancia l'installer." }
}
Write-Host "Python: $PythonExe" -ForegroundColor DarkGray

Write-Step "Verifica Ollama"
$OllamaExe = Resolve-Ollama
if (-not $OllamaExe) {
    Write-Host "Ollama non trovato: uso l'installer ufficiale..."
    Invoke-RestMethod "https://ollama.com/install.ps1" | Invoke-Expression
    Reload-Path
    $OllamaExe = Resolve-Ollama
    if (-not $OllamaExe) { throw "Ollama non è stato rilevato dopo l'installazione." }
}

if (-not (Test-OllamaApi)) {
    Write-Host "Avvio il servizio Ollama..."
    Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WindowStyle Hidden | Out-Null
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-OllamaApi) {
            $ready = $true
            break
        }
    }
    if (-not $ready) { throw "Ollama installato ma il servizio locale non risponde." }
}
Write-Host "Ollama: OK" -ForegroundColor DarkGray

if (-not $SkipOCR) {
    Write-Step "Verifica OCR"
    $TesseractExe = Resolve-Tesseract
    if (-not $TesseractExe) {
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if ($winget) {
            Write-Host "Tesseract non trovato: provo a installarlo per i PDF scannerizzati..."
            try {
                & $winget.Source install --id UB-Mannheim.TesseractOCR --exact --silent --accept-package-agreements --accept-source-agreements
                Reload-Path
                $TesseractExe = Resolve-Tesseract
            } catch {
                Write-Warning "OCR opzionale non installato. I PDF con testo normale funzioneranno comunque."
            }
        } else {
            Write-Warning "winget assente: salto Tesseract. I PDF con testo normale funzioneranno comunque."
        }
    }

    if ($TesseractExe) {
        $TesseractDir = Split-Path -Parent $TesseractExe
        $tessdata = Join-Path $TesseractDir "tessdata"

        $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $PathItems = @($UserPath -split ";" | Where-Object { $_ })
        if ($PathItems -notcontains $TesseractDir) {
            [Environment]::SetEnvironmentVariable(
                "Path",
                (($PathItems + $TesseractDir) -join ";"),
                "User"
            )
        }
        if (($env:Path -split ";") -notcontains $TesseractDir) {
            $env:Path = "$env:Path;$TesseractDir"
        }

        if (Test-Path $tessdata) {
            $env:TESSDATA_PREFIX = $tessdata
            [Environment]::SetEnvironmentVariable("TESSDATA_PREFIX", $tessdata, "User")

            $ItalianData = Join-Path $tessdata "ita.traineddata"
            if (-not (Test-Path $ItalianData)) {
                Write-Host "Modello OCR italiano non trovato: installo ita.traineddata..."
                try {
                    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/ita.traineddata" -OutFile $ItalianData
                } catch {
                    Write-Warning "Non riesco a installare ita.traineddata: OCR limitato a eng."
                }
            }

            if (Test-Path $ItalianData) {
                $env:BC_SCIENCE_OCR_LANG = "ita+eng"
                [Environment]::SetEnvironmentVariable(
                    "BC_SCIENCE_OCR_LANG",
                    "ita+eng",
                    "User"
                )
                Write-Host "OCR: ita+eng" -ForegroundColor DarkGray
            } else {
                $env:BC_SCIENCE_OCR_LANG = "eng"
                [Environment]::SetEnvironmentVariable(
                    "BC_SCIENCE_OCR_LANG",
                    "eng",
                    "User"
                )
            }
        }
    }
}

Write-Step "Scarico BC Science"
if (Test-Path $Archive) { Remove-Item -Force $Archive }
if (Test-Path $ExtractDir) { Remove-Item -Recurse -Force $ExtractDir }
Invoke-WebRequest -Uri "https://github.com/Johnnyilbello/bc-science1/archive/refs/heads/main.zip" -OutFile $Archive
Expand-Archive -Path $Archive -DestinationPath $env:TEMP -Force

$ExtractedRoot = Join-Path $env:TEMP "bc-science1-main"
if (-not (Test-Path $ExtractedRoot)) { throw "Archivio GitHub estratto in un percorso inatteso." }

if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
Move-Item -Path $ExtractedRoot -Destination $AppDir
Remove-Item -Force $Archive -ErrorAction SilentlyContinue

Write-Step "Configuro ambiente Python"
if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
    & $PythonExe -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "Creazione ambiente virtuale fallita." }
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install --upgrade $AppDir
if ($LASTEXITCODE -ne 0) { throw "Installazione dipendenze BC Science fallita." }

Write-Step "Rilevo hardware e preparo i modelli locali"
& $VenvPython -m bc_science.bootstrap
if ($LASTEXITCODE -ne 0) { throw "Configurazione modelli fallita." }

Write-Step "Creo comando bc-science"
$Launcher = Join-Path $BinDir "bc-science.cmd"
$LauncherContent = "@echo off" + [Environment]::NewLine +
    '"' + $VenvPython + '" -m bc_science.cli %*' + [Environment]::NewLine
Set-Content -Path $Launcher -Value $LauncherContent -Encoding ASCII

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$PathItems = @($UserPath -split ";" | Where-Object { $_ })
if ($PathItems -notcontains $BinDir) {
    $NewUserPath = (($PathItems + $BinDir) -join ";")
    [Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
}
$env:Path = "$env:Path;$BinDir"

Write-Step "Diagnostica finale"
& $Launcher doctor

Write-Host ""
Write-Host "BC Science è pronto." -ForegroundColor Green
Write-Host "Esempi:"
Write-Host '  bc-science summarize "C:\Studio\Fisiologia.zip" --single'
Write-Host '  bc-science ingest "C:\Studio\Fisiologia.zip"'
Write-Host '  bc-science ask "Spiegami il ruolo del calcio nella contrazione muscolare"'
Write-Host '  bc-science clarity'
Write-Host '  bc-science finalize'
Write-Host ""
Write-Host "Dopo una nuova apertura di PowerShell il comando bc-science sarà disponibile globalmente."
