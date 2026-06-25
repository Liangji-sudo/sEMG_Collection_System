# ble_server.py V1→V2 适配修改方案

**日期**: 2026-05-21
**原则**: 仅改动数据包解析层，不修改任何操作逻辑（连接/断开/启停/订阅流程保持不变）

---

## 1. 修改范围总览

| 项目 | 是否修改 | 说明 |
|------|---------|------|
| 数据包解析 (`parse_packet`) | **是** | IMU 解析适配 V2 |
| 包长校验 | **是** | 固定→动态 |
| 通道映射 | **是** | 新增，对齐供应商上位机 |
| V1/V2 自动检测 | **是** | 方法A: STATUS_CHAR 特征判断 |
| STATUS_CHAR 订阅 | **是** | 仅在 connect 成功后增加一条订阅 |
| IMU 转换系数 | **是** | SCALE_ACCEL 变更 |
| 连接/断开/扫描流程 | **否** | 不动 |
| start_stream/stop_stream | **否** | 不动 |
| 消息路由/线程模型 | **否** | 不动 |
| 滤波器系统 | **否** | 不动 |
| 远程关机 | **否** | 不做 |
| 前端控制命令 | **否** | 不动 |

---

## 2. 改动点 #1: 常量区

### 2.1 修改现有常量

```python
# 修改前:
SCALE_ACCEL = 16.0 / 32768.0
SCALE_MAG = 0.15

# 修改后:
SCALE_ACCEL = 32.0 / 32768.0      # V2 默认 ±32g (LSM6DSV32X)
SCALE_ACCEL_V1 = 16.0 / 32768.0   # 保留 V1 备用
SCALE_GYRO = 2000.0 / 32768.0     # 不变
SCALE_MAG = 0.15                  # 保留，V1 设备仍需要
```

### 2.2 新增常量

```python
# IMU 配置
BYTES_PER_IMU = 18             # 单 IMU 数据长度 (Acc6+Gyro6+Reserved6)
MAX_NUM_IMUS_V2 = 3            # V2 最多 3 个 IMU
MAX_NUM_IMUS_V1 = 2            # V1 固定 2 个 IMU

# 通道映射 (物理CH=1-indexed, 按供应商上位机排列顺序)
CHANNELS_MAP_V1 = [14, 15, 16, 3, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
CHANNELS_MAP_V2 = [15, 16, 14, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]

# 状态特征 (V2 新增)
STATUS_CHAR_UUID = "9e5c100d-afc2-4e4b-b132-f2c0032f7a0e"
STATUS_PACKET_SNAPSHOT = 0x01
STATUS_PACKET_EVENT = 0x02
STATUS_SNAPSHOT_FORMAT = "<BBBBHBBHBBIIIIIII16s16s"
```

### 2.3 新增 DeviceState 字段

```python
@dataclass
class DeviceState:
    # ... 现有字段保持不变 ...

    # 【新增】V2 设备状态
    hw_version: str = "V1"           # 硬件版本: "V1" 或 "V2"
    firmware_version: str = ""        # 固件版本字符串
    hardware_version: str = ""        # 硬件版本字符串
    num_imus: int = 2                 # 检测到的 IMU 数量
    channel_map: list = field(default_factory=lambda: CHANNELS_MAP_V1)
    status_flags: int = 0             # 设备状态标志位
    storage_state: int = 0            # SD 卡状态
    sd_free_kb: int = 0               # SD 卡剩余空间
```

---

## 3. 改动点 #2: V1/V2 检测 (方法A)

### 3.1 原理

- V2 设备注册了 `STATUS_CHAR_UUID` 特征（UUID ...0e）
- V1 设备没有这个特征
- 连接成功后尝试订阅该特征：成功 → V2，失败（异常） → V1

### 3.2 实施位置

在 `connect_device()` 函数末尾，连接成功后（现有配置命令发送完成之后），插入：

```python
# ===== 新增: V1/V2 检测 + STATUS_CHAR 订阅 =====
try:
    await dev.client.start_notify(
        STATUS_CHAR_UUID,
        create_status_handler(dev)
    )
    dev.hw_version = "V2"
    dev.channel_map = CHANNELS_MAP_V2
    dev.num_imus = 0  # 等待第一个 Snapshot 更新
    log(f"[Dev{device_id}] 检测到 V2 设备，已订阅状态通知")
except Exception as e:
    dev.hw_version = "V1"
    dev.channel_map = CHANNELS_MAP_V1
    dev.num_imus = 2
    log(f"[Dev{device_id}] V1 设备 (状态特征不存在: {e})")
# ===== 新增结束 =====
```

### 3.3 对操作逻辑的影响

**无影响**。这只是连接成功后多订阅了一个 Notify 特征。如果设备不支持（V1），`start_notify` 会抛出异常，被捕获后静默降级为 V1 模式。不会中断连接流程，不会影响 EMG 数据订阅。

---

## 4. 改动点 #3: 数据包解析 `parse_packet()`

这是核心修改，根据 `dev.hw_version` 使用不同的解析路径。

### 4.1 包长校验 — 从固定改为动态

```python
def parse_packet(data: bytearray, dev: DeviceState) -> Optional[dict]:
    params = get_packet_params(dev.config)
    
    if dev.hw_version == "V2":
        # V2: 包长可变, 4 + emg_len + N*18
        emg_len = params['emg_len']
        payload_len = len(data) - 4
        if payload_len < emg_len:
            log(f"[Dev{dev.device_id}] 包过短: {len(data)}")
            return None
        imu_byte_count = payload_len - emg_len
        if imu_byte_count % BYTES_PER_IMU != 0:
            log(f"[Dev{dev.device_id}] IMU 数据异常: {imu_byte_count} bytes")
            return None
        num_imus = imu_byte_count // BYTES_PER_IMU
        if num_imus > MAX_NUM_IMUS_V2:
            log(f"[Dev{dev.device_id}] IMU 数量超限: {num_imus}")
            return None
        # 更新实际的 IMU 数量
        if num_imus != dev.num_imus:
            dev.num_imus = num_imus
            log(f"[Dev{dev.device_id}] IMU 数量: {num_imus}")
    else:
        # V1: 固定包长 472
        if len(data) != params['total_len']:
            log(f"[Dev{dev.device_id}] 包长错误: {len(data)} != {params['total_len']}")
            return None
        num_imus = 2  # V1 固定 2 个 IMU
```

### 4.2 EMG 解析 — 新增通道映射

EMG 原始数据解析不变（始终按物理通道顺序），但在构建输出时应用通道映射：

```python
# 现有逻辑中，构建 emg_raw 和 emg_uv 后，新增:
# 应用通道映射 (对齐供应商上位机显示顺序)
emg_raw_mapped = []
emg_uv_mapped = []
for row_raw, row_uv in zip(emg_raw, emg_uv):
    mapped_raw = [row_raw[i - 1] for i in dev.channel_map]  # 1-indexed
    mapped_uv = [row_uv[i - 1] for i in dev.channel_map]
    emg_raw_mapped.append(mapped_raw)
    emg_uv_mapped.append(mapped_uv)
```

### 4.3 IMU 解析 — 按版本分叉

```python
imu = None
if config['imu_enabled']:
    if dev.hw_version == "V2":
        imu = parse_imu_v2(data, emg_len, num_imus)
    else:
        imu = parse_imu_v1(data, emg_len)

def parse_imu_v2(data, emg_len, num_imus):
    """V2 IMU: LSM6DSV32X, Little Endian, Acc+Gyro only"""
    imu_start = 4 + emg_len
    imus = []
    for i in range(num_imus):
        offset = imu_start + i * BYTES_PER_IMU
        b = data[offset: offset + BYTES_PER_IMU]
        ag = struct.unpack('<6h', b[0:12])   # V2: Little Endian
        imus.append([
            [x * SCALE_ACCEL for x in ag[0:3]],     # Accel X/Y/Z
            [x * SCALE_GYRO for x in ag[3:6]],      # Gyro X/Y/Z
        ])
    return imus

def parse_imu_v1(data, emg_len):
    """V1 IMU: ICM-20948, Big Endian Acc/Gyro + Little Endian Mag"""
    # 保持现有逻辑不变 (提取为函数)
    imu_start = 4 + emg_len
    imu_len = 36
    imu_bytes = data[imu_start: imu_start + imu_len]

    def parse_chip(b):
        ag = struct.unpack('>6h', b[0:12])       # V1: Big Endian
        m = struct.unpack('<3h', b[12:18])
        return [
            [x * SCALE_ACCEL_V1 for x in ag[0:3]],
            [x * SCALE_GYRO for x in ag[3:6]],
            [x * SCALE_MAG for x in m[0:3]],
        ]

    return [
        parse_chip(imu_bytes[0:18]),
        parse_chip(imu_bytes[18:36]),
    ]
```

### 4.4 返回结构统一

V1 和 V2 的 `parse_packet()` 返回值中增加 `hw_version` 和 `num_imus` 字段，让下游知道 IMU 结构：

```python
return {
    'f': start_frame,
    'n': fpkt,
    'frame_ids': frame_ids,
    'raw': emg_raw_mapped,       # 已映射
    'uv': emg_uv_filtered,       # 已映射（滤波后）
    'imu': imu,                  # V1: [[acc,gyr,mag],...], V2: [[acc,gyr],...]
    'num_imus': num_imus,        # 新增
    'hw_version': dev.hw_version, # 新增
    's': [dev.total_frames, dev.lost_frames],
}
```

---

## 5. 改动点 #4: STATUS_CHAR 回调处理

### 5.1 状态回调函数

```python
def create_status_handler(dev: DeviceState):
    """V2 设备状态通知回调 — 仅更新本地状态，不影响控制流"""
    def handler(sender: int, data: bytearray):
        try:
            if not data or len(data) < 1:
                return
            packet_type = data[0]

            if packet_type == STATUS_PACKET_SNAPSHOT and len(data) >= 59:
                s = struct.unpack(STATUS_SNAPSHOT_FORMAT, data[:59])
                # 同步到 DeviceState (只读更新，不触发任何操作)
                dev.num_imus = s[7] if s[7] <= MAX_NUM_IMUS_V2 else dev.num_imus
                dev.status_flags = s[8]
                dev.storage_state = s[9]
                dev.sd_free_kb = s[10]
                dev.firmware_version = s[17].split(b'\x00')[0].decode('ascii', errors='ignore')
                dev.hardware_version = s[18].split(b'\x00')[0].decode('ascii', errors='ignore')

            elif packet_type == STATUS_PACKET_EVENT:
                # 事件记录（后续可扩展透传给前端用于诊断）
                pass

        except Exception as e:
            log(f"[Dev{dev.device_id}] 状态解析错误: {e}")
    return handler
```

### 5.2 对操作逻辑的影响确认

STATUS_CHAR 回调**只做信息更新**：
- 不发送任何 BLE 控制命令
- 不触发连接/断开/启停
- 不改变 `is_streaming` 或其他控制状态
- 失败时静默忽略，不影响 EMG 数据流

因此不会影响任何现有的设备控制逻辑。

---

## 6. 改动点 #5: 滤波器的采样率适配

滤波器仍用 `BLE_SAMPLE_RATE = 250`，V1 和 V2 在这点上完全一致，**不需要改动**。

如果后续需要根据 STATUS_CHAR 中的 `sample_rate_hz` 字段动态调整，可以放在后续优化，本次不改。

---

## 7. 通道映射验证对照表

### 7.1 V1 映射 (CHANNELS_MAP_V1)

```
逻辑CH   1   2   3   4   5   6   7   8   9  10  11  12  13  14  15  16
物理CH  14  15  16   3   1   2   4   5   6   7   8   9  10  11  12  13
```

来源: `wband_emg_V1/wband_emg_client_V3.py` 第 99 行

### 7.2 V2 映射 (CHANNELS_MAP_V2)

```
逻辑CH   1   2   3   4   5   6   7   8   9  10  11  12  13  14  15  16
物理CH  15  16  14   1   2   3   4   5   6   7   8   9  10  11  12  13
```

来源: `wband_emg_V2/wband_emg_client_V5.py` 第 173 行

### 7.3 差异说明

V1 和 V2 仅前 6 个通道的映射不同（CH1-CH6），CH7-CH16 映射完全相同（物理 CH4-13）。

---

## 8. 修改涉及的文件清单

| 文件 | 修改内容 | 影响范围 |
|------|---------|---------|
| `ble_server.py` | 常量区: 新增/修改 SCALE_ACCEL, BYTES_PER_IMU, CHANNELS_MAP, STATUS_CHAR_UUID 等 | 全局 |
| `ble_server.py` | `DeviceState`: 新增 hw_version, num_imus, channel_map 等字段 | 数据结构 |
| `ble_server.py` | `connect_device()`: 末尾增加 STATUS_CHAR 订阅 + V1/V2 检测 | 连接流程(追加) |
| `ble_server.py` | `parse_packet()`: 包长动态校验 + IMU 分叉解析 + 通道映射 | 数据解析 |
| `ble_server.py` | 新增 `parse_imu_v1()`, `parse_imu_v2()`, `create_status_handler()` | 新增函数 |
| `ble_server.py` | `get_packet_params()`: 不再返回固定 total_len (V2 模式) | 辅助函数 |

---

## 9. 不改动的清单 (明确排除)

- ❌ `scan_devices()` — 不修改
- ❌ `connect_device()` 中除末尾 STATUS_CHAR 订阅外的逻辑 — 不修改
- ❌ `disconnect_device()` — 不修改（清理时 `dev.hw_version` 等字段由 `reset_stats` 覆盖）
- ❌ `start_stream()` / `stop_stream()` / `start_all()` / `stop_all()` — 不修改
- ❌ `data_sender_thread()` / `process_queue()` / `add_to_queue()` — 不修改
- ❌ `handle_control_client()` / `handle_data_client()` — 不修改
- ❌ `EMGRealtimeFilter` / `init_filters()` — 不修改
- ❌ 会话管理 / SD 文件名生成 — 不修改
- ❌ 远程关机 (0xFF) — 不做

---

## 10. 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| STATUS_CHAR 订阅在 V1 设备上抛异常 | 高 (预期行为) | 无 | try/except 静默降级 |
| V2 设备 IMU 数量不是 3 个 | 中 | IMU 字段长度变化 | 自动从包长检测 num_imus |
| 通道映射导致历史数据不兼容 | 低 | 前端显示顺序变化 | 仅影响实时显示，不影响存储数据 |
| SCALE_ACCEL 变更影响现有 V1 采集 | 中 | V1 数据值翻倍 | 根据 hw_version 使用不同系数 |
