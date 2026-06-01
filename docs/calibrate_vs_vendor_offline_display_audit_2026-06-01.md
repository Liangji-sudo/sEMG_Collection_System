# calibrate_tool vs 供应商 wband_emg_V3 离线显示差异审计

> 日期: 2026-06-01
> 分支: fix_sync
> 状态: 第一阶段只读审计，未改业务代码

---

## 1. 供应商 V3 离线显示流程

### 1.1 数据加载 (wband_emg_client_V5.py)

**入口函数**: `start_replay_mode()` → `playback_timer_tick()` / `update_playback_frame()`

加载流程:
1. 选择 `.bin` 文件 (magic `0xAABBCCDD`)
2. 解析 Header (126 字节):
   - `magic`, `sample_rate`, `gain_idx`, `bit_depth`, `imu_en`
3. 计算 LSB:
   ```python
   base_lsb = 0.476837       # 4.0V ref / 2^23 * 1e6
   lsb_uV = base_lsb / (actual_gain * 10)   # gain=12 → 0.003974 μV/LSB
   if bit_depth == 16: lsb_uV *= 16
   ```
4. 逐帧解析: 每帧 4B frame_id + 16ch × 3B (24-bit big-endian signed)
5. 应用 LSB → uV → 存储到 `playback_emg_data` (已滤后 uV 值)
6. 离线滤波: `SignalFilter.do_filter()` — **零相位 filtfilt**, Q=50

### 1.2 滤波参数 (signalfilter.py)

| 参数 | 值 |
|------|-----|
| 带通 | 20-100 Hz, 4th-order Butterworth |
| 工频 | 50, 100, 150... Hz iirnotch |
| 离线 Q 值 | **50** (精确陷波) |
| 在线 Q 值 | 15 (防震荡) |
| 方法 | `signal.filtfilt` (零相位) |

### 1.3 绘图参数 (custom_widgets.py: OffsetSeriesPlot)

| 参数 | 值 |
|------|-----|
| 时间窗口 | **固定 PLOT_WINDOW_S = 5 秒** |
| 通道布局 | 16 通道**同轴堆叠**，单图 |
| Y Offset | **DEFAULT_OFFSET = 300 μV** |
| Clamp | 可选，`±offset*0.48` 裁剪 |
| 图表库 | **pyqtgraph** (QCustomPlot) |
| 刷新率 | PLOT_REFRESH_RATE_HZ = **60 Hz** |
| Buffer | `(16, 1, fs*5)` 循环缓冲区 |

**关键显示逻辑** (`replot()`):
```python
for i in range(num_channels):
    y_base = (num_channels - 1 - i) * self.offset   # CH16 at bottom
    plot_data = np.clip(raw_data, -clip_limit, clip_limit) if clamp else raw_data
    self.graphs[i].setData(x_axis, plot_data + y_base)
```

### 1.4 通道映射

```python
CHANNELS_MAP = [15, 16, 14, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]  # V5
```

物理顺序: CH1-CH16 → 显示顺序: CH15, CH16, CH14, CH1, CH2, ..., CH13

### 1.5 回放模式 (playback_timer_tick)

- 每 tick 步进 `fs / 60` 个采样点
- 追加到 OffsetSeriesPlot buffer → `replot()`
- 所有数据**预加载并滤波**到内存，显示时只做 buffer 填充 + replot
- 拖拽滑块: `reset_buffers()` → `update_playback_frame()` 重新填充最后 5s

---

## 2. calibrate_tool 当前显示流程

### 2.1 数据加载 (load_emg_data)

优先级: `emg*_250hz_adc` > `emg*_250hz` > `emg*_2khz_adc` > `emg*_2khz` > `emg*`
**当前默认加载 250Hz 数据**。

### 2.2 LSB 转换 (calculate_lsb_uv)

```python
BASE_LSB_24BIT = 0.2861      # 2.4V ref / 2^23 * 1e6  ← 与供应商不同!
lsb_uv = BASE_LSB_24BIT / (gain * HARDWARE_FRONTEND_GAIN)
# gain=12 → 0.002384 μV/LSB
```

### 2.3 滤波参数 (EMGFilter)

| 参数 | 值 |
|------|-----|
| 带通 | 20-100 Hz, 4th-order Butterworth |
| 工频 | 50, 100, 150 Hz iirnotch |
| Q 值 | **15** (与供应商离线 Q=50 不同) |
| 方法 | `scipy_signal.filtfilt` (零相位) |

### 2.4 绘图参数 (init_plots / update_emg_plot)

| 参数 | 值 |
|------|-----|
| 时间窗口 | **15000 采样点** (默认) |
| @250Hz | = **60 秒** (严重压缩) |
| @2kHz | = **7.5 秒** |
| 通道布局 | **16 个独立子图 × 2 设备** = 32 子图 |
| Y 轴 | 每通道自动范围，无堆叠 offset |
| 图表库 | **matplotlib** |
| 降采样 | 拖动 800 点，正常 2500 点 (最近优化) |

### 2.5 通道映射

从 H5 读取，L015 旧 H5 经 one_to_many_adc_search 同步后识别为 **physical** 顺序。
**与供应商 V5 映射不同**。

---

## 3. 两者差异汇总表

| 维度 | 供应商 V3 离线 | calibrate_tool | 影响 |
|------|--------------|----------------|------|
| **数据源** | 2kHz 原始 .bin | H5 (优先 250Hz) | 采样率不同 |
| **采样率** | 2kHz (bin 原始) | 250Hz (默认) | 8× 差异 |
| **LSB (uV)** | 0.476837 (4.0V ref) | 0.2861 (2.4V ref) | **1.667× uV 差异** |
| **时间窗口** | 固定 5 秒 | 15000 采样点 = 60s@250Hz | **12× 时间压缩** |
| **带通滤波** | 20-100Hz, Butterworth 4th | 20-100Hz, Butterworth 4th | 一致 |
| **陷波 Q** | 50 (离线) | 15 | 更宽陷波 |
| **filtfilt** | 是 | 是 | 一致 |
| **通道布局** | 单轴堆叠 Offset=300uV | 32 独立子图自动缩放 | **视觉完全不同** |
| **通道映射** | V5: [15,16,14,1..13] | physical (L015) | 通道顺序不同 |
| **图表库** | pyqtgraph (QCustomPlot) | matplotlib | 性能差距 |
| **Clamp** | 可选 | 无 | — |
| **Y 轴范围** | 固定 offset 堆叠 | 每通道自动 | 幅值感知不同 |

---

## 4. LSB 不一致详细分析

### 4.1 代码库中存在两个 BASE_LSB_24BIT 值

**0.2861 (2.4V ref)** — 使用位置:
- `ble_server.py` (line 110)
- `calibrate_tool.py` (line 39)
- `ble_server_sim_v3.py` (line 80)
- `ble_server_sim_v2.py` (line 77)

**0.476837 (4.0V ref)** — 使用位置:
- `ble_server_real.py` (line 94)
- `ble_server_sim.py` (line 82)
- `bin_sync_tool.py` (line 145)
- **供应商 wband_emg_V3** (line 1532)

### 4.2 数据流追踪

```
ESP32 SD卡 → .bin 文件 (raw ADC)
    ↓
bin_sync_tool (LSB=0.476837) → H5 emg*_2khz_adc (raw ADC, lsb_uv attr=0.476837/120)
    ↓
calibrate_tool (LSB=0.2861) → 显示 uV (使用错误的 0.2861 转换)
    → 结果: 显示 uV = 真实 uV × 0.2861/0.476837 = 真实 uV × 0.60
```

```
ESP32 BLE → ble_server (LSB=0.2861, 仅用于自己滤波显示)
    ↓
storage_server → H5 emg*_250hz_adc (raw ADC, 无 lsb_uv attr)
    ↓
calibrate_tool (LSB=0.2861) → 显示 uV
    → 与 ble_server 一致，但与供应商 (0.476837) 差 1.667×
```

### 4.3 结论

- `calibrate_tool` 应优先读取 H5 dataset attr `lsb_uv` 进行转换
- 若无 attr，应使用 `bin_sync_tool` 的 0.476837 作为默认值（与供应商一致）
- 当前 `calibrate_tool` 硬编码 0.2861 导致 uV 值偏低约 40%

---

## 5. 数值对比结论

### 5.1 L015 H5 2kHz 数据 vs 对应 bin 数据

**预期结论** (基于代码分析):
- H5 `emg*_2khz_adc` 的 raw ADC 值应等于 bin 对应 sd_frame_id 的 raw ADC 值
- 差异仅在于 calibrate_tool 的 LSB 转换系数（0.2861 vs 0.476837）
- **数值正确，显示系数错误**

### 5.2 L015 H5 250Hz 数据

- 250Hz 是 BLE 下采样后的数据，帧率 250Hz
- raw ADC 值正确（来自 ESP32 BLE 传输）
- 显示 uV 受 calibrate_tool LSB 影响

### 5.3 推荐对比方法

运行 `_diag_compare_l015.py` 脚本（同目录）来验证:
```bash
python _diag_compare_l015.py
```

---

## 6. 截图差异原因排序（可能性从高到低）

| 排名 | 原因 | 影响程度 |
|------|------|---------|
| **1** | **时间窗口**: 60s@250Hz vs 5s@2kHz (12× 压缩) | ★★★★★ |
| **2** | **LSB 单位**: 0.2861 vs 0.476837 (1.67× 幅值差异) | ★★★★☆ |
| **3** | **通道布局**: 独立子图自动缩放 vs 同轴堆叠 Offset=300uV | ★★★★☆ |
| **4** | **数据源采样率**: 250Hz BLE 下采样 vs 2kHz bin 原始 | ★★★☆☆ |
| **5** | **通道映射**: physical vs V5 映射 | ★★☆☆☆ |
| **6** | **滤波 Q**: Q=15 vs Q=50 | ★☆☆☆☆ |

---

## 7. 是否需要改 calibrate_tool

**是，建议修改。** 但分阶段：

### 第一阶段（信息显示，低风险）
- 读 H5 2kHz dataset 的 `lsb_uv` attr → 使用正确的 LSB 转换
- 状态栏显示当前加载的数据源和窗口秒数
- 不弹窗

### 第二阶段（视图改进，中等风险）
- 增加"供应商风格视图"模式:
  - 5s 窗口（2kHz: 10000 点, 250Hz: 1250 点）
  - 16 通道同轴堆叠 + Offset(uV)=300
  - 可选 Clamp
- 增加 EMG 数据源选择: 250Hz / 2kHz / 自动
- 切到 2kHz 时默认窗口 10000 点
- 切到 250Hz 时默认窗口 1250 点

### 第三阶段（LSB 纠正，需确认硬件）
- 统一 `BASE_LSB_24BIT` 为 0.476837（与供应商一致）
- 或从 H5 attrs 动态读取

---

## 8. 建议最小修改方案（待主管确认后执行）

```python
# calibrate_tool.py 修改点:

# 1. LSB: 优先读 H5 dataset attr
def _get_lsb_uv(self, dataset_name):
    if self.h5_file and dataset_name in self.h5_file:
        ds_lsb = self.h5_file[dataset_name].attrs.get('lsb_uv')
        if ds_lsb is not None:
            return float(ds_lsb)
    # fallback: 与供应商一致的 0.476837
    return 0.476837 / (DEFAULT_GAIN * HARDWARE_FRONTEND_GAIN)

# 2. 状态栏: 显示窗口秒数和数据源
# 3. 供应商视图: 新增 toggle 按钮切换
# 4. 窗口自适应: 2kHz→10000点, 250Hz→1250点
```

---

## 附录 A: 相关文件路径

| 文件 | 说明 |
|------|------|
| `wband_emg_V3/wband_emg_client_V5.py` | 供应商上位机主程序 |
| `wband_emg_V3/signalfilter.py` | 供应商滤波器 |
| `wband_emg_V3/custom_widgets.py` | 供应商 OffsetSeriesPlot |
| `tools/calibrate_tool.py` | 我们的 H5 可视化工具 |
| `tools/bin_sync_tool.py` | 同步引擎 (含 0.476837 LSB) |
| `ble_server.py` | BLE 服务器 (含 0.2861 LSB) |
| `ble_server_real.py` | 真实 BLE 服务器 (含 0.476837 LSB) |
| `storage_server.py` | H5 存储 (写 raw ADC) |

## 附录 B: 供应商滤波代码 (signalfilter.py 离线滤波)

```python
def do_filter(self, data):
    """离线滤波: 零相位 filtfilt, Q=50"""
    y = data.copy()
    if self.isbandpass:
        y = signal.filtfilt(self.b_bandpass, self.a_bandpass, y, axis=0)
    if self.ispowerline:
        for filt in self.powerline_filters_offline:  # Q=50
            y = signal.filtfilt(filt['b'], filt['a'], y, axis=0)
    return y
```

## 附录 C: 供应商绘图核心 (OffsetSeriesPlot.replot)

```python
def replot(self):
    clip_limit = self.offset * 0.48
    for i in range(self.num_channels):
        y_base = (self.num_channels - 1 - i) * self.offset
        plot_data = np.clip(self.buffers[i, 0], -clip_limit, clip_limit) if clamp else self.buffers[i, 0]
        self.graphs[i].setData(self.x_axis, plot_data + y_base)
    # 指示线 + replot
```
