@echo off
chcp 65001 >nul
echo ============================================================
echo   sEMG 数据采集系统 - Python 依赖安装脚本
echo ============================================================
echo.

:: 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.9+
    echo 下载地址: https://www.python.org/downloads/
    echo.
    echo 安装时请勾选 "Add Python to PATH"
    pause
    exit /b 1
)

echo [OK] Python 已安装:
python --version
echo.

:: 升级 pip
echo [1/8] 升级 pip...
python -m pip install --upgrade pip

:: 安装依赖
echo.
echo [2/8] 安装 websockets (WebSocket 通信)...
pip install websockets

echo.
echo [3/8] 安装 msgpack (消息序列化)...
pip install msgpack

echo.
echo [4/8] 安装 bleak (BLE 蓝牙通信)...
pip install bleak

echo.
echo [5/8] 安装 numpy (数值计算)...
pip install numpy

echo.
echo [6/8] 安装 scipy (信号滤波)...
pip install scipy

echo.
echo [7/8] 安装 h5py (HDF5 数据存储)...
pip install h5py

echo.
echo [8/8] 安装 pyzmq (ZeroMQ 通信)...
pip install pyzmq

echo.
echo ============================================================
echo   安装完成！
echo ============================================================
echo.
echo 已安装的包:
pip list | findstr /i "websockets msgpack bleak numpy scipy h5py pyzmq"
echo.
echo 现在可以直接运行 Python 脚本，无需打包成 exe
echo.
pause
