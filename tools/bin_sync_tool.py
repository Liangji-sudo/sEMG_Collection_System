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
  IMU: 文件头126字节 + 每帧40字节（4字节帧号 + 36字节数据）

采样率关系：
  EMG: SD卡2000Hz, BLE 250Hz (降采样比8:1)
  IMU: SD卡100Hz, BLE ~28Hz (每9帧EMG附带1个IMU)
  EMG与IMU的SD帧号比: 2000/100 = 20:1
"""

import os
import sys
import json
import shutil
import struct
import argparse
import numpy as np
import h5py
from datetime import datetime

# ===================== 常量定义 =====================

# bin文件Magic Word
EMG_MAGIC = 0xAABBCCDD
IMU_MAGIC = 0xBBCCDDEE

# 文件头大小
HEADER_SIZE = 126

# EMG帧大小：4字节帧号 + 16通道 * 3字节 = 52字节
EMG_FRAME_SIZE = 4 + 16 * 3

# IMU单芯片数据大小：18 字节 (acc 6 + gyro 6 + reserved 6)
BYTES_PER_IMU_CHIP = 18
# IMU帧大小：4字节帧号 + num_imus * 18字节（动态，默认 2 IMU = 40 字节）
IMU_FRAME_SIZE = 4 + 36  # 保留旧常量兼容 2 IMU，新代码用 IMUBinParser.num_imus 动态计算

# 降采样比例（2kHz -> 250Hz）
DOWNSAMPLE_RATIO = 8

# EMG与IMU的SD帧号比（EMG 2000Hz / IMU 100Hz = 20）
EMG_IMU_RATIO = 20

# ===================== 通道映射常量 =====================
# 与 ble_server.py 保持一致。1-indexed: 使用时要 i-1 转到 0-indexed。
# physical 顺序 (SD/bin 和 BLE 原始包): chip1[0..7] + chip2[0..7]
# mapped 顺序 (H5 存储): 按 channel_map 重排后的逻辑显示顺序
CHANNELS_MAP_V1 = [14, 15, 16, 3, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
CHANNELS_MAP_V2 = [15, 16, 14, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
CHANNEL_MAPS_BY_NAME = {
    'V1': CHANNELS_MAP_V1,
    'V2': CHANNELS_MAP_V2,
    'physical': None,  # None 表示恒等映射（不重排）
}


def map_physical_to_h5_order(row, channel_map):
    """将物理顺序的 16 通道数据转换为 H5 存储的 mapped 顺序。

    Args:
        row: 物理顺序的 16 通道数据 (list/tuple/array)
        channel_map: 1-indexed 通道映射表 (如 CHANNELS_MAP_V2)，None 表示不映射

    Returns:
        mapped 顺序的 list
    """
    if channel_map is None:
        return list(row)
    return [row[i - 1] for i in channel_map]


def _resolve_channel_map(h5_file, dataset_250hz_name, channel_map_name='V2'):
    """解析应使用的通道映射。

    优先级: H5 dataset attrs > H5 file attrs > channel_map_name 参数

    Args:
        h5_file: h5py File 对象
        dataset_250hz_name: 250Hz 数据集名称 (含路径)
        channel_map_name: 默认映射名称 ('V1'/'V2'/'physical')

    Returns:
        tuple: (channel_map_list_or_None, resolved_name_str)
    """
    resolved_name = channel_map_name
    found_in_dataset = False

    # 1) 尝试从数据集 attrs 读取（最高优先级）
    ds = h5_file.get(dataset_250hz_name)
    if ds is not None:
        ds_map = ds.attrs.get('channel_map', None) or ds.attrs.get('channel_map_name', None)
        if ds_map is not None:
            if isinstance(ds_map, bytes):
                ds_map = ds_map.decode('utf-8')
            resolved_name = str(ds_map)
            found_in_dataset = True

    # 2) 尝试从文件 attrs 读取（仅当数据集 attrs 未命中时）
    if not found_in_dataset:
        file_map = h5_file.attrs.get('channel_map', None) or h5_file.attrs.get('channel_map_name', None)
        if file_map is not None:
            if isinstance(file_map, bytes):
                file_map = file_map.decode('utf-8')
            resolved_name = str(file_map)

    # 3) 规范化名称并取映射表
    resolved_name = resolved_name.strip() if resolved_name else 'V2'
    # 大小写不敏感匹配
    name_lower = resolved_name.lower()
    if name_lower in ('physical', 'none', 'identity'):
        return None, 'physical'
    if name_lower in ('v1',):
        return CHANNELS_MAP_V1, 'V1'
    if name_lower in ('v2',):
        return CHANNELS_MAP_V2, 'V2'
    log(f"未知 channel_map 名称 '{resolved_name}'，回退为 V2")
    return CHANNELS_MAP_V2, 'V2'


# 增益映射表
GAIN_MAP = [1, 2, 3, 4, 6, 8, 12]

# LSB基准值
BASE_LSB_24BIT = 0.476837
HARDWARE_FRONTEND_GAIN = 10  # 供应商固件使用10

# IMU转换系数
# V2 wristbands use LSM6DSV32X at +/-32g. Keep V1 documented for legacy data,
# but default the sync path to V2 so SD IMU units match realtime ble_server.py.
SCALE_ACCEL_V2 = 32.0 / 32768.0
SCALE_ACCEL_V1 = 16.0 / 32768.0
SCALE_ACCEL = SCALE_ACCEL_V2
SCALE_GYRO = 70.0 / 1000.0
SCALE_MAG = 0.15

# ===================== 同步校验配置 =====================
# 防御性校验阈值 — 任何校验失败将阻止 sync_status 被设为 "synced"

VALIDATION_CONFIG = {
    'max_duplicate_ratio': 0.0,        # 允许的 frame_id 重复率（0 = 严格不允许重复）
    'min_coverage_ratio': 0.95,        # SD 帧覆盖率最低要求（实际唯一覆盖/理论覆盖）
    # 'max_gap_rate' 已废弃 — 任何 gap 都直接判失败，不再按比例放行
    # 'max_gap_rate': 0.10,
    'adc_sample_count': 200,           # ADC 一致性校验抽样帧数（均匀抽样）
    'adc_match_threshold': 0.95,       # ADC 抽样匹配率最低要求
}


def log(message):
    """打印日志"""
    text = f"[bin_sync_tool] {message}"
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or 'utf-8'
        print(text.encode(encoding, errors='replace').decode(encoding, errors='replace'))


# ===================== 校验辅助函数 =====================

def validate_frame_ids(frame_ids):
    """校验 frame_id 序列的健康状况。

    检测项：
      - monotonic: 是否严格递增
      - duplicates: 重复 frame_id 的数量
      - gaps: 跳变位置（diff > 1）的数量和位置

    Args:
        frame_ids: numpy array of frame_id values

    Returns:
        dict: {
            'total': int, 'unique': int, 'duplicates': int,
            'duplicate_ratio': float, 'gap_count': int,
            'gap_indices': list of (pos, prev_id, curr_id),
            'max_gap': int, 'is_monotonic': bool,
            'is_strictly_increasing': bool,
            'passed': bool, 'reason': str, 'report_lines': list
        }
    """
    total = len(frame_ids)
    unique = len(set(frame_ids))
    duplicates = total - unique
    duplicate_ratio = duplicates / total if total > 0 else 0.0

    report = []
    report.append(f"frame_id 总数: {total}, 唯一值: {unique}, 重复: {duplicates} ({duplicate_ratio:.2%})")

    # 检测单调性
    diffs = np.diff(frame_ids.astype(np.int64))
    non_increasing = np.sum(diffs <= 0)
    gap_mask = diffs > 1
    gap_count = int(np.sum(gap_mask))
    max_gap = int(np.max(diffs)) if len(diffs) > 0 else 0

    is_strictly_increasing = (non_increasing == 0)

    if not is_strictly_increasing:
        report.append(f"[FAIL] frame_id 非严格递增: {non_increasing} 处 diff<=0 (重复/回退)")

    # 收集 gap 位置（前 10 个）
    gap_indices = []
    if gap_count > 0:
        gap_positions = np.where(gap_mask)[0]
        for pos in gap_positions[:10]:
            gap_indices.append({
                'index': int(pos),
                'prev_id': int(frame_ids[pos]),
                'curr_id': int(frame_ids[pos + 1]),
                'diff': int(diffs[pos])
            })
        report.append(f"[FAIL] frame_id 存在 {gap_count} 处 gap (diff>1)，输出 2kHz 数据将缺失对应 SD 帧段")
        report.append(f"       最大 gap: {max_gap}，前 3 个 gap 位置: {gap_indices[:3]}")

    if gap_count == 0 and is_strictly_increasing:
        report.append("[PASS] frame_id 严格递增连续，无重复无 gap")

    # 判定：重复或 gap 任一出现即失败
    passed = True
    reason = ""
    if duplicate_ratio > VALIDATION_CONFIG['max_duplicate_ratio']:
        passed = False
        reason = f"frame_id 重复率 {duplicate_ratio:.2%} > 阈值 {VALIDATION_CONFIG['max_duplicate_ratio']:.0%}"
        report.append(f"[FAIL] {reason}")

    if gap_count > 0:
        passed = False
        gap_reason = f"frame_id gap detected: {gap_count} 处不连续，缺帧将导致 2kHz 输出不完整"
        if reason:
            reason += "; " + gap_reason
        else:
            reason = gap_reason
        report.append(f"[FAIL] {gap_reason}")

    return {
        'total': total, 'unique': unique, 'duplicates': duplicates,
        'duplicate_ratio': duplicate_ratio,
        'non_increasing': int(non_increasing),
        'gap_count': gap_count,
        'gap_indices': gap_indices,
        'max_gap': max_gap,
        'is_strictly_increasing': is_strictly_increasing,
        'passed': passed,
        'reason': reason,
        'report_lines': report
    }


def validate_sd_coverage(frame_ids, downsample_ratio=8):
    """校验 SD 帧覆盖率。

    计算 frame_id 通过 sd_frame_id = frame_id * downsample_ratio + j
    映射后覆盖的唯一 SD 帧数量，与理论覆盖范围对比。

    Args:
        frame_ids: numpy array of frame_id values
        downsample_ratio: 降采样比 (默认 8)

    Returns:
        dict: {
            'unique_sd_count': int, 'expected_sd_count': int,
            'coverage_ratio': float,
            'sd_range_start': int, 'sd_range_end': int,
            'passed': bool, 'reason': str, 'report_lines': list
        }
    """
    total = len(frame_ids)
    if total == 0:
        return {'passed': False, 'reason': 'frame_ids 为空',
                'unique_sd_count': 0, 'expected_sd_count': 0,
                'coverage_ratio': 0.0, 'report_lines': ['frame_ids 为空']}

    # 计算映射到的所有 SD 帧号
    unique_sd_frames = set()
    sd_range_start = int(frame_ids[0]) * downsample_ratio
    sd_range_end = int(frame_ids[-1]) * downsample_ratio + (downsample_ratio - 1)

    for fid in frame_ids:
        sd_base = int(fid) * downsample_ratio
        for j in range(downsample_ratio):
            unique_sd_frames.add(sd_base + j)

    unique_sd_count = len(unique_sd_frames)
    expected_sd_count = total * downsample_ratio
    coverage_ratio = unique_sd_count / expected_sd_count if expected_sd_count > 0 else 0.0

    report = []
    report.append(f"SD 帧映射区间: [{sd_range_start}, {sd_range_end}]"
                  f" (首帧 {int(frame_ids[0])}×{downsample_ratio} ~ 尾帧 {int(frame_ids[-1])}×{downsample_ratio}+{downsample_ratio-1})")
    report.append(f"SD 帧唯一覆盖: {unique_sd_count}, 理论覆盖: {expected_sd_count}, 覆盖率: {coverage_ratio:.2%}")

    passed = coverage_ratio >= VALIDATION_CONFIG['min_coverage_ratio']
    reason = ""
    if not passed:
        reason = (f"SD 覆盖率 {coverage_ratio:.2%} < 阈值 "
                  f"{VALIDATION_CONFIG['min_coverage_ratio']:.0%}，"
                  f"frame_id 存在严重重叠（已知 bug 特征：覆盖率接近 1/{downsample_ratio+1} ≈ {1/(downsample_ratio+1):.0%}）")
        report.append(f"[FAIL] {reason}")
    else:
        report.append(f"[PASS] SD 覆盖率 {coverage_ratio:.2%} >= {VALIDATION_CONFIG['min_coverage_ratio']:.0%}")

    return {
        'unique_sd_count': unique_sd_count,
        'expected_sd_count': expected_sd_count,
        'coverage_ratio': coverage_ratio,
        'sd_range_start': sd_range_start,
        'sd_range_end': sd_range_end,
        'passed': passed,
        'reason': reason,
        'report_lines': report
    }


def run_adc_verification(frame_ids, channels_250hz, emg_parser,
                         sample_count=None, downsample_ratio=8,
                         channel_map=None, channel_map_name='physical'):
    """强 ADC 一致性校验。

    均匀抽样 frame_ids，比对 H5 250Hz 的通道数据与 bin 中对应锚点帧（SD 帧号 = frame_id × 8 + 7）的通道数据。
    不只看第一通道差值，而是对所有 16 通道逐个比对。

    注意：bin 数据为物理通道顺序，H5 数据为 mapped 顺序。比对前会将 bin 数据通过
    channel_map 转为 mapped 顺序，确保同一 channel index 含义一致。

    Args:
        frame_ids: numpy array of frame_id values
        channels_250hz: numpy array of H5 250Hz channel data (shape: [N, 16]) — mapped 顺序
        emg_parser: EMGBinParser 实例（已 parse）
        sample_count: 抽样数量（None 则使用 VALIDATION_CONFIG 默认值）
        downsample_ratio: 降采样比 (默认 8)
        channel_map: 1-indexed 通道映射表或 None (None=恒等映射/physical)
        channel_map_name: 映射名称，仅用于日志

    Returns:
        dict: {
            'checked': int, 'matched': int, 'mismatched': int, 'missing': int,
            'match_rate': float, 'mismatch_rate': float,
            'mismatch_details': list of dict,
            'passed': bool, 'reason': str, 'report_lines': list,
            'channel_map_name': str
        }
    """
    if sample_count is None:
        sample_count = VALIDATION_CONFIG['adc_sample_count']

    total = len(frame_ids)
    if total == 0:
        return {'checked': 0, 'matched': 0, 'mismatched': 0, 'missing': 0,
                'match_rate': 0.0, 'mismatch_rate': 0.0,
                'mismatch_details': [], 'passed': False,
                'reason': 'frame_ids 为空', 'report_lines': ['frame_ids 为空']}

    # 均匀抽样
    actual_sample = min(sample_count, total)
    indices = np.linspace(0, total - 1, actual_sample, dtype=int)

    matched = 0
    mismatched = 0
    missing = 0
    mismatch_details = []

    for idx in indices:
        ble_frame_id = int(frame_ids[idx])
        # 锚点 SD 帧号：该 BLE 帧对应的 8 个原始帧中的最后一帧
        sd_anchor = ble_frame_id * downsample_ratio + (downsample_ratio - 1)
        bin_data = emg_parser.get_frame(sd_anchor)

        if bin_data is None:
            missing += 1
            if missing <= 3:
                mismatch_details.append({
                    'idx': int(idx), 'ble_frame_id': ble_frame_id,
                    'sd_anchor': sd_anchor, 'error': 'bin frame not found'
                })
            continue

        # bin 数据是物理顺序 → 映射到 H5 的 mapped 顺序后再比较
        bin_data_mapped = map_physical_to_h5_order(bin_data, channel_map)

        # 逐通道比较 H5 250Hz 数据与 bin 数据（相同 mapped 顺序）
        h5_channels = channels_250hz[idx]
        # 严格比较：所有 16 通道完全相等（允许 ±1 的舍入差异）
        all_match = all(abs(int(h5_channels[ch]) - int(bin_data_mapped[ch])) <= 1
                        for ch in range(16))

        if all_match:
            matched += 1
        else:
            mismatched += 1
            if mismatched <= 3:
                # 找出不匹配的通道（使用 mapped 顺序的 bin_data_mapped，与主比较逻辑一致）
                bad_chs = [ch for ch in range(16)
                           if abs(int(h5_channels[ch]) - int(bin_data_mapped[ch])) > 1]
                mismatch_details.append({
                    'idx': int(idx), 'ble_frame_id': ble_frame_id,
                    'sd_anchor': sd_anchor,
                    'mismatched_channels': bad_chs[:5],
                    'h5_sample': [int(h5_channels[ch]) for ch in bad_chs[:3]],
                    'bin_sample': [int(bin_data_mapped[ch]) for ch in bad_chs[:3]]
                })

    checked = matched + mismatched  # 不包括 missing（bin 中找不到的帧）
    match_rate = matched / checked if checked > 0 else 0.0
    mismatch_rate = mismatched / checked if checked > 0 else 0.0

    report = []
    report.append(f"ADC 抽样校验 (通道映射: {channel_map_name}): 抽查 {len(indices)} 帧, "
                  f"匹配 {matched}, 不匹配 {mismatched}, 缺失 {missing}, "
                  f"匹配率 {match_rate:.2%}")

    passed = (match_rate >= VALIDATION_CONFIG['adc_match_threshold'] and
              missing <= len(indices) * 0.05)  # 缺失率不超过 5%
    reason = ""

    if not passed:
        if match_rate < VALIDATION_CONFIG['adc_match_threshold']:
            reason = (f"ADC 匹配率 {match_rate:.2%} < 阈值 "
                      f"{VALIDATION_CONFIG['adc_match_threshold']:.0%}")
            report.append(f"[FAIL] {reason}")
        if missing > len(indices) * 0.05:
            reason = reason + "; " if reason else ""
            reason += f"bin 缺失率 {missing}/{len(indices)} 过高"
            report.append(f"[FAIL] {reason}")
        for d in mismatch_details[:3]:
            report.append(f"  不匹配 @帧号{int(d['idx'])}, BLE帧{d['ble_frame_id']}, "
                          f"SD锚点{d['sd_anchor']}")
    else:
        report.append(f"[PASS] ADC 抽样一致性 {match_rate:.2%} >= {VALIDATION_CONFIG['adc_match_threshold']:.0%}")

    return {
        'checked': checked, 'matched': matched, 'mismatched': mismatched,
        'missing': missing, 'match_rate': match_rate,
        'mismatch_rate': mismatch_rate,
        'mismatch_details': mismatch_details,
        'passed': passed, 'reason': reason,
        'report_lines': report,
        'channel_map_name': channel_map_name,
    }


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

            log(f"EMG文件信息: 采样率={sample_rate}Hz, 增益={self.gain}, 位深={bit_depth}bit")
            log(f"时间戳: {self.timestamp_str}")
            log(f"LSB系数: {self.lsb_uv:.6f} μV/LSB (用于转换)")

            # 读取所有帧
            while True:
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
    """IMU bin文件解析器 — 支持 2/3 IMU 动态数量"""

    def __init__(self, bin_path, num_imus=2):
        self.bin_path = bin_path
        self.num_imus = max(1, min(4, int(num_imus or 2)))
        self.sample_rate = 0
        self.timestamp_str = ""
        self.frames = {}  # {frame_id: tuple of imu dicts}
        self.frame_count = 0

    def parse(self):
        """解析bin文件"""
        file_size = os.path.getsize(self.bin_path)
        if file_size < HEADER_SIZE:
            raise ValueError(f"文件太小: {file_size} bytes")

        frame_size = 4 + self.num_imus * BYTES_PER_IMU_CHIP

        with open(self.bin_path, 'rb') as f:
            # 读取文件头
            header = f.read(HEADER_SIZE)
            magic, sample_rate, _, _, _, ts_bytes = struct.unpack(
                '<I H B B B 32s', header[:41]
            )

            if magic != IMU_MAGIC:
                raise ValueError(f"无效的IMU文件Magic: 0x{magic:08X}")

            self.sample_rate = sample_rate if 0 < sample_rate <= 1000 else 100
            self.timestamp_str = ts_bytes.decode('utf-8').strip('\x00')

            log(f"IMU文件信息: 采样率={self.sample_rate}Hz, num_imus={self.num_imus}, frame_size={frame_size}B")
            log(f"时间戳: {self.timestamp_str}")

            # 读取所有帧
            while True:
                chunk = f.read(frame_size)
                if len(chunk) < frame_size:
                    break

                frame_id = struct.unpack('<I', chunk[0:4])[0]
                raw_data = chunk[4:]

                # 解析每个 IMU 芯片
                def parse_chip(b):
                    ag = struct.unpack('<6h', b[0:12])
                    m = struct.unpack('<3h', b[12:18])
                    return {
                        'acc': [x * SCALE_ACCEL for x in ag[0:3]],
                        'gyr': [x * SCALE_GYRO for x in ag[3:6]],
                        'mag': [x * SCALE_MAG for x in m[0:3]]
                    }

                imus = []
                for k in range(self.num_imus):
                    off = k * BYTES_PER_IMU_CHIP
                    imus.append(parse_chip(raw_data[off:off + BYTES_PER_IMU_CHIP]))

                self.frames[frame_id] = tuple(imus)
                self.frame_count += 1

        log(f"解析完成: 共 {self.frame_count} 帧")
        return self


class SyntheticEMGParser:
    """EMG parser facade for rescue sync across multiple reset-frame-id bins."""

    def __init__(self, parsers, segment_bases):
        self.parsers = parsers
        self.segment_bases = segment_bases
        self.bin_path = " + ".join(os.path.basename(p.bin_path) for p in parsers)
        self.lsb_uv = parsers[0].lsb_uv if parsers else 0
        self.frames = {}
        for parser, base in zip(parsers, segment_bases):
            for fid, row in parser.frames.items():
                self.frames[int(base) + int(fid)] = row

    def get_frame(self, frame_id):
        return self.frames.get(int(frame_id))


class SyntheticIMUParser:
    """IMU parser facade aligned to SyntheticEMGParser synthetic frame ids."""

    def __init__(self, parsers, segment_bases):
        self.parsers = [p for p in parsers if p is not None]
        self.segment_bases = segment_bases
        self.bin_path = " + ".join(os.path.basename(p.bin_path) for p in self.parsers) if self.parsers else ""
        self.num_imus = self.parsers[0].num_imus if self.parsers else 2
        self.frames = {}
        for parser, base in zip(parsers, segment_bases):
            if parser is None:
                continue
            imu_base = int(base) // EMG_IMU_RATIO
            for fid, row in parser.frames.items():
                self.frames[imu_base + int(fid)] = row


def _format_validation_report(validation_report):
    """将校验报告格式化为可写入 H5 attrs 的字符串。

    Args:
        validation_report: dict，包含 frame_id_check, coverage_check, adc_verify

    Returns:
        str: 格式化的校验报告
    """
    lines = ["=== Sync Validation Report ==="]
    lines.append(f"Time: {datetime.now().isoformat()}")
    lines.append(f"Overall: {'PASS' if validation_report.get('all_passed', False) else 'FAIL'}")
    lines.append(f"Channel Map: {validation_report.get('channel_map_name', 'unknown')}")

    fid = validation_report.get('frame_id_check', {})
    if fid:
        lines.append(f"--- Frame ID Check ---")
        lines.append(f"Total: {fid.get('total', '?')}, Unique: {fid.get('unique', '?')}, "
                     f"Duplicates: {fid.get('duplicates', '?')} "
                     f"({fid.get('duplicate_ratio', 0):.2%})")
        lines.append(f"Gaps: {fid.get('gap_count', '?')}, Max gap: {fid.get('max_gap', '?')}")
        lines.append(f"Strictly increasing: {fid.get('is_strictly_increasing', '?')}")
        lines.append(f"Passed: {fid.get('passed', '?')}")

    cov = validation_report.get('coverage_check', {})
    if cov:
        lines.append(f"--- SD Coverage Check ---")
        lines.append(f"Unique SD frames: {cov.get('unique_sd_count', '?')}, "
                     f"Expected: {cov.get('expected_sd_count', '?')}, "
                     f"Coverage: {cov.get('coverage_ratio', 0):.2%}")
        lines.append(f"Passed: {cov.get('passed', '?')}")

    adc = validation_report.get('adc_verify', {})
    if adc:
        lines.append(f"--- ADC Verification ---")
        if adc.get('skipped'):
            lines.append(f"Status: SKIPPED ({adc.get('reason', 'verify=False')})")
        else:
            lines.append(f"Checked: {adc.get('checked', '?')}, Matched: {adc.get('matched', '?')}, "
                         f"Mismatched: {adc.get('mismatched', '?')}, Missing: {adc.get('missing', '?')}")
            lines.append(f"Match rate: {adc.get('match_rate', 0):.2%}")
            lines.append(f"Passed: {adc.get('passed', '?')}")

    if validation_report.get('failure_reasons'):
        lines.append(f"--- Failure Reasons ---")
        for r in validation_report['failure_reasons']:
            lines.append(f"  - {r}")

    return '\n'.join(lines)


# ===================== h5文件同步 =====================

def sync_h5_with_bin(h5_path, emg_bin_path, imu_bin_path=None, device_id=1, verify=True, set_synced=True,
                     channel_map_name='V2', manual_num_imus=None):
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
        channel_map_name: 通道映射名称 ('V1'/'V2'/'physical')，默认 'V2'
                          H5 attrs 中的 channel_map 优先于此参数
        manual_num_imus: 手动指定 IMU 数量，bin 自动检测失败时启用（None=自动检测）

    Returns:
        dict: 同步结果统计
    """
    log(f"开始同步: {os.path.basename(h5_path)}")
    log(f"EMG bin: {os.path.basename(emg_bin_path)}")
    if imu_bin_path:
        log(f"IMU bin: {os.path.basename(imu_bin_path)}")

    # 解析bin文件
    emg_parser = EMGBinParser(emg_bin_path).parse()
    _num_imus = 2
    try:
        with h5py.File(h5_path, 'r') as _f:
            _num_imus = _resolve_num_imus(_f, device_id, imu_bin_path, manual_num_imus=manual_num_imus)
    except Exception:
        pass
    imu_parser = IMUBinParser(imu_bin_path, num_imus=_num_imus).parse() if imu_bin_path else None

    # 打开h5文件
    with h5py.File(h5_path, 'r+') as f:
        # 检查sync_status
        current_status = f.attrs.get('sync_status', 'unknown')
        if current_status == 'synced':
            log("警告: 文件已同步，跳过")
            return {'status': 'skipped', 'reason': 'already_synced'}

        # Phase 4: 检查 collection_status，异常中断 segment 提示但不阻止同步
        coll_status = f.attrs.get('collection_status', 'unknown')
        if coll_status == 'abnormal_interrupted':
            log("⚠️ 注意: 这是异常中断 segment，仅同步已采集到的有效前半段数据")
            log(f"   中断原因: {f.attrs.get('interrupt_reason', '未知')}")
        elif coll_status == 'manual_stopped':
            log("ℹ️ 手动停止 segment，同步已采集数据")

        # ==== 新格式检测：stream_format_version & bin_pair_source ====
        stream_fmt_ver = f.attrs.get('stream_format_version', None)
        stream_mode = f.attrs.get('stream_mode', 'unknown')
        bin_pair_source = f.attrs.get('bin_pair_source', 'unknown')

        # 处理 bytes→str
        if isinstance(stream_mode, bytes):
            stream_mode = stream_mode.decode('utf-8')
        if isinstance(bin_pair_source, bytes):
            bin_pair_source = bin_pair_source.decode('utf-8')

        if stream_fmt_ver is not None and int(stream_fmt_ver) >= 2:
            log(f"📋 H5 格式: v{stream_fmt_ver} (新格式，一对一 bin 映射)")
            log(f"   stream_mode: {stream_mode}")
            log(f"   bin_pair_source: {bin_pair_source}")
            if bin_pair_source == 'collection_stream':
                log("   ✅ 使用 collection_stream bin（由 ble_server 切流产生）")
            elif bin_pair_source == 'preview_stream':
                log("   ⚠️ 警告: bin_pair_source 为 preview_stream！此 H5 可能错误引用了 preview bin")
        else:
            log("📋 H5 格式: v1 (旧格式，可能多 H5 共享同一个长 bin)")
            log("   将使用兼容模式同步（允许 ADC offset search 等降级策略）")

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

        # ===== 解析通道映射 =====
        channel_map, resolved_map_name = _resolve_channel_map(f, ds_250hz_name, channel_map_name)
        log(f"通道映射: {resolved_map_name} {'(1-indexed, 16ch reorder)' if channel_map else '(physical — 恒等映射)'}")

        # 读取250Hz数据和帧号
        data_250hz = ds_250hz[:]
        frame_ids = data_250hz['frame_id']
        channels_250hz = data_250hz['channels']
        timestamps_250hz = data_250hz['time']

        log(f"BLE帧号范围: [{frame_ids[0]}, {frame_ids[-1]}]")

        # ================================================================
        # == 防御性校验 1：frame_id 序列健康检查 ==
        # ================================================================
        log("=" * 50)
        log("防御性校验 1/3: frame_id 序列健康检查")
        validation_report = {
            'frame_id_check': None,
            'coverage_check': None,
            'adc_verify': None,
            'all_passed': False,
            'failure_reasons': [],
            'channel_map_name': resolved_map_name,
        }

        fid_result = validate_frame_ids(frame_ids)
        validation_report['frame_id_check'] = fid_result
        for line in fid_result['report_lines']:
            log(f"  {line}")

        if not fid_result['passed']:
            validation_report['failure_reasons'].append(fid_result['reason'])
            validation_report['all_passed'] = False

        # ================================================================
        # == 防御性校验 2：SD 覆盖率检查 ==
        # ================================================================
        log("防御性校验 2/3: SD 覆盖率检查")
        cov_result = validate_sd_coverage(frame_ids, DOWNSAMPLE_RATIO)
        validation_report['coverage_check'] = cov_result
        for line in cov_result['report_lines']:
            log(f"  {line}")

        if not cov_result['passed']:
            validation_report['failure_reasons'].append(cov_result['reason'])
            validation_report['all_passed'] = False

        # 计算对应的SD卡帧号范围（供后续使用）
        sd_frame_start = int(frame_ids[0]) * DOWNSAMPLE_RATIO
        sd_frame_end = int(frame_ids[-1]) * DOWNSAMPLE_RATIO + (DOWNSAMPLE_RATIO - 1)

        # ================================================================
        # == 防御性校验 3：ADC 一致性校验（强校验，非仅日志）==
        # ================================================================
        if verify:
            log(f"防御性校验 3/3: ADC 一致性校验 (通道映射: {resolved_map_name})")
            adc_result = run_adc_verification(
                frame_ids, channels_250hz, emg_parser,
                sample_count=VALIDATION_CONFIG['adc_sample_count'],
                downsample_ratio=DOWNSAMPLE_RATIO,
                channel_map=channel_map,
                channel_map_name=resolved_map_name,
            )
            validation_report['adc_verify'] = adc_result
            for line in adc_result['report_lines']:
                log(f"  {line}")

            if not adc_result['passed']:
                validation_report['failure_reasons'].append(adc_result['reason'])
        else:
            log("防御性校验 3/3: ADC 一致性校验 [已跳过] (verify=False)")
            adc_result = {
                'skipped': True,
                'reason': 'verify=False, ADC 校验已跳过',
                'checked': 0, 'matched': 0, 'mismatched': 0, 'missing': 0,
                'match_rate': 0.0, 'mismatch_rate': 0.0,
                'mismatch_details': [],
                'passed': True,  # 跳过不参与失败判定
                'report_lines': ['[SKIP] ADC 校验已跳过 (verify=False)'],
                'channel_map_name': resolved_map_name,
            }
            validation_report['adc_verify'] = adc_result

        # 汇总校验结果
        validation_report['all_passed'] = (fid_result['passed'] and
                                           cov_result['passed'] and
                                           adc_result['passed'])

        if not validation_report['all_passed']:
            log("=" * 50)
            log("[FAIL] 防御性校验未通过，拒绝同步:")
            for reason in validation_report['failure_reasons']:
                log(f"  - {reason}")
            log("=" * 50)

            # 写入失败状态到 H5
            f.attrs["sync_status"] = "sync_failed"
            f.attrs["sync_time"] = datetime.now().isoformat()
            f.attrs["sync_error"] = "; ".join(validation_report['failure_reasons'])
            f.attrs["sync_validation_report"] = _format_validation_report(validation_report)
            f.attrs["channel_map_name"] = resolved_map_name
            append_sync_history(f, action='sync', status='sync_failed',
                                details={'reasons': validation_report['failure_reasons']})
            log("sync_status 已设为 'sync_failed'，详细信息已写入 H5 attrs")

            result = {
                'status': 'validation_failed',
                'reason': '; '.join(validation_report['failure_reasons']),
                'frames_250hz': num_frames_250hz,
                'validation_report': {
                    'frame_id_duplicates': fid_result['duplicates'],
                    'frame_id_gaps': fid_result['gap_count'],
                    'sd_coverage_ratio': cov_result['coverage_ratio'],
                    'adc_match_rate': adc_result['match_rate']
                }
            }
            if imu_parser is not None:
                result['imu_status'] = 'skipped'
            return result

        log("=" * 50)
        log("[PASS] 所有防御性校验通过，继续同步...")
        log("=" * 50)

        # 构建2kHz数据
        log(f"正在构建2kHz数据 (通道顺序: {resolved_map_name})...")

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
                    # bin 数据是物理顺序 → 映射到 mapped 顺序，与 H5 250Hz 数据集一致
                    bin_data_mapped = map_physical_to_h5_order(bin_data, channel_map)
                    data_2khz[idx_2khz]['channels'] = np.array(bin_data_mapped, dtype=np.int32)
                    data_2khz[idx_2khz]['sd_frame_id'] = sd_frame_id
                    filled_frames += 1
                else:
                    # 帧丢失，使用插值或最近邻填充
                    # channels_250hz[i] 已经是 mapped 顺序，直接使用
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
                chunks=(1000,), compression="gzip"
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
        # == IMU 100Hz 同步：委托给 _sync_imu_100hz（支持 1-4 动态数量）==
        # ============================================================
        imu_result = _sync_imu_100hz(f, emg_parser, imu_parser, data_2khz, device_id)

        # 更新sync_status（仅当set_synced=True时）
        if set_synced:
            f.attrs["sync_status"] = "synced"
            f.attrs["sync_time"] = datetime.now().isoformat()
            f.attrs["channel_map_name"] = resolved_map_name
            # 写入校验报告供后续审计
            f.attrs["sync_validation_report"] = _format_validation_report(validation_report)
            append_sync_history(f, action='sync', status='synced',
                                details={'device_id': device_id, 'frames_2khz': num_frames_2khz})
            log(f"同步完成！EMG 2kHz: {ds_2khz_name}, IMU: {imu_result.get('imu_status', 'skipped')}, 状态已设为synced")
        else:
            log(f"同步完成！EMG 2kHz: {ds_2khz_name}, IMU: {imu_result.get('imu_status', 'skipped')}, 状态保持pending（等待其他设备同步）")

        result = {
            'status': 'success',
            'frames_250hz': num_frames_250hz,
            'frames_2khz': num_frames_2khz,
            'filled_frames': filled_frames,
            'missing_frames': missing_frames,
            'validation': {
                'frame_id_duplicates': validation_report['frame_id_check']['duplicates'],
                'frame_id_gaps': validation_report['frame_id_check']['gap_count'],
                'sd_coverage_ratio': validation_report['coverage_check']['coverage_ratio'],
                'adc_match_rate': validation_report['adc_verify']['match_rate'],
                'all_passed': True
            }
        }
        result.update(imu_result)
        return result


# ===================== ADC Offset Search (一对多模式) =====================

def _build_h5_signature_set(channels_250hz, anchor_indices, channel_map, channel_indices=None):
    """从 H5 锚点构建签名集合: {signature_tuple: set of anchor_indices}

    channel_indices=None 时使用全部 16 通道。
    """
    sig_set = {}
    for aidx in anchor_indices:
        row = channels_250hz[aidx]
        if channel_indices is not None:
            sig = tuple(int(row[ch]) for ch in channel_indices)
        else:
            sig = tuple(int(v) for v in row)
        sig_set.setdefault(sig, set()).add(aidx)
    return sig_set


def _scan_bin_for_h5_signatures(parser, channel_map, h5_sig_set, channel_indices, anchor_indices, max_offset):
    """单次扫描 bin frames，只记录命中 H5 签名集合的帧并反推 offset。

    返回: all_offset_votes dict (与之前格式兼容)
    """
    all_offset_votes = {}
    sig_hits = 0
    for sd_fid, row in parser.frames.items():
        mapped = map_physical_to_h5_order(row, channel_map)
        if channel_indices is not None:
            sig = tuple(int(mapped[ch]) for ch in channel_indices)
        else:
            sig = tuple(int(v) for v in mapped)
        matching_anchors = h5_sig_set.get(sig)
        if matching_anchors is None:
            continue
        sig_hits += len(matching_anchors)
        for aidx in matching_anchors:
            offset = sd_fid - aidx * DOWNSAMPLE_RATIO - (DOWNSAMPLE_RATIO - 1)
            if 0 <= offset <= max_offset:
                entry = all_offset_votes.setdefault(offset, {'votes': 0, 'matched_anchors': [], 'phase': offset % DOWNSAMPLE_RATIO})
                entry['votes'] += 1
                if len(entry['matched_anchors']) < 20:
                    entry['matched_anchors'].append(int(aidx))
    return all_offset_votes, sig_hits


def _select_high_variance_channels(channels_data, top_k=8):
    """选择方差最高的 k 个通道索引，用于 fallback 签名。"""
    variances = np.var(np.asarray(channels_data, dtype=np.float64), axis=0)
    top_indices = np.argsort(variances)[-top_k:][::-1]
    return list(int(i) for i in top_indices)


def _scan_bin_for_h5_rows(parser, channels_250hz, channel_map):
    """Scan bin frames and match complete H5 250Hz rows."""
    sig_to_rows = {}
    for row_idx, row in enumerate(channels_250hz):
        sig = tuple(int(v) for v in row)
        sig_to_rows.setdefault(sig, []).append(int(row_idx))

    matched_rows = {}
    duplicate_hits = 0
    for sd_fid, row in parser.frames.items():
        mapped = map_physical_to_h5_order(row, channel_map)
        sig = tuple(int(v) for v in mapped)
        row_indices = sig_to_rows.get(sig)
        if not row_indices:
            continue
        for row_idx in row_indices:
            if row_idx not in matched_rows:
                matched_rows[row_idx] = int(sd_fid)
            else:
                duplicate_hits += 1

    if not matched_rows:
        return {
            'found': False,
            'matched_rows': 0,
            'match_rate': 0.0,
            'start_sd_frame_id': None,
            'end_sd_frame_id': None,
            'duplicate_hits': duplicate_hits,
        }

    ordered = sorted(matched_rows.items())
    sd_values = [sd for _, sd in ordered]
    return {
        'found': True,
        'matched_rows': len(matched_rows),
        'match_rate': len(matched_rows) / max(1, len(channels_250hz)),
        'start_sd_frame_id': int(min(sd_values)),
        'end_sd_frame_id': int(max(sd_values)),
        'first_h5_row': int(ordered[0][0]),
        'last_h5_row': int(ordered[-1][0]),
        'duplicate_hits': duplicate_hits,
        'matched_row_map': matched_rows,
    }


def find_bin_offset_by_adc(h5_path, emg_bin_path, device_id=1, channel_map_name='V2',
                           num_anchors=40, match_threshold=0.95, max_offset_search=None):
    """通过 ADC 采样值在长 bin 中搜索 H5 250Hz 数据的对应 offset。

    用于一对多模式：一个长 bin 对应多个 H5。不依赖 H5 的旧 frame_id，
    只使用 H5 250Hz ADC 的原始通道值在 bin 中做锚点匹配。

    Args:
        h5_path: H5 文件路径
        emg_bin_path: EMG bin 文件路径
        device_id: 设备 ID
        channel_map_name: 通道映射名称
        num_anchors: 使用的锚点数量（均匀抽样）
        match_threshold: 匹配率阈值（>=此值才认为找到）
        max_offset_search: 最大搜索 offset（None = 搜整个 bin）

    Returns:
        dict: {
            'found': bool,
            'offset': int or None,  # H5 row 0 对应的 bin 2kHz frame offset
            'match_rate': float,
            'checked': int, 'matched': int, 'mismatched': int,
            'candidates': list of (offset, match_rate),  # 候选偏移
            'channel_map_name': str,
            'error': str or None,
        }
    """
    log("=" * 50)
    log(f"ADC offset search: {os.path.basename(h5_path)}")
    log(f"  bin: {os.path.basename(emg_bin_path)}, device_id={device_id}")
    log(f"  anchors={num_anchors}, threshold={match_threshold}")
    log("=" * 50)

    # 1. 读取 H5 250Hz ADC 数据
    with h5py.File(h5_path, 'r') as f:
        ds_name = f"emg{device_id}_250hz_adc"
        if ds_name not in f:
            return {'found': False, 'offset': None, 'error': f'数据集 {ds_name} 不存在'}
        ds = f[ds_name]
        n = ds.shape[0]
        if n < 10:
            return {'found': False, 'offset': None, 'error': f'250Hz 数据太少 ({n} 帧)'}
        data_250hz = ds[:]
        channel_map, resolved_name = _resolve_channel_map(f, ds_name, channel_map_name)

    channels_250hz = data_250hz['channels']

    # 2. 解析 bin
    parser = EMGBinParser(emg_bin_path).parse()
    bin_total = len(parser.frames)
    if bin_total == 0:
        return {'found': False, 'offset': None, 'error': 'bin 文件为空'}

    log(f"  H5 250Hz: {n} frames, bin 2kHz: {bin_total} frames")

    # 3. 构建候选 channel_map 列表（自动 fallback 到 physical，用于旧 L015 数据）
    map_candidates = []
    if channel_map is not None:
        map_candidates.append((channel_map, resolved_name))
    if channel_map is None or resolved_name.lower() not in ('physical', 'none', 'identity'):
        map_candidates.append((None, 'physical'))

    max_offset = max_offset_search or (bin_total - n * DOWNSAMPLE_RATIO)
    if max_offset <= 0:
        return {'found': False, 'offset': None, 'error': f'bin too small (bin={bin_total}, need>{n * DOWNSAMPLE_RATIO})'}

    # 4. 选择锚点（用于 anchor fallback）
    skip_start = min(500, n // 5)
    skip_end = min(500, n // 5)
    usable = n - skip_start - skip_end
    if usable < num_anchors:
        num_anchors = max(5, usable)
        skip_start = (n - num_anchors) // 2
        skip_end = n - skip_start - num_anchors
    anchor_indices = np.linspace(skip_start, n - skip_end - 1, num_anchors, dtype=int)

    # 5. 尝试每种 channel_map：先完整行扫描，失败再锚点签名搜索
    best_overall = None

    for cm, cm_name in map_candidates:
        log(f"  --- trying channel_map={cm_name} ---")

        # 5a. 完整行扫描（最可靠；不受 frame_id 或 BLE 丢包影响）
        row_result = _scan_bin_for_h5_rows(parser, channels_250hz, cm)
        row_rate = row_result['match_rate']
        log(f"    row scan: matched={row_result['matched_rows']}/{n}, rate={row_rate:.3f}")
        if row_result.get('duplicate_hits', 0) > 0:
            log(f"      duplicate hits: {row_result['duplicate_hits']}")

        if row_rate >= match_threshold:
            sd_start = row_result['start_sd_frame_id']
            sd_end = row_result['end_sd_frame_id']
            first_row = row_result['first_h5_row']
            derived_offset = sd_start - first_row * DOWNSAMPLE_RATIO
            log(f"    row scan PASSED (channel_map={cm_name}): start={sd_start}, end={sd_end}, rate={row_rate:.3f}")
            log(f"    derived offset={derived_offset} (from first matched row {first_row})")
            log(f"    range_mode=row_signature_span")
            return {
                'found': True,
                'offset': int(derived_offset),
                'match_rate': row_rate,
                'checked': n,
                'matched': row_result['matched_rows'],
                'mismatched': n - row_result['matched_rows'],
                'candidates': [],
                'channel_map_name': cm_name,
                'phase': None,
                'range_mode': 'row_signature_span',
                'start_sd_frame_id': int(sd_start),
                'end_sd_frame_id': int(sd_end),
                'error': None,
            }

        # 5b. 锚点签名搜索（fallback）
        def _make_score_offset(_cm):
            def _score(offset):
                matched = 0; checked = 0
                for idx in anchor_indices:
                    sd_fid = offset + idx * DOWNSAMPLE_RATIO + (DOWNSAMPLE_RATIO - 1)
                    row = parser.get_frame(sd_fid)
                    if row is None: continue
                    checked += 1
                    if np.all(np.abs(np.array(map_physical_to_h5_order(row, _cm), dtype=np.int32) - channels_250hz[idx]) <= 1):
                        matched += 1
                return matched, checked
            return _score

        score_offset = _make_score_offset(cm)
        sig_configs = [
            (16, None, 'full 16ch'),
            (8, _select_high_variance_channels(channels_250hz, top_k=8), 'high-var 8ch'),
            (4, _select_high_variance_channels(channels_250hz, top_k=4), 'high-var 4ch'),
        ]

        all_offset_votes = {}
        for n_ch, ch_indices, label in sig_configs:
            h5_sig_set = _build_h5_signature_set(channels_250hz, anchor_indices, cm, ch_indices)
            votes, sig_hits = _scan_bin_for_h5_signatures(parser, cm, h5_sig_set, ch_indices, anchor_indices, max_offset)
            all_offset_votes.update(votes)
            log(f"    anchor {label}: H5 set={len(h5_sig_set)}, hits={sig_hits}, candidates={len(all_offset_votes)}")
            if len(all_offset_votes) > 500:
                break

        if not all_offset_votes:
            log(f"    anchor search: 0 hits for {cm_name}")
            continue

        sorted_candidates = sorted(all_offset_votes.items(), key=lambda x: -x[1]['votes'])
        top_k = min(50, len(sorted_candidates))
        final_results = []
        for off, info in sorted_candidates[:top_k]:
            matched, checked = score_offset(off)
            if checked >= num_anchors * 0.3:
                rate = matched / checked if checked > 0 else 0
                final_results.append((off, rate, matched, checked, info['votes'], info['phase']))
        final_results.sort(key=lambda x: -x[1])

        if final_results:
            best = final_results[0]
            log(f"    anchor best: offset={best[0]}, rate={best[1]:.3f} ({best[2]}/{best[3]})")
            if best_overall is None or best[1] > best_overall[1]:
                best_overall = (best[0], best[1], best[2], best[3], best[4], best[5], cm_name)

    # 6. 没有 channel map 找到任何结果
    if best_overall is None:
        names = ', '.join(n for _, n in map_candidates)
        return {
            'found': False, 'offset': None, 'match_rate': 0.0,
            'checked': 0, 'matched': 0, 'mismatched': 0,
            'candidates': [], 'channel_map_name': resolved_name, 'phase': None,
            'error': f'no match in any channel map (tried: {names})',
        }

    best_off, best_rate, best_matched, best_checked, best_votes, best_phase, best_cm = best_overall
    found = best_rate >= match_threshold

    log(f"  best overall: channel_map={best_cm}, offset={best_off}, rate={best_rate:.3f} ({best_matched}/{best_checked})")
    if not found:
        log(f"  [FAIL] best_rate {best_rate:.3f} < threshold {match_threshold}")

    return {
        'found': found,
        'offset': best_off if found else None,
        'match_rate': best_rate,
        'checked': best_checked,
        'matched': best_matched,
        'mismatched': best_checked - best_matched,
        'candidates': [],
        'channel_map_name': best_cm,
        'phase': best_phase,
        'range_mode': 'anchor_votes',
        'start_sd_frame_id': None,
        'end_sd_frame_id': None,
        'error': None if found else f'match_rate {best_rate:.3f} < threshold {match_threshold}',
    }

def _verify_imu_frame_ids(imu_bin_path, num_imus, max_frames=100):
    """用给定的 num_imus 解析 bin 前 max_frames 帧，验证 frame_id 严格递增。

    错误 num_imus 会导致帧边界偏移，后续帧的 "frame_id" 实际是传感器数据字节，
    极不可能形成 prev_id+1 的递增序列。因此 frame_id 是否严格 +1 递增是强判别信号。

    针对多段录制拼接 bin（前段尾部残余 + 新段从 0 开始），不要求从第 0 帧起连续，
    而是追踪**全局最长连续递增序列 (longest_consecutive_run)**。

    Returns:
        dict: {
            'total': int,                    # 成功读取的帧数
            'valid': int,                    # 所有 +1 递增帧数 (含不同段)
            'longest_run': int,              # 最长连续 +1 递增序列长度
            'score': float,                  # longest_run / total (0.0-1.0)
            'first_fid': int,                # 首帧 frame_id
            'last_fid': int,                 # 末帧 frame_id
            'run_start_fid': int,            # 最长连续序列起始 frame_id
        }
        读取失败返回 {'total': 0, 'valid': 0, 'score': 0.0, ...}
    """
    frame_size = 4 + num_imus * BYTES_PER_IMU_CHIP
    result = {'total': 0, 'valid': 0, 'score': 0.0,
              'first_fid': None, 'last_fid': None,
              'longest_run': 0, 'run_start_fid': None}
    try:
        with open(imu_bin_path, 'rb') as f:
            f.seek(HEADER_SIZE)
            prev_fid = None
            current_run = 0
            for i in range(max_frames):
                chunk = f.read(frame_size)
                if len(chunk) < frame_size:
                    break
                result['total'] += 1
                fid = struct.unpack('<I', chunk[0:4])[0]
                if result['first_fid'] is None:
                    result['first_fid'] = fid
                result['last_fid'] = fid

                if prev_fid is None:
                    # 第一帧：始终接受，启动当前连续序列
                    current_run = 1
                    result['valid'] += 1
                    if current_run > result['longest_run']:
                        result['longest_run'] = current_run
                        result['run_start_fid'] = fid
                elif fid == prev_fid + 1:
                    result['valid'] += 1
                    current_run += 1
                    if current_run > result['longest_run']:
                        result['longest_run'] = current_run
                        result['run_start_fid'] = fid - current_run + 1
                else:
                    # 断裂点：结束当前 run，开始新的
                    # 如果断裂后立即恢复递增（多段拼接），重置 current_run
                    # 否则中断整个验证（错误 num_imus）
                    if current_run < 2 and i > 1:
                        # 已读超过 1 帧但最长 run 仍 < 2 → 无法形成有效递增，中断
                        break
                    current_run = 1  # 从当前帧重新开始计数
                    result['valid'] += 1  # 当前帧也算 valid (作为新 run 的起点)
                    if current_run > result['longest_run']:
                        result['longest_run'] = current_run
                        result['run_start_fid'] = fid
                prev_fid = fid

            # 最终 score 基于最长连续序列
            result['score'] = result['longest_run'] / result['total'] if result['total'] > 0 else 0.0
    except Exception:
        pass
    return result


def _verify_imu_count_fits_bin(imu_bin_path, num_imus, min_consecutive=5):
    """快速验证：给定的 num_imus 能否正确解析 bin 文件的帧结构。

    用于交叉验证：当 bin 自动检测与 BLE 数据冲突时，
    分别验证两个候选值，选择帧 ID 递增序列更长的一方。

    Args:
        imu_bin_path: IMU bin 文件路径
        num_imus: 待验证的 IMU 数量
        min_consecutive: 最少连续递增帧数（用于判定"解析正确"的阈值）

    Returns:
        dict: _verify_imu_frame_ids 的完整结果，额外增加 'passed' 字段
    """
    vr = _verify_imu_frame_ids(imu_bin_path, num_imus, max_frames=200)
    vr['passed'] = vr['score'] >= 0.90 and vr['longest_run'] >= min_consecutive
    return vr


def _validate_imu_sensor_data(all_data, num_imus, sample_size=500):
    """校验每个 IMU 传感器的数据质量，标记疑似损坏的传感器。

    检测指标：
    1. 非零率 < 10% → 疑似损坏（正常 IMU 数据几乎不会精确为零）
    2. Acc 范围超过 ±8g → 疑似字节错位（正常 ±2g 传感器 + 余量）
    3. 方差接近 0 → 传感器卡死（输出恒定值）
    4. 含 NaN/Inf → 数据损坏

    Args:
        all_data: list of np.ndarray, 每个元素是一个传感器的 100hz 数据
        num_imus: 传感器总数
        sample_size: 抽样校验的帧数（取均匀分布的样本）

    Returns:
        dict: {
            'active': [0, 1, ...],      # 正常工作的传感器索引
            'inactive': [2, ...],        # 疑似损坏的传感器索引
            'sensors': [                 # 每个传感器的详细统计
                {'idx': 0, 'non_zero_rate': 0.98, 'acc_range': [-1.5, 1.5],
                 'gyr_range': [-200, 200], 'issue': None},
                {'idx': 1, 'non_zero_rate': 0.02, 'acc_range': [0, 0],
                 'gyr_range': [0, 0], 'issue': 'near_zero'},
            ]
        }
    """
    if num_imus == 0 or not all_data:
        return {'active': [], 'inactive': [], 'sensors': []}

    result = {'active': [], 'inactive': [], 'sensors': []}

    for k in range(num_imus):
        data = all_data[k]
        n_frames = len(data)
        if n_frames == 0:
            result['sensors'].append({
                'idx': k, 'non_zero_rate': 0.0,
                'acc_range': [0, 0], 'gyr_range': [0, 0],
                'issue': 'empty_dataset'
            })
            result['inactive'].append(k)
            continue

        # 均匀抽样
        indices = np.linspace(0, n_frames - 1, min(sample_size, n_frames), dtype=int)
        sample = data[indices]

        acc = sample['acc']
        gyr = sample['gyr']

        # 指标 1: 非零率
        acc_nonzero = np.count_nonzero(acc)
        gyr_nonzero = np.count_nonzero(gyr)
        total_vals = acc.size + gyr.size
        non_zero_rate = (acc_nonzero + gyr_nonzero) / total_vals if total_vals > 0 else 0.0

        # 指标 2: 值范围
        acc_min, acc_max = (float(np.min(acc)), float(np.max(acc))) if acc.size > 0 else (0, 0)
        gyr_min, gyr_max = (float(np.min(gyr)), float(np.max(gyr))) if gyr.size > 0 else (0, 0)

        # 指标 3: 方差（防止恒定值）
        acc_var = float(np.var(acc)) if acc.size > 0 else 0.0
        gyr_var = float(np.var(gyr)) if gyr.size > 0 else 0.0

        # 指标 4: NaN/Inf
        has_nan_inf = bool(np.any(np.isnan(acc)) or np.any(np.isinf(acc)) or
                          np.any(np.isnan(gyr)) or np.any(np.isinf(gyr)))

        # 诊断
        issues = []
        if non_zero_rate < 0.10:
            issues.append(f'near_zero({non_zero_rate:.1%})')
        if abs(acc_min) > 8.0 or abs(acc_max) > 8.0:
            issues.append(f'acc_range_abnormal([{acc_min:.1f}, {acc_max:.1f}]g)')
        if abs(gyr_min) > 4000 or abs(gyr_max) > 4000:
            issues.append(f'gyr_range_abnormal([{gyr_min:.0f}, {gyr_max:.0f}]dps)')
        if acc_var < 1e-8 and gyr_var < 1e-8:
            issues.append('constant_signal')
        if has_nan_inf:
            issues.append('nan_inf')

        sensor_info = {
            'idx': k,
            'non_zero_rate': round(non_zero_rate, 4),
            'acc_range': [round(acc_min, 4), round(acc_max, 4)],
            'gyr_range': [round(gyr_min, 4), round(gyr_max, 4)],
            'acc_var': round(acc_var, 6),
            'gyr_var': round(gyr_var, 6),
            'issue': '; '.join(issues) if issues else None,
        }
        result['sensors'].append(sensor_info)

        if issues:
            result['inactive'].append(k)
        else:
            result['active'].append(k)

    return result


def _detect_num_imus_from_bin(imu_bin_path):
    """通过 IMU bin 文件自动检测 IMU 芯片数量。

    三级策略（按优先级递进）：
    1. 整除检测：data_size % frame_size == 0 → 精确匹配，直接返回
    2. 帧解析验证：对每个候选 num_imus 解析前 N 帧，
       验证 frame_id 严格 +1 递增 — 这是强判别信号，
       错误 num_imus 会导致帧边界错位，读到的 "frame_id" 实际是传感器数据
    3. 最佳余数：截断文件场景，选择余数最小且 < frame/2 的 num_imus
       （仅当其他候选余数均 > frame/2 时，避免平局误判）

    Returns:
        int: 检测到的 IMU 数量 (2/3/4)，检测失败返回 None
    """
    if not imu_bin_path or not os.path.exists(imu_bin_path):
        return None
    try:
        file_size = os.path.getsize(imu_bin_path)
        data_size = file_size - HEADER_SIZE
        if data_size <= 0:
            return None

        # ---- 策略 1: 整除检测 ----
        candidates_exact = []
        for n in [4, 3, 2, 1]:
            frame_size = 4 + n * BYTES_PER_IMU_CHIP
            if data_size % frame_size == 0:
                candidates_exact.append(n)
        if len(candidates_exact) == 1:
            return candidates_exact[0]

        # ---- 策略 2: 帧解析验证 (多候选或有截断时) ----
        # 计算每个候选 num_imus 的最高帧数和最低帧数
        max_frames_by_n = {}
        for n in [4, 3, 2, 1]:
            fs = 4 + n * BYTES_PER_IMU_CHIP
            max_f = data_size // fs
            if max_f >= 3:  # 至少需要 3 帧才能验证递增
                max_frames_by_n[n] = min(100, max_f)

        if max_frames_by_n:
            verify_results = {}
            for n, max_f in max_frames_by_n.items():
                vr = _verify_imu_frame_ids(imu_bin_path, n, max_f)
                verify_results[n] = vr
                if vr['total'] > 0:
                    log(f"  num_imus={n}: 解析 {vr['total']} 帧, "
                        f"最长递增序列 {vr['longest_run']}/{vr['total']} "
                        f"(score={vr['score']:.1%}), "
                        f"first={vr['first_fid']}, last={vr['last_fid']}")

            # 筛选：最长连续序列 >= 10 帧且 score >= 90%
            high_score = {n: vr for n, vr in verify_results.items()
                          if vr['score'] >= 0.90 and vr['longest_run'] >= 10}
            if len(high_score) == 1:
                n = list(high_score.keys())[0]
                log(f"  帧解析验证: num_imus={n} (最长连续 {high_score[n]['longest_run']} 帧, "
                    f"score={high_score[n]['score']:.1%})")
                return n
            if len(high_score) > 1:
                log(f"  ⚠️ 多个 num_imus 帧解析均通过: {list(high_score.keys())}，继续检测...")
                # 多个都通过 → 歧义，继续策略 3

            # 宽松：至少连续 3 帧递增
            any_valid = {n: vr for n, vr in verify_results.items()
                         if vr['longest_run'] >= 3}
            if len(any_valid) == 1:
                n = list(any_valid.keys())[0]
                log(f"  帧解析验证(宽松): num_imus={n} (最长连续 {any_valid[n]['longest_run']} 帧)")
                return n

        # ---- 策略 3: 最佳余数 (截断文件 fallback) ----
        remainders = {}
        for n in [4, 3, 2, 1]:
            frame_size = 4 + n * BYTES_PER_IMU_CHIP
            rem = data_size % frame_size
            remainders[n] = (rem, frame_size)

        # 找余数最小（绝对值）的候选
        best_n = min(remainders, key=lambda n: remainders[n][0])
        best_rem, best_fs = remainders[best_n]

        # 检查：最佳候选的余数 < frame/2，且其他候选余数 >= frame/2
        others_below_half = [n for n, (rem, fs) in remainders.items()
                             if n != best_n and rem < fs * 0.5]
        if best_rem < best_fs * 0.5 and len(others_below_half) == 0:
            log(f"  最佳余数检测: num_imus={best_n} (余数={best_rem}B < frame/2={best_fs * 0.5:.0f}B, "
                f"其他候选余数均 > frame/2)")
            return best_n

        # 检查：最佳候选余数明显小于其他候选（差距 > 4 字节）
        second_rem = min((remainders[n][0] for n in remainders if n != best_n), default=999)
        if best_rem + 4 < second_rem:
            log(f"  最佳余数检测: num_imus={best_n} (余数={best_rem}B << 次优={second_rem}B)")
            return best_n

        if candidates_exact:
            log(f"  ⚠️ bin 整除检测歧义 (均整除): {candidates_exact}，保守回退")
        else:
            log(f"  ⚠️ bin 自动检测不确定: remainders={[(n, remainders[n][0]) for n in [4,3,2,1]]}")
        return None
    except OSError:
        pass
    return None


def _resolve_num_imus(h5_file, device_id, imu_bin_path=None, manual_num_imus=None):
    """多源融合推断 IMU 数量，鲁棒处理 1~3 传感器任意损坏/截断场景。

    数据源（按可靠性排序）：
    A. BLE 实测数据 — imu_all_ble 中实际出现的 imu_index（硬件真实工作的传感器）
    B. BLE 握手报告 — imu{dev}_num_imus attr（硬件固件报告的物理传感器数）
    C. bin 结构检测 — SD 卡 bin 文件的帧大小整除/帧ID验证

    冲突解决策略：
    - bin 检测 vs BLE 实测：以「帧 ID 验证」为裁判，对两个候选值分别验证，
      选择帧 ID 递增序列更长（即能正确解析 bin 帧结构）的一方
    - 若双方都通过验证 → 取较小值（避免写入无数据传感器的全零/垃圾数据）
    - 若仅一方通过验证 → 取通过方
    - 若都未通过 → 取较小值 + 记录警告

    这种策略能同时处理：
    - bin 截断误判（bin=2, BLE=1 → 验证 n=1 通过、n=2 也通过 → 取 1）
    - BLE 带宽限制漏传（bin=3, BLE=2 → 验证 n=3 通过、n=2 也通过 → 取 2，保守）
    - 传感器损坏（bin=3, BLE=3，但有一个传感器全零 → 后续数据质量校验会标记）
    """
    # 0. 手动指定（最高优先级）
    if manual_num_imus is not None:
        n = max(1, min(4, int(manual_num_imus)))
        log(f"  IMU 数量由用户指定: {n}")
        return n

    # ── 收集各数据源的 IMU 数量 ──

    # A. BLE 实测数据（imu_all_ble 中实际出现的 imu_index 范围）
    ble_data_count = None
    all_ds_name = f'imu{device_id}_all_ble'
    if all_ds_name in h5_file and h5_file[all_ds_name].shape[0] > 0:
        ds = h5_file[all_ds_name]
        if hasattr(ds, 'dtype') and ds.dtype.names and 'imu_index' in ds.dtype.names:
            try:
                max_idx = int(ds['imu_index'][:].max())
                ble_data_count = max(1, min(4, max_idx + 1))
            except Exception:
                pass

    # B. BLE 握手报告（H5 attrs 中存储的硬件配置值）
    ble_hw_count = None
    for attr_key in (f'imu{device_id}_num_imus', 'num_imus'):
        val = h5_file.attrs.get(attr_key)
        if val is not None:
            try:
                ble_hw_count = max(1, min(4, int(val)))
                break
            except (ValueError, TypeError):
                pass

    # C. bin 文件结构检测
    bin_count = None
    if imu_bin_path:
        bin_count = _detect_num_imus_from_bin(imu_bin_path)

    # ── 多源融合决策 ──

    # 有效 BLE 参考值：实测数据优先，握手报告兜底
    ble_ref = ble_data_count or ble_hw_count

    if bin_count is not None and ble_ref is not None:
        if bin_count == ble_ref:
            # 一致 → 高置信度
            log(f"  IMU 数量: {bin_count} (bin检测=BLE数据={ble_ref}，一致)")
            return bin_count

        # 冲突 → 帧 ID 验证裁判
        log(f"  ⚠️ IMU 数量冲突: bin检测={bin_count}, BLE参考={ble_ref}")
        log(f"     来源: BLE实测={ble_data_count}, BLE握手={ble_hw_count}, bin检测={bin_count}")
        log(f"     启动帧ID验证裁决...")

        # 分别验证两个候选值
        candidates = sorted(set([bin_count, ble_ref]))
        best_n = None
        best_score = -1

        for n in candidates:
            vr = _verify_imu_count_fits_bin(imu_bin_path, n, min_consecutive=10)
            passed = vr.get('passed', False)
            log(f"     n={n}: score={vr['score']:.1%}, "
                f"longest_run={vr['longest_run']}/{vr['total']}, "
                f"first_fid={vr.get('first_fid')}, {'✓ PASS' if passed else '✗ FAIL'}")
            if passed and vr['score'] > best_score:
                best_score = vr['score']
                best_n = n

        if best_n is not None:
            log(f"     裁决结果: n={best_n} (帧ID验证得分={best_score:.1%})")
            return best_n

        # 双方都未通过帧ID验证 → 取较小值（保守）
        conservative = min(bin_count, ble_ref)
        log(f"     ⚠️ 双方均未通过帧ID验证，保守取较小值: {conservative}")
        return conservative

    # ── 单一数据源回退 ──
    if bin_count is not None:
        log(f"  IMU 数量来自 bin 检测: {bin_count}")
        return bin_count
    if ble_ref is not None:
        log(f"  IMU 数量来自 BLE 参考: {ble_ref}")
        return ble_ref

    # ── 无 bin 且无 BLE attr → 数据集推断 ──
    # 从 imu*_all_ble dataset 推断（即使为空也尝试）
    if all_ds_name in h5_file:
        # 尝试从 imu_index 推断
        ds = h5_file[all_ds_name]
        if hasattr(ds, 'dtype') and ds.dtype.names and 'imu_index' in ds.dtype.names:
            try:
                data = ds[:]
                if len(data) > 0:
                    max_idx = int(data['imu_index'].max())
                    n = max(1, min(4, max_idx + 1))
                    log(f"  IMU 数量推断自 BLE 数据: {n} (imu_index max={max_idx})")
                    return n
            except Exception:
                pass

    # 从 imu*{a,b,c}_ble dataset 存在性推断
    for candidate in [3, 2]:
        ds = f'imu{device_id}{chr(ord("a") + candidate - 1)}_ble'
        if ds in h5_file:
            log(f"  IMU 数量推断自数据集 {ds}: {candidate}")
            return candidate

    log("  IMU 数量默认: 2")
    return 2


# ===================== 一对一同步 =====================

def _get_250hz_anchor_sd_frame_ids(data_250hz, bin_offset=0):
    """Return the SD 2 kHz frame id for each stored 250 Hz anchor row."""
    names = data_250hz.dtype.names or ()
    if 'sd_frame_id' in names:
        return data_250hz['sd_frame_id'].astype(np.int64), 'sd_frame_id'
    if 'frame_id' in names:
        return data_250hz['frame_id'].astype(np.int64) * DOWNSAMPLE_RATIO + (DOWNSAMPLE_RATIO - 1), 'frame_id'
    return (np.arange(len(data_250hz), dtype=np.int64) * DOWNSAMPLE_RATIO
            + int(bin_offset) + (DOWNSAMPLE_RATIO - 1)), 'row_index'


def _verify_anchor_matches(parser, channels_250hz, channel_map, anchor_sd_frame_ids, num_check=200):
    total = len(channels_250hz)
    if total == 0:
        return 0, 0, 0.0, []

    check_indices = np.linspace(0, total - 1, min(num_check, total), dtype=int)
    matched = 0
    checked = 0
    mismatch_details = []

    for idx in check_indices:
        sd_fid = int(anchor_sd_frame_ids[idx])
        if sd_fid < 0:
            continue
        bin_data = parser.get_frame(sd_fid)
        if bin_data is None:
            continue
        checked += 1
        bin_mapped = map_physical_to_h5_order(bin_data, channel_map)
        if np.all(np.abs(np.array(bin_mapped, dtype=np.int32) - channels_250hz[idx]) <= 1):
            matched += 1
        elif len(mismatch_details) < 5:
            mismatch_details.append({
                'h5_row': int(idx), 'sd_fid': sd_fid,
                'h5_ch0': int(channels_250hz[idx][0]),
                'bin_ch0': int(bin_mapped[0]),
            })

    match_rate = matched / checked if checked > 0 else 0.0
    return matched, checked, match_rate, mismatch_details


def _recover_one_to_one_anchors(parser, channels_250hz, channel_map):
    row_result = _scan_bin_for_h5_rows(parser, channels_250hz, channel_map)
    threshold = VALIDATION_CONFIG['adc_match_threshold']
    if not row_result.get('found') or row_result.get('match_rate', 0.0) < threshold:
        return None, row_result

    matched_map = row_result.get('matched_row_map', {})
    anchors = np.full(len(channels_250hz), -1, dtype=np.int64)
    for row_idx, sd_fid in matched_map.items():
        anchors[int(row_idx)] = int(sd_fid)

    matched_positions = np.flatnonzero(anchors >= 0)
    if len(matched_positions) >= 2:
        first_pos = int(matched_positions[0])
        first_anchor = int(anchors[first_pos])
        for i in range(first_pos - 1, -1, -1):
            anchors[i] = first_anchor - (first_pos - i) * DOWNSAMPLE_RATIO

        last_pos = int(matched_positions[-1])
        last_anchor = int(anchors[last_pos])
        for i in range(last_pos + 1, len(anchors)):
            anchors[i] = last_anchor + (i - last_pos) * DOWNSAMPLE_RATIO

    return anchors, row_result


def _round_to_imu_boundary(value):
    return int(round(float(value) / EMG_IMU_RATIO) * EMG_IMU_RATIO)


def _build_multibin_rescue_anchors(segments, total_rows):
    anchors = np.full(total_rows, -1, dtype=np.int64)
    segment_bases = []
    metadata = []

    first_filled_row = None
    prev_last_row = None
    prev_last_anchor = None
    for seg_idx, seg in enumerate(segments):
        row_map = seg['row_result'].get('matched_row_map', {})
        ordered = sorted((int(r), int(sd)) for r, sd in row_map.items())
        if not ordered:
            continue

        first_row, first_sd = ordered[0]
        last_row, last_sd = ordered[-1]
        if seg_idx == 0 or prev_last_row is None:
            base = 0
        else:
            expected_first_anchor = prev_last_anchor + (first_row - prev_last_row) * DOWNSAMPLE_RATIO
            base = _round_to_imu_boundary(expected_first_anchor - first_sd)
            if base < 0:
                base = 0

        segment_bases.append(base)
        if first_filled_row is None:
            first_filled_row = first_row

        # Fill the whole matched span with the expected 250 Hz stride. Rows that
        # do not exist in the actual bin will become interpolated/missing frames.
        for row in range(first_row, last_row + 1):
            anchors[row] = base + first_sd + (row - first_row) * DOWNSAMPLE_RATIO

        if prev_last_row is not None and first_row > prev_last_row + 1:
            for row in range(prev_last_row + 1, first_row):
                anchors[row] = prev_last_anchor + (row - prev_last_row) * DOWNSAMPLE_RATIO

        prev_last_row = last_row
        prev_last_anchor = base + first_sd + (last_row - first_row) * DOWNSAMPLE_RATIO
        metadata.append({
            'bin': os.path.basename(seg['parser'].bin_path),
            'synthetic_base': int(base),
            'first_h5_row': int(first_row),
            'last_h5_row': int(last_row),
            'matched_rows': int(len(row_map)),
            'original_start_sd': int(first_sd),
            'original_end_sd': int(last_sd),
            'match_rate_partial': float(len(row_map) / max(1, last_row - first_row + 1)),
        })

    if first_filled_row is not None and first_filled_row > 0:
        first_anchor = int(anchors[first_filled_row])
        for row in range(first_filled_row - 1, -1, -1):
            anchors[row] = first_anchor - (first_filled_row - row) * DOWNSAMPLE_RATIO

    if prev_last_row is not None and prev_last_row < total_rows - 1:
        for row in range(prev_last_row + 1, total_rows):
            anchors[row] = prev_last_anchor + (row - prev_last_row) * DOWNSAMPLE_RATIO

    return anchors, segment_bases, metadata


def sync_h5_one_to_one_multibin_rescue(h5_path, emg_bin_paths, imu_bin_paths=None, device_id=1,
                                       verify=True, set_synced=True, channel_map_name='V2',
                                       manual_num_imus=None, min_matched_rows=40,
                                       min_single_segment_coverage=0.50):
    """Rescue an H5 that accidentally spans adjacent collection bins.

    This mode is intentionally conservative: every segment must be supported by
    direct ADC row matches. Uncovered H5 rows are kept as missing/interpolated
    2 kHz data and the segment metadata is written into H5 attrs.
    """
    emg_bin_paths = [p for p in (emg_bin_paths or []) if p]
    imu_bin_paths = list(imu_bin_paths or [])
    if len(emg_bin_paths) < 1:
        return {'status': 'error', 'reason': 'rescue needs at least 1 EMG bin'}

    log("=" * 60)
    log("[one_to_one_multibin_rescue] 开始相邻 bin 补救同步")
    log(f"  H5: {os.path.basename(h5_path)}")
    for p in emg_bin_paths:
        log(f"  EMG segment: {os.path.basename(p)}")
    log("=" * 60)

    with h5py.File(h5_path, 'r+') as f:
        ds_250hz_name = f"emg{device_id}_250hz_adc"
        if ds_250hz_name not in f:
            return {'status': 'error', 'reason': f'dataset {ds_250hz_name} not found'}
        ds_250hz = f[ds_250hz_name]
        num_frames_250hz = ds_250hz.shape[0]
        if num_frames_250hz == 0:
            return {'status': 'error', 'reason': 'empty_250hz_dataset'}
        data_250hz = ds_250hz[:]
        channels_250hz = data_250hz['channels']
        channel_map, resolved_name = _resolve_channel_map(f, ds_250hz_name, channel_map_name)

    emg_parsers = [EMGBinParser(p).parse() for p in emg_bin_paths]
    segments = []
    used_row_ranges = []
    for parser in emg_parsers:
        row_result = _scan_bin_for_h5_rows(parser, channels_250hz, channel_map)
        matched_rows = int(row_result.get('matched_rows', 0))
        if matched_rows < min_matched_rows:
            log(f"  skip segment {os.path.basename(parser.bin_path)}: matched_rows={matched_rows} < {min_matched_rows}")
            continue
        first_row = int(row_result.get('first_h5_row', 0))
        last_row = int(row_result.get('last_h5_row', first_row))
        overlaps = any(not (last_row < a or first_row > b) for a, b in used_row_ranges)
        if overlaps:
            log(f"  skip overlapping segment {os.path.basename(parser.bin_path)}: rows={first_row}-{last_row}")
            continue
        used_row_ranges.append((first_row, last_row))
        segments.append({'parser': parser, 'row_result': row_result})
        log(f"  segment matched: {os.path.basename(parser.bin_path)} rows={first_row}-{last_row}, matched={matched_rows}")

    segments.sort(key=lambda s: int(s['row_result'].get('first_h5_row', 0)))
    if len(segments) < 2:
        if len(segments) != 1:
            return {'status': 'validation_failed', 'reason': 'could not find any reliable matched bin segment'}
        only_rate = float(segments[0]['row_result'].get('match_rate', 0.0))
        only_first = int(segments[0]['row_result'].get('first_h5_row', 0))
        if only_rate < min_single_segment_coverage or only_first > 100:
            return {'status': 'validation_failed',
                    'reason': f'only one matched segment, coverage={only_rate:.3f} < {min_single_segment_coverage:.2f}'}
        log(f"  partial rescue: only one reliable segment found, coverage={only_rate:.3f}")

    anchor_sd_frame_ids, segment_bases, segment_meta = _build_multibin_rescue_anchors(segments, num_frames_250hz)
    matched_total = int(sum(m['matched_rows'] for m in segment_meta))
    anchored_rows = int(np.count_nonzero(anchor_sd_frame_ids >= 0))
    coverage = anchored_rows / max(1, num_frames_250hz)
    direct_match_rate = matched_total / max(1, num_frames_250hz)

    _ni = 2
    try:
        with h5py.File(h5_path, 'r') as _f:
            _ni = _resolve_num_imus(_f, device_id, imu_bin_paths[0] if imu_bin_paths else None,
                                    manual_num_imus=manual_num_imus)
    except Exception:
        pass
    selected_emg_parsers = [seg['parser'] for seg in segments]
    selected_indices = [emg_bin_paths.index(seg['parser'].bin_path) for seg in segments]

    imu_parsers_all = []
    for p in imu_bin_paths[:len(emg_bin_paths)]:
        imu_parsers_all.append(IMUBinParser(p, num_imus=_ni).parse() if p else None)
    while len(imu_parsers_all) < len(emg_bin_paths):
        imu_parsers_all.append(None)

    imu_parsers = []
    for idx in selected_indices:
        parser = imu_parsers_all[idx] if idx < len(imu_parsers_all) else None
        imu_parsers.append(parser)

    rescue_emg_parser = SyntheticEMGParser(selected_emg_parsers, segment_bases)
    rescue_imu_parser = SyntheticIMUParser(imu_parsers, segment_bases) if any(imu_parsers) else None

    rescue_mode = 'one_to_one_partial_rescue' if len(segments) == 1 else 'one_to_one_multibin_rescue'

    result = _build_and_write_2khz(
        h5_path, rescue_emg_parser, rescue_imu_parser, device_id, channel_map, resolved_name,
        data_250hz, num_frames_250hz, 0, set_synced,
        sync_mode=rescue_mode,
        sync_match_rate=direct_match_rate,
        verify_passed=verify,
        anchor_sd_frame_ids=anchor_sd_frame_ids,
        sync_frame_id_mode='multibin_rescue_adc_rows',
        anchor_position=DOWNSAMPLE_RATIO - 1,
    )

    with h5py.File(h5_path, 'r+') as f:
        f.attrs[f'sync_rescue_segments_dev{device_id}'] = json.dumps(segment_meta, ensure_ascii=False)
        f.attrs[f'sync_rescue_anchored_rows_dev{device_id}'] = int(anchored_rows)
        f.attrs[f'sync_rescue_direct_match_rate_dev{device_id}'] = float(direct_match_rate)
        f.attrs[f'sync_rescue_coverage_dev{device_id}'] = float(coverage)
        if rescue_mode == 'one_to_one_partial_rescue':
            f.attrs[f'sync_rescue_warning_dev{device_id}'] = (
                'partial rescue: only one bin segment matched; uncovered rows are interpolated from H5 250Hz anchors'
            )
        append_sync_history(f, action='sync_rescue', status='synced', details={
            'mode': rescue_mode,
            'segments': segment_meta,
            'anchored_rows': anchored_rows,
            'direct_match_rate': direct_match_rate,
            'coverage': coverage,
        })

    result['rescue_segments'] = segment_meta
    result['match_rate'] = direct_match_rate
    result['coverage'] = coverage
    return result


def sync_h5_one_to_one(h5_path, emg_bin_path, imu_bin_path=None, device_id=1,
                       verify=True, set_synced=True, channel_map_name='V2',
                       manual_num_imus=None):
    """一对一同步：H5 与 bin 一一对应，bin_offset=0，使用 row_index 定位。

    适用于 stream_format_version >= 2 且 bin_pair_source=collection_stream 的新格式 H5。

    Args:
        h5_path: H5 文件路径
        emg_bin_path: EMG bin 文件路径
        imu_bin_path: IMU bin 文件路径
        device_id: 设备 ID
        verify: 是否 ADC 校验
        set_synced: 是否设 sync_status=synced
        channel_map_name: 通道映射
        manual_num_imus: 手动指定 IMU 数量，bin 自动检测失败时启用（None=自动检测）

    Returns:
        dict: 同步结果
    """
    log("=" * 60)
    log(f"[one_to_one] 开始同步 (bin_offset=0)")
    log(f"  H5: {os.path.basename(h5_path)}")
    log(f"  EMG bin: {os.path.basename(emg_bin_path)}")
    log("=" * 60)

    with h5py.File(h5_path, 'r+') as f:
        current_status = f.attrs.get('sync_status', 'unknown')
        if current_status == 'synced':
            log("警告: 文件已同步，跳过")
            return {'status': 'skipped', 'reason': 'already_synced'}

        ds_250hz_name = f"emg{device_id}_250hz_adc"
        if ds_250hz_name not in f:
            return {'status': 'error', 'reason': f'dataset {ds_250hz_name} not found'}

        ds_250hz = f[ds_250hz_name]
        num_frames_250hz = ds_250hz.shape[0]
        if num_frames_250hz == 0:
            return {'status': 'error', 'reason': 'empty_250hz_dataset'}

        data_250hz = ds_250hz[:]
        channels_250hz = data_250hz['channels']
        timestamps_250hz = data_250hz['time']

        channel_map, resolved_name = _resolve_channel_map(f, ds_250hz_name, channel_map_name)
        log(f"通道映射: {resolved_name}")

    parser = EMGBinParser(emg_bin_path).parse()
    _ni = 2
    try:
        with h5py.File(h5_path, 'r') as _f:
            _ni = _resolve_num_imus(_f, device_id, imu_bin_path, manual_num_imus=manual_num_imus)
    except Exception:
        pass
    imu_parser = IMUBinParser(imu_bin_path, num_imus=_ni).parse() if imu_bin_path else None

    bin_offset = 0  # 一对一模式固定 offset=0
    match_rate = None  # 当 verify=False 时保持 None

    # ---- ADC 校验：H5 row i 的 250Hz ADC == bin frame i*8+7 ----
    anchor_sd_frame_ids, frame_id_mode = _get_250hz_anchor_sd_frame_ids(data_250hz, bin_offset)
    anchor_position = int(anchor_sd_frame_ids[0] % DOWNSAMPLE_RATIO) if len(anchor_sd_frame_ids) else DOWNSAMPLE_RATIO - 1
    log(f"[one_to_one] frame_id_mode={frame_id_mode}")

    if verify:
        num_check = min(200, num_frames_250hz)
        check_indices = np.linspace(0, num_frames_250hz - 1, num_check, dtype=int)
        matched = 0
        checked = 0
        mismatch_details = []

        for idx in check_indices:
            sd_fid = int(anchor_sd_frame_ids[idx])
            bin_data = parser.get_frame(sd_fid)
            if bin_data is None:
                continue
            checked += 1
            bin_mapped = map_physical_to_h5_order(bin_data, channel_map)
            if np.all(np.abs(np.array(bin_mapped, dtype=np.int32) - channels_250hz[idx]) <= 1):
                matched += 1
            elif len(mismatch_details) < 5:
                mismatch_details.append({
                    'h5_row': int(idx), 'sd_fid': sd_fid,
                    'h5_ch0': int(channels_250hz[idx][0]),
                    'bin_ch0': int(bin_mapped[0]),
                })

        match_rate = matched / checked if checked > 0 else 0.0
        log(f"ADC 校验: {matched}/{checked} matched, rate={match_rate:.3f}")

        if match_rate < VALIDATION_CONFIG['adc_match_threshold']:
            recovered_anchors, scan_result = _recover_one_to_one_anchors(parser, channels_250hz, channel_map)
            if recovered_anchors is not None:
                anchor_sd_frame_ids = recovered_anchors
                frame_id_mode = 'adc_row_scan'
                anchor_position = int(scan_result['start_sd_frame_id'] % DOWNSAMPLE_RATIO)
                match_rate = float(scan_result.get('match_rate', 0.0))
                checked = int(scan_result.get('matched_rows', 0))
                matched = checked
                log(
                    f"[one_to_one] H5 sd_frame_id 校验失败，已通过 ADC 行扫描恢复: "
                    f"matched_rows={checked}/{num_frames_250hz}, rate={match_rate:.3f}, "
                    f"start_sd={scan_result.get('start_sd_frame_id')}, "
                    f"anchor_position={anchor_position}"
                )
            else:
                scan_rate = float(scan_result.get('match_rate', 0.0)) if scan_result else 0.0
                log(f"[one_to_one] ADC 行扫描未找到可靠锚点: rate={scan_rate:.3f}")

        if match_rate < VALIDATION_CONFIG['adc_match_threshold']:
            with h5py.File(h5_path, 'r+') as f:
                f.attrs['sync_status'] = 'sync_failed'
                f.attrs['sync_time'] = datetime.now().isoformat()
                f.attrs['sync_error'] = f'one_to_one ADC match_rate {match_rate:.3f} < threshold'
                f.attrs['sync_mode'] = 'one_to_one'
                f.attrs[f'sync_bin_offset_dev{device_id}'] = int(bin_offset)
                f.attrs[f'sync_offset_match_rate_dev{device_id}'] = float(match_rate)
                append_sync_history(f, action='sync', status='sync_failed',
                                    details={'mode': 'one_to_one', 'match_rate': match_rate})
            log(f"[FAIL] ADC 校验失败，sync_status=sync_failed")
            return {
                'status': 'validation_failed',
                'reason': f'ADC match_rate {match_rate:.3f} < threshold',
                'match_rate': match_rate, 'checked': checked, 'matched': matched,
                'mismatch_details': mismatch_details,
            }

    # ---- 构建 2kHz ----
    if anchor_sd_frame_ids is not None and np.any(anchor_sd_frame_ids < 0):
        valid_mask = anchor_sd_frame_ids >= 0
        dropped_rows = int(len(anchor_sd_frame_ids) - np.count_nonzero(valid_mask))
        data_250hz = data_250hz[valid_mask]
        anchor_sd_frame_ids = anchor_sd_frame_ids[valid_mask]
        num_frames_250hz = int(len(data_250hz))
        log(f"[one_to_one] 丢弃 {dropped_rows} 行不属于当前 bin 的 250Hz 前缀数据")

    return _build_and_write_2khz(
        h5_path, parser, imu_parser, device_id, channel_map, resolved_name,
        data_250hz, num_frames_250hz, bin_offset, set_synced,
        sync_mode='one_to_one', sync_match_rate=match_rate, verify_passed=verify,
        anchor_sd_frame_ids=anchor_sd_frame_ids, sync_frame_id_mode=frame_id_mode,
        anchor_position=anchor_position,
    )


def _build_and_write_2khz(h5_path, emg_parser, imu_parser, device_id,
                          channel_map, resolved_map_name,
                          data_250hz, num_frames_250hz, bin_offset,
                          set_synced, sync_mode, sync_match_rate=None, verify_passed=True,
                          anchor_sd_frame_ids=None, sync_frame_id_mode='row_index',
                          anchor_position=DOWNSAMPLE_RATIO - 1):
    """构建并写入 2kHz 数据 + IMU 100Hz 数据到 H5（共享逻辑）。

    sync_match_rate: 成功路径写入 sync_offset_match_rate_dev{device_id} 供 UI 展示
    """
    channels_250hz = data_250hz['channels']
    timestamps_250hz = data_250hz['time']

    num_frames_2khz = num_frames_250hz * DOWNSAMPLE_RATIO
    emg_2khz_dtype = np.dtype([
        ("channels", "<i4", (16,)),
        ("sd_frame_id", "<u4"),
        ("time", "<f8")
    ])

    data_2khz = np.empty(num_frames_2khz, dtype=emg_2khz_dtype)
    filled_frames = 0
    missing_frames = 0

    for i in range(num_frames_250hz):
        if anchor_sd_frame_ids is not None:
            sd_base = int(anchor_sd_frame_ids[i]) - int(anchor_position)
        else:
            sd_base = bin_offset + int(i) * DOWNSAMPLE_RATIO
        for j in range(DOWNSAMPLE_RATIO):
            sd_frame_id = sd_base + j
            idx_2khz = i * DOWNSAMPLE_RATIO + j
            bin_data = emg_parser.get_frame(sd_frame_id)
            if bin_data is not None:
                bin_mapped = map_physical_to_h5_order(bin_data, channel_map)
                data_2khz[idx_2khz]['channels'] = np.array(bin_mapped, dtype=np.int32)
                data_2khz[idx_2khz]['sd_frame_id'] = sd_frame_id
                filled_frames += 1
            else:
                if j == int(anchor_position):
                    data_2khz[idx_2khz]['channels'] = channels_250hz[i].astype(np.int32)
                elif idx_2khz > 0:
                    data_2khz[idx_2khz]['channels'] = data_2khz[idx_2khz - 1]['channels']
                else:
                    data_2khz[idx_2khz]['channels'] = np.zeros(16, dtype=np.int32)
                data_2khz[idx_2khz]['sd_frame_id'] = sd_frame_id
                missing_frames += 1

            anchor_time = timestamps_250hz[i]
            data_2khz[idx_2khz]['time'] = anchor_time + (j - int(anchor_position)) / 2000.0

    log(f"2kHz: {filled_frames} from bin, {missing_frames} interpolated")

    with h5py.File(h5_path, 'r+') as f:
        ds_2khz_name = f"emg{device_id}_2khz_adc"
        if ds_2khz_name in f:
            ds_2khz = f[ds_2khz_name]
            ds_2khz.resize(num_frames_2khz, axis=0)
            ds_2khz[:] = data_2khz
        else:
            ds_2khz = f.create_dataset(ds_2khz_name, data=data_2khz, chunks=(1000,), compression="gzip")
        ds_2khz.attrs["lsb_uv"] = emg_parser.lsb_uv
        ds_2khz.attrs["source_bin"] = os.path.basename(emg_parser.bin_path)
        ds_2khz.attrs["sync_time"] = datetime.now().isoformat()
        ds_2khz.attrs["filled_frames"] = filled_frames
        ds_2khz.attrs["missing_frames"] = missing_frames
        ds_2khz.attrs["sample_rate"] = 2000

        # IMU 同步（复用原逻辑）
        imu_result = _sync_imu_100hz(f, emg_parser, imu_parser, data_2khz, device_id)

        # sync attrs (Issue 4: include match_rate)
        if set_synced:
            f.attrs["sync_status"] = "synced"
            f.attrs["sync_time"] = datetime.now().isoformat()
            f.attrs["sync_mode"] = sync_mode
            if "sync_error" in f.attrs:
                del f.attrs["sync_error"]
            f.attrs[f"sync_bin_offset_dev{device_id}"] = int(bin_offset)
            if sync_match_rate is not None:
                f.attrs[f"sync_offset_match_rate_dev{device_id}"] = float(sync_match_rate)
            f.attrs["sync_frame_id_mode"] = sync_frame_id_mode
            f.attrs["sync_bin_offset_mode"] = "none" if bin_offset == 0 else "adc_search"
            f.attrs["sync_time_alignment"] = "anchor_sample"
            f.attrs["sync_250hz_anchor_position"] = int(anchor_position)
            f.attrs["sync_2khz_sample_interval"] = 0.0005
            detail = {'mode': sync_mode, 'offset': bin_offset, 'filled': filled_frames, 'missing': missing_frames}
            if sync_match_rate is not None:
                detail['match_rate'] = float(sync_match_rate)
            append_sync_history(f, action='sync', status='synced', details=detail)

    imu_info = f", IMU: {imu_result.get('imu_frames',0)}f filled={imu_result.get('imu_filled',0)}" if imu_result.get('imu_status') == 'success' else f", IMU: {imu_result.get('imu_status','skipped')}"
    if imu_result.get('imu_status') == 'success':
        active_cnt = imu_result.get('imu_active_count', '?')
        imu_cnt = imu_result.get('imu_count', '?')
        imu_info += f" [{active_cnt}/{imu_cnt}活跃]"
    log(f"[{sync_mode}] 同步完成！2kHz: {filled_frames}f{imu_info}")
    return {
        'status': 'success', 'frames_250hz': num_frames_250hz,
        'frames_2khz': num_frames_2khz, 'filled_frames': filled_frames,
        'missing_frames': missing_frames, 'bin_offset': bin_offset,
        'imu_status': imu_result.get('imu_status', 'skipped'),
        'imu_frames': imu_result.get('imu_frames', 0),
        'imu_filled': imu_result.get('imu_filled', 0),
        'imu_missing': imu_result.get('imu_missing', 0),
        'imu_count': imu_result.get('imu_count', 0),
        'imu_active_count': imu_result.get('imu_active_count', 0),
        'imu_active_indices': imu_result.get('imu_active_indices', []),
        'imu_inactive_indices': imu_result.get('imu_inactive_indices', []),
    }


def _sync_imu_100hz(h5_file, emg_parser, imu_parser, data_2khz, device_id):
    """IMU 100Hz 同步 — 支持 2/3 动态数量 IMU"""
    if imu_parser is None:
        return {'imu_status': 'skipped'}
    num_imus = imu_parser.num_imus
    labels = ['a', 'b', 'c', 'd'][:num_imus]

    emg_sd_frame_ids = data_2khz['sd_frame_id']
    imu_frame_ids_all = emg_sd_frame_ids // EMG_IMU_RATIO
    imu_frame_ids_unique = np.unique(imu_frame_ids_all)
    imu_time_by_frame = {}
    for emg_idx, imu_fid_raw in enumerate(imu_frame_ids_all):
        imu_fid_int = int(imu_fid_raw)
        if imu_fid_int not in imu_time_by_frame:
            imu_time_by_frame[imu_fid_int] = float(data_2khz[emg_idx]['time'])
    num_imu_frames = len(imu_frame_ids_unique)

    imu_100hz_dtype = np.dtype([
        ("acc", "<f4", (3,)), ("gyr", "<f4", (3,)), ("mag", "<f4", (3,)),
        ("sd_frame_id", "<u4"), ("time", "<f8")
    ])

    # 为每个 IMU 分配独立的 data array
    # 用 np.zeros 初始化，避免未初始化内存泄露为垃圾数据
    all_data = [np.zeros(num_imu_frames, dtype=imu_100hz_dtype) for _ in range(num_imus)]
    imu_filled = 0
    imu_missing = 0

    for idx, imu_fid in enumerate(imu_frame_ids_unique):
        imu_fid = int(imu_fid)
        imu_data = imu_parser.frames.get(imu_fid)
        if imu_data is not None:
            for k in range(num_imus):
                if k < len(imu_data):
                    all_data[k][idx]['acc'] = np.array(imu_data[k]['acc'], dtype=np.float32)
                    all_data[k][idx]['gyr'] = np.array(imu_data[k]['gyr'], dtype=np.float32)
                    all_data[k][idx]['mag'] = np.array(imu_data[k]['mag'], dtype=np.float32)
                else:
                    # 防御性：k 超出实际数据范围（bin 帧截断/损坏），填零
                    all_data[k][idx]['acc'] = np.zeros(3, dtype=np.float32)
                    all_data[k][idx]['gyr'] = np.zeros(3, dtype=np.float32)
                    all_data[k][idx]['mag'] = np.zeros(3, dtype=np.float32)
            imu_filled += 1
        else:
            for k in range(num_imus):
                all_data[k][idx]['acc'] = np.zeros(3, dtype=np.float32)
                all_data[k][idx]['gyr'] = np.zeros(3, dtype=np.float32)
                all_data[k][idx]['mag'] = np.zeros(3, dtype=np.float32)
            imu_missing += 1
        for k in range(num_imus):
            all_data[k][idx]['sd_frame_id'] = imu_fid
        t = imu_time_by_frame.get(imu_fid, float(idx) * 0.01)
        for k in range(num_imus):
            all_data[k][idx]['time'] = t

    def write_or_create_dataset(name, data, filled, missing, imu_index=None):
        created = name not in h5_file
        if created:
            dataset = h5_file.create_dataset(name, data=data, chunks=(1000,), compression="gzip")
            log(f"  IMU dataset CREATED: {name}")
        else:
            dataset = h5_file[name]
            dataset.resize(num_imu_frames, axis=0)
            dataset[:] = data
            log(f"  IMU dataset RESIZED: {name}")
        dataset.attrs["sample_rate"] = 100
        dataset.attrs["source_bin"] = os.path.basename(imu_parser.bin_path)
        dataset.attrs["sync_time"] = datetime.now().isoformat()
        dataset.attrs["filled_frames"] = filled
        dataset.attrs["missing_frames"] = missing
        if imu_index is not None:
            dataset.attrs["imu_index"] = imu_index
        dataset.attrs["imu_count"] = num_imus
        return dataset

    for k, label in enumerate(labels):
        ds_name = f"imu{device_id}{label}_100hz"
        write_or_create_dataset(ds_name, all_data[k], imu_filled, imu_missing, imu_index=k)

    # ── 数据质量校验：标记疑似损坏的传感器 ──
    validation = _validate_imu_sensor_data(all_data, num_imus)
    active_count = len(validation['active'])
    inactive_indices = validation['inactive']

    # 对每个传感器打印诊断信息
    for s in validation['sensors']:
        status = '✓ ACTIVE' if s['idx'] in validation['active'] else '⚠️ INACTIVE'
        issue_str = f' ({s["issue"]})' if s['issue'] else ''
        log(f"  IMU sensor {chr(ord('a') + s['idx'])} [{s['idx']}]: {status}{issue_str}")
        log(f"    non_zero_rate={s['non_zero_rate']:.1%}, "
            f"acc_range={s['acc_range']}, gyr_range={s['gyr_range']}")

    if inactive_indices:
        log(f"  ⚠️ 检测到 {len(inactive_indices)} 个疑似损坏传感器: "
            f"{[chr(ord('a') + i) for i in inactive_indices]}")
        # 截断损坏传感器的数据集（避免加载时读到垃圾/全零数据）
        for idx in inactive_indices:
            label = chr(ord('a') + idx)
            ds_name = f"imu{device_id}{label}_100hz"
            if ds_name in h5_file:
                ds_inactive = h5_file[ds_name]
                if ds_inactive.shape[0] > 0:
                    ds_inactive.resize(0, axis=0)
                    log(f"  IMU dataset TRUNCATED (inactive sensor): {ds_name} → 0 rows")

    # 截断超出 num_imus 范围的传感器数据集（旧同步残留清理）
    all_labels = ['a', 'b', 'c', 'd']
    unused_labels = all_labels[num_imus:]
    for label in unused_labels:
        ds_name = f"imu{device_id}{label}_100hz"
        if ds_name in h5_file:
            ds_unused = h5_file[ds_name]
            if ds_unused.shape[0] > 0:
                ds_unused.resize(0, axis=0)
                log(f"  IMU dataset TRUNCATED (unused sensor): {ds_name} → 0 rows")

    # 更新 H5 attrs：记录实际活跃的 IMU 数量
    h5_file.attrs[f'imu{device_id}_active_count'] = active_count
    h5_file.attrs[f'imu{device_id}_active_indices'] = str(validation['active'])

    # legacy single-IMU dataset (keep for old tools)
    legacy_name = f"imu{device_id}_100hz"
    if legacy_name in h5_file:
        write_or_create_dataset(legacy_name, all_data[0], imu_filled, imu_missing)

    return {
        'imu_status': 'success',
        'imu_frames': num_imu_frames,
        'imu_filled': imu_filled,
        'imu_missing': imu_missing,
        'imu_count': num_imus,
        'imu_labels': labels,
        'imu_active_count': active_count,
        'imu_active_indices': validation['active'],
        'imu_inactive_indices': inactive_indices,
    }


def sync_h5_one_to_many_adc_search(h5_path, emg_bin_path, imu_bin_path=None, device_id=1,
                                   verify=True, set_synced=True, channel_map_name='V2',
                                   num_anchors=40, match_threshold=0.95,
                                   manual_num_imus=None):
    """一对多 ADC 搜索同步：通过 ADC 值搜索 H5 在长 bin 中的 offset，然后同步。

    适用于旧格式 H5（多 H5 共享一个长 bin），不依赖 frame_id。

    Args:
        h5_path: H5 文件路径
        emg_bin_path: 长 EMG bin 路径
        imu_bin_path: IMU bin 路径
        device_id: 设备 ID
        verify: 是否 ADC 校验
        set_synced: 是否标记 synced
        channel_map_name: 通道映射
        num_anchors: 搜索锚点数量
        match_threshold: 匹配阈值
        manual_num_imus: 手动指定 IMU 数量，bin 自动检测失败时启用（None=自动检测）

    Returns:
        dict: 同步结果
    """
    log("=" * 60)
    log("[one_to_many_adc_search] 开始 ADC offset 搜索 + 同步")
    log(f"  H5: {os.path.basename(h5_path)}, bin: {os.path.basename(emg_bin_path)}")
    log("=" * 60)

    # Step 1: ADC offset search
    search_result = find_bin_offset_by_adc(
        h5_path, emg_bin_path, device_id, channel_map_name,
        num_anchors=num_anchors, match_threshold=match_threshold,
    )

    if not search_result['found']:
        with h5py.File(h5_path, 'r+') as f:
            f.attrs['sync_status'] = 'sync_failed'
            f.attrs['sync_time'] = datetime.now().isoformat()
            f.attrs['sync_error'] = f'one_to_many ADC offset search failed: {search_result.get("error", "unknown")}'
            f.attrs['sync_mode'] = 'one_to_many_adc_search'
            f.attrs['sync_bin_offset_mode'] = 'adc_search'
            append_sync_history(f, action='sync', status='sync_failed',
                                details={'mode': 'one_to_many_adc_search',
                                         'error': search_result.get('error')})
        log(f"[FAIL] ADC offset search failed: {search_result.get('error')}")
        return {'status': 'sync_failed', 'reason': search_result.get('error'),
                'search_result': search_result}

    bin_offset = search_result['offset']
    log(f"[FOUND] bin_offset={bin_offset}, match_rate={search_result['match_rate']:.3f}")

    # Step 2: Build 2kHz with offset (use channel_map from search result)
    parser = EMGBinParser(emg_bin_path).parse()
    _ni = 2
    try:
        with h5py.File(h5_path, 'r') as _f:
            _ni = _resolve_num_imus(_f, device_id, imu_bin_path, manual_num_imus=manual_num_imus)
    except Exception:
        pass
    imu_parser = IMUBinParser(imu_bin_path, num_imus=_ni).parse() if imu_bin_path else None

    # 使用搜索命中的 channel_map（如 L015 physical），而非 H5 attr 默认 V2
    resolved_cm_name = search_result.get('channel_map_name', channel_map_name)
    actual_cm = CHANNEL_MAPS_BY_NAME.get(resolved_cm_name, CHANNEL_MAPS_BY_NAME.get('V2'))
    # If name is 'physical', actual_cm is None

    with h5py.File(h5_path, 'r') as f:
        ds_name = f"emg{device_id}_250hz_adc"
        ds = f[ds_name]
        num_frames_250hz = ds.shape[0]
        data_250hz = ds[:]

    range_mode = search_result.get('range_mode', 'unknown')
    bin_offset_mode = 'row_signature_span' if range_mode == 'row_signature_span' else 'adc_search'
    log(f"  using channel_map={resolved_cm_name}, range_mode={range_mode}")

    result = _build_and_write_2khz(
        h5_path, parser, imu_parser, device_id, actual_cm, resolved_cm_name,
        data_250hz, num_frames_250hz, bin_offset, set_synced,
        sync_mode='one_to_many_adc_search', sync_match_rate=search_result['match_rate'], verify_passed=True,
    )

    # Write additional search metadata
    with h5py.File(h5_path, 'r+') as f:
        f.attrs['sync_bin_offset_mode'] = bin_offset_mode
        f.attrs['sync_range_mode'] = range_mode
        f.attrs[f'sync_offset_match_rate_dev{device_id}'] = float(search_result['match_rate'])
        f.attrs['sync_frame_id_mode'] = 'row_index'
        f.attrs['sync_adc_search_num_anchors'] = int(num_anchors)
        f.attrs['sync_adc_search_channel_map'] = resolved_cm_name
        if search_result.get('start_sd_frame_id') is not None:
            f.attrs[f'sync_start_sd_frame_id_dev{device_id}'] = int(search_result['start_sd_frame_id'])
            f.attrs[f'sync_end_sd_frame_id_dev{device_id}'] = int(search_result['end_sd_frame_id'])

    result['offset'] = bin_offset
    result['match_rate'] = search_result['match_rate']
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

            # 【新增】IMU数量选择
            opt_layout.addWidget(QLabel("IMU数量:"))
            self.num_imus_spin = QSpinBox()
            self.num_imus_spin.setMinimum(1)
            self.num_imus_spin.setMaximum(4)
            self.num_imus_spin.setValue(3)  # 默认3个IMU
            self.num_imus_spin.setToolTip("bin文件中IMU芯片的数量（1-4个，默认3个）")
            opt_layout.addWidget(self.num_imus_spin)

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

            self.log(f"\n{'='*60}")
            self.log(f"开始批量同步 ({len(self.h5_paths)} 个文件)")
            self.log(f"设备: {device_id}, 校验: {verify}")
            self.log(f"{'='*60}\n")

            # 【IMU数量解析】始终交由 _resolve_num_imus 做多源融合（bin帧ID验证 + BLE实测 + BLE握手）
            # GUI spinbox 仅作显示参考，不参与决策。需要手动覆盖请用 CLI --num-imus 参数
            resolved_num_imus = None
            self.log(f"  IMU数量: 交由多源融合自动裁决（bin帧ID验证 + BLE实测 + BLE握手）")
            if self.imu_bin_path:
                detected = _detect_num_imus_from_bin(self.imu_bin_path)
                if detected is not None:
                    self.log(f"  bin结构预检测: {detected}（供参考，以融合结果为准）")
                else:
                    self.log(f"  bin结构预检测失败")
            else:
                self.log(f"  无IMU bin文件，由BLE数据推断")
            self.log(f"  同步时将启动多源融合裁决（bin帧ID验证 + BLE实测 + BLE握手）")
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
                    # 【IMU数量】仅当用户手动覆盖时预写入 H5 attrs
                    # 自动检测模式下，_resolve_num_imus 会在 sync 内部查询 BLE 数据
                    if resolved_num_imus is not None:
                        with h5py.File(h5_path, 'r+') as f:
                            dev_attr = f'imu{device_id}_num_imus'
                            if dev_attr not in f.attrs:
                                f.attrs[dev_attr] = resolved_num_imus
                                self.log(f"  设置 IMU 数量: {resolved_num_imus} (用户手动指定)")
                            else:
                                existing_val = int(f.attrs[dev_attr])
                                self.log(f"  手动覆盖 IMU 数量: {existing_val} → {resolved_num_imus}")
                                f.attrs[dev_attr] = resolved_num_imus
                            f.attrs['num_imus'] = resolved_num_imus

                    result = sync_h5_with_bin(
                        h5_path,
                        self.emg_bin_path,
                        self.imu_bin_path,
                        device_id=device_id,
                        verify=verify,
                        manual_num_imus=resolved_num_imus,
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
    parser.add_argument("--num-imus", type=int, default=None, choices=[1, 2, 3, 4],
                        help="手动指定 IMU 芯片数量 (默认: 自动检测 bin 文件)")
    parser.add_argument("--channel-map", type=str, default="V2", choices=["V1", "V2", "physical"],
                        help="通道映射模式 (默认 V2，V1/V2 将 bin 物理顺序映射到 H5 显示顺序)")
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
        verify=not args.no_verify,
        channel_map_name=args.channel_map,
        manual_num_imus=args.num_imus,
    )

    if result['status'] == 'success':
        log("同步成功！")
        sys.exit(0)
    else:
        log(f"同步失败: {result.get('reason', 'unknown')}")
        sys.exit(1)


# ===================== Phase 1: 只读诊断 =====================

def diagnose_frame_ids(h5_path):
    """只读诊断 H5 中 frame_id 健康状态 + 同步产物

    Returns:
        dict: {
            'emg1': { 'count', 'has_2khz', 'frame_ids': [], 'duplicates', 'gap_count',
                      'is_monotonic', 'overlap_ratio', 'risk', 'risk_reason' },
            'emg2': { ... },
            'sync_status', 'has_2khz_any', 'sync_attrs_present', 'bin_dev1', 'bin_dev2'
        }
    """
    result = {'emg1': {}, 'emg2': {}, 'sync_status': 'unknown',
              'has_2khz_any': False, 'sync_attrs_present': [], 'bin_dev1': None, 'bin_dev2': None}

    try:
        with h5py.File(h5_path, 'r') as f:
            result['sync_status'] = f.attrs.get('sync_status', 'unknown')
            if isinstance(result['sync_status'], bytes):
                result['sync_status'] = result['sync_status'].decode('utf-8')

            result['bin_dev1'] = None
            result['bin_dev2'] = None
            for dev_label, attr_key in [('bin_dev1', 'sd_bin_dev1'), ('bin_dev2', 'sd_bin_dev2')]:
                val = f.attrs.get(attr_key)
                if isinstance(val, bytes):
                    val = val.decode('utf-8')
                result[dev_label] = val

            present = []
            for ak in ('sync_time', 'sync_error', 'sync_validation_report', 'channel_map_name'):
                if ak in f.attrs:
                    present.append(ak)
            result['sync_attrs_present'] = present

            for dev_id, ds_250hz in [('emg1', 'emg1_250hz_adc'), ('emg2', 'emg2_250hz_adc')]:
                diag = {'count': 0, 'has_2khz': False, 'frame_ids': None, 'duplicates': 0,
                        'gap_count': 0, 'is_monotonic': True, 'overlap_ratio': 0.0,
                        'risk': 'ok', 'risk_reason': ''}

                ds_2khz = f'{dev_id}_2khz_adc'
                diag['has_2khz'] = ds_2khz in f
                if diag['has_2khz']:
                    result['has_2khz_any'] = True

                if ds_250hz in f:
                    data = f[ds_250hz][:]
                    diag['count'] = len(data)
                    if diag['count'] > 0:
                        frame_ids = data['frame_id'].astype(np.int64)
                        diag['frame_ids_sample'] = [int(frame_ids[0]), int(frame_ids[-1]),
                                                    int(frame_ids[min(5, len(frame_ids)-1)])]
                        # duplicates
                        unique = len(set(frame_ids))
                        diag['duplicates'] = int(diag['count']) - unique
                        # gaps / monotonic — 与 validate_frame_ids 标准一致
                        # frame_id 是帧级 ID，正常 diff=1；diff>1=gap，diff<=0=非单调/重复
                        diffs = np.diff(frame_ids)
                        diag['is_monotonic'] = bool(np.all(diffs >= 0))
                        diag['is_strictly_increasing'] = bool(np.all(diffs > 0))
                        diag['gap_count'] = int(np.sum(diffs > 1))
                        non_increasing = int(np.sum(diffs <= 0))
                        # overlap ratio (frame_id range / expected count * DOWNSAMPLE_RATIO)
                        expected = int(diag['count']) * 8
                        actual_range = int(frame_ids[-1] - frame_ids[0] + 1) if diag['count'] > 1 else 1
                        diag['overlap_ratio'] = round(actual_range / expected, 4) if expected > 0 else 1.0

                        # risk assessment (与 validate_frame_ids 标准一致)
                        dup_ratio = diag['duplicates'] / max(diag['count'], 1)
                        if diag['duplicates'] > 2 and diag['overlap_ratio'] < 0.3 and dup_ratio > 0.05:
                            diag['risk'] = 'high'
                            diag['risk_reason'] = (f"疑似旧bug: duplicate={diag['duplicates']}, "
                                                   f"dup_ratio={dup_ratio:.1%}, overlap={diag['overlap_ratio']}")
                        elif diag['duplicates'] > 0:
                            diag['risk'] = 'medium'
                            diag['risk_reason'] = f"frame_id 重复 {diag['duplicates']} 个，新版同步会拒绝"
                        elif diag['gap_count'] > 0:
                            diag['risk'] = 'medium'
                            diag['risk_reason'] = f"frame_id gap {diag['gap_count']} 处，新版同步会拒绝"
                        elif not diag['is_strictly_increasing']:
                            diag['risk'] = 'medium'
                            diag['risk_reason'] = 'frame_id 非严格递增，新版同步会拒绝'

                result[dev_id] = diag
    except Exception as e:
        result['error'] = str(e)

    return result


def clear_sync_outputs(h5_path, backup=True):
    """Phase 2: 清除 H5 中旧同步产物（2kHz datasets + sync attrs），保留 250Hz 原始数据

    Args:
        h5_path: H5 文件路径
        backup: 是否在同目录创建 .bak 备份

    Returns:
        dict: { 'success', 'backup_path', 'removed_datasets', 'removed_attrs', 'errors' }
    """
    removed_datasets = []
    removed_attrs = []
    errors = []
    backup_path = None

    # backup
    if backup:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = h5_path + f'.bak_{ts}'
        try:
            shutil.copy2(h5_path, backup_path)
            log(f"已备份: {backup_path}")
        except Exception as e:
            errors.append(f"备份失败: {e}")
            return {'success': False, 'backup_path': None, 'removed_datasets': [],
                    'removed_attrs': [], 'errors': errors}

    try:
        with h5py.File(h5_path, 'a') as f:
            # 2kHz/100Hz sync datasets to remove (new + legacy names)
            sync_datasets = [
                'emg1_2khz_adc', 'emg2_2khz_adc',
                'imu1a_100hz', 'imu1b_100hz', 'imu1c_100hz',
                'imu2a_100hz', 'imu2b_100hz', 'imu2c_100hz',
                'imu1_100hz', 'imu2_100hz',  # legacy single-IMU names
            ]
            for ds_name in sync_datasets:
                if ds_name in f:
                    del f[ds_name]
                    removed_datasets.append(ds_name)
                    log(f"  已删除 dataset: {ds_name}")

            # sync attrs to clear (including new mode/offset attrs)
            sync_attrs = ['sync_status', 'sync_time', 'sync_error', 'sync_validation_report',
                          'sync_mode', 'sync_frame_id_mode', 'sync_bin_offset_mode',
                          'sync_adc_search_num_anchors', 'sync_adc_search_channel_map']
            # also clear per-device offset/match attrs
            for dev_id in [1, 2]:
                sync_attrs.append(f'sync_bin_offset_dev{dev_id}')
                sync_attrs.append(f'sync_offset_match_rate_dev{dev_id}')
            for ak in sync_attrs:
                if ak in f.attrs:
                    del f.attrs[ak]
                    removed_attrs.append(ak)

            # reset sync_status to pending
            f.attrs['sync_status'] = 'pending'
            log(f"  sync_status 已重置为 pending")

            # audit
            append_sync_history(f, action='clear', status='success',
                                details={'backup_path': backup_path,
                                         'removed_datasets': removed_datasets,
                                         'removed_attrs': removed_attrs})

    except Exception as e:
        errors.append(f"清除失败: {e}")

    if len(errors) == 0:
        # success already recorded inside the try block
        pass
    else:
        # record failure (backup or clear failed)
        try:
            append_sync_history(h5_path, action='clear', status='error',
                                details={'errors': errors})
        except Exception:
            pass

    return {
        'success': len(errors) == 0,
        'backup_path': backup_path,
        'removed_datasets': removed_datasets,
        'removed_attrs': removed_attrs,
        'errors': errors,
    }


# ===================== sync history audit =====================

def append_sync_history(h5_file_or_path, action, status, details=None, max_entries=20):
    """Phase 6: 在 H5 attrs 中追加同步审计记录

    Args:
        h5_file_or_path: h5py.File 对象或 H5 路径字符串
        action: 'sync' | 'resync' | 'clear'
        status: 'started' | 'synced' | 'success' | 'sync_failed' | 'error' | 'exception'
        details: dict of extra info
        max_entries: 最多保留条数
    """
    now = datetime.now().isoformat()
    entry = {'time': now, 'action': action, 'status': status, 'details': details or {}}

    def _apply(f):
        # read existing
        history = []
        raw = f.attrs.get('sync_history')
        if raw:
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8')
                history = json.loads(raw)
                if not isinstance(history, list):
                    history = []
            except (json.JSONDecodeError, TypeError):
                history = []

        history.append(entry)
        if len(history) > max_entries:
            history = history[-max_entries:]

        # convert numpy types to Python native for JSON serialization
        def _to_native(obj):
            if isinstance(obj, dict):
                return {k: _to_native(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_to_native(v) for v in obj]
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, (np.ndarray,)):
                return _to_native(obj.tolist())
            return obj
        history_native = _to_native(history)

        f.attrs['sync_history'] = json.dumps(history_native, ensure_ascii=False)

        # convenience counters
        attempt_count = f.attrs.get('sync_attempt_count', 0)
        clear_count = f.attrs.get('sync_clear_count', 0)

        if action == 'clear':
            clear_count += 1
            f.attrs['last_sync_clear_time'] = now
        if action in ('sync', 'resync'):
            attempt_count += 1
            f.attrs['last_sync_attempt_time'] = now
        if status in ('synced', 'success'):
            f.attrs['last_sync_success_time'] = now
        if status in ('sync_failed', 'error', 'exception'):
            f.attrs['last_sync_error_time'] = now

        f.attrs['sync_attempt_count'] = attempt_count
        f.attrs['sync_clear_count'] = clear_count

    if isinstance(h5_file_or_path, h5py.File):
        _apply(h5_file_or_path)
    else:
        with h5py.File(h5_file_or_path, 'a') as f:
            _apply(f)


# ===================== CLI =====================

if __name__ == "__main__":
    main()
