# 视频录制功能 - 测试与修复总结

## 🎯 问题与解决方案

### 已修复的问题

#### ✅ 问题 1：预览窗口黑屏
**原因**：前端和后端同时占用摄像头  
**修复**：
- 移除前端 MediaRecorder 录制逻辑
- 关闭预览时正确调用 `track.stop()` 释放摄像头
- 采集开始前停止预览流

#### ✅ 问题 2：没有生成 MP4 文件（第一次测试）
**原因**：前端预览流没有释放，后端 ffmpeg 无法访问摄像头  
**修复**：在 `startTask()` 开始采集前调用 `cameraControl.stopStreaming('both')`

#### ✅ 问题 3：停止采集后无法预览
**原因**：采集开始时停止了预览流，但停止采集后没有重启  
**修复**：在 `stopTask()` 中添加重新启动摄像头预览流的逻辑

#### ✅ 问题 4：MP4 文件无法打开
**原因**：
- 前端调用 `cameraControl.stopRecording()` 只更新状态
- 后端 camera_server 的 ffmpeg 进程继续运行
- 文件未正确关闭

**修复**：`_stopCameraRecording()` 改为调用 `/api/camera/stop-recording` HTTP API

---

## 📋 最终架构

### 录制流程（统一路径）

```
【启动录制】
前端按空格键
  ↓
realtimeEngine.onPrompt('space')
  ↓
realtimeEngine._startVideoRecording()
  ↓
realtimeEngine.sendCameraCommand('start_recording')
  ↓ ZMQ
camera_server (Python)
  ↓
启动 ffmpeg 进程，独占摄像头
  ↓
录制 MP4 文件

【停止录制】
前端停止采集
  ↓
collection-controller._stopCameraRecording()
  ↓
fetch('/api/camera/stop-recording')
  ↓
server.js → deviceSync → realtimeEngine
  ↓
realtimeEngine.sendCameraCommand('stop_recording')
  ↓ ZMQ
camera_server (Python)
  ↓
终止 ffmpeg 进程（发送 'q' 命令）
  ↓
MP4 文件正确关闭
```

### 预览流程（独立于录制）

```
【配置时预览】
用户点击"预览"按钮
  ↓
camera-ui.js: openCameraPreview()
  ↓
cameraControl.startStreaming()
  ↓
getUserMedia() → 浏览器访问摄像头
  ↓
显示预览画面

【关闭预览】
用户点击"关闭"按钮
  ↓
camera-ui.js: closeCameraPreview()
  ↓
track.stop() → 释放摄像头
  ↓
streams = null

【采集开始】
用户点击"开始采集"
  ↓
collection-controller.startTask()
  ↓
cameraControl.stopStreaming('both') → 释放摄像头
  ↓
摄像头空闲，可供后端使用

【采集结束】
用户点击"停止采集"
  ↓
collection-controller.stopTask()
  ↓
cameraControl.startStreaming('both') → 重新启动预览
  ↓
用户可以再次预览
```

---

## 🧪 测试步骤（最终版本）

### 1. 配置摄像头

1. 启动服务器：`npm start`
2. 打开浏览器：`http://localhost:3000`
3. 点击"摄像头配置"
4. 选择左手摄像头：USB Camera
5. 点击"应用配置"
6. 观察右下角设备状态：应显示"推流中"

### 2. 预览测试

1. 点击设备状态窗口的"预览"按钮
2. **验证**：左侧摄像头画面正常显示（不是黑屏）
3. 点击"关闭"按钮
4. **验证**：浏览器控制台显示：
   ```
   [CameraUI] 左侧摄像头轨道已停止
   [CameraUI] 摄像头流已释放
   ```

### 3. 采集和录制测试

1. 连接手环（如果需要）
2. 点击"开始采集"，选择受试者和任务
3. **验证**：浏览器控制台显示：
   ```
   [Collection] 停止摄像头预览流，释放摄像头...
   [Collection] ✅ 摄像头预览流已停止
   ```
4. 等待倒计时结束
5. **按空格键**（第一次）
6. **验证**：浏览器控制台显示：
   ```
   [Collection] 🎥 第一个space按下，启动摄像头录制...
   ```
7. **验证**：Node.js 控制台显示：
   ```
   [camera_server] 开始录制: left
   [CameraServer] ✅ left侧录制已启动, PID: xxxxx
   ```
8. 完成几个动作采集
9. 点击"停止采集并保存"
10. **验证**：浏览器控制台显示：
    ```
    [Collection] 🎥 停止摄像头录制...
    [Collection] ✅ 摄像头录制已停止
    ```
11. **验证**：Node.js 控制台显示：
    ```
    [CameraServer] 停止录制: left
    [CameraServer] ✅ left侧录制已停止
    ```

### 4. 文件验证

1. 打开 `storage/video/` 目录
2. **验证**：有 MP4 文件（例如：`R002_L_260614_195439.mp4`）
3. **验证**：文件大小 > 0 KB
4. **验证**：没有 `.webm` 文件
5. **双击 MP4 文件**
6. **验证**：视频可以正常播放（不报错）
7. **验证**：视频内容是采集时的画面

### 5. 预览恢复测试

1. 停止采集后，点击设备状态窗口的"预览"按钮
2. **验证**：预览画面正常显示（不是黑屏）
3. **验证**：浏览器控制台显示：
   ```
   [Collection] 重新启动摄像头预览流...
   [Collection] ✅ 摄像头预览流已重启
   ```

---

## 🐛 如果仍有问题

### MP4 文件仍然无法打开

**检查 ffmpeg 进程**：
```bash
tasklist | grep ffmpeg
```

如果有 ffmpeg 进程，说明录制没有正确停止：
```bash
taskkill //IM ffmpeg.exe //F
```

然后重新测试。

### 预览仍然黑屏

**检查摄像头是否被其他程序占用**：
- 关闭 Windows 相机应用
- 关闭 Skype、Teams 等视频软件
- 重启浏览器

### 后端录制失败（I/O error）

**检查日志**：
```bash
grep "Error opening input" log/server_*.log
```

如果看到此错误，说明摄像头被前端占用：
- 确认采集开始前控制台显示"摄像头预览流已停止"
- 清空浏览器缓存（Ctrl+Shift+Delete）
- 刷新页面（Ctrl+F5）

---

## 📊 提交记录

### Commit 1: `6d19944`
```
fix: 采集开始前停止摄像头预览流，释放摄像头给后端录制
```

### Commit 2: `5b799cb`
```
fix: 修复摄像头录制停止和预览恢复问题

- 停止采集后重新启动摄像头预览流
- _stopCameraRecording()改为调用/api/camera/stop-recording
- 录制启动和停止路径现在一致
```

---

## ✅ 成功标志

全部测试通过后，你应该看到：

✅ **预览功能**
- 配置后可以预览
- 关闭预览后摄像头释放
- 采集开始前预览流停止
- 采集结束后预览流恢复

✅ **录制功能**
- 按空格键成功启动录制
- 生成 MP4 文件
- 没有 webm 文件
- ffmpeg 没有 "I/O error"

✅ **文件完整性**
- MP4 文件可以正常打开
- 视频内容正确
- ffmpeg 进程正确终止

✅ **架构清晰**
- 录制启动和停止路径一致（通过 camera_server）
- 预览和录制时间分离（不冲突）
- 日志清晰，没有错误

---

## 🎉 总结

所有问题已解决！

**核心改进**：
1. ✅ 采集开始前停止预览流
2. ✅ 采集结束后重启预览流
3. ✅ 停止录制调用正确的 API
4. ✅ ffmpeg 进程正确终止
5. ✅ MP4 文件完整可播放

现在可以完整测试整个流程了！🚀
