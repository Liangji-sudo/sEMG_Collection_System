# sEMG 采集系统架构文档

## 系统架构概览

本系统采用**微服务架构**，将各个外设管理模块独立为 Python 进程，通过 WebSocket 和 ZMQ 与主服务通信。

```
┌─────────────────────────────────────────────────────────────────┐
│                         Node.js 主服务                           │
│                        (server.js + realtimeEngine.js)           │
│                         HTTP :3000 | WebSocket :8080            │
└────────┬────────┬────────┬────────┬────────────────────────────┘
         │        │        │        │
         │        │        │        └──────────────────┐
         │        │        │                           │
         ▼        ▼        ▼                           ▼
┌────────────┐ ┌──────────────┐ ┌────────────┐ ┌──────────────┐
│ BLE Server │ │ Mocap Server │ │   Camera   │ │   Storage    │
│            │ │              │ │   Server   │ │   Server     │
│  (Python)  │ │   (Python)   │ │  (Python)  │ │   (Python)   │
│            │ │              │ │            │ │              │
│ :8764 Data │ │   :8767      │ │   :8768    │ │ ZMQ :5555    │
│ :8766 Ctrl │ │              │ │            │ │     :5556    │
└─────┬──────┘ └──────┬───────┘ └─────┬──────┘ └──────┬───────┘
      │                │               │                │
      ▼                ▼               ▼                ▼
  蓝牙EMG设备      动捕系统SDK      USB摄像头        HDF5文件
```

---

## 各模块职责

### 1. **BLE Server** (ble_server.py)
- **端口**: :8764 (数据流), :8766 (控制)
- **功能**:
  - 扫描和连接蓝牙 EMG 设备
  - 实时接收 EMG 数据流
  - 设备状态管理（连接、断开、重连）
- **启动**: 由 `deviceSync.js` 自动启动

### 2. **Mocap Server** (mocap_server.py)
- **端口**: :8767
- **功能**:
  - 连接 Noitom 动捕系统 SDK
  - 接收实时骨骼数据
  - 通道选择和数据过滤
- **启动**: 由 `deviceSync.js` 自动启动

### 3. **Camera Server** ⭐ (camera_server.py)
- **端口**: :8768
- **功能**:
  - 枚举 USB 摄像头设备
  - 使用 ffmpeg 后端录制视频
  - 视频文件管理（存储到 `storage/video/`)
  - 支持 Windows DirectShow
- **启动**: 由 `server.js` 自动启动
- **命令**:
  - `list_cameras` - 枚举可用摄像头
  - `set_camera` - 设置摄像头配置 (side, device_name)
  - `start_recording` - 开始录制 (side, output_filename)
  - `stop_recording` - 停止录制 (side)
  - `get_status` - 获取状态

### 4. **Storage Server** (storage_server.py)
- **端口**: ZMQ :5555 (命令), :5556 (数据)
- **功能**:
  - HDF5 文件创建和管理
  - 实时写入 EMG 数据
  - 元数据保存（prompt, 视频信息等）
- **启动**: 由 `dataStorage.js` 自动启动

### 5. **Realtime Engine** (realtimeEngine.js)
- **端口**: WebSocket :8080
- **功能**:
  - 协调各子系统
  - 数据流汇总和分发
  - 采集流程控制
  - 连接管理和重连

### 6. **HTTP Server** (server.js)
- **端口**: HTTP :3000
- **功能**:
  - 静态文件服务
  - RESTful API
  - 启动和管理所有子系统

---

## 视频录制流程

### 1. 前端配置摄像头
```javascript
// 前端通过 WebSocket 发送命令到 realtimeEngine
ws.send(JSON.stringify({
    action: 'camera_set_config',
    side: 'left',
    device_name: 'USB Camera (4c4a:4a55)',
    device_id: 'USB Camera (4c4a:4a55)'
}));
```

### 2. realtimeEngine 转发到 camera_server
```javascript
// realtimeEngine.js
async onCameraSetConfig(data) {
    const result = await this.sendCameraCommand('set_camera', {
        side: data.side,
        device_name: data.device_name,
        device_id: data.device_id
    });
}
```

### 3. 采集开始，按下空格键触发录制
```javascript
// realtimeEngine.js
onPrompt(name, stageName, timestamp) {
    if (name === 'space' && !this.videoRecordingStarted) {
        this._startVideoRecording(timestamp, stageName);
        this.videoRecordingStarted = true;
    }
}
```

### 4. camera_server 启动 ffmpeg
```python
# camera_server.py
ffmpeg_cmd = [
    'ffmpeg',
    '-f', 'dshow',
    '-video_size', '1280x720',
    '-framerate', '30',
    '-i', f'video={device_name}',
    '-c:v', 'libx264',
    '-preset', 'ultrafast',
    '-crf', '23',
    '-pix_fmt', 'yuv420p',
    '-y',
    str(output_path)  # storage/video/R003_L_260614_162119.mp4
]
process = subprocess.Popen(ffmpeg_cmd, ...)
```

### 5. 采集结束，自动停止录制
```javascript
// realtimeEngine.js
async onCollectionStop(completed) {
    if (this.videoRecordingStarted) {
        await this.sendCameraCommand('stop_recording', { side: 'left' });
        await this.sendCameraCommand('stop_recording', { side: 'right' });
    }
}
```

---

## 文件命名规范

### EMG 数据文件
- **格式**: `R{编号}_{L/R}_{日期}_{时间}.bin`
- **示例**: `R003_L_260614_162119.bin`
- **位置**: `storage/{task_id}/`

### 视频文件
- **格式**: `R{编号}_{L/R}_{日期}_{时间}.mp4`
- **示例**: `R003_L_260614_162119.mp4`
- **位置**: `storage/video/`

### HDF5 文件
- **格式**: `{subject_id}_session{n}_{stage_name}_{日期}_{时间}.h5`
- **示例**: `S001_session1_离散手势_20260614_162119.h5`
- **位置**: `storage/{task_id}/`

**关联关系**: 视频文件名与 bin 文件名一致（除了扩展名），便于数据关联。

---

## 部署要求

### 软件依赖
1. **Node.js**: v18.x+
2. **Python**: 3.8+
3. **ffmpeg**: 必须在系统 PATH 中
   ```bash
   # Windows 安装
   winget install Gyan.FFmpeg
   ```
4. **Python 包**:
   ```bash
   pip install websockets
   ```

### 启动顺序
```bash
# 单个命令启动所有服务
npm start

# 启动顺序（自动）:
# 1. realtimeEngine (WebSocket :8080)
# 2. deviceSync → ble_server.py (:8764, :8766)
# 3. deviceSync → mocap_server.py (:8767)
# 4. camera_server.py (:8768)
# 5. dataStorage → storage_server.py (ZMQ :5555, :5556)
# 6. HTTP Server (:3000)
```

---

## 架构优势

### ✅ 统一的外设管理
- 所有外设都是独立 Python 进程
- WebSocket 统一通信协议
- 便于维护和扩展

### ✅ 进程隔离
- 单个外设崩溃不影响主系统
- 资源占用独立
- 便于调试和监控

### ✅ 语言优势
- Python: 处理硬件设备（蓝牙、SDK、摄像头）
- Node.js: Web 服务和实时通信
- 各取所长

### ✅ 可扩展性
- 新增外设只需添加新的 Python server
- 不影响现有代码
- 遵循统一的接口规范

---

## 下一步优化

1. **前端摄像头配置 UI**
   - 枚举可用摄像头
   - 左右手摄像头配置界面

2. **视频预览**
   - 实时预览摄像头画面
   - 录制状态指示

3. **视频质量配置**
   - 分辨率、帧率、码率可配置
   - 支持多种编码格式

4. **错误恢复**
   - ffmpeg 异常重启
   - 录制失败提示

---

*文档更新时间: 2026-06-14*
