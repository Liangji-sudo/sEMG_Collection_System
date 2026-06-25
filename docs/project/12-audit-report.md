# 12 — 系统全面审计报告

**审计日期**: 2026-06-25  
**审计范围**: 全部源码（~60 文件，~20000 行）  
**审计层次**: 架构设计 + 逐文件 Bug 审查  
**状态**: 仅汇总，等评审确认后统一修复

---

## 一、审计统计概览

| 子系统 | 严重 | 高 | 中 | 低 | 设计层 | 合计 |
|--------|------|-----|-----|-----|--------|------|
| 架构 & 数据流 | 2 | 3 | 7 | 3 | — | 15 |
| Python 服务层 | 3 | — | 13 | 8 | 2 | 26 |
| Node.js 中间件 | 3 | 6 | 9 | 12 | 6 | 36 |
| 前端 UI 层 | 13 | — | 26 | 18 | 5 | 62 |
| Python GUI 工具 | 6 | 10 | 12 | 8 | 5 | 41 |
| **合计** | **27** | **19** | **67** | **49** | **18** | **180** |

---

## 二、严重问题清单（27 项，阻塞性 — 必须修复）

### 2.1 Node.js 中间件（3 项）

**CRITICAL-N1** — ZMQ 连接必然失败（启动时序错误）
- 位置: [realtimeEngine.js:196](realtimeEngine.js#L196) + [server.js:520-532](server.js#L520-L532)
- 问题: `realtimeEngine.start()` 在 line 196 调用 `storage_server_connect()`，但 `dataStorage.initialize()` 在 line 532 才启动 Python storage_server。ZMQ connect 无对端，所有 H5 写入失败。
- 修复: 调整启动顺序，`dataStorage.initialize()` 移到 `realtimeEngine.start()` 之前；或为 ZMQ 连接添加重试机制。

**CRITICAL-N2** — `deviceSync.js` 中 `this.realtimeEngine` 导致 TypeError
- 位置: [deviceSync.js:360](deviceSync.js#L360), [deviceSync.js:402](deviceSync.js#L402)
- 问题: `this.realtimeEngine.sendCameraCommand(...)` 使用了不存在的实例属性。`realtimeEngine` 是模块级导入，应直接使用而非 `this.`。
- 修复: 将 `this.realtimeEngine` 替换为 `realtimeEngine`（已导入的模块引用）。

**CRITICAL-N3** — Electron 开发模式下 Python 子进程从不清理
- 位置: [main.js:8](main.js#L8), [main.js:101-109](main.js#L101-L109)
- 问题: `PYTHON_PROCESSES` 硬编码 `.exe` 后缀名，开发模式进程名为 `python.exe`，`taskkill` 找不到。`camera_server` 也未在清理列表中。
- 修复: 使用 PID 追踪子进程；或在 `before-quit` 中通过 IPC 通知 server.js 优雅关闭。

### 2.2 Python 服务层（3 项）

**CRITICAL-P1** — `camera_server.py` 信号处理器立即 `sys.exit(0)` 导致资源泄漏
- 位置: [camera_server.py:1297-1301](camera_server.py#L1297-L1301)
- 问题: `signal_handler` 创建异步清理任务后立即 `sys.exit(0)`，cleanup 协程得不到执行，ffmpeg 进程不被终止，MJPEG 临时文件不被清理。
- 修复: 不要在信号处理器中调用 `sys.exit(0)`。用 asyncio 原生信号处理触发清理后正常返回。

**CRITICAL-P2** — `bin_sync_tool.py` GUI 中 `QSpinBox` 未导入
- 位置: [bin_sync_tool.py:2386](tools/bin_sync_tool.py#L2386)
- 问题: `from PyQt5.QtWidgets import (...)` 中没有导入 `QSpinBox`，但第 2386 行 `self.num_imus_spin = QSpinBox()` 使用它。GUI 模式启动即崩溃。
- 修复: 在导入列表中添加 `QSpinBox`。

**CRITICAL-P3** — `ble_server.py` 死代码 `return` 阻断了 notification handler
- 位置: [ble_server.py:812](ble_server.py#L812)
- 问题: `_legacy_create_notification_handler` 第 812 行 `return` 后 813-841 行永远不会执行。需确认是废弃代码还是隐藏回归 bug。
- 修复: 如果是废弃代码，删除整个 `_legacy_create_notification_handler`；如果是 bug，移除 `return`。

### 2.3 前端 UI 层（13 项）

**CRITICAL-F1** — `collection-controller.js` JSON.parse 无 try/catch
- 位置: [collection-controller.js:232](public/scripts/collection-controller.js#L232)
- 问题: 若 localStorage 数据被意外损坏，`JSON.parse` 抛出未捕获 SyntaxError，构造函数中断，整个采集控制器不可用。
- 修复: 用 try/catch 包裹，失败时降级到 `loadDefaultConfig()`。

**CRITICAL-F2** — 空格键事件监听器绑定两次
- 位置: [collection-controller.js:3808](public/scripts/collection-controller.js#L3808)
- 问题: `_initSpaceKeyListener()` 在 `init()` (line 116) 和 `bindEvents()` (line 221) 各调用一次。每次空格按下发送两条 prompt、两次 toast、两次视觉闪烁。
- 修复: 删除 `bindEvents()` 中的重复调用。

**CRITICAL-F3** — 续采模式下 `loadCollectionConfig()` 覆盖断点状态
- 位置: [collection-controller.js:477](public/scripts/collection-controller.js#L477)
- 问题: `onPageShow()` 无条件调用 `loadCollectionConfig()` 覆盖掉 `loadBreakpointState()` 中恢复的手势、Session/Stage 索引等状态。第 466-470 行还把 progress 索引全部重置为 0。
- 修复: `onPageShow()` 中检测 `this._isResumeMode`，仅在非续采模式下重置进度。

**CRITICAL-F4** — `collection_stop` 发送位置不一致
- 位置: [collection-controller.js:2560](public/scripts/collection-controller.js#L2560)
- 问题: 离散手势在所有手势完成后发送 `collection_stop`，但连续手势仅在最后一个 Stage 完成后发送。中间 Stage 结束后不发送该消息。
- 修复: 在 `stopTask()` 中也补充发送 `collection_stop`。

**CRITICAL-F5** — 标定完成后的 setTimeout 无引用、无法取消
- 位置: [collection-controller.js:1878](public/scripts/collection-controller.js#L1878)
- 问题: `onCalibrationComplete()` 中 `setTimeout(..., 1500)` 没有存储引用。如果用户 1.5 秒内点击停止，定时器仍触发，与停止逻辑冲突。
- 修复: 存入 `this._calibrationDelayTimer`，在 `stopTask()`/`abortTask()` 中清除。

**CRITICAL-F6** — Stream 切换失败后无回滚
- 位置: [collection-controller.js:1066-1092](public/scripts/collection-controller.js#L1066-L1092)
- 问题: `set_session_id` 成功后若 `switch_preview_to_collection` 失败，设备端 session_id 已被修改但前端不知道。
- 修复: 失败时尝试回滚 session_id，或至少记录警告。

**CRITICAL-F7** — 离散手势动画 promptLibrary 清空与覆盖冲突
- 位置: [discrete-gesture-animation.js:432](public/scripts/discrete-gesture-animation.js#L432)
- 问题: `startShuffleMode()` line 1168 `this.promptLibrary = {}` 清空整个 promptLibrary。与 `startGesture()` 的动态添加冲突。
- 修复: `startShuffleMode` 不应清空 `promptLibrary`，使用局部变量管理乱序序列。

**CRITICAL-F8** — 每帧创建临时 Canvas 对象
- 位置: [discrete-gesture-animation.js:917-928](public/scripts/discrete-gesture-animation.js#L917-L928)
- 问题: `drawEmojiIcon` 对每个 emoji 每帧 `document.createElement('canvas')`。10 个 prompt × 60fps = 每秒 600 个临时 canvas。
- 修复: 将临时 canvas 缓存为实例属性，仅在尺寸变化时重建。

**CRITICAL-F9** — backend-manager XSS 注入 via 文件名
- 位置: [backend-manager.js:761](public/scripts/backend-manager.js#L761)
- 问题: `<div class="file-name">${file.name}</div>` 文件名字符串直接插入 innerHTML。
- 修复: 使用 `textContent` 或 DOMPurify 转义。

**CRITICAL-F10** — backend-manager renderErrorState 中 XSS via error 消息
- 位置: [backend-manager.js:456](public/scripts/backend-manager.js#L456)
- 问题: `<p class="text-sm">${error}</p>` 错误消息直接插入 innerHTML。
- 修复: 对所有外部输入的字符串使用 textContent 赋值。

**CRITICAL-F11** — waveform.js IMU 数据双重渲染
- 位置: [waveform.js:199-212](public/scripts/waveform.js#L199-L212)
- 问题: IMU1 数据被渲染两次——一次错误格式、一次正确格式。每帧额外渲染开销和潜在视觉抖动。IMU2 同样问题 (line 247-258)。
- 修复: 删除多余的 `imuXChips` 渲染逻辑。

**CRITICAL-F12** — ble_control 重连竞态条件
- 位置: [ble_control.js:135-153](public/scripts/ble_control.js#L135-L153)
- 问题: `scheduleReconnect()` 设置 `BleState.reconnecting = true` 后 1 秒内外部代码调用 `connect()` 会被静默忽略。
- 修复: 在 `connect()` 中清除 pending 的 reconnect timer，然后继续连接。

**CRITICAL-F13** — ble_control disconnect() 不重置 reconnectAttempts
- 位置: [ble_control.js:67](public/scripts/ble_control.js#L67)
- 问题: `disconnect()` 未将 `BleState.reconnectAttempts` 重置为 0，下次 connect() 带着旧的计数。
- 修复: 在 `disconnect()` 中添加 `BleState.reconnectAttempts = 0;`。

### 2.4 Python GUI 工具（6 项）

**CRITICAL-G1** — hdf5_tool 主线程 sleep 阻塞
- 位置: [hdf5_tool.py:3012](tools/hdf5_tool.py#L3012)
- 问题: `_save_calibration()` 中 `time.sleep(0.3)` 运行在主线程，冻结整个 GUI 300ms。
- 修复: 用 `QTimer.singleShot(300, callback)` 异步重试。

**CRITICAL-G2** — hdf5_tool SyncCalibrationTab 数据丢失风险
- 位置: [hdf5_tool.py:2999-3037](tools/hdf5_tool.py#L2999-L3037)
- 问题: 先清空视频时序字典再尝试打开 H5 文件，打开失败则数据永久丢失。
- 修复: 先完成所有数据拷贝，再清理和重新打开文件。

**CRITICAL-G3** — hdf5_tool QThread 孤儿风险（多个 Tab 均有）
- 位置: [hdf5_tool.py:3649-3656](tools/hdf5_tool.py#L3649-L3656), [hdf5_tool.py:4061](tools/hdf5_tool.py#L4061), [hdf5_tool.py:4346](tools/hdf5_tool.py#L4346)
- 问题: SyncTab、OneToManySyncTab、SyncToolsTab 创建新 worker 前未检查上一个是否仍在运行。两个线程竞争写入同一个 H5 导致数据损坏。
- 修复: 启动前检查并等待旧 worker 退出。

**CRITICAL-G4** — calibrate_tool 视频 seek 性能灾难
- 位置: [calibrate_tool.py:1705-1716](tools/calibrate_tool.py#L1705-L1716)
- 问题: 每次视频 seek 失败后回退 50 帧逐帧解码，播放器模式每次 tick 都触发此操作。
- 修复: 将逐帧解码循环改为只解码关键帧到目标帧的差异。

**CRITICAL-G5** — calibrate_tool `__getattr__` 代理架构缺陷
- 位置: [calibrate_tool.py:2771-2796](tools/calibrate_tool.py#L2771-L2796)
- 问题: `CalibrateTool.__getattr__` 代理到 `CalibrateWidget`，导致 `closeEvent` 与 Qt 框架虚函数同名冲突；嵌入 hdf5_tool 时调用不存在的 close_file 方法会产生误导性错误。
- 修复: 移除 `__getattr__` 代理，显式定义所有公共 API。

**CRITICAL-G6** — calibrate_tool load_h5_file 部分失败时状态不一致
- 位置: [calibrate_tool.py:758-793](tools/calibrate_tool.py#L758-L793)
- 问题: EMG 加载成功但 Prompt 加载失败时，UI 显示新 EMG + 旧 Prompt 的混合状态，严重的时间轴对齐错误。
- 修复: 加载失败时回滚所有数据到 None 或上一文件状态。

---

## 三、架构层面问题

### 3.1 单点故障

| 组件 | 故障影响 | 严重程度 | 自动恢复 |
|------|---------|----------|----------|
| realtimeEngine | 所有数据流停止 | **严重** | 无 |
| ble_server.py | BLE 数据丢失 | **严重** | 无 |
| storage_server.py | H5 写入静默失败 | **高** | 无 |
| camera_server.py | 视频丢失 | **中** | 无 |
| mocap_server.py | 连续手势不可用 | **低** | 无 |

**建议**: 添加 watchdog 进程监控 Python 子进程并自动重启。

### 3.2 数据丢失风险点

1. **`saveDataToStorage()` 静默丢弃数据**（`realtimeEngine.js:1613-1632`）— ZMQ PUSH 失败时无日志、无重试、无告警
2. **Camera 录制失败静默吞没** — `_markVideoRecordingStart()` 中 sendCameraCommand 失败仅 log，前端仍显示绿色"录制中"
3. **BLE 重连期间数据丢失** — `isCollecting` 仍为 true 但 `ble_client` 为 null，无缓冲

### 3.3 代码架构反模式

1. **God Object**: `RealtimeEngine` (1700 行) 管理 5 种连接 + 6 种数据流 + 采集状态机
2. **God Class**: `CollectionController` (4139 行) 承担 12+ 种不同职责
3. **摄像头管理三重分散**: cameraManager / realtimeEngine / deviceSync 各自维护状态
4. **全局状态无管理**: localStorage 被 7+ 文件分散读写，无 schema 验证

### 3.4 时间戳双时钟问题

- Python `time.time()` vs JS `Date.now()` 混用，当 camera_server 未连接时回退到 JS 时钟
- `closeStageFile` end_time 用 JS 时钟，start_time 用 Python 时钟，存在偏差
- 建议: 会话开始时获取 Python 时钟并存储偏差，所有后续时间戳从偏差换算

### 3.5 端口复杂度

8 个端口可精简至约 5 个：mocap_server (:8767) 可合并入 ble_server；camera 控制可走 :8080。

### 3.6 测试覆盖

**零自动化测试**。建议优先添加：
1. IMU normalization 单元测试（纯函数，投入产出比最高）
2. bin_sync_tool IMU count 推断（多源融合逻辑复杂）
3. HDF5 schema 验证

---

## 四、高等问题摘要（19 项）

### Python 服务层

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| H-P1 | ffmpeg 超时硬编码 60s | camera_server.py:384-393 | 长录制封装耗时可能远超 60s |
| H-P2 | EMG 滤波器非线程安全 | ble_server.py:303-307 | 全局滤波器被多线程并发修改 |
| H-P3 | 流启动时序中 stream_mode 回滚不完整 | ble_server.py:1644-1672 | 失败路径中 stream_mode 不一致 |
| H-P4 | EMGBinParser 全量加载 bin 到内存 | bin_sync_tool.py:470-525 | 10 分钟录音 ≈ 600MB，可能 OOM |
| H-P5 | 三种同步模式代码严重重复 | bin_sync_tool.py:663-1000 | 维护成本高 |

### Node.js 中间件

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| H-N1 | async Promise executor 反模式 | realtimeEngine.js:1675 | Promise 可能保持 pending 永不 resolve |
| H-N2 | async Promise executor（deviceSync） | deviceSync.js:190 | 同上 |
| H-N3 | WebSocket 启动失败时延迟定时器未清理 | realtimeEngine.js:130-193 | 操作已销毁对象 |
| H-N4 | Electron startServer() 盲目等待 3s | main.js:45-47 | 无就绪信号机制 |
| H-N5 | onPrompt() 重试可能重复写入 | realtimeEngine.js:682-705 | fire-and-forget + 重试 = 并发写入 |
| H-N6 | dataStorage.js 缺少 PYTHON_ENV | dataStorage.js:40 | 缺 UTF-8 编码导致 Windows GBK 乱码 |

### Python GUI 工具

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| H-G1 | 配对刷新无异步 | hdf5_tool.py:3553-3586 | 每次添加文件同步 I/O 扫描 |
| H-G2 | BreakpointTab 扫描同步阻塞 UI | hdf5_tool.py:3684-3826 | 大 storage 完全冻结 |
| H-G3 | Segment 链路扫描主线程阻塞 | hdf5_tool.py:1468-1473 | 逐文件打开 H5 |
| H-G4 | bin_pair_source 无健壮性 fallback | hdf5_tool.py:1231-1236 | 意外类型导致异常 |
| H-G5 | 全局 matplotlib rcParams 污染 | calibrate_tool.py:37-38 | 影响同一进程其他组件 |
| H-G6 | 潜在除零 | calibrate_tool.py:1574-1694 | 极端时间戳导致 int 溢出 |
| H-G7 | build_tool --windowed 隐藏所有错误 | build_tool.py:34 | 缺失依赖时用户看不到错误 |
| H-G8 | 缺少关键依赖 hidden-import | build_tool.py:37-43 | scipy/cv2 可能未打包 |

### 前端 UI（设计层问题）

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| H-F1 | 前端无形式化状态管理 | 跨文件 | localStorage 被 7+ 文件分散读写 |
| H-F2 | 摄像头管理职责三重分散 | cameraManager / realtimeEngine / deviceSync | 无唯一真相源 |
| H-F3 | 前端 Toast 三重实现 | page-switch / collection-controller / ble_control | 互相可能覆盖 |
| H-F4 | 类职责过重 | CollectionController 4139 行 | 12+ 种职责，难以维护 |
| H-F5 | 状态机定义不完整 | collection-controller | currentPhase 无显式状态机，通过赋值修改 |

---

## 五、中等问题摘要（67 项，列主要）

| 类别 | 数量 | 典型问题 |
|------|------|---------|
| Python 服务 | 13 | magic sleep 等待资源释放（camera）、JSON解析异常未捕获（storage）、串行广播阻塞（mocap）、IMU bin 帧边界不匹配、H5 重复打开 |
| Node.js 中间件 | 9 | 死代码 Express 实例、未使用的 dataBuffer、cameraManager 不实际控制摄像头、openBrowser 命令注入隐患、子进程 kill 缺少错误处理 |
| 前端 UI | 26 | 未声明属性 _isTestMode、phaseTimer 被覆盖、session overlay setTimeout 无引用、config rejected Promise 永不重试、toast 结构重复、削波闪烁定时器无条件运行 |
| GUI 工具 | 12 | update_stats 静默吞异常、WaveformWidget 无异常处理、QSettings 键名分散、QLabel size 初始化时序、时间漂移累积、H5 文件锁 Windows 高发问题 |

---

## 六、修复优先级建议

### 第一优先级（阻塞性 — 修复后系统才能可靠运行）

1. **CRITICAL-N1**: ZMQ 启动时序 → 调整 server.js 启动顺序
2. **CRITICAL-N2**: `this.realtimeEngine` TypeError → 改为模块引用
3. **CRITICAL-P2**: QSpinBox 未导入 → 添加导入（GUI 模式完全不可用）
4. **CRITICAL-F2**: 空格键重复绑定 → 删除重复调用
5. **CRITICAL-F11**: IMU 双重渲染 → 删除多余逻辑

### 第二优先级（数据安全 — 可能导致数据丢失或损坏）

6. **CRITICAL-N3**: Electron 进程泄漏 → PID 追踪 + IPC 优雅关闭
7. **CRITICAL-F3**: 续采状态覆盖 → onPageShow 检测续采模式
8. **CRITICAL-P1**: camera signal_handler 资源泄漏 → 移除 sys.exit
9. **CRITICAL-G3**: QThread 孤儿 → worker 运行状态检查
10. saveDataToStorage 静默丢数据 → 添加计数 + 日志
11. Camera 录制失败静默吞没 → 前端状态反馈

### 第三优先级（正确性 — 影响功能行为）

12. **CRITICAL-F1**: JSON.parse 缺 try/catch → 添加错误处理
13. **CRITICAL-F5**: 标定 setTimeout 无引用 → 存储引用
14. **CRITICAL-F12**: BLE 重连竞态 → 清除 pending timer
15. **CRITICAL-G1**: 主线程 sleep → 异步化
16. **CRITICAL-G2**: 数据丢失风险 → 先拷贝再清理
17. **CRITICAL-G5**: __getattr__ 代理 → 移除代理模式
18. **CRITICAL-G6**: 部分加载状态不一致 → 失败回滚
19. onPrompt 重试并发写入 → 使用 await + 标志位

### 第四优先级（性能与可维护性）

20. **CRITICAL-F8**: 每帧创建 Canvas → 缓存
21. **CRITICAL-G4**: 视频 seek 性能 → 关键帧跳转
22. EMGBinParser 内存 OOM 风险 → memmap 按需读取
23. BreakpointTab 扫描异步化 → QThread 后台执行
24. WebSocket 重连逻辑三处重复 → 抽取 ReconnectingWSClient 类

### 第五优先级（安全与防御性编程）

25. **CRITICAL-F9**, **CRITICAL-F10**: XSS 风险 → textContent 代替 innerHTML
26. God Object 重构 → 拆分 RealtimeEngine / CollectionController
27. 统一时间戳管理 → Python 时钟基准 + 偏差换算
28. 添加自动化测试

---

## 七、常见模式总结

以下模式在多个文件中重复出现，属于系统性改进机会：

| 模式 | 出现次数 | 改进方向 |
|------|---------|---------|
| setTimeout 无引用（无法取消） | 8+ | 创建 TimerManager 工具类 |
| 异常静默吞没 | 12+ | 添加统一错误日志 + 前端反馈 |
| 资源未清理（定时器/文件/连接） | 10+ | 实现显式 destroy/close 方法 |
| 同步 I/O 阻塞事件循环/UI | 6 | 移到 Worker/QThread |
| 硬编码常量（阈值/路径/关键字） | 15+ | 集中到常量文件 |
| 重复逻辑未抽取 | 8 | 提取通用函数/类 |
| 多模块各自实现相同功能 | 5 | 统一到单一模块 |
| localStorage 分散读写 | 7+ 文件 | 集中 AppState 管理 |

---

## 八、审计结论

系统在功能层面上设计合理，核心数据流（BLE → Node.js → 前端 → H5）架构清晰，离线同步 pipeline 完善。但在**工程健壮性**方面存在系统性不足：

1. **错误处理覆盖面不足** — 大量静默失败导致问题难以发现和诊断
2. **资源生命周期管理不完整** — setTimeout/文件句柄/进程/连接释放存在遗漏
3. **并发安全薄弱** — 多线程/多协程共享状态缺保护
4. **God Object 反模式** — 两个核心类超过 4000 行和 1700 行，维护成本高
5. **零自动化测试** — 无法回归验证修改的正确性

建议按照第五节优先级顺序分批次修复，每个批次修复后提交一次，便于追踪和回滚。

---

> 🤖 Generated with [Claude Code](https://claude.com/claude-code)
> 
> 子审查报告详见各个 Agent 输出：
> - Python 服务层: [ac6bceb1](任务日志)
> - Node.js 中间件: [aebdce0a](任务日志)
> - 前端 UI 层: [a6ba6ea1](任务日志)
> - Python GUI 工具: [a4d03487](任务日志)
> - 架构评审: [a341cc41](任务日志)
