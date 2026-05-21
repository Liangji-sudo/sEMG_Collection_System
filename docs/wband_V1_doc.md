# 腕带 EMG 系统 V1 — 供应商参考实现文档

**日期**: 2026-05-21
**来源目录**: `wband_emg_V1/`
**内容**: 供应商提供的上位机 + ESP32 固件，是 `ble_server.py` 的参考基线和逆向依据

---

## 1. 目录结构

```
wband_emg_V1/
├── wband_emg_client_V3.py          # 供应商上位机 (PyQt5 GUI)
├── gatts_demo_imu-v3.1-260128.c    # ESP32-S3 固件源码
├── signalfilter.py                 # 信号滤波模块
└── custom_widgets.py               # 自定义绘图控件
```

---

## 2. 硬件架构总览

### 2.1 芯片组成

| 芯片 | 数量 | 接口 | 用途 |
|------|------|------|------|
| ADS1298 | 2 (菊花链) | SPI2 | 16通道 EMG 信号采集 |
| ICM-20948 | 2 (AD0=0/1) | I2C_NUM_1 | 9轴 IMU 姿态传感器 |
| BQ25120A | 1 | I2C_NUM_0 | 电源管理 (PMIC) |
| SD 卡 | 1 | SDMMC (4-bit) | 数据本地存储 |
| USB MSC | — | TinyUSB | USB 大容量存储模式 |

### 2.2 ADS1298 菊花链

```
ADS1298 #1 (Chip1) → ADS1298 #2 (Chip2)
    ↓ 菊花链级联
ESP32-S3 SPI2_HOST (MOSI/MISO/CLK/CS)
  - 一次 SPI 读取返回：Chip1(27 bytes) + 1-bit间隙 + Chip2(27 bytes) = 54 bytes
  - 每 chip 包含: 3 状态字节 + 8 通道 × 3 字节 = 27 bytes
```

### 2.3 IMU (ICM-20948)

```
ICM-20948 #1 (AD0=LOW, 0x68)  →  I2C_NUM_1 (SCL:IO16, SDA:IO17)
ICM-20948 #2 (AD0=HIGH, 0x69) →  同一 I2C 总线
内置 AK09916 磁力计 (I2C addr 0x0C)
```

---

## 3. 信号采集链与采样频率

### 3.1 ADS1298 采样率（固件配置）

| 寄存器值 | 采样率 | 模式 |
|----------|--------|------|
| `DR_500_LP` (0b101) | 500 Hz | Low Power |
| `DR_1K_LP` (0b100) | 1000 Hz | Low Power |
| `DR_2K_LP` (0b011) | 2000 Hz | Low Power |

默认采集模式：连续转换 (Continuous Mode)，内部参考 2.4V。

### 3.2 两条数据路径的分叉

**关键设计**：ADS1298 按用户配置的采样率运行后，数据分两路处理：

```
ADS1298 SPI 读取 (1k/2k Hz)
        │
        ├──→ 路径A: SD 卡存储 ─────────────────────────────────
        │    完整采样率，用户配置的位深（24-bit 或 16-bit）
        │    每帧: Counter(4B) + Chip1(24B) + Chip2(24B) = 52B (24-bit)
        │
        └──→ 路径B: BLE 传输 ─────────────────────────────────
             降采样至 250Hz（硬件降采样）
             固定 24-bit 原始数据透传
             打包后通过 BLE Notify 发送
```

### 3.3 BLE 传输的降采样策略

| ADC 采样率 | 降采样比 | BLE 发送频率 | 说明 |
|-----------|---------|-------------|------|
| 2000 Hz | 8:1 | 250 Hz | 每 8 个 ADC 采样取 1 帧 |
| 1000 Hz | 4:1 | 250 Hz | 每 4 个 ADC 采样取 1 帧 |
| 500 Hz | 2:1 | 250 Hz | 每 2 个 ADC 采样取 1 帧 |

降采样实现（固件 `emg_data_task`）：
```c
if (decimation_count >= decimation_ratio) {
    decimation_count = 0;
    // 将当前帧写入 BLE 包缓冲区
}
```

### 3.4 IMU 采样率

| 参数 | 值 | 说明 |
|------|----|------|
| IMU 任务周期 | **10ms (100Hz)** | `vTaskDelayUntil` 精确控制 |
| 磁力计 | **100 Hz** 连续模式 | `AK09916_MODE_CONT_100HZ` |
| BLE 中 IMU 发送频率 | **~27.8 Hz** | 250Hz ÷ 9帧/包 = 27.8 包/秒，每包含 1 组 IMU |
| SD 卡 IMU 存储频率 | **100 Hz** | 完整存储 |

**IMU 数据与 BLE 包的关系**：BLE 每发一个包（含 9 帧 EMG），附带**当前最新的**一组 IMU 数据（从全局变量 `g_latest_imu_data` 快照获取，信号量保护）。

---

## 4. BLE 数据包完整格式

### 4.1 包结构（固件 v3.1，默认 24-bit 模式）

```
┌──────────┬──────────────────────────────┬────────────────────┐
│ Header   │  EMG 数据区                   │  IMU 数据区         │
│ 4 bytes  │  432 bytes (9帧×16ch×3B)     │  36 bytes          │
└──────────┴──────────────────────────────┴────────────────────┘
总长度: 4 + 432 + 36 = 472 bytes
```

### 4.2 Header (4 bytes, Little Endian)

```
uint32_t start_frame_index  — BLE 帧计数器（每发一帧 BLE 递增 1）
```

该计数器从 0 开始递增，用于：
- 接收端丢包检测
- BLE 帧号 ↔ SD 卡帧号映射 (SD卡帧号 = BLE帧号 × 降采样比 + (降采样比 - 1))

### 4.3 EMG 数据区 (9 帧 × 16 通道 × 3 字节)

```
帧 0: [CH0_MSB CH0_MID CH0_LSB] [CH1_MSB CH1_MID CH1_LSB] ... [CH15...]  (48 bytes)
帧 1: [CH0...] ... (48 bytes)
...
帧 8: [CH0...] ... (48 bytes)

总计: 9 × 48 = 432 bytes
```

- 每通道 3 字节，**Big Endian signed**（直接透传 ADS1298 的 24-bit 补码输出）
- 芯片顺序：Chip1(CH0-7) → Chip2(CH8-15)

### 4.4 IMU 数据区 (36 bytes)

```
┌──────────────────────┬──────────────────────┐
│  IMU Chip1 (18 bytes)│  IMU Chip2 (18 bytes)│
└──────────────────────┴──────────────────────┘

每 Chip 18 bytes:
  Accel X/Y/Z (6B, Big Endian int16 × 3)
  Gyro  X/Y/Z (6B, Big Endian int16 × 3)
  Mag   X/Y/Z (6B, Little Endian int16 × 3)
```

### 4.5 16-bit 模式下的包结构变化

固件支持通过 CONFIG 命令切换到 16-bit 模式（仅影响 BLE 传输路径）：

| 参数 | 24-bit 默认 | 16-bit 选项 |
|------|------------|------------|
| 每采样字节 | 3 | 2 |
| 每帧字节 (16ch) | 48 | 32 |
| 帧数/包 | **9** | **13** |
| EMG 数据区 | 9×48=432 | 13×32=416 |
| 总包长 | 472 | 456 |

（注意 `ble_server.py` 目前只实现了 24-bit 模式，`frames_per_packet` 固定为 9）

---

## 5. BLE GATT 服务定义

### 5.1 UUID 定义

| 特征 | UUID (128-bit) | 属性 |
|------|---------------|------|
| Service | `9e5c100d-afc2-4e4b-b132-f2c0032f7a0a` | Primary |
| Control Char | `...0b` | Write |
| EMG Data Char | `...0c` | Notify |
| Battery Char | `...0d` | Notify |

### 5.2 设备名称

```
格式: "WristBand_XXXX"  (XXXX = MAC 地址后 2 字节的十六进制)
例如: "WristBand_3A76"
```

BLE 广播设置：
- MTU 设为 500
- 连接参数: `min_int=16, max_int=32, latency=0, timeout=400`（单位 1.25ms）

---

## 6. 控制命令协议

### 6.1 命令码列表

| 命令 | 字节 | 说明 |
|------|------|------|
| `500Hz` | `0x10` | 设置 ADC 采样率 500Hz |
| `1kHz` | `0x11` | 设置 ADC 采样率 1kHz |
| `2kHz` | `0x12` | 设置 ADC 采样率 2kHz |
| `START` | `0xA0` | 启动采集（唤醒 ADS + IMU，获取 CPU 锁） |
| `STOP` | `0xA1` | 停止采集（休眠 ADS + IMU，释放 CPU 锁） |
| `CONFIG` | `0xC0 + 4B` | 复合配置（增益/位深/移位/IMU开关） |
| `SET_FILENAME` | `0xD0 + ASCII` | 设置 SD 卡文件名（最大 31 字节） |
| `SHUTDOWN` | `0xFF` | 远程关机（进入运输模式） |

### 6.2 CONFIG 命令格式 (0xC0)

```
[0xC0, gain_index, mode, shift, imu_en]
```

| 字节 | 含义 | 取值范围 |
|------|------|---------|
| `gain_index` | 增益索引 | 0-6 → [1, 2, 3, 4, 6, 8, 12] |
| `mode` | 位深 | 0=24-bit, 1=16-bit |
| `shift` | 16-bit 模式右移位数 | 0-8 (默认 4) |
| `imu_en` | IMU 使能 | 0=禁用, 1=启用 |

**注意**：固件收到 CONFIG 后：
- `mode=0` → SD 卡存 24-bit（`g_sd_save_24bit = true`）
- `mode=1` → SD 卡存 16-bit，BLE 也 16-bit
- BLE 路径的 24-bit 是**固定透传**，与用户配置的 mode 无关

### 6.3 START 命令流程（固件端）

```
收到 0xA0:
  1. 检查 USB MSC 是否占用（占用则拒绝）
  2. 获取 CPU 频率锁 (esp_pm_lock_acquire)，禁止降频
  3. 唤醒两个 IMU (imu_set_power_mode(true))
  4. 唤醒 ADS1298 (ads_power_up_reset → ads_config_global → ads_start_conversion)
  5. 设置 g_is_streaming = true
```

### 6.4 STOP 命令流程（固件端）

```
收到 0xA1:
  1. g_is_streaming = false
  2. 等 50ms 让最后的数据写出
  3. 停止 ADS1298 (ads_stop_conversion: START拉低 → SDATAC → PWDN拉低)
  4. 休眠两个 IMU (imu_set_power_mode(false))
  5. 释放 CPU 频率锁，允许 Light Sleep 省电
```

---

## 7. SD 卡存储格式

### 7.1 文件命名

```
/data/{timestamp}_emg.bin   例如: /data/20260128_120000_emg.bin
/data/{timestamp}_imu.bin   例如: /data/20260128_120000_imu.bin
```

时间戳由上位机通过 `0xD0` 命令下发（格式 `YYYYMMDD_HHMMSS`）。

### 7.2 EMG bin 文件格式

**文件头 (126 bytes)**：
```c
typedef struct {
    uint32_t magic_word;      // 0xAABBCCDD (EMG 标识)
    uint16_t sample_rate;     // 实际 ADC 采样率 (500/1000/2000)
    uint8_t  gain_index;      // 增益索引 (0-6)
    uint8_t  bit_depth;       // 16 或 24
    uint8_t  imu_enabled;     // 0 (EMG 文件不含 IMU)
    char     timestamp[32];   // "20260128_120000"
    uint8_t  reserved[85];    // 填充至 126 字节
} emg_file_header_t;
```

**数据帧 (24-bit 模式, 52 bytes/帧)**：
```
Counter(4B, LE) + Chip1 CH0-7(24B, 3B/ch) + Chip2 CH8-15(24B, 3B/ch)
```

**数据帧 (16-bit 模式, 36 bytes/帧)**：
```
Counter(4B, LE) + Chip1 CH0-7(16B, 2B/ch) + Chip2 CH8-15(16B, 2B/ch)
```

### 7.3 IMU bin 文件格式

**文件头 (126 bytes)**：
```c
magic_word = 0xBBCCDDEE  (IMU 标识)
sample_rate = 100        (固定 100Hz)
```

**数据帧 (40 bytes/帧)**：
```
Counter(4B, LE) + IMU1(18B) + IMU2(18B)
```

### 7.4 存储写入策略

- 使用 **RingBuffer** 缓冲（EMG: 160KB, IMU: 16KB）
- 文件流缓冲区 32KB (EMG) / 8KB (IMU)
- 每 64KB 或每 5 秒强制 `fsync` 一次
- 采集停止时 `fflush` + `fsync` + `fclose`

---

## 8. 上位机 (wband_emg_client_V3.py) 设计

### 8.1 技术栈

| 组件 | 库 |
|------|-----|
| GUI 框架 | PyQt5 |
| 异步事件循环 | qasync |
| BLE 通信 | bleak |
| 绘图 | pyqtgraph (via QCustomPlot_PyQt5) |
| 滤波 | scipy (signalfilter.py) |

### 8.2 GUI 结构

```
┌────────────────────────────────────────────────────────────────┐
│  连接/文件控制  │  高级配置 (采样率/增益/位深/IMU)  │  数据采集  │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  EMG 信号 (16通道, Offset 调节)              │  IMU 姿态传感器   │
│  波形偏移显示 + 限制幅度选项                  │  加速度计 (g)     │
│                                              │  陀螺仪 (deg/s)   │
│                                              │  磁力计 (uT)      │
│                                              │  (实线=IMU1,      │
│                                              │   虚线=IMU2)      │
└────────────────────────────────────────────────────────────────┘
```

### 8.3 上位机 START 流程（与 ble_server.py 的差异）

**供应商上位机**：
1. 发送 `0xD0` + 时间戳（设置 SD 卡文件名）
2. `await asyncio.sleep(0.1)`
3. 重新初始化滤波器（使用 250Hz BLE 采样率）
4. 重新设置绘图控件参数（fs=250Hz）
5. `start_notify` → 发送 `START` 命令

**ble_server.py（我们的实现）**：
1. `reset_stats`（含滤波器重置）
2. 发送 `0xD0` + `{session_id}_{L/R}_{timestamp}`
3. `await asyncio.sleep(0.1)`
4. `start_notify` → 发送 `START` 命令
5. 配置命令在 `connect_device` 时已发送，START 时不重复

### 8.4 通道映射

供应商上位机使用自定义的通道重排：
```python
CHANNELS_MAP = [14, 15, 16, 3, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
```

`ble_server.py` **不使用**此映射，保持原始通道顺序（CH0-15），因为通道映射由前端处理。

---

## 9. 滤波器设计 (signalfilter.py)

### 9.1 类接口

```python
class SignalFilter:
    def __init__(self, lowpass=20, highpass=100, fs=1000, numchs=16, ...)
    def do_filter(data)           # 离线滤波 (零相位 filtfilt, 高 Q)
    def do_rt_filter_2d(data, is_discontinuous)  # 在线流式滤波 (lfilter, 低 Q)
    def reset_rt_filter_state()   # 重置 IIR 状态
    def set_fs(fs)                # 更新采样率
    def set_passband(low, high)   # 更新带通范围
```

### 9.2 双 Q 值设计

| 模式 | Q 值 | 方法 | 用途 |
|------|------|------|------|
| 离线 | **50** | `filtfilt` (零相位) | 后处理/数据分析，精确陷波 |
| 在线 | **15** | `lfilter` (因果) | 实时显示，抑制振荡 |

低 Q 值的在线陷波器响应更平滑，不会因为数据不连续而产生剧烈振铃。

### 9.3 带通滤波器

- 4 阶 Butterworth 带通
- 默认范围：20-100 Hz
- 截止频率自动钳制：highcut ≤ fs/2 × 0.9（防止超过奈奎斯特频率）

### 9.4 陷波器

- 级联陷波器：50Hz, 100Hz, 150Hz, ... 直到接近 fs/2
- 使用 `scipy.signal.iirnotch` 设计

### 9.5 ble_server.py 的滤波器对比

| 特性 | signalfilter.py | ble_server.py EMGRealtimeFilter |
|------|----------------|--------------------------------|
| 参数命名 | lowpass=20, highpass=100 (语义互换) | lowcut=20, highcut=100 (正确语义) |
| 带通滤波器 | 4 阶 Butterworth | 4 阶 Butterworth |
| 在线 Q 值 | 15 | 15 (FILTER_NOTCH_Q) |
| 离线滤波 | 支持 (do_filter) | 不支持（仅实时） |
| 淡入窗口 | 支持 (50ms Hann) | 不支持 |
| fs 参数 | 可动态更新 | 初始化时固定 |
| 批量滤波 | do_rt_filter_2d (axis=0) | filter_batch (axis=0) |

---

## 10. BLE 包中各信号的发送频率汇总

| 信号 | 传感器采样率 | BLE 包中发送频率 | 说明 |
|------|------------|-----------------|------|
| EMG (ADC) | 500/1000/2000 Hz | **250 Hz** (降采样) | 每包 9 帧，包频率 = 250÷9 ≈ 27.8 Hz |
| 加速度计 | 由 ICM-20948 决定 | **~27.8 Hz** | 随 BLE 包发送，每包 1 组 (2芯片×3轴) |
| 陀螺仪 | 由 ICM-20948 决定 | **~27.8 Hz** | 同上 |
| 磁力计 | 100 Hz (AK09916) | **~27.8 Hz** | 同上 |
| 电池电量 | 每 5 秒采样 | **按需 Notify** | 独立特征，不随数据包发送 |

### 10.1 关键频率关系

```
ADC采样率 (2kHz) ──降采样8倍──→ BLE EMG帧率 (250Hz)
                                       │
                             每 9 帧打包 1 个 BLE 包
                                       │
                                       ↓
                              BLE 包频率 ≈ 27.8 Hz  ←── IMU 发送频率
                                                        (快照最新 IMU 数据)
                              IMU 实际采样 = 100 Hz  → SD 卡存全部
```

### 10.2 BLE ↔ SD 卡帧号映射

```
SD卡 EMG 帧号 = BLE帧号 × 8 + 7    (2kHz ADC, 8:1降采样)
SD卡 EMG 帧号 = BLE帧号 × 4 + 3    (1kHz ADC, 4:1降采样)
SD卡 EMG 帧号 = BLE帧号 × 2 + 1    (500Hz ADC, 2:1降采样)

SD卡 IMU 帧号 → 独立 100Hz 计数器，与 BLE 帧号无直接映射
```

---

## 11. ble_server.py 对供应商代码的继承与修改

| 方面 | 供应商原版 | ble_server.py |
|------|-----------|---------------|
| GUI | PyQt5 桌面应用 | 无 GUI，WebSocket 服务端 |
| 滤波 | signalfilter.py (双 Q 值) | EMGRealtimeFilter (单 Q=15，仅在线) |
| 数据输出 | 本地 CSV + 实时绘图 | WebSocket JSON 流 → realtimeEngine |
| 设备数量 | 单设备 | 双设备（左手/右手） |
| 连接策略 | 手动扫描+连接 | 手动扫描+连接，支持重试 |
| 配置时机 | 用户手动"应用配置" | 连接成功后自动发送 |
| 通道映射 | CHANNELS_MAP 重排 | 保持原始顺序 |
| IMU 显示 | 3 个独立图表 (Acc/Gyr/Mag) | 不做 IMU 可视化，数据透传 |
| SD 文件名 | `{YYYYMMDD_HHMMSS}` | `{session_id}_{L/R}_{YYMMDD_HHMMSS}` |
| 会话管理 | 无 | 支持 session_id 设置 |
| 超时处理 | 无 | 3 秒无数据发空包保持连接 |
| 蓝牙预热 | 无 | `warmup_ble_adapter()` 解决首次扫描失败 |

### 11.1 协议兼容性

`ble_server.py` 的下述定义与固件/供应商代码**完全一致**：
- BLE UUID (Service/Control/Data)
- 命令码 `CMD_MAP`
- 数据包结构（Header + EMG 432B + IMU 36B = 472B）
- IMU 转换系数 (`SCALE_ACCEL`, `SCALE_GYRO`, `SCALE_MAG`)
- 增益映射表 `gain_map = [1, 2, 3, 4, 6, 8, 12]`
- CONFIG 命令格式 `[0xC0, gain_idx, mode, shift, imu_en]`
- SET_FILENAME 命令格式 `[0xD0, ASCII...]`
- LSB 计算: `BASE_LSB = 0.2861 / (gain × 10)`
- BLE 采样率固定 250Hz

---

## 12. ESP32 固件任务架构

```
app_main()
  │
  ├── bq_task (Core 0, prio 5)     — 电源管理 + LED + 按键关机
  ├── emg_data_task (Core 1, prio 5) — 核心数据采集
  │     ├── ADS1298 SPI 读取 (DRDY 中断驱动)
  │     ├── SD 卡数据写入 (RingBuffer)
  │     └── BLE 数据打包 + Notify
  ├── sd_write_task (Core 0, prio 4) — SD 卡异步写入
  │     ├── EMG 文件写入 (RingBuffer → fwrite)
  │     └── IMU 文件写入 (RingBuffer → fwrite)
  └── imu_task (Core 1, prio 3)     — IMU 100Hz 采样
        ├── ICM-20948 ×2 读取
        ├── 更新 g_latest_imu_data (BLE 快照)
        └── 推入 IMU RingBuffer (SD 卡存储)
```

Core 0 主要处理 IO 密集任务（SD 写入、电源管理），Core 1 处理实时采集（SPI、定时 IMU 采样）。

### 12.1 省电策略

- 非采集状态：ADS1298 掉电 (`PWDN=0`)，IMU 进入 Sleep 模式
- 采集状态：获取 `ESP_PM_CPU_FREQ_MAX` 锁，禁止降频
- 蓝牙断开 + 无 USB 供电 → 5 分钟无活动自动关机 (运输模式)
