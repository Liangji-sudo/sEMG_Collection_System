#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mocap_server.py - 动捕数据服务器
================================
用于接收动捕数据并计算手势参数，通过WebSocket发送给realtimeEngine.js

数据源：
- 连接到 mocap_simulator.py (ws://localhost:8768) 接收 marker 点数据
- 或连接到真实动捕设备

数据通道（根据当前采集的手势类型计算）：
- finger_joint_angle: 食指关节角度 (0-90°) - 连续手势1 (食指上抬)
- thumb_index_distance: 拇指食指距离 (mm) - 连续手势2 (捏合)
- palm_rotation_angle: 手掌翻转角度 (0-180°) - 连续手势3 (翻转)

WebSocket端口: 8767 (供 realtimeEngine.js 连接)
"""

import asyncio
import websockets
import json
import time
import math
import sys
import io

# 编码配置
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

try:
    import numpy as np
except ImportError:
    print("[MocapServer] 错误: 请安装 numpy: pip install numpy", file=sys.stderr)
    sys.exit(1)


# ==================== 配置 ====================

# 动捕数据源地址 (mocap_simulator.py)
MOCAP_SOURCE_URL = "ws://localhost:8768"

# 本服务器端口
SERVER_HOST = "localhost"
SERVER_PORT = 8767

# 数据发送频率
SEND_RATE = 50  # Hz


# ==================== 手势计算函数 ====================

def fit_line_direction(p1, p2, p3):
    """
    用三个点拟合直线，返回方向向量（单位向量）
    """
    p1, p2, p3 = np.array(p1), np.array(p2), np.array(p3)
    direction = p3 - p1
    norm = np.linalg.norm(direction)
    if norm < 1e-6:
        return np.array([0, 0, 1])
    return direction / norm


def fit_plane_normal(p1, p2, p3):
    """
    用三个点拟合平面，返回法向量（单位向量）
    """
    p1, p2, p3 = np.array(p1), np.array(p2), np.array(p3)
    v1 = p2 - p1
    v2 = p3 - p1
    normal = np.cross(v1, v2)
    norm = np.linalg.norm(normal)
    if norm < 1e-6:
        return np.array([0, 0, 1])
    return normal / norm


def calculate_finger_joint_angle(markers):
    """
    计算食指上抬角度（连续手势1）
    ri1-ri2-ri3 拟合直线与 m1-m2-m3 平面的夹角
    返回: 0° = 食指垂直于手掌, 90° = 食指平行于手掌
    """
    ri1 = np.array(markers.get("ri1", [0, 0, 0]))
    ri2 = np.array(markers.get("ri2", [0, 0, 0]))
    ri3 = np.array(markers.get("ri3", [0, 0, 0]))
    m1 = np.array(markers.get("m1", [0, 0, 0]))
    m2 = np.array(markers.get("m2", [0, 0, 0]))
    m3 = np.array(markers.get("m3", [0, 0, 0]))

    # 食指方向向量
    index_dir = fit_line_direction(ri1, ri2, ri3)
    # 手掌平面法向量
    palm_normal = fit_plane_normal(m1, m2, m3)

    # 计算直线与平面的夹角
    cos_angle = abs(np.dot(index_dir, palm_normal))
    cos_angle = np.clip(cos_angle, -1, 1)
    angle_with_normal = math.degrees(math.acos(cos_angle))
    angle_with_plane = 90 - angle_with_normal

    return angle_with_plane


def calculate_thumb_index_distance(markers):
    """
    计算拇指食指距离（连续手势2）
    rt1 与 ri1 的欧氏距离
    返回: 距离 (mm)
    """
    rt1 = np.array(markers.get("rt1", [0, 0, 0]))
    ri1 = np.array(markers.get("ri1", [0, 0, 0]))
    distance = np.linalg.norm(rt1 - ri1)
    return distance


def calculate_palm_rotation_angle(markers):
    """
    计算手掌翻转角度（连续手势3）
    手掌平面法向量与向下方向的夹角
    返回: 0° = 掌心向下, 90° = 手掌竖直, 180° = 掌心向上
    """
    m1 = np.array(markers.get("m1", [0, 0, 0]))
    m2 = np.array(markers.get("m2", [0, 0, 0]))
    m3 = np.array(markers.get("m3", [0, 0, 0]))

    palm_normal = fit_plane_normal(m1, m2, m3)
    downward = np.array([0, 0, -1])  # 向下方向

    # 掌心向下时法向量指向下方，与[0,0,-1]夹角≈0°
    cos_angle = np.dot(palm_normal, downward)
    cos_angle = np.clip(cos_angle, -1, 1)
    angle = math.degrees(math.acos(cos_angle))

    return angle


# ==================== 动捕数据接收器 ====================

class MocapDataReceiver:
    """从动捕模拟器接收数据"""

    def __init__(self, source_url=MOCAP_SOURCE_URL):
        self.source_url = source_url
        self.ws = None
        self.connected = False
        self.latest_markers = {}
        self.latest_frame = 0
        self._running = False
        self._reconnect_delay = 2.0
        # 【新增】帧缓冲区，用于批量发送
        self._frame_buffer = []
        self._buffer_lock = asyncio.Lock() if hasattr(asyncio, 'Lock') else None

    async def connect(self):
        """连接到动捕数据源"""
        import asyncio
        self._buffer_lock = asyncio.Lock()

        while self._running:
            try:
                self.ws = await websockets.connect(self.source_url)
                self.connected = True
                print(f"[MocapReceiver] 已连接到动捕数据源")

                async for message in self.ws:
                    if not self._running:
                        break
                    try:
                        data = json.loads(message)
                        msg_type = data.get("type", "")

                        if msg_type == "mocap_data":
                            self.latest_markers = data.get("markers", {})
                            self.latest_frame = data.get("frame", 0)
                            # 【新增】将帧数据加入缓冲区
                            async with self._buffer_lock:
                                self._frame_buffer.append({
                                    "markers": self.latest_markers,
                                    "frame": self.latest_frame,
                                    "time": data.get("time", 0)
                                })

                    except json.JSONDecodeError:
                        pass

            except websockets.exceptions.ConnectionClosed:
                pass
            except ConnectionRefusedError:
                pass
            except Exception as e:
                print(f"[MocapReceiver] 错误: {e}")

            self.connected = False
            self.ws = None

            if self._running:
                await asyncio.sleep(self._reconnect_delay)

    def start(self):
        """启动接收器"""
        self._running = True

    def stop(self):
        """停止接收器"""
        self._running = False
        if self.ws:
            asyncio.create_task(self.ws.close())

    def get_markers(self):
        """获取最新的 marker 数据"""
        return self.latest_markers

    async def get_buffered_frames(self):
        """获取并清空缓冲区中的所有帧"""
        async with self._buffer_lock:
            frames = self._frame_buffer.copy()
            self._frame_buffer.clear()
            return frames


# ==================== 动捕服务器 ====================

class MocapServer:
    """动捕数据WebSocket服务器"""

    def __init__(self, host=SERVER_HOST, port=SERVER_PORT):
        self.host = host
        self.port = port
        self.clients = set()

        # 动捕数据接收器
        self.receiver = MocapDataReceiver()

        # 【新增】SDK连接状态
        self.sdk_connected = False

        # 当前采集状态
        self.collecting = False
        self.active_gesture = None  # 'continual_gesture_1', 'continual_gesture_2', 'continual_gesture_3'
        self._debug_counter = 0  # 调试计数器

        # 各通道的当前值
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
                'max': 150.0,  # mm
                'unit': 'mm',
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

        # 当前活动通道
        self.active_channel = 'finger_joint_angle'

        # 数据发送频率
        self.send_rate = SEND_RATE
        self._running = False

    def update_from_mocap(self):
        """从动捕数据更新通道值"""
        markers = self.receiver.get_markers()
        if not markers:
            return

        # 始终计算所有通道的值（用于调试）
        finger_angle = calculate_finger_joint_angle(markers)
        pinch_dist = calculate_thumb_index_distance(markers)
        palm_angle = calculate_palm_rotation_angle(markers)

        # 更新所有通道
        self.channels['finger_joint_angle']['value'] = finger_angle
        self.channels['thumb_index_distance']['value'] = pinch_dist
        self.channels['palm_rotation_angle']['value'] = palm_angle

    async def register(self, websocket):
        """注册新客户端"""
        self.clients.add(websocket)
        print(f"[MocapServer] 客户端连接 (总数: {len(self.clients)})")

    async def unregister(self, websocket):
        """注销客户端"""
        self.clients.discard(websocket)
        print(f"[MocapServer] 客户端断开 (剩余: {len(self.clients)})")

    async def handle_message(self, websocket, message):
        """处理来自客户端的消息"""
        try:
            data = json.loads(message)
            cmd = data.get('cmd')

            if cmd == 'start_collecting':
                # 开始采集
                gesture = data.get('gesture')  # 'continual_gesture_1', 'continual_gesture_2', 'continual_gesture_3'
                self.collecting = True
                self.active_gesture = gesture

                # 设置对应的活动通道
                if gesture == 'continual_gesture_1':
                    self.active_channel = 'finger_joint_angle'
                elif gesture == 'continual_gesture_2':
                    self.active_channel = 'thumb_index_distance'
                elif gesture == 'continual_gesture_3':
                    self.active_channel = 'palm_rotation_angle'

                await websocket.send(json.dumps({
                    'type': 'response',
                    'cmd': 'start_collecting',
                    'status': 'ok',
                    'gesture': gesture,
                    'channel': self.active_channel
                }))

            elif cmd == 'stop_collecting':
                # 停止采集
                self.collecting = False
                self.active_gesture = None
                await websocket.send(json.dumps({
                    'type': 'response',
                    'cmd': 'stop_collecting',
                    'status': 'ok'
                }))

            elif cmd == 'set_channel':
                # 设置活动通道（兼容旧接口）
                channel = data.get('channel')
                if channel in self.channels:
                    self.active_channel = channel
                await websocket.send(json.dumps({
                    'type': 'response',
                    'cmd': 'set_channel',
                    'status': 'ok',
                    'channel': channel
                }))

            elif cmd == 'reset_channel':
                # 重置通道值
                channel = data.get('channel')
                value = data.get('value', 0)
                if channel in self.channels:
                    self.channels[channel]['value'] = value
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
                        'collecting': self.collecting,
                        'active_gesture': self.active_gesture,
                        'active_channel': self.active_channel,
                        'mocap_connected': self.receiver.connected,
                        'send_rate': self.send_rate,
                        'client_count': len(self.clients)
                    }
                }))

            elif cmd == 'sdk_connect':
                # 【新增】连接动捕SDK
                print("[MocapServer] 收到SDK连接请求")
                # TODO: 这里后续实现真正的SDK连接逻辑
                self.sdk_connected = True
                print("[MocapServer] SDK连接成功 (模拟)")
                await websocket.send(json.dumps({
                    'type': 'response',
                    'cmd': 'sdk_connect',
                    'status': 'ok',
                    'sdk_connected': True,
                    'message': 'SDK连接成功'
                }))

            elif cmd == 'sdk_disconnect':
                # 【新增】断开动捕SDK
                print("[MocapServer] 收到SDK断开请求")
                # TODO: 这里后续实现真正的SDK断开逻辑
                self.sdk_connected = False
                print("[MocapServer] SDK已断开 (模拟)")
                await websocket.send(json.dumps({
                    'type': 'response',
                    'cmd': 'sdk_disconnect',
                    'status': 'ok',
                    'sdk_connected': False,
                    'message': 'SDK已断开'
                }))

            elif cmd == 'sdk_get_status':
                # 【新增】获取SDK连接状态
                await websocket.send(json.dumps({
                    'type': 'response',
                    'cmd': 'sdk_get_status',
                    'status': 'ok',
                    'sdk_connected': getattr(self, 'sdk_connected', False)
                }))

        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"[MocapServer] 错误: {e}")

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
                # 从动捕数据更新通道值
                self.update_from_mocap()
                self._debug_counter += 1

                # 【修改】获取缓冲区中的所有帧（批量）
                buffered_frames = await self.receiver.get_buffered_frames()

                # 构建数据包
                data = {
                    'type': 'mocap',
                    'timestamp': time.time(),
                    'collecting': self.collecting,
                    'active_channel': self.active_channel,
                    'active_gesture': self.active_gesture,
                    'mocap_connected': self.receiver.connected,
                    'channels': {
                        name: {
                            'value': ch['value'],
                            'min': ch['min'],
                            'max': ch['max'],
                            'unit': ch['unit']
                        }
                        for name, ch in self.channels.items()
                    },
                    # 【修改】批量发送所有缓冲的帧数据
                    'frames': buffered_frames,  # [{markers, frame, time}, ...]
                    'frame_count': len(buffered_frames)
                }
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
        self.receiver.start()

        # 启动动捕数据接收任务
        receiver_task = asyncio.create_task(self.receiver.connect())

        # 启动WebSocket服务器
        server = await websockets.serve(
            self.handler,
            self.host,
            self.port
        )

        print(f"[MocapServer] 已启动 ws://{self.host}:{self.port} (数据源: {MOCAP_SOURCE_URL})")

        # 启动数据广播任务
        broadcast_task = asyncio.create_task(self.broadcast_data())

        try:
            await asyncio.Future()  # 永久运行
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            self.receiver.stop()
            broadcast_task.cancel()
            receiver_task.cancel()
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

    parser = argparse.ArgumentParser(description='动捕数据服务器')
    parser.add_argument('--host', default=SERVER_HOST, help='监听地址')
    parser.add_argument('--port', type=int, default=SERVER_PORT, help='监听端口')
    parser.add_argument('--rate', type=int, default=SEND_RATE, help='数据发送频率(Hz)')
    parser.add_argument('--source', default=MOCAP_SOURCE_URL, help='动捕数据源地址')

    args = parser.parse_args()

    server = MocapServer(host=args.host, port=args.port)
    server.send_rate = args.rate
    # 如果指定了不同的数据源，更新接收器的地址
    if args.source != MOCAP_SOURCE_URL:
        server.receiver.source_url = args.source
    server.run()


if __name__ == '__main__':
    main()
