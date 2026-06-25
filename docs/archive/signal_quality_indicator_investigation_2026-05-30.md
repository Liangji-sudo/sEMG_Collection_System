# 信号质量颜色指示 — 调查与实现方案

**日期**：2026-05-30  
**分支**：feat_band_V3  
**状态**：✅ 已实现（v1），已通过 JS 语法检查和 mock 数据验证

---

## 一、总体结论

供应商 V3 上位机的信号质量算法分为两层：

| 层 | 类 | 用途 | 是否适合前端移植 |
|----|-----|------|-----------------|
| 实时监测 | `RealTimeQualityMonitor` | 250ms 滑动窗口，逐通道判定 dead/clipped/RMS | ✅ **适合**，纯数值计算，无依赖 |
| 两阶段评估 | `SignalQualityEvaluator` | 先采集静息基线（3s），再采集活动段（3s），计算 SNR | ⚠️ 需用户交互引导，采集中不适合 |

**推荐方案**：只移植 `RealTimeQualityMonitor` + `ChannelStatusRow` 颜色映射到前端 JS。

---

## 二、已阅读文件列表

### V3 供应商源码
| 文件 | 说明 |
|------|------|
| `wband_emg_V3/signal_quality.py` | 信号质量算法核心（195行） |
| `wband_emg_V3/custom_widgets.py` | ChannelStatusRow 颜色映射组件 + OffsetSeriesPlot 波形控件 |
| `wband_emg_V3/signalfilter.py` | 带通+陷波滤波（我们不移植） |
| `wband_emg_V3/wband_emg_client_V5.py` | 主界面，质量功能集成点 |

### 当前前端
| 文件 | 说明 |
|------|------|
| `public/scripts/waveform-renderer.js` | Canvas 波形渲染，WINDOW_DURATION=5s |
| `public/scripts/waveform.js` | WaveformController，数据接收与分发 |
| `public/scripts/ble_control.js` | BleState，设备连接状态管理 |
| `public/scripts/collection-controller.js` | CollectionController，采集状态管理 |
| `public/scripts/collection-selector.js` | 采集选择器，进入采集页入口 |
| `public/scripts/page-switch.js` | 页面切换控制 |
| `public/index.html` | 布局：左侧波形面板 + 右侧任务面板 |

### 数据链路
| 文件 | 说明 |
|------|------|
| `ble_server.py` | BLE 解析，raw ADC→μV 转换，滤波，发送到 realtimeEngine |
| `realtimeEngine.js` | 数据中转，前端用 μV(滤波后)，存储用 raw |
| `docs/public_doc.md` | 架构文档 |

---

## 三、V3 算法定位

### 3.1 `RealTimeQualityMonitor` — 实时滑动窗口监测

**文件**：[wband_emg_V3/signal_quality.py:150-194](wband_emg_V3/signal_quality.py#L150-L194)

```
构造参数：
  num_channels = 16
  fs = 250 (Hz)
  window_s = 0.25 (秒)
  buffer_size = 63 samples

feed(emg_chunk, clip_limit_uv):
  1. 积累数据到滑动缓冲区
  2. 缓冲区满 (>=63 samples) 时：
     - rms = sqrt(mean(square(proc_data))) per channel   # 均方根
     - dead_flags = variance < 0.1                        # 全平/脱落检测
     - clipped_flags = max_abs > clip_limit_uv * 0.99     # 削波检测
  3. 返回 { rms, dead, clipped } 三个数组
```

### 3.2 `ChannelStatusRow` — 颜色映射

**文件**：[wband_emg_V3/custom_widgets.py:208-291](wband_emg_V3/custom_widgets.py#L208-L291)

**颜色规则**（每通道独立）：

| 状态 | 条件 | 颜色 |
|------|------|------|
| 全平(脱落) | `variance < 0.1` | 深灰 `#555555` |
| 正常低RMS | `RMS ≤ 25 μV` | 绿色 `rgb(76,175,80)` |
| 正常中RMS | `25 < RMS ≤ 50 μV` | 绿→黄线性插值 |
| 正常高RMS | `RMS > 50 μV` | 黄→红线性插值 |
| 削波闪烁 | `max_abs > clip_limit * 0.99` | 红色边框 4Hz 闪烁 |

**RGB 插值公式**：

```
RMS_MAX = 50.0 μV
ratio = min(rms, RMS_MAX) / RMS_MAX

if ratio < 0.5:
    t = ratio * 2
    R = 76 + (255-76)*t, G = 175 + (235-175)*t, B = 80 + (59-80)*t
    // 绿色(76,175,80) → 黄色(255,235,59)
else:
    t = (ratio - 0.5) * 2
    R = 255 + (244-255)*t, G = 235 + (67-235)*t, B = 59 + (54-59)*t
    // 黄色(255,235,59) → 红色(244,67,54)
```

**削波限值计算**（对应 ble_server.py 参数）：

```python
# ble_server.py 的 lsb_uv 计算
BASE_LSB_24BIT = 0.2861  # μV/LSB
HARDWARE_FRONTEND_GAIN = 10
gain = 12  # 默认增益
lsb_uv = 0.2861 / (12 * 10) = 0.002384 μV/LSB

# 24-bit 满量程对应的 μV
clip_limit_uv = lsb_uv * 8388607 ≈ 20000 μV  # ≈ 20 mV
# 实际削波阈值: clip_limit_uv * 0.99 ≈ 19800 μV
```

### 3.3 `SignalQualityEvaluator` — 两阶段 SNR 评估（暂不移植）

**文件**：[wband_emg_V3/signal_quality.py:4-148](wband_emg_V3/signal_quality.py#L4-L148)

需用户配合：先保持放松（采集静息基线 3s），再用力收缩（采集活动段 3s），然后计算 SNR。采集中不适合做。

---

## 四、算法输入/输出

### 输入

| 项目 | 值 |
|------|-----|
| 数据类型 | **已滤波的 μV 值**（float32） |
| 滤波链 | 20-100Hz 带通 + 50Hz 工频陷波（在 ble_server.py 完成） |
| 通道数 | 16 |
| 采样率 | 250 Hz |
| 窗口长度 | 0.25 秒 = **63 个样本** |
| 通道顺序 | mapped 顺序（与前端波形显示一致） |

### 输出

| 项目 | 说明 |
|------|------|
| `rms` | float[16]，每通道 RMS（μV） |
| `dead` | bool[16]，方差 < 0.1 判定为全平/脱落 |
| `clipped` | bool[16]，超过削波限值 99% 判定为削波 |
| 每通道颜色 | 基于 RMS 的 RGB 颜色 |
| 整体质量 | 无单一分数，16 个独立通道状态 |

### 与前端数据通道一致性

| 环节 | 通道顺序 |
|------|----------|
| BLE 原始包 | physical（chip1[0..7], chip2[0..7]） |
| ble_server.py channel_map(V2) | 重排为 mapped |
| ble_server.py 滤波后 `uv` | **mapped** |
| realtimeEngine → 前端 `emgN` | **mapped**（透传，只做转置） |
| WaveformRenderer | **mapped**（逐通道绘制） |

✅ 质量监测的输入数据通道顺序与波形显示完全一致，不需要额外映射。

---

## 五、当前前端数据链路

```
BLE 硬件
  → ble_server.py (WS:8764 控制, 8766 数据)
    → parse_packet(): raw ADC → ×lsb_uv → μV → channel_map → filter
    → data_sender_thread(): {type:'data', dev1:{uv, raw, ...}, dev2:{uv, raw, ...}}
  → realtimeEngine.js (WS:8766 接收)
    → handleBleDataPacket(): 前端用 dev.uv, 存储用 dev.raw
    → flushRealtimeDataBuffer(): transpose [frame][ch] → [ch][frame]
    → broadcastToClients(): {type:'realtime_data_batch', batch:[{emg1, emg2, imu1, imu2}]}
  → 前端 waveform.js (WS:8080 接收)
    → RealtimeDataReceiver.handleMessage()
    → renderRealtimeData(): renderer.renderPoints(data.emg1)
  → WaveformRenderer.renderPoints(): Canvas 2D 逐通道波形绘制
```

### 关键数据格式（前端收到的）

```javascript
data.emg1 = [
  [ch0_val1, ch0_val2, ...],  // 通道0的所有点（μV, mapped顺序）
  [ch1_val1, ch1_val2, ...],  // 通道1的所有点
  ...
  [ch15_val1, ch15_val2, ...] // 通道15的所有点
]
// 每帧 18 个点（250Hz × 18 = 4500Hz 内插渲染）
```

---

## 六、UI 状态切换方案

### 6.1 "已连接腕带但还没开始采集"的判断

**需要同时满足三个条件**：

| 条件 | 判断来源 | 变量/方法 |
|------|----------|-----------|
| ① 当前在采集页面 | PageSwitchController | `document.getElementById('collectionScreen').style.display !== 'none'` |
| ② 腕带已连接 | BleState | `bleControl.state.devices[1].connected === true` 或 `bleControl.state.devices[2].connected === true` |
| ③ 还没开始采集 | CollectionController | `collectionController._isRunning === false` |

**进入采集页的流程**：
```
欢迎页 → collectionSelector.open() → 选择完成 → complete()
  → pageSwitchController.showCollection()       ← 进入采集页
    → startWaveform()                            ← 开始波形
    → BleControl.startAll()                       ← 确保设备启动
  → collectionController.selectTask()            ← 设置任务（此时 _isRunning=false）
```

**关键时间窗口**：
- 从进入采集页到用户点击"开始采集"之间，③ 为 false，① 为 true
- 如果设备已连接，② 也为 true
- **此窗口就是颜色指示器应显示的时间段**

### 6.2 显示模式控制

| 时机 | 动作 |
|------|------|
| 进入采集页 + 设备已连接 | **显示**通道质量颜色条 |
| 用户点击"开始采集"（`startTask()`） | **隐藏**颜色条，恢复/保持正常波形 |
| 采集结束（`stopTask()`） + 仍在采集页 | **恢复**颜色条 |
| 返回欢迎页（`backToWelcome()`） | **隐藏**（不在采集页） |
| 设备断开 | **隐藏**或显示"未连接" |

### 6.3 控制信号

| 信号来源 | 触发方式 |
|----------|----------|
| `collectionController._isRunning` 变化 | `startTask()` → true, `stopTask()` → false |
| `bleControl.state.devices[N].connected` 变化 | WebSocket 状态更新回调 |
| 页面切换 | `showCollection()` / `backToWelcome()` |

---

## 七、横轴时间窗口方案

### 当前窗口

**文件**：[public/scripts/waveform-renderer.js:25](public/scripts/waveform-renderer.js#L25)

```javascript
WINDOW_DURATION: 5  // 秒
```

对应计算：`totalPoints = 100 × 18 × 5 = 9000` 个点（100Hz 刷新率 × 18 点/帧 × 5 秒）

### 建议调整

| 方案 | 窗口 | 优缺点 |
|------|------|--------|
| A | 2 秒 | 最细，方便观察单个肌电脉冲形态 |
| B | 3 秒 | 折中，供应商质量评估默认也是 3s |
| C | 可配置（2/3/5s） | 最灵活，但增加 UI 复杂度 |

**推荐**：方案 B（3 秒），与 V3 质量窗口 `DEFAULT_QUALITY_WINDOW_S = 3` 一致。如需更细可调到 2 秒。

### 修改位置

只需改一处：`waveform-renderer.js` 的 `WINDOW_DURATION` 常量。Canvas 缓冲区和绘制逻辑会自动适配新值。

---

## 八、推荐实现步骤

### 第一阶段：质量计算模块（纯 JS，无 UI 依赖）

**新建文件**：`public/scripts/signal-quality.js`

1. 移植 `RealTimeQualityMonitor` 逻辑：
   ```
   class RealTimeQualityMonitor {
     constructor(numChannels=16, fs=250, windowS=0.25)
     feed(emgChunk, clipLimitUv) → { rms, dead, clipped } | null
     reset()
   }
   ```

2. 移植颜色映射函数：
   ```
   function rmsToColor(rmsUv) → cssColorString
   function getChannelStatusColor(rmsUv, isDead, isClipped) → { bg, border }
   ```

3. 从 `ble_server.py` 同步 `clip_limit_uv` 计算：
   ```javascript
   const BASE_LSB_24BIT = 0.2861;
   const gain = 12; // 默认
   const lsbUv = BASE_LSB_24BIT / (gain * 10);
   const CLIP_LIMIT_UV = lsbUv * 8388607; // ≈ 20000 μV
   ```

**关键点**：
- 输入数据直接复用实时波形数据（`data.emg1`/`data.emg2`），已是 μV 且已滤波
- 每次 `renderRealtimeData()` 时顺便 `feed()` 给质量监测器
- 不影响保存（保存用的是另一条路径 `storage_server` 的 raw 数据）

### 第二阶段：UI 组件

**修改文件**：`public/index.html`  
在 EMG 波形容器上方添加通道状态行：
```html
<!-- EMG1 通道状态行 -->
<div id="emg1-channel-status" class="channel-status-row">
  <!-- 16 个彩色方块，JS 动态生成 -->
</div>
```

**新建/修改 CSS**（在 index.html 的 `<style>` 中）：
```css
.channel-status-row {
  display: flex; gap: 2px; padding: 2px 4px;
}
.channel-status-dot {
  width: 20px; height: 20px; border-radius: 3px;
  text-align: center; color: white; font-weight: bold;
  font-size: 10px; line-height: 20px;
}
```

### 第三阶段：状态控制

**修改文件**：`public/scripts/waveform.js`

在 `WaveformController` 中：
1. 新增 `qualityMonitor1` / `qualityMonitor2` 实例
2. 在 `renderRealtimeData()` 中 feed 数据给质量监测器
3. 当监测器返回结果时，更新 UI

**修改文件**：`public/scripts/collection-controller.js`

在 `startTask()` 中：隐藏通道状态行  
在 `stopTask()` 中：恢复通道状态行（如果仍在采集页且设备已连接）

**修改文件**：`public/scripts/page-switch.js`

在 `showCollection()` 中：如果设备已连接，显示通道状态行  
在 `backToWelcome()` 中：隐藏通道状态行

### 第四阶段：横轴时间窗口

**修改文件**：`public/scripts/waveform-renderer.js`

```javascript
// 修改前
WINDOW_DURATION: 5,

// 修改后
WINDOW_DURATION: 3,  // 从5秒缩短到3秒，方便观察细节
```

### 第五阶段：削波闪烁定时器

**修改文件**：`public/scripts/waveform.js`  
添加 250ms 间隔定时器，切换削波通道的红色边框闪烁。

---

## 九、风险与待确认问题

### 9.1 依赖风险

| 风险 | 评估 | 缓解 |
|------|------|------|
| V3 算法依赖 PyQt | `RealTimeQualityMonitor` 只依赖 numpy（mean, square, var），纯数值计算 | JS 中用简单循环替代，计算量极小 |
| V3 算法依赖滤波库 | 滤波在 ble_server.py 已完成，质量监测只消费滤波后数据 | 前端直接复用 `emgN` 数据 |
| 输入数据精度差异 | V3 用 float32 numpy，JS 用 float64 number | 无影响，阈值比较允许 ±1 |

### 9.2 通道顺序风险

| 项目 | 结论 |
|------|------|
| 前端波形通道顺序 | mapped（与 ble_server V2 映射一致） |
| 质量监测输入 | 直接用波形数据，无需额外映射 |
| H5 存储通道顺序 | mapped（250Hz 数据集） |
| bin 文件通道顺序 | physical（已在 bin_sync_tool 中映射） |

✅ 无冲突。

### 9.3 性能风险

| 项目 | 评估 |
|------|------|
| 每次渲染计算量 | 250Hz 下每 0.25s 触发一次计算（63×16 个 float），远低于 Canvas 绘制开销 |
| 内存 | 每设备 63×16 float32 ≈ 4KB 缓冲区 |
| UI 更新频率 | 最多 4Hz（250ms 窗口满一次），远低于 60fps 渲染 |

✅ 性能无问题。

### 9.4 待确认问题

1. **增益值如何获取**：`clip_limit_uv` 依赖 gain 计算。当前 ble_server.py 支持动态增益（`config['gain']`），前端需要知道当前增益。方案：
   - 在 `realtime_data_batch` 中附带 `lsb_uv` 或 `gain` 字段
   - 或在 BLE 连接时通过状态消息传递

2. **双设备场景**：如果同时连接两个腕带，EMG1 和 EMG2 各自独立显示通道质量条。实现上两个 Monitor 实例独立运行。

3. **横轴窗口是否需要配置 UI**：如果希望用户可调节，需要在 EMG 波形区域加下拉框（2s/3s/5s），否则直接硬编码 3s。

4. **削波闪烁边框**：PyQt 用 QTimer。前端可用 `setInterval`(250ms)，但需在页面切换时清理定时器。

5. **采集后数据回放时是否也显示质量**：V3 回放模式也支持质量显示。可以先不支持，后续按需添加。

---

## 十、修改文件清单（预划）

| 文件 | 操作 | 说明 |
|------|------|------|
| `public/scripts/signal-quality.js` | **新建** | 质量监测类 + 颜色映射 |
| `public/index.html` | 修改 | 添加通道状态行 HTML + CSS，引入新脚本 |
| `public/scripts/waveform.js` | 修改 | 集成质量监测，feed 数据 + 更新 UI |
| `public/scripts/waveform-renderer.js` | 修改 | WINDOW_DURATION 5→3 |
| `public/scripts/collection-controller.js` | 修改 | startTask/stopTask 时切换显示 |
| `public/scripts/page-switch.js` | 修改 | 页面切换时控制显示/隐藏 |
| `realtimeEngine.js` | 可选修改 | 附带 gain/lsb_uv 到前端消息 |

---

## 十一、推荐最小实现方案

**如果只做最小可行版本（MVP）**：

1. 新建 `public/scripts/signal-quality.js` — 包含 `RealTimeQualityMonitor` 类 + `rmsToColor()` 函数
2. 修改 `public/index.html` — 在 EMG 波形上方加 `<div id="emg1-channel-status">` 行（16 个方块），写 CSS
3. 修改 `public/scripts/waveform.js` — 在 `renderRealtimeData()` 中 feed 数据，更新通道颜色
4. 修改 `public/scripts/collection-controller.js` — startTask 隐藏 / stopTask 恢复
5. 修改 `public/scripts/waveform-renderer.js` — WINDOW_DURATION: 5 → 3

**不修改**：ble_server.py、realtimeEngine.js、storage_server.py — 数据流不需变动。

---

## 十二、实现结果 v1

**实现日期**：2026-05-30

### 12.1 修改文件列表

| 文件 | 操作 | 说明 |
|------|------|------|
| `public/scripts/signal-quality.js` | **新建** | QualityMonitor 类 + rmsToColor 颜色映射 + clipped 闪烁定时器 |
| `public/index.html` | 修改 | 添加 .channel-status-row/.channel-status-dot CSS + EMG1/EMG2 通道状态行 HTML(16格) + signal-quality.js script 引入 |
| `public/scripts/waveform.js` | 修改 | WaveformController 新增 _qualityMonitor1/2 + feedQualityMonitor() + refreshQualityVisibility()；RealtimeDataReceiver.renderRealtimeData 中 feed 数据；stop/clearAll 中重置质量状态 |
| `public/scripts/waveform-renderer.js` | 修改 | WINDOW_DURATION: 5 → 3 |
| `public/scripts/collection-controller.js` | 修改 | startTask() 和 stopTask() 中调用 refreshQualityVisibility() |
| `public/scripts/page-switch.js` | 修改 | showCollection/showWelcome/showBackend 中调用 refreshQualityVisibility() |
| `public/scripts/ble_control.js` | 修改 | BleControl 暴露 state (BleState)；device_status 更新后调用 refreshQualityVisibility()；WS onclose 时调用 refreshQualityVisibility() |

**未修改**：ble_server.py、realtimeEngine.js、storage_server.py — 数据流未变动。

### 12.2 显示/隐藏状态机

```
refreshQualityVisibility() 统一入口:
  if collectionScreen 可见
     AND BleState.devices[devId].connected
     AND !collectionController.isRunning()
    → row.style.display = 'flex'  (显示)
  else
    → row.style.display = 'none'   (隐藏)
    → 重置为灰色 (updateChannelStatusRow null)
```

| 触发时机 | 调用位置 |
|----------|----------|
| 进入采集页 | page-switch.js `showCollection()` |
| 离开采集页(回首页) | page-switch.js `showWelcome()` |
| 离开采集页(去后台) | page-switch.js `showBackend()` |
| 设备连接/断开 | ble_control.js `handleStatusResponse()` |
| BLE 服务器断开 | ble_control.js `BleState.ws.onclose` |
| 开始采集 | collection-controller.js `startTask()` |
| 停止采集 | collection-controller.js `stopTask()` |

### 12.3 算法参数

| 参数 | 值 | 来源 |
|------|-----|------|
| 通道数 | 16 | V3 signal_quality.py |
| 采样率 | 250 Hz | BLE 下行频率 |
| 滑窗大小 | 0.25s (62 samples) | V3 RealTimeQualityMonitor.window_s |
| RMS 颜色上限 | 50 μV | V3 ChannelStatusRow.RMS_MAX |
| Dead 方差阈值 | 0.1 | V3 RealTimeQualityMonitor.feed() |
| Clipped 阈值比例 | clipLimitUv × 0.99 | V3 RealTimeQualityMonitor.feed() |
| clipLimitUv 默认 | ≈ 20000 μV | lsb_uv × 8388607, gain=12 |
| 削波闪烁频率 | 4 Hz (250ms) | V3 ChannelStatusRow.flash_timer |

### 12.4 验证结果

| 测试 | 结果 |
|------|------|
| JS 语法检查 (6 文件) | ✅ 全部通过 (`node --check`) |
| mock: 低 RMS (5μV + noise) | ✅ rms≈5, dead=false, clipped=false, 绿色 |
| mock: 高 RMS (80μV + noise) | ✅ rms≈80, 偏红 |
| mock: 常量 100μV (no noise) | ✅ dead=true (方差=0 < 0.1) |
| mock: 超 clipLimit (21000μV) | ✅ clipped=true |
| mock: 缓冲区未满 (1 sample) | ✅ 返回 null |
| mock: 中 RMS (37.5μV) 颜色 | ✅ rgb(250,151,57) 黄色 |

### 12.5 已知限制

1. **clipLimitUv 使用默认 gain=12 推导值**（≈20000 μV）。QualityMonitor 已预留 `setClipLimitUv()` 方法，后续可从 realtimeEngine 传入真实 gain/lsb_uv。
2. **未做浏览器真机验证**。开发环境无浏览器自动化，建议在真机上验证：
   - 连接腕带后进入采集页，应看到 16 通道颜色格（随信号 RMS 变化颜色）
   - 开始采集后颜色格隐藏，波形正常显示
   - 停止采集后颜色格恢复
   - 返回首页后颜色格隐藏
3. **削波闪烁定时器为全局单例**（页面级），始终运行。闪烁状态通过 `data-clipped` 属性驱动，非削波通道无性能影响。

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
