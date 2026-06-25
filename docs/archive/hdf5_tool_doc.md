# hdf5_tool.py 详细分析报告

**文件**: `tools/hdf5_tool.py` (1787 行)  
**依赖**: `tools/bin_sync_tool.py` (同步功能核心)  
**分析日期**: 2026-05-21  
**原则**: 只读分析，不修改任何代码

---

## 目录

- [第一部分：数据查看功能](#第一部分数据查看功能)
- [第二部分：数据同步功能](#第二部分数据同步功能)
- [第三部分：V2 适配缺口清单](#第三部分v2-适配缺口清单)

---

## 第一部分：数据查看功能

### 1.1 架构概览

```
HDF5Tool (QMainWindow) ──主窗口──
├── 左侧面板: 目录选择 + 文件列表 (多选)
│   ├── select_directory() → scan_h5_files() 递归扫描 .h5/.hdf5
│   ├── 单选 → view_selected_file()
│   ├── 双击 → on_file_double_clicked()
│   └── 多选 → add_to_sync_list() 批量添加到同步标签页
└── 右侧标签页:
    ├── "查看" → ViewerTab
    └── "同步" → SyncTab
```

### 1.2 ViewerTab 查看功能详解

**入口函数**: `ViewerTab.load_file(file_path)` (L659)

```
load_file()
├── StatisticsPanel.update_stats(file_path)  → 统计信息面板
├── populate_tree(f, root_item)              → 文件结构树
│   └── 递归遍历 Group/Dataset，显示名称、类型、形状
├── on_tree_item_clicked(item)               → 点击树节点
│   ├── show_attributes(obj)                 → 属性面板
│   └── show_data_preview(dataset, path)     → 数据预览
│       ├── show_emg_data()    (EMG 结构化数据)
│       ├── show_imu_data()    (IMU 结构化数据)
│       ├── show_prompt_data() (Prompts 数据)
│       ├── update_table_view() (通用表格)
│       ├── update_text_view()  (通用文本)
│       └── WaveformWidget.plot_data() (波形图)
```

#### 1.2.1 文件结构树 (`populate_tree`, L675-691)

| 项目 | 详情 |
|------|------|
| 函数 | `populate_tree(group, parent_item, path)` |
| 输入 | `h5py.Group` 对象, `QTreeWidgetItem` 父节点 |
| 输出 | 树形控件显示 Group/Dataset，列：[名称, 类型, 形状/值] |

- **通用实现**：递归遍历所有 Group 和 Dataset，无硬编码路径
- V2 的新 dataset（`imu1_all_ble`, `imu2_all_ble`）**能被树形控件展示**

#### 1.2.2 属性面板 (`show_attributes`, L709-724)

| 项目 | 详情 |
|------|------|
| 函数 | `show_attributes(obj)` |
| 输入 | `h5py.Group` 或 `h5py.Dataset` 对象 |
| 输出 | 两列表格：属性名 \| 值 |

- 对 Dataset 额外显示 `[dtype]`, `[shape]`, `[size]`
- 展示 `obj.attrs` 中的所有属性，**通用实现，能展示 V2 的 attrs**（如 `imu1_hw_version`, `v2_no_magnetometer` 等）

#### 1.2.3 统计信息面板 (`StatisticsPanel`, L270-479)

**入口**: `update_stats(file_path)` (L341)

**展示的字段** (L292-312):

| 分类 | 字段 (key) | 标签 | 来源 |
|------|-----------|------|------|
| 基本信息 | 文件名, 文件大小, 创建时间 | — | `os.path` / `os.stat` |
| 同步状态 | `sync_status` | 同步状态 | `f.attrs['sync_status']` |
| Session | `session_index`, `session_count`, `recording_session_id`, `is_multi_session` | — | `f.attrs` |
| 任务信息 | `task_id`, `user_id`, `stage_name`, `template_name` | — | `f.attrs` |
| EMG 数据集 | `emg1_250hz`, `emg1_2khz`, `emg2_250hz`, `emg2_2khz` | — | Dataset shape |
| IMU 数据集 | `imu1a_ble`, `imu1a_100hz`, `imu1b_ble`, `imu1b_100hz` | — | Dataset shape |
| IMU 数据集 | `imu2a_ble`, `imu2a_100hz`, `imu2b_ble`, `imu2b_100hz` | — | Dataset shape |
| MoCap | `mocap` | — | Dataset shape |
| SD 卡 bin | `sd_bin_dev1`, `sd_bin_dev2` | — | `f.attrs` |
| BLE 设备 | `ble_device_dev1`, `ble_device_dev2` | — | `f.attrs` |

**Dataset shape 读取逻辑** (L459-468):
```python
for key in ['emg1_250hz', 'emg1_2khz', 'emg2_250hz', 'emg2_2khz',
           'imu1a_ble', 'imu1a_100hz', 'imu1b_ble', 'imu1b_100hz',
           'imu2a_ble', 'imu2a_100hz', 'imu2b_ble', 'imu2b_100hz']:
    adc_key = key.replace('hz', 'hz_adc')
    if adc_key in f:
        self.labels[key].setText(str(f[adc_key].shape))
    elif key in f:
        self.labels[key].setText(str(f[key].shape))
    else:
        self.labels[key].setText('-')
```

**⚠️ V2 缺口**: 该硬编码列表不包含:
- `imu1_all_ble` / `imu2_all_ble` 的形状
- `imu1_hw_version` / `imu2_hw_version` / `imu1_num_imus` / `imu2_num_imus` 属性
- `total_imu1_all_frames` / `total_imu2_all_frames` 统计

#### 1.2.4 数据预览 — 类型识别 (`show_data_preview`, L726-763)

| 条件 | 识别为 | 处理函数 |
|------|--------|---------|
| 结构化 + `'channels' in dtype.names` | EMG | `show_emg_data()` |
| 结构化 + (`'acc'` 或 `'gyr'` 或 `'gyro'`) in dtype.names | IMU | `show_imu_data()` |
| `'prompts' in path.lower()` | Prompts | `show_prompt_data()` |
| 其他 | 通用数组 | `update_table_view()` + `update_text_view()` |

**V2 的 `IMU_ALL_BLE_DTYPE` 包含 `acc` 和 `gyr` 字段 → 会被正确识别为 IMU 类型** ✅

#### 1.2.5 EMG 数据查看 (`show_emg_data`, L765-857)

**输入 dtype 假设**:
- `channels`: shape `(16,)`, int32
- `frame_id`: uint32 (BLE 帧号)
- `sd_frame_id`: uint32 (SD 卡帧号)
- `time`: float64

**表格列**: 帧序号 → (BLE帧号) → (SD卡帧号) → Ch0..Ch15 → (时间戳)

适用于 `emg*_250hz_adc` 和 `emg*_2khz_adc`。列的存在性由 `dtype.names` 动态检测。

#### 1.2.6 IMU 数据查看 (`show_imu_data`, L859-970)

**输入 dtype 假设**:
- `acc`: shape `(3,)`, float32
- `gyr` 或 `gyro`: shape `(3,)`, float32
- `mag`: shape `(3,)`, float32 (可选)
- `frame_id`: uint32 (可选)
- `sd_frame_id`: uint32 (可选)
- `time`: float64 (可选)

**表格列**: 帧序号 → (BLE帧号) → (SD卡帧号) → Acc_X/Y/Z → Gyr_X/Y/Z → (Mag_X/Y/Z) → (时间戳)

所有列的存在性由 `dtype.names` 动态检测。`mag` 列仅在 `'mag' in dtype.names` 时显示。

**V2 数据集 `imu*_all_ble` 的表现**:
- `IMU_ALL_BLE_DTYPE` 有 `mag` 字段 → 会显示 Mag_X/Y/Z 列 ✅
- V2 数据 `mag = [NaN, NaN, NaN]` → 表格显示 "nan" ⚠️ (不崩溃，但无信息量)
- **`imu_index` 字段完全不被显示** ⚠️ (表格未识别该字段)
- **`has_mag` 字段完全不被显示** ⚠️

#### 1.2.7 Prompt 数据查看 (`show_prompt_data`, L972-1017)

- 两列表格：序号 \| 值
- 自动解码 bytes → str
- 适用于 `prompts` dataset（变长字符串数组）

#### 1.2.8 通用数组查看 (`update_table_view` + `update_text_view`, L1019-1090)

- 1D 数组：单列 Value
- 2D 数组：Ch0..ChN 列头，最多显示 20 列
- 文本视图：shape, dtype, min/max/mean/std, 前 20 行

#### 1.2.9 波形图 (`WaveformWidget.plot_data`, L247-267)

- 1D 数据：直接绘制（最多 2000 点）
- 2D 数据：前 8 通道各画一条线

### 1.3 当前对 HDF5 Schema 的假设汇总

| 假设内容 | 硬编码位置 | V1 | V2 |
|---------|-----------|----|----|
| `emg1_250hz_adc` / `emg2_250hz_adc` 存在 | StatisticsPanel L459 | ✅ | ✅ |
| `emg1_2khz_adc` / `emg2_2khz_adc` 存在 | StatisticsPanel L459 | ✅ | ✅ |
| `imu1a_ble` / `imu1b_ble` / `imu2a_ble` / `imu2b_ble` 存在 | StatisticsPanel L460-462 | ✅ | ✅ (V2 也创建但为空) |
| `imu1a_100hz` / `imu1b_100hz` / `imu2a_100hz` / `imu2b_100hz` 存在 | StatisticsPanel L460-462 | ✅ | ❓ (同步后) |
| `mocap_L` / `mocap_R` (或 `mocap`) | StatisticsPanel L471 | ✅ | ✅ |
| `prompts` dataset | `show_data_preview` L744 | ✅ | ✅ |
| IMU 数据有 `mag` 字段 | `show_imu_data` L879 | ✅ | ⚠️ (V2 有字段但值为 NaN) |
| 没有 `imu_index` / `has_mag` 字段 | — | — | ⚠️ |

### 1.4 V2 通用数据集识别状态

| V2 数据集/属性 | 树形控件 | 属性面板 | 统计面板 | 数据预览 |
|---------------|---------|---------|---------|---------|
| `imu1_all_ble` | ✅ 显示 | ✅ 显示 attrs | ❌ 不显示 shape | ⚠️ 显示但有缺 |
| `imu2_all_ble` | ✅ 显示 | ✅ 显示 attrs | ❌ 不显示 shape | ⚠️ 显示但有缺 |
| `imu_index` 字段 | — | — | — | ❌ 不显示 |
| `has_mag` 字段 | — | — | — | ❌ 不显示 |
| `mag=NaN` | — | — | — | ⚠️ 显示 "nan" |
| `imu1_hw_version` attr | — | ✅ (root attrs) | ❌ 不显示 | — |
| `imu2_hw_version` attr | — | ✅ (root attrs) | ❌ 不显示 | — |
| `imu1_num_imus` attr | — | ✅ (root attrs) | ❌ 不显示 | — |
| `imu2_num_imus` attr | — | ✅ (root attrs) | ❌ 不显示 | — |
| `supports_variable_imus` attr | — | ✅ (dataset attrs) | ❌ 不显示 | — |
| `v2_no_magnetometer` attr | — | ✅ (dataset attrs) | ❌ 不显示 | — |

---

## 第二部分：数据同步功能

### 2.1 架构概览

```
hdf5_tool.py                        bin_sync_tool.py
────────────                        ───────────────
SyncTab.start_sync()
├── SyncWorker.run()                sync_h5_with_bin()
│   ├── _find_bin_files()           ├── EMGBinParser.parse()
│   └── sync_h5_with_bin() ───────► ├── IMUBinParser.parse()
│                                   ├── EMG 校验 + 2kHz 构建
│                                   ├── IMU 100Hz 构建
│                                   └── 写入 HDF5 + 更新 attrs
```

**hdf5_tool.py 本身不包含同步算法**，所有核心逻辑在 `bin_sync_tool.py` 中。hdf5_tool.py 的职责是：
1. 提供 GUI 界面（文件选择、设备勾选、进度条、日志）
2. 从 HDF5 attrs 中读取 `sd_bin_dev1`/`sd_bin_dev2` 自动查找 bin 文件
3. 调用 `sync_h5_with_bin()` 执行实际同步

### 2.2 同步入口与调用链

#### 2.2.1 hdf5_tool.py 侧

```
SyncTab.start_sync()                           L1390
├── 收集勾选的设备: devices = ['emg1','emg2','imu1','imu2']  L1398-1402
├── SyncWorker(h5_files, bin_dir, devices, validate)         L1414
│   └── SyncWorker.run()                                     L103
│       ├── _find_bin_files(h5_path, device_id)              L61
│       │   └── 读 f.attrs['sd_bin_dev{device_id}'] → "{prefix}_emg.bin" / "{prefix}_imu.bin"
│       └── sync_h5_with_bin(h5_path, emg_bin, imu_bin, device_id, verify, set_synced)  L176
```

**`_find_bin_files()` 查找逻辑** (L61-101):
1. 从 HDF5 attrs 中读取 `sd_bin_dev{N}` 属性（如 `"S001_L_260312_143025"`）
2. 拼接 `{前缀}_emg.bin` 和 `{前缀}_imu.bin`
3. 在用户选择的 bin 目录中查找

**设备选择 → 同步映射** (L113-118):
| 勾选框 | devices 列表值 | device_id |
|--------|-------------|-----------|
| EMG1 或 IMU1 | 包含 `'imu1'` 或 `'emg1'` | 1 |
| EMG2 或 IMU2 | 包含 `'imu2'` 或 `'emg2'` | 2 |

#### 2.2.2 bin_sync_tool.py 侧 (`sync_h5_with_bin`, L232-591)

```
sync_h5_with_bin(h5_path, emg_bin_path, imu_bin_path, device_id, verify, set_synced)
│
├── EMGBinParser(emg_bin_path).parse()                    L254
│   └── 解析 EMG bin: header(126B) + frames(52B each)
│       每帧: frame_id(u4) + 16ch × 3bytes(BE int24)
│
├── IMUBinParser(imu_bin_path).parse()                    L255
│   └── 解析 IMU bin: header(126B) + frames(40B each)
│       每帧: frame_id(u4) + 2 chips × 18B
│       每 chip: acc(6B BE) + gyr(6B BE) + mag(6B LE)
│
├── [校验] 比对 250Hz BLE 数据与 bin 中 SD 帧             L296-341
│
├── [EMG 2kHz 构建]                                       L344-428
│   ├── 对每个 BLE 帧，展开 8 个 SD 帧 (DOWNSAMPLE_RATIO=8)
│   ├── sd_frame_id = ble_frame_id * 8 + j  (j=0..7)
│   ├── 从 EMGBinParser 取帧，缺失则插值
│   ├── 写入 emg{device_id}_2khz_adc
│   └── attrs: lsb_uv, source_bin, sync_time, filled/missing_frames
│
├── [IMU 100Hz 构建]                                      L436-573
│   ├── IMU 帧号 = EMG SD帧号 // EMG_IMU_RATIO (20)
│   ├── 去重 → imu_frame_ids_unique
│   ├── 从 IMUBinParser 取帧 (imu1, imu2)
│   ├── 构建 data_imu_100hz (imu1 → imuA)
│   ├── 构建 data_imu_b_100hz (imu2 → imuB)
│   ├── 写入 imu{device_id}a_100hz / imu{device_id}b_100hz
│   └── attrs: sample_rate, source_bin, sync_time, filled/missing_frames
│
└── [更新状态]                                            L576-579
    └── set_synced=True → f.attrs["sync_status"] = "synced"
```

### 2.3 同步输入

| 输入 | 来源 | 说明 |
|------|------|------|
| HDF5 文件 (.h5) | 用户选择 | 必须包含 `emg{id}_250hz_adc` dataset |
| EMG bin 文件 | `_find_bin_files()` 自动查找 | 通过 `sd_bin_dev{N}` attr 匹配文件前缀 |
| IMU bin 文件 (可选) | `_find_bin_files()` 自动查找 | 同上 |
| `sd_bin_dev{N}` attr | storage_server 写入 | bin 文件前缀，如 `"S001_L_260312_143025"` |
| `frame_id` 字段 | `emg*_250hz_adc` dataset | BLE 帧号，用于计算 SD 帧号 |
| `sd_frame_id` 字段 | 计算: `ble_frame_id * 8 + j` | 映射到 bin 帧号 |
| `sync_status` attr | 同步前检查，同步后更新 | `"pending"` → `"synced"` |

### 2.4 同步输出

| 输出 Dataset | dtype | 说明 |
|-------------|-------|------|
| `emg{id}_2khz_adc` | `EMG_2KHZ_ADC_DTYPE` | 2kHz 16ch raw ADC (int32) |
| `imu{id}a_100hz` | `IMU_100HZ_DTYPE` | IMU 芯片 A 100Hz (固定) |
| `imu{id}b_100hz` | `IMU_100HZ_DTYPE` | IMU 芯片 B 100Hz (固定) |
| `imu{id}_100hz` (兼容) | `IMU_100HZ_DTYPE` | 旧版单数据集 |

**写入的 attrs**:
- `lsb_uv`, `source_bin`, `sync_time`, `filled_frames`, `missing_frames` (每个 dataset)
- `sync_status = "synced"`, `sync_time` (root attrs, 仅 set_synced=True)

### 2.5 当前同步逻辑对 V1 的硬编码假设

#### 2.5.1 IMUBinParser (bin_sync_tool.py L169-227)

| 假设 | 位置 | V1 正确 | V2 兼容 |
|------|------|---------|---------|
| 固定 2 个 IMU 芯片 | L220-221: `imu1 = parse_chip(...)`, `imu2 = parse_chip(...)` | ✅ | ❌ V2 0-3 个 |
| IMU 帧大小 = 40 字节 (4+36) | L203: `IMU_FRAME_SIZE = 4 + 36` | ✅ 2×18=36 | ❌ V2 N×18 |
| Acc/Gyro Big Endian | L212: `struct.unpack('>6h', ...)` | ✅ ICM-20948 | ❌ V2 全 LE |
| Mag Little Endian | L213: `struct.unpack('<3h', ...)` | ✅ ICM-20948 | ❌ V2 无 Mag |
| 必有 Mag 字段 | L217: `'mag': [x * SCALE_MAG ...]` | ✅ | ❌ V2 无磁力计 |
| SCALE_ACCEL = 16/32768 | L69: `SCALE_ACCEL = 16.0 / 32768.0` | ✅ ±16g | ❌ V2 ±32g |
| 无 V4.1 Footer | L202-224: 读到文件末尾，不处理 footer | ✅ | ❌ V2 bin 有 footer |

#### 2.5.2 sync_h5_with_bin IMU 同步部分 (bin_sync_tool.py L436-573)

| 假设 | 位置 | V1 正确 | V2 兼容 |
|------|------|---------|---------|
| 固定 2 个 IMU 100Hz 数据集 (a/b) | L497-498: `imu{id}a_100hz`, `imu{id}b_100hz` | ✅ | ❌ V2 0-3 个 |
| IMU_100HZ_DTYPE 必有 mag | L451-457: mag 字段 mandatory | ✅ | ❌ V2 无 mag |
| IMU 数据固定填 acc+gyr+mag | L470-472: `imu1['acc/gyr/mag']` | ✅ | ❌ V2 结构不同 |
| 第二个芯片存在且结构相同 | L513-515: `imu2['acc/gyr/mag']` | ✅ | ❌ V2 可能不存在 |
| 无 `imu*_all_100hz` 长表 | — | — | ❌ 未创建 |

#### 2.5.3 EMG 同步部分 — V1/V2 兼容 ✅

EMG bin 格式在 V1 和 V2 之间相同（header 126B + 16ch×3bytes per frame），EMG 同步逻辑不依赖 V1/V2 硬件差异。**EMG 同步路径对 V2 安全**。

#### 2.5.4 hdf5_tool.py SyncTab 选项 (L1227-1235)

| 假设 | 位置 | V2 兼容 |
|------|------|---------|
| "IMU1 A+B (imu1a/1b_ble → 100hz)" 标签 | L1232 | ❌ 无 V2 通用选项 |
| "IMU2 A+B (imu2a/2b_ble → 100hz)" 标签 | L1234 | ❌ 无 V2 通用选项 |
| 勾选 imu1 → 只处理 device 1 | L1398-1402 | ⚠️ 需新增 "IMU1 All" 选项 |

### 2.6 V2 同步适配风险矩阵

| 风险项 | 严重程度 | 影响范围 | 说明 |
|--------|---------|---------|------|
| IMUBinParser 无法解析 V2 bin | **高** | 同步完全失败 | 帧大小、字节序、IMU 数量均不同 |
| SCALE_ACCEL 用错 (16→32) | **高** | IMU 数值错误 (2x 偏差) | V2 LSM6DSV32X 用 32g 量程 |
| 无 Mag → IMU_100HZ_DTYPE 填零 | **中** | 100Hz dataset mag 无意义 | V2 应填 NaN 或不填 |
| V2 bin Footer V4.1 被当作数据帧 | **中** | 最后几帧 IMU 数据错乱 | Footer 数据可能被 parse 为 IMU 帧 |
| 固定 2 个 100Hz dataset 不够 | **中** | 第 3 个 IMU 无处存储 | V2 最多 3 个 IMU/设备 |
| 无 `imu*_all_100hz` 长表 | **中** | 无法按 IMU index 查询 | 后续分析不友好 |
| hdf5_tool.py 同步选项 UI | **低** | 用户体验 | 标签误导 V2 用户 |
| StatisticsPanel 不显示 V2 字段 | **低** | 查看不便 | 不影响功能 |

---

## 第三部分：V2 适配缺口清单

按优先级从高到低排列。

### 优先级 P0 — 阻塞 V2 同步功能

#### GAP-1: IMUBinParser 完全不兼容 V2 IMU bin (bin_sync_tool.py L169-227)

**当前代码**:
```python
IMU_FRAME_SIZE = 4 + 36  # 固定 40 字节 = 4 + 2×18
def parse_chip(b):
    ag = struct.unpack('>6h', b[0:12])   # Big Endian
    m = struct.unpack('<3h', b[12:18])   # Little Endian
    return {'acc': [...], 'gyr': [...], 'mag': [...]}
imu1 = parse_chip(raw_data[0:18])
imu2 = parse_chip(raw_data[18:36])
```

**V2 需要的**:
- 帧大小 = `4 + N × 18`（N 来自 header 或动态检测）
- Acc/Gyro 全部 Little Endian: `struct.unpack('<6h', b[0:12])`
- 无 Mag（Reserved 6 bytes）
- SCALE_ACCEL = 32.0/32768.0
- 检测并跳过 V4.1 Footer（CRC + frame_count + version）

**修改建议**:
1. 在 `IMUBinParser.__init__` 中增加 `hw_version` 参数
2. 添加 `parse_imu_v2()` 方法（可变 IMU 数、全 LE、无 Mag）
3. 从 bin header 的保留字段或 HDF5 attr 中获取 V1/V2 标识
4. 增加 Footer 检测逻辑：在接近文件末尾时检查 V4.1 Footer magic

#### GAP-2: sync_h5_with_bin IMU 部分硬编码 V1 (bin_sync_tool.py L436-573)

**当前代码**: 只写 `imu{id}a_100hz` 和 `imu{id}b_100hz`，使用 `IMU_100HZ_DTYPE`（强制含 mag）

**修改建议**:
1. 根据 `hw_version` 分支：
   - V1: 保持现有 `imu{id}a_100hz` / `imu{id}b_100hz` 写入
   - V2: 新增 `imu{id}_all_100hz` 长表（dtype 与 `IMU_ALL_BLE_DTYPE` 结构一致），每 IMU 一行
2. 创建 `IMU_ALL_100HZ_DTYPE`（或复用 `IMU_ALL_BLE_DTYPE`），含 `imu_index`、`has_mag`、`mag`(NaN for V2)
3. 为 `imu{id}_all_100hz` 设置 attrs: `supports_variable_imus=True`, `v2_no_magnetometer=True`

### 优先级 P1 — 影响查看体验

#### GAP-3: StatisticsPanel 硬编码 dataset 列表 (hdf5_tool.py L459-468)

**缺失字段**:
- `imu1_all_ble` / `imu2_all_ble` shape
- `imu1_hw_version` / `imu2_hw_version` attr
- `imu1_num_imus` / `imu2_num_imus` attr
- `total_imu1_all_frames` / `total_imu2_all_frames` root attr

**修改建议**: 在 dataset shape 读取循环中增加 `'imu1_all_ble'`、`'imu2_all_ble'`；在 attr 读取中增加 V2 元数据字段。

#### GAP-4: show_imu_data 不显示 imu_index 和 has_mag (hdf5_tool.py L859-970)

**当前行为**: 表格只显示 Acc/Gyr/Mag 列，`imu_index` 和 `has_mag` 字段被忽略

**修改建议**:
1. 在表头中为 `IMU_ALL_BLE_DTYPE` 增加 `IMU索引` 列（显示 `imu_index` 值）
2. 根据 `has_mag` 字段值给 Mag 列着色（has_mag=1 正常，has_mag=0 灰色）
3. 对 NaN 值显示 "-" 而非 "nan"

#### GAP-5: SyncTab IMU 选项 UI (hdf5_tool.py L1231-1235)

**当前标签**: "IMU1 A+B (imu1a/1b_ble → 100hz)" / "IMU2 A+B (imu2a/2b_ble → 100hz)"

**修改建议**:
1. 增加 "IMU1 All (imu1_all_ble → 100hz)" / "IMU2 All (imu2_all_ble → 100hz)" 选项
2. 保留旧选项用于 V1 兼容
3. 或改为自动检测：勾选 IMU1 时，根据 HDF5 中的 `imu1_hw_version` 自动选择同步目标

### 优先级 P2 — 完善性补充

#### GAP-6: _find_bin_files 不支持 V2 bin 命名差异 (hdf5_tool.py L61-101)

**当前**: 通过 `sd_bin_dev{N}` attr 查找 `{prefix}_emg.bin` / `{prefix}_imu.bin`

**V2 场景**: 如果 V2 bin 文件名格式有变化（如增加版本后缀），此函数需要适配。如果文件命名规则不变，此 gap 可忽略。

#### GAP-7: 同步日志不区分 V1/V2 (hdf5_tool.py L186-198)

**当前**: 日志固定显示 "IMU: {imu_frames}帧 (来自bin:{imu_filled}, 缺失:{imu_missing})"

**修改建议**: 增加 hw_version 信息到日志：`IMU(V2, 3chips): {imu_frames}帧`

---

## 附录：数据流图

### A.1 查看路径数据流

```
HDF5 文件 (.h5)
│
├── f.attrs ───────────────→ StatisticsPanel     → 元数据显示
├── f.keys() ──────────────→ populate_tree()     → 文件结构树
│
├── 点击 Dataset 节点
│   ├── obj.attrs ─────────→ show_attributes()   → 属性面板
│   └── dataset[:] ────────→ show_data_preview()
│       ├── [EMG]  ────────→ show_emg_data()     → 表格 + 文本 + 波形
│       ├── [IMU]  ────────→ show_imu_data()     → 表格 + 文本 + 波形
│       ├── [Prompts] ─────→ show_prompt_data()  → 表格 + 文本
│       └── [其他] ────────→ update_table_view() + update_text_view()
│
└── waveform.plot_data() ──→ Matplotlib FigureCanvas
```

### A.2 同步路径数据流

```
用户操作                              bin_sync_tool
────────                              ────────────
1. 选择 bin 目录
2. 添加 H5 文件列表
3. 勾选同步选项 (EMG1/2, IMU1/2)
4. 点击"开始同步"
   │
   ▼
SyncWorker.run()
│
├── for each H5 file:
│   ├── _find_bin_files()
│   │   ├── 读 f.attrs['sd_bin_dev1'] → "S001_L_260312_143025"
│   │   └── 拼接 "{prefix}_emg.bin" / "{prefix}_imu.bin"
│   │
│   └── sync_h5_with_bin() ──────────────────────►
│       │
│       ├── EMGBinParser.parse()
│       │   └── 126B header + N×52B frames
│       │       frame: [frame_id(u4)] [16ch×3B int24 BE]
│       │
│       ├── IMUBinParser.parse()
│       │   └── 126B header + N×40B frames
│       │       frame: [frame_id(u4)] [chip1(18B)] [chip2(18B)]
│       │       chip:  [acc(6B BE)] [gyr(6B BE)] [mag(6B LE)]
│       │
│       ├── [校验] 250Hz BLE ↔ bin SD 帧比对
│       │
│       ├── [EMG 2kHz]
│       │   250Hz frame_id × 8 → 8 个 SD 帧
│       │   从 EMGBinParser 取帧 → emg{id}_2khz_adc
│       │
│       ├── [IMU 100Hz]
│       │   SD帧号 // 20 → IMU 帧号
│       │   从 IMUBinParser 取帧 → imu{id}a_100hz / imu{id}b_100hz
│       │
│       └── f.attrs['sync_status'] = 'synced'
│
└── Signal: progress → 进度条更新
    Signal: log → 日志面板追加
    Signal: finished → 弹窗提示
```

---

## 文档版本

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-05-21 | 初稿，基于 hdf5_tool.py r1 和 bin_sync_tool.py 分析 |
