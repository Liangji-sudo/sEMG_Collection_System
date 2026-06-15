# ffmpeg 使用方式对比

## 方案对比

### 方案 1：我们当前的方式（直接调用命令行）

```python
import subprocess

subprocess.run([
    'ffmpeg',
    '-f', 'dshow',
    '-i', 'video=USB Camera',
    '-c:v', 'libx264',
    'output.mp4'
])
```

**优点**：
- ✅ 最直接、最透明
- ✅ 无额外 Python 依赖
- ✅ 性能最优（没有中间层）
- ✅ 调试简单（可以直接看到完整的 ffmpeg 命令）
- ✅ 出错时可以直接在命令行测试
- ✅ 所有 ffmpeg 功能都可用

**缺点**：
- ❌ 需要手动构建命令字符串
- ❌ 代码不够 Pythonic

---

### 方案 2：使用 ffmpeg-python

```python
import ffmpeg

stream = (
    ffmpeg
    .input('video=USB Camera', format='dshow')
    .output('output.mp4', vcodec='libx264')
    .run()
)
```

**优点**：
- ✅ API 更友好、更 Pythonic
- ✅ 链式调用，代码更优雅

**缺点**：
- ❌ **仍然需要安装系统的 ffmpeg.exe**（这是关键！）
- ❌ 增加了一个依赖：需要 `pip install ffmpeg-python`
- ❌ 多了一层抽象，调试更复杂
- ❌ 遇到问题时需要理解两层：Python API + ffmpeg 命令
- ❌ 某些高级功能可能不支持

---

### 方案 3：使用 PyAV

```python
import av

container = av.open('output.mp4', 'w')
stream = container.add_stream('h264', rate=30)
# ... 需要手动处理每一帧
```

**优点**：
- ✅ 性能最好（直接调用 C 库）
- ✅ 可以精细控制每一帧

**缺点**：
- ❌ 使用非常复杂
- ❌ 学习曲线陡峭
- ❌ 需要理解编解码器细节
- ❌ 代码量大

---

## 关键事实

### 🚨 重要：所有方案都需要系统安装 ffmpeg！

无论你用哪种方式，都需要：
1. 下载并安装 ffmpeg 可执行文件（ffmpeg.exe）
2. 将其添加到系统 PATH，或指定完整路径

**`pip install ffmpeg-python` 不会安装 ffmpeg.exe！**
它只是安装了一个 Python 包装器，底层还是调用你系统上的 ffmpeg.exe。

---

## 我们的选择：方案 1

### 为什么选择直接调用命令行？

1. **透明度**：
   ```python
   # 我们的代码直接显示完整的 ffmpeg 命令
   print(f'ffmpeg命令: {" ".join(ffmpeg_cmd)}')
   ```
   日志中可以看到：
   ```
   ffmpeg -f dshow -video_size 1280x720 -framerate 30 -i video=HP HD Camera ...
   ```
   出问题时可以直接复制这个命令在终端测试！

2. **简单性**：
   - 不需要额外的 `pip install`
   - 项目依赖更少
   - 新手也能看懂代码

3. **可靠性**：
   - 直接调用官方工具，没有中间层可能出错
   - ffmpeg 命令行是最稳定、最成熟的接口

4. **灵活性**：
   - 支持所有 ffmpeg 功能
   - 可以轻松添加任何命令行参数

---

## 什么时候应该用 ffmpeg-python？

如果你的项目：
- ✅ 需要复杂的视频处理流水线（多个输入、多个输出、复杂滤镜）
- ✅ 代码可读性比性能更重要
- ✅ 团队熟悉 Python 链式 API

例如：
```python
# 复杂的视频处理流水线
(
    ffmpeg
    .input('input1.mp4')
    .overlay(ffmpeg.input('watermark.png'))
    .filter('scale', 1280, 720)
    .output('output.mp4')
    .run()
)
```

但对于我们的场景（简单录制），直接调用命令行已经足够好了。

---

## 总结

| 特性 | 直接命令行 | ffmpeg-python | PyAV |
|------|-----------|---------------|------|
| 需要安装 ffmpeg.exe | ✅ 是 | ✅ 是 | ✅ 是 |
| Python 依赖 | 无 | 需要 pip install | 需要 pip install |
| 代码复杂度 | 简单 | 中等 | 复杂 |
| 调试难度 | 简单 | 中等 | 困难 |
| 性能 | 高 | 高 | 最高 |
| 功能完整性 | 100% | 95% | 95% |
| 适合场景 | 简单录制、转换 | 复杂流水线 | 实时处理 |

**我们的选择**：直接命令行 ✅

因为我们的需求很简单：录制摄像头到文件。直接调用命令行是最简单、最可靠的方案。
