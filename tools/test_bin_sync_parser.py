"""
test_bin_sync_parser.py — IMUBinParser 自测脚本

构造 V2 3IMU / V1 2IMU / V2 2IMU(歧义) 小样本 bin，验证 parser 行为。
不含第三方依赖，直接运行即可。
"""

import os
import sys
import struct
import tempfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bin_sync_tool import IMUBinParser, HEADER_SIZE, FOOTER_SIZE
from bin_sync_tool import IMU_MAGIC, FOOTER_MAGIC_IMU
from bin_sync_tool import SCALE_ACCEL, SCALE_ACCEL_V2, SCALE_GYRO, SCALE_MAG

PASS = 0
FAIL = 0


def check(condition, msg):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {msg}")
    else:
        FAIL += 1
        print(f"  [FAIL] {msg}")


# ---- helpers ----

def make_header(sample_rate=100):
    ts = b"2024-01-01_00:00:00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    hdr = struct.pack('<I H B B B 32s', IMU_MAGIC, sample_rate, 0, 0, 0, ts)
    return hdr.ljust(HEADER_SIZE, b'\x00')


def make_v2_footer(total=10, sd=0, imu_d=0, ble=0, reason=2):
    buf = struct.pack('<I', FOOTER_MAGIC_IMU)
    buf += struct.pack('<4I', total, sd, imu_d, ble)
    buf += struct.pack('B', reason)
    buf = buf.ljust(FOOTER_SIZE, b'\x00')
    return buf


def write_bin(path, data):
    with open(path, 'wb') as f:
        f.write(data)


# ============================================================
# Test 1: V2 3IMU (58-byte frames) — 应解析为 V2, 3 IMU, has_mag=0
# ============================================================
def test_v2_3imu():
    print("\n=== Test 1: V2 3IMU (58-byte frames) ===")
    frame_size = 4 + 3 * 18
    num_frames = 5
    header = make_header(100)
    payload = b''
    for fid in range(num_frames):
        payload += struct.pack('<I', fid)
        for imu_idx in range(3):
            acc_raw = [100 * (imu_idx + 1), 200, 300]
            gyr_raw = [10, 20, 30]
            payload += struct.pack('<6h', *acc_raw, *gyr_raw)
            payload += b'\x00' * 6  # reserved
    bin_data = header + payload

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    try:
        parser = IMUBinParser(tmp_path).parse()
        check(parser.parser_version == 2, f"parser_version == 2 (got {parser.parser_version})")
        check(parser.num_imus == 3, f"num_imus == 3 (got {parser.num_imus})")
        check(parser.has_mag == False, f"has_mag == False")
        check(parser.frame_count == num_frames, f"frame_count == {num_frames}")
        check(parser.sample_rate == 100, f"sample_rate == 100")

        # Check first frame
        frame = parser.frames[0]
        check(len(frame) == 3, f"第一帧有 3 个 IMU")
        check(frame[0]['index'] == 0, "IMU0 index=0")
        check(frame[1]['index'] == 1, "IMU1 index=1")
        check(frame[2]['index'] == 2, "IMU2 index=2")

        # V2: Acc ±32g, Little Endian
        expected_acc0 = 100 * SCALE_ACCEL_V2
        check(abs(frame[0]['acc'][0] - expected_acc0) < 1e-6,
              f"IMU0 acc_x ≈ {expected_acc0:.4f} (32g量程, 小端)")

        # V2: has_mag=0, mag=NaN
        check(frame[0]['has_mag'] == 0, "IMU0 has_mag=0")
        check(np.isnan(frame[0]['mag'][0]), "IMU0 mag[0] is NaN")
        check(np.isnan(frame[0]['mag'][1]), "IMU0 mag[1] is NaN")
        check(np.isnan(frame[0]['mag'][2]), "IMU0 mag[2] is NaN")

        # Check all frames exist
        for fid in range(num_frames):
            check(fid in parser.frames, f"frame {fid} 存在")
            check(len(parser.frames[fid]) == 3, f"frame {fid} 有 3 个 IMU")

        print(f"  解析器信息: V{parser.parser_version}, {parser.num_imus}IMU, "
              f"has_mag={parser.has_mag}, {parser.frame_count}帧")

    finally:
        os.unlink(tmp_path)


# ============================================================
# Test 2: V1 2IMU (40-byte frames, no footer) — 应解析为 V1
# ============================================================
def test_v1_2imu():
    print("\n=== Test 2: V1 2IMU (40-byte frames) ===")
    frame_size = 4 + 36  # 40
    num_frames = 5
    header = make_header(100)
    payload = b''
    for fid in range(num_frames):
        payload += struct.pack('<I', fid)
        for imu_idx in range(2):
            acc_raw = [10, 20, 30]
            gyr_raw = [100, 200, 300]
            mag_raw = [1, 2, 3]
            payload += struct.pack('>6h', *acc_raw, *gyr_raw)  # Big Endian
            payload += struct.pack('<3h', *mag_raw)  # Little Endian
    bin_data = header + payload

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    try:
        parser = IMUBinParser(tmp_path).parse()
        check(parser.parser_version == 1, f"parser_version == 1 (got {parser.parser_version})")
        check(parser.num_imus == 2, f"num_imus == 2 (got {parser.num_imus})")
        check(parser.has_mag == True, f"has_mag == True")
        check(parser.frame_count == num_frames, f"frame_count == {num_frames}")

        # Check first frame
        frame = parser.frames[0]
        check(len(frame) == 2, f"第一帧有 2 个 IMU")

        # V1: Acc ±16g, Big Endian
        expected_acc0 = 10 * SCALE_ACCEL
        check(abs(frame[0]['acc'][0] - expected_acc0) < 1e-6,
              f"IMU0 acc_x ≈ {expected_acc0:.4f} (16g量程, 大端)")

        # V1: has_mag=1, mag=实际值
        check(frame[0]['has_mag'] == 1, "IMU0 has_mag=1")
        expected_mag0 = 1 * SCALE_MAG
        check(abs(frame[0]['mag'][0] - expected_mag0) < 1e-6,
              f"IMU0 mag_x ≈ {expected_mag0:.4f}")

        print(f"  解析器信息: V{parser.parser_version}, {parser.num_imus}IMU, "
              f"has_mag={parser.has_mag}, {parser.frame_count}帧")

    finally:
        os.unlink(tmp_path)


# ============================================================
# Test 3: V2 2IMU with footer (40-byte frames + footer = 歧义区域)
#         无 H5 属性 → 应默认 V1（保守策略）
# ============================================================
def test_v2_2imu_with_footer_ambiguous():
    print("\n=== Test 3: V2 2IMU + Footer (歧义: 40字节帧 + footer) ===")
    print("  预期: Footer 被检测到 → 判定 V2")
    frame_size = 4 + 2 * 18  # 40 bytes
    num_frames = 5
    header = make_header(100)
    payload = b''
    for fid in range(num_frames):
        payload += struct.pack('<I', fid)
        for imu_idx in range(2):
            acc_raw = [50, 60, 70]
            gyr_raw = [5, 10, 15]
            payload += struct.pack('<6h', *acc_raw, *gyr_raw)
            payload += b'\x00' * 6
    footer = make_v2_footer(total=num_frames)
    bin_data = header + payload + footer

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    try:
        parser = IMUBinParser(tmp_path).parse()
        # 有 footer → 应检测为 V2
        check(parser.parser_version == 2,
              f"有Footer时 parser_version == 2 (got {parser.parser_version})")
        check(parser.num_imus == 2, f"num_imus == 2 (got {parser.num_imus})")
        check(parser._detected_footer == True, "检测到 Footer")
        check(parser.has_mag == False, "has_mag == False")

        # V2: Little Endian, ±32g
        frame = parser.frames[0]
        expected_acc0 = 50 * SCALE_ACCEL_V2
        check(abs(frame[0]['acc'][0] - expected_acc0) < 1e-6,
              f"IMU0 acc_x ≈ {expected_acc0:.4f} (32g量程, 小端)")
        check(np.isnan(frame[0]['mag'][0]), "IMU0 mag is NaN")

        print(f"  解析器信息: V{parser.parser_version}, {parser.num_imus}IMU, "
              f"has_mag={parser.has_mag}, footer_detected={parser._detected_footer}")

    finally:
        os.unlink(tmp_path)


# ============================================================
# Test 4: 40-byte frames WITHOUT footer, no H5 → 保守 V1
# ============================================================
def test_ambiguous_40byte_no_metadata():
    print("\n=== Test 4: 40字节帧, 无Footer, 无H5属性 → 默认V1 (保守) ===")
    frame_size = 40
    num_frames = 5
    header = make_header(100)
    payload = b''
    for fid in range(num_frames):
        payload += struct.pack('<I', fid)
        payload += b'\x00' * 36
    bin_data = header + payload

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    try:
        parser = IMUBinParser(tmp_path).parse()
        check(parser.parser_version == 1,
              f"无元数据时默认 V1 (got {parser.parser_version})")
        check(parser.num_imus == 2, f"V1 num_imus == 2")

        print(f"  解析器信息: V{parser.parser_version}, {parser.num_imus}IMU")

    finally:
        os.unlink(tmp_path)


# ============================================================
# Test 5: V2 Footer 解析内容验证
# ============================================================
def test_v2_footer_content():
    print("\n=== Test 5: V2 Footer 内容验证 ===")
    frame_size = 4 + 3 * 18
    num_frames = 10
    header = make_header(100)
    payload = b''
    for fid in range(num_frames):
        payload += struct.pack('<I', fid)
        for _ in range(3):
            payload += struct.pack('<6h', 0, 0, 0, 0, 0, 0)
            payload += b'\x00' * 6
    footer = make_v2_footer(total=10, sd=1, imu_d=2, ble=3, reason=2)
    bin_data = header + payload + footer

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    try:
        parser = IMUBinParser(tmp_path).parse()
        check(parser._detected_footer == True, "Footer 被检测到")
        check(parser.frame_count == num_frames,
              f"共解析 {num_frames} 帧 (未将 footer 当数据)")
        # Verify footer was not parsed as data frames
        check(num_frames not in parser.frames,
              f"帧号 {num_frames} 不存在 (footer 未误解析)")

        print(f"  解析器信息: {parser.frame_count} 帧")

    finally:
        os.unlink(tmp_path)


# ============================================================
# Test 6: V1 29帧 (40×29=1160 可被58整除) → 反例，必须判 V1
# ============================================================
def test_v1_29frames_not_v2():
    print("\n=== Test 6: V1 29帧 (40×29=1160可被58整除) 反例 → 必须判 V1 ===")
    num_frames = 29
    header = make_header(100)
    payload = b''
    for fid in range(num_frames):
        payload += struct.pack('<I', fid)
        payload += b'\x00' * 36
    bin_data = header + payload

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    try:
        parser = IMUBinParser(tmp_path).parse()
        check(parser.parser_version == 1,
              f"V1 29帧应判 V1, 实际 parser_version={parser.parser_version}")

        # 旧代码 bug: 40×29=1160, 1160%58=0, 会误判 V2 3IMU 且只解析 20 帧
        if parser.parser_version == 1:
            check(parser.frame_count == num_frames,
                  f"frame_count == {num_frames} (实际 {parser.frame_count})")
        else:
            check(False, f"误判为 V{parser.parser_version}! V1数据被当V2解析, 数据损坏")

        print(f"  V1 29帧反例: parser_version={parser.parser_version}, "
              f"frame_count={parser.frame_count}")

    finally:
        os.unlink(tmp_path)


# ============================================================
# Test 7: V1 11帧 (40×11=440 可被22整除) → 反例，必须判 V1
# ============================================================
def test_v1_11frames_not_v2():
    print("\n=== Test 7: V1 11帧 (40×11=440可被22整除) 反例 → 必须判 V1 ===")
    num_frames = 11
    header = make_header(100)
    payload = b''
    for fid in range(num_frames):
        payload += struct.pack('<I', fid)
        payload += b'\x00' * 36
    bin_data = header + payload

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    try:
        parser = IMUBinParser(tmp_path).parse()
        check(parser.parser_version == 1,
              f"V1 11帧应判 V1, 实际 parser_version={parser.parser_version}")

        if parser.parser_version == 1:
            check(parser.frame_count == num_frames,
                  f"frame_count == {num_frames} (实际 {parser.frame_count})")
        else:
            check(False, f"误判为 V{parser.parser_version}! V1数据被当V2解析")

        print(f"  V1 11帧反例: parser_version={parser.parser_version}, "
              f"frame_count={parser.frame_count}")

    finally:
        os.unlink(tmp_path)


# ============================================================
# Test 8: H5 parser_version=2, 无num_imus, V2 3IMU 58字节
#         应推断 num_imus=3（而非默认2）
# ============================================================
def test_h5_v2_no_numimus_infer_3():
    print("\n=== Test 8: H5 parser_version=2, 无num_imus, V2 3IMU → 推断 num_imus=3 ===")
    frame_size = 4 + 3 * 18
    num_frames = 5
    header = make_header(100)
    payload = b''
    for fid in range(num_frames):
        payload += struct.pack('<I', fid)
        for imu_idx in range(3):
            acc_raw = [10 * (imu_idx + 1), 20, 30]
            gyr_raw = [1, 2, 3]
            payload += struct.pack('<6h', *acc_raw, *gyr_raw)
            payload += b'\x00' * 6
    bin_data = header + payload

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    # 创建临时 H5 文件，只设 parser_version=2，不设 num_imus
    h5_tmp_path = None
    try:
        import h5py
        fd, h5_tmp_path = tempfile.mkstemp(suffix='.h5')
        os.close(fd)
        with h5py.File(h5_tmp_path, 'w') as hf:
            hf.attrs['imu1_parser_version'] = 2
            # 故意不设 imu1_num_imus

        parser = IMUBinParser(tmp_path, h5_file=h5_tmp_path, device_id=1).parse()
        check(parser.parser_version == 2,
              f"parser_version == 2 (got {parser.parser_version})")
        check(parser.num_imus == 3,
              f"H5 parser_version=2 无num_imus 应推断 num_imus=3 (got {parser.num_imus})")
        check(parser.has_mag == False, "has_mag == False")

        print(f"  H5引导推断: V{parser.parser_version}, num_imus={parser.num_imus}")

    finally:
        os.unlink(tmp_path)
        if h5_tmp_path and os.path.exists(h5_tmp_path):
            os.unlink(h5_tmp_path)


# ============================================================
# Test 9: V2 缺失帧 all_100hz 填充 — has_mag=0 时 mag 应填 NaN
# ============================================================
def test_v2_missing_frame_mag_nan():
    print("\n=== Test 9: V2 缺失帧 — has_mag=0 则 mag=NaN (非0) ===")
    num_frames = 3
    header = make_header(100)
    payload = b''
    # 只写帧 0 和帧 2，帧 1 缺失
    for fid in [0, 2]:
        payload += struct.pack('<I', fid)
        for _ in range(3):
            payload += struct.pack('<6h', 1, 2, 3, 4, 5, 6)
            payload += b'\x00' * 6
    bin_data = header + payload

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    try:
        parser = IMUBinParser(tmp_path).parse()

        # 验证帧 0 存在，帧 1 缺失，帧 2 存在
        check(0 in parser.frames, "帧0存在")
        check(1 not in parser.frames, "帧1缺失")
        check(2 in parser.frames, "帧2存在")

        # 模拟 sync 中的缺失帧填充逻辑
        nan3 = np.array([np.nan, np.nan, np.nan], dtype=np.float32)
        # V2 parser 应 has_mag=False
        check(parser.has_mag == False, f"V2 has_mag == False (got {parser.has_mag})")

        # 验证缺失帧填充逻辑：has_mag=0 → mag=NaN
        mag_fill = np.zeros(3, dtype=np.float32) if parser.has_mag else nan3
        check(np.isnan(mag_fill[0]), "V2缺失帧 mag[0] 应为 NaN")
        check(np.isnan(mag_fill[1]), "V2缺失帧 mag[1] 应为 NaN")
        check(np.isnan(mag_fill[2]), "V2缺失帧 mag[2] 应为 NaN")

        print(f"  V2缺失帧填充验证: has_mag={parser.has_mag}, mag[0]={'NaN' if np.isnan(mag_fill[0]) else mag_fill[0]}")

    finally:
        os.unlink(tmp_path)


# ============================================================
# Test 10: V2 2IMU + Footer + 29帧 (40×29=1160也可被58整除)
#          Footer total_frames=29 交叉校验 → num_imus=2
# ============================================================
def test_v2_2imu_footer_29frames():
    print("\n=== Test 10: V2 2IMU + Footer + 29帧 (footer cross-validate) ===")
    print("  1160字节可被58整除, 但footer total_frames=29 → 应判2IMU非3IMU")
    num_frames = 29
    frame_size = 4 + 2 * 18  # 40 bytes
    header = make_header(100)
    payload = b''
    for fid in range(num_frames):
        payload += struct.pack('<I', fid)
        for _ in range(2):
            payload += struct.pack('<6h', 1, 2, 3, 4, 5, 6)
            payload += b'\x00' * 6
    footer = make_v2_footer(total=num_frames)
    bin_data = header + payload + footer

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    try:
        parser = IMUBinParser(tmp_path).parse()
        check(parser.parser_version == 2,
              f"parser_version == 2 (got {parser.parser_version})")
        check(parser.num_imus == 2,
              f"V2 2IMU footer校验应 num_imus=2 (got {parser.num_imus})")
        check(parser.frame_count == num_frames,
              f"frame_count == {num_frames} (got {parser.frame_count})")
        check(parser.has_mag == False, "has_mag == False")

        print(f"  V2 2IMU footer校验: num_imus={parser.num_imus}, frame_count={parser.frame_count}")

    finally:
        os.unlink(tmp_path)


# ============================================================
# Test 11: V2 1IMU + Footer + 20帧 (22×20=440也可被40整除)
#          Footer total_frames=20 交叉校验 → num_imus=1
# ============================================================
def test_v2_1imu_footer_20frames():
    print("\n=== Test 11: V2 1IMU + Footer + 20帧 (footer cross-validate) ===")
    print("  440字节可被40整除, 但footer total_frames=20 → 应判1IMU非2IMU")
    num_frames = 20
    frame_size = 4 + 1 * 18  # 22 bytes
    header = make_header(100)
    payload = b''
    for fid in range(num_frames):
        payload += struct.pack('<I', fid)
        payload += struct.pack('<6h', 1, 2, 3, 4, 5, 6)
        payload += b'\x00' * 6
    footer = make_v2_footer(total=num_frames)
    bin_data = header + payload + footer

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    try:
        parser = IMUBinParser(tmp_path).parse()
        check(parser.parser_version == 2,
              f"parser_version == 2 (got {parser.parser_version})")
        check(parser.num_imus == 1,
              f"V2 1IMU footer校验应 num_imus=1 (got {parser.num_imus})")
        check(parser.frame_count == num_frames,
              f"frame_count == {num_frames} (got {parser.frame_count})")

        print(f"  V2 1IMU footer校验: num_imus={parser.num_imus}, frame_count={parser.frame_count}")

    finally:
        os.unlink(tmp_path)


# ============================================================
# Test 12: V2 3IMU + Footer + 20帧 (58×20=1160也可被40整除)
#          Footer total_frames=20 交叉校验 → num_imus=3
# ============================================================
def test_v2_3imu_footer_20frames():
    print("\n=== Test 12: V2 3IMU + Footer + 20帧 (footer cross-validate) ===")
    print("  1160字节可被40整除, 但footer total_frames=20 → 应判3IMU非2IMU")
    num_frames = 20
    frame_size = 4 + 3 * 18  # 58 bytes
    header = make_header(100)
    payload = b''
    for fid in range(num_frames):
        payload += struct.pack('<I', fid)
        for _ in range(3):
            payload += struct.pack('<6h', 1, 2, 3, 4, 5, 6)
            payload += b'\x00' * 6
    footer = make_v2_footer(total=num_frames)
    bin_data = header + payload + footer

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    try:
        parser = IMUBinParser(tmp_path).parse()
        check(parser.parser_version == 2,
              f"parser_version == 2 (got {parser.parser_version})")
        check(parser.num_imus == 3,
              f"V2 3IMU footer校验应 num_imus=3 (got {parser.num_imus})")
        check(parser.frame_count == num_frames,
              f"frame_count == {num_frames} (got {parser.frame_count})")

        print(f"  V2 3IMU footer校验: num_imus={parser.num_imus}, frame_count={parser.frame_count}")

    finally:
        os.unlink(tmp_path)


# ============================================================
# Test 13: H5 parser_version=2, 无 num_imus, 无 footer,
#          V2 2IMU 29帧 (1160可被58整除) → 多候选应 raise ValueError
# ============================================================
def test_h5_v2_no_footer_multi_candidate_must_fail():
    print("\n=== Test 13: H5 V2 无 num_imus 无 footer 多候选 → 必须报错 ===")
    num_frames = 29
    header = make_header(100)
    payload = b''
    for fid in range(num_frames):
        payload += struct.pack('<I', fid)
        for _ in range(2):
            payload += struct.pack('<6h', 1, 2, 3, 4, 5, 6)
            payload += b'\x00' * 6
    bin_data = header + payload  # no footer

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    h5_tmp_path = None
    try:
        import h5py
        fd, h5_tmp_path = tempfile.mkstemp(suffix='.h5')
        os.close(fd)
        with h5py.File(h5_tmp_path, 'w') as hf:
            hf.attrs['imu1_parser_version'] = 2
            # 故意不设 imu1_num_imus

        raised = False
        try:
            IMUBinParser(tmp_path, h5_file=h5_tmp_path, device_id=1).parse()
        except ValueError as e:
            raised = True
            print(f"  正确报错: {str(e)[:80]}...")

        check(raised, "H5 V2 无num_imus 无footer 多候选 应 raise ValueError")

        if not raised:
            print("  错误: 本应报错但静默通过了! (这是数据损坏漏洞)")

    finally:
        os.unlink(tmp_path)
        if h5_tmp_path and os.path.exists(h5_tmp_path):
            os.unlink(h5_tmp_path)


# ============================================================
# Test 14: H5 hw_version='V2', num_imus=2, 40字节 V2 bin
#          应判 V2 2IMU (hw_version 被识别为 V2 信号)
# ============================================================
def test_h5_hw_version_v2_with_num_imus():
    print("\n=== Test 14: H5 hw_version='V2' + num_imus=2 → 应判 V2 2IMU ===")
    num_frames = 5
    header = make_header(100)
    payload = b''
    for fid in range(num_frames):
        payload += struct.pack('<I', fid)
        for _ in range(2):
            payload += struct.pack('<6h', 1, 2, 3, 4, 5, 6)
            payload += b'\x00' * 6
    bin_data = header + payload  # 40-byte frames, no footer

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    h5_tmp_path = None
    try:
        import h5py
        fd, h5_tmp_path = tempfile.mkstemp(suffix='.h5')
        os.close(fd)
        with h5py.File(h5_tmp_path, 'w') as hf:
            hf.attrs['imu1_hw_version'] = 'V2'
            hf.attrs['imu1_num_imus'] = 2
            # 不设 parser_version

        parser = IMUBinParser(tmp_path, h5_file=h5_tmp_path, device_id=1).parse()
        check(parser.parser_version == 2,
              f"hw_version=V2 应判 parser_version=2 (got {parser.parser_version})")
        check(parser.num_imus == 2,
              f"hw_version=V2 + num_imus=2 应 num_imus=2 (got {parser.num_imus})")
        check(parser.has_mag == False, "V2 has_mag == False")

        print(f"  hw_version引导: V{parser.parser_version}, num_imus={parser.num_imus}")

    finally:
        os.unlink(tmp_path)
        if h5_tmp_path and os.path.exists(h5_tmp_path):
            os.unlink(h5_tmp_path)


# ============================================================
# Test 15: H5 hw_version='V2', 无 num_imus, 无 footer, 多候选
#          应 raise ValueError (不能 3→2→1 静默选择)
# ============================================================
def test_h5_hw_version_v2_multi_candidate_must_fail():
    print("\n=== Test 15: H5 hw_version='V2' 无num_imus 无footer 多候选 → 必须报错 ===")
    num_frames = 29
    header = make_header(100)
    payload = b''
    for fid in range(num_frames):
        payload += struct.pack('<I', fid)
        for _ in range(2):
            payload += struct.pack('<6h', 1, 2, 3, 4, 5, 6)
            payload += b'\x00' * 6
    bin_data = header + payload

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        write_bin(tmp.name, bin_data)
        tmp_path = tmp.name

    h5_tmp_path = None
    try:
        import h5py
        fd, h5_tmp_path = tempfile.mkstemp(suffix='.h5')
        os.close(fd)
        with h5py.File(h5_tmp_path, 'w') as hf:
            hf.attrs['imu1_hw_version'] = 'V2'
            # 不设 num_imus, 不设 parser_version

        raised = False
        try:
            IMUBinParser(tmp_path, h5_file=h5_tmp_path, device_id=1).parse()
        except ValueError as e:
            raised = True
            print(f"  正确报错: {str(e)[:80]}...")

        check(raised, "H5 hw_version=V2 无num_imus 无footer 多候选 应 raise ValueError")

    finally:
        os.unlink(tmp_path)
        if h5_tmp_path and os.path.exists(h5_tmp_path):
            os.unlink(h5_tmp_path)


# ============================================================
# Test 16: sync_h5_with_bin() 集成 — 短数据 chunk shape 验证
# ============================================================
def test_sync_integration_small_data():
    print("\n=== Test 16: sync_h5_with_bin 短数据集成 (chunk shape bug) ===")
    import h5py
    from bin_sync_tool import sync_h5_with_bin, EMG_MAGIC, HEADER_SIZE, FOOTER_SIZE
    from bin_sync_tool import FOOTER_MAGIC_IMU

    h5_path = None
    emg_bin_path = None
    imu_bin_path = None

    try:
        # ---- 构建 H5 ----
        emg_250hz_dtype = np.dtype([
            ("frame_id", "<u4"),
            ("channels", "<i4", (16,)),
            ("time", "<f8")
        ])
        emg_2khz_dtype = np.dtype([
            ("channels", "<i4", (16,)),
            ("sd_frame_id", "<u4"),
            ("time", "<f8")
        ])
        imu_legacy_dtype = np.dtype([
            ("acc", "<f4", (3,)),
            ("gyr", "<f4", (3,)),
            ("mag", "<f4", (3,)),
            ("sd_frame_id", "<u4"),
            ("time", "<f8")
        ])

        fd, h5_path = tempfile.mkstemp(suffix='.h5')
        os.close(fd)
        with h5py.File(h5_path, 'w') as hf:
            hf.attrs['sync_status'] = 'pending'
            # EMG 250Hz: 2 BLE frames
            ds = hf.create_dataset('emg1_250hz_adc', shape=(2,), dtype=emg_250hz_dtype)
            ds[0] = (0, np.zeros(16, dtype=np.int32), 0.0)
            ds[1] = (1, np.zeros(16, dtype=np.int32), 0.004)

            # EMG 2kHz: empty resizable
            hf.create_dataset('emg1_2khz_adc', shape=(0,), maxshape=(None,),
                              dtype=emg_2khz_dtype)
            # IMU legacy: empty resizable
            hf.create_dataset('imu1a_100hz', shape=(0,), maxshape=(None,),
                              dtype=imu_legacy_dtype)
            hf.create_dataset('imu1b_100hz', shape=(0,), maxshape=(None,),
                              dtype=imu_legacy_dtype)

        # ---- 构建 EMG bin (16 帧) ----
        ts = b"2024-01-01_00:00:00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        emg_header = struct.pack('<I H B B B 32s', EMG_MAGIC, 2000, 5, 24, 0, ts)
        emg_header = emg_header.ljust(HEADER_SIZE, b'\x00')
        emg_payload = b''
        for fid in range(16):
            emg_payload += struct.pack('<I', fid)
            emg_payload += b'\x01' * 48  # dummy channel data
        emg_data = emg_header + emg_payload

        fd, emg_bin_path = tempfile.mkstemp(suffix='_emg.bin')
        os.close(fd)
        with open(emg_bin_path, 'wb') as f:
            f.write(emg_data)

        # ---- 构建 V2 3IMU IMU bin (1帧 + footer) ----
        imu_header = struct.pack('<I H B B B 32s', 0xBBCCDDEE, 100, 0, 0, 0, ts)
        imu_header = imu_header.ljust(HEADER_SIZE, b'\x00')
        imu_payload = struct.pack('<I', 0)  # frame_id=0
        for _ in range(3):
            imu_payload += struct.pack('<6h', 1, 2, 3, 4, 5, 6)
            imu_payload += b'\x00' * 6
        # Footer: total_frames=1
        imu_footer = struct.pack('<I', FOOTER_MAGIC_IMU)
        imu_footer += struct.pack('<4I', 1, 0, 0, 0)
        imu_footer += struct.pack('B', 2)
        imu_footer = imu_footer.ljust(FOOTER_SIZE, b'\x00')
        imu_data = imu_header + imu_payload + imu_footer

        fd, imu_bin_path = tempfile.mkstemp(suffix='_imu.bin')
        os.close(fd)
        with open(imu_bin_path, 'wb') as f:
            f.write(imu_data)

        # ---- 执行同步 ----
        result = sync_h5_with_bin(
            h5_path, emg_bin_path, imu_bin_path,
            device_id=1, verify=False, set_synced=True
        )
        check(result['status'] == 'success',
              f"sync status == success (got {result['status']})")
        check(result.get('imu_status') == 'success',
              f"imu_status == success (got {result.get('imu_status')})")
        check(result.get('imu_parser_version') == 2,
              f"imu_parser_version == 2 (got {result.get('imu_parser_version')})")
        check(result.get('imu_num_imus') == 3,
              f"imu_num_imus == 3 (got {result.get('imu_num_imus')})")
        check(result.get('imu_all_rows') == 3,
              f"imu_all_rows == 3 (got {result.get('imu_all_rows')})")

        # ---- 验证 HDF5 内容 ----
        with h5py.File(h5_path, 'r') as hf:
            # all_100hz 存在且 shape 正确
            check('imu1_all_100hz' in hf,
                  "imu1_all_100hz 数据集存在")
            ds_all = hf['imu1_all_100hz']
            check(ds_all.shape == (3,),
                  f"imu1_all_100hz shape == (3,) (got {ds_all.shape})")

            # attrs
            check(ds_all.attrs.get('parser_version') == 2,
                  "attr parser_version == 2")
            check(ds_all.attrs.get('num_imus') == 3,
                  "attr num_imus == 3")
            check(ds_all.attrs.get('has_mag') == 0,
                  "attr has_mag == 0")
            check(ds_all.attrs.get('row_layout') == 'one_row_per_imu_per_timestamp',
                  "attr row_layout 正确")

            # 数据内容
            row0 = ds_all[0]
            check(row0['imu_index'] == 0, f"row0 imu_index == 0 (got {row0['imu_index']})")
            check(row0['has_mag'] == 0, f"row0 has_mag == 0")
            check(np.isnan(row0['mag'][0]), "row0 mag[0] is NaN (V2)")
            check(np.isnan(row0['mag'][1]), "row0 mag[1] is NaN (V2)")
            check(np.isnan(row0['mag'][2]), "row0 mag[2] is NaN (V2)")
            check(row0['sd_frame_id'] == 0, f"row0 sd_frame_id == 0")
            # acc should be non-zero (from real IMU data)
            check(abs(row0['acc'][0]) > 0, "row0 acc_x != 0")

            row1 = ds_all[1]
            check(row1['imu_index'] == 1, f"row1 imu_index == 1 (got {row1['imu_index']})")

            row2 = ds_all[2]
            check(row2['imu_index'] == 2, f"row2 imu_index == 2 (got {row2['imu_index']})")

            # legacy datasets
            check('imu1a_100hz' in hf, "imu1a_100hz legacy 仍存在")
            check('imu1b_100hz' in hf, "imu1b_100hz legacy 仍存在")
            check(hf['imu1a_100hz'].shape == (1,),
                  f"imu1a shape == (1,) (got {hf['imu1a_100hz'].shape})")
            check(hf['imu1b_100hz'].shape == (1,),
                  f"imu1b shape == (1,) (got {hf['imu1b_100hz'].shape})")

        print(f"  集成测试通过: all_100hz shape=(3,), legacy a/b=(1,), V2 attrs 正确")

    finally:
        for p in [h5_path, emg_bin_path, imu_bin_path]:
            if p and os.path.exists(p):
                os.unlink(p)


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("IMUBinParser 自测")
    print("=" * 60)

    test_v2_3imu()
    test_v1_2imu()
    test_v2_2imu_with_footer_ambiguous()
    test_ambiguous_40byte_no_metadata()
    test_v2_footer_content()
    test_v1_29frames_not_v2()
    test_v1_11frames_not_v2()
    test_h5_v2_no_numimus_infer_3()
    test_v2_missing_frame_mag_nan()
    test_v2_2imu_footer_29frames()
    test_v2_1imu_footer_20frames()
    test_v2_3imu_footer_20frames()
    test_h5_v2_no_footer_multi_candidate_must_fail()
    test_h5_hw_version_v2_with_num_imus()
    test_h5_hw_version_v2_multi_candidate_must_fail()
    test_sync_integration_small_data()

    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"结果: {PASS}/{total} 通过, {FAIL}/{total} 失败")
    if FAIL > 0:
        print("FAIL: 存在失败项，请检查！")
        sys.exit(1)
    else:
        print("PASS: 所有测试通过！")
        sys.exit(0)
