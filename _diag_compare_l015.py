#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_diag_compare_l015.py — 只读诊断脚本

比较 L015 H5 2kHz/250Hz 数据与对应 raw bin 数据的数值一致性。

用法:
    python _diag_compare_l015.py

不修改任何文件，只打印统计信息和生成对比图到 docs/ 目录。
"""

import os, sys, struct
import numpy as np
import h5py

# ── 路径配置 ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # project root
H5_FILE = os.path.join(BASE_DIR, 'L015_h5',
    'L015_坐姿_(连续手势1)_坐姿_食指角度_session1_20260527_153138.h5')
BIN_L = os.path.join(BASE_DIR, 'L015_bin', 'L015_L_260527_153015_emg.bin')
BIN_R = os.path.join(BASE_DIR, 'L015_bin', 'L015_R_260527_153015_emg.bin')

# ── 参数 ──
COMPARE_SECONDS = 5        # 比较前 N 秒
GAIN = 12
HW_GAIN = 10

# 两个 LSB 值
LSB_CALIBRATE = 0.2861     # calibrate_tool 使用的
LSB_VENDOR = 0.476837      # 供应商 / bin_sync_tool 使用的


def parse_bin_header(bin_path):
    """解析 EMG bin 文件头"""
    with open(bin_path, 'rb') as f:
        header = f.read(126)
        magic, sample_rate, gain_idx, bit_depth, imu_en, ts_bytes = struct.unpack(
            '<I H B B B 32s', header[:41])
        gain_map = [1, 2, 3, 4, 6, 8, 12]
        actual_gain = gain_map[gain_idx] if gain_idx < len(gain_map) else 12
        return {
            'magic': magic,
            'sample_rate': sample_rate,
            'gain': actual_gain,
            'bit_depth': bit_depth,
            'imu_en': imu_en,
            'timestamp': ts_bytes.decode('utf-8').strip('\x00'),
        }


def read_bin_raw(bin_path, max_frames=None):
    """读取 bin 文件的 raw ADC 值，返回 (n_frames, 16) numpy array"""
    info = parse_bin_header(bin_path)
    bps = 3 if info['bit_depth'] == 24 else 2
    frame_size = 4 + 16 * bps

    frames = []
    with open(bin_path, 'rb') as f:
        f.seek(126)  # skip header
        while True:
            chunk = f.read(frame_size)
            if len(chunk) < frame_size:
                break
            # frame_id = struct.unpack('<I', chunk[0:4])[0]
            raw = chunk[4:]
            vals = []
            for i in range(16):
                start = i * bps
                val = int.from_bytes(raw[start:start + bps], 'big', signed=True)
                vals.append(val)
            frames.append(vals)
            if max_frames and len(frames) >= max_frames:
                break

    return np.array(frames, dtype=np.int32), info


def main():
    print('=' * 70)
    print('L015 H5 vs Bin 数值对比诊断')
    print('=' * 70)

    # ── 1. 检查 H5 ──
    if not os.path.exists(H5_FILE):
        print(f'[ERROR] H5 文件不存在: {H5_FILE}')
        return
    print(f'\n[H5] {os.path.basename(H5_FILE)}')

    with h5py.File(H5_FILE, 'r') as f:
        # 打印 H5 attrs
        print('\n--- H5 Attrs ---')
        for k in ('sync_status', 'sync_mode', 'sync_time_alignment',
                  'sync_range_mode', 'sync_adc_search_channel_map',
                  'stream_format_version', 'channel_map_name'):
            v = f.attrs.get(k)
            if v is not None:
                if isinstance(v, bytes):
                    v = v.decode('utf-8')
                print(f'  {k}: {v}')

        # 检查 EMG datasets
        for ds_name in ('emg1_250hz_adc', 'emg1_2khz_adc',
                        'emg2_250hz_adc', 'emg2_2khz_adc'):
            if ds_name in f:
                ds = f[ds_name]
                print(f'\n--- {ds_name} ---')
                print(f'  shape: {ds.shape}')
                print(f'  dtype: {ds.dtype}')
                if ds.dtype.names:
                    print(f'  fields: {list(ds.dtype.names)}')
                for ak in ('lsb_uv', 'sample_rate', 'data_type', 'source_bin',
                           'sync_match_rate', 'filled_frames', 'missing_frames'):
                    av = ds.attrs.get(ak)
                    if av is not None:
                        print(f'  attr {ak}: {av}')

                # 前 5s 统计
                sr = ds.attrs.get('sample_rate', 2000)
                if isinstance(sr, np.ndarray):
                    sr = int(sr)
                n_samples = min(int(COMPARE_SECONDS * sr), ds.shape[0])
                raw_data = ds[:n_samples]

                if raw_data.dtype.names and 'channels' in raw_data.dtype.names:
                    ch_data = raw_data['channels']
                elif raw_data.dtype.names and 'ch' in str(raw_data.dtype.names).lower():
                    for fn in raw_data.dtype.names:
                        if 'ch' in fn.lower():
                            ch_data = raw_data[fn]
                            break
                else:
                    ch_data = raw_data

                ch_data = np.array(ch_data)
                print(f'\n  First {COMPARE_SECONDS}s ({n_samples} frames) stats (raw ADC):')
                print(f'    min: {ch_data.min():.0f}, max: {ch_data.max():.0f}')
                print(f'    mean: {ch_data.mean():.1f}, std: {ch_data.std():.1f}')
                print(f'    median: {np.median(ch_data):.1f}')

                # 按通道统计
                if ch_data.ndim == 2 and ch_data.shape[1] >= 16:
                    for ch in range(16):
                        ch_vals = ch_data[:, ch]
                        print(f'    CH{ch:2d}: min={ch_vals.min():6.0f}  max={ch_vals.max():6.0f}  '
                              f'std={ch_vals.std():7.1f}  mean={ch_vals.mean():7.1f}')

                # uV 转换
                ds_lsb = ds.attrs.get('lsb_uv')
                if ds_lsb is not None:
                    if isinstance(ds_lsb, np.ndarray):
                        ds_lsb = float(ds_lsb)
                    print(f'\n  Using H5 attr lsb_uv: {ds_lsb:.6f}')
                else:
                    ds_lsb = LSB_VENDOR / (GAIN * HW_GAIN)
                    print(f'\n  No lsb_uv attr, using vendor default: {ds_lsb:.6f}')
                print(f'  calibrate_tool LSB: {LSB_CALIBRATE / (GAIN * HW_GAIN):.6f}')
                print(f'  Ratio (H5/vendor): {ds_lsb / (LSB_VENDOR / (GAIN * HW_GAIN)):.3f}')
                print(f'  Ratio (calibrate/vendor): {(LSB_CALIBRATE / (GAIN * HW_GAIN)) / (LSB_VENDOR / (GAIN * HW_GAIN)):.3f}')

                # uV range
                ch_data_uv_vendor = ch_data * (LSB_VENDOR / (GAIN * HW_GAIN))
                ch_data_uv_calib = ch_data * (LSB_CALIBRATE / (GAIN * HW_GAIN))
                print(f'\n  uV range (vendor LSB): {ch_data_uv_vendor.min():.1f} ~ {ch_data_uv_vendor.max():.1f}')
                print(f'  uV range (calib  LSB): {ch_data_uv_calib.min():.1f} ~ {ch_data_uv_calib.max():.1f}')
                print(f'  Ratio: {ch_data_uv_calib.max() / max(abs(ch_data_uv_vendor.max()), 1):.3f}')

    # ── 2. 检查 Bin ──
    for label, bin_path in [('L (Left)', BIN_L), ('R (Right)', BIN_R)]:
        if not os.path.exists(bin_path):
            print(f'\n[Bin {label}] 文件不存在: {bin_path}')
            continue

        print(f'\n--- Bin {label}: {os.path.basename(bin_path)} ---')
        info = parse_bin_header(bin_path)
        sr = info['sample_rate']
        n_frames = int(COMPARE_SECONDS * sr)
        raw_adc, _ = read_bin_raw(bin_path, max_frames=n_frames)

        print(f'  Sample rate: {sr} Hz')
        print(f'  Gain: {info["gain"]}')
        print(f'  Bit depth: {info["bit_depth"]}-bit')
        print(f'  LSB (vendor): {LSB_VENDOR / (info["gain"] * HW_GAIN):.6f}')
        print(f'  First {COMPARE_SECONDS}s ({raw_adc.shape[0]} frames):')
        print(f'    min: {raw_adc.min():.0f}, max: {raw_adc.max():.0f}')
        print(f'    mean: {raw_adc.mean():.1f}, std: {raw_adc.std():.1f}')

        for ch in range(16):
            ch_vals = raw_adc[:, ch]
            print(f'    CH{ch:2d}: min={ch_vals.min():6.0f}  max={ch_vals.max():6.0f}  '
                  f'std={ch_vals.std():7.1f}  mean={ch_vals.mean():7.1f}')

    # ── 3. H5 2kHz vs Bin 逐点比较 ──
    print('\n' + '=' * 70)
    print('H5 2kHz ADC vs Bin raw ADC 逐点比较')
    print('=' * 70)

    with h5py.File(H5_FILE, 'r') as f:
        for h5_ds, bin_path, dev_label in [
            ('emg1_2khz_adc', BIN_L, 'emg1/Left'),
            ('emg2_2khz_adc', BIN_R, 'emg2/Right'),
        ]:
            if h5_ds not in f:
                print(f'\n[{dev_label}] {h5_ds} 不存在，跳过')
                continue
            if not os.path.exists(bin_path):
                print(f'\n[{dev_label}] bin 不存在，跳过')
                continue

            print(f'\n[{dev_label}] 比较 {h5_ds} vs {os.path.basename(bin_path)}')

            ds = f[h5_ds]
            bin_info = parse_bin_header(bin_path)
            bin_sr = bin_info['sample_rate']

            # 取前 10 秒 (20000 frames @2kHz)
            h5_n = min(20000, ds.shape[0])
            bin_n = min(int(10 * bin_sr), h5_n)  # 忽略 10s 后的差异

            h5_raw = ds[:h5_n]
            if h5_raw.dtype.names and 'channels' in h5_raw.dtype.names:
                h5_data = np.array(h5_raw['channels'])
            else:
                h5_data = np.array(h5_raw)

            bin_data, _ = read_bin_raw(bin_path, max_frames=bin_n)

            # 对齐长度
            min_len = min(len(h5_data), len(bin_data))
            h5_data = h5_data[:min_len]
            bin_data = bin_data[:min_len]

            print(f'  比较帧数: {min_len}')
            print(f'  H5 channels shape: {h5_data.shape}')
            print(f'  Bin channels shape: {bin_data.shape}')

            # 逐通道比较
            match_count = 0
            total_count = 0
            max_diff = 0
            diffs_by_ch = []

            for ch in range(16):
                h5_ch = h5_data[:, ch]
                bin_ch = bin_data[:, ch]
                diff = np.abs(h5_ch.astype(np.int64) - bin_ch.astype(np.int64))
                ch_match = int(np.sum(diff == 0))
                ch_max_diff = int(diff.max())
                match_count += ch_match
                total_count += len(diff)
                max_diff = max(max_diff, ch_max_diff)
                diffs_by_ch.append((ch, ch_match, len(diff), ch_max_diff))

            match_rate = match_count / total_count if total_count > 0 else 0
            print(f'\n  总匹配率: {match_count}/{total_count} = {match_rate:.4%}')
            print(f'  最大差异: {max_diff} LSB')
            print(f'  逐通道匹配:')
            for ch, m, t, md in diffs_by_ch:
                rate = m / t if t > 0 else 0
                flag = ' ✓' if rate > 0.999 else (' ⚠' if rate > 0.99 else ' ✗')
                print(f'    CH{ch:2d}: {m}/{t} = {rate:.4%}  max_diff={md}{flag}')

            if match_rate > 0.999:
                print(f'\n  ✅ 结论: H5 2kHz ADC 值 == Bin raw ADC 值，同步正确')
            elif match_rate > 0.99:
                print(f'\n  ⚠️ 结论: 大部分匹配 (>{match_rate:.1%})，少量差异需检查')
            else:
                print(f'\n  ❌ 结论: 匹配率低 ({match_rate:.1%})，同步结果可疑')

    # ── 4. 生成对比图 ──
    print('\n' + '=' * 70)
    print('生成对比图...')
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False

        fig, axes = plt.subplots(3, 1, figsize=(16, 10))

        with h5py.File(H5_FILE, 'r') as f:
            # (a) H5 250Hz × vendor LSB — 供应商风格堆叠
            ax = axes[0]
            for ds_name, label in [('emg1_250hz_adc', 'H5 250Hz (vendor LSB)')]:
                if ds_name in f:
                    ds = f[ds_name]
                    sr = ds.attrs.get('sample_rate', 250)
                    n = min(int(5 * sr), ds.shape[0])
                    raw = ds[:n]
                    ch_data = np.array(raw['channels']) if raw.dtype.names else np.array(raw)
                    uv_data = ch_data.astype(np.float64) * (LSB_VENDOR / (GAIN * HW_GAIN))
                    t = np.arange(len(uv_data)) / sr
                    offset = 300
                    for ch in range(16):
                        y = uv_data[:, ch] + (15 - ch) * offset
                        ax.plot(t, y, linewidth=0.5, color=f'C{ch % 10}')
                    ax.set_title(f'{label} — Offset=300uV, 5s window')
                    ax.set_xlabel('Time (s)')
                    ax.set_ylabel('uV (stacked)')

            # (b) H5 2kHz × vendor LSB — 供应商风格堆叠
            ax = axes[1]
            for ds_name, label in [('emg1_2khz_adc', 'H5 2kHz (vendor LSB)')]:
                if ds_name in f:
                    ds = f[ds_name]
                    sr = ds.attrs.get('sample_rate', 2000)
                    n = min(int(5 * sr), ds.shape[0])
                    raw = ds[:n]
                    ch_data = np.array(raw['channels']) if raw.dtype.names else np.array(raw)
                    uv_data = ch_data.astype(np.float64) * (LSB_VENDOR / (GAIN * HW_GAIN))
                    t = np.arange(len(uv_data)) / sr
                    offset = 300
                    for ch in range(16):
                        y = uv_data[:, ch] + (15 - ch) * offset
                        ax.plot(t, y, linewidth=0.3, color=f'C{ch % 10}')
                    ax.set_title(f'{label} — Offset=300uV, 5s window')
                    ax.set_xlabel('Time (s)')
                    ax.set_ylabel('uV (stacked)')

            # (c) H5 2kHz × calibrate LSB — 当前 calibrate_tool LSB
            ax = axes[2]
            for ds_name, label in [('emg1_2khz_adc', 'H5 2kHz (calibrate LSB = 0.2861)')]:
                if ds_name in f:
                    ds = f[ds_name]
                    sr = ds.attrs.get('sample_rate', 2000)
                    n = min(int(5 * sr), ds.shape[0])
                    raw = ds[:n]
                    ch_data = np.array(raw['channels']) if raw.dtype.names else np.array(raw)
                    uv_data = ch_data.astype(np.float64) * (LSB_CALIBRATE / (GAIN * HW_GAIN))
                    t = np.arange(len(uv_data)) / sr
                    offset = 300
                    for ch in range(16):
                        y = uv_data[:, ch] + (15 - ch) * offset
                        ax.plot(t, y, linewidth=0.3, color=f'C{ch % 10}')
                    ax.set_title(f'{label} — Offset=300uV, 5s window')
                    ax.set_xlabel('Time (s)')
                    ax.set_ylabel('uV (stacked)')

        plt.tight_layout()
        out_path = os.path.join(BASE_DIR, 'docs', 'l015_compare_vendor_vs_calibrate.png')
        fig.savefig(out_path, dpi=150)
        print(f'对比图已保存: {out_path}')
        plt.close(fig)

    except Exception as e:
        print(f'生成对比图失败: {e}')
        import traceback
        traceback.print_exc()

    print('\n' + '=' * 70)
    print('诊断完成')
    print('=' * 70)


if __name__ == '__main__':
    main()
