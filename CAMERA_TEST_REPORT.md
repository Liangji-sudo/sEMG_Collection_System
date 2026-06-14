# Camera Server 功能测试报告

## 测试时间
2026-06-14

## 测试结果：✅ 全部通过

### 1. ffmpeg 安装检测
- ✅ 自动检测到 WinGet 安装的 ffmpeg
- 路径：`C:\Users\liangji\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe`

### 2. 摄像头枚举
- ✅ 成功枚举到 2 个摄像头：
  - HP HD Camera（内置摄像头）
  - USB Camera（外置USB摄像头）

### 3. 摄像头配置
- ✅ 成功配置左侧摄像头为 "HP HD Camera"
- ✅ 配置信息正确保存到 camera_server

### 4. 状态查询
- ✅ 成功获取摄像头配置和录制状态
- 返回数据格式正确

### 5. 视频录制
- ✅ 成功启动 ffmpeg 录制进程（PID: 19372）
- ✅ 录制 5 秒后成功生成视频文件
- 文件：`storage/video/test_video.mp4`
- 大小：15MB
- 编码：H.264, 1280x720@30fps

## 问题修复记录

### 问题 1：ffmpeg 未安装
- **现象**：FileNotFoundError，系统找不到 ffmpeg
- **原因**：ffmpeg 虽然通过 WinGet 安装，但不在 Python 的 PATH 中
- **解决**：添加 `find_ffmpeg()` 函数自动查找 WinGet 安装的 ffmpeg

### 问题 2：设备枚举解析失败
- **现象**：枚举成功但返回空列表
- **原因**：新版 ffmpeg 输出格式改变，旧的解析逻辑失效
- **解决**：更新正则表达式适配新格式 `[in#0 @ xxx] "设备名称" (video)`

### 问题 3：输出编码问题
- **现象**：Python 输出中文乱码
- **原因**：Windows GBK 编码
- **解决**：添加 `encoding='utf-8', errors='ignore'` 参数

## 完整工作流程

```
前端选择摄像头
    ↓
调用 /api/camera/set-mapping
    ↓
server.js → realtimeEngine.onCameraSetConfig()
    ↓
通过 WebSocket 发送给 camera_server
    ↓
camera_server 保存配置（self.cameras）
    ↓
开始采集时调用 start_recording
    ↓
启动 ffmpeg 进程录制视频
    ↓
停止采集时调用 stop_recording
    ↓
ffmpeg 保存视频文件到 storage/video/
```

## 下一步测试

1. ✅ **已完成**：独立测试脚本验证
2. 🔜 **待测试**：在实际采集流程中测试
3. 🔜 **待测试**：同时录制左右手摄像头
4. 🔜 **待测试**：长时间录制稳定性

## 相关文件

- `camera_server.py` - 摄像头服务器主程序
- `test_camera.py` - 测试脚本
- `list_devices_raw.py` - 设备枚举调试脚本
- `check_ffmpeg.py` - ffmpeg 检测脚本
