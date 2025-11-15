import serial
import time
import argparse
import threading

def calculate_frequency():
    """
    一个独立的线程，用于定期计算和打印接收频率。
    """
    global packet_count, last_print_time

    while not stop_event.is_set():
        # 记录当前时间
        current_time = time.perf_counter()
        
        # 计算时间差（秒）
        time_elapsed = current_time - last_print_time
        
        # 计算频率
        if time_elapsed > 0:
            frequency = packet_count / time_elapsed
            print(f"\r[频率监测] 接收频率: {frequency:.2f} Hz | 过去 {time_elapsed:.2f} 秒内接收了 {packet_count} 个数据包", end="")
        
        # 重置计数器和时间戳
        packet_count = 0
        last_print_time = current_time
        
        # 每1秒更新一次
        time.sleep(1)

def receive_and_print_data(port, baudrate=115200):
    """
    从串口接收数据并打印。
    """
    global packet_count, stop_event

    try:
        # 打开串口
        with serial.Serial(port, baudrate, timeout=1) as ser:
            print(f"成功打开串口: {port} (波特率: {baudrate})")
            print("开始监听数据... (按 Ctrl+C 停止)")
            print("-" * 50)

            # 启动频率计算线程
            frequency_thread = threading.Thread(target=calculate_frequency)
            frequency_thread.start()

            try:
                # 持续读取数据
                while not stop_event.is_set():
                    # 读取一行数据（直到换行符'\n'）
                    line = ser.readline()
                    #print(f"{line}")
                    
                    if line:
                        try:
                            # 解码字节流为字符串并去除首尾空白
                            data_str = line.decode('utf-8').strip()
                            
                            # 增加数据包计数
                            packet_count += 1
                            
                            # 打印接收到的数据（可选，数据量大时会刷屏）
                            #print(f"[接收数据] {data_str}")

                        except UnicodeDecodeError:
                            # 如果解码失败（可能是二进制数据），则打印原始字节
                            print(f"[接收数据] 无法解码的二进制数据: {line}")
            
            except KeyboardInterrupt:
                # 捕获 Ctrl+C，设置停止事件
                print("\n" + "-" * 50)
                print("用户手动停止接收。")
            finally:
                # 确保频率计算线程退出
                stop_event.set()
                frequency_thread.join()

    except serial.SerialException as e:
        print(f"串口错误: {e}")
    except Exception as e:
        print(f"发生未知错误: {e}")

# --- 全局变量和事件 ---
packet_count = 0
last_print_time = time.perf_counter()
stop_event = threading.Event()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="串口数据接收与频率分析工具")
    parser.add_argument("-p", "--port", default="COM3", help="串口名称 (默认: 'COM3')")
    parser.add_argument("-b", "--baudrate", type=int, default=115200, help="串口波特率 (默认: 115200)")
    
    args = parser.parse_args()
    
    receive_and_print_data(args.port, args.baudrate)
