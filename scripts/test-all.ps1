[CmdletBinding()]
param(
    [string]$RepoRoot = (Join-Path $PSScriptRoot ".."),
    [string]$CourseRoot = "",
    [switch]$Full
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$reportDir = Join-Path $repo ("reports\test-all\" + $stamp)
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
$script:Stages = New-Object System.Collections.Generic.List[object]

function Invoke-Stage {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$Arguments = @()
    )
    $slug = $Name -replace '[^A-Za-z0-9_-]', '-'
    $log = Join-Path $reportDir ($slug + ".log")
    Write-Host ("=== " + $Name + " ===") -ForegroundColor Cyan
    $exitCode = 0
    try {
        $global:LASTEXITCODE = 0
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $Executable @Arguments 2>&1 | Tee-Object -FilePath $log | Out-Host
            $exitCode = [int]$LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
    }
    catch {
        $message = $_ | Out-String
        Add-Content -LiteralPath $log -Value $message -Encoding UTF8
        Write-Host $message -ForegroundColor Red
        $exitCode = 1
    }
    $status = if ($exitCode -eq 0) { "PASS" } else { "FAIL" }
    $script:Stages.Add([pscustomobject]@{
        name = $Name
        status = $status
        exit_code = $exitCode
        log = $log
    })
    Write-Host ("[" + $status + "] " + $Name) -ForegroundColor $(if ($exitCode -eq 0) { "Green" } else { "Red" })
    return $exitCode
}

function Add-Skipped {
    param([string]$Name, [string]$Reason)
    $script:Stages.Add([pscustomobject]@{
        name = $Name
        status = "SKIP"
        reason = $Reason
    })
    Write-Host ("[SKIP] " + $Name + ": " + $Reason) -ForegroundColor Yellow
}

Push-Location $repo
try {
    $python = Join-Path $repo ".venv\Scripts\python.exe"
    $cli = Join-Path $repo ".venv\Scripts\bc-science.exe"

    if (-not (Test-Path -LiteralPath $python)) {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($launcher) {
            $null = Invoke-Stage "create-venv" $launcher.Source @("-3", "-m", "venv", (Join-Path $repo ".venv"))
        }
        else {
            $launcher = Get-Command python -ErrorAction SilentlyContinue
            if ($launcher) {
                $null = Invoke-Stage "create-venv" $launcher.Source @("-m", "venv", (Join-Path $repo ".venv"))
            }
            else {
                Add-Skipped "create-venv" "Python 3.10+ non trovato nel PATH"
            }
        }
    }

    if (Test-Path -LiteralPath $python) {
        $null = Invoke-Stage "install-dev" $python @("-m", "pip", "install", "--disable-pip-version-check", "-e", ".[dev]")
        $lintCode = Invoke-Stage "ruff" $python @("-m", "ruff", "check", ".")
        $testCode = Invoke-Stage "pytest" $python @("-m", "pytest", "-q")
        if (Test-Path -LiteralPath $cli) {
            $null = Invoke-Stage "verify-cli-help" $cli @("courses", "verify", "--help")
            $null = Invoke-Stage "agents-list" $cli @("agents", "list")
            $null = Invoke-Stage "agents-plan" $cli @("agents", "plan", "--task", "study", "--limit", "24")
            $null = Invoke-Stage "doctor" $cli @("doctor")
        }
        else {
            Add-Skipped "cli" "bc-science.exe assente: installazione incompleta"
        }

        if (-not $CourseRoot) {
            $configured = [Environment]::GetEnvironmentVariable("BC_SCIENCE_COURSES_ROOT")
            if ($configured -and (Test-Path -LiteralPath $configured -PathType Container)) {
                $CourseRoot = $configured
            }
            elseif (Test-Path -LiteralPath "C:\Studio\SCIENZE MOTORIE" -PathType Container) {
                $CourseRoot = "C:\Studio\SCIENZE MOTORIE"
            }
        }

        if (-not $CourseRoot) {
            $homeRoot = ""
            if ($env:BC_SCIENCE_HOME) {
                $homeRoot = $env:BC_SCIENCE_HOME
            }
            elseif ($env:LOCALAPPDATA) {
                $homeRoot = Join-Path $env:LOCALAPPDATA "BCScience"
            }
            if ($homeRoot) {
                $workspaces = Join-Path $homeRoot "workspaces\courses"
                if (Test-Path -LiteralPath $workspaces -PathType Container) {
                    $candidates = @()
                    foreach ($folder in (Get-ChildItem -LiteralPath $workspaces -Directory)) {
                        $manifestPath = Join-Path $folder.FullName "manifest.json"
                        if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
                            continue
                        }
                        try {
                            $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 |
                                ConvertFrom-Json
                            if ($manifest.source -and (Test-Path -LiteralPath $manifest.source -PathType Container)) {
                                $parent = Split-Path -Path $manifest.source -Parent
                                if (Test-Path -LiteralPath $parent -PathType Container) {
                                    $candidates += $parent
                                }
                            }
                        }
                        catch {
                            # Un manifest corrotto non deve bloccare i test del codice.
                        }
                    }
                    $groups = @($candidates | Group-Object | Sort-Object Count -Descending)
                    if (
                        $groups.Count -eq 1 -or
                        ($groups.Count -gt 1 -and $groups[0].Count -gt $groups[1].Count)
                    ) {
                        $CourseRoot = $groups[0].Name
                        Write-Host ("Cartella materie recuperata dai manifest: " + $CourseRoot)
                    }
                }
            }
        }

        if ($CourseRoot -and (Test-Path -LiteralPath $CourseRoot -PathType Container) -and (Test-Path -LiteralPath $cli)) {
            $CourseRoot = (Resolve-Path -LiteralPath $CourseRoot).Path
            $null = Invoke-Stage "courses-list" $cli @("courses", "list", $CourseRoot)
            $null = Invoke-Stage "courses-status" $cli @("courses", "status", $CourseRoot)
            if ($Full -and $lintCode -eq 0 -and $testCode -eq 0) {
                $null = Invoke-Stage "courses-build" $cli @("courses", "build", $CourseRoot, "--profile", "standard")
                $null = Invoke-Stage "courses-audit" $cli @("courses", "audit", $CourseRoot, "--strict")
            }
            elseif ($Full) {
                Add-Skipped "courses-build" "Prima correggere i test o lint falliti"
            }
            $null = Invoke-Stage "courses-verify" $cli @(
                "courses", "verify", $CourseRoot, "--strict",
                "--output-dir", (Join-Path $reportDir "corpus")
            )
            $null = Invoke-Stage "agent-mesh-all" $cli @(
                "agents", "run", $CourseRoot, "--task", "publish", "--all",
                "--output-dir", (Join-Path $reportDir "agent-mesh")
            )
            Write-Host "Gli SKIP e INCOMPLETE degli agenti sono visibili nel report; non equivalgono a PASS."
        }
        else {
            Add-Skipped "corpus-reale" "Impostare -CourseRoot con la cartella delle materie"
            if ($Full) {
                $script:Stages.Add([pscustomobject]@{
                    name = "full-corpus-required"
                    status = "FAIL"
                    reason = "Modalita Full senza cartella materie valida"
                })
            }
        }
    }
    else {
        $script:Stages.Add([pscustomobject]@{
            name = "python-required"
            status = "FAIL"
            reason = "Impossibile creare ambiente Python"
        })
    }
}
finally {
    Pop-Location
    $revision = ""
    try {
        $revision = (& git -C $repo rev-parse HEAD 2>$null) -join ""
    }
    catch {
        $revision = "non disponibile"
    }
    $report = [ordered]@{
        timestamp = (Get-Date).ToString("o")
        repository = $repo
        revision = $revision
        corpus = $CourseRoot
        full = [bool]$Full
        stages = @($script:Stages.ToArray())
    }
    $reportPath = Join-Path $reportDir "result.json"
    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-Host ("Report: " + $reportPath) -ForegroundColor Cyan
}
$failed = @($script:Stages | Where-Object { $_.status -eq "FAIL" })
if ($failed.Count -gt 0) {
    Write-Host ("Test falliti: " + $failed.Count) -ForegroundColor Red
    exit 1
}
Write-Host "BC Science: tutti i test eseguiti sono PASS." -ForegroundColor Green
exit 0
