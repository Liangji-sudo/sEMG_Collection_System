"""
test_camera_recording.py - 独立测试 camera_server 的录制功能

模拟 realtimeEngine 的完整操作流程：
1. 连接 camera_server WebSocket (:8768)
2. 扫描摄像头设备
3. 配置并打开摄像头（启动MJPEG预览）
4. 监听键盘：按空格键 → 开始录制 → 自动停止并保存 AVI
5. 检查输出文件大小和时长

当前架构: 直接 AVI + MJPEG 录制（start_continuous_recording + stop_and_save）
录制参数: 640x480, 15fps, MJPEG q:v 12, AVI 容器

用法：
  1. 先启动 camera_server:  python camera_server.py
  2. 再运行本脚本:        python test_camera_recording.py

仅依赖 Python 标准库 + websockets
"""

import asyncio
import websockets
import json
import sys
import threading
import time
import os
import shutil
import subprocess
from pathlib import Path

if sys.platform == 'win32':
    import msvcrt
else:
    import select
    import tty
    import termios

WS_URL = 'ws://localhost:8768'

# 录制参数
RECORD_DURATION = 10  # 默认录制时长（秒）


def getch():
    """跨平台获取单个按键（阻塞）"""
    if sys.platform == 'win32':
        return msvcrt.getch().decode('utf-8', errors='ignore')
    else:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


class CameraTester:
    """摄像头录制测试器"""

    def __init__(self):
        self.ws = None
        self.request_id = 0
        self.pending = {}            # request_id -> asyncio.Future
        self.frame_count = {'left': 0, 'right': 0}
        self.recording = False
        self._receiver_task = None

    def _next_id(self):
        self.request_id += 1
        return f'test_{self.request_id}_{int(time.time() * 1000)}'

    # ==================== 连接 ====================

    async def connect(self):
        print(f'连接 Camera Server: {WS_URL}')
        self.ws = await websockets.connect(WS_URL, ping_interval=None, close_timeout=5)
        print('✅ 已连接')
        # 启动消息接收器
        self._receiver_task = asyncio.create_task(self._receiver())

    async def _receiver(self):
        """接收服务器推送的消息"""
        try:
            async for msg in self.ws:
                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get('type', '')

                if msg_type == 'preview_frame':
                    side = data.get('side', '?')
                    self.frame_count[side] = self.frame_count.get(side, 0) + 1
                    total = sum(self.frame_count.values())
                    if total % 30 == 0:
                        print(f'  📷 预览帧统计: {self.frame_count}')

                elif msg_type == 'recording_status':
                    print(f'  📼 录制状态推送: recording={data.get("recording")}, '
                          f'sides={data.get("recording_sides")}')

                elif msg_type == 'status':
                    pass  # 初始状态，不打印

                elif 'request_id' in data:
                    rid = data['request_id']
                    fut = self.pending.pop(rid, None)
                    if fut and not fut.done():
                        fut.set_result(data)

        except websockets.exceptions.ConnectionClosed:
            print('⚠️ WebSocket 连接已关闭')
        except Exception as e:
            print(f'⚠️ 接收消息出错: {e}')

    # ==================== 命令发送 ====================

    async def send_command(self, command, data=None, timeout=20):
        """发送命令并等待响应"""
        if data is None:
            data = {}
        rid = self._next_id()
        payload = {'command': command, 'request_id': rid, **data}

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.pending[rid] = fut

        await self.ws.send(json.dumps(payload))

        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self.pending.pop(rid, None)
            return {'success': False, 'error': f'命令 {command} 超时 ({timeout}s)'}

    # ==================== 摄像头操作 ====================

    async def list_cameras(self):
        print('\n🔍 扫描摄像头设备...')
        result = await self.send_command('list_cameras', timeout=20)
        if result.get('success'):
            devices = result.get('devices', [])
            print(f'找到 {len(devices)} 个摄像头:')
            for i, d in enumerate(devices):
                print(f'  [{i}] {d["name"]}')
            return devices
        else:
            print(f'❌ 扫描失败: {result.get("error")}')
            return []

    async def open_camera(self, side, device_name):
        print(f'\n📷 配置 {side} 侧摄像头: {device_name}')

        # Step 1: set_camera（保存配置，不启动采集）
        r1 = await self.send_command('set_camera', {
            'side': side,
            'device_name': device_name,
            'device_id': device_name
        })
        if not r1.get('success'):
            print(f'❌ 配置失败: {r1.get("error")}')
            return False
        print('✅ 配置已保存')

        # Step 2: open_camera（启动MJPEG采集 + 自动订阅预览）
        print('🔓 打开摄像头（启动MJPEG预览）...')
        r2 = await self.send_command('open_camera', {'side': side})
        if not r2.get('success'):
            print(f'❌ 打开失败: {r2.get("error")}')
            return False
        print('✅ 摄像头已打开，MJPEG预览推流中')
        return True

    # ==================== 录制流程（核心测试） ====================

    async def start_recording(self, side, duration=RECORD_DURATION):
        """
        模拟 realtimeEngine 的完整录制流程（当前架构）：

        1. start_continuous_recording(side, output_filename)
           → camera_server 停止 MJPEG 预览，启动 ffmpeg 直接 AVI 录制
           → 录制参数: 640x480, 15fps, MJPEG q:v 12, AVI 容器

        2. 等待 duration 秒 → 模拟采集过程

        3. stop_and_save(side)
           → camera_server 终止 ffmpeg，提取时间戳（ffprobe）
           → 返回: output_path, file_size, timing (含 first/last_frame_unix)
        """
        print(f'\n{"=" * 60}')
        print(f'🎬 开始录制流程 ({side}侧, 目标 {duration} 秒)')
        print(f'   架构: 直接 AVI + MJPEG (640x480, 15fps, q:v 12)')
        print(f'{"=" * 60}')
        self.recording = True

        # ---- 步骤1: 启动直接AVI录制 ----
        timestamp = time.strftime('%y%m%d_%H%M%S')
        output_filename = f'test_recording_{side}_{timestamp}.avi'

        print(f'\n[步骤 1/2] 启动直接AVI录制...')
        print(f'  输出文件: {output_filename}')
        r1 = await self.send_command('start_continuous_recording', {
            'side': side,
            'output_filename': output_filename
        })
        if not r1.get('success'):
            print(f'  ❌ 录制启动失败: {r1.get("error")}')
            self.recording = False
            return False
        print('  ✅ 直接AVI录制已启动 (ffmpeg dshow → MJPEG → AVI)')

        # ---- 步骤2: 录制指定时长 ----
        print(f'\n[录制中] 等待 {duration} 秒（模拟采集过程）...')
        for i in range(duration):
            await asyncio.sleep(1)
            bars = '█' * (i + 1) + '░' * (duration - i - 1)
            print(f'  [{bars}] {i + 1}/{duration}s')

        # ---- 步骤3: 停止并保存 ----
        print(f'\n[步骤 2/2] 停止录制并提取时间戳...')
        r3 = await self.send_command('stop_and_save', {'side': side})

        self.recording = False

        if r3.get('success'):
            output_path = r3.get('output_path', '?')
            file_size = r3.get('file_size', 0)
            file_size_mb = file_size / (1024 * 1024) if file_size else 0
            print(f'  ✅ AVI 已保存: {output_path}')
            print(f'  📦 文件大小: {file_size_mb:.2f} MB')

            # 显示时间戳信息
            timing = r3.get('timing', {})
            if timing:
                print(f'  ⏱️  录制时间戳:')
                print(f'     ffmpeg 启动:      {timing.get("recording_started_at", "?"):.3f}')
                print(f'     ffmpeg 停止:      {timing.get("recording_stopped_at", "?"):.3f}')
                print(f'     视频时长:         {timing.get("duration", "?"):.2f}s')
                print(f'     首帧相对PTS:      {timing.get("first_pts", "?"):.3f}s')
                print(f'     首帧 Unix:        {timing.get("first_frame_unix", "?"):.3f}  (对应 H5 时间戳)')
                print(f'     末帧 Unix:        {timing.get("last_frame_unix", "?"):.3f}  (对应 H5 时间戳)')
                if timing.get('recording_started_at') and timing.get('duration'):
                    latency = timing['first_frame_unix'] - timing['recording_started_at']
                    print(f'     ffmpeg启动→首帧:  {latency:.3f}s 延迟')

            # 用 ffprobe 验证
            await self._check_output(output_path, expected_duration=duration)
            return True
        else:
            print(f'  ❌ 保存失败: {r3.get("error")}')
            return False

    # ==================== 输出检查 ====================

    async def _check_output(self, filepath, expected_duration=None):
        """用 ffprobe 检查输出视频文件的大小和时长"""
        path = Path(filepath)
        if not path.exists():
            print(f'  ❌ 文件不存在: {filepath}')
            return

        size_bytes = path.stat().st_size
        size_mb = size_bytes / (1024 * 1024)
        print(f'\n  📁 文件检查:')
        print(f'     路径:    {filepath}')
        print(f'     大小:    {size_bytes:,} bytes ({size_mb:.2f} MB)')

        # 用 ffprobe 检查
        ffprobe_path = self._find_ffprobe()
        if not ffprobe_path:
            print('  ⚠️ 未找到 ffprobe，跳过时长检查')
            return

        try:
            loop = asyncio.get_running_loop()
            duration = await loop.run_in_executor(None, self._ffprobe_duration, ffprobe_path, str(path))
            if duration is not None:
                print(f'     时长:    {duration:.2f}s')
                if expected_duration:
                    if duration < 1.0:
                        print(f'  ❌ 时长异常！预期 ~{expected_duration}s，实际 {duration:.2f}s')
                    elif duration >= expected_duration * 0.8:
                        print(f'  ✅ 时长正常（>= 预期的 80%）！')
                    else:
                        print(f'  ⚠️ 时长偏短（预期 ~{expected_duration}s，实际 {duration:.2f}s）')
            else:
                # ffprobe 可能返回 0 或无法解析
                print(f'  ⚠️ ffprobe 无法解析时长（可能是 AVI 容器特性）')
                print(f'     文件大小 {size_mb:.2f} MB 表明数据已写入，请手动播放确认')
        except Exception as e:
            print(f'  ⚠️ 检查时长失败: {e}')

    def _find_ffprobe(self):
        """查找 ffprobe"""
        ffprobe_path = shutil.which('ffprobe')
        if not ffprobe_path:
            import glob
            user_home = Path.home()
            for pattern in [
                user_home / 'AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg-*/bin/ffprobe.exe',
                user_home / 'AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg.Essentials*/ffmpeg-*/bin/ffprobe.exe',
                user_home / 'AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg.Shared*/ffmpeg-*/bin/ffprobe.exe',
            ]:
                matches = glob.glob(str(pattern))
                if matches:
                    ffprobe_path = matches[0]
                    break
        return ffprobe_path if (ffprobe_path and os.path.exists(ffprobe_path)) else None

    def _ffprobe_duration(self, ffprobe_path, filepath):
        """阻塞式 ffprobe 时长查询（在 executor 中运行）"""
        result = subprocess.run(
            [ffprobe_path, '-v', 'error',
             '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1',
             filepath],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                return float(result.stdout.strip())
            except ValueError:
                pass
        return None

    # ==================== 关闭 ====================

    async def close_camera(self, side):
        print(f'\n🔒 关闭 {side} 侧摄像头...')
        r = await self.send_command('close_camera', {'side': side})
        if r.get('success'):
            print('✅ 摄像头已关闭')
        else:
            print(f'⚠️ 关闭失败: {r.get("error")}')

    async def cleanup(self):
        if self._receiver_task:
            self._receiver_task.cancel()
        if self.ws:
            try:
                await self.ws.close()
            except:
                pass
            self.ws = None


# ==================== 主入口 ====================

async def main():
    print('=' * 60)
    print('  Camera Server 录制测试脚本')
    print('  模拟: realtimeEngine 录制流程')
    print(f'  架构: 直接 AVI + MJPEG (640x480, 15fps, q:v 12)')
    print(f'  默认录制: {RECORD_DURATION}s')
    print('=' * 60)

    tester = CameraTester()

    try:
        # 1. 连接
        await tester.connect()

        # 2. 扫描摄像头
        devices = await tester.list_cameras()
        if not devices:
            print('\n❌ 没有找到摄像头设备，请确认：')
            print('   1. USB 摄像头已连接')
            print('   2. ffmpeg 已安装')
            print('   3. camera_server.py 正在运行')
            return

        # 3. 选择摄像头
        if len(devices) == 1:
            idx = 0
            print('\n只有 1 个摄像头，自动选择')
        else:
            print('\n选择摄像头 (输入序号): ', end='', flush=True)
            try:
                idx = int(sys.stdin.readline().strip())
            except ValueError:
                print('无效输入，使用第 1 个')
                idx = 0

        device = devices[idx]
        side = 'left'
        print(f'使用: [{idx}] {device["name"]} → {side} 侧')

        # 4. 打开摄像头
        ok = await tester.open_camera(side, device['name'])
        if not ok:
            return

        # 5. 等待按键
        print('\n' + '=' * 60)
        print('  预览中... 操作说明:')
        print(f'    空格键  → 开始录制 {RECORD_DURATION} 秒 (自动停止)')
        print(f'    r       → 再次录制 {RECORD_DURATION} 秒')
        print(f'    1-9     → 录制指定秒数')
        print(f'    q       → 退出')
        print('=' * 60)

        # 使用线程读取键盘（msvcrt.getch 是阻塞的）
        loop = asyncio.get_running_loop()
        key_queue = asyncio.Queue()

        def read_keys():
            while True:
                try:
                    ch = getch()
                    loop.call_soon_threadsafe(key_queue.put_nowait, ch)
                except Exception:
                    break

        key_thread = threading.Thread(target=read_keys, daemon=True)
        key_thread.start()

        custom_duration = RECORD_DURATION

        # 主循环
        running = True
        while running:
            try:
                ch = await asyncio.wait_for(key_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            if ch == ' ' or ch.lower() == 'r':
                if tester.recording:
                    print('⚠️ 正在录制中，请等待完成...')
                else:
                    await tester.start_recording(side, duration=custom_duration)
                    custom_duration = RECORD_DURATION  # reset
                    print('\n' + '=' * 60)
                    print('  录制完成！')
                    print(f'    空格键 → 再次录制 {RECORD_DURATION}s')
                    print(f'    1-9    → 录制指定秒数')
                    print(f'    q      → 退出')
                    print('=' * 60)

            elif ch in '123456789':
                custom_duration = int(ch)
                print(f'  ⏱️  下次录制时长设为: {custom_duration}s (按空格开始)')

            elif ch.lower() == 'q':
                running = False
                print('\n👋 退出...')

            elif ch == '\x03':  # Ctrl+C
                raise KeyboardInterrupt()

    except KeyboardInterrupt:
        print('\n\n⚠️ 用户中断')
    except websockets.exceptions.InvalidURI:
        print(f'\n❌ 无法连接到 {WS_URL}')
        print('   请确认 camera_server.py 已启动')
    except ConnectionRefusedError:
        print(f'\n❌ 连接被拒绝: {WS_URL}')
        print('   请确认 camera_server.py 正在运行')
    except Exception as e:
        print(f'\n❌ 未预期的错误: {e}')
        import traceback
        traceback.print_exc()
    finally:
        print('\n清理资源...')
        await tester.cleanup()
        print('测试结束')


if __name__ == '__main__':
    asyncio.run(main())
