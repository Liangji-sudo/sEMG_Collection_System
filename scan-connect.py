import asyncio
import sys
import json
from datetime import datetime, timedelta
from bleak import BleakScanner, BleakClient

import io

# 强制 stdout/stderr 使用 UTF-8 编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 固定蓝牙设备MAC地址
FIXED_MAC_ADDRESS = "dc:b4:d9:1f:52:be"

# 心跳相关特征UUID
HEART_RATE_INDICATE_UUID2 = "0000ff01-0000-1000-8000-00805f9b34fb"

# 新增统计相关变量
last_receive_time = None
current_second_packets = 0
current_second_start = None
interval_list = []
MAX_INTERVALS = 100

def parse_emg_frame(frame_hex):
    """解析单帧32字节HEX为16通道EMG值（uint32_t，小端模式）"""
    emg_values = []
    # 每2字节一组，共16组（32字节）
    for i in range(0, 32, 2):
        # 取2字节，按小端模式转换为uint32_t（补两个0字节）
        two_bytes = frame_hex[i:i+4]  # 注意：HEX字符串中2个字符代表1字节
        if len(two_bytes) < 4:
            emg_values.append(0)
            continue
        # 小端模式：低字节在前，高字节在后，补两个0字节凑成4字节
        uint32_hex = two_bytes + "0000"  # 例如 "0201" → "02010000"
        emg_values.append(int(uint32_hex, 16))  # 转换为整数
    return emg_values

def notification_handler(sender, data):
    """处理BLE数据，解析为5帧并发送JSON"""
    global last_receive_time, current_second_packets, current_second_start, interval_list
    
    current_time = datetime.now()
    hex_data = data.hex().upper()
    timestamp = current_time.timestamp()  # 时间戳（秒级，带小数）

    # 计算时间间隔
    time_interval = None
    if last_receive_time is not None:
        time_diff = current_time - last_receive_time
        time_interval = time_diff.total_seconds()
        interval_list.append(time_interval)
        if len(interval_list) > MAX_INTERVALS:
            interval_list.pop(0)
    last_receive_time = current_time

    # 统计每秒包数
    if current_second_start is None:
        current_second_start = current_time.replace(microsecond=0)
        current_second_packets = 1
    else:
        if current_time >= current_second_start + timedelta(seconds=1):
            current_second_start = current_time.replace(microsecond=0)
            current_second_packets = 1
        else:
            current_second_packets += 1

    # 解析原始HEX数据（168字节）
    try:
        # 校验包头（1字节：AA）
        if len(hex_data) != 336:  # 168字节 → 336个HEX字符
            print(f"数据长度错误: 预期336字符，实际{len(hex_data)}", file=sys.stderr)
            return
        if hex_data[:2] != "AA":
            print(f"包头错误: 预期AA，实际{hex_data[:2]}", file=sys.stderr)
            return

        # 提取5帧数据（每帧32字节 → 64个HEX字符）
        frames_hex = []
        # 跳过包头(2字符)、包id(8字符)、采样率(2)、电池(2) → 共14字符
        # 数据区从第14字符开始，每64字符一帧，共5帧
        data_start = 14
        for i in range(5):
            frame_start = data_start + i * 64
            frame_end = frame_start + 64
            frames_hex.append(hex_data[frame_start:frame_end])

        # 解析每帧为16通道EMG值
        frames_data = [parse_emg_frame(frame) for frame in frames_hex]

        # 发送JSON数据（一行一条）
        output = {
            "type": "emg",
            "timestamp": timestamp,
            "interval": time_interval,
            "packets_per_sec": current_second_packets,
            "frames": frames_data  # 5帧数据，每帧16通道
        }
        print(json.dumps(output))
        sys.stdout.flush()  # 强制刷新缓冲区

    except Exception as e:
        print(f"数据解析错误: {str(e)}", file=sys.stderr)

async def connect_to_device():
    mac_address = FIXED_MAC_ADDRESS
    print(f"正在扫描设备 {mac_address}...")
    device = await BleakScanner.find_device_by_address(mac_address, timeout=10.0)
    
    if not device:
        print(f"错误：未找到设备 {mac_address}。请确保设备已开启且在附近。")
        return
    
    print(f"成功找到设备: {device.name or '未知设备'} ({device.address})")
    
    try:
        async with BleakClient(device) as client:
            print(f"成功连接到 {device.address}")
            
            if client.is_connected:
                print("设备连接成功，正在获取服务信息...")
                
                print("\n设备服务和特征值：")
                for service in client.services:
                    print(f"服务 UUID: {service.uuid}")
                    for char in service.characteristics:
                        props = ", ".join(char.properties)
                        print(f"  特征值 UUID: {char.uuid} | 属性: {props}")
                
                print("\n开始订阅emg数据... (按 Ctrl+C 停止)")
                

                found2 = False
                for service in client.services:
                    for char in service.characteristics:
                        if char.uuid == HEART_RATE_INDICATE_UUID2:
                            await client.start_notify(char.uuid, notification_handler)
                            print(f"已订阅心跳特征: {char.uuid}")
                            found2 = True
                            break
                    if found2:
                        break
                if not found2:
                    print(f"未找到心跳特征: {HEART_RATE_INDICATE_UUID2}")
                
                try:
                    while client.is_connected:
                        await asyncio.sleep(1)
                except KeyboardInterrupt:
                    print("\n用户请求停止，正在取消订阅...")
                
                if found2:
                    await client.stop_notify(HEART_RATE_INDICATE_UUID2)
        
        print(f"\n已与 {device.address} 断开连接")
    
    except Exception as e:
        print(f"连接过程中发生错误: {e}")

async def main():
    # 直接连接固定MAC地址的设备，无需命令行参数
    await connect_to_device()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序被用户手动中断")
    except Exception as e:
        print(f"程序运行出错: {e}")