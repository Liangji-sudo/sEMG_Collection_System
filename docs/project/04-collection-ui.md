# 04 - Web 采集界面

## 1. 概述

Web 前端是系统的**用户交互层**，运行在浏览器中，通过 WebSocket 与 Node.js/Python 后端通信。核心是一个单页应用 (SPA)，包含采集引导、设备监控、后台管理三大区域。

| 模块 | 文件 | 行数 | 职责 |
|------|------|------|------|
| **index.html** | `public/index.html` | 6300+ | 完整 HTML/CSS 布局 (任务面板/动画区/设备区/后台页) |
| **collection-controller** | `scripts/collection-controller.js` | 3760+ | 采集流程核心控制 — 状态机/手势推进/断点续采 |
| **collection-selector** | `scripts/collection-selector.js` | — | 采集任务选择 UI (任务类型/Stage/手势模板) |
| **config-manager** | `scripts/config-manager.js` | — | 采集配置管理 (受试者/分类/Stage 参数) |
| **page-switch** | `scripts/page-switch.js` | — | 页面切换 + 用户信息 + WebSocket 连接管理 |
| **backend-manager** | `scripts/backend-manager.js` | — | 后台数据统计 (文件列表/受试者/分类) |

---

## 2. 页面布局 (index.html)

```
┌─────────────────────────────────────────────────────────┐
│  顶部状态栏 (连接状态 / 磁盘空间 / 设备电量)              │
├──────────────────────────┬──────────────────────────────┤
│                          │                              │
│   任务引导面板             │   设备监控面板                 │
│  ┌──────────────────┐    │  ┌──────────────────────┐    │
│  │ 任务名称 + 配置标签│    │  │ 信号质量指示器         │    │
│  │ 轮次/Stage切换    │    │  │ 16通道EMG包络线        │    │
│  ├──────────────────┤    │  │ 摄像头预览 (左右)       │    │
│  │ 动画显示区        │    │  └──────────────────────┘    │
│  │ (labjs 动画)     │    │                              │
│  ├──────────────────┤    │                              │
│  │ 状态指示器        │    │                              │
│  │ 手势进度列表      │    │                              │
│  │ 手势显示区        │    │                              │
│  │ 控制按钮          │    │                              │
│  │ 底部进度条        │    │                              │
│  │ GIF手势示范       │    │                              │
│  └──────────────────┘    │                              │
├──────────────────────────┴──────────────────────────────┤
│  后台页面 (数据统计 / 文件列表)                            │
└─────────────────────────────────────────────────────────┘
```

---

## 3. CollectionController — 采集流程核心

**文件**: `collection-controller.js` (3760+ 行)

### 3.1 类结构

```javascript
class CollectionController {
    // === 配置 ===
    collectionConfig: null          // 采集配置对象
    currentTaskId: 'discrete_gesture'  // 任务类型
    stages: []                      // Stage 列表
    gestures: []                    // 当前手势库
    currentExecutionParams: {...}   // 执行参数 (重复次数/休息时间等)

    // === 进度状态 ===
    currentSessionIndex: 0          // 当前轮次 (0-based)
    currentStageIndex: 0            // 当前 Stage
    currentGestureIndex: 0          // 当前手势索引
    gestureRepeatCount: 0           // 当前手势重复次数
    continualTrialCount: 0          // 连续手势试次计数

    // === 运行标志 ===
    _isRunning: false               // 是否正在采集
    _isPaused: false                // 是否暂停
    _isAllSessionsMode: false       // 全部轮次模式
    _isTestMode: false              // 测试模式
    _isResumeMode: false            // 断点续采模式
    _shuffleMode: false             // 乱序模式

    // === 同步 ===
    _sessionSyncDone: false         // 当前 session 同步是否完成
    _syncPhaseActive: false         // 同步阶段进行中
    _syncRemainingGestures: []      // 同步后的手势库

    // === 录像 ===
    _recordingSessionId: null       // 录像会话 ID
}
```

### 3.2 采集状态机

```
                    ┌──────────┐
                    │  IDLE    │ 初始/完成状态
                    └────┬─────┘
                         │ startCollection()
                         ▼
                    ┌──────────┐
                    │  SYNC    │ 采集第一个 Stage 前 → 精准对齐同步 prompt
                    │  PREPARE │ (仅 discrete_gesture 任务)
                    └────┬─────┘
                         │ _onSyncPhaseComplete()
                         ▼
                    ┌──────────┐
                    │ PREPARE  │ 准备倒计时 (3s)
                    └────┬─────┘
                         │
                    ┌────▼─────┐
              ┌─────│ GESTURE  │ 手势执行
              │     └────┬─────┘
              │          │ onGestureAnimationComplete()
              │          ▼
              │     ┌──────────────┐
              │     │ gestureIndex │
              │     │ < length?    │
              │     └──┬───────┬───┘
              │      Y│       │N
              │       │       └──► onAllGesturesComplete()
              │       ▼                │
              │  ┌──────────┐          ▼
              └──│ INTERVAL │    ┌──────────────┐
                 │ (休息)    │    │ NEXT STAGE?  │
                 └──────────┘    │ 或 SESSION    │
                                 │ 完成          │
                                 └──────────────┘
```

### 3.3 任务类型与执行参数

| 任务 ID | 类型 | 执行参数 | 动画引擎 |
|---------|------|---------|---------|
| `discrete_gesture` | 离散手势 | `repeatPerGesture: 5`, `restBetweenGestures: 30s` | `discreteGestureAnimation` |
| `continual_gesture_1` | 连续手势1 (食指关节角度) | `trialsPerStage: 10`, `dwellTime: 0.5s` | `continualGesture1Animation` |
| `continual_gesture_2` | 连续手势2 (拇指食指距离) | `trialsPerStage: 10`, `dwellTime: 0.5s` | `continualGesture2Animation` |

### 3.4 乱序模式 (Shuffle Mode)

离散手势采集支持乱序模式：
- 手势顺序阶段执行，乱序阶段每个手势实例仅执行 **1 次**
- 使用 `startShuffleModeAnimation(opts)` (line 2203) 代替 `startNextGesture()`
- Prompt 通过 `onShufflePromptTriggered()` 回调发送

### 3.5 全部轮次模式

```javascript
startAllSessions() {
    // 循环采集所有轮次:
    for (let s = 0; s < sessionCount; s++) {
        采集一个完整 session (所有 stages)
        → 轮次间休息 _restBetweenSessions 秒
        → 自动进入下一轮次
    }
}
```

### 3.6 断点续采完整流程

```
1. 异常中断触发
   ├─ _abortFreeze()     → 冻结 UI, 保存断点快照到 localStorage
   └─ _executeAbort()    → 发送 abnormal_interrupt 到 realtimeEngine
                           → H5 标记 abnormal_interrupted
                           → 返回首页 (显示续采按钮)

2. 用户点击"断点续采"
   ├─ page-switch.resumeBreakpoint()
   ├─ popup 确认 (显示 12/50 等进度)
   └─ collection-controller.loadBreakpointState(state)
       ├─ 恢复 collectionConfig / stages / gestures (快照)
       ├─ 恢复 currentGestureIndex / gestureRepeatCount
       └─ 设置 _isResumeMode = true

3. 用户点击"开始续采"
   └─ startCollection()
       ├─ 同步 prompt (每个 H5 开头必须)
       ├─ _resumeGestureStartIndex 保存断点索引
       └─ 同步完成后恢复手势索引 → 从断点继续
```

### 3.7 关键方法索引

| 方法 | 行 | 说明 |
|------|-----|------|
| `startCollection()` | ~1080 | 主入口，分发到不同任务类型 |
| `startDiscreteGestureCollection()` | ~1955 | 离散手势采集入口 |
| `startNextGesture()` | ~2345 | 非乱序模式的逐手势推进 |
| `startShuffleModeAnimation()` | ~2203 | 乱序模式动画启动 |
| `onShufflePromptTriggered()` | ~2263 | 乱序 prompt 回调 |
| `onGestureAnimationComplete()` | ~2394 | 单手势完成 → 索引+1 |
| `onAllGesturesComplete()` | ~2436 | 当前 Stage 所有手势完成 |
| `onStageComplete()` | ~2497 | Stage 完成 → 下一 Stage 或 Session |
| `loadBreakpointState()` | ~3562 | 从快照恢复断点状态 |
| `showPreparation()` | ~2302 | 准备倒计时 |
| `updateProgress()` | ~3100 | 更新底部进度条 |
| `updateGestureList()` | ~578 | 更新左侧手势进度列表 |
| `updateControlButtons()` | ~2952 | 控制按钮启用/禁用 |

---

## 4. 前端通信架构

### 4.1 WebSocket 连接

```
前端浏览器
├─ WS :8080 → realtimeEngine       # 主数据通道 (波形/控制命令)
├─ WS :8764 → ble_server (控制端)   # BLE 设备扫描/连接/配置
└─ WS :8768 → camera_server         # 摄像头 MJPEG 预览帧
```

### 4.2 消息类型

**前端 → realtimeEngine (:8080)**:
```javascript
{ type: 'control_command', action: 'collection_start', data: {...} }
{ type: 'control_command', action: 'prompt', data: {name, stageName, timestamp} }
{ type: 'client_identify', clientName: 'Waveform' }
```

**realtimeEngine → 前端 (:8080)**:
```javascript
{ type: 'realtime_data_batch', batch: [...] }       // EMG/IMU 批量数据
{ type: 'mocap_data', data: {...} }                  // 动捕数据
{ type: 'ble_connection_status', connected: true }   // BLE 状态
{ type: 'camera_connection_status', connected: true } // 摄像头状态
```

### 4.3 按钮命令路由

前端按钮点击 → `POST /button-click` → `realtimeEngine.taskManager_get_command(buttonName)`

---

## 5. PageSwitchController — 页面生命周期

**文件**: `page-switch.js`

管理三页面切换：
- `showWelcome()` — 首页 (设备连接 + 开始采集)
- `showCollection()` — 采集页 (建立 WS 连接，启动波形)
- `showBackend()` — 后台页 (数据统计)

关键入口：
- `resumeBreakpoint()` — 从 localStorage 读取断点快照 → 弹出确认 → 调用 `collectionController.loadBreakpointState()`
- `showUserModal()` — 用户信息录入 (ID/姓名/年龄等)

---

## 6. BackendManager — 后台数据管理

**文件**: `backend-manager.js`

从 `/api/storage/files` 加载 H5 文件列表，提供：
- 左侧统计栏：总文件数、受试者数、任务分类、变化气泡
- 右侧文件列表：支持文件名/大小/时间排序
- 断点恢复扫描：自动检测 `abnormal_interrupted` 状态的文件
