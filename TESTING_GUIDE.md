# 视频录制功能测试指南

## ✅ 已完成的重构

### 架构变更

**之前（问题）**：
```
前端 getUserMedia() ─→ 占用摄像头 ─→ 预览 + MediaRecorder录制webm
                            ↓
后端 cameraManager ──→ ffmpeg录制mp4 ─→ ❌ 摄像头已被占用，录制失败
```

**现在（修复）**：
```
【预览阶段】
前端 getUserMedia() ─→ 短暂预览 ─→ 关闭预览 ─→ 释放摄像头（track.stop()）
                                              ↓
                                        摄像头空闲

【录制阶段】
前端 ─→ HTTP API ─→ deviceSync ─→ realtimeEngine ─→ camera_server ─→ ffmpeg独占摄像头 ─→ 录制mp4
```

### 关键修改

1. **删除前端 MediaRecorder 录制**（camera-control.js）
   - ❌ 不再使用 `new MediaRecorder()`
   - ❌ 不再生成 webm 文件
   - ✅ 只调用后端 API

2. **删除 cameraManager 的 ffmpeg 调用**（cameraManager.js）
   - ❌ 不再直接调用 `spawn('ffmpeg')`
   - ✅ 只负责状态管理
   - ✅ 实际录制由 camera_server 完成

3. **统一录制入口**（deviceSync.js）
   - ✅ 直接调用 `realtimeEngine.sendCameraCommand()`
   - ✅ 通过 ZMQ 与 camera_server 通信

4. **正确释放预览流**（camera-ui.js）
   - ✅ 关闭预览时调用 `track.stop()` 释放摄像头
   - ✅ 清空 `cameraControl.streams` 对象

5. **清理设备名称**（camera_server.py）
   - ✅ 去掉硬件ID后缀 `(4c4a:4a55)`
   - ✅ ffmpeg 使用正确的设备名称

---

## 🧪 测试步骤

### 准备工作

1. **启动服务器**：
   ```bash
   cd C:/Users/liangji/Desktop/华为横向/sEMG_Collection_System
   npm start
   ```

2. **检查进程**：
   - ✅ Node.js 服务器（端口 8080）
   - ✅ camera_server（端口 5555）
   - ✅ BLE 服务器（端口 8764/8766/8768）

3. **打开浏览器**：
   ```
   http://localhost:3000
   ```

---

### 测试 1：摄像头配置和预览

**目的**：验证预览功能正常，且关闭预览后摄像头正确释放

**步骤**：

1. 点击顶部菜单栏的 **"摄像头配置"**

2. 在弹出的配置窗口中：
   - 左手摄像头：选择 `USB Camera`
   - 点击 **"应用配置"**

3. 等待配置完成，观察：
   - ✅ 右下角设备状态窗口显示 "推流中"
   - ✅ 浏览器控制台显示：`[CameraUI] 左手摄像头推流成功`

4. 点击设备状态窗口的 **"预览"** 按钮

5. 预览窗口应该显示：
   - ✅ 左侧摄像头画面（不是黑屏）
   - ✅ 画面清晰，实时更新

6. **关闭预览窗口**（点击右上角 "关闭" 按钮）

7. 检查浏览器控制台：
   ```
   [CameraUI] 左侧摄像头轨道已停止
   [CameraUI] 摄像头流已释放
   ```

8. **验证摄像头已释放**：
   - 打开 Windows 相机应用
   - ✅ 应该能正常打开摄像头（说明已释放）
   - 关闭 Windows 相机应用

**预期结果**：
- ✅ 预览画面正常显示
- ✅ 关闭预览后摄像头正确释放
- ✅ 其他应用可以访问摄像头

---

### 测试 2：采集时的视频录制（空格键触发）

**目的**：验证通过空格键触发的录制功能正常

**步骤**：

1. 连接手环（如果还没连接）：
   - 扫描设备
   - 连接设备1

2. 点击 **"开始采集"**
   - 选择受试者：`R001` 或其他
   - 选择任务类型：例如 "离散手势"

3. 等待倒计时结束

4. **按下空格键**（第一次）

5. 检查控制台日志：
   ```
   [realtimeEngine] 🎥 检测到space，准备启动视频录制...
   [realtimeEngine] 启动左手摄像头录制: R001_L_260614_190310.mp4
   [camera_server] 开始录制: left
   [camera_server]   设备: USB Camera (4c4a:4a55)
   [camera_server]   清理后: USB Camera
   [camera_server] ffmpeg命令: ... -i video=USB Camera ...
   [CameraServer] ✅ left侧录制已启动
   ```

6. 完成几个动作的采集

7. 点击 **"停止采集并保存"**

8. 检查文件：
   - 打开 `storage/video/` 目录
   - ✅ 应该有 `R001_L_260614_190310.mp4` 文件
   - ✅ 文件大小 > 0 KB
   - ✅ **没有** `.webm` 文件

9. **播放 mp4 文件**：
   - 双击打开
   - ✅ 应该能正常播放
   - ✅ 视频内容是采集时的画面

**预期结果**：
- ✅ 空格键成功触发录制
- ✅ 生成 mp4 文件
- ✅ 没有 webm 文件
- ✅ ffmpeg 没有报错 "I/O error"
- ✅ 视频可以正常播放

---

### 测试 3：检查日志文件

**目的**：验证日志中没有 webm 相关记录

**步骤**：

1. 打开最新的日志文件：
   ```
   C:\Users\liangji\Desktop\华为横向\sEMG_Collection_System\log\server_*.log
   ```

2. 搜索 `webm`：
   - ✅ 应该**没有**找到任何结果

3. 搜索 `Error opening input`：
   - ✅ 应该**没有**找到 ffmpeg 错误

4. 搜索 `start_recording`：
   ```
   [camera_server] 收到命令: start_recording
   [CameraServer] 开始录制: left
   [CameraServer]   设备: USB Camera (4c4a:4a55)
   [CameraServer]   清理后: USB Camera
   [CameraServer] ffmpeg命令: ... -i video=USB Camera ...
   [CameraServer] ✅ left侧录制已启动, PID: xxxxx
   ```

5. 搜索 `stop_recording`：
   ```
   [camera_server] 收到命令: stop_recording
   [CameraServer] 停止录制: left
   [CameraServer] ✅ left侧录制已停止
   ```

**预期结果**：
- ✅ 没有 webm 相关日志
- ✅ 没有 ffmpeg 错误
- ✅ 录制命令正常执行

---

### 测试 4：H5 文件中的视频信息

**目的**：验证 H5 文件正确记录了 mp4 文件信息

**步骤**：

1. 找到最新的 H5 文件：
   ```
   C:\Users\liangji\Desktop\华为横向\sEMG_Collection_System\storage\discrete_gesture\R001_sess*.h5
   ```

2. 使用 Python 读取 H5 文件：
   ```python
   import h5py
   
   with h5py.File('R001_sess*.h5', 'r') as f:
       print(f.attrs.get('video_left'))
       print(f.attrs.get('video_right'))
   ```

3. 检查输出：
   - ✅ 应该显示：`R001_L_260614_190310.mp4`
   - ✅ **不应该**包含 `.webm`

**预期结果**：
- ✅ H5 文件记录的是 mp4 文件名
- ✅ 没有 webm 文件名

---

## 🐛 故障排查

### 问题 1：预览仍然黑屏

**可能原因**：
- 摄像头被其他程序占用（关闭 Windows 相机、Skype、Teams 等）
- 浏览器权限问题（检查浏览器是否允许访问摄像头）

**排查步骤**：
1. 打开 Windows 相机应用，看能否正常显示
2. 如果 Windows 相机也黑屏，说明摄像头硬件问题
3. 检查浏览器控制台是否有 `getUserMedia` 错误

### 问题 2：录制时 ffmpeg 报错 "I/O error"

**可能原因**：
- 摄像头仍被前端预览占用

**排查步骤**：
1. 确认**已关闭预览窗口**
2. 检查浏览器控制台是否有 `摄像头流已释放` 日志
3. 重启浏览器，重新配置摄像头

### 问题 3：仍然生成 webm 文件

**可能原因**：
- 代码未更新或浏览器缓存

**排查步骤**：
1. 确认 git commit：`af13ae3`
2. 重启服务器：`npm start`
3. 清空浏览器缓存（Ctrl+Shift+Delete）
4. 刷新页面（Ctrl+F5）

### 问题 4：没有生成 mp4 文件

**可能原因**：
- camera_server 未启动
- ffmpeg 未安装或路径不正确

**排查步骤**：
1. 检查日志：搜索 `[camera_server]`
   - 如果没有，说明 camera_server 未启动
2. 检查 ffmpeg：
   ```bash
   ffmpeg -version
   ```
   - 如果报错，参考 `install_ffmpeg.md` 安装

---

## 📊 成功标志

测试全部通过后，你应该看到：

✅ **预览功能**：
- 预览窗口显示画面（不是黑屏）
- 关闭预览后摄像头正确释放

✅ **录制功能**：
- 按空格键成功触发录制
- 生成 mp4 文件
- 没有 webm 文件

✅ **日志清洁**：
- 没有 webm 相关日志
- 没有 "I/O error" 错误
- ffmpeg 命令使用正确的设备名称（不含硬件ID）

✅ **架构清晰**：
- 前端只负责预览
- 后端 camera_server 负责录制
- 两者不冲突

---

## 🎯 架构总结

```
┌─────────────────────────────────────────────────────────┐
│                    前端 Browser                          │
│                                                          │
│  【预览】                                                │
│  camera-control.js: getUserMedia() → 显示画面          │
│  camera-ui.js: 关闭预览 → track.stop() → 释放摄像头    │
│                                                          │
│  【录制触发】                                            │
│  collection-controller.js                               │
│    ↓ fetch('/api/camera/start-recording')              │
└──────────────────────────────────────────────────────────┘
                        │
                        ↓
┌─────────────────────────────────────────────────────────┐
│              后端 Node.js (server.js)                    │
│                                                          │
│  server.js → deviceSync.js                              │
│    ↓ deviceSync.startCameraRecording()                  │
│    ↓ realtimeEngine.sendCameraCommand()                 │
│    ↓ ZMQ (TCP 5555)                                     │
└─────────────────────────────────────────────────────────┘
                        │
                        ↓
┌─────────────────────────────────────────────────────────┐
│            camera_server.py (Python)                     │
│                                                          │
│  1. 接收 ZMQ 命令：start_recording                      │
│  2. 清理设备名称：USB Camera (4c4a:4a55) → USB Camera │
│  3. 调用 ffmpeg 独占摄像头录制 mp4                     │
│  4. 返回结果                                            │
└─────────────────────────────────────────────────────────┘
                        │
                        ↓
                   📹 mp4 文件
```

**关键点**：
- ✅ 前端预览和后端录制**时间分离**（预览 → 关闭 → 录制）
- ✅ 只有一个路径录制视频（camera_server）
- ✅ 没有 MediaRecorder，没有 webm
- ✅ 摄像头不会被同时占用

---

测试完成后，请告诉我结果！🎉
