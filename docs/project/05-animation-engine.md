# 05 - 动画引擎

## 1. 概述

动画引擎负责采集引导区域的可视化呈现，使用 **Canvas + labjs 库** 实现手势提示动画。根据任务类型分为离散手势和连续手势两套系统。

| 模块 | 文件 | 职责 |
|------|------|------|
| **discrete-gesture-animation** | `discrete-gesture-animation.js` | 离散手势 Canvas 动画 (滚动 prompt 经过指示线) |
| **continual-gesture-1-animation** | `continual-gesture-1-animation.js` | 连续手势1 — 食指关节角度 (0-90°) 驱动同心圆光标 |
| **continual-gesture-2-animation** | `continual-gesture-2-animation.js` | 连续手势2 — 拇指食指距离驱动同心圆光标 |
| **continual-gesture-3-animation** | `continual-gesture-3-animation.js` | 连续手势3 (备选) |
| **animation-controller** | `animation-controller.js` | 动画容器管理 + 倒计时/timer 显示 |
| **animation-input-interface** | `animation-input-interface.js` | 动捕数据输入接口 — 接收 mocap_data → 转发给动画模块 |
| **animation-position-manager** | `animation-position-manager.js` | 动画面板拖拽/缩放/复位管理 |
| **calibration-guide-animation** | `calibration-guide-animation.js` | 标定引导动画 |

---

## 2. DiscreteGestureAnimationController

**文件**: `discrete-gesture-animation.js` (1400+ 行)

### 2.1 动画原理

Canvas 画布上创建 prompt 对象，从右向左匀速滚动，经过固定指示线时触发 `onPromptTriggered` 回调：

```
┌─────────────────────────────────────────┐
│                                         │
│    ✊ 握拳    ✋ 张开   🖖 剪刀            │  ← prompt 对象
│         ←── 滚动方向 (scrollSpeed px/帧)   │
│                   │                      │
│              ┌────┴────┐                 │
│              │ 指示线   │                 │  ← 固定位置
│              └─────────┘                 │
└─────────────────────────────────────────┘
```

### 2.2 核心方法

| 方法 | 说明 |
|------|------|
| `init(containerSelector)` | 初始化 Canvas，挂载到指定容器 |
| `startGesture(gesture, executionParams, onComplete)` | **正常模式**: 单个手势重复 `repeatPerGesture` 次 |
| `startShuffleMode(gestures, executionParams, stageConfig, onComplete, onPromptTriggered, onUpcomingGesture, startIndex)` | **乱序模式**: 所有手势连续滚动 |
| `stop()` | 停止动画，取消 animationFrame |

### 2.3 执行参数

```javascript
executionParams = {
    repeatPerGesture: 5,       // 每个手势重复次数 (正常模式)
    intervalBetweenRepeat: 1.0, // 手势间隔时间 (秒) → 计算 promptSpacing
    scrollSpeed: 2,            // 滚动速度 (px/帧)
    sustainedDuration: 2.0,    // 持续手势保持时间 (秒)
    shuffleInterval: 1.0,      // 乱序手势间隔 (秒)
    shuffleIntervalMin/Max,    // 乱序随机间隔范围 (秒)
    gestureLabelFontSize: 18,  // 手势标签字号 (px)
}
```

核心计算: **手势间距 = scrollSpeed × 间隔时间 × 60fps**

### 2.4 乱序模式流程

```
startShuffleMode(gestures, ..., startIndex)
  ├─ 从 startIndex 开始创建 prompt 队列
  │   跳过前 startIndex 个手势
  ├─ 每个 prompt 经过指示线:
  │   ├─ onPromptTriggered(name, index, stageName, promptType)
  │   │   └─ collectionController.onShufflePromptTriggered()
  │   │       └─ 发送 prompt 到后端 + 更新进度条
  │   └─ onUpcomingGesture(nextGesture)
  │       └─ 更新左下角 GIF 示范
  └─ 所有 prompt 完成 → onComplete()
```

### 2.5 手势类型

| gestureType | 行为 |
|-------------|------|
| `instant` | 瞬间手势 — 经过指示线立即触发 |
| `sustained` | 持续手势 — 经过指示线后保持 `sustainedDuration` 秒 |

---

## 3. 连续手势动画

**文件**: `continual-gesture-1-animation.js`, `continual-gesture-2-animation.js`

### 3.1 动画原理

使用**同心圆光标**方案：动捕数据解算出手指状态（角度或距离），映射为光标在同心圆上的位置。受试者需跟随光标的运动。

```
      ┌─────────────────┐
      │    ╭──────╮      │
      │   ╱   ◎    ╲    │  ← 目标圆 (target)
      │  │    ╱ ╲    │   │
      │  │   │光标│   │   │  ← 受试者需将光标保持在目标圆内
      │  │    ╲ ╱    │   │     dwellTime 秒
      │   ╲        ╱    │
      │    ╰──────╯      │
      └─────────────────┘
```

### 3.2 动捕 → 光标映射

| 通道 | 计算 | 输入 | 输出 |
|------|------|------|------|
| `finger_joint_angle` | 食指 P3→手背连线 与 手背法向量的夹角 | mocap marker 3D 坐标 | 0-90° → 光标位置 |
| `thumb_index_distance` | TH1 与 IN1 欧氏距离 | mocap marker 3D 坐标 | mm → 光标位置 |

### 3.3 执行参数

```javascript
executionParams = {
    trialsPerStage: 10,        // 每 Stage 试次数
    stageTimeout: 120,         // Stage 超时 (秒)
    dwellTime: 0.5,            // 光标驻留时间 (秒)
    targetSize: 0.12,          // 目标圆大小 (相对值)
    preparationTime: 3.0,      // 准备时间 (秒)
}
```

---

## 4. AnimationController

**文件**: `animation-controller.js`

轻量级控制器，管理动画的启动/停止/倒计时：

| 方法 | 说明 |
|------|------|
| `playCountdown(seconds, onComplete)` | 播放准备倒计时 |
| `updateStageTimer(remainingSeconds)` | 更新 Stage 计时器 |
| `updateProgressRing(remaining, total)` | 更新进度环 |

---

## 5. AnimationInputInterface

**文件**: `animation-input-interface.js`

动捕数据输入接口，连接 realtimeEngine 的 mocap_data 消息到动画模块：

```javascript
// 接收 realtimeEngine 转发的 mocap_data
// 解算 finger_joint_angle / thumb_index_distance
// 转发给 continualGestureAnimation.setInput(value)
```

---

## 6. AnimationPositionManager

**文件**: `animation-position-manager.js`

支持用户拖拽动画面板：
- 拖拽手柄移动面板位置
- 双击复位到中心
- 窗口大小变化时自适应居中
