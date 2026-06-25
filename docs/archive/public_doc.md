# public/ 前端架构设计文档

## 1. 概述

`public/` 目录是一个纯静态前端应用，作为 sEMG 数据采集系统的用户界面。它是一个单页 HTML 应用（SPA），通过 WebSocket 与后端服务通信，不依赖任何前端框架（如 React/Vue），仅使用原生 JavaScript + Canvas + tkinter 风格 CSS。

### 1.1 技术栈

| 技术 | 用途 |
|------|------|
| 原生 HTML/CSS/JS | 主体架构 |
| Canvas 2D API | 实时波形渲染 |
| WebSocket | 后端通信（BLE 控制、波形数据、动捕数据） |
| localStorage | 本地持久化（模板、用户、标定缓存、统计） |
| Font Awesome 5 | 图标库 |
| Dygraph | 时序图表（备用，已加载但未使用） |
| lab.js | 实验流程库（备用，已加载但未使用） |

### 1.2 三页架构

应用由三个页面组成，通过 CSS `display`/`hidden` 类切换，无路由系统：

| 页面 | DOM ID | 说明 |
|------|--------|------|
| 欢迎页 | `welcomeScreen` | 初始界面，BLE 设备槽位、状态栏、入口按钮 |
| 采集页 | `collectionScreen` | 核心采集界面，波形显示 + 动画区域 + 控制面板 |
| 后台页 | `backend-page` | 数据统计 + 配置管理（标签页切换） |

---

## 2. 目录结构

```
public/
├── index.html                          # 主页面（~5500行，含内嵌CSS）
├── dygraph.min.css / dygraph.min.js    # Dygraph图表库（备用）
├── emoji-reference.md                  # Emoji参考
├── images/                             # Logo图片
│   ├── hit-logo.png
│   └── seu-logo.png
├── lib/                                # 第三方库
│   ├── fontawesome/                    # Font Awesome图标
│   ├── lab.css / lab.js               # lab.js实验库（备用）
│   └── loading.svg                     # 加载动画
├── scripts/                            # 所有JS模块（详见第3节）
├── tutorial/                           # 教程资源
│   ├── README.md
│   ├── gestures/                       # 手势示范GIF
│   │   ├── discrete/                   # 离散手势GIF（~19个）
│   │   ├── continual_1/               # 连续手势1 GIF
│   │   ├── continual_2/               # 连续手势2 GIF
│   │   └── continual_3/               # 连续手势3 GIF
│   └── video/                          # 教程MP4视频
│       ├── discrete.mp4
│       ├── continual_1.mp4
│       ├── continual_2.mp4
│       └── continual_3.mp4
```

---

## 3. JS 模块架构

### 3.1 加载顺序（依赖关系）

脚本按以下顺序在 `index.html` 中加载，顺序至关重要：

```
第1层：基础设施
  ├── ble_control.js                 # BLE控制（WS:8764）
  ├── waveform.js                    # 波形显示主入口（WS:8080）
  └── waveform-renderer.js           # Canvas渲染引擎

第2层：常量与动画模块（无相互依赖）
  ├── task-config.js                 # 任务定义
  ├── collection-constants.js        # 系统常量和配置
  ├── discrete-gesture-animation.js  # 离散手势滚动动画
  ├── continual-gesture-1-animation.js  # 连续手势1动画
  ├── continual-gesture-2-animation.js  # 连续手势2动画
  ├── animation-input-interface.js   # 动捕数据输入层
  ├── calibration-guide-animation.js # 标定指导动画
  └── animation-controller.js        # 动画总控

第3层：业务逻辑（defer加载，依赖第1-2层）
  ├── collection-selector.js         # 采集选择流程
  ├── page-switch.js                 # 页面切换控制
  ├── config-manager.js              # 配置加载管理
  ├── backend-manager.js             # 后台数据统计
  ├── template-config.js             # 模板编辑器
  └── collection-controller.js       # 采集任务主控
```

### 3.2 模块编码规范

所有模块采用统一的 IIFE 模式：

```javascript
(function() {
    'use strict';
    // ... 类定义 ...
    // 暴露到全局
    window.moduleName = instance;
})();
```

全局命名空间约定：
- 控制器实例：`window.collectionController`、`window.animationController`
- 配置对象：`window.COLLECTION_CONSTANTS`、`window.DISCRETE_GESTURE_CONFIG`
- 工具函数：`window.startRealtime()`、`window.stopWaveform()`

### 3.3 模块详细说明

#### 3.3.1 ble_control.js — BLE 控制模块

- **职责**：与 `ble_server.py` 的控制端口（WS:8764）通信
- **核心对象**：`window.BleControl`（全局 API 对象）
- **内部状态**：`BleState`（闭包私有），包含 2 个设备槽位的连接/采集状态
- **消息协议**：JSON + MessagePack 双模解码（优先 msgpack，降级 JSON）
- **重连策略**：最大 10 次重连，1 秒间隔，非主动关闭才重连
- **心跳**：30 秒间隔发送 `{action: 'status'}`
- **关键方法**：
  - `scan()` — 扫描 BLE 设备
  - `connectDevice(id, mac)` / `disconnectDevice(id)` — 连接/断开
  - `startAll()` / `stopAll()` — 开始/停止全部设备采集
  - `startAll()` 中会自动发送 `set_session_id`（从 sessionIdInput 读取）
  - `setSessionId(id)` — 设置会话 ID

#### 3.3.2 waveform.js — 波形显示主入口

- **职责**：整合渲染器，提供完整波形显示，连接 realtimeEngine WebSocket
- **核心类**：
  - `RealtimeDataReceiver` — WebSocket 客户端（WS:8080），接收 `realtime_data` 和 `realtime_data_batch` 消息
  - `WaveformController` — 主控制器，创建所有渲染器并管理生命周期
- **数据流**：`realtimeEngine.js` → WS:8080 → `renderRealtimeData()` → 各 `WaveformRenderer`
- **渲染器创建**：为每个设备创建 1 个 EMG 渲染器 + 3 个 IMU 渲染器（Acc/Gyr/Mag）
  - 共计 10 个渲染器：emg1, emg2, imu1Acc, imu1Gyr, imu1Mag, imu2Acc, imu2Gyr, imu2Mag
- **帧计数**：维护 `frameCount1` / `frameCount2`

#### 3.3.3 waveform-renderer.js — Canvas 渲染引擎

- **职责**：纯 Canvas 2D 波形渲染，不包含数据生成逻辑
- **核心类**：
  - `WaveformRenderer` — 单个波形窗口渲染器
  - `RendererManager` — 渲染器管理器工厂
- **渲染配置**：5 秒窗口，100Hz 刷新率，EMG 每帧 18 点（加倍），IMU 每帧 1 点
- **关键参数**：
  - EMG：16 通道，16 种颜色
  - IMU：3 轴（X/Y/Z），红/绿/蓝
  - 支持 DPR（devicePixelRatio）高清渲染
  - 支持通道范围选择（1-8 / 9-16）
  - 支持 Offset 调节（EMG 默认 300，IMU 默认 4）
- **渲染方式**：滚动窗口 + 清区域 + 重绘，非全量重绘

#### 3.3.4 collection-constants.js — 系统常量

- **职责**：定义所有采集任务的常量配置
- **全局导出**：
  - `COLLECTION_CONSTANTS` — 通用常量（开场动画、准备倒计时、调试模式）
  - `DISCRETE_GESTURE_CONFIG` — 离散手势配置（Stage 定义 + Prompt 库）
  - `CONTINUAL_GESTURE_1_CONFIG` — 连续手势 1（滚轮光标任务）
  - `CONTINUAL_GESTURE_2_CONFIG` — 连续手势 2（滚轮光标任务）
  - `CONTINUAL_GESTURE_3_CONFIG` — 连续手势 3（滚轮光标任务）
  - `CollectionTiming` — 便捷时间/参数计算工具
  - `TaskConfig` — 任务定义 API（兼容旧 task-config.js）
- **任务类型**：
  - `prompt_sequence` — 离散手势：Stage 内按 Prompt 序列执行
  - `wheel_cursor` — 连续手势：滚轮控制光标命中目标

#### 3.3.5 collection-controller.js — 采集任务主控

- **职责**：管理整个采集生命周期
- **核心状态**：
  - `currentTaskId` — 当前任务类型
  - `sessionCount` / `currentSessionIndex` — 轮次管理（默认 3 轮）
  - `stages` / `currentStageIndex` — Stage 队列管理
  - `gestures` / `currentGestureIndex` — 手势队列管理
  - `_isRunning` / `_isPaused` / `currentPhase` — 运行状态
  - `_isAllSessionsMode` — 全部轮次采集模式
  - `_shuffleMode` — 乱序模式标志
  - `_isTestMode` — 测试模式标志
- **核心流程**：
  1. `startTask(isTestMode)` — 开始采集（分派到离散/连续流程）
  2. 离散手势流程：`prepare → startNextGesture → onGestureAnimationComplete → rest → next`
  3. 连续手势流程：`prepare → startCalibrationFlow → startContinualAnimation`
  4. 乱序模式：Fisher-Yates 洗牌 → 生成实例序列 → 连续执行无休息
  5. 全部轮次模式：`startAllSessions()` → 自动循环所有 Session → 休息倒计时 → 下一轮
- **标定流程**：`startCalibrationFlow() → startCalibration() → endCalibration() → onCalibrationComplete()`
- **WebSocket 通信**：通过 `waveformController.dataReceiver.ws` 发送 `control_command` 消息
- **录像同步**：空格键触发 SYNC 全屏闪烁视觉信号 + 发送 `prompt(name:'space')`

#### 3.3.6 animation-controller.js — 动画总控

- **职责**：管理采集过程中的所有动画播放
- **动画类型**：
  - 开场动画（Intro）：倒计时或视频
  - Stage 动画：委托给具体动画模块（discrete/continual）
  - 准备倒计时：Stage 间切换
- **动画模块映射**：
  ```
  discrete_gesture → discreteGestureAnimation
  continual_gesture_1 → continualGesture1Animation
  continual_gesture_2 → continualGesture2Animation
  ```

#### 3.3.7 animation-input-interface.js — 动捕输入层

- **职责**：接收动捕数据，标定，归一化，提供给动画模块
- **数据流**：`mocap_server.py → realtimeEngine.js → WS:8080 → AnimationInputInterface.onMocapData()`
- **通道映射**：
  - `continual_gesture_1` → `finger_joint_angle_L` / `finger_joint_angle_R`
  - `continual_gesture_2` → `thumb_index_distance_L` / `thumb_index_distance_R`
- **标定**：单阶段标定，去头尾 5% 噪声，计算 min/max，双手独立标定
- **归一化**：`(raw - min) / (max - min)`，clamp 到 [0, 1]，支持指数平滑
- **输入源**：`mocap`（动捕）或 `mouse`（滚轮模拟），可切换
- **持久化**：`localStorage['emg_animation_calibration']` 缓存标定数据
- **独立 WebSocket**：自己连接 `ws://localhost:8080`，发送 `client_identify: 'AnimationInput'`

#### 3.3.8 动画模块（discrete/continual-gesture-X-animation.js）

- **discrete-gesture-animation.js**：Canvas 滚动 Prompt 条动画，Prompt 从右向左滚动经过指示线
- **continual-gesture-1-animation.js**：垂直轨道 + 光标 + 目标区域，滚轮/动捕控制光标移动到目标
- **continual-gesture-2-animation.js**：同上，但通道映射为 `thumb_index_distance`
- **continual-gesture-3-animation.js**：同上，自定义控制模式

每个动画模块遵循统一接口：
- `start(stageConfig, onComplete, onTrial, execParams)` — 启动
- `stop()` — 停止
- `reset()` — 重置试次计数
- `getProgress()` — 获取当前进度 `{trial, total}`
- `isAnimationRunning()` — 是否正在运行

#### 3.3.9 calibration-guide-animation.js — 标定指导

- **职责**：全屏居中显示标定提示，指导用户做标准动作
- **UI 组成**：中央面板（图标+标题+倒计时+进度条+原始值显示）+ 左下角 GIF 示范
- **与 animationInputInterface 联动**：开始标定时调用 `startCalibration()`，结束时调用 `endCalibration()`
- **原始值实时显示**：左手（红色）和右手（蓝色）的原始值

#### 3.3.10 collection-selector.js — 采集选择流程

- **职责**：分步选择采集配置（5 步骤向导）
- **步骤流程**：
  1. 采集任务（离散/连续 1/2/3）
  2. 大类（静态/动态）
  3. 大场景（坐姿/卧姿）
  4. 人群（正常/运动力竭）
  5. 受试者信息（15+ 字段，含 BMI 自动计算）
- **完成动作**：保存配置到 `window.currentCollectionConfig` + localStorage → 启动 BLE → 切换到采集页
- **category3（子场景）跳过原因**：子场景按顺序全部执行，不需要用户选择

#### 3.3.11 page-switch.js — 页面切换

- **职责**：欢迎页 ↔ 采集页 ↔ 后台页 切换，用户信息管理，教程视频弹窗
- **页面切换副作用**：
  - 进入采集页：启动波形显示（`startWaveform()`），通知采集控制器（`onPageShow()`）
  - 离开采集页：停止 BLE 数据流（`BleControl.stopAll()`），停止波形
  - 进入后台页：通知后台管理器刷新数据（`onPageShow()`）

#### 3.3.12 config-manager.js — 配置管理

- **职责**：加载/预览采集模板配置
- **配置来源**：
  - 服务器 `config/` 目录（通过 `/api/config/files` 和 `/api/config/load/<name>`）
  - 本地 JSON 文件导入（FileReader）
  - localStorage（`emg_collection_template`）
  - 内置默认模板
- **模板验证**：检查 `templateName`、`tasks`、`category3` 必填字段

#### 3.3.13 backend-manager.js — 后台统计

- **职责**：后台页面左侧统计面板 + 右侧文件列表
- **数据来源**：`/api/storage/files` HTTP API
- **统计维度**：文件总数、受试者数、任务类型数、数据总量
- **文件解析**：支持新旧两种命名格式（`S001_stage_date_time` 和 `task_S001_date_time`）
- **变化检测**：通过 localStorage 保存上次统计，计算增量，智能检测目录切换
- **页面切换触发**：通过 `MutationObserver` 监听 `backend-page` 的 class 变化

#### 3.3.14 template-config.js — 模板编辑器

- **职责**：后台"配置"标签页的模板编辑器
- **功能**：创建/编辑采集模板，管理分类层级、手势库、受试者字段、执行参数、模板导入/导出

---

## 4. WebSocket 通信架构

系统使用多条 WebSocket 连接，分别用于不同目的：

```
┌─────────────────────────────────────────────────────────────┐
│                        index.html                           │
│                                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐ │
│  │ ble_control │  │  waveform.js │  │ animation-input-   │ │
│  │    .js      │  │              │  │ interface.js       │ │
│  │             │  │              │  │                    │ │
│  │ WS:8764     │  │  WS:8080     │  │  WS:8080 (独立)    │ │
│  └──────┬──────┘  └──────┬───────┘  └─────────┬──────────┘ │
│         │                │                     │            │
│         │ JSON/msgpack   │ JSON                │ JSON       │
└─────────┼────────────────┼─────────────────────┼────────────┘
          │                │                     │
          ▼                ▼                     ▼
   ┌──────────────┐ ┌────────────────┐ ┌──────────────────┐
   │ ble_server   │ │ realtimeEngine │ │ realtimeEngine   │
   │   .py        │ │   .js          │ │   .js            │
   │ 控制端口8764  │ │ 数据端口8080    │ │ 数据端口8080      │
   └──────────────┘ └────────────────┘ └──────────────────┘
```

### 4.1 ble_control.js → ble_server.py (WS:8764)

- **协议**：JSON + MessagePack（BLE 服务器优先 msgpack，客户端自动降级 JSON）
- **消息类型**：
  - 请求：`{action: 'scan'|'connect1'|'start_all'|'stop_all'|'set_session_id'|'status'}`
  - 响应：`{type: 'response', action, success, ...}`
  - 事件：`{type: 'event', event: 'device_connected'|'stream_started'|...}`
  - 欢迎：`{type: 'welcome', dev1: {...}, dev2: {...}}`

### 4.2 waveform.js → realtimeEngine.js (WS:8080)

- **自报身份**：连上后发送 `{type: 'client_identify', clientName: 'Waveform'}`
- **接收消息**：
  - `realtime_data` — 单包实时数据
  - `realtime_data_batch` — 批量实时数据（数组循环渲染）
- **数据包格式**：`{emg1, emg2, imu1, imu2, activeDevices, stats1, stats2, framesInPacket}`

### 4.3 animation-input-interface.js → realtimeEngine.js (WS:8080)

- **自报身份**：`{type: 'client_identify', clientName: 'AnimationInput'}`
- **接收消息**：
  - `mocap_data` — 动捕数据 `{channels: {finger_joint_angle_L: {...}, ...}}`
  - `mocap_connection_status` — 动捕连接状态

### 4.4 collection-controller.js → realtimeEngine.js (WS:8080)

- **复用 waveformController.dataReceiver.ws** 连接
- **发送消息格式**：
  ```json
  {
    "type": "control_command",
    "action": "collection_start|stage_start|prompt|stage_end|collection_stop|...",
    "data": {...},
    "timestamp": <秒时间戳>
  }
  ```

---

## 5. 数据持久化（localStorage）

| Key | 内容 | 写入者 | 读取者 |
|-----|------|--------|--------|
| `emg_collection_template` | 采集模板 JSON | template-config.js, config-manager.js | collection-controller.js, collection-selector.js |
| `emg_current_collection_config` | 当前采集配置 | collection-selector.js | collection-controller.js |
| `emg_current_user` | 当前受试者信息 | collection-selector.js, page-switch.js | collection-controller.js, page-switch.js |
| `emg_user_history` | 受试者历史记录 | page-switch.js | — |
| `emg_animation_calibration` | 标定缓存 | animation-input-interface.js | animation-input-interface.js |
| `emg_backend_last_stats` | 上次后台统计 | backend-manager.js | backend-manager.js |

---

## 6. HTML 页面结构

### 6.1 欢迎页（welcomeScreen）

```
┌──────────────────────────────────────────┐
│ Top Bar: Logo + 时间 + 受试者编号输入      │
├──────────────────────────────────────────┤
│                                          │
│         标题 + 副标题                      │
│                                          │
│     [开始采集]    [后台]                   │
│     (大按钮)     (大按钮)                  │
│                                          │
├──────────────────────────────────────────┤
│ Bottom Bar: BLE设备槽位1 + 槽位2 + 状态    │
│ [扫描] [选择设备▼] [连接] [信号条] [状态]  │
│ [扫描] [选择设备▼] [连接] [信号条] [状态]  │
│ 服务状态 | 设备1 RSSI | 设备2 RSSI | 时间  │
└──────────────────────────────────────────┘
```

### 6.2 采集页（collectionScreen）

```
┌────波形区域（上部）────────────────────────────┐
│ 设备1: [EMG波形] [IMU Acc] [IMU Gyr] [IMU Mag]│
│ 设备2: [EMG波形] [IMU Acc] [IMU Gyr] [IMU Mag]│
│ 底部栏: 帧计数 | 丢包率                         │
├────动画区域（下部）────────────────────────────┤
│ ┌─左侧手势列表─┐  ┌───中央动画显示区────────┐  │
│ │ 轮次: 1/3   │  │   gestureDisplay       │  │
│ │ Stage: 2/4  │  │   - gestureName        │  │
│ │ 手势: 3/8   │  │   - gestureInstruction │  │
│ │             │  │   - gestureIcon         │  │
│ │ [手势列表]  │  │   - countdown           │  │
│ │             │  │   - Canvas动画           │  │
│ └─────────────┘  └────────────────────────┘  │
│                                               │
│ [全部轮次] [开始采集] [测试模式] [停止] [教程] │
│ 轮次选择▼ Stage选择▼           [下一Stage]    │
└───────────────────────────────────────────────┘
│ 左下角: GIF手势示范 (gestureGifContainer)      │
│ 顶部: 返回按钮 | 任务名称 | 分类标签            │
│ 状态栏: 进度条 + 状态指示                       │
└───────────────────────────────────────────────┘
```

### 6.3 后台页（backend-page）

- **标签页**：统计 / 配置
- **统计标签**：左栏（统计卡片 + 按任务/受试者分类）+ 右栏（文件列表分页）
- **配置标签**：模板编辑器（由 template-config.js 渲染）

---

## 7. 关键技术细节

### 7.1 Canvas 渲染优化

- **DPR 感知**：Canvas 物理尺寸 = CSS 尺寸 × devicePixelRatio，避免模糊
- **增量清除**：只清除写入位置前方的小区域，不平铺重绘
- **ResizeObserver**：容器尺寸变化时自动重设 Canvas

### 7.2 时间戳约定

- 所有发送到 realtimeEngine 的时间戳使用**秒**（`Date.now() / 1000`），与 ble_server 保持一致

### 7.3 WebSocket 重连策略

- 所有模块统一：最大 10 次重连，非主动关闭（code !== 1000）才重连
- 重连间隔：ble_control 1 秒，waveform 3 秒
- 页面关闭前主动发送 `ws.close(1000)` 防止触发重连

### 7.4 手势示范 GIF 机制

- 离散手势：每个手势对象携带 `gifFile` 字段，采集时显示在左下角
- 连续手势：每个任务类型一个 GIF，从 `collectionConfig.gestures.continual_X[0]` 获取
- GIF 路径：`tutorial/gestures/{discrete|continual_1|continual_2}/{gifFile}`

### 7.5 录制同步机制

- 采集开始时生成 `recordingSessionId`（格式 `rec_YYYYMMDD_HHMMSS_N`）
- 空格键触发全屏视觉信号（白色闪烁 + "SYNC" 大字）+ 发送 `prompt(name:'space')`
- 全部轮次模式共享一个 recordingSessionId

### 7.6 乱序模式（Shuffle Mode）

- 由 Stage 配置的 `shuffleGestures: true` 启用（仅离散手势采集）
- **部分顺序 + 部分乱序**：顺序段占比由执行参数 `orderedShuffleRatio` 控制（默认 0.6），剩余进入 Fisher-Yates 打乱
- 例如 10 个手势每个 100 次、ratio=0.6：顺序段每个手势 60 次按序执行，乱序段每个手势 40 次共 400 个实例打乱执行
- 每个实例执行 1 次（repeatPerGesture 用于生成实例数量）
- 无手势间休息，缩短间隔时间
- 隐藏手势列表显示
- 采集窗口进度条显示"顺序"/"乱序"阶段标签并变色（蓝绿/红）
- `repeatCount=1` 时 `orderedRepeat=0`，全部进入乱序段
- `orderedShuffleRatio` 可在后台 执行参数 → 离散手势 中配置（0~1，step=0.05）

---

## 8. API 端点依赖

前端依赖以下 HTTP API（由 `storage_server.py` 或类似后端提供）：

| 端点 | 方法 | 用途 | 调用者 |
|------|------|------|--------|
| `/api/storage/files` | GET | 获取 storage 目录文件列表 | backend-manager.js |
| `/api/config/files` | GET | 获取 config 目录文件列表 | config-manager.js |
| `/api/config/load/<name>` | GET | 加载指定配置文件 | config-manager.js |
| `/api/config/delete/<name>` | DELETE | 删除配置文件 | config-manager.js |

---

## 9. 扩展指南

### 9.1 添加新任务类型

1. 在 `collection-constants.js` 中添加 `XXX_CONFIG`（Stage 定义 + 参数）
2. 在 `TASK_DEFINITIONS` 中注册
3. 在 `TASK_ID_MAP` 中添加 HTML data-task 到内部 ID 的映射
4. 创建对应的动画模块（如 `xxx-animation.js`）
5. 在 `animation-controller.js` 的 `animationModules` 中注册
6. 在 `index.html` 中按正确顺序引入脚本

### 9.2 添加新动画模块

动画模块必须实现以下接口：
```javascript
{
    start(stageConfig, onComplete, onTrial, execParams),
    stop(),
    reset(),
    getProgress(),  // 返回 {trial, total}
    isAnimationRunning()
}
```

### 9.3 添加新分类层级

1. 在 `template-config.js` 的 `DEFAULT_TEMPLATE` 中添加新分类数组
2. 在 `collection-selector.js` 的 `STEPS` 中添加选择步骤
3. 在 `collection-controller.js` 中处理新分类数据
