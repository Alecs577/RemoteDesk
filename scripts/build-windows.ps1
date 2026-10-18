param(
    [string]$Profile = "",
    [switch]$PublicRelease,
    [string]$SigningThumbprint = "",
    [string]$TimestampUrl = ""
)
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$VenvPath = Join-Path $ProjectRoot ".venv-build"
$PythonPath = Join-Path $VenvPath "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    $Launcher = Get-Command py -ErrorAction SilentlyContinue
    $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    $DirectPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
    $CreatedVenv = $false

    if ($Launcher) {
        & $Launcher.Source -3.13 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)"
        if ($LASTEXITCODE -eq 0) {
            & $Launcher.Source -3.13 -m venv $VenvPath
            if ($LASTEXITCODE -ne 0) { throw "Creazione dell'ambiente virtuale non riuscita." }
            $CreatedVenv = $true
        }
    }

    if (-not $CreatedVenv) {
        $Candidates = @()
        if (Test-Path -LiteralPath $DirectPython) { $Candidates += $DirectPython }
        if ($PythonCommand) { $Candidates += $PythonCommand.Source }
        foreach ($Candidate in $Candidates) {
            & $Candidate -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)"
            if ($LASTEXITCODE -eq 0) {
                & $Candidate -m venv $VenvPath
                if ($LASTEXITCODE -ne 0) { throw "Creazione dell'ambiente virtuale non riuscita." }
                $CreatedVenv = $true
                break
            }
        }
    }

    if (-not $CreatedVenv) {
        throw "Installa Python 3.13 x64 con Tkinter e assicurati che py -3.13 o python.exe lo avvii."
    }
}

& $PythonPath -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)"
if ($LASTEXITCODE -ne 0) { throw "L'ambiente .venv-build esistente non usa Python 3.13." }

& $PythonPath -m pip install --require-hashes --only-binary=:all: -r requirements/windows-build.lock
if ($LASTEXITCODE -ne 0) { throw "Installazione delle dipendenze di build non riuscita." }

if ($PublicRelease -and (-not $Profile -or -not $SigningThumbprint -or -not $TimestampUrl)) {
    throw "Release pubblica: specificare -Profile, -SigningThumbprint e -TimestampUrl."
}
$GeneratedProfile = Join-Path $ProjectRoot "remotedesk\_distribution.py"
if (Test-Path -LiteralPath $GeneratedProfile) { throw "Profilo generato già presente: rimuoverlo prima della build." }
try {
    if ($Profile) {
        $ProfileArgs = @("scripts/release.py", "profile", "--config", $Profile, "--output", $GeneratedProfile)
        if ($PublicRelease) { $ProfileArgs += "--public" }
        & $PythonPath @ProfileArgs
        if ($LASTEXITCODE -ne 0) { throw "Profilo distribuzione non valido." }
    }
    & $PythonPath scripts/collect-notices.py build/notices
    if ($LASTEXITCODE -ne 0) { throw "Raccolta licenze non riuscita." }
    $NoticesPath = Join-Path $ProjectRoot "build\notices"
    $LegalPath = Join-Path $ProjectRoot "legal"
    & $PythonPath -m PyInstaller --noconfirm --clean --onefile --windowed --name RemoteDesk --add-data "$NoticesPath;notices" --add-data "$LegalPath;legal" --specpath build\spec --workpath build\work --distpath dist remotedesk\__main__.py
    if ($LASTEXITCODE -ne 0) { throw "Build PyInstaller non riuscita." }
    # Companion console tool supports getpass during recovery on a new PC.
    & $PythonPath -m PyInstaller --noconfirm --clean --onefile --console --name RemoteDeskTools --add-data "$NoticesPath;notices" --add-data "$LegalPath;legal" --specpath build\spec --workpath build\tools --distpath dist remotedesk\__main__.py
    if ($LASTEXITCODE -ne 0) { throw "Build strumento di ripristino non riuscita." }
    Copy-Item -LiteralPath build/notices/THIRD_PARTY_NOTICES.txt -Destination dist/
    Copy-Item -LiteralPath build/notices/sbom.json -Destination dist/
    foreach ($Executable in @("dist/RemoteDesk.exe", "dist/RemoteDeskTools.exe")) {
        if ($PublicRelease) {
            & signtool sign /sha1 $SigningThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $Executable
            if ($LASTEXITCODE -ne 0) { throw "Firma Authenticode non riuscita." }
            & signtool verify /pa /all /v $Executable
            if ($LASTEXITCODE -ne 0) { throw "Verifica firma Authenticode non riuscita." }
        }
        $Hash = Get-FileHash -LiteralPath $Executable -Algorithm SHA256
        "$($Hash.Hash.ToLower())  $(Split-Path -Leaf $Executable)" | Set-Content -LiteralPath "$Executable.sha256" -Encoding ascii
    }
} finally {
    if (Test-Path -LiteralPath $GeneratedProfile) { Remove-Item -LiteralPath $GeneratedProfile }
}

$Artifact = Join-Path $ProjectRoot "dist\RemoteDesk.exe"
if (-not (Test-Path -LiteralPath $Artifact)) {
    throw "Build conclusa senza generare dist\RemoteDesk.exe."
}
Write-Output "Artefatto creato: $Artifact"
