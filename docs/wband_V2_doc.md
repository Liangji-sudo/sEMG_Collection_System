# 腕带 EMG 系统 V2 — 供应商参考实现文档

**日期**: 2026-05-21
**来源目录**: `wband_emg_V2/`
**上位机版本**: wband_emg_client_V5.py
**固件版本**: V4.1+（固件 `.c` 文件未随附，通过上位机行为推断）

---

## 1. 目录结构

```
wband_emg_V2/
├── wband_emg_client_V5.py       # 供应商上位机 (PyQt5 GUI)
├── signalfilter.py              # 信号滤波模块（与 V1 相同）
├── signal_quality.py            # 【新增】信号质量评估模块
└── custom_widgets.py            # 自定义绘图控件（与 V1 相同）
```

---

## 2. V1 → V2 硬件变动总览

| 方面 | V1 | V2 |
|------|----|----|
| **IMU 芯片** | ICM-20948 (9轴: Acc+Gyro+Mag) | **LSM6DSV32X (6轴: Acc+Gyro)** |
| **IMU 数量** | 2 (固定) | **1-3 (可变, 自动检测)** |
| **磁力计** | 有 (AK09916 @100Hz) | **无** |
| **加速度计量程** | ±16g | **±32g** |
| **IMU 字节序** | Acc/Gyro: Big Endian, Mag: Little | **Acc/Gyro: 全部 Little Endian** |
| **设备状态** | 仅电池电量 Notify | **新增 STATUS_CHAR (UUID ...0e)** |
| **远程关机** | 无 | **0xFF 命令** |
| **通道映射** | `[14,15,16,3,1,2,4-13]` | **`[15,16,14,1,2,3,4-13]`** |
| **BIN 文件格式** | Header 126B + 数据帧 | **Header 126B + 数据帧 + Footer 36B (V4.1)** |
| **控制写方式** | Write without response | **Write with response (3s timeout)** |

---

## 3. 新增 BLE 特征: STATUS_CHAR

### 3.1 UUID

```python
STATUS_CHAR_UUID = "9e5c100d-afc2-4e4b-b132-f2c0032f7a0e"
```

### 3.2 两种状态包类型

| 类型值 | 名称 | 触发方式 | 用途 |
|--------|------|---------|------|
| `0x01` | Snapshot | 每次状态变化时推送 | 完整设备状态快照 |
| `0x02` | Event | 事件发生时推送 | 异步事件通知 |

### 3.3 Status Snapshot 格式

```
Format: <BBBBHBBHBBIIIIIII16s16s  (59 bytes)
```

| 偏移 | 字段 | 类型 | 说明 |
|------|------|------|------|
| 0 | `packet_type` | u8 | 固定 0x01 |
| 1 | `version` | u8 | 协议版本 |
| 2 | `reason` | u8 | 触发原因码 |
| 3 | `gain_code` | u8 | 增益索引 (0-6→[1,2,3,4,6,8,12]) |
| 4-5 | `sample_rate_hz` | u16 | ADC 采样率 |
| 6 | `shift_bits` | u8 | 16-bit 模式右移位数 |
| 7 | `num_imus` | u8 | 检测到的 IMU 数量 |
| 8-9 | `flags` | u16 | 状态位掩码 (见下表) |
| 10 | `battery_percent` | u8 | 电池电量 0-100% |
| 11 | `storage_state` | u8 | SD 卡状态 |
| 12-13 | `free_kb` | u16 | SD 卡剩余空间 (KB) |
| 14-17 | `emg_frames_written` | u32 | EMG 帧写入计数 |
| 18-21 | `imu_frames_written` | u32 | IMU 帧写入计数 |
| 22-25 | `sd_drop_count` | u32 | SD 卡丢包计数 |
| 26-29 | `ble_drop_count` | u32 | BLE 丢包计数 |
| 30-33 | `imu_drop_count` | u32 | IMU 丢包计数 |
| 34-37 | `uptime_s` | u32 | 设备运行时间 (秒) |
| 38-53 | `firmware_version` | char[16] | 固件版本字符串 |
| 54-69 | `hardware_version` | char[16] | 硬件版本字符串 |

#### 触发原因码 (reason)

| 值 | 含义 |
|----|------|
| 0 | Read (主动读取) |
| 1 | Boot (设备启动) |
| 2 | Config (配置变更) |
| 3 | Connection (连接状态变化) |
| 4 | Stream (采集状态变化) |
| 5 | Storage (存储状态变化) |
| 6 | Battery (电量更新) |
| 7 | IMU (IMU 状态变化) |

#### 标志位 (flags)

| 位 | 常量 | 含义 |
|----|------|------|
| 0 | `STATUS_FLAG_STREAMING` | 正在采集 |
| 1 | `STATUS_FLAG_BLE_CONNECTED` | BLE 已连接 |
| 2 | `STATUS_FLAG_ADVERTISING` | 正在广播 |
| 3 | `STATUS_FLAG_EMG_NOTIFY` | EMG Notify 已启用 |
| 4 | `STATUS_FLAG_BATTERY_NOTIFY` | 电池 Notify 已启用 |
| 5 | `STATUS_FLAG_STATUS_NOTIFY` | 状态 Notify 已启用 |
| 6 | `STATUS_FLAG_IMU_ENABLED` | IMU 已使能 |
| 7 | `STATUS_FLAG_SD_24BIT` | SD 卡 24-bit 模式 |
| 8 | `STATUS_FLAG_I2C_READY` | I2C 总线就绪 |
| 9 | `STATUS_FLAG_VIN_CONNECTED` | USB 已连接 |
| 10 | `STATUS_FLAG_SD_AVAILABLE` | SD 卡可用 |
| 11 | `STATUS_FLAG_SD_MOUNTED` | SD 卡已挂载 |

#### 存储状态 (storage_state)

| 值 | 含义 |
|----|------|
| 0 | OK |
| 1 | Missing (未检测到卡) |
| 2 | Mount failed |
| 3 | Low space |
| 4 | Full |
| 5 | Write failed |
| 6 | USB busy (大容量存储模式占用) |

### 3.4 Status Event 格式

```
Format: <BBBBiII  (16 bytes)
```

| 偏移 | 字段 | 类型 | 说明 |
|------|------|------|------|
| 0 | `packet_type` | u8 | 固定 0x02 |
| 1 | `version` | u8 | 协议版本 |
| 2 | `event_code` | u8 | 事件码 |
| 3 | `severity` | u8 | 严重级别: 1=info, 2=warn, 3=error |
| 4-7 | `value` | i32 | 事件附带数值 |
| 8-11 | `detail` | u32 | 事件详情 |
| 12-15 | `event_seq` | u32 | 事件序号 |

#### 事件码

| 值 | 事件 | 说明 |
|----|------|------|
| 1 | Boot | 固件启动完成 |
| 2 | Config updated | 配置更新 |
| 3 | Stream started | 采集开始 |
| 4 | Stream stopped | 采集停止 |
| 5 | ADS watchdog recovered | ADS1298 看门狗恢复 |
| 6 | ADS start failed | ADS1298 启动失败 |
| 7 | IMU scan complete | IMU 扫描完成 |
| 8 | IMU comm error | IMU 通信错误 |
| 9 | SD unavailable | SD 卡不可用 |
| 10 | SD mount failed | SD 卡挂载失败 |
| 11 | SD low space | SD 卡低空间 |
| 12 | SD full | SD 卡已满 |
| 13 | SD write failed | SD 卡写入失败 |
| 14 | USB MSC active | USB 大容量存储激活 |

---

## 4. BLE 数据包格式 (V2 变动)

### 4.1 包结构（24-bit 模式）

```
┌──────────┬──────────────────────────────┬──────────────────────────────┐
│ Header   │  EMG 数据区                   │  IMU 数据区 (可变长度)        │
│ 4 bytes  │  432 bytes (9帧×16ch×3B)     │  N × 18 bytes               │
└──────────┴──────────────────────────────┴──────────────────────────────┘

N = num_imus (自动检测, 0-3), 每 IMU 18 bytes
总长度: 4 + 432 + N×18 = 436 + N×18 bytes
```

### 4.2 单 IMU 数据格式 (LSM6DSV32X, 18 bytes)

```
┌──────────────────────┬──────────────────────┬──────────────────┐
│  Accel X/Y/Z (6B)    │  Gyro X/Y/Z (6B)     │  Reserved (6B)   │
│  int16 × 3, LE       │  int16 × 3, LE       │  (填充/保留)      │
└──────────────────────┴──────────────────────┴──────────────────┘
```

**关键变动**：V1 中 Accel+Gyro 是 Big Endian (`>6h`)，V2 改为 **Little Endian** (`<6h`)。

### 4.3 IMU 编号规则

- `num_imus` 由固件在启动时自动扫描 I2C 总线确定 (0-3)
- 上位机可设置期望数量（"自动/1/2/3"），解析时做一致性校验
- BLE 包中 IMU 顺序 = I2C 地址顺序

### 4.4 IMU 转换系数对比

| 系数 | V1 (ICM-20948) | V2 (LSM6DSV32X) |
|------|---------------|-----------------|
| Accel Scale | `16.0 / 32768.0` | **`32.0 / 32768.0`** |
| Gyro Scale | `2000.0 / 32768.0` | `2000.0 / 32768.0` (不变) |
| Mag Scale | `0.15` | **无 (已移除)** |

---

## 5. 通道映射对比

| 逻辑 CH | V1 物理 CH | V2 物理 CH |
|---------|-----------|-----------|
| CH1 | 14 | **15** |
| CH2 | 15 | **16** |
| CH3 | 16 | **14** |
| CH4 | 3 | 1 |
| CH5 | 1 | 2 |
| CH6 | 2 | 3 |
| CH7-16 | 4-13 | 4-13 (不变) |

```python
# V1:
CHANNELS_MAP = [14, 15, 16, 3, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
# V2:
CHANNELS_MAP = [15, 16, 14, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
```

---

## 6. 控制命令变更

### 6.1 CMD_MAP 对比

| 命令 | V1 | V2 | 说明 |
|------|----|----|------|
| `500Hz` | 0x10 | 0x10 | 不变 |
| `1kHz` | 0x11 | 0x11 | 不变 |
| `2kHz` | 0x12 | 0x12 | 不变 |
| `START` | 0xA0 | 0xA0 | 不变 |
| `STOP` | 0xA1 | 0xA1 | 不变 |
| `CONFIG` | **0xC0** | 0xC0 (未入MAP) | 格式不变 `[C0, g, m, s, i]` |
| `SET_FILENAME` | **0xD0** | 0xD0 | 格式不变 |
| `SHUTDOWN` | 无 | **0xFF** | 新增: 远程关机 |

### 6.2 写操作变更

- V1: `write_gatt_char(..., response=False)` — 无响应写入
- V2: `write_gatt_char(..., response=True)` — 带响应写入，带 3 秒超时

### 6.3 START 流程变更

V2 在 `start_notify` 和 `START` 命令之间增加了 0.25 秒的稳定延迟：

```python
# V2 新增:
await self.client.start_notify(EMG_DATA_CHAR_UUID, handler)
await asyncio.sleep(0.25)  # START_NOTIFY_SETTLE_DELAY_S
await self.send_control_command(CMD_MAP['START'])
```

---

## 7. 新增功能: 信号质量评估 (signal_quality.py)

### 7.1 SignalQualityEvaluator 类

评估流程：
1. 采集静息段（用户放松，记录噪声基线 RMS）
2. 采集激活段（用户收缩，计算 SNR = 20×log10(Signal_RMS / Noise_RMS)）

### 7.2 评估标准

**静息噪声等级**：
| RMS 范围 | 评估 |
|----------|------|
| ≤ 3 μV | 静息噪声很低 |
| ≤ 5 μV | 静息噪声可接受 |
| ≤ 10 μV | 静息噪声偏高 |
| > 10 μV | 建议检查电极贴合 |

**SNR 等级**：
| SNR 范围 | 评估 |
|----------|------|
| ≥ 20 dB | SNR 很好 |
| ≥ 10 dB | SNR 可用 |
| ≥ 6 dB | SNR 偏低 |
| < 6 dB | 建议重新贴电极 |

---

## 8. SD 卡 BIN 文件格式 (V4.1 Footer)

V2 新增了 **Footer 结构**（36 bytes），写在文件末尾：

```
EMG Footer Magic: 0xDDCCBBAA
IMU Footer Magic: 0xEEDDCCBB

Footer 结构 (36 bytes):
  magic:    u32 (4B)
  total_frames: u32  — 固件端累计帧数
  sd_drop:      u32  — SD 丢包计数
  imu_drop:     u32  — IMU 丢包计数
  ble_drop:     u32  — BLE 丢包计数
  stop_reason:  u8   — 停止原因: 0=无,1=运行中,2=用户停止,3=BLE断连,4=远程关机
  reserved:     15B
```

---

## 9. V1 vs V2 完整差异矩阵

| 特性 | V1 | V2 | ble_server.py 影响 |
|------|----|----|-------------------|
| IMU 类型 | ICM-20948 9轴 | LSM6DSV32X 6轴 | 解析逻辑变更 |
| IMU 数量 | 固定 2 | 可变 0-3 | 需自动检测 |
| IMU Acc 量程 | ±16g | ±32g | SCALE_ACCEL 变更 |
| IMU Mag | 有 (6B/chip) | 无 | 移除磁力计解析 |
| IMU 字节序 | Acc/Gyr BE, Mag LE | Acc/Gyr **全 LE** | unpack fmt 变更 |
| 单 IMU 字节 | 18 | 18 (不变) | — |
| 包大小 | 固定 472B | **可变 436-490B** | 解析需适配 |
| STATUS_CHAR | 无 | 新增 UUID ...0e | 需订阅+解析 |
| 设备名称 | `WristBand_XXXX` | `WristBand_XXXX` | 不变 |
| 控制 UUID | ...0b | ...0b | 不变 |
| 数据 UUID | ...0c | ...0c | 不变 |
| 电池 UUID | ...0d | ...0d | 不变 |
| CONFIG 命令 | 0xC0 | 0xC0 (不变) | 不变 |
| SHUTDOWN | 无 | **0xFF** | 可选新增 |
| 通道映射 | `[14,15,16,3,1,2,4-13]` | `[15,16,14,1,2,3,4-13]` | 需适配 |
| START 延迟 | 无 | **0.25s** | 建议增加 |
| 写响应方式 | Write w/o resp | Write with resp | 可选调整 |
| BIN Footer | 无 | V4.1 (36B) | 不影响(我们不读BIN) |

---

## 10. 上位机 UI 对比

### V1 上位机布局
```
[连接/文件控制] [高级配置(采样率/增益/位深/IMU)] [采集控制]
[EMG 16通道波形] [IMU: Acc | Gyro | Mag (各含 IMU1/2)]
```

### V2 上位机布局
```
[Device(扫描/连接/电量/固件信息/存储信息/事件日志)] [设备配置(+IMU数量)] [采集控制(+信号质量)]
[EMG 16通道波形] [IMU: Acc | Gyro (各含 IMU1/2/3, Mag已移除)]
```

### 新增 UI 元素
- 固件/硬件版本信息标签
- 存储状态和剩余空间标签
- 最后事件日志标签
- IMU 数量选择 (自动/1/2/3)
- 信号质量评估按钮和结果显示
- 远程关机按钮

---

## 11. 需要注意的细节

1. **IMU 数量自动检测**：固件启动时扫描 I2C 总线确定 IMU 数量，上位机通过包长推断。如果手动设置了 IMU 数量但检测到不一致，V2 会上报错误。

2. **IMU 字节序变更是最容易遗漏的点** — V1 用 `>6h`，V2 用 `<6h`，如果搞反了，加速度值会完全错误。

3. **SCALE_ACCEL 变了** — V1 的 `16/32768` 变成 V2 的 `32/32768`，原因是 LSM6DSV32X 的默认量程是 ±32g。

4. **磁力计已移除** — 所有 Mag 相关的解析、绘图、数据存储都不再需要（V2 GUI 没有 Mag 图表了）。

5. **包大小不再固定** — V1 始终 472 bytes，V2 是 `4 + 432 + N×18`，需要改为动态包长校验。

6. **V2 没有附随固件 C 文件** — 推测 V2 固件是基于 V1 固件修改而来，主要变动就是 IMU 驱动从 ICM-20948 换成 LSM6DSV32X + 新增 Status Notify 逻辑。协议层面完全向后兼容（除了包大小可变）。
