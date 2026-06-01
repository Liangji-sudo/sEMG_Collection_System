"""
BLE Server Sim V3 — 模拟 wband_emg_V2 设备 + preview/collection 切流
====================================================================
基于 ble_server_sim_v2.py，新增 preview/collection 延时切流方案验证支持。

与 v2 的关键差异：
  - stream_mode: idle / preview / collection / legacy 四种状态
  - 新增 API: start_preview_stream, switch_preview_to_collection,
    switch_collection_to_preview, stop_collection_stream, stop_any_stream
  - 模拟 STREAM_SWITCH_DELAY_MS 延时（FAST_SIM_MODE=True 时缩短）
  - collection_stream_id 在 switch 时生成，贯穿 response + broadcast event
  - preview bin: PREVIEW_L/R_YYMMDD_HHMMSS
  - collection bin: {session_id}_L/R_YYMMDD_HHMMSS 或 COLLECT_L/R_...
  - 每次新 stream 重置 frame counter

端口 (与 ble_server.py 相同):
  控制端: ws://localhost:8764
  数据端: ws://localhost:8766

快速测试: FAST_SIM_MODE = True   (延时 200ms/20ms 代替 3000ms/200ms)
真实模拟: FAST_SIM_MODE = False  (延时与真实 ble_server.py 一致)

直接运行:  python ble_server_sim_v3.py
"""

import asyncio
import json
import math
import random
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

try:
    import msgpack  # noqa: F401
    HAS_MSGPACK = True
except ImportError:
    HAS_MSGPACK = False

try:
    import numpy as np  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


# ==================== 服务配置 ====================
WEBSOCKET_HOST = "localhost"
CONTROL_PORT = 8764
DATA_PORT = 8766

# ==================== 切流延时配置 ====================
FAST_SIM_MODE = True  # True: 快速测试 (200ms/20ms); False: 真实延时 (3000ms/200ms)
STREAM_SWITCH_DELAY_MS = 200 if FAST_SIM_MODE else 3000
TIMESTAMP_TO_START_DELAY_MS = 20 if FAST_SIM_MODE else 200
PREVIEW_FILENAME_PREFIX = "PREVIEW"
COLLECTION_FILENAME_PREFIX = "COLLECT"

# ==================== 模拟设备列表 ====================
MOCK_DEVICE_PREFIX = "ESP32S3_EMG"
MOCK_DEVICES = [
    {"name": "ESP32S3_EMG_V2_001", "mac": "AA:BB:CC:DD:EE:11", "rssi": -42},
    {"name": "ESP32S3_EMG_V2_002", "mac": "AA:BB:CC:DD:EE:22", "rssi": -48},
    {"name": "ESP32S3_EMG_V2_003", "mac": "AA:BB:CC:DD:EE:33", "rssi": -65},
]

# ==================== V2 常量 ====================
CHANNELS_MAP_V2 = [15, 16, 14, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
SCALE_ACCEL = 32.0 / 32768.0
SCALE_GYRO = 2000.0 / 32768.0
BASE_LSB_24BIT = 0.2861
HARDWARE_FRONTEND_GAIN = 12

FRAMES_PER_PACKET = 9
BLE_SAMPLE_RATE = 250
FRAME_INTERVAL = 1.0 / BLE_SAMPLE_RATE
NUM_IMUS_V2 = 3
SEND_INTERVAL = 0.036

BATCH_MIN = 2
BATCH_MAX = 5
BATCH_PROBABILITY = 0.5
STRESS_BATCH_MODE = False

PRIORITY_CONTROL = 0
PRIORITY_HIGH = 1
PRIORITY_LOW = 2


def log(message: str):
    print(f"[BLE-SimV3] {message}", file=sys.stderr, flush=True)


# ==================== V2 模拟数据生成器 ====================

class MockDataGeneratorV2:
    """V2 模拟数据生成器 — 与 ble_server_sim_v2.py 一致"""

    def __init__(self, device_id: int):
        self.device_id = device_id
        self.frame_index = 0
        self._t = 0.0

        offset = 0.3 if device_id == 2 else 0.0
        self.emg_freqs = [18 + offset * 10 + i * 2.5 for i in range(16)]
        self.emg_amps = [40 + random.uniform(-15, 15) + offset * 30 for _ in range(16)]
        self.emg_phases = [random.uniform(0, 2 * math.pi) for _ in range(16)]
        self.emg_noise = 12 + device_id * 3

        self._activity = [0.5] * 16
        self._activity_target = [0.5] * 16

        self.acc_bias = [[random.uniform(-0.03, 0.03) for _ in range(3)] for _ in range(3)]
        self.gyr_bias = [[random.uniform(-1.5, 1.5) for _ in range(3)] for _ in range(3)]
        self.motion_freq = 0.4 + random.uniform(-0.15, 0.15)
        self.imu_phases = [0.0, 2.0 * math.pi / 3.0, 4.0 * math.pi / 3.0]

    def _generate_emg_channel(self, ch: int) -> float:
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
        frame = [self._generate_emg_channel(ch) for ch in range(16)]
        self._t += FRAME_INTERVAL
        return frame

    def _apply_channel_map(self, frames: List[List[float]]) -> List[List[float]]:
        return [[row[i - 1] for i in CHANNELS_MAP_V2] for row in frames]

    def generate_emg_packet(self) -> Dict:
        raw_frames = []
        uv_frames = []
        start_frame = self.frame_index
        for i in range(FRAMES_PER_PACKET):
            phys_uv = self._generate_emg_frame()
            lsb_uv = BASE_LSB_24BIT / (12 * HARDWARE_FRONTEND_GAIN)
            phys_raw = [int(v / lsb_uv) for v in phys_uv]
            raw_frames.append(phys_raw)
            uv_frames.append(phys_uv)
            self.frame_index += 1
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

    def generate_imu_data(self) -> List[List[List[float]]]:
        t = self._t
        imus = []
        for i in range(NUM_IMUS_V2):
            phi = self.imu_phases[i]
            mf = self.motion_freq
            ax = self.acc_bias[i][0] + 0.12 * math.sin(2 * math.pi * mf * t + phi)
            ay = self.acc_bias[i][1] + 0.12 * math.cos(2 * math.pi * mf * t + phi)
            az = 1.0 + self.acc_bias[i][2] + 0.06 * math.sin(2 * math.pi * mf * 2 * t + phi)
            acc = [round(ax + random.uniform(-0.015, 0.015), 6),
                   round(ay + random.uniform(-0.015, 0.015), 6),
                   round(az + random.uniform(-0.015, 0.015), 6)]
            gx = self.gyr_bias[i][0] + 8 * math.sin(2 * math.pi * mf * t + phi * 1.3)
            gy = self.gyr_bias[i][1] + 8 * math.cos(2 * math.pi * mf * t + phi * 1.1)
            gz = self.gyr_bias[i][2] + 4 * math.sin(2 * math.pi * mf * 0.7 * t + phi)
            gyr = [round(gx + random.uniform(-1.5, 1.5), 6),
                   round(gy + random.uniform(-1.5, 1.5), 6),
                   round(gz + random.uniform(-1.5, 1.5), 6)]
            imus.append([acc, gyr])
        return imus

    def generate_packet(self, ts: float) -> Dict:
        emg = self.generate_emg_packet()
        emg_timestamps = [
            ts - (FRAMES_PER_PACKET - 1 - i) * FRAME_INTERVAL
            for i in range(FRAMES_PER_PACKET)
        ]
        imu = self.generate_imu_data()
        imu_timestamps = [ts] * len(imu)
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


# ==================== DeviceState (支持 stream_mode) ====================

@dataclass
class DeviceState:
    """单个模拟 BLE 设备状态 (V2 + stream_mode)"""
    device_id: int

    _connected: bool = False
    mac: Optional[str] = None
    name: Optional[str] = None
    rssi: Optional[int] = None

    is_streaming: bool = False
    stream_mode: str = "idle"  # idle | preview | collection | legacy
    total_frames: int = 0
    lost_frames: int = 0
    last_frame_index: int = -1
    last_data_time: float = 0.0
    connect_task: Any = None

    hw_version: str = "V2"
    firmware_version: str = "v2.1.0-sim"
    hardware_version: str = "ESP32S3-EMG-V2"
    num_imus: int = NUM_IMUS_V2

    sd_filename: Optional[str] = None
    data_generator: Optional[MockDataGeneratorV2] = None

    def __post_init__(self):
        self.data_generator = MockDataGeneratorV2(self.device_id)

    def reset_stats(self):
        self.total_frames = 0
        self.lost_frames = 0
        self.last_frame_index = -1
        self.sd_filename = None
        self.stream_mode = "idle"
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
            'stream_mode': self.stream_mode,
            'total': self.total_frames,
            'lost': self.lost_frames,
            'hw_version': self.hw_version,
            'num_imus': self.num_imus,
            'firmware_version': self.firmware_version,
            'hardware_version': self.hardware_version,
        }


# ==================== 文件名构建 ====================

def _build_stream_filename(dev: DeviceState, prefix_hint: str = "PREVIEW") -> str:
    """构建 stream 的模拟 SD 卡 bin 文件名前缀"""
    now_str = datetime.now().strftime("%y%m%d_%H%M%S")
    hand_label = "L" if dev.device_id == 1 else "R"
    if prefix_hint == "PREVIEW":
        return f"{PREVIEW_FILENAME_PREFIX}_{hand_label}_{now_str}"
    elif state.session_id:
        return f"{state.session_id}_{hand_label}_{now_str}"
    else:
        return f"{COLLECTION_FILENAME_PREFIX}_{hand_label}_{now_str}"


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
    packet = dev.data_generator.generate_packet(ts)
    dev.total_frames += packet['n']
    packet['s'] = [dev.total_frames, dev.lost_frames]
    dev.last_data_time = ts
    return packet


def data_sender_thread():
    """数据发送线程 — preview 和 collection 都发送数据"""
    log("V3 数据发送线程启动 (preview + collection 均发送)")
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

                use_batch = STRESS_BATCH_MODE and random.random() < BATCH_PROBABILITY
                batch_size = random.randint(BATCH_MIN, BATCH_MAX) if use_batch else 1

                if state.dev1.is_streaming and state.dev1.data_generator:
                    if use_batch:
                        packets = [_build_dev_packet(state.dev1, base_ts + k * SEND_INTERVAL) for k in range(batch_size)]
                        dev1_data = packets if len(packets) > 1 else packets[0]
                    else:
                        dev1_data = _build_dev_packet(state.dev1, base_ts)

                if state.dev2.is_streaming and state.dev2.data_generator:
                    if use_batch:
                        packets = [_build_dev_packet(state.dev2, base_ts + k * SEND_INTERVAL) for k in range(batch_size)]
                        dev2_data = packets if len(packets) > 1 else packets[0]
                    else:
                        dev2_data = _build_dev_packet(state.dev2, base_ts)

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

            now = time.time()
            if now - last_log_time >= 5.0:
                if send_count > 0:
                    modes = f"dev1={state.dev1.stream_mode}, dev2={state.dev2.stream_mode}"
                    log(f"[数据发送] 已发送 {send_count} 批, 活跃: {active}, "
                        f"Dev1帧={state.dev1.total_frames}, Dev2帧={state.dev2.total_frames}, modes={modes}")
                last_log_time = now
                send_count = 0

            sleep_duration = max(SEND_INTERVAL, batch_size * SEND_INTERVAL) if use_batch else SEND_INTERVAL
            time.sleep(sleep_duration)

        except Exception as e:
            if not state.stop_thread:
                log(f"发送线程错误: {e}")
                traceback.print_exc()

    log("V3 数据发送线程结束")


# ==================== 底层 Stream 操作 ====================

async def _do_start_stream_for_device(dev: DeviceState, filename_str: str) -> bool:
    """模拟 TIMESTAMP → 短延时 → START"""
    try:
        # 模拟 TIMESTAMP 发送
        log(f"[Dev{dev.device_id}] TIMESTAMP: {filename_str}")
        await asyncio.sleep(TIMESTAMP_TO_START_DELAY_MS / 1000.0)

        # 模拟 START
        dev.sd_filename = filename_str
        dev.is_streaming = True
        log(f"[Dev{dev.device_id}] START (stream_mode={dev.stream_mode}, file={filename_str})")
        return True
    except Exception as e:
        log(f"[Dev{dev.device_id}] 启动失败: {e}")
        return False


async def _do_stop_stream_for_device(dev: DeviceState):
    """模拟 STOP"""
    if dev.is_streaming:
        old_mode = dev.stream_mode
        dev.is_streaming = False
        log(f"[Dev{dev.device_id}] STOP (was: {old_mode}, frames={dev.total_frames})")


# ==================== Stream 管理 API ====================

async def start_preview_stream(ws, device_id=None):
    """启动 preview stream（对已连接的设备）"""
    devices_to_start = []
    if device_id is not None:
        dev = state.get_device(device_id)
        if dev.is_connected() and not dev.is_streaming:
            devices_to_start.append(dev)
    else:
        for did in [1, 2]:
            dev = state.get_device(did)
            if dev.is_connected() and not dev.is_streaming:
                devices_to_start.append(dev)

    if not devices_to_start:
        log("[preview] 没有需要启动 preview 的设备")
        return

    started_ids = []
    for dev in devices_to_start:
        dev.reset_stats()
        dev.stream_mode = "preview"
        fn = _build_stream_filename(dev, "PREVIEW")
        ok = await _do_start_stream_for_device(dev, fn)
        if ok:
            started_ids.append(dev.device_id)
            log(f"[preview] Dev{dev.device_id} preview: {fn}")

    if ws:
        await send_to_control(ws, 'start_preview_stream', {
            'success': True, 'started': started_ids, 'stream_mode': 'preview',
        })


async def stop_preview_stream(ws, device_id=None, silent=False):
    """停止 preview stream"""
    devices_to_stop = []
    if device_id is not None:
        dev = state.get_device(device_id)
        if dev.stream_mode == "preview" and dev.is_streaming:
            devices_to_stop.append(dev)
    else:
        for did in [1, 2]:
            dev = state.get_device(did)
            if dev.stream_mode == "preview" and dev.is_streaming:
                devices_to_stop.append(dev)

    stopped_ids = []
    for dev in devices_to_stop:
        await _do_stop_stream_for_device(dev)
        dev.stream_mode = "idle"
        stopped_ids.append(dev.device_id)
        log(f"[preview] Dev{dev.device_id} 已停止")

    if ws and not silent:
        await send_to_control(ws, 'stop_preview_stream', {
            'success': True, 'stopped': stopped_ids, 'stream_mode': 'preview',
        })


async def stop_collection_stream(ws, device_id=None, silent=False):
    """停止 collection stream"""
    devices_to_stop = []
    if device_id is not None:
        dev = state.get_device(device_id)
        if dev.stream_mode == "collection" and dev.is_streaming:
            devices_to_stop.append(dev)
    else:
        for did in [1, 2]:
            dev = state.get_device(did)
            if dev.stream_mode == "collection" and dev.is_streaming:
                devices_to_stop.append(dev)

    stopped_ids = []
    sd_filenames = {}
    for dev in devices_to_stop:
        sd_filenames[f'dev{dev.device_id}'] = dev.sd_filename
        await _do_stop_stream_for_device(dev)
        dev.stream_mode = "idle"
        stopped_ids.append(dev.device_id)
        log(f"[collection] Dev{dev.device_id} 已停止: {dev.sd_filename}")

    # 等待 ESP32 关闭 bin
    log(f"[collection] 等待 {STREAM_SWITCH_DELAY_MS}ms (ESP32 关闭 bin)...")
    await asyncio.sleep(STREAM_SWITCH_DELAY_MS / 1000.0)

    if ws and not silent:
        await send_to_control(ws, 'stop_collection_stream', {
            'success': True, 'stopped': stopped_ids,
            'sd_filenames': sd_filenames, 'stream_mode': 'collection',
        })

    await broadcast_event('collection_stopped', {
        'stopped': stopped_ids, 'sd_filenames': sd_filenames,
    })


async def switch_preview_to_collection(ws):
    """preview → collection 切流"""
    action = 'switch_preview_to_collection'
    log("=" * 50)
    log("[switch] === preview → collection 切换开始 ===")
    log("=" * 50)

    connected_ids = state.get_connected_devices()
    if not connected_ids:
        await send_to_control(ws, action, {'success': False, 'error': '没有已连接的设备'})
        return

    # Phase 1: 停止活跃 stream
    log("[switch] Phase 1: 停止活跃 stream...")
    any_was_streaming = False
    for did in connected_ids:
        dev = state.get_device(did)
        old_mode = dev.stream_mode
        if dev.is_streaming:
            await _do_stop_stream_for_device(dev)
            any_was_streaming = True
        dev.stream_mode = "idle"
        log(f"[switch] Dev{did} STOP (was: {old_mode})")

    # Phase 2: 等待 ESP32 关闭 bin（仅当确实有活跃 stream 时）
    skipped_delay_when_idle = not any_was_streaming
    if any_was_streaming:
        log(f"[switch] Phase 2: 等待 {STREAM_SWITCH_DELAY_MS}ms...")
        await asyncio.sleep(STREAM_SWITCH_DELAY_MS / 1000.0)
    else:
        log(f"[switch] Phase 2: SKIP — 所有设备已 idle，无需等待 {STREAM_SWITCH_DELAY_MS}ms")

    # Phase 3: 启动 collection
    log("[switch] Phase 3: 启动 collection...")
    collection_stream_id = datetime.now().isoformat()
    collection_bins = {}
    device_names = {}
    started_ids = []

    for did in connected_ids:
        dev = state.get_device(did)
        if not dev.is_connected():
            log(f"[switch] Dev{did} 已断开，跳过")
            continue

        dev.reset_stats()
        dev.stream_mode = "collection"
        fn = _build_stream_filename(dev, "COLLECT")
        ok = await _do_start_stream_for_device(dev, fn)
        if ok:
            started_ids.append(did)
            collection_bins[f'dev{did}'] = fn
            if dev.name:
                device_names[f'dev{did}'] = dev.name
            log(f"[switch] Dev{did} collection: {fn}")

    if not started_ids:
        await send_to_control(ws, action, {'success': False, 'error': '所有设备启动 collection 失败'})
        return

    log(f"[switch] === 切换完成, collection_stream_id={collection_stream_id} ===")
    log(f"[switch] bins: {collection_bins}")

    # Phase 4: 响应
    await send_to_control(ws, action, {
        'success': True,
        'started': started_ids,
        'collection_bins': collection_bins,
        'device_names': device_names,
        'stream_mode': 'collection',
        'collection_stream_id': collection_stream_id,
        'switch_delay_ms': STREAM_SWITCH_DELAY_MS,
        'timestamp_to_start_delay_ms': TIMESTAMP_TO_START_DELAY_MS,
        'skipped_delay_when_idle': skipped_delay_when_idle,
    })

    # Phase 5: broadcast（兜底）
    await broadcast_event('sd_filenames_updated', {
        'sd_filenames': collection_bins,
        'device_names': device_names,
        'stream_mode': 'collection',
        'collection_stream_id': collection_stream_id,
        'switch_delay_ms': STREAM_SWITCH_DELAY_MS,
        'skipped_delay_when_idle': skipped_delay_when_idle,
    })


async def switch_collection_to_preview(ws):
    """collection → preview 切流"""
    action = 'switch_collection_to_preview'
    log("=" * 50)
    log("[switch] === collection → preview 切换开始 ===")
    log("=" * 50)

    connected_ids = state.get_connected_devices()
    if not connected_ids:
        await send_to_control(ws, action, {'success': False, 'error': '没有已连接的设备'})
        return

    # Phase 1: 停止 collection
    log("[switch] Phase 1: 停止 collection...")
    for did in connected_ids:
        dev = state.get_device(did)
        old_mode = dev.stream_mode
        if dev.is_streaming:
            await _do_stop_stream_for_device(dev)
        dev.stream_mode = "idle"
        log(f"[switch] Dev{did} STOP (was: {old_mode})")

    # Phase 2: 等待 ESP32 关闭 collection bin
    log(f"[switch] Phase 2: 等待 {STREAM_SWITCH_DELAY_MS}ms...")
    await asyncio.sleep(STREAM_SWITCH_DELAY_MS / 1000.0)

    # Phase 3: 启动 preview
    log("[switch] Phase 3: 启动 preview...")
    started_ids = []
    for did in connected_ids:
        dev = state.get_device(did)
        if not dev.is_connected():
            continue
        dev.reset_stats()
        dev.stream_mode = "preview"
        fn = _build_stream_filename(dev, "PREVIEW")
        ok = await _do_start_stream_for_device(dev, fn)
        if ok:
            started_ids.append(did)
            log(f"[switch] Dev{did} preview: {fn}")

    log("[switch] === 切换完成 ===")
    await send_to_control(ws, action, {
        'success': True, 'started': started_ids, 'stream_mode': 'preview',
    })


async def stop_any_stream(ws, device_id=None, silent=False):
    """停止任意活跃流"""
    devices_to_stop = []
    if device_id is not None:
        dev = state.get_device(device_id)
        if dev.is_streaming:
            devices_to_stop.append(dev)
    else:
        for did in [1, 2]:
            dev = state.get_device(did)
            if dev.is_streaming:
                devices_to_stop.append(dev)

    stopped_ids = []
    for dev in devices_to_stop:
        old_mode = dev.stream_mode
        await _do_stop_stream_for_device(dev)
        dev.stream_mode = "idle"
        stopped_ids.append(dev.device_id)
        log(f"[stop_any] Dev{dev.device_id} 停止 (was: {old_mode})")

    if ws and not silent:
        await send_to_control(ws, 'stop_any_stream', {
            'success': True, 'stopped': stopped_ids,
        })


# ==================== 旧 BLE 操作 API ====================

async def scan_devices(ws):
    log("模拟扫描设备...")
    await asyncio.sleep(0.5)
    state.devices_found.clear()
    state.scan_results.clear()
    for dev in MOCK_DEVICES:
        display = f"{dev['name']} ({dev['mac']})"
        state.devices_found[display] = dev
        state.scan_results.append({
            'name': dev['name'], 'mac': dev['mac'],
            'display': display, 'rssi': dev['rssi'],
            'manufacturer': 'Mock V2 Device',
        })
    state.scan_results.sort(key=lambda x: x['rssi'], reverse=True)
    log(f"扫描完成: {len(state.scan_results)} 个设备")
    await send_to_control(ws, 'scan', {
        'success': True, 'devices': state.scan_results,
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
    await asyncio.sleep(0.3)

    dev.set_connected(True)
    dev.mac = mac
    dev.name = name
    dev.rssi = rssi
    dev.reset_stats()
    dev.stream_mode = "idle"
    log(f"[Dev{device_id}] 连接成功 (V2, stream_mode=idle)")

    await send_to_control(ws, action, {
        'success': True, 'device_id': device_id, 'mac': mac,
        'name': name, 'rssi': rssi,
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
            await stop_any_stream(ws, device_id=device_id, silent=True)
            dev.stream_mode = "idle"

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
    """旧 API: 启动 stream（无 stream_mode 区分，标记为 legacy）"""
    dev = state.get_device(device_id)
    action = f'start{device_id}'
    if not dev.is_connected():
        await send_to_control(ws, action, {'success': False, 'device_id': device_id, 'error': "设备未连接"})
        return
    if dev.is_streaming:
        await send_to_control(ws, action, {'success': False, 'device_id': device_id, 'error': "已在采集中"})
        return

    dev.reset_stats()
    dev.stream_mode = "legacy"
    now_str = datetime.now().strftime("%y%m%d_%H%M%S")
    hand_label = "L" if device_id == 1 else "R"
    dev.sd_filename = f"{state.session_id}_{hand_label}_{now_str}" if state.session_id else f"LEGACY_{hand_label}_{now_str}"
    dev.is_streaming = True
    log(f"[Dev{device_id}] 旧 API start (legacy, file={dev.sd_filename})")

    await send_to_control(ws, action, {
        'success': True, 'device_id': device_id, 'active': state.get_active_devices(),
    })
    await broadcast_event('stream_started', {
        'device_id': device_id, 'active': state.get_active_devices(),
    })


async def stop_stream(ws, device_id: int, silent=False):
    """旧 API: 停止 stream"""
    dev = state.get_device(device_id)
    action = f'stop{device_id}'
    if not dev.is_streaming:
        if not silent:
            await send_to_control(ws, action, {'success': False, 'device_id': device_id, 'error': "未在采集"})
        return
    dev.is_streaming = False
    dev.stream_mode = "idle"
    log(f"[Dev{device_id}] 旧 API stop: total={dev.total_frames}")
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
    """旧 API: start_all（标记为 stream_mode='legacy'）"""
    started = []
    for did in [1, 2]:
        dev = state.get_device(did)
        if dev.is_connected() and not dev.is_streaming:
            await start_stream(ws, did)
            if dev.is_streaming:
                started.append(did)

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
        'sd_filenames': sd_filenames, 'device_names': device_names,
    })
    if sd_filenames or device_names:
        await broadcast_event('sd_filenames_updated', {
            'sd_filenames': sd_filenames, 'device_names': device_names,
            'stream_mode': 'legacy',
        })


async def stop_all(ws):
    stopped = []
    for did in [1, 2]:
        dev = state.get_device(did)
        if dev.is_streaming:
            await stop_stream(ws, did, silent=True)
            stopped.append(did)
    await send_to_control(ws, 'stop_all', {
        'success': True, 'stopped': stopped, 'active': state.get_active_devices(),
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
        'message': '控制端已连接 (V3 模拟模式 - preview/collection 切流)',
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

                # ---- 扫描/连接/断开 ----
                if action == 'scan':
                    await scan_devices(websocket)

                elif action == 'connect1':
                    target = data.get('mac') or data.get('display')
                    if target:
                        state.dev1.connect_task = asyncio.create_task(connect_device(websocket, 1, target))
                    else:
                        await send_to_control(websocket, 'connect1', {'success': False, 'device_id': 1, 'error': "请提供 mac"})

                elif action == 'disconnect1':
                    await disconnect_device(websocket, 1)
                elif action == 'connect2':
                    target = data.get('mac') or data.get('display')
                    if target:
                        state.dev2.connect_task = asyncio.create_task(connect_device(websocket, 2, target))
                    else:
                        await send_to_control(websocket, 'connect2', {'success': False, 'device_id': 2, 'error': "请提供 mac"})
                elif action == 'disconnect2':
                    await disconnect_device(websocket, 2)

                # ---- 旧 API（兼容）----
                elif action == 'start1':
                    await start_stream(websocket, 1)
                elif action == 'stop1':
                    await stop_stream(websocket, 1)
                elif action == 'start2':
                    await start_stream(websocket, 2)
                elif action == 'stop2':
                    await stop_stream(websocket, 2)
                elif action == 'start_all':
                    await start_all(websocket)
                elif action == 'stop_all':
                    await stop_all(websocket)

                # ---- 新 API：Preview / Collection 流管理 ----
                elif action == 'start_preview_stream':
                    await start_preview_stream(websocket)
                elif action == 'stop_preview_stream':
                    await stop_preview_stream(websocket)
                elif action == 'start_collection_stream':
                    # 直接启动 collection
                    connected_ids = state.get_connected_devices()
                    if not connected_ids:
                        await send_to_control(websocket, 'start_collection_stream', {'success': False, 'error': '没有已连接的设备'})
                    else:
                        collection_bins = {}
                        started = []
                        for did in connected_ids:
                            dev = state.get_device(did)
                            if dev.is_connected() and not dev.is_streaming:
                                dev.reset_stats()
                                dev.stream_mode = "collection"
                                fn = _build_stream_filename(dev, "COLLECT")
                                if await _do_start_stream_for_device(dev, fn):
                                    started.append(did)
                                    collection_bins[f'dev{did}'] = fn
                        await send_to_control(websocket, 'start_collection_stream', {
                            'success': len(started) > 0, 'started': started,
                            'collection_bins': collection_bins, 'stream_mode': 'collection',
                        })
                        if collection_bins:
                            await broadcast_event('sd_filenames_updated', {
                                'sd_filenames': collection_bins, 'stream_mode': 'collection',
                            })

                elif action == 'stop_collection_stream':
                    await stop_collection_stream(websocket)
                elif action == 'switch_preview_to_collection':
                    await switch_preview_to_collection(websocket)
                elif action == 'switch_collection_to_preview':
                    await switch_collection_to_preview(websocket)
                elif action == 'stop_any_stream':
                    await stop_any_stream(websocket)
                elif action == 'stop_any_stream_single':
                    device_id = data.get('device_id', 1)
                    await stop_any_stream(websocket, device_id=device_id)

                elif action == 'status':
                    await get_status(websocket)

                elif action == 'set_session_id':
                    session_id = data.get('session_id', '')
                    if session_id:
                        clean_id = ''.join(c for c in session_id if c.isalnum() or c == '_')
                        clean_id = clean_id[:10]
                        state.session_id = clean_id
                        log(f"[控制端] session_id 已设置: {state.session_id}")
                        await send_to_control(websocket, 'set_session_id', {
                            'success': True, 'session_id': state.session_id,
                        })
                    else:
                        state.session_id = ""
                        log(f"[控制端] session_id 已清空")
                        await send_to_control(websocket, 'set_session_id', {
                            'success': True, 'session_id': "",
                        })

                else:
                    await send_to_control(websocket, 'error', {'error': f"未知命令: {action}"})

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
        'message': '数据端已连接 (V3 模拟模式 - preview/collection 切流)',
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
    log("BLE Server Sim V3 — preview/collection 切流模拟")
    log("=" * 60)
    log(f"控制端口: ws://{WEBSOCKET_HOST}:{CONTROL_PORT}  (index.html)")
    log(f"数据端口: ws://{WEBSOCKET_HOST}:{DATA_PORT}    (realtimeEngine.js)")
    log("=" * 60)
    log(f"FAST_SIM_MODE: {FAST_SIM_MODE}")
    log(f"  STREAM_SWITCH_DELAY_MS: {STREAM_SWITCH_DELAY_MS}ms")
    log(f"  TIMESTAMP_TO_START_DELAY_MS: {TIMESTAMP_TO_START_DELAY_MS}ms")
    log(f"  SEND_INTERVAL: {SEND_INTERVAL*1000:.0f}ms (9帧/包, 250Hz)")
    log("=" * 60)
    log("Stream 模式: idle → preview → (switch) → collection → (switch) → preview → idle")
    log("preview bin:  PREVIEW_L/R_YYMMDD_HHMMSS")
    log("collection bin: {session_id}_L/R_YYMMDD_HHMMSS (或 COLLECT_L/R_...)")
    log("=" * 60)
    log("模拟设备 (V2):")
    for dev in MOCK_DEVICES:
        log(f"  - {dev['name']} ({dev['mac']}) RSSI: {dev['rssi']}")
    log("=" * 60)
    log("新 API (V3):")
    log("  start_preview_stream, stop_preview_stream")
    log("  switch_preview_to_collection, switch_collection_to_preview")
    log("  stop_collection_stream, stop_any_stream")
    log("旧 API (兼容):")
    log("  start1/2, stop1/2, start_all, stop_all (→ stream_mode='legacy')")
    log("=" * 60)
    log("⚠️  使用前请确保已停止真实 ble_server.py (端口冲突)")
    log("=" * 60)

    try:
        control_server = await websockets.serve(
            handle_control_client, WEBSOCKET_HOST, CONTROL_PORT,
            max_size=2 * 1024 * 1024,
        )
        data_server = await websockets.serve(
            handle_data_client, WEBSOCKET_HOST, DATA_PORT,
            max_size=10 * 1024 * 1024,
        )
        log("WebSocket 服务器启动完成，等待连接...")
        await asyncio.Future()
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
