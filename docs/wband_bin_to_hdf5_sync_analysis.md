# 腕带 bin 解析与 HDF5 同步适配分析

## 1. 结论摘要

- **V1 bin 结构**：EMG 帧=52字节(4头+48数据)，IMU 帧=40字节(4头+2×18)，IMU 每芯片含 Acc(3)+Gyro(3)+Mag(3) 共9轴，Acc/Gyro 大端，Mag 小端
- **V2 bin 结构**：EMG 帧同 V1 (52字节)，IMU 帧=4 + N×18 字节（N=1/2/3 可变），每芯片仅 Acc(3)+Gyro(3) 共6轴（**无磁力计**），Acc/Gyro **小端**，有 Footer 尾块
- **两者核心差异**：① IMU 帧长可变 vs 固定 ② 端序相反 ③ V2 无 Mag ④ V2 Accel 量程翻倍(32g vs 16g) ⑤ V2 支持 3 个 IMU ⑥ V2 通道映射调整
- **当前 hdf5_tool.py sync 功能差在哪里**：`bin_sync_tool.py` 的 `IMUBinParser` 硬编码 V1 结构（固定 40 字节帧、大端解析、含 Mag、固定 2 IMU），**完全不兼容 V2 IMU bin**
- **后续适配建议**：抽象 IMU parser 支持 V1/V2 自动检测，IMU bin 帧长自适应，端序自适应，HDF5 中新增 `imu{dev}_all_ble` / `imu{dev}_all_100hz` 数据集存储可变数量 IMU

## 2. V1: wband_emg_client_V3.py bin 转 csv 流程

### 2.1 入口函数

| 功能 | 函数/方法 | 行号 |
|------|----------|------|
| 智能识别 bin 类型 | `smart_convert_bin_to_csv()` | L898 |
| EMG bin → csv | `process_emg_bin()` | L929 |
| IMU bin → csv | `process_imu_bin()` | L1002 |
| BLE 实时解析 | `notification_handler()` | L442 |

### 2.2 bin 文件识别

通过前 4 字节 Magic Word 区分：
- `0xAABBCCDD` → EMG bin
- `0xBBCCDDEE` → IMU bin

### 2.3 文件头结构 (126 字节)

```
Offset  Size  Field        Type
0       4     Magic         uint32 LE
4       2     SampleRate    uint16 LE
6       1     GainIdx       uint8
7       1     BitDepth      uint8
8       1     ImuEn         uint8
9       32    Timestamp     char[32] (ASCII)
41      85    (reserved/padding)
```

### 2.4 EMG bin 帧结构

- **帧大小**: 4 (Counter uint32 LE) + 16通道 × 3字节 = **52 字节**
- **每通道**: 3 字节 (24-bit)，Big Endian，signed
- **采样率**: 由 header 中 `sample_rate` 字段决定
- **LSB 转换系数**:
  - `base_lsb = 0.476837` (4.0V ref)
  - `lsb_uV = base_lsb / (actual_gain * 10)` (10 = hardware frontend gain)
  - 16-bit 模式: `lsb_uV *= 2**4` (补偿固件右移)
- **CSV 列**: `Frame_Counter, Time_Sec, CH1_uV ~ CH16_uV`

关键代码 (`process_emg_bin`, L972-L988):
```
counter = struct.unpack('<I', chunk[0:4])[0]
val = int.from_bytes(raw_data[...], 'big', signed=True)
emg_vals.append(val * lsb_uV)
```

### 2.5 IMU bin 帧结构

- **帧大小**: 4 (Counter uint32 LE) + 2 × 18 = **40 字节** (固定)
- **每芯片 18 字节解析** (`parse_chip`, L1049-1056):
  - `[0:12]`: Acc(3轴) + Gyro(3轴)，**Big Endian** (`>6h`)，signed 16-bit
  - `[12:18]`: Mag(3轴)，**Little Endian** (`<3h`)，signed 16-bit
- **转换系数**:
  - `SCALE_ACCEL = 16.0 / 32768.0` (±16g)
  - `SCALE_GYRO = 2000.0 / 32768.0` (±2000dps)
  - `SCALE_MAG = 0.15` (0.15 uT/LSB)
- **CSV 列**: `Frame_Counter, Time_Sec, IMU1_AX..IMU1_MZ, IMU2_AX..IMU2_MZ` (共 1+1+9+9=20 列)

### 2.6 BLE 实时包结构

在 `notification_handler()` (L442) 中解析的 BLE 包：
- **包大小**: 4(Header) + 9帧×48字节 + 36字节(IMU) = **472 字节** (固定)
- **Header**: uint32 LE (start_frame)
- **EMG 部分**: 9帧 × 16通道 × 3字节 = 432字节，每帧 48 字节，24-bit Big Endian
- **IMU 部分**: 36 字节 = 2 × 18 字节 (IMU1 + IMU2)
- **通道映射 (CHANNELS_MAP)**: `[14, 15, 16, 3, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]`
- **LSB (BLE 路径)**: `base_lsb_24bit = 0.2861` (不同于文件路径)，`lsb_uV = 0.2861 / (gain * 10)`

### 2.7 EMG bin CSV 输出列

| 列号 | 列名 | 数据来源 | 类型 |
|------|------|---------|------|
| 0 | Frame_Counter | bin 帧头 uint32 LE | int |
| 1 | Time_Sec | frame_count / sample_rate | float |
| 2-17 | CH1_uV ~ CH16_uV | 16通道 ADC × lsb_uV | float |

### 2.8 IMU bin CSV 输出列

| 列号 | 列名 | 数据来源 | 单位 |
|------|------|---------|------|
| 0 | Frame_Counter | bin 帧头 uint32 LE | — |
| 1 | Time_Sec | frame_count / sample_rate | s |
| 2-4 | IMU1_AX, AY, AZ | chip1 acc × SCALE_ACCEL | g |
| 5-7 | IMU1_GX, GY, GZ | chip1 gyr × SCALE_GYRO | deg/s |
| 8-10 | IMU1_MX, MY, MZ | chip1 mag × SCALE_MAG | uT |
| 11-13 | IMU2_AX, AY, AZ | chip2 acc × SCALE_ACCEL | g |
| 14-16 | IMU2_GX, GY, GZ | chip2 gyr × SCALE_GYRO | deg/s |
| 17-19 | IMU2_MX, MY, MZ | chip2 mag × SCALE_MAG | uT |

## 3. V2: wband_emg_client_V5.py bin 转 csv 流程

### 3.1 入口函数

| 功能 | 函数/方法 | 行号 |
|------|----------|------|
| 智能识别 bin 类型 | `smart_convert_bin_to_csv()` | L1375 |
| EMG bin → csv | `process_emg_bin()` | L1407 |
| IMU bin → csv | `process_imu_bin()` | L1509 |
| BLE 实时解析 | `notification_handler()` | L873 |

### 3.2 bin 文件识别

与 V1 相同：Magic `0xAABBCCDD` (EMG) / `0xBBCCDDEE` (IMU)。

### 3.3 文件头结构 (126 字节)

与 V1 一致。新增 **Footer 支持** (V4.1+):

```
文件末尾 36 字节 Footer:
Offset  Size  Field
0       4     Magic (EMG: 0xDDCCBBAA, IMU: 0xEEDDCCBB)
4       16    统计信息 (4×uint32: total, sd_drop, imu_drop, ble_drop)
20      1     stop_reason (uint8)
21      15    (reserved)
```

`process_emg_bin` 和 `process_imu_bin` 均在文件末尾检测 Footer magic 并据此调整数据区大小。

### 3.4 EMG bin 帧结构

**与 V1 完全一致**：52 字节，24-bit Big Endian，16 通道。CSV 输出列相同。

### 3.5 IMU bin 帧结构 (核心差异)

- **帧大小可变**: `4 + N × 18` 字节（N = 1/2/3 个 IMU）
- **自动检测 IMU 数量** (L1555-1562): 从 N=3 向下尝试，找到满足 `data_size % (4 + N*18) == 0` 的最大 N
- **每芯片 18 字节解析** (`parse_chip`, L1582-1587):
  - `[0:12]`: Acc(3轴) + Gyro(3轴)，**Little Endian** (`<6h`)，signed 16-bit
  - `[12:18]`: **Reserved (不使用)** — **无磁力计数据**
- **转换系数**:
  - `SCALE_ACCEL = 32.0 / 32768.0` (±32g，**V1 的 2 倍**)
  - `SCALE_GYRO = 2000.0 / 32768.0` (与 V1 相同)
- **CSV 列**: `Frame_Counter, Time_Sec, IMU1_AX..IMU1_GZ, IMU2_AX..IMU2_GZ, [IMU3_AX..IMU3_GZ]` (**无 Mag 列**)

### 3.6 BLE 实时包结构 (V2 差异)

在 `notification_handler()` (L873) 中：
- **包大小可变**: `4(Header) + 432(EMG) + N×18(IMU)`, N 由实际数据长度推断
- **IMU 解析** (`parse_single_imu`, L943-947):
  - `[0:12]`: Acc(3)+Gyro(3)，**Little Endian** (`<6h`)
  - **不解析 Mag**
- **IMU 数量检测**: `detected_num_imus = (payload_len - 432) // 18`
- **通道映射变更 (CHANNELS_MAP)**: V5=`[15, 16, 14, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]` (前三个与 V1 不同)

### 3.7 EMG bin CSV 输出列

与 V1 相同。

### 3.8 IMU bin CSV 输出列 (V2)

| 列号 | 列名 | 数据来源 | 单位 |
|------|------|---------|------|
| 0 | Frame_Counter | bin 帧头 uint32 LE | — |
| 1 | Time_Sec | frame_count / sample_rate | s |
| 2-4 | IMU1_AX, AY, AZ | chip1 acc × SCALE_ACCEL | g |
| 5-7 | IMU1_GX, GY, GZ | chip1 gyr × SCALE_GYRO | deg/s |
| 8-13 | IMU2_AX..GZ | chip2 (同上) | g, deg/s |
| 14-19 | IMU3_AX..GZ | chip3 (同上，如有) | g, deg/s |

注意：**无 Mag 列**。列数 = 2 + N×6。

### 3.9 V2 相对 V1 的差异总结

| 差异项 | V1 | V2 |
|--------|----|----|
| IMU 数量 | 固定 2 | 可变 1/2/3 (自动检测) |
| IMU 轴数/芯片 | 9 (Acc+Gyro+Mag) | **6 (仅 Acc+Gyro)** |
| Acc/Gyro 端序 | Big Endian (`>6h`) | **Little Endian (`<6h`)** |
| Accel 量程 | ±16g | **±32g** (系数翻倍) |
| IMU 帧大小 | 固定 40 字节 | **可变** 22/40/58 字节 |
| 磁力计 | 有 (3轴) | **无** |
| Footer | 无 | **有** (36字节尾块) |
| 通道映射 | [14,15,16,3,1,2,...] | [15,16,14,1,2,3,...] |

## 4. V1/V2 字段映射对比表

### 4.1 BLE 实时包结构

| 字段类别 | V1 字段/结构 | V2 字段/结构 | 数据类型 | 单位/缩放 | 备注 |
|---------|-------------|-------------|---------|----------|------|
| 包头 | start_frame (4B LE) | start_frame (4B LE) | uint32 | — | 相同 |
| EMG 帧数/包 | 9 帧 (固定) | 9 帧 (固定) | — | — | 相同 |
| EMG 每帧 | 48 字节 (16ch×3B) | 48 字节 (16ch×3B) | int24 BE | ADC raw | 相同 |
| IMU 数据/包 | 36 字节 (2×18) | N×18 字节 (N=1/2/3) | — | — | **V2 可变** |
| IMU 单芯片 | Acc(6B)+Gyro(6B)+Mag(6B) | Acc(6B)+Gyro(6B)+Reserved(6B) | int16 | — | **V2 无 Mag** |
| Acc/Gyro 端序 | Big Endian (`>6h`) | **Little Endian (`<6h`)** | int16 | — | **相反** |
| Acc 系数 | 16.0 / 32768 | **32.0 / 32768** | float | g | **V2 翻倍** |
| Gyro 系数 | 2000.0 / 32768 | 2000.0 / 32768 | float | deg/s | 相同 |
| Mag 系数 | 0.15 | — (不存在) | float | uT | **V2 移除** |
| 通道映射 | [14,15,16,3,1,2,4...] | [15,16,14,1,2,3,4...] | — | — | 前三个不同 |
| 包总长 | 472 字节 (固定) | 4+N×18+432 字节 (可变) | — | — | **V2 可变** |

### 4.2 SD 卡 bin 文件结构

| 字段类别 | V1 字段/列名 | V2 字段/列名 | 数据类型 | 单位/缩放 | 备注 |
|---------|-------------|-------------|---------|----------|------|
| 文件头 | 126 字节 | 126 字节 | — | — | 相同 |
| Footer | 无 | 36 字节 (可选) | — | — | **V2 新增** |
| EMG 帧 | Counter(4)+16ch×3B = 52B | Counter(4)+16ch×3B = 52B | int24 BE | ADC raw | 相同 |
| IMU 帧 | Counter(4)+36B = **40B 固定** | Counter(4)+N×18B = **22/40/58B 可变** | — | — | **V2 可变** |
| IMU 单芯片 | Acc(6)+Gyro(6)+Mag(6)=18B | Acc(6)+Gyro(6)+Pad(6)=18B | int16 | — | **V2 无 Mag** |
| EMG CSV 列数 | 2 + 16 = 18 | 2 + 16 = 18 | — | — | 相同 |
| IMU CSV 列数 | 2 + 18 = 20 | 2 + N×6 (8/14/20) | — | — | **V2 可变** |
| LSB 基准 (文件) | 0.476837 (4.0V ref) | 0.476837 (4.0V ref) | — | μV/LSB | 相同 |
| LSB 基准 (BLE) | 0.2861 (2.4V ref) | 0.2861 (2.4V ref) | — | μV/LSB | 相同 |

## 5. hdf5_tool.py 当前 sync 实现

### 5.1 架构概述

`tools/hdf5_tool.py` 是 GUI 壳（PyQt5），**实际 sync 逻辑在 `tools/bin_sync_tool.py`**。

调用链：
```
hdf5_tool.py SyncWorker.run()
  → _find_bin_files()  # 从 H5 属性读取 bin 前缀，拼接路径
  → bin_sync_tool.sync_h5_with_bin()
      → EMGBinParser.parse()   # 解析 EMG bin
      → IMUBinParser.parse()   # 解析 IMU bin
      → sync_h5_with_bin()     # 帧号映射 + 写入 HDF5
```

### 5.2 关键函数位置

| 功能 | 文件 | 函数/类 | 行号 |
|------|------|---------|------|
| EMG bin 解析 | bin_sync_tool.py | `EMGBinParser` | L81-166 |
| IMU bin 解析 | bin_sync_tool.py | `IMUBinParser` | L169-227 |
| 同步主逻辑 | bin_sync_tool.py | `sync_h5_with_bin()` | L232-591 |
| 查找 bin 文件 | hdf5_tool.py | `SyncWorker._find_bin_files()` | L61-101 |
| 同步入口 | hdf5_tool.py | `SyncWorker.run()` | L103-225 |

### 5.3 bin 文件定位方式

`hdf5_tool.py` 的 `_find_bin_files()` (L61-101):
1. 从 H5 属性 `sd_bin_dev{1|2}` 读取 bin 文件名前缀
2. 拼接 `{prefix}_emg.bin` 和 `{prefix}_imu.bin`
3. 在用户选择的 bin 目录中查找

### 5.4 当前 EMG 解析 (bin_sync_tool.py EMGBinParser, L81-166)

- 帧大小：**固定 52 字节** (4 + 16×3)
- 端序：Big Endian (24-bit signed)
- 存储：原始 ADC 值 (`int`)，不转换 μV
- V1/V2 兼容性：**EMG 帧结构 V1 和 V2 相同，此 parser 对两者均适用**

### 5.5 当前 IMU 解析（bin_sync_tool.py IMUBinParser, L169-227）— **仅兼容 V1**

```python
IMU_FRAME_SIZE = 4 + 36  # L53: 硬编码 40 字节 = 2 IMU
```

解析逻辑 (L211-218):
```python
def parse_chip(b):
    ag = struct.unpack('>6h', b[0:12])    # Big Endian (V1)
    m = struct.unpack('<3h', b[12:18])     # Mag Little Endian
    return {
        'acc': [x * SCALE_ACCEL for x in ag[0:3]],    # 16.0/32768
        'gyr': [x * SCALE_GYRO for x in ag[3:6]],
        'mag': [x * SCALE_MAG for x in m[0:3]]
    }
```

**与 V2 冲突点**:
1. 帧大小固定 40 字节 → V2 的 3 IMU 帧为 58 字节，解析偏移全错
2. Acc/Gyro 大端 `>6h` → V2 用小端 `<6h`
3. Mag 字段解析 → V2 没有 Mag（字节 12-17 是 reserved）
4. `SCALE_ACCEL = 16.0/32768.0` → V2 是 `32.0/32768.0`
5. 无 IMU 数量自适应

### 5.6 当前 HDF5 写入路径

| HDF5 路径 | 内容 | 数据类型 | 写入条件 |
|-----------|------|---------|---------|
| `emg{dev}_250hz_adc` | BLE 250Hz 数据 (只读，用作输入) | 结构化 (frame_id, channels, time) | 必须存在 |
| `emg{dev}_2khz_adc` | 同步后 2kHz 数据 (写入) | 结构化 (channels, sd_frame_id, time) | 创建或覆盖 |
| `imu{dev}a_100hz` | IMU-A 100Hz 数据 | 结构化 (acc, gyr, mag, sd_frame_id, time) | 如存在则写入 |
| `imu{dev}b_100hz` | IMU-B 100Hz 数据 | 结构化 (acc, gyr, mag, sd_frame_id, time) | 如存在则写入 |
| `imu{dev}_100hz` | 旧版 IMU 数据集 (兼容) | 同上 | 如 a/b 不存在则尝试 |

**IMU 写入策略** (L496-563):
- IMU-A 使用 chip1 数据
- IMU-B 使用 chip2 数据
- 每个数据集 dtype 固定为 `(acc, gyr, mag, sd_frame_id, time)` — **始终包含 mag**
- **最多写 2 个 IMU，不支持 3 个 IMU**

### 5.7 帧号映射关系

- EMG: SD帧号 = BLE帧号 × 8 + offset (0~7)
- IMU: IMU帧号 = EMG_SD帧号 ÷ 20
- 采样率: EMG 2kHz(SD) / 250Hz(BLE), IMU 100Hz(SD) / ~28Hz(BLE)

### 5.8 HDF5 属性面板已预留的 V2 字段

`hdf5_tool.py` 的 `StatisticsPanel` (L270-510) 已展示了 V2 相关字段：
- `imu1_all_ble` / `imu2_all_ble` — V1/V2 通用 IMU 数据集
- `imu1_hw_version` / `imu2_hw_version` — 硬件版本
- `imu1_num_imus` / `imu2_num_imus` — IMU 数量
- `total_imu1_all_frames` / `total_imu2_all_frames` — 帧数统计

说明 GUI 层已经为 V2 预留了展示空间，但底层 `bin_sync_tool.py` 的解析/写入逻辑尚未适配。

## 6. 新腕带 V2 适配 hdf5_tool.py 的设计建议

### 6.1 V1/V2 bin 区分策略

推荐 **按 IMU bin 帧长自动检测**（无需文件名字段或用户参数）：

```
优先级：
1. 读取 IMU bin 文件头 126 字节后，计算 data_size = file_size - header - footer(如有)
2. 尝试 N=3,2,1: 若 data_size % (4 + N*18) == 0，则确定 N
3. 若只有 1 种 N 满足整除 → 确定版本
4. 若 N=2 时满足整除（V1 和 V2 都可能）→ 进一步判断：
   - V1 标记：含 Mag（字节 12-17 有非零值），大端 Acc/Gyro
   - V2 标记：无 Mag（字节 12-17 为 0x000000 或 reserved），小端 Acc/Gyro
   或更简单：看 H5 属性中是否有 `imu{dev}_num_imus` / `imu{dev}_hw_version` 字段
5. 备选：H5 属性显式存储 `imu{dev}_parser_version` (1 或 2)
```

### 6.2 Parser 抽象建议

在 `bin_sync_tool.py` 中新增：

```python
class IMUBinParserV2:
    """V2 IMU bin 解析器：可变IMU数量(1-3)，无Mag，小端Acc/Gyro，Accel量程±32g"""
    - 自动检测 num_imus
    - 帧大小 = 4 + num_imus * 18
    - Acc/Gyro: struct.unpack('<6h', ...)  # 小端
    - 无 Mag
    - SCALE_ACCEL = 32.0 / 32768.0

class IMUBinParserV1:  # 重命名现有 IMUBinParser
    """V1 IMU bin 解析器：固定2 IMU，含Mag，大端Acc/Gyro，Accel量程±16g"""
    - 帧大小 = 40 字节 (固定)
    - Acc/Gyro: struct.unpack('>6h', ...)  # 大端
    - 含 Mag
    - SCALE_ACCEL = 16.0 / 32768.0

def create_imu_parser(bin_path) -> Union[IMUBinParserV1, IMUBinParserV2]:
    """工厂函数：自动检测并返回合适的 parser"""
```

### 6.3 HDF5 中 IMU 存储建议

**推荐方案**：新增 `imu{dev}_all_100hz` 统一数据集，**与 `imu{dev}_all_ble` 完全对齐**。

#### 数据组织方式

采用 **"一行 = 一个 IMU 在一个时间点"** 的行式组织（与 BLE 侧 `IMU_ALL_BLE_DTYPE` 一致，定义于 `storage_server.py:96-105`）：

```python
# 推荐 dtype，与 IMU_ALL_BLE_DTYPE 对应
IMU_ALL_100HZ_DTYPE = np.dtype([
    ("imu_index", "<u1"),    # IMU 索引 (0-based, 对应物理 I2C 地址)
    ("acc", "<f4", (3,)),    # 加速度计 [ax, ay, az]
    ("gyr", "<f4", (3,)),   # 陀螺仪 [gx, gy, gz]
    ("has_mag", "<u1"),      # 是否有磁力计数据 (V1=1, V2=0)
    ("mag", "<f4", (3,)),   # 磁力计 [mx, my, mz] (V2 填充 NaN)
    ("sd_frame_id", "<u4"), # IMU SD 卡帧号
    ("time", "<f8")         # 时间戳
])
```

#### 实例说明

假设某个时间点 T 有 3 个 IMU 的数据，则在数据集中写入 **3 行**：

| imu_index | acc | gyr | has_mag | mag | sd_frame_id | time |
|-----------|-----|-----|---------|-----|-------------|------|
| 0 | [ax0,ay0,az0] | [gx0,gy0,gz0] | 0 | [NaN,NaN,NaN] | 100 | T |
| 1 | [ax1,ay1,az1] | [gx1,gy1,gz1] | 0 | [NaN,NaN,NaN] | 100 | T |
| 2 | [ax2,ay2,az2] | [gx2,gy2,gz2] | 0 | [NaN,NaN,NaN] | 100 | T |

#### 为什么采用这种行式结构

1. **与 BLE 侧对齐**：`imu{dev}_all_ble` 已使用此结构（`storage_server.py` L837-847），sync 侧与之保持一致，下游消费者无需处理两种不同的数据形状
2. **自然支持可变 IMU 数量**：1 个 IMU 写 1 行，3 个 IMU 写 3 行，不需要预留空列或创建/删除数据集，追加即用
3. **`has_mag` 标记解决 Mag 有无歧义**：V1 设 1 且 mag 填有效值，V2 设 0 且 mag 填 NaN，不会出现"mag=0 是零值还是缺失"的二义性
4. **时间点分组**：同一 `sd_frame_id` 的所有行属于同一采样时刻，按 `(time, imu_index)` 即可还原时序关系

#### 备选方案（不推荐）

保留现有 `imu{dev}a/b_100hz` + 新增 `imu{dev}c_100hz`，每个数据集一个 IMU、一行一个时间点。缺点：IMU 数量变化时需要创建/删除数据集；V2 时 mag 填零无法区分"零值"和"无传感器"。

### 6.4 兼容策略

| 场景 | 策略 |
|------|------|
| V1 旧 H5 + V1 bin | 现有逻辑，不变 |
| V1 旧 H5 + V2 bin | 不应出现（硬件不匹配），检测后报错 |
| V2 新 H5 + V2 bin | 使用 V2 parser，写入 `imu{dev}_all_100hz` |
| V2 新 H5 + V1 bin | 不应出现，检测后报错 |
| 已有 synced H5 文件 | 不重新同步（sync_status='synced' 跳过） |

H5 属性中新增：
- `imu{dev}_parser_version`: 1 或 2
- `imu{dev}_num_imus`: 1/2/3 (V2) 或 2 (V1)
- `imu{dev}_has_mag`: true (V1) / false (V2)

### 6.5 需要补的测试样例

1. V1 IMU bin (2IMU, 含Mag, 大端) → 解析正确性验证
2. V2 IMU bin (2IMU, 无Mag, 小端) → 解析正确性验证
3. V2 IMU bin (3IMU, 无Mag, 小端) → 解析正确性验证
4. V2 IMU bin (1IMU) → 解析正确性验证
5. 带 Footer 的 V2 bin → Footer 跳过正确性
6. 自动检测: 给定 40 字节帧 bin + 无版本属性 → 正确识别为 V1 还是 V2

## 7. 风险点和待确认问题

| # | 风险/问题 | 严重程度 | 建议 |
|---|----------|---------|------|
| 1 | V2 bin 只有 2 IMU 时帧大小=40 字节，与 V1 完全相同，仅凭帧长无法区分版本 | **高** | 需要 H5 属性 `imu_hw_version` 或 `imu_parser_version` 辅助判断；或分析数据内容（Mag 字节区是否全零） |
| 2 | V2 无磁力计，现有 HDF5 IMU dtype 含 `mag` 字段 | 中 | V2 时 mag 填 0 或 NaN，设置 `has_mag=0` 属性；消费者需能处理无 mag 的情况 |
| 3 | BLE 侧 `imu{dev}_all_ble` 已有 V2 支持（带 `imu_index`/`has_mag`），但 sync 侧 IMU 写入路径未对齐 | 中 | sync 侧应与 BLE 侧数据格式对齐 |
| 4 | V2 Acc 量程翻倍 (32g vs 16g)，如果 sync 用错系数，数值偏差 2 倍 | **高** | 必须根据版本选择正确的 SCALE_ACCEL |
| 5 | V2 Acc/Gyro 端序相反，用错端序会导致数值完全错误 | **高** | 关键差异，parser 必须区分 |
| 6 | 3 IMU 场景下物理位置命名未定义 (哪一个是 IMU1/IMU2/IMU3) | 中 | 需与硬件团队确认，暂时按 bin 中出现顺序编号 |
| 7 | 现有 HDF5 消费者是否假设 IMU 固定为 2 个 | 中 | 需排查下游分析代码，可能需要同时写入 a/b 兼容路径 |
| 8 | bin 文件无显式版本号字段 | 中 | 需依赖外部元数据（H5 属性）或启发式检测 |
| 9 | 带 Footer 的 V2 bin：当前 EMGBinParser/IMUBinParser 均未处理 Footer（会把 Footer 当数据帧解析） | 中 | 需在 parser 中检测并跳过 Footer 区域 |
| 10 | V2 EMG bin 通道映射可能已变更（V5 CHANNELS_MAP 与 V3 不同），但 bin 文件中数据按物理通道顺序存储，映射只在 BLE 侧生效 | 低 | bin 文件数据本身不受影响，LSB 系数转换时注意 |

---

> **生成信息**: 阅读了 `wband_emg_V1/wband_emg_client_V3.py` (1092行)、`wband_emg_V2/wband_emg_client_V5.py` (1639行)、`tools/hdf5_tool.py` (1860行)、`tools/bin_sync_tool.py` (933行)。生成了 `docs/wband_bin_to_hdf5_sync_analysis.md`。建议下一步：评审本文档确认差异分析无误后，进入 sync 代码改造阶段。
