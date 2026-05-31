# Bin 同步机制调查报告

**调查日期**: 2026-05-30  
**调查人**: Claude (开发员工)  
**分支**: feat_band_V3  
**背景**: 供应商发现 ESP32 腕带 BLE 包号可能总是连续，即使蓝牙丢包，采集软件也可能不知道。需评估是否影响 250Hz BLE EMG → SD/bin 2kHz EMG 的同步。

---

## 结论摘要

当前同步机制存在**两类独立问题**，综合风险等级 **高**。

### 问题 A：无丢包时的 frame_id 映射错误

ESP32 固件 BLE 包头是**包序号**（每发送/丢弃一包 +1），但 `ble_server.py` 将其当作**帧索引**来生成 frame_id。由于每包包含 9 帧，连续包的 frame_id 重叠（包 0: `[0..8]`，包 1: `[1..9]`），导致 sd_frame_id 映射错误、同步覆盖率趋近 1/9。

**正确映射应为**: `frame_id = packet_counter × frames_per_packet + i`，即 `[0..8]`, `[9..17]`, `[18..26]`。

### 问题 B：真实 BLE 丢包时的同步保障

即使修正了问题 A 的映射，如果固件在真实丢包时 `ble_frame_counter` 仍然连续不跳变，则：
- 服务器无法通过包序号检测丢包
- 即使使用 `packet_counter × 9 + i` 生成 frame_id，frame_id 也会与真实 SD 帧号错位
- 丢包后的所有同步映射将整体偏移，且无法自动恢复

**结论**: 问题 A 是当前已知可修复的映射错误；问题 B 需要与供应商确认固件丢包时 `ble_frame_counter` 的真实行为，且仅靠服务器端修复无法解决。

**关键数据**:
- 每个 BLE 包 = 1 个包计数器 + 9 个降采样 EMG 帧（每帧 48 字节，16通道×3字节）
- 降采样比 = 8（2kHz → 250Hz）
- BLE 包率 ≈ 27.8 Hz（250/9）
- SD bin 中每个 2kHz 帧 = 4字节帧号（连续递增） + 48 字节原始数据

---

## 阅读过的文件列表

| 文件 | 作用 | 关键行号 |
|------|------|----------|
| `wband_emg_V2/wband_emg_esp32s3_v5/main/ble_gatt.c` | BLE 包构造与发送 | 695-811 |
| `wband_emg_V2/wband_emg_esp32s3_v5/main/ads1298.c` | SD 卡帧写入 | 280-336 |
| `wband_emg_V2/wband_emg_esp32s3_v5/main/app_common.h` | 常量定义 | 115-125 |
| `ble_server.py` | BLE 数据解析、frame_id 生成 | 560-745 |
| `storage_server.py` | H5 存储、sd_frame_id 计算 | 54-60, 405-425, 692-756 |
| `tools/bin_sync_tool.py` | bin→H5 同步工具 | 44-60, 81-166, 240-440 |
| `docs/hdf5_tool_doc.md` | 已有 HDF5 工具文档 | 全文 |

---

## 当前同步流程

### 整体数据链路

```
ESP32 固件                          Python 后端                         同步工具
─────────                          ──────────                         ────────
DRDY 中断 (2kHz)
  │
  ├─→ SD bin: s_raw_interrupt_counter (帧号, 连续+1)
  │   每帧 52字节 (4B头 + 48B数据)
  │
  └─→ 降采样(×8) → BLE 包 (9帧/包)
      包头: ble_frame_counter (包号, 每包+1)
                                          │
                                          ▼
                              ble_server.py 解析
                              start_frame = 包头4字节
                              frame_ids = [start_frame ..+8]
                                          │
                                          ▼
                              storage_server.py 存储
                              sd_frame_id = ble_frame_id * 8 + 7
                              写入 emg{dev}_250hz_adc
                                          │
                                          ▼
                              realtimeEngine.js 转发
                                          │
                                          ▼
                              前端接收并显示波形
                                          │
                                    (采集结束后)
                                          │
                                          ▼
                              bin_sync_tool.py 同步
                              1. 读 H5 frame_ids
                              2. sd_base = frame_id * 8
                              3. 从 bin 读 sd_base..sd_base+7
                              4. 写入 emg{dev}_2khz_adc
```

### 同步映射公式

BLE 帧号 → SD 帧号的映射（`storage_server.py:736`）:

```
sd_frame_id = ble_frame_id × 8 + 7
```

**逻辑**: 250Hz 的每帧对应 8 个原始 2kHz 帧中的最后一帧（第 8 个），所以 BLE 帧 0 → SD 帧 7，BLE 帧 1 → SD 帧 15，以此类推。

同步时（`bin_sync_tool.py:362`）:

```python
sd_base = int(ble_frame_id) * DOWNSAMPLE_RATIO  # DOWNSAMPLE_RATIO = 8
for j in range(DOWNSAMPLE_RATIO):
    sd_frame_id = sd_base + j
    bin_data = emg_parser.get_frame(sd_frame_id)  # 从 bin 读取
```

**这个映射公式本身是正确的**，前提是 `ble_frame_id` 是顺序递增的 BLE 帧索引。

---

## BLE 包结构相关结论

### 每个 BLE 通知的结构

```
[4 字节 LE uint32] [9 × 48 = 432 字节 EMG] [可选 IMU 数据]
     ↑                      ↑
  ble_frame_counter    9个降采样帧（每帧16通道×24bit大端序）
  (包计数器)
```

### 关键常量（`app_common.h:115-125`）

| 常量 | 值 | 说明 |
|------|----|------|
| `BLE_NOTIFY_FRAMES_PER_PACKET` | 9 | 每包 EMG 帧数 |
| `SPI_READ_LEN` | 54 | 每次 SPI 读取字节 (2 chips × 27B) |
| `BYTES_PER_CHIP_PACKET` | 27 | 每 chip 读取字节 (3B状态 + 8通道×3B) |
| `FRAMES_PER_PACKET` | 10 | 另一包帧数常量（非 BLE 通知用） |

### ble_frame_counter 的行为（`ble_gatt.c`）

```c
// 声明 (L699)
static uint32_t ble_frame_counter = 0;

// 复位 (L716)
ble_frame_counter = 0;  // 停止采集时清零

// 递增 (L774, L778, L802) — 所有路径都 +1
ble_frame_counter++;  // 无论发送成功、拥塞丢弃、低内存丢弃
```

**关键结论**:
1. `ble_frame_counter` 是**包级别的计数器**，每发送或丢弃一个包就 +1
2. 初始值为 0，采集停止时重置
3. 包序号为 0, 1, 2, 3, ...（不论是否丢包，始终连续）
4. **每个包实际包含 9 帧，但计数器只 +1**

### BLE 服务器如何生成 frame_id（`ble_server.py:574,624`）

```python
start_frame = struct.unpack('<I', data[0:4])[0]         # L574
frame_ids = [start_frame + i for i in range(fpkt)]       # L624, fpkt=9
```

**问题**: 服务器将 `ble_frame_counter`（包号 0,1,2,...）当作首帧的**帧索引**，生成 `[0..8]`, `[1..9]`, `[2..10]`。

**正确的帧索引应该是**: `[0..8]`, `[9..17]`, `[18..26]`（每包跳 9）。

### 丢包检测逻辑（`ble_server.py:616-621`）

```python
expected = dev.last_frame_index + 1
if start_frame != expected and start_frame > expected:
    dev.lost_frames += start_frame - expected
dev.last_frame_index = start_frame + fpkt - 1
```

**问题**: 包 0 后 `last_frame_index = 8`, `expected = 9`。包 1 的 `start_frame = 1`。条件 `1 > 9` 为 False，**丢包永远不会被检测到**。

---

## SD/bin 结构相关结论

### EMG bin 文件格式（`bin_sync_tool.py:44-52`）

```
[126 字节文件头]
  - 4B magic (EMG_MAGIC)
  - 2B sample_rate
  - 1B gain_idx
  - 1B bit_depth
  - 1B imu_en
  - 32B timestamp_str
  - 填充至 126 字节

[52 字节/帧 × N 帧]
  每帧:
  - 4B LE uint32 frame_id (= s_raw_interrupt_counter, 从 0 开始连续递增)
  - 48B raw_data (16通道 × 3字节 24bit 大端序有符号 int)
```

### SD 帧号的生成（`ads1298.c:286-288`）

```c
memcpy(&s_sd_frame_buffer[sd_ptr], &s_raw_interrupt_counter, 4);
s_raw_interrupt_counter++;  // 每个 DRDY 中断 +1（即 2kHz 速率）
```

- `s_raw_interrupt_counter` 从 0 开始，每个 2kHz 采样点 +1
- 这是 bin 文件中的 frame_id，是一个**连续的、单调递增的计数器**
- 与 BLE 的 `ble_frame_counter` 是**两个完全独立的计数器**

---

## 丢包场景影响评估

### 场景 1：正常采集（无丢包）

以 3 个 BLE 包为例：

| 包序号 | ble_frame_counter | 服务器 frame_ids | 实际对应 SD 帧号 | 存储的 sd_frame_id |
|--------|-------------------|-----------------|------------------|-------------------|
| 0 | 0 | [0,1,2,3,4,5,6,7,8] | [7,15,23,31,39,47,55,63,71] | [7,15,23,31,39,47,55,63,71] ✓ |
| 1 | 1 | [1,2,3,4,5,6,7,8,9] | [79,87,95,103,111,119,127,135,143] | [15,23,31,39,47,55,63,71,79] ✗ |
| 2 | 2 | [2,3,4,5,6,7,8,9,10] | [151,159,167,175,183,191,199,207,215] | [23,31,39,47,55,63,71,79,87] ✗ |

**问题**:
- 包 1 的 9 帧中，8 帧的 sd_frame_id 与包 0 重复
- 包 2 的 9 帧中，8 帧的 sd_frame_id 与包 0/1 重复
- **只有每包的最后一帧（frame_id=8, 9, 10）的 sd_frame_id 映射正确**

### 场景 2：BLE 丢包

假设包 1 因拥塞被丢弃：

| 包序号 | ble_frame_counter | frame_ids | 服务器检测 |
|--------|-------------------|-----------|-----------|
| 0 | 0 | [0..8] | — |
| 1 (丢弃) | 1→2 | — | 无法检测 |
| 2 | 2 | [2..10] | `start_frame=2`, `expected=9`, `2>9`=False → **未检测到** |

**结论**: 丢包检测完全失效。无论丢不丢包，`start_frame` 始终远小于 `expected`。

### 场景 3：同步时的实际影响

同步工具读取 H5 中的 `frame_ids`，对每个 frame_id 执行：

```
sd_base = frame_id × 8
读取 SD bin 帧 [sd_base .. sd_base+7]
```

以 3 包 × 9 帧 = 27 个 H5 行为例：

| H5 行索引 | frame_id | 读取的 SD 帧范围 | 备注 |
|----------|----------|-----------------|------|
| 0..8 | 0..8 | 0..71 | 正确 |
| 9..17 | 1..9 | 8..79 | **0..7 区域被跳过，8..71 重复读取** |
| 18..26 | 2..10 | 16..87 | **大量重复** |

**实际影响**:
1. 2kHz 输出中的大部分帧来自重叠的 SD 帧范围，而非应有的后续帧
2. 真正的后续 SD 帧（如 SD 88..215）**从不被读取**，相当于数据丢失
3. 输出帧数 = H5 行数 × 8 = 216 帧，但有效唯一覆盖的 SD 帧范围仅 0..87（88 帧）
4. 对于更长的采集，覆盖率约为 `(9 + (N-1)) / (N × 9)`，随包数 N 增长趋近于 **1/9 ≈ 11%**

### 场景 4：数据校验能否发现问题？

同步工具的 `verify=True`（`bin_sync_tool.py:296-320`）：

```python
sd_frame_id = int(ble_frame_id) * DOWNSAMPLE_RATIO + (DOWNSAMPLE_RATIO - 1)
bin_data = emg_parser.get_frame(sd_frame_id)
# 比较 H5 通道数据与 bin 数据
```

对于 H5 中的 frame_id=1（来自包 1），校验会比较 `bin[15]` 与 `H5[frame_id=1]` 的数据。但 H5[frame_id=1] 的数据**实际来自 SD 帧 79**，而 bin[15] 是真正的 SD 帧 15。如果两个不同的 SD 帧恰巧数值相近（如静息 EMG），校验会通过；如果数值差异大，校验会失败。

**结论**: 校验**可能但不保证**发现此问题。它只抽查 100 帧，且依赖于数值差异。

---

## 问题分类：问题 A（无丢包映射错误）与问题 B（真实丢包同步保障）

### 问题 A：无丢包时的 frame_id 映射错误

**现象**: 即使 BLE 传输完美无丢包，当前代码也会产生 frame_id 重叠。

**根因**: 固件 BLE 包头是 `packet_counter`（每包 +1），`ble_server.py:624` 用 `start_frame + i` 生成 frame_ids：
- `packet_counter = 0` → `frame_ids = [0,1,2,3,4,5,6,7,8]`
- `packet_counter = 1` → `frame_ids = [1,2,3,4,5,6,7,8,9]`  ← 8 个重叠
- `packet_counter = 2` → `frame_ids = [2,3,4,5,6,7,8,9,10]` ← 8 个重叠

**正确映射**: `frame_id = packet_counter × frames_per_packet + i`
- `packet_counter = 0` → `frame_ids = [0,1,2,3,4,5,6,7,8]`
- `packet_counter = 1` → `frame_ids = [9,10,11,12,13,14,15,16,17]`
- `packet_counter = 2` → `frame_ids = [18,19,20,21,22,23,24,25,26]`

**影响范围**: 问题 A 影响**每一次同步**，无论是否有丢包。

**可修复性**: 可以直接在服务器端修复——用 `packet_counter × 9 + i` 替换 `start_frame + i`。但仅修复此映射不能解决丢包错位问题（问题 B）。

### 问题 B：真实 BLE 丢包时的同步保障

**场景**: 假设采集过程中 BLE 丢了一包。

**子场景 B1 — 固件 packet_counter 能反映丢包跳变**:
- 包 0: `packet_counter = 0`
- 包 1: 被丢弃，`packet_counter++` → `1`
- 包 2: `packet_counter = 2`（跳过了 1）

此时用 `packet_counter × 9 + i` 生成 frame_id：
- 包 0: `frame_ids = [0..8]`
- 包 2: `frame_ids = [18..26]`（`2 × 9 + i`）

→ frame_id 出现 gap (9..17 缺失)，**可以检测到丢包**，且已收到的数据 frame_id 不会错位。

**子场景 B2 — 固件 packet_counter 在丢包时仍然连续**（供应商担心的场景）:
- 包 0: `packet_counter = 0`
- 包 1: 被丢弃，但 packet_counter 因某种原因**不递增**（或递增后又被某种机制抹平）
- 包 2: `packet_counter = 1`（仍然连续！）

此时无论是 `start_frame + i` 还是 `packet_counter × 9 + i`：
- 包 0: `frame_ids = [0..8]`
- 包 2: `frame_ids = [1..9]` 或 `[9..17]`

→ **无论哪种公式，都无法检测到丢包**，且后续数据会与 SD/bin 的帧号对应关系错位。

**关键不确定性**: 需要与供应商确认固件在真实 BLE 丢包时 `ble_frame_counter` 的实际行为。当前代码（`ble_gatt.c:772-775`）显示拥塞丢弃时也会执行 `ble_frame_counter++`，因此子场景 B1 的概率更大；但如果是 BLE 链路层丢包（包已发出但空中丢失），接收端永远不会知道，此时 packet_counter 当然也不会跳变。

**结论**: 问题 B 无法仅靠服务器端修复。需要：
- 固件在 BLE 包头提供真实 SD frame id（`s_raw_interrupt_counter`），或
- bin_sync_tool 增加 ADC 波形校验/匹配作为第二道防线

---

## 修复方案评估

### 方案 1：使用 `packet_counter × frames_per_packet + i` 生成 BLE frame_id

**改动文件**: `ble_server.py:624`

```python
# 当前 (L624)
frame_ids = [start_frame + i for i in range(fpkt)]

# 修复后
frame_ids = [start_frame * fpkt + i for i in range(fpkt)]
# 或等价地维护 dev.ble_packet_count 并用 packet_count * 9 + i
```

**优点**:
- 修复问题 A（frame_id 重叠），改动极小（1 行）
- 如果固件丢包时 packet_counter 跳变，可借此检测丢包（子场景 B1）
- 不依赖固件修改

**风险**:
- 如果固件丢包时 packet_counter 仍连续（子场景 B2），此修复不能检测丢包，且 frame_id 仍然与实际 SD 帧号错位
- **需要确认 `packet_counter` 是否跨采集会话持久化**——当前固件停止采集时会清零 `ble_frame_counter`（`ble_gatt.c:716`），与新 H5 文件对应，风险较低
- 对已有历史 H5 文件无帮助（frame_id 已写入，无法追溯修正）

### 方案 2：要求固件在 BLE 包头提供真实 SD frame id

**改动范围**: 固件（`ble_gatt.c`） + 协议 + `ble_server.py`

**具体方案**: 固件在 BLE 包头写入 `s_raw_interrupt_counter`（即该包中第一个 2kHz 原始帧号，或第一个降采样帧对应的 SD frame id），而不是 `ble_frame_counter`。

**优点**:
- **从根本上解决问题 A 和问题 B**：服务器直接拿到真实的 SD 帧号，无需任何推算
- 同步工具可直接锚定 SD/bin，无需担心映射公式是否正确
- 丢包检测变为直接比对：上一包最后一个 SD 帧号与下一包第一个 SD 帧号之间的差值

**风险**:
- **需要供应商修改固件和 BLE 协议**，周期不确定
- 已有固件版本（V1/V2）可能无法升级，需要保持向后兼容
- 包头字段语义变化需要版本协商机制

**建议**: 与供应商确认 V3 固件是否已包含此类字段，或在 V3 协议中增加。

### 方案 3：bin_sync_tool 增加 ADC 波形校验/匹配与置信度检测

**改动文件**: `tools/bin_sync_tool.py`

**具体措施**:

**(a) frame_id 完整性校验**（同步前，防御性检测）:
```python
# 检测 H5 中 frame_id 是否严格单调递增
diffs = np.diff(frame_ids)
overlaps = np.sum(diffs <= 0)
gaps = np.sum(diffs > 1)
if overlaps > 0:
    log(f"[WARN] frame_id 存在 {overlaps} 处重叠/非单调，同步结果不可靠")
if gaps > 0:
    log(f"[WARN] frame_id 存在 {gaps} 处跳变 (gap>1)，可能存在丢包")
```

**(b) SD 覆盖率检测**（同步后）:
```python
# 实际覆盖的唯一 SD 帧范围 vs 期望覆盖范围
unique_sd_frames = len(set(sd_frame_ids_read))
expected_sd_frames = num_frames_250hz * DOWNSAMPLE_RATIO
coverage = unique_sd_frames / expected_sd_frames
if coverage < 0.90:
    log(f"[WARN] SD 帧覆盖率仅 {coverage:.1%}，同步结果可能不完整")
```

**(c) ADC 抽样一致性校验**（增强现有 verify）:
```python
# 不只是抽查 100 帧，而是对全量数据按固定间隔抽取
# 比较 H5 250Hz 数据与 bin 中对应 SD 抽取帧的 ADC 值
# 如果匹配率过低（如 < 95%），标记 sync_confidence 为 "low"
```

**(d) sync_status 写入控制**:
```python
# 只有通过所有校验后才写入 sync_status = 'synced'
# 如果校验失败，写入 sync_status = 'sync_failed' 或 'sync_unreliable'
# 并附带校验失败原因和 sync_confidence 字段
```

**优点**:
- 不需要固件/协议修改，可在当前版本立即实施
- 对已有历史 H5 文件也能运行检测
- 防御性设计：宁可拒标 `synced`，也不静默写入错误数据

**风险**:
- ADC 波形匹配可能因信号特征相似而产生假阳性/假阴性
- 如果数据确实损坏，只能检测不能修复
- 计算量增加（但仍在可接受范围内）

**自动重新对齐的可能性**: 
- 如果 frame_id 只是偏移（如整体错位 N 帧），理论上可通过滑动窗口 ADC 互相关搜索最佳偏移量来重新对齐
- 但实现复杂度高，且需要验证收敛性
- **建议先实现检测告警，自动对齐作为后续增强功能**

### 方案对比

| 维度 | 方案 1 (packet×9映射) | 方案 2 (固件提供SD帧号) | 方案 3 (ADC校验防御) |
|------|----------------------|------------------------|---------------------|
| 解决问题 A | ✅ | ✅ | ⚠️ 只能检测 |
| 解决问题 B | ⚠️ 依赖硬件行为 | ✅ | ⚠️ 只能检测 |
| 需要固件修改 | 否 | 是 | 否 |
| 需要协议修改 | 否 | 是 | 否 |
| 实施周期 | 短（1行） | 长（需供应商） | 中（几天） |
| 对已有数据有用 | 否 | 否 | 是 |
| 可修复损坏数据 | 否 | 否 | 否（仅检测） |

---

## 当前机制问答

### Q1: 当前 bin_sync_tool 是不是主要基于 frame_id 映射，而不是 ADC 波形匹配？

**是的。** 当前同步完全基于 `frame_id → sd_frame_id` 的算术映射，不使用任何 ADC 波形匹配。

代码路径（`bin_sync_tool.py`）:
1. 第 282 行: `frame_ids = data_250hz['frame_id']` — 从 H5 读取 BLE 帧号
2. 第 362 行: `sd_base = int(ble_frame_id) * DOWNSAMPLE_RATIO` — 算术映射
3. 第 368 行: `bin_data = emg_parser.get_frame(sd_frame_id)` — 按帧号从 bin 精确读取

整个过程不涉及 ADC 值比对、互相关、或任何波形匹配算法。如果 frame_id 错了，同步工具会无误地从 bin 中读取**错误的帧号范围**。

### Q2: verify 当前只是抽样校验，不是同步依据，对吗？

**对的。** `verify` 参数（`bin_sync_tool.py:296-320`）的行为：

- 仅在 `verify=True` 时执行
- 只抽样 100 帧（`sample_count = min(100, len(frame_ids))`）
- 比对 H5 250Hz 第一通道 ADC 值与 bin 对应帧的数据
- 比对结果以日志形式输出（match/mismatch 计数），**不影响同步流程**
- verify 失败不会阻止 `sync_status = 'synced'` 的写入

**verify 是纯粹的诊断功能，不是同步依据，也不是数据完整性守护者。**

### Q3: 如果 frame_id 错了，bin_sync_tool 是否会按错误 frame_id 读取错误 SD 范围？

**会。** 同步工具无条件信任 H5 中的 `frame_id` 字段。

以 frame_id=1（来自包 1，实际数据对应 SD 帧 79）为例：
- 同步工具计算: `sd_base = 1 × 8 = 8`，读取 SD 帧 8..15
- 实际应读: `sd_base = 9 × 8 = 72`，读取 SD 帧 72..79
- **读取了完全错误的 SD 帧范围**

同步工具不会质疑 frame_id 的合理性，也不会交叉验证读取的数据是否与 H5 中存储的 250Hz 数据一致（verify 除外，但 verify 不阻止同步）。

### Q4: 旧数据是否可以通过检测 H5 250Hz frame_id 是否重复/非单调来筛查？

**可以。** 筛查方法：

```python
import h5py
import numpy as np

def check_frame_id_health(h5_path, device_id=1):
    """检测 H5 中 frame_id 的健康状况"""
    with h5py.File(h5_path, 'r') as f:
        ds_name = f'emg{device_id}_250hz_adc'
        if ds_name not in f:
            return {'error': f'{ds_name} not found'}
        
        frame_ids = f[ds_name][:]['frame_id']
        diffs = np.diff(frame_ids.astype(np.int64))
        
        return {
            'total_frames': len(frame_ids),
            'unique_frame_ids': len(set(frame_ids)),
            'duplicate_rate': 1 - len(set(frame_ids)) / len(frame_ids),
            'overlaps': int(np.sum(diffs <= 0)),        # 非单调（重复）
            'gaps': int(np.sum(diffs > 1)),              # 跳变（可能丢包）
            'max_frame_id': int(np.max(frame_ids)),
            'min_frame_id': int(np.min(frame_ids)),
            'expected_max': len(frame_ids) - 1,          # 如果 frame_id 从 0 严格递增
            'is_healthy': bool(np.all(diffs == 1)),      # 严格递增且无跳变
        }
```

**判断标准**:
- `duplicate_rate ≈ 0.89`（接近 8/9）→ 当前映射错误（问题 A）
- `gaps > 0` 且 `duplicate_rate > 0` → 映射错误 + 可能有真实丢包
- `is_healthy == True` → frame_id 严格递增，无已知问题

---

## 主管审批意见

**审批日期**: 2026-05-30

### 已批准

1. 调查报告对问题 A（frame_id 映射错误）的分析成立，根因定位准确
2. 问题 A 与问题 B 的区分是必要的，不应混为一谈

### 不批准

1. **暂不批准"服务器端维护 `ble_global_frame_index`"作为最终修复方案**
   - 理由: 该方案只能修复问题 A（无丢包映射），无法解决问题 B（真实丢包时的同步错位）
   - 如果固件丢包时 packet_counter 确实连续不跳变，自维护的 frame index 仍然会与 SD/bin 帧号整体错位，且无法自知
   - 作为无丢包映射修正方案 1 的一部分是可接受的，但不能作为"完整修复"

### 建议的修复优先级

**第一优先级（立即执行）: 防御性修复 — bin_sync_tool 增加强校验**

修改 `tools/bin_sync_tool.py`，在同步流程中增加以下校验，**任何一项失败则不得写入 `sync_status='synced'`**：

| 校验项 | 检测内容 | 失败后果 |
|--------|---------|---------|
| frame_id 单调性 | `np.diff(frame_ids) <= 0` 的数量 | `sync_status = 'sync_failed'` |
| frame_id 重复率 | `1 - unique/total` 是否 > 阈值 | `sync_status = 'sync_failed'` |
| SD 覆盖率 | 实际覆盖唯一 SD 帧数 / 期望帧数 | 若 < 90%，标记 `sync_confidence = 'low'` |
| ADC 抽样一致性 | 增强 verify，非仅日志，而是全量抽样比对 | 若匹配率 < 95%，标记 `sync_confidence = 'low'` |

**第二优先级（与供应商确认后执行）: 映射修正**

1. 与供应商确认 V2/V3 固件在真实 BLE 丢包时 `ble_frame_counter` 的行为（跳变 vs 连续）
2. 如果确认 packet_counter 跳变 → 实施方案 1（`packet_counter × 9 + i` 映射）作为正式修复
3. 如果确认 packet_counter 仍连续 → 推进方案 2（要求固件提供真实 SD frame id）

**第三优先级（长期）: 固件协议增强**

与供应商协商 V3 固件在 BLE 包头提供 `s_raw_interrupt_counter`（或该包首帧对应的 SD frame id），从根本上解决同步锚定问题。

---

## 仍不确定的问题

1. **固件丢包时 `ble_frame_counter` 的真实行为**: 当前代码分析（`ble_gatt.c:772-775`）显示拥塞丢弃时 `ble_frame_counter++` 会执行，但需要确认 BLE 链路层丢包（包已发出但空中丢失）是否算作"已发送"从而完成递增。**需要供应商提供明确答案。**

2. **已有历史数据**: 之前用当前逻辑同步过的 H5 文件，其 `emg{dev}_2khz_adc` 数据集是否已受影响？建议用 Q4 中的 `check_frame_id_health()` 函数抽查若干历史文件。受影响的文件应标记 `sync_status` 为非 `synced`。

3. **IMU 同步是否受影响**: IMU 同步使用的是 EMG SD 帧号作为锚点（`bin_sync_tool.py:439-440`），如果 EMG 同步的 SD 帧号映射出错，IMU 同步也会连锁受影响。

4. **V3 固件是否已修复此问题**: 本调查仅针对 `wband_emg_V2` 固件。`wband_emg_V3` 可能已有不同实现，需另行检查。

5. **方案 1 中 packet_counter × 9 的通用性**: 当前 `fpkt=9` 只在 2kHz 采样率下成立。如果采样率为 1kHz（decimation_ratio=4, fpkt 可能不同），公式需要适配。需确认 `fpkt` 是否会随采样率变化。

---

## 文档版本

- v1.0, 2026-05-30: 初始版本，基于 feat_band_V3 分支代码
- v1.1, 2026-05-30: 主管审阅更新 — 新增问题 A/B 分类、三种修复方案评估、当前机制问答、主管审批意见、防御性修复优先级

