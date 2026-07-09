"""
Python 脚本打包工具
将 Python 脚本打包成独立的 .exe 文件，用于 Electron 应用分发

使用方法:
    python build_python.py

前置条件:
    pip install pyinstaller

打包后的 exe 文件会放在 python_dist/ 目录下
"""

import subprocess
import sys
import os
import shutil

# 需要打包的脚本配置
SCRIPTS = [
    {
        'name': 'ble_server',
        'script': 'ble_server.py',
        'hidden_imports': [
            'msgpack', 'websockets', 'asyncio', 'json',
            # BLE 相关（关键！）
            'bleak', 'bleak.backends', 'bleak.backends.winrt',
            'bleak.backends.winrt.scanner', 'bleak.backends.winrt.client',
            # 滤波器相关
            'scipy', 'scipy.signal', 'numpy',
            # Windows BLE 后端依赖
            'winrt', 'winrt.windows.devices.bluetooth',
            'winrt.windows.devices.bluetooth.genericattributeprofile',
            'winrt.windows.devices.bluetooth.advertisement',
            'winrt.windows.devices.enumeration',
            'winrt.windows.foundation',
            'winrt.windows.storage.streams',
        ],
    },
    {
        'name': 'storage_server',
        'script': 'storage_server.py',
        'hidden_imports': ['h5py', 'zmq', 'numpy', 'json', 'argparse'],
    },
    {
        'name': 'mocap_server',
        'script': 'mocap_server.py',
        'hidden_imports': ['websockets', 'asyncio', 'json'],
    },
    {
        'name': 'camera_server',
        'script': 'camera_server.py',
        'hidden_imports': ['websockets', 'asyncio', 'json'],
    },
    {
        'name': 'video_encoder_worker',
        'script': 'video_encoder_worker.py',
        'hidden_imports': ['websockets', 'asyncio', 'json'],
    },
]

# 输出目录
OUTPUT_DIR = 'python_dist'

def check_pyinstaller():
    """检查 PyInstaller 是否安装"""
    try:
        import PyInstaller
        print(f"[OK] PyInstaller 已安装: {PyInstaller.__version__}")
        return True
    except ImportError:
        print("[ERROR] PyInstaller 未安装")
        print("请运行: pip install pyinstaller")
        return False

def build_script(config):
    """打包单个脚本"""
    name = config['name']
    script = config['script']
    hidden_imports = config.get('hidden_imports', [])

    print(f"\n{'='*50}")
    print(f"正在打包: {name}")
    print(f"{'='*50}")

    if not os.path.exists(script):
        print(f"[ERROR] 脚本不存在: {script}")
        return False

    # 构建 PyInstaller 命令
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onedir',                     # 打包成文件夹（比 --onefile 快很多）
        '--console',                    # 保留控制台窗口（便于调试）
        '--clean',                      # 清理临时文件
        f'--name={name}',               # 输出文件名
        f'--distpath={OUTPUT_DIR}',     # 输出目录
        '--workpath=build_temp',        # 临时工作目录
        '--specpath=build_temp',        # spec 文件目录
    ]

    # 添加隐藏导入
    for imp in hidden_imports:
        cmd.extend(['--hidden-import', imp])

    # 添加脚本路径
    cmd.append(script)

    print(f"执行命令: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, check=True, capture_output=False)
        print(f"[OK] {name}.exe 打包成功")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] {name} 打包失败: {e}")
        return False

def clean_build():
    """清理构建临时文件"""
    dirs_to_clean = ['build_temp', '__pycache__']
    for d in dirs_to_clean:
        if os.path.exists(d):
            shutil.rmtree(d)
            print(f"[清理] 删除目录: {d}")

def main():
    print("=" * 60)
    print("Python 脚本打包工具 for Electron")
    print("=" * 60)

    # 检查 PyInstaller
    if not check_pyinstaller():
        sys.exit(1)

    # 创建输出目录
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 打包每个脚本
    success_count = 0
    for config in SCRIPTS:
        if build_script(config):
            success_count += 1

    # 清理临时文件
    clean_build()

    # 打印结果
    print("\n" + "=" * 60)
    print(f"打包完成: {success_count}/{len(SCRIPTS)} 成功")
    print(f"输出目录: {os.path.abspath(OUTPUT_DIR)}")
    print("=" * 60)

    if success_count == len(SCRIPTS):
        print("\n[提示] 请修改 deviceSync.js 和 dataStorage.js 中的 Python 调用路径")
        print("将 spawn('python', ['xxx.py']) 改为 spawn('xxx.exe')")

if __name__ == '__main__':
    main()
