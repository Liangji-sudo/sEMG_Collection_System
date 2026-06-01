# calibrate_tool.py 兼容性审计 — 2026-06-01

## 1. 当前功能

- 读取 H5 文件，可视化 EMG 16 通道 + IMU 3 轴
- 支持滑块拖动、窗口缩放
- 支持 prompt 标签导航（prev/next）
- EMG 数据加载优先级：`emg*_2khz_adc` > `emg*_2khz` > `emg*_250hz_adc` > `emg*_250hz` > `emg*`
- IMU 数据：`imu*a_100hz` > `imu*a_ble` > `imu*a`
- 时间轴：优先使用 `time` 字段，fallback 到采样率推算
- prompt 时间基于 `emg_start_time`（`data['time'][0]`）

## 2. 不兼容点

| 问题 | 严重程度 |
|------|---------|
| 无 sync_status 展示 | 中 |
| 无 collection_status / segment 展示 | 低 |
| 无旧 2kHz 风险提示 | 高 |
| 优先加载 2kHz 数据，即使 sync_status 非 synced | 中 |
| IMU 数据未包含 legacy `imu1_100hz` / `imu2_100hz` | 低 |

## 3. 建议改造

1. 加载后显示 sync_status / collection_status / segment_index
2. 优先 250Hz，2kHz 可选且有 sync_status 警告
3. 调用 diagnose_frame_ids 检查 frame_id 健康度
4. IMU 增加 legacy 名称
5. 时间轴 fallback 增强
