#!/usr/bin/env python3
"""诊断: IMU 曲线平坦原因排查。只读，不改任何文件。"""
import os, sys, struct
import numpy as np
import h5py

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
H5_FILE = os.path.join(BASE_DIR, 'L015_h5',
    'L015_坐姿_(连续手势1)_坐姿_食指角度_session1_20260527_153138.h5')
BIN_IMU_L = os.path.join(BASE_DIR, 'L015_bin', 'L015_L_260527_153015_imu.bin')
BIN_IMU_R = os.path.join(BASE_DIR, 'L015_bin', 'L015_R_260527_153015_imu.bin')

SCALE_ACCEL_BIN = 16.0 / 32768.0    # bin_sync_tool IMUBinParser
SCALE_GYRO_BIN = 2000.0 / 32768.0

print('=' * 70)
print('IMU 平坦诊断')
print('=' * 70)

# ── 1. H5 IMU dataset dtype 和值域 ──
print('\n--- H5 IMU Datasets ---')
with h5py.File(H5_FILE, 'r') as f:
    for ds_name in sorted(f.keys()):
        if 'imu' not in ds_name.lower():
            continue
        ds = f[ds_name]
        print(f'\n[{ds_name}]')
        print(f'  shape: {ds.shape}')
        print(f'  dtype: {ds.dtype}')
        if ds.dtype.names:
            print(f'  fields: {list(ds.dtype.names)}')
            for field in ds.dtype.names:
                try:
                    vals = ds[field][:]
                    if len(vals) > 0:
                        v = np.array(vals, dtype=np.float64)
                        print(f'  {field}: shape={v.shape}, min={v.min():.6f}, max={v.max():.6f}, '
                              f'std={v.std():.6f}, mean={v.mean():.6f}')
                except Exception as e:
                    print(f'  {field}: ERROR {e}')
        else:
            vals = ds[:]
            if len(vals) > 0:
                v = np.array(vals, dtype=np.float64)
                print(f'  min={v.min():.6f}, max={v.max():.6f}, std={v.std():.6f}, mean={v.mean():.6f}')

        for ak in sorted(ds.attrs.keys()):
            print(f'  attr [{ak}]: {ds.attrs[ak]}')

# ── 2. calibrate_tool 会怎么读 ──
print('\n' + '=' * 70)
print('calibrate_tool _extract_imu_acc() 读取结果模拟')
print('=' * 70)
with h5py.File(H5_FILE, 'r') as f:
    for ds_name in ['imu1a_100hz', 'imu1b_100hz', 'imu2a_100hz', 'imu2b_100hz',
                    'imu1a_ble', 'imu1b_ble', 'imu2a_ble', 'imu2b_ble']:
        if ds_name not in f:
            continue
        ds = f[ds_name]
        raw = ds[:]
        print(f'\n[{ds_name}]')
        print(f'  dtype: {raw.dtype}')
        print(f'  names: {raw.dtype.names}')

        # simulate _extract_imu_acc
        if hasattr(raw, 'dtype') and raw.dtype.names:
            if 'acc' in raw.dtype.names:
                acc_data = raw['acc']
                if acc_data.ndim == 1:
                    acc = np.array([list(row) for row in acc_data])
                else:
                    acc = np.array(acc_data)
                print(f'  acc (from acc field): shape={acc.shape}, min={acc.min():.6f}, '
                      f'max={acc.max():.6f}, std={acc.std():.6f}')
            else:
                print(f'  acc: "acc" field NOT found in {list(raw.dtype.names)}')

            if 'gyr' in raw.dtype.names:
                gyr_data = raw['gyr']
                if gyr_data.ndim == 1:
                    gyr = np.array([list(row) for row in gyr_data])
                else:
                    gyr = np.array(gyr_data)
                print(f'  gyr (from gyr field): shape={gyr.shape}, min={gyr.min():.6f}, '
                      f'max={gyr.max():.6f}, std={gyr.std():.6f}')
            else:
                print(f'  gyr: "gyr" field NOT found in {list(raw.dtype.names)}')
        else:
            arr = np.array(raw)
            print(f'  plain array: shape={arr.shape}, min={arr.min():.6f}, '
                  f'max={arr.max():.6f}, std={arr.std():.6f}')
            if arr.ndim == 2:
                print(f'  cols 0-2 (acc): min={arr[:,:3].min():.6f}, max={arr[:,:3].max():.6f}, '
                      f'std={arr[:,:3].std():.6f}')
                if arr.shape[1] >= 6:
                    print(f'  cols 3-5 (gyr): min={arr[:,3:6].min():.6f}, max={arr[:,3:6].max():.6f}, '
                          f'std={arr[:,3:6].std():.6f}')

# ── 3. BIN 直接解析对比 ──
print('\n' + '=' * 70)
print('Bin IMU 直接解析 (bin_sync_tool IMUBinParser 逻辑)')
print('=' * 70)
BYTES_PER_IMU = 18
for label, bin_path in [('L', BIN_IMU_L), ('R', BIN_IMU_R)]:
    if not os.path.exists(bin_path):
        print(f'\n[{label}] 文件不存在: {bin_path}')
        continue
    file_size = os.path.getsize(bin_path)
    with open(bin_path, 'rb') as f:
        hdr = f.read(126)
        magic, sr, _, _, _, _ = struct.unpack('<I H B B B 32s', hdr[:41])
        print(f'\n[{label}] magic=0x{magic:08X}, sr={sr}Hz, size={file_size}')

        # 检测 num_imus
        data_size = file_size - 126
        num_imus = 2
        for nc in [3, 2, 1]:
            frame_sz = 4 + nc * BYTES_PER_IMU
            if data_size > 0 and data_size % frame_sz == 0:
                num_imus = nc
                break
        frame_size = 4 + num_imus * BYTES_PER_IMU
        print(f'  num_imus={num_imus}, frame_size={frame_size}B')

        # 读最近 1 秒 (~100 帧)
        total_frames = data_size // frame_size
        f.seek(126)
        acc_vals = {i: [] for i in range(num_imus)}
        gyr_vals = {i: [] for i in range(num_imus)}
        max_frames = min(100, total_frames - 1)
        # Skip to middle section to avoid startup artifacts
        f.seek(126 + (total_frames // 2) * frame_size)
        for _ in range(max_frames):
            chunk = f.read(frame_size)
            if len(chunk) < frame_size:
                break
            raw = chunk[4:]
            for k in range(num_imus):
                off = k * BYTES_PER_IMU
                b = raw[off:off + BYTES_PER_IMU]
                ag = struct.unpack('>6h', b[0:12])  # IMUBinParser uses Big Endian
                acc_vals[k].append([x * SCALE_ACCEL_BIN for x in ag[0:3]])
                gyr_vals[k].append([x * SCALE_GYRO_BIN for x in ag[3:6]])

        for k in range(num_imus):
            a = np.array(acc_vals[k])
            g = np.array(gyr_vals[k])
            print(f'  IMU{k+1} acc (g): min={a.min():.4f}, max={a.max():.4f}, '
                  f'std={a.std():.4f}, mean={a.mean():.4f}')
            print(f'  IMU{k+1} gyr (d/s): min={g.min():.2f}, max={g.max():.2f}, '
                  f'std={g.std():.2f}, mean={g.mean():.2f}')

# ── 4. 供应商 V3 离线 BIN 解析对比 (Little Endian for V2) ──
print('\n' + '=' * 70)
print('供应商 V3 方式解析 (Little Endian, ±32g for V2)')
print('=' * 70)
SCALE_ACCEL_VENDOR_V2 = 32.0 / 32768.0
for label, bin_path in [('L', BIN_IMU_L), ('R', BIN_IMU_R)]:
    if not os.path.exists(bin_path):
        continue
    file_size = os.path.getsize(bin_path)
    with open(bin_path, 'rb') as f:
        hdr = f.read(126)
        data_size = file_size - 126
        num_imus = 2
        for nc in [3, 2, 1]:
            frame_sz = 4 + nc * BYTES_PER_IMU
            if data_size > 0 and data_size % frame_sz == 0:
                num_imus = nc
                break
        frame_size = 4 + num_imus * BYTES_PER_IMU
        total_frames = data_size // frame_size
        f.seek(126)

        acc_v2 = {i: [] for i in range(num_imus)}
        gyr_v2 = {i: [] for i in range(num_imus)}
        max_frames = min(100, total_frames - 1)
        f.seek(126 + (total_frames // 2) * frame_size)
        for _ in range(max_frames):
            chunk = f.read(frame_size)
            if len(chunk) < frame_size:
                break
            raw = chunk[4:]
            for k in range(num_imus):
                off = k * BYTES_PER_IMU
                b = raw[off:off + BYTES_PER_IMU]
                ag = struct.unpack('<6h', b[0:12])  # VENDOR: Little Endian
                acc_v2[k].append([x * SCALE_ACCEL_VENDOR_V2 for x in ag[0:3]])
                gyr_v2[k].append([x * SCALE_GYRO_BIN for x in ag[3:6]])

        for k in range(num_imus):
            a = np.array(acc_v2[k])
            g = np.array(gyr_v2[k])
            print(f'  [{label}] IMU{k+1} acc (g, LE ±32g): min={a.min():.4f}, max={a.max():.4f}, '
                  f'std={a.std():.4f}, mean={a.mean():.4f}')
            print(f'  [{label}] IMU{k+1} gyr (d/s, LE): min={g.min():.2f}, max={g.max():.2f}, '
                  f'std={g.std():.2f}, mean={g.mean():.2f}')

# ── 5. H5 vs Bin 同一 frame_id 对比 ──
print('\n' + '=' * 70)
print('H5 imu*_100hz vs Bin IMU 同 frame_id 对比')
print('=' * 70)
with h5py.File(H5_FILE, 'r') as f:
    for dev_id, ds_a, ds_b, bin_p in [(1, 'imu1a_100hz', 'imu1b_100hz', BIN_IMU_L),
                                       (2, 'imu2a_100hz', 'imu2b_100hz', BIN_IMU_R)]:
        if ds_a not in f:
            print(f'\n[{ds_a}] 不存在，跳过')
            continue
        if not os.path.exists(bin_p):
            print(f'\nBin 不存在: {bin_p}')
            continue

        # H5 前 10 帧
        h5a = f[ds_a][:10]
        h5b = f[ds_b][:10] if ds_b in f else None

        print(f'\n[Dev{dev_id}] {ds_a}, {ds_b}')
        print(f'  H5 dtype: {h5a.dtype}')
        if h5a.dtype.names:
            h5_sd_ids = h5a['sd_frame_id'][:]
            h5a_acc = np.array(h5a['acc'])
            h5a_gyr = np.array(h5a['gyr'])
        else:
            print(f'  UNEXPECTED: not structured, dtype={h5a.dtype}')
            continue

        print(f'  前10帧 sd_frame_id: {list(h5_sd_ids[:10])}')
        print(f'  IMUA acc[0]: {h5a_acc[0]}')
        print(f'  IMUA gyr[0]: {h5a_gyr[0]}')

        # Bin 对应 frame_id
        if os.path.exists(bin_p):
            # Parse bin with IMUBinParser logic
            data_size = os.path.getsize(bin_p) - 126
            num_imus = 2
            for nc in [3, 2, 1]:
                if data_size > 0 and data_size % (4 + nc * BYTES_PER_IMU) == 0:
                    num_imus = nc
                    break
            frame_size = 4 + num_imus * BYTES_PER_IMU

            with open(bin_p, 'rb') as bf:
                bf.seek(126)
                bin_frames = {}
                while True:
                    chunk = bf.read(frame_size)
                    if len(chunk) < frame_size:
                        break
                    fid = struct.unpack('<I', chunk[0:4])[0]
                    raw = chunk[4:]
                    imus = []
                    for k in range(num_imus):
                        off = k * BYTES_PER_IMU
                        b = raw[off:off + BYTES_PER_IMU]
                        ag = struct.unpack('>6h', b[0:12])
                        imus.append({
                            'acc': [x * SCALE_ACCEL_BIN for x in ag[0:3]],
                            'gyr': [x * SCALE_GYRO_BIN for x in ag[3:6]],
                        })
                    bin_frames[fid] = imus
                    if len(bin_frames) >= 20:
                        break

            # Compare
            for h5_idx in range(min(10, len(h5_sd_ids))):
                h5_fid = int(h5_sd_ids[h5_idx])
                bin_imu = bin_frames.get(h5_fid)
                if bin_imu:
                    match_acc = np.allclose(h5a_acc[h5_idx], bin_imu[0]['acc'], atol=1e-4)
                    match_gyr = np.allclose(h5a_gyr[h5_idx], bin_imu[0]['gyr'], atol=1e-4)
                    status = '✓' if (match_acc and match_gyr) else '✗'
                    print(f'  fid={h5_fid}: H5 a_acc={h5a_acc[h5_idx]}, '
                          f'Bin a_acc={np.array(bin_imu[0]["acc"])}, match={status}')
                    if not match_acc:
                        diff_acc = h5a_acc[h5_idx] - np.array(bin_imu[0]['acc'])
                        print(f'    ACC DIFF: {diff_acc}')
                    if not match_gyr:
                        diff_gyr = h5a_gyr[h5_idx] - np.array(bin_imu[0]['gyr'])
                        print(f'    GYR DIFF: {diff_gyr}')
                else:
                    print(f'  fid={h5_fid}: NOT FOUND in bin')

# ── 6. 检查 calibrate_tool 绘图 y 轴范围 ──
print('\n' + '=' * 70)
print('calibrate_tool 绘图 Y 轴范围分析')
print('=' * 70)
with h5py.File(H5_FILE, 'r') as f:
    for ds_name in ['imu1a_100hz', 'imu1b_100hz', 'imu2a_100hz', 'imu2b_100hz']:
        if ds_name not in f:
            continue
        ds = f[ds_name]
        raw = ds[:]
        if raw.dtype.names and 'acc' in raw.dtype.names:
            acc = np.array(raw['acc'])
            gyr = np.array(raw['gyr'])
        else:
            continue

        print(f'\n[{ds_name}]')
        print(f'  Acc range: {acc.min():.4f} ~ {acc.max():.4f} g')
        print(f'  Gyr range: {gyr.min():.4f} ~ {gyr.max():.4f} deg/s')
        print(f'  Acc std: {acc.std():.4f} g')
        print(f'  Gyr std: {gyr.std():.4f} deg/s')

        # calibrate_tool Acc 图 ylim: -0.2*4 to 3*4-0.2*4 = -0.8 to 11.2 g
        acc_offset = 4.0
        acc_ylim = (-acc_offset * 0.2, 3 * acc_offset - acc_offset * 0.2)
        print(f'  Acc plot ylim: {acc_ylim} (offset={acc_offset})')
        print(f'  Acc data span / ylim span: {(acc.max()-acc.min()) / (acc_ylim[1]-acc_ylim[0]):.4f} '
              f'({"VISIBLE" if (acc.max()-acc.min())/(acc_ylim[1]-acc_ylim[0]) > 0.05 else "FLAT ←"})')

        gyr_offset = 600.0
        gyr_ylim = (-gyr_offset * 0.2, 3 * gyr_offset - gyr_offset * 0.2)
        print(f'  Gyr plot ylim: {gyr_ylim} (offset={gyr_offset})')
        print(f'  Gyr data span / ylim span: {(gyr.max()-gyr.min()) / (gyr_ylim[1]-gyr_ylim[0]):.4f} '
              f'({"VISIBLE" if (gyr.max()-gyr.min())/(gyr_ylim[1]-gyr_ylim[0]) > 0.05 else "FLAT ←"})')

print('\n' + '=' * 70)
print('诊断完成')
print('=' * 70)
