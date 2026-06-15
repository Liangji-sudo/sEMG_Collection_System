# 摄像头 WebSocket 直连架构 & 问题排查记录

日期: 2026-06-15
分支: fix_new

---

## 1. 架构改造概述

### 改造前（HLS预录制 + HTTP中转）

```
前端 camera-ui.js ──HTTP──► server.js ──► realtimeEngine.js ──WS :8768──► camera_server.py
                                                                              │
                                                                         [HLS持续录制]
                                                                         [静态帧提取预览]
```

问题:
- 前端不直连后端，经过 HTTP → Node.js → WebSocket 三层转发
- 预览是静态帧提取（从 HLS 分段手动刷新），不是实时推流
- 对标 `ble_server` 的直连架构不一致

### 改造后（WebSocket直连 + MJPEG实时推流）

```
前端 camera_control.js ──WS :8768──► camera_server.py
       (直连, 对标ble_control.js)        │
                                    [MJPEG实时采集→帧推送]
                                    [HLS录制→按需启动]
                                         │
realtimeEngine.js    ──WS :8768──►       │
       (录制控制)                   [start_continuous_recording]
                                    [mark_recording_start]
                                    [stop_and_save]
```

数据链路（预览）:
```
USB摄像头 → ffmpeg(dshow MJPEG pipe) → CameraCapture线程读取JPEG帧
    → base64编码 → asyncio.Queue → _broadcast_loop → WebSocket推送
    → 前端 camera_control.js → camera-ui.js → <img src="data:image/jpeg;base64,...">
```

---

## 2. 涉及文件

| 文件 | 角色 | 改动 |
|------|------|------|
| `camera_server.py` | 核心后端服务，WebSocket :8768 | 完全重写：新增 CameraCapture(MJPEG)、HLSRecorder 分段修复、多客户端支持、录制状态广播 |
| `public/scripts/camera_control.js` | 前端 WebSocket 客户端 | **新建**，对标 `ble_control.js`，直连 :8768 |
| `public/scripts/camera-ui.js` | 前端 UI 控制 | 重写：WS 直连代替 HTTP API，实时预览，录制状态更新 |
| `public/index.html` | 页面结构 | 预览窗口改为实时推流，移除静态帧刷新按钮 |
| `server.js` | Node.js 主服务 | 精简 camera HTTP API 为降级路由 |
| `realtimeEngine.js` | 采集引擎 | 录制流程改为先启动 HLS 再标记，添加 request_id 匹配，引入分片等待延迟 |

---

## 3. 用户交互流程

1. **打开摄像头**: 点击"打开摄像头" → 弹出配置弹窗 → 后端扫描 USB 设备 → 选择左右手 → 点击"打开摄像头"
2. **实时预览**: MJPEG 帧通过 WebSocket 持续推送（~30fps），前端 `<img>` 实时刷新
3. **开始采集**: 预览继续（MJPEG），不启动录制
4. **按空格键**: 停止 MJPEG → 启动 HLS 录制 → 等待 1.5s → 标记起始分段
5. **采集过程中**: 设备状态窗口显示"写盘中"，预览按钮禁用
6. **停止采集**: 合并 HLS 分段为 MP4 → 保存到 `storage/video/` → 推送录制结束状态 → 自动恢复 MJPEG 预览

---

## 4. 发现 & 修复的 Bug

### Bug 1: 点击"打开摄像头"无反应

**现象**: 初始界面点击"打开摄像头"按钮后没有任何反应

**原因**: `camera-ui.js` 的 `waitForCameraControl()` 等待 WebSocket **连接成功**后才绑定 DOM 事件。如果 `camera_server` 启动慢或 ffmpeg 未安装，WebSocket 一直连不上，事件永不绑定。

**修复** (commit: `cf060cc`):
- `waitForCameraControl` 只等 `window.CameraControl` 模块存在就绑定事件
- 新增完整的 HTTP 降级路径（`bindEventsFallback`）

### Bug 2: 没有扫描出摄像头设备

**现象**: 弹窗能打开，但设备列表为空

**原因**: 
1. 电脑未安装 ffmpeg
2. `find_ffmpeg()` 的 glob 模式只匹配 `Gyan.FFmpeg*`，新安装的 `Gyan.FFmpeg.Essentials*` 包名不匹配

**修复**:
- winget 安装 FFmpeg 8.1.1
- `find_ffmpeg()` 新增 `Gyan.FFmpeg.Essentials*` 和 `Gyan.FFmpeg.Shared*` 包名匹配
- `_cmd_list_cameras` 改用 `loop.run_in_executor` + `subprocess.run`（Windows asyncio subprocess 不稳定）
- 支持新旧两种 ffmpeg 输出格式的正则匹配

### Bug 3: 断开摄像头后，预览残留上一手画面

**现象**: 左手断开 → 摄像头接右手 → 左手预览窗口仍显示最后一帧

**原因**: `stopAllCameras()` 只关闭了摄像头进程，没有清空 DOM 中的 `<img>.src` 和 `CamState.previewFrames` 缓存

**修复** (commit: `cf060cc`):
- 新增 `clearPreviewFrames()` 方法
- `stopAllCameras()` 调用 `clearPreviewImages()` 清空所有预览图和缓存

### Bug 4: MP4 只有 1 秒

**现象**: 完整采集流程走完，最后保存的 MP4 只有 1 秒长度

**原因（多重）**:
1. **分片追踪 bug**: `HLSRecorder.current_segment = 0` 初始化 + `if latest > self.current_segment` 比较 → segment_00000 (index=0) 永远不触发更新
2. **启动后立即标记**: `start_continuous_recording` 后立刻 `mark_recording_start`，此时第一个 HLS 分片还未生成，`mark_segment` 可能仍为 -1
3. **停止前 current_segment 滞后**: 监控线程每 0.5s 更新一次，停止时可能未捕获最新分片

**修复** (commits: `c252876`, `833c8d9`):
- `current_segment` 初始值改为 `-1`
- HLS 启动后等待 1.5s 再标记（`realtimeEngine.js`）
- `stop_and_save` 重写：ffmpeg 退出后直接扫描 temp 目录下所有 `segment_*.ts` 文件，用实际文件列表确定合并范围，不再依赖监控线程变量

### Bug 5: 采集后无法恢复预览

**现象**: H5 结束后回到初始界面，点击预览无画面

**原因**: `_do_stop_and_save` 检查 `preview_subscribers` 是否有订阅者来决定是否恢复 MJPEG。但采集期间预览弹窗已关闭，订阅者为空 → MJPEG 不恢复

**修复** (commit: `c252876`):
- 新增 `camera_opened` 状态字典追踪摄像头是否曾被打开
- `_do_stop_and_save` 基于 `camera_opened` 判断恢复 MJPEG，不依赖订阅者

### Bug 6: 设备状态窗口录制时不更新

**现象**: 采集过程中，设备状态窗口一直显示"预览中"，没有切换为"写盘中"

**原因**: camera_server 未向所有客户端广播录制状态变化

**修复** (commit: `c252876`):
- camera_server 新增 `_broadcast_status_to_all()` 和 `_push_recording_status()`
- 前后端均处理 `recording_status` 消息类型
- 前端 `camera-ui.js` 监听后更新状态文字 + 禁用/启用预览按钮

---

## 5. 当前待观察问题

- [ ] MP4 时长是否正确（需要重启后实测，查看 `stop_and_save` 日志输出）
- [ ] 采集期间 MJPEG→HLS 切换是否流畅（摄像头资源释放/重占的延迟）
- [ ] 左右手同时录制时，串行 1.5s 等待是否影响体验（可优化为并行）
- [ ] HTTP 降级路径需要完整测试

---

## 6. camera_server WebSocket 命令参考

| 命令 | 调用方 | 说明 |
|------|--------|------|
| `list_cameras` | 前端 | 枚举 USB 摄像头 |
| `set_camera` | 前端 | 保存摄像头配置（不启动） |
| `open_camera` | 前端 | 启动 MJPEG 采集 + 订阅预览 |
| `close_camera` | 前端 | 停止 MJPEG，清空订阅 |
| `subscribe_preview` | 前端 | 订阅帧推送 |
| `unsubscribe_preview` | 前端 | 取消帧订阅 |
| `start_continuous_recording` | realtimeEngine | 停止 MJPEG，启动 HLS |
| `mark_recording_start` | realtimeEngine | 标记录制起始分段 |
| `stop_and_save` | realtimeEngine | 停止 HLS，合并 MP4，恢复 MJPEG |
| `get_status` | 通用 | 获取服务器状态 |

推送消息类型:
- `preview_frame`: {type, side, frame(base64)}
- `recording_status`: {type, recording(bool), recording_sides, preview_available}
- `status`: {type, cameras, captures, recording, preview_subscribers}
