# 03 - Node.js 中间层

## 1. 概述

Node.js 中间层是系统的"神经系统"，负责进程编排、数据路由和状态管理。由 6 个模块组成：

| 模块 | 文件 | 行数 | 职责 |
|------|------|------|------|
| **realtimeEngine** | `realtimeEngine.js` | 1710 | 实时数据中枢 — 连接所有数据源与消费端 |
| **server.js** | `server.js` | 636 | HTTP 服务 + 模块编排 + REST API |
| **deviceSync** | `deviceSync.js` | 180+ | ble_server 子进程生命周期管理 |
| **dataStorage** | `dataStorage.js` | 120 | storage_server 子进程生命周期管理 |
| **cameraManager** | `cameraManager.js` | 336 | 摄像头状态管理 (前端侧) |
| **paths** | `paths.js` | 97 | 开发/打包路径自适应 |

---

## 2. realtimeEngine.js — 实时数据中枢

**文件**: `realtimeEngine.js` (1710 行)

### 2.1 架构定位

realtimeEngine 是系统的**核心数据交换机**，所有实时数据流经它：

```
                           ┌─────────────────────┐
    ble_server  ──WS──────►│                     │──WS──► 前端浏览器
    mocap_server──WS──────►│   realtimeEngine     │──WS──► 前端浏览器
    camera_server─WS──────►│      (WS :8080)      │──ZMQ─► storage_server
   前端控制命令──WS──────►│                     │──ZMQ─► storage_server
                           └─────────────────────┘
```

### 2.2 核心类: RealtimeEngine

```javascript
class RealtimeEngine extends EventEmitter {
    // === WebSocket 服务 ===
    websocket_server: null        // WS Server :8080 (前端连接)
    clients: Set                  // 已连接前端客户端

    // === 下游连接 (客户端身份) ===
    ble_client: null              // → ble_server :8766
    mocap_client: null            // → mocap_server :8767
    camera_client: null           // → camera_server :8768

    // === ZMQ 连接 ===
    storage_server_socket: zmq.Request  // REP :5555 (控制命令)
    storage_push_socket: zmq.Push       // PUSH :5556 (数据流)

    // === 采集状态 ===
    isCollecting: false           // 是否在采集
    collectionPaused: false       // 是否暂停
    isTestMode: false             // 测试模式 (不写 H5)
    stageFileOpen: false          // H5 文件是否打开
    
    // === Stream 切流 ===
    streamMode: 'idle'            // "idle" | "preview" | "collection"
    collectionBinFilenames: {dev1, dev2}
}
```

### 2.3 连接拓扑

realtimeEngine 同时扮演 **Server** 和 **Client**:

| 角色 | 端口 | 协议 | 对方 |
|------|------|------|------|
| Server | :8080 | WebSocket | 前端浏览器 (多客户端) |
| Client | → :8766 | WebSocket | ble_server 数据端 |
| Client | → :8767 | WebSocket | mocap_server |
| Client | → :8768 | WebSocket | camera_server |
| Client | → :5555 | ZMQ REP | storage_server 控制端 |
| Client | → :5556 | ZMQ PUSH | storage_server 数据端 |

### 2.4 前端命令处理

前端通过 `type: "control_command"` 消息发送命令，经 `handleFrontendMessage()` 分发：

| action | 处理函数 | 说明 |
|--------|---------|------|
| `task_change` | `onTaskChange()` | 切换采集任务，自动设置 mocap 通道 |
| `collection_start` | `onCollectionStart()` | 启动采集 → 同步时钟 → 开 H5 → 启录像 |
| `collection_stop` | `onCollectionStop()` | 停止采集 → 保存视频 → 关 H5 |
| `collection_pause/resume` | `onCollectionPause/Resume()` | 暂停/恢复 |
| `session_change` | `onSessionChange()` | 切换轮次 |
| `stage_start` | `onStageStart()` → `openStageFile()` | 开始 Stage → 创建 H5 文件 |
| `stage_end` | `onStageEnd()` → `closeStageFile()` | 结束 Stage → 关闭 H5 |
| `prompt` | `onPrompt()` | 保存手势 prompt 到 H5 |
| `abnormal_interrupt_freeze` | `onAbnormalInterruptFreeze()` | 冻结写入 (不关 H5) |
| `abnormal_interrupt` | `onAbnormalInterrupt()` | 异常中断 → 关闭 H5 + 标记 |
| `video_recording_started` | `onVideoRecordingStarted()` | 录制通知 → 写 H5 attrs |
| `camera_set_config` | `onCameraSetConfig()` | 设置摄像头 |
| `mocap_set_channel` | `onMocapSetChannel()` | 设置动捕通道 |

### 2.5 BLE 数据处理

`handleBleDataPacket()` (line 1400) — 核心数据处理流水线：

```
BLE 数据包到达
  ├─ normalizeImuData() — V1/V2 IMU 规范化
  │    V1: 2 chips, acc+gyr+mag
  │    V2: 0-3 chips, acc+gyr (no mag)
  │
  ├─ transposeEMG() — EMG 数据转置 (按帧→按通道)
  │
  ├─ 构建 dataItem → realtimeDataBuffer (批量发送缓冲区)
  │   每 3 个包或 50ms 触发 flush
  │
  ├─ broadcastToClients() → 前端实时波形
  │
  └─ saveDataToStorage() → storage_server (仅 collection stream)
       │  条件: isCollecting && !paused && stageFileOpen && !testMode
       │  时间过滤: 丢弃 collection_start 之前的陈旧数据
       └─ ZMQ PUSH :5556
```

### 2.6 批量发送缓冲

```javascript
// 批量发送策略
realtimeDataBufferLimit = 3;    // 缓冲 3 个 BLE 包
realtimeDataMaxDelay = 50;      // 最大延迟 50ms

// 触发条件: 缓冲区满 或 定时器到期
// flushRealtimeDataBuffer() 一次性发送整个 batch 给前端
```

### 2.7 统一时钟机制

```javascript
// collection_start 时获取 Python 时钟 (消除 Node.js/Python 时钟偏差)
const timeResult = await this.sendCameraCommand('get_server_time', {});
this.collectionDataStartTs = timeResult.server_time;

// 使用此时间戳过滤陈旧 BLE 数据
isFreshCollectionPacket = storagePacketTs >= (collectionDataStartTs - 0.05);
```

### 2.8 异常中断流程

```
前端点击"异常中断"
  ├─ abnormal_interrupt_freeze → 立即停止 append (isCollecting = false)
  │    H5 文件保持 open（不关闭）
  │
  └─ abnormal_interrupt → 关闭 H5 + 写入状态 attrs:
       ├─ collection_status = "abnormal_interrupted"
       ├─ interrupted_at (ISO 时间)
       ├─ interrupt_reason
       ├─ resume_progress (JSON 进度快照)
       └─ breakpoint_state (完整可恢复状态)
```

---

## 3. server.js — HTTP 服务与编排

**文件**: `server.js` (636 行)

### 3.1 启动顺序

```javascript
async function startServer() {
    await realtimeEngine.start(8080);      // 1. WebSocket 中枢
    await deviceSync.initialize();          // 2. spawn ble_server
    await startCameraServer();             // 3. spawn camera_server
    await dataStorage.initialize();         // 4. spawn storage_server
    app.listen(3000);                      // 5. HTTP 服务
}
```

### 3.2 REST API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/storage/files` | GET | 递归列出 storage 目录下所有 .h5 文件 |
| `/api/config/files` | GET | 列出 config 目录下所有 .json 文件 |
| `/api/config/load/:filename` | GET | 读取单个配置 JSON |
| `/api/config/save` | POST | 保存配置 JSON |
| `/api/config/delete/:filename` | DELETE | 删除配置文件 |
| `/api/device-status` | GET | 获取设备协同模块状态 |
| `/api/storage-volume` | GET | 获取存储空间信息 |
| `/api/camera/list` | GET | 枚举摄像头 (降级路由) |
| `/api/camera/set-camera` | POST | 配置摄像头 (降级路由) |
| `/api/camera/status` | GET | 获取摄像头状态 |
| `/button-click` | POST | 转发按钮事件到 realtimeEngine |

### 3.3 优雅关闭

```
SIGINT/SIGTERM →
  ├─ deviceSync.close()       → kill ble_server
  ├─ realtimeEngine.stop()    → 关闭所有 WS + ZMQ
  ├─ dataStorage.close()      → kill storage_server
  └─ stopCameraServer()       → SIGTERM → 2s → SIGKILL
```

---

## 4. deviceSync.js — BLE 子进程管理

**文件**: `deviceSync.js` (180+ 行)

```javascript
class DeviceSync extends EventEmitter {
    pythonProcess: null         // ble_server 子进程引用
    mocapProcess: null          // mocap_server 子进程引用
    
    async initialize() {
        // spawn ble_server.py
        this.pythonProcess = spawn(command, args, { env: PYTHON_ENV });
        // 监控 stdout/stderr, 统计吞吐量
    }
    
    getStatus() {
        return { isConnected, dataCount, currentRate, ... };
    }
    
    getStorageVolumeInfo() {
        // 获取磁盘剩余空间
    }
}
```

---

## 5. dataStorage.js — Storage 子进程管理

**文件**: `dataStorage.js` (120 行)

轻量级进程管理器，仅负责：
- spawn `storage_server.py --storage_dir <PATHS.storage>`
- 监控进程状态
- 提供 `getStatus()` 接口

```javascript
class DataStorage extends EventEmitter {
    pythonProcess: null
    
    async initialize() {
        this.pythonProcess = spawn(command, args);
    }
    
    async close() {
        this.pythonProcess.kill();
    }
}
```

---

## 6. cameraManager.js — 摄像头状态管理

**文件**: `cameraManager.js` (336 行)

前端侧的摄像头状态管理器（Node.js 进程内），维护：

```javascript
class CameraManager extends EventEmitter {
    cameras: { left: null, right: null }
    cameraStatus: {
        left/right: { deviceId, label, streaming, recording, resolution, fps }
    }
    recordingConfig: { videoFormat: 'mp4', fps: 30, resolution: '1280x720' }
}
```

**注意**: 实际摄像头操作（预览/录制）由 `camera_server.py` 执行，`cameraManager` 仅维护状态。

---

## 7. paths.js — 路径自适应

**文件**: `paths.js` (97 行)

自动检测运行环境并返回正确路径：

| 环境 | 源码目录 (read) | 数据目录 (write) |
|------|----------------|-----------------|
| 开发 | `__dirname` | `__dirname` |
| Electron 打包 | `app.asar` 内部 | `process.execPath` 同级 |

```javascript
const PATHS = {
    source: getSourceRoot(),                     // 只读
    public: path.join(getSourceRoot(), 'public'),
    data: getDataRoot(),                         // 可读写
    storage: path.join(getDataRoot(), 'storage'),
    config: path.join(getDataRoot(), 'config'),
    log: path.join(getDataRoot(), 'log'),
};
```

---

## 8. pythonPath.js — Python 解释器解析

自动选择 Python 运行方式：
1. 开发环境 → `python script.py`
2. Electron 打包 → `script.exe` (PyInstaller 产物)
