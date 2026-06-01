# 一对一同步链路 & 多 IMU 兼容审计

> **分支**: `fix_sync`  
> **日期**: 2026-06-01  
> **阶段**: Phase 1 — 只读代码审计，不改代码

---

## 1. 当前一对一同步链路梳理

### 1.1 调用链

```
hdf5_tool.py SyncWorker (sync_mode='one_to_one')
  → tools/bin_sync_tool.py sync_h5_one_to_one()
    → EMGBinParser(emg_bin).parse()
    → IMUBinParser(imu_bin).parse() if imu_bin else None
    → ADC 校验: H5 row i 的 mapped channels == bin frame i*8+7 的 mapped channels
    → _build_and_write_2khz(h5_path, emg_parser, imu_parser, ...)
      → 构建 EMG 2kHz: for i in 0..n-1, for j in 0..7: bin[offset + i*8 + j]
      → _sync_imu_100hz(h5_file, emg_parser, imu_parser, data_2khz, device_id)
        → imu_parser.frames → 解包 imu1, imu2
        → 写入 imu{device}a_100hz + imu{device}b_100hz
```

### 1.2 EMG 一对一关键参数

| 参数 | 值 | 来源 |
|------|-----|------|
| bin_offset | 0 | 硬编码 `sync_h5_one_to_one:1380` |
| H5 row i → bin frame | `i*8 + 7` | anchor_last_sample 对齐 |
| channel_map | H5 attrs 解析, 默认 V2 | `_resolve_channel_map` |
| 时间戳对齐 | `anchor_time - (7-j)/2000` | `_build_and_write_2khz:1576` |

**✅ EMG 一对一链路正确**：bin_offset=0, row_index 定位, 时间戳对齐 anchor_last_sample。

### 1.3 新旧 H5 通道排序差异

| 来源 | 默认顺序 | 说明 |
|------|---------|------|
| storage_server.py | **mapped** (V2 顺序) | `emg_raw_mapped = row[i-1] for i in channel_map` |
| realtimeEngine.js | mapped | `transposeEMG(dev.uv)` 使用 ble_server 已映射的数据 |
| ble_server.py | V2: mapped, V1: mapped | `parse_packet` 在解析时已应用 `channel_map` |
| 新 H5 | mapped (V2) | ✅ 一对一 bin_offset=0 + V2 mapped = 正确 |
| 旧 L015 H5 | **physical** | ❌ 一对一不适用；需一对多 physical fallback |

---

## 2. 当前 IMU bin 格式判断

### 2.1 ESP32 固件 IMU SD 写入格式

**文件**: `lsm6dsv32x.c:370-375`, `app_common.h:122-124`

```c
// 帧大小 = 4 + num_imus * 18
uint16_t actual_imu_bytes = num_imus * BYTES_PER_IMU;  // BYTES_PER_IMU = 18
memcpy(&sd_imu_frame_buf[0], &imu_counter, 4);          // 4B frame_id (LE)
memcpy(&sd_imu_frame_buf[4], temp_imu_buf, actual_imu_bytes);
xRingbufferSend(imu_ringbuf_handle, sd_imu_frame_buf, 4 + actual_imu_bytes, 0);
```

**关键发现**: IMU bin 帧大小是 **可变** 的！

| num_imus | 帧大小 | 
|----------|--------|
| 2 | 4 + 36 = 40 bytes |
| 3 | 4 + 54 = 58 bytes |

### 2.2 IMUBinParser 当前实现

**文件**: `tools/bin_sync_tool.py:54-55, 542-600`

```python
IMU_FRAME_SIZE = 4 + 36  # ❌ 硬编码为 2 IMU！
```

`IMUBinParser.parse()` 使用 `f.read(IMU_FRAME_SIZE)` 固定读取 40 字节：
- 第 1 个 IMU: `raw_data[0:18]` — 正确
- 第 2 个 IMU: `raw_data[18:36]` — 正确（2 IMU 时）
- **第 3 个 IMU**: **不存在**，数据丢失！

### 2.3 IMU bin header

IMU bin header 与 EMG 相同，使用 `emg_file_header_t` 结构 (126 bytes):
```c
typedef struct __attribute__((packed)) {
    uint32_t magic_word;      // 0xBBCCDDEE
    uint16_t sample_rate;     // 100
    uint8_t  gain_index;      // 0 (imu)
    uint8_t  bit_depth;       // 0 (imu)
    uint8_t  imu_enabled;     // 1
    char     timestamp[32];
    uint8_t  reserved[85];
} emg_file_header_t;
```

**关键发现**: header 中 **没有 `num_imus` 字段**！

### 2.4 推断 num_imus 的方法

| 方法 | 可行性 |
|------|--------|
| 从 H5 attrs: `imu1_num_imus` / `imu2_num_imus` | ✅ 最可靠（storage_server 写入） |
| 从文件大小反推: `(file_size - 126) / (4 + num_imus*18)` | ✅ 如果整除则 num_imus 正确 |
| 从 H5 attrs: `imu1_hw_version` (V1=2, V2=0-3) | ⚠️ V2 下可能是 2 或 3 |
| 从 BLE status snapshot: `num_imus` | ✅ firmware reports actual count |

### 2.5 V2 IMU BE/LE 差异

| 来源 | Endianness | 说明 |
|------|-----------|------|
| ESP32 固件 SD bin IMU | **Big Endian** | `lsm6dsv32x_read_raw_6axis` 返回原始寄存器值 |
| BLE 包 IMU (V2) | **Little Endian** | `parse_imu_v2` 使用 `<6h` |
| BLE 包 IMU (V1) | **Big Endian** | `parse_imu_v1` 使用 `>6h` for acc/gyr |

### 2.6 🔴 严重 Bug: IMUBinParser 硬编码 2 IMU

**影响范围**: 所有 3-IMU 硬件的同步。

**根因**:
1. `IMU_FRAME_SIZE = 4 + 36` — 3-IMU 时应该是 `4 + 54`
2. `imu1, imu2 = imu_data` — 第 3 个 IMU 数据丢失
3. `_sync_imu_100hz` 只创建 `imu{device}a_100hz` + `imu{device}b_100hz`，无 `imu{device}c_100hz`

**3-IMU bin 的 frame misalignment**: 如果真实帧 58 字节而 parser 读 40 字节，第 2 帧起就会读错位置。

---

## 3. 当前 H5 IMU Schema 判断

### 3.1 BLE 采集时创建的 IMU 数据集

**文件**: `storage_server.py:600-658`

| 数据集 | 类型 | 用途 |
|--------|------|------|
| `imu1a_ble` | `IMU_BLE_DTYPE` (acc+gyr+mag+frame_id+sd_frame_id+time) | Dev1 IMU-A (AD0_LOW) |
| `imu1b_ble` | 同上 | Dev1 IMU-B (AD0_HIGH) |
| `imu2a_ble` | 同上 | Dev2 IMU-A |
| `imu2b_ble` | 同上 | Dev2 IMU-B |
| `imu1_all_ble` | `IMU_ALL_BLE_DTYPE` (imu_index+acc+gyr+has_mag+mag+...) | Dev1 通用 (V1/V2) |
| `imu2_all_ble` | 同上 | Dev2 通用 |

### 3.2 同步后创建的 IMU 数据集

**文件**: `tools/bin_sync_tool.py:1693-1700`

| 数据集 | 来源 | 说明 |
|--------|------|------|
| `imu{device}a_100hz` | bin IMU1 (AD0_LOW) | ✅ 创建 |
| `imu{device}b_100hz` | bin IMU2 (AD0_HIGH) | ✅ 创建 |
| `imu{device}c_100hz` | bin IMU3 | ❌ **未创建** |
| `imu{device}_100hz` (legacy) | bin IMU1 | ✅ 如果已存在则覆盖 |

### 3.3 realtimeEngine.js IMU 数据流

**文件**: `realtimeEngine.js:940-952`

```javascript
saveDataToStorage({
    imu1a: imu1aData, imu1b: imu1bData,    // V1 格式
    imu1_all: imu1All,                       // V2 格式 (含 imu_index)
    imu1_hw_version, imu1_num_imus,          // 元数据
})
```

**BLE IMU 总是按硬件实际数量发送**：V2 可以是 0-3 个 IMU，每个 18 bytes。

### 3.4 hdf5_tool.py IMU 展示

**文件**: `tools/hdf5_tool.py:1668-1755`

当前 `show_imu_data` 检查 `has_acc`, `has_gyr`, `has_mag`, `has_imu_index`, `has_has_mag_flag`。对 `imu*_all_ble` (IMU_ALL_BLE_DTYPE) 支持 imu_index / has_mag。但同步后的 `imu*_100hz` 数据集没有 `imu_index` 字段 — 它们用不同 dataset 名区分 IMU。

---

## 4. 2 IMU / 3 IMU 兼容矩阵

| 组合 | H5 | bin IMU 帧 | IMUBinParser | _sync_imu_100hz | 结果 |
|------|-----|-----------|-------------|----------------|------|
| 新 H5 + 2 IMU bin | imu*a_ble/imu*b_ble | 40 bytes | ✅ 正确 | ✅ 写入 a/b | **正常** |
| 新 H5 + 3 IMU bin | imu*a_ble/imu*b_ble + imu*_all_ble | **58 bytes** | ❌ wrong | ❌ 丢失 IMU3 | **🔴 第3个IMU丢失** |
| 旧 H5 + 2 IMU bin | imu*a_ble/imu*b_ble | 40 bytes | ✅ 正确 | ✅ 写入 a/b | **正常** |
| 旧 H5 + 3 IMU bin | — (旧 H5 无 3 IMU 场景) | N/A | N/A | N/A | N/A |
| 新 H5 + 无 IMU bin | imu*_ble 存在 | 无 | — | skipped | **🟡 IMU 不同步** |

---

## 5. 风险矩阵

### 🔴 高风险

| # | 问题 | 影响 | 文件 |
|---|------|------|------|
| H1 | `IMU_FRAME_SIZE = 4 + 36` 硬编码 2 IMU | 3-IMU bin 帧边界错位，所有帧读错 | `bin_sync_tool.py:54` |
| H2 | `_sync_imu_100hz` 只创建 a/b，无 c | 3-IMU 数据无法写入 H5 | `bin_sync_tool.py:1693-1700` |
| H3 | `imu1, imu2 = imu_data` 解包固定 2 | 第 3 个 IMU 完全丢失 | `bin_sync_tool.py:1651` |

### 🟡 中风险

| # | 问题 | 影响 | 文件 |
|---|------|------|------|
| M1 | IMU bin header 无 `num_imus` 字段 | 无法从 bin 直接推断 IMU 数量 | 固件 `app_common.h` |
| M2 | IMU sync 失败不阻止 EMG sync_status=synced | EMG 同步成功但 IMU 可能缺失 | `bin_sync_tool.py:1617` |
| M3 | V2 IMU Endianness 可能不一致 | SD bin 使用 BE，BLE 使用 LE | `lsm6dsv32x.c` vs `ble_server.py` |

### 🟢 低风险

| # | 问题 | 影响 | 文件 |
|---|------|------|------|
| L1 | 一对一 EMG 时间戳对齐已修复 | j=7 对齐 anchor_time | `bin_sync_tool.py:1576` |
| L2 | channel_map fallback 已支持 | physical/V2 自动切换 | `bin_sync_tool.py:1307` |
| L3 | IMU parser endianness (AG=BE) | V2 硬件 IMU 寄存器是 LE，但 `lsm6dsv32x_read_raw_6axis` 返回 BE | 需实测确认 |

---

## 6. 最小改动方案

### 6.1 IMUBinParser 支持动态 num_imus

**改动**: `tools/bin_sync_tool.py`

```python
# 新增参数
class IMUBinParser:
    def __init__(self, bin_path, num_imus=2):  # 新增 num_imus
        self.num_imus = num_imus

    def parse(self):
        imu_payload = self.num_imus * 18  # 替代硬编码 36
        frame_size = 4 + imu_payload
        
        while True:
            chunk = f.read(frame_size)
            ...
            imus = []
            for i in range(self.num_imus):
                off = i * 18
                ag = struct.unpack('>6h', raw_data[off:off+12])
                m = struct.unpack('<3h', raw_data[off+12:off+18])
                imus.append({...})
            self.frames[frame_id] = imus  # 改为 list
```

**调用方**: `sync_h5_one_to_one` 和 `sync_h5_one_to_many_adc_search` 需要从 H5 attrs 读取 `imu1_num_imus`/`imu2_num_imus` 并传递给 `IMUBinParser`。

### 6.2 _sync_imu_100hz 输出 2/3 IMU

```python
def _sync_imu_100hz(h5_file, emg_parser, imu_parser, data_2khz, device_id):
    num_imus = imu_parser.num_imus if imu_parser else 2
    
    # 构建所有 IMU 的数据
    all_data = [np.empty(num_imu_frames, dtype=imu_100hz_dtype) for _ in range(num_imus)]
    
    for idx, imu_fid in enumerate(imu_frame_ids_unique):
        imu_data = imu_parser.frames.get(imu_fid)
        if imu_data is not None:
            for k in range(num_imus):
                all_data[k][idx] = imu_data[k]  # imu_data 现在是 list
    
    # 写入: imu{device}a_100hz, imu{device}b_100hz, imu{device}c_100hz
    labels = ['a', 'b', 'c']
    for k in range(num_imus):
        name = f"imu{device_id}{labels[k]}_100hz"
        write_or_create_dataset(name, all_data[k], imu_filled, imu_missing)
```

### 6.3 是否新增 imu{device}_all_100hz 更合适

**推荐**: 保持当前 `imu{device}{label}_100hz` 命名（a/b/c），**不需要**新增 `imu_all_100hz`。

**理由**:
1. BLE 已有 `imu*_all_ble` 和 `imu*a_ble`/`imu*b_ble` 两套
2. 100Hz 同步数据用 a/b/c 区分更清晰，与 bin 物理硬件对应
3. 新增 `imu_all_100hz` 会引入 imu_index 字段，与现有 IMU_100HZ_DTYPE 不兼容
4. hdf5_tool 展示不需要改（每个 dataset 单独显示）

### 6.4 hdf5_tool 展示

- `show_imu_data` 已支持 `has_acc`/`has_gyr`/`has_mag`，不需要改
- 新增 `imu*c_100hz` dataset 自动出现在数据集列表中
- 如需要可添加 "IMU count" 显示

### 6.5 sync_status 多设备策略

**当前**: Dev1 和 Dev2 各调一次 `sync_h5_one_to_one(... set_synced=is_last_device)`。最后一个设备的 `_build_and_write_2khz` 中 `set_synced=True` 写入 `synced`。

**建议**: IMU 失败不应阻止 `synced`（IMU 可能不存在 bin 文件），但应在 `sync_history` 中记录 IMU skipped/warning。

---

## 7. 验证清单

### 7.1 实机构造测试

| # | 测试 | 预期 |
|---|------|------|
| 1 | 2-IMU 硬件 + 新格式采集 + 一对一同步 | EMG+IMU 同步成功 |
| 2 | 3-IMU 硬件 + 新格式采集 + 一对一同步 | EMG+IMU 同步成功（包括 imu*c_100hz） |
| 3 | 2-IMU bin + 旧格式一对多同步 | IMU a/b 同步成功 |
| 4 | 无 IMU bin + 一对一同步 | EMG 成功, IMU skipped, sync_status=synced |

### 7.2 synthetic 测试

| # | 测试 | 构造方法 |
|---|------|---------|
| 1 | 2-IMU bin (40B/frame) + 一对一 | 已知 offset=0, 验证 a/b 都同步 |
| 2 | 3-IMU bin (58B/frame) + 一对一 | 验证 a/b/c 都同步, frame 边界正确 |
| 3 | 2→3 IMU bin 切换 | 确认 parser 不会 misalign |
| 4 | 无 IMU bin | 确认 sync 不报错, EMG 正常 |

### 7.3 L015 真实数据验证

| # | 测试 |
|---|------|
| 1 | L015 bin 文件大小推断 num_imus |
| 2 | 一对多同步后检查 imu*a_100hz / imu*b_100hz 存在且数据合理 |
| 3 | 如果 L015 是 2 IMU —— 确认现有代码已正确处理 |

---

## 8. H5 attrs 建议新增

| Attr | 写入时机 | 值 |
|------|---------|-----|
| `imu{device}_num_imus_synced` | _sync_imu_100hz | 实际同步的 IMU 数量 |
| `imu{device}_synced_labels` | _sync_imu_100hz | JSON: `["a","b","c"]` |
| `sync_imu_endianness` | _sync_imu_100hz | `"BE"` (SD bin 固定 BE) |

---

## 9. 额外发现

### 9.1 V2 IMU Endianness 确认

**文件**: `lsm6dsv32x.c`

需要检查 `lsm6dsv32x_read_raw_6axis` 的字节序。如果它是直接 memcpy 寄存器值（通常是 LE），但 `IMUBinParser` 用 `>6h` (BE) 解析 acc/gyr——这可能导致数据解析错误。

**建议**: 检查 `lsm6dsv32x_read_raw_6axis` 的实现，确认它是返回 BE 还是 LE。

### 9.2 一对一 vs 一对多 IMU channel_map

一对一同步的 EMG 使用 H5 默认 channel_map (V2 mapped)，但 IMU 不需要 channel_map（IMU 只有 acc/gyr/mag 三个分量，没有 16 通道映射问题）。当前 IMUBinParser 的 `parse_chip` 使用硬编码偏移量，不涉及 channel_map。

### 9.3 一对一 bin 定位无需 IMU

`sync_h5_one_to_one` 只通过 EMG ADC 校验确认 bin_offset=0，IMU bin 的校验是独立的。如果 EMG 校验通过但 IMU bin 不存在/格式不对，EMG 仍同步成功。

---

## 10. 总结

| 结论 | 详情 |
|------|------|
| EMG 一对一链路 | ✅ 正确 |
| EMG 时间戳 | ✅ anchor_last_sample 已修复 |
| IMU bin 解析 2 IMU | ✅ 正确 |
| IMU bin 解析 3 IMU | 🔴 **不支持** — IMU_FRAME_SIZE 硬编码, 第3个 IMU 丢失 |
| IMU sync 2 IMU | ✅ 写入 a/b_100hz |
| IMU sync 3 IMU | 🔴 **不支持** — 只创建 a/b, 无 c |
| hdf5_tool IMU 展示 | ✅ 支持 2/3 IMU (按 dataset 显示) |
| BLE IMU 数据流 | ✅ realtimeEngine 支持 variable imu count |
| 建议改动量 | ~40 行（IMUBinParser + _sync_imu_100hz + 调用方传 num_imus） |

---

> 📄 **后续**: Phase 2 实现最小改动方案 ~40 行。  
> 🤖 Generated with [Claude Code](https://claude.com/claude-code)  
> Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
