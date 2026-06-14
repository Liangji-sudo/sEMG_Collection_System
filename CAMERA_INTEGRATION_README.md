# USB摄像头录制功能集成文档

## 📋 功能概述

本系统集成了USB摄像头录制功能，用于在数据采集过程中同步录制左右手视频，实现视频与EMG/IMU数据的时间同步。

### 核心特性

- ✅ 双USB摄像头支持（左手/右手）
- ✅ 视频流实时预览
- ✅ 基于空格键的时间同步
- ✅ 视频文件与H5数据文件关联
- ✅ WebM格式录制（VP8编码，2.5Mbps）
- ✅ 浏览器和Electron环境兼容

---

## 🏗️ 架构设计

### 后端模块

#### 1. **cameraManager.js**
摄像头管理器，负责摄像头状态管理和录制控制。

**主要方法：**
- `setCameraMapping(side, cameraInfo)` - 设置摄像头映射（左/右）
- `startStreaming(side)` - 开始视频流
- `stopStreaming(side)` - 停止视频流
- `startRecording(side, outputPath, metadata)` - 开始录制
- `stopRecording(side)` - 停止录制
- `getCameraStatus(side)` - 获取状态

**状态管理：**
```javascript
cameraStatus = {
    left: {
        deviceId: null,
        label: '',
        streaming: false,
        recording: false,
        resolution: { width: 0, height: 0 },
        fps: 0
    },
    right: { ... }
}
```

#### 2. **deviceSync.js**
集成摄像头管理到设备同步模块。

**新增API：**
- `setCameraMapping(side, cameraInfo)`
- `startCameraStreaming(side)`
- `stopCameraStreaming(side)`
- `startCameraRecording(outputPath, metadata)`
- `stopCameraRecording()`
- `getCameraStatus()`

#### 3. **server.js**
HTTP API路由，暴露摄像头控制接口。

**API端点：**
```
POST /api/camera/set-mapping
POST /api/camera/start-streaming
POST /api/camera/stop-streaming
POST /api/camera/start-recording
POST /api/camera/stop-recording
GET  /api/camera/status
```

#### 4. **storage_server.py**
H5文件写入视频文件信息。

**新增方法：**
- `record_video_info(params)` - 将视频文件名写入H5属性

**H5属性：**
```python
attrs['video_left'] = 'subject001_session1_pinch_20260611_143022_left.webm'
attrs['video_right'] = 'subject001_session1_pinch_20260611_143022_right.webm'
attrs['video_start_timestamp'] = 1686480622.345  # space按下时刻
```

#### 5. **realtimeEngine.js**
转发视频录制信息到storage_server。

**新增处理：**
- `onVideoRecordingStarted(data)` - 处理视频录制启动事件

---

### 前端模块

#### 1. **camera-control.js**
前端摄像头控制核心，使用浏览器MediaDevices API。

**主要功能：**
- 枚举USB摄像头（`navigator.mediaDevices.enumerateDevices()`）
- 获取视频流（`getUserMedia()`）
- 录制视频（`MediaRecorder API`）
- 保存视频文件（WebM格式）

**关键代码：**
```javascript
// 枚举摄像头
const devices = await navigator.mediaDevices.enumerateDevices();
const cameras = devices.filter(d => d.kind === 'videoinput');

// 启动视频流
const stream = await navigator.mediaDevices.getUserMedia({
    video: {
        deviceId: { exact: cameraId },
        width: { ideal: 1280 },
        height: { ideal: 720 },
        frameRate: { ideal: 30 }
    }
});

// 录制视频
const recorder = new MediaRecorder(stream, {
    mimeType: 'video/webm;codecs=vp8',
    videoBitsPerSecond: 2500000
});
```

#### 2. **camera-ui.js**
UI交互控制，绑定按钮事件和弹窗管理。

**主要功能：**
- 摄像头推流按钮控制
- 摄像头配置弹窗（选择左右手摄像头）
- 摄像头预览弹窗（双摄像头实时预览）
- 设备状态显示更新

#### 3. **collection-controller.js**
采集流程集成，实现时间同步。

**关键逻辑：**
```javascript
// 采集开始时重置状态
this._cameraRecordingStarted = false;
this._enableSpaceKey();

// 第一个space按下时启动录制
async _onSpaceKeyPressed() {
    if (!this._cameraRecordingStarted) {
        await this._startCameraRecording(timestamp);
        this._cameraRecordingStarted = true;
    }
}

// 采集结束时停止录制
async stopTask() {
    await this._stopCameraRecording();
}
```

#### 4. **index.html**
UI组件和弹窗。

**新增UI：**
- 初始界面：摄像头推流按钮
- 设备状态悬浮窗：摄像头状态、磁盘空间
- 摄像头配置弹窗：选择左右手摄像头
- 摄像头预览弹窗：双摄像头实时预览

---

## 🔄 工作流程

### 1. 启动推流

```
用户操作                  前端                    后端
   │                      │                       │
   ├──点击"摄像头推流"──→  │                       │
   │                      ├──打开配置弹窗          │
   │                      ├──枚举摄像头            │
   │                      │  (getUserMedia)       │
   │                      │                       │
   ├──选择摄像头──────→    │                       │
   ├──点击"应用配置"──→    │                       │
   │                      ├──setCameraMapping()──→│
   │                      │                       ├──保存映射
   │                      │                       │
   │                      ├──startStreaming()────→│
   │                      │  (MediaStream启动)    │
   │                      │                       ├──更新状态
   │                      │                       │
   │                      ←──────推流成功──────────┤
   │                      │                       │
```

### 2. 采集与录制

```
采集流程                 录制控制                H5文件
   │                      │                       │
   ├──开始采集            │                       │
   │  (startTask)         ├──重置录制状态          │
   │                      │  _cameraRecordingStarted=false
   │                      │                       │
   ├──启用空格键监听       │                       │
   │  (_enableSpaceKey)   │                       │
   │                      │                       │
   ├──【第一个space】──→   │                       │
   │  按下空格键           ├──启动录制              │
   │                      │  _startCameraRecording()
   │                      │  timestamp记录         │
   │                      │                       │
   │                      ├──生成文件名            │
   │                      │  {user}_session{N}_   │
   │                      │  {stage}_{time}_      │
   │                      │  left/right.webm      │
   │                      │                       │
   │                      ├──startRecording()────→├──写入H5属性
   │                      │  (MediaRecorder)      │  video_left
   │                      │                       │  video_right
   │                      │                       │  video_start_timestamp
   │                      │                       │
   ├──第二、三个space      │                       │
   │  （不启动录制）        │  （忽略）              │
   │                      │                       │
   ├──采集结束            │                       │
   │  (stopTask)          ├──停止录制              │
   │                      │  _stopCameraRecording()
   │                      │                       │
   │                      ├──stopRecording()      │
   │                      │  保存视频文件          ├──关闭H5文件
   │                      │                       │
```

### 3. 时间同步机制

```
时间轴：
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                                                  
采集开始                                          采集结束
  │                                                 │
  ├──准备阶段────┬──space1──┬──space2──┬──space3──┤
  │             │          │          │          │
  │             │          │          │          │
EMG数据  ========●==========●==========●==========│
  │             │          │          │          │
  │             │          │          │          │
视频录制        [开始录制]                         [停止]
                ▲                                  ▲
                │                                  │
           timestamp                          duration
        (space1时刻)                          计算

同步点：
- space1按下时刻 = 视频第一帧时间戳
- EMG数据中的prompt时间戳 = space按下时刻
- H5文件的video_start_timestamp = space1时刻
```

---

## 📁 文件组织

### 当前实现（浏览器下载）

```
下载文件夹/
├── subject001_session1_pinch_20260611_143022_left.webm
└── subject001_session1_pinch_20260611_143022_right.webm
```

### 建议的最终结构（Electron环境）

```
storage/
├── discrete_gesture/
│   ├── static/
│   │   ├── sitting/
│   │   │   ├── normal/
│   │   │   │   ├── S001/
│   │   │   │   │   ├── S001_session1_pinch_20260611_143022.h5
│   │   │   │   │   └── videos/
│   │   │   │   │       ├── S001_session1_pinch_20260611_143022_left.webm
│   │   │   │   │       └── S001_session1_pinch_20260611_143022_right.webm
```

---

## 🧪 测试指南

### 开发环境测试（浏览器）

#### 1. 启动系统
```bash
cd sEMG_Collection_System
npm start
```

浏览器访问：`http://localhost:3000`

#### 2. 测试推流功能

**步骤：**
1. 在初始界面点击"摄像头推流"按钮
2. 在配置弹窗中选择左右手摄像头
3. 点击"应用配置"
4. 等待推流启动成功

**验证：**
- 按钮变为红色"停止推流"
- 设备状态窗口显示"推流中"
- 控制台输出推流成功日志

#### 3. 测试预览功能

**步骤：**
1. 推流启动后，点击设备状态窗口的"预览"按钮
2. 查看双摄像头实时画面

**验证：**
- 弹窗显示左右两个视频画面
- 视频流畅无卡顿

#### 4. 测试录制功能

**步骤：**
1. 确保推流已启动
2. 点击"采集"按钮，填写受试者信息
3. 开始采集任务
4. **按下空格键**（第一次）
5. 继续采集，再按几次空格键
6. 停止采集

**验证：**
- 第一次space按下后，控制台显示"摄像头录制已启动"
- 录制过程中，设备状态显示"录制中"
- 采集结束后，浏览器下载两个.webm文件

#### 5. 测试H5文件关联

**步骤：**
1. 完成一次完整采集（包含space按键）
2. 使用Python读取生成的H5文件：

```python
import h5py

with h5py.File('subject001_session1_pinch_20260611_143022.h5', 'r') as f:
    print('视频文件信息：')
    print(f'  video_left: {f.attrs.get("video_left")}')
    print(f'  video_right: {f.attrs.get("video_right")}')
    print(f'  video_start_timestamp: {f.attrs.get("video_start_timestamp")}')
    
    # 查看prompt时间戳
    if 'prompts' in f:
        prompts = f['prompts'][:]
        print(f'\nPrompts:')
        for p in prompts:
            print(f'  {p["name"].decode()}: {p["time"]}')
```

**验证：**
- H5文件包含video_left和video_right属性
- video_start_timestamp与第一个space的prompt时间戳一致

---

## 🐛 常见问题

### 1. 摄像头权限被拒绝

**症状：**
```
[CameraControl] 无法获取摄像头权限: NotAllowedError
```

**解决：**
- 浏览器地址栏检查摄像头权限设置
- Chrome: 地址栏左侧摄像头图标 → 允许
- Firefox: 地址栏左侧 → 权限 → 摄像头 → 允许

### 2. 找不到摄像头设备

**症状：**
```
[CameraUI] 找到 0 个摄像头设备
```

**解决：**
- 检查USB摄像头是否正常连接
- Windows设备管理器检查驱动
- 其他软件（如QQ、微信）是否占用摄像头
- 刷新页面重新枚举

### 3. 录制未启动

**症状：**
- 按下space键后没有录制提示

**解决：**
- 确认已启动摄像头推流
- 检查浏览器控制台错误日志
- 确认是**第一个**space按键（后续space不触发录制）

### 4. 视频文件未下载

**症状：**
- 采集结束后没有视频文件下载

**解决：**
- 检查是否按下过space键（必须有space才触发录制）
- 检查浏览器下载设置（可能被阻止）
- 查看控制台是否有保存失败的错误

### 5. H5文件中没有视频信息

**症状：**
```python
f.attrs.get("video_left")  # 返回 None
```

**解决：**
- 确认录制已启动（控制台有"摄像头录制已启动"日志）
- 检查realtimeEngine是否正常运行
- 检查storage_server.py日志

---

## 🚀 Electron打包注意事项

### 当前限制

由于开发环境使用浏览器调试，视频文件通过浏览器下载API保存，存在以下限制：

1. **用户需手动保存**：浏览器会弹出下载对话框
2. **路径不可控**：文件保存到浏览器默认下载目录
3. **无法自动关联**：视频文件与H5文件不在同一目录

### Electron环境优化方案

在Electron环境下，需要修改视频保存逻辑：

#### 方案1：主进程保存（推荐）

**前端（camera-control.js）：**
```javascript
// 通过IPC发送Blob到主进程
async _saveRecording(side, outputBasePath, metadata) {
    const blob = new Blob(this.recordedChunks[side], { type: 'video/webm' });
    const arrayBuffer = await blob.arrayBuffer();
    const buffer = Buffer.from(arrayBuffer);
    
    // 发送到主进程
    window.electronAPI.saveVideoFile({
        side: side,
        buffer: buffer,
        basePath: outputBasePath,
        metadata: metadata
    });
}
```

**主进程（main.js）：**
```javascript
const { ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');

ipcMain.handle('save-video-file', async (event, data) => {
    const { side, buffer, basePath, metadata } = data;
    const fileName = `${path.basename(basePath)}_${side}.webm`;
    const outputDir = path.dirname(basePath);
    const fullPath = path.join(outputDir, 'videos', fileName);
    
    // 确保目录存在
    await fs.promises.mkdir(path.dirname(fullPath), { recursive: true });
    
    // 写入文件
    await fs.promises.writeFile(fullPath, buffer);
    
    console.log(`视频文件已保存: ${fullPath}`);
    return { success: true, path: fullPath };
});
```

#### 方案2：直接文件系统写入

**前端使用Node.js fs模块：**
```javascript
const fs = require('fs');
const path = require('path');

async _saveRecording(side, outputBasePath, metadata) {
    const blob = new Blob(this.recordedChunks[side], { type: 'video/webm' });
    const arrayBuffer = await blob.arrayBuffer();
    const buffer = Buffer.from(arrayBuffer);
    
    const fileName = `${path.basename(outputBasePath)}_${side}.webm`;
    const outputDir = path.dirname(outputBasePath);
    const videosDir = path.join(outputDir, 'videos');
    const fullPath = path.join(videosDir, fileName);
    
    // 创建目录
    await fs.promises.mkdir(videosDir, { recursive: true });
    
    // 写入文件
    await fs.promises.writeFile(fullPath, buffer);
    
    console.log(`视频文件已保存: ${fullPath}`);
}
```

---

## 📊 性能考虑

### 视频文件大小估算

**编码参数：**
- 分辨率：1280x720 (720p)
- 帧率：30 fps
- 比特率：2.5 Mbps
- 编码：VP8

**文件大小：**
- 1分钟：约 18.75 MB
- 5分钟：约 93.75 MB
- 10分钟：约 187.5 MB

**双摄像头：**
- 10分钟采集 = 约 375 MB (左右各187.5 MB)

### 磁盘空间监控

系统已实现磁盘空间实时监控，建议：
- **预留空间**：至少保留 10 GB 可用空间
- **实时提醒**：可用空间 < 5 GB 时弹出警告
- **自动停止**：可用空间 < 2 GB 时禁止开始录制

---

## 🔒 数据安全

### 视频文件管理

1. **文件命名**：包含受试者ID、session、stage、时间戳
2. **访问控制**：仅本地存储，不上传云端
3. **删除策略**：与H5文件同步删除，避免遗留

### 隐私保护

1. **摄像头权限**：必须用户明确授权
2. **预览控制**：默认不开启预览，按需查看
3. **数据脱敏**：受试者ID使用编码而非真实姓名

---

## 📝 后续优化方向

### 短期（已完成）
- ✅ 前后端摄像头管理模块
- ✅ 双摄像头推流和录制
- ✅ 时间同步机制
- ✅ H5文件关联

### 中期（待实现）
- ⏳ Electron环境视频保存优化
- ⏳ videos/子目录自动创建
- ⏳ 视频文件自动压缩（可选）
- ⏳ 录制暂停/恢复功能

### 长期（规划中）
- 📋 视频标注工具集成
- 📋 视频与EMG数据可视化同步播放
- 📋 视频质量自动检测（模糊、遮挡）
- 📋 多摄像头角度支持（>2个）

---

## 🎯 总结

USB摄像头录制功能已完整集成到系统中，核心流程如下：

1. **推流启动**：用户配置并启动双摄像头推流
2. **采集开始**：系统准备录制，等待space触发
3. **时间同步**：第一个space按下时启动录制，记录时间戳
4. **数据关联**：视频文件名写入H5文件属性
5. **采集结束**：停止录制，保存视频文件

**关键优势：**
- ✨ 时间精确同步（基于space按键）
- 🔗 数据文件关联（H5属性记录视频文件名）
- 🎥 双摄像头支持（左右手独立录制）
- 🌐 环境兼容（浏览器和Electron）

---

## 📞 技术支持

如有问题，请查看：
1. 浏览器控制台日志
2. storage_server.py输出
3. realtimeEngine.js日志
4. 本文档"常见问题"章节

**联系方式：**
- 项目仓库：[GitHub链接]
- 技术文档：[文档链接]
