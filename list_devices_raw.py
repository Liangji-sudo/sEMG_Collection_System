import subprocess
import glob
from pathlib import Path

# 查找 ffmpeg
user_home = Path.home()
winget_pattern = user_home / 'AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe'
matches = glob.glob(str(winget_pattern))

if matches:
    ffmpeg_path = matches[0]
    print(f'使用 ffmpeg: {ffmpeg_path}\n')

    print('=' * 60)
    print('运行: ffmpeg -list_devices true -f dshow -i dummy')
    print('=' * 60)

    result = subprocess.run(
        [ffmpeg_path, '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy'],
        capture_output=True,
        encoding='utf-8',
        errors='ignore',  # 忽略编码错误
        timeout=10
    )

    print('\n===== STDERR 输出 (ffmpeg 的设备列表在这里) =====')
    print(result.stderr)
    print('\n===== STDOUT 输出 =====')
    print(result.stdout if result.stdout else '(无输出)')
else:
    print('未找到 ffmpeg')
