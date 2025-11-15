import h5py
import serial
import time
import argparse

def send_emg_data_continuously(file_path, dataset_name, port, baudrate=115200):
    """
    从HDF5文件中持续读取EMG数据，并通过串口以2kHz频率发送。

    Args:
        file_path (str): HDF5文件路径。
        dataset_name (str): 包含EMG数据的数据集名称。
        port (str): 串口名称，例如 'COM3' 或 '/dev/ttyUSB0'。
        baudrate (int): 串口波特率，默认115200。
    """
    # --- 1. 配置参数 ---
    #sample_rate = 2000.0
    sample_rate = 1000.0
    send_period = 1.0 / sample_rate  # 发送周期，单位为秒 (0.0005秒 = 500微秒)
    current_index = 0
    
    # --- 2. 打开资源 ---
    try:
        # 打开HDF5文件
        with h5py.File(file_path, 'r') as f:
            print(f"成功打开HDF5文件: {file_path}")
            
            # 获取数据集
            if dataset_name not in f:
                print(f"错误: 数据集 '{dataset_name}' 在文件中未找到。")
                return
            
            data_set = f[dataset_name]
            total_samples = data_set.shape[0]
            print(f"找到数据集 '{dataset_name}', 共 {total_samples} 个样本。")

            # 获取字段名 (根据之前的对话，假设是复合类型)
            field_names = list(data_set.dtype.names)
            if not field_names:
                print("错误: 数据集不是复合类型，无法自动识别EMG字段。")
                return
            emg_field = field_names[0] # 假设第一个字段是EMG数据
            print(f"使用EMG字段: '{emg_field}'")

            # 打开串口
            with serial.Serial(port, baudrate, timeout=1) as ser:
                print(f"成功打开串口: {port} (波特率: {baudrate})")
                print(f"开始以 {sample_rate} Hz 频率发送数据... (按 Ctrl+C 停止)")
                
                # --- 3. 进入发送循环 ---
                while current_index < total_samples:
                    print(f"{current_index}/{total_samples}")
                    # 记录本轮循环的起始时间
                    start_time = time.perf_counter()

                    # 读取当前帧数据
                    try:
                        emg_data = data_set[current_index][emg_field]
                    except IndexError:
                        print("数据已发送完毕。")
                        break

                    # 格式化数据为字符串 (例如: "1.23,4.56,...,7.89\n")
                    # 为了提高发送效率，可以适当减少小数位数
                    data_str = ','.join(f'{x:.6f}' for x in emg_data) + '\n'
                    
                   
                    #header = b'\xAA\x55'  # 2字节帧头
    		        #data_bytes = struct.pack('16f', *data_str)
           	        #footer = b'\x0D\x0A'  # 2字节帧尾（可选）
        	        #frame = header + data_str + footer
                    
                    # 发送数据
                    ser.write(data_str.encode('utf-8'))
                    #ser.write(frame)
                    
                    # 更新索引
                    current_index += 1
                    
                    # 计算耗时并休眠
                    elapsed_time = time.perf_counter() - start_time
                    sleep_time = send_period - elapsed_time
                    
                    # 只有当计算出的休眠时间为正时才休眠
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                    # 如果耗时超过了发送周期，则打印一个警告
                    elif elapsed_time > send_period * 1.1: # 允许10%的误差
                        print(f"警告: 发送周期过长! 耗时 {elapsed_time*1000:.2f} ms > 预期 {send_period*1000:.2f} ms")

    except FileNotFoundError:
        print(f"错误: 无法找到文件 '{file_path}'。")
    except serial.SerialException as e:
        print(f"串口错误: {e}")
    except KeyboardInterrupt:
        print("\n发送被用户手动停止。")
    except Exception as e:
        print(f"发生未知错误: {e}")

if __name__ == "__main__":
    # 使用argparse让脚本更易用
    parser = argparse.ArgumentParser(description="HDF5 EMG数据串口发送模拟器")
    parser.add_argument("file", help="HDF5文件路径")
    parser.add_argument("-d", "--dataset", default="data", help="数据集中的EMG数据集名称 (默认: 'data')")
    parser.add_argument("-p", "--port", default="COM3", help="串口名称 (默认: 'COM3')")
    parser.add_argument("-b", "--baudrate", type=int, default=115200, help="串口波特率 (默认: 115200)")
    
    args = parser.parse_args()
    
    send_emg_data_continuously(args.file, args.dataset, args.port, args.baudrate)
