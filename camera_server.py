"""
camera_server.py - USB摄像头管理服务器

功能：
1. 枚举USB摄像头设备（通过ffmpeg dshow）
2. 实时MJPEG预览推流（WebSocket推送帧给前端）
3. HLS录制（采集时通过realtimeEngine触发）
4. 支持多客户端同时连接（前端预览 + realtimeEngine录制控制）

WebSocket端口: 8768

架构对标 ble_server：前端直连WebSocket发命令、收数据
"""

import asyncio
import websockets
import json
import sys

# 强制输出立即刷新
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
import subprocess
import os
import signal
import threading
import time
from datetime import datetime
from pathlib import Path
import shutil
import glob
import re
import base64

# ==================== ffmpeg 查找 ====================

def find_ffmpeg():
    """查找 ffmpeg 可执行文件"""
    ffmpeg_path = shutil.which('ffmpeg')
    if ffmpeg_path:
        return ffmpeg_path

    if sys.platform == 'win32':
        user_home = Path.home()
        # 尝试多个 WinGet 包名模式（Gyan.FFmpeg / Gyan.FFmpeg.Essentials / Gyan.FFmpeg.Shared）
        win_patterns = [
            user_home / 'AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe',
            user_home / 'AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg.Essentials*/ffmpeg-*/bin/ffmpeg.exe',
            user_home / 'AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg.Shared*/ffmpeg-*/bin/ffmpeg.exe',
        ]
        for pattern in win_patterns:
            matches = glob.glob(str(pattern))
            if matches:
                print(f'[CameraServer] 找到 WinGet 安装的 ffmpeg: {matches[0]}')
                return matches[0]

    return None


# ==================== MJPEG 实时采集器 ====================

class CameraCapture:
    """实时摄像头采集器 - 通过 ffmpeg MJPEG pipe 抓取帧并推送给订阅者"""

    def __init__(self, side, device_name, ffmpeg_path, frame_queue):
        self.side = side
        self.device_name = device_name
        self.ffmpeg_path = ffmpeg_path
        self.frame_queue = frame_queue  # asyncio.Queue，用于跨线程传递帧
        self.process = None
        self.running = False
        self.reader_thread = None
        self.latest_frame_b64 = None
        self.fps_frame_count = 0
        self.fps_last_time = time.time()
        self.current_fps = 0

    def start(self):
        """启动 MJPEG 采集"""
        if self.running:
            print(f'[CameraCapture] [{self.side}] 已在运行中')
            return True

        clean_name = re.sub(r'\s*\([0-9a-fA-F:]+\)\s*$', '', self.device_name).strip()

        cmd = [
            self.ffmpeg_path,
            '-f', 'dshow',
            '-video_size', '1280x720',
            '-framerate', '30',
            '-i', f'video={clean_name}',
            '-vcodec', 'mjpeg',
            '-q:v', '8',
            '-f', 'image2pipe',
            'pipe:1'
        ]

        print(f'[CameraCapture] [{self.side}] 启动MJPEG采集: {clean_name}')

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )

            self.running = True
            self.reader_thread = threading.Thread(target=self._read_frames, daemon=True)
            self.reader_thread.start()

            # 启动 stderr 读取线程（避免管道阻塞）
            stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
            stderr_thread.start()

            print(f'[CameraCapture] [{self.side}] ✅ MJPEG采集已启动, PID: {self.process.pid}')
            return True

        except Exception as e:
            print(f'[CameraCapture] [{self.side}] ❌ 启动失败: {e}')
            import traceback
            traceback.print_exc()
            return False

    def _read_frames(self):
        """读取 MJPEG 帧（在单独线程中运行）"""
        buf = b''
        while self.running and self.process and self.process.poll() is None:
            try:
                data = self.process.stdout.read(4096)
                if not data:
                    time.sleep(0.001)
                    continue
                buf += data

                # 查找 JPEG 边界 (SOI: 0xFFD8, EOI: 0xFFD9)
                while True:
                    start = buf.find(b'\xff\xd8')
                    if start == -1:
                        break
                    end = buf.find(b'\xff\xd9', start + 2)
                    if end == -1:
                        # 不完整的帧，保留从 start 开始的数据
                        if start > 0:
                            buf = buf[start:]
                        break
                    # 提取完整帧
                    frame = buf[start:end + 2]
                    buf = buf[end + 2:]

                    # Base64 编码
                    b64 = base64.b64encode(frame).decode()

                    # 线程安全地放入 asyncio 队列
                    try:
                        self.frame_queue.put_nowait({
                            'side': self.side,
                            'frame': b64,
                            'timestamp': time.time()
                        })
                    except asyncio.QueueFull:
                        # 队列满了，丢弃旧帧
                        try:
                            self.frame_queue.get_nowait()
                            self.frame_queue.put_nowait({
                                'side': self.side,
                                'frame': b64,
                                'timestamp': time.time()
                            })
                        except:
                            pass

                    # FPS 统计
                    self.fps_frame_count += 1

            except Exception as e:
                print(f'[CameraCapture] [{self.side}] 读取帧出错: {e}')
                break

        # 进程已退出
        if self.running:
            print(f'[CameraCapture] [{self.side}] ffmpeg进程意外退出')
            self.running = False

    def _read_stderr(self):
        """读取 ffmpeg stderr（避免管道阻塞）"""
        try:
            while self.running and self.process and self.process.poll() is None:
                line = self.process.stderr.readline()
                if not line:
                    break
                # 只打印关键信息
                line_str = line.decode('utf-8', errors='ignore').strip()
                if 'error' in line_str.lower() or 'cannot' in line_str.lower():
                    print(f'[CameraCapture] [{self.side}] ffmpeg: {line_str}')
        except:
            pass

    def stop(self):
        """停止 MJPEG 采集"""
        print(f'[CameraCapture] [{self.side}] 停止MJPEG采集...')
        self.running = False

        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except:
                try:
                    self.process.kill()
                except:
                    pass
            self.process = None

        print(f'[CameraCapture] [{self.side}] MJPEG采集已停止')

    def get_status(self):
        """获取采集状态"""
        now = time.time()
        elapsed = now - self.fps_last_time
        if elapsed >= 1.0:
            self.current_fps = self.fps_frame_count / elapsed
            self.fps_frame_count = 0
            self.fps_last_time = now
        return {
            'running': self.running,
            'fps': round(self.current_fps, 1),
            'device': self.device_name
        }


# ==================== HLS 录制器（采集时使用） ====================

class HLSRecorder:
    """HLS 持续录制器 - 采集时以 HLS 分段录制，按标记保存精确片段"""

    def __init__(self, side, device_name, ffmpeg_path, temp_dir):
        self.side = side
        self.device_name = device_name
        self.ffmpeg_path = ffmpeg_path
        self.temp_dir = temp_dir / side
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self.process = None
        self.running = False
        self.current_segment = -1  # -1 确保第一个分片(segment_00000)能被追踪到
        self.mark_segment = None
        self.monitor_thread = None

    def start(self):
        """启动 HLS 录制"""
        if self.running:
            print(f'[HLSRecorder] [{self.side}] 已在运行中')
            return True

        # 清理旧临时文件
        for f in self.temp_dir.glob('*.ts'):
            f.unlink()
        for f in self.temp_dir.glob('*.m3u8'):
            f.unlink()

        clean_device_name = re.sub(r'\s*\([0-9a-fA-F:]+\)\s*$', '', self.device_name).strip()

        m3u8_path = self.temp_dir / 'stream.m3u8'
        segment_pattern = str(self.temp_dir / 'segment_%05d.ts')

        ffmpeg_cmd = [
            self.ffmpeg_path,
            '-f', 'dshow',
            '-video_size', '1280x720',
            '-framerate', '30',
            '-i', f'video={clean_device_name}',
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-tune', 'zerolatency',
            '-g', '30',
            '-f', 'hls',
            '-hls_time', '1',
            '-hls_list_size', '0',
            '-hls_flags', 'append_list',
            '-hls_segment_filename', segment_pattern,
            str(m3u8_path)
        ]

        print(f'[HLSRecorder] [{self.side}] 启动HLS录制: {clean_device_name}')

        try:
            self.process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )

            self.running = True
            print(f'[HLSRecorder] [{self.side}] ✅ HLS录制已启动, PID: {self.process.pid}')

            self.monitor_thread = threading.Thread(target=self._monitor_segments, daemon=True)
            self.monitor_thread.start()

            return True

        except Exception as e:
            print(f'[HLSRecorder] [{self.side}] ❌ 启动失败: {e}')
            import traceback
            traceback.print_exc()
            return False

    def _monitor_segments(self):
        """监控生成的分段"""
        while self.running and self.process and self.process.poll() is None:
            try:
                m3u8_path = self.temp_dir / 'stream.m3u8'
                if m3u8_path.exists():
                    with open(m3u8_path, 'r') as f:
                        content = f.read()
                        segments = re.findall(r'segment_(\d+)\.ts', content)
                        if segments:
                            latest = max([int(s) for s in segments])
                            if latest > self.current_segment:
                                self.current_segment = latest
                time.sleep(0.5)
            except Exception as e:
                print(f'[HLSRecorder] [{self.side}] 监控分段出错: {e}')
                time.sleep(1)

    def mark_start(self):
        """标记录制起始点"""
        self.mark_segment = self.current_segment
        print(f'[HLSRecorder] [{self.side}] 🎬 标记录制起始: 分段 {self.mark_segment}')
        return self.mark_segment

    def stop_and_save(self, output_path):
        """停止录制并保存标记片段到MP4"""
        if not self.running:
            return False

        end_segment_before = self.current_segment
        print(f'[HLSRecorder] [{self.side}] 停止前: mark={self.mark_segment}, current_segment={end_segment_before}')

        # 停止监控线程
        self.running = False

        # 停止 ffmpeg
        try:
            if self.process:
                # 发送 'q' 让 ffmpeg 优雅退出，写完最后的分片
                self.process.stdin.write(b'q')
                self.process.stdin.flush()
                self.process.wait(timeout=8)
                print(f'[HLSRecorder] [{self.side}] ffmpeg 已退出, exitcode={self.process.returncode}')
        except subprocess.TimeoutExpired:
            print(f'[HLSRecorder] [{self.side}] ffmpeg 超时，强制终止')
            if self.process:
                self.process.kill()
                self.process.wait(timeout=3)
        except Exception as e:
            print(f'[HLSRecorder] [{self.side}] 停止ffmpeg出错: {e}')
            if self.process:
                self.process.kill()

        # 等待文件系统刷新
        time.sleep(0.5)

        # 扫描实际存在的所有分段文件（不依赖 current_segment）
        all_segments = []
        for f in self.temp_dir.glob('segment_*.ts'):
            m = re.search(r'segment_(\d+)\.ts', f.name)
            if m:
                all_segments.append(int(m.group(1)))

        if all_segments:
            all_segments.sort()
            actual_start = all_segments[0]
            actual_end = all_segments[-1]
            print(f'[HLSRecorder] [{self.side}] 实际分段: {actual_start}~{actual_end} (共{len(all_segments)}个)')
            print(f'[HLSRecorder] [{self.side}] 文件列表: {all_segments}')
        else:
            print(f'[HLSRecorder] [{self.side}] ❌ 未找到任何分段文件！')
            print(f'[HLSRecorder] [{self.side}] 目录内容: {list(self.temp_dir.iterdir())}')
            return False

        # 用实际分段范围进行合并
        if self.mark_segment is not None and self.mark_segment >= actual_start:
            start_seg = self.mark_segment
        else:
            # 如果 mark 还没更新（current_segment=-1 的情况），从最早的分段开始
            print(f'[HLSRecorder] [{self.side}] ⚠️ mark_segment={self.mark_segment} 无效，使用最早分段 {actual_start}')
            start_seg = actual_start

        end_seg = actual_end
        print(f'[HLSRecorder] [{self.side}] 合并范围: {start_seg} -> {end_seg}')

        segment_files = []
        for i in range(start_seg, end_seg + 1):
            seg_path = self.temp_dir / f'segment_{i:05d}.ts'
            if seg_path.exists():
                segment_files.append(seg_path)

        if not segment_files:
            print(f'[HLSRecorder] [{self.side}] ❌ 没有找到分段文件')
            return False

        print(f'[HLSRecorder] [{self.side}] 找到 {len(segment_files)} 个分段待合并')

        concat_file = self.temp_dir / 'concat.txt'
        with open(concat_file, 'w') as f:
            for seg in segment_files:
                f.write(f"file '{seg.absolute()}'\n")

        merge_cmd = [
            self.ffmpeg_path,
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_file),
            '-c', 'copy',
            '-y',
            str(output_path)
        ]

        try:
            result = subprocess.run(
                merge_cmd,
                capture_output=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )

            if result.returncode == 0:
                file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
                print(f'[HLSRecorder] [{self.side}] ✅ MP4已保存: {output_path} ({file_size} bytes)')
                self._cleanup_temp_files()
                return True
            else:
                print(f'[HLSRecorder] [{self.side}] ❌ 合并失败:')
                print(result.stderr.decode('utf-8', errors='ignore'))
                self._cleanup_temp_files()
                return False

        except Exception as e:
            print(f'[HLSRecorder] [{self.side}] ❌ 合并出错: {e}')
            self._cleanup_temp_files()
            return False

    def _cleanup_temp_files(self):
        """清理临时文件"""
        try:
            for f in self.temp_dir.glob('*.ts'):
                f.unlink()
            for f in self.temp_dir.glob('*.m3u8'):
                f.unlink()
            for f in self.temp_dir.glob('*.txt'):
                f.unlink()
            print(f'[HLSRecorder] [{self.side}] 临时文件已清理')
        except Exception as e:
            print(f'[HLSRecorder] [{self.side}] 清理临时文件出错: {e}')

    def get_preview_frame_b64(self):
        """从HLS分段提取预览帧（base64编码）"""
        try:
            # 找最新分段
            latest_seg = self.temp_dir / f'segment_{self.current_segment:05d}.ts'
            if not latest_seg.exists():
                latest_seg = self.temp_dir / f'segment_{max(0, self.current_segment - 1):05d}.ts'
            if not latest_seg.exists():
                return None

            cmd = [
                self.ffmpeg_path,
                '-i', str(latest_seg),
                '-vframes', '1',
                '-f', 'image2pipe',
                '-vcodec', 'mjpeg',
                'pipe:1'
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )

            if result.returncode == 0 and result.stdout:
                return base64.b64encode(result.stdout).decode()
            return None

        except Exception as e:
            print(f'[HLSRecorder] [{self.side}] 提取预览帧失败: {e}')
            return None


# ==================== Camera Server 主类 ====================

class CameraServer:
    def __init__(self):
        self.cameras = {}           # {side: {device_name, device_id}}
        self.captures = {}          # {side: CameraCapture}   MJPEG实时采集
        self.recorders = {}         # {side: HLSRecorder}     HLS录制
        self.camera_opened = {'left': False, 'right': False}  # 追踪摄像头是否曾被打开

        self.output_dir = Path('storage/video')
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.temp_dir = Path('storage/video/temp')
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # 帧队列（跨线程通信）
        self.frame_queue = asyncio.Queue(maxsize=30)

        # 预览订阅者: {side: set(websocket)}
        self.preview_subscribers = {'left': set(), 'right': set()}
        self.subscribers_lock = threading.Lock()

        # 所有连接的客户端（用于广播状态）
        self.all_clients = set()
        self.clients_lock = threading.Lock()

        # 查找 ffmpeg
        self.ffmpeg_path = find_ffmpeg()
        if self.ffmpeg_path:
            print(f'[CameraServer] 使用 ffmpeg: {self.ffmpeg_path}')
        else:
            print('[CameraServer] ⚠️ 未找到 ffmpeg，视频录制功能将不可用')

        print('[CameraServer] 摄像头服务器初始化完成')
        print(f'[CameraServer] 视频输出目录: {self.output_dir.absolute()}')
        print('[CameraServer] 模式: MJPEG实时预览 + HLS录制')

    # ==================== 帧广播任务 ====================

    async def start_broadcast_task(self):
        """启动帧广播后台任务"""
        asyncio.create_task(self._broadcast_loop())
        asyncio.create_task(self._hls_preview_broadcast_loop())

    async def _broadcast_loop(self):
        """从队列取帧，广播给订阅的前端客户端"""
        while True:
            try:
                frame_data = await self.frame_queue.get()
                side = frame_data['side']
                frame_b64 = frame_data['frame']

                dead = set()
                # 复制订阅者集合避免迭代时修改
                subscribers = list(self.preview_subscribers.get(side, set()))
                for ws in subscribers:
                    try:
                        await ws.send(json.dumps({
                            'type': 'preview_frame',
                            'side': side,
                            'frame': frame_b64
                        }))
                    except websockets.exceptions.ConnectionClosed:
                        dead.add(ws)
                    except Exception as e:
                        print(f'[CameraServer] 发送帧给客户端失败: {e}')
                        dead.add(ws)

                # 清理断开的连接
                if dead:
                    with self.subscribers_lock:
                        self.preview_subscribers[side] -= dead

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f'[CameraServer] 广播帧出错: {e}')

    async def _hls_preview_broadcast_loop(self):
        """录制期间从HLS分段提取预览帧（每500ms）"""
        while True:
            await asyncio.sleep(0.5)
            for side in ['left', 'right']:
                if side in self.recorders and self.recorders[side].running:
                    subscribers = list(self.preview_subscribers.get(side, set()))
                    if not subscribers:
                        continue
                    recorder = self.recorders[side]
                    frame_b64 = recorder.get_preview_frame_b64()
                    if frame_b64:
                        dead = set()
                        for ws in subscribers:
                            try:
                                await ws.send(json.dumps({
                                    'type': 'preview_frame',
                                    'side': side,
                                    'frame': frame_b64
                                }))
                            except:
                                dead.add(ws)
                        if dead:
                            with self.subscribers_lock:
                                self.preview_subscribers[side] -= dead

    # ==================== 状态广播 ====================

    async def _broadcast_status_to_all(self, status_msg):
        """向所有连接的客户端广播状态消息"""
        dead = set()
        with self.clients_lock:
            clients = list(self.all_clients)
        for ws in clients:
            try:
                await ws.send(json.dumps(status_msg))
            except:
                dead.add(ws)
        if dead:
            with self.clients_lock:
                self.all_clients -= dead

    async def _push_recording_status(self):
        """推送当前录制状态给所有客户端"""
        recording_sides = [s for s in ['left', 'right'] if s in self.recorders and self.recorders[s].running]
        opened_sides = [s for s in ['left', 'right'] if self.camera_opened[s]]
        await self._broadcast_status_to_all({
            'type': 'recording_status',
            'recording': len(recording_sides) > 0,
            'recording_sides': recording_sides,
            'preview_available': opened_sides
        })

    # ==================== WebSocket 客户端处理 ====================

    async def handle_client(self, websocket):
        """处理客户端连接（前端或 realtimeEngine）"""
        client_addr = websocket.remote_address
        print(f'[CameraServer] 客户端已连接: {client_addr}')

        # 注册客户端
        with self.clients_lock:
            self.all_clients.add(websocket)

        # 发送初始状态
        await self._send_status(websocket)
        await self._push_recording_status()

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    command = data.get('command')
                    request_id = data.get('request_id')  # 用于客户端匹配响应
                    print(f'[CameraServer] 收到命令: {command} 来自 {client_addr}')

                    response = await self._dispatch(command, data, websocket)
                    if response is not None:
                        # 回传 request_id 以便客户端匹配
                        if request_id:
                            response['request_id'] = request_id
                        await websocket.send(json.dumps(response))

                except json.JSONDecodeError as e:
                    await websocket.send(json.dumps({
                        'success': False, 'error': f'JSON解析错误: {str(e)}'
                    }))
                except Exception as e:
                    print(f'[CameraServer] 处理消息时出错: {e}')
                    import traceback
                    traceback.print_exc()
                    await websocket.send(json.dumps({
                        'success': False, 'error': str(e)
                    }))

        except websockets.exceptions.ConnectionClosed:
            print(f'[CameraServer] 客户端断开连接: {client_addr}')
        except Exception as e:
            print(f'[CameraServer] 连接错误: {e}')

        # 清理：取消该客户端的所有预览订阅
        with self.subscribers_lock:
            for side in ['left', 'right']:
                self.preview_subscribers[side].discard(websocket)
        with self.clients_lock:
            self.all_clients.discard(websocket)
        print(f'[CameraServer] 客户端已清理: {client_addr}')

    async def _dispatch(self, command, data, websocket):
        """分发命令"""
        if command == 'list_cameras':
            return await self._cmd_list_cameras()
        elif command == 'set_camera':
            return self._cmd_set_camera(data)
        elif command == 'open_camera':
            return await self._cmd_open_camera(data, websocket)
        elif command == 'close_camera':
            return self._cmd_close_camera(data)
        elif command == 'subscribe_preview':
            return self._cmd_subscribe_preview(data, websocket)
        elif command == 'unsubscribe_preview':
            return self._cmd_unsubscribe_preview(data, websocket)
        elif command == 'start_continuous_recording':
            return await self._cmd_start_continuous_recording(data)
        elif command == 'mark_recording_start':
            return self._cmd_mark_recording_start(data)
        elif command == 'stop_and_save':
            return await self._cmd_stop_and_save(data)
        elif command == 'get_preview_frame':
            return self._cmd_get_preview_frame(data)
        elif command == 'get_status':
            return self._cmd_get_status()
        elif command == 'start_recording':
            # 兼容旧接口
            return self._cmd_mark_recording_start(data)
        elif command == 'stop_recording':
            # 兼容旧接口
            side = data.get('side')
            if side and side in self.recorders:
                recorder = self.recorders[side]
                timestamp = datetime.now().strftime('%y%m%d_%H%M%S')
                output_filename = f'recording_{side}_{timestamp}.mp4'
                return await self._do_stop_and_save(side, output_filename)
            return {'success': True, 'message': '未在录制中'}
        else:
            return {'success': False, 'error': f'未知命令: {command}'}

    # ==================== 命令处理 ====================

    async def _cmd_list_cameras(self):
        """枚举可用摄像头"""
        print('[CameraServer] 枚举摄像头设备...')

        if not self.ffmpeg_path:
            return {
                'success': False,
                'error': 'ffmpeg 未安装，无法枚举摄像头',
                'devices': []
            }

        ffmpeg_path = self.ffmpeg_path

        def _run():
            return subprocess.run(
                [ffmpeg_path, '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy'],
                capture_output=True,
                encoding='utf-8',
                errors='ignore',
                timeout=10
            )

        try:
            loop = asyncio.get_running_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _run),
                timeout=15
            )

            # ffmpeg 设备列表输出在 stderr
            output = result.stderr

            # 调试：打印原始输出（截断）
            output_preview = output[:2000] if len(output) > 2000 else output
            print(f'[CameraServer] ffmpeg 输出:\n{output_preview}')

            devices = []

            for line in output.split('\n'):
                # 新格式: [in#0 @ xxx] "设备名称" (video)
                match = re.search(r'\[in#\d+.*?\]\s*"([^"]+)"\s*\(video\)', line)
                if match:
                    device_name = match.group(1)
                    if 'Alternative name' not in line:
                        devices.append({
                            'name': device_name,
                            'id': device_name
                        })
                        print(f'[CameraServer]   [新格式] {device_name}')
                        continue

                # 旧格式: DirectShow video devices 段落中的 "设备名称"
                # 兼容没有 [in#...] 前缀的旧版 ffmpeg
                alt_match = re.search(r'^\s*"([^"]+)"\s*\(video\)', line)
                if alt_match:
                    device_name = alt_match.group(1)
                    if 'Alternative name' not in line and device_name not in {d['name'] for d in devices}:
                        devices.append({
                            'name': device_name,
                            'id': device_name
                        })
                        print(f'[CameraServer]   [旧格式] {device_name}')

            print(f'[CameraServer] 找到 {len(devices)} 个摄像头设备')
            return {
                'success': True,
                'devices': devices
            }

        except asyncio.TimeoutError:
            print('[CameraServer] 枚举设备超时')
            return {
                'success': False,
                'error': '枚举设备超时（15秒）',
                'devices': []
            }
        except Exception as e:
            print(f'[CameraServer] 枚举设备失败: {e}')
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'error': str(e),
                'devices': []
            }

    def _cmd_set_camera(self, data):
        """设置摄像头配置（不启动采集，只保存配置）"""
        side = data.get('side')
        device_name = data.get('device_name')
        device_id = data.get('device_id')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        self.cameras[side] = {
            'device_name': device_name,
            'device_id': device_id or device_name
        }

        print(f'[CameraServer] 摄像头配置已保存: {side} -> {device_name}')
        return {
            'success': True,
            'side': side,
            'device_name': device_name,
            'message': f'{side}侧摄像头配置已保存'
        }

    async def _cmd_open_camera(self, data, websocket):
        """打开摄像头，开始MJPEG采集并推送预览"""
        side = data.get('side')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        if side not in self.cameras:
            return {'success': False, 'error': f'{side}侧摄像头未配置，请先调用set_camera'}

        if not self.ffmpeg_path:
            return {'success': False, 'error': 'ffmpeg未安装'}

        # 如果已打开，先关闭
        if side in self.captures and self.captures[side].running:
            print(f'[CameraServer] [{side}] 摄像头已打开，先关闭再重新打开')
            self.captures[side].stop()
            await asyncio.sleep(0.3)

        # 如果正在HLS录制，不能打开MJPEG（摄像头只能被一个进程占用）
        if side in self.recorders and self.recorders[side].running:
            return {'success': False, 'error': f'{side}侧正在HLS录制中，不能同时打开预览'}

        camera = self.cameras[side]
        capture = CameraCapture(side, camera['device_name'], self.ffmpeg_path, self.frame_queue)
        success = capture.start()

        if success:
            self.captures[side] = capture
            self.camera_opened[side] = True  # 记录已打开状态
            # 自动订阅该客户端的预览
            with self.subscribers_lock:
                self.preview_subscribers[side].add(websocket)
            return {
                'success': True,
                'side': side,
                'message': f'{side}侧摄像头已打开，预览已启动'
            }
        else:
            return {'success': False, 'error': f'{side}侧摄像头打开失败'}

    def _cmd_close_camera(self, data):
        """关闭摄像头，停止MJPEG采集"""
        side = data.get('side')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        if side in self.captures:
            self.captures[side].stop()
            del self.captures[side]
            print(f'[CameraServer] [{side}] 摄像头已关闭')

        self.camera_opened[side] = False

        # 清理预览订阅
        with self.subscribers_lock:
            self.preview_subscribers[side].clear()

        return {'success': True, 'side': side, 'message': f'{side}侧摄像头已关闭'}

    def _cmd_subscribe_preview(self, data, websocket):
        """订阅预览帧推送"""
        side = data.get('side')
        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        with self.subscribers_lock:
            self.preview_subscribers[side].add(websocket)

        print(f'[CameraServer] 客户端订阅{side}侧预览 (总订阅数: {len(self.preview_subscribers[side])})')
        return {'success': True, 'side': side, 'subscribed': True}

    def _cmd_unsubscribe_preview(self, data, websocket):
        """取消预览帧订阅"""
        side = data.get('side')
        if side:
            with self.subscribers_lock:
                self.preview_subscribers[side].discard(websocket)
        else:
            with self.subscribers_lock:
                for s in ['left', 'right']:
                    self.preview_subscribers[s].discard(websocket)

        return {'success': True, 'subscribed': False}

    async def _cmd_start_continuous_recording(self, data):
        """启动HLS持续录制（由realtimeEngine在采集开始时调用）"""
        side = data.get('side')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        if side not in self.cameras:
            return {'success': False, 'error': f'{side}侧摄像头未配置'}

        if not self.ffmpeg_path:
            return {'success': False, 'error': 'ffmpeg未安装'}

        # 先停止MJPEG采集（释放摄像头给HLS）
        if side in self.captures and self.captures[side].running:
            print(f'[CameraServer] [{side}] 停止MJPEG预览，切换到HLS录制...')
            self.captures[side].stop()
            del self.captures[side]

        camera = self.cameras[side]
        recorder = HLSRecorder(side, camera['device_name'], self.ffmpeg_path, self.temp_dir)
        success = recorder.start()

        if success:
            self.recorders[side] = recorder
            # 推送录制状态给所有前端客户端
            await self._push_recording_status()
            return {'success': True, 'side': side, 'message': f'{side}侧HLS录制已启动'}
        else:
            return {'success': False, 'error': f'{side}侧HLS录制启动失败'}

    def _cmd_mark_recording_start(self, data):
        """标记录制起始点（按空格键时由realtimeEngine调用）"""
        side = data.get('side')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        if side not in self.recorders:
            return {'success': False, 'error': f'{side}侧HLS录制未启动'}

        recorder = self.recorders[side]
        mark_segment = recorder.mark_start()

        return {
            'success': True,
            'side': side,
            'mark_segment': mark_segment
        }

    async def _cmd_stop_and_save(self, data):
        """停止HLS录制并保存MP4"""
        side = data.get('side')
        output_filename = data.get('output_filename')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        return await self._do_stop_and_save(side, output_filename)

    async def _do_stop_and_save(self, side, output_filename):
        """执行停止和保存逻辑"""
        if side not in self.recorders:
            return {'success': False, 'error': f'{side}侧HLS录制未启动'}

        output_path = self.output_dir / output_filename
        recorder = self.recorders[side]

        # stop_and_save 包含 time.sleep 和 subprocess.run，放到 executor 避免阻塞事件循环
        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(None, recorder.stop_and_save, output_path)

        if success:
            del self.recorders[side]
            result = {
                'success': True,
                'side': side,
                'output_path': str(output_path),
                'filename': output_filename
            }
        else:
            del self.recorders[side]
            result = {
                'success': False,
                'error': '保存MP4失败'
            }

        # 推送录制结束状态
        await self._push_recording_status()

        # 录制完成后，如果摄像头之前被打开过，自动恢复MJPEG预览
        if side in self.cameras and self.ffmpeg_path and self.camera_opened.get(side):
            print(f'[CameraServer] [{side}] 录制完成，恢复MJPEG预览...')
            await asyncio.sleep(0.5)  # 等待摄像头完全释放
            camera = self.cameras[side]
            capture = CameraCapture(side, camera['device_name'], self.ffmpeg_path, self.frame_queue)
            if capture.start():
                self.captures[side] = capture
                print(f'[CameraServer] [{side}] ✅ MJPEG预览已恢复')
            else:
                print(f'[CameraServer] [{side}] ⚠️ MJPEG预览恢复失败')

        return result

    def _cmd_get_preview_frame(self, data):
        """获取单帧预览（手动请求）"""
        side = data.get('side')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        # 如果有MJPEG采集，返回最新帧
        if side in self.captures and self.captures[side].latest_frame_b64:
            return {
                'success': True,
                'side': side,
                'frame': self.captures[side].latest_frame_b64
            }

        # 如果有HLS录制，从分段提取
        if side in self.recorders and self.recorders[side].running:
            frame_b64 = self.recorders[side].get_preview_frame_b64()
            if frame_b64:
                return {
                    'success': True,
                    'side': side,
                    'frame': frame_b64
                }

        return {
            'success': False,
            'error': f'{side}侧无可用预览帧'
        }

    def _cmd_get_status(self):
        """获取服务器状态"""
        status = {
            'success': True,
            'cameras': self.cameras,
            'captures': {
                side: cap.get_status() if cap else None
                for side, cap in self.captures.items()
            },
            'recording': {
                side: rec.running if rec else False
                for side, rec in self.recorders.items()
            },
            'preview_subscribers': {
                side: len(subs)
                for side, subs in self.preview_subscribers.items()
            }
        }
        return status

    async def _send_status(self, websocket):
        """向新客户端发送当前状态"""
        try:
            status = self._cmd_get_status()
            status['type'] = 'status'
            await websocket.send(json.dumps(status))
        except:
            pass

    # ==================== 清理 ====================

    async def cleanup(self):
        """清理资源"""
        print('[CameraServer] 正在清理资源...')

        # 停止所有MJPEG采集
        for side, capture in list(self.captures.items()):
            capture.stop()
        self.captures.clear()

        # 停止所有HLS录制
        for side, recorder in list(self.recorders.items()):
            if recorder.running:
                try:
                    recorder.process.kill()
                    recorder.running = False
                except:
                    pass
        self.recorders.clear()

        print('[CameraServer] 清理完成')


# ==================== 入口 ====================

async def main():
    camera_server = CameraServer()

    # 启动帧广播任务
    await camera_server.start_broadcast_task()

    # 启动 WebSocket 服务器
    port = 8768
    server = await websockets.serve(camera_server.handle_client, 'localhost', port)

    print(f'╔══════════════════════════════════════════════════════════╗')
    print(f'║  Camera Server 已启动                                    ║')
    print(f'║  WebSocket: ws://localhost:{port}                        ║')
    print(f'║  客户端: 前端 camera_control.js + realtimeEngine.js      ║')
    print(f'╚══════════════════════════════════════════════════════════╝')

    # 处理退出信号
    def signal_handler(sig, frame):
        print('\n[CameraServer] 收到退出信号，正在关闭...')
        asyncio.create_task(camera_server.cleanup())
        server.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 保持运行
    await asyncio.Future()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\n[CameraServer] 服务器已停止')
