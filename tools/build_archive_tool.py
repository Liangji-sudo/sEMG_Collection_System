"""
打包 archive_tool.py (数据统计查看器) 为独立 exe

使用方法:
    python tools/build_archive_tool.py              # 发布模式（onedir，启动更快）
    python tools/build_archive_tool.py --onefile    # 单文件模式（部署简单，但启动较慢）
    python tools/build_archive_tool.py --console    # 调试模式（保留控制台，查看错误）

输出:
    dist/stats_viewer/stats_viewer.exe
"""

import subprocess
import sys
import os


def main():
    script = 'archive_tool.py'
    name = 'stats_viewer'

    here = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(here, script)
    if not os.path.exists(script_path):
        print(f"[ERROR] 找不到脚本: {script_path}")
        sys.exit(1)

    debug_mode = '--console' in sys.argv or '--debug' in sys.argv
    onefile_mode = '--onefile' in sys.argv
    window_mode = '--console' if debug_mode else '--windowed'

    print("=" * 60)
    print(f"正在打包: {script} → {name}.exe")
    if debug_mode:
        print("模式: 调试 (保留控制台窗口)")
    print("打包形态:", "onefile 单文件（启动较慢）" if onefile_mode else "onedir 文件夹（推荐，启动更快）")
    print("=" * 60)

    # 统计查看器依赖 PyQt5 + h5py (H5状态读取)
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile' if onefile_mode else '--onedir',
        window_mode,
        '--clean',
        '--noupx',
        f'--name={name}',
        # PyQt5
        '--hidden-import=PyQt5',
        '--hidden-import=PyQt5.QtCore',
        '--hidden-import=PyQt5.QtGui',
        '--hidden-import=PyQt5.QtWidgets',
        '--hidden-import=PyQt5.sip',
        # h5py for reading H5 sync status (with fallback, core libs)
        '--collect-all', 'h5py',
        script_path,
    ]

    print(f"执行: {' '.join(cmd)}\n")

    try:
        subprocess.run(cmd, check=True)
        print(f"\n{'=' * 60}")
        print(f"[OK] 打包成功!")
        output_path = f'dist/{name}.exe' if onefile_mode else f'dist/{name}/{name}.exe'
        print(f"输出: {os.path.abspath(output_path)}")
        print("")
        print("提示:")
        print("  - onedir 模式: 把整个 dist/stats_viewer/ 文件夹拷贝给现场人员即可")
        print("  - onefile 模式: 把 dist/stats_viewer.exe 单独分发即可")
        print('=' * 60)
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] 打包失败: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
