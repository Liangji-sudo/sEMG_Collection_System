# 采集界面实时 EMG 显示 vs 供应商 V3 上位机审计

> 日期: 2026-06-01
> 分支: fix_sync
> 状态: 第一阶段只读审计，未改业务代码

---

## 1. 当前实时数据链路

```
ESP32 BLE (250Hz, raw ADC, physical order)
  │
  ▼
ble_server.py parse_packet()
  │ int.from_bytes(big, signed) → raw ADC
  │ LSB: 0.2861 / (gain × 10) → uV ★ WRONG (应为 0.476837)
  │ CHANNELS_MAP_V2: [15,16,14,1,2,...,13] → mapped
  │ EMGRealtimeFilter(Q=15, lfilter, 250Hz) → filtered uV
  │ return {'raw': mapped_raw_adc, 'uv': filtered_uv_mapped}
  │
  ▼
realtimeEngine.js (Node.js WebSocket bridge)
  │ dev1.uv → transposeEMG() → [ch][frame]
  │ broadcastToClients({ emg1: transposedUV, emg2: ..., imu1: ..., ... })
  │
  ▼
waveform.js WaveformController
  │ rendererManager.get('emg1').renderPoints(data.emg1)
  │ feedQualityMonitor(1, data.emg1)  → signal-quality.js
  │
  ▼
waveform-renderer.js WaveformRenderer.renderPoints()
  │ Canvas 2D 绘制
  │ 每通道独立垂直 band: channelSpacing = height / channelCount
  │ scale = channelHeight / (2 × offset)  ← offset 默认 150 uV
  │ y = centerY - value × scale
  │ WINDOW_DURATION = 3s, 250Hz → 750 points
  │ 逐点绘制，lineTo 连线
```

## 2. 当前配置参数

| 参数 | ble_server.py | waveform-renderer.js | 供应商 V3 |
|------|-------------|---------------------|-----------|
| **BASE_LSB_24BIT** | **0.2861** | — | **0.476837** |
| LSB uV (gain=12) | 0.002384 | — | 0.003974 |
| 滤波 Q (notch) | **15** | — | 15 (online) / 50 (offline) |
| 滤波方法 | lfilter (streaming) | — | lfilter (streaming) |
| 带通 | 20-100Hz, 4th Butter | — | 20-100Hz, 4th Butter |
| 显示窗口 | — | **3 秒** | **5 秒** |
| EMG Offset | — | **150 uV** (HTML) | **300 uV** |
| 显示采样率 | — | 250 Hz | 250 Hz |
| 通道映射 | V2 mapped | (收到已 mapped) | V5 mapped |
| 通道布局 | 独立 band / 无堆叠 | 每通道均分高度 | **同轴堆叠** |
| Clamp | — | — | 可选 ±0.48×offset |
| 渲染引擎 | — | Canvas 2D lineTo | QCustomPlot (pyqtgraph) |

## 3. 关键发现

### 3.1 LSB 不一致 ★ CRITICAL

**ble_server.py line 110:**
```python
BASE_LSB_24BIT = 0.2861  # 2.4V ref
```

**供应商 V3 (wband_emg_client_V5.py line 1532):**
```python
base_lsb = 0.476837      # 4.0V ref
```

**bin_sync_tool.py line 145:**
```python
BASE_LSB_24BIT = 0.476837
```

**calibrate_tool.py (已修正):**
```python
BASE_LSB_24BIT_VENDOR = 0.476837
```

**结论**: ble_server.py 的 LSB 比供应商低 1.667×。采集界面显示的 uV 幅值偏低约 40%。

**影响范围**:
- ble_server 发送给前端的 `uv` 字段（滤波后 uV）
- 存储到 H5 的 250Hz 数据是 **raw ADC**（不受 LSB 影响） ← 安全
- 前端 signal-quality.js 的 RMS/clip 阈值用 0.2861 计算，与实际不匹配
- 前端 waveform-renderer.js 不直接使用 LSB（数据已是 uV）

### 3.2 滤波 Q 值

ble_server 的 Q=15 与供应商**在线滤波**一致。供应商离线滤波用 Q=50 配合 filtfilt。实时场景应保持 Q=15（lfilter + 低 Q 避免振铃），**不需要改为 Q=50**。

### 3.3 前端显示差异

当前前端波形显示与供应商的主要差距在**布局方式**：

**当前前端** (waveform-renderer.js):
- 16 通道均分 Canvas 高度
- 每个通道在自己的 band 内垂直居中
- 不同通道间无视觉叠加
- offset=150 uV 仅控制缩放，非通道偏移
- 窗口 3 秒

**供应商 V3** (OffsetSeriesPlot):
- 16 通道**同轴叠加**
- CH1 在最上 (y=15×300=4500), CH16 在最下 (y=0)
- 每个通道画在同一条 y 轴上，人工 offset 分离
- Clamp 防止波形重叠
- 窗口 5 秒

### 3.4 signal-quality.js 硬编码 LSB

`public/scripts/signal-quality.js` line 30:
```javascript
var BASE_LSB_24BIT = 0.2861;
```

clip limit 计算依赖此值。如果 ble_server LSB 修正为 0.476837，这里也需要同步修正。

## 4. 差异汇总表

| 维度 | 当前实时采集界面 | 供应商 V3 实时 | 影响程度 |
|------|---------------|---------------|---------|
| **LSB 常数** | 0.2861 | 0.476837 | ★★★★★ |
| **显示窗口** | 3 秒 | 5 秒 | ★★★☆☆ |
| **Offset** | 150 uV | 300 uV | ★★★☆☆ |
| **通道布局** | 独立 band | 同轴堆叠 | ★★★★☆ |
| **Clamp** | 无 | 可选 | ★★☆☆☆ |
| **滤波 Q** | 15 (正确) | 15 (在线) | — |
| **滤波方法** | lfilter (正确) | lfilter | — |
| **带通频率** | 20-100Hz (正确) | 20-100Hz | — |
| **通道映射** | V2 mapped (正确) | V5 mapped | — |

## 5. 各问题回答

### 5.1 采集界面现在画的是 raw ADC 还是 uV？
**uV**（滤波后）。ble_server 将 raw ADC × LSB(0.2861) → uV → 滤波 → 发送。

### 5.2 ble_server.py 发给前端的是滤波前还是滤波后的 uV？
**滤波后的 uV**。`parse_packet()` 返回 `{'uv': emg_uv_filtered}`。

### 5.3 当前实时 LSB 是否还是旧常量 0.2861？是否需要改成 0.476837？
**是，仍是 0.2861。需要改**。与供应商/bin_sync_tool/calibrate_tool 统一为 0.476837。

### 5.4 当前实时滤波参数是否和供应商一致？
**一致**。Q=15 对应供应商在线滤波 `q_online=15`，lfilter 方法一致，带通参数一致。不需要修改。

### 5.5 前端实时图现在是怎样画的？
Canvas 2D 逐点 lineTo。16 通道均分高度，每个通道在自己的 band 内绘制，offset 仅控制缩放。**不是真正的堆叠**。

### 5.6 要实现供应商风格，应该改前端还是后端？
**两端都要改**，但后端改动极小（只改一个常量），前端改动为主。

### 5.7 风险分析

| 改动 | 风险 | 缓解 |
|------|------|------|
| ble_server LSB: 0.2861→0.476837 | 实时显示 uV 幅值变化 1.667× | 不影响 H5 raw ADC 保存；前端 offset 可能需要调大 |
| 前端堆叠模式 | 性能风险 | Canvas 绘制 16 条线 vs 16 条线，性能相当 |
| signal-quality.js LSB | RMS 颜色阈值变化 | 同步修正 LSB 常数即可 |
| 窗口 3s→5s | Canvas 点位增加 | 750→1250 点，Canvas 性能足够 |

## 6. 推荐最小改动方案

### Phase 1: 后端 LSB 修正（低风险，1 文件）

**文件**: `ble_server.py`

1. **line 110**: `BASE_LSB_24BIT = 0.2861` → `0.476837`
2. 无需改滤波逻辑
3. 无需改 parse_packet / 通道映射
4. 无需改 realtimeEngine.js
5. 无需改 storage_server.py（H5 存 raw ADC，不受 LSB 影响）

### Phase 2: 前端信号质量 LSB 同步（低风险，1 文件）

**文件**: `public/scripts/signal-quality.js`

1. **line 30**: `var BASE_LSB_24BIT = 0.2861;` → `0.476837;`

### Phase 3: 前端堆叠视图增强（中等风险，2 文件）

**文件**: `public/scripts/waveform-renderer.js`

1. 新增 `STACKED_MODE = true` 或从外部配置接收
2. 修改 `renderPoints()`:
   - 堆叠模式: 所有通道画在同一个坐标空间
   - `y = (15 - ch) * offset - value` (CH1 在上, CH16 在下)
   - 可选 clamp: `value = Math.max(-offset*0.48, Math.min(offset*0.48, value))`
3. 修改 `WINDOW_DURATION = 5` (3→5 秒)
4. 保留现有 band 模式作为 fallback
5. Y 轴标签: CH1-CH16

**文件**: `public/index.html`

1. Offset input 默认值: `value="150"` → `value="300"`
2. 新增 Clamp checkbox (可选, 默认不勾选)
3. 新增 "堆叠/分通道" 切换（可选 dropdown）

### 不改的文件

- `realtimeEngine.js` — 不改（数据透传，已正确）
- `storage_server.py` — 不改（写 raw ADC，不受 LSB 影响）
- `ble_control.js` — 不改（只控制流管理）
- `collection-controller.js` — 不改
- `page-switch.js` — 不改

## 7. 实施阶段计划

### 第一阶段（立即执行，低风险）
1. 修改 `ble_server.py`: `BASE_LSB_24BIT = 0.476837`
2. 修改 `public/scripts/signal-quality.js`: `BASE_LSB_24BIT = 0.476837`
3. 验证: 启动系统，查看实时波形幅值变化

### 第二阶段（可与第一阶段并行）
1. 修改 `public/scripts/waveform-renderer.js`:
   - `WINDOW_DURATION` 3→5
   - 新增堆叠模式
   - 新增 clamp 支持
2. 修改 `public/index.html`:
   - Offset 默认值 150→300
   - 新增 Clamp checkbox
   - 新增显示模式切换

### 第三阶段（可选，后续优化）
1. IMU 绘制对齐供应商风格
2. Y 轴 CH1-CH16 标签 overlay
3. 实时 Offset 调整热键

## 8. 附录: 关键代码引用

### ble_server.py 需改行

```python
# line 110 (当前)
BASE_LSB_24BIT = 0.2861        # 2.4V ref / 2^23 * 1e6 (μV)

# 改为
BASE_LSB_24BIT = 0.476837      # 4.0V ref / 2^23 * 1e6 (μV) — 对齐供应商/bin_sync
```

### signal-quality.js 需改行

```javascript
// line 30 (当前)
var BASE_LSB_24BIT = 0.2861;

// 改为
var BASE_LSB_24BIT = 0.476837;
```

### waveform-renderer.js 需改

1. `WINDOW_DURATION: 3` → `5`
2. `renderPoints()` 增加堆叠模式分支
```
// 堆叠模式伪代码:
if (this.stackedMode) {
    const offset = this.getOffset();
    for (let i = 0; i < pointsCount; i++) {
        for (let ch = 0; ch < channels; ch++) {
            const value = data[ch][i];
            const y = (15 - ch) * offset - value;  // CH1 top, CH16 bottom
            // clamp if enabled
            // lineTo
        }
    }
}
```

### index.html 需改

```html
<!-- Offset 默认值 -->
<input ... id="emg1-offset" value="300" ...>  <!-- 150→300 -->
```
