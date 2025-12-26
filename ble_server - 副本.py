import asyncio
import websockets
import json
import sys
import io
from bleak import BleakScanner, BleakClient, BleakError
import traceback
import time
from queue import PriorityQueue
import threading
from collections import deque
import itertools  # 用于生成唯一序列号

# ================= 基础配置（解决编码和超时问题）=================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# 全局配置（针对Windows优化）
CONNECT_TIMEOUT = 30.0
SCAN_TIMEOUT = 1.0
MAX_RETRIES = 3
RETRY_DELAY = 2.0

# ========== 核心时间配置（匹配你的BLE设备）==========
BLE_PACKET_INTERVAL = 0.010    # BLE设备发送大包的间隔：10ms（100Hz）
EMG_GROUPS_PER_PACKET = 5      # 每个大包包含5组EMG数据
EMG_GROUP_INTERVAL = BLE_PACKET_INTERVAL / EMG_GROUPS_PER_PACKET  # 组内间隔：2ms

# 全局状态
server_state = {
    "connected_client": None,
    "bluetooth_client": None,
    "connected_mac": None,
    "scan_results": [],
    "notification_subscribed": False,
    "subscribe_uuid": "0000ff01-0000-1000-8000-00805f9b34fb",
    "connect_task": None,
    "msg_queue": PriorityQueue(),
    "processing_queue": False,
    # 新增：数据缓冲+时间戳校正
    "data_buffer": deque(maxlen=100),  # 大包缓冲队列
    "last_packet_timestamp": 0.0,     # 上一个大包的真实时间戳
    "packet_sequence": 0,             # 大包序列号（用于校正）
    "data_process_thread": None,      # 独立处理线程
    "stop_process_thread": False,
    "queue_seq": itertools.count(),   # 队列唯一序列号生成器
    "main_loop": None                 # 新增：保存主线程的事件循环
}

# 优先级定义
PRIORITY_HIGH = 1    # 控制指令
PRIORITY_LOW = 2     # 传感器数据

# ================= 高精度时间戳函数 =================
def get_monotonic_time():
    """
    获取单调递增时间（不受系统时间调整影响）
    精度：μs级（10^-6秒），足够满足2ms间隔的需求
    """
    return time.monotonic()

def get_utc_time():
    """获取UTC时间戳（用于业务记录）"""
    return time.time()

# ================= 时间戳校正逻辑（核心适配你的场景）=================
def correct_packet_timestamp(raw_ts):
    """
    校正大包的时间戳：保证相邻大包间隔稳定在10ms
    raw_ts: 回调中采集的原始时间戳
    return: 校正后的大包基准时间戳
    """
    # 首次采集，初始化
    if server_state["last_packet_timestamp"] == 0.0:
        server_state["last_packet_timestamp"] = raw_ts
        server_state["packet_sequence"] = 0
        return raw_ts
    
    # 计算该大包的预期时间戳（基于上一个包+10ms）
    expected_ts = server_state["last_packet_timestamp"] + (server_state["packet_sequence"] + 1) * BLE_PACKET_INTERVAL
    
    # 偏差阈值：超过20ms则用预期时间戳（避免跳变），否则用原始时间戳
    if abs(raw_ts - expected_ts) > 0.020:
        corrected_ts = expected_ts
        #print(f"[校正] 原始{raw_ts:.9f} → 预期{corrected_ts:.9f}（偏差{abs(raw_ts-expected_ts)*1000:.1f}ms）", file=sys.stderr)
    else:
        corrected_ts = raw_ts
    
    # 更新状态
    server_state["packet_sequence"] += 1
    return round(corrected_ts, 9)

def generate_group_timestamps(packet_base_ts):
    """
    为大包内的5组EMG数据生成2ms间隔的精确时间戳
    packet_base_ts: 大包的基准时间戳（校正后）
    return: [ts1, ts2, ts3, ts4, ts5]（每组间隔2ms）
    """
    return [
        round(packet_base_ts + i * EMG_GROUP_INTERVAL, 9)
        for i in range(EMG_GROUPS_PER_PACKET)
    ]

# ================= 调试日志 =================
def debug_log(message):
    print(f"[ble_server] {message}\n", file=sys.stderr)

# ================= 独立数据处理线程（避免阻塞回调）=================
def data_process_worker():
    """
    独立线程处理缓冲的大包数据
    职责：将数据加入消息队列，避免阻塞蓝牙回调
    """
    while not server_state["stop_process_thread"]:
        try:
            if server_state["data_buffer"] and server_state["connected_client"]:
                # 取出缓冲的大包数据
                packet_data = server_state["data_buffer"].popleft()
                
                # 低优先级加入消息队列（传感器数据）
                add_message_to_queue(PRIORITY_LOW, "sensor_data", packet_data)
            
            # 5ms轮询（低于2ms，保证不丢数据）
            time.sleep(0.005)
        except Exception as e:
            debug_log(f"数据处理线程异常: {str(e)}")
            traceback.print_exc()

# ================= 消息队列处理（优化调度）=================
async def process_message_queue():
    """异步处理消息队列，优先高优先级，不饿死低优先级"""
    if server_state["processing_queue"]:
        return
    server_state["processing_queue"] = True
    
    try:
        # 先处理所有高优先级消息（控制指令）
        high_prio_processed = False
        while not server_state["msg_queue"].empty() and server_state["msg_queue"].queue[0][0] == PRIORITY_HIGH:
            # 匹配新的元组结构 (priority, seq, msg_type, data, ws)
            prio, seq, msg_type, data, ws = server_state["msg_queue"].get()
            try:
                if msg_type == "control_response" and ws:
                    await ws.send(json.dumps(data))
            except Exception as e:
                debug_log(f"发送高优先级消息失败: {str(e)}")
            finally:
                server_state["msg_queue"].task_done()
            high_prio_processed = True
        
        # 处理一个低优先级消息（传感器数据）
        if not high_prio_processed and not server_state["msg_queue"].empty():
            # 匹配新的元组结构
            prio, seq, msg_type, data, ws = server_state["msg_queue"].get()
            try:
                if msg_type == "sensor_data" and server_state["connected_client"]:
                    await server_state["connected_client"].send(json.dumps(data))
            except Exception as e:
                debug_log(f"发送传感器数据失败: {str(e)}")
            finally:
                server_state["msg_queue"].task_done()
                await asyncio.sleep(0.001)  # 让出CPU
    finally:
        server_state["processing_queue"] = False

def add_message_to_queue(priority, msg_type, data, websocket=None):
    """添加消息到队列，低优先级队列满时丢弃旧数据"""
    try:
        # 低优先级队列保护（避免内存溢出）
        if priority == PRIORITY_LOW and server_state["msg_queue"].qsize() > 1000:
            try:
                # 匹配新的元组结构
                old_prio, old_seq, old_type, old_data, old_ws = server_state["msg_queue"].get_nowait()
                if old_prio == PRIORITY_LOW:
                    debug_log(f"低优先级队列满，丢弃旧数据（队列长度：{server_state['msg_queue'].qsize()}）")
                else:
                    server_state["msg_queue"].put((old_prio, old_seq, old_type, old_data, old_ws))
            except Exception as e:
                debug_log(f"清理旧数据失败: {str(e)}")
                pass
        
        # 添加唯一序列号，避免同优先级比较后续元素
        seq = next(server_state["queue_seq"])
        # 队列元素改为 (priority, seq, msg_type, data, websocket)
        server_state["msg_queue"].put((priority, seq, msg_type, data, websocket))
        
        # 核心修复：跨线程提交异步任务
        if server_state["connected_client"] and server_state["main_loop"]:
            # 在主线程的事件循环中提交任务（线程安全）
            asyncio.run_coroutine_threadsafe(
                process_message_queue(),
                server_state["main_loop"]
            )
    except Exception as e:
        debug_log(f"添加消息到队列失败: {str(e)}")
        traceback.print_exc()

# ================= 蓝牙核心逻辑 =================
# 蓝牙数据回调（最轻量化操作）
def handle_bluetooth_notification(sender, data):
    """
    蓝牙通知回调函数（核心要求：快！轻！）
    仅做3件事：
    1. 立即采集原始时间戳（回调执行第一时间）
    2. 解析大包数据（5组EMG）
    3. 校正时间戳 + 生成组内2ms间隔时间戳
    4. 放入缓冲队列（不阻塞回调）
    """
    try:
        # ========== 步骤1：立即采集原始时间戳（关键！）==========
        raw_packet_ts = get_monotonic_time()
        
        # ========== 步骤2：轻量化解析大包数据 ==========
        hex_data = data.hex().upper()
        header_len = 7 * 2    # 头部14个字符（7字节）
        footer_len = 2        # 尾部2个字符（1字节）
        
        # 数据合法性检查
        if len(hex_data) < header_len + footer_len:
            debug_log(f"数据长度异常：{len(hex_data)}（最小需要{header_len+footer_len}）")
            return
        
        # 提取EMG数据部分（去掉头/尾）
        emg_raw = hex_data[header_len:-footer_len]
        # 分割为5组（每组64字符）
        emg_groups = [emg_raw[i:i+64] for i in range(0, len(emg_raw), 64)]
        
        # 检查组数是否为5
        if len(emg_groups) != EMG_GROUPS_PER_PACKET:
            debug_log(f"EMG组数异常：实际{len(emg_groups)}组，预期{EMG_GROUPS_PER_PACKET}组")
            return
        
        # ========== 步骤3：时间戳校正 ==========
        # 校正大包基准时间戳（保证10ms间隔）
        corrected_packet_ts = correct_packet_timestamp(raw_packet_ts)
        # 生成组内2ms间隔的时间戳
        group_timestamps = generate_group_timestamps(corrected_packet_ts)
        
        # ========== 步骤4：构造最终数据 ==========
        output = {
            "type": "emg_packet",          # 标识为EMG大包
            "packet_sequence": server_state["packet_sequence"],  # 大包序列号
            "packet_raw_ts": round(raw_packet_ts, 9),            # 大包原始时间戳
            "packet_corrected_ts": corrected_packet_ts,          # 大包校正时间戳
            "emg_group_interval_ms": EMG_GROUP_INTERVAL * 1000,  # 组内间隔（ms）
            "timestamp_array": group_timestamps,                 # 5组数据的精确时间戳（2ms间隔）
            "big_bag_raw_data": emg_groups,                      # 5组EMG原始数据
            "utc_ts": round(get_utc_time(), 9)                   # UTC时间（用于跨设备同步）
        }
        
        # ========== 步骤5：放入缓冲队列（不阻塞回调）==========
        server_state["data_buffer"].append(output)
        
    except Exception as e:
        error_msg = f"处理蓝牙数据失败: {str(e)}"
        debug_log(error_msg)
        debug_log(traceback.format_exc())

# 蓝牙扫描
async def scan_bluetooth_devices(websocket):
    try:
        debug_log(f"开始扫描蓝牙设备（超时{SCAN_TIMEOUT}秒）...")
        devices = await BleakScanner.discover(
            timeout=SCAN_TIMEOUT,
            return_adv=True,
            scanning_mode="active"
        )
        
        result = []
        for dev, adv in devices.values():
            if dev.address and not any(d['mac'] == dev.address for d in result):
                result.append({
                    "mac": dev.address.upper(),
                    "name": dev.name or adv.local_name or "未知设备",
                    "rssi": adv.rssi if adv.rssi is not None else None,
                    "manufacturer": str(adv.manufacturer_data)[:50]
                })
        
        server_state["scan_results"] = result
        debug_log(f"扫描完成，找到{len(result)}个设备")
        
        # 高优先级发送扫描结果
        add_message_to_queue(PRIORITY_HIGH, "control_response", {
            "action": "scan_result",
            "devices": result,
            "count": len(result)
        }, websocket)
        
        return result
    
    except Exception as e:
        error_msg = f"扫描蓝牙设备失败: {str(e)}"
        debug_log(error_msg)
        debug_log(traceback.format_exc())
        add_message_to_queue(PRIORITY_HIGH, "control_response", {
            "action": "error",
            "message": error_msg
        }, websocket)
        return []

# 蓝牙设备连接
async def connect_bluetooth_device(mac_address, websocket):
    mac_address = mac_address.upper()
    debug_log(f"开始连接设备 {mac_address}（最大重试{MAX_RETRIES}次，超时{CONNECT_TIMEOUT}秒）")

    if server_state["bluetooth_client"]:
        await disconnect_bluetooth_device(websocket)
    
    retry_count = 0
    final_result = None
    
    while retry_count < MAX_RETRIES:
        try:
            debug_log(f"\n第{retry_count+1}/{MAX_RETRIES}次连接尝试...")
            # 发送连接中状态
            add_message_to_queue(PRIORITY_HIGH, "control_response", {
                "action": "connect_result",
                "success": None,
                "message": f"正在进行第{retry_count+1}/{MAX_RETRIES}次连接尝试...",
                "mac": mac_address
            }, websocket)
            
            # 查找设备
            device = await BleakScanner.find_device_by_address(mac_address, timeout=5.0)
            if not device:
                retry_count += 1
                error_msg = f"未找到设备 {mac_address}，{RETRY_DELAY}秒后重试..."
                debug_log(error_msg)
                add_message_to_queue(PRIORITY_HIGH, "control_response", {
                    "action": "connect_result",
                    "success": None,
                    "message": error_msg,
                    "mac": mac_address
                }, websocket)
                await asyncio.sleep(RETRY_DELAY)
                continue
            
            # 连接设备
            async with BleakClient(device, timeout=CONNECT_TIMEOUT) as client:
                if client.is_connected:
                    debug_log(f"成功连接到 {device.address} ，正在获取服务信息...")
                    server_state["bluetooth_client"] = client
                    server_state["connected_mac"] = mac_address

                    # 获取服务/特征信息
                    service_info = []
                    for service in client.services:
                        debug_log(f"服务 UUID: {service.uuid}")
                        for char in service.characteristics:
                            props = ", ".join(char.properties)
                            debug_log(f"  特征值 UUID: {char.uuid} | 属性: {props}")
                            service_info.append({
                                "service_uuid": service.uuid,
                                "char_uuid": char.uuid,
                                "properties": props
                            })
                    
                    # 订阅目标特征
                    found = False
                    for service in client.services:
                        for char in service.characteristics:
                            if char.uuid == server_state["subscribe_uuid"]:
                                await client.start_notify(char.uuid, handle_bluetooth_notification)
                                debug_log(f"成功订阅特征: {char.uuid}（监听10ms间隔的EMG大包）")
                                server_state["notification_subscribed"] = True
                                found = True
                                break
                        if found:
                            break
                    
                    # 构造连接结果
                    device_info = next((dev for dev in server_state["scan_results"] if dev["mac"] == mac_address), None)
                    final_result = {
                        "success": True,
                        "message": "连接并订阅成功" if found else "连接成功，但未找到指定特征UUID",
                        "mac": mac_address,
                        "rssi": device_info["rssi"] if device_info else None,
                        "service_info": service_info,
                        "config": {
                            "packet_interval_ms": BLE_PACKET_INTERVAL * 1000,
                            "groups_per_packet": EMG_GROUPS_PER_PACKET,
                            "group_interval_ms": EMG_GROUP_INTERVAL * 1000
                        }
                    }

                    # 发送连接成功结果
                    add_message_to_queue(PRIORITY_HIGH, "control_response", {
                        "action": "connect_result",
                        **final_result
                    }, websocket)
                    
                    # 保持连接，持续接收数据
                    try:
                        debug_log(f"设备连接成功，开始接收EMG大包（每{BLE_PACKET_INTERVAL*1000}ms1个，每组{EMG_GROUP_INTERVAL*1000}ms）...")
                        while server_state["notification_subscribed"] and client.is_connected:
                            await asyncio.sleep(1)
                    finally:
                        # 取消订阅
                        if found and server_state["notification_subscribed"]:
                            await client.stop_notify(server_state["subscribe_uuid"])
                            server_state["notification_subscribed"] = False
                            debug_log(f"已取消订阅特征: {server_state['subscribe_uuid']}")
                else:
                    retry_count += 1
                    msg = f"连接失败（设备未响应），{RETRY_DELAY}秒后重试..."
                    debug_log(msg)
                    add_message_to_queue(PRIORITY_HIGH, "control_response", {
                        "action": "connect_result",
                        "success": None,
                        "message": msg,
                        "mac": mac_address
                    }, websocket)
                    await asyncio.sleep(RETRY_DELAY)
                return final_result
            
        except TimeoutError:
            retry_count += 1
            error_msg = f"连接超时（{CONNECT_TIMEOUT}秒）"
            debug_log(error_msg)
            add_message_to_queue(PRIORITY_HIGH, "control_response", {
                "action": "connect_result",
                "success": None,
                "message": f"{error_msg}，{RETRY_DELAY}秒后进行第{retry_count+1}次重试...",
                "mac": mac_address
            }, websocket)
            await asyncio.sleep(RETRY_DELAY)
        except BleakError as e:
            retry_count += 1
            error_msg = f"Bleak蓝牙错误: {str(e)}"
            debug_log(error_msg)
            if "pairing" in str(e).lower():
                error_msg += "（提示：设备可能需要配对，请先在系统蓝牙设置中配对！）"
            add_message_to_queue(PRIORITY_HIGH, "control_response", {
                "action": "connect_result",
                "success": None,
                "message": error_msg,
                "mac": mac_address
            }, websocket)
            await asyncio.sleep(RETRY_DELAY)
        except Exception as e:
            retry_count += 1
            error_msg = f"连接异常: {str(e)}"
            debug_log(error_msg)
            debug_log(traceback.format_exc())
            add_message_to_queue(PRIORITY_HIGH, "control_response", {
                "action": "connect_result",
                "success": None,
                "message": error_msg,
                "mac": mac_address
            }, websocket)
            await asyncio.sleep(RETRY_DELAY)
    
    # 所有重试失败
    final_result = {
        "success": False,
        "message": f"连接失败（已重试{MAX_RETRIES}次），请检查设备是否开启、在范围内或已配对",
        "mac": mac_address
    }
    add_message_to_queue(PRIORITY_HIGH, "control_response", {
        "action": "connect_result",
        **final_result
    }, websocket)
    return final_result

# 断开蓝牙连接
async def disconnect_bluetooth_device(websocket):
    """安全断开蓝牙连接"""
    try:
        result = {
            "success": True,
            "message": "断开连接成功",
            "mac": server_state["connected_mac"]
        }
        
        if server_state["bluetooth_client"]:
            client = server_state["bluetooth_client"]
            mac = server_state["connected_mac"]
            
            if client.is_connected:
                if server_state["notification_subscribed"]:
                    try:
                        await client.stop_notify(server_state["subscribe_uuid"])
                        debug_log(f"已取消订阅特征: {server_state['subscribe_uuid']}")
                    except:
                        pass
            
            server_state["bluetooth_client"] = None
            server_state["notification_subscribed"] = False
            debug_log(f"成功断开设备 {mac} 连接")
        
        # 重置时间戳校正状态
        server_state["last_packet_timestamp"] = 0.0
        server_state["packet_sequence"] = 0
        
        disconnected_mac = server_state["connected_mac"]
        server_state["connected_mac"] = None
        server_state["connect_task"] = None
        result["mac"] = disconnected_mac
        
        # 发送断开结果
        add_message_to_queue(PRIORITY_HIGH, "control_response", {
            "action": "disconnect_result",
            **result
        }, websocket)
        
        return result
    
    except Exception as e:
        error_msg = f"断开连接失败: {str(e)}"
        debug_log(error_msg)
        debug_log(traceback.format_exc())
        
        result = {
            "success": False,
            "message": str(e),
            "mac": server_state["connected_mac"]
        }
        add_message_to_queue(PRIORITY_HIGH, "control_response", {
            "action": "disconnect_result",
            **result
        }, websocket)
        return result

# ================= WebSocket客户端处理 =================
async def handle_client(websocket):
    server_state["connected_client"] = websocket
    debug_log("WebSocket客户端已连接")
    
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                debug_log(f"收到客户端消息: {data}")

                # 扫描设备
                if data.get("action") == "scan":
                    asyncio.create_task(scan_bluetooth_devices(websocket))

                # 连接设备（防止重复请求）
                elif data.get("action") == "connect" and data.get("mac"):
                    mac = data["mac"].upper()
                    
                    if server_state["connect_task"] and not server_state["connect_task"].done():
                        debug_log("当前正有连接任务，请稍后再试")
                        add_message_to_queue(PRIORITY_HIGH, "control_response", {
                            "action": "connect_result",
                            "success": False,
                            "message": "已有连接任务正在执行，请稍后再试",
                            "mac": mac
                        }, websocket)
                        continue
                    
                    server_state["connect_task"] = asyncio.create_task(connect_bluetooth_device(mac, websocket))

                # 断开连接
                elif data.get("action") == "disconnect":
                    asyncio.create_task(disconnect_bluetooth_device(websocket))

            except json.JSONDecodeError:
                error_msg = "无效的JSON消息格式"
                debug_log(error_msg)
                add_message_to_queue(PRIORITY_HIGH, "control_response", {
                    "action": "error",
                    "message": error_msg
                }, websocket)

            except Exception as e:
                error_msg = f"处理客户端消息失败: {str(e)}"
                debug_log(error_msg)
                debug_log(traceback.format_exc())
                add_message_to_queue(PRIORITY_HIGH, "control_response", {
                    "action": "error",
                    "message": str(e)
                }, websocket)
    finally:
        debug_log("WebSocket客户端已断开连接")
        server_state["connected_client"] = None
        # 断开蓝牙连接
        if server_state["bluetooth_client"]:
            asyncio.create_task(disconnect_bluetooth_device(websocket))

# ================= 服务启动 =================
async def main():
    try:
        # 保存主线程的事件循环（核心修复）
        server_state["main_loop"] = asyncio.get_running_loop()
        
        # 启动独立的数据处理线程
        server_state["stop_process_thread"] = False
        server_state["data_process_thread"] = threading.Thread(target=data_process_worker, daemon=True)
        server_state["data_process_thread"].start()
        debug_log("数据处理线程已启动")
        
        # 启动WebSocket服务
        async with websockets.serve(handle_client, "localhost", 8766):
            debug_log("服务器已启动，监听端口8766")
            debug_log(f"===== 配置信息 =====")
            debug_log(f"BLE大包间隔: {BLE_PACKET_INTERVAL*1000}ms")
            debug_log(f"每组EMG间隔: {EMG_GROUP_INTERVAL*1000}ms")
            debug_log(f"每包EMG组数: {EMG_GROUPS_PER_PACKET}组")
            debug_log(f"连接超时: {CONNECT_TIMEOUT}秒 | 最大重试: {MAX_RETRIES}次")
            debug_log(f"订阅UUID: {server_state['subscribe_uuid']}")
            debug_log(f"====================")
            await asyncio.Future()  # 保持服务运行
    finally:
        # 停止数据处理线程
        server_state["stop_process_thread"] = True
        if server_state["data_process_thread"]:
            server_state["data_process_thread"].join(timeout=2.0)
        debug_log("数据处理线程已停止")

if __name__ == "__main__":
    try:
        #print("______main______")
        asyncio.run(main())
    except KeyboardInterrupt:
        debug_log("服务被用户手动中断")
    except Exception as e:
        error_msg = f"服务启动失败: {str(e)}"
        debug_log(error_msg)
        debug_log(traceback.format_exc())