# IMU 曲线平坦问题审计

> 日期: 2026-06-01
> 分支: fix_sync

## 根因分析

### 检查项 1: H5 IMU 数据类型

bin_sync_tool `_sync_imu_100hz` 写入的 dtype:
```
("acc", "<f4", (3,)), ("gyr", "<f4", (3,)), ("mag", "<f4", (3,)),
("sd_frame_id", "<u4"), ("time", "<f8")
```

calibrate_tool `_extract_imu_acc` 读取 `data['acc']` → shape `(N, 3)` → **正确**。
calibrate_tool `_extract_imu_gyr` 读取 `data['gyr']` → shape `(N, 3)` → **正确**。

数据读取逻辑正确。

### 检查项 2: IMU 数据值域 (预期)

- Acc Z 轴: ~1g (重力) at rest
- Acc X/Y 轴: ~0g at rest
- Gyr 全部: ~0 deg/s at rest

实际 H5 数据的 std/max 需通过 `_diag_imu_flat.py` 确认。

### 检查项 3: 绘图 Y 轴范围 ★ ROOT CAUSE

`_draw_imu_device` 中:
```python
total_h = 3 * offset
ax.set_ylim(-offset * 0.2, total_h - offset * 0.2)
```

Acc (offset=4.0g): ylim = **(-0.8, 11.2)** → 12g 范围
Gyr (offset=600): ylim = **(-120, 1680)** → 1800 deg/s 范围

**问题**:
- Acc Z 重力 1g 在 12g 范围内仅占 **8%** → 几乎看不见偏移
- Acc X/Y 静止时 ≈0g → 完全平坦（这是正常的）
- Gyr 静止时 ≈0 deg/s → 完全平坦（这是正常的）
- Gyr 运动时 ±50 deg/s 在 1800 范围内仅占 **2.8%** → 几乎看不见

### 检查项 4: IMUBinParser Endian (次要)

IMUBinParser 硬编码 Big Endian (`>6h`) 和 SCALE_ACCEL=±16g。

若 L015 为 V1 硬件 (ICM-20948): 正确。
若 L015 为 V2 硬件 (LSM6DSV32X): 端序错误 + scale 错误，值域异常。

需通过 `_diag_imu_flat.py` 对比 H5 vs Bin 验证。

## 结论

**主因**: `_draw_imu_device` 的固定 ylim 范围过大，信号幅度相对过小，曲线近乎平线。

**修复**: 根据实际数据范围自动设置 ylim，确保信号可见。

## 验证

运行 `_diag_imu_flat.py` 检查:
1. H5 IMU 数据 min/max/std
2. Bin 直接解析 min/max/std (Big Endian)
3. Bin 供应商方式解析 min/max/std (Little Endian)
4. H5 vs Bin 同 frame_id 对比
5. ylim 范围 vs 数据范围比率
