@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo ============================================================
echo    EMG数据采集系统 - Python环境安装脚本
echo ============================================================
echo.

:: ==================== 检查管理员权限 ====================
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [警告] 建议以管理员身份运行此脚本
    echo.
)

:: ==================== 检查Python是否已安装 ====================
echo [1/4] 检查Python环境...
python --version >nul 2>&1
if %errorLevel% equ 0 (
    for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VER=%%i
    echo       已安装Python !PYTHON_VER!
    goto :check_pip
) else (
    echo       未检测到Python，需要安装
    goto :install_python
)

:install_python
echo.
echo ============================================================
echo    请手动安装Python 3.9或更高版本
echo ============================================================
echo.
echo 请按以下步骤操作：
echo.
echo 1. 打开浏览器，访问: https://www.python.org/downloads/
echo.
echo 2. 下载 Python 3.11.x (推荐) 或 3.9+
echo.
echo 3. 运行安装程序时，务必勾选：
echo    [√] Add Python to PATH  （非常重要！）
echo    [√] Install pip
echo.
echo 4. 安装完成后，重新运行此脚本
echo.
echo ============================================================
echo.
echo 按任意键打开Python下载页面...
pause >nul
start https://www.python.org/downloads/
echo.
echo 安装Python后，请重新运行此脚本。
pause
exit /b 1

:: ==================== 检查pip ====================
:check_pip
echo.
echo [2/4] 检查pip包管理器...
python -m pip --version >nul 2>&1
if %errorLevel% equ 0 (
    echo       pip已就绪
) else (
    echo       正在安装pip...
    python -m ensurepip --default-pip
    if %errorLevel% neq 0 (
        echo [错误] pip安装失败，请检查Python安装
        pause
        exit /b 1
    )
)

:: ==================== 升级pip ====================
echo.
echo [3/4] 升级pip...

:: wheels 目录（与 bat 同级）
set WHEELS_DIR=%~dp0wheels
if exist "%WHEELS_DIR%\pip-*.whl" (
    echo       从本地 wheels 目录升级 pip...
    python -m pip install --no-index --find-links="%WHEELS_DIR%" --upgrade pip -q
) else (
    echo       [跳过] wheels 目录中没有 pip，使用当前版本
)
echo       pip 已就绪

:: ==================== 安装依赖包 ====================
echo.
echo [4/4] 安装Python依赖包...
echo.

:: wheels 目录（与 bat 同级）
if exist "%WHEELS_DIR%\*.whl" (
    echo ┌──────────────────────────────────────────────────────┐
    echo │  [离线模式] 检测到本地 wheels 目录                   │
    echo │  将从本地安装，不会联网下载                          │
    echo └──────────────────────────────────────────────────────┘
    echo.
    echo wheels 目录: %WHEELS_DIR%
    echo.

    :: 定义所需的包
    set PACKAGES=websockets bleak msgpack numpy scipy h5py pyzmq

    echo 正在从本地 wheels 目录安装所有依赖...
    echo.

    :: 强制离线安装，不允许联网
    python -m pip install --no-index --find-links="%WHEELS_DIR%" !PACKAGES!

    if !errorLevel! equ 0 (
        echo.
        echo [OK] 所有依赖包安装成功
    ) else (
        echo.
        echo [错误] 离线安装失败！
        echo 可能原因：
        echo   1. wheels 目录中缺少某些 whl 文件
        echo   2. whl 文件与当前 Python 版本不匹配
        echo.
        echo 请检查 wheels 目录中的文件，或重新运行 download_wheels.bat
        pause
        exit /b 1
    )
) else (
    echo ┌──────────────────────────────────────────────────────┐
    echo │  [错误] 未检测到本地 wheels 目录                     │
    echo │  请先运行 download_wheels.bat 下载依赖包             │
    echo └──────────────────────────────────────────────────────┘
    echo.
    echo 期望的 wheels 目录: %WHEELS_DIR%
    echo.
    pause
    exit /b 1
)

goto :verify_install

:verify_install

:: ==================== 验证安装 ====================
echo.
echo ============================================================
echo    验证安装结果
echo ============================================================
echo.

:: 检查关键包
set ALL_OK=1

echo 检查 websockets...
python -c "import websockets" 2>nul
if %errorLevel% equ 0 (echo   [OK] websockets) else (echo   [FAIL] websockets & set ALL_OK=0)

echo 检查 bleak (蓝牙库)...
python -c "import bleak" 2>nul
if %errorLevel% equ 0 (echo   [OK] bleak) else (echo   [FAIL] bleak & set ALL_OK=0)

echo 检查 msgpack...
python -c "import msgpack" 2>nul
if %errorLevel% equ 0 (echo   [OK] msgpack) else (echo   [FAIL] msgpack & set ALL_OK=0)

echo 检查 scipy (滤波器)...
python -c "import scipy" 2>nul
if %errorLevel% equ 0 (echo   [OK] scipy) else (echo   [FAIL] scipy & set ALL_OK=0)

echo 检查 numpy...
python -c "import numpy" 2>nul
if %errorLevel% equ 0 (echo   [OK] numpy) else (echo   [FAIL] numpy & set ALL_OK=0)

echo 检查 h5py (HDF5存储)...
python -c "import h5py" 2>nul
if %errorLevel% equ 0 (echo   [OK] h5py) else (echo   [FAIL] h5py & set ALL_OK=0)

echo 检查 zmq (进程通信)...
python -c "import zmq" 2>nul
if %errorLevel% equ 0 (echo   [OK] zmq) else (echo   [FAIL] zmq & set ALL_OK=0)

:: ==================== 显示结果 ====================
echo.
echo ============================================================
if %ALL_OK% equ 1 (
    echo    [成功] 所有依赖已安装完成！
    echo.
    echo    现在可以运行 EMG数据采集系统 了。
) else (
    echo    [警告] 部分依赖安装失败
    echo.
    echo    请尝试手动安装失败的包：
    echo    python -m pip install 包名
)
echo ============================================================
echo.

pause
exit /b 0
