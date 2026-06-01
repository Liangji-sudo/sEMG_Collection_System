# ESP32 BLE / SD Write Lifecycle Audit

**日期:** 2026-06-01  
**分支:** `fix_sync`  
**调查人:** Claude (read-only audit, no code changes)  
**目标:** 确认 BLE send 与 SD/bin write 的生命周期和对应关系

---

## 1. ESP32 固件状态机

### 1.1 核心状态标志

所有状态由 `app_state_t` 中的原子标志控制（[app_common.h:230-272](wband_emg_V2/wband_emg_esp32s3_v5/main/app_common.h#L230-L272)）：

| 标志 | 含义 | 初始值 |
|------|------|--------|
| `is_streaming` | 是否正在流式传输/记录 | `false` |
| `notify_enabled` | BLE 订阅是否启用 | `false` |
| `ble_connected` | BLE 是否已连接 | `false` |
| `sd_files_closed` | SD 文件是否已关闭 | `true` |
| `sd_mounted` | SD 卡是否已挂载 | `false` |

### 1.2 状态机图

```
[上电] → BLE广播 → [BLE连接]
                       ↓
                  [连接状态]
                  BLE已连接, 未streaming
                  is_streaming=false, is_streaming=false
                       ↓
              ┌── 电脑发 0xA0 (STREAM_START) ──┐
              ↓                                  ↓
         [已订阅] notify_enabled=true       [未订阅] notify_enabled=false
              ↓                                  ↓
         start_streaming_system()            拒绝启动，发警告事件
              ↓
         [Streaming状态]
         is_streaming=true
         ├─ emg_data_task: 读ADS → 写sd_ringbuf + ble_ringbuf
         ├─ sd_write_task: 读sd_ringbuf → 写SD/bin
         └─ ble_send_task: 读ble_ringbuf → BLE notify
              ↓
         ┌─ 电脑发 0xA1 (STREAM_STOP) ─┐
         └─ BLE断开 (DISCONNECT) ──────┘
              ↓
         [停止Streaming]
         is_streaming=false
         sd_write_task: drain剩余数据, close bin, sd_files_closed=true
```

### 1.3 关键代码路径

**Streaming 启动** — [main.c:104-158](wband_emg_V2/wband_emg_esp32s3_v5/main/main.c#L104-L158):
```c
void start_streaming_system(void) {
    if (app_state_is_streaming()) return;  // 幂等
    app_state_reset_stream_counters();     // 重置 overflow counters（非 frame counter）
    app_state_set_sd_files_closed(false);  // 允许sd_write_task创建新文件
    clear_ble_ringbuf_pending();           // 清空残留BLE数据
    // ... ADS初始化 ...
    app_state_set_streaming(true);         // ← 核心：同时启用SD和BLE
}
```

**Streaming 停止** — [main.c:160-189](wband_emg_V2/wband_emg_esp32s3_v5/main/main.c#L160-L189):
```c
void stop_streaming_system(const char *reason, stream_stop_reason_t stop_reason) {
    app_state_set_streaming(false);       // 先设false
    vTaskDelay(50ms);                     // 等待pipeline排空
    ads_stop_conversion();                // 停ADS
    imu_set_power_mode(false);            // 停IMU
    // sd_write_task 会在检测到 !is_streaming 后自动关闭文件
}
```

---

## 2. BLE EMG Package 发送条件

### 2.1 发送路径

`emg_data_task` ([ads1298.c:233-336](wband_emg_V2/wband_emg_esp32s3_v5/main/ads1298.c#L233-L336)) 在 `is_streaming==true` 时，每收到一个 DRDY 中断：
1. 读 SPI → 获取原始 ADS 数据
2. 写 `sd_ringbuf_handle`（每帧）
3. 写 `ble_ringbuf_handle`（每帧原始 54 字节 SPI 数据）

`ble_send_task` ([ble_gatt.c:688-811](wband_emg_V2/wband_emg_esp32s3_v5/main/ble_gatt.c#L688-L811)) 从 `ble_ringbuf_handle` 取数据，**仅在 `is_streaming==true` 且 `notify_enabled==true` 时**打包发送 BLE notification。

### 2.2 关键发现

**结论:** BLE EMG package 的发送条件是 `is_streaming == true AND notify_enabled == true`。

- **连接后不立即发送** — 仅 BLE 连接 (`ble_connected=true`) 不会触发 streaming，`is_streaming` 仍是 `false`
- **必须收到 STREAM_START (0xA0) 命令** — 该命令调用 `start_streaming_system()` 设置 `is_streaming=true`
- **也必须先订阅 EMG characteristic** — `notify_enabled` 必须为 true（`ble_server.py` 在 `start_stream()` 中先 `start_notify`，再发 `START`）
- **左侧信号预览阶段** — 如果 preview 显示的是 BLE 实时信号，那必须已经是 streaming 状态。换句话说，**预览和采集在 ESP32 看来是同一个 streaming 状态**，两者都会写 SD

### 2.3 代码证据

```c
// ble_gatt.c:714-719 — ble_send_task 主循环
if (!app_state_is_streaming()) {
    ble_frames_buffered = 0;
    ble_frame_counter = 0;   // ← 不streaming时重置
    decimation_count = 0;
    memset(imu_packet_cache, 0, sizeof(imu_packet_cache));
    continue;                // ← 跳过，不发BLE包
}
```

---

## 3. SD/bin 写入条件

### 3.1 写入路径

`sd_write_task` ([sd_storage.c:428-648](wband_emg_V2/wband_emg_esp32s3_v5/main/sd_storage.c#L428-L648)) 是唯一写 SD 卡的任务。

**核心逻辑** — [sd_storage.c:536-646](wband_emg_V2/wband_emg_esp32s3_v5/main/sd_storage.c#L536-L646):
```c
if (app_state_is_streaming()) {
    // 创建/写入 bin 文件
    if (fn_emg[0] == '\0') {
        app_state_build_data_path("emg", fn_emg, sizeof(fn_emg));  // 生成文件名
    }
    drain_ringbuf_to_file(sd_ringbuf_handle, &f_emg, fn_emg, ...);
    // ... 也写 IMU bin ...
} else {
    // 不streaming时：drain剩余数据，然后关闭文件
    if (f_emg != NULL && !ringbuf_has_pending_item(sd_ringbuf_handle)) {
        close_session_file(&f_emg, ...);
        fn_emg[0] = '\0';
    }
    if (f_emg == NULL && f_imu == NULL) {
        app_state_set_sd_files_closed(true);
    }
}
```

### 3.2 核心发现

**SD 写入和 BLE 发送由同一个 `app_state_is_streaming()` 控制。**

- `is_streaming` 为 `true` 时，同时执行 BLE 发送和 SD 写入
- `is_streaming` 为 `false` 时，两者同时停止
- **不存在 "只发 BLE 不写 SD" 的状态** — `is_streaming` 是单一布尔值，没有单独的 "BLE mode" 或 "SD mode"
- `emg_data_task` 生产数据时同时写 `sd_ringbuf` 和 `ble_ringbuf`（[ads1298.c:284-334](wband_emg_V2/wband_emg_esp32s3_v5/main/ads1298.c#L284-L334)），不存在可选路径

### 3.3 供应商说法的验证

供应商说法："连接后只是 BLE streaming / 信号预览；电脑端点击开始采集后手环才开始写 SD/bin"

**验证结果：源码不支持此说法。** `is_streaming` 是单一标志，同时控制 BLE 发送和 SD 写入。如果电脑端点击 "开始采集" 触发了 `STREAM_START (0xA0)`，那么 ESP32 会同时开始 BLE streaming 和 SD/bin 写入。

但如果供应商的意思是 "连接后只做了 BLE 连接，没有做任何 streaming" — 那是对的：连接后 `is_streaming=false`，确实没有任何数据流。直到收到 `STREAM_START` 命令。

---

## 4. Bin 文件创建/关闭生命周期

### 4.1 文件创建时机

Bin 文件由 `sd_write_task` **延迟创建** — 不直接在 `start_streaming_system()` 中创建：

1. `start_streaming_system()` → `app_state_set_streaming(true)` → `app_state_set_sd_files_closed(false)`
2. `emg_data_task` 开始写 `sd_ringbuf`
3. `sd_write_task` 检测 `is_streaming == true`
4. 调用 `app_state_build_data_path("emg", fn_emg, ...)` 生成文件名
5. 从 ringbuf 取第一帧数据时，`drain_ringbuf_to_file` 调用 `open_session_file` 创建实际文件
6. 文件打开后立即写 header（[sd_storage.c:170-182](wband_emg_V2/wband_emg_esp32s3_v5/main/sd_storage.c#L170-L182)）

**创建时机 = 第一次数据流入 `sd_ringbuf` 且 `is_streaming==true` 时**，而非 `start_streaming_system()` 调用时。

### 4.2 文件名来源

文件名由 `app_state_build_data_path()` 生成（[app_state.c:243-256](wband_emg_V2/wband_emg_esp32s3_v5/main/app_state.c#L243-L256)）：

```c
void app_state_build_data_path(const char *suffix, char *dst, size_t dst_size) {
    if (suffix == NULL || suffix[0] == '\0') {
        snprintf(dst, dst_size, "/data/%s.bin", g_app_state.timestamp_str);
    } else {
        snprintf(dst, dst_size, "/data/%s_%s.bin", g_app_state.timestamp_str, suffix);
    }
}
```

文件名格式: `/data/{timestamp_str}_{suffix}.bin`  
例如: `/data/S001_L_260312_143025_emg.bin` 和 `/data/S001_L_260312_143025_imu.bin`

`timestamp_str` 由电脑端通过 `BLE_CMD_TIMESTAMP (0xD0)` 写入（[ble_gatt.c:489-494](wband_emg_V2/wband_emg_esp32s3_v5/main/ble_gatt.c#L489-L494)）。

**电脑端生成文件名逻辑**（[ble_server.py:1253-1278](ble_server.py#L1253-L1278)）：
```python
filename_str = f"{session_id}_{hand_label}_{now_str}"  # "S001_L_260312_143025"
filename_cmd = bytes([CMD_MAP['SET_FILENAME']]) + filename_str.encode('ascii')
await send_control_command(dev, filename_cmd)
```

### 4.3 文件关闭时机

Bin 文件在以下情况下关闭：

| 触发条件 | 代码路径 | 关闭行为 |
|----------|----------|----------|
| `stop_streaming_system()` 被调用 | `sd_write_task` 检测 `!is_streaming` → drain → close | 正常关闭+footer |
| USB MSC 连接 | `sd_write_task` USB active 分支 | 强制关闭 |
| SD 写失败 | `handle_sd_write_failure()` | 紧急关闭（无 footer？注意：`handle_sd_write_failure` 中 `fclose(*file); *file = NULL;` 不写 footer） |
| BLE 断开 | `ESP_GATTS_DISCONNECT_EVT` → `stop_streaming_system()` | 正常关闭+footer |

关闭时：
1. `append_session_footer()` 写 footer（magic + total_frames + drop counts + stop_reason）
2. `fflush()` + `fsync()` 确保落盘
3. `fclose()` 关闭文件
4. `app_state_set_sd_files_closed(true)` 标记关闭

### 4.4 关键结论

- **每次 `STREAM_START (0xA0)` 都会创建一对新的 bin 文件**（emg + imu）
- **`STREAM_STOP (0xA1)` 或 BLE 断开 都会关闭当前 bin**
- **仅连接不点击 "开始采集"**：`is_streaming=false`，不会生成任何 bin
- **在一个 streaming session 内多次点击 "开始任务"（stage）**：不会创建新 bin，所有 stage 写入同一个 bin
- **文件名由电脑端在 `STREAM_START` 前通过 `TIMESTAMP (0xD0)` 命令设置**，ESP32 不自行生成文件名

---

## 5. BLE packet_counter 生命周期

### 5.1 两个独立的计数器

ESP32 固件中有两个完全独立的帧计数器：

| 计数器 | 位置 | 含义 | 重置条件 |
|--------|------|------|----------|
| `s_raw_interrupt_counter` | [ads1298.c:13](wband_emg_V2/wband_emg_esp32s3_v5/main/ads1298.c#L13) | SD bin 帧号（2kHz） | `!is_streaming` 时重置为 0，或静态初始化=0 |
| `ble_frame_counter` | [ble_gatt.c:699](wband_emg_V2/wband_emg_esp32s3_v5/main/ble_gatt.c#L699) | BLE 包号（~27.8Hz，每包含9帧） | `!is_streaming` 时重置为 0，或静态初始化=0 |

### 5.2 重置行为细节

`ble_frame_counter` 的 reset 逻辑（[ble_gatt.c:714-716](wband_emg_V2/wband_emg_esp32s3_v5/main/ble_gatt.c#L714-L716)）：
```c
if (!app_state_is_streaming()) {
    ble_frame_counter = 0;
    // ...
    continue;
}
```

`ble_send_task` 在收到 ringbuffer 数据后才检查 `is_streaming`。在 `portMAX_DELAY` 阻塞期间无法重置计数器。

**实际行为：**
- 如果 streaming 停止后 ringbuffer 中没有残留数据，`ble_send_task` 阻塞在 `xRingbufferReceive`，无法到达 reset 代码
- `start_streaming_system()` 调用 `clear_ble_ringbuf_pending()` 清空 ringbuffer
- 新 streaming 开始时，`ble_send_task` 从 `xRingbufferReceive` 解阻塞，看到 `is_streaming==true`，**跳过 reset**
- 因此 `ble_frame_counter` **可能在 streaming 重启时不重置为 0**

**但如果在停止 streaming 后 ringbuffer 有残留**（`emg_data_task` 在 stop 前已产生数据），`ble_send_task` 会先收到数据，检测 `!is_streaming`，重置计数器 → 这种情况下会重置为 0。

**结论：`ble_frame_counter` 在 streaming restart 时是否重置为 0 存在竞态条件，不可靠依赖。**

### 5.3 L015 实测数据验证

```
# H5 文件的 BLE frame_id（= ble_frame_counter 值）：
session1: emg1 [2900, 6573], emg2 [2292, 7670]
session2: emg1 [7572, 12836], emg2 [8687, 14090]
session3: emg1 [13850, 19242], emg2 [15106, 20517]
session4: emg1 [20265, 25513], emg2 [21533, 26943]
session5: emg1 [26438, 31636], emg2 [27959, 33366]
session6: emg1 [32163, 36227], emg2 [34389, 39801]

# 对应的 bin 文件 L015_L_260527_153015_emg.bin：
SD frame_id 范围: [101896, 2882917]（2,882,603 帧 ≈ 24分钟@2kHz）
```

数据清晰显示：
1. **BLE packet_counter 跨 session 连续递增** — session1 emg1 结束于 6573，session2 开始于 7572
2. **跨 session 有 gap** — 6573→7572 的 gap (~999) 对应 session 间休息/过渡期（ESP32 仍在 streaming，但 H5 未记录）
3. **SD bin 的 frame_id 是一个完全不同的计数器**，从远大于 BLE frame_id 的值开始（101896 vs BLE 的 2900），证明这是独立计数器
4. **两个计数器之间没有直接的数学关系** — BLE 有降采样（250Hz，每9帧1包），SD 是全速率（2kHz）

---

## 6. 电脑端 Start/Stop 流程

### 6.1 完整命令链路

```
用户操作                →  前端                         →  ble_server.py (8764)    →  ESP32
────────────────────────────────────────────────────────
点击"连接"             →  ble_control.js connect1/2   →  BleakClient.connect()   →  BLE connect
                                                          + BLE订阅(subscribe)
                                                          + 发送配置(rate/gain)
                                                          + 发送STATUS_CHAR订阅
                        
进入采集界面           →  collection-controller        →  (无 BLE 命令)
  (左侧预览)             init()                          →  左栏实时预览已通过
                                                          ble_server:8766 接收
                                                          BLE data

点击"开始采集"         →  ble_control.js start_all     →  ble_server.start_all()  →  0xD0 TIMESTAMP(文件名)
  (主工具栏)                                                → start_stream(dev1)     →  0xA0 STREAM_START
                                                           → start_stream(dev2)     
                                                           → broadcast sd_filenames_updated
                        
点击"开始任务"         →  collection-controller        →  realtimeEngine
  (collection页面)       startTask()                    →  collection_start
                                                          → (NOT send BLE START)
                        →  for each stage:
                           stage_start                  →  realtimeEngine
                                                          →  create H5 file
                                                          →  开始 saveDataToStorage()

点击"停止任务"         →  collection-controller        →  realtimeEngine
                           stopTask()                   →  collection_stop
                                                          →  closeStageFile()

点击"停止采集"         →  ble_control.js stop_all      →  ble_server.stop_all()   →  0xA1 STREAM_STOP
  (主工具栏)                                               → stop_stream(dev1)
                                                           → stop_stream(dev2)

点击"断开"             →  ble_control.js disconnect1/2 →  disconnect_device()     →  BLE disconnect
                                                          → 先 stop_stream 再 disconnect
                                                          (ESP32 侧: DISCONNECT → stop_streaming_system)
```

### 6.2 关键发现

1. **"开始采集" 和 "开始任务" 是两个独立操作**
   - "开始采集" → `ble_server.start_all()` → 发送 `0xD0` + `0xA0` → ESP32 开始 streaming + SD 写
   - "开始任务" → `realtimeEngine.collection_start` → 开始写 H5 文件
   - **两者之间没有互锁**：可以先点 "开始采集" 再点 "开始任务"，也可以反过来

2. **一个 STREAM_START 周期内可能有多个 H5 stage 文件**
   - ESP32 streaming 是全局状态，一旦开始就一直运行
   - Collection controller 的多个 stage/session 都在同一个 streaming 周期内
   - 每个 stage 创建独立 H5，但 bin 只有一对

3. **`sd_filenames` 只在 `start_all` 时更新**
   - `ble_server.py start_all()` → `broadcast_event('sd_filenames_updated')`
   - `realtimeEngine.onSdFilenamesUpdated()` 接收并保存
   - **如果 `start_all` 只调用一次，`sd_filenames` 永远不会更新**
   - 如果 streaming 被停止后重新开始（新 bin），`sd_filenames` 不会自动更新为新的 bin 文件名

### 6.3 H5 attrs 中 bin 文件名来源

流程：
1. `ble_server.py start_all()` → 生成 `filename_str = f"{session_id}_{hand_label}_{now_str}"`
2. 保存到 `dev.sd_filename`（[ble_server.py:1278](ble_server.py#L1278)）
3. 通过 `start_all` 返回 `sd_filenames` 和 `device_names`（[ble_server.py:1386-1399](ble_server.py#L1386-L1399)）
4. `realtimeEngine.onSdFilenamesUpdated()` 接收（[realtimeEngine.js:374-386](realtimeEngine.js#L374-L386)）
5. `realtimeEngine.openStageFile()` 将 `sd_bin_dev1`/`sd_bin_dev2` 传递给 `storage_server.create_file()`（[realtimeEngine.js:578-579](realtimeEngine.js#L578-L579)）
6. `storage_server.create_file()` 写入 H5 attrs `sd_bin_dev1`/`sd_bin_dev2`（[storage_server.py:435-440](storage_server.py#L435-L440)）

**结论：`sd_bin_dev1`/`sd_bin_dev2` 是 `start_all` 时生成的文件名，保存在 realtimeEngine 的内存中。同一 streaming 周期内所有 H5 共享相同的值。**

---

## 7. H5 与 Bin 的真实对应关系

### 7.1 当前架构的实际行为

```
                    ┌──  H5 session1 (sd_bin_dev1: L015_L_153015)
                    ├──  H5 session2 (sd_bin_dev1: L015_L_153015)
ESP32 START (0xA0)  ├──  H5 session3 (sd_bin_dev1: L015_L_153015)
    ↓               ├──  H5 session4 (sd_bin_dev1: L015_L_153015)
  SD写入开始        ├──  H5 session5 (sd_bin_dev1: L015_L_153015)
    ↓               └──  H5 session6 (sd_bin_dev1: L015_L_153015)
  ONE bin pair
  (emg + imu)            ↑
                        所有H5指向同一个bin
                    
ESP32 STOP (0xA1) → bin关闭+footer
```

**结论：在当前代码下，一个 bin 对应对多个 H5 是正常行为。**  
**"每个 H5 对应独立的 bin pair" 仅在每次 stage 之间都 STOP+START streaming 时才成立。**

### 7.2 L015 数据对应关系

```
L015_bin 文件 (8个 = 4对):
├── L015_L/R_260527_151728 (最早的一对)
├── L015_L/R_260527_152916/152917 (第二对)
├── L015_L/R_260527_153015 (第三对) ← 所有6个H5都指向这对
└── L015_L/R_260527_155430 (第四对)

L015_h5 文件 (6个):
├── session1_153138 → sd_bin_dev1: L015_L_260527_153015
├── session2_153529 → sd_bin_dev1: L015_L_260527_153015
├── session3_153920 → sd_bin_dev1: L015_L_260527_153015
├── session4_154312 → sd_bin_dev1: L015_L_260527_153015
├── session5_154703 → sd_bin_dev1: L015_L_260527_153015
└── session6_155055 → sd_bin_dev1: L015_L_260527_153015
```

**session6 的 H5 时间戳是 15:50:55，bin 的 `_155430` 时间戳是 15:54:30。session6 理应对应 `_155430` bin，但 H5 attrs 仍记录为 `_153015`。这是因为 `sd_filenames` 从未更新 — `start_all` 只在最初调用过一次。**

---

## 8. 对旧数据的兼容策略

### 8.1 情况A：新数据（一个 H5 对一个 bin）

**条件：** 每个 stage 开始前都执行完整的 stop+start streaming（即 `ble_server.py stop_all → start_all`）

**同步策略：** 直接使用 H5 attrs 中的 `sd_bin_dev1`/`sd_bin_dev2` 找到对应 bin 文件，按 BLE frame_id → SD frame_id 映射同步。

**优点：** 简单直接，bin 完全覆盖 H5 的 BLE frame_id 范围。

### 8.2 情况B：旧数据（一个 bin 对多个 H5）

**条件：** 一次 `start_all` 后连续采集多个 stage/session（当前实际行为）

**同步策略：** 同样可以使用 H5 attrs 中的 bin 文件名。因为 `bin_sync_tool` 的工作方式是：
1. 读取 H5 中的 `frame_id` 范围（如 session3 emg1: [13850, 19242]）
2. 计算对应的 SD frame_id 范围（[13850×8, 19242×8+7] = [110800, 153943]）
3. 从 bin 中提取该范围的帧
4. 只要 bin 包含该范围的帧，同步就成功

**前提条件：**
- bin 文件确实包含该 H5 的 frame_id 范围的数据
- BLE frame_id → SD frame_id 映射关系正确（`SD = BLE × 8 + j`）

### 8.3 混合情况

如果在一个 streaming session 内采集了部分 H5，然后 stop+restart streaming 继续采集更多 H5：
- 早期 H5 指向 bin1
- 后期 H5 指向 bin2
- 如果 `sd_filenames` 没有更新（当前 bug），后期 H5 仍指向 bin1，但它们的 frame_id 可能在 bin2 中

**当前 L015 数据可能存在这个问题。** session6 可能是这种情况。

---

## 9. 当前同步工具分析

### 9.1 bin_sync_tool.py 工作原理

`sync_h5_with_bin()`（[bin_sync_tool.py:656-1102](tools/bin_sync_tool.py#L656-L1102)）：
1. 解析 EMG bin 文件 → 建立 `{frame_id: channel_data}` 字典
2. 读取 H5 的 `emg{device_id}_250hz_adc` 数据集 → 获取 BLE frame_ids
3. 映射：`SD frame_id = BLE frame_id × 8 + j`（j = 0..7）
4. 从 bin 字典中查找每个 `SD frame_id`，提取对应 channel 数据
5. 写入 H5 的 `emg{device_id}_2khz_adc` 数据集
6. 类似处理 IMU 数据

### 9.2 关键参数

- `DOWNSAMPLE_RATIO = 8` — BLE 250Hz → SD 2kHz
- `channel_map` — 默认 V2，将物理通道顺序映射到显示顺序
- 校验阈值：`adc_match_threshold = 0.95`，`min_coverage_ratio = 0.95`

### 9.3 当前工具对两种情况的兼容性

**情况A（一个H5对一个bin）：** ✅ 完全支持  
**情况B（一个bin对多个H5）：** ✅ 技术上支持，因为每个H5有自己的frame_id范围

但存在以下风险：
1. **ADC校验可能失败** — 如果 `sd_bin_dev1` 指向的 bin 不包含该 H5 的 frame_id 范围的数据（如 L015 session6）
2. **frame_id 覆盖校验可能失败** — 如果 BLE frame_id 和 SD frame_id 不对齐
3. **SD frame_id 计数器可能未重置** — 导致 bin 中的 frame_id 从非零值开始，而同步工具默认从 BLE frame_id × 8 开始查找

### 9.4 发现的 L015 数据异常

- **session1 sync_status = sync_failed** — 验证未通过
- emg1: 32652 frames, frame_id [2900, 6573]  
  预期 SD 帧范围: [23200, 52591]  
  bin 的 SD 帧范围: [101896, 2882917]  
  ✅ SD 帧 [23200, 52591] 在 bin 范围内
- emg2: 48339 frames, frame_id [2292, 7670]  
  预期 SD 帧范围: [18336, 61367]  
  ✅ SD 帧 [18336, 61367] 在 bin 范围内

**但 session1 sync_failed！** 这可能是 ADC 校验失败 — BLE 数据与 bin 中对应帧的数据不匹配。可能原因：
1. channel_map 不正确
2. 24-bit vs 16-bit 解析差异
3. BLE 降采样锚点帧选择错误（当前使用最后一帧 j=DOWNSAMPLE_RATIO-1=7）

### 9.5 是否需要调整

**不需要调整核心同步逻辑**（第一阶段不改代码）。当前设计 "每个 H5 使用自己的 frame_id 范围 + attrs 中的 bin 文件名" 已经覆盖了两种场景。

需要关注的是 **异常处理**：
1. 当 bin 不包含 H5 的 frame_id 范围时，应给出明确错误信息（当前校验已做）
2. 当 `sd_bin_dev1/dev2` 不存在时，应有回退策略（当前校验已做）
3. `sd_filenames` 应该在每次 `start_all` 时更新，当前流程依赖 `start_all` 广播 `sd_filenames_updated` 事件

---

## 10. 必须实测验证的问题

以下问题无法仅通过源码分析回答，需要在实际硬件上测试：

| # | 问题 | 优先级 | 验证方法 |
|---|------|--------|----------|
| 1 | **预览阶段是否生成 bin？** | 中 | 连接手环，观察 SD 卡是否出现 bin 文件（不点开始采集） |
| 2 | **开始采集后 packet_counter 是否从 0 开始？** | 高 | 重启 ESP32 → 点开始采集 → 检查 BLE 包的第一个 packet_counter |
| 3 | **连续两次 stop+start 后 packet_counter 是否重置？** | 高 | 一次采集 → 停止 → 再次采集 → 检查第一个包的 packet_counter |
| 4 | **SD bin 的 s_raw_interrupt_counter 是否在每次 START 时重置？** | 高 | 检查连续两个 bin 文件的第一个 frame_id |
| 5 | **BLE frame_id → SD frame_id 映射是否正确（SD = BLE×8+7）？** | 高 | 采集一小段数据（如10秒），比对 BLE 250Hz 数据与 bin 中对应 SD 帧 |
| 6 | **stop_all 后 ESP32 是否可靠关闭 bin 文件？** | 中 | 点停止采集 → 检查 bin 是否有 footer（magic 0xDDCCBBAA） |
| 7 | **BLE 断开是否可靠关闭 bin 文件？** | 中 | 不点停止采集，直接断开 BLE → 检查 bin 状态 |
| 8 | **`sd_filenames_updated` 事件是否在每次 start_all 时都触发？** | 高 | 多次 start_all → 检查 realtimeEngine 日志中的 `sd_filenames` |
| 9 | **多个 session 后 bin 是否覆盖所有 session 的数据？** | 高 | 采集 3 个 session（各3分钟），检查 bin 的帧数是否 ≈ 3×3×60×2000 = 1,080,000 |
| 10 | **`s_raw_interrupt_counter` 在 ADS watchdog recovery 后是否继续？** | 低 | 触发 ADS watchdog（拔掉 SPI 线再插回），检查 SD bin 中 frame_id 的变化 |

---

## 11. 总结

### 核心结论

1. **`is_streaming` 是单一控制标志**，同时控制 BLE 发送和 SD 写入，不存在 "只发 BLE 不写 SD" 的状态
2. **每次 `STREAM_START (0xA0)` 创建一个 bin pair**，文件名由电脑端通过 `TIMESTAMP (0xD0)` 命令设置
3. **在同一 streaming session 内的多个 stage/session 共享同一个 bin** — 这是当前架构的预期行为
4. **BLE packet_counter 的 reset 行为不可靠** — 依赖竞态条件，不应假设每次 START 都从 0 开始
5. **SD frame_id 和 BLE frame_id 是独立计数器** — 没有线性数学关系，仅通过降采样锚点关联
6. **H5 attrs 中的 `sd_bin_dev1/dev2` 来自 `start_all` 时生成的文件名**，同一 streaming session 内不变

### 对同步工具的建议

- **新数据同步**：使用 H5 attrs 中的 bin 文件名 + 该 H5 的 frame_id 范围，一 H5 对一 bin（如果每次 stage 都 stop+start）
- **旧数据同步**：同样使用 H5 attrs 中的 bin 文件名 + frame_id 范围，一 bin 对多 H5（提取对应子范围）
- **当前工具已支持两种场景**，无需改变核心逻辑
- 但需要修复 `sd_filenames` 更新问题（如果中间有 stop+restart 导致 bin 文件名变化）

### 对采集流程的建议

如果要实现 "一个 stage 对应一个 bin"，需要在每个 stage 之间执行完整的 `stop_all → start_all` 流程。这需要修改 collection-controller 在每个 stage 开始前：
1. 发送 `stop_all` → 停止 ESP32 streaming → 关闭当前 bin
2. 发送 `start_all` → 重新设置文件名 → 启动新 streaming → 创建新 bin

**第一阶段不修改代码**，仅在本文档中记录发现。

---

## 附录 A: 关键文件索引

| 文件 | 关键行 | 内容 |
|------|--------|------|
| [ads1298.c:233-336](wband_emg_V2/wband_emg_esp32s3_v5/main/ads1298.c#L233-L336) | `emg_data_task()` | ESP32 数据生产：读 ADS → 写 sd_ringbuf + ble_ringbuf |
| [ads1298.c:11-15](wband_emg_V2/wband_emg_esp32s3_v5/main/ads1298.c#L11-L15) | 文件域变量 | `s_raw_interrupt_counter` — SD 帧号计数器 |
| [ads1298.c:247-249](wband_emg_V2/wband_emg_esp32s3_v5/main/ads1298.c#L247-L249) | counter reset | SD 帧号计数器仅在 `!is_streaming` 时重置 |
| [ble_gatt.c:688-811](wband_emg_V2/wband_emg_esp32s3_v5/main/ble_gatt.c#L688-L811) | `ble_send_task()` | BLE 数据发送：读 ble_ringbuf → 打包 → notify |
| [ble_gatt.c:699,714-716](wband_emg_V2/wband_emg_esp32s3_v5/main/ble_gatt.c#L699) | counter reset | `ble_frame_counter` 重置逻辑 |
| [ble_gatt.c:516-531](wband_emg_V2/wband_emg_esp32s3_v5/main/ble_gatt.c#L516-L531) | BLE_CMD_STREAM_START | 处理 `0xA0` 命令，调用 `start_streaming_system()` |
| [ble_gatt.c:489-494](wband_emg_V2/wband_emg_esp32s3_v5/main/ble_gatt.c#L489-L494) | BLE_CMD_TIMESTAMP | 处理 `0xD0` 命令，设置 bin 文件名前缀 |
| [main.c:104-158](wband_emg_V2/wband_emg_esp32s3_v5/main/main.c#L104-L158) | `start_streaming_system()` | Streaming 启动：复位 ADS → 设 `is_streaming=true` |
| [main.c:160-189](wband_emg_V2/wband_emg_esp32s3_v5/main/main.c#L160-L189) | `stop_streaming_system()` | Streaming 停止：设 `is_streaming=false` → 停 ADS |
| [sd_storage.c:428-648](wband_emg_V2/wband_emg_esp32s3_v5/main/sd_storage.c#L428-L648) | `sd_write_task()` | SD 写任务：读 sd_ringbuf → 创建文件 → 写 header → 写帧 → 写 footer → 关闭 |
| [sd_storage.c:536-646](wband_emg_V2/wband_emg_esp32s3_v5/main/sd_storage.c#L536-L646) | streaming 分支 | `is_streaming` 控制写入/关闭 |
| [app_state.c:243-256](wband_emg_V2/wband_emg_esp32s3_v5/main/app_state.c#L243-L256) | `app_state_build_data_path()` | Bin 文件路径生成逻辑 |
| [ble_server.py:1230-1318](ble_server.py#L1230-L1318) | `start_stream()` | 电脑端开始采集：发 TIMESTAMP → 发 START |
| [ble_server.py:1321-1368](ble_server.py#L1321-L1368) | `stop_stream()` | 电脑端停止采集：发 STOP → 取消订阅 |
| [ble_server.py:1371-1412](ble_server.py#L1371-L1412) | `start_all()` | 双设备同时开始，广播 sd_filenames |
| [realtimeEngine.js:374-386](realtimeEngine.js#L374-L386) | `onSdFilenamesUpdated()` | 接收并保存 sd_filenames |
| [realtimeEngine.js:513-599](realtimeEngine.js#L513-L599) | `openStageFile()` | 创建 H5，传入 sd_bin_dev1/dev2 |
| [storage_server.py:344-729](storage_server.py#L344-L729) | `create_file()` | 创建 H5 并写入 attrs（含 sd_bin_dev1/dev2） |
| [bin_sync_tool.py:656-1102](tools/bin_sync_tool.py#L656-L1102) | `sync_h5_with_bin()` | 核心同步逻辑：H5 frame_id → SD frame_id → bin 提取 |

## 附录 B: L015 数据明细

```
# Bin 文件:
L015_L_260527_151728_emg.bin   ← 最早的一对 (15:17:28)
L015_L_260527_152916_emg.bin   ← 第二对 (15:29:16)
L015_L_260527_153015_emg.bin   ← 第三对 (15:30:15), 所有H5引用这对, frame_id=[101896, 2882917], ~2.88M帧@2kHz≈24分钟
L015_L_260527_155430_emg.bin   ← 第四对 (15:54:30)

# H5 文件 (全部引用 L015_L_260527_153015):
session1_153138: emg1 [2900,6573] 32652帧, emg2 [2292,7670] 48339帧 → sync_failed
session2_153529: emg1 [7572,12836] 46692帧, emg2 [8687,14090] 48564帧 → synced
session3_153920: emg1 [13850,19242] 48033帧, emg2 [15106,20517] 48636帧 → synced
session4_154312: emg1 [20265,25513] 46575帧, emg2 [21533,26943] 48627帧 → synced
session5_154703: emg1 [26438,31636] 46044帧, emg2 [27959,33366] 48600帧 → synced
session6_155055: emg1 [32163,36227] 36090帧, emg2 [34389,39801] 48645帧 → synced

# 关键观察:
- BLE frame_id 跨 session 连续递增（session1→6 形成连续区间）
- 每个 session 之间有 gap (~999帧)，对应休息/过渡期
- dev1 和 dev2 的 frame_id 范围不同（不同设备，独立计数器）
- session1 sync_failed（需进一步诊断原因）
- session6 可能横跨两个 bin（_153015 和 _155430），但 H5 只记录了 _153015
```
