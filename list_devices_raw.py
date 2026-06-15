"""list_devices_raw.py - 枚举 USB 摄像头设备（独立工具，不依赖 camera_server）"""
import subprocess
import glob
import os
import shutil
from pathlib import Path


def find_ffmpeg():
    """查找 ffmpeg（优先 PATH，回退常见安装位置）"""
    path = shutil.which('ffmpeg')
    if path:
        print(f'找到 ffmpeg (PATH): {path}')
        return path

    # %LOCALAPPDATA% 搜索 WinGet
    local_appdata = os.environ.get('LOCALAPPDATA', '')
    if local_appdata:
        for pkg in ['Gyan.FFmpeg', 'Gyan.FFmpeg.Essentials', 'Gyan.FFmpeg.Shared']:
            pattern = str(Path(local_appdata) / f'Microsoft/WinGet/Packages/{pkg}*' / 'ffmpeg-*' / 'bin' / 'ffmpeg.exe')
            matches = glob.glob(pattern)
            if matches:
                print(f'找到 ffmpeg (WinGet): {matches[0]}')
                return matches[0]

    # 常见固定路径
    for p in ['C:/ffmpeg/bin/ffmpeg.exe', 'C:/Program Files/ffmpeg/bin/ffmpeg.exe', 'C:/tools/ffmpeg/bin/ffmpeg.exe']:
        if os.path.exists(p):
            print(f'找到 ffmpeg (固定路径): {p}')
            return p

    return None


ffmpeg_path = find_ffmpeg()

if ffmpeg_path:
    print(f'\n{"=" * 60}')
    print('运行: ffmpeg -list_devices true -f dshow -i dummy')
    print('=' * 60)

    result = subprocess.run(
        [ffmpeg_path, '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy'],
        capture_output=True,
        encoding='utf-8',
        errors='ignore',
        timeout=10
    )

    print('\n===== STDERR 输出 (ffmpeg 的设备列表在这里) =====')
    print(result.stderr)
    print('\n===== STDOUT 输出 =====')
    print(result.stdout if result.stdout else '(无输出)')
else:
    print('未找到 ffmpeg')
    print('请安装 ffmpeg: winget install Gyan.FFmpeg.Essentials')
    print('或下载: https://www.gyan.dev/ffmpeg/builds/')
