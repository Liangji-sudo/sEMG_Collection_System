@echo off
chcp 65001 >nul
echo ============================================
echo   相机功能依赖检查
echo ============================================
echo.

echo [1/2] 检查 ffmpeg ...
where ffmpeg >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=*" %%i in ('where ffmpeg') do echo   ✅ ffmpeg: %%i
    ffmpeg -version 2>&1 | findstr "ffmpeg version"
) else (
    echo   ❌ ffmpeg 未安装或不在 PATH 中
)

echo.
echo [2/2] 检查 ffprobe ...
where ffprobe >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=*" %%i in ('where ffprobe') do echo   ✅ ffprobe: %%i
    ffprobe -version 2>&1 | findstr "ffprobe version"
) else (
    echo   ❌ ffprobe 未安装或不在 PATH 中
)

echo.
echo ============================================
echo   检查完成
echo ============================================

if %errorlevel% equ 0 (
    echo   所有依赖已就绪，相机功能可用。
) else (
    echo   部分依赖缺失，请参考 deps\README.md 安装。
)

pause
