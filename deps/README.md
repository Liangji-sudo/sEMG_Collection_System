# 相机功能依赖安装指南

## 概述

相机功能（`camera_server.py`）基于 **ffmpeg** 实现 USB 摄像头的 MJPEG 采集和录制。

| 组件 | 用途 | 来源 |
|------|------|------|
| `ffmpeg.exe` | 摄像头采集、MJPEG 编码、AVI 封装 | [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) |
| `ffprobe.exe` | 视频时间戳提取 | 随 ffmpeg 一起安装 |

> **注意：** ffprobe 包含在 ffmpeg 的安装包中，无需单独下载。

---

## Windows 安装步骤

### 方法一：WinGet（推荐，自动加入 PATH）

```powershell
winget install Gyan.FFmpeg.Essentials
```

安装后**重启终端**，验证：

```powershell
ffmpeg -version
ffprobe -version
```

### 方法二：手动下载

1. 打开 https://www.gyan.dev/ffmpeg/builds/
2. 下载 **`ffmpeg-release-essentials.zip`**
3. 解压到任意目录，例如 `C:\ffmpeg`
4. 将 `C:\ffmpeg\bin` 添加到系统 PATH：
   - 右键"此电脑" → 属性 → 高级系统设置 → 环境变量
   - 在 `Path`（用户变量或系统变量）中添加 `C:\ffmpeg\bin`
5. 重启终端验证

---

## 验证安装

运行 `check_deps.bat` 脚本：

```
deps\check_deps.bat
```

或手动检查：

```powershell
ffmpeg -version
ffprobe -version
```

预期输出类似：

```
ffmpeg version 7.x.x-essentials_build-www.gyan.dev ...
ffprobe version 7.x.x-essentials_build-www.gyan.dev ...
```

---

## Python 依赖

相机功能所需的 Python 包已包含在项目根目录的 `requirements.txt` 中：

```
websockets>=11.0
```

安装：

```powershell
pip install -r requirements.txt
```

---

## camera_server.py 查找 ffmpeg 的逻辑

`camera_server.py` 启动时会按以下顺序搜索 ffmpeg：

1. **`shutil.which('ffmpeg')`** → 搜索 PATH 中的 ffmpeg
2. **WinGet 路径** → 搜索 `%LOCALAPPDATA%/Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe`

如果两种方式都找不到，摄像头功能将不可用，但不会影响其他模块。
