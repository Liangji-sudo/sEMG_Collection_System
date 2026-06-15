@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo   ffmpeg 自动安装脚本
echo ============================================
echo.

:: ====== 先检查是否已安装 ======
where ffmpeg >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=*" %%i in ('where ffmpeg') do echo ✅ ffmpeg 已安装: %%i
    ffmpeg -version 2>&1 | findstr "ffmpeg version"
    echo.
    pause
    exit /b 0
)

echo 📦 ffmpeg 未安装，开始安装...
echo.

:: ====== 方法1: 尝试 winget ======
where winget >nul 2>&1
if %errorlevel% equ 0 (
    echo [方式1] 通过 winget 安装 ffmpeg（推荐）...
    echo.
    winget install Gyan.FFmpeg.Essentials --accept-package-agreements --accept-source-agreements

    if %errorlevel% equ 0 (
        echo.
        echo ============================================
        echo ✅ winget 安装完成！
        echo ============================================
        echo.
        echo ⚠️  请重启终端或刷新环境变量后生效。
        echo    关闭此窗口，重新打开一个终端再运行 check_deps.bat
        echo.
        pause
        exit /b 0
    )
    echo ⚠️  winget 安装失败，尝试手动下载...
    echo.
)

:: ====== 方法2: 手动下载 ======
echo [方式2] 手动下载 ffmpeg...
echo.

set "FFMPEG_DIR=C:\ffmpeg"
set "FFMPEG_URL=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
set "DOWNLOAD_PATH=%TEMP%\ffmpeg-release-essentials.zip"

echo   下载地址: %FFMPEG_URL%
echo   安装位置: %FFMPEG_DIR%
echo.

:: 检查是否已有下载好的 zip
if exist "%DOWNLOAD_PATH%" (
    echo 📁 发现已下载的 zip，跳过下载...
    goto :extract
)

:: 使用 PowerShell 下载
echo ⏳ 正在下载 ffmpeg (~30MB)...
powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%FFMPEG_URL%' -OutFile '%DOWNLOAD_PATH%' -UseBasicParsing}"

if not exist "%DOWNLOAD_PATH%" (
    echo.
    echo ❌ 下载失败！
    echo    请手动访问 https://www.gyan.dev/ffmpeg/builds/
    echo    下载 ffmpeg-release-essentials.zip 解压到 %FFMPEG_DIR%
    echo.
    pause
    exit /b 1
)

:extract
echo 📦 正在解压到 %FFMPEG_DIR% ...
powershell -Command "& {Expand-Archive -Path '%DOWNLOAD_PATH%' -DestinationPath '%TEMP%\ffmpeg_extract' -Force}"

:: 找到解压后的 ffmpeg 文件夹
for /d %%d in ("%TEMP%\ffmpeg_extract\ffmpeg-*") do set "EXTRACTED=%%d"
if "%EXTRACTED%"=="" (
    echo ❌ 解压失败，未找到 ffmpeg 目录
    pause
    exit /b 1
)

:: 复制到 C:\ffmpeg
if exist "%FFMPEG_DIR%" rmdir /s /q "%FFMPEG_DIR%"
xcopy /e /i /q "%EXTRACTED%" "%FFMPEG_DIR%"

:: 清理临时文件
rmdir /s /q "%TEMP%\ffmpeg_extract" 2>nul
del "%DOWNLOAD_PATH%" 2>nul

:: ====== 加入 PATH ======
echo.
echo 📝 正在将 ffmpeg 加入系统 PATH...

set "FFMPEG_BIN=%FFMPEG_DIR%\bin"

:: 检查是否已在 PATH 中
echo %PATH% | findstr /i /c:"%FFMPEG_BIN%" >nul
if %errorlevel% neq 0 (
    :: 添加到用户 PATH
    for /f "skip=2 tokens=3*" %%a in ('reg query HKCU\Environment /v PATH 2^>nul') do set "OLD_PATH=%%b"
    if "!OLD_PATH!"=="" (
        setx PATH "%FFMPEG_BIN%" >nul
    ) else (
        setx PATH "!OLD_PATH!;%FFMPEG_BIN%" >nul
    )
    echo ✅ 已添加 %FFMPEG_BIN% 到用户 PATH
) else (
    echo ✅ %FFMPEG_BIN% 已在 PATH 中
)

echo.
echo ============================================
echo ✅ ffmpeg 安装完成！
echo ============================================
echo.
echo   位置: %FFMPEG_BIN%
echo.
echo ⚠️  请重新打开终端使 PATH 生效，然后运行:
echo   deps\check_deps.bat
echo.

:: 当前会话临时可用（立即验证）
set "PATH=%PATH%;%FFMPEG_BIN%"
"%FFMPEG_BIN%\ffmpeg.exe" -version 2>&1 | findstr "ffmpeg version"

pause
