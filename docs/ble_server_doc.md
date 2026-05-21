# BLE Server 架构设计文档

**日期**: 2026-05-21
**模块**: `ble_server.py`
**功能**: BLE 数据采集服务器 — 接收 EMG 腕带的蓝牙数据，解包、滤波，发送到 realtimeEngine

---

## 1. 整体架构

```
  ┌─────────────────┐     ┌─────────────────┐
  │  index.html     │     │ realtimeEngine  │
  │  (控制端)       │     │   (数据端)      │
  └────────┬────────┘     └────────┬────────┘
           │ :8764                 │ :8766
           │ 控制命令              │ 数据流 (JSON)
           ▼                       ▼
  ┌─────────────────────────────────────────┐
  │            ble_server.py                │
  │  ┌─────────────┐   ┌─────────────┐     │
  │  │   Device 1   │   │   Device 2   │     │
  │  │  (左手/L)    │   │  (右手/R)    │     │
  │  └─────────────┘   └─────────────┘     │
  └─────────────────────────────────────────┘
```

- **控制端 (8764)**: 接收前端控制命令（扫描、连接、开始/停止采集等），返回响应
- **数据端 (8766)**: 单向推送实时 EMG 数据流给 `realtimeEngine.js`
- **双设备支持**: 两个 BLE 腕带独立连接、独立控制、独立滤波

---

## 2. 核心数据结构

### 2.1 ServerState — 全局服务器状态

| 字段 | 类型 | 说明 |
|------|------|------|
| `control_clients` | `Set[WebSocket]` | 控制端 WS 客户端集合 |
| `data_clients` | `Set[WebSocket]` | 数据端 WS 客户端集合 |
| `dev1` / `dev2` | `DeviceState` | 两个腕带设备的状态 |
| `devices_found` | `dict` | 扫描发现的所有设备 |
| `scan_results` | `list[dict]` | 扫描结果列表（按 RSSI 排序） |
| `msg_queue` | `PriorityQueue` | 全局消息队列（跨线程安全） |
| `queue_seq` | `itertools.count` | 消息序号生成器 |
| `data_thread` | `Thread` | 数据发送后台线程 |
| `session_id` | `str` | 会话 ID，用于 SD 卡文件命名（如 `"S001"`） |

### 2.2 DeviceState — 单设备状态

```python
@dataclass
class DeviceState:
    device_id: int              # 1 或 2
    client: BleakClient         # BLE 客户端实例
    device: Any                 # BLE 设备对象
    mac: str                    # MAC 地址
    name: str                   # 设备名称（如 "WristBand_3A76"）
    rssi: int                   # 信号强度

    is_streaming: bool          # 是否正在采集
    total_frames: int           # 累计收到的帧数
    lost_frames: int            # 丢帧计数（通过帧序号连续性检测）
    last_frame_index: int       # 上一帧的序号
    last_data_time: float       # 最后收到数据的时间戳（用于超时检测）

    config: dict                # 设备配置（采样率、增益等）
    data_buffer: deque          # 数据缓冲队列（maxlen=500）
    sd_filename: str            # 当前采集的 SD 卡 bin 文件名前缀
    connect_task: asyncio.Task  # 异步连接任务
```

### 2.3 消息优先级系统

| 优先级 | 常量 | 用途 |
|--------|------|------|
| 0 (最高) | `PRIORITY_CONTROL` | 控制命令 |
| 1 | `PRIORITY_HIGH` | 控制响应 |
| 2 (最低) | `PRIORITY_LOW` | 传感器数据 |

数据消息队列满时（>500），低优先级的数据消息会被丢弃，保证控制命令优先送达。

---

## 3. BLE 数据包格式与解析

### 3.1 包结构

```
┌────────────┬──────────────────┬──────────────────┐
│  Header    │   EMG 数据区      │   IMU 数据区     │
│  4 bytes   │  (可变长度)       │  (36 bytes)      │
└────────────┴──────────────────┴──────────────────┘
```

- **Header**: `uint32` 起始帧序号（小端序）
- **EMG 数据区**: `frames_per_packet × 16 channels × bps`，默认 `9 × 16 × 3 = 432 bytes`（24-bit 模式）
- **IMU 数据区**: 2 组 × 18 bytes = 36 bytes（加速度计 6×2 + 陀螺仪 6×2 + 磁力计 3×2+3×2）

总长度为 `4 + emg_len + imu_len`。

### 3.2 采样配置参数

由 `get_packet_params(config)` 计算：

| 参数 | 16-bit 模式 | 24-bit 模式 |
|------|------------|------------|
| `bps` (每采样字节) | 2 | 3 |
| `stride` (每帧字节) | 16×2=32 | 16×3=48 |
| EMG 数据长度 | 9×32=288 | 9×48=432 |
| IMU 数据长度 | 36 (使能时) | 36 (使能时) |

### 3.3 数据转换流程

```
原始字节 → int (signed big-endian) → raw_value × LSB → μV 值
```

**LSB 电压转换系数**:
```
BASE_LSB_24BIT = 0.2861 μV    # 2.4V ref / 2^23 * 1e6
lsb_uv = BASE_LSB_24BIT / (gain × HARDWARE_FRONTEND_GAIN)
       = 0.2861 / (12 × 10) = 0.002384 μV  (24-bit 默认配置)
```

16-bit 模式下，LSB 值再乘以 `2^shift`。

### 3.4 辅助数据

#### IMU 传感器数据
| 传感器 | 数据范围 | 转换公式 | 采样率 |
|--------|---------|---------|--------|
| 加速度计 | ±16g | `raw × 16/32768` | ~27.8 Hz |
| 陀螺仪 | ±2000°/s | `raw × 2000/32768` | ~27.8 Hz |
| 磁力计 | — | `raw × 0.15` | ~27.8 Hz |

#### 帧序号与丢包检测
```
expected = last_frame_index + 1
if start_frame > expected:
    lost_frames += start_frame - expected
```

#### BLE ↔ SD 卡帧号映射
SD 卡存储 2kHz 数据，BLE 传输 250Hz（8 倍降采样）：
```
SD卡帧号 = BLE帧号 × 8 + 7
```

---

## 4. 滤波器系统

### 4.1 EMGRealtimeFilter 类

基于 `scipy.signal` 的 IIR 滤波器，支持流式处理（通过 `lfilter` 的 `zi` 状态参数）。

**关键设计决策**：
- 使用**单一 4 阶 Butterworth 带通滤波器**（而非分离的高通+低通），保持相位响应一致性
- 采样率使用 **BLE 实际传输频率 250Hz**，而不是 ADC 采样率 2kHz
- 两个设备各自维护独立的滤波器实例（`emg_filter_dev1` / `emg_filter_dev2`）

### 4.2 滤波器参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| 带通低截止 | 20 Hz | 去除低频运动伪迹 |
| 带通高截止 | 100 Hz | EMG 主要能量在 20-100Hz，上限自动限制为 ≤ 奈奎斯特频率 × 0.9 |
| 陷波频率 | 50 Hz | 工频干扰基频 |
| 陷波 Q 值 | 15 | 较低 Q 值更稳定（与供应商一致） |
| 带通滤波器阶数 | 4 | Butterworth |
| 谐波处理 | 自动生成 50/100/150...Hz 的级联陷波器 |

### 4.3 滤波方法

**`filter_frame(uv_data)`** — 单帧滤波：
```python
data = np.array(uv_data)        # (16,) → (1, 16)
→ bandpass lfilter (zi 保持)    # 4阶 Butterworth 带通
→ notch lfilter × N (zi 保持)   # 级联陷波器
→ return data.flatten().tolist()
```

**`filter_batch(uv_data_batch)`** — 批量滤波：
```python
data = np.array(batch)          # (n_frames, 16)
→ 同上流程（axis=0 逐帧处理）
→ return data.tolist()
```

批量处理在 `parse_packet` 中用于同时滤波一个 BLE 包内的 9 帧数据。

### 4.4 滤波器生命周期

- **初始化**: `init_filters()` 在 `main()` 启动时调用，创建双设备滤波器
- **重置**: `DeviceState.reset_stats()` 时调用对应滤波器的 `reset()`，清除 IIR 状态
- **降级**: 滤波异常时自动回退到原始数据，不中断采集

---

## 5. 控制命令体系

### 5.1 命令列表

控制端通过 JSON 消息的 `action` 字段分发：

| 命令 | 说明 | 底层操作 |
|------|------|---------|
| `scan` | 扫描蓝牙设备 | `BleakScanner.discover(timeout=5s)` |
| `connect1/2` | 连接指定设备 | 先扫描查找 → 创建 `BleakClient` → 发送配置命令 |
| `disconnect1/2` | 断开设备 | 先停止采集 → `disconnect()` → 清理状态 |
| `start1/2` | 开始采集 | 发送 SD 文件名 → `start_notify` → 发送 START 命令 |
| `stop1/2` | 停止采集 | 发送 STOP → `stop_notify` |
| `start_all` | 同时开始双设备 | 依次调用 `start1` + `start2`，汇总文件名 |
| `stop_all` | 同时停止双设备 | 依次调用 `stop1` + `stop2` |
| `status` | 查询状态 | 返回双方连接/采集状态 |
| `set_session_id` | 设置会话 ID | 用于 SD 卡文件命名（如 `"S001"`） |

### 5.2 BLE 控制命令码 (CMD_MAP)

```python
CMD_MAP = {
    '500Hz': 0x10,      # 设置 ADC 采样率
    '1kHz':  0x11,
    '2kHz':  0x12,
    'START': 0xA0,      # 开始数据流
    'STOP':  0xA1,      # 停止数据流
    'CONFIG': 0xC0,      # 复合配置: [cmd, gain_idx, mode, shift, imu_en]
    'SET_FILENAME': 0xD0, # SD 卡文件名: [cmd] + ascii_filename
}
```

所有 BLE 命令都写入 `CONTROL_CHAR_UUID` (write without response)。

### 5.3 连接的配置流程

设备连接成功后立即发送配置命令（不再等待 START 时配置，避免时序问题）：

1. 发送采样率命令 `0x12`（2kHz）
2. `await asyncio.sleep(0.1)` 等待 ESP32 处理
3. 发送复合配置命令 `0xC0 + [6, 0, 4, 1]`（增益12, 24-bit, shift=4, IMU 开启）
4. `await asyncio.sleep(0.1)` 等待处理

### 5.4 START 流程

```
reset_stats() → SET_FILENAME cmd → sleep(0.1) → start_notify → START cmd
```

- 文件名格式: `{session_id}_{L/R}_{YYMMDD_HHMMSS}`，最大 31 字节
- 例如: `S001_L_260521_143025`

---

## 6. 消息路由与发送

### 6.1 线程模型

```
┌─ asyncio 主循环 ────────────────────────────┐
│  WebSocket Server (8764) + (8766)           │
│  心跳任务 (30s)                              │
│  handle_control_client / handle_data_client  │
│      ↓ asyncio.run_coroutine_threadsafe      │
│  process_queue()  ←── 消息生产触发           │
└──────────────────────────────────────────────┘
                      ↑ add_to_queue()
┌─ Thread ────────────────────────────────────┐
│  data_sender_thread()                       │
│  每 5ms 检查 data_buffer → 打包 → 入队      │
│  超时检测 (3s 无数据 → 发空包)               │
└──────────────────────────────────────────────┘
```

### 6.2 消息类型

| 类型 | 目标 | 说明 |
|------|------|------|
| `control` | 控制端 WS | 命令响应 |
| `data` | 数据端 WS | 实时 EMG 数据 |
| `broadcast` | 所有 WS | 事件通知（连接/断开/开始/停止） |

### 6.3 data_sender_thread 逻辑

1. 每 5ms 循环检查设备 buffer
2. 从 `data_buffer` 中取出最多 5 个包，打包为 `dev1_data` / `dev2_data`
3. 若 3 秒无数据（`DATA_TIMEOUT`），发送空包保持连接
4. 消息通过 `add_to_queue(PRIORITY_LOW, 'data', msg)` 入队
5. 队列满 500 时，丢弃旧的低优先级数据消息

### 6.4 数据消息结构

```json
{
  "type": "data",
  "ts": 1716296400.123,
  "dev1": { "f": 100, "n": 9, "uv": [[...]], "imu": [[...]], ... },
  "dev2": null,
  "active": [1],
  "timeout": { "dev1": false, "dev2": false }
}
```

---

## 7. BLE 回调与诊断

### 7.1 Notification 回调

`create_notification_handler(dev)` 返回一个闭包回调函数：

1. 更新时间戳 → 检测回调间隔异常（>100ms 告警）
2. 调用 `parse_packet()` 解析
3. 为每帧生成时间戳（`emg_t`）：按 250Hz BLE 采样率反推
4. 为 IMU 生成时间戳（`imu_t`）：使用 BLE 包到达时间
5. 追加到 `data_buffer`

### 7.2 诊断机制

| 诊断项 | 检测方式 | 阈值 |
|--------|---------|------|
| 回调间隔异常 | `ts - last_callback_time > 0.1s` | 每设备仅打印一次 |
| 数据超时 | `now - last_data_time > 3s` | 每设备仅打印一次 |
| 帧序号跳跃 | `start_frame > expected + 1` | 累计到 `lost_frames` |
| 包长错误 | `len(data) != expected_len` | 丢弃该包 |
| 发送统计 | 每 5 秒打印发送批次和活跃设备 | 仅在有数据时 |

### 7.3 心跳任务

每 30 秒打印一次：设备 1/2 的当前状态（采集中/已连接/未连接）。

---

## 8. 配置参数汇总

### 8.1 设备默认配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `sample_rate` | 2000 Hz | ADC 采样率（SD 卡存储用） |
| `gain` | 12 | 总增益 |
| `gain_index` | 6 | 增益寄存器索引 |
| `is_16bit` | False | 24-bit 模式 |
| `shift` | 4 | 16-bit 模式下的移位 |
| `imu_enabled` | True | IMU 传感器使能 |
| `frames_per_packet` | 9 | 每 BLE 包的帧数 |

### 8.2 BLE 传输配置

| 参数 | 值 | 说明 |
|------|----|------|
| `BLE_SAMPLE_RATE` | 250 Hz | BLE 实际传输频率（2kHz÷8 降采样） |
| `CONNECT_TIMEOUT` | 30s | BLE 连接超时 |
| `SCAN_TIMEOUT` | 5s | 设备扫描超时 |
| `MAX_RETRIES` | 3 | 连接重试次数 |
| `RETRY_DELAY` | 2s | 重试间隔 |
| `DATA_TIMEOUT` | 3s | 数据超时检测 |
| `BATCH_INTERVAL` | 5ms | 数据发送批次间隔 |

### 8.3 WebSocket 配置

| 参数 | 值 |
|------|----|
| 控制端口 | 8764 |
| 数据端口 | 8766 |
| 控制端 max_size | 1 MB |
| 数据端 max_size | 10 MB |

---

## 9. 依赖项

```
pip install websockets bleak msgpack scipy numpy
```

- **websockets**: WebSocket 服务端
- **bleak**: 跨平台 BLE 客户端库
- **msgpack**: 二进制序列化（备用，当前主要用 JSON）
- **scipy + numpy**: 信号滤波（可选降级）

若 scipy 未安装，滤波功能自动禁用（`HAS_SCIPY = False`），原始数据直接透传。

---

## 10. 关键设计决策与注意事项

### 10.1 滤波采样率的选择
滤波器使用 **BLE 传输频率 250Hz**（而非 ADC 的 2kHz），因为滤波发生在 BLE 数据接收之后，输入数据的有效采样率是 250Hz。使用 2kHz 会导致滤波器系数计算错误。

### 10.2 配置发送时机
在 `connect_device` 成功后立即发送配置命令，而非在 `start_stream` 时发送。这是为了消除配置命令和 START 命令之间的时序竞争问题。

### 10.3 IIR 状态管理
滤波器使用 `lfilter` 的 `zi` 参数维护 IIR 状态，实现流式逐帧滤波。`reset_stats()` 时重置 `zi`，避免上一段采集的状态影响下一段采集的数据。

### 10.4 SD 卡文件名生成
- 格式: `{session_id}_{L/R}_{YYMMDD_HHMMSS}`（如 `S001_L_260521_143025`）
- 最大 31 字节限制（ESP32 固件约束）
- `session_id` 由控制端通过 `set_session_id` 命令设置
- 设备 1 固定标记为左手 (L)，设备 2 固定标记为右手 (R)

### 10.5 时间戳生成策略
- **EMG 每帧时间戳**: 从 BLE 包到达时刻 `ts` 反推，`frame_ts = ts - (fpkt-1-i) × (1/250)`
- **IMU 时间戳**: 直接使用 BLE 包到达时刻 `ts`
- 时间戳用于后续与 SD 卡 bin 文件同步

### 10.6 蓝牙适配器预热
`warmup_ble_adapter()` 在启动时执行一次快速扫描（2 秒），解决 Windows 蓝牙后端首次扫描失败的问题。

### 10.7 空包机制
当设备处于采集状态但 3 秒未收到数据时，`data_sender_thread` 仍发送带 `timeout: {dev1: true}` 标记的空包，保持前端连接活跃，避免误判断开。
