# 采集流与预览流切段架构审计与实施设计

> **文档状态**: Phase 1 — 只读架构审计 + 实施方案设计  
> **分支**: `fix_sync`  
> **日期**: 2026-06-01  
> **前序文档**: [esp32_ble_sd_write_lifecycle_audit_2026-06-01.md](esp32_ble_sd_write_lifecycle_audit_2026-06-01.md)

---

## 目录

1. [当前 ESP32 Streaming / SD 写入状态机](#1-当前-esp32-streaming--sd-写入状态机)
2. [当前电脑端连接、预览、采集、停止、异常中断的实际时序](#2-当前电脑端时序)
3. [新目标时序图](#3-新目标时序图)
4. [ESP32 短时间 stop/start 的结论和风险](#4-esp32-短时间-stopstart-的结论和风险)
5. [固件改动需求评估](#5-固件改动需求评估)
6. [ble_server.py API 设计](#6-ble_serverpy-api-设计)
7. [前端按钮行为矩阵](#7-前端按钮行为矩阵)
8. [realtimeEngine / storage_server 数据流改动点](#8-realtimeengine--storage_server-数据流改动点)
9. [H5 Schema 改动建议](#9-h5-schema-改动建议)
10. [bin_sync_tool / hdf5_tool 兼容新旧数据的策略](#10-bin_sync_tool--hdf5_tool-兼容新旧数据的策略)
11. [分阶段实施计划](#11-分阶段实施计划)
12. [必须实测验证清单](#12-必须实测验证清单)

---

## 1. 当前 ESP32 Streaming / SD 写入状态机

### 1.1 核心状态标志

| 标志 | 位置 | 含义 |
|------|------|------|
| `is_streaming` | `app_common.h:234` atomic_bool | 唯一 streaming 控制标志，同时控制 BLE 发送 + SD 写入 |
| `notify_enabled` | `app_common.h:231` atomic_bool | BLE notify 是否启用（CCCD） |
| `sd_files_closed` | `app_common.h:238` atomic_bool | SD bin 文件是否已 footer+close |
| `sd_files_closed` 初始值 | `APP_STATE_INIT` | `true`（没有文件打开状态） |

### 1.2 start_streaming_system() 时序（main.c:104-157）

```
1. 检查 is_streaming，若已在 streaming 则直接返回
2. 获取 CPU lock（CONFIG_PM_ENABLE）
3. IMU 上电
4. app_state_reset_stream_counters() — 重置 overflow 计数器和 frames_written
   ⚠️ 注意：不重置 s_raw_interrupt_counter（SD帧计数）和 ble_frame_counter（BLE帧计数）
5. app_state_set_sd_files_closed(false) — 标记文件未关闭，允许sd_write_task写文件
6. clear_ble_ringbuf_pending() — 清空 BLE ringbuffer
   ⚠️ 注意：不清空 SD ringbuffer、IMU ringbuffer
7. ADS 上电/配置/启动（最多重试3次）
8. 若 ADS 成功: app_state_set_streaming(true) + 通知事件
```

### 1.3 stop_streaming_system() 时序（main.c:160-189）

```
1. 记录 was_streaming = is_streaming 原值
2. app_state_set_streaming(false) ← 关键：此操作后 emg_data_task 不再写 ringbuffer
3. app_state_set_stream_stop_reason(stop_reason)
4. vTaskDelay(50ms) — 等待最后一个中断周期结束
5. ads_stop_conversion() — 停止 ADS（GPIO START=0, SDATAC, PWDN=0）
6. imu_set_power_mode(false)
7. 释放 CPU lock
8. 发送 BLE_STATUS_EVENT_STREAM_STOPPED 通知
```

### 1.4 sd_write_task() 文件关闭逻辑（sd_storage.c:600-647）

```
if (!is_streaming):
    // Phase A: 排出 ringbuffer 中剩余数据
    drain_ringbuf_to_file(sd_ringbuf, &f_emg, ...)  // 无超时，drain 完所有待处理数据
    drain_ringbuf_to_file(imu_ringbuf, &f_imu, ...)

    // Phase B: 如果 ringbuffer 为空且文件仍打开，关闭文件
    if (f_emg != NULL && !ringbuf_has_pending_item(sd_ringbuf_handle)):
        close_session_file(&f_emg, ...) // 写 footer + fflush + fsync + fclose
        header_written_emg = false
        fn_emg[0] = '\0'  ← 清除文件名字符串

    if (f_imu != NULL && !ringbuf_has_pending_item(imu_ringbuf_handle)):
        close_session_file(&f_imu, ...) // 同上
        fn_imu[0] = '\0'

    // Phase C: 如果两个文件都关闭了
    if (f_emg == NULL && f_imu == NULL):
        app_state_set_sd_files_closed(true)  ← 关闭完成标记

    vTaskDelay(100ms)  ← 每个 loop 迭代至少等待 100ms
```

**关键结论**：文件关闭不是即时的。STREAM_STOP 后，需要 sd_write_task 至少完成一次 loop 才能排出数据并关闭文件。最小延迟 = `drain_time + vTaskDelay(100ms)`。且 STREAM_STOP 后还有 `vTaskDelay(50ms)`（main.c:168）。

### 1.5 BLE frame_counter 和 SD raw_interrupt_counter 生命周期

| 计数器 | 位置 | 重置时间 | 重置条件 |
|--------|------|----------|----------|
| `s_raw_interrupt_counter` | ads1298.c:13 (file static) | 每次 `!is_streaming` 时的 while loop 迭代 (line 247) | 无条件，每次循环检查 |
| `ble_frame_counter` | ble_gatt.c:699 (function static) | `!is_streaming` 时重置 (line 716) | **有竞态条件**：task 可能在 `xRingbufferReceive` 阻塞时错过重置 |

### 1.6 文件名生成

- 电脑端 ble_server.py `start_stream()` 第 1257-1278 行构建文件名：`{session_id}_{hand_label}_{now_str}`（如 `S001_L_260601_143025`）
- 通过 `BLE_CMD_TIMESTAMP (0xD0)` 发送给 ESP32
- ESP32 端 `app_state_set_timestamp()` 存储到 `g_app_state.timestamp_str`
- `sd_write_task` 中第一次有数据时通过 `app_state_build_data_path("emg", fn_emg, ...)` 生成完整路径 `/data/{timestamp_str}_emg.bin`
- `app_state_build_data_path` 直接使用 `timestamp_str`，不做任何递增或修改

**关键风险**：TIMESTAMP 在 START 之前发送，若新 START 的 TIMESTAMP 未在文件打开前到达，会使用旧文件名。

---

## 2. 当前电脑端时序

### 2.1 连接 + 进入采集页

```
用户操作                    前端                       ble_server(8764)         realtimeEngine(8080)     ESP32
─────────                   ────                       ────────────────         ──────────────────       ────
扫描/连接                    BleControl.connectDevice()  connect_device()
                                                                                (已自动连接8766)
进入采集页                   page-switch.showCollection()
                              └─ startWaveform()                                 dataReceiver.connect()
                              └─ (不发送 start/stop 给 ble_server)
```

**当前行为**：连接后不做 BLE streaming。进入采集页只建立 WebSocket 连接，等待数据。但 BLE 数据流尚未启动，所以波形无数据显示。需要手动点 `start_all` 或通过 `submitUserInfo()` 间接启动。

### 2.2 开始采集（单轮）

```
用户点击"开始采集"           collection-controller.js     ble_server(8764)         realtimeEngine(8080)     storage_server
─────────                   ────────────────────────     ────────────────         ──────────────────       ──────────────
(可能先 submitUserInfo)
  └─ BleControl.startAll()  → 发送到 8764               start_all()
                                                          └─ start_stream(1)
                                                          │   └─ TIMESTAMP(0xD0)
                                                          │   └─ START_NOTIFY
                                                          │   └─ START(0xA0)
                                                          └─ start_stream(2)
                                                          └─ broadcast: sd_filenames_updated → (8766端口)  → onSdFilenamesUpdated()
submitUserInfo 完成后
  └─ showCollection()
  
用户点击"开始采集（单轮）"   startTask()
                              └─ sendToRealtimeEngine('collection_start')
                                                                                 onCollectionStart()
                              └─ sendToRealtimeEngine('stage_start')
                                                                                 onStageStart() → openStageFile()
                                                                                   └─ sendStorageCommand('create')
                                                                                   │   使用 sd_filenames.dev1/dev2

                              └─ 手势动画开始                                     saveDataToStorage('append') → 写 H5
```

**关键问题**：`start_all` 发送给 ble_server（8764端口），`collection_start` 发送给 realtimeEngine（8080端口）。两者在不同通道，不同步。`sd_filenames_updated` 事件通过 ble_server 数据端（8766）发送，realtimeEngine 监听。存在竞态：如果 `openStageFile` 在 `sd_filenames_updated` 到达之前调用，`sd_filenames` 为空，H5 缺少 bin 引用。

当前代码有 300ms 等待（realtimeEngine.js:533），但不能 100% 保证。

### 2.3 停止采集

```
用户点击"停止"               collection-controller.js     realtimeEngine(8080)     ble_server
─────────                   ────────────────────────     ──────────────────       ──────────
stopTask()                    sendToRealtimeEngine('collection_stop', {completed: false})
                                                        onCollectionStop(false)
                                                          └─ closeStageFile({collection_status:'manual_stopped'})

(如果返回首页)                 page-switch.backToWelcome()
                              └─ BleControl.stopAll()    (→ ble_server stop_all)
                                                          └─ STOP(0xA1) to ESP32
```

**当前问题**：
1. `stopTask()` 只通知 realtimeEngine 停止 H5 记录，不停止 BLE stream
2. BLE stream 只在 `backToWelcome()` 中通过 `BleControl.stopAll()` 停止
3. 如果在采集页内多次 start/stop，BLE stream 保持运行，bin 文件继续增长
4. 下一次 startAll 复用同一个 session_id → 生成**同名** bin 文件 → ESP32 的 TIMESTAMP 被覆盖 → 新数据覆盖旧 bin

### 2.4 异常中断

```
用户点击"异常中断"           collection-controller.js     realtimeEngine(8080)
─────────                   ────────────────────────     ──────────────────
abortTask()                  1. 立即冻结本地状态
                             2. 生成断点快照
                             3. sendToRealtimeEngine('abnormal_interrupt_freeze')
                                                        onAbnormalInterruptFreeze()
                                                          └─ isCollecting=false (冻结写入)
                             4. 弹出原因选择框
                             5. 用户选择原因后:
                              sendToRealtimeEngine('abnormal_interrupt')
                                                        onAbnormalInterrupt()
                                                          └─ closeStageFile({collection_status:'abnormal_interrupted'})
                             6. 保存 breakpoint_state 到 localStorage
                             7. backToWelcome()
                                └─ BleControl.stopAll()  → STOP(0xA1) to ESP32
```

### 2.5 全部轮次采集

```
startAllSessions()            session=1 startTask(false)  → stage_start → open H5 → stage_end → close H5
                              休息倒计时 N 秒
                              session=2 startTask(false)  → stage_start → open H5 → stage_end → close H5
                              ...
```

**当前问题**：所有 session 共享同一个 bin 文件（BLE stream 全程未重启），所有 H5 的 `sd_bin_dev1/dev2` 相同。

---

## 3. 新目标时序图

### 3.1 Connect（连接手环，不推流）

```
用户操作                    前端                       ble_server               ESP32
─────────                   ────                       ──────────               ────
扫描 → 选择设备 → 连接      BleControl.connectDevice()  connect_device()         BLE 连接建立
                                                       └─ 发送 CONFIG 命令
                                                       └─ 不发送 START
状态: is_streaming=false, notify_enabled=false
SD: 不产生 bin
```

### 3.2 Enter Collection Page → Preview Stream

```
进入采集页                  page-switch                ble_server               ESP32                realtimeEngine
─────────                   ───────────                ──────────               ────                ──────────────
showCollection()            
  └─ startWaveform()                                  (通过8764发preview_start)→ TIMESTAMP(preview)   (8766连接已有)
  └─ 发送 start_preview                              └─ START(0xA0) → is_streaming=true → 写 preview bin
                                                                                                  → 发 BLE 数据
                                                                               preview bin 创建     收到数据 → 前端波形显示
                                                                                                  但 ❌ 不写入 H5
状态: stream_mode=preview
SD: 产生 preview bin（如 "PREVIEW_L_260601_120000_emg.bin"）
H5: 未打开
```

### 3.3 Start Collection（单轮 / 全部轮次 / 续采）

```
用户点击"开始采集"           collection-controller     ble_server               ESP32                    realtimeEngine
─────────                   ────────────────────────   ──────────               ────                    ──────────────
startTask()
  └─ Phase A: 切流
      └─ 发送 stop_preview to ble_server              STOP(0xA1) 
                                                      └─ 等待 sd_files_closed  ← 关键：需等待确认
                                                                                └─ preview bin footer+close
                                                      (等待 N ms 安全窗口)
      └─ 发送 start_collection to ble_server          TIMESTAMP(collection)     collection bin 名称设置
                                                      └─ START(0xA0)            collection bin 创建
                                                      └─ sd_filenames_updated(bin_names)
                                                                                                        onSdFilenamesUpdated()
  └─ Phase B: 确认 bin 文件名已到达
      └─ 等待 sd_filenames 非空（最多 500ms）
  └─ Phase C: 打开 H5
      └─ sendToRealtimeEngine('collection_start')
                                                                                onCollectionStart()
      └─ sendToRealtimeEngine('stage_start')
                                                                                openStageFile()
                                                                                  └─ create H5 attrs: sd_bin_dev1/dev2 = collection bin names
  └─ Phase D: 手势动画 + EMG 数据写入 H5
```

### 3.4 Stop Collection → Resume Preview

```
Stage 完成 / 用户点击"停止"  collection-controller     realtimeEngine           ble_server               ESP32
─────────                    ────────────────────────  ──────────────────       ──────────               ────
stopTask() / stage完成
  └─ sendToRealtimeEngine('collection_stop')
                                                      onCollectionStop()
                                                        └─ closeStageFile() → H5 closed
  └─ Phase E: 切回流
      └─ 发送 stop_collection to ble_server                                   STOP(0xA1)                collection bin close
                                                                              等待 sd_files_closed
      └─ 发送 start_preview to ble_server                                     TIMESTAMP(preview)
                                                                              START(0xA0)               preview bin 创建
                                                                              波形数据恢复
```

### 3.5 全部轮次：每个 Session H5 对应一对 Collection Bin

```
startAllSessions()           全局: recordingSessionId = "rec_20260601_120000_3"
                             Session 1:
                               stop_preview → start_collection → TIMESTAMP(S001_L) → START → open H5(segment=1)
                               ... 采集 ...
                               close H5 → stop_collection
                               start_preview → 恢复波形预览
                               休息 30s...
                             Session 2:
                               stop_preview → start_collection → TIMESTAMP(S002_L) → START → open H5(segment=2)
                               ... 采集 ...
                               close H5 → stop_collection → start_preview
                               ...
```

每个 session H5 有独立的 `sd_bin_dev1/dev2`，指向不同的 bin 文件对。

### 3.6 Abnormal Interrupt

```
用户点击"异常中断"           collection-controller     realtimeEngine           ble_server               ESP32
─────────                    ────────────────────────  ──────────────────       ──────────               ────
abortTask()
  1. 立即冻结本地状态
  2. sendToRealtimeEngine('abnormal_interrupt_freeze')
                                                      onAbnormalInterruptFreeze()
                                                        └─ isCollecting=false (停止 H5 写入)
  3. sendToRealtimeEngine('abnormal_interrupt')
                                                      onAbnormalInterrupt()
                                                        └─ closeStageFile({collection_status:'abnormal_interrupted'})
  4. Phase E': stop_collection_stream                 stop_collection → STOP(0xA1)                       collection bin close
  5. 保存 breakpoint_state
  6. backToWelcome()
     ⚠️ 默认不重启 preview（因为返回首页后不显示波形）
```

### 3.7 Return Home / Disconnect

```
返回首页                     page-switch               ble_server               ESP32
─────────                    ───────────               ──────────               ────
backToWelcome()
  └─ 如果 stream_mode==preview:
      └─ stop_preview to ble_server                    STOP(0xA1)               preview bin close
  └─ 如果 stream_mode==collection:
      └─ 先 close H5（如果还开着）
      └─ stop_collection to ble_server                 STOP(0xA1)               collection bin close
  └─ 不自动 start preview（首页不需要波形）

断开连接                     BleControl.disconnect()    disconnect_device()
                                                       └─ stop_stream            STOP(0xA1)
                                                       └─ disconnect BLE
```

---

## 4. ESP32 短时间 stop/start 的结论和风险

### 4.1 STREAM_STOP 后 SD 写任务何时关闭 bin？

SD 写任务在 `!is_streaming` 分支中执行：
1. 首先 drain 所有 ringbuffer 剩余数据（无超时）
2. 确认 ringbuffer 为空后：写 footer → fflush → fsync → fclose
3. 设置 `fn_emg[0] = '\0'`
4. 两个文件都关闭后：`app_state_set_sd_files_closed(true)`
5. 完成一次循环需要至少 `vTaskDelay(100ms)`（sd_storage.c:645）

**实测最小时间**：`stop_streaming_system 50ms 延迟` + `drain 时间（取决于残留数据量）` + `至少一次 sd_write_task 循环（100ms）` ≈ **>150ms**。如果 ringbuffer 中有大量数据，drain 时间可能更长。

### 4.2 是否有 drain 队列机制？关闭完成有没有状态/事件反馈？

**Drain 机制：有**。`drain_ringbuf_to_file()` 使用 `xRingbufferReceive(ringbuf, &data_size, 0)` 即非阻塞方式连续取出所有待处理数据。

**关闭完成反馈：有限**。
- ESP32 内部：`app_state_are_sd_files_closed()` 可查询
- BLE Status Snapshot 中：有 `sd_drop_count`、`emg_frames_written`、`imu_frames_written`、`storage_state` 字段
- **没有**专门的 "bin file finalized" 事件通知
- PC 端无法直接确认 bin 文件已完整关闭

### 4.3 立刻 STREAM_STOP → STREAM_START 的风险矩阵

| 风险项 | 严重度 | 详情 | 当前代码行为 |
|--------|--------|------|-------------|
| 上一个 bin footer 未写完 | **HIGH** | 新 START 后 sd_write_task 回到 `is_streaming` 分支，旧文件的 footer 永不会写入 | sd_write_task 只在 `!is_streaming` 时检查是否需要 close。如果 STOP→START 太快，sd_write_task 从未进入 close 分支 |
| 新 bin 文件名未更新 | **HIGH** | `fn_emg[0]='\0'` 在 close 分支中执行。若旧文件未关闭，`fn_emg[0]` 仍为非空——sd_write_task 不会生成新路径 | sd_storage.c:545: `if (fn_emg[0] == '\0') { app_state_build_data_path(...); }` — 旧路径存在则跳过 |
| ringbuffer 残留数据进入新 bin | **MEDIUM** | `start_streaming_system` 只 clear BLE ringbuffer，不 clear SD/IMU ringbuffer | main.c:121: `clear_ble_ringbuf_pending()` 仅清理 BLE ringbuf |
| packet_counter 没有重置 | **HIGH** | `ble_frame_counter` 重置存在竞态条件（task 可能阻塞在 ringbuf receive） | ble_gatt.c:714-717: 仅当 `!is_streaming` 且 task 不阻塞时才能重置 |
| raw_counter 没有重置 | **LOW** | `s_raw_interrupt_counter` 在 `!is_streaming` 的每次循环都重置 | ads1298.c:247: 无条件重置 |
| sd_filename 仍是旧文件名 | **HIGH** | TIMESTAMP 命令更新 `g_app_state.timestamp_str`，若在 START 前未到达 → 使用旧名 | ble_gatt.c:489-494: TIMESTAMP handler |
| BLE notify 仍在发送旧流尾巴 | **MEDIUM** | BLE 数据在 ringbuf 中可能残留 | clear_ble_ringbuf_pending 已清理，但仅限 BLE ringbuf |

### 4.4 可信 stop/start 的最小固件改动

**如果不改固件，仅靠 PC 端 delay**：
- 推荐 delay: **≥ 1000ms**
- 理由：STREAM_STOP 内 50ms + drain 时间（最多数百ms）+ sd_write_task 至少 1 次循环（100ms）+ 安全余量
- **仍然不可靠**：无法确认 bin 文件已关闭；无法确认 counter 已重置
- **源码依据**：sd_storage.c:645 `vTaskDelay(pdMS_TO_TICKS(100))`，但实际可能需要多个循环才能 drain 完

**如果需要可靠切段，最小固件改动**（详见第 5 节）：
1. `start_streaming_system()` 中增加 clear SD + IMU ringbuffer
2. 增加 `sd_files_closed` 相关状态通知到 BLE status
3. TIMESTAMP 更新后重置 `fn_emg[0]`，强制重新生成文件名

---

## 5. 固件改动需求评估

### 5.1 结论：需要最小固件改动

当前固件**设计上假设一次 streaming session 创建一个 bin 文件**。`fn_emg[0] = '\0'` 和 `s_raw_interrupt_counter = 0` 等重置逻辑都发生在"非 streaming 状态"周期，而不是"新 streaming 开始"时。这导致快速 stop/start 不可靠。

### 5.2 最小固件改动列表

**优先级 P0（必须）**：

#### P0-1: start_streaming_system() 中增加 SD/IMU ringbuffer 清理

```c
// main.c start_streaming_system() 中，约第 121 行后添加：
static void clear_ringbuf_pending(RingbufHandle_t rb) {
    if (rb == NULL) return;
    size_t item_size = 0;
    void *item = NULL;
    while ((item = xRingbufferReceive(rb, &item_size, 0)) != NULL) {
        vRingbufferReturnItem(rb, item);
    }
}

void start_streaming_system(void) {
    // ... existing code ...
    clear_ble_ringbuf_pending();
    clear_ringbuf_pending(sd_ringbuf_handle);   // 新增
    clear_ringbuf_pending(imu_ringbuf_handle);  // 新增
    // ... rest ...
}
```

**影响**：防止旧 stream 的残留数据进入新 bin。

#### P0-2: 确保 frame_counter 在 START 时可靠重置

```c
// 在 start_streaming_system() 中将 counter 重置从被动改为主动：
// ads1298.c: 新增函数
void ads_reset_raw_frame_counter(void) {
    s_raw_interrupt_counter = 0;
}

// ble_gatt.c: 新增函数或使用 atomic 变量
// 将 ble_frame_counter 改为全局 atomic，在 start_streaming_system 中重置
```

**影响**：确保每个 bin 文件的 frame_id 从 0 开始，便于 bin_sync_tool 定位。

#### P0-3: TIMESTAMP 更新后强制重新生成 bin 文件名

```c
// app_state.c: 修改 app_state_set_timestamp
void app_state_set_timestamp(const uint8_t *value, size_t len) {
    // ... existing code ...
    // 新增：通知 sd_write_task 需要重新生成文件名
    g_app_state.bin_path_dirty = true;  // 新 flag
}

// sd_storage.c: 修改文件名生成逻辑
if (fn_emg[0] == '\0' || g_app_state.bin_path_dirty) {
    app_state_build_data_path("emg", fn_emg, sizeof(fn_emg));
    g_app_state.bin_path_dirty = false;
}
```

**影响**：防止新 stream 写到旧 bin 文件。

**优先级 P1（强烈建议）**：

#### P1-1: 增加 bin 文件关闭完成事件

```c
// sd_storage.c: close_session_file 后
app_state_set_sd_files_closed(true);
ble_gatt_notify_event(BLE_STATUS_EVENT_FILE_CLOSED, BLE_STATUS_SEVERITY_INFO, 0, 0);
ble_gatt_publish_status_snapshot(BLE_STATUS_REASON_STORAGE);
```

**影响**：PC 端可确认 bin 已安全关闭，不再需要盲目等待 delay。

#### P1-2: 增加 stream_generation_id

```c
// 每次 start_streaming_system() 递增
static atomic_uint_fast32_t stream_generation = 0;
atomic_fetch_add(&stream_generation, 1);

// 包含在 status snapshot 或数据包头中
```

**影响**：PC 端可精确区分不同 streaming session 的数据。

**优先级 P2（可选优化）**：

#### P2-1: 支持 stream mode hint
```c
// 在 TIMESTAMP 或 CONFIG 命令中增加 mode 字节，区分 preview/collection
// 使 ESP32 可以选择不同的 SD 写入策略（如 preview 不写 bin）
```

### 5.3 如果不改固件的风险缓解策略

如果固件不能修改，需要采取以下策略：

1. **stop→start 之间延迟 ≥ 1500ms**：给足时间让 sd_write_task 完成 drain + close
2. **使用不同的 session_id 前缀**：每次 start_collection 使用新的 TIMESTAMP（带秒级时间戳），确保 bin 文件名不重复
3. **PC 端验证 sd_files_closed**：通过 BLE Status Snapshot 的 `storage_state` 字段间接判断
4. **接受 preview bin 的存在**：preview bin 文件名加 `PREVIEW_` 前缀，同步工具过滤
5. **SD 卡空间管理**：定期清理 preview bin

---

## 6. ble_server.py API 设计

### 6.1 新增 stream_mode 状态

在 `DeviceState` 和 `ServerState` 中增加：

```python
@dataclass
class DeviceState:
    # ... existing fields ...
    stream_mode: str = "idle"  # "idle" | "preview" | "collection"
    
class ServerState:
    # ... existing fields ...
    stream_mode: str = "idle"
```

### 6.2 新增 WebSocket 控制 API

| Action | 描述 | 参数 | 响应 |
|--------|------|------|------|
| `start_preview` | 启动预览流 | `{device_id?: 1\|2}` 或 `start_preview_all` | 同 start_stream，但 stream_mode=preview |
| `stop_preview` | 停止预览流 | `{device_id?: 1\|2}` 或 `stop_preview_all` | 同 stop_stream |
| `start_collection` | 启动采集流 | `{session_id, recording_session_id?, segment_index?}` | 返回 `collection_started` 含 `sd_filenames` |
| `stop_collection` | 停止采集流 | `{device_id?: 1\|2}` 或 `stop_collection_all` | 返回 `collection_stopped` 含 `final_stats` |
| `get_stream_filenames` | 查询当前流产生的 bin 文件名 | — | `{dev1_bin, dev2_bin, dev1_imu_bin, dev2_imu_bin}` |
| `wait_bin_closed` | 等待 bin 文件关闭（带超时） | `{timeout_ms: 3000}` | `{closed: true/false, timeout: bool}` |

### 6.3 start_collection 实现要点

```python
async def start_collection(ws, device_id=None):
    """
    启动采集流（collection stream）
    
    与 start_stream（preview）的区别：
    1. TIMESTAMP 使用 collection 前缀（不含 PREVIEW_）
    2. 返回的 sd_filenames 需要被 H5 记录
    3. stream_mode 标记为 "collection"
    """
    # 1. 如果当前有 preview stream，先 stop
    if dev.stream_mode == "preview":
        await stop_preview(ws, device_id, silent=True)
        # 等待 bin close（TODO: 需要 ESP32 固件支持或 delay）
        await asyncio.sleep(1.5)  # 安全窗口
    
    # 2. 生成 collection bin 文件名
    now_str = datetime.now().strftime("%y%m%d_%H%M%S")
    hand_label = "L" if device_id == 1 else "R"
    filename_str = f"{state.session_id}_{hand_label}_{now_str}"
    
    # 3. 发送 TIMESTAMP + START（与 start_stream 相同硬件命令）
    filename_cmd = bytes([CMD_MAP['SET_FILENAME']]) + filename_str.encode('ascii')
    await send_control_command(dev, filename_cmd)
    dev.sd_filename = filename_str
    
    await asyncio.sleep(0.1)
    await dev.client.start_notify(EMG_DATA_CHAR_UUID, handler)
    await send_control_command(dev, bytes([CMD_MAP['START']]))
    
    dev.stream_mode = "collection"
    dev.is_streaming = True
    
    # 4. 立即通知 sd_filenames_updated
    await broadcast_event('sd_filenames_updated', {
        'sd_filenames': {f'dev{device_id}': filename_str},
        'stream_mode': 'collection',
        'stream_generation_id': state.stream_generation_id,
    })
```

### 6.4 stop_collection 实现要点

```python
async def stop_collection(ws, device_id=None):
    """停止采集流"""
    # 1. 发送 STOP 命令
    await send_control_command(dev, bytes([CMD_MAP['STOP']]))
    await dev.client.stop_notify(EMG_DATA_CHAR_UUID)
    dev.is_streaming = False
    dev.stream_mode = "idle"
    
    # 2. 等待 bin close（ESP32 sd_write_task drain + footer + close）
    # 当前：盲目等待 1.5s
    await asyncio.sleep(1.5)
    
    # 3. 返回最终统计
    await broadcast_event('collection_stopped', {
        'device_id': device_id,
        'sd_filename': dev.sd_filename,
        'total_frames': dev.total_frames,
        'lost_frames': dev.lost_frames,
    })
```

### 6.5 API 兼容性

旧的 `start1/start2/start_all` API 保持不变，但内部映射为 `start_preview`。添加废弃警告日志：

```python
elif action == 'start_all':
    log("[控制端] 警告: start_all 已废弃，请使用 start_preview_all 或 start_collection_all")
    await start_preview_all(ws)  # 默认作为 preview
```

---

## 7. 前端按钮行为矩阵

### 7.1 行为矩阵表

| # | 按钮/操作 | 当前行为 | 新目标行为 | 涉及模块 |
|---|----------|---------|-----------|---------|
| 1 | **连接设备** | connect → 发送 CONFIG，不推流 | **不变** — 只连接，不推流 | ble_control.js |
| 2 | **进入采集页** | startWaveform()（连数据 WS，不启动流） | **新增**：自动启动 **preview stream**（如果设备已连接且未在 streaming） | page-switch.js, ble_control.js |
| 3 | **返回首页** | stopWaveform() + BleControl.stopAll() | **修改**：停止当前 stream（preview 或 collection），不自动重启 | page-switch.js |
| 4 | **断开连接** | disconnect → stop stream + disconnect BLE | **不变** — stop any stream + disconnect | ble_control.js |
| 5 | **开始采集（单轮）** | startTask() → collection_start → open H5 | **新增**：先 `stop_preview` → 等待 → `start_collection` → 等待 sd_filenames → `collection_start` → `open H5` | collection-controller.js |
| 6 | **开始全部轮次** | startAllSessions() → 多个 startTask() | **新增**：每个 session 前执行 `stop_preview → start_collection`，session 结束后 `stop_collection → start_preview` | collection-controller.js |
| 7 | **停止按钮** | stopTask() → collection_stop → close H5 | **新增**：close H5 → `stop_collection` → `start_preview`（恢复预览） | collection-controller.js |
| 8 | **异常中断** | abortTask() → freeze + close H5 (abnormal) | **新增**：close H5 → `stop_collection` → 保存断点 → 返回首页 → **不**自动 start preview | collection-controller.js |
| 9 | **断点续采** | resumeBreakpoint() → start_all → showCollection → startTask() | **新增**：进入采集页 → `start_preview` → 用户确认 → `stop_preview → start_collection` → 续采 H5 | page-switch.js, collection-controller.js |
| 10 | **测试按钮** | startTask(isTestMode=true) | **不变** — 测试模式不开 collection bin，不需要切换流（继续使用 preview stream）| collection-controller.js |

### 7.2 状态转换图

```
                    disconnect / return home
                  ┌─────────────────────────────┐
                  │                             │
                  ▼                             │
  ┌─────────┐  enter collection   ┌───────────┐ │
  │  IDLE   │ ──────────────────> │ PREVIEW   │ │
  │ (首页)  │                     │ (预览波形) │ │
  └─────────┘ <────────────────── └─────┬─────┘ │
       ▲          return home           │       │
       │                    start collection  │
       │                                │       │
       │          ┌─────────────────────┘       │
       │          ▼                             │
       │   ┌─────────────┐    stop / complete   │
       │   │ COLLECTION  │ ─────────────────────┘
       │   │ (采集记录)  │
       │   └──────┬──────┘
       │          │ abnormal interrupt
       │          ▼
       │   ┌──────────────┐
       │   │ INTERRUPTED  │ ────> 返回首页 (IDLE, breakpoint 已保存)
       │   └──────────────┘
       │
       └─── (从首页重新进入采集页 → PREVIEW → 续采 → COLLECTION)
```

---

## 8. realtimeEngine / storage_server 数据流改动点

### 8.1 realtimeEngine.js 改动

#### 8.1.1 stream_mode 状态

```javascript
class RealtimeEngine extends EventEmitter {
    constructor() {
        // ... existing ...
        this.streamMode = 'idle';  // 'idle' | 'preview' | 'collection'
        this.collectionStreamStartedAt = null;
        this.collectionBinFilenames = { dev1: null, dev2: null };
    }
}
```

#### 8.1.2 新增事件处理

```javascript
// 新增: 处理 stream_mode_changed 事件
if (packet.type === 'event' && packet.event === 'stream_mode_changed') {
    this.streamMode = packet.stream_mode;
    if (packet.stream_mode === 'collection') {
        this.collectionStreamStartedAt = Date.now();
    }
}

// 修改: onSdFilenamesUpdated — 区分 preview 和 collection
onSdFilenamesUpdated(sd_filenames, device_names, stream_mode) {
    this.sd_filenames = { dev1: sd_filenames?.dev1 || null, dev2: sd_filenames?.dev2 || null };
    this.device_names = { dev1: device_names?.dev1 || null, dev2: device_names?.dev2 || null };
    
    if (stream_mode === 'collection') {
        this.collectionBinFilenames = { ...this.sd_filenames };
        console.log(`[realtimeEngine] Collection bin filenames: dev1=${this.collectionBinFilenames.dev1}, dev2=${this.collectionBinFilenames.dev2}`);
    } else {
        console.log(`[realtimeEngine] Preview bin filenames (not stored in H5): dev1=${this.sd_filenames.dev1}`);
    }
}
```

#### 8.1.3 saveDataToStorage 改动

```javascript
saveDataToStorage(sensorData) {
    // 只在 collection 模式下写入 H5
    if (this.streamMode !== 'collection') {
        return;  // preview 数据不写入 H5
    }
    if (this.isClosingStageFile || !this.stageFileOpen) return;
    // ... existing code ...
}
```

#### 8.1.4 openStageFile 改动

```javascript
async openStageFile(stageName, stageIndex) {
    // ... existing checks ...
    
    // 等待 collection bin filenames
    if (!this.collectionBinFilenames.dev1 && !this.collectionBinFilenames.dev2) {
        console.log('[realtimeEngine] 等待 collection bin filenames...');
        let waited = 0;
        while (waited < 3000) {  // 最多等 3 秒
            await new Promise(r => setTimeout(r, 100));
            waited += 100;
            if (this.collectionBinFilenames.dev1 || this.collectionBinFilenames.dev2) break;
        }
    }
    
    // 使用 collection bin filenames 创建 H5
    const response = await this.sendStorageCommand('create', {
        // ... existing params ...
        sd_bin_dev1: this.collectionBinFilenames.dev1,  // 确保使用 collection bin
        sd_bin_dev2: this.collectionBinFilenames.dev2,
        stream_mode: this.streamMode,  // "collection"
        collection_stream_started_at: this.collectionStreamStartedAt,
    });
}
```

### 8.2 storage_server.py 改动

#### 8.2.1 新增 H5 attrs

```python
def create_file(self, params):
    # ... existing code ...
    
    # 新增 attrs
    stream_mode = params.get("stream_mode", "unknown")
    self.f.attrs["stream_mode"] = str(stream_mode)  # "collection" | "preview" | "unknown"
    
    collection_stream_started_at = params.get("collection_stream_started_at")
    if collection_stream_started_at:
        self.f.attrs["collection_stream_started_at"] = float(collection_stream_started_at)
    
    # sd_bin_dev1/dev2 已经在现有代码中写入
    # 但增加校验：确保 stream_mode==collection 时才写入
    if stream_mode == "collection":
        if sd_bin_dev1:
            self.f.attrs["sd_bin_dev1"] = sd_bin_dev1
        if sd_bin_dev2:
            self.f.attrs["sd_bin_dev2"] = sd_bin_dev2
    else:
        # preview mode: 不写 sd_bin_dev attrs（或写 preview_bin_dev 用于 debug）
        debug_log(f"   stream_mode={stream_mode}, 不写入 sd_bin_dev attrs")
```

#### 8.2.2 close_file 新增

```python
def close_file(self, params=None):
    # ... existing code ...
    
    if stream_mode == "collection":
        self.f.attrs["collection_stream_stopped_at"] = datetime.now().isoformat()
    
    # ... existing close logic ...
```

---

## 9. H5 Schema 改动建议

### 9.1 新增根属性 (Root Attrs)

| 属性名 | 类型 | 描述 | 何时写入 | 旧 H5 兼容 |
|--------|------|------|---------|-----------|
| `stream_mode` | string | `"collection"` / `"preview"` / `"unknown"` | create_file | 旧 H5 中不存在，读取时默认 `"unknown"` |
| `stream_format_version` | int | `2`（新格式），`1`（旧格式，默认） | create_file | 旧: `1` |
| `collection_stream_started_at` | float64 | collection stream 启动时间戳 | create_file | 旧 H5 不存在 |
| `collection_stream_stopped_at` | string | collection stream 停止时间 | close_file | 旧 H5 不存在 |
| `collection_stream_generation_id` | int | stream generation 序号 | create_file | 旧 H5 不存在 |
| `sd_bin_dev1` | string | dev1 collection bin 文件名 | create_file | **已有** |
| `sd_bin_dev2` | string | dev2 collection bin 文件名 | create_file | **已有** |
| `preview_bin_dev1` | string | dev1 preview bin 文件名 (debug) | create_file | 新字段 |
| `preview_bin_dev2` | string | dev2 preview bin 文件名 (debug) | create_file | 新字段 |
| `h5_bin_mapping` | string | `"one_to_one"` / `"many_to_one"` | 由 hdf5_tool/bin_sync_tool 写入 | 新增 |

### 9.2 新增段元数据 (Per-segment Attrs)

| 属性名 | 类型 | 描述 | 何时写入 |
|--------|------|------|---------|
| `segment_bin_unique` | bool | 此 segment 是否有独享 bin | close_file |
| `segment_bin_shared_with` | string | 共享 bin 的 segment 列表 (JSON) | close_file |

### 9.3 Sync 相关 Attrs 扩展

| 属性名 | 类型 | 描述 |
|--------|------|------|
| `sync_bin_source` | string | `"h5_attrs"` / `"directory_search"` — 同步时如何找到 bin |
| `sync_preview_bin_skipped` | bool | 同步是否跳过了 preview bin（确认未误选） |

---

## 10. bin_sync_tool / hdf5_tool 兼容新旧数据的策略

### 10.1 bin_sync_tool 改动

#### 10.1.1 bin 文件查找优先级

当前逻辑（hdf5_tool.py:65-105）：从 H5 attrs 读 `sd_bin_dev{id}`，在目录中找 `{prefix}_emg.bin` 和 `{prefix}_imu.bin`。

**新策略**（优先级从高到低）：

```
1. 检查 stream_format_version attrs:
   a. version >= 2: 优先使用 sd_bin_dev{id} attrs（collection bin）
   b. version == 1 或不存在: 使用 sd_bin_dev{id} attrs（旧格式）
   
2. 如果 sd_bin_dev{id} 不存在或对应 bin 文件找不到:
   a. 在 bin_dir 中按 H5 的 frame_id 范围搜索匹配的 bin 文件
   b. ⚠️ 排除 preview bin（文件名含 "PREVIEW_" 前缀的）
   c. 如果找到唯一匹配 → 使用
   d. 如果找到多个匹配 → 警告用户选择
```

#### 10.1.2 preview bin 过滤

```python
def is_preview_bin(filename):
    """判断 bin 文件是否为 preview 流产生的"""
    return filename.startswith("PREVIEW_") or "_PREVIEW_" in filename

def find_collection_bins(bin_dir, h5_frame_id_min, h5_frame_id_max):
    """在目录中搜索 collection bin（排除 preview bin）"""
    candidates = []
    for f in os.listdir(bin_dir):
        if not (f.endswith('_emg.bin') or f.endswith('_imu.bin')):
            continue
        if is_preview_bin(f):
            continue  # skip preview bins
        # 检查 frame_id 范围是否匹配
        ...
    return candidates
```

#### 10.1.3 旧 H5 兼容策略

| H5 特征 | 同步策略 |
|---------|---------|
| `stream_format_version == 1` 且有 `sd_bin_dev1` | 直接使用 attrs 中的 bin（可能是一对多）|
| `stream_format_version == 1` 无 `sd_bin_dev` | 目录搜索，排除 preview bin（如果存在），按 frame_id 范围匹配 |
| `stream_format_version == 2` | 使用 attrs 中的 bin（一对一），校验 frame_id 范围 |
| `sync_status == "sync_failed"` | 允许重新同步 |
| `collection_status == "abnormal_interrupted"` | 仅同步有效前半段，不报错 |

#### 10.1.4 同步失败时的诊断增强

```python
# 当同步失败时，增加检查：
1. 是否误选了 preview bin？
   → 检查 bin 的 frame_id 是否从 0 开始（collection bin 应该从 0 开始）
   → 检查 bin 文件名是否含 PREVIEW_ 前缀
2. 是否多个 H5 指向同一个 bin？
   → 检查同目录下其他 H5 的 sd_bin_dev 值
3. 建议："此 H5 可能是旧格式（多 H5 共享一个长 bin），是否尝试 ADC 偏移搜索？"
```

### 10.2 hdf5_tool 改动

#### 10.2.1 展示增强

在 hdf5_tool 的 H5 查看标签页中增加：

```
文件基本信息:
  ├─ stream_mode: collection / preview / unknown
  ├─ stream_format: v2 (one-to-one) / v1 (legacy)
  ├─ collection_status: completed / manual_stopped / abnormal_interrupted
  ├─ sd_bin_dev1: S001_L_260601_120000  ← collection bin
  ├─ sd_bin_dev2: S001_R_260601_120000
  ├─ preview_bin_dev1: PREVIEW_L_260601_115500  ← preview bin (ignored by sync)
  ├─ h5_bin_mapping: one_to_one / many_to_one
  └─ sync_status: pending / synced / sync_failed

帧范围:
  ├─ emg1_frame_id: [0, 11999] (12000 frames @ 250Hz, ~48s)
  └─ emg2_frame_id: [0, 11999]

Bin 文件:
  ├─ emg1 对应: S001_L_260601_120000_emg.bin (frame_id: [0, 95999] @ 2kHz)
  ├─ imu1 对应: S001_L_260601_120000_imu.bin
  └─ 映射: one_to_one ✓

旧格式检测:
  如需多个 H5 共享同一对 bin，此处显示:
  ⚠️ 此 H5 为旧格式 — frame_id 范围 [2900, 6299] 是长 bin 的一部分
  ⚠️ 同目录其他 H5 也指向此 bin: ["S001_session1_gesture1.h5", ...]
```

#### 10.2.2 操作按钮

- **强制重新同步**：即使 `sync_status==synced` 也允许重新同步
- **指定 bin 文件**：手动选择 bin 文件（解决目录搜索失败的情况）
- **标记为旧格式**：手动设置 `h5_bin_mapping=many_to_one`（不影响同步逻辑，仅标记）

---

## 11. 分阶段实施计划

### Phase 1: 禁止连接后自动推流 + 进入采集页启动 preview + 返回首页停止 preview

**目标**：建立明确的 stream 生命周期，分离"连接"和"推流"。

**改动文件**：
- `ble_server.py`: 新增 `start_preview` / `stop_preview` API（内部复用 start_stream/stop_stream）
- `page-switch.js`: `showCollection()` 中发送 `start_preview`；`backToWelcome()` 和 `showWelcome()` 中发送 `stop_preview`
- `ble_control.js`: 新增 `startPreviewAll()` / `stopPreviewAll()` 方法

**验证标准**：
- [ ] 连接后 BLE status 中 `is_streaming=false`
- [ ] 进入采集页后 `is_streaming=true`，波形有数据
- [ ] 返回首页后 `is_streaming=false`，bin 文件已关闭
- [ ] SD 卡中生成 preview bin（文件名含时间戳）

### Phase 2: 开始采集前切 preview → collection + H5 attrs 写入 collection bin

**目标**：每个 H5（每个 session）对应一对 collection bin。

**改动文件**：
- `ble_server.py`: 新增 `start_collection` / `stop_collection` API；增加 stream_mode 状态管理
- `collection-controller.js`: `startTask()` 前发送 `stop_preview` + `start_collection`；`stopTask()` 后发送 `stop_collection` +（如在采集页）`start_preview`
- `realtimeEngine.js`: stream_mode 状态；preview 数据不写入 H5；使用 collection bin filenames 创建 H5
- `storage_server.py`: 新增 `stream_mode` attrs；校验 collection bin

**验证标准**：
- [ ] collection H5 的 `sd_bin_dev1/dev2` 指向 collection bin（非 preview bin）
- [ ] preview bin 仍然存在但不影响 H5
- [ ] 单轮采集：stop → start collection → 新 bin 文件名（含新时间戳）
- [ ] `stream_mode` attrs == `"collection"`

### Phase 3: 采集结束后 collection → preview

**目标**：采集完成后自动恢复波形预览，无需用户手动操作。

**改动文件**：
- `collection-controller.js`: `stopTask()` 和 stage完成回调中，close H5 → stop_collection → start_preview
- `page-switch.js`: 返回首页逻辑区分"从采集页返回"和"正常返回"

**验证标准**：
- [ ] 单轮采集完成后波形立即恢复
- [ ] 多个 stage 连续采集时，每个 stage 结束后波形恢复，下一 stage 开始前再切流
- [ ] 返回首页后不自动 start preview

### Phase 4: 异常中断 / 断点续采 / 全部轮次完整适配

**目标**：全部采集场景覆盖。

**改动文件**：
- `collection-controller.js`: `abortTask()` → close H5 → stop_collection；全部轮次：每 session 前切流
- `page-switch.js`: 断点续采 → start_preview → start_collection 流程
- `realtimeEngine.js`: freeze 状态下确保不写 H5

**验证标准**：
- [ ] 异常中断后 bin 正常关闭（footer 写入）
- [ ] 断点续采的 H5 的 `segment_index=2`，`sd_bin_dev1` 指向新 collection bin
- [ ] 全部轮次每个 H5 的 `sd_bin_dev1/dev2` 不同

### Phase 5: hdf5_tool / bin_sync_tool 展示与兼容

**目标**：工具能正确识别新格式并兼容旧数据。

**改动文件**：
- `bin_sync_tool.py`: preview bin 过滤；新版 H5 优先使用 attrs；旧版 H5 兼容搜索
- `hdf5_tool.py`: UI 增加 stream_mode、bin mapping 展示；同步失败诊断

**验证标准**：
- [ ] 新格式 H5: 同步直接成功，无需手动选择 bin
- [ ] 旧格式 H5: 仍可同步（可能需要手动选择）
- [ ] H5 查看页正确展示 stream_mode 和 bin mapping 标签

### Phase 6: 实机验证脚本和日志

**目标**：编写验证脚本，确保所有场景在实机上可复现。

**内容**：
- `scripts/verify_stream_lifecycle.py`: 自动测试脚本
  - 连接 → 禁止自动推流验证
  - preview → collection → preview 切换验证
  - bin 文件名正确性验证
  - H5 attrs 完整性验证
- `scripts/check_bin_files.py`: bin 文件健康检查
  - 检查是否有未关闭的 bin（缺 footer）
  - 检查 preview bin 是否被误引用
- `docs/stream_lifecycle_verification_log.md`: 验证日志模板

---

## 12. 必须实测验证清单

### 12.1 核心功能验证

| # | 测试项 | 预期结果 | 通过标准 |
|---|--------|---------|---------|
| 1 | stop/start 最小可靠间隔 | 不丢 footer、不混数据 | 连续 20 次 stop→start（间隔依次为 500ms/1s/1.5s/2s），所有 bin 文件 footer 完整、frame_id 从 0 开始 |
| 2 | 每次 start_collection 产生新 bin | SD 卡中可见独立 bin 文件对 | 3 次 start_collection → 6 个 bin（dev1 emg+imu, dev2 emg+imu） |
| 3 | preview bin 不写入 H5 attrs | H5 的 `sd_bin_dev1` ≠ preview bin 文件名 | 检查 H5 attrs，文件名不含 "PREVIEW_" |
| 4 | 单轮采集：H5 指向 collection bin | sync 成功，数据完整 | `bin_sync_tool.py` 同步通过 3 项校验 |
| 5 | 全部轮次：每个 H5 对应不同 bin | 3 个 session → 3 对 bin → 3 个 H5 各自指向不同 bin | sync 后 3 个 H5 的 `sd_bin_dev1` 各不相同 |
| 6 | 异常中断后 bin 正常关闭 | bin 文件有 valid footer | bin footer magic = `0xDDCCBBAA`，`stop_reason != 0` |
| 7 | 断点续采 H5 指向新 bin | segment=2 的 H5 有新的 `sd_bin_dev1` | 新 H5 的 bin 文件名与中断前不同 |
| 8 | 旧 H5 兼容同步 | 旧格式 H5（无 stream_mode attrs）仍可同步 | `bin_sync_tool.py` 不因缺失 attrs 而报错 |
| 9 | preview bin 不被同步工具误选 | sync 时自动排除 preview bin | preview bin 文件存在于目录中，但同步工具选择了正确的 collection bin |
| 10 | 返回首页后无残留 streaming | ESP32 `is_streaming=false` | BLE status snapshot 确认 streaming flag=0 |

### 12.2 边界条件验证

| # | 测试项 | 预期结果 |
|---|--------|---------|
| 11 | 设备未连接时进入采集页 | 不报错，不发送 start_preview |
| 12 | preview 期间断连 BLE | preview stream 自动停止，bin close |
| 13 | collection 期间断连 BLE | H5 标记 abnormal，bin close（BLE disconnect handler 触发 stop_streaming_system）|
| 14 | 快速连续点击 stop/start | 不产生状态混乱，stream_mode 正确 |
| 15 | SD 卡满时 start_collection | ESP32 拒绝 start，ble_server 返回 error，前端提示 |
| 16 | 同时连接 dev1 和 dev2 | 两个设备独立切流，各自产生 bin |
| 17 | 旧版 ESP32 固件（无 P0 改动）| 加长 delay 到 2s 后仍能工作（可能有残留数据） |

### 12.3 工具验证

| # | 测试项 | 预期结果 |
|---|--------|---------|
| 18 | hdf5_tool 展示 v2 格式 H5 | 显示 stream_mode=collection, h5_bin_mapping=one_to_one |
| 19 | hdf5_tool 展示 v1 格式 H5 | 显示 stream_format=v1, h5_bin_mapping=many_to_one（如检测到） |
| 20 | bin_sync_tool 目录中有 preview bin | 自动排除，选择正确的 collection bin |

---

## 附录 A: 文件修改清单

### A.1 ESP32 固件（如需改动）

| 文件 | 改动 | 优先级 |
|------|------|--------|
| `main/main.c` | start_streaming_system: 新增 clear SD/IMU ringbuffer | P0 |
| `main/ble_gatt.c` | 将 ble_frame_counter 改为可外部重置 | P0 |
| `main/ads1298.c` | 新增 ads_reset_raw_frame_counter() | P0 |
| `main/app_state.c` | TIMESTAMP 更新时设置 bin_path_dirty flag | P0 |
| `main/sd_storage.c` | 文件名生成逻辑中使用 bin_path_dirty | P0 |
| `main/ble_gatt.c` | 增加 BLE_STATUS_EVENT_FILE_CLOSED 事件 | P1 |

### A.2 Python 后端

| 文件 | 改动 | Phase |
|------|------|-------|
| `ble_server.py` | 新增 stream_mode, start/stop_preview, start/stop_collection | 1-2 |
| `storage_server.py` | 新增 stream_mode attrs, collection_stream attrs | 2 |

### A.3 Node.js 后端

| 文件 | 改动 | Phase |
|------|------|-------|
| `realtimeEngine.js` | stream_mode 状态, preview/collection 数据分流, openStageFile 等待逻辑 | 2-4 |

### A.4 前端

| 文件 | 改动 | Phase |
|------|------|-------|
| `public/scripts/ble_control.js` | 新增 startPreviewAll/stopPreviewAll/startCollectionAll/stopCollectionAll | 1-2 |
| `public/scripts/page-switch.js` | 进入/离开采集页的 stream 控制 | 1 |
| `public/scripts/collection-controller.js` | startTask/stopTask/abortTask/startAllSessions 中增加切流逻辑 | 2-4 |

### A.5 工具

| 文件 | 改动 | Phase |
|------|------|-------|
| `tools/bin_sync_tool.py` | preview bin 过滤, 新版 H5 优先策略, 旧版兼容 | 5 |
| `tools/hdf5_tool.py` | UI 展示 stream_mode/bin mapping, 同步失败诊断 | 5 |

### A.6 新建文件

| 文件 | 用途 | Phase |
|------|------|-------|
| `scripts/verify_stream_lifecycle.py` | 自动化验证脚本 | 6 |
| `scripts/check_bin_files.py` | bin 文件健康检查 | 6 |
| `docs/stream_lifecycle_verification_log.md` | 验证日志 | 6 |

---

## 附录 B: 关键源码引用索引

### ESP32 固件

| 功能 | 文件:行号 |
|------|----------|
| start_streaming_system() | main.c:104-157 |
| stop_streaming_system() | main.c:160-189 |
| clear_ble_ringbuf_pending() | main.c:78-102 |
| emg_data_task() — 数据生产 | ads1298.c:233-336 |
| s_raw_interrupt_counter 重置 | ads1298.c:247 |
| ble_send_task() — BLE 发送 | ble_gatt.c:688-811 |
| ble_frame_counter 重置 | ble_gatt.c:714-717 |
| BLE_CMD_STREAM_START (0xA0) | ble_gatt.c:516-531 |
| BLE_CMD_STREAM_STOP (0xA1) | ble_gatt.c:532-533 |
| BLE_CMD_TIMESTAMP (0xD0) | ble_gatt.c:489-494 |
| sd_write_task() — SD 写入 | sd_storage.c:428-648 |
| drain_ringbuf_to_file() | sd_storage.c:139-209 |
| close_session_file() | sd_storage.c:228-238 |
| ringbuf_has_pending_item() | sd_storage.c:298-314 |
| app_state_build_data_path() | app_state.c:243-256 |
| app_state_reset_stream_counters() | app_state.c:135-142 |
| sd_files_closed 状态 | app_state.c + app_common.h:238 |

### PC 端

| 功能 | 文件:行号 |
|------|----------|
| ble_server start_stream() | ble_server.py:1230-1318 |
| ble_server stop_stream() | ble_server.py:1321-1368 |
| ble_server start_all() | ble_server.py:1371-1412 |
| ble_server stop_all() | ble_server.py:1415-1435 |
| sd_filenames_updated 广播 | ble_server.py:1408-1412 |
| realtimeEngine onCollectionStart() | realtimeEngine.js:256-300 |
| realtimeEngine onCollectionStop() | realtimeEngine.js:305-322 |
| realtimeEngine onSdFilenamesUpdated() | realtimeEngine.js:374-387 |
| realtimeEngine openStageFile() | realtimeEngine.js:513-605 |
| realtimeEngine closeStageFile() | realtimeEngine.js:607-627 |
| realtimeEngine saveDataToStorage() | realtimeEngine.js:1042-1057 |
| realtimeEngine onAbnormalInterrupt() | realtimeEngine.js:341-370 |
| storage_server create_file() | storage_server.py:344-729 |
| storage_server close_file() | storage_server.py:1070-1174 |
| page-switch showCollection() | page-switch.js:202-224 |
| page-switch backToWelcome() | page-switch.js:285-302 |
| collection-controller startTask() | collection-controller.js:948-1069 |
| collection-controller stopTask() | collection-controller.js:1207-1288 |
| collection-controller abortTask() | collection-controller.js:1300-1410 |
| collection-controller startAllSessions() | collection-controller.js:1076-1118 |
| collection-controller sendToRealtimeEngine() | collection-controller.js:2473-2487 |

### 工具

| 功能 | 文件:行号 |
|------|----------|
| bin_sync_tool sync_h5_with_bin() | bin_sync_tool.py:656-1102 |
| hdf5_tool SyncWorker._find_bin_files() | hdf5_tool.py:65-105 |

---

> **文档结束**  
> 🤖 Generated with [Claude Code](https://claude.com/claude-code)  
> Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
