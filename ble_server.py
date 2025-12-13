# ble_server
# 作为websocket服务器，负责实现蓝牙连接，订阅消息，数据处理，数据/状态广播

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

# ================= 基础配置（解决编码和超时问题）=================
# 强制stdout/stderr使用UTF-8编码（解决中文乱码）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# 全局配置（针对Windows优化）
CONNECT_TIMEOUT = 30.0  # 延长连接超时到30秒（默认10秒太短）
SCAN_TIMEOUT = 1.0     # 延长扫描超时
MAX_RETRIES = 3         # 连接重试次数
RETRY_DELAY = 2.0       # 重试间隔（秒）

# 全局状态
server_state = {
    "connected_client": None, #python websocket_client：deviceSync
    "bluetooth_client": None, #as ble_client
    "connected_mac": None, #ble_server mac address
    "scan_results": [], #ble_device_scan result
    "notification_subscribed": False,   #notify后台订阅任务
    "subscribe_uuid": "0000ff01-0000-1000-8000-00805f9b34fb",  # 目标特征UUID
    "connect_task": None, # 存储当前连接任务
    "msg_queue": PriorityQueue(), # 优先级消息队列：1=高优先级（控制），2=低优先级（传感器）
    "processing_queue": False # 是否正在处理队列
}

# 优先级定义
PRIORITY_HIGH = 1    # 控制指令（扫描/连接/断开）
PRIORITY_LOW = 2     # 传感器数据（EMG）

# 获取秒级时间戳，精确到小数点后9位（纳秒级精度）
def get_sys_time():
    ns_timestamp = time.time_ns()
    s_timestamp = ns_timestamp / 1_000_000_000.0
    return round(s_timestamp, 9)

# 调试日志函数
def debug_log(message):
    print(f"[ble_server] {message}\n", file=sys.stderr)

# ================= 消息队列处理 =================
async def process_message_queue():
    """异步处理优先级消息队列"""
    if server_state["processing_queue"]:
        return
    server_state["processing_queue"] = True
    
    try:
        while not server_state["msg_queue"].empty():
            # 优先获取高优先级消息
            priority, msg_type, data, websocket = server_state["msg_queue"].get()
            
            if not server_state["connected_client"]:
                continue
                
            try:
                if msg_type == "control_response":
                    # 发送控制指令响应
                    await websocket.send(json.dumps(data))
                elif msg_type == "sensor_data":
                    # 发送传感器数据
                    await server_state["connected_client"].send(json.dumps(data))
            except Exception as e:
                debug_log(f"发送消息失败（优先级{priority}）: {str(e)}")
            finally:
                server_state["msg_queue"].task_done()
            # 高优先级消息处理后立即检查新的高优先级消息，低优先级消息每次处理后让出CPU
            if priority == PRIORITY_LOW:
                await asyncio.sleep(0.001)
    finally:
        server_state["processing_queue"] = False

def add_message_to_queue(priority, msg_type, data, websocket=None):
    """添加消息到优先级队列"""
    server_state["msg_queue"].put((priority, msg_type, data, websocket))
    # 触发队列处理
    if server_state["connected_client"]:
        asyncio.create_task(process_message_queue())

# ================= 蓝牙核心逻辑 =================
# 蓝牙扫描
async def scan_bluetooth_devices(websocket):
    try:
        debug_log(f"开始扫描蓝牙设备...")
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
        scan_result = {
            "action": "scan_result",
            "devices": result,
            "count": len(result)
        }
        add_message_to_queue(PRIORITY_HIGH, "control_response", scan_result, websocket)
        
        return result
    
    except Exception as e:
        error_msg = f"扫描蓝牙设备失败: {str(e)}"
        debug_log(error_msg)
        debug_log(traceback.format_exc())
        # 高优先级发送错误响应
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
            # 发送连接中状态（高优先级）
            add_message_to_queue(PRIORITY_HIGH, "control_response", {
                "action": "connect_result",
                "success": None,
                "message": f"正在进行第{retry_count+1}/{MAX_RETRIES}次连接尝试...",
                "mac": mac_address
            }, websocket)
            
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
            
            async with BleakClient(device) as client:
                if client.is_connected:
                    debug_log(f"成功连接到 {device.address} ，正在获取服务信息...")
                    server_state["bluetooth_client"] = client
                    server_state["connected_mac"] = mac_address

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
                                debug_log(f"成功订阅特征: {char.uuid}")
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
                        "service_info": service_info
                    }

                    # 高优先级发送连接成功结果
                    add_message_to_queue(PRIORITY_HIGH, "control_response", {
                        "action": "connect_result",
                        **final_result
                    }, websocket)
                    
                    # 保持连接
                    try:
                        debug_log("设备连接成功，持续接收数据...")
                        while server_state["notification_subscribed"] and client.is_connected:
                            await asyncio.sleep(1)
                    finally:
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

# 蓝牙数据处理回调函数（低优先级）
def handle_bluetooth_notification(sender, data):
    try:
        hex_data = data.hex().upper()
        header_len = 7 * 2
        footer_len = 2
        emg_data = hex_data[header_len:-footer_len]
        emg_data_groups = [emg_data[i:i+64] for i in range(0, len(emg_data), 64)]
        
        if len(emg_data_groups) != 5:
            raise ValueError(f"EMG数据组数异常，实际{len(emg_data_groups)}组，预期5组")
        
        output = {
            "type": "emg",
            "timestamp": get_sys_time(),
            "raw_data": emg_data_groups
        }
        
        # 低优先级添加传感器数据到队列
        add_message_to_queue(PRIORITY_LOW, "sensor_data", output)
        
    except Exception as e:
        error_msg = f"处理蓝牙数据失败: {str(e)}"
        debug_log(error_msg)
        debug_log(traceback.format_exc())

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
        
        disconnected_mac = server_state["connected_mac"]
        server_state["connected_mac"] = None
        server_state["connect_task"] = None
        result["mac"] = disconnected_mac
        
        # 高优先级发送断开结果
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

                # 1. 扫描设备（高优先级）
                if data.get("action") == "scan":
                    asyncio.create_task(scan_bluetooth_devices(websocket))

                # 2. 连接设备（高优先级，防止重复请求）
                elif data.get("action") == "connect" and data.get("mac"):
                    mac = data["mac"].upper()
                    
                    if server_state["connect_task"] and not server_state["connect_task"].done():
                        debug_log("precheck, 当前正有连接任务，请稍后再试")
                        add_message_to_queue(PRIORITY_HIGH, "control_response", {
                            "action": "connect_result",
                            "success": False,
                            "message": "已有连接任务正在执行，请稍后再试",
                            "mac": mac
                        }, websocket)
                        continue
                    
                    server_state["connect_task"] = asyncio.create_task(connect_bluetooth_device(mac, websocket))

                # 3. 断开蓝牙设备（高优先级）
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
    # 启动WebSocket服务
    async with websockets.serve(handle_client, "localhost", 8766):
        debug_log("服务器已启动，监听端口8766")
        debug_log(f"配置参数：连接超时{CONNECT_TIMEOUT}秒 | 最大重试{MAX_RETRIES}次 | 订阅UUID{server_state['subscribe_uuid']}")
        await asyncio.Future()  # 保持服务运行

if __name__ == "__main__":
    try:
        print("______main______")
        asyncio.run(main())
    except KeyboardInterrupt:
        debug_log("服务被用户手动中断")
    except Exception as e:
        error_msg = f"服务启动失败: {str(e)}"
        debug_log(error_msg)
        debug_log(traceback.format_exc())