import sys
import time
import random
import signal

# 确保输出实时刷新
sys.stdout.flush()

# 处理退出信号
def handle_exit(signal, frame):
    print("\n模拟数据生成器已停止", file=sys.stderr)
    sys.exit(0)

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

# 生成16通道EMG模拟数据
def generate_emg_data():
    # 生成16个模拟EMG值，范围在0-1000之间
    return [random.uniform(0, 100) for _ in range(16)]

try:
    print("EMG模拟数据生成器已启动，开始发送数据...", file=sys.stderr)
    # 模拟较高的数据速率（约1000Hz）
    while True:
        # 生成数据
        emg_data = generate_emg_data()
        # 转换为CSV格式字符串
        data_str = ','.join(f"{x:.2f}" for x in emg_data)
        # 输出数据（通过stdout发送给Node.js）
        print(data_str)
        # 刷新缓冲区
        sys.stdout.flush()
        # 控制数据发送速率（约1ms间隔，1000Hz）
        time.sleep(0.001)
        
except Exception as e:
    print(f"发生错误: {str(e)}", file=sys.stderr)
    sys.exit(1)
