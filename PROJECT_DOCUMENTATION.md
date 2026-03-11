# 肌电手环数据采集系统 - 项目文档

## 📋 目录
- [项目概述](#项目概述)
- [项目结构](#项目结构)
- [技术栈](#技术栈)
- [核心模块详解](#核心模块详解)
- [数据流架构](#数据流架构)
- [前端界面](#前端界面)
- [部署方式](#部署方式)
- [开发指南](#开发指南)
- [参考资源](#参考资源)

---

## 项目概述

这是一个针对**华为肌电手环（EMG手环）**的数据采集软件系统，用于收集肌电（sEMG）和运动捕捉（MoCap）数据。

### 核心功能
- 💾 **实时数据采集**：支持多种手势的离散和连续采集
- 📊 **实时数据展示**：可视化EMG和IMU数据波形
- 🎯 **动作标签化**：为采集的数据自动标记对应的手势/姿态阶段
- 💿 **数据持久化**：将采集数据保存为HDF5格式
- 🎬 **动作指导**：通过动画引导用户进行标准化动作采集

### 支持设备
- **EMG手环**：最多2台BLE设备，16通道肌电信号 + 9轴IMU数据
- **动捕系统**：Nokov光学动捕系统（可选）

---

## 项目结构

```
sEMG_Collection_System/
├── 【核心后端模块】
│   ├── server.js                 # 应用入口，启动所有模块和Web服务
│   ├── deviceSync.js             # 设备同步模块，管理BLE和MoCap服务器
│   ├── realtimeEngine.js         # 实时引擎，协调数据流和存储逻辑
│   ├── dataStorage.js            # 数据存储模块，启动storage_server
│   ├── main.js                   # Electron主进程配置
│   ├── constants.js              # 全局常量定义
│   ├── logger.js                 # 日志系统
│   ├── paths.js                  # 路径管理（开发/打包环境兼容）
│   ├── pythonPath.js             # Python命令解析器
│   └── package.json              # Node.js依赖配置
│
├── 【核心Python模块】
│   ├── ble_server.py             # BLE数据接收服务器（真实设备）
│   ├── ble_server_real.py        # BLE实现选项（参考）
│   ├── ble_server_sim.py         # BLE模拟器（开发调试用）
│   ├── mocap_server.py           # 动捕数据接收和解算服务器
│   ├── mocap_simulator.py        # 动捕模拟器（开发调试用）
│   ├── storage_server.py         # 数据存储服务器（HDF5文件保存）
│   ├── mocap_client_demo.py      # 动捕客户端示例
│   └── build_python.py           # Python脚本打包工具
│
├── 【前端模块】
│   └── public/
│       ├── index.html                    # 主页面
│       ├── dygraph.min.js                # 数据图表库
│       ├── lib/
│       │   ├── lab.js                    # 实验室框架
│       │   └── lab.dev.js
│       └── scripts/
│           ├── waveform.js               # 波形渲染（基础）
│           ├── waveform-renderer.js      # 波形渲染（增强版）
│           ├── collection-controller.js  # 采集流程控制
│           ├── collection-selector.js    # 采集类型选择
│           ├── discrete-gesture-animation.js     # 离散手势动画
│           ├── continual-gesture-1-animation.js  # 连续手势1动画
│           ├── continual-gesture-2-animation.js  # 连续手势2动画
│           ├── continual-gesture-3-animation.js  # 连续手势3动画
│           ├── calibration-guide-animation.js    # 标定指导动画
│           ├── animation-controller.js   # 动画控制器
│           ├── animation-input-interface.js # 动画输入接口
│           ├── backend-manager.js        # 后端通信管理
│           ├── config-manager.js         # 配置文件管理
│           ├── page-switch.js            # 页面切换逻辑
│           ├── ble_control.js            # BLE控制接口
│           ├── collection-constants.js   # 采集常量定义
│           ├── template-config.js        # 模板配置
│           └── collection-controller-patch.js # 采集补丁
│
├── 【工具模块】
│   └── tools/
│       ├── hdf5_tool.py              # HDF5文件分析工具
│       ├── hdf5_stage_viewer.py      # HDF5数据查看器
│       ├── bin_sync_tool.py          # 二进制同步工具
│       └── build_viewer.py           # 界面构建工具
│
├── 【参考资源】
│   ├── band/
│   │   ├── gatts_demo_imu-v3.1-260128.c
│   │   │   # 供应商提供的肌电手环上位机代码
│   │   │   # BLE数据接收、滤波算法的参考实现
│   │   │
│   │   └── wband_app_v3_code_260128/
│   │       ├── wband_emg_client_V3.py  # 肌电手环客户端
│   │       ├── custom_widgets.py       # 自定义UI组件
│   │       └── signalfilter.py         # 信号滤波
│   │
│   ├── mocap_sdk/
│   │   └── examples/
│   │       ├── Nokov_SDK_Client.py     # 动捕SDK基础示例
│   │       ├── Nokov_SDK_Client_With_Vel_Acc_Degree.py
│   │       └── Utility.py              # 动捕SDK工具函数
│   │
│   └── motion_marker_data/
│       └── visualize_markers.py  # 动捕标记点可视化工具
│
├── 【配置文件】
│   ├── defconfg.json             # 默认配置（请注意拼写：defconfg）
│   └── config/                   # 采集配置保存目录
│       └── *.json               # 用户采集配置模板
│
├── 【数据存储】
│   └── storage/                  # HDF5数据文件保存目录
│       └── *.h5, *.hdf5         # 采集的原始数据文件
│
└── 【日志】
    └── logs/                    # 运行日志目录
        └── server_*.log        # 服务器日志文件
```

---

## 技术栈

### 后端技术
| 技术 | 用途 | 版本 |
|------|------|------|
| **Node.js** | 主应用框架 | 14.x+ |
| **Express.js** | HTTP服务器框架 | ^4.19.2 |
| **WebSocket (ws)** | 实时数据推送 | ^8.18.3 |
| **ZeroMQ (zeromq)** | 进程间通信 | ^6.5.0 |
| **Electron** | 桌面应用打包 | 最新版 |

### 后端依赖
```json
{
  "serialport": "^13.0.0",           // 串口通信
  "cors": "^2.8.5",                  // 跨域资源共享
  "dygraphs": "^2.2.1"               // 数据图表
}
```

### 前端技术
- **HTML5 Canvas**：波形实时渲染
- **原生JavaScript**：无框架依赖（轻量级）
- **WebSocket**：实时数据接收
- **Dygraphs**：时间序列数据可视化

### Python技术
| 库 | 用途 |
|----|------|
| **asyncio** | 异步编程 |
| **websockets** | WebSocket服务器 |
| **bleak** | BLE通信 |
| **h5py** | HDF5文件操作 |
| **numpy** | 数值计算 |
| **scipy** | 信号处理（滤波） |

---

## 核心模块详解

### 1. server.js - 应用入口

**职责**：协调所有模块的启动和HTTP服务

**启动流程**：
```
server.js启动
  ↓
初始化日志系统 (logger.js)
  ↓
启动 realtimeEngine (WebSocket服务，端口8080)
  ↓
启动 deviceSync (BLE和MoCap服务)
  ↓
启动 dataStorage (HDF5存储服务)
  ↓
启动 Express HTTP服务 (端口3000)
  ↓
自动打开浏览器（仅非Electron模式）
```

**重要API**：
- `GET /api/device-status` - 获取设备连接状态和实时数据
- `GET /api/storage/files` - 获取所有采集的HDF5文件列表
- `GET /api/config/files` - 获取所有采集配置文件
- `POST /api/config/save` - 保存新的采集配置
- `GET /api/config/load/:filename` - 加载指定配置
- `POST /button-click` - 接收前端按钮事件

---

### 2. deviceSync.js - 设备同步模块

**职责**：启动和管理外部Python设备服务

**核心子进程**：
1. **ble_server.py** (端口8766)
   - 通过BLE与肌电手环通信
   - 支持最多2台设备并发
   - 每个设备独立的WebSocket连接
   - 输出：原始16通道EMG + 9轴IMU数据

2. **mocap_server.py** (端口8767)
   - 连接Nokov动捕系统
   - 接收marker点位置
   - 进行关节点解算
   - 输出：3D骨架数据

**关键方法**：
```javascript
async initialize()          // 初始化并启动两个服务
getStatus()                 // 获取连接状态
getCurrentThroughput()      // 获取数据吞吐量
getStorageVolumeInfo()      // 获取磁盘容量信息
async close()               // 优雅关闭所有子进程
```

---

### 3. realtimeEngine.js - 实时引擎

**职责**：协调数据流、接收采集指令、控制数据存储

**核心功能**：

#### 3.1 数据接收与转发
```
BLE服务器 (ws://localhost:8766)
      ↓
realtimeEngine (WebSocket服务端)
      ↓
      ├→ 前端浏览器 (实时波形渲染)
      └→ Storage服务器 (条件存储)

MoCap服务器 (ws://localhost:8767)
      ↓
realtimeEngine
      ↓
      └→ 前端 + Storage (可选)
```

#### 3.2 采集状态管理
```javascript
this.isCollecting          // 是否正在采集
this.collectionConfig      // 当前采集配置
this.currentStageName      // 当前采集阶段名称
this.currentSessionIndex   // 当前会话索引
this.sessionCount          // 总会话数
```

#### 3.3 阶段标签系统
- 实时接收前端采集脚本的**stage start/end**信号
- 接收**prompt**提示信号
- 将这些标签元数据附加到EMG数据中
- 转发给Storage服务器进行HDF5保存

**重要事件**：
```javascript
taskManager_get_command()      // 接收采集指令（开始/停止/暂停）
stage_start()                  // 采集阶段开始
stage_end()                    // 采集阶段结束
set_prompt()                   // 设置提示信息
```

---

### 4. dataStorage.js - 数据存储模块

**职责**：启动Python storage_server进程

**数据流**：
```
realtimeEngine (ZMQ Request)
      ↓
storage_server.py (ZMQ Reply)
      ↓
将接收的EMG/MoCap数据 + 标签
      ↓
存储为HDF5文件（按时间/配置组织）
```

**存储路径**：
- 开发环境：`./storage/`
- 打包后：与exe同级的`storage/`目录

---

### 5. ble_server.py - BLE数据服务

**通信协议**：
```
肌电手环 (BLE)
      ↓
[数据包解析]
 - 16通道EMG数据（采样率: 200Hz）
 - 9轴IMU数据（采样率: 100Hz）
 - 手势识别结果
      ↓
[信号处理]
 - 带通滤波
 - 降噪
      ↓
WebSocket (ws://localhost:8766)
      ↓
realtimeEngine
```

**关键参数**：
```python
SAMPLE_RATE = 200          # EMG采样率
IMU_SAMPLE_RATE = 100      # IMU采样率
EMG_CHANNELS = 16          # EMG通道数
IMU_AXES = 9               # IMU轴数（3加速度+3陀螺仪+3磁力计）
MAX_DEVICES = 2            # 最多2台设备
```

**数据包格式**：
```json
{
  "device_id": "device_1",
  "timestamp": 1234567890.123,
  "emg": [ch0, ch1, ..., ch15],      // 16个EMG值
  "imu": {
    "acc": [x, y, z],                // 加速度
    "gyro": [x, y, z],               // 陀螺仪
    "mag": [x, y, z]                 // 磁力计
  },
  "gesture": "thumb_up"              // 可选的手势识别
}
```

---

### 6. mocap_server.py - 动捕数据服务

**功能**：
- 连接Nokov SDK服务
- 接收marker点3D坐标
- 进行骨架关节点解算
- 通过WebSocket转发数据

**输出数据格式**：
```json
{
  "timestamp": 1234567890.123,
  "markers": {
    "marker_id": [x, y, z],
    ...
  },
  "skeleton": {
    "joint_name": [x, y, z],
    ...
  }
}
```

---

### 7. storage_server.py - HDF5存储服务

**职责**：
- 接收ZMQ请求中的原始数据 + 标签
- 组织为HDF5文件结构
- 管理文件命名和目录

**HDF5文件结构**：
```
数据文件.h5
├── /emg_data
│   ├── @sampling_rate: 200
│   ├── @channels: 16
│   ├── channel_0 [N,]      # 时间序列数据
│   ├── channel_1 [N,]
│   └── ...
├── /imu_data
│   ├── accelerometer [N, 3]
│   ├── gyroscope [N, 3]
│   └── magnetometer [N, 3]
├── /labels
│   ├── stage_name [N,]
│   ├── prompt [N,]
│   └── timestamps [N,]
├── /mocap_data (可选)
│   ├── markers [N, M, 3]
│   └── skeleton [N, J, 3]
└── /metadata
    ├── collection_date: "2025-11-07"
    ├── subject_id: "subject_001"
    ├── duration: 300.5
    └── notes: "..."
```

---

## 数据流架构

### 完整数据流

```
【硬件层】
肌电手环 ←→ PC (BLE)
MoCap系统 ←→ PC (以太网)


【Python服务层】
ble_server.py              mocap_server.py            mocap_simulator.py
    ↓                          ↓                            ↓
    └──────────────────────────┴───────────────────────────┘
              ↓
        WebSocket服务
        (本地8766/8767)


【Node.js协调层】
server.js (HTTP服务, 端口3000)
    ↓
realtimeEngine.js (WebSocket服务, 端口8080)
    ├─→ 前端实时波形渲染
    │
    ├─→ dataStorage.js
    │       ↓
    │   storage_server.py (ZMQ, 端口5555)
    │       ↓
    │   保存HDF5文件


【前端展示层】
index.html
├── waveform-renderer.js   (EMG/IMU波形实时渲染)
├── collection-controller.js (采集流程控制)
├── discrete-gesture-animation.js (离散手势指导)
├── continual-gesture-*-animation.js (连续手势指导)
└── backend-manager.js (与后端通信)
```

### 采集流程

```
用户点击【开始采集】
    ↓
前端显示采集配置选择界面
（选择姓名、手势类型、会话数等）
    ↓
用户填写并确认配置
    ↓
realtimeEngine 接收配置
    ↓
前端进入采集界面
├─ 左侧：EMG/IMU波形实时显示
└─ 右侧：动作指导动画区域


【采集动作的流程】
前端采集脚本 (discrete-gesture-animation.js等)
    ↓
    ├─ 显示动作指导动画
    ├─ 等待用户准备
    ├─ 开始倒计时
    ├─ 发送 "stage_start" 信号 ──→ realtimeEngine
    ├─ 等待采集时长
    └─ 发送 "stage_end" 信号 ──→ realtimeEngine


realtimeEngine 接收标签信号
    ↓
    ├─ 记录 currentStageName
    ├─ 记录时间戳
    ├─ 继续转发BLE数据到前端和存储服务
    │  （同时附加标签信息）
    └─ 发送给 storage_server.py 进行保存


所有动作完成后
    ↓
用户点击【保存数据】
    ↓
storage_server.py 将缓存数据
写入单个HDF5文件
    ↓
文件保存到 storage/ 目录
    ↓
前端显示保存成功提示
    ↓
返回主界面
```

---

## 前端界面

### 页面结构

#### 1. 初始主界面 (index.html)
```
┌─────────────────────────────────────┐
│     肌电手环数据采集系统 v1.0       │
├─────────────────────────────────────┤
│                                     │
│  【采集】  【查看】  【设置】       │ ← 主菜单按钮
│                                     │
│  设备状态：                          │
│  ├─ BLE: 已连接 (1/2)               │
│  ├─ MoCap: 已连接                   │
│  └─ 存储: 1.2TB / 2.0TB (60%)       │
│                                     │
│  最近文件：                          │
│  ├─ data_20251107_1430.h5 (128 MB)  │
│  └─ data_20251107_1500.h5 (135 MB)  │
│                                     │
└─────────────────────────────────────┘
```

#### 2. 采集配置选择界面
```
┌──────────────────────────────────────┐
│         采集配置选择                 │
├──────────────────────────────────────┤
│                                      │
│ 受试者ID: [____________]             │
│ 采集类型: ○离散手势 ○连续手势1      │
│           ○连续手势2 ○连续手势3     │
│ 会话数:   [    3    ]                │
│ 是否采集MoCap: ○是 ○否              │
│                                      │
│                   [取消]  [开始]     │
│                                      │
└──────────────────────────────────────┘
```

#### 3. 实时采集界面
```
┌────────────────────────────────────────────────────┐
│              实时数据采集 - 离散手势采集             │
├─────────────────────┬───────────────────────────────┤
│                     │                               │
│  【波形显示区】     │   【动作指导区】              │
│                     │                               │
│  EMG波形 (16通道)  │   ╔═══════════════════════╗  │
│  ┌───────────────┐  │   ║  准备：右手向下(V)    ║  │
│  │ ░░░░░░░░░░░░░ │  │   ║                     ║  │
│  │ ░░░░░░░░░░░░░ │  │   ║   10 秒后开始        ║  │
│  │ ░░░░░░░░░░░░░ │  │   ║   [取消]             ║  │
│  │ ░░░░░░░░░░░░░ │  │   ╚═══════════════════════╝  │
│  │ ░░░░░░░░░░░░░ │  │                               │
│  └───────────────┘  │   进度：[█████░░░░░░] 50%    │
│                     │   当前阶段：                  │
│  IMU波形 (9轴)     │   discrete_gesture_1          │
│  ┌───────────────┐  │   会话：2/3                  │
│  │ ▓▓▓▓▓▓▓▓▓▓▓▓▓ │  │                               │
│  │ ▓▓▓▓▓▓▓▓▓▓▓▓▓ │  │                               │
│  │ ▓▓▓▓▓▓▓▓▓▓▓▓▓ │  │                               │
│  └───────────────┘  │                               │
│                     │   [暂停]  [停止采集]          │
└─────────────────────┴───────────────────────────────┘
```

### 核心前端脚本

| 脚本 | 功能 |
|------|------|
| **waveform-renderer.js** | 实时波形渲染引擎，使用Canvas绘制 |
| **collection-controller.js** | 采集流程逻辑控制器 |
| **discrete-gesture-animation.js** | 离散手势动画（4个手势方向） |
| **continual-gesture-1-animation.js** | 连续手势1动画 |
| **continual-gesture-2-animation.js** | 连续手势2动画 |
| **continual-gesture-3-animation.js** | 连续手势3动画 |
| **calibration-guide-animation.js** | 标定指导动画 |
| **backend-manager.js** | 后端API通信管理 |
| **config-manager.js** | 配置文件持久化管理 |

---

## 部署方式

### 开发环境

#### 1. 安装依赖
```bash
# Node.js依赖
npm install

# Python依赖（需要Python 3.8+）
pip install bleak websockets h5py numpy scipy
```

#### 2. 启动开发服务
```bash
# 启动所有服务
npm start

# 或使用热重载
npm run dev
```

访问 `http://localhost:3000`

### 生产部署（Electron打包）

#### 1. 打包应用
```bash
npm run package
```

输出文件：
```
dist/数据采集系统-win32-x64/
├── 数据采集系统.exe          # 主应用
├── resources/
│   ├── ble_server.exe
│   ├── mocap_server.exe
│   ├── storage_server.exe
│   └── ...
└── ...
```

#### 2. 部署到新机器

**前置条件**：
- Windows 10/11
- Python 3.8+ (手动安装)
- Python依赖包
  ```bash
  pip install bleak websockets h5py numpy scipy
  ```

**安装步骤**：
1. 运行 `数据采集系统.exe` 进行安装
2. 应用会自动查找Python并启动各服务
3. 浏览器自动打开应用界面

**目录结构** (exe同级)：
```
C:/Program Files/数据采集系统/
├── 数据采集系统.exe
├── storage/          # 数据文件保存位置（自动创建）
├── config/           # 配置文件（自动创建）
└── logs/             # 日志文件（自动创建）
```

---

## 开发指南

### 快速开发流程

#### 修改后端逻辑 (Node.js)
```bash
# 修改 server.js, deviceSync.js, realtimeEngine.js 等
# 修改后自动热重载
npm run dev
```

#### 修改前端代码
```bash
# 修改 public/scripts/ 下的文件
# 在浏览器中刷新即可看到变化
# 无需重启服务
```

#### 修改Python服务 (ble_server.py等)
```bash
# 需要重启服务才能生效
# 按 Ctrl+C 停止 npm start
# 重新运行 npm start
```

### 常见开发场景

#### 场景1：添加新的采集手势

1. **在 constants.js 中定义手势名称**
```javascript
export const discrete_gesture_prompt_name = Object.freeze([
  'thumb_up',
  'thumb_down',
  'thumb_left',
  'thumb_right',
  'new_gesture',  // ← 新增
  'null'
]);
```

2. **创建对应的动画脚本**
```javascript
// public/scripts/new-gesture-animation.js
// 参考 discrete-gesture-animation.js 的结构
```

3. **在 collection-controller.js 中注册**
```javascript
// 导入新脚本
const newGestureAnimation = require('./new-gesture-animation');

// 在采集流程中调用
```

#### 场景2：修改采样率或通道数

1. **修改 ble_server.py**
```python
SAMPLE_RATE = 250  # 修改采样率
EMG_CHANNELS = 16  # 修改通道数
```

2. **同步修改前端波形渲染参数**
```javascript
// waveform-renderer.js
const SAMPLE_RATE = 250;
const CHANNELS = 16;
```

#### 场景3：添加新的数据可视化

1. **在 waveform-renderer.js 中添加渲染逻辑**
```javascript
class WaveformRenderer {
  render(emgData, imuData, mocapData) {
    // 自定义渲染逻辑
  }
}
```

2. **在 collection-controller.js 中调用**
```javascript
renderer.render(emgData, imuData, mocapData);
```

### 调试技巧

#### 1. 启用BLE模拟器
```python
# ble_server.py
USE_SIMULATOR = True  # 使用模拟数据而不是真实设备
```

#### 2. 启用MoCap模拟器
```javascript
// deviceSync.js
const USE_SIMULATOR = true;  // ← 改为 true
```

#### 3. 查看详细日志
```bash
# 查看服务器日志
tail -f logs/server_*.log

# 实时查看Python输出
# ble_server.py 的所有print输出会显示在控制台
```

#### 4. WebSocket调试
```javascript
// 在浏览器控制台测试WebSocket
const ws = new WebSocket('ws://localhost:8080');
ws.onmessage = (event) => {
  console.log('收到数据:', JSON.parse(event.data));
};
```

---

## 参考资源

### 文档位置

| 资源 | 路径 | 说明 |
|------|------|------|
| **肌电手环参考** | `band/gatts_demo_imu-v3.1-260128.c` | 供应商上位机实现 |
| **BLE数据处理** | `band/wband_app_v3_code_260128/` | 手环应用源码 |
| **动捕SDK示例** | `mocap_sdk/examples/` | Nokov SDK集成示例 |
| **HDF5查看工具** | `tools/hdf5_stage_viewer.py` | 数据文件查看 |

### 关键通信接口

#### BLE WebSocket (端口8766)
```python
# 连接URL
ws://localhost:8766

# 数据格式
{
  "device_id": "device_1",
  "emg": [ch0, ch1, ..., ch15],
  "imu": {"acc": [...], "gyro": [...], "mag": [...]},
  "timestamp": 1234567890.123
}
```

#### MoCap WebSocket (端口8767)
```python
# 连接URL
ws://localhost:8767

# 数据格式
{
  "markers": {"id": [x,y,z], ...},
  "skeleton": {"joint": [x,y,z], ...},
  "timestamp": 1234567890.123
}
```

#### Storage ZMQ (端口5555)
```python
# 请求格式 (JSON)
{
  "command": "save_data",
  "task_id": "task_001",
  "emg_data": [...],
  "imu_data": [...],
  "labels": {...},
  "metadata": {...}
}
```

### 常见问题

**Q1: 启动时报 "找不到Python" 错误**
- A: 确保已安装Python 3.8+，并添加到环境变量PATH中

**Q2: BLE连接失败**
- A: 检查手环是否已打开，尝试重新启动应用，或在deviceSync.js中启用模拟器

**Q3: HDF5文件无法打开**
- A: 使用 `tools/hdf5_tool.py` 查看文件结构，确保h5py库已正确安装

**Q4: 前端无法实时显示波形**
- A: 检查WebSocket连接状态（浏览器F12开发者工具），确保后端服务正常启动

### 扩展开发

#### 添加新的信号处理算法
1. 在 `ble_server.py` 的 `process_emg_data()` 中修改
2. 重新启动BLE服务

#### 集成其他动捕系统
1. 参考 `mocap_server.py` 的架构
2. 实现新的服务器并通过WebSocket输出标准格式数据
3. 在 `deviceSync.js` 中配置新服务

#### 自定义HDF5文件结构
1. 修改 `storage_server.py` 的数据组织逻辑
2. 在 `tools/hdf5_tool.py` 中添加查看代码

---

## 快速命令参考

```bash
# 开发
npm start              # 启动所有服务
npm run dev            # 启动并监听文件变化

# 生产
npm run package        # 打包Electron应用

# 工具
python tools/hdf5_tool.py storage/data.h5          # 查看HDF5文件
python tools/hdf5_stage_viewer.py storage/data.h5  # 可视化查看数据
python mocap_simulator.py                          # 启动动捕模拟器
```

---

## 项目历史与维护

- **最后更新**：2025年3月
- **当前分支**：main_v2
- **稳定版本**：v1.0.0

### 最近改动
- 修复HDF5工具bug和100Hz IMU数据问题
- 解决多项采集逻辑问题
- 优化连续手势3采集（改为10秒）

---

**此文档为项目技术参考指南，如有更新请保持同步。**
