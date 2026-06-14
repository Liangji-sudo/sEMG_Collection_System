import subprocess
import sys

print("检查 ffmpeg 是否可用...")

try:
    result = subprocess.run(
        ['ffmpeg', '-version'],
        capture_output=True,
        text=True,
        timeout=5
    )
    print("✓ ffmpeg 可用！")
    print(result.stdout.split('\n')[0])
except FileNotFoundError:
    print("✗ ffmpeg 未找到")
    print("\n可能的解决方案：")
    print("1. 重新打开终端（让新的 PATH 生效）")
    print("2. 或者重启电脑")
    print("3. 或者在 camera_server.py 中使用完整路径")
except Exception as e:
    print(f"✗ 错误: {e}")
