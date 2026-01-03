"""
BLE Server Mock - 模拟ESP32S3_EMG设备 (无硬件测试版本)
=================================================================
这是一个模拟版本，不需要实际的BLE硬件设备。
它会生成模拟的EMG和IMU数据，供前端调试使用。

【测试波形版本】
=================================================================
此版本使用特殊的测试波形，便于验证硬件隔离：

EMG 16通道波形分布：
  - 通道 0-3:   方波 (Square Wave)   - 频率 2/3.5/5/6.5 Hz
  - 通道 4-7:   锯齿波 (Sawtooth)    - 频率 3/5/7/9 Hz
  - 通道 8-11:  三角波 (Triangle)    - 频率 4/5.5/7/8.5 Hz
  - 通道 12-15: 阶梯波 (Stair-step)  - 频率 1.5/2/2.5/3 Hz

IMU 运动模式：
  - 加速度计：8字形运动轨迹
  - 陀螺仪：大幅度(±50°/s)正弦摆动
  - 磁力计：缓慢旋转（5秒一圈）

如需恢复原始模拟波形，请使用 ble_server_original.py
=================================================================

支持：
  - 两个模拟蓝牙设备（独立控制）
  - 两个 WebSocket 客户端：
    * 控制端（index.html）: 端口 8764，处理控制命令
    * 数据端（realtimeEngine.js）: 端口 8766，接收数据流

依赖安装：
  pip install websockets msgpack numpy

使用方法：
  python ble_server.py
"""

import asyncio
import struct
import time
import traceback
import sys
import io
import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Set
from queue import PriorityQueue
import threading
import itertools

import msgpack
import json
import websockets

# 尝试导入numpy，如果没有则使用纯Python实现
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    print("[警告] numpy未安装，将使用纯Python生成数据（性能较低）")

# ================= 编码配置 =================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# ================= 服务配置 =================
WEBSOCKET_HOST = "localhost"
CONTROL_PORT = 8764   # 控制端口（index.html）
DATA_PORT = 8766      # 数据端口（realtimeEngine.js）

# ================= 模拟设备配置 =================
MOCK_DEVICE_PREFIX = "ESP32S3_EMG"

# 模拟扫描到的设备列表
MOCK_DEVICES = [
    {"name": "ESP32S3_EMG_001", "mac": "AA:BB:CC:DD:EE:01", "rssi": -45},
    {"name": "ESP32S3_EMG_002", "mac": "AA:BB:CC:DD:EE:02", "rssi": -52},
    {"name": "ESP32S3_EMG_003", "mac": "AA:BB:CC:DD:EE:03", "rssi": -68},
    {"name": "Other_BLE_Device", "mac": "11:22:33:44:55:66", "rssi": -75},
]

# ================= 默认配置 =================
DEFAULT_CONFIG = {
    'sample_rate': 1000,
    'gain': 12,
    'gain_index': 6,
    'is_16bit': False,
    'shift': 4,
    'imu_enabled': True,
    'frames_per_packet': 9,
}

# ================= 转换系数 =================
SCALE_ACCEL = 16.0 / 32768.0
SCALE_GYRO = 2000.0 / 32768.0
SCALE_MAG = 0.15
BASE_LSB_24BIT = 0.476837
HARDWARE_FRONTEND_GAIN = 5.9

# ================= 批量发送配置 =================
BATCH_INTERVAL = 0.01  # 10ms，对应100Hz数据率

# ================= 优先级 =================
PRIORITY_CONTROL = 0   # 控制命令（最高优先级）
PRIORITY_HIGH = 1      # 控制响应
PRIORITY_LOW = 2       # 传感器数据


# ================= 模拟数据生成器 =================
class MockDataGenerator:
    """模拟EMG和IMU数据生成器"""
    
    def __init__(self, device_id: int):
        self.device_id = device_id
        self.frame_index = 0
        self.time = 0.0
        self.dt = 1.0 / 1000.0  # 1kHz采样率
        
        # EMG模拟参数 - 每个设备有不同的特征
        self.emg_base_freqs = [20 + random.uniform(-5, 5) + i * 3 for i in range(16)]
        self.emg_amplitudes = [50 + random.uniform(-20, 20) for _ in range(16)]
        self.emg_phases = [random.uniform(0, 2 * math.pi) for _ in range(16)]
        self.emg_noise_level = 15 + device_id * 5  # 不同设备有不同噪声水平
        
        # EMG肌肉活动模拟
        self.muscle_activity = [0.5] * 16
        self.muscle_targets = [0.5] * 16
        
        # IMU模拟参数
        self.acc_bias = [random.uniform(-0.05, 0.05) for _ in range(3)]
        self.gyr_bias = [random.uniform(-1, 1) for _ in range(3)]
        self.mag_base = [25 + random.uniform(-5, 5), 
                        random.uniform(-10, 10), 
                        -40 + random.uniform(-5, 5)]
        
        # 运动模拟
        self.motion_phase = random.uniform(0, 2 * math.pi)
        self.motion_freq = 0.5 + random.uniform(-0.2, 0.2)
    
    def generate_emg_frame(self) -> List[float]:
        """
        生成一帧16通道EMG数据（单位：μV）
        
        【测试波形版本】- 使用明显不同的波形模式：
        - 通道 0-3:   方波 (Square Wave) - 频率递增
        - 通道 4-7:   锯齿波 (Sawtooth Wave) - 频率递增
        - 通道 8-11:  三角波 (Triangle Wave) - 频率递增
        - 通道 12-15: 阶梯波 (Stair-step Wave) - 每通道不同阶数
        
        这样你可以在示波器界面上一眼看出是测试数据！
        """
        emg_data = []
        t = self.time
        
        for ch in range(16):
            amp = 80 + ch * 5  # 幅度随通道递增，便于区分
            
            if ch < 4:
                # ===== 通道 0-3: 方波 =====
                freq = 2 + ch * 1.5  # 2Hz, 3.5Hz, 5Hz, 6.5Hz
                period = 1.0 / freq
                # 方波：周期前半为正，后半为负
                phase_in_period = (t % period) / period
                signal = amp if phase_in_period < 0.5 else -amp
                
            elif ch < 8:
                # ===== 通道 4-7: 锯齿波 =====
                freq = 3 + (ch - 4) * 2  # 3Hz, 5Hz, 7Hz, 9Hz
                period = 1.0 / freq
                # 锯齿波：从 -amp 线性上升到 +amp
                phase_in_period = (t % period) / period
                signal = -amp + 2 * amp * phase_in_period
                
            elif ch < 12:
                # ===== 通道 8-11: 三角波 =====
                freq = 4 + (ch - 8) * 1.5  # 4Hz, 5.5Hz, 7Hz, 8.5Hz
                period = 1.0 / freq
                phase_in_period = (t % period) / period
                # 三角波：前半周期上升，后半周期下降
                if phase_in_period < 0.5:
                    signal = -amp + 4 * amp * phase_in_period
                else:
                    signal = amp - 4 * amp * (phase_in_period - 0.5)
                    
            else:
                # ===== 通道 12-15: 阶梯波 =====
                freq = 1.5 + (ch - 12) * 0.5  # 1.5Hz, 2Hz, 2.5Hz, 3Hz
                steps = 4 + (ch - 12)  # 4级, 5级, 6级, 7级阶梯
                period = 1.0 / freq
                phase_in_period = (t % period) / period
                # 阶梯波：分成N级台阶
                step_index = int(phase_in_period * steps)
                signal = -amp + (2 * amp / (steps - 1)) * step_index
            
            # 添加少量噪声（但保持波形清晰可辨）
            noise = random.uniform(-3, 3)
            signal += noise
            
            emg_data.append(signal)
        
        self.time += self.dt
        return emg_data
    
    def generate_emg_packet(self, frames_per_packet: int = 9) -> Dict:
        """生成一个EMG数据包，每帧都有独立的时间戳"""
        emg_raw = []
        emg_uv = []
        emg_timestamps = []  # 每帧的时间戳
        
        start_frame = self.frame_index
        base_time = time.time()  # 包的基准时间
        
        for i in range(frames_per_packet):
            frame_data = self.generate_emg_frame()
            # raw数据（模拟ADC原始值）
            raw_frame = [int(v / 0.5) for v in frame_data]  # 假设0.5 μV/LSB
            emg_raw.append(raw_frame)
            emg_uv.append(frame_data)
            # 每帧时间戳 = 基准时间 + 帧索引 * 采样间隔(1ms)
            emg_timestamps.append(base_time + i * self.dt)
            self.frame_index += 1
        
        return {
            'raw': emg_raw,
            'uv': emg_uv,
            't': emg_timestamps,  # 改为时间戳数组
            'start_frame': start_frame,
            'frames': frames_per_packet,
        }
    
    def generate_imu_data(self) -> Dict:
        """
        生成IMU数据（2组，每组包含加速度计、陀螺仪、磁力计）
        
        【测试波形版本】- 使用明显的周期性模式：
        - 加速度计：模拟"8字形"运动轨迹
        - 陀螺仪：大幅度正弦摆动
        - 磁力计：缓慢旋转
        """
        imu_data = []
        imu_timestamps = []
        
        base_time = time.time()
        imu_interval = 0.005  # 5ms (200Hz)
        
        for i in range(2):
            t = self.time + i * imu_interval
            
            # ===== 加速度计 (g) - 8字形运动 =====
            motion_freq = 0.8  # 较快的运动频率
            acc = [
                0.3 * math.sin(2 * math.pi * motion_freq * t),           # X: 正弦
                0.3 * math.sin(2 * math.pi * motion_freq * 2 * t),       # Y: 2倍频（形成8字）
                1.0 + 0.2 * math.cos(2 * math.pi * motion_freq * t),     # Z: 重力 + 上下摆动
            ]
            
            # ===== 陀螺仪 (deg/s) - 大幅度摆动 =====
            gyro_freq = 1.2
            gyr = [
                50 * math.sin(2 * math.pi * gyro_freq * t),              # Roll: ±50 deg/s
                50 * math.cos(2 * math.pi * gyro_freq * t),              # Pitch: ±50 deg/s  
                30 * math.sin(2 * math.pi * gyro_freq * 0.5 * t),        # Yaw: 慢速摆动
            ]
            
            # ===== 磁力计 (μT) - 模拟缓慢旋转 =====
            mag_freq = 0.2  # 5秒一圈
            mag_strength = 50  # 地磁场强度约50μT
            mag = [
                mag_strength * math.cos(2 * math.pi * mag_freq * t),     # X
                mag_strength * math.sin(2 * math.pi * mag_freq * t),     # Y
                -30 + 5 * math.sin(2 * math.pi * mag_freq * 2 * t),      # Z (向下)
            ]
            
            # 添加少量噪声
            acc = [a + random.uniform(-0.01, 0.01) for a in acc]
            gyr = [g + random.uniform(-1, 1) for g in gyr]
            mag = [m + random.uniform(-0.5, 0.5) for m in mag]
            
            imu_data.append([acc, gyr, mag])
            imu_timestamps.append(base_time + i * imu_interval)
        
        return {
            'data': imu_data,
            't': imu_timestamps,
        }
    
    def generate_packet(self) -> Dict:
        """生成完整的数据包"""
        emg = self.generate_emg_packet()
        imu = self.generate_imu_data()
        
        return {
            'f': emg['start_frame'],
            'n': emg['frames'],
            'raw': emg['raw'],
            'uv': emg['uv'],
            'emg_t': emg['t'],       # EMG时间戳数组（每帧一个）
            'imu': imu['data'],       # IMU数据
            'imu_t': imu['t'],        # IMU时间戳数组（每组一个）
        }
    
    def reset(self):
        """重置生成器"""
        self.frame_index = 0
        self.time = 0.0
        self.muscle_activity = [0.5] * 16
        self.muscle_targets = [0.5] * 16


# ================= 单设备状态类 =================
@dataclass
class DeviceState:
    """单个模拟BLE设备的状态"""
    device_id: int
    
    # 模拟连接状态
    _connected: bool = False
    mac: Optional[str] = None
    name: Optional[str] = None
    rssi: Optional[int] = None
    
    is_streaming: bool = False
    total_frames: int = 0
    lost_frames: int = 0
    last_frame_index: int = -1
    
    config: Dict = field(default_factory=lambda: DEFAULT_CONFIG.copy())
    data_buffer: deque = field(default_factory=lambda: deque(maxlen=500))
    connect_task: Any = None
    
    # 数据生成器
    data_generator: Optional[MockDataGenerator] = None
    
    def __post_init__(self):
        self.data_generator = MockDataGenerator(self.device_id)
    
    def reset_stats(self):
        self.total_frames = 0
        self.lost_frames = 0
        self.last_frame_index = -1
        self.data_buffer.clear()
        if self.data_generator:
            self.data_generator.reset()
    
    def is_connected(self) -> bool:
        return self._connected
    
    def set_connected(self, connected: bool):
        self._connected = connected
    
    def to_dict(self) -> dict:
        """转换为字典（用于状态响应）"""
        return {
            'connected': self.is_connected(),
            'mac': self.mac,
            'name': self.name,
            'rssi': self.rssi,
            'streaming': self.is_streaming,
            'total': self.total_frames,
            'lost': self.lost_frames,
        }


# ================= 全局状态 =================
class ServerState:
    def __init__(self):
        # WebSocket 客户端
        self.control_clients: Set = set()  # 控制端（可能多个）
        self.data_clients: Set = set()     # 数据端（可能多个）
        
        # 双设备
        self.dev1 = DeviceState(device_id=1)
        self.dev2 = DeviceState(device_id=2)
        
        # 扫描结果
        self.devices_found: Dict[str, Any] = {}
        self.scan_results: List[dict] = []
        
        # 消息队列
        self.msg_queue = PriorityQueue()
        self.queue_seq = itertools.count()
        self.main_loop = None
        
        # 数据发送线程
        self.data_thread = None
        self.stop_thread = False
    
    def get_device(self, device_id: int) -> DeviceState:
        return self.dev1 if device_id == 1 else self.dev2
    
    def get_active_devices(self) -> List[int]:
        active = []
        if self.dev1.is_streaming:
            active.append(1)
        if self.dev2.is_streaming:
            active.append(2)
        return active
    
    def get_connected_devices(self) -> List[int]:
        connected = []
        if self.dev1.is_connected():
            connected.append(1)
        if self.dev2.is_connected():
            connected.append(2)
        return connected


state = ServerState()


# ================= 工具函数 =================

def log(message: str):
    print(f"[BLE-Mock] {message}", file=sys.stderr)


# ================= 消息队列 =================

def data_sender_thread():
    """数据发送线程 - 生成模拟数据并发送到数据端"""
    log("数据发送线程启动")
    
    while not state.stop_thread:
        try:
            active = state.get_active_devices()
            
            if state.data_clients and active:
                dev1_data = None
                dev2_data = None
                
                # 为设备1生成数据
                if state.dev1.is_streaming and state.dev1.data_generator:
                    packet = state.dev1.data_generator.generate_packet()
                    state.dev1.total_frames += packet['n']
                    packet['s'] = [state.dev1.total_frames, state.dev1.lost_frames]
                    dev1_data = packet
                
                # 为设备2生成数据
                if state.dev2.is_streaming and state.dev2.data_generator:
                    packet = state.dev2.data_generator.generate_packet()
                    state.dev2.total_frames += packet['n']
                    packet['s'] = [state.dev2.total_frames, state.dev2.lost_frames]
                    dev2_data = packet
                
                if dev1_data is not None or dev2_data is not None:
                    msg = {
                        'type': 'data',
                        'ts': time.time(),
                        'dev1': dev1_data,
                        'dev2': dev2_data,
                        'active': active,
                    }
                    add_to_queue(PRIORITY_LOW, 'data', msg)
            
            time.sleep(BATCH_INTERVAL)
            
        except Exception as e:
            if not state.stop_thread:
                log(f"发送线程错误: {e}")
                traceback.print_exc()
    
    log("数据发送线程结束")


def add_to_queue(priority: int, msg_type: str, data: dict, target_ws=None):
    """添加消息到队列"""
    try:
        q = state.msg_queue
        
        if priority == PRIORITY_LOW and q.qsize() > 500:
            try:
                old = q.get_nowait()
                if old[0] <= PRIORITY_HIGH:
                    q.put(old)
            except:
                pass
        
        seq = next(state.queue_seq)
        q.put((priority, seq, msg_type, data, target_ws))
        
        if state.main_loop:
            asyncio.run_coroutine_threadsafe(process_queue(), state.main_loop)
            
    except Exception as e:
        log(f"入队错误: {e}")


async def process_queue():
    """处理消息队列"""
    q = state.msg_queue
    
    try:
        while not q.empty():
            prio, seq, msg_type, data, target_ws = q.get_nowait()
            
            try:
                payload = json.dumps(data, ensure_ascii=False)
                
                if msg_type == 'control':
                    # 控制响应 -> 发送到控制端
                    targets = [target_ws] if target_ws else list(state.control_clients)
                    for ws in targets:
                        if ws:
                            try:
                                await ws.send(payload)
                            except:
                                pass
                
                elif msg_type == 'data':
                    # 数据 -> 发送到数据端
                    for ws in list(state.data_clients):
                        try:
                            await ws.send(payload)
                        except:
                            pass
                
                elif msg_type == 'broadcast':
                    # 广播 -> 发送到所有客户端
                    all_clients = list(state.control_clients) + list(state.data_clients)
                    for ws in all_clients:
                        try:
                            await ws.send(payload)
                        except:
                            pass
                            
            except Exception as e:
                log(f"发送错误: {e}")
                
    except Exception as e:
        log(f"队列处理错误: {e}")


async def send_to_control(ws, action: str, data: dict):
    """发送响应到控制端"""
    msg = {'type': 'response', 'action': action, **data}
    add_to_queue(PRIORITY_HIGH, 'control', msg, ws)


async def broadcast_event(event: str, data: dict):
    """广播事件到所有客户端"""
    msg = {'type': 'event', 'event': event, **data}
    add_to_queue(PRIORITY_HIGH, 'broadcast', msg)


# ================= 模拟BLE操作 =================

async def scan_devices(ws):
    """模拟扫描设备"""
    log(f"模拟扫描设备...")
    
    # 模拟扫描延迟
    await asyncio.sleep(1.5)
    
    state.devices_found.clear()
    state.scan_results.clear()
    
    for dev in MOCK_DEVICES:
        display = f"{dev['name']} ({dev['mac']})"
        state.devices_found[display] = dev
        
        info = {
            'name': dev['name'],
            'mac': dev['mac'],
            'display': display,
            'rssi': dev['rssi'],
            'manufacturer': 'Mock Device'
        }
        state.scan_results.append(info)
    
    # 按RSSI排序
    state.scan_results.sort(key=lambda x: x['rssi'], reverse=True)
    
    log(f"模拟扫描完成，找到 {len(state.scan_results)} 个设备")
    
    await send_to_control(ws, 'scan', {
        'success': True,
        'devices': state.scan_results,
        'count': len(state.scan_results),
        'targets': [d for d in state.scan_results if MOCK_DEVICE_PREFIX in d['name']],
    })


async def connect_device(ws, device_id: int, mac_or_name: str):
    """模拟连接设备"""
    dev = state.get_device(device_id)
    action = f'connect{device_id}'
    
    # 查找设备
    device_info = None
    
    if mac_or_name in state.devices_found:
        device_info = state.devices_found[mac_or_name]
    else:
        mac_upper = mac_or_name.upper()
        for info in state.scan_results:
            if info['mac'].upper() == mac_upper:
                device_info = info
                break
    
    if device_info is None:
        await send_to_control(ws, action, {
            'success': False,
            'device_id': device_id,
            'error': f"未找到设备: {mac_or_name}",
        })
        return
    
    mac = device_info['mac']
    name = device_info['name']
    rssi = device_info.get('rssi', -50)
    
    log(f"[Dev{device_id}] 模拟连接: {name} ({mac})")
    
    # 如果已连接，先断开
    if dev.is_connected():
        await disconnect_device(ws, device_id, silent=True)
    
    # 模拟连接过程
    await send_to_control(ws, action, {
        'success': None,
        'device_id': device_id,
        'message': f"连接中...",
        'mac': mac,
    })
    
    # 模拟连接延迟
    await asyncio.sleep(0.5 + random.random() * 0.5)
    
    # 设置连接状态
    dev.set_connected(True)
    dev.mac = mac
    dev.name = name
    dev.rssi = rssi
    dev.reset_stats()
    
    log(f"[Dev{device_id}] 连接成功: {mac}")
    
    await send_to_control(ws, action, {
        'success': True,
        'device_id': device_id,
        'mac': mac,
        'name': name,
        'rssi': rssi,
        'connected': state.get_connected_devices(),
    })
    
    # 广播连接事件
    await broadcast_event('device_connected', {
        'device_id': device_id,
        'mac': mac,
        'name': name,
    })


async def disconnect_device(ws, device_id: int, silent=False):
    """模拟断开设备"""
    dev = state.get_device(device_id)
    action = f'disconnect{device_id}'
    mac = dev.mac
    
    try:
        if dev.is_streaming:
            await stop_stream(ws, device_id, silent=True)
        
        dev.set_connected(False)
        dev.mac = None
        dev.name = None
        dev.rssi = None
        dev.connect_task = None
        
        log(f"[Dev{device_id}] 已断开: {mac}")
        
        if not silent:
            await send_to_control(ws, action, {
                'success': True,
                'device_id': device_id,
                'mac': mac,
                'connected': state.get_connected_devices(),
            })
            
            await broadcast_event('device_disconnected', {
                'device_id': device_id,
                'mac': mac,
            })
            
    except Exception as e:
        log(f"[Dev{device_id}] 断开失败: {e}")
        if not silent:
            await send_to_control(ws, action, {
                'success': False,
                'device_id': device_id,
                'error': str(e),
            })


async def start_stream(ws, device_id: int):
    """开始模拟采集"""
    dev = state.get_device(device_id)
    action = f'start{device_id}'
    
    if not dev.is_connected():
        await send_to_control(ws, action, {
            'success': False,
            'device_id': device_id,
            'error': "设备未连接",
        })
        return
    
    if dev.is_streaming:
        await send_to_control(ws, action, {
            'success': False,
            'device_id': device_id,
            'error': "已在采集中",
        })
        return
    
    dev.reset_stats()
    dev.is_streaming = True
    
    log(f"[Dev{device_id}] 开始模拟采集")
    
    await send_to_control(ws, action, {
        'success': True,
        'device_id': device_id,
        'active': state.get_active_devices(),
    })
    
    await broadcast_event('stream_started', {
        'device_id': device_id,
        'active': state.get_active_devices(),
    })


async def stop_stream(ws, device_id: int, silent=False):
    """停止模拟采集"""
    dev = state.get_device(device_id)
    action = f'stop{device_id}'
    
    if not dev.is_streaming:
        if not silent:
            await send_to_control(ws, action, {
                'success': False,
                'device_id': device_id,
                'error': "未在采集",
            })
        return
    
    dev.is_streaming = False
    
    log(f"[Dev{device_id}] 停止模拟采集")
    
    if not silent:
        await send_to_control(ws, action, {
            'success': True,
            'device_id': device_id,
            'total': dev.total_frames,
            'lost': dev.lost_frames,
            'active': state.get_active_devices(),
        })
        
        await broadcast_event('stream_stopped', {
            'device_id': device_id,
            'total': dev.total_frames,
            'lost': dev.lost_frames,
            'active': state.get_active_devices(),
        })


async def start_all(ws):
    """同时开始所有已连接设备"""
    started = []
    
    if state.dev1.is_connected() and not state.dev1.is_streaming:
        await start_stream(ws, 1)
        if state.dev1.is_streaming:
            started.append(1)
    
    if state.dev2.is_connected() and not state.dev2.is_streaming:
        await start_stream(ws, 2)
        if state.dev2.is_streaming:
            started.append(2)
    
    await send_to_control(ws, 'start_all', {
        'success': True,
        'started': started,
        'active': state.get_active_devices(),
    })


async def stop_all(ws):
    """同时停止所有设备"""
    stopped = []
    
    if state.dev1.is_streaming:
        await stop_stream(ws, 1, silent=True)
        stopped.append(1)
    
    if state.dev2.is_streaming:
        await stop_stream(ws, 2, silent=True)
        stopped.append(2)
    
    await send_to_control(ws, 'stop_all', {
        'success': True,
        'stopped': stopped,
        'active': state.get_active_devices(),
        'stats': {
            'dev1': {'total': state.dev1.total_frames, 'lost': state.dev1.lost_frames},
            'dev2': {'total': state.dev2.total_frames, 'lost': state.dev2.lost_frames},
        }
    })


async def get_status(ws):
    """获取状态"""
    await send_to_control(ws, 'status', {
        'dev1': state.dev1.to_dict(),
        'dev2': state.dev2.to_dict(),
        'connected': state.get_connected_devices(),
        'active': state.get_active_devices(),
        'control_clients': len(state.control_clients),
        'data_clients': len(state.data_clients),
    })


# ================= WebSocket 处理 =================

async def handle_control_client(websocket):
    """处理控制端客户端（index.html）"""
    state.control_clients.add(websocket)
    log(f"[控制端] 客户端已连接 (总数: {len(state.control_clients)})")
    
    # 发送当前状态
    await send_to_control(websocket, 'welcome', {
        'message': '控制端已连接 (模拟模式)',
        'dev1': state.dev1.to_dict(),
        'dev2': state.dev2.to_dict(),
        'connected': state.get_connected_devices(),
        'active': state.get_active_devices(),
    })
    
    try:
        async for message in websocket:
            try:
                if isinstance(message, bytes):
                    data = msgpack.unpackb(message)
                else:
                    data = json.loads(message)
                
                action = data.get('action', '')
                log(f"[控制端] 命令: {action}")
                
                # 扫描
                if action == 'scan':
                    await scan_devices(websocket)
                
                # 设备1
                elif action == 'connect1':
                    target = data.get('mac') or data.get('display')
                    if target:
                        if state.dev1.connect_task and not state.dev1.connect_task.done():
                            await send_to_control(websocket, 'connect1', {
                                'success': False, 'device_id': 1, 'error': "连接中"
                            })
                        else:
                            state.dev1.connect_task = asyncio.create_task(
                                connect_device(websocket, 1, target)
                            )
                    else:
                        await send_to_control(websocket, 'connect1', {
                            'success': False, 'device_id': 1, 'error': "请提供 mac"
                        })
                
                elif action == 'disconnect1':
                    await disconnect_device(websocket, 1)
                
                elif action == 'start1':
                    await start_stream(websocket, 1)
                
                elif action == 'stop1':
                    await stop_stream(websocket, 1)
                
                # 设备2
                elif action == 'connect2':
                    target = data.get('mac') or data.get('display')
                    if target:
                        if state.dev2.connect_task and not state.dev2.connect_task.done():
                            await send_to_control(websocket, 'connect2', {
                                'success': False, 'device_id': 2, 'error': "连接中"
                            })
                        else:
                            state.dev2.connect_task = asyncio.create_task(
                                connect_device(websocket, 2, target)
                            )
                    else:
                        await send_to_control(websocket, 'connect2', {
                            'success': False, 'device_id': 2, 'error': "请提供 mac"
                        })
                
                elif action == 'disconnect2':
                    await disconnect_device(websocket, 2)
                
                elif action == 'start2':
                    await start_stream(websocket, 2)
                
                elif action == 'stop2':
                    await stop_stream(websocket, 2)
                
                # 全局
                elif action == 'start_all':
                    await start_all(websocket)
                
                elif action == 'stop_all':
                    await stop_all(websocket)
                
                elif action == 'status':
                    await get_status(websocket)
                
                else:
                    await send_to_control(websocket, 'error', {
                        'error': f"未知命令: {action}"
                    })
                    
            except Exception as e:
                log(f"[控制端] 处理错误: {e}")
                traceback.print_exc()
                await send_to_control(websocket, 'error', {'error': str(e)})
                
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        state.control_clients.discard(websocket)
        log(f"[控制端] 客户端断开 (剩余: {len(state.control_clients)})")


async def handle_data_client(websocket):
    """处理数据端客户端（realtimeEngine.js）"""
    state.data_clients.add(websocket)
    log(f"[数据端] 客户端已连接 (总数: {len(state.data_clients)})")
    
    # 发送欢迎消息
    welcome = {
        'type': 'welcome',
        'message': '数据端已连接 (模拟模式)',
        'active': state.get_active_devices(),
        'connected': state.get_connected_devices(),
    }
    try:
        await websocket.send(json.dumps(welcome, ensure_ascii=False))
    except:
        pass
    
    try:
        # 数据端主要是接收数据，但也可以接收简单命令
        async for message in websocket:
            try:
                if isinstance(message, bytes):
                    data = msgpack.unpackb(message)
                else:
                    data = json.loads(message)
                
                action = data.get('action', '')
                
                # 数据端只支持状态查询
                if action == 'status':
                    status = {
                        'type': 'status',
                        'active': state.get_active_devices(),
                        'connected': state.get_connected_devices(),
                        'dev1': state.dev1.to_dict(),
                        'dev2': state.dev2.to_dict(),
                    }
                    await websocket.send(json.dumps(status, ensure_ascii=False))
                    
            except Exception as e:
                log(f"[数据端] 处理错误: {e}")
                
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        state.data_clients.discard(websocket)
        log(f"[数据端] 客户端断开 (剩余: {len(state.data_clients)})")


# ================= 主函数 =================

async def main():
    state.main_loop = asyncio.get_running_loop()
    
    # 启动数据发送线程
    state.stop_thread = False
    state.data_thread = threading.Thread(target=data_sender_thread, daemon=True)
    state.data_thread.start()
    
    log("=" * 60)
    log("BLE Server (模拟模式 - 无需硬件) 已启动")
    log("=" * 60)
    log(f"控制端口: ws://{WEBSOCKET_HOST}:{CONTROL_PORT} (index.html)")
    log(f"数据端口: ws://{WEBSOCKET_HOST}:{DATA_PORT} (realtimeEngine.js)")
    log("=" * 60)
    log("模拟设备列表:")
    for dev in MOCK_DEVICES:
        log(f"  - {dev['name']} ({dev['mac']}) RSSI: {dev['rssi']}")
    log("=" * 60)
    log("控制命令:")
    log("  scan, connect1/2, disconnect1/2")
    log("  start1/2, stop1/2, start_all, stop_all")
    log("  status")
    log("=" * 60)
    
    try:
        # 启动两个 WebSocket 服务器
        control_server = await websockets.serve(
            handle_control_client,
            WEBSOCKET_HOST,
            CONTROL_PORT,
            max_size=1 * 1024 * 1024,
        )
        
        data_server = await websockets.serve(
            handle_data_client,
            WEBSOCKET_HOST,
            DATA_PORT,
            max_size=10 * 1024 * 1024,
        )
        
        log("服务器启动完成，等待连接...")
        
        await asyncio.Future()  # 永久运行
        
    finally:
        state.stop_thread = True
        if state.data_thread:
            state.data_thread.join(timeout=2.0)
        log("服务已停止")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("用户中断")
    except Exception as e:
        log(f"启动失败: {e}")
        traceback.print_exc()
