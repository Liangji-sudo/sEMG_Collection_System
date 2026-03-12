"""
BLE Server for ESP32S3_EMG Device (Dual Device + Dual WebSocket)
=================================================================
支持：
  - 两个蓝牙设备（独立控制）
  - 两个 WebSocket 客户端：
    * 控制端（index.html）: 端口 8764，处理控制命令
    * 数据端（realtimeEngine.js）: 端口 8766，接收数据流

依赖安装：
  pip install websockets bleak msgpack

架构：
  ┌─────────────────┐     ┌─────────────────┐
  │  index.html     │     │ realtimeEngine  │
  │  (控制端)       │     │   (数据端)      │
  └────────┬────────┘     └────────┬────────┘
           │ :8764                 │ :8766
           │ 控制命令              │ 数据流
           ▼                       ▼
  ┌─────────────────────────────────────────┐
  │            ble_server.py                │
  │  ┌─────────────┐   ┌─────────────┐     │
  │  │   Device 1  │   │   Device 2  │     │
  │  └─────────────┘   └─────────────┘     │
  └─────────────────────────────────────────┘
"""

import asyncio
import struct
import time
import traceback
import sys
import io
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Set
from queue import PriorityQueue
from datetime import datetime
import threading
import itertools

import msgpack
import json
import websockets
from bleak import BleakScanner, BleakClient, BleakError

# 尝试导入scipy用于滤波
try:
    from scipy import signal as scipy_signal
    import numpy as np
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    print("[警告] scipy未安装，滤波功能不可用。请运行: pip install scipy numpy", file=sys.stderr)

# ================= 编码配置 =================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# ================= 服务配置 =================
WEBSOCKET_HOST = "localhost"
CONTROL_PORT = 8764   # 控制端口（index.html）
DATA_PORT = 8766      # 数据端口（realtimeEngine.js）

# ================= ESP32 设备配置 =================
TARGET_DEVICE_NAME = "ESP32S3_EMG"

CONTROL_CHAR_UUID = "9e5c100d-afc2-4e4b-b132-f2c0032f7a0b"
EMG_DATA_CHAR_UUID = "9e5c100d-afc2-4e4b-b132-f2c0032f7a0c"

CMD_MAP = {
    '500Hz': 0x10,
    '1kHz': 0x11,
    '2kHz': 0x12,
    'START': 0xA0,
    'STOP': 0xA1,
    'CONFIG': 0xC0,  # 【新增】复合配置命令
    'SET_FILENAME': 0xD0,  # 【新增】设置SD卡文件名命令
}

# ================= 默认配置 =================
# 【修改】默认使用2kHz采样率，与SD卡存储一致
DEFAULT_CONFIG = {
    'sample_rate': 2000,      # 【修改】2kHz采样率
    'gain': 12,
    'gain_index': 6,          # 增益索引：6 对应增益12
    'is_16bit': False,        # 24-bit模式
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

# ================= 滤波器配置 =================
# 【重要】参考供应商滤波参数，优化实时显示效果
FILTER_ENABLED = True          # 总开关：是否启用滤波
FILTER_LOWCUT = 20             # 带通下限 (Hz) - 与供应商一致，去除运动伪迹
FILTER_HIGHCUT = 100           # 带通上限 (Hz) - 与供应商一致，EMG主要能量在20-100Hz
FILTER_NOTCH_FREQ = 50         # 工频频率 (Hz)
FILTER_NOTCH_Q = 15            # 陷波器Q值 - 与供应商一致，较低Q值更稳定
FILTER_BANDPASS_ENABLED = True # 启用带通滤波
FILTER_NOTCH_ENABLED = True    # 启用工频陷波

# 注意：如果前端显示信号幅度太小，可以在前端调整 Offset 值
# 典型EMG信号幅度：静息时 10-50μV，收缩时 100-500μV
# 建议前端 Offset 设置：100-200μV (默认300可能太大)


# ================= EMG实时滤波器类 =================
class EMGRealtimeFilter:
    """
    EMG实时滤波器（参考供应商实现优化）

    功能：
    - 带通滤波 (默认20-100Hz)：去除直流分量和高频噪声，聚焦EMG主要频率
    - 工频陷波 (50Hz及谐波)：去除电源干扰

    使用单一带通滤波器（而非分离的高通+低通），保持相位响应一致性
    使用IIR滤波器，保持状态实现流式处理
    """

    def __init__(self, fs=1000, num_channels=16,
                 lowcut=FILTER_LOWCUT, highcut=FILTER_HIGHCUT,
                 notch_freq=FILTER_NOTCH_FREQ, notch_q=FILTER_NOTCH_Q,
                 enable_bandpass=FILTER_BANDPASS_ENABLED,
                 enable_notch=FILTER_NOTCH_ENABLED):
        """
        初始化滤波器

        参数:
            fs: 采样率 (Hz)
            num_channels: 通道数
            lowcut: 带通下限 (Hz)
            highcut: 带通上限 (Hz)
            notch_freq: 工频频率 (Hz)
            notch_q: 陷波器Q值（较低值更稳定，推荐15）
            enable_bandpass: 是否启用带通滤波
            enable_notch: 是否启用工频陷波
        """
        if not HAS_SCIPY:
            log("[滤波器] scipy未安装，滤波功能不可用")
            self.enabled = False
            return

        self.enabled = True
        self.fs = fs
        self.num_channels = num_channels
        self.lowcut = lowcut
        self.highcut = min(highcut, fs / 2 * 0.9)  # 不能超过奈奎斯特频率的90%
        self.notch_freq = notch_freq
        self.notch_q = notch_q
        self.enable_bandpass = enable_bandpass
        self.enable_notch = enable_notch

        # 带通滤波器系数（单一带通，而非分离的高通+低通）
        self.b_bandpass = None
        self.a_bandpass = None
        self.zi_bandpass = None

        # 陷波滤波器列表
        self.notch_filters = []  # [(b, a), ...]
        self.zi_notch = None

        # 初始化滤波器
        self._init_filters()

        log(f"[滤波器] 初始化完成: fs={fs}Hz, 带通={self.lowcut}-{self.highcut}Hz, 陷波Q={notch_q}")

    def _init_filters(self):
        """初始化滤波器系数和状态（参考供应商实现）"""
        if not self.enabled:
            return

        # 1. 带通滤波器 - 使用单一4阶Butterworth带通（与供应商一致）
        if self.enable_bandpass:
            # 计算归一化频率
            wn = [self.lowcut * 2 / self.fs, self.highcut * 2 / self.fs]
            # 4阶带通滤波器
            self.b_bandpass, self.a_bandpass = scipy_signal.butter(4, wn, btype='bandpass')
            # 初始化状态
            zi = scipy_signal.lfilter_zi(self.b_bandpass, self.a_bandpass)
            self.zi_bandpass = np.tile(zi[:, np.newaxis], (1, self.num_channels))

        # 2. 工频陷波滤波器 (50Hz及谐波)
        if self.enable_notch:
            self.notch_filters = []
            self.zi_notch = []

            # 陷波50Hz及其谐波 (100Hz, 150Hz, ...)，直到接近奈奎斯特频率
            for freq in range(self.notch_freq, int(self.fs / 2), self.notch_freq):
                w0 = freq / (self.fs / 2)  # 归一化频率
                b, a = scipy_signal.iirnotch(w0, self.notch_q)
                self.notch_filters.append((b, a))

                zi = scipy_signal.lfilter_zi(b, a)
                self.zi_notch.append(np.tile(zi[:, np.newaxis], (1, self.num_channels)))

    def reset(self):
        """重置滤波器状态"""
        if not self.enabled:
            return
        self._init_filters()
        log("[滤波器] 状态已重置")

    def filter_frame(self, uv_data: list) -> list:
        """
        滤波单帧数据 (16通道)

        参数:
            uv_data: 16通道的μV数据列表 [ch0, ch1, ..., ch15]

        返回:
            滤波后的数据列表
        """
        if not self.enabled or not HAS_SCIPY:
            return uv_data

        # 转换为numpy数组 (1, num_channels)
        data = np.array(uv_data, dtype=np.float64).reshape(1, -1)

        # 应用带通滤波（单一带通滤波器）
        if self.enable_bandpass and self.b_bandpass is not None:
            data, self.zi_bandpass = scipy_signal.lfilter(
                self.b_bandpass, self.a_bandpass, data, axis=0, zi=self.zi_bandpass
            )

        # 应用工频陷波
        if self.enable_notch and self.notch_filters:
            for i, (b, a) in enumerate(self.notch_filters):
                data, self.zi_notch[i] = scipy_signal.lfilter(
                    b, a, data, axis=0, zi=self.zi_notch[i]
                )

        return data.flatten().tolist()

    def filter_batch(self, uv_data_batch: list) -> list:
        """
        滤波批量数据 (多帧)

        参数:
            uv_data_batch: 多帧数据 [[ch0, ch1, ...], [ch0, ch1, ...], ...]

        返回:
            滤波后的数据列表
        """
        if not self.enabled or not HAS_SCIPY:
            return uv_data_batch

        # 转换为numpy数组 (num_frames, num_channels)
        data = np.array(uv_data_batch, dtype=np.float64)

        # 应用带通滤波（单一带通滤波器）
        if self.enable_bandpass and self.b_bandpass is not None:
            data, self.zi_bandpass = scipy_signal.lfilter(
                self.b_bandpass, self.a_bandpass, data, axis=0, zi=self.zi_bandpass
            )

        # 应用工频陷波
        if self.enable_notch and self.notch_filters:
            for i, (b, a) in enumerate(self.notch_filters):
                data, self.zi_notch[i] = scipy_signal.lfilter(
                    b, a, data, axis=0, zi=self.zi_notch[i]
                )

        return data.tolist()


# ================= 全局滤波器实例 =================
# 设备1和设备2各自独立的滤波器
emg_filter_dev1 = None
emg_filter_dev2 = None

def init_filters():
    """初始化全局滤波器"""
    global emg_filter_dev1, emg_filter_dev2
    
    if not FILTER_ENABLED:
        log("[滤波器] 滤波功能已禁用")
        return
    
    if not HAS_SCIPY:
        log("[滤波器] scipy未安装，无法初始化滤波器")
        return
    
    emg_filter_dev1 = EMGRealtimeFilter(
        fs=DEFAULT_CONFIG['sample_rate'],
        num_channels=16,
        enable_bandpass=FILTER_BANDPASS_ENABLED,
        enable_notch=FILTER_NOTCH_ENABLED
    )
    
    emg_filter_dev2 = EMGRealtimeFilter(
        fs=DEFAULT_CONFIG['sample_rate'],
        num_channels=16,
        enable_bandpass=FILTER_BANDPASS_ENABLED,
        enable_notch=FILTER_NOTCH_ENABLED
    )
    
    log("[滤波器] 双设备滤波器初始化完成")

# ================= 连接配置 =================
CONNECT_TIMEOUT = 30.0
SCAN_TIMEOUT = 5.0
MAX_RETRIES = 3
RETRY_DELAY = 2.0

# ================= 批量发送配置 =================
BATCH_INTERVAL = 0.005  # 5ms

# ================= 优先级 =================
PRIORITY_CONTROL = 0   # 控制命令（最高优先级）
PRIORITY_HIGH = 1      # 控制响应
PRIORITY_LOW = 2       # 传感器数据


# ================= 单设备状态类 =================
@dataclass
class DeviceState:
    """单个 BLE 设备的状态"""
    device_id: int
    
    client: Optional[BleakClient] = None
    device: Any = None
    mac: Optional[str] = None
    name: Optional[str] = None
    rssi: Optional[int] = None
    
    is_streaming: bool = False
    total_frames: int = 0
    lost_frames: int = 0
    last_frame_index: int = -1
    sd_filename: Optional[str] = None  # 【新增】当前采集的SD卡bin文件名前缀
    
    config: Dict = field(default_factory=lambda: DEFAULT_CONFIG.copy())
    data_buffer: deque = field(default_factory=lambda: deque(maxlen=500))
    connect_task: Any = None
    
    def reset_stats(self):
        self.total_frames = 0
        self.lost_frames = 0
        self.last_frame_index = -1
        self.data_buffer.clear()
        self.sd_filename = None  # 【新增】重置SD卡文件名
        
        # 重置对应设备的滤波器状态
        if FILTER_ENABLED and HAS_SCIPY:
            emg_filter = emg_filter_dev1 if self.device_id == 1 else emg_filter_dev2
            if emg_filter:
                emg_filter.reset()
    
    def is_connected(self) -> bool:
        if self.client is None:
            return False
        try:
            # 显式转换为 bool，避免 _DeprecatedIsConnectedReturn 类型无法 JSON 序列化
            return bool(self.client.is_connected)
        except:
            return False
    
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

        # 【新增】会话ID，用于SD卡文件命名
        self.session_id: str = ""  # 例如 "S001"
    
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
    print(f"[BLE-Server] {message}", file=sys.stderr)


def calculate_lsb_uv(config: dict) -> float:
    lsb_uv = BASE_LSB_24BIT / (config['gain'] * HARDWARE_FRONTEND_GAIN)
    if config['is_16bit']:
        lsb_uv = lsb_uv * (2 ** config['shift'])
    return lsb_uv


def get_packet_params(config: dict) -> dict:
    bps = 2 if config['is_16bit'] else 3
    emg_len = config['frames_per_packet'] * 16 * bps
    imu_len = 36 if config['imu_enabled'] else 0
    return {
        'bps': bps,
        'emg_len': emg_len,
        'imu_len': imu_len,
        'total_len': 4 + emg_len + imu_len,
        'fpkt': config['frames_per_packet'],
    }


# ================= 数据解析 =================

def parse_packet(data: bytearray, dev: DeviceState) -> Optional[dict]:
    params = get_packet_params(dev.config)
    
    if len(data) != params['total_len']:
        log(f"[Dev{dev.device_id}] 包长错误: {len(data)} != {params['total_len']}")
        return None
    
    try:
        config = dev.config
        lsb_uv = calculate_lsb_uv(config)
        bps = params['bps']
        fpkt = params['fpkt']
        emg_len = params['emg_len']
        imu_len = params['imu_len']
        
        start_frame = struct.unpack('<I', data[0:4])[0]
        
        emg_raw = []
        emg_uv = []
        raw_bytes = data[4: 4 + emg_len]
        stride = 16 * bps
        
        for i in range(fpkt):
            offset = i * stride
            raw_row = []
            uv_row = []
            
            for ch in range(16):
                ch_offset = offset + ch * bps
                chunk = raw_bytes[ch_offset: ch_offset + bps]
                val = int.from_bytes(chunk, 'big', signed=True)
                raw_row.append(val)
                uv_row.append(val * lsb_uv)
            
            emg_raw.append(raw_row)
            emg_uv.append(uv_row)
        
        imu = None
        if config['imu_enabled'] and imu_len > 0:
            imu_start = 4 + emg_len
            imu_bytes = data[imu_start: imu_start + imu_len]
            
            def parse_imu(b):
                ag = struct.unpack('>6h', b[0:12])
                m = struct.unpack('<3h', b[12:18])
                return [
                    [x * SCALE_ACCEL for x in ag[0:3]],
                    [x * SCALE_GYRO for x in ag[3:6]],
                    [x * SCALE_MAG for x in m[0:3]],
                ]
            
            imu = [
                parse_imu(imu_bytes[0:18]),
                parse_imu(imu_bytes[18:36]),
            ]
        
        dev.total_frames += fpkt
        
        if dev.last_frame_index >= 0:
            expected = dev.last_frame_index + 1
            if start_frame != expected and start_frame > expected:
                dev.lost_frames += start_frame - expected
        
        dev.last_frame_index = start_frame + fpkt - 1

        # ===== 生成每帧的BLE帧号 =====
        # 用于后续与SD卡bin文件同步
        # 映射关系: SD卡帧号 = BLE帧号 * 8 + 7 (2kHz采样时)
        frame_ids = [start_frame + i for i in range(fpkt)]

        # ===== 应用滤波 =====
        emg_uv_filtered = emg_uv  # 默认使用原始数据
        
        if FILTER_ENABLED and HAS_SCIPY:
            # 根据设备ID选择对应的滤波器
            emg_filter = emg_filter_dev1 if dev.device_id == 1 else emg_filter_dev2
            
            if emg_filter and emg_filter.enabled:
                try:
                    emg_uv_filtered = emg_filter.filter_batch(emg_uv)
                except Exception as e:
                    log(f"[Dev{dev.device_id}] 滤波错误: {e}")
                    emg_uv_filtered = emg_uv  # 滤波失败时使用原始数据
        
        return {
            'f': start_frame,
            'n': fpkt,
            'frame_ids': frame_ids,  # 每帧的BLE帧号，用于同步
            'raw': emg_raw,
            'uv': emg_uv_filtered,  # 使用滤波后的数据
            'imu': imu,
            's': [dev.total_frames, dev.lost_frames],
        }
        
    except Exception as e:
        log(f"[Dev{dev.device_id}] 解析错误: {e}")
        return None


# ================= BLE 回调 =================

def create_notification_handler(dev: DeviceState):
    def handler(sender: int, data: bytearray):
        try:
            ts = time.time()
            parsed = parse_packet(data, dev)
            if parsed:
                parsed['t'] = ts

                # 【调试】每100个包打印一次日志
                if dev.total_frames % 100 == 0:
                    log(f"[Dev{dev.device_id}] 已收到 {dev.total_frames} 包, 丢包: {dev.lost_frames}, 缓冲区: {len(dev.data_buffer)}")

                # 生成每帧EMG的时间戳
                # 注意：BLE传输的是250Hz数据（2kHz降采样8倍），所以时间间隔是1/250=0.004秒
                fpkt = parsed.get('n', 9)
                ble_sample_rate = 250  # BLE传输频率固定为250Hz
                frame_interval = 1.0 / ble_sample_rate  # 0.004秒

                # 为每帧生成时间戳（从当前时间向前推算）
                emg_timestamps = []
                for i in range(fpkt):
                    # 最后一帧的时间是ts，往前推算
                    frame_ts = ts - (fpkt - 1 - i) * frame_interval
                    emg_timestamps.append(frame_ts)
                parsed['emg_t'] = emg_timestamps

                # IMU时间戳（每包1帧IMU，与EMG同步）
                # IMU也是250Hz，使用最后一帧的时间戳
                if parsed.get('imu'):
                    imu_timestamps = [ts]
                    parsed['imu_t'] = imu_timestamps
                
                dev.data_buffer.append(parsed)
        except Exception as e:
            log(f"[Dev{dev.device_id}] 回调错误: {e}")
    return handler


# ================= 消息队列 =================

def data_sender_thread():
    """数据发送线程 - 发送到数据端"""
    log("数据发送线程启动")
    
    while not state.stop_thread:
        try:
            active = state.get_active_devices()
            
            if state.data_clients and active:
                dev1_data = None
                dev2_data = None
                
                if state.dev1.is_streaming and state.dev1.data_buffer:
                    batch1 = []
                    while state.dev1.data_buffer and len(batch1) < 5:
                        batch1.append(state.dev1.data_buffer.popleft())
                    if batch1:
                        dev1_data = batch1 if len(batch1) > 1 else batch1[0]
                
                if state.dev2.is_streaming and state.dev2.data_buffer:
                    batch2 = []
                    while state.dev2.data_buffer and len(batch2) < 5:
                        batch2.append(state.dev2.data_buffer.popleft())
                    if batch2:
                        dev2_data = batch2 if len(batch2) > 1 else batch2[0]
                
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
                #payload = msgpack.packb(data)
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


# ================= BLE 操作 =================

async def scan_devices(ws):
    """扫描设备"""
    log(f"扫描设备（{SCAN_TIMEOUT}秒）...")
    
    try:
        state.devices_found.clear()
        state.scan_results.clear()
        
        devices = await BleakScanner.discover(
            timeout=SCAN_TIMEOUT,
            return_adv=True,
            scanning_mode="active"
        )
        
        targets = []
        
        for d, adv in devices.values():
            if d.address and not any(_d['mac'] == d.address for _d in targets):

                display = f"{d.name} ({d.address})"
                state.devices_found[display] = d
                
                # 尝试获取 RSSI
                #rssi = getattr(d, 'rssi', None) or -100
                
                info = {
                    'name': d.name or adv.local_name or "未知设备",
                    'mac': d.address.upper(),
                    'display': display,
                    "rssi": adv.rssi if adv.rssi is not None else None,
                    "manufacturer": str(adv.manufacturer_data)[:50]
                }
                state.scan_results.append(info)
                
                # if d.name == TARGET_DEVICE_NAME:
                #     targets.append(info)
        
        # 按 RSSI 排序
        state.scan_results.sort(key=lambda x: x['rssi'], reverse=True)
        
        #log(f"找到 {len(state.scan_results)} 个设备，目标 {len(targets)} 个")
        log(f"找到 {len(state.scan_results)} 个设备")
        
        await send_to_control(ws, 'scan', {
            'success': True,
            'devices': state.scan_results,
            'count': len(state.scan_results),
            'targets': targets,
        })
        
    except Exception as e:
        log(f"扫描失败: {e}")
        await send_to_control(ws, 'scan', {
            'success': False,
            'error': str(e),
        })


async def connect_device(ws, device_id: int, mac_or_name: str):
    """连接设备"""
    dev = state.get_device(device_id)
    action = f'connect{device_id}'
    
    # 查找设备
    device = None
    rssi = None
    
    if mac_or_name in state.devices_found:
        device = state.devices_found[mac_or_name]
    else:
        mac_upper = mac_or_name.upper()
        for info in state.scan_results:
            if info['mac'] == mac_upper:
                device = state.devices_found.get(info['display'])
                rssi = info.get('rssi')
                break
        
        if device is None:
            for name, d in state.devices_found.items():
                if d.address.upper() == mac_upper:
                    device = d
                    break
    
    if device is None:
        log(f"[Dev{device_id}] 直接查找: {mac_or_name}")
        device = await BleakScanner.find_device_by_address(
            mac_or_name.upper(), timeout=5.0
        )
    
    if device is None:
        await send_to_control(ws, action, {
            'success': False,
            'device_id': device_id,
            'error': f"未找到设备: {mac_or_name}",
        })
        return
    
    mac = device.address.upper()
    log(f"[Dev{device_id}] 连接: {device.name} ({mac})")
    
    if dev.is_connected():
        await disconnect_device(ws, device_id, silent=True)
    
    for retry in range(MAX_RETRIES):
        try:
            await send_to_control(ws, action, {
                'success': None,
                'device_id': device_id,
                'message': f"连接中 ({retry+1}/{MAX_RETRIES})...",
                'mac': mac,
            })
            
            client = BleakClient(device, timeout=CONNECT_TIMEOUT)
            await client.connect()
            
            if client.is_connected:
                dev.client = client
                dev.device = device
                dev.mac = mac
                dev.name = device.name
                dev.rssi = rssi
                dev.reset_stats()

                log(f"[Dev{device_id}] 连接成功: {mac}")

                # ===================== 连接成功后发送配置命令 =====================
                # 在连接成功后立即配置ESP32，而不是在start_stream时配置
                # 这样可以避免配置命令和START命令之间的时序问题
                try:
                    # 1. 发送采样率配置 (2kHz = 0x12)
                    sample_rate_cmd = bytes([CMD_MAP['2kHz']])
                    await dev.client.write_gatt_char(
                        CONTROL_CHAR_UUID,
                        sample_rate_cmd,
                        response=False
                    )
                    log(f"[Dev{device_id}] 已发送采样率配置: 2kHz (0x12)")
                    await asyncio.sleep(0.1)  # 等待ESP32处理

                    # 2. 发送复合配置命令 (0xC0 + [gain_index, mode, shift, imu_en])
                    # gain_index=6 (增益12), mode=0 (24-bit), shift=4, imu_enabled=1
                    config = dev.config
                    config_cmd = bytes([
                        CMD_MAP['CONFIG'],
                        config['gain_index'],      # 6 = 增益12
                        0 if not config['is_16bit'] else 1,  # 0 = 24-bit模式
                        config['shift'],           # 4
                        1 if config['imu_enabled'] else 0,   # 1 = IMU启用
                    ])
                    await dev.client.write_gatt_char(
                        CONTROL_CHAR_UUID,
                        config_cmd,
                        response=False
                    )
                    log(f"[Dev{device_id}] 已发送复合配置: Gain={config['gain_index']}, Mode={'16bit' if config['is_16bit'] else '24bit'}, Shift={config['shift']}, IMU={config['imu_enabled']}")
                    await asyncio.sleep(0.1)  # 等待ESP32处理

                except Exception as e:
                    log(f"[Dev{device_id}] 配置命令发送失败: {e}")
                    # 配置失败不影响连接状态，继续执行
                # ===================== 配置命令结束 =====================

                await send_to_control(ws, action, {
                    'success': True,
                    'device_id': device_id,
                    'mac': mac,
                    'name': device.name,
                    'rssi': rssi,
                    'connected': state.get_connected_devices(),
                })

                # 广播连接事件
                await broadcast_event('device_connected', {
                    'device_id': device_id,
                    'mac': mac,
                    'name': device.name,
                })
                return
                
        except TimeoutError:
            log(f"[Dev{device_id}] 连接超时")
        except BleakError as e:
            log(f"[Dev{device_id}] BLE 错误: {e}")
        except Exception as e:
            log(f"[Dev{device_id}] 连接异常: {e}")
        
        if retry < MAX_RETRIES - 1:
            await asyncio.sleep(RETRY_DELAY)
    
    await send_to_control(ws, action, {
        'success': False,
        'device_id': device_id,
        'error': f"连接失败（已重试 {MAX_RETRIES} 次）",
        'mac': mac,
    })


async def disconnect_device(ws, device_id: int, silent=False):
    """断开设备"""
    dev = state.get_device(device_id)
    action = f'disconnect{device_id}'
    mac = dev.mac

    try:
        if dev.is_streaming:
            await stop_stream(ws, device_id, silent=True)

        if dev.client:
            try:
                await dev.client.disconnect()
            except:
                pass
            dev.client = None

        dev.device = None
        dev.mac = None
        dev.name = None
        dev.rssi = None
        dev.connect_task = None
        dev.sd_filename = None  # 【修复】断开连接时清除SD卡文件名

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
    """开始采集"""
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

    try:
        dev.reset_stats()

        # ===================== 发送SD卡文件名命令 =====================
        # 格式: 0xD0 + "S001_L_20260312_143025" (最大31字节)
        # 生成文件名字符串: 会话ID + 左右手标识 + 时间戳
        now_str = datetime.now().strftime("%y%m%d_%H%M%S")  # 例如 "260312_143025" (6位年份节省空间)

        # 根据设备ID确定左右手标识: 设备1=左手(L), 设备2=右手(R)
        hand_label = "L" if device_id == 1 else "R"

        if state.session_id:
            # 有会话ID: "S001_L_260312_143025"
            filename_str = f"{state.session_id}_{hand_label}_{now_str}"
        else:
            # 无会话ID: "L_260312_143025"
            filename_str = f"{hand_label}_{now_str}"

        # 确保不超过31字节
        if len(filename_str) > 31:
            filename_str = filename_str[:31]

        filename_cmd = bytes([CMD_MAP['SET_FILENAME']]) + filename_str.encode('ascii')
        await dev.client.write_gatt_char(
            CONTROL_CHAR_UUID,
            filename_cmd,
            response=False
        )
        log(f"[Dev{device_id}] 已发送SD卡文件名: {filename_str} ({'左手' if device_id == 1 else '右手'})")

        # 【新增】保存文件名到设备状态，用于后续传递给storage_server
        dev.sd_filename = filename_str

        await asyncio.sleep(0.1)  # 等待ESP32处理
        # ===================== SD卡文件名命令结束 =====================

        # 【重要】不再发送额外的配置命令，使用ESP32的默认配置
        # 供应商代码也是这样做的：只在用户点击"应用配置"时才发送配置命令
        # ESP32默认配置: 24-bit, 9帧/包, IMU启用, 增益12

        handler = create_notification_handler(dev)
        await dev.client.start_notify(EMG_DATA_CHAR_UUID, handler)
        log(f"[Dev{device_id}] 已订阅数据通知")

        await dev.client.write_gatt_char(
            CONTROL_CHAR_UUID,
            bytes([CMD_MAP['START']]),
            response=False
        )
        log(f"[Dev{device_id}] 已发送 START 命令")

        dev.is_streaming = True

        await send_to_control(ws, action, {
            'success': True,
            'device_id': device_id,
            'active': state.get_active_devices(),
        })

        await broadcast_event('stream_started', {
            'device_id': device_id,
            'active': state.get_active_devices(),
        })

    except Exception as e:
        log(f"[Dev{device_id}] 启动失败: {e}")
        import traceback
        traceback.print_exc()
        await send_to_control(ws, action, {
            'success': False,
            'device_id': device_id,
            'error': str(e),
        })


async def stop_stream(ws, device_id: int, silent=False):
    """停止采集"""
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
    
    try:
        if dev.client and dev.is_connected():
            await dev.client.write_gatt_char(
                CONTROL_CHAR_UUID,
                bytes([CMD_MAP['STOP']]),
                response=False
            )
            log(f"[Dev{device_id}] 已发送 STOP")
            
            await dev.client.stop_notify(EMG_DATA_CHAR_UUID)
            log(f"[Dev{device_id}] 已取消订阅")
        
        dev.is_streaming = False
        
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
            
    except Exception as e:
        log(f"[Dev{device_id}] 停止失败: {e}")
        if not silent:
            await send_to_control(ws, action, {
                'success': False,
                'device_id': device_id,
                'error': str(e),
            })


async def start_all(ws):
    """同时开始"""
    started = []

    if state.dev1.is_connected() and not state.dev1.is_streaming:
        await start_stream(ws, 1)
        if state.dev1.is_streaming:
            started.append(1)

    if state.dev2.is_connected() and not state.dev2.is_streaming:
        await start_stream(ws, 2)
        if state.dev2.is_streaming:
            started.append(2)

    # 【新增】收集当前采集的SD卡bin文件名
    sd_filenames = {}
    if state.dev1.sd_filename:
        sd_filenames['dev1'] = state.dev1.sd_filename  # 例如 "S001_L_260312_143025"
    if state.dev2.sd_filename:
        sd_filenames['dev2'] = state.dev2.sd_filename  # 例如 "S001_R_260312_143025"

    await send_to_control(ws, 'start_all', {
        'success': True,
        'started': started,
        'active': state.get_active_devices(),
        'sd_filenames': sd_filenames,  # 【新增】返回bin文件名
    })

    # 【新增】广播bin文件名事件给数据端（realtimeEngine）
    if sd_filenames:
        await broadcast_event('sd_filenames_updated', {
            'sd_filenames': sd_filenames,
        })


async def stop_all(ws):
    """同时停止"""
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
        'message': '控制端已连接',
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
                    import json
                    data = json.loads(message)
                
                action = data.get('action', '')
                # 不打印 status 命令，减少日志噪音
                if action != 'status':
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

                # 【新增】设置会话ID
                elif action == 'set_session_id':
                    session_id = data.get('session_id', '')
                    # 验证会话ID格式（只允许字母、数字、下划线，最大10字符）
                    if session_id:
                        # 清理非法字符
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
        'message': '数据端已连接',
        'active': state.get_active_devices(),
        'connected': state.get_connected_devices(),
    }
    try:
        #await websocket.send(msgpack.packb(welcome))
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
                    import json
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
                    #await websocket.send(msgpack.packb(status))
                    await websocket.send(json.dumps(status, ensure_ascii=False))
                    
            except Exception as e:
                log(f"[数据端] 处理错误: {e}")
                
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        state.data_clients.discard(websocket)
        log(f"[数据端] 客户端断开 (剩余: {len(state.data_clients)})")


# ================= 主函数 =================

async def warmup_ble_adapter():
    """预热蓝牙适配器 - 解决首次扫描失败的问题"""
    log("预热蓝牙适配器...")
    try:
        # 进行一次快速扫描来初始化Windows蓝牙后端
        await BleakScanner.discover(timeout=2.0)
        log("蓝牙适配器预热完成")
    except Exception as e:
        log(f"蓝牙适配器预热警告: {e} (可忽略)")


async def heartbeat_task():
    """每30秒打印一次心跳，证明服务还活着"""
    while True:
        await asyncio.sleep(30)
        dev1_status = "采集中" if state.dev1.is_streaming else ("已连接" if state.dev1.client and state.dev1.client.is_connected else "未连接")
        dev2_status = "采集中" if state.dev2.is_streaming else ("已连接" if state.dev2.client and state.dev2.client.is_connected else "未连接")
        log(f"[心跳] 服务运行中 | 设备1: {dev1_status} | 设备2: {dev2_status}")


async def main():
    state.main_loop = asyncio.get_running_loop()

    # 预热蓝牙适配器（解决首次扫描失败问题）
    await warmup_ble_adapter()

    # 初始化滤波器
    init_filters()
    
    # 启动数据发送线程
    state.stop_thread = False
    state.data_thread = threading.Thread(target=data_sender_thread, daemon=True)
    state.data_thread.start()
    
    log("=" * 60)
    log("BLE Server (Dual Device + Dual WebSocket) 已启动")
    log("=" * 60)
    log(f"控制端口: ws://{WEBSOCKET_HOST}:{CONTROL_PORT} (index.html)")
    log(f"数据端口: ws://{WEBSOCKET_HOST}:{DATA_PORT} (realtimeEngine.js)")
    log(f"目标设备: {TARGET_DEVICE_NAME}")
    log(f"滤波功能: {'启用' if FILTER_ENABLED and HAS_SCIPY else '禁用'}")
    if FILTER_ENABLED and HAS_SCIPY:
        log(f"  - 带通滤波: {FILTER_LOWCUT}-{FILTER_HIGHCUT}Hz")
        log(f"  - 工频陷波: {FILTER_NOTCH_FREQ}Hz (Q={FILTER_NOTCH_Q})")
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

        # 启动心跳任务
        asyncio.create_task(heartbeat_task())

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
