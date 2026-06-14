# 视频录制功能重构总结

## 📝 问题诊断

你报告的问题：
1. ❌ 预览窗口黑屏（摄像头被占用）
2. ❌ 采集后没有生成 mp4 文件
3. ❌ 日志中出现 webm 文件记录

根本原因：
- **架构混乱**：前端和后端都在录制视频，导致摄像头被多次占用
- **前端 MediaRecorder**：浏览器录制 webm 格式，占用摄像头
- **后端 camera_server**：ffmpeg 录制 mp4 格式，无法访问被占用的摄像头
- **设备名称错误**：ffmpeg 收到的设备名包含硬件ID `(4c4a:4a55)`，无法识别

---

## ✅ 已完成的修复

### 1. 清理设备名称（camera_server.py）

**问题**：浏览器返回的设备名称是 `"USB Camera (4c4a:4a55)"`，但 ffmpeg 只需要 `"USB Camera"`

**修复**：
```python
import re
clean_device_name = re.sub(r'\s*\([0-9a-fA-F:]+\)\s*$', '', device_name).strip()
```

现在 ffmpeg 命令使用清理后的设备名称：
```bash
ffmpeg -f dshow -i video=USB Camera ...
```

### 2. 移除前端 MediaRecorder 录制逻辑（camera-control.js）

**删除的内容**：
- `this.recorders` 对象（MediaRecorder 实例）
- `this.recordedChunks` 对象（录制数据缓存）
- `_saveRecording()` 方法（保存 webm 文件）
- `startRecording()` 中的 MediaRecorder 创建和启动逻辑
- `stopRecording()` 中的 MediaRecorder 停止逻辑

**保留的内容**：
- `this.streams` 对象（用于预览）
- `startStreaming()` 方法（启动摄像头预览流）
- `stopStreaming()` 方法（停止预览流）
- `attachStreamToVideo()` 方法（绑定预览视频）

**新逻辑**：
```javascript
// 只调用后端 API，不再使用 MediaRecorder
async startRecording(pathOrConfig, metadata = {}) {
    const recordings = [
        { side: 'left', output_filename: 'R001_L_xxx.mp4' },
        { side: 'right', output_filename: 'R001_R_xxx.mp4' }
    ];
    
    const response = await fetch('/api/camera/start-recording', {
        method: 'POST',
        body: JSON.stringify({ recordings, metadata })
    });
    
    return await response.json();
}
```

### 3. 更新后端 API 接口

**server.js**：
```javascript
// 旧：接收 outputPath
app.post('/api/camera/start-recording', async (req, res) => {
    const { outputPath, metadata } = req.body;
    const result = await deviceSync.startCameraRecording(outputPath, metadata);
});

// 新：接收 recordings 数组
app.post('/api/camera/start-recording', async (req, res) => {
    const { recordings, metadata } = req.body;
    const result = await deviceSync.startCameraRecording(recordings, metadata);
});
```

**deviceSync.js**：
```javascript
// 旧：同时启动左右手
async startCameraRecording(outputPath, metadata = {}) {
    const leftResult = await this.cameraManager.startRecording('left', outputPath, metadata);
    const rightResult = await this.cameraManager.startRecording('right', outputPath, metadata);
}

// 新：只启动配置的摄像头
async startCameraRecording(recordings, metadata = {}) {
    for (const recording of recordings) {
        const { side, output_filename } = recording;
        const result = await this.cameraManager.startRecording(side, output_filename, metadata);
    }
}
```

### 4. 更新 cameraManager.js

**移除推流状态检查**：
```javascript
// 旧：检查前端推流状态
if (!this.cameraStatus[side].streaming) {
    return { success: false, error: '摄像头未推流' };
}

// 新：不检查推流状态（录制通过 camera_server 独立完成）
// 因为录制通过 camera_server 的 ffmpeg 完成，不依赖前端推流状态
```

**参数改为文件名**：
```javascript
// 旧：接收完整路径
async startRecording(side, outputPath, metadata = {}) {
    const videoFileName = `${path.basename(outputPath)}.mp4`;
    const fullPath = path.join(path.dirname(outputPath), videoFileName);
}

// 新：接收文件名
async startRecording(side, outputFilename, metadata = {}) {
    const fullPath = path.join(PATHS.storage, 'video', outputFilename);
}
```

---

## ⚠️ 剩余问题

### 问题 1：架构不一致

**当前状态**：
- **前端** → 通过 HTTP API `/api/camera/start-recording` 启动录制
- **API 路径**：server.js → deviceSync → cameraManager → **直接调用 ffmpeg**
- **正确路径**：realtimeEngine → camera_server → **ffmpeg**

**问题**：
- `cameraManager.js` 中有自己的 ffmpeg 调用逻辑（第192-250行）
- 但 `realtimeEngine.js` 已经在通过 `camera_server` 调用 ffmpeg
- 两条路径并存，造成混乱

**应该的架构**：

```
【方案 A：通过 HTTP API】
前端 collection-controller
  ↓ fetch('/api/camera/start-recording')
server.js
  ↓ 
deviceSync
  ↓
【删除 cameraManager 的 ffmpeg 调用】
  ↓ 通过 camera_server
realtimeEngine.sendCameraCommand('start_recording')
  ↓ ZMQ
camera_server (Python)
  ↓
ffmpeg 录制 mp4

【方案 B：通过 WebSocket】
前端 collection-controller
  ↓ ws.send({ type: 'start_camera_recording' })
realtimeEngine (WebSocket handler)
  ↓ sendCameraCommand('start_recording')
camera_server (Python)
  ↓
ffmpeg 录制 mp4
```

**推荐方案 B**：
- 更简洁，不需要 HTTP API
- 与 space 键触发录制的逻辑一致
- 删除 cameraManager 中的 ffmpeg 调用代码

---

### 问题 2：预览可能仍然占用摄像头

**原因**：
- 前端 `startStreaming()` 调用 `getUserMedia()` 占用摄像头
- 即使关闭预览窗口，`stream` 对象可能没有释放
- 导致后端 ffmpeg 无法访问摄像头

**解决方案**：
1. **配置时短暂预览**：
   - 点击"预览"按钮 → 显示画面 → 手动关闭预览
   - 关闭预览时调用 `stream.getTracks().forEach(track => track.stop())`

2. **采集前不要预览**：
   - 开始采集后，前端不再调用 `getUserMedia()`
   - camera_server 独占摄像头进行录制

3. **最佳流程**：
   ```
   1. 配置摄像头 → 短暂预览确认 → 关闭预览（释放摄像头）
   2. 开始采集 → 按空格键 → camera_server 录制（独占摄像头）
   3. 采集完成 → 停止录制 → 可以再次预览
   ```

---

## 🔧 需要完成的工作

### 1. 删除 cameraManager 中的 ffmpeg 调用（高优先级）

**文件**：`cameraManager.js`

**需要删除的代码**（第192-250行左右）：
```javascript
// 构建 ffmpeg 命令
const ffmpegArgs = [
    '-f', 'dshow',
    '-video_size', this.recordingConfig.resolution,
    '-framerate', String(this.recordingConfig.fps),
    '-i', `video=${this.cameraStatus[side].label}`,
    '-c:v', 'libx264',
    '-preset', 'ultrafast',
    '-crf', '23',
    '-pix_fmt', 'yuv420p',
    '-y',
    fullPath
];

// 启动 ffmpeg 进程
const ffmpegProcess = spawn('cmd.exe', cmdArgs, {
    windowsHide: true,
    shell: false
});
```

**改为调用 realtimeEngine**：
```javascript
async startRecording(side, outputFilename, metadata = {}) {
    // 通过 realtimeEngine 调用 camera_server
    const result = await this.realtimeEngine.startCameraRecording(side, outputFilename, metadata);
    return result;
}
```

### 2. 确保预览流正确释放（中优先级）

**文件**：`camera-control.js`

**检查 `stopStreaming()` 方法**：
```javascript
async stopStreaming(side = 'both') {
    const sides = side === 'both' ? ['left', 'right'] : [side];
    
    for (const s of sides) {
        if (this.streams[s]) {
            // 停止所有轨道
            this.streams[s].getTracks().forEach(track => {
                track.stop();
                console.log(`[CameraControl] ${s}侧视频轨道已停止`);
            });
            this.streams[s] = null;
        }
    }
    
    this.isStreaming = false;
}
```

**在关闭预览窗口时调用**：
```javascript
// camera-ui.js 中的 closeCameraPreviewBtn 事件
closeCameraPreviewBtn.addEventListener('click', () => {
    // 停止预览视频播放
    leftVideo.pause();
    rightVideo.pause();
    leftVideo.srcObject = null;
    rightVideo.srcObject = null;
    
    // 【新增】释放摄像头流
    // window.cameraControl.stopStreaming('both');
    
    modal.style.display = 'none';
});
```

**注意**：如果采集过程中需要实时预览，则**不要**在关闭预览窗口时停止流，只在采集结束后停止。

### 3. 统一录制触发流程（中优先级）

**当前**：
- `collection-controller.js` 调用 `window.cameraControl.startRecording()`
- 走 HTTP API 路径

**改为**：
- `collection-controller.js` 通过 WebSocket 发送消息
- `realtimeEngine` 监听消息并调用 `camera_server`

**修改 collection-controller.js**：
```javascript
async _startCameraRecording(timestamp) {
    // 通过 WebSocket 发送录制请求
    const message = {
        type: 'start_camera_recording',
        data: {
            left: videoBaseNameLeft,  // 例如：R001_L_260614_153129
            right: videoBaseNameRight,
            timestamp: timestamp
        }
    };
    
    this.ws.send(JSON.stringify(message));
}
```

**修改 realtimeEngine.js**：
```javascript
// 在 WebSocket 消息处理中添加
case 'start_camera_recording':
    const { left, right, timestamp } = parsedMessage.data;
    await this.startCameraRecording(left, right, timestamp);
    break;
```

---

## 🎯 最终架构（推荐）

```
┌─────────────────────────────────────────────────────────┐
│                      前端 Browser                        │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  1. 配置阶段：                                          │
│     - camera-control.js: getUserMedia() → 预览         │
│     - 关闭预览：stream.stop() → 释放摄像头              │
│                                                          │
│  2. 采集阶段：                                          │
│     - collection-controller.js                          │
│     - 按空格键 → ws.send('start_camera_recording')     │
│     - 不再调用 getUserMedia()（摄像头已释放）           │
│                                                          │
└─────────────────────────────────────────────────────────┘
                        │ WebSocket
                        ↓
┌─────────────────────────────────────────────────────────┐
│                 后端 Node.js (server.js)                 │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  realtimeEngine.js                                      │
│    ↓ 监听 WebSocket 消息                                │
│    ↓ 检测到 'start_camera_recording'                    │
│    ↓ 或检测到 space 键（第一次）                        │
│    ↓                                                     │
│    ↓ sendCameraCommand('start_recording', {            │
│          side: 'left',                                  │
│          output_filename: 'R001_L_260614_153129.mp4'   │
│      })                                                  │
│                                                          │
└─────────────────────────────────────────────────────────┘
                        │ ZMQ (TCP 5555)
                        ↓
┌─────────────────────────────────────────────────────────┐
│              camera_server.py (Python)                   │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  1. 接收命令：start_recording                           │
│  2. 清理设备名称：USB Camera (4c4a:4a55) → USB Camera │
│  3. 构建 ffmpeg 命令：                                  │
│     ffmpeg -f dshow -i video=USB Camera \              │
│            -c:v libx264 -preset ultrafast \            │
│            R001_L_260614_153129.mp4                    │
│  4. 启动 ffmpeg 进程                                    │
│  5. 返回结果给 realtimeEngine                          │
│                                                          │
└─────────────────────────────────────────────────────────┘
                        │
                        ↓
┌─────────────────────────────────────────────────────────┐
│                    ffmpeg 进程                           │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  - 独占 USB 摄像头                                      │
│  - 录制 mp4 文件到 storage/video/                      │
│  - 分辨率：1280x720                                     │
│  - 帧率：30fps                                          │
│  - 编码：H.264                                          │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

**关键点**：
1. ✅ **预览和录制分离**：预览用完立即释放，录制时摄像头空闲
2. ✅ **单一录制路径**：只有 camera_server 通过 ffmpeg 录制
3. ✅ **设备名称清理**：去掉硬件ID后缀
4. ✅ **mp4 格式**：不再生成 webm 文件

---

## 📚 测试步骤

完成上述修改后，按以下步骤测试：

1. **重启服务器**：
   ```bash
   npm start
   ```

2. **打开浏览器**：
   ```
   http://localhost:3000
   ```

3. **配置摄像头**：
   - 点击"摄像头配置"
   - 选择左手摄像头（USB Camera）
   - 点击"应用配置"

4. **短暂预览**：
   - 点击设备状态窗口的"预览"按钮
   - 确认画面正常显示
   - **关闭预览窗口**（重要！释放摄像头）

5. **开始采集**：
   - 点击"开始采集"
   - 等待连接手环

6. **触发录制**：
   - 按第一次空格键
   - 查看控制台日志：应该看到 `[realtimeEngine] 🎥 检测到space，准备启动视频录制...`
   - 查看日志文件：应该看到 `[camera_server] 开始录制: left`

7. **完成采集**：
   - 完成所有动作采集
   - 点击"停止采集并保存"
   - 检查 `storage/video/` 目录：应该有 `.mp4` 文件

8. **验证结果**：
   - 没有 `.webm` 文件
   - `.mp4` 文件可以正常播放
   - 日志中没有 `webm` 相关记录

---

## 🐛 如果仍然有问题

### 日志检查清单：

1. **设备名称**：
   ```
   [camera_server] 开始录制: left
   [camera_server]   设备: USB Camera (4c4a:4a55)
   [camera_server]   清理后: USB Camera
   ```

2. **ffmpeg 命令**：
   ```
   ffmpeg -f dshow -i video=USB Camera ...
   ```
   （不应该包含 `(4c4a:4a55)`）

3. **ffmpeg 错误**：
   ```
   [CameraServer] [left] ffmpeg: [in#0 @ xxx] Error opening input: I/O error
   ```
   如果看到这个错误，说明：
   - 摄像头被其他程序占用（检查预览是否未释放）
   - 设备名称仍然不正确（检查清理逻辑）

4. **WebSocket 消息**：
   ```
   [realtimeEngine] 收到WebSocket消息: start_camera_recording
   ```
   如果没有看到，说明前端没有发送消息

---

## 📌 总结

当前提交已完成：
- ✅ 移除前端 MediaRecorder 逻辑
- ✅ 清理设备名称中的硬件ID
- ✅ 更新API接口参数格式

还需要完成：
- ⏳ 删除 cameraManager 中的 ffmpeg 调用
- ⏳ 确保预览流正确释放
- ⏳ 统一录制触发流程（WebSocket）

完成这些后，架构将会清晰，预览和录制不再冲突！
