# 02 - Python 服务层

## 1. 概述

Python 服务层由 4 个独立进程组成，每个进程通过特定的网络协议与 Node.js 中间层和前端通信：

| 进程 | 端口 | 协议 | 职责 |
|------|------|------|------|
| **ble_server.py** | :8764 (控制) / :8766 (数据) | WebSocket | BLE 腕带数据采集 |
| **storage_server.py** | :5555 (控制) / :5556 (数据) | ZMQ REP/PULL | HDF5 数据存储 |
| **camera_server.py** | :8768 | WebSocket | USB 摄像头 MJPEG 预览 + AVI 录制 |
| **mocap_server.py** | :8767 | WebSocket | Nokov 动作捕捉 SDK 数据接入 |

---

## 2. ble_server.py — BLE 腕带数据采集服务

**文件**: `ble_server.py` (2409 行)  
**依赖**: `websockets`, `bleak`, `msgpack`, `scipy`(可选)

### 2.1 架构

```
 ┌──────────┐  WS :8764  ┌──────────────────────────────┐  WS :8766  ┌──────────────┐
 │ 前端      │◄──────────►│        ble_server.py         │◄──────────►│realtimeEngine│
 │ble_control│  控制命令   │                              │  数据流     │   .js        │
 └──────────┘            │  DeviceState(1) DeviceState(2) │            └──────────────┘
                         │       │              │         │
                         └───────┼──────────────┼─────────┘
                                 │ BLE          │ BLE
                                 ▼              ▼
                          ESP32S3_EMG    ESP32S3_EMG
                          腕带 Dev1      腕带 Dev2
```

### 2.2 核心类

#### DeviceState (line 359)
单个 BLE 设备（腕带）的完整运行时状态，使用 `@dataclass`:

```python
@dataclass
class DeviceState:
    device_id: int                          # 1 或 2
    client: Optional[BleakClient] = None    # BLE 客户端
    mac: Optional[str] = None               # MAC 地址
    name: Optional[str] = None              # 蓝牙名称 (如 "WristBand_3A76")
    
    is_streaming: bool = False              # 是否正在采集
    total_frames: int = 0                   # 累计帧数
    lost_frames: int = 0                    # 丢帧数
    sd_filename: Optional[str] = None       # 当前 SD 卡 bin 文件名前缀
    
    hw_version: str = "V1"                  # 硬件版本: "V1" | "V2"
    firmware_version: str = ""              # 固件版本 (来自 STATUS_CHAR)
    num_imus: int = 2                       # IMU 传感器数量 (0~3)
    channel_map: List[int]                  # 通道映射表
    battery_percent: int = 0                # 电池百分比
    
    stream_mode: str = "idle"               # "idle" | "preview" | "collection"
    config: Dict                            # 采集配置 (采样率/通道等)
```

#### ServerState (line 447)
全局状态管理器，维护两个设备实例：

```python
class ServerState:
    def __init__(self):
        self.dev1 = DeviceState(device_id=1)
        self.dev2 = DeviceState(device_id=2)
        self.control_clients = set()         # 控制端 WS 连接集合
        self.data_clients = set()            # 数据端 WS 连接集合
```

#### EMGRealtimeFilter (line 146)
可选的实时滤波模块（依赖 scipy）：
- `filter_frame(uv_data)` — 对单帧 16 通道 EMG 数据进行带通滤波
- 滤波器类型: Butterworth 带通 (20-500Hz)
- 支持 500Hz / 1kHz / 2kHz 三种采样率

### 2.3 BLE 通信

#### 特征 UUID

| 特征 | UUID | 用途 |
|------|------|------|
| CONTROL_CHAR | `9e5c100d-...-7a0b` | 发送控制命令 (START/STOP/采样率) |
| EMG_DATA_CHAR | `9e5c100d-...-7a0c` | 接收 EMG+IMU 数据通知 |
| STATUS_CHAR | (ESP32 设备信息) | 读取 hw_version / firmware_version |

#### 数据包格式 (EMG_DATA_CHAR 通知)

数据包包含多帧 EMG + IMU，由 BLE `packet_counter` 标识序号:
- **EMG**: μV 值 (16 通道 × 9 帧) + 原始 ADC 值
- **IMU V1 (ICM-20948)**: 2 个 IMU chip，每 chip 含 acc(3) + gyr(3) + mag(3)
- **IMU V2 (LSM6DSV32X)**: 0~3 个 IMU chip，每 chip 含 acc(3) + gyr(3)，无 mag
- **帧号**: BLE 帧号 (`frame_index`) + 对应 SD 卡帧号 (= BLE 帧号 × 8 + 7)

### 2.4 Stream 管理

系统实现了 **Preview/Collection 双流模式**：

```
┌──────────┐     ┌──────────┐
│ Preview  │     │Collection│
│ Stream   │     │ Stream   │
├──────────┤     ├──────────┤
│ 实时预览  │     │ 正式采集  │
│ 数据不写  │     │ 数据写    │
│ 入 H5     │     │ 入 H5     │
│ bin 丢弃  │     │ bin 保留  │
└──────────┘     └──────────┘
     │                │
     └── switch ──────┘
   STOP → 延迟 3s → START
```

关键函数：
- `start_preview_stream()` (line 1694) — 启动预览流
- `switch_preview_to_collection()` (line 1826) — 切换为采集流（6 阶段时序）
- `stop_collection_stream()` (line 1785) — 停止采集流
- `switch_collection_to_preview()` (line 1938) — 切换回预览流

### 2.5 WebSocket 命令协议

#### 控制端 (:8764) — 前端 → ble_server

| 命令 | 说明 |
|------|------|
| `scan_devices` | 扫描附近 BLE 设备 |
| `connect_device` | 连接指定设备 (MAC 或名称) |
| `disconnect_device` | 断开设备 |
| `start_stream` / `stop_stream` | 控制单个设备流 |
| `start_all` / `stop_all` | 启动/停止所有设备 |
| `start_preview_stream` / `stop_preview_stream` | 预览流控制 |
| `switch_preview_to_collection` | 切流到采集 |
| `get_status` | 获取设备状态 |
| `set_filename` | 设置 SD 卡文件名 |

#### 数据端 (:8766) — ble_server → realtimeEngine

| 消息类型 | 说明 |
|---------|------|
| `type: "data"` | EMG + IMU 实时数据包 |
| `type: "event", event: "sd_filenames_updated"` | bin 文件名更新通知 |
| `type: "event", event: "collection_stopped"` | 采集流停止通知 |

---

## 3. storage_server.py — HDF5 数据存储服务

**文件**: `storage_server.py` (1552 行)  
**依赖**: `h5py`, `zmq`, `numpy`

### 3.1 通信架构

```
realtimeEngine.js
    │
    ├── ZMQ REP :5555 ──► 控制命令 (create/close/stats/video_info)
    │                     请求-响应模式，确保可靠性
    │
    └── ZMQ PUSH :5556 ──► 数据追加 (append)
                          非阻塞单向发送，保证实时性
```

### 3.2 核心类

#### HDF5StorageServer (line 156)

```python
class HDF5StorageServer:
    def __init__(self, host="127.0.0.1", port=5555, storage_dir="./storage"):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REP)      # 控制端口 :5555
        self.data_socket = self.context.socket(zmq.PULL)  # 数据端口 :5556
        self.f = None           # 当前打开的 H5 文件句柄
        self.file_path = None   # 当前文件路径
        self.lock = Lock()      # 线程锁 (H5 非线程安全)
```

### 3.3 H5 数据集 Schema

#### EMG 250Hz ADC (`emg{1,2}_250hz_adc`)
BLE 传输的原始 ADC 数据（同步前）:
```python
np.dtype([
    ("channels", "i4", (16,)),      # 16通道原始ADC值
    ("frame_id", "u4"),             # BLE帧号
    ("sd_frame_id", "u4"),          # 对应SD卡帧号 (= BLE帧号 × 8 + 7)
    ("time", "f8")                  # Python time.time() 时间戳
])
```

#### EMG 2kHz ADC (`emg{1,2}_2khz_adc`)
同步后从 bin 补齐的完整数据:
```python
np.dtype([
    ("channels", "i4", (16,)),      # 16通道原始ADC值
    ("sd_frame_id", "u4"),          # SD卡帧号
    ("time", "f8")                  # 时间戳
])
```

#### IMU BLE (`imu{1,2}_all_ble`)
V1/V2 通用格式，支持可变数量 IMU:
```python
np.dtype([
    ("imu_index", "u1"),            # IMU索引 (0-based, 对应物理I2C地址)
    ("acc", "f4", (3,)),            # 加速度计
    ("gyr", "f4", (3,)),           # 陀螺仪
    ("has_mag", "u1"),              # 是否有磁力计 (V1=1, V2=0)
    ("mag", "f4", (3,)),           # 磁力计 (V2填NaN)
    ("frame_id", "u4"),            # BLE帧号
    ("sd_frame_id", "u4"),         # 对应SD卡帧号
    ("time", "f8")                 # 时间戳
])
```

#### IMU 100Hz (`imu{1,2}{a,b,c}_100hz`)
同步后从 bin 补齐的每传感器数据:
```python
np.dtype([
    ("acc", "f4", (3,)),            # 加速度计
    ("gyr", "f4", (3,)),           # 陀螺仪
    ("mag", "f4", (3,)),           # 磁力计
    ("sd_frame_id", "u4"),         # IMU SD卡帧号
    ("time", "f8")                 # 时间戳
])
```

#### Mocap 动捕 (`mocap_l`, `mocap_r`)
每只手 12 个 marker 点 + 2 个扩展点:
```python
np.dtype([
    ("IN1_L", "f4", (3,)),   # 食指指尖
    ("IN2_L", "f4", (3,)),   # 食指第一关节
    ("IN3_L", "f4", (3,)),   # 食指第二关节
    ("HB1_L", "f4", (3,)),   # 手背点1
    ("HB2_L", "f4", (3,)),   # 手背点2
    ("HB3_L", "f4", (3,)),   # 手背点3
    ("TH1_L", "f4", (3,)),   # 拇指指尖
    # ... TH2-4, MD1-2
    ("MD2_L", "f4", (3,)),   # 中指点2
    ("frame", "i4"),         # 帧号
    ("time", "f8")           # 时间戳
])
```

#### Prompt 事件 (`prompts`)
```python
np.dtype([
    ("name", "S64"),            # prompt 名称
    ("time", "f8"),             # 时间戳
    ("stage", "S64")            # 所属 stage
])
```

### 3.4 控制命令

| 命令 | 参数 | 说明 |
|------|------|------|
| `create` | filename, subdirectory, task_id, user_id, stage_name, ... | 创建 H5 文件，写入元数据 attrs |
| `close` | collection_status, video_left/right, ... | 关闭 H5 文件，写入结束状态 |
| `append` | data: {emg1, emg2, imu1_all, ...} | 追加传感器数据 |
| `stats` | — | 返回当前文件统计信息 |
| `video_recording_started` | video_left, video_right | 写入视频文件路径到 H5 attrs |
| `directory_tree` | — | 返回 storage 目录树结构 |

### 3.5 目录结构

```
storage/
└── {task_id}/              # 离散手势采集 / 连续手势1 / 连续手势2
    └── {category1}/        # static / dynamic
        └── {category2}/    # sitting / standing / lying / walking
            └── {category4}/ # normal / exercise (人群)
                └── {user_id}/  # S001 / S002
                    └── S001_session1_stageName_20260105_143000.h5
```

### 3.6 文件元数据 (H5 attrs)

创建 H5 文件时写入约 **40+ 个属性**，涵盖：

| 类别 | 属性示例 |
|------|---------|
| **采集配置** | `task_id`, `stage_name`, `session_index`, `session_count` |
| **受试者信息** | `user_id`, `subject_info` (JSON) |
| **硬件信息** | `sd_bin_dev1/2`, `ble_dev1/2`, `hw_version` |
| **流信息** | `stream_mode`, `collection_stream_id`, `bin_pair_source` |
| **录像信息** | `recording_session_id`, `is_multi_session`, `video_left/right` |
| **续采信息** | `is_resumed`, `segment_index`, `resume_reason`, `breakpoint_state` |
| **同步状态** | `sync_status`, `sync_mode`, `sync_timestamp` |

---

## 4. camera_server.py — USB 摄像头管理服务

**文件**: `camera_server.py` (1314 行)  
**依赖**: `websockets`, `ffmpeg` (外部可执行文件)

### 4.1 架构

```
 ┌──────────────────┐                     ┌──────────────────┐
 │ camera_control.js │◄── WS :8768 ──────►│ realtimeEngine.js│
 │ (前端预览/控制)    │                     │ (录制控制)        │
 └──────────────────┘                     └──────────────────┘
             │                                       │
             └────────── WS :8768 ───────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │    CameraServer        │
                    │  ┌─────────────────┐   │
                    │  │ CameraCapture   │   │
                    │  │ (MJPEG 预览,     │   │
                    │  │  ffmpeg 管道)    │   │
                    │  └─────────────────┘   │
                    │  ┌─────────────────┐   │
                    │  │ FrameRecorder   │   │
                    │  │ (AVI 录制,       │   │
                    │  │  ffmpeg 编码)    │   │
                    │  └─────────────────┘   │
                    └───────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │    USB 摄像头           │
                    │  (左手 + 右手, 各一路)   │
                    └───────────────────────┘
```

### 4.2 核心类

#### CameraCapture (line 87)
MJPEG 预览采集器，通过 ffmpeg 从 USB 摄像头读取帧：

```python
class CameraCapture:
    def __init__(self, side, device_name, ffmpeg_path, frame_queue):
        self.side = side                    # "left" | "right"
        self.device_name = device_name       # dshow 设备名
        self.process = None                  # ffmpeg 子进程
        self._subscribers = set()            # 订阅预览的 WS 客户端
```

关键方法：
- `start()` — 启动 ffmpeg 进程，`-f dshow` 采集 1920×1080@30fps MJPEG
- `_read_frames()` — 从 stdout 管道持续读取 JPEG 帧，base64 编码后推送给订阅者
- `stop()` — 终止 ffmpeg 进程

#### FrameRecorder (line 287)
连续帧录制器，从 MJPEG 预览管道直接保存帧：

```python
class FrameRecorder:
    def __init__(self, side, ffmpeg_path, output_dir):
        self.side = side
        self.recording = False
        self.frame_count = 0
        self.start_timestamp = None          # Python time.time() 录制起始时间
```

关键方法：
- `start(output_filename, start_timestamp)` — 启动录制，记录起始时间戳
- `write_frame(frame_bytes)` — 写入一帧 JPEG 到 AVI
- `stop_and_save()` — 停止录制，使用 ffprobe 提取精确帧时间戳
- `_extract_timing(avi_path)` — 从 AVI 提取每帧的 PTS 时间戳列表

#### CameraServer (line 594)
WebSocket 命令处理器，维护设备查找表和客户端列表。

### 4.3 WebSocket 命令协议

| 命令 | 说明 |
|------|------|
| `list_cameras` | 枚举 USB 摄像头设备 (ffmpeg -list_devices) |
| `set_camera` | 设置某侧摄像头 (side + device_name) |
| `close_camera` | 关闭某侧摄像头 |
| `subscribe_preview` | 订阅 MJPEG 帧推送 (用于前端实时预览) |
| `unsubscribe_preview` | 取消订阅 |
| `start_continuous_recording` | 开始连续录制 (output_filename + start_timestamp) |
| `stop_and_save` | 停止录制并保存 AVI + 提取帧时间戳 |
| `get_preview_frame` | 获取单帧 JPEG (快照模式，降级用) |
| `get_server_time` | 获取 Python `time.time()` (统一时钟) |
| `get_status` | 获取摄像头状态 |

### 4.4 时间戳策略

- 录制起始时间 (`start_timestamp`) 由 realtimeEngine 从 `get_server_time` 获取，确保与 EMG 数据同源
- 每帧时间戳 = `start_timestamp + frame_index / fps`
- 录完后用 ffprobe 提取实际 PTS 精确修正

---

## 5. mocap_server.py — 动作捕捉服务

**文件**: `mocap_server.py` (790 行)  
**依赖**: `websockets`, `numpy`, Nokov SDK (可选)

### 5.1 两种运行模式

| 模式 | 数据源 | 启动参数 |
|------|--------|---------|
| **SDK 模式** | Nokov 光学动捕 SDK (服务器 IP: 10.1.1.198) | `-s 10.1.1.198` |
| **模拟器模式** | `mocap_simulator.py` (本地模拟) | `--simulator` |

### 5.2 数据通道

左右手各自独立计算两个通道：

| 通道 | 计算方式 | 值域 | 用途 |
|------|---------|------|------|
| `finger_joint_angle` | IN3→HB2 连线与手背平面法向量的夹角 | 0-90° | 连续手势1 (食指上抬) |
| `thumb_index_distance` | TH1 和 IN1 的欧氏距离 | mm | 连续手势2 (拇指食指捏合) |

### 5.3 数据流

```
Nokov SDK / Simulator
    │ marker 3D 坐标 (20 个点 × 3D)
    ▼
mocap_server.py
    │ 计算 finger_joint_angle + thumb_index_distance
    │ @ 50Hz (SEND_RATE)
    ▼
realtimeEngine.js (:8767 WebSocket)
    │ 转发
    ├──► 前端 animation-input-interface.js
    │     └── 同心圆光标驱动采集引导
    └──► storage_server.py
          └── mocap_l / mocap_r 数据集 (原始 marker 坐标)
```

### 5.4 Marker 点命名

每只手 12 个 marker + 2 个扩展 (MD1/MD2)，共 24 个:

| 手指 | Marker | 说明 |
|------|--------|------|
| 食指 | IN1, IN2, IN3 | 指尖 / 第一关节 / 第二关节 |
| 拇指 | TH1, TH2, TH3, TH4 | 指尖 → 根部 |
| 手背 | HB1, HB2, HB3 | 参考点 |
| 中指 | MD1, MD2 | 扩展点 |

---

## 6. 进程生命周期

所有 Python 进程由 Node.js 层通过 `spawn()` 管理：

```
server.js → startServer()
├── deviceSync.initialize()      → spawn ble_server.py
├── startCameraServer()          → spawn camera_server.py
└── dataStorage.initialize()     → spawn storage_server.py

mocap_server.py 由 deviceSync 根据需要启动

退出时:
server.js → gracefulShutdown()
├── deviceSync.close()           → kill ble_server.py
├── dataStorage.close()          → kill storage_server.py
├── stopCameraServer()           → kill camera_server.py (SIGTERM → SIGKILL)
└── realtimeEngine.stop()        → 关闭所有 WS + ZMQ 连接
```
