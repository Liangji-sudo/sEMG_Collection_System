#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mocap_server.py - 动捕数据服务器
用于采集动捕数据并通过WebSocket发送给realtimeEngine.js

目前使用鼠标滚轮模拟动捕数据，便于调试。
后续可替换为真实动捕设备的数据源。

数据通道：
- finger_joint_angle: 食指关节角度 (0-90°) - 用于连续手势1
- thumb_index_distance: 拇指食指距离 (0-10cm) - 用于连续手势2  
- palm_rotation_angle: 手掌翻转角度 (0-180°) - 用于连续手势3

WebSocket端口: 8767 (区别于ble_server的8766)
"""

import asyncio
import websockets
import json
import time
import threading
import platform

# ==================== 鼠标滚轮监听 ====================

class MouseWheelListener:
    """鼠标滚轮监听器 - 跨平台实现"""
    
    def __init__(self):
        self.accumulated_delta = 0.0
        self.lock = threading.Lock()
        self._running = False
        self._listener_thread = None
        
    def start(self):
        """启动监听"""
        self._running = True
        self._listener_thread = threading.Thread(target=self._listen, daemon=True)
        self._listener_thread.start()
        print("[MouseWheel] 监听器已启动")
        
    def stop(self):
        """停止监听"""
        self._running = False
        if self._listener_thread:
            self._listener_thread.join(timeout=1.0)
        print("[MouseWheel] 监听器已停止")
        
    def _listen(self):
        """监听线程"""
        system = platform.system()
        
        if system == 'Windows':
            self._listen_windows()
        elif system == 'Darwin':  # macOS
            self._listen_macos()
        else:  # Linux
            self._listen_linux()
            
    def _listen_windows(self):
        """Windows下使用pynput监听"""
        try:
            from pynput import mouse
            
            def on_scroll(x, y, dx, dy):
                if not self._running:
                    return False
                with self.lock:
                    # dy > 0 向上滚动, dy < 0 向下滚动
                    self.accumulated_delta += dy * 5.0  # 放大系数
                return True
                
            with mouse.Listener(on_scroll=on_scroll) as listener:
                while self._running:
                    time.sleep(0.1)
                listener.stop()
                    
        except ImportError:
            print("[MouseWheel] 警告: pynput未安装，使用模拟数据")
            self._simulate_data()
            
    def _listen_macos(self):
        """macOS下使用pynput监听"""
        self._listen_windows()  # pynput在macOS上也能工作
        
    def _listen_linux(self):
        """Linux下监听/dev/input/mice"""
        try:
            with open('/dev/input/mice', 'rb') as f:
                while self._running:
                    data = f.read(3)
                    if len(data) == 3:
                        # 第三个字节是滚轮数据（有符号）
                        wheel = data[2]
                        if wheel > 127:
                            wheel -= 256
                        if wheel != 0:
                            with self.lock:
                                self.accumulated_delta += wheel * 5.0
        except PermissionError:
            print("[MouseWheel] 警告: 无权限读取/dev/input/mice，尝试使用pynput")
            self._listen_windows()
        except FileNotFoundError:
            print("[MouseWheel] 警告: /dev/input/mice不存在，使用pynput")
            self._listen_windows()
            
    def _simulate_data(self):
        """无法监听鼠标时的回退方案 - 保持静态值，等待用户操作"""
        print("[MouseWheel] 警告: 无法监听鼠标滚轮，数据将保持静态")
        print("[MouseWheel] 请确保已安装 pynput: pip install pynput")
        while self._running:
            time.sleep(0.5)
            # 保持静态值，不自动变化
                
    def get_and_reset_delta(self):
        """获取累积的滚轮值并重置"""
        with self.lock:
            delta = self.accumulated_delta
            self.accumulated_delta = 0.0
            return delta
            
    def get_delta(self):
        """获取累积的滚轮值（不重置）"""
        with self.lock:
            return self.accumulated_delta


# ==================== 动捕数据模拟器 ====================

class MocapSimulator:
    """
    动捕数据模拟器
    将鼠标滚轮输入转换为模拟的动捕数据
    """
    
    def __init__(self, wheel_listener: MouseWheelListener):
        self.wheel_listener = wheel_listener
        
        # 各通道的当前值和范围
        self.channels = {
            'finger_joint_angle': {
                'value': 0.0,
                'min': 0.0,
                'max': 90.0,
                'unit': '°',
                'description': '食指关节角度'
            },
            'thumb_index_distance': {
                'value': 0.0,
                'min': 0.0,
                'max': 10.0,
                'unit': 'cm',
                'description': '拇指食指距离'
            },
            'palm_rotation_angle': {
                'value': 0.0,
                'min': 0.0,
                'max': 180.0,
                'unit': '°',
                'description': '手掌翻转角度'
            }
        }
        
        # 当前活动通道（由前端指定）
        self.active_channel = 'finger_joint_angle'
        
        # 灵敏度设置
        self.sensitivity = {
            'finger_joint_angle': 2.0,      # 每单位滚轮对应的角度变化
            'thumb_index_distance': 0.2,    # 每单位滚轮对应的距离变化
            'palm_rotation_angle': 4.0      # 每单位滚轮对应的角度变化
        }
        
    def set_active_channel(self, channel_name: str):
        """设置当前活动通道"""
        if channel_name in self.channels:
            self.active_channel = channel_name
            print(f"[MocapSimulator] 活动通道切换为: {channel_name}")
        else:
            print(f"[MocapSimulator] 未知通道: {channel_name}")
            
    def update(self):
        """更新动捕数据（根据鼠标滚轮输入）"""
        delta = self.wheel_listener.get_and_reset_delta()
        
        if delta != 0 and self.active_channel:
            channel = self.channels[self.active_channel]
            sensitivity = self.sensitivity[self.active_channel]
            
            # 更新通道值
            new_value = channel['value'] + delta * sensitivity
            # 限制在范围内
            channel['value'] = max(channel['min'], min(channel['max'], new_value))
            
    def get_data(self) -> dict:
        """获取当前动捕数据"""
        self.update()
        
        return {
            'type': 'mocap',
            'timestamp': time.time(),
            'active_channel': self.active_channel,
            'channels': {
                name: {
                    'value': ch['value'],
                    'min': ch['min'],
                    'max': ch['max'],
                    'unit': ch['unit']
                }
                for name, ch in self.channels.items()
            }
        }
        
    def reset_channel(self, channel_name: str, value: float = None):
        """重置通道值"""
        if channel_name in self.channels:
            ch = self.channels[channel_name]
            if value is not None:
                ch['value'] = max(ch['min'], min(ch['max'], value))
            else:
                ch['value'] = ch['min']
            print(f"[MocapSimulator] 重置通道 {channel_name} = {ch['value']}")


# ==================== WebSocket服务器 ====================

class MocapServer:
    """动捕数据WebSocket服务器"""
    
    def __init__(self, host='localhost', port=8767):
        self.host = host
        self.port = port
        self.clients = set()
        
        # 初始化鼠标监听和模拟器
        self.wheel_listener = MouseWheelListener()
        self.simulator = MocapSimulator(self.wheel_listener)
        
        # 数据发送频率 (Hz)
        self.send_rate = 50  # 50Hz = 20ms间隔
        
        self._running = False
        
    async def register(self, websocket):
        """注册新客户端"""
        self.clients.add(websocket)
        client_info = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        print(f"[MocapServer] 客户端连接: {client_info} (总数: {len(self.clients)})")
        
    async def unregister(self, websocket):
        """注销客户端"""
        self.clients.discard(websocket)
        print(f"[MocapServer] 客户端断开 (剩余: {len(self.clients)})")
        
    async def handle_message(self, websocket, message):
        """处理来自客户端的消息"""
        try:
            data = json.loads(message)
            cmd = data.get('cmd')
            
            if cmd == 'set_channel':
                # 设置活动通道
                channel = data.get('channel')
                self.simulator.set_active_channel(channel)
                await websocket.send(json.dumps({
                    'type': 'response',
                    'cmd': 'set_channel',
                    'status': 'ok',
                    'channel': channel
                }))
                
            elif cmd == 'reset_channel':
                # 重置通道值
                channel = data.get('channel')
                value = data.get('value')
                self.simulator.reset_channel(channel, value)
                await websocket.send(json.dumps({
                    'type': 'response',
                    'cmd': 'reset_channel',
                    'status': 'ok'
                }))
                
            elif cmd == 'get_status':
                # 获取状态
                await websocket.send(json.dumps({
                    'type': 'response',
                    'cmd': 'get_status',
                    'status': 'ok',
                    'data': {
                        'active_channel': self.simulator.active_channel,
                        'send_rate': self.send_rate,
                        'client_count': len(self.clients)
                    }
                }))
                
            elif cmd == 'set_sensitivity':
                # 设置灵敏度
                channel = data.get('channel')
                sensitivity = data.get('sensitivity')
                if channel in self.simulator.sensitivity:
                    self.simulator.sensitivity[channel] = sensitivity
                    print(f"[MocapServer] 设置灵敏度: {channel} = {sensitivity}")
                    
        except json.JSONDecodeError:
            print(f"[MocapServer] 无效JSON: {message}")
        except Exception as e:
            print(f"[MocapServer] 处理消息错误: {e}")
            
    async def handler(self, websocket, path=None):
        """WebSocket连接处理"""
        await self.register(websocket)
        try:
            async for message in websocket:
                await self.handle_message(websocket, message)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await self.unregister(websocket)
            
    async def broadcast_data(self):
        """广播动捕数据到所有客户端"""
        interval = 1.0 / self.send_rate
        
        while self._running:
            if self.clients:
                data = self.simulator.get_data()
                message = json.dumps(data)
                
                # 广播给所有客户端
                disconnected = set()
                for client in self.clients:
                    try:
                        await client.send(message)
                    except websockets.exceptions.ConnectionClosed:
                        disconnected.add(client)
                        
                # 移除断开的客户端
                for client in disconnected:
                    await self.unregister(client)
                    
            await asyncio.sleep(interval)
            
    async def start(self):
        """启动服务器"""
        self._running = True
        
        # 启动鼠标监听
        self.wheel_listener.start()
        
        # 启动WebSocket服务器
        server = await websockets.serve(
            self.handler,
            self.host,
            self.port
        )
        
        print(f"[MocapServer] WebSocket服务器启动: ws://{self.host}:{self.port}")
        print(f"[MocapServer] 数据发送频率: {self.send_rate}Hz")
        print(f"[MocapServer] 使用鼠标滚轮控制动捕数据")
        print(f"[MocapServer] 按 Ctrl+C 停止服务器")
        
        # 启动数据广播任务
        broadcast_task = asyncio.create_task(self.broadcast_data())
        
        try:
            await asyncio.Future()  # 永久运行
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            broadcast_task.cancel()
            self.wheel_listener.stop()
            server.close()
            await server.wait_closed()
            print("[MocapServer] 服务器已停止")
            
    def run(self):
        """运行服务器（阻塞）"""
        try:
            asyncio.run(self.start())
        except KeyboardInterrupt:
            print("\n[MocapServer] 收到停止信号")


# ==================== 主程序 ====================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='动捕数据模拟服务器')
    parser.add_argument('--host', default='localhost', help='监听地址')
    parser.add_argument('--port', type=int, default=8767, help='监听端口')
    parser.add_argument('--rate', type=int, default=50, help='数据发送频率(Hz)')
    
    args = parser.parse_args()
    
    server = MocapServer(host=args.host, port=args.port)
    server.send_rate = args.rate
    server.run()


if __name__ == '__main__':
    main()
