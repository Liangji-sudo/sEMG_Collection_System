# BLE Server 问题排查与修复记录

**日期**: 2026-06-08  
**分支**: `fix_new` (基于 `merge_test`)  
**问题设备**: WristBand_8CD2 (MAC: 1C:DB:D4:81:8C:D2), 检测为 V2

---

## 1. 问题报告

新版本手环连接后：
- A. 连接卡顿
- B. 进入采集界面无信号显示
- C. Codex 修复后：信号显示一会后回调时间过长，丢包严重
- D. 连接中断后重连，再次进入采集界面无数据显示

## 2. 日志对比分析

| 日志时间 | 状态 | stream packet counter base | 数据速率 | 丢帧率 | 备注 |
|---------|------|---------------------------|---------|-------|------|
| 04:55:51 | ✅ 正常 | 有 | ~130批/5s | ~10% | response=True, 简单3s超时 |
| 05:14:03 | ✅ 正常 | 有 | ~138批/5s | ~10% | 同上 |
| 06:10:55 | ❌ 0数据 | **无** | 0 | — | epoch 竞态 |
| 06:21:19 | ❌ 0数据 | **无** | 0 | — | epoch 竞态 |
| 06:29:42 | ⚠️ 数据流恢复，大量丢包 | 有 | ~50批/5s | 69% | response=False → BLE参数未优化 |
| 06:39:47 | ❌ 第一次有数据(70%丢)，重连0数据 | 第一次有/重连无 | ~40批/5s | 70% | 同06:29 + 重连失败 |

## 3. 根因分析

### 根因 A：0 数据 — `notification_epoch` 竞态条件

**代码路径**：`_do_start_stream_for_device()` → `create_notification_handler()` → `start_notify()` → `send_control_command(START)` → `is_streaming = True`

**因果链**：
1. `create_notification_handler()` 捕获当前 `notification_epoch` 作为 `handler_epoch`
2. `data_sender_thread` 每 5ms 检查 `is_streaming`，为 `False` 时调用 `clear_stream_buffers()`
3. `clear_stream_buffers()` 执行 `notification_epoch += 1`
4. handler 创建后到 `is_streaming = True` 之间，存在多个 async 操作（`start_notify`、`sleep(0.25)`、`send_control_command(START)`），耗时可达数秒
5. `clear_stream_buffers()` 被调用数百次，`notification_epoch` 远超 `handler_epoch`
6. BLE 数据到达时，handler 检查 `handler_epoch != dev.notification_epoch` → `return` → **所有数据静默丢弃**

**修复**：将 `dev.is_streaming = True` 移到 `create_notification_handler()` 之前，阻断 `clear_stream_buffers` 的 epoch 递增。

### 根因 B：70% 丢包 — retry+fallback 过度阻塞事件循环

**现象**：回调间隔极其稳定地落在 104.9~105.1ms（78 次警告），吞吐量 ~50批/5s

**因果链**：
1. `send_control_command` 被人叠加了复杂的 retry+fallback 逻辑（timeout=5s, retries=1, fallback timeout=1.5s）
2. 连接后 `connect_device()` 发两条命令（采样率 + 复合配置），每条最坏 13s，共 26s
3. 事件循环被长时间阻塞，Windows BLE 栈无法处理 Connection Parameter Update
4. BLE 连接锁定在默认 **105ms**（84 × 1.25ms）间隔
5. 105ms × 9帧/包 ≈ 86帧/秒，目标 250帧/秒 → **66% 帧丢失**

**对比证据**：04-55 工作日志使用简单版 `response=True`（3s 超时，无 retry），数据速率正常。问题不在 `response=True` 本身，而在后续叠加的 retry/fallback 复杂度。

**修复**：回退到原始简单版 — V2 用 `response=True` 3s 超时，V1 用 `response=False`。只用基本的 SET_FILENAME 超时兜底。

### 根因 C：重连 0 数据 — 设备状态残留

**现象**：第一次连接正常（有丢包但数据流存在），`stop_any_stream` → `connect1` → `start_preview_stream` 后，stream 启动成功但 0 数据，无 `stream packet counter base`。

**原因**：
1. 第一次会话 BLE 连接中断后，设备可能仍处于 streaming 状态
2. 重连时直接发 START，设备因已处于 streaming 状态而忽略该命令
3. `disconnect_device` 后立即 `connect_device`，BLE 栈资源未完全释放

**修复**：
1. `_do_start_stream_for_device` 开头先发 STOP 重置设备状态
2. `connect_device` 中 `disconnect_device` 后 `await asyncio.sleep(0.5)` 等待 BLE 栈清理

### 根因 D：预览无法启动 — 前端收不到响应

**问题**：`start_preview_stream()` 在无可用设备时直接 `return`，不发送 WebSocket 响应，前端永久挂起。

**修复**：始终向 `ws` 发送响应（success/device_status/errors），失败时回滚 `stream_mode` 到 `"idle"`。

## 4. 最终修改清单

| # | 位置 | 改动 | 解决问题 |
|---|------|------|---------|
| 1 | `_do_start_stream_for_device()` L1637 | `is_streaming=True` 移到 handler 创建之前 | 根因 A：0 数据 |
| 2 | `_do_start_stream_for_device()` L1655 | except 块回滚 `is_streaming=False` | 失败状态清理 |
| 3 | `_do_start_stream_for_device()` L1618-1626 | 开头先发 STOP 重置设备状态 | 根因 C：重连 0 数据 |
| 4 | `send_control_command()` L1132-1159 | 回退到原始简单版（V2: response=True 3s，V1: response=False） | 根因 B：70% 丢包 |
| 5 | `connect_device()` L1196-1198 | disconnect 后 `await asyncio.sleep(0.5)` | 根因 C：BLE 栈清理 |
| 6 | `start_preview_stream()` L1686-1729 | 补全 WebSocket 响应 + 失败回滚 stream_mode | 根因 D：前端挂起 |

## 5. 架构说明

### 数据流管道

```
手环 BLE 通知
  → create_notification_handler (epoch check → enqueue_raw_packet)
  → dev.raw_buffer (deque, maxlen=1000)
  → drain_raw_packets() (data_sender_thread, 每 5ms)
  → parse_packet() (解析 + 滤波 + 通道映射)
  → dev.data_buffer (deque, maxlen=500)
  → data_sender_thread (批量 5 个/次)
  → add_to_queue() → process_queue() → WebSocket :8766 → realtimeEngine.js
```

### send_control_command 行为

| 设备版本 | GATT Write 方式 | 超时 | 异常处理 |
|---------|----------------|------|---------|
| V1 | Write without Response | — | OSError/BleakError → propagate |
| V2 | Write with Response | 3s | Timeout → SET_FILENAME 兜底 response=False，其他 propagate |

### 关键状态

- `stream_mode`: `"idle"` → `"preview"` → `"collection"` → `"idle"`
- `is_streaming`: 控制 `data_sender_thread` 行为（drain vs clear_buffers）,**必须在 handler 创建前设为 True**
- `notification_epoch`: 每次 `reset_stats()` 或 `clear_stream_buffers()` 递增，用于无效化旧 handler

## 6. 日志关键指标

正常运行的预期值：
- `stream packet counter base: 0` — 启动后应立即出现
- `已收到 900 帧, 丢帧: 0` — 初始无丢帧
- `数据发送] 已发送 130+ 批数据` — 每 5 秒发送统计
- 回调间隔警告 < 10 次/分钟（偶尔出现属正常，BLE 无线特性）

异常信号：
- 无 `stream packet counter base` → BLE 通知未到达 handler
- `已发送 0 批数据` → 数据处理管道中断
- 回调间隔稳定在 105ms → BLE 连接参数未优化
- 重连后 0 数据 → 设备状态残留，需 STOP-before-START
