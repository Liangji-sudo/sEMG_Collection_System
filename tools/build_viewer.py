"""
打包 hdf5_stage_viewer.py 为独立 exe

使用方法:
    python build_viewer.py

前置条件:
    pip install pyinstaller

输出:
    dist/hdf5_stage_viewer.exe
"""

import subprocess
import sys
import os
import shutil

def main():
    script = 'hdf5_stage_viewer.py'
    name = 'hdf5_stage_viewer'

    # 检查脚本是否存在
    if not os.path.exists(script):
        print(f"[ERROR] 找不到脚本: {script}")
        print("请在 tools/ 目录下运行此脚本")
        sys.exit(1)

    print("=" * 50)
    print(f"正在打包: {script}")
    print("=" * 50)

    # PyInstaller 命令
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',                    # 单文件
        '--windowed',                   # GUI程序，不显示控制台
        '--clean',                      # 清理缓存
        f'--name={name}',
        '--hidden-import=h5py',
        '--hidden-import=numpy',
        '--hidden-import=PyQt5',
        '--hidden-import=matplotlib',
        '--hidden-import=matplotlib.backends.backend_qt5agg',
        script
    ]

    print(f"执行: {' '.join(cmd)}\n")

    try:
        subprocess.run(cmd, check=True)
        print("\n" + "=" * 50)
        print(f"[OK] 打包成功!")
        print(f"输出: {os.path.abspath(f'dist/{name}.exe')}")
        print("=" * 50)

        # 清理临时文件
        for d in ['build', '__pycache__']:
            if os.path.exists(d):
                shutil.rmtree(d)
        if os.path.exists(f'{name}.spec'):
            os.remove(f'{name}.spec')

    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] 打包失败: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
