# HLS 预录制方案 - 完整集成指南

## 🎯 方案目标

实现你要求的核心功能：
1. **配置摄像头时**：立即启动 HLS 持续录制（不写盘）
2. **按空格键时**：标记录制起始分段
3. **H5 结束时**：停止并合并 space → H5结束 的精确片段为 MP4
4. **预览功能**：静态帧预览（不占用摄像头）

## 📊 架构流程

```
【初始界面 - 配置摄像头】
用户选择摄像头
  ↓
调用 camera_server: set_camera
  ↓
自动启动 HLS 持续录制
  ↓
每1秒生成一个 .ts 分段（临时文件）
  ↓
循环保留最近 60 个分段（60秒缓冲）
  ↓
持续运行...

【采集界面 - H5 开始】
点击"开始采集"
  ↓
H5 文件打开
  ↓
HLS 继续录制...
  ↓
按空格键（第1次）
  ↓
调用 camera_server: mark_recording_start
  ↓
标记当前分段索引（例如：segment_45）
  ↓
继续 HLS 录制...
  ↓
采集数据...
  ↓
H5 结束（停止采集）
  ↓
调用 camera_server: stop_and_save
  ↓
停止 HLS 录制
  ↓
合并 segment_45 → segment_120 为 MP4
  ↓
最终 MP4：space → H5结束 的精确片段
  ↓
清理临时 .ts 文件

【预览功能】
用户点击"预览"按钮
  ↓
调用 camera_server: get_preview_frame
  ↓
从最新的 .ts 分段提取一帧
  ↓
返回 base64 编码的 JPEG
  ↓
前端显示静态图像
  ↓
用户点击"刷新"→ 再次请求
```

## ✅ 已完成的修改

### 1. camera_server.py（已完成）

**新增类**：`HLSRecorder`
- `start()`：启动持续 HLS 录制
- `mark_start()`：标记录制起始分段
- `stop_and_save(output_path)`：停止并合并为 MP4
- `get_preview_frame()`：提取预览帧

**新增 API**：
- `set_camera`：配置摄像头 → 自动启动 HLS 录制
- `mark_recording_start`：标记录制起始分段
- `stop_and_save`：停止并保存 MP4
- `get_preview_frame`：获取静态预览帧

### 2. realtimeEngine.js（部分完成）

**已修改**：
- `onPrompt`：检测第一个 space 时调用 `_markVideoRecordingStart`

**需要添加**：
- `_markVideoRecordingStart` 方法（见下文）

## 🔧 需要完成的修改

### 1. realtimeEngine.js

#### 添加 `_markVideoRecordingStart` 方法

在 `_startVideoRecording` 方法后面添加：

```javascript
async _markVideoRecordingStart(timestamp, stageName) {
    console.log('[realtimeEngine] 🎥 标记HLS录制起始点（按空格键）...');

    if (!this.camera_connected) {
        console.error('[realtimeEngine] ❌ camera_server未连接');
        return;
    }

    // 获取 collection bins
    const binFileNameLeft = this.collectionBins?.dev1;
    const binFileNameRight = this.collectionBins?.dev2;

    if (!binFileNameLeft && !binFileNameRight) {
        console.warn('[realtimeEngine] 未找到collection bins');
        return;
    }

    // 初始化 videoFileNames
    this.videoFileNames = this.videoFileNames || {};

    // 标记左手摄像头
    if (binFileNameLeft) {
        const videoFileName = `${binFileNameLeft}.mp4`;
        try {
            const result = await this.sendCameraCommand('mark_recording_start', {
                side: 'left'
            });

            if (result.success) {
                console.log('[realtimeEngine] ✅ 左手摄像头录制起始已标记，分段:', result.mark_segment);
                this.videoFileNames.left = videoFileName;

                // 通知 storage_server 记录视频信息
                this._saveVideoInfoToH5({
                    video_left: videoFileName,
                    video_right: null,
                    video_start_timestamp: timestamp,
                    h5_file_name: this.currentH5FileName || null
                });
            }
        } catch (error) {
            console.error('[realtimeEngine] 标记左手摄像头失败:', error);
        }
    }

    // 标记右手摄像头（类似）
    if (binFileNameRight) {
        // ... 同上
    }
}
```

#### 修改 `onCollectionStop` 方法

将停止录制改为停止并保存：

```javascript
async onCollectionStop(completed) {
    // 【修改】停止视频录制并保存 MP4
    if (this.videoRecordingStarted && this.camera_connected) {
        console.log('[realtimeEngine] 🎥 停止视频录制并保存 MP4...');

        // 停止左手摄像头并保存
        if (this.videoFileNames?.left) {
            try {
                await this.sendCameraCommand('stop_and_save', {
                    side: 'left',
                    output_filename: this.videoFileNames.left
                });
                console.log('[realtimeEngine] ✅ 左手摄像头 MP4 已保存');
            } catch (err) {
                console.error('[realtimeEngine] 保存左手 MP4 失败:', err);
            }
        }

        // 停止右手摄像头并保存（类似）
        if (this.videoFileNames?.right) {
            // ... 同上
        }

        this.videoRecordingStarted = false;
        this.videoFileNames = null;
    }

    // ... 其余代码保持不变
}
```

### 2. collection-controller.js

#### 修改 `startTask` 方法

**移除**采集开始时启动录制的代码（第1115-1132行）：

```javascript
// 【删除】采集开始时立即启动摄像头录制
// 因为 HLS 录制已在配置摄像头时启动
// if (!isTestMode && window.cameraControl) {
//     console.log('[Collection] 🎥 采集开始，立即启动摄像头录制...');
//     ...
// }
```

HLS 录制在配置摄像头时已经启动，不需要在采集开始时再启动。

#### 修改 `_handleSpaceKey` 方法

保持不变，space 键只记录时间戳。后端 realtimeEngine 会自动标记 HLS 起始点。

### 3. camera-ui.js（新增静态帧预览）

#### 修改 `openCameraPreview` 方法

将视频预览改为静态帧预览：

```javascript
async function openCameraPreview() {
    const modal = document.getElementById('cameraPreviewModal');
    if (!modal) return;

    modal.style.display = 'flex';

    // 【修改】不再使用 getUserMedia，改为请求静态帧
    // 左侧摄像头
    if (window.cameraControl.selectedCameras.left) {
        await refreshPreviewFrame('left');
    }

    // 右侧摄像头
    if (window.cameraControl.selectedCameras.right) {
        await refreshPreviewFrame('right');
    }
}

async function refreshPreviewFrame(side) {
    console.log(`[CameraUI] 刷新${side}侧预览帧...`);

    try {
        const response = await fetch('/api/camera/get-preview-frame', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ side: side })
        });

        const result = await response.json();

        if (result.success && result.frame) {
            // 显示 base64 图像
            const imgElement = document.getElementById(`${side}CameraPreview`);
            if (imgElement) {
                imgElement.src = `data:image/jpeg;base64,${result.frame}`;
                console.log(`[CameraUI] ✅ ${side}侧预览帧已更新`);
            }
        } else {
            console.error(`[CameraUI] 获取${side}侧预览帧失败:`, result.error);
        }
    } catch (error) {
        console.error(`[CameraUI] 刷新${side}侧预览帧失败:`, error);
    }
}
```

#### 修改 HTML 模板

将 `<video>` 元素改为 `<img>` 元素，并添加"刷新"按钮：

```html
<div class="preview-container">
    <div class="preview-item">
        <h3>左手摄像头</h3>
        <img id="leftCameraPreview" style="width: 100%; max-height: 400px; background: #000;">
        <button onclick="refreshPreviewFrame('left')">🔄 刷新</button>
    </div>
    <div class="preview-item">
        <h3>右手摄像头</h3>
        <img id="rightCameraPreview" style="width: 100%; max-height: 400px; background: #000;">
        <button onclick="refreshPreviewFrame('right')">🔄 刷新</button>
    </div>
</div>
```

### 4. server.js（新增 API 路由）

添加 `get-preview-frame` API：

```javascript
// 获取预览帧
app.post('/api/camera/get-preview-frame', async (req, res) => {
    try {
        const { side } = req.body;
        
        const result = await realtimeEngine.sendCameraCommand('get_preview_frame', {
            side: side
        });

        res.json(result);
    } catch (error) {
        console.error('[server.js] 获取预览帧失败:', error);
        res.json({ success: false, error: error.message });
    }
});
```

## 🧪 测试步骤

### 1. 测试 HLS 持续录制

1. 启动服务器：`npm start`
2. 配置摄像头（选择 USB Camera）
3. **验证**：检查日志应该显示：
   ```
   [HLSRecorder] [left] 启动HLS录制
   [HLSRecorder] [left] ✅ HLS录制已启动, PID: xxxxx
   ```
4. **验证**：检查临时目录 `storage/video/temp/left/` 应该有 `.ts` 分段文件生成
5. 等待几秒，应该看到 `segment_00000.ts`, `segment_00001.ts`, ...

### 2. 测试静态帧预览

1. 点击设备状态中的"预览"按钮
2. **验证**：应该显示静态图像（不是黑屏）
3. 点击"刷新"按钮
4. **验证**：图像应该更新

### 3. 测试完整录制流程

1. 点击"开始采集"
2. **验证**：HLS 录制继续运行（不会重新启动）
3. 按空格键（第1次）
4. **验证**：日志显示：
   ```
   [realtimeEngine] ✅ 左手摄像头录制起始已标记，分段: 45
   ```
5. 完成几个动作采集
6. 点击"停止采集并保存"
7. **验证**：日志显示：
   ```
   [HLSRecorder] [left] 停止录制并保存
   [HLSRecorder] [left]   起始分段: 45
   [HLSRecorder] [left]   结束分段: 120
   [HLSRecorder] [left] 合并分段 45 -> 120
   [HLSRecorder] [left] ✅ MP4已保存: storage/video/R001_L_xxx.mp4
   ```
8. **验证**：`storage/video/` 目录有 MP4 文件
9. **验证**：播放 MP4，第一帧应该是按空格键的时刻

## 📝 关键注意事项

### HLS 分段精度

- 每1秒一个分段
- MP4 起始精度：±1秒
- 如果需要更高精度，可以修改 `-hls_time` 参数（例如 `0.5` = 每0.5秒一个分段）

### 临时文件管理

- HLS 分段保存在 `storage/video/temp/left/` 和 `storage/video/temp/right/`
- 每次录制后自动清理
- 循环保留最近 60 个分段（防止磁盘占满）

### 摄像头占用

- HLS 录制一旦启动，摄像头被 ffmpeg 独占
- 前端无法使用 `getUserMedia()` 预览
- 只能通过 `get_preview_frame` 获取静态帧

### 性能考虑

- HLS 持续录制会占用 CPU（~5-15%）
- 临时 .ts 文件会占用磁盘空间（~1GB/小时）
- 合并分段需要几秒时间（取决于分段数量）

## 🎯 优势总结

✅ **精确时间戳**：MP4 起始就是 space 时刻（±1秒）  
✅ **无启动延迟**：HLS 已在运行，标记即时生效  
✅ **不丢帧**：持续录制，不会错过任何画面  
✅ **前端不占用**：摄像头独占给后端，前端用静态预览  
✅ **无需后期处理**：MP4 保存后即为最终文件  

## 🚀 后续优化

1. **更高精度分段**：修改 `-hls_time` 参数
2. **多摄像头支持**：左右手同时 HLS 录制
3. **实时预览流**：使用 HLS.js 在前端播放 HLS 流（延迟 2-3 秒）
4. **断点续录**：如果采集异常中断，可以继续标记下一个起始点

---

**当前状态**：camera_server.py 已完成，需要完成前端和 realtimeEngine 的集成。

测试完成后，请告诉我结果！🎉
