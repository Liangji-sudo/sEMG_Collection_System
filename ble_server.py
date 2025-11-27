# ble_server
# 作为websocket服务器，负责实现蓝牙连接，订阅消息，数据处理，数据/状态广播

import asyncio
import websockets
import json
import sys
import io
from bleak import BleakScanner, BleakClient, BleakError
import traceback

# ================= 基础配置（解决编码和超时问题）=================
# 强制stdout/stderr使用UTF-8编码（解决中文乱码）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# 全局配置（针对Windows优化）
CONNECT_TIMEOUT = 30.0  # 延长连接超时到30秒（默认10秒太短）
SCAN_TIMEOUT =5.0     # 延长扫描超时
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
    "connect_task": None # 存储当前连接任务
    #"disconnecting_flag": 0
}

# 调试日志函数
def debug_log(message):
    print(f"[ble_server] {message}\n", file=sys.stderr)
    #print(f"[ble_server] {message}")

# 蓝牙扫描
async def scan_bluetooth_devices():
    try:
        debug_log(f"开始扫描蓝牙设备...")
        
        # Windows下优化扫描参数：增加扫描时间，返回广播数据
        devices = await BleakScanner.discover(
            timeout=SCAN_TIMEOUT,
            return_adv=True,  # 返回广播数据，提高设备发现率
            scanning_mode="active"  # 主动扫描（适合BLE设备）
        )
        
        result = []
        for dev, adv in devices.values():
            if dev.address and not any(d['mac'] == dev.address for d in result):
                # 补充更多设备信息，方便调试
                result.append({
                    "mac": dev.address.upper(),  # MAC统一转为大写，避免大小写问题
                    "name": dev.name or adv.local_name or "未知设备",
                    "rssi": adv.rssi if adv.rssi is not None else None,
                    "manufacturer": str(adv.manufacturer_data)[:50]  # 制造商数据（截取前50字符）
                })
        
        server_state["scan_results"] = result
        debug_log(f"扫描完成，找到{len(result)}个设备")
        
        # 打印找到的设备列表（方便调试）
        for dev in result:
            debug_log(f"  - {dev['name']} | MAC: {dev['mac']} | RSSI: {dev['rssi']}")
        
        # 返回扫描的蓝牙设备列表
        return result
    
    except Exception as e:
        error_msg = f"扫描蓝牙设备失败: {str(e)}"
        debug_log(error_msg)
        debug_log(traceback.format_exc())
        return []

# 蓝牙设备连接
async def connect_bluetooth_device(mac_address, websocket):
    mac_address = mac_address.upper()  # 统一MAC为大写，避免匹配问题
    debug_log(f"开始连接设备 {mac_address}（最大重试{MAX_RETRIES}次，超时{CONNECT_TIMEOUT}秒）")

    # 先断开现有蓝牙连接
    if server_state["bluetooth_client"]:
        await disconnect_bluetooth_device()
    
    retry_count = 0
    final_result = None
    
    while retry_count < MAX_RETRIES:
        try:
            # 每次重试前重新扫描设备（确保设备在范围内）
            debug_log(f"\n第{retry_count+1}/{MAX_RETRIES}次连接尝试...")

            # 等待自动连接， 阻塞
            device = await BleakScanner.find_device_by_address(mac_address, timeout=5.0)
            
            if not device:
                retry_count += 1
                error_msg = f"未找到设备 {mac_address}，{RETRY_DELAY}秒后重试..."
                debug_log(error_msg)
                await asyncio.sleep(RETRY_DELAY)
                continue
            
            debug_log(f"找到设备: {device.name or '未知设备'} | MAC: {device.address}")
            
            async with BleakClient(device) as client:
                if client.is_connected:
                    debug_log("成功连接到 {device.address} ，正在获取服务信息...")
                    server_state["bluetooth_client"] = client

                    service_info = []
                    for service in client.services:
                        debug_log("服务 UUID: {service.uuid}")
                        for char in service.characteristics:
                            props = ", ".join(char.properties)
                            debug_log("  特征值 UUID: {char.uuid} | 属性: {props}")

                    # 订阅目标特征
                    found = False
                    for service in client.services:
                        for char in service.characteristics:
                            if char.uuid == server_state["subscribe_uuid"]:
                                # 订阅通知， 开启处理任务， 指定接受回调函数
                                await client.start_notify(char.uuid, handle_bluetooth_notification)
                                debug_log(f"成功订阅特征: {char.uuid}")
                                server_state["notification_subscribed"] = True
                                found = True
                                break
                        if found:
                            break
                    
                    if not found:
                        final_result = {
                            "success": True,
                            "message": "连接成功，但未找到指定特征UUID",
                            "mac": mac_address,
                            "rssi": device.rssi if hasattr(device, 'rssi') else None,
                            "service_info": service_info
                        }
                    else:
                        final_result = {
                            "success": True,
                            "message": "连接并订阅成功",
                            "mac": mac_address,
                            "rssi": device.rssi if hasattr(device, 'rssi') else None,
                            "service_info": service_info
                        }
                    
                    #发送成功蓝牙连接结果到deviceSync
                    await websocket.send(json.dumps({
                        "action": "connect_result",
                        **final_result
                    }))
                    
                    # 保持连接（直到断开）
                    try:
                        debug_log("设备连接成功，一直接受数据，直到点击断开连接")
                        # 只要蓝牙连接还存在，该接受后台任务就一直存在，指导ble_server收到disconnect,并清除server_state["bluetooth_client"]
                        #while server_state["bluetooth_client"]:
                        #while client.is_connected:
                        while(server_state["notification_subscribed"]):
                            await asyncio.sleep(1)

                    except KeyboardInterrupt:
                        debug_log("用户请求停止，正在断开连接...")
                    finally:
                
                        if found:
                            if(server_state["notification_subscribed"]):
                                await client.stop_notify(server_state["subscribe_uuid"])
                                server_state["notification_subscribed"] = False
                                debug_log(f"已与设备 {device.address} 断开notify")
                else:
                    retry_count += 1
                    debug_log(f"连接失败（设备未响应），{RETRY_DELAY}秒后重试...")
                    await asyncio.sleep(RETRY_DELAY)
                return final_result
            
        except TimeoutError:
            retry_count += 1
            error_msg = f"连接超时（{CONNECT_TIMEOUT}秒）"
            debug_log(error_msg)
            if retry_count < MAX_RETRIES:
                debug_log(f"{RETRY_DELAY}秒后进行第{retry_count+1}次重试...")
                await asyncio.sleep(RETRY_DELAY)
        except BleakError as e:
            retry_count += 1
            error_msg = f"Bleak蓝牙错误: {str(e)}"
            debug_log(error_msg)
            if "pairing" in str(e).lower():
                debug_log("提示：设备可能需要配对，请先在系统蓝牙设置中配对设备！")
            await asyncio.sleep(RETRY_DELAY)
        except Exception as e:
            retry_count += 1
            error_msg = f"连接异常: {str(e)}"
            debug_log(error_msg)
            debug_log(traceback.format_exc())
            await asyncio.sleep(RETRY_DELAY)
    

    # 所有重试都失败
    final_result = {
        "success": False,
        "message": f"连接失败（已重试{MAX_RETRIES}次），请检查设备是否开启、在范围内或已配对",
        "mac": mac_address
    }

    await websocket.send(json.dumps({
        "action": "connect_result",
        **final_result
    }))
    return final_result

# 蓝牙数据处理回调函数
def handle_bluetooth_notification(sender, data):
    try:
        hex_data = data.hex().upper()
        timestamp = asyncio.get_event_loop().time()
        
        output = {
            "type": "emg",
            "timestamp": timestamp,
            "raw_data": hex_data
            #"sender_uuid": str(sender)
        }
        
        # 广播转发给WebSocket客户端
        if server_state["connected_client"]:
            asyncio.create_task(
                server_state["connected_client"].send(json.dumps(output))
            )
        
    except Exception as e:
        error_msg = f"处理蓝牙数据失败: {str(e)}"
        debug_log(error_msg)
        debug_log(traceback.format_exc())

# 断开蓝牙连接
async def disconnect_bluetooth_device(websocket):
    """安全断开蓝牙连接"""
    try:
        # 如果当前存在蓝牙连接
        if server_state["bluetooth_client"]:
            client = server_state["bluetooth_client"]
            mac = server_state["connected_mac"]
            
            if client.is_connected:
                # 取消订阅
                if server_state["notification_subscribed"]:
                    try:
                        await client.stop_notify(server_state["subscribe_uuid"])
                        debug_log(f"已取消订阅特征: {server_state['subscribe_uuid']}")
                    except:
                        pass
                
                # 断开连接, do nothing, because async with will disconnect the connect
                # await client.disconnect()
                debug_log(f"成功断开设备 {mac} 连接")
            
            server_state["bluetooth_client"] = None
            server_state["notification_subscribed"] = False
        
        disconnected_mac = server_state["connected_mac"]
        server_state["connected_mac"] = None
        server_state["connect_task"] = None  # 重置连接任务
        
        result = {
            "success": True,
            "message": "断开连接成功",
            "mac": disconnected_mac
        }

        await websocket.send(json.dumps({
            "action": "disconnect_result",
            **result
        }))
        
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

        await websocket.send(json.dumps({
            "action": "disconnect_result",
            **result
        }))
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

                # 1. 扫描设备， 阻塞
                if data.get("action") == "scan":
                    
                    devices = await scan_bluetooth_devices()
                    scan_result = {
                        "action": "scan_result",
                        "devices": devices,
                        "count": len(devices)
                    }

                    # 将扫描结果走websocket协议发送出去
                    await websocket.send(json.dumps(scan_result))
                

                # 2. 连接设备（防止重复请求）
                elif data.get("action") == "connect" and data.get("mac"):
                    mac = data["mac"].upper()
                    
                    # 检查是否已有连接任务
                    if server_state["connect_task"] and not server_state["connect_task"].done():
                        debug_log("precheck, 当前正有连接任务，请稍后再试")
                        response = {
                            "action": "connect_result",
                            "success": False,
                            "message": "已有连接任务正在执行，请稍后再试",
                            "mac": mac
                        }

                        # 广播 连接结果
                        await websocket.send(json.dumps(response))
                        continue
                    
                    # 启动连接蓝牙后台任务，非阻塞
                    server_state["connect_task"] = asyncio.create_task(connect_bluetooth_device(mac, websocket))
                    
                    # 发送"连接中"状态
                    connecting_msg = {
                        "action": "connect_result",
                        "success": None,
                        "message": f"正在进行第1/{MAX_RETRIES}次连接尝试...",
                        "mac": mac
                    }
                    await websocket.send(json.dumps(connecting_msg))
                

                # 3. 断开蓝牙设备，阻塞
                elif data.get("action") == "disconnect":
                    #server_state["disconnecting_flag"] = 1;
                    result = await disconnect_bluetooth_device(websocket)
                    await websocket.send(json.dumps({
                        "action": "disconnect_result",
                        **result
                    }))

            # 捕捉处理接受消息的异常
            except json.JSONDecodeError:
                error_msg = "无效的JSON消息格式"
                debug_log(error_msg)
                response = {
                    "action": "error",
                    "message": error_msg
                }
                await websocket.send(json.dumps(response))

            except Exception as e:
                error_msg = f"处理客户端消息失败: {str(e)}"
                debug_log(error_msg)
                debug_log(traceback.format_exc())
                response = {
                    "action": "error",
                    "message": str(e)
                }
                await websocket.send(json.dumps(response))
    finally:
        debug_log("WebSocket客户端已断开连接")
        server_state["connected_client"] = None
        # 断开蓝牙连接
        if server_state["bluetooth_client"]:
            await disconnect_bluetooth_device(websocket)

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