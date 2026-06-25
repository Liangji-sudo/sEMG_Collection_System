# 06 - 设备监控与摄像头

## 1. 概述

设备监控模块负责实时显示采集过程中的信号质量、设备状态和摄像头预览。运行在前端浏览器中。

| 模块 | 文件 | 职责 |
|------|------|------|
| **waveform** | `waveform.js` + `waveform-renderer.js` | EMG 16 通道实时波形显示 |
| **signal-quality** | `signal-quality.js` | 信号质量颜色指示 (绿/黄/红) |
| **ble_control** | `ble_control.js` | BLE 设备扫描/连接/配置 (直连 ble_server :8764) |
| **camera_control** | `camera_control.js` | 摄像头 MJPEG 预览 (直连 camera_server :8768) |
| **device-status-widget** | `device-status-widget.js` | 设备状态组件 (电量/连接/流模式) |
| **fullscreen-waveform** | `fullscreen-waveform.js` | 全屏波形显示 |

---

## 2. Waveform — 实时波形

**文件**: `waveform.js`, `waveform-renderer.js`

### 2.1 架构

```javascript
// waveform.js
class RealtimeDataReceiver {
    wsUrl: 'ws://localhost:8080'  // realtimeEngine
    connect()                      // 建立连接 + 自动重连 (最多10次)
    // 接收 realtime_data_batch → 提取 emg1/emg2 → 传给渲染器
}

// waveform-renderer.js
class WaveformRenderer {
    // Canvas 绑制, 16通道并排
    // 自动缩放 (autoScale)
    // 支持 μV / ADC 两种显示模式
}
```

### 2.2 数据流

```
realtimeEngine :8080    →    Waveform (前端)
realtime_data_batch           │
  ├─ emg1[16][n]              ├─ Canvas 渲染 (16通道波形)
  │   └─ 手环1 8通道 + 手环2 8通道      ├─ μV 值显示
  └─ emg2[16][n]                      └─ 自动缩放 + 网格线
```

### 2.3 配置

- Y 轴范围: ±300 μV (可调)
- 刷新率: 跟随 BLE 数据包到达频率 (~27.8Hz)
- 通道映射: 手环1 通道1-8, 手环2 通道9-16

---

## 3. SignalQuality — 信号质量

**文件**: `signal-quality.js`

### 3.1 检测窗口

```javascript
NUM_CHANNELS = 16;
DEFAULT_FS = 250;            // Hz
DEFAULT_WINDOW_S = 0.25;     // 窗口: 0.25s = 63 samples
```

### 3.2 颜色映射

| RMS 范围 (μV) | 颜色 | 含义 |
|---------------|------|------|
| ≤ 25 | 绿色 `rgb(76,175,80)` | 正常 |
| 25-50 | 黄色 `rgb(255,193,7)` | 偏弱 |
| > 50 | 红色 `rgb(244,67,54)` | 过强 |

附加检测:
- **Dead 通道**: 方差 < 0.1 → 灰色 (传感器脱落)
- **Clipped 通道**: 超过 clip limit (≈ 33333 μV) → 品红色

### 3.3 LSB 系数

```javascript
BASE_LSB_24BIT = 0.476837;       // 与供应商 V3 上位机一致
DEFAULT_GAIN = 12;
HARDWARE_FRONTEND_GAIN = 10;
lsbUv = 0.476837 / (gain × 10);  // ≈ 0.003974 μV/LSB
clipLimitUv = lsbUv × 8388607;   // ≈ 33333 μV
```

---

## 4. BLE Control

**文件**: `ble_control.js`

前端直连 `ble_server.py` 控制端 (:8764)，管理 BLE 设备生命周期：

### 4.1 状态

```javascript
const BleState = {
    ws: null,                    // WebSocket → :8764
    devices: {
        1: { connected, streaming, mac, name, rssi },
        2: { connected, streaming, mac, name, rssi },
    },
    scannedDevices: [],           // 扫描结果
}
```

### 4.2 命令

| 命令 | 说明 |
|------|------|
| `scan_devices` | 扫描附近 BLE 设备 |
| `connect_device` | 连接指定设备 (MAC 或名称 "ESP32S3_EMG") |
| `disconnect_device` | 断开设备 |
| `start_all` / `stop_all` | 启动/停止所有设备流 |
| `switch_preview_to_collection` | 切换到采集流 |
| `get_status` | 获取设备状态 (电量/流模式) |
| `set_filename` | 设置 SD 卡文件名 |

### 4.3 自动连接

页面加载后自动扫描并连接名为 `ESP32S3_EMG` 的设备（支持 V2 前缀 `WristBand_`）。

---

## 5. Camera Control

**文件**: `camera_control.js`, `camera-control.js`, `camera-control-simple.js`

前端直连 `camera_server.py` (:8768)，管理摄像头预览与录制：

### 5.1 功能

| 功能 | 说明 |
|------|------|
| **设备枚举** | 通过 `list_cameras` 获取可用 USB 摄像头 |
| **摄像头配置** | `set_camera(side, device_name)` 左右手各分配一个摄像头 |
| **实时预览** | `subscribe_preview` 订阅 MJPEG 帧 (base64 编码，1s 刷新缩略图) |
| **录制控制** | `start_continuous_recording` / `stop_and_save` |

### 5.2 预览机制

- 前端订阅后，camera_server 持续推送 MJPEG 帧
- 前端以 **1 秒间隔** 更新缩略图 (降低 CPU 开销)
- 左手/右手各一个预览单元

---

## 6. DeviceStatusWidget

**文件**: `device-status-widget.js`

顶部状态栏组件，显示：
- BLE 连接状态 (✓/✗)
- 设备电量 (0-100%)
- 流模式 (idle/preview/collection)
- 磁盘剩余空间
- SD 卡状态
