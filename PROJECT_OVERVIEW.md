# sEMG Collection System — 项目全貌

> **阅读本文即可上手。** 本文是项目的唯一入口文档，涵盖架构、模块职责、数据流、文件清单、代码约定和已知问题。所有其他文档的索引见文末。

---

## 1. 项目是什么

**表面肌电信号（sEMG）数据采集系统**，用于手势识别研究。核心功能：

- 通过 **蓝牙 BLE** 连接 ESP32-S3 腕带设备，实时采集 **16通道 EMG + IMU（加速度/陀螺仪）** 数据
- 通过 **USB 摄像头** 录制手部动作视频（左右双侧）
- 通过 **Nokov 光学动捕系统** 获取手部标记点三维坐标
- 数据统一存入 **HDF5 文件**，附带完整元数据和采集协议信息
- 提供 **PyQt5 桌面工具** 进行数据可视化、同步校准、视频回放

运行环境：**Windows 10/11**，以 **Electron** 桌面应用形式运行。

---

## 2. 三层架构一览

```
┌──────────────────────────────────────────────────────────┐
│  Tier 3: 浏览器前端 (Electron BrowserWindow)              │
│  public/index.html + 22 个 JS 模块                        │
│  采集引导 → 手势动画 → 波形显示 → 摄像头预览 → 文件管理    │
└────────────────────────┬─────────────────────────────────┘
                         │ WebSocket (多个端口)
                         │ HTTP (Express, :3000)
┌────────────────────────┴─────────────────────────────────┐
│  Tier 2: Node.js 中间层 (server.js 启动)                  │
│  realtimeEngine.js  ← 数据路由中枢 (ZMQ + WebSocket)       │
│  deviceSync.js      ← BLE/摄像头设备生命周期管理           │
│  dataStorage.js     ← HDF5 存储服务管理                    │
│  cameraManager.js   ← 摄像头状态机                         │
└────────────────────────┬─────────────────────────────────┘
                         │ ZMQ (IPC) + subprocess stdin/stdout
┌────────────────────────┴─────────────────────────────────┐
│  Tier 1: Python 后端服务 (4 个独立子进程)                  │
│  ble_server.py      ← BLE 数据采集 (WebSocket :8764/8766) │
│  camera_server.py   ← 摄像头管理   (WebSocket :8768)      │
│  mocap_server.py    ← 动捕数据     (WebSocket :8767)      │
│  storage_server.py  ← HDF5 存储    (WebSocket :8765)      │
└──────────────────────────────────────────────────────────┘
```

### 启动链

```
main.js (Electron) → spawn server.js → server.js 初始化所有模块
                   → 创建 BrowserWindow 加载 http://localhost:3000
                   → serve 静态文件 (public/)
```

**Python 子进程由各自对应的 Node 模块 spawn**（`deviceSync.js`→`ble_server.py`+`mocap_server.py`，`dataStorage.js`→`storage_server.py`），而非 server.js 直接启动。

---

## 3. 完整文件清单（~200 个源文件）

### 3.1 Node.js 后端（10 个文件，~4000 行）

| 文件 | 行数 | 职责 |
|------|------|------|
| `main.js` | 169 | Electron 主进程：创建窗口，spawn server.js，子进程生命周期管理，健康检查轮询 |
| `server.js` | 651 | 应用编排器：Express 服务(:3000)，启动所有后端模块，提供 `/api/health` |
| `realtimeEngine.js` | 1759 | **核心**：WebSocket 服务器，ZMQ PUB/SUB 内部路由，BLE/摄像头/动捕/存储数据转发 |
| `deviceSync.js` | 458 | 设备同步：spawn ble_server + mocap_server，吞吐量统计，摄像头录制控制 |
| `cameraManager.js` | 335 | 摄像头状态机：枚举、预览、录制状态管理 |
| `dataStorage.js` | 126 | 存储管理：spawn storage_server，文件保存统计 |
| `logger.js` | 217 | 日志模块：文件轮转（20MB×10），双输出 |
| `paths.js` | 96 | 路径解析：兼容 dev/Electron-packaged 两种模式 |
| `pythonPath.js` | 50 | Python 解释器选择：dev 用 `.py` 脚本，packaged 用 `.exe` |
| `constants.js` | 36 | 共享常量：任务名、手势名、采集阶段名 |

### 3.2 Python 后端服务（4 个常驻进程，~6000 行）

| 文件 | 行数 | 端口 | 职责 |
|------|------|------|------|
| `ble_server.py` | 2364 | WS:8764(控制) / :8766(数据) | BLE 连接腕带，接收 EMG+IMU，支持双设备 |
| `camera_server.py` | 1324 | WS:8768 | USB 摄像头枚举(ffmpeg DShow)，MJPEG 预览，AVI 录制 |
| `mocap_server.py` | 790 | WS:8767 | Nokov 动捕 SDK 数据接收，手势参数计算 |
| `storage_server.py` | 1552 | WS:8765 | HDF5 存储，会话管理，断点续存，备份 |

### 3.3 Python 桌面工具（`tools/`，8 个文件，~15,000 行）

| 文件 | 行数 | 用途 |
|------|------|------|
| `tools/hdf5_tool.py` | 4930 | H5 文件浏览器 + BLE/SD 数据同步（PyQt5 GUI） |
| `tools/bin_sync_tool.py` | 2935 | EMG/IMU 帧号映射同步（250Hz BLE ↔ 2kHz SD） |
| `tools/calibrate_tool.py` | 2851 | 数据可视化 + scipy 滤波器 + 视频回放 + Prompt 标注 |
| `tools/hdf5_stage_viewer.py` | 1061 | 按采集阶段浏览 H5 数据 |
| `tools/diagnose_imu.py` | 235 | IMU 数据健康诊断（跨会话对比） |
| `tools/mp4_2_gif.py` | 240 | MP4 转 GIF（压缩） |
| `tools/build_tool.py` | 84 | tools 打包为 exe（PyInstaller） |
| `tools/build_viewer.py` | 69 | hdf5_tool 单独打包 |

### 3.4 Python 工具脚本（根目录，~1700 行）

| 文件 | 行数 | 用途 |
|------|------|------|
| `build_python.py` | 149 | 打包所有 Python 后端为 exe |
| `mocap_simulator.py` | 662 | 动捕模拟器（读取 TRC 文件，WebSocket 转发） |
| `mocap_client_demo.py` | 312 | 动捕模拟器测试客户端 |
| `mocap_server_test.py` | 253 | mocap_server 测试套件 |
| `test_camera.py` | 96 | 摄像头集成测试 |
| `list_devices_raw.py` | 57 | ffmpeg 摄像头枚举 |
| `check_ffmpeg.py` | 22 | ffmpeg 可用性检测 |

### 3.5 浏览器前端（`public/`，22 个 JS 模块，~56,000 行）

**采集核心：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `scripts/collection-controller.js` | 4197 | **采集主控**：任务选择→阶段切换→Trial 管理→事件分发 |
| `scripts/collection-selector.js` | 653 | 采集引导向导（任务/类别/场景/受试者选择） |
| `scripts/collection-constants.js` | 667 | 采集常量：时间参数、UI 配置、手势定义 |

**动画引擎：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `scripts/animation-controller.js` | 534 | 动画编排器：开场、阶段内容、倒计时动画 |
| `scripts/discrete-gesture-animation.js` | 1463 | 离散手势动画：倒计时、文字提示、进度跟踪 |
| `scripts/continual-gesture-1-animation.js` | 1088 | 连续手势1：食指角度跟踪（滚轮式光标） |
| `scripts/continual-gesture-2-animation.js` | 1059 | 连续手势2：拇指-食指捏合跟踪 |
| `scripts/continual-gesture-3-animation.js` | 726 | 连续手势3：手掌旋转角度可视化 |
| `scripts/calibration-guide-animation.js` | 616 | 传感器校准引导动画 |
| `scripts/animation-position-manager.js` | 474 | 动画元素屏幕位置管理 |
| `scripts/animation-input-interface.js` | 791 | 手势动画期间的键盘/鼠标/触摸输入 |

**设备与数据：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `scripts/ble_control.js` | 906 | BLE 设备连接/断开，采集启停 |
| `scripts/camera-ui.js` | 1147 | 摄像头预览、录制控制、设备选择 |
| `scripts/camera_control.js` | 527 | 摄像头 WebSocket 命令通信 |
| `scripts/waveform.js` | 570 | 波形主入口：集成渲染器与 WebSocket |
| `scripts/waveform-renderer.js` | 593 | Canvas 实时 EMG 波形渲染 |
| `scripts/fullscreen-waveform.js` | 337 | 全屏波形模式 |
| `scripts/signal-quality.js` | 286 | EMG 信号质量指示器 |
| `scripts/device-status-widget.js` | 338 | 浮动状态窗：连接/电量/流模式 |

**配置与后台：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `scripts/template-config.js` | 2622 | 采集模板编辑器（类别层级+手势库） |
| `scripts/config-manager.js` | 913 | 模板导入/预览（思维导图式）/应用 |
| `scripts/backend-manager.js` | 937 | 后台数据管理：文件列表、存储统计、变更提示 |
| `scripts/page-switch.js` | 775 | 页面导航：欢迎页/采集页/后台页 + 用户管理 |

### 3.6 腕带固件（`wband_emg_V1/V2/V3/`）

三个版本的 ESP32 腕带 PC 客户端（PyQt5），功能逐步演进：

| 目录 | 核心文件 | 行数 | 版本特点 |
|------|----------|------|----------|
| `wband_emg_V1/` | `wband_emg_client_V3.py` | 1091 | 原始版本 |
| `wband_emg_V2/` | `wband_emg_client_V5.py` | 1638 | 改进协议，含 ESP32-S3 固件源码 |
| `wband_emg_V3/` | `wband_emg_client_V5.py` | 2083 | 最新版本，信号质量评估 |

**ESP32-S3 固件**（`wband_emg_V2/wband_emg_esp32s3_v5/main/`）：
- `main.c` — 固件入口
- `ads1298.c/.h` — 16通道 ADC 驱动
- `lsm6dsv32x.c/.h` — IMU 传感器驱动
- `ble_gatt.c/.h` — BLE GATT 服务
- `bq25120.c/.h` — 电池管理
- `sd_storage.c/.h` — SD 卡存储

### 3.7 配置与数据

| 路径 | 说明 |
|------|------|
| `package.json` | Node 依赖（electron, express, ws, zeromq, uuid） |
| `requirements.txt` | Python 依赖（bleak, websockets, msgpack, h5py, scipy, opencv-python, PyQt5） |
| `defconfg.json` | 默认采集模板（任务、类别、场景、手势 Prompt 定义） |
| `config/*.json` | 历史采集配置快照（17 个） |
| `log/*.txt` | 运行日志（自动轮转） |
| `storage/video/` | AVI 视频 + `.timing.json` 帧时间戳 |
| `storage/离散手势采集/` | H5 数据文件（按任务/类别/场景/受试者组织） |
| `L015_bin/` | SD 卡原始二进制数据 |
| `L015_h5/` | 已同步的 H5 数据集 |
| `public/tutorial/gestures/` | 手势教学 GIF 动画（离散24个 + 连续3个） |
| `public/tutorial/video/` | 教学视频（4个） |

---

## 4. 数据流

### 4.1 采集流程

```
腕带ESP32 ──BLE──→ ble_server.py ──ZMQ PUB──→ realtimeEngine.js
                                                    │
USB摄像头 ──ffmpeg─→ camera_server.py ──ZMQ PUB──→  │
                                                    │
Nokov SDK ──UDP──→ mocap_server.py ──WS────→        │
                                                    ↓
                                          realtimeEngine.js (路由中枢)
                                           │          │
                                           ↓          ↓
                                   storage_server.py   前端 WebSocket
                                   (写入 HDF5)        (波形/动画/UI更新)
```

### 4.2 关键端口

| 端口 | 协议 | 用途 |
|------|------|------|
| 3000 | HTTP | Express 静态文件服务 + REST API |
| 8764 | WebSocket | ble_server 控制命令 |
| 8765 | WebSocket | storage_server 数据写入 |
| 8766 | WebSocket | ble_server EMG/IMU 数据流 |
| 8767 | WebSocket | mocap_server 动捕数据 |
| 8768 | WebSocket | camera_server 视频帧 + 控制 |
| 8080 | WebSocket | realtimeEngine→前端 实时数据推送 |

### 4.3 HDF5 文件结构

```
.h5
├── raw/
│   ├── emg/          (16ch × N samples, float32)
│   └── imu/          (acc_* + gyro_* per sensor, float32)
├── sync/
│   ├── emg_timestamps/
│   ├── imu_timestamps/
│   └── video_frame_times/
├── video/
│   ├── left_data/    (原始帧 bytes)
│   └── right_data/
├── markers/          (动捕标记点坐标)
└── attrs/
    ├── subject_info  (受试者ID、年龄、性别等)
    ├── task_info     (任务名、类别、场景)
    ├── collection_protocol
    ├── device_info   (MAC地址、固件版本)
    └── video_metadata (分辨率、编码、FPS)
```

---

## 5. 技术栈

| 层 | 技术 |
|----|------|
| 桌面框架 | **Electron 28** |
| 前端 | 原生 JS (ES6+)，无框架，Canvas 2D 绘图，**dygraph** 图表库 |
| 前端样式 | 原生 CSS（~6800行在 index.html 内联），**Font Awesome 6** 图标 |
| 后端运行时 | **Node.js** (Express + ws + zeromq) |
| Python 运行时 | **Python 3.11** |
| BLE 通信 | **bleak** (Python 异步 BLE 库) |
| 视频处理 | **ffmpeg** (DShow 采集)，**OpenCV** (帧处理) |
| 数据存储 | **HDF5** (h5py)，**LZ4** 压缩 |
| 信号处理 | **scipy** (滤波、插值) |
| 桌面工具 GUI | **PyQt5** |
| 数据序列化 | **msgpack** (二进制)，JSON (配置) |
| 进程间通信 | **ZMQ** (PUB/SUB)，**WebSocket** |
| 打包 | **PyInstaller** (Python→exe)，**electron-builder** (Node→exe) |

---

## 6. 代码约定与已知模式

> 这些是审计修复过程中总结的，新增代码务必遵守。

### 6.1 必须遵守的规则

1. **禁止 `new Promise(async (resolve) => {...})`** — async Promise executor 会导致 rejected Promise 丢失。改用 `async function() { ...; return new Promise((resolve) => ...); }`

2. **禁止 Python bare `except:`** — 必须使用 `except Exception:`，否则会拦截 `KeyboardInterrupt` 和 `SystemExit`，导致进程无法正常终止

3. **所有 `JSON.parse(localStorage.getItem(...))` 必须 try/catch** — 存储数据可能损坏或格式不对

4. **addEventListener 的匿名函数必须用命名变量存储** — 否则 `removeEventListener` 无效，造成内存泄漏。拖拽事件（mousemove/mouseup）尤其关键

5. **ResizeObserver / MutationObserver 必须 `.disconnect()`** — 在 `destroy()`/`stop()`/`beforeunload` 中清理

6. **setInterval / setTimeout 返回的 ID 必须存储** — 在清理函数中 `clearInterval`/`clearTimeout`

7. **Python 子进程 spawn 时必须设置 `PYTHONIOENCODING: 'utf-8'`** — Windows 下编码问题的根源

8. **ffmpeg 子进程必须有超时保护** — `camera_server.py` 中 ffmpeg 录制超时为 `max(60, recording_duration * 0.5)`

9. **ZMQ：发送端 bind 必须在接收端 connect 之前** — 否则消息丢失

10. **HDF5 写入：先写成功再清旧数据** — `hdf5_tool.py` 和 `calibrate_tool.py` 中已修复

### 6.2 架构约定

- **`realtimeEngine.js` 是数据中枢** — 所有 Python 服务通过 ZMQ PUB 发数据给它，它再路由到前端和 storage_server
- **`deviceSync.js` 和 `dataStorage.js` 是单例** — 通过 `module.exports` 导出实例，不是类
- **前端模块用 IIFE 模式** — `(function() { 'use strict'; ... })()`，通过 `window.xxx` 暴露接口
- **前后端通过 WebSocket 通信** — 不是 HTTP REST（REST 只用于状态查询）
- **采集流程状态由 `collection-controller.js` 管理** — 它是一个 God Object（~4200行），已知设计债务

### 6.3 路径处理

- **开发模式 vs 打包模式**：`paths.js` 根据 `process.resourcesPath` 判断
- **Python 脚本路径**：`pythonPath.js` 决定用 `.py`（开发）还是 `.exe`（打包）

---

## 7. 运行与构建

### 开发模式

```bash
# 1. 安装 Python 依赖
pip install -r requirements.txt

# 2. 安装 Node 依赖
npm install

# 3. 确保 ffmpeg 在 PATH 中（或运行 deps/install_ffmpeg.bat）

# 4. 启动
npm start
# 或者直接: node main.js
```

### 打包

```bash
# Python 后端打包为 exe（可选，减小分发体积）
python build_python.py

# Electron 打包
npx electron-builder
```

### 单独运行工具

```bash
# HDF5 浏览器 + 数据同步
python tools/hdf5_tool.py

# 数据可视化 + 视频回放
python tools/calibrate_tool.py

# BIN 数据同步（命令行）
python tools/bin_sync_tool.py --bin-dir L015_bin --h5-file output.h5
```

---

## 8. 已完成的审计与修复

项目在 `audit_fix` 分支上经历了全面代码审计，修复了 **77 个问题**（11 次提交）：

| 类别 | 修复数量 | 关键项 |
|------|----------|--------|
| 阻塞性 Bug | 5 | async Promise executor、ZMQ 时序、h5py 文件锁 |
| 数据安全 | 6 | 先写后清、文件回滚、HDF5 写入验证 |
| 正确性 | 8 | ffmpeg 超时、BLE stream_mode 状态、健康检查替代盲等 |
| 安全+性能 | 7 | exec→spawn 防注入、端口验证、Express dead code 移除 |
| 防御性编程 | 28 | bare except→Exception(26处)、JSON.parse 保护(6处) |
| 资源泄漏 | 8 | Observer disconnect、Timer 清理、Listener 移除 |
| 其他 | 15 | 硬编码 MAC 移除、switch default、未知命令响应 |

**已知未修复的设计债务**（需要大规模重构，留待后续版本）：
- `collection-controller.js` God Object 分解（~4200行集中了太多职责）
- localStorage 使用缺乏统一管理
- WebSocket 端口过多（6 个），可合并精简
- 缺少 watchdog 进程监控和自动重启
- EMGBinParser 未使用内存映射（大文件性能瓶颈）

---

## 9. 文档索引

### 本文档库（`docs/`）

**核心文档（`docs/project/`）**：
- [01-architecture.md](docs/project/01-architecture.md) — 系统架构详解
- [02-python-servers.md](docs/project/02-python-servers.md) — Python 后端服务
- [03-node-middleware.md](docs/project/03-node-middleware.md) — Node.js 中间层
- [04-collection-ui.md](docs/project/04-collection-ui.md) — 采集 UI 详解
- [05-animation-engine.md](docs/project/05-animation-engine.md) — 动画引擎
- [06-monitoring.md](docs/project/06-monitoring.md) — 设备监控与状态
- [07-data-format.md](docs/project/07-data-format.md) — 数据格式规范
- [08-bin-sync-tool.md](docs/project/08-bin-sync-tool.md) — BIN 同步工具
- [09-hdf5-tool.md](docs/project/09-hdf5-tool.md) — HDF5 浏览器工具
- [10-calibrate-tool.md](docs/project/10-calibrate-tool.md) — 标定与可视化工具
- [11-build-deploy.md](docs/project/11-build-deploy.md) — 构建与部署
- [12-audit-report.md](docs/project/12-audit-report.md) — 180 项问题审计报告

**专题文档（`docs/claude/`）**：
- [ARCHITECTURE.md](docs/claude/ARCHITECTURE.md) — 架构图与模块关系
- [DATA_FORMAT.md](docs/claude/DATA_FORMAT.md) — 数据传输格式
- [README.md](docs/claude/README.md) — 项目说明
- [CAMERA_INTEGRATION_README.md](docs/claude/CAMERA_INTEGRATION_README.md) — 摄像头集成说明
- [FFMPEG_EXPLANATION.md](docs/claude/FFMPEG_EXPLANATION.md) — ffmpeg 技术细节
- [HLS_INTEGRATION_GUIDE.md](docs/claude/HLS_INTEGRATION_GUIDE.md) — HLS 推流指南
- [TESTING_GUIDE.md](docs/claude/TESTING_GUIDE.md) — 测试指南
- [REFACTOR_COMPLETION_REPORT.md](docs/claude/REFACTOR_COMPLETION_REPORT.md) — 重构完成报告

**其他**：
- [docs/IMU数量推断修复方案.md](docs/IMU数量推断修复方案.md) — IMU 传感器计数修复
- [deps/README.md](deps/README.md) — 依赖安装说明
- [public/tutorial/README.md](public/tutorial/README.md) — 教学视频说明

---

## 10. Git 分支结构

```
main_windows  ← 主分支（Windows 稳定版）
  │
  └── fix_new     ← 功能开发分支
  │
  └── audit_fix   ← 代码审计与修复分支（77项修复，11次提交）
```

---

> **本文档更新于 2026-06-25。** 阅读本文后如有疑问，优先查阅 `docs/project/` 中的详细文档和源代码中的注释。
