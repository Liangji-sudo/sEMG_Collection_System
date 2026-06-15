"""
打包 hdf5_tool.py 为独立 exe

使用方法:
    python tools/build_tool.py

输出:
    dist/hdf5_tool.exe
"""

import subprocess
import sys
import os


def main():
    script = 'hdf5_tool.py'
    name = 'hdf5_tool'

    # 确保在 tools/ 目录下能找到脚本
    here = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(here, script)
    if not os.path.exists(script_path):
        print(f"[ERROR] 找不到脚本: {script_path}")
        sys.exit(1)

    print("=" * 60)
    print(f"正在打包: {script}")
    print("=" * 60)

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        '--windowed',
        '--clean',
        f'--name={name}',
        # h5py 需要 --collect-all 才能把 hdf5.dll 等原生库一起打包
        '--collect-all', 'h5py',
        '--hidden-import=numpy',
        '--hidden-import=PyQt5',
        '--hidden-import=matplotlib',
        '--hidden-import=matplotlib.backends.backend_qt5agg',
        '--hidden-import=msgpack',
        script_path
    ]

    print(f"执行: {' '.join(cmd)}\n")

    try:
        subprocess.run(cmd, check=True)
        print(f"\n{'=' * 60}")
        print(f"[OK] 打包成功!")
        print(f"输出: {os.path.abspath(f'dist/{name}.exe')}")
        print('=' * 60)
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] 打包失败: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
