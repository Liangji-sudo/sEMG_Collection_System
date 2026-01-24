@echo off
chcp 65001 >nul
echo ============================================================
echo   打包 setup_env.py 为 setup.exe
echo ============================================================
echo.

:: 检查 PyInstaller
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [提示] 正在安装 PyInstaller...
    pip install pyinstaller
)

echo 正在打包 setup_env.py...
echo.

pyinstaller --onefile --windowed --name=setup --clean setup_env.py

echo.
echo ============================================================
if exist "dist\setup.exe" (
    echo [成功] setup.exe 已生成
    echo 位置: dist\setup.exe

    :: 清理中间产物（保留 dist 目录）
    echo.
    echo [清理] 删除打包中间文件...
    if exist "build" rmdir /s /q build
    if exist "setup.spec" del /q setup.spec
    echo [清理] 完成
) else (
    echo [失败] 打包失败，请检查错误信息
)
echo ============================================================
pause
