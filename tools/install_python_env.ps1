# ============================================================
#    EMG数据采集系统 - Python环境安装脚本 (PowerShell版)
# ============================================================
# 
# 使用方法：
#   1. 右键点击此文件，选择"使用 PowerShell 运行"
#   2. 或在PowerShell中执行: .\install_python_env.ps1
#
# 如果遇到执行策略错误，请先执行：
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
#
# ============================================================

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   EMG数据采集系统 - Python环境安装脚本" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ==================== 配置 ====================
$PYTHON_VERSION = "3.11.1"
$PYTHON_INSTALLER_URL = "https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-amd64.exe"
$PYTHON_INSTALLER_PATH = "$env:TEMP\python-$PYTHON_VERSION-installer.exe"

$REQUIRED_PACKAGES = @(
    "websockets",
    "bleak", 
    "msgpack-python",
    "scipy",
    "numpy",
    "h5py",
    "pyzmq"
)

# ==================== 函数定义 ====================

function Test-PythonInstalled {
    try {
        $version = python --version 2>&1
        if ($version -match "Python (\d+\.\d+\.\d+)") {
            return $Matches[1]
        }
    } catch {
        return $null
    }
    return $null
}

function Install-PythonAuto {
    Write-Host ""
    Write-Host "[安装] 正在下载 Python $PYTHON_VERSION..." -ForegroundColor Yellow
    
    try {
        # 下载安装程序
        $webClient = New-Object System.Net.WebClient
        $webClient.DownloadFile($PYTHON_INSTALLER_URL, $PYTHON_INSTALLER_PATH)
        
        Write-Host "[安装] 正在安装 Python (这可能需要几分钟)..." -ForegroundColor Yellow
        
        # 静默安装，添加到PATH
        $installArgs = "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0"
        Start-Process -FilePath $PYTHON_INSTALLER_PATH -ArgumentList $installArgs -Wait
        
        # 刷新环境变量
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        
        # 验证安装
        Start-Sleep -Seconds 2
        $version = Test-PythonInstalled
        if ($version) {
            Write-Host "[成功] Python $version 安装完成！" -ForegroundColor Green
            return $true
        } else {
            Write-Host "[警告] Python可能已安装，但需要重启终端才能生效" -ForegroundColor Yellow
            Write-Host "       请关闭此窗口，重新运行脚本" -ForegroundColor Yellow
            return $false
        }
        
    } catch {
        Write-Host "[错误] 自动安装失败: $_" -ForegroundColor Red
        return $false
    } finally {
        # 清理安装文件
        if (Test-Path $PYTHON_INSTALLER_PATH) {
            Remove-Item $PYTHON_INSTALLER_PATH -Force -ErrorAction SilentlyContinue
        }
    }
}

function Install-PythonManual {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Yellow
    Write-Host "   请手动安装 Python" -ForegroundColor Yellow
    Write-Host "============================================================" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "1. 即将打开Python下载页面" -ForegroundColor White
    Write-Host ""
    Write-Host "2. 下载 Python 3.11.x (推荐)" -ForegroundColor White
    Write-Host ""
    Write-Host "3. 安装时务必勾选:" -ForegroundColor White
    Write-Host "   [√] Add Python to PATH  (非常重要！)" -ForegroundColor Green
    Write-Host "   [√] Install pip" -ForegroundColor Green
    Write-Host ""
    Write-Host "4. 安装完成后，关闭此窗口，重新运行脚本" -ForegroundColor White
    Write-Host ""
    
    Start-Process "https://www.python.org/downloads/"
}

function Install-PipPackages {
    Write-Host ""
    Write-Host "[安装] 升级pip..." -ForegroundColor Yellow
    python -m pip install --upgrade pip -q 2>$null
    
    Write-Host ""
    Write-Host "[安装] 安装Python依赖包..." -ForegroundColor Yellow
    Write-Host ""
    
    $total = $REQUIRED_PACKAGES.Count
    $current = 0
    $failed = @()
    
    foreach ($package in $REQUIRED_PACKAGES) {
        $current++
        Write-Host "[$current/$total] 安装 $package..." -NoNewline
        
        $result = python -m pip install $package -q 2>&1
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host " OK" -ForegroundColor Green
        } else {
            Write-Host " FAILED" -ForegroundColor Red
            $failed += $package
        }
    }
    
    return $failed
}

function Test-AllPackages {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "   验证安装结果" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
    
    $allOk = $true
    $packageChecks = @{
        "websockets" = "websockets"
        "bleak" = "bleak (蓝牙通信)"
        "msgpack" = "msgpack (数据序列化)"
        "scipy" = "scipy (信号滤波)"
        "numpy" = "numpy (数值计算)"
        "h5py" = "h5py (HDF5存储)"
        "zmq" = "zmq (进程通信)"
    }
    
    foreach ($pkg in $packageChecks.Keys) {
        $desc = $packageChecks[$pkg]
        Write-Host "检查 $desc... " -NoNewline
        
        $testResult = python -c "import $pkg" 2>&1
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[OK]" -ForegroundColor Green
        } else {
            Write-Host "[FAIL]" -ForegroundColor Red
            $allOk = $false
        }
    }
    
    return $allOk
}

# ==================== 主流程 ====================

# 步骤1: 检查Python
Write-Host "[1/3] 检查Python环境..." -ForegroundColor White
$pythonVersion = Test-PythonInstalled

if ($pythonVersion) {
    Write-Host "      已安装 Python $pythonVersion" -ForegroundColor Green
} else {
    Write-Host "      未检测到Python" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "请选择安装方式:" -ForegroundColor White
    Write-Host "  [1] 自动下载安装 Python $PYTHON_VERSION (推荐)" -ForegroundColor White
    Write-Host "  [2] 手动安装 (打开下载页面)" -ForegroundColor White
    Write-Host ""
    
    $choice = Read-Host "请输入选项 (1 或 2)"
    
    switch ($choice) {
        "1" {
            $success = Install-PythonAuto
            if (-not $success) {
                Write-Host ""
                Read-Host "按Enter键退出"
                exit 1
            }
        }
        "2" {
            Install-PythonManual
            Write-Host ""
            Read-Host "按Enter键退出"
            exit 0
        }
        default {
            Write-Host "无效选项，退出" -ForegroundColor Red
            exit 1
        }
    }
}

# 步骤2: 安装依赖包
Write-Host ""
Write-Host "[2/3] 安装Python依赖包..." -ForegroundColor White
$failedPackages = Install-PipPackages

# 步骤3: 验证
Write-Host ""
Write-Host "[3/3] 验证安装..." -ForegroundColor White
$allOk = Test-AllPackages

# 显示最终结果
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
if ($allOk) {
    Write-Host "   [成功] 所有依赖已安装完成！" -ForegroundColor Green
    Write-Host "" 
    Write-Host "   现在可以运行 EMG数据采集系统 了。" -ForegroundColor White
} else {
    Write-Host "   [警告] 部分依赖安装失败" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   请尝试手动安装:" -ForegroundColor White
    Write-Host "   python -m pip install 包名" -ForegroundColor White
}
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

Read-Host "按Enter键退出"
