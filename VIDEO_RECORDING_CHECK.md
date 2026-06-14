# 视频录制功能检查报告

## 检查日期
2026-06-14

---

## 📋 用户需求检查

### 需求 1：拉流（预览）
**期望**：
- 打开相机后就开始拉流
- 拉流一直在 camera_server 中进行
- 点击"预览"按钮时弹窗显示实时画面
- 视频流数据链路清晰

**实际实现**：
❌ **不符合** - 当前实现有架构偏差

#### 当前实现方式：
1. **前端预览**：
   - 使用浏览器 `navigator.mediaDevices.getUserMedia()` 直接访问摄像头
   - 视频流在浏览器中处理，不经过 camera_server
   - 代码位置：`camera-control.js:212-219`

2. **后端录制**：
   - camera_server 通过 ffmpeg 独立录制视频到文件
   - 不涉及流媒体推送

#### 数据链路：

**前端预览链路（当前）**：
```
浏览器 → getUserMedia() → 操作系统驱动 → USB摄像头
         ↓
    <video> 元素直接显示
```

**后端录制链路（当前）**：
```
camera_server → ffmpeg → dshow → 操作系统驱动 → USB摄像头
                  ↓
            写入 mp4 文件
```

**关键问题**：
- ❌ camera_server **没有**拉流功能
- ❌ camera_server **没有**推流给前端的功能
- ❌ 前端和后端**各自独立**访问摄像头

#### 用户期望的架构（需要重构）：
```
USB摄像头 → camera_server (拉流)
              ↓
         ┌────┴────┐
         ↓         ↓
    WebRTC推流  ffmpeg录制
         ↓         ↓
    前端预览    mp4文件
```

这需要：
1. camera_server 使用 ffmpeg 持续拉流
2. 实现 WebRTC 或 WebSocket + JPEG 推流到前端
3. 同时可以从流中录制到文件

---

### 需求 2：写盘触发条件

#### 2.1 开始写盘
**期望**：
- ✅ 敲击第一次空格键（开始 h5 采集后）
- ✅ 多次敲击空格，仅第一次触发视频录制

**实际实现**：✅ **完全符合**

**代码验证**：
```javascript
// collection-controller.js:3474-3498
async _onSpaceKeyPressed() {
    if (!this._isRunning) {
        console.log('[Collection] 采集未运行，忽略空格键');
        return;
    }

    // ... 发送 space prompt ...

    // 【关键】第一个space按下时，启动视频录制
    if (!this._cameraRecordingStarted) {
        console.log('[Collection] 🎥 第一个space按下，启动摄像头录制...');
        await this._startCameraRecording(timestamp);
        this._cameraRecordingStarted = true;  // ← 标志位，防止重复启动
    }
}
```

**验证**：
- ✅ 只有 `_cameraRecordingStarted === false` 时才启动录制
- ✅ 启动后设置标志为 `true`，后续空格键不再触发

---

#### 2.2 结束写盘
**期望**：
- ✅ 当 h5 写完保存时，同时结束 mp4 写盘

**实际实现**：✅ **完全符合**

**代码验证**：

1. **正常完成采集**：
```javascript
// collection-controller.js:2283
this.sendToRealtimeEngine('collection_stop', { completed: true });
```

2. **手动停止采集**：
```javascript
// collection-controller.js:1398
this.sendToRealtimeEngine('collection_stop', { completed: false });
```

3. **异常中断采集**：
```javascript
// collection-controller.js:1528
await this._stopCameraRecording();  // 在 abortTask() 中直接停止
```

4. **realtimeEngine 收到 collection_stop**：
```javascript
// realtimeEngine.js:375-422
async onCollectionStop(completed) {
    // 【步骤1】停止视频录制
    if (this.videoRecordingStarted && this.camera_connected) {
        console.log('[realtimeEngine] 🎥 停止视频录制...');
        
        // 停止左手摄像头
        if (this.collectionBins?.dev1) {
            await this.sendCameraCommand('stop_recording', { side: 'left' });
        }
        
        // 停止右手摄像头
        if (this.collectionBins?.dev2) {
            await this.sendCameraCommand('stop_recording', { side: 'right' });
        }
        
        this.videoRecordingStarted = false;
    }

    // 【步骤2】关闭 H5 文件
    if (this.stageFileOpen && !this.isClosingStageFile) {
        await this.closeStageFile({
            collection_status: completed ? 'completed' : 'manual_stopped'
        });
    }
}
```

**验证**：
- ✅ 停止视频录制 **在** 关闭 H5 文件 **之前**
- ✅ 保证 mp4 和 h5 同时结束
- ✅ 同时处理左右手摄像头

---

## 🎯 总结

| 需求 | 状态 | 说明 |
|------|------|------|
| **拉流/预览** | ❌ 不符合 | 前端直接访问摄像头，camera_server 不负责拉流和推流 |
| **第一次空格触发录制** | ✅ 符合 | 使用 `_cameraRecordingStarted` 标志防止重复触发 |
| **多次空格不重复触发** | ✅ 符合 | 标志位机制正确 |
| **h5保存时结束mp4** | ✅ 符合 | `onCollectionStop()` 中先停止视频，后关闭 h5 |

---

## 🔧 需要修复的问题

### 问题：拉流/预览架构不符合需求

**当前架构的问题**：
1. 前端和后端各自独立访问摄像头
2. 同一个摄像头被两个进程占用可能导致冲突
3. 无法保证前端预览和后端录制的画面完全同步

**建议方案**：

#### 方案 A：轻量级方案（推荐）
**保持当前架构，但优化顺序**：
1. 前端只在"预览"时使用 getUserMedia
2. 开始录制后，前端释放摄像头
3. camera_server 接管摄像头并录制
4. 如果需要预览，使用 ffmpeg 生成缩略图定期推送

**优点**：
- 改动最小
- 避免同时占用摄像头
- 录制性能最优

**缺点**：
- 录制时无法实时预览（或预览有延迟）

#### 方案 B：完整方案
**camera_server 统一管理拉流和推流**：
1. camera_server 持续运行 ffmpeg 拉流
2. 使用 `tee` 分流：一路推送给前端预览，一路录制到文件
3. 推流方式：
   - WebSocket + JPEG 帧（简单，延迟 100-500ms）
   - WebRTC（复杂，延迟 < 100ms）

**优点**：
- 符合用户期望的架构
- 前后端画面完全同步
- 避免摄像头冲突

**缺点**：
- 需要大量重构
- camera_server 增加推流功能（Python + asyncio + 编码）
- 前端增加流解码功能

---

## 📝 当前架构的优势

尽管不符合用户期望的"拉流"架构，但当前实现也有优点：

1. **简单可靠**：前端预览和后端录制完全解耦
2. **录制质量高**：ffmpeg 直接录制，没有中间层损耗
3. **易于调试**：两个独立的路径，问题定位清晰
4. **资源占用低**：不需要额外的推流进程

---

## 🤔 建议

### 短期建议（保持当前架构）
1. ✅ 保持当前的空格键触发逻辑（已正确）
2. ✅ 保持当前的 h5/mp4 同步停止逻辑（已正确）
3. 📝 在文档中说明：前端预览和后端录制是独立的
4. 📝 提示用户：录制开始前关闭预览窗口（避免冲突）

### 长期建议（如果需要符合期望架构）
1. 重构 camera_server，添加持续拉流功能
2. 实现 WebSocket + JPEG 推流（先做简单版本）
3. 前端增加流播放器（使用 `<img>` 标签显示 JPEG）
4. 录制时从同一个流 fork 一路到文件

---

## ✅ 当前可以正常工作的功能

1. ✅ 第一次空格键触发录制（不重复）
2. ✅ h5 保存时同步停止 mp4
3. ✅ 左右手摄像头独立控制
4. ✅ ffmpeg 自动查找和使用
5. ✅ 视频文件命名与 bin 文件对应
6. ✅ 异常中断时正确停止录制

---

## 测试建议

### 测试场景 1：正常采集流程
1. 打开浏览器 http://localhost:3000
2. 配置左手摄像头
3. 开始采集
4. 按第一次空格键 → 检查是否启动录制
5. 按多次空格键 → 检查是否只录制一次
6. 完成采集 → 检查 storage/video/ 是否生成 mp4

### 测试场景 2：异常中断
1. 开始采集
2. 按第一次空格键启动录制
3. 点击"异常中断"按钮
4. 选择中断原因
5. 检查 mp4 文件是否正确保存

### 测试场景 3：同时使用多个摄像头
1. 连接 2 个 USB 摄像头
2. 配置左手和右手摄像头
3. 开始采集并按空格
4. 检查是否生成两个 mp4 文件
