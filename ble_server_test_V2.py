"""
BLE Server Test V2 — 腕带供应商 V2 真机链路测试工具
=====================================================
独立运行，不依赖 Node、realtimeEngine、storage_server、前端页面。
通过 BLE 直接连接腕带设备，采集数据并校验 V2 协议格式，
保存测试结果到本地文件。

运行:  python ble_server_test_V2.py

依赖:  pip install bleak
"""

import asyncio
import csv
import io
import json
import os
import struct
import sys
import threading
import time
import traceback
import queue
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# ---- 编码配置 (Windows 中文输出) ----
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

from bleak import BleakScanner, BleakClient, BleakError

# ---- tkinter ----
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

# ==================== BLE 常量 (来自 ble_server.py) ====================

TARGET_DEVICE_NAME = "ESP32S3_EMG"
CONTROL_CHAR_UUID = "9e5c100d-afc2-4e4b-b132-f2c0032f7a0b"
EMG_DATA_CHAR_UUID = "9e5c100d-afc2-4e4b-b132-f2c0032f7a0c"
STATUS_CHAR_UUID = "9e5c100d-afc2-4e4b-b132-f2c0032f7a0e"

CMD_MAP = {
    '500Hz': 0x10,
    '1kHz': 0x11,
    '2kHz': 0x12,
    'START': 0xA0,
    'STOP': 0xA1,
    'CONFIG': 0xC0,
    'SET_FILENAME': 0xD0,
}

DEFAULT_CONFIG = {
    'sample_rate': 2000,
    'gain': 12,
    'gain_index': 6,
    'is_16bit': False,
    'shift': 4,
    'imu_enabled': True,
    'frames_per_packet': 9,
}

SCALE_ACCEL = 32.0 / 32768.0
SCALE_ACCEL_V1 = 16.0 / 32768.0
SCALE_GYRO = 2000.0 / 32768.0
SCALE_MAG = 0.15
BASE_LSB_24BIT = 0.2861
HARDWARE_FRONTEND_GAIN = 10

BLE_SAMPLE_RATE = 250
BYTES_PER_IMU = 18
MAX_NUM_IMUS_V1 = 2
MAX_NUM_IMUS_V2 = 3

CHANNELS_MAP_V1 = [14, 15, 16, 3, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
CHANNELS_MAP_V2 = [15, 16, 14, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]

STATUS_PACKET_SNAPSHOT = 0x01
STATUS_PACKET_EVENT = 0x02
STATUS_SNAPSHOT_FORMAT = "<BBBBHBBHBBIIIIIII16s16s"

MAX_RETRIES = 3
RETRY_DELAY = 1.0

# ==================== 工具函数 (来自 ble_server.py) ====================

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


def parse_imu_v1(data: bytearray, emg_len: int) -> list:
    """解析 V1 IMU (ICM-20948, 2 chips, Big Endian acc/gyr, LE mag)"""
    imu_start = 4 + emg_len
    imu_bytes = data[imu_start: imu_start + 36]

    def parse_chip(b):
        ag = struct.unpack('>6h', b[0:12])
        m = struct.unpack('<3h', b[12:18])
        return [
            [x * SCALE_ACCEL_V1 for x in ag[0:3]],
            [x * SCALE_GYRO for x in ag[3:6]],
            [x * SCALE_MAG for x in m[0:3]],
        ]

    return [parse_chip(imu_bytes[0:18]), parse_chip(imu_bytes[18:36])]


def parse_imu_v2(data: bytearray, emg_len: int, num_imus: int) -> list:
    """解析 V2 IMU (LSM6DSV32X, 0-3 chips, 全部 Little Endian, 无 mag)"""
    imu_start = 4 + emg_len
    imus = []
    for i in range(num_imus):
        offset = imu_start + i * BYTES_PER_IMU
        b = data[offset: offset + BYTES_PER_IMU]
        ag = struct.unpack('<6h', b[0:12])
        imus.append([
            [x * SCALE_ACCEL for x in ag[0:3]],
            [x * SCALE_GYRO for x in ag[3:6]],
        ])
    return imus


def parse_packet(data: bytearray, dev: 'TestDeviceState') -> Optional[dict]:
    """解析单个 BLE 数据包 (来自 ble_server.py parse_packet)"""
    params = get_packet_params(dev.config)
    emg_len = params['emg_len']
    payload_len = len(data) - 4

    # V2 动态包长校验
    if dev.hw_version == "V2":
        if payload_len < emg_len:
            return None
        imu_byte_count = payload_len - emg_len
        if imu_byte_count % BYTES_PER_IMU != 0:
            return None
        num_imus = imu_byte_count // BYTES_PER_IMU
        if num_imus > MAX_NUM_IMUS_V2:
            return None
        if num_imus != dev.num_imus:
            dev.num_imus = num_imus
    else:
        if len(data) != params['total_len']:
            return None
        num_imus = MAX_NUM_IMUS_V1

    try:
        bps = params['bps']
        fpkt = params['fpkt']
        lsb_uv = calculate_lsb_uv(dev.config)

        start_frame = struct.unpack('<I', data[0:4])[0]

        # EMG 解析 (物理顺序)
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

        # 通道映射
        emg_raw_mapped = []
        emg_uv_mapped = []
        for row_raw, row_uv in zip(emg_raw, emg_uv):
            mapped_raw = [row_raw[i - 1] for i in dev.channel_map]
            mapped_uv = [row_uv[i - 1] for i in dev.channel_map]
            emg_raw_mapped.append(mapped_raw)
            emg_uv_mapped.append(mapped_uv)

        # IMU 解析
        imu = None
        if dev.config['imu_enabled'] and num_imus > 0:
            if dev.hw_version == "V2":
                imu = parse_imu_v2(data, emg_len, num_imus)
            else:
                imu = parse_imu_v1(data, emg_len)

        dev.total_frames += fpkt

        if dev.last_frame_index >= 0:
            expected = dev.last_frame_index + 1
            if start_frame != expected and start_frame > expected:
                dev.lost_frames += start_frame - expected
        dev.last_frame_index = start_frame + fpkt - 1

        frame_ids = [start_frame + i for i in range(fpkt)]

        return {
            'f': start_frame,
            'n': fpkt,
            'frame_ids': frame_ids,
            'raw': emg_raw_mapped,
            'uv': emg_uv_mapped,
            'imu': imu,
            'num_imus': num_imus,
            'hw_version': dev.hw_version,
        }

    except Exception:
        return None


# ==================== 测试设备状态 ====================

@dataclass
class TestDeviceState:
    """单个 BLE 测试设备状态"""
    device_id: int

    client: Optional[BleakClient] = None
    device: Any = None
    mac: Optional[str] = None
    name: Optional[str] = None

    is_streaming: bool = False
    total_frames: int = 0
    lost_frames: int = 0
    last_frame_index: int = -1

    config: Dict = field(default_factory=lambda: DEFAULT_CONFIG.copy())

    # V2 检测
    hw_version: str = "V1"
    firmware_version: str = ""
    hardware_version: str = ""
    num_imus: int = 2
    channel_map: List[int] = field(default_factory=lambda: CHANNELS_MAP_V1)

    # 统计
    packet_count: int = 0
    imu_row_count: int = 0
    parse_error_count: int = 0
    last_packet_len: int = 0
    last_frame_id: int = 0
    last_num_imus: int = 0
    mag_detected: bool = False  # V2 不应有 mag
    mag_detected_count: int = 0

    def reset_stats(self):
        self.total_frames = 0
        self.lost_frames = 0
        self.last_frame_index = -1
        self.packet_count = 0
        self.imu_row_count = 0
        self.parse_error_count = 0
        self.last_packet_len = 0
        self.last_frame_id = 0
        self.last_num_imus = 0
        self.mag_detected = False
        self.mag_detected_count = 0

    def is_connected(self) -> bool:
        if self.client is None:
            return False
        try:
            return bool(self.client.is_connected)
        except Exception:
            return False


# ==================== V2 协议校验 ====================

class V2Validator:
    """V2 协议格式校验器"""

    def __init__(self):
        self.errors: List[Dict] = []

    def check_packet(self, parsed: dict, dev: TestDeviceState, raw_len: int) -> List[Dict]:
        """校验一个已解析的 BLE 包，返回错误列表"""
        errors = []

        # 1. hw_version 应为 V2
        if parsed.get('hw_version') != 'V2':
            errors.append({
                'code': 'HW_VERSION_NOT_V2',
                'message': f"hw_version={parsed.get('hw_version')}, 期望 V2",
            })

        # 2. 每包 EMG 16 通道
        uv = parsed.get('uv', [])
        for fi, frame in enumerate(uv):
            if len(frame) != 16:
                errors.append({
                    'code': 'EMG_CHANNEL_COUNT',
                    'message': f"帧 {fi} 通道数={len(frame)}, 期望 16",
                })

        # 3. frames_per_packet 应为 9
        n = parsed.get('n', 0)
        if n != 9:
            errors.append({
                'code': 'FRAMES_PER_PACKET',
                'message': f"n={n}, 期望 9",
            })

        # 4-6. IMU 校验 (V2 特定: 必须有 3 个 6 轴 IMU, 无 mag)
        imu = parsed.get('imu')
        num_imus = parsed.get('num_imus', 0)

        if imu and num_imus > 0:
            # 4. V2 IMU 应为 3 个
            actual_count = len(imu)
            if actual_count != 3:
                errors.append({
                    'code': 'IMU_COUNT',
                    'message': f"实际 IMU 数={actual_count}, 期望 3",
                })

            # 5. 每个 IMU 格式应为 [acc[3], gyr[3]] (无 mag)
            for ii, chip in enumerate(imu):
                if len(chip) != 2:
                    errors.append({
                        'code': 'IMU_FORMAT',
                        'message': f"IMU[{ii}] 子数组数={len(chip)}, 期望 2 (acc/gyr only)",
                    })
                else:
                    acc, gyr = chip[0], chip[1]
                    if len(acc) != 3:
                        errors.append({
                            'code': 'IMU_ACC_AXES',
                            'message': f"IMU[{ii}] acc 轴数={len(acc)}, 期望 3",
                        })
                    if len(gyr) != 3:
                        errors.append({
                            'code': 'IMU_GYR_AXES',
                            'message': f"IMU[{ii}] gyr 轴数={len(gyr)}, 期望 3",
                        })
                    # 6. V2 不应有 mag
                    if len(chip) >= 3:
                        dev.mag_detected = True
                        dev.mag_detected_count += 1
                        errors.append({
                            'code': 'V2_HAS_MAG',
                            'message': f"IMU[{ii}] 包含 mag 数据 (len={len(chip)}), V2 不应有磁力计",
                        })
        else:
            # V2 必须有 IMU 数据
            errors.append({
                'code': 'IMU_MISSING',
                'message': f"parsed['imu'] 为空或 num_imus={num_imus}, V2 必须有 3 个 IMU",
            })

        # 7. num_imus 必须等于 3 (包括 0 也要报错)
        if num_imus != 3:
            errors.append({
                'code': 'NUM_IMUS',
                'message': f"num_imus={num_imus}, 期望 3",
            })

        return errors


# ==================== 数据写入器 ====================

class DataWriter:
    """写入测试数据到 CSV/JSON 文件"""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        self.emg_files: Dict[int, Any] = {}
        self.imu_files: Dict[int, Any] = {}
        self.error_file = None
        self.meta_file = None
        self._error_writer = None
        self._meta_writer = None

    def open(self):
        """打开所有输出文件"""
        # EMG CSV
        for did in [1, 2]:
            path = os.path.join(self.output_dir, f"dev{did}_emg.csv")
            f = open(path, 'w', newline='', encoding='utf-8-sig')
            w = csv.writer(f)
            ch_headers = [f"ch{i}" for i in range(1, 17)]
            w.writerow(['timestamp', 'frame_id'] + ch_headers)
            self.emg_files[did] = (f, w)

        # IMU CSV
        for did in [1, 2]:
            path = os.path.join(self.output_dir, f"dev{did}_imu.csv")
            f = open(path, 'w', newline='', encoding='utf-8-sig')
            w = csv.writer(f)
            w.writerow(['timestamp', 'frame_id', 'imu_index',
                        'ax', 'ay', 'az', 'gx', 'gy', 'gz', 'has_mag'])
            self.imu_files[did] = (f, w)

        # Errors CSV
        epath = os.path.join(self.output_dir, "errors.csv")
        self.error_file = open(epath, 'w', newline='', encoding='utf-8-sig')
        self._error_writer = csv.writer(self.error_file)
        self._error_writer.writerow(['timestamp', 'device', 'level', 'code',
                                      'message', 'packet_len', 'frame_id'])

        # Raw packets meta CSV
        mpath = os.path.join(self.output_dir, "raw_packets_meta.csv")
        self.meta_file = open(mpath, 'w', newline='', encoding='utf-8-sig')
        self._meta_writer = csv.writer(self.meta_file)
        self._meta_writer.writerow(['timestamp', 'device', 'packet_len',
                                     'frame_id', 'frames_per_packet',
                                     'parsed_num_imus', 'hw_version'])

    def write_emg(self, device_id: int, ts: float, frame_ids: List[int],
                  uv_frames: List[List[float]]):
        entry = self.emg_files.get(device_id)
        if not entry:
            return
        _, w = entry
        for fi, fid in enumerate(frame_ids):
            if fi < len(uv_frames):
                row = [ts, fid] + uv_frames[fi]
                w.writerow(row)

    def write_imu(self, device_id: int, ts: float, frame_id: int,
                  imu_data: list, num_imus: int):
        entry = self.imu_files.get(device_id)
        if not entry:
            return
        _, w = entry
        for ii, chip in enumerate(imu_data):
            has_mag = 1 if len(chip) >= 3 else 0
            acc = chip[0] if len(chip) >= 1 else [0.0, 0.0, 0.0]
            gyr = chip[1] if len(chip) >= 2 else [0.0, 0.0, 0.0]
            w.writerow([ts, frame_id, ii,
                        acc[0], acc[1], acc[2],
                        gyr[0], gyr[1], gyr[2],
                        has_mag])

    def write_error(self, ts: float, device_id: int, level: str,
                    code: str, message: str, packet_len: int, frame_id: int):
        if self._error_writer:
            self._error_writer.writerow(
                [ts, f"dev{device_id}", level, code, message, packet_len, frame_id])

    def write_meta(self, ts: float, device_id: int, packet_len: int,
                   frame_id: int, fpkt: int, num_imus: int, hw_version: str):
        if self._meta_writer:
            self._meta_writer.writerow(
                [ts, f"dev{device_id}", packet_len, frame_id, fpkt, num_imus, hw_version])

    def write_summary(self, dev1: TestDeviceState, dev2: TestDeviceState,
                      errors: List[Dict]):
        """写入 summary.json (严格 PASS/FAIL 判定)"""
        path = os.path.join(self.output_dir, "summary.json")

        def dev_summary(dev):
            return {
                'device_id': dev.device_id,
                'mac': dev.mac,
                'name': dev.name,
                'hw_version': dev.hw_version,
                'firmware_version': dev.firmware_version,
                'hardware_version': dev.hardware_version,
                'num_imus': dev.num_imus,
                'packet_count': dev.packet_count,
                'total_frames': dev.total_frames,
                'lost_frames': dev.lost_frames,
                'imu_row_count': dev.imu_row_count,
                'parse_error_count': dev.parse_error_count,
                'last_frame_id': dev.last_frame_id,
                'mag_detected': dev.mag_detected,
                'mag_detected_count': dev.mag_detected_count,
            }

        # ---- 判断哪些设备参与了测试 ----
        started_devices = []
        devices_with_packets = []
        for dev in [dev1, dev2]:
            if dev.is_connected() or dev.packet_count > 0:
                started_devices.append(dev.device_id)
            if dev.packet_count > 0:
                devices_with_packets.append(dev.device_id)

        # ---- 逐项判定 ----
        fail_reasons = []

        # 0. 至少有一个设备收到数据
        has_data = len(devices_with_packets) > 0
        if not has_data:
            fail_reasons.append("NO_DATA: 没有任何设备收到 BLE 数据包")

        # 逐设备检查
        pass_criteria = {
            'has_data': has_data,
            'devices': {},
        }

        for dev in [dev1, dev2]:
            if dev.device_id not in started_devices:
                continue

            dev_criteria = {
                'packet_count_gt_0': dev.packet_count > 0,
                'hw_version_v2': dev.hw_version == 'V2',
                'num_imus_eq_3': dev.num_imus == 3,
                'imu_row_count_gt_0': dev.imu_row_count > 0,
                'parse_error_count_eq_0': dev.parse_error_count == 0,
                'mag_not_detected': not dev.mag_detected,
            }
            pass_criteria['devices'][f"dev{dev.device_id}"] = dev_criteria

            if not dev_criteria['packet_count_gt_0']:
                fail_reasons.append(
                    f"Dev{dev.device_id}: packet_count=0, 未收到任何有效数据包")
            if not dev_criteria['hw_version_v2']:
                fail_reasons.append(
                    f"Dev{dev.device_id}: hw_version={dev.hw_version}, 期望 V2")
            if not dev_criteria['num_imus_eq_3']:
                fail_reasons.append(
                    f"Dev{dev.device_id}: num_imus={dev.num_imus}, 期望 3")
            if not dev_criteria['imu_row_count_gt_0']:
                fail_reasons.append(
                    f"Dev{dev.device_id}: imu_row_count=0, V2 必须有 IMU 数据")
            if not dev_criteria['parse_error_count_eq_0']:
                fail_reasons.append(
                    f"Dev{dev.device_id}: parse_error_count={dev.parse_error_count}, 存在包解析失败")
            if not dev_criteria['mag_not_detected']:
                fail_reasons.append(
                    f"Dev{dev.device_id}: mag_detected=True, V2 不应有磁力计数据")

        # 汇总 validation errors
        for e in errors:
            fail_reasons.append(
                f"[{e.get('code', '?')}] Dev{e.get('device_id', '?')}: {e.get('message', '')}")

        all_pass = all(
            dev_criteria[k]
            for dev_criteria in pass_criteria['devices'].values()
            for k in dev_criteria
        ) and has_data and len(errors) == 0

        summary = {
            'test_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'result': 'PASS' if all_pass else 'FAIL',
            'output_dir': self.output_dir,
            'started_devices': started_devices,
            'devices_with_packets': devices_with_packets,
            'pass_criteria': pass_criteria,
            'fail_reasons': fail_reasons,
            'dev1': dev_summary(dev1) if 1 in started_devices else None,
            'dev2': dev_summary(dev2) if 2 in started_devices else None,
            'total_errors': len(errors),
        }

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

    def close(self):
        for f, _ in self.emg_files.values():
            f.close()
        for f, _ in self.imu_files.values():
            f.close()
        if self.error_file:
            self.error_file.close()
        if self.meta_file:
            self.meta_file.close()


# ==================== BLE 引擎 (后台 asyncio 线程) ====================

class BleEngine:
    """在后台线程中管理 BLE asyncio 事件循环"""

    def __init__(self, log_queue: queue.Queue, data_queue: queue.Queue):
        self.log_queue = log_queue
        self.data_queue = data_queue
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

        # 设备状态
        self.dev1 = TestDeviceState(device_id=1)
        self.dev2 = TestDeviceState(device_id=2)
        self.scan_results: List[Dict] = []

        # 校验器
        self.validator = V2Validator()

        # 控制标志
        self._stop = False

    def log(self, msg: str):
        self.log_queue.put(('log', msg))

    def start(self):
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def stop(self):
        self._stop = True
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)

    def run_coro(self, coro):
        """从任意线程调度协程到 BLE 事件循环"""
        if self.loop and self.loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, self.loop)
        return None

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        try:
            self.loop.run_forever()
        except Exception:
            pass
        finally:
            # 清理 pending tasks
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            self.loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()

    # ===== BLE 操作 =====

    async def scan(self):
        self.log("开始扫描 BLE 设备...")
        try:
            devices = await BleakScanner.discover(timeout=5.0)
            self.scan_results.clear()
            for d in devices:
                if d.name and TARGET_DEVICE_NAME in d.name:
                    self.scan_results.append({
                        'name': d.name,
                        'mac': d.address,
                        'rssi': d.rssi if hasattr(d, 'rssi') else 0,
                    })
            self.scan_results.sort(key=lambda x: x.get('rssi', -100), reverse=True)
            self.log(f"扫描完成: 找到 {len(self.scan_results)} 个 {TARGET_DEVICE_NAME} 设备")
            self.log_queue.put(('scan_done', list(self.scan_results)))
        except Exception as e:
            self.log(f"扫描失败: {e}")
            self.log_queue.put(('error', f"扫描失败: {e}"))

    async def connect(self, device_id: int, mac: str):
        dev = self.dev1 if device_id == 1 else self.dev2
        action = f"connect{device_id}"

        self.log(f"[Dev{device_id}] 正在连接: {mac}")

        for retry in range(MAX_RETRIES):
            try:
                client = BleakClient(mac, timeout=10.0)
                await client.connect()
                if not client.is_connected:
                    if retry < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_DELAY)
                        continue
                    self.log(f"[Dev{device_id}] 连接失败: 无法建立连接")
                    self.log_queue.put(('connect_result', {'device_id': device_id, 'success': False, 'error': '无法建立连接'}))
                    return

                dev.client = client
                dev.mac = mac
                dev.name = mac  # BleakClient 不直接提供 name，用 MAC 代替
                dev.reset_stats()

                self.log(f"[Dev{device_id}] 已连接，检测硬件版本...")

                # V1/V2 检测: 尝试订阅 STATUS_CHAR
                try:
                    await dev.client.start_notify(
                        STATUS_CHAR_UUID,
                        self._make_status_handler(dev))
                    dev.hw_version = "V2"
                    dev.channel_map = CHANNELS_MAP_V2
                    dev.num_imus = 0
                    self.log(f"[Dev{device_id}] 检测到 V2 设备")
                except Exception as e:
                    dev.hw_version = "V1"
                    dev.channel_map = CHANNELS_MAP_V1
                    dev.num_imus = MAX_NUM_IMUS_V1
                    self.log(f"[Dev{device_id}] V1 设备 (STATUS_CHAR 不可用: {e})")

                # 发送配置命令
                try:
                    sample_rate_cmd = bytes([CMD_MAP['2kHz']])
                    await self._send_control_cmd(dev, sample_rate_cmd)
                    self.log(f"[Dev{device_id}] 已发送采样率: 2kHz")

                    await asyncio.sleep(0.1)

                    cfg = dev.config
                    config_cmd = bytes([
                        CMD_MAP['CONFIG'],
                        cfg['gain_index'],
                        0 if not cfg['is_16bit'] else 1,
                        cfg['shift'],
                        1 if cfg['imu_enabled'] else 0,
                    ])
                    await self._send_control_cmd(dev, config_cmd)
                    self.log(f"[Dev{device_id}] 已发送配置: Gain={cfg['gain_index']}, 24bit, IMU=ON")
                except Exception as e:
                    self.log(f"[Dev{device_id}] 配置发送失败: {e}")

                self.log(f"[Dev{device_id}] 连接成功 (hw_version={dev.hw_version})")
                self.log_queue.put(('connect_result', {
                    'device_id': device_id,
                    'success': True,
                    'mac': mac,
                    'hw_version': dev.hw_version,
                }))
                return

            except TimeoutError:
                self.log(f"[Dev{device_id}] 连接超时 (重试 {retry+1}/{MAX_RETRIES})")
            except BleakError as e:
                self.log(f"[Dev{device_id}] BLE 错误: {e}")
            except Exception as e:
                self.log(f"[Dev{device_id}] 连接异常: {e}")

            if retry < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAY)

        self.log_queue.put(('connect_result', {
            'device_id': device_id,
            'success': False,
            'error': f"连接失败 (已重试 {MAX_RETRIES} 次)",
        }))

    async def disconnect(self, device_id: int):
        dev = self.dev1 if device_id == 1 else self.dev2
        mac = dev.mac

        try:
            if dev.is_streaming:
                await self._stop_stream(dev)

            if dev.client:
                try:
                    await dev.client.disconnect()
                except Exception:
                    pass
                dev.client = None

            dev.mac = None
            dev.name = None
            dev.hw_version = "V1"
            dev.channel_map = CHANNELS_MAP_V1
            dev.num_imus = MAX_NUM_IMUS_V1
            dev.firmware_version = ""
            dev.hardware_version = ""

            self.log(f"[Dev{device_id}] 已断开: {mac}")
            self.log_queue.put(('disconnect_result', {'device_id': device_id, 'success': True}))
        except Exception as e:
            self.log(f"[Dev{device_id}] 断开失败: {e}")
            self.log_queue.put(('disconnect_result', {'device_id': device_id, 'success': False, 'error': str(e)}))

    async def start_stream(self, device_id: int):
        dev = self.dev1 if device_id == 1 else self.dev2

        if not dev.is_connected():
            self.log(f"[Dev{device_id}] 无法启动: 未连接")
            return
        if dev.is_streaming:
            self.log(f"[Dev{device_id}] 已在采集中")
            return

        dev.reset_stats()

        # 发送 SD 卡文件名
        now_str = datetime.now().strftime("%y%m%d_%H%M%S")
        hand_label = "L" if device_id == 1 else "R"
        filename_str = f"TEST_{hand_label}_{now_str}"
        if len(filename_str) > 31:
            filename_str = filename_str[:31]

        try:
            filename_cmd = bytes([CMD_MAP['SET_FILENAME']]) + filename_str.encode('ascii')
            await self._send_control_cmd(dev, filename_cmd)
            await asyncio.sleep(0.1)
        except Exception as e:
            self.log(f"[Dev{device_id}] SD文件名发送失败: {e}")

        # 订阅 EMG 数据通知
        try:
            await dev.client.start_notify(EMG_DATA_CHAR_UUID,
                                          self._make_data_handler(dev))
            self.log(f"[Dev{device_id}] 已订阅 EMG 数据通知")
        except Exception as e:
            self.log(f"[Dev{device_id}] 订阅数据通知失败: {e}")
            return

        # V2 设备 notify 稳定延迟 (对齐 ble_server.py START_NOTIFY_SETTLE_DELAY_S)
        if dev.hw_version == "V2":
            await asyncio.sleep(0.25)

        # 发送 START
        try:
            await self._send_control_cmd(dev, bytes([CMD_MAP['START']]))
            dev.is_streaming = True
            self.log(f"[Dev{device_id}] 开始采集 (V2, 期望 3 IMU)")
            self.log_queue.put(('stream_result', {'device_id': device_id, 'started': True}))
        except Exception as e:
            self.log(f"[Dev{device_id}] START 发送失败: {e}")

    async def stop_stream(self, device_id: int):
        dev = self.dev1 if device_id == 1 else self.dev2
        await self._stop_stream(dev)
        self.log_queue.put(('stream_result', {'device_id': device_id, 'started': False}))

    async def _stop_stream(self, dev: TestDeviceState):
        if not dev.is_streaming:
            return
        try:
            if dev.client and dev.is_connected():
                await self._send_control_cmd(dev, bytes([CMD_MAP['STOP']]))
                await dev.client.stop_notify(EMG_DATA_CHAR_UUID)
                self.log(f"[Dev{dev.device_id}] 已停止采集")
        except Exception as e:
            self.log(f"[Dev{dev.device_id}] 停止采集失败: {e}")
        dev.is_streaming = False

    async def shutdown(self):
        """安全关闭所有连接"""
        for dev in [self.dev1, self.dev2]:
            if dev.is_connected():
                try:
                    if dev.is_streaming:
                        await self._stop_stream(dev)
                    if dev.client:
                        await dev.client.disconnect()
                except Exception:
                    pass

    # ===== 内部辅助 =====

    async def _send_control_cmd(self, dev: TestDeviceState, payload: bytes,
                                 timeout: float = 2.0):
        if dev.hw_version == "V2":
            await asyncio.wait_for(
                dev.client.write_gatt_char(CONTROL_CHAR_UUID, payload, response=True),
                timeout=timeout)
        else:
            await dev.client.write_gatt_char(CONTROL_CHAR_UUID, payload, response=False)

    def _make_status_handler(self, dev: TestDeviceState):
        """STATUS_CHAR 通知回调 — 解析设备快照"""
        def handler(sender: int, data: bytearray):
            try:
                if not data or len(data) < 1:
                    return
                packet_type = data[0]
                expected_size = struct.calcsize(STATUS_SNAPSHOT_FORMAT)
                if packet_type == STATUS_PACKET_SNAPSHOT and len(data) >= expected_size:
                    s = struct.unpack(STATUS_SNAPSHOT_FORMAT, data[:expected_size])
                    fw_num_imus = s[6]
                    if 0 <= fw_num_imus <= MAX_NUM_IMUS_V2:
                        dev.num_imus = fw_num_imus
                    dev.firmware_version = s[17].split(b'\x00')[0].decode('ascii', errors='ignore')
                    dev.hardware_version = s[18].split(b'\x00')[0].decode('ascii', errors='ignore')
                    self.log(f"[Dev{dev.device_id}] STATUS: num_imus={fw_num_imus}, "
                             f"fw={dev.firmware_version}, hw={dev.hardware_version}")
            except Exception as e:
                self.log(f"[Dev{dev.device_id}] STATUS 解析错误: {e}")
        return handler

    def _make_data_handler(self, dev: TestDeviceState):
        """EMG_DATA_CHAR 通知回调 — 解析 BLE 数据包并推送校验结果"""
        def handler(sender: int, data: bytearray):
            try:
                ts = time.time()
                raw_len = len(data)
                dev.last_packet_len = raw_len

                parsed = parse_packet(data, dev)
                if parsed is None:
                    dev.parse_error_count += 1
                    self.data_queue.put(('parse_error', {
                        'ts': ts, 'device_id': dev.device_id,
                        'packet_len': raw_len,
                    }))
                    return

                dev.packet_count += 1
                dev.last_frame_id = parsed.get('f', 0)
                dev.last_num_imus = parsed.get('num_imus', 0)

                # IMU 统计
                imu_data = parsed.get('imu')
                if imu_data:
                    dev.imu_row_count += len(imu_data)

                # V2 协议校验
                errors = self.validator.check_packet(parsed, dev, raw_len)
                for e in errors:
                    self.data_queue.put(('validation_error', {
                        'ts': ts,
                        'device_id': dev.device_id,
                        'level': 'ERROR',
                        'code': e['code'],
                        'message': e['message'],
                        'packet_len': raw_len,
                        'frame_id': parsed.get('f', 0),
                    }))

                # 推送数据到主线程
                self.data_queue.put(('packet', {
                    'ts': ts,
                    'device_id': dev.device_id,
                    'parsed': parsed,
                    'raw_len': raw_len,
                }))

            except Exception as e:
                dev.parse_error_count += 1
                self.log(f"[Dev{dev.device_id}] 数据回调错误: {e}")

        return handler


# ==================== tkinter GUI ====================

class TestApp:
    """V2 测试工具主界面"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("BLE V2 腕带测试工具")
        self.root.geometry("900x700")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 队列
        self.log_queue = queue.Queue()
        self.data_queue = queue.Queue()

        # BLE 引擎
        self.engine = BleEngine(self.log_queue, self.data_queue)

        # 数据写入器
        self.writer: Optional[DataWriter] = None

        # 错误收集
        self._all_errors: List[Dict] = []
        self._stop_futures: List = []

        # 构建 UI
        self._build_ui()

        # 启动 BLE 引擎
        self.engine.start()

        # 启动定时轮询
        self._poll_queues()

    # ===== UI 构建 =====

    def _build_ui(self):
        # 顶部控制按钮
        ctrl_frame = ttk.Frame(self.root, padding=5)
        ctrl_frame.pack(fill=tk.X)

        self.btn_scan = ttk.Button(ctrl_frame, text="扫描设备", command=self._on_scan)
        self.btn_scan.pack(side=tk.LEFT, padx=2)

        self.btn_connect1 = ttk.Button(ctrl_frame, text="连接设备1", command=lambda: self._on_connect(1))
        self.btn_connect1.pack(side=tk.LEFT, padx=2)

        self.btn_connect2 = ttk.Button(ctrl_frame, text="连接设备2", command=lambda: self._on_connect(2))
        self.btn_connect2.pack(side=tk.LEFT, padx=2)

        self.btn_start = ttk.Button(ctrl_frame, text="开始采集", command=self._on_start_all)
        self.btn_start.pack(side=tk.LEFT, padx=2)
        self.btn_start.configure(state='disabled')

        self.btn_stop = ttk.Button(ctrl_frame, text="停止采集", command=self._on_stop_all)
        self.btn_stop.pack(side=tk.LEFT, padx=2)
        self.btn_stop.configure(state='disabled')

        self.btn_disconnect = ttk.Button(ctrl_frame, text="断开全部",
                                          command=self._on_disconnect_all)
        self.btn_disconnect.pack(side=tk.LEFT, padx=2)

        # 设备列表
        list_frame = ttk.LabelFrame(self.root, text="扫描到的设备", padding=5)
        list_frame.pack(fill=tk.X, padx=5, pady=2)

        self.device_list = ttk.Treeview(list_frame, columns=('name', 'mac', 'rssi'),
                                        show='headings', height=4)
        self.device_list.heading('name', text='设备名称')
        self.device_list.heading('mac', text='MAC 地址')
        self.device_list.heading('rssi', text='RSSI')
        self.device_list.column('name', width=300)
        self.device_list.column('mac', width=200)
        self.device_list.column('rssi', width=80)
        self.device_list.pack(fill=tk.X)
        self.device_list.bind('<Double-1>', self._on_list_dblclick)

        # 实时统计
        stats_frame = ttk.LabelFrame(self.root, text="实时统计", padding=5)
        stats_frame.pack(fill=tk.X, padx=5, pady=2)

        self.stats_vars: Dict[str, tk.StringVar] = {}
        self._build_stats_row(stats_frame, 0, "Dev1",
                              ['connected', 'hw_version', 'firmware_version',
                               'packet_count', 'total_frames', 'imu_row_count',
                               'parse_errors', 'last_packet_len', 'last_frame_id',
                               'last_num_imus', 'mag_flag'])
        self._build_stats_row(stats_frame, 3, "Dev2",
                              ['connected', 'hw_version', 'firmware_version',
                               'packet_count', 'total_frames', 'imu_row_count',
                               'parse_errors', 'last_packet_len', 'last_frame_id',
                               'last_num_imus', 'mag_flag'])

        # 日志区域
        log_frame = ttk.LabelFrame(self.root, text="日志", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, wrap=tk.WORD,
                                                   font=('Consolas', 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 状态栏
        self.status_var = tk.StringVar(value="就绪")
        status_bar = ttk.Label(self.root, textvariable=self.status_var,
                               relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _build_stats_row(self, parent, row: int, label: str, fields: List[str]):
        ttk.Label(parent, text=label, font=('', 9, 'bold')).grid(
            row=row, column=0, sticky=tk.W, padx=5)

        col = 1
        for field in fields:
            ttk.Label(parent, text=field, font=('', 8)).grid(
                row=row, column=col, sticky=tk.W, padx=1)
            var = tk.StringVar(value='-')
            ttk.Label(parent, textvariable=var, font=('', 8, 'bold'),
                      foreground='#333').grid(
                row=row+1, column=col, sticky=tk.W, padx=1)
            key = f"{label}_{field}"
            self.stats_vars[key] = var
            col += 1

        # 空行分隔
        ttk.Label(parent, text='').grid(row=row+2, column=0)

    def _update_stats(self, dev_label: str, dev: TestDeviceState):
        d = self.stats_vars
        prefix = dev_label
        d[f'{prefix}_connected'].set('是' if dev.is_connected() else '否')
        d[f'{prefix}_hw_version'].set(dev.hw_version)
        d[f'{prefix}_firmware_version'].set(dev.firmware_version or '-')
        d[f'{prefix}_packet_count'].set(str(dev.packet_count))
        d[f'{prefix}_total_frames'].set(str(dev.total_frames))
        d[f'{prefix}_imu_row_count'].set(str(dev.imu_row_count))
        d[f'{prefix}_parse_errors'].set(str(dev.parse_error_count))
        d[f'{prefix}_last_packet_len'].set(str(dev.last_packet_len))
        d[f'{prefix}_last_frame_id'].set(str(dev.last_frame_id))
        d[f'{prefix}_last_num_imus'].set(str(dev.last_num_imus))
        d[f'{prefix}_mag_flag'].set('异常!' if dev.mag_detected else 'OK')

    # ===== 队列轮询 =====

    def _poll_queues(self):
        """定时从线程安全队列拉取数据到 UI 线程"""
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._handle_log_msg(msg)
        except queue.Empty:
            pass

        try:
            while True:
                msg = self.data_queue.get_nowait()
                self._handle_data_msg(msg)
        except queue.Empty:
            pass

        # 更新统计
        self._update_stats("Dev1", self.engine.dev1)
        self._update_stats("Dev2", self.engine.dev2)

        self.root.after(100, self._poll_queues)

    def _handle_log_msg(self, msg):
        msg_type, content = msg[0], msg[1]
        if msg_type == 'log':
            self._append_log(content)
        elif msg_type == 'scan_done':
            self._update_device_list(content)
            self.status_var.set(f"扫描完成: {len(content)} 个设备")
        elif msg_type == 'connect_result':
            self._append_log(f"Dev{content['device_id']}: {'连接成功' if content['success'] else '失败 - ' + content.get('error', '')}")
            if content['success']:
                self.status_var.set(f"Dev{content['device_id']} 已连接 ({content.get('hw_version', '?')})")
                self.btn_start.configure(state='normal')
        elif msg_type == 'disconnect_result':
            self._append_log(f"Dev{content['device_id']}: 已断开")
            if not self.engine.dev1.is_connected() and not self.engine.dev2.is_connected():
                self.btn_start.configure(state='disabled')
                self.btn_stop.configure(state='disabled')
        elif msg_type == 'stream_result':
            if content.get('started'):
                self._append_log(f"Dev{content['device_id']}: 采集已开始")
                self.btn_stop.configure(state='normal')
            else:
                self._append_log(f"Dev{content['device_id']}: 采集已停止")
        elif msg_type == 'error':
            self._append_log(f"ERROR: {content}")

    def _handle_data_msg(self, msg):
        msg_type, content = msg[0], msg[1]
        if msg_type == 'packet':
            self._write_packet_data(content)
        elif msg_type == 'validation_error':
            self._all_errors.append(content)
            self._write_error(content)
            self._append_log(f"[校验错误] Dev{content['device_id']}: [{content['code']}] {content['message']}")
        elif msg_type == 'parse_error':
            dev = self.engine.dev1 if content['device_id'] == 1 else self.engine.dev2
            err = {
                'ts': content['ts'],
                'device_id': content['device_id'],
                'level': 'ERROR',
                'code': 'PARSE_PACKET_FAILED',
                'message': f"Dev{content['device_id']} 包解析失败, packet_len={content['packet_len']}",
                'packet_len': content['packet_len'],
                'frame_id': dev.last_frame_id,
            }
            self._all_errors.append(err)
            self._write_error(err)
            self._append_log(f"[解析错误] Dev{content['device_id']}: packet_len={content['packet_len']}")

    def _write_packet_data(self, content: dict):
        if self.writer is None:
            return
        ts = content['ts']
        device_id = content['device_id']
        parsed = content['parsed']
        raw_len = content['raw_len']

        # EMG CSV
        uv_frames = parsed.get('uv', [])
        frame_ids = parsed.get('frame_ids', [])
        self.writer.write_emg(device_id, ts, frame_ids, uv_frames)

        # IMU CSV
        imu = parsed.get('imu')
        if imu:
            num_imus = parsed.get('num_imus', len(imu))
            self.writer.write_imu(device_id, ts, parsed.get('f', 0), imu, num_imus)

        # Meta CSV
        self.writer.write_meta(ts, device_id, raw_len,
                               parsed.get('f', 0), parsed.get('n', 0),
                               parsed.get('num_imus', 0), parsed.get('hw_version', '?'))

    def _write_error(self, error: dict):
        if self.writer is None:
            return
        self.writer.write_error(
            error.get('ts', time.time()),
            error.get('device_id', 0),
            error.get('level', 'ERROR'),
            error.get('code', '?'),
            error.get('message', ''),
            error.get('packet_len', 0),
            error.get('frame_id', 0))

    # ===== 按钮事件 =====

    def _on_scan(self):
        self._append_log(">>> 扫描...")
        self.status_var.set("扫描中...")
        self.engine.run_coro(self.engine.scan())

    def _on_connect(self, device_id: int):
        # 获取列表选中项
        sel = self.device_list.selection()
        if not sel:
            self._append_log(f"请先在设备列表中双击选择要连接的设备")
            return
        values = self.device_list.item(sel[0], 'values')
        mac = values[1]
        self._append_log(f">>> 连接 Dev{device_id}: {mac}")
        self.status_var.set(f"连接 Dev{device_id}...")
        self.engine.run_coro(self.engine.connect(device_id, mac))

    def _on_start_all(self):
        self._append_log(">>> 开始采集...")
        self.status_var.set("采集中...")

        # 创建输出目录
        ts_dir = datetime.now().strftime("v2_test_%Y%m%d_%H%M%S")
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "test_output", ts_dir)
        self.writer = DataWriter(output_dir)
        self.writer.open()
        self._all_errors.clear()
        self._append_log(f"输出目录: {output_dir}")

        self.btn_stop.configure(state='normal')
        for did in [1, 2]:
            dev = self.engine.dev1 if did == 1 else self.engine.dev2
            if dev.is_connected() and not dev.is_streaming:
                self.engine.run_coro(self.engine.start_stream(did))

    def _on_stop_all(self):
        self._append_log(">>> 停止采集...")
        self.status_var.set("停止中...")
        self._stop_futures = []
        for did in [1, 2]:
            dev = self.engine.dev1 if did == 1 else self.engine.dev2
            if dev.is_streaming:
                fut = self.engine.run_coro(self.engine.stop_stream(did))
                if fut:
                    self._stop_futures.append(fut)

        # 轮询等待 stop futures 完成后再写 summary
        self._poll_stop_futures()

    def _poll_stop_futures(self):
        """等待所有停止 future 完成，然后 flush summary"""
        pending = [f for f in self._stop_futures if not f.done()]
        if pending:
            self.root.after(200, self._poll_stop_futures)
            return
        # 额外短延迟让最后的数据包和错误从队列中排空
        self._stop_futures = []
        self.root.after(300, self._flush_and_summary)

    def _on_disconnect_all(self):
        self._append_log(">>> 断开全部...")
        for dev in [self.engine.dev1, self.engine.dev2]:
            if dev.is_connected():
                self.engine.run_coro(self.engine.disconnect(dev.device_id))

    def _on_list_dblclick(self, event):
        """双击设备列表填充选中 MAC 到连接"""
        sel = self.device_list.selection()
        if sel:
            values = self.device_list.item(sel[0], 'values')
            self._append_log(f"选中: {values[0]} ({values[1]}) RSSI={values[2]}")

    # ===== 工具方法 =====

    def _update_device_list(self, devices: List[Dict]):
        self.device_list.delete(*self.device_list.get_children())
        for d in devices:
            self.device_list.insert('', 'end', values=(d['name'], d['mac'], d.get('rssi', '?')))

    def _append_log(self, msg: str):
        ts = datetime.now().strftime('%H:%M:%S')
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_text.see(tk.END)

    def _flush_and_summary(self):
        if self.writer is None:
            return
        try:
            self.writer.write_summary(self.engine.dev1, self.engine.dev2,
                                       self._all_errors)
            self.writer.close()
            # 从 summary.json 读取实际结果
            result = 'FAIL'
            spath = os.path.join(self.writer.output_dir, "summary.json")
            try:
                with open(spath, 'r', encoding='utf-8') as f:
                    s = json.load(f)
                    result = s.get('result', 'FAIL')
            except Exception:
                pass
            self._append_log(f"=== 测试结果: {result} (共 {len(self._all_errors)} 个错误) ===")
            self._append_log(f"输出目录: {self.writer.output_dir}")
            self.status_var.set(f"测试完成: {result}")
        except Exception as e:
            self._append_log(f"写入 summary 失败: {e}")
        self.writer = None
        self.btn_stop.configure(state='disabled')

    def _on_close(self):
        self._append_log("关闭中，正在断开 BLE...")
        if self.writer:
            self._flush_and_summary()
        self.engine.run_coro(self.engine.shutdown())
        self.root.after(1500, self._final_close)

    def _final_close(self):
        self.engine.stop()
        self.root.destroy()


# ==================== 入口 ====================

def main():
    root = tk.Tk()
    app = TestApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
