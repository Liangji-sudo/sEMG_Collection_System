"""
打包 hdf5_tool.py 为独立 exe

使用方法:
    python tools/build_tool.py              # 发布模式（无控制台窗口）
    python tools/build_tool.py --console    # 调试模式（保留控制台，查看错误）

输出:
    dist/hdf5_tool.exe
"""

import subprocess
import sys
import os


def main():
    script = 'hdf5_tool.py'
    name = 'hdf5_tool'

    # 【修复 H-G7】支持 --console 调试模式，保留控制台窗口查看错误
    debug_mode = '--console' in sys.argv or '--debug' in sys.argv
    window_mode = '--console' if debug_mode else '--windowed'

    # 确保在 tools/ 目录下能找到脚本
    here = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(here, script)
    if not os.path.exists(script_path):
        print(f"[ERROR] 找不到脚本: {script_path}")
        sys.exit(1)

    print("=" * 60)
    print(f"正在打包: {script}")
    if debug_mode:
        print("模式: 调试 (保留控制台窗口)")
    print("=" * 60)

    # 【修复 H-G6/H-G8】补充缺失的 hidden-import，确保依赖正确打包
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        window_mode,       # 发布模式隐藏控制台，调试模式保留
        '--clean',
        f'--name={name}',
        # h5py 需要 --collect-all 才能把 hdf5.dll 等原生库一起打包
        '--collect-all', 'h5py',
        # Python 科学计算
        '--hidden-import=numpy',
        '--hidden-import=scipy',
        '--hidden-import=scipy.signal',
        '--hidden-import=scipy.interpolate',
        # Qt GUI
        '--hidden-import=PyQt5',
        '--hidden-import=PyQt5.QtCore',
        '--hidden-import=PyQt5.QtGui',
        '--hidden-import=PyQt5.QtWidgets',
        '--hidden-import=PyQt5.sip',
        # 可视化
        '--hidden-import=matplotlib',
        '--hidden-import=matplotlib.backends.backend_qt5agg',
        # 数据处理
        '--hidden-import=msgpack',
        # 视频处理 (calibrate_tool 子模块需要)
        # 【修复】--hidden-import 只打包 Python 模块，不收集原生 DLL
        # cv2.VideoCapture 在 Windows 上依赖 opencv_videoio_ffmpeg*.dll
        # 必须用 --collect-all 才能把 FFmpeg 后端 DLL 一起打包
        '--collect-all', 'cv2',
        '--hidden-import=PIL',
        '--hidden-import=lz4.frame',
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
