# LiveAIO build — single exe + multi DLL (Core / Pages / Tools).
# Usage:
#   .\scripts\build.ps1
#   .\scripts\build.ps1 -Release
#   .\scripts\build.ps1 -Release -Upx -Browsers -Nsis
#   .\scripts\build.ps1 -SkipGo | -SkipCpp | -Soft
#
# Packaging flags (same idea as old Nuitka packager):
#   -Upx       UPX-compress LiveAIO.exe + Core/Pages/Tools DLL
#   -Browsers  copy repo browsers/ (chromedp headless shell) into out/stage
#   -Nsis / -Installer  build NSIS setup under build/installers/
#
# Output default: build/build_work/custom/
# Release:        build/build_work/<version>/
# Do NOT create go.work / go.work.sum at repo root (use main/go.work).

[CmdletBinding()]
param(
    [switch]$Release,
    [switch]$Installer,
    [switch]$NSIS,
    [switch]$Upx,
    [switch]$Browsers,
    [switch]$Soft,
    [switch]$SkipGo,
    [switch]$SkipCpp,
    [string]$Version = "",
    [string]$QtPrefix = "",
    [string]$CmakeGenerator = ""
)

$ErrorActionPreference = "Stop"
$ScriptsDir = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ScriptsDir)) {
    $ScriptsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$Root = Split-Path -Parent $ScriptsDir
$script:Root = $Root
$script:ScriptsDir = $ScriptsDir
Set-Location $Root
$WantNsis = [bool]($Installer -or $NSIS)
$UpxUrl = "https://github.com/upx/upx/releases/download/v5.0.2/upx-5.0.2-win64.zip"

function Write-Step([string]$msg) { Write-Host "[build] $msg" -ForegroundColor Cyan }
function Write-Warn([string]$msg) { Write-Host "[build] WARN: $msg" -ForegroundColor Yellow }
function Fail([string]$msg) {
    if ($Soft) { Write-Warn $msg; return }
    throw $msg
}

function Get-AppVersion {
    if ($Version) { return $Version }
    $proto = Join-Path $Root "core\protocol.go"
    if (Test-Path $proto) {
        $m = Select-String -Path $proto -Pattern 'ProtocolVersion\s*=\s*"([^"]+)"' | Select-Object -First 1
        if ($m) { return $m.Matches[0].Groups[1].Value }
    }
    return "0.0.0"
}

function Find-CommandPath([string]$name, [string[]]$extraDirs) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($d in $extraDirs) {
        if (-not $d) { continue }
        $p = Join-Path $d $name
        if (Test-Path $p) { return (Resolve-Path $p).Path }
    }
    return $null
}

function Find-QtPrefix {
    if ($QtPrefix -and (Test-Path $QtPrefix)) { return (Resolve-Path $QtPrefix).Path }
    if ($env:CMAKE_PREFIX_PATH) {
        foreach ($p in ($env:CMAKE_PREFIX_PATH -split ";")) {
            if ($p -and (Test-Path (Join-Path $p "bin\qmake.exe"))) { return $p }
            if ($p -and (Test-Path (Join-Path $p "lib\cmake\Qt6"))) { return $p }
        }
    }
    if ($env:Qt6_DIR) {
        $cand = Split-Path (Split-Path $env:Qt6_DIR -Parent) -Parent
        if (Test-Path $cand) { return $cand }
    }
    $roots = @(
        "C:\Qt", "D:\Qt", "E:\Qt",
        (Join-Path $env:USERPROFILE "Qt"),
        "C:\Qt\Tools",
        "D:\aqt"
    )
    foreach ($r in $roots) {
        if (-not (Test-Path $r)) { continue }
        $qmakes = Get-ChildItem -Path $r -Filter qmake.exe -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\(mingw|msvc|llvm).*\\bin\\qmake\.exe$' } |
            Select-Object -First 8
        foreach ($q in $qmakes) {
            return (Split-Path $q.Directory.FullName -Parent)
        }
    }
    return $null
}

function Ensure-GccOnPath {
    # Prefer Qt MinGW first so CGO and C++ share one runtime when possible;
    # fall back to MSYS2.
    $candidates = @(
        "C:\Qt\Tools\mingw1310_64\bin",
        "C:\Qt\Tools\mingw1120_64\bin",
        "D:\msys64\ucrt64\bin",
        "D:\msys64\mingw64\bin",
        "C:\msys64\ucrt64\bin",
        "C:\msys64\mingw64\bin"
    )
    foreach ($d in $candidates) {
        $g = Join-Path $d "gcc.exe"
        if (Test-Path $g) {
            $env:PATH = "$d;$env:PATH"
            Write-Step "gcc → $g"
            return $g
        }
    }
    $gcc = Get-Command gcc -ErrorAction SilentlyContinue
    if ($gcc) { return $gcc.Source }
    return $null
}

function Ensure-QtToolchainOnPath([string]$QtRoot) {
    $dirs = @(
        (Join-Path $QtRoot "bin"),
        "C:\Qt\Tools\mingw1310_64\bin",
        "C:\Qt\Tools\mingw1120_64\bin",
        "C:\Qt\Tools\Ninja"
    )
    $prepend = @()
    foreach ($d in $dirs) {
        if ($d -and (Test-Path $d)) { $prepend += $d }
    }
    if ($prepend.Count -gt 0) {
        $env:PATH = ($prepend -join ";") + ";" + $env:PATH
        Write-Step ("toolchain PATH ← " + ($prepend -join "; "))
    }
}

function Invoke-GoCoreBuild([string]$OutDir) {
    Write-Step "Go Core DLL (c-shared) → $OutDir"
    if (-not (Ensure-GccOnPath)) {
        Fail "gcc not found (needed for CGO c-shared). Install MSYS2 ucrt64 or Qt MinGW."
        return
    }
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $env:CGO_ENABLED = "1"
    if (-not $env:GOPROXY) { $env:GOPROXY = "https://goproxy.cn,direct" }
    # Never materialize go.work at repo root from this script.
    $dll = Join-Path $OutDir "LiveAIOCore.dll"
    $hdr = Join-Path $OutDir "LiveAIOCore.h"
    & go build -buildmode=c-shared -o $dll ./core/dll
    if ($LASTEXITCODE -ne 0) {
        Fail "go build -buildmode=c-shared failed (exit $LASTEXITCODE)"
        return
    }
    if (Test-Path $hdr) { Remove-Item -Force $hdr -ErrorAction SilentlyContinue }
    Write-Step "OK LiveAIOCore.dll"
}

function Invoke-CmakeBuild([string]$OutDir, [string]$QtRoot) {
    Write-Step "CMake host/pages/tools → $OutDir"
    $cmake = Find-CommandPath "cmake.exe" @(
        "C:\Program Files\CMake\bin",
        "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin",
        "C:\Program Files\Microsoft Visual Studio\2022\Professional\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin",
        "D:\msys64\ucrt64\bin",
        "C:\msys64\ucrt64\bin"
    )
    if (-not $cmake) {
        Fail "cmake not found on PATH"
        return
    }
    if (-not $QtRoot) {
        Fail "Qt6 prefix not found. Pass -QtPrefix <Qt\6.x\mingw_64> or set CMAKE_PREFIX_PATH."
        return
    }
    Write-Step "Qt prefix → $QtRoot"
    Ensure-QtToolchainOnPath $QtRoot

    # 用 $script:Root，避免函数作用域里 $Root/$binDir 偶发为空导致 Test-Path 炸。
    $repoRoot = $script:Root
    if ([string]::IsNullOrWhiteSpace($repoRoot)) {
        $repoRoot = Split-Path -Parent $PSScriptRoot
    }
    if ([string]::IsNullOrWhiteSpace($repoRoot)) {
        Fail "repo root is empty; cannot locate build/cmake/all"
        return
    }
    $cmakeBuildDir = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "build\cmake\all"))
    New-Item -ItemType Directory -Force -Path $OutDir, $cmakeBuildDir | Out-Null

    $gen = $CmakeGenerator
    if (-not $gen) {
        $ninja = Get-Command ninja -ErrorAction SilentlyContinue
        if ($ninja) { $gen = "Ninja" }
        elseif (Get-Command mingw32-make -ErrorAction SilentlyContinue) { $gen = "MinGW Makefiles" }
        else { $gen = "MinGW Makefiles" }
    }

    $env:CMAKE_PREFIX_PATH = $QtRoot
    $gxx = Get-Command g++.exe -ErrorAction SilentlyContinue
    $gcc = Get-Command gcc.exe -ErrorAction SilentlyContinue

    $cmakeArgs = @(
        "-S", $ScriptsDir,
        "-B", $cmakeBuildDir,
        "-G", $gen,
        "-DCMAKE_BUILD_TYPE=Release",
        "-DLIVEAIO_OUT_DIR=$OutDir",
        "-DCMAKE_PREFIX_PATH=$QtRoot",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"
    )
    if ($gcc) { $cmakeArgs += "-DCMAKE_C_COMPILER=$($gcc.Source)" }
    if ($gxx) { $cmakeArgs += "-DCMAKE_CXX_COMPILER=$($gxx.Source)" }
    & $cmake @cmakeArgs
    if ($LASTEXITCODE -ne 0) {
        Fail "cmake configure failed"
        return
    }

    # clangd：仓库根 .clangd 已设 CompilationDatabase: build/cmake/all；勿复制到仓库根。
    $compDb = Join-Path $cmakeBuildDir "compile_commands.json"
    if (-not [string]::IsNullOrWhiteSpace($compDb) -and (Test-Path -LiteralPath $compDb)) {
        Write-Step "compile_commands.json → $cmakeBuildDir (clangd via .clangd)"
    } else {
        Write-Warn "CMAKE_EXPORT_COMPILE_COMMANDS did not produce compile_commands.json"
    }

    & $cmake --build $cmakeBuildDir --config Release
    if ($LASTEXITCODE -ne 0) {
        Fail "cmake build failed"
        return
    }

    $qtBin = Join-Path $QtRoot "bin"
    $windeploy = Join-Path $qtBin "windeployqt.exe"
    if (-not (Test-Path $windeploy)) {
        $windeploy = Find-CommandPath "windeployqt.exe" @($qtBin)
    }
    if ($windeploy) {
        # PowerShell $ErrorActionPreference=Stop 会把 stderr 警告当终止错误；
        # windeployqt 缺 dxcompiler 时仍 exit 0，必须吞掉 stderr 只看退出码。
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            foreach ($bin in @("LiveAIO.exe", "LiveAIOPages.dll", "LiveAIOTools.dll")) {
                $path = Join-Path $OutDir $bin
                if (-not (Test-Path $path)) { continue }
                Write-Step "windeployqt $bin"
                $null = & $windeploy --release --no-translations --dir $OutDir $path 2>&1
                if ($LASTEXITCODE -ne 0) {
                    Fail "windeployqt $bin exit $LASTEXITCODE"
                    return
                }
            }
        } finally {
            $ErrorActionPreference = $prevEap
        }
    } else {
        Write-Warn "windeployqt not found — Qt runtime DLLs may be missing beside the exe"
    }

    # MinGW 运行时不总被 windeployqt 带上，显式从 toolchain 拷贝。
    $mingwBins = @(
        "C:\Qt\Tools\mingw1310_64\bin",
        "C:\Qt\Tools\mingw1120_64\bin",
        (Join-Path $QtRoot "bin")
    )
    foreach ($runtime in @("libgcc_s_seh-1.dll", "libstdc++-6.dll", "libwinpthread-1.dll")) {
        $dst = Join-Path $OutDir $runtime
        if (Test-Path $dst) { continue }
        foreach ($dir in $mingwBins) {
            $src = Join-Path $dir $runtime
            if (Test-Path $src) {
                Copy-Item $src $dst -Force
                break
            }
        }
    }

    $obsolete = Join-Path $OutDir "LiveAIOUI.exe"
    if (Test-Path $obsolete) { Remove-Item -Force $obsolete }

    foreach ($name in @("LiveAIO.exe", "LiveAIOPages.dll", "LiveAIOTools.dll")) {
        if (-not (Test-Path (Join-Path $OutDir $name))) {
            Fail "missing artifact: $name"
        }
    }
    foreach ($name in @(
        "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Network.dll",
        "platforms\qwindows.dll"
    )) {
        if (-not (Test-Path (Join-Path $OutDir $name))) {
            Fail "missing Qt runtime after windeployqt: $name"
        }
    }
    Write-Step "OK C++ artifacts + Qt runtime"
}

function Invoke-StageRelease {
    param(
        [string]$CustomDir,
        [string]$StageDir,
        [switch]$IncludeBrowsers
    )
    Write-Step "Stage release → $StageDir"
    if (Test-Path $StageDir) { Remove-Item -Recurse -Force $StageDir }
    New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
    Copy-Item -Path (Join-Path $CustomDir "*") -Destination $StageDir -Recurse -Force

    $resSrc = Join-Path $Root "resources"
    if (Test-Path $resSrc) {
        $resDst = Join-Path $StageDir "resources"
        if (Test-Path $resDst) { Remove-Item -Recurse -Force $resDst }
        Copy-Item -Path $resSrc -Destination $resDst -Recurse -Force
    }
    $lic = Join-Path $Root "LICENSE"
    if (Test-Path $lic) { Copy-Item $lic (Join-Path $StageDir "LICENSE") -Force }

    # External helpers (not part of the single-exe rule; optional).
    $proxy = Join-Path $Root "listener\proxy_shell.exe"
    if (Test-Path $proxy) {
        New-Item -ItemType Directory -Force -Path (Join-Path $StageDir "listener") | Out-Null
        Copy-Item $proxy (Join-Path $StageDir "listener\proxy_shell.exe") -Force
    }

    if ($IncludeBrowsers) {
        Invoke-CopyBrowsers -DestDir $StageDir
    }
}

function Find-UpxExe {
    if ($env:LIVEAIO_UPX -and (Test-Path $env:LIVEAIO_UPX)) {
        return (Resolve-Path $env:LIVEAIO_UPX).Path
    }
    $found = Find-CommandPath "upx.exe" @(
        (Join-Path $Root "build\tools"),
        (Join-Path $ScriptsDir "tools"),
        "C:\Tools\upx",
        "C:\upx"
    )
    if ($found) { return $found }
    return $null
}

function Ensure-Upx {
    $existing = Find-UpxExe
    if ($existing) { return $existing }

    $toolsDir = Join-Path $Root "build\tools"
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    $zip = Join-Path $Root "_upx.zip"
    $dest = Join-Path $toolsDir "upx.exe"
    Write-Step "UPX not found — downloading $UpxUrl"
    try {
        Invoke-WebRequest -Uri $UpxUrl -OutFile $zip -UseBasicParsing
        $extract = Join-Path $toolsDir "_upx_extract"
        if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
        Expand-Archive -Path $zip -DestinationPath $extract -Force
        $bin = Get-ChildItem -Path $extract -Filter upx.exe -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $bin) { throw "upx.exe missing inside zip" }
        Copy-Item $bin.FullName $dest -Force
        Remove-Item -Recurse -Force $extract -ErrorAction SilentlyContinue
        Remove-Item -Force $zip -ErrorAction SilentlyContinue
        Write-Step "UPX → $dest"
        return $dest
    } catch {
        Fail "UPX download/setup failed: $_"
        return $null
    }
}

function Invoke-UpxCompress([string]$Dir) {
    $upx = Ensure-Upx
    if (-not $upx) { return }
    Write-Step "UPX compress → $Dir"
    $targets = @(
        "LiveAIO.exe",
        "LiveAIOCore.dll",
        "LiveAIOPages.dll",
        "LiveAIOTools.dll"
    )
    $ok = 0
    $skip = 0
    $fail = 0
    foreach ($name in $targets) {
        $path = Join-Path $Dir $name
        if (-not (Test-Path $path)) {
            Write-Warn "UPX skip missing $name"
            $skip++
            continue
        }
        Write-Step "UPX $name"
        $p = Start-Process -FilePath $upx -ArgumentList @("--best", "--compress-icons=0", "-q", $path) `
            -Wait -PassThru -NoNewWindow
        switch ($p.ExitCode) {
            0 { $ok++ }
            2 {
                # already packed / nothing to do
                Write-Step "UPX skip (already packed): $name"
                $skip++
            }
            default {
                Write-Warn "UPX failed $name exit $($p.ExitCode)"
                $fail++
            }
        }
    }
    Write-Step "UPX done: ok=$ok skip=$skip fail=$fail"
}

function Invoke-CopyBrowsers([string]$DestDir) {
    $src = Join-Path $Root "browsers"
    if (-not (Test-Path $src)) {
        Fail "browsers/ not found at $src (needed for -Browsers)"
        return
    }
    $shell = Get-ChildItem -Path $src -Filter "chrome-headless-shell.exe" -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $shell) {
        Fail "no chrome-headless-shell.exe under browsers/ — chromedp PreferBundled will fail"
        return
    }
    $dst = Join-Path $DestDir "browsers"
    Write-Step "Copy browsers/ → $dst"
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    # Copy tree but skip Playwright .links noise if present as top-level symlink stash.
    robocopy $src $dst /E /XD .links /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    $rc = $LASTEXITCODE
    # robocopy: 0-7 success-ish
    if ($rc -ge 8) {
        Fail "robocopy browsers failed code $rc"
        return
    }
    Write-Step "OK browsers (from $($shell.FullName))"
}

function Get-InstallerTagSuffix {
    $tags = @()
    if ($Upx) { $tags += "upx" }
    if ($Browsers) { $tags += "browsers" }
    if ($tags.Count -eq 0) { return "" }
    return "_" + ($tags -join "_")
}

function Invoke-Nsis([string]$StageDir, [string]$AppVer) {
    $makensis = Find-CommandPath "makensis.exe" @(
        "C:\Program Files (x86)\NSIS",
        "C:\Program Files\NSIS"
    )
    if (-not $makensis) {
        Fail "makensis not found — install NSIS or skip -Nsis/-Installer"
        return
    }
    # UPX 会破坏 PE 内嵌图标，NSIS 的 MUI_ICON 不能再指向压过的 LiveAIO.exe。
    $ico = Join-Path $Root "resources\image\LiveAIO.ico"
    if (-not (Test-Path $ico)) {
        Fail "missing installer icon: $ico"
        return
    }
    Copy-Item $ico (Join-Path $StageDir "LiveAIO.ico") -Force
    $outDir = Join-Path $Root "build\installers"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $safeVer = ($AppVer -replace '[^\w\.\-]', '_')
    $tag = Get-InstallerTagSuffix
    $outfile = Join-Path $outDir "LiveAIO-setup${tag}_$safeVer.exe"
    $nsi = Join-Path $ScriptsDir "installer.nsi"
    $license = Join-Path $Root "LICENSE"
    Write-Step "NSIS → $outfile"
    & $makensis `
        "/DSRCDIR=$StageDir" `
        "/DOUTFILE=$outfile" `
        "/DAPP_VERSION=$AppVer" `
        "/DLICENSE_FILE=$license" `
        "/DAPP_ICON=$ico" `
        $nsi
    if ($LASTEXITCODE -ne 0) {
        Fail "makensis failed"
        return
    }
    Write-Step "OK installer $outfile"
}

# ── main ──────────────────────────────────────────────────────────────
$AppVer = Get-AppVersion
$WorkRoot = Join-Path $Root "build\build_work"
$CustomDir = Join-Path $WorkRoot "custom"
$OutDir = $CustomDir
if ($Release) {
    $OutDir = Join-Path $WorkRoot $AppVer
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Step "root=$Root version=$AppVer out=$OutDir flags: Release=$Release Upx=$Upx Browsers=$Browsers Nsis=$WantNsis Soft=$Soft"

$qt = Find-QtPrefix
if (-not $SkipGo) {
    try { Invoke-GoCoreBuild -OutDir $OutDir }
    catch { if ($Soft) { Write-Warn $_ } else { throw } }
} else {
    Write-Step "SkipGo"
}

if (-not $SkipCpp) {
    try { Invoke-CmakeBuild -OutDir $OutDir -QtRoot $qt }
    catch { if ($Soft) { Write-Warn $_ } else { throw } }
} else {
    Write-Step "SkipCpp"
}

# 始终把 resources 同步进产物目录，使 custom/ / version / NSIS 成为「完整包」
# （Core + resources），与 ResolveAppRoot 的 isPackagedRoot 一致。
if ([string]::IsNullOrWhiteSpace($Root)) { $Root = $script:Root }
if ([string]::IsNullOrWhiteSpace($Root)) { throw "build.ps1: `$Root is empty after C++ build" }
if ([string]::IsNullOrWhiteSpace($OutDir)) { throw "build.ps1: `$OutDir is empty after C++ build" }
$resSrc = Join-Path $Root "resources"
$resDst = Join-Path $OutDir "resources"
if (-not [string]::IsNullOrWhiteSpace($resSrc) -and (Test-Path -LiteralPath $resSrc)) {
    Write-Step "Sync resources → $OutDir"
    if (-not [string]::IsNullOrWhiteSpace($resDst) -and (Test-Path -LiteralPath $resDst)) {
        Remove-Item -Recurse -Force $resDst
    }
    Copy-Item $resSrc $resDst -Recurse -Force
}

if ($Release -and ($OutDir -ne $CustomDir)) {
    # Already writing into version dir; resources synced above.
    $lic = Join-Path $Root "LICENSE"
    if ((Test-Path $lic) -and -not (Test-Path (Join-Path $OutDir "LICENSE"))) {
        Copy-Item $lic (Join-Path $OutDir "LICENSE") -Force
    }
}

if ($Browsers) {
    try { Invoke-CopyBrowsers -DestDir $OutDir }
    catch { if ($Soft) { Write-Warn $_ } else { throw } }
}

if ($Upx) {
    try { Invoke-UpxCompress -Dir $OutDir }
    catch { if ($Soft) { Write-Warn $_ } else { throw } }
}

# Keep custom/ mirrored for F5 / LIVEAIO_ROOT debug when building Release into version dir.
if ($Release -and ($OutDir -ne $CustomDir)) {
    Write-Step "Mirror binaries + resources → custom/"
    New-Item -ItemType Directory -Force -Path $CustomDir | Out-Null
    foreach ($name in @(
        "LiveAIO.exe", "LiveAIOCore.dll", "LiveAIOPages.dll", "LiveAIOTools.dll"
    )) {
        $src = Join-Path $OutDir $name
        if (Test-Path $src) { Copy-Item $src (Join-Path $CustomDir $name) -Force }
    }
    $resSrc = Join-Path $OutDir "resources"
    if (Test-Path $resSrc) {
        $resDst = Join-Path $CustomDir "resources"
        if (Test-Path $resDst) { Remove-Item -Recurse -Force $resDst }
        Copy-Item $resSrc $resDst -Recurse -Force
    }
}

if ($WantNsis) {
    $stage = $OutDir
    if (-not $Release) {
        $tmp = Join-Path $WorkRoot ("stage_" + $AppVer)
        Invoke-StageRelease -CustomDir $CustomDir -StageDir $tmp -IncludeBrowsers:$Browsers
        $stage = $tmp
    } else {
        # Release out dir may already have resources/browsers; fill gaps.
        if (-not (Test-Path (Join-Path $stage "resources"))) {
            $resSrc = Join-Path $Root "resources"
            if (Test-Path $resSrc) {
                Copy-Item $resSrc (Join-Path $stage "resources") -Recurse -Force
            }
        }
        if ($Browsers -and -not (Test-Path (Join-Path $stage "browsers"))) {
            Invoke-CopyBrowsers -DestDir $stage
        }
    }
    Invoke-Nsis -StageDir $stage -AppVer $AppVer
}

Write-Host "=== build done → $OutDir ===" -ForegroundColor Green
Write-Host "Artifacts: LiveAIO.exe + LiveAIOCore.dll + LiveAIOPages.dll + LiveAIOTools.dll"
if ($Browsers) { Write-Host "Packaged: browsers/" }
if ($Upx) { Write-Host "Packaged: UPX on product binaries" }
if ($WantNsis) { Write-Host "Packaged: NSIS under build/installers/" }