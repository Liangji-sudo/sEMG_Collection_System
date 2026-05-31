# 波形窗口时间修复 — 2026-05-31

**日期**：2026-05-31  
**分支**：feat_band_V3  
**相关文件**：`public/scripts/waveform-renderer.js`

---

## 根因

上次将 `WINDOW_DURATION` 从 5 改为 3 时，**只改了窗口秒数，未修正 `totalPoints` 的计算公式**。

旧公式使用「渲染批处理估算速率」：

```javascript
// 旧公式 (错误)
EMG: RENDER_RATE(100) × EMG_POINTS_PER_RENDER(18) × WINDOW_DURATION(3) = 5400 点
IMU: RENDER_RATE(100) × IMU_POINTS_PER_RENDER(1)  × WINDOW_DURATION(3) = 300  点
```

但 `renderPoints(data)` 中 `writeIndex` 每次增加的是 `data[0].length` 个**真实样本点**，不是渲染批次数。EMG 数据来自 BLE 250Hz 硬件采样，每包 9 个样本。因此 `totalPoints` 应直接按采样率 × 窗口秒数计算，与渲染批处理速率无关。

旧公式导致：
- EMG `totalPoints = 5400`，红线一圈实际需要 5400 / 250 = **21.6 秒**（远超 3 秒预期）
- IMU `totalPoints = 300`，红线一圈实际需要 300 / 27.78 ≈ **10.8 秒**

## 修复

新增真实显示采样率常量，`totalPoints` 改为「采样率 × 窗口秒数」：

```javascript
EMG_DISPLAY_SAMPLE_RATE: 250,          // BLE 硬件采样率
IMU_DISPLAY_SAMPLE_RATE: 250 / 9,      // 每包 9 EMG + 1 IMU

// 新公式
EMG: Math.round(250 × 3)    = 750 点  → 红线一圈 3.0 秒
IMU: Math.round(27.78 × 3)  = 83  点  → 红线一圈 2.99 秒
```

## 修改内容

| 位置 | 修改 |
|------|------|
| `RENDERER_CONFIG` | 新增 `EMG_DISPLAY_SAMPLE_RATE: 250`, `IMU_DISPLAY_SAMPLE_RATE: 250/9` |
| `RENDERER_CONFIG` | `RENDER_RATE` / `EMG_POINTS_PER_RENDER` / `IMU_POINTS_PER_RENDER` 注释标注「旧公式，不再用于时间窗」 |
| `resize()` | `totalPoints` 改用 `Math.round(SAMPLE_RATE × WINDOW_DURATION)` |

## 影响范围

| 项目 | 是否受影响 |
|------|------------|
| 前端 EMG 波形横轴 | ✅ 从 ~21.6s 修正为 3s |
| 前端 IMU 波形横轴 | ✅ 从 ~10.8s 修正为 ~3s |
| BLE 数据采集 | ❌ 不影响 |
| H5 存储 | ❌ 不影响 |
| bin 同步 | ❌ 不影响 |
| realtimeEngine / ble_server | ❌ 不影响 |

## 验证

- `node --check waveform-renderer.js` ✅ 通过
- EMG `totalPoints = Math.round(250 × 3) = 750` ✅
- IMU `totalPoints = Math.round(250/9 × 3) = 83` ✅
- 浏览器端实际红线走一圈时间：**待硬件验证**
