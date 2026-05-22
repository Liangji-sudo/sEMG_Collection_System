"""
bin_sync_tool.py - EMG/IMU数据同步工具
======================================

功能：
  将h5文件中的250Hz EMG数据与SD卡bin文件同步，补全为2kHz完整数据。
  同时利用EMG 2kHz的SD卡帧号作为锚点，从IMU bin中补全100Hz IMU数据。

使用方法：
  python bin_sync_tool.py

原理：
  1. 读取h5文件中的250Hz数据和BLE帧号
  2. 根据帧号映射关系（SD帧号 = BLE帧号 * 8 + 7）定位bin文件中的数据
  3. 从bin文件中提取完整的2kHz数据
  4. 可选：比对250Hz数据与bin中对应帧，校验一致性
  5. 将2kHz数据写入h5文件
  6. 利用EMG 2kHz的SD帧号映射IMU帧号（IMU帧号 = EMG帧号 // 20）
  7. 从IMU bin中提取100Hz数据写入h5文件
  8. 更新sync_status为"synced"

bin文件格式（来自ESP32固件）：
  EMG: 文件头126字节 + 每帧52字节（4字节帧号 + 48字节数据）
  IMU V1: 文件头126字节 + 每帧40字节（4字节帧号 + 36字节数据，2 IMU）
  IMU V2: 文件头126字节 + 每帧(4+N*18)字节（N=1/2/3个IMU），可选36字节Footer
         V2 每个IMU仅Acc+Gyro（6轴），无Mag；Acc/Gyro小端，Accel量程±32g

采样率关系：
  EMG: SD卡2000Hz, BLE 250Hz (降采样比8:1)
  IMU: SD卡100Hz, BLE ~28Hz (每9帧EMG附带1个IMU)
  EMG与IMU的SD帧号比: 2000/100 = 20:1
"""

import os
import sys
import struct
import argparse
import numpy as np
import h5py
from datetime import datetime

# ===================== 常量定义 =====================

# bin文件Magic Word
EMG_MAGIC = 0xAABBCCDD
IMU_MAGIC = 0xBBCCDDEE

# V2 Footer Magic
FOOTER_MAGIC_EMG = 0xDDCCBBAA
FOOTER_MAGIC_IMU = 0xEEDDCCBB

# 文件头大小
HEADER_SIZE = 126

# Footer大小 (V4.1+)
FOOTER_SIZE = 36

# EMG帧大小：4字节帧号 + 16通道 * 3字节 = 52字节
EMG_FRAME_SIZE = 4 + 16 * 3

# IMU帧大小（V1固定）：4字节帧号 + 36字节数据 = 40字节
IMU_FRAME_SIZE = 4 + 36

# IMU V2 参数
BYTES_PER_IMU = 18       # 单个IMU芯片数据字节数
AXES_PER_IMU = 6         # 每IMU轴数（Acc3 + Gyro3，无Mag）
MAX_NUM_IMUS = 3         # V2最大IMU数量

# 降采样比例（2kHz -> 250Hz）
DOWNSAMPLE_RATIO = 8

# EMG与IMU的SD帧号比（EMG 2000Hz / IMU 100Hz = 20）
EMG_IMU_RATIO = 20

# 增益映射表
GAIN_MAP = [1, 2, 3, 4, 6, 8, 12]

# LSB基准值
BASE_LSB_24BIT = 0.476837
HARDWARE_FRONTEND_GAIN = 10  # 供应商固件使用10

# IMU转换系数
SCALE_ACCEL = 16.0 / 32768.0      # V1: ±16g
SCALE_ACCEL_V2 = 32.0 / 32768.0   # V2: ±32g
SCALE_GYRO = 2000.0 / 32768.0     # ±2000dps (V1与V2相同)
SCALE_MAG = 0.15                  # V1 only

# V1/V2 通用 IMU 100Hz dtype（与 storage_server.py IMU_ALL_BLE_DTYPE 对齐，sd_frame_id 替代 frame_id）
IMU_ALL_100HZ_DTYPE = np.dtype([
    ("imu_index", "<u1"),    # IMU 索引 (0-based)
    ("acc", "<f4", (3,)),    # 加速度计 [ax, ay, az]
    ("gyr", "<f4", (3,)),   # 陀螺仪 [gx, gy, gz]
    ("has_mag", "<u1"),      # 是否有磁力计 (V1=1, V2=0)
    ("mag", "<f4", (3,)),   # 磁力计 [mx, my, mz] (V2 填充 NaN)
    ("sd_frame_id", "<u4"), # IMU SD卡帧号
    ("time", "<f8")         # 时间戳
])


def log(message):
    """打印日志"""
    print(f"[bin_sync_tool] {message}")


# ===================== Footer 检测 =====================

def _detect_footer(file_handle, file_size, footer_magic):
    """
    检测 bin 文件末尾是否存在 Footer (V4.1+)。

    Returns:
        (has_footer: bool, footer_info: dict | None)
        footer_info 包含: total_frames, sd_drop, imu_drop, ble_drop, stop_reason
    """
    if file_size < HEADER_SIZE + FOOTER_SIZE:
        return False, None
    saved_pos = file_handle.tell()
    try:
        file_handle.seek(file_size - FOOTER_SIZE)
        magic = struct.unpack('<I', file_handle.read(4))[0]
        if magic != footer_magic:
            return False, None
        ft_total, ft_sd, ft_imu, ft_ble = struct.unpack('<4I', file_handle.read(16))
        ft_reason = struct.unpack('B', file_handle.read(1))[0]
        reason_map = {0: '无', 1: '运行中', 2: '用户停止', 3: 'BLE断连', 4: '远程关机'}
        return True, {
            'total_frames': ft_total, 'sd_drop': ft_sd,
            'imu_drop': ft_imu, 'ble_drop': ft_ble,
            'stop_reason': reason_map.get(ft_reason, f'未知({ft_reason})')
        }
    finally:
        file_handle.seek(saved_pos)


# ===================== bin文件解析 =====================

class EMGBinParser:
    """EMG bin文件解析器

    注意：解析后的数据是原始ADC值（int），不是μV值。
    如需转换为μV，请使用: uv_value = raw_value * lsb_uv
    """

    def __init__(self, bin_path):
        self.bin_path = bin_path
        self.sample_rate = 0
        self.gain = 12
        self.bit_depth = 24
        self.lsb_uv = 0  # LSB系数，用于转换为μV
        self.timestamp_str = ""
        self.frames = {}  # {frame_id: channels_data} - 存储原始ADC值
        self.frame_count = 0

    def parse(self):
        """解析bin文件，返回原始ADC值（非μV）"""
        file_size = os.path.getsize(self.bin_path)
        if file_size < HEADER_SIZE:
            raise ValueError(f"文件太小: {file_size} bytes")

        with open(self.bin_path, 'rb') as f:
            # 读取文件头
            header = f.read(HEADER_SIZE)
            magic, sample_rate, gain_idx, bit_depth, imu_en, ts_bytes = struct.unpack(
                '<I H B B B 32s', header[:41]
            )

            if magic != EMG_MAGIC:
                raise ValueError(f"无效的EMG文件Magic: 0x{magic:08X}")

            self.sample_rate = sample_rate
            self.gain = GAIN_MAP[gain_idx] if gain_idx < len(GAIN_MAP) else 12
            self.bit_depth = bit_depth
            self.timestamp_str = ts_bytes.decode('utf-8').strip('\x00')

            # 计算LSB（保存供后续转换使用，但解析时不应用）
            self.lsb_uv = BASE_LSB_24BIT / (self.gain * HARDWARE_FRONTEND_GAIN)
            if bit_depth == 16:
                self.lsb_uv *= (2 ** 4)

            bytes_per_sample = 3 if bit_depth == 24 else 2
            frame_payload = 16 * bytes_per_sample
            frame_size = 4 + frame_payload

            # 检测 V4.1+ Footer，确定数据区结束位置
            has_footer, footer_info = _detect_footer(f, file_size, FOOTER_MAGIC_EMG)
            if has_footer:
                self._has_footer = True
                self._footer_info = footer_info
                log(f"检测到 EMG Footer: 固件帧数={footer_info['total_frames']}, "
                    f"SD丢包={footer_info['sd_drop']}, IMU丢包={footer_info['imu_drop']}, "
                    f"BLE丢包={footer_info['ble_drop']}, 停止原因={footer_info['stop_reason']}")
                data_end = file_size - FOOTER_SIZE
            else:
                self._has_footer = False
                self._footer_info = None
                data_end = file_size

            log(f"EMG文件信息: 采样率={sample_rate}Hz, 增益={self.gain}, 位深={bit_depth}bit")
            log(f"时间戳: {self.timestamp_str}")
            log(f"LSB系数: {self.lsb_uv:.6f} μV/LSB (用于转换)")

            # 读取所有帧（不超过 data_end）
            while f.tell() + frame_size <= data_end:
                chunk = f.read(frame_size)
                if len(chunk) < frame_size:
                    break

                frame_id = struct.unpack('<I', chunk[0:4])[0]
                raw_data = chunk[4:]

                # 解析16通道数据 - 保持原始ADC值，不转换为μV
                channels = []
                for i in range(16):
                    start = i * bytes_per_sample
                    val = int.from_bytes(raw_data[start:start + bytes_per_sample], 'big', signed=True)
                    channels.append(val)  # 原始ADC值

                self.frames[frame_id] = channels
                self.frame_count += 1

        log(f"解析完成: 共 {self.frame_count} 帧, 帧号范围 [{min(self.frames.keys())}, {max(self.frames.keys())}]")
        return self

    def get_frame(self, frame_id):
        """获取指定帧号的数据"""
        return self.frames.get(frame_id)

    def get_frames_range(self, start_id, end_id):
        """获取帧号范围内的所有数据"""
        result = []
        for fid in range(start_id, end_id + 1):
            if fid in self.frames:
                result.append((fid, self.frames[fid]))
            else:
                result.append((fid, None))  # 丢失的帧
        return result


class IMUBinParser:
    """IMU bin文件解析器 — 自动检测 V1/V2 并适配解析

    统一接口：
      - parser.parse() 后 .frames 为 {frame_id: [imu_dict, ...]}，列表长度 = num_imus
      - 每个 imu_dict 含 acc/gyr/mag/has_mag/index 键
      - .parser_version: 1 或 2
      - .num_imus: 实际 IMU 数量
      - .has_mag: V1=True, V2=False
    """

    def __init__(self, bin_path, h5_file=None, device_id=None):
        self.bin_path = bin_path
        self.h5_file = h5_file
        self.device_id = device_id
        self.sample_rate = 0
        self.timestamp_str = ""
        self.frames = {}       # {frame_id: [imu_dict, ...]}  统一为 list
        self.frame_count = 0
        self.parser_version = 1
        self.num_imus = 2
        self.has_mag = True
        self._detected_footer = False

    # ------------------------------------------------------------------
    # 版本检测
    # ------------------------------------------------------------------

    def _detect_version(self, file_size):
        """
        保守检测策略：先收集所有候选帧长，再按优先级决策。

        V1 40字节帧是历史基线。40的倍数可能恰好也被 58/22 整除
        （例如 40×29=1160 可被 58 整除），所以不能按顺序抢先匹配。
        策略：先收集，后决策。
        """
        # --- 1. 从 H5 属性读取元数据 ---
        h5_num_imus = None
        h5_hw_version = None
        h5_parser_ver = None
        if self.h5_file and self.device_id:
            try:
                with h5py.File(self.h5_file, 'r') as hf:
                    attr_name = f'imu{self.device_id}_parser_version'
                    v = hf.attrs.get(attr_name, None)
                    if isinstance(v, (int, np.integer)):
                        h5_parser_ver = int(v)

                    attr_name = f'imu{self.device_id}_num_imus'
                    v = hf.attrs.get(attr_name, None)
                    if v is not None:
                        h5_num_imus = int(v) if not isinstance(v, bytes) else int(v.decode('utf-8'))

                    attr_name = f'imu{self.device_id}_hw_version'
                    v = hf.attrs.get(attr_name, None)
                    if v is not None:
                        h5_hw_version = str(v) if isinstance(v, bytes) else str(v)
            except Exception:
                pass

        # H5 显式 parser_version=1 → 直接 V1（最高优先级）
        if h5_parser_ver == 1:
            log("  IMU版本检测: H5 显式 parser_version=1, 按 V1 解析")
            return 1, 2

        # --- 2. 检测 V2 Footer，读取 total_frames 用于交叉校验 ---
        data_size = file_size - HEADER_SIZE

        has_v2_footer = False
        footer_total_frames = None
        effective_size = data_size
        if data_size >= FOOTER_SIZE:
            try:
                with open(self.bin_path, 'rb') as tmp_f:
                    tmp_f.seek(file_size - FOOTER_SIZE)
                    fb = tmp_f.read(20)  # magic(4) + 4*uint32(16)
                    footer_magic = struct.unpack('<I', fb[0:4])[0]
                if footer_magic == FOOTER_MAGIC_IMU:
                    has_v2_footer = True
                    self._detected_footer = True
                    effective_size = data_size - FOOTER_SIZE
                    ft_total, ft_sd, ft_imu, ft_ble = struct.unpack('<4I', fb[4:20])
                    footer_total_frames = ft_total
                    log(f"  检测到 V2 IMU Footer (0xEEDDCCBB), 固件帧数={ft_total}")
            except Exception:
                pass

        # --- 3. 收集所有候选帧长 ---
        v1_candidate   = effective_size > 0 and effective_size % 40 == 0
        v2_1_candidate = effective_size > 0 and effective_size % 22 == 0
        v2_2_candidate = effective_size > 0 and effective_size % 40 == 0
        v2_3_candidate = effective_size > 0 and effective_size % 58 == 0

        FRAME_SIZES = {1: 22, 2: 40, 3: 58}
        CANDIDATES  = {1: v2_1_candidate, 2: v2_2_candidate, 3: v2_3_candidate}

        def _pick_v2_num_imus(prefer_list=(3, 2, 1), footer_total=None):
            """
            从 V2 候选中选择 num_imus。
            若 footer_total 已知，用它做交叉校验：
            effective_size // frame_size 必须 == footer_total 才接受该候选。
            """
            for n in prefer_list:
                if not CANDIDATES[n]:
                    continue
                if footer_total is not None:
                    inferred = effective_size // FRAME_SIZES[n]
                    if inferred != footer_total:
                        continue  # footer 帧数不匹配，跳过
                return n
            return None

        # --- 4. H5 明确 V2（parser_version=2 或 hw_version 含 V2） ---
        h5_is_v2 = (h5_parser_ver == 2) or \
                   (h5_hw_version and 'V2' in h5_hw_version.upper())

        if h5_is_v2:
            source = "parser_version=2" if h5_parser_ver == 2 else f"hw_version={h5_hw_version}"
            if h5_num_imus and 1 <= h5_num_imus <= MAX_NUM_IMUS:
                log(f"  IMU版本检测: H5 明确 V2 ({source}), num_imus={h5_num_imus}")
                return 2, h5_num_imus
            # 有 footer 时用交叉校验推断
            if footer_total_frames is not None:
                n = _pick_v2_num_imus(footer_total=footer_total_frames)
                if n is not None:
                    log(f"  IMU版本检测: H5 明确 V2 ({source}), footer校验推断 num_imus={n} "
                        f"(帧长={FRAME_SIZES[n]}字节)")
                    return 2, n
            # 无 footer / footer 不匹配: 统计唯一 V2 候选，多候选报错
            v2_cands = [n for n in (1, 2, 3) if CANDIDATES[n]]
            if len(v2_cands) == 1:
                n = v2_cands[0]
                log(f"  IMU版本检测: H5 明确 V2 ({source}), 唯一候选 num_imus={n} "
                    f"(帧长={FRAME_SIZES[n]}字节)")
                return 2, n
            if len(v2_cands) > 1:
                raise ValueError(
                    f"无法确定 imu{self.device_id}_num_imus: H5 已标记为 V2 ({source}), "
                    f"但存在多个候选 num_imus={v2_cands} 且无 Footer 可交叉校验。"
                    f"请在 H5 属性中设置 imu{self.device_id}_num_imus, "
                    f"或使用带 Footer 的 V2 bin 文件。"
                )
            # 无候选
            raise ValueError(
                f"H5 已标记为 V2 ({source}), 但数据区 {effective_size} 字节"
                f"无法被任何 V2 帧长 (22/40/58) 整除。"
            )

        # --- 5. 有 V2 Footer → V2 (用 footer total_frames 交叉校验) ---
        if has_v2_footer:
            n = _pick_v2_num_imus(footer_total=footer_total_frames)
            if n is not None:
                log(f"  IMU版本检测: V2 Footer + 帧长={FRAME_SIZES[n]}字节 "
                    f"(footer校验通过, total_frames={footer_total_frames}) "
                    f"→ V2, {n} IMU(s)")
                return 2, n
            log(f"  警告: 检测到 V2 Footer (total_frames={footer_total_frames}) "
                f"但帧长无法匹配，回退 V1")
            return 1, 2

        # --- 6. V1 40字节是历史基线：只要 V1 候选成立就默认 V1 ---
        if v1_candidate:
            log("  IMU版本检测: 帧长=40字节，无 V2 元数据，默认按 V1 解析")
            log("    提示: 若实际为 V2 腕带，请在 H5 属性中设置 imu{dev}_parser_version=2")
            return 1, 2

        # --- 7. V1 候选不成立时，只有唯一 V2 候选才自动判 V2 ---
        if v2_3_candidate and not v2_1_candidate:
            log("  IMU版本检测: 帧长=58字节 (唯一) → V2, 3 IMU")
            return 2, 3
        if v2_1_candidate and not v2_3_candidate:
            log("  IMU版本检测: 帧长=22字节 (唯一) → V2, 1 IMU")
            return 2, 1

        # --- 8. 兜底 ---
        log("  IMU版本检测: 无法唯一确定格式，回退 V1 尝试解析")
        return 1, 2

    # ------------------------------------------------------------------
    # 统一解析入口
    # ------------------------------------------------------------------

    def parse(self):
        """解析 IMU bin 文件，自动适配 V1/V2"""
        file_size = os.path.getsize(self.bin_path)
        if file_size < HEADER_SIZE:
            raise ValueError(f"文件太小: {file_size} bytes")

        self.parser_version, self.num_imus = self._detect_version(file_size)

        if self.parser_version == 2:
            return self._parse_v2()
        else:
            return self._parse_v1()

    # ------------------------------------------------------------------
    # V1 解析（保留原逻辑，输出格式统一为 list）
    # ------------------------------------------------------------------

    def _parse_v1(self):
        """V1 IMU bin: 固定 2 IMU, 40 字节帧, Acc/Gyro 大端, Mag 小端, ±16g"""
        self.has_mag = True
        self.num_imus = 2
        file_size = os.path.getsize(self.bin_path)

        with open(self.bin_path, 'rb') as f:
            header = f.read(HEADER_SIZE)
            magic, sample_rate, _, _, _, ts_bytes = struct.unpack(
                '<I H B B B 32s', header[:41]
            )
            if magic != IMU_MAGIC:
                raise ValueError(f"无效的IMU文件Magic: 0x{magic:08X}")

            self.sample_rate = sample_rate if 0 < sample_rate <= 1000 else 100
            self.timestamp_str = ts_bytes.decode('utf-8').strip('\x00')

            # Footer 检测
            has_footer, footer_info = _detect_footer(f, file_size, FOOTER_MAGIC_IMU)
            if has_footer:
                self._detected_footer = True
            data_end = file_size - FOOTER_SIZE if has_footer else file_size

            log(f"IMU文件信息(V1): 采样率={self.sample_rate}Hz, 固定 2 IMU, "
                f"Acc/Gyro大端, 含Mag, Accel±16g")
            log(f"时间戳: {self.timestamp_str}")
            if has_footer:
                log(f"  Footer: 固件帧数={footer_info['total_frames']}, "
                    f"SD丢包={footer_info['sd_drop']}, 停止原因={footer_info['stop_reason']}")

            def parse_chip_v1(b):
                ag = struct.unpack('>6h', b[0:12])
                m = struct.unpack('<3h', b[12:18])
                return {
                    'acc': [x * SCALE_ACCEL for x in ag[0:3]],
                    'gyr': [x * SCALE_GYRO for x in ag[3:6]],
                    'mag': [x * SCALE_MAG for x in m[0:3]],
                    'has_mag': 1,
                    'index': -1  # 由外部赋值
                }

            while f.tell() + IMU_FRAME_SIZE <= data_end:
                chunk = f.read(IMU_FRAME_SIZE)
                if len(chunk) < IMU_FRAME_SIZE:
                    break
                frame_id = struct.unpack('<I', chunk[0:4])[0]
                raw_data = chunk[4:]
                imu1 = parse_chip_v1(raw_data[0:18])
                imu2 = parse_chip_v1(raw_data[18:36])
                imu1['index'] = 0
                imu2['index'] = 1
                self.frames[frame_id] = [imu1, imu2]
                self.frame_count += 1

        log(f"V1 解析完成: 共 {self.frame_count} 帧, "
            f"帧号范围 [{min(self.frames.keys())}, {max(self.frames.keys())}]")
        return self

    # ------------------------------------------------------------------
    # V2 解析（新增）
    # ------------------------------------------------------------------

    def _parse_v2(self):
        """V2 IMU bin: 可变 1-3 IMU, 帧长=4+N*18, Acc/Gyro 小端, 无Mag, ±32g"""
        self.has_mag = False
        file_size = os.path.getsize(self.bin_path)

        with open(self.bin_path, 'rb') as f:
            header = f.read(HEADER_SIZE)
            magic, sample_rate, _, _, _, ts_bytes = struct.unpack(
                '<I H B B B 32s', header[:41]
            )
            if magic != IMU_MAGIC:
                raise ValueError(f"无效的IMU文件Magic: 0x{magic:08X}")

            self.sample_rate = sample_rate if 0 < sample_rate <= 1000 else 100
            self.timestamp_str = ts_bytes.decode('utf-8').strip('\x00')

            # Footer 检测
            has_footer, footer_info = _detect_footer(f, file_size, FOOTER_MAGIC_IMU)
            if has_footer:
                self._detected_footer = True
            data_end = file_size - FOOTER_SIZE if has_footer else file_size

            frame_size = 4 + self.num_imus * BYTES_PER_IMU

            log(f"IMU文件信息(V2): 采样率={self.sample_rate}Hz, {self.num_imus} IMU(s), "
                f"帧长={frame_size}字节, Acc/Gyro小端, 无Mag, Accel±32g")
            log(f"时间戳: {self.timestamp_str}")
            if has_footer:
                log(f"  Footer: 固件帧数={footer_info['total_frames']}, "
                    f"SD丢包={footer_info['sd_drop']}, 停止原因={footer_info['stop_reason']}")

            def parse_chip_v2(b, imu_idx):
                ag = struct.unpack('<6h', b[0:12])
                return {
                    'acc': [x * SCALE_ACCEL_V2 for x in ag[0:3]],
                    'gyr': [x * SCALE_GYRO for x in ag[3:6]],
                    'mag': [np.nan, np.nan, np.nan],
                    'has_mag': 0,
                    'index': imu_idx
                }

            while f.tell() + frame_size <= data_end:
                chunk = f.read(frame_size)
                if len(chunk) < frame_size:
                    break
                frame_id = struct.unpack('<I', chunk[0:4])[0]
                raw_data = chunk[4:]
                imus = []
                for i in range(self.num_imus):
                    offset = i * BYTES_PER_IMU
                    imus.append(parse_chip_v2(raw_data[offset:offset + BYTES_PER_IMU], i))
                self.frames[frame_id] = imus
                self.frame_count += 1

        log(f"V2 解析完成: 共 {self.frame_count} 帧, "
            f"帧号范围 [{min(self.frames.keys())}, {max(self.frames.keys())}]")
        return self


# ===================== h5文件同步 =====================

def sync_h5_with_bin(h5_path, emg_bin_path, imu_bin_path=None, device_id=1, verify=True, set_synced=True):
    """
    将h5文件与bin文件同步

    Args:
        h5_path: h5文件路径
        emg_bin_path: EMG bin文件路径
        imu_bin_path: IMU bin文件路径（可选）
        device_id: 设备ID（1或2）
        verify: 是否进行数据校验
        set_synced: 是否在同步完成后设置sync_status为synced（默认True）
                    当需要同步多个设备时，应在最后一个设备同步时才设为True

    Returns:
        dict: 同步结果统计
    """
    log(f"开始同步: {os.path.basename(h5_path)}")
    log(f"EMG bin: {os.path.basename(emg_bin_path)}")
    if imu_bin_path:
        log(f"IMU bin: {os.path.basename(imu_bin_path)}")

    # 解析bin文件
    emg_parser = EMGBinParser(emg_bin_path).parse()
    imu_parser = IMUBinParser(imu_bin_path, h5_file=h5_path, device_id=device_id).parse() if imu_bin_path else None

    # 打开h5文件
    with h5py.File(h5_path, 'r+') as f:
        # 检查sync_status
        current_status = f.attrs.get('sync_status', 'unknown')
        if current_status == 'synced':
            log("警告: 文件已同步，跳过")
            return {'status': 'skipped', 'reason': 'already_synced'}

        # 获取250Hz ADC数据集
        ds_250hz_name = f"emg{device_id}_250hz_adc"
        if ds_250hz_name not in f:
            log(f"错误: 找不到数据集 {ds_250hz_name}")
            return {'status': 'error', 'reason': f'dataset {ds_250hz_name} not found'}

        ds_250hz = f[ds_250hz_name]
        num_frames_250hz = ds_250hz.shape[0]

        if num_frames_250hz == 0:
            log("警告: 250Hz数据集为空")
            return {'status': 'error', 'reason': 'empty_250hz_dataset'}

        log(f"250Hz数据集: {num_frames_250hz} 帧")

        # 读取250Hz数据和帧号
        data_250hz = ds_250hz[:]
        frame_ids = data_250hz['frame_id']
        channels_250hz = data_250hz['channels']
        timestamps_250hz = data_250hz['time']

        log(f"BLE帧号范围: [{frame_ids[0]}, {frame_ids[-1]}]")

        # 计算对应的SD卡帧号范围
        # 映射关系: SD帧号 = BLE帧号 * 8 + 7 (BLE发送的是每8帧中的最后一帧)
        sd_frame_start = int(frame_ids[0]) * DOWNSAMPLE_RATIO
        sd_frame_end = int(frame_ids[-1]) * DOWNSAMPLE_RATIO + (DOWNSAMPLE_RATIO - 1)

        log(f"对应SD卡帧号范围: [{sd_frame_start}, {sd_frame_end}]")

        # 校验（可选）- 比较h5中的250Hz数据与bin中对应帧的数值
        if verify:
            log("正在校验数据一致性...")
            found_count = 0
            missing_count = 0
            match_count = 0
            mismatch_count = 0

            # 检查h5中的SD帧号是否都能在bin中找到，并比较数值
            sample_count = min(100, len(frame_ids))
            for i in range(sample_count):
                ble_frame_id = frame_ids[i]
                sd_frame_id = int(ble_frame_id) * DOWNSAMPLE_RATIO + (DOWNSAMPLE_RATIO - 1)
                bin_data = emg_parser.get_frame(sd_frame_id)

                if bin_data is not None:
                    found_count += 1
                    # 比较数值（都是原始ADC值）
                    h5_channels = channels_250hz[i]
                    # 检查第一个通道的值是否接近（允许小误差）
                    if abs(h5_channels[0] - bin_data[0]) < 1:
                        match_count += 1
                    else:
                        mismatch_count += 1
                        if mismatch_count <= 3:
                            log(f"  数值不匹配 @帧{sd_frame_id}: h5={h5_channels[0]:.0f}, bin={bin_data[0]}")
                else:
                    missing_count += 1

            if found_count == 0:
                log(f"错误: bin文件中找不到任何对应的帧号！")
                log(f"  h5 SD帧号范围: [{sd_frame_start}, {sd_frame_end}]")
                log(f"  bin帧号范围: [{min(emg_parser.frames.keys())}, {max(emg_parser.frames.keys())}]")
                return {'status': 'error', 'reason': 'no_matching_frames',
                        'h5_range': [sd_frame_start, sd_frame_end],
                        'bin_range': [min(emg_parser.frames.keys()), max(emg_parser.frames.keys())]}

            coverage = found_count / (found_count + missing_count) * 100
            log(f"帧号校验: {found_count}/{found_count + missing_count} 帧在bin中找到 ({coverage:.1f}%)")

            if match_count > 0:
                match_rate = match_count / found_count * 100
                log(f"数值校验: {match_count}/{found_count} 帧数值匹配 ({match_rate:.1f}%)")

            if coverage < 50:
                log(f"警告: 帧号覆盖率过低，可能选错了bin文件")
                return {'status': 'error', 'reason': 'low_coverage',
                        'coverage': coverage}

        # 构建2kHz数据
        log("正在构建2kHz数据...")

        num_frames_2khz = num_frames_250hz * DOWNSAMPLE_RATIO
        # 2kHz数据集类型：使用int32存储原始ADC值（与250Hz一致）
        emg_2khz_dtype = np.dtype([
            ("channels", "<i4", (16,)),  # 原始ADC值（int32）
            ("sd_frame_id", "<u4"),      # SD卡帧号
            ("time", "<f8")
        ])

        data_2khz = np.empty(num_frames_2khz, dtype=emg_2khz_dtype)

        missing_frames = 0
        filled_frames = 0

        for i, ble_frame_id in enumerate(frame_ids):
            # 计算这个BLE帧对应的8个SD卡帧
            sd_base = int(ble_frame_id) * DOWNSAMPLE_RATIO

            for j in range(DOWNSAMPLE_RATIO):
                sd_frame_id = sd_base + j
                idx_2khz = i * DOWNSAMPLE_RATIO + j

                bin_data = emg_parser.get_frame(sd_frame_id)

                if bin_data is not None:
                    data_2khz[idx_2khz]['channels'] = np.array(bin_data, dtype=np.int32)
                    data_2khz[idx_2khz]['sd_frame_id'] = sd_frame_id
                    filled_frames += 1
                else:
                    # 帧丢失，使用插值或最近邻填充
                    if j == DOWNSAMPLE_RATIO - 1:
                        # 最后一帧应该和250Hz数据一致
                        data_2khz[idx_2khz]['channels'] = channels_250hz[i].astype(np.int32)
                    elif idx_2khz > 0:
                        # 使用前一帧数据
                        data_2khz[idx_2khz]['channels'] = data_2khz[idx_2khz - 1]['channels']
                    else:
                        data_2khz[idx_2khz]['channels'] = np.zeros(16, dtype=np.int32)
                    data_2khz[idx_2khz]['sd_frame_id'] = sd_frame_id
                    missing_frames += 1

                # 插值时间戳
                if i < len(timestamps_250hz) - 1:
                    t_start = timestamps_250hz[i]
                    t_end = timestamps_250hz[i + 1]
                    data_2khz[idx_2khz]['time'] = t_start + (t_end - t_start) * j / DOWNSAMPLE_RATIO
                else:
                    # 最后一组，使用固定间隔
                    data_2khz[idx_2khz]['time'] = timestamps_250hz[i] + j * (1.0 / 2000.0)

        log(f"2kHz数据构建完成: {filled_frames} 帧来自bin, {missing_frames} 帧插值填充")

        # 写入2kHz ADC数据集（写入已存在的空数据集，而非创建新的）
        ds_2khz_name = f"emg{device_id}_2khz_adc"

        if ds_2khz_name in f:
            ds_2khz = f[ds_2khz_name]
            # 调整大小并写入数据
            ds_2khz.resize(num_frames_2khz, axis=0)
            ds_2khz[:] = data_2khz
            # 更新属性
            ds_2khz.attrs["lsb_uv"] = emg_parser.lsb_uv  # 保存LSB系数，用于转换为μV
            ds_2khz.attrs["source_bin"] = os.path.basename(emg_bin_path)
            ds_2khz.attrs["sync_time"] = datetime.now().isoformat()
            ds_2khz.attrs["filled_frames"] = filled_frames
            ds_2khz.attrs["missing_frames"] = missing_frames
        else:
            log(f"警告: 数据集 {ds_2khz_name} 不存在，创建新数据集")
            ds_2khz = f.create_dataset(
                ds_2khz_name, data=data_2khz,
                chunks=(min(1000, num_frames_2khz),), compression="gzip"
            )
            ds_2khz.attrs["device"] = f"device_{device_id}"
            ds_2khz.attrs["channels"] = 16
            ds_2khz.attrs["sample_rate"] = 2000
            ds_2khz.attrs["data_type"] = "raw_adc"
            ds_2khz.attrs["lsb_uv"] = emg_parser.lsb_uv
            ds_2khz.attrs["description"] = "2kHz EMG raw ADC data synced from SD card bin (not uV, multiply by lsb_uv to convert)"
            ds_2khz.attrs["source_bin"] = os.path.basename(emg_bin_path)
            ds_2khz.attrs["sync_time"] = datetime.now().isoformat()
            ds_2khz.attrs["filled_frames"] = filled_frames
            ds_2khz.attrs["missing_frames"] = missing_frames

        log(f"同步完成！2kHz数据已写入 {ds_2khz_name}")

        # ============================================================
        # == IMU 100Hz 同步：利用 EMG 2kHz SD帧号作为锚点 ==
        # ============================================================
        imu_result = {'imu_status': 'skipped'}

        if imu_parser is not None:
            log("正在同步IMU 100Hz数据...")
            log(f"  IMU 解析器版本: V{imu_parser.parser_version}, "
                f"IMU数量: {imu_parser.num_imus}, "
                f"{'含' if imu_parser.has_mag else '不含'}磁力计")
            if imu_parser._detected_footer:
                log(f"  检测到并跳过了 IMU Footer")

            # 从EMG 2kHz数据中提取所有SD帧号，映射到IMU帧号
            emg_sd_frame_ids = data_2khz['sd_frame_id']
            imu_frame_ids_all = emg_sd_frame_ids // EMG_IMU_RATIO  # EMG帧号/20 = IMU帧号

            # 去重并排序，得到需要的IMU帧号列表
            imu_frame_ids_unique = np.unique(imu_frame_ids_all)
            num_imu_frames = len(imu_frame_ids_unique)

            log(f"EMG SD帧号范围: [{emg_sd_frame_ids[0]}, {emg_sd_frame_ids[-1]}]")
            log(f"对应IMU帧号范围: [{imu_frame_ids_unique[0]}, {imu_frame_ids_unique[-1]}], "
                f"共 {num_imu_frames} 帧")

            # ---- 构建统一 all_100hz 数据集 (行 = 每IMU每时间点) ----
            num_all_rows = num_imu_frames * imu_parser.num_imus
            data_imu_all = np.empty(num_all_rows, dtype=IMU_ALL_100HZ_DTYPE)

            # ---- 构建 legacy a/b 数据集 (V1 向后兼容) ----
            imu_legacy_dtype = np.dtype([
                ("acc", "<f4", (3,)),
                ("gyr", "<f4", (3,)),
                ("mag", "<f4", (3,)),
                ("sd_frame_id", "<u4"),
                ("time", "<f8")
            ])
            data_imu_a = np.empty(num_imu_frames, dtype=imu_legacy_dtype)
            data_imu_b = np.empty(num_imu_frames, dtype=imu_legacy_dtype)

            imu_filled = 0
            imu_missing = 0
            imu_b_filled = 0
            imu_b_missing = 0

            for idx, imu_fid in enumerate(imu_frame_ids_unique):
                imu_fid = int(imu_fid)
                imu_list = imu_parser.frames.get(imu_fid)

                # 计算该 IMU 帧对应的时间戳
                emg_idx = idx * EMG_IMU_RATIO
                if emg_idx < len(data_2khz):
                    imu_time = data_2khz[emg_idx]['time']
                elif len(data_2khz) > 0:
                    imu_time = data_2khz[-1]['time'] + (idx - num_imu_frames + 1) * 0.01
                else:
                    imu_time = idx * 0.01

                if imu_list is not None:
                    # 填充 all_100hz — 每个 IMU 一行
                    for imu_dict in imu_list:
                        row_idx = idx * imu_parser.num_imus + imu_dict['index']
                        data_imu_all[row_idx]['imu_index'] = imu_dict['index']
                        data_imu_all[row_idx]['acc'] = np.array(imu_dict['acc'], dtype=np.float32)
                        data_imu_all[row_idx]['gyr'] = np.array(imu_dict['gyr'], dtype=np.float32)
                        data_imu_all[row_idx]['has_mag'] = imu_dict['has_mag']
                        data_imu_all[row_idx]['mag'] = np.array(imu_dict['mag'], dtype=np.float32)
                        data_imu_all[row_idx]['sd_frame_id'] = imu_fid
                        data_imu_all[row_idx]['time'] = imu_time

                    # 填充 legacy IMU_A (index=0)
                    imu0 = imu_list[0]
                    data_imu_a[idx]['acc'] = np.array(imu0['acc'], dtype=np.float32)
                    data_imu_a[idx]['gyr'] = np.array(imu0['gyr'], dtype=np.float32)
                    data_imu_a[idx]['mag'] = np.array(imu0['mag'], dtype=np.float32)
                    imu_filled += 1

                    # 填充 legacy IMU_B (index=1，若存在)
                    if len(imu_list) > 1:
                        imu1 = imu_list[1]
                        data_imu_b[idx]['acc'] = np.array(imu1['acc'], dtype=np.float32)
                        data_imu_b[idx]['gyr'] = np.array(imu1['gyr'], dtype=np.float32)
                        data_imu_b[idx]['mag'] = np.array(imu1['mag'], dtype=np.float32)
                        imu_b_filled += 1
                    else:
                        data_imu_b[idx]['acc'] = np.zeros(3, dtype=np.float32)
                        data_imu_b[idx]['gyr'] = np.zeros(3, dtype=np.float32)
                        data_imu_b[idx]['mag'] = np.zeros(3, dtype=np.float32)
                        imu_b_missing += 1
                else:
                    imu_missing += 1
                    imu_b_missing += 1
                    # all_100hz 缺失帧填充（mag 按 has_mag 决定填 0 或 NaN）
                    nan3 = np.array([np.nan, np.nan, np.nan], dtype=np.float32)
                    for i_imu in range(imu_parser.num_imus):
                        row_idx = idx * imu_parser.num_imus + i_imu
                        data_imu_all[row_idx]['imu_index'] = i_imu
                        data_imu_all[row_idx]['acc'] = np.zeros(3, dtype=np.float32)
                        data_imu_all[row_idx]['gyr'] = np.zeros(3, dtype=np.float32)
                        data_imu_all[row_idx]['has_mag'] = int(imu_parser.has_mag)
                        data_imu_all[row_idx]['mag'] = (
                            np.zeros(3, dtype=np.float32) if imu_parser.has_mag else nan3
                        )
                        data_imu_all[row_idx]['sd_frame_id'] = imu_fid
                        data_imu_all[row_idx]['time'] = imu_time
                    # legacy a/b 填零
                    data_imu_a[idx]['acc'] = np.zeros(3, dtype=np.float32)
                    data_imu_a[idx]['gyr'] = np.zeros(3, dtype=np.float32)
                    data_imu_a[idx]['mag'] = np.zeros(3, dtype=np.float32)
                    data_imu_b[idx]['acc'] = np.zeros(3, dtype=np.float32)
                    data_imu_b[idx]['gyr'] = np.zeros(3, dtype=np.float32)
                    data_imu_b[idx]['mag'] = np.zeros(3, dtype=np.float32)

                data_imu_a[idx]['sd_frame_id'] = imu_fid
                data_imu_a[idx]['time'] = imu_time
                data_imu_b[idx]['sd_frame_id'] = imu_fid
                data_imu_b[idx]['time'] = imu_time

            log(f"IMU all_100hz 数据构建完成: {num_all_rows} 行 "
                f"(={num_imu_frames} 帧 x {imu_parser.num_imus} IMU)")
            log(f"IMU legacy 数据: A={imu_filled}来自bin/{imu_missing}缺失, "
                f"B={imu_b_filled}来自bin/{imu_b_missing}缺失")

            # ---- 写入统一 all_100hz 数据集 ----
            ds_imu_all_name = f"imu{device_id}_all_100hz"
            if ds_imu_all_name in f:
                ds_imu_all = f[ds_imu_all_name]
                ds_imu_all.resize(num_all_rows, axis=0)
                ds_imu_all[:] = data_imu_all
            else:
                ds_imu_all = f.create_dataset(
                    ds_imu_all_name, data=data_imu_all,
                    chunks=(min(1000, num_all_rows),), compression="gzip"
                )
            ds_imu_all.attrs["sample_rate"] = 100
            ds_imu_all.attrs["source_bin"] = os.path.basename(imu_bin_path)
            ds_imu_all.attrs["sync_time"] = datetime.now().isoformat()
            ds_imu_all.attrs["parser_version"] = imu_parser.parser_version
            ds_imu_all.attrs["num_imus"] = imu_parser.num_imus
            ds_imu_all.attrs["has_mag"] = int(imu_parser.has_mag)
            ds_imu_all.attrs["row_layout"] = "one_row_per_imu_per_timestamp"
            ds_imu_all.attrs["description"] = (
                f"IMU 100Hz synced data, V{imu_parser.parser_version}, "
                f"{imu_parser.num_imus} IMU(s), "
                f"{'with' if imu_parser.has_mag else 'without'} magnetometer"
            )
            log(f"  [OK]已写入 {ds_imu_all_name}: {num_all_rows} 行")

            # ---- 写入 legacy a/b 数据集 (V1 向后兼容) ----
            ds_imu_a_name = f"imu{device_id}a_100hz"
            ds_imu_b_name = f"imu{device_id}b_100hz"
            ds_imu_name_legacy = f"imu{device_id}_100hz"

            if ds_imu_a_name in f:
                ds_imu_a = f[ds_imu_a_name]
                ds_imu_a.resize(num_imu_frames, axis=0)
                ds_imu_a[:] = data_imu_a
                ds_imu_a.attrs["sample_rate"] = 100
                ds_imu_a.attrs["source_bin"] = os.path.basename(imu_bin_path)
                ds_imu_a.attrs["sync_time"] = datetime.now().isoformat()
                ds_imu_a.attrs["filled_frames"] = imu_filled
                ds_imu_a.attrs["missing_frames"] = imu_missing
                ds_imu_a.attrs["parser_version"] = imu_parser.parser_version
                log(f"  [OK]已写入 {ds_imu_a_name}: {imu_filled}来自bin/{imu_missing}缺失")
            elif ds_imu_name_legacy in f:
                ds_imu = f[ds_imu_name_legacy]
                ds_imu.resize(num_imu_frames, axis=0)
                ds_imu[:] = data_imu_a
                ds_imu.attrs["sample_rate"] = 100
                ds_imu.attrs["source_bin"] = os.path.basename(imu_bin_path)
                ds_imu.attrs["sync_time"] = datetime.now().isoformat()
                ds_imu.attrs["filled_frames"] = imu_filled
                ds_imu.attrs["missing_frames"] = imu_missing
                ds_imu.attrs["parser_version"] = imu_parser.parser_version
                log(f"  [OK]已写入旧版 {ds_imu_name_legacy}")
            else:
                log(f"  - {ds_imu_a_name} 不存在，跳过 IMU_A legacy 写入")

            if ds_imu_b_name in f:
                ds_imu_b = f[ds_imu_b_name]
                ds_imu_b.resize(num_imu_frames, axis=0)
                ds_imu_b[:] = data_imu_b
                ds_imu_b.attrs["sample_rate"] = 100
                ds_imu_b.attrs["source_bin"] = os.path.basename(imu_bin_path)
                ds_imu_b.attrs["sync_time"] = datetime.now().isoformat()
                ds_imu_b.attrs["filled_frames"] = imu_b_filled
                ds_imu_b.attrs["missing_frames"] = imu_b_missing
                ds_imu_b.attrs["parser_version"] = imu_parser.parser_version
                log(f"  [OK]已写入 {ds_imu_b_name}: {imu_b_filled}来自bin/{imu_b_missing}缺失")
            else:
                log(f"  - {ds_imu_b_name} 不存在，跳过 IMU_B legacy 写入")

            log(f"IMU同步完成！V{imu_parser.parser_version}, "
                f"{imu_parser.num_imus}IMU, all_100hz={num_all_rows}行, "
                f"A={imu_filled}帧, B={imu_b_filled}帧")
            imu_result = {
                'imu_status': 'success',
                'imu_parser_version': imu_parser.parser_version,
                'imu_num_imus': imu_parser.num_imus,
                'imu_has_mag': imu_parser.has_mag,
                'imu_frames': num_imu_frames,
                'imu_filled': imu_filled,
                'imu_missing': imu_missing,
                'imu_b_filled': imu_b_filled,
                'imu_b_missing': imu_b_missing,
                'imu_all_rows': num_all_rows,
                'imu_all_dataset': ds_imu_all_name
            }

        # 更新sync_status（仅当set_synced=True时）
        if set_synced:
            f.attrs["sync_status"] = "synced"
            f.attrs["sync_time"] = datetime.now().isoformat()
            log(f"同步完成！EMG 2kHz: {ds_2khz_name}, IMU: {imu_result.get('imu_status', 'skipped')}, 状态已设为synced")
        else:
            log(f"同步完成！EMG 2kHz: {ds_2khz_name}, IMU: {imu_result.get('imu_status', 'skipped')}, 状态保持pending（等待其他设备同步）")

        result = {
            'status': 'success',
            'frames_250hz': num_frames_250hz,
            'frames_2khz': num_frames_2khz,
            'filled_frames': filled_frames,
            'missing_frames': missing_frames
        }
        result.update(imu_result)
        return result


# ===================== GUI界面 =====================

def run_gui():
    """运行GUI界面"""
    try:
        from PyQt5.QtWidgets import (
            QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
            QPushButton, QLabel, QFileDialog, QTextEdit, QGroupBox,
            QCheckBox, QComboBox, QMessageBox, QProgressBar, QListWidget,
            QListWidgetItem, QAbstractItemView
        )
        from PyQt5.QtCore import Qt
    except ImportError:
        log("错误: PyQt5未安装，请运行: pip install PyQt5")
        log("或使用命令行模式: python bin_sync_tool.py --h5 <h5文件> --emg-bin <emg.bin>")
        sys.exit(1)

    class SyncToolWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.h5_paths = []  # 支持多个h5文件
            self.emg_bin_path = None
            self.imu_bin_path = None
            self.init_ui()

        def init_ui(self):
            self.setWindowTitle("EMG/IMU数据同步工具 - bin_sync_tool (批量模式)")
            self.setMinimumSize(700, 600)

            central = QWidget()
            self.setCentralWidget(central)
            layout = QVBoxLayout(central)

            # 文件选择区域
            file_group = QGroupBox("文件选择")
            file_layout = QVBoxLayout()

            # H5文件列表（支持多选）
            h5_header = QHBoxLayout()
            h5_header.addWidget(QLabel("H5文件列表:"))
            h5_header.addStretch()
            h5_add_btn = QPushButton("添加H5文件")
            h5_add_btn.clicked.connect(self.add_h5_files)
            h5_clear_btn = QPushButton("清空列表")
            h5_clear_btn.clicked.connect(self.clear_h5_files)
            h5_header.addWidget(h5_add_btn)
            h5_header.addWidget(h5_clear_btn)
            file_layout.addLayout(h5_header)

            self.h5_list = QListWidget()
            self.h5_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.h5_list.setMaximumHeight(150)
            file_layout.addWidget(self.h5_list)

            # EMG bin文件
            emg_row = QHBoxLayout()
            self.emg_label = QLabel("EMG bin: 未选择")
            self.emg_label.setWordWrap(True)
            emg_btn = QPushButton("选择EMG bin")
            emg_btn.clicked.connect(self.select_emg_bin)
            emg_row.addWidget(self.emg_label, 1)
            emg_row.addWidget(emg_btn)
            file_layout.addLayout(emg_row)

            # IMU bin文件（可选）
            imu_row = QHBoxLayout()
            self.imu_label = QLabel("IMU bin: 未选择（可选）")
            self.imu_label.setWordWrap(True)
            imu_btn = QPushButton("选择IMU bin")
            imu_btn.clicked.connect(self.select_imu_bin)
            imu_row.addWidget(self.imu_label, 1)
            imu_row.addWidget(imu_btn)
            file_layout.addLayout(imu_row)

            file_group.setLayout(file_layout)
            layout.addWidget(file_group)

            # 选项区域
            opt_group = QGroupBox("同步选项")
            opt_layout = QHBoxLayout()

            self.verify_cb = QCheckBox("数据校验")
            self.verify_cb.setChecked(True)
            self.verify_cb.setToolTip("比对250Hz数据与bin文件中对应帧，确认数据一致性")
            opt_layout.addWidget(self.verify_cb)

            opt_layout.addWidget(QLabel("设备:"))
            self.device_combo = QComboBox()
            self.device_combo.addItems(["设备1 (emg1)", "设备2 (emg2)"])
            opt_layout.addWidget(self.device_combo)

            opt_layout.addStretch()
            opt_group.setLayout(opt_layout)
            layout.addWidget(opt_group)

            # 进度条
            self.progress_bar = QProgressBar()
            self.progress_bar.setVisible(False)
            layout.addWidget(self.progress_bar)

            # 同步按钮
            self.sync_btn = QPushButton("开始批量同步")
            self.sync_btn.setMinimumHeight(40)
            self.sync_btn.setStyleSheet("font-size: 14px; font-weight: bold;")
            self.sync_btn.clicked.connect(self.do_batch_sync)
            layout.addWidget(self.sync_btn)

            # 日志区域
            log_group = QGroupBox("日志")
            log_layout = QVBoxLayout()
            self.log_text = QTextEdit()
            self.log_text.setReadOnly(True)
            log_layout.addWidget(self.log_text)
            log_group.setLayout(log_layout)
            layout.addWidget(log_group)

        def log(self, msg):
            self.log_text.append(msg)
            # 滚动到底部
            self.log_text.verticalScrollBar().setValue(
                self.log_text.verticalScrollBar().maximum()
            )
            QApplication.processEvents()

        def add_h5_files(self):
            """添加多个H5文件"""
            paths, _ = QFileDialog.getOpenFileNames(
                self, "选择H5文件（可多选）", "", "HDF5 Files (*.h5 *.hdf5);;All Files (*)"
            )
            if paths:
                for path in paths:
                    if path not in self.h5_paths:
                        self.h5_paths.append(path)
                        # 获取文件信息
                        try:
                            with h5py.File(path, 'r') as f:
                                sync_status = f.attrs.get('sync_status', 'unknown')
                                emg1_frames = f['emg1_250hz'].shape[0] if 'emg1_250hz' in f else 0
                                emg2_frames = f['emg2_250hz'].shape[0] if 'emg2_250hz' in f else 0
                            info = f"[{sync_status}] EMG1:{emg1_frames} EMG2:{emg2_frames}"
                        except:
                            info = "[读取失败]"

                        item = QListWidgetItem(f"{os.path.basename(path)} {info}")
                        item.setData(Qt.UserRole, path)
                        if 'synced' in info:
                            item.setForeground(Qt.gray)
                        self.h5_list.addItem(item)

                self.log(f"已添加 {len(paths)} 个H5文件，当前共 {len(self.h5_paths)} 个")

        def clear_h5_files(self):
            """清空H5文件列表"""
            self.h5_paths.clear()
            self.h5_list.clear()
            self.log("已清空H5文件列表")

        def select_emg_bin(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "选择EMG bin文件", "", "Binary Files (*emg*.bin *.bin);;All Files (*)"
            )
            if path:
                self.emg_bin_path = path
                self.emg_label.setText(f"EMG bin: {os.path.basename(path)}")
                self.log(f"已选择EMG bin: {path}")

                # 显示bin文件信息
                try:
                    parser = EMGBinParser(path)
                    parser.parse()
                    self.log(f"  帧数: {parser.frame_count}, 采样率: {parser.sample_rate}Hz")
                except Exception as e:
                    self.log(f"  解析bin文件失败: {e}")

        def select_imu_bin(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "选择IMU bin文件", "", "Binary Files (*imu*.bin *.bin);;All Files (*)"
            )
            if path:
                self.imu_bin_path = path
                self.imu_label.setText(f"IMU bin: {os.path.basename(path)}")
                self.log(f"已选择IMU bin: {path}")

        def do_batch_sync(self):
            """批量同步所有H5文件"""
            if not self.h5_paths:
                QMessageBox.warning(self, "错误", "请先添加H5文件")
                return
            if not self.emg_bin_path:
                QMessageBox.warning(self, "错误", "请先选择EMG bin文件")
                return

            device_id = self.device_combo.currentIndex() + 1
            verify = self.verify_cb.isChecked()

            # 过滤出pending状态的文件
            pending_files = []
            for path in self.h5_paths:
                try:
                    with h5py.File(path, 'r') as f:
                        status = f.attrs.get('sync_status', 'unknown')
                        if status != 'synced':
                            pending_files.append(path)
                except:
                    pending_files.append(path)

            if not pending_files:
                QMessageBox.information(self, "提示", "所有文件都已同步，无需处理")
                return

            self.log("=" * 60)
            self.log(f"开始批量同步 {len(pending_files)} 个文件...")
            self.sync_btn.setEnabled(False)
            self.progress_bar.setVisible(True)
            self.progress_bar.setMaximum(len(pending_files))
            self.progress_bar.setValue(0)

            success_count = 0
            fail_count = 0

            # 解析bin文件（只解析一次）
            self.log("正在解析bin文件...")
            try:
                emg_parser = EMGBinParser(self.emg_bin_path).parse()
            except Exception as e:
                self.log(f"解析EMG bin失败: {e}")
                QMessageBox.critical(self, "错误", f"解析EMG bin失败: {e}")
                self.sync_btn.setEnabled(True)
                self.progress_bar.setVisible(False)
                return

            for i, h5_path in enumerate(pending_files):
                self.log("-" * 40)
                self.log(f"[{i+1}/{len(pending_files)}] {os.path.basename(h5_path)}")

                try:
                    result = sync_h5_with_bin(
                        h5_path,
                        self.emg_bin_path,
                        self.imu_bin_path,
                        device_id=device_id,
                        verify=verify
                    )

                    if result['status'] == 'success':
                        self.log(f"  ✅ EMG: {result['frames_2khz']}帧 (来自bin:{result['filled_frames']}, 插值:{result['missing_frames']})")
                        if result.get('imu_status') == 'success':
                            self.log(f"  ✅ IMU: {result['imu_frames']}帧 (来自bin:{result['imu_filled']}, 缺失:{result['imu_missing']})")
                        elif result.get('imu_status') == 'skipped':
                            self.log(f"  ⏭ IMU: 未提供IMU bin文件，跳过")
                        success_count += 1
                    else:
                        self.log(f"  ❌ 失败: {result.get('reason', 'unknown')}")
                        fail_count += 1

                except Exception as e:
                    self.log(f"  ❌ 出错: {e}")
                    fail_count += 1

                self.progress_bar.setValue(i + 1)

            self.log("=" * 60)
            self.log(f"批量同步完成！成功: {success_count}, 失败: {fail_count}")
            self.sync_btn.setEnabled(True)
            self.progress_bar.setVisible(False)

            # 刷新列表显示
            self.h5_list.clear()
            for path in self.h5_paths:
                try:
                    with h5py.File(path, 'r') as f:
                        sync_status = f.attrs.get('sync_status', 'unknown')
                        emg1_frames = f['emg1_250hz'].shape[0] if 'emg1_250hz' in f else 0
                    info = f"[{sync_status}] EMG1:{emg1_frames}"
                except:
                    info = "[读取失败]"
                item = QListWidgetItem(f"{os.path.basename(path)} {info}")
                item.setData(Qt.UserRole, path)
                if 'synced' in info:
                    item.setForeground(Qt.gray)
                self.h5_list.addItem(item)

            QMessageBox.information(
                self, "完成",
                f"批量同步完成！\n成功: {success_count} 个\n失败: {fail_count} 个"
            )

    app = QApplication(sys.argv)
    window = SyncToolWindow()
    window.show()
    sys.exit(app.exec_())


# ===================== 命令行接口 =====================

def main():
    parser = argparse.ArgumentParser(
        description="EMG数据同步工具 - 将h5文件中的250Hz数据与SD卡bin文件同步为2kHz"
    )
    parser.add_argument("--h5", help="H5文件路径")
    parser.add_argument("--emg-bin", help="EMG bin文件路径")
    parser.add_argument("--imu-bin", help="IMU bin文件路径（可选）")
    parser.add_argument("--device", type=int, default=1, choices=[1, 2], help="设备ID (1或2)")
    parser.add_argument("--no-verify", action="store_true", help="跳过数据校验")
    parser.add_argument("--gui", action="store_true", help="启动GUI界面")

    args = parser.parse_args()

    # 如果没有参数或指定--gui，启动GUI
    if args.gui or (not args.h5 and not args.emg_bin):
        run_gui()
        return

    # 命令行模式
    if not args.h5:
        log("错误: 请指定H5文件 (--h5)")
        sys.exit(1)
    if not args.emg_bin:
        log("错误: 请指定EMG bin文件 (--emg-bin)")
        sys.exit(1)

    result = sync_h5_with_bin(
        args.h5,
        args.emg_bin,
        args.imu_bin,
        device_id=args.device,
        verify=not args.no_verify
    )

    if result['status'] == 'success':
        log("同步成功！")
        sys.exit(0)
    else:
        log(f"同步失败: {result.get('reason', 'unknown')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
