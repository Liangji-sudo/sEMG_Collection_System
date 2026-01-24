@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo ============================================================
echo    EMG数据采集系统 - Python依赖包下载工具
echo    目标平台: Windows 10/11, Python 3.11
echo ============================================================
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.11
    pause
    exit /b 1
)

:: 设置输出目录
set WHEELS_DIR=%~dp0..\wheels
if not exist "%WHEELS_DIR%" mkdir "%WHEELS_DIR%"

echo [信息] whl 文件将下载到: %WHEELS_DIR%
echo.

:: 定义需要下载的包（包含 pip）
set PACKAGES=pip websockets bleak msgpack numpy scipy h5py pyzmq

echo ┌──────────────────────────────────────────────────────┐
echo │  正在下载依赖包 (Windows x64, Python 3.11)          │
echo │  包含所有间接依赖，请耐心等待...                    │
echo └──────────────────────────────────────────────────────┘
echo.

:: 下载所有包及其依赖
echo [1/2] 下载所有依赖包...
python -m pip download ^
    --dest "%WHEELS_DIR%" ^
    --only-binary=:all: ^
    --platform win_amd64 ^
    --python-version 3.11 ^
    %PACKAGES%

if errorlevel 1 (
    echo.
    echo [警告] 部分包可能下载失败，尝试不限制平台重新下载...
    python -m pip download --dest "%WHEELS_DIR%" %PACKAGES%
)

:: 统计下载结果
echo.
echo [2/2] 统计下载结果...
set /a COUNT=0
for %%f in ("%WHEELS_DIR%\*.whl") do set /a COUNT+=1

echo.
echo ============================================================
echo    下载完成！
echo    共下载 %COUNT% 个 whl 文件
echo    位置: %WHEELS_DIR%
echo ============================================================
echo.
echo 文件列表:
dir /b "%WHEELS_DIR%\*.whl"
echo.

pause
exit /b 0
