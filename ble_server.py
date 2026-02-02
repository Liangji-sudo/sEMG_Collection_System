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
FILTER_ENABLED = True          # 总开关：是否启用滤波
FILTER_LOWCUT = 10             # 带通下限 (Hz) - 降低到10Hz保留更多低频信号
FILTER_HIGHCUT = 450           # 带通上限 (Hz)
FILTER_NOTCH_FREQ = 50         # 工频频率 (Hz)
FILTER_NOTCH_Q = 30            # 陷波器Q值（越大越窄）
FILTER_BANDPASS_ENABLED = True # 启用带通滤波
FILTER_NOTCH_ENABLED = True    # 启用工频陷波

# 注意：如果前端显示信号幅度太小，可以在前端调整 Offset 值
# 典型EMG信号幅度：静息时 10-50μV，收缩时 100-500μV
# 建议前端 Offset 设置：100-200μV (默认300可能太大)


# ================= EMG实时滤波器类 =================
class EMGRealtimeFilter:
    """
    EMG实时滤波器
    
    功能：
    - 带通滤波 (默认20-450Hz)：去除直流分量和高频噪声
    - 工频陷波 (50Hz及谐波)：去除电源干扰
    
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
            notch_q: 陷波器Q值
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
        self.highcut = min(highcut, fs / 2 - 1)  # 不能超过奈奎斯特频率
        self.notch_freq = notch_freq
        self.notch_q = notch_q
        self.enable_bandpass = enable_bandpass
        self.enable_notch = enable_notch
        
        # 滤波器系数
        self.b_hp = None  # 高通滤波器
        self.a_hp = None
        self.b_lp = None  # 低通滤波器
        self.a_lp = None
        self.notch_filters = []  # 陷波滤波器列表 [(b, a), ...]
        
        # 滤波器状态 (每个通道独立)
        self.zi_hp = None
        self.zi_lp = None
        self.zi_notch = None
        
        # 初始化滤波器
        self._init_filters()
        
        log(f"[滤波器] 初始化完成: fs={fs}Hz, 带通={lowcut}-{highcut}Hz, 陷波={notch_freq}Hz")
    
    def _init_filters(self):
        """初始化滤波器系数和状态"""
        if not self.enabled:
            return
        
        # 1. 高通滤波器 (去除直流)
        if self.enable_bandpass:
            self.b_hp, self.a_hp = scipy_signal.butter(
                2, self.lowcut, btype='highpass', fs=self.fs
            )
            # 初始化状态
            zi = scipy_signal.lfilter_zi(self.b_hp, self.a_hp)
            self.zi_hp = np.tile(zi[:, np.newaxis], (1, self.num_channels))
            
            # 低通滤波器 (去除高频噪声)
            self.b_lp, self.a_lp = scipy_signal.butter(
                2, self.highcut, btype='lowpass', fs=self.fs
            )
            zi = scipy_signal.lfilter_zi(self.b_lp, self.a_lp)
            self.zi_lp = np.tile(zi[:, np.newaxis], (1, self.num_channels))
        
        # 2. 工频陷波滤波器 (50Hz及谐波)
        if self.enable_notch:
            self.notch_filters = []
            self.zi_notch = []
            
            # 陷波50Hz及其谐波 (100Hz, 150Hz, ...)
            for freq in range(self.notch_freq, int(self.fs / 2), self.notch_freq):
                b, a = scipy_signal.iirnotch(freq, self.notch_q, self.fs)
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
        
        # 应用高通滤波 (去除直流)
        if self.enable_bandpass and self.b_hp is not None:
            data, self.zi_hp = scipy_signal.lfilter(
                self.b_hp, self.a_hp, data, axis=0, zi=self.zi_hp
            )
            # 应用低通滤波
            data, self.zi_lp = scipy_signal.lfilter(
                self.b_lp, self.a_lp, data, axis=0, zi=self.zi_lp
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
        
        # 应用高通滤波 (去除直流)
        if self.enable_bandpass and self.b_hp is not None:
            data, self.zi_hp = scipy_signal.lfilter(
                self.b_hp, self.a_hp, data, axis=0, zi=self.zi_hp
            )
            # 应用低通滤波
            data, self.zi_lp = scipy_signal.lfilter(
                self.b_lp, self.a_lp, data, axis=0, zi=self.zi_lp
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
    
    config: Dict = field(default_factory=lambda: DEFAULT_CONFIG.copy())
    data_buffer: deque = field(default_factory=lambda: deque(maxlen=500))
    connect_task: Any = None
    
    def reset_stats(self):
        self.total_frames = 0
        self.lost_frames = 0
        self.last_frame_index = -1
        self.data_buffer.clear()
        
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

        # ===================== 【新增】发送配置命令 =====================
        # 配置ESP32工作在2kHz采样率，确保SD卡存储的是2kHz数据
        config = dev.config

        # 1. 发送采样率命令 (0x12 = 2kHz)
        sample_rate_cmd = CMD_MAP['2kHz']
        await dev.client.write_gatt_char(
            CONTROL_CHAR_UUID,
            bytes([sample_rate_cmd]),
            response=False
        )
        log(f"[Dev{device_id}] 已发送采样率配置: 2kHz (0x{sample_rate_cmd:02X})")
        await asyncio.sleep(0.1)  # 等待ESP32处理

        # 2. 发送复合配置命令 [0xC0, GainIdx, Mode, Shift, IMU_En]
        # GainIdx: 6 (增益12)
        # Mode: 0 (24-bit), 1 (16-bit)
        # Shift: 4 (默认)
        # IMU_En: 1 (启用IMU)
        gain_idx = config.get('gain_index', 6)
        mode = 1 if config.get('is_16bit', False) else 0
        shift = config.get('shift', 4)
        imu_en = 1 if config.get('imu_enabled', True) else 0

        config_cmd = bytes([CMD_MAP['CONFIG'], gain_idx, mode, shift, imu_en])
        await dev.client.write_gatt_char(
            CONTROL_CHAR_UUID,
            config_cmd,
            response=False
        )
        log(f"[Dev{device_id}] 已发送复合配置: Gain={gain_idx}, Mode={mode}, Shift={shift}, IMU={imu_en}")
        await asyncio.sleep(0.1)  # 等待ESP32处理
        # ===================== 配置命令结束 =====================

        handler = create_notification_handler(dev)
        await dev.client.start_notify(EMG_DATA_CHAR_UUID, handler)
        log(f"[Dev{device_id}] 已订阅")

        await dev.client.write_gatt_char(
            CONTROL_CHAR_UUID,
            bytes([CMD_MAP['START']]),
            response=False
        )
        log(f"[Dev{device_id}] 已发送 START")

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
    
    await send_to_control(ws, 'start_all', {
        'success': True,
        'started': started,
        'active': state.get_active_devices(),
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
