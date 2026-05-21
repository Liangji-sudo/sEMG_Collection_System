"""
BLE Server Sim V2 — 模拟 wband_emg_V2 设备的 ble_server.py 数据输出
=====================================================================
模拟 V2 硬件 (LSM6DSV32X IMU, 3 chips, 无磁力计)，直接输出 JSON 数据包，
用于测试 realtimeEngine.js → waveform.js → storage_server.py 的 V2 链路。

与 ble_server_sim.py (V1) 的关键差异：
  - IMU: 3 chips × [acc, gyr]，无 mag
  - hw_version: "V2", num_imus: 3
  - 批量包: dev1/dev2 有时为单对象，有时为长度为 2-5 的数组
  - 通道映射: 使用 CHANNELS_MAP_V2（与真实 ble_server.py 一致）
  - 支持 set_session_id / sd_filenames_updated（对齐真实 ble_server.py）

前端显示: 每个设备只显示第 0 个 IMU 的 Acc/Gyr，隐藏 Mag
HDF5 存储: imu*_all_ble 每包保存 3 行 (imu_index=0/1/2)，acc+gyr only

支持的控制命令 (action):
  scan, connect1/2, disconnect1/2, start1/2, stop1/2, start_all, stop_all, status
  set_session_id

端口 (与 ble_server.py 相同，使用前必须停掉真实 ble_server.py):
  控制端: ws://localhost:8764  (index.html)
  数据端: ws://localhost:8766  (realtimeEngine.js)

直接运行:  python ble_server_sim_v2.py
"""

import asyncio
import json
import math
import random
import struct
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from itertools import count
from queue import PriorityQueue
from typing import Any, Dict, List, Optional, Set

import websockets

# -------- 可选依赖 --------
try:
    import msgpack  # noqa: F401 — 保留导入以匹配 ble_server.py 行为
    HAS_MSGPACK = True
except ImportError:
    HAS_MSGPACK = False

try:
    import numpy as np  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


# ==================== 服务配置 (与 ble_server.py 一致) ====================
WEBSOCKET_HOST = "localhost"
CONTROL_PORT = 8764
DATA_PORT = 8766

# ==================== 模拟设备列表 ====================
MOCK_DEVICE_PREFIX = "ESP32S3_EMG"
MOCK_DEVICES = [
    {"name": "ESP32S3_EMG_V2_001", "mac": "AA:BB:CC:DD:EE:11", "rssi": -42},
    {"name": "ESP32S3_EMG_V2_002", "mac": "AA:BB:CC:DD:EE:22", "rssi": -48},
    {"name": "ESP32S3_EMG_V2_003", "mac": "AA:BB:CC:DD:EE:33", "rssi": -65},
]

# ==================== V2 常量 (与 ble_server.py 一致) ====================
CHANNELS_MAP_V2 = [15, 16, 14, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
SCALE_ACCEL = 32.0 / 32768.0           # V2 LSM6DSV32X ±32g
SCALE_GYRO = 2000.0 / 32768.0
BASE_LSB_24BIT = 0.2861                 # μV per LSB (24-bit, 2.4V ref / 2^23 * 1e6)
HARDWARE_FRONTEND_GAIN = 12             # V2 硬件前端增益

# ==================== 模拟 V2 固定配置 ====================
FRAMES_PER_PACKET = 9
BLE_SAMPLE_RATE = 250                   # BLE 传输 250Hz
FRAME_INTERVAL = 1.0 / BLE_SAMPLE_RATE  # 0.004 秒
NUM_IMUS_V2 = 3                         # V2 固定 3 个 IMU
SEND_INTERVAL = 0.036                   # 每包约 36ms (9帧/250Hz)

# ==================== 批量发送配置 ====================
BATCH_MIN = 2
BATCH_MAX = 5
BATCH_PROBABILITY = 0.5                 # 50% 概率发送批量包（其余发送单包）

# ==================== 优先级 ====================
PRIORITY_CONTROL = 0
PRIORITY_HIGH = 1
PRIORITY_LOW = 2


def log(message: str):
    print(f"[BLE-Mock-V2] {message}", file=sys.stderr, flush=True)


# ==================== V2 模拟数据生成器 ====================

class MockDataGeneratorV2:
    """V2 模拟数据生成器 — 生成符合 ble_server.py V2 输出格式的数据"""

    def __init__(self, device_id: int):
        self.device_id = device_id
        self.frame_index = 0            # BLE 帧号累计
        self._t = 0.0                   # 内部信号时间 (秒)

        # ---- EMG 参数 (每个设备有不同特征) ----
        offset = 0.3 if device_id == 2 else 0.0  # dev2 偏移以区分
        self.emg_freqs = [18 + offset * 10 + i * 2.5 for i in range(16)]
        self.emg_amps = [40 + random.uniform(-15, 15) + offset * 30 for _ in range(16)]
        self.emg_phases = [random.uniform(0, 2 * math.pi) for _ in range(16)]
        self.emg_noise = 12 + device_id * 3

        # 肌肉活动包络
        self._activity = [0.5] * 16
        self._activity_target = [0.5] * 16

        # ---- IMU 参数 (3 chips, acc+gyr only) ----
        self.acc_bias = [[random.uniform(-0.03, 0.03) for _ in range(3)] for _ in range(3)]
        self.gyr_bias = [[random.uniform(-1.5, 1.5) for _ in range(3)] for _ in range(3)]
        self.motion_freq = 0.4 + random.uniform(-0.15, 0.15)
        # 三个 IMU 的相位差以产生可区分波形
        self.imu_phases = [0.0, 2.0 * math.pi / 3.0, 4.0 * math.pi / 3.0]

    # -------- EMG --------

    def _generate_emg_channel(self, ch: int) -> float:
        """生成单通道 EMG μV 值"""
        if random.random() < 0.015:
            self._activity_target[ch] = 0.15 + random.random() * 0.85
        diff = self._activity_target[ch] - self._activity[ch]
        self._activity[ch] += diff * 0.04

        t = self._t
        f = self.emg_freqs[ch]
        amp = self.emg_amps[ch] * self._activity[ch]
        phi = self.emg_phases[ch]

        signal = amp * math.sin(2 * math.pi * f * t + phi)
        signal += amp * 0.25 * math.sin(2 * math.pi * f * 2 * t + phi * 1.4)
        signal += amp * 0.12 * math.sin(2 * math.pi * f * 0.5 * t + phi * 0.6)

        if random.random() < 0.008:
            signal += random.uniform(-80, 80) * self._activity[ch]

        if HAS_NUMPY:
            signal += np.random.normal(0, self.emg_noise)
        else:
            u1, u2 = random.random(), random.random()
            signal += math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2) * self.emg_noise

        return round(signal, 6)

    def _generate_emg_frame(self) -> List[float]:
        """生成一帧 16 通道 EMG μV 数据 (物理通道顺序)"""
        frame = [self._generate_emg_channel(ch) for ch in range(16)]
        self._t += 1.0 / 2000.0  # 每帧 0.5ms (2kHz)
        return frame

    def _apply_channel_map(self, frames: List[List[float]]) -> List[List[float]]:
        """应用 V2 通道映射 (1-indexed → 0-indexed)"""
        return [[row[i - 1] for i in CHANNELS_MAP_V2] for row in frames]

    def generate_emg_packet(self) -> Dict:
        """生成一个 EMG 数据包的 EMG 部分"""
        raw_frames = []   # 通道映射后的 raw ADC
        uv_frames = []    # 通道映射后的 μV

        start_frame = self.frame_index

        for i in range(FRAMES_PER_PACKET):
            # 物理通道顺序的 μV
            phys_uv = self._generate_emg_frame()
            # 物理通道 raw ADC (24-bit LSB)
            lsb_uv = BASE_LSB_24BIT / (12 * HARDWARE_FRONTEND_GAIN)
            phys_raw = [int(v / lsb_uv) for v in phys_uv]
            raw_frames.append(phys_raw)
            uv_frames.append(phys_uv)
            self.frame_index += 1

        # 应用通道映射
        raw_mapped = self._apply_channel_map(raw_frames)
        uv_mapped = self._apply_channel_map(uv_frames)

        frame_ids = [start_frame + i for i in range(FRAMES_PER_PACKET)]

        return {
            'f': start_frame,
            'n': FRAMES_PER_PACKET,
            'frame_ids': frame_ids,
            'raw': raw_mapped,
            'uv': uv_mapped,
        }

    # -------- IMU (V2) --------

    def generate_imu_data(self) -> List[List[List[float]]]:
        """
        生成 V2 IMU 数据: 3 chips × [acc[3], gyr[3]] (无 mag)
        返回: [
            [[ax,ay,az], [gx,gy,gz]],   # IMU 0
            [[ax,ay,az], [gx,gy,gz]],   # IMU 1
            [[ax,ay,az], [gx,gy,gz]],   # IMU 2
        ]
        """
        t = self._t  # 当前信号时间
        imus = []

        for i in range(NUM_IMUS_V2):
            phi = self.imu_phases[i]
            mf = self.motion_freq

            # 加速度计 (g)
            ax = self.acc_bias[i][0] + 0.12 * math.sin(2 * math.pi * mf * t + phi)
            ay = self.acc_bias[i][1] + 0.12 * math.cos(2 * math.pi * mf * t + phi)
            az = 1.0 + self.acc_bias[i][2] + 0.06 * math.sin(2 * math.pi * mf * 2 * t + phi)
            acc = [round(ax + random.uniform(-0.015, 0.015), 6),
                   round(ay + random.uniform(-0.015, 0.015), 6),
                   round(az + random.uniform(-0.015, 0.015), 6)]

            # 陀螺仪 (°/s)
            gx = self.gyr_bias[i][0] + 8 * math.sin(2 * math.pi * mf * t + phi * 1.3)
            gy = self.gyr_bias[i][1] + 8 * math.cos(2 * math.pi * mf * t + phi * 1.1)
            gz = self.gyr_bias[i][2] + 4 * math.sin(2 * math.pi * mf * 0.7 * t + phi)
            gyr = [round(gx + random.uniform(-1.5, 1.5), 6),
                   round(gy + random.uniform(-1.5, 1.5), 6),
                   round(gz + random.uniform(-1.5, 1.5), 6)]

            imus.append([acc, gyr])  # V2: [acc, gyr] only, no mag

        return imus

    # -------- 完整数据包 --------

    def generate_packet(self, ts: float) -> Dict:
        """
        生成一个完整的 V2 数据包 (与 ble_server.py parse_packet + handler 输出一致)

        返回结构:
        {
            f, n, frame_ids, raw, uv,           # EMG
            imu, num_imus, hw_version,           # IMU (V2 格式)
            emg_t, imu_t, t,                     # 时间戳
            s                                     # 统计
        }
        """
        emg = self.generate_emg_packet()

        # EMG 时间戳 (从 ts 向前推算)
        emg_timestamps = [
            ts - (FRAMES_PER_PACKET - 1 - i) * FRAME_INTERVAL
            for i in range(FRAMES_PER_PACKET)
        ]

        imu = self.generate_imu_data()
        imu_timestamps = [ts] * len(imu)  # 同一 BLE 包内所有 IMU 共享时间戳

        return {
            'f': emg['f'],
            'n': emg['n'],
            'frame_ids': emg['frame_ids'],
            'raw': emg['raw'],
            'uv': emg['uv'],
            'emg_t': emg_timestamps,
            't': ts,
            'imu': imu,
            'num_imus': NUM_IMUS_V2,
            'hw_version': 'V2',
            'imu_t': imu_timestamps,
        }

    def reset(self):
        self.frame_index = 0
        self._t = 0.0
        self._activity = [0.5] * 16
        self._activity_target = [0.5] * 16


# ==================== DeviceState (支持 V2 字段) ====================

@dataclass
class DeviceState:
    """单个模拟 BLE 设备状态 (V2)"""
    device_id: int

    # 连接状态
    _connected: bool = False
    mac: Optional[str] = None
    name: Optional[str] = None
    rssi: Optional[int] = None

    is_streaming: bool = False
    total_frames: int = 0
    lost_frames: int = 0
    last_frame_index: int = -1
    last_data_time: float = 0.0

    # V2 设备信息
    hw_version: str = "V2"
    firmware_version: str = "v2.1.0-sim"
    hardware_version: str = "ESP32S3-EMG-V2"
    num_imus: int = NUM_IMUS_V2

    # SD卡文件名
    sd_filename: Optional[str] = None

    # 数据生成器
    data_generator: Optional[MockDataGeneratorV2] = None

    def __post_init__(self):
        self.data_generator = MockDataGeneratorV2(self.device_id)

    def reset_stats(self):
        self.total_frames = 0
        self.lost_frames = 0
        self.last_frame_index = -1
        self.sd_filename = None
        if self.data_generator:
            self.data_generator.reset()

    def is_connected(self) -> bool:
        return self._connected

    def set_connected(self, connected: bool):
        self._connected = connected

    def to_dict(self) -> dict:
        return {
            'connected': self.is_connected(),
            'mac': self.mac,
            'name': self.name,
            'rssi': self.rssi,
            'streaming': self.is_streaming,
            'total': self.total_frames,
            'lost': self.lost_frames,
            'hw_version': self.hw_version,
            'num_imus': self.num_imus,
            'firmware_version': self.firmware_version,
            'hardware_version': self.hardware_version,
        }


# ==================== 全局状态 ====================

class ServerState:
    def __init__(self):
        self.control_clients: Set = set()
        self.data_clients: Set = set()
        self.dev1 = DeviceState(device_id=1)
        self.dev2 = DeviceState(device_id=2)
        self.devices_found: Dict[str, Any] = {}
        self.scan_results: List[dict] = []
        self.msg_queue = PriorityQueue()
        self.queue_seq = count()
        self.main_loop = None
        self.data_thread = None
        self.stop_thread = False
        self.session_id: str = ""

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


# ==================== 消息队列 ====================

def add_to_queue(priority: int, msg_type: str, data: dict, target_ws=None):
    try:
        q = state.msg_queue
        if priority == PRIORITY_LOW and q.qsize() > 500:
            try:
                old = q.get_nowait()
                if old[0] <= PRIORITY_HIGH:
                    q.put(old)
            except Exception:
                pass
        seq = next(state.queue_seq)
        q.put((priority, seq, msg_type, data, target_ws))
        if state.main_loop:
            asyncio.run_coroutine_threadsafe(process_queue(), state.main_loop)
    except Exception as e:
        log(f"入队错误: {e}")


async def process_queue():
    q = state.msg_queue
    try:
        while not q.empty():
            prio, seq, msg_type, data, target_ws = q.get_nowait()
            try:
                payload = json.dumps(data, ensure_ascii=False)
                if msg_type == 'control':
                    targets = [target_ws] if target_ws else list(state.control_clients)
                    for ws in targets:
                        if ws:
                            try:
                                await ws.send(payload)
                            except Exception:
                                pass
                elif msg_type == 'data':
                    for ws in list(state.data_clients):
                        try:
                            await ws.send(payload)
                        except Exception:
                            pass
                elif msg_type == 'broadcast':
                    all_clients = list(state.control_clients) + list(state.data_clients)
                    for ws in all_clients:
                        try:
                            await ws.send(payload)
                        except Exception:
                            pass
            except Exception as e:
                log(f"发送错误: {e}")
    except Exception as e:
        log(f"队列处理错误: {e}")


async def send_to_control(ws, action: str, data: dict):
    msg = {'type': 'response', 'action': action, **data}
    add_to_queue(PRIORITY_HIGH, 'control', msg, ws)


async def broadcast_event(event: str, data: dict):
    msg = {'type': 'event', 'event': event, **data}
    add_to_queue(PRIORITY_HIGH, 'broadcast', msg)


# ==================== 数据发送线程 ====================

def _build_dev_packet(dev: DeviceState, ts: float) -> dict:
    """构建单个设备的单包数据，添加统计信息"""
    packet = dev.data_generator.generate_packet(ts)
    dev.total_frames += packet['n']
    packet['s'] = [dev.total_frames, dev.lost_frames]
    dev.last_data_time = ts
    return packet


def data_sender_thread():
    """数据发送线程 — 生成 V2 格式模拟数据并批量/单包发送"""
    log("V2 数据发送线程启动")
    send_count = 0
    last_log_time = time.time()

    while not state.stop_thread:
        try:
            active = state.get_active_devices()
            use_batch = False
            batch_size = 1

            if state.data_clients and active:
                base_ts = time.time()
                dev1_data = None
                dev2_data = None

                # ---- 决定本周期是否发送批量包 ----
                use_batch = random.random() < BATCH_PROBABILITY
                batch_size = random.randint(BATCH_MIN, BATCH_MAX) if use_batch else 1

                # ---- Dev1 ----
                if state.dev1.is_streaming and state.dev1.data_generator:
                    if use_batch:
                        packets = [
                            _build_dev_packet(state.dev1, base_ts + k * SEND_INTERVAL)
                            for k in range(batch_size)
                        ]
                        dev1_data = packets if len(packets) > 1 else packets[0]
                    else:
                        dev1_data = _build_dev_packet(state.dev1, base_ts)

                # ---- Dev2 ----
                if state.dev2.is_streaming and state.dev2.data_generator:
                    if use_batch:
                        packets = [
                            _build_dev_packet(state.dev2, base_ts + k * SEND_INTERVAL)
                            for k in range(batch_size)
                        ]
                        dev2_data = packets if len(packets) > 1 else packets[0]
                    else:
                        dev2_data = _build_dev_packet(state.dev2, base_ts)

                # ---- 组装并发送 ----
                if dev1_data is not None or dev2_data is not None:
                    msg = {
                        'type': 'data',
                        'ts': base_ts + (batch_size - 1) * SEND_INTERVAL,
                        'dev1': dev1_data,
                        'dev2': dev2_data,
                        'active': active,
                        'timeout': {'dev1': False, 'dev2': False},
                    }
                    add_to_queue(PRIORITY_LOW, 'data', msg)
                    send_count += 1

            # ---- 周期日志 ----
            now = time.time()
            if now - last_log_time >= 5.0:
                if send_count > 0:
                    log(f"[数据发送] 已发送 {send_count} 批, 活跃: {active}, "
                        f"Dev1帧={state.dev1.total_frames}, Dev2帧={state.dev2.total_frames}")
                last_log_time = now
                send_count = 0

            # batch_size > 1 时按批量大小延长 sleep，避免模拟器数据速率过快
            # 注释掉下面这行可切换为压力测试模式（不限速）
            sleep_duration = max(SEND_INTERVAL, batch_size * SEND_INTERVAL) if use_batch else SEND_INTERVAL
            time.sleep(sleep_duration)

        except Exception as e:
            if not state.stop_thread:
                log(f"发送线程错误: {e}")
                traceback.print_exc()

    log("V2 数据发送线程结束")


# ==================== BLE 操作 (与 ble_server_sim.py 兼容) ====================

async def scan_devices(ws):
    log("模拟扫描设备...")
    await asyncio.sleep(1.0 + random.random() * 0.5)

    state.devices_found.clear()
    state.scan_results.clear()

    for dev in MOCK_DEVICES:
        display = f"{dev['name']} ({dev['mac']})"
        state.devices_found[display] = dev
        state.scan_results.append({
            'name': dev['name'],
            'mac': dev['mac'],
            'display': display,
            'rssi': dev['rssi'],
            'manufacturer': 'Mock V2 Device',
        })

    state.scan_results.sort(key=lambda x: x['rssi'], reverse=True)
    log(f"扫描完成: {len(state.scan_results)} 个设备")

    await send_to_control(ws, 'scan', {
        'success': True,
        'devices': state.scan_results,
        'count': len(state.scan_results),
        'targets': [d for d in state.scan_results if MOCK_DEVICE_PREFIX in d['name']],
    })


async def connect_device(ws, device_id: int, mac_or_name: str):
    dev = state.get_device(device_id)
    action = f'connect{device_id}'

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
            'success': False, 'device_id': device_id,
            'error': f"未找到设备: {mac_or_name}",
        })
        return

    mac = device_info['mac']
    name = device_info['name']
    rssi = device_info.get('rssi', -50)

    if dev.is_connected():
        await disconnect_device(ws, device_id, silent=True)

    log(f"[Dev{device_id}] 模拟连接: {name} ({mac})")

    await asyncio.sleep(0.3 + random.random() * 0.4)

    dev.set_connected(True)
    dev.mac = mac
    dev.name = name
    dev.rssi = rssi
    dev.reset_stats()

    # V2 字段已在 DeviceState 默认值中设置

    log(f"[Dev{device_id}] 连接成功 (V2)")

    await send_to_control(ws, action, {
        'success': True,
        'device_id': device_id,
        'mac': mac,
        'name': name,
        'rssi': rssi,
        'connected': state.get_connected_devices(),
        'hw_version': dev.hw_version,
    })

    await broadcast_event('device_connected', {
        'device_id': device_id, 'mac': mac, 'name': name,
    })


async def disconnect_device(ws, device_id: int, silent=False):
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
        dev.sd_filename = None

        log(f"[Dev{device_id}] 已断开: {mac}")

        if not silent:
            await send_to_control(ws, action, {
                'success': True, 'device_id': device_id, 'mac': mac,
                'connected': state.get_connected_devices(),
            })
            await broadcast_event('device_disconnected', {
                'device_id': device_id, 'mac': mac,
            })
    except Exception as e:
        log(f"[Dev{device_id}] 断开失败: {e}")
        if not silent:
            await send_to_control(ws, action, {
                'success': False, 'device_id': device_id, 'error': str(e),
            })


async def start_stream(ws, device_id: int):
    dev = state.get_device(device_id)
    action = f'start{device_id}'

    if not dev.is_connected():
        await send_to_control(ws, action, {
            'success': False, 'device_id': device_id, 'error': "设备未连接",
        })
        return

    if dev.is_streaming:
        await send_to_control(ws, action, {
            'success': False, 'device_id': device_id, 'error': "已在采集中",
        })
        return

    dev.reset_stats()

    # ---- 生成模拟 SD 卡文件名 (对齐真实 ble_server.py) ----
    now_str = datetime.now().strftime("%y%m%d_%H%M%S")
    hand_label = "L" if device_id == 1 else "R"
    if state.session_id:
        dev.sd_filename = f"{state.session_id}_{hand_label}_{now_str}"
    else:
        dev.sd_filename = f"SIM_{hand_label}_{now_str}"

    dev.is_streaming = True

    log(f"[Dev{device_id}] 开始 V2 模拟采集 (sd_filename={dev.sd_filename})")

    await send_to_control(ws, action, {
        'success': True, 'device_id': device_id,
        'active': state.get_active_devices(),
    })
    await broadcast_event('stream_started', {
        'device_id': device_id, 'active': state.get_active_devices(),
    })


async def stop_stream(ws, device_id: int, silent=False):
    dev = state.get_device(device_id)
    action = f'stop{device_id}'

    if not dev.is_streaming:
        if not silent:
            await send_to_control(ws, action, {
                'success': False, 'device_id': device_id, 'error': "未在采集",
            })
        return

    dev.is_streaming = False
    log(f"[Dev{device_id}] 停止采集: total={dev.total_frames}")

    if not silent:
        await send_to_control(ws, action, {
            'success': True, 'device_id': device_id,
            'total': dev.total_frames, 'lost': dev.lost_frames,
            'active': state.get_active_devices(),
        })
        await broadcast_event('stream_stopped', {
            'device_id': device_id, 'total': dev.total_frames,
            'lost': dev.lost_frames, 'active': state.get_active_devices(),
        })


async def start_all(ws):
    started = []
    for did in [1, 2]:
        dev = state.get_device(did)
        if dev.is_connected() and not dev.is_streaming:
            await start_stream(ws, did)
            if dev.is_streaming:
                started.append(did)

    # 收集 SD 卡 bin 文件名和设备名称
    sd_filenames = {}
    if state.dev1.sd_filename:
        sd_filenames['dev1'] = state.dev1.sd_filename
    if state.dev2.sd_filename:
        sd_filenames['dev2'] = state.dev2.sd_filename

    device_names = {}
    if state.dev1.name:
        device_names['dev1'] = state.dev1.name
    if state.dev2.name:
        device_names['dev2'] = state.dev2.name

    await send_to_control(ws, 'start_all', {
        'success': True, 'started': started,
        'active': state.get_active_devices(),
        'sd_filenames': sd_filenames,
        'device_names': device_names,
    })

    # 广播 sd_filenames_updated 事件给数据端 (realtimeEngine.js)
    if sd_filenames or device_names:
        await broadcast_event('sd_filenames_updated', {
            'sd_filenames': sd_filenames,
            'device_names': device_names,
        })


async def stop_all(ws):
    stopped = []
    for did in [1, 2]:
        dev = state.get_device(did)
        if dev.is_streaming:
            await stop_stream(ws, did, silent=True)
            stopped.append(did)
    await send_to_control(ws, 'stop_all', {
        'success': True, 'stopped': stopped,
        'active': state.get_active_devices(),
        'stats': {
            'dev1': {'total': state.dev1.total_frames, 'lost': state.dev1.lost_frames},
            'dev2': {'total': state.dev2.total_frames, 'lost': state.dev2.lost_frames},
        },
    })


async def get_status(ws):
    await send_to_control(ws, 'status', {
        'dev1': state.dev1.to_dict(),
        'dev2': state.dev2.to_dict(),
        'connected': state.get_connected_devices(),
        'active': state.get_active_devices(),
        'control_clients': len(state.control_clients),
        'data_clients': len(state.data_clients),
    })


# ==================== WebSocket 处理 ====================

async def handle_control_client(websocket):
    state.control_clients.add(websocket)
    log(f"[控制端] 客户端已连接 (总数: {len(state.control_clients)})")

    await send_to_control(websocket, 'welcome', {
        'message': '控制端已连接 (V2 模拟模式)',
        'dev1': state.dev1.to_dict(),
        'dev2': state.dev2.to_dict(),
        'connected': state.get_connected_devices(),
        'active': state.get_active_devices(),
    })

    try:
        async for message in websocket:
            try:
                if isinstance(message, bytes):
                    data = json.loads(message.decode('utf-8'))
                else:
                    data = json.loads(message)

                action = data.get('action', '')

                if action == 'scan':
                    await scan_devices(websocket)

                elif action == 'connect1':
                    target = data.get('mac') or data.get('display')
                    if target:
                        if state.dev1.connect_task and not state.dev1.connect_task.done():
                            await send_to_control(websocket, 'connect1', {
                                'success': False, 'device_id': 1, 'error': "连接中",
                            })
                        else:
                            state.dev1.connect_task = asyncio.create_task(
                                connect_device(websocket, 1, target))
                    else:
                        await send_to_control(websocket, 'connect1', {
                            'success': False, 'device_id': 1, 'error': "请提供 mac",
                        })

                elif action == 'disconnect1':
                    await disconnect_device(websocket, 1)

                elif action == 'start1':
                    await start_stream(websocket, 1)

                elif action == 'stop1':
                    await stop_stream(websocket, 1)

                elif action == 'connect2':
                    target = data.get('mac') or data.get('display')
                    if target:
                        if state.dev2.connect_task and not state.dev2.connect_task.done():
                            await send_to_control(websocket, 'connect2', {
                                'success': False, 'device_id': 2, 'error': "连接中",
                            })
                        else:
                            state.dev2.connect_task = asyncio.create_task(
                                connect_device(websocket, 2, target))
                    else:
                        await send_to_control(websocket, 'connect2', {
                            'success': False, 'device_id': 2, 'error': "请提供 mac",
                        })

                elif action == 'disconnect2':
                    await disconnect_device(websocket, 2)

                elif action == 'start2':
                    await start_stream(websocket, 2)

                elif action == 'stop2':
                    await stop_stream(websocket, 2)

                elif action == 'start_all':
                    await start_all(websocket)

                elif action == 'stop_all':
                    await stop_all(websocket)

                elif action == 'status':
                    await get_status(websocket)

                elif action == 'set_session_id':
                    session_id = data.get('session_id', '')
                    if session_id:
                        clean_id = ''.join(c for c in session_id if c.isalnum() or c == '_')
                        if len(clean_id) > 10:
                            clean_id = clean_id[:10]
                        state.session_id = clean_id
                        log(f"[控制端] 会话ID已设置: {state.session_id}")
                        await send_to_control(websocket, 'set_session_id', {
                            'success': True,
                            'session_id': state.session_id,
                        })
                    else:
                        state.session_id = ""
                        log(f"[控制端] 会话ID已清空")
                        await send_to_control(websocket, 'set_session_id', {
                            'success': True,
                            'session_id': "",
                        })

                else:
                    await send_to_control(websocket, 'error', {
                        'error': f"未知命令: {action}",
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
    state.data_clients.add(websocket)
    log(f"[数据端] 客户端已连接 (总数: {len(state.data_clients)})")

    welcome = {
        'type': 'welcome',
        'message': '数据端已连接 (V2 模拟模式)',
        'active': state.get_active_devices(),
        'connected': state.get_connected_devices(),
    }
    try:
        await websocket.send(json.dumps(welcome, ensure_ascii=False))
    except Exception:
        pass

    try:
        async for message in websocket:
            try:
                if isinstance(message, bytes):
                    data = json.loads(message.decode('utf-8'))
                else:
                    data = json.loads(message)

                if data.get('action') == 'status':
                    status_msg = {
                        'type': 'status',
                        'active': state.get_active_devices(),
                        'connected': state.get_connected_devices(),
                        'dev1': state.dev1.to_dict(),
                        'dev2': state.dev2.to_dict(),
                    }
                    await websocket.send(json.dumps(status_msg, ensure_ascii=False))

            except Exception as e:
                log(f"[数据端] 处理错误: {e}")

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        state.data_clients.discard(websocket)
        log(f"[数据端] 客户端断开 (剩余: {len(state.data_clients)})")


# ==================== 主函数 ====================

async def main():
    state.main_loop = asyncio.get_running_loop()

    state.stop_thread = False
    state.data_thread = threading.Thread(target=data_sender_thread, daemon=True)
    state.data_thread.start()

    log("=" * 60)
    log("BLE Server (V2 模拟模式 — wband_emg_V2) 已启动")
    log("=" * 60)
    log(f"控制端口: ws://{WEBSOCKET_HOST}:{CONTROL_PORT}  (index.html)")
    log(f"数据端口: ws://{WEBSOCKET_HOST}:{DATA_PORT}    (realtimeEngine.js)")
    log("=" * 60)
    log("V2 模拟特性:")
    log(f"  - IMU: {NUM_IMUS_V2} chips (LSM6DSV32X), acc+gyr only, 无磁力计")
    log(f"  - 前端显示: 第 0 个 IMU 的 Acc/Gyr (不显示 Mag); HDF5 imu*_all_ble 保存 3 行")
    log(f"  - hw_version: V2, num_imus: {NUM_IMUS_V2}")
    log(f"  - 批量包: {BATCH_PROBABILITY*100:.0f}% 概率发送 {BATCH_MIN}-{BATCH_MAX} 个包/批")
    log(f"  - 发送间隔: {SEND_INTERVAL*1000:.0f}ms (9帧/包, 250Hz)")
    log("=" * 60)
    log("模拟设备 (V2):")
    for dev in MOCK_DEVICES:
        log(f"  - {dev['name']} ({dev['mac']}) RSSI: {dev['rssi']}")
    log("=" * 60)
    log("控制命令: scan, connect1/2, disconnect1/2,")
    log("           start1/2, stop1/2, start_all, stop_all, status,")
    log("           set_session_id")
    log("=" * 60)
    log("⚠️  使用前请确保已停止真实 ble_server.py (端口冲突)")
    log("=" * 60)

    async def serve():
        control_server = await websockets.serve(
            handle_control_client, WEBSOCKET_HOST, CONTROL_PORT,
            max_size=2 * 1024 * 1024,
        )
        data_server = await websockets.serve(
            handle_data_client, WEBSOCKET_HOST, DATA_PORT,
            max_size=10 * 1024 * 1024,
        )
        log("WebSocket 服务器启动完成，等待连接...")
        await asyncio.Future()  # 永久运行
        # Unreachable, but for cleanup:
        control_server.close()
        data_server.close()

    try:
        await serve()
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
