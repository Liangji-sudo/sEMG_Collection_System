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

# IMU帧大小：4字节帧号 + 36字节数据 = 40字节
IMU_FRAME_SIZE = 4 + 36

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
SCALE_ACCEL = 16.0 / 32768.0
SCALE_GYRO = 2000.0 / 32768.0
SCALE_MAG = 0.15


def log(message):
    """打印日志"""
    print(f"[bin_sync_tool] {message}")


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
    """IMU bin文件解析器"""

    def __init__(self, bin_path):
        self.bin_path = bin_path
        self.sample_rate = 0
        self.timestamp_str = ""
        self.frames = {}  # {frame_id: (imu1_data, imu2_data)}
        self.frame_count = 0

    def parse(self):
        """解析bin文件"""
        file_size = os.path.getsize(self.bin_path)
        if file_size < HEADER_SIZE:
            raise ValueError(f"文件太小: {file_size} bytes")

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

            log(f"IMU文件信息: 采样率={self.sample_rate}Hz")
            log(f"时间戳: {self.timestamp_str}")

            # 读取所有帧
            while True:
                chunk = f.read(IMU_FRAME_SIZE)
                if len(chunk) < IMU_FRAME_SIZE:
                    break

                frame_id = struct.unpack('<I', chunk[0:4])[0]
                raw_data = chunk[4:]

                # 解析IMU数据
                def parse_chip(b):
                    ag = struct.unpack('>6h', b[0:12])
                    m = struct.unpack('<3h', b[12:18])
                    return {
                        'acc': [x * SCALE_ACCEL for x in ag[0:3]],
                        'gyr': [x * SCALE_GYRO for x in ag[3:6]],
                        'mag': [x * SCALE_MAG for x in m[0:3]]
                    }

                imu1 = parse_chip(raw_data[0:18])
                imu2 = parse_chip(raw_data[18:36])

                self.frames[frame_id] = (imu1, imu2)
                self.frame_count += 1

        log(f"解析完成: 共 {self.frame_count} 帧")
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
    imu_parser = IMUBinParser(imu_bin_path).parse() if imu_bin_path else None

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
        # == IMU 100Hz 同步：利用 EMG 2kHz SD帧号作为锚点 ==
        # ============================================================
        imu_result = {'imu_status': 'skipped'}

        if imu_parser is not None:
            log("正在同步IMU 100Hz数据...")

            # 从EMG 2kHz数据中提取所有SD帧号，映射到IMU帧号
            emg_sd_frame_ids = data_2khz['sd_frame_id']
            imu_frame_ids_all = emg_sd_frame_ids // EMG_IMU_RATIO  # EMG帧号/20 = IMU帧号

            # 去重并排序，得到需要的IMU帧号列表
            imu_frame_ids_unique = np.unique(imu_frame_ids_all)
            num_imu_frames = len(imu_frame_ids_unique)

            log(f"EMG SD帧号范围: [{emg_sd_frame_ids[0]}, {emg_sd_frame_ids[-1]}]")
            log(f"对应IMU帧号范围: [{imu_frame_ids_unique[0]}, {imu_frame_ids_unique[-1]}], 共 {num_imu_frames} 帧")

            # 构建IMU 100Hz数据
            imu_100hz_dtype = np.dtype([
                ("acc", "<f4", (3,)),
                ("gyr", "<f4", (3,)),
                ("mag", "<f4", (3,)),
                ("sd_frame_id", "<u4"),
                ("time", "<f8")
            ])

            data_imu_100hz = np.empty(num_imu_frames, dtype=imu_100hz_dtype)
            imu_filled = 0
            imu_missing = 0

            for idx, imu_fid in enumerate(imu_frame_ids_unique):
                imu_fid = int(imu_fid)
                imu_data = imu_parser.frames.get(imu_fid)

                if imu_data is not None:
                    imu1, imu2 = imu_data
                    # 使用第一个IMU芯片的数据（与BLE上报一致）
                    data_imu_100hz[idx]['acc'] = np.array(imu1['acc'], dtype=np.float32)
                    data_imu_100hz[idx]['gyr'] = np.array(imu1['gyr'], dtype=np.float32)
                    data_imu_100hz[idx]['mag'] = np.array(imu1['mag'], dtype=np.float32)
                    imu_filled += 1
                else:
                    # IMU帧丢失，用零填充
                    data_imu_100hz[idx]['acc'] = np.zeros(3, dtype=np.float32)
                    data_imu_100hz[idx]['gyr'] = np.zeros(3, dtype=np.float32)
                    data_imu_100hz[idx]['mag'] = np.zeros(3, dtype=np.float32)
                    imu_missing += 1

                data_imu_100hz[idx]['sd_frame_id'] = imu_fid

                # 插值时间戳：根据EMG时间戳推算
                # 找到对应EMG帧的时间戳（该IMU帧对应的第一个EMG帧）
                emg_idx = idx * EMG_IMU_RATIO
                if emg_idx < len(data_2khz):
                    data_imu_100hz[idx]['time'] = data_2khz[emg_idx]['time']
                elif len(data_2khz) > 0:
                    data_imu_100hz[idx]['time'] = data_2khz[-1]['time'] + (idx - num_imu_frames + 1) * 0.01
                else:
                    data_imu_100hz[idx]['time'] = idx * 0.01

            log(f"IMU 100Hz数据构建完成: {imu_filled} 帧来自bin, {imu_missing} 帧缺失填零")

            # 【修改】写入 2 个 IMU 100Hz 数据集（每设备有 2 个 IMU 传感器：a 和 b）
            # imu1a_100hz / imu1b_100hz 或 imu2a_100hz / imu2b_100hz
            ds_imu_a_name = f"imu{device_id}a_100hz"
            ds_imu_b_name = f"imu{device_id}b_100hz"
            ds_imu_name_legacy = f"imu{device_id}_100hz"  # 兼容旧版单一数据集

            # 构建 IMU_B 的数据（与 IMU_A 类似，但使用 imu2 的数据）
            data_imu_b_100hz = np.empty(num_imu_frames, dtype=imu_100hz_dtype)
            imu_b_filled = 0
            imu_b_missing = 0

            for idx, imu_fid in enumerate(imu_frame_ids_unique):
                imu_fid = int(imu_fid)
                imu_data = imu_parser.frames.get(imu_fid)

                if imu_data is not None:
                    imu1, imu2 = imu_data
                    # IMU_B 使用第二个 IMU 芯片的数据
                    data_imu_b_100hz[idx]['acc'] = np.array(imu2['acc'], dtype=np.float32)
                    data_imu_b_100hz[idx]['gyr'] = np.array(imu2['gyr'], dtype=np.float32)
                    data_imu_b_100hz[idx]['mag'] = np.array(imu2['mag'], dtype=np.float32)
                    imu_b_filled += 1
                else:
                    data_imu_b_100hz[idx]['acc'] = np.zeros(3, dtype=np.float32)
                    data_imu_b_100hz[idx]['gyr'] = np.zeros(3, dtype=np.float32)
                    data_imu_b_100hz[idx]['mag'] = np.zeros(3, dtype=np.float32)
                    imu_b_missing += 1

                data_imu_b_100hz[idx]['sd_frame_id'] = imu_fid
                data_imu_b_100hz[idx]['time'] = data_imu_100hz[idx]['time']  # 使用相同的时间戳

            # 写入 IMU_A 数据集
            if ds_imu_a_name in f:
                ds_imu_a = f[ds_imu_a_name]
                ds_imu_a.resize(num_imu_frames, axis=0)
                ds_imu_a[:] = data_imu_100hz
                ds_imu_a.attrs["sample_rate"] = 100
                ds_imu_a.attrs["source_bin"] = os.path.basename(imu_bin_path)
                ds_imu_a.attrs["sync_time"] = datetime.now().isoformat()
                ds_imu_a.attrs["filled_frames"] = imu_filled
                ds_imu_a.attrs["missing_frames"] = imu_missing
                log(f"IMU_A 同步完成！100Hz数据已写入 {ds_imu_a_name}")
            elif ds_imu_name_legacy in f:
                # 兼容旧版：如果只有旧版数据集，则只写入 IMU_A
                ds_imu = f[ds_imu_name_legacy]
                ds_imu.resize(num_imu_frames, axis=0)
                ds_imu[:] = data_imu_100hz
                ds_imu.attrs["sample_rate"] = 100
                ds_imu.attrs["source_bin"] = os.path.basename(imu_bin_path)
                ds_imu.attrs["sync_time"] = datetime.now().isoformat()
                ds_imu.attrs["filled_frames"] = imu_filled
                ds_imu.attrs["missing_frames"] = imu_missing
                log(f"使用旧版数据集名 {ds_imu_name_legacy}")
            else:
                log(f"警告: 数据集 {ds_imu_a_name} 不存在，跳过 IMU_A 写入")

            # 写入 IMU_B 数据集
            if ds_imu_b_name in f:
                ds_imu_b = f[ds_imu_b_name]
                ds_imu_b.resize(num_imu_frames, axis=0)
                ds_imu_b[:] = data_imu_b_100hz
                ds_imu_b.attrs["sample_rate"] = 100
                ds_imu_b.attrs["source_bin"] = os.path.basename(imu_bin_path)
                ds_imu_b.attrs["sync_time"] = datetime.now().isoformat()
                ds_imu_b.attrs["filled_frames"] = imu_b_filled
                ds_imu_b.attrs["missing_frames"] = imu_b_missing
                log(f"IMU_B 同步完成！100Hz数据已写入 {ds_imu_b_name}")
            else:
                log(f"数据集 {ds_imu_b_name} 不存在，跳过 IMU_B 写入")

            log(f"IMU同步完成！A: {imu_filled}帧, B: {imu_b_filled}帧")
            imu_result = {
                'imu_status': 'success',
                'imu_frames': num_imu_frames,
                'imu_filled': imu_filled,
                'imu_missing': imu_missing,
                'imu_b_filled': imu_b_filled,
                'imu_b_missing': imu_b_missing
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
