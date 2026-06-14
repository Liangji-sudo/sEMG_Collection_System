# 安装 ffmpeg 指南

## 方案1：使用 Chocolatey（最简单）

如果你已经安装了 Chocolatey，在管理员权限的 PowerShell 中运行：

```powershell
choco install ffmpeg
```

## 方案2：手动安装

1. 访问 ffmpeg 官网下载页面：
   https://ffmpeg.org/download.html#build-windows

2. 推荐下载地址（已编译好的Windows版本）：
   https://www.gyan.dev/ffmpeg/builds/
   
3. 下载 "ffmpeg-release-essentials.zip"

4. 解压到一个目录，例如：
   `C:\ffmpeg\`

5. 将 ffmpeg 的 bin 目录添加到系统 PATH：
   - 右键"此电脑" -> "属性" -> "高级系统设置"
   - "环境变量" -> 系统变量中找到 "Path"
   - 点击"编辑" -> "新建"
   - 添加：`C:\ffmpeg\bin`
   - 确定保存

6. 重新打开命令行窗口，测试：
   ```bash
   ffmpeg -version
   ```

## 方案3：便携式方案（不修改PATH）

如果不想修改系统PATH，可以把 ffmpeg.exe 直接放到项目目录：

1. 下载 ffmpeg
2. 将 `ffmpeg.exe` 复制到项目根目录
3. 修改 camera_server.py，使用相对路径

---

安装完成后，重启服务器即可使用摄像头录制功能。
