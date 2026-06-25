# IMU 显示对比审计：供应商 V3 vs calibrate_tool vs 前端实时

> 日期: 2026-06-01
> 分支: fix_sync
> 状态: 第一阶段只读审计，未改代码

---

## 1. 供应商 V3 IMU 显示流程

### 1.1 IMU 数据解析

**实时 BLE 包解析** (wband_emg_client_V5.py parse_imu):
```python
SCALE_ACCEL = 32.0 / 32768.0      # V2: LSM6DSV32X ±32g
SCALE_GYRO = 2000.0 / 32768.0     # V1/V2 相同
BYTES_PER_IMU = 18                # Acc6 + Gyro6 + Reserved6

def parse_single_imu(b_data):
    acc_gyr = struct.unpack('<6h', b_data[0:12])    # Little Endian (V2)
    ax, ay, az = [x * SCALE_ACCEL for x in acc_gyr[0:3]]
    gx, gy, gz = [x * SCALE_GYRO for x in acc_gyr[3:6]]
    return [ax, ay, az, gx, gy, gz]                 # [acc_x,y,z, gyr_x,y,z]
```

**离线 BIN 回放** (start_replay_mode):
- 同目录下查找对应 IMU bin 文件（将 `emg` 替换为 `imu`）
- 解析 IMU bin header: 100Hz sample rate
- 每帧: 4B frame_id + num_imus × 18B IMU 数据
- 同样应用 SCALE_ACCEL / SCALE_GYRO 转为物理单位

### 1.2 IMU 显示布局

**加速度图** (acc_plot):
| 参数 | 值 |
|------|-----|
| 标题 | "加速度 (g)" |
| 通道数 | 3 (XYZ) |
| 每通道曲线数 | **MAX_NUM_IMUS=3** (IMU1+IMU2+IMU3 叠加) |
| Offset | **4.0 g** |
| 颜色 | RGB: 红(X), 绿(Y), 蓝(Z) |
| 线型 | IMU1 实线, IMU2 虚线, IMU3 点线 |
| 窗口 | 5 秒 |
| 绘图库 | QCustomPlot (OffsetSeriesPlot) |
| 布局 | **同轴堆叠** (3 通道 × Offset 分离) |
| Clamp | 无 |
| 滤波 | **无** |

**角速度图** (gyr_plot):
| 参数 | 值 |
|------|-----|
| 标题 | "角速度 (deg/s)" |
| 通道数 | 3 (XYZ) |
| 每通道曲线数 | **MAX_NUM_IMUS=3** |
| Offset | **600.0 deg/s** |
| 颜色 | RGB: 红(X), 绿(Y), 蓝(Z) |
| 线型 | IMU1 实线, IMU2 虚线, IMU3 点线 |
| 窗口 | 5 秒 |

**磁力计**: **供应商 V3 不显示磁力计图**。

### 1.3 关键设计特征
- Acc 和 Gyr **分两个独立的 OffsetSeriesPlot**
- 每个 plot 内，**所有 IMU 芯片叠加在同一坐标轴**
- IMU 数量通过实时检测 (`detected_num_imus`) 或离线解析确定
- **不滤波、不平滑** IMU 数据
- 窗口固定 5 秒，与 EMG 一致

---

## 2. calibrate_tool.py IMU 显示流程

### 2.1 常量定义 vs 实际使用

```python
# tools/calibrate_tool.py line 57-60
SCALE_ACCEL = 16.0 / 32768.0       # 定义但未在 IMU 显示中使用！
SCALE_GYRO = 2000.0 / 32768.0      # 定义但未在 IMU 显示中使用！
SCALE_MAG = 0.15                   # 定义但未在 IMU 显示中使用！
```

calibrate_tool 从 H5 读取的 IMU 数据**已经是物理单位**（由 ble_server 或 bin_sync_tool 转换），所以 SCALE_* 常量不参与显示计算。

### 2.2 数据加载

```python
# _extract_imu_acc() — 只提取 acc 字段
# H5 dataset 可能是结构化 dtype (acc, gyr 字段) 或普通数组
# 返回 acc 数据 (N, 3)，丢弃 gyr/mag
```

### 2.3 显示布局

| 参数 | 值 |
|------|-----|
| 列数 | 6 (imu1a, imu1b, imu1c, imu2a, imu2b, imu2c) |
| 每列行数 | 3 (X, Y, Z) |
| 总子图 | **18** (matplotlib subplots) |
| 每列标题 | IMU1A, IMU1B, IMU1C, IMU2A, IMU2B, IMU2C |
| 每行标签 | X, Y, Z |
| 曲线数/子图 | 1 (每个子图只画一条线) |
| 窗口 | 跟随 EMG 滑块 |
| 降采样 | 拖动 800 点 / 正常 2500 点 |

### 2.4 缺失的功能
1. **只显示 acc**，gyr 和 mag 不显示
2. **没有角速度图**
3. **每个 IMU 芯片独立子图**，而不是同轴叠加
4. **没有多 IMU 线型区分**（不需要，因为已分列）
5. **没有 offset 堆叠**
6. 没有对 IMU 做任何滤波/平滑

---

## 3. 前端实时 IMU 显示流程

### 3.1 数据链路

```
ble_server.py parse_imu_v2()
  │ SCALE_ACCEL=32/32768, SCALE_GYRO=2000/32768
  │ 返回: [[acc, gyr], [acc, gyr], ...]  per IMU chip
  │
  ▼
realtimeEngine.js normalizeImuData()
  │ imus[i] = { acc: [ax,ay,az], gyr: [gx,gy,gz], mag: null }
  │ frontImu1 = imus[0]  ★ 只取第一个 IMU!
  │
  ▼
waveform.js renderRealtimeData()
  │ imu1Acc.renderPoints([[ax],[ay],[az]])  ← 3 通道 banded
  │ imu1Gyr.renderPoints([[gx],[gy],[gz]])
  │ imu1Mag.renderPoints([[mx],[my],[mz]])
  │
  ▼
waveform-renderer.js WaveformRenderer (banded mode)
  │ 3 channels, channelSpacing = height/3
  │ y = centerY - value * scale
```

### 3.2 显示布局

| 参数 | Acc | Gyr | Mag |
|------|-----|-----|-----|
| 标题 | IMU1 加速度 | IMU1 角速度 | IMU1 磁力计 |
| 通道数 | 3 (XYZ) | 3 (XYZ) | 3 (XYZ) |
| 每通道曲线数 | **1** (只有 IMU1) | **1** | **1** |
| Offset 默认 | **4.0** (g) ✓ | **600** (deg/s) ✓ | 100 |
| 模式 | banded | banded | banded |
| 窗口 | 5 秒 (250/9 Hz) | 5 秒 | 5 秒 |
| 滤波 | 无 | 无 | 无 |
| Canvas 数 | 6 (2 设备 × 3 类型) | | |

### 3.3 与供应商差异

1. **只显示 1 个 IMU** (IMU1)，不显示 IMU2/IMU3
2. **Banded 模式**而非堆叠模式
3. **显示 Mag**（供应商不显示）
4. Offset 值匹配供应商: acc=4.0, gyr=600 ✓
5. 无多 IMU 线型

### 3.4 realtimeEngine.js 限制

```javascript
// 只发送第一个 IMU 给前端!
const frontImu1 = imu1Norm?.imus?.[0] || null;
const frontImu2 = imu2Norm?.imus?.[0] || null;
```

这是故意设计还是简化？需要确认。

---

## 4. 三者差异汇总表

| 维度 | 供应商 V3 | calibrate_tool | 前端实时 | 备注 |
|------|----------|---------------|---------|------|
| **显示 Acc** | ✅ 3ch × 3IMU 叠加 | ✅ 6col × 3row 分列 | ✅ 3ch banded, 仅 IMU1 | |
| **显示 Gyr** | ✅ 3ch × 3IMU 叠加 | ❌ 不显示 | ✅ 3ch banded, 仅 IMU1 | calibrate 缺 gyr! |
| **显示 Mag** | ❌ 不显示 | ❌ 不显示 | ✅ 3ch banded, 仅 IMU1 | 前端多此一项 |
| **IMU 叠加方式** | 同轴堆叠 Offset | 独立子图分列 | 独立 band | |
| **多 IMU 线型** | 实线/虚线/点线 | 无需 (已分列) | 无 (仅 1 IMU) | |
| **Acc Offset** | 4.0 g | — (自动纵轴) | 4.0 ✓ | |
| **Gyr Offset** | 600 deg/s | — | 600 ✓ | |
| **窗口** | 5 秒 | 跟随 EMG 滑块 | 5 秒 | |
| **IMU 滤波** | 无 | 无 | 无 | |
| **图表库** | QCustomPlot | matplotlib | Canvas 2D | |
| **数据单位** | 物理单位 (g, deg/s) | 物理单位 (读取 H5) | 物理单位 (BLE→realTime) | |

---

## 5. 问题回答

### 5.1 供应商 IMU 离线显示做了什么处理？
- **原始单位**: raw ADC (16-bit signed) → × SCALE → g / deg/s / μT
- **滤波**: **不做滤波**
- **平滑**: **不做平滑**（实时 lfilter 也不做 IMU 滤波）
- **Acc 图**: XYZ 3 通道，每个通道画最多 3 条 IMU 曲线，offset=4.0g
- **Gyr 图**: XYZ 3 通道，每个通道画最多 3 条 IMU 曲线，offset=600deg/s
- **Mag 图**: **不显示**

### 5.2 calibrate_tool.py 当前和供应商差在哪里？
1. **只显示 acc，不显示 gyr/mag**（最大差异）
2. 6 列分列布局 vs 供应商 2 个堆叠图
3. 无 offset 堆叠效果
4. 无多 IMU 线型区分
5. SCALE_ACCEL 常量定义为 ±16g（V1）但实际未使用，H5 数据已转换

### 5.3 前端实时 IMU 当前和供应商差在哪里？
1. **只显示 1 个 IMU**（IMU1），不显示 IMU2/IMU3
2. **Banded** 通道模式 vs 供应商堆叠
3. **多显示了 Mag**（供应商不显示）
4. **Offset 默认值匹配**: acc=4.0 ✓, gyr=600 ✓
5. **现有 6 个 Canvas** (acc+gyr+mag × 2 设备) vs 供应商 4 个 Block (acc+gyr × 2 设备)

### 5.4 是否需要同时改 calibrate_tool.py 和前端？
**建议分步：**
- **calibrate_tool**: 增加 gyr 子图；考虑增加堆叠模式
- **前端**: 改为堆叠模式；支持多 IMU (2-3)；可去掉 mag 显示或设为可选

两个改动可独立进行，不互相阻塞。

### 5.5 是否需要改 ble_server.py？
**不需要。** ble_server.py 的 IMU 解析已经正确（SCALE_ACCEL=32/32768 for V2, Little Endian）。IMU 数据以物理单位输出。不需要修改。

### 5.6 是否影响 H5 保存？
**不影响。** H5 保存的 IMU 数据已经是物理单位（g, deg/s），显示端的滤波/平滑/布局修改只影响 UI 渲染。

---

## 6. 附加发现

### 6.1 SCALE_ACCEL 不一致 (跨代码库)

| 位置 | SCALE_ACCEL | 硬件 | Endian |
|------|-------------|------|--------|
| 供应商 V3 | 32/32768 (±32g) | V2 LSM6DSV32X | Little |
| ble_server.py | 32/32768 (±32g) | V2 | Little |
| ble_server.py (V1) | 16/32768 (±16g) | V1 ICM-20948 | Big |
| bin_sync_tool.py | 16/32768 (±16g) | **硬编码 V1** | **Big** |
| calibrate_tool.py | 16/32768 (定义但未使用) | — | — |

**风险**: bin_sync_tool 的 IMUBinParser 硬编码了 V1 参数。如果同步 V2 硬件的 IMU bin，会因 endian 和 scale 错误导致 H5 100Hz IMU 数据不正确。

**影响范围**: 当前 L015 数据使用 V1 硬件，解析正确。V2 硬件设备同步时需修复 bin_sync_tool 的 IMU 解析。

### 6.2 IMU Mag 数据仅在 V1 存在

V2 硬件 (LSM6DSV32X) 无磁力计。前端显示 mag 图时：
- V2 设备: mag 数据为 null，前端已处理隐藏逻辑 ✓
- V1 设备: 有 mag 数据

---

## 7. 推荐最小改动方案

### Phase 1: calibrate_tool.py IMU 增强 (中等风险)

1. **增加角速度图**: 
   - 在 EMG 图下方新增 gyr subplot section
   - 或在 IMU 图中增加 6 列 gyr (与现有 6 列 acc 并列)
   - 或者改为 Acc 和 Gyr 两个独立的 Figure (类似供应商)
   
2. **增加供应商风格堆叠模式** (可选):
   - 3 XYZ 通道堆叠 + Offset
   - 每个通道内多 IMU 曲线叠加 (实线/虚线/点线)

3. **最小方案** (推荐先做):
   - 现有 6 列 acc 保留
   - 新增 6 列 gyr (同样是 3 行 XYZ)
   - 标题改为 IMU1A_Acc / IMU1A_Gyr 等
   - 或是将 Acc 和 Gyr 分两个 Figure 但共用一个滑块

### Phase 2: 前端 IMU 堆叠模式 (低风险)

1. **waveform-renderer.js**: IMU renderer 也支持 stackedMode
   - Acc: 3 通道堆叠，offset=4.0g
   - Gyr: 3 通道堆叠，offset=600deg/s
   - 可选 per-channel clamp
2. **waveform.js**: 传入 stackedMode=true + clampCheckboxId
3. **index.html**: 可保留现有布局

### Phase 3: 前端多 IMU 支持 (中等风险)

1. **realtimeEngine.js**: `frontImu1` 发送全部 IMU 数据而非仅第一个
   ```javascript
   const frontImu1 = imu1Norm?.imus || [];  // 全部 IMU
   ```
2. **waveform-renderer.js**: 支持 seriesPerChannel > 1
3. **waveform.js**: 传入 seriesPerChannel + styles

### Phase 4: bin_sync_tool IMU 修复 (V2 硬件必需)

1. IMUBinParser 根据硬件版本选择 endian 和 scale
2. 从 H5 attrs 读取 hw_version 或 num_imus 判断

---

## 8. 验证方案

1. **calibrate_tool**: 打开同步后 H5 → IMU 区域显示 acc + gyr
2. **前端**: 启动模拟器 → Acc 图 offset=4.0 堆叠, Gyr 图 offset=600 堆叠
3. **对比图**: 取同一 H5 的 IMU 数据，分别用供应商风格和现有风格绘制对比
4. **多 IMU**: 如果有 3-IMU 设备的 H5，确认所有 IMU 都显示

---

## 9. 不改的文件

- `ble_server.py` — IMU 解析正确，不需要改
- `storage_server.py` — H5 IMU 数据已正确保存
- `bin_sync_tool.py` — Phase 4 才动，本次不改
- `realtimeEngine.js` — Phase 3 才动，本次不改 (透传逻辑正确)
