#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IMU 数据诊断工具 — 对比两个 H5 文件的 IMU 数据健康状态
用法: python diagnose_imu.py <session1.h5> <session2.h5>
"""

import sys
import os
import h5py
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bin_sync_tool import _detect_num_imus_from_bin, HEADER_SIZE, BYTES_PER_IMU_CHIP


def diagnose_h5(h5_path, label):
    """诊断单个 H5 文件的 IMU 数据"""
    print(f"\n{'='*70}")
    print(f"  {label}: {os.path.basename(h5_path)}")
    print(f"{'='*70}")

    with h5py.File(h5_path, 'r') as f:
        # 1. H5 attrs
        print(f"\n--- H5 Attrs ---")
        for key in ['sync_status', 'sync_mode', 'stream_format_version', 'bin_pair_source',
                     'num_imus', 'imu1_num_imus', 'imu2_num_imus',
                     'imu1_hw_version', 'imu2_hw_version',
                     'sd_bin_dev1', 'sd_bin_dev2']:
            val = f.attrs.get(key, 'N/A')
            if isinstance(val, bytes):
                val = val.decode('utf-8')
            print(f"  {key}: {val}")

        # 2. IMU datasets
        for dev in [1, 2]:
            print(f"\n--- Device {dev} IMU Datasets ---")

            # all_ble
            all_name = f'imu{dev}_all_ble'
            if all_name in f:
                ds = f[all_name]
                print(f"  {all_name}: shape={ds.shape}, dtype={ds.dtype}")
                if ds.shape[0] > 0:
                    data = ds[:]
                    if 'imu_index' in ds.dtype.names:
                        imu_indices = data['imu_index']
                        unique_indices = np.unique(imu_indices)
                        print(f"    imu_index 范围: {unique_indices}, 计数: {len(unique_indices)}")
                        for idx in unique_indices:
                            count = np.sum(imu_indices == idx)
                            print(f"      imu_index={idx}: {count} 行")
                    if 'acc' in ds.dtype.names:
                        acc = data['acc']
                        print(f"    acc range: [{np.min(acc):.4f}, {np.max(acc):.4f}], "
                              f"nonzero: {np.count_nonzero(acc)}/{acc.size}")
                    if 'time' in ds.dtype.names:
                        t = data['time']
                        print(f"    time range: [{t[0]:.3f}, {t[-1]:.3f}]")
                else:
                    print(f"    (empty)")

            # 100hz datasets
            for sensor in ['a', 'b', 'c']:
                ds_name = f'imu{dev}{sensor}_100hz'
                if ds_name in f:
                    ds = f[ds_name]
                    print(f"  {ds_name}: shape={ds.shape}")
                    if ds.shape[0] > 0:
                        data = ds[:]
                        if 'acc' in ds.dtype.names:
                            acc = data['acc']
                            nonzero_count = np.count_nonzero(acc)
                            print(f"    acc range: [{np.min(acc):.4f}, {np.max(acc):.4f}], "
                                  f"nonzero: {nonzero_count}/{acc.size} "
                                  f"({'ALL ZERO!' if nonzero_count == 0 else 'OK'})")
                        if 'gyr' in ds.dtype.names:
                            gyr = data['gyr']
                            nonzero_count = np.count_nonzero(gyr)
                            print(f"    gyr range: [{np.min(gyr):.4f}, {np.max(gyr):.4f}], "
                                  f"nonzero: {nonzero_count}/{gyr.size} "
                                  f"({'ALL ZERO!' if nonzero_count == 0 else 'OK'})")
                        if 'time' in ds.dtype.names:
                            t = data['time']
                            print(f"    time range: [{t[0]:.3f}, {t[-1]:.3f}]")
                        if 'sd_frame_id' in ds.dtype.names:
                            sfid = data['sd_frame_id']
                            print(f"    sd_frame_id range: [{sfid[0]}, {sfid[-1]}]")
                        # Check for NaN/Inf
                        acc_nan = np.sum(np.isnan(acc)) if 'acc' in ds.dtype.names else 0
                        acc_inf = np.sum(np.isinf(acc)) if 'acc' in ds.dtype.names else 0
                        if acc_nan > 0 or acc_inf > 0:
                            print(f"    ⚠️ acc NaN={acc_nan}, Inf={acc_inf}")
                    else:
                        print(f"    (empty)")
                else:
                    print(f"  {ds_name}: NOT FOUND")

            # BLE datasets
            for sensor in ['a', 'b']:
                ds_name = f'imu{dev}{sensor}_ble'
                if ds_name in f:
                    ds = f[ds_name]
                    print(f"  {ds_name}: shape={ds.shape}" + (" (empty)" if ds.shape[0] == 0 else ""))

        # 3. EMG 2kHz datasets (for cross-reference)
        for dev in [1, 2]:
            ds_name = f'emg{dev}_2khz_adc'
            if ds_name in f:
                ds = f[ds_name]
                if ds.shape[0] > 0:
                    data = ds[:]
                    if 'sd_frame_id' in ds.dtype.names:
                        sfid = data['sd_frame_id']
                        print(f"\n  {ds_name}: sd_frame_id range [{sfid[0]}, {sfid[-1]}], "
                              f"len={len(sfid)}")
                        # Check continuity
                        diffs = np.diff(sfid.astype(np.int64))
                        gaps = np.sum(diffs != 1)
                        print(f"    sd_frame_id gaps (diff != 1): {gaps}/{len(diffs)} "
                              f"({'CONTINUOUS' if gaps == 0 else '⚠️ HAS GAPS'})")


def diagnose_bin(bin_path, label):
    """诊断 IMU bin 文件"""
    if not bin_path or not os.path.exists(bin_path):
        print(f"\n  {label}: FILE NOT FOUND ({bin_path})")
        return None

    print(f"\n--- {label} ---")
    print(f"  Path: {bin_path}")

    file_size = os.path.getsize(bin_path)
    data_size = file_size - HEADER_SIZE
    print(f"  File size: {file_size} bytes, Data size: {data_size} bytes")

    # IMU count detection
    detected = _detect_num_imus_from_bin(bin_path)
    print(f"  Auto-detected num_imus: {detected}")

    # Frame size analysis for each candidate
    print(f"\n  Frame size candidates:")
    for n in [4, 3, 2, 1]:
        frame_size = 4 + n * BYTES_PER_IMU_CHIP
        num_frames = data_size // frame_size
        remainder = data_size % frame_size
        marker = " ✓ EXACT" if remainder == 0 else ""
        print(f"    n={n}: frame_size={frame_size}B, frames={num_frames}, "
              f"remainder={remainder}B{marker}")

    return detected


def main():
    if len(sys.argv) < 3:
        print("用法: python diagnose_imu.py <session1.h5> <session2.h5>")
        print("      python diagnose_imu.py --bin <imu_bin1> <imu_bin2>")
        sys.exit(1)

    if sys.argv[1] == '--bin':
        diagnose_bin(sys.argv[2], "IMU Bin 1")
        diagnose_bin(sys.argv[3], "IMU Bin 2")
        return

    h5_1, h5_2 = sys.argv[1], sys.argv[2]

    # --- H5 diagnosis ---
    diagnose_h5(h5_1, "Session 1")
    diagnose_h5(h5_2, "Session 2")

    # --- Comparison ---
    print(f"\n{'='*70}")
    print(f"  COMPARISON SUMMARY")
    print(f"{'='*70}")

    with h5py.File(h5_1, 'r') as f1, h5py.File(h5_2, 'r') as f2:
        for dev in [1, 2]:
            for sensor in ['a', 'b', 'c']:
                ds_name = f'imu{dev}{sensor}_100hz'
                in1 = ds_name in f1
                in2 = ds_name in f2
                if in1 and in2:
                    s1 = f1[ds_name].shape[0]
                    s2 = f2[ds_name].shape[0]
                    if s1 > 0 and s2 > 0:
                        d1 = f1[ds_name][:]
                        d2 = f2[ds_name][:]
                        if 'acc' in d1.dtype.names:
                            nz1 = np.count_nonzero(d1['acc'])
                            nz2 = np.count_nonzero(d2['acc'])
                            r1 = f"[{np.min(d1['acc']):.2f}, {np.max(d1['acc']):.2f}]"
                            r2 = f"[{np.min(d2['acc']):.2f}, {np.max(d2['acc']):.2f}]"
                            status = "MATCH" if abs(nz1 - nz2) < 10 else "⚠️ DIFFER"
                            print(f"  {ds_name}: s1={s1}(nz={nz1}) {r1} | "
                                  f"s2={s2}(nz={nz2}) {r2}  [{status}]")
                    elif s1 > 0 or s2 > 0:
                        print(f"  {ds_name}: s1={s1}, s2={s2}  ⚠️ ONE EMPTY")
                elif in1 != in2:
                    print(f"  {ds_name}: {'EXISTS' if in1 else 'MISSING'} in s1, "
                          f"{'EXISTS' if in2 else 'MISSING'} in s2  ⚠️")

    # --- Bin diagnosis ---
    print(f"\n--- IMU Bin File Analysis ---")
    for h5_path, label in [(h5_1, "Session 1"), (h5_2, "Session 2")]:
        with h5py.File(h5_path, 'r') as f:
            for dev in [1, 2]:
                attr_name = f'sd_bin_dev{dev}'
                bin_prefix = f.attrs.get(attr_name)
                if bin_prefix:
                    if isinstance(bin_prefix, bytes):
                        bin_prefix = bin_prefix.decode('utf-8')
                    # Try to find the IMU bin in the H5's directory and parent dirs
                    h5_dir = os.path.dirname(os.path.abspath(h5_path))
                    for search_dir in [h5_dir] + [
                        os.path.dirname(h5_dir),
                        os.path.join(h5_dir, '..'),
                    ]:
                        search_dir = os.path.abspath(search_dir)
                        for root, dirs, files in os.walk(search_dir):
                            imu_name = f"{bin_prefix}_imu.bin"
                            if imu_name in files:
                                imu_path = os.path.join(root, imu_name)
                                diagnose_bin(imu_path, f"{label} Dev{dev} IMU Bin")
                                break
                        else:
                            continue
                        break
                    else:
                        print(f"\n  {label} Dev{dev}: IMU bin NOT FOUND for prefix '{bin_prefix}'")
                else:
                    print(f"\n  {label} Dev{dev}: No sd_bin_dev{dev} attr")


if __name__ == '__main__':
    main()
