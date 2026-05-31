# 数据接收与 bin 同步链路审计报告

**日期**: 2026-05-30  
**分支**: feat_band_V3  
**审计范围**: ESP32 固件 → ble_server.py → realtimeEngine.js → storage_server.py → bin_sync_tool.py / hdf5_tool.py  
**审计类型**: 完整链路只读审计（不改代码）

---

## 1. 总体结论

| 维度 | 结论 |
|------|------|
| BLE frame_id 修正 | ✅ **已正确** — ble_server.py 已完成 packet_counter × fpkt + i |
| sd_frame_id 公式 | ✅ **正确** — frame_id × 8 + 7 与固件降采样策略一致 |
| frame_id 连续性防御 | ✅ **已就位** — bin_sync_tool.py gap/duplicate 检测到位 |
| 同步核心逻辑 | ✅ **正确** — 按 frame_id × 8 范围从 bin 读取 SD 帧 |
| **通道顺序一致性** | ⚠️ **发现 bug** — H5 存储映射后顺序，bin 为物理顺序，ADC 校验将误判 |
| 丢包检测 | ✅ **已修正** — 基于 packet_counter 连续性，每设备独立 |
| IMU 同步 | ⚠️ **需验证** — EMG:IMU 帧号比 20:1 假设双方计数器严格同步 |
| sync_status 可靠性 | ✅ **可靠** — 防御校验通过才写 synced |

**总体**: 链路核心修复已就位，无阻塞性问题。发现 2 个需要修复的问题（1 个高风险、1 个中风险）和 1 个建议改进项。

---

## 2. 已阅读文件列表

### 固件层
| 文件 | 关键内容 |
|------|----------|
| [ble_gatt.c](../wband_emg_V2/wband_emg_esp32s3_v5/main/ble_gatt.c:695-811) | BLE 包构建、ble_frame_counter 递增逻辑、拥塞丢弃 |
| [ads1298.c](../wband_emg_V2/wband_emg_esp32s3_v5/main/ads1298.c:13,247,280-336) | s_raw_interrupt_counter 声明、重置、SD 帧写入 |
| [app_common.h](../wband_emg_V2/wband_emg_esp32s3_v5/main/app_common.h:112-126) | 常量：NUM_CHIPS=2, BLE_NOTIFY_FRAMES_PER_PACKET=9, SPI_READ_LEN=54 |
| [sd_storage.c](../wband_emg_V2/wband_emg_esp32s3_v5/main/sd_storage.c:128-226) | bin 文件头/尾格式、写入逻辑 |

### Python 服务层
| 文件 | 关键内容 |
|------|----------|
| [ble_server.py](../ble_server.py:349-363,540-664,695-745) | DeviceState、parse_packet()、丢包检测 |
| [storage_server.py](../storage_server.py:54-106,406-553,692-756) | dtype 定义、数据集创建、_append_emg() sd_frame_id 计算 |
| [bin_sync_tool.py](../tools/bin_sync_tool.py:74-355,560-710) | VALIDATION_CONFIG、3 个校验函数、sync_h5_with_bin() 流程 |

### 前端转发层
| 文件 | 关键内容 |
|------|----------|
| [realtimeEngine.js](../realtimeEngine.js:750-863) | frame_ids 提取与转发、多设备处理 |

### 工具层
| 文件 | 关键内容 |
|------|----------|
| [hdf5_tool.py](../tools/hdf5_tool.py:286-371,821-1027) | sync_status 显示、frame_id/sd_frame_id 动态检测 |

---

## 3. 数据链路图

```
ESP32 固件 (2kHz DRDY ISR)
│
├─ SD 写任务 (sd_storage.c)
│  ├─ [文件头 126B] → [帧数据 52B]×N → [文件尾 32B]
│  ├─ frame_id: s_raw_interrupt_counter (连续 +1, 2kHz)
│  ├─ 通道顺序: chip1[0..7] + chip2[0..7] (物理顺序)
│  └─ 输出: {timestamp}_emg.bin, {timestamp}_imu.bin
│
├─ BLE 发送任务 (ble_gatt.c)
│  ├─ 降采样 8:1 (2kHz → 250Hz)
│  ├─ 每 9 帧打包 1 个 BLE 包
│  ├─ 包头: ble_frame_counter (包计数器, 发包/丢弃均 +1)
│  ├─ 通道顺序: chip1[0..7] + chip2[0..7] (物理顺序)
│  └─ 拥塞/低堆内存时丢弃包但仍 +1
│
▼
ble_server.py (Python BLE 接收)
│  ├─ packet_counter = data[0:4] (读包号)
│  ├─ start_frame = packet_counter × fpkt (计算真实帧起始)
│  ├─ frame_ids = [start_frame + i for i in range(fpkt)]
│  ├─ 通道映射: emg_raw_mapped → 按 channel_map 重排
│  ├─ 丢包检测: last_packet_counter 连续性检查
│  └─ WebSocket 发出: {f, packet_counter, n, frame_ids, raw, uv, imu, s, ...}
│
▼
realtimeEngine.js (Node 转发)
│  ├─ dev1.frame_ids / dev2.frame_ids 原样提取
│  ├─ dev1.raw / dev2.raw 原样提取 (已映射)
│  ├─ 多设备: dev1/dev2 独立处理，不会串
│  └─ saveDataToStorage({emg1_frame_ids, emg1, ...})
│
▼
storage_server.py (H5 存储)
│  ├─ dataset: emg{1,2}_250hz_adc (EMG_250HZ_ADC_DTYPE)
│  ├─ 字段: channels(16×i4), frame_id(u4), sd_frame_id(u4), time(f8)
│  ├─ sd_frame_id = frame_id × 8 + 7
│  ├─ channels 存储的是**映射后**顺序 (raw ADC from realtimeEngine)
│  ├─ sync_status: 初始 "pending"
│  └─ dataset: emg{1,2}_2khz_adc (初始空, 等 sync)
│
▼
bin_sync_tool.py (离线同步)
│  ├─ 读取 H5 250Hz frame_ids
│  ├─ 3 步防御校验: frame_id 健康 + SD 覆盖率 + ADC 一致性
│  ├─ 校验失败 → sync_failed (不写 2kHz 数据)
│  ├─ 校验通过 → 按 frame_id×8 范围从 bin 读取 SD 帧
│  ├─ 构建 2kHz 数据写入 emg{1,2}_2khz_adc
│  └─ sync_status → "synced"
│
▼
hdf5_tool.py (查看工具)
   ├─ 动态检测 frame_id / sd_frame_id 字段
   ├─ sync_status 颜色: synced=绿, pending=橙, 其他=灰
   └─ sync_failed 无专用颜色 (显示为灰色)
```

---

## 4. ESP32 SD/bin 格式 (已验证)

| 属性 | EMG bin | IMU bin |
|------|---------|---------|
| Magic Word | 0xAABBCCDD | 0xBBCCDDEE |
| 文件头大小 | 126 字节 | 126 字节 |
| 文件头结构 | `emg_file_header_t` (magic, sample_rate, gain_index, bit_depth, imu_enabled, timestamp[32], reserved[85]) | 同左 |
| 文件尾 | 32 字节 (magic 0xDDCCBBAA, total_frames, drop counts, stop_reason) | 同左 (magic 0xEEDDCCBB) |
| 每帧大小 | 52 字节 (4B frame_id + 48B data) | 40 字节 (4B frame_id + 36B data) |
| frame_id 来源 | `s_raw_interrupt_counter` (static uint32_t, 初始 0) | IMU 自有 frame counter |
| frame_id 递增 | 每次 DRDY (2kHz) +1，流开始时重置为 0 | 每次 IMU 采样 (100Hz) +1 |
| 通道顺序 | chip1[0..7] → chip2[0..7] (物理顺序) | Acc+Gyro 6×int16 + Mag 3×int16 |
| 24bit 模式 | 直接 memcpy 3 字节/通道，大端序 | N/A |
| 16bit 模式 | 24→16 移位转换 (shift=4 默认) | N/A |

---

## 5. ESP32 BLE 包格式 (已验证)

| 属性 | 值 |
|------|-----|
| 包头 4 字节 | `ble_frame_counter` (uint32 LE) — **包计数器，非帧号** |
| 每包帧数 | `BLE_NOTIFY_FRAMES_PER_PACKET = 9` |
| 每帧字节数 | 48 (16 通道 × 3 字节) |
| 包体布局 | 4B 包头 + 9×48B EMG + (可选) N×18B IMU |
| 通道顺序 | chip1[0..7] → chip2[0..7] (物理顺序，大端 24bit) |
| counter 递增时机 | 成功发送: +1; 拥塞丢弃: +1; 低堆丢弃: +1 |
| counter 重置时机 | 流停止时重置为 0 (ble_gatt.c:716) |
| 降采样策略 | decimation_ratio=8 → 每 8 个 2kHz 帧取 1 个 250Hz 帧 |

**关键确认**: `ble_frame_counter` 是包计数器，发包/丢弃均 +1。丢失一个 BLE 包 = 丢失 9 个 250Hz 帧 = 丢失 72 个 2kHz SD 帧。

---

## 6. ble_server.py 解析结论

### 6.1 frame_id 生成 (✅ 已修正)

```python
# 修正后 (ble_server.py:574-577)
packet_counter = struct.unpack('<I', data[0:4])[0]
start_frame = packet_counter * fpkt       # 包号 × 每包帧数
frame_ids = [start_frame + i for i in range(fpkt)]
```

| 包号 | frame_ids | 与前包重叠 |
|------|-----------|-----------|
| 0 | [0..8] | — |
| 1 | [9..17] | ✅ 无重叠 |
| 2 | [18..26] | ✅ 无重叠 |

### 6.2 丢包检测 (✅ 已修正)

```python
# ble_server.py:619-628
if dev.last_packet_counter >= 0:
    expected_packet = dev.last_packet_counter + 1
    if packet_counter > expected_packet:
        lost_packets = packet_counter - expected_packet
        dev.lost_frames += lost_packets * fpkt
```

- 每设备独立跟踪 `last_packet_counter`
- 丢失 1 包 → lost_frames += 9
- 丢包时有日志输出

### 6.3 通道映射 (⚠️ 影响下游)

```python
# ble_server.py:597-604
emg_raw_mapped = []
for row_raw in emg_raw:
    mapped_raw = [row_raw[i - 1] for i in dev.channel_map]  # 1-indexed
```

- V2 channel_map: `[15, 16, 14, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]`
- V1 channel_map: `[14, 15, 16, 3, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]`
- `parsed['raw']` = **映射后** 的数据 → 存入 H5 的 channels 也是映射后顺序
- bin 文件中的通道是**物理顺序**

### 6.4 时间戳 (✅ 合理)

每个 BLE 帧的时间戳按 1/250s 间隔倒推生成，最末帧用接收时间戳。对实时显示足够，对离线同步无影响（同步使用 frame_id 而非时间戳）。

---

## 7. storage_server.py 保存结论

### 7.1 数据集结构 (✅ 正确)

| 数据集 | dtype | 状态 |
|--------|-------|------|
| `emg{1,2}_250hz_adc` | EMG_250HZ_ADC_DTYPE | 采集时实时写入 |
| `emg{1,2}_2khz_adc` | EMG_2KHZ_ADC_DTYPE | 初始空，sync 后填充 |
| `imu{1,2}{a,b}_ble` | IMU_BLE_DTYPE | 采集时实时写入 |
| `imu{1,2}{a,b}_100hz` | IMU_100HZ_DTYPE | 初始空，sync 后填充 |

### 7.2 sd_frame_id 计算 (✅ 正确)

```python
# storage_server.py:736
sd_frame_id = ble_frame_id * 8 + 7
```

验证：`ble_frame_id=0 → SD 7`, `ble_frame_id=1 → SD 15`, `ble_frame_id=8 → SD 71`, `ble_frame_id=9 → SD 79`

固件降采样：每 8 个 DRDY 取 1 帧，SD counter 从 0 开始。第 1 个 BLE 帧 = SD 帧 7。公式正确。

### 7.3 sync_status 状态机 (✅ 正确)

```
create → "pending" → (sync 成功) → "synced"
                   → (sync 失败) → "sync_failed"
```

sync_status 初始为 "pending"，仅在 bin_sync_tool 全部校验通过后才写 "synced"。

---

## 8. bin_sync_tool.py 同步结论

### 8.1 同步核心逻辑 (✅ 正确)

```python
sd_base = int(ble_frame_id) * DOWNSAMPLE_RATIO  # = frame_id × 8
for j in range(DOWNSAMPLE_RATIO):
    sd_frame_id = sd_base + j
    bin_data = emg_parser.get_frame(sd_frame_id)
```

每个 BLE frame_id 对应 8 个 SD 帧 `[frame_id×8 .. frame_id×8+7]`。

与固件降采样一致：帧 0→SD[0..7]，帧 1→SD[8..15]，帧 9→SD[72..79]。

### 8.2 防御校验 (✅ 已就位)

| 校验项 | 条件 | 失败后果 |
|--------|------|----------|
| frame_id 连续性 | gap_count > 0 或 duplicate_ratio > 0 | sync_failed |
| SD 覆盖率 | unique_sd / expected_sd < 95% | sync_failed |
| ADC 一致性 | match_rate < 95% (仅 verify=True) | sync_failed |

### 8.3 verify=False 行为 (✅ 安全)

- frame_id 连续性 + SD 覆盖率始终强制执行
- ADC 校验跳过，标记 `skipped=True`
- 报告一致：Overall PASS 时 failure_reasons 为空

---

## 9. hdf5_tool.py 影响评估

| 项目 | 结论 |
|------|------|
| frame_id 显示 | ✅ 动态检测 `'frame_id' in dtype.names`，自动显示 |
| sd_frame_id 显示 | ✅ 同上 |
| sync_status 颜色 | ⚠️ synced=绿, pending=橙, sync_failed/其他=灰 (无专用红色) |
| 旧数据 frame_id | ⚠️ 旧数据 frame_id 有重叠，显示时会看到重复值但工具不会报错 |

**建议**: hdf5_tool.py 对 sync_failed 添加红色显示，对旧数据 frame_id 添加去重提示。

---

## 10. 通道顺序核对 (⚠️ 发现 bug)

### 10.1 数据流中的通道顺序

| 阶段 | 顺序 | 说明 |
|------|------|------|
| ESP32 BLE 发送 | 物理: chip1[0..7], chip2[0..7] | 直接 memcpy 从 SPI buffer |
| ESP32 SD 写入 | 物理: chip1[0..7], chip2[0..7] | 直接 memcpy 从 SPI buffer |
| ble_server.py 解析后 | 映射: 按 channel_map 重排 | `parsed['raw']` = emg_raw_mapped |
| realtimeEngine.js 转发 | 原样传递 mapped raw | `dev1.raw` → `emg1RawData` |
| storage_server.py H5 存储 | 映射顺序 | `channels = [emg_data[ch][i] for ch in range(16)]` |
| bin 文件 | 物理顺序 | `emg_parser.frames[sd_anchor]` |

### 10.2 ADC 校验通道不匹配 (🔴 高风险)

**问题**: `run_adc_verification()` 比较 H5.channels[idx] (映射顺序) 与 bin_data (物理顺序) 时，比较的是不同通道。

V2 channel_map 示例：
```
H5[0] = physical[14] (chip2_ch6)  vs  bin[0] = physical[0] (chip1_ch0)  → 误判
H5[3] = physical[0]  (chip1_ch0)  vs  bin[3] = physical[3] (chip1_ch3)  → 误判
```

**影响**:
- 真实数据上 ADC 校验将**几乎 100% 失败**（16 通道中仅约 3-4 个位置碰巧对应）
- 导致正常数据被拒绝 `sync_failed`
- `verify=False` 可绕过，但失去了强校验保护

**推荐修复** (2 选 1):
- **方案 A** (推荐): 在 `_append_emg()` 中同时存储未映射的 raw ADC (新增字段 `channels_physical` 或新 dataset)，ADC 校验使用物理顺序比对
- **方案 B**: 在 `run_adc_verification()` 中应用反向 channel_map 将 bin 数据映射为 H5 顺序后再比对

### 10.3 同步本身不受影响

bin_sync_tool 的 2kHz 同步不依赖通道比对 — 它直接从 bin 读取并写入 H5 2kHz 数据集。通道顺序错误只影响 ADC 校验，不影响同步数据完整性。

---

## 11. 丢包场景核对

### 11.1 packet_counter 跳变

| 行为 | 当前状态 |
|------|----------|
| ble_server 检测到跳变 | ✅ lost_packets 正确计算，日志输出 |
| lost_frames 累加 | ✅ lost_packets × 9 |
| H5 frame_id 产生 gap | ✅ 有 gap (packet=0→frames[0..8], packet=2→frames[18..26], 中间缺 9..17) |
| bin_sync_tool 检测到 gap | ✅ validate_frame_ids 检测 gap_count > 0 → sync_failed |
| sync_status | ✅ sync_failed (不会错误标记 synced) |

### 11.2 packet_counter 连续但数据丢失

如果 BLE 链路层重传使得 packet_counter 连续但部分帧内容损坏：当前系统**无法检测**此场景。需要固件在包中提供真实 SD frame_id 才能彻底解决。

### 11.3 供应商声称 "packet_counter 仍连续"

如果 packet_counter 不存在 gap，bin_sync_tool 的 frame_id 连续性检查不会触发。但 SD 覆盖率检查和 ADC 校验仍能捕获数据不一致（如果存在）。

---

## 12. 已修复项确认

| 修复项 | 文件 | 状态 |
|--------|------|------|
| BLE 包号→帧号映射修正 | ble_server.py | ✅ 已修复 |
| packet-level 丢包检测 | ble_server.py | ✅ 已修复 |
| frame_id 连续性校验 (gap→fail) | bin_sync_tool.py | ✅ 已修复 |
| SD 覆盖率校验 | bin_sync_tool.py | ✅ 已部署 |
| ADC 校验 (强校验) | bin_sync_tool.py | ⚠️ 存在通道顺序 bug |
| verify=False 报告一致性 | bin_sync_tool.py | ✅ 已修复 |

---

## 13. 仍存在风险

| 风险 | 等级 | 说明 |
|------|------|------|
| ADC 校验通道顺序不匹配 | 🔴 高 | 真实数据上 ADC 校验必然误判失败 |
| BLE 链路丢包但 packet_counter 连续 | 🟡 中 | 无法检测，需固件提供真实 SD frame_id |
| IMU EMG:IMU 帧号比假设 | 🟡 中 | 假设双方计数器严格同步，需实测验证 |
| V3 固件可能未修复 | 🟡 中 | V3 client 仍使用 `start_frame + i` 模式 |
| hdf5_tool sync_failed 无红色 | 🟢 低 | UX 问题，不影响数据 |
| 旧 H5 数据 frame_id 重叠 | 🟢 低 | 已有数据需重新同步 |

---

## 14. 修改建议清单

### 必须修改 (阻塞上线)

| # | 描述 | 文件 | 方案 |
|---|------|------|------|
| 1 | **ADC 校验通道顺序匹配** | bin_sync_tool.py | 在 H5 中同时存储物理顺序 raw ADC，或在 ADC 校验时用反向 channel_map 转换 bin 数据 |

### 建议修改 (本迭代完成)

| # | 描述 | 文件 | 方案 |
|---|------|------|------|
| 2 | hdf5_tool sync_failed 红色显示 | hdf5_tool.py | 添加 `elif sync_status == 'sync_failed': color=red` |
| 3 | 旧数据 frame_id 重叠提示 | hdf5_tool.py | 检测到 frame_id 重复时显示警告 |

### 可暂缓修改

| # | 描述 | 文件 | 方案 |
|---|------|------|------|
| 4 | 固件在 BLE 包中提供真实 SD frame_id | 固件 | 需要与供应商协调，长期方案 |
| 5 | V3 固件同步修复 | wband_emg_V3 | 独立迭代，与 V2 修复对齐 |
| 6 | IMU 同步偏移验证 | bin_sync_tool.py | 对比 IMU BLE 帧和 IMU bin 帧验证对齐 |

---

## 15. 关键代码位置速查

| 用途 | 文件:行号 |
|------|-----------|
| BLE 包计数器声明 | ble_gatt.c:699 |
| BLE 包计数器写入包头 | ble_gatt.c:739 |
| BLE 包计数器递增 | ble_gatt.c:774,778,802 |
| BLE 包计数器重置 | ble_gatt.c:716 |
| SD 帧计数器声明 | ads1298.c:13 |
| SD 帧计数器重置 | ads1298.c:247 |
| SD 帧写入格式 | ads1298.c:286-288 |
| ble_server frame_id 生成 | ble_server.py:574-577 |
| ble_server 丢包检测 | ble_server.py:619-628 |
| ble_server 通道映射 | ble_server.py:597-604 |
| realtimeEngine frame_ids 转发 | realtimeEngine.js:788,810,856 |
| storage_server sd_frame_id 计算 | storage_server.py:736 |
| storage_server 250Hz dtype | storage_server.py:57-62 |
| bin_sync_tool 防御校验 | bin_sync_tool.py:617-709 |
| bin_sync_tool validate_frame_ids | bin_sync_tool.py:92-178 |
| bin_sync_tool run_adc_verification | bin_sync_tool.py:247-355 |
| bin_sync_tool sync 核心 | bin_sync_tool.py:731-756 |
| hdf5_tool sync_status 显示 | hdf5_tool.py:361-371 |
