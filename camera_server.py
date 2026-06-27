"""
camera_server.py - USB摄像头管理服务器

功能：
1. 枚举USB摄像头设备（通过ffmpeg dshow）
2. 实时MJPEG预览推流（WebSocket推送帧给前端，1920x1080@30fps）
3. 帧录制（FrameRecorder：从MJPEG管道直接保存帧，预览不中断）
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
    """查找 ffmpeg 可执行文件（优先 PATH，回退常见安装位置）"""
    ffmpeg_path = shutil.which('ffmpeg')
    if ffmpeg_path:
        print(f'[CameraServer] 找到 ffmpeg (PATH): {ffmpeg_path}')
        return ffmpeg_path

    if sys.platform == 'win32':
        # 使用 %LOCALAPPDATA% 环境变量（用户名无关），而非 Path.home()
        local_appdata = os.environ.get('LOCALAPPDATA', '')
        search_roots = []

        # 1. WinGet 安装路径（通过 %LOCALAPPDATA% 环境变量）
        if local_appdata:
            search_roots.append(Path(local_appdata) / 'Microsoft/WinGet/Packages')

        # 2. 常见手动安装位置（用户名无关）
        search_roots.extend([
            Path('C:/ffmpeg/bin'),
            Path('C:/Program Files/ffmpeg/bin'),
            Path('C:/tools/ffmpeg/bin'),
        ])

        # 3. 回退：Path.home() (最后手段，用户名相关)
        search_roots.append(Path.home() / 'AppData/Local/Microsoft/WinGet/Packages')

        win_pkg_names = ['Gyan.FFmpeg', 'Gyan.FFmpeg.Essentials', 'Gyan.FFmpeg.Shared']

        for root in search_roots:
            root_str = str(root)
            # 如果 root 包含 WinGet/Packages，尝试 glob 匹配包名
            if 'WinGet' in root_str and 'Packages' in root_str:
                for pkg in win_pkg_names:
                    pattern = str(root / f'{pkg}*' / 'ffmpeg-*' / 'bin' / 'ffmpeg.exe')
                    matches = glob.glob(pattern)
                    if matches:
                        print(f'[CameraServer] 找到 WinGet 安装的 ffmpeg: {matches[0]}')
                        return matches[0]
            else:
                # 直接检查 ffmpeg.exe 是否在该目录
                exe_path = root / 'ffmpeg.exe'
                if exe_path.exists():
                    print(f'[CameraServer] 找到 ffmpeg (固定路径): {exe_path}')
                    return str(exe_path)

    return None


# ==================== MJPEG 实时采集器 ====================

class CameraCapture:
    """实时摄像头采集器 - 通过 ffmpeg MJPEG pipe 抓取帧并推送给订阅者"""

    def __init__(self, side, device_name, ffmpeg_path, frame_queue, alt_name=None):
        self.side = side
        self.device_name = device_name
        self.alt_name = alt_name  # 备用设备路径（@device_pnp_...），用于 fallback
        self.ffmpeg_path = ffmpeg_path
        self.frame_queue = frame_queue  # asyncio.Queue，用于跨线程传递帧
        self.process = None
        self.running = False
        self.reader_thread = None
        self.latest_frame_b64 = None
        self.fps_frame_count = 0
        self.fps_last_time = time.time()
        self.current_fps = 0
        self.frame_recorder = None  # FrameRecorder instance (set when recording)
        self._stdout_fd = None      # raw pipe fd for unbuffered reads
        self._startup_time = None   # 启动时间，用于控制诊断日志窗口
        self._last_device_label = None
        self._last_capture_mode = None

    def _build_ffmpeg_args(self, device_id, capture_mode='auto'):
        """构建 ffmpeg dshow 采集命令参数

        capture_mode:
          'auto'   — 不指定 video_size/framerate，让 DShow 自动协商
          '1080p30' — 显式指定 1920x1080@30fps
          'yuy2'   — 指定 YUY2 像素格式 + 1080p30
        """
        base = [
            self.ffmpeg_path,
            '-fflags', 'nobuffer',
            '-flags', 'low_delay',
            '-rtbufsize', '2M',
            '-f', 'dshow',
        ]
        if capture_mode == 'auto':
            # 不指定 video_size/framerate，DShow 自动协商
            pass
        elif capture_mode == 'yuy2':
            base += ['-pixel_format', 'yuyv422',
                     '-video_size', '1920x1080',
                     '-framerate', '30']
        else:  # 1080p30
            base += ['-video_size', '1920x1080',
                     '-framerate', '30']
        base += [
            '-i', f'video={device_id}',
            '-vcodec', 'mjpeg',
            '-q:v', '8',
            '-f', 'image2pipe',
            '-flush_packets', '1',
            'pipe:1'
        ]
        return base

    def _try_open_camera(self, device_id, label, capture_mode='1080p30'):
        """尝试用指定设备ID打开摄像头，返回 (success, process)"""
        cmd = self._build_ffmpeg_args(device_id, capture_mode)
        print(f'[CameraCapture] [{self.side}] 尝试打开: {label} (模式={capture_mode}) ...')
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            return True, proc
        except Exception as e:
            print(f'[CameraCapture] [{self.side}] ❌ 启动失败 ({label}): {e}')
            return False, None

    def start(self):
        """启动 MJPEG 采集（支持重试 + Alternative name fallback）"""
        if self.running:
            print(f'[CameraCapture] [{self.side}] 已在运行中')
            return True

        self._startup_time = time.time()

        # 候选设备ID列表：优先友好名称（最可靠），
        # 后备 @device_pnp 路径（同名设备时用于区分；某些摄像头不支持 @device_pnp）
        friendly_name = re.sub(r'\s*\([0-9a-fA-F:]+\)\s*$', '', self.device_name).strip()
        device_candidates = [('友好名称', friendly_name)]
        if self.alt_name:
            device_candidates.append(('设备路径', self.alt_name))

        # 采集模式候选：从最宽松到最严格
        # - 'auto': 不指定参数，DShow 自动协商（RealSense 等需要）
        # - '1080p30': 显式 1920x1080@30fps（大多数 USB 摄像头）
        # - 'yuy2': YUY2 像素格式 + 1080p30
        capture_modes = ['auto', '1080p30', 'yuy2']

        # 尝试打开摄像头（只重试 1 次，因为已有 6 种候选组合）
        for attempt in range(1):
            for label, device_id in device_candidates:
                for mode in capture_modes:
                    ok, proc = self._try_open_camera(device_id, label, mode)
                    if not ok:
                        continue

                    self.process = proc
                    self._stdout_fd = self.process.stdout.fileno()
                    self._last_device_label = label
                    self._last_capture_mode = mode

                    # 两阶段启动检查（ffmpeg 先打印版本信息 ~100ms，
                    # 然后才进行 DShow 协商，单次 100ms poll 会漏过延迟失败）
                    # 阶段1：100ms 后检查是否立即崩溃
                    time.sleep(0.1)
                    if self.process.poll() is not None:
                        # ffmpeg 进程已退出，读取 stderr 诊断信息
                        stderr_output = ''
                        try:
                            stderr_output = self.process.stderr.read().decode('utf-8', errors='ignore')
                        except Exception:
                            pass
                        exit_code = self.process.returncode
                        print(f'[CameraCapture] [{self.side}] ❌ ffmpeg 立即退出 (exit={exit_code}) '
                              f'标签={label}, 模式={mode}, 设备={device_id[:40]}')
                        if stderr_output:
                            for err_line in stderr_output.strip().split('\n'):
                                if err_line.strip():
                                    print(f'[CameraCapture] [{self.side}]   stderr: {err_line.strip()[:200]}')
                        self.process = None
                        self._stdout_fd = None
                        continue  # 尝试下一个模式

                    # 阶段2：再等 400ms（DShow 协商），每 100ms 轮询
                    startup_ok = True
                    for _ in range(4):
                        time.sleep(0.1)
                        if self.process.poll() is not None:
                            # DShow 协商阶段失败
                            stderr_output = ''
                            try:
                                stderr_output = self.process.stderr.read().decode('utf-8', errors='ignore')
                            except Exception:
                                pass
                            exit_code = self.process.returncode
                            print(f'[CameraCapture] [{self.side}] ❌ ffmpeg 启动后崩溃 (exit={exit_code}) '
                                  f'标签={label}, 模式={mode}, 设备={device_id[:40]}')
                            if stderr_output:
                                for err_line in stderr_output.strip().split('\n'):
                                    if err_line.strip():
                                        print(f'[CameraCapture] [{self.side}]   stderr: {err_line.strip()[:200]}')
                            self.process = None
                            self._stdout_fd = None
                            startup_ok = False
                            break
                    if not startup_ok:
                        continue  # 尝试下一个模式

                    # 阶段3：进程存活 500ms+，启动读取线程，等待首帧
                    # （有些情况如 @device_pnp 不可用时，ffmpeg 挂死但不退出）
                    self.running = True
                    self.reader_thread = threading.Thread(target=self._read_frames, daemon=True)
                    self.reader_thread.start()
                    stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
                    stderr_thread.start()

                    first_frame_deadline = time.time() + 2.0
                    frame_arrived = False
                    while time.time() < first_frame_deadline:
                        if self.latest_frame_b64 is not None:
                            frame_arrived = True
                            break
                        if self.process.poll() is not None:
                            # ffmpeg 在等待首帧期间退出（延迟崩溃）
                            self.running = False
                            stderr_output = ''
                            try:
                                stderr_output = self.process.stderr.read().decode('utf-8', errors='ignore')
                            except Exception:
                                pass
                            print(f'[CameraCapture] [{self.side}] ❌ ffmpeg 等待首帧期间退出 '
                                  f'(exit={self.process.returncode}) '
                                  f'标签={label}, 模式={mode}')
                            if stderr_output:
                                for err_line in stderr_output.strip().split('\n'):
                                    if err_line.strip():
                                        print(f'[CameraCapture] [{self.side}]   stderr: {err_line.strip()[:200]}')
                            self.process = None
                            self._stdout_fd = None
                            break
                        time.sleep(0.1)

                    if frame_arrived:
                        print(f'[CameraCapture] [{self.side}] ✅ MJPEG采集已启动 '
                              f'(标签={label}, 模式={mode}, PID={self.process.pid})')
                        return True

                    # 首帧超时或进程崩溃，清理并尝试下一个候选
                    if self.latest_frame_b64 is None:
                        if self.process and self.process.poll() is None:
                            print(f'[CameraCapture] [{self.side}] ❌ 首帧超时(2s)，'
                                  f'ffmpeg 进程存活但无帧输出 (DShow 挂死，'
                                  f'标签={label}, 模式={mode})')
                        self.running = False
                        if self.process and self.process.poll() is None:
                            try:
                                self.process.terminate()
                                self.process.wait(timeout=3)
                            except Exception:
                                try:
                                    self.process.kill()
                                except Exception:
                                    pass
                        self.process = None
                        self._stdout_fd = None
                        # 继续尝试下一个模式
                        continue
                # 该设备的所有模式都失败，继续尝试下一个设备
                pass
        # 所有设备候选都失败
        print(f'[CameraCapture] [{self.side}] ❌ 所有设备/模式候选均失败')
        return False

    def _read_frames(self):
        """读取 MJPEG 帧（在单独线程中运行，使用无缓冲 os.read）"""
        buf = b''
        fd = self._stdout_fd
        while self.running and self.process and self.process.poll() is None:
            try:
                # 使用 os.read 无缓冲直接读管道，避免 Python BufferedReader 延迟
                data = os.read(fd, 65536) if fd else self.process.stdout.read(4096)
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

                    # 缓存最新帧（供 snapshots / get_preview_frame 按需取用）
                    self.latest_frame_b64 = b64

                    # 写入录制器（如果正在录制）
                    # 先捕获引用避免 TOCTOU 竞态
                    recorder = self.frame_recorder
                    if recorder:
                        try:
                            recorder.write_frame(frame)
                        except OSError as e:
                            # 磁盘满或写入错误，记录一次后禁用录制器避免日志风暴
                            print(f'[CameraCapture] [{self.side}] ⚠️ 写入帧失败 (磁盘满?): {e}')
                            self.frame_recorder = None
                        except Exception as e:
                            print(f'[CameraCapture] [{self.side}] ⚠️ 写入帧异常: {e}')
                            self.frame_recorder = None

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
        """读取 ffmpeg stderr（避免管道阻塞），启动阶段打印全部诊断行"""
        try:
            startup_deadline = (self._startup_time or time.time()) + 3.0  # 前3s全量输出
            while self.running and self.process and self.process.poll() is None:
                line = self.process.stderr.readline()
                if not line:
                    break
                line_str = line.decode('utf-8', errors='ignore').strip()
                if not line_str:
                    continue
                # 启动诊断窗口内全量输出；稳定运行后只输出异常行
                in_startup = time.time() < startup_deadline
                if in_startup or 'error' in line_str.lower() or 'cannot' in line_str.lower() \
                        or 'fail' in line_str.lower() or 'unable' in line_str.lower() \
                        or 'invalid' in line_str.lower():
                    print(f'[CameraCapture] [{self.side}] ffmpeg: {line_str}')
        except Exception:
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


# ==================== 帧录制器（采集时使用，复用MJPEG管道） ====================

class FrameRecorder:
    """帧录制器 — 从 MJPEG 预览管道直接保存帧，无需单独的 ffmpeg 进程

    核心思路：预览用 1920x1080@30fps（保证摄像头兼容性），
    录制时直接从预览管道保存 MJPEG 帧（1920x1080），停止时 ffmpeg 封装为 AVI（保持原始分辨率和帧率）。
    预览在录制期间持续可用，不存在 dshow 设备独占冲突。
    """

    def __init__(self, side, ffmpeg_path, output_dir):
        self.side = side
        self.ffmpeg_path = ffmpeg_path
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_file = None
        self.raw_path = None
        self.output_path = None
        self.recording = False
        self.recording_started_at = None
        self.recording_stopped_at = None
        self.frame_count = 0
        self.first_frame_real_time = None   # 第一帧实际到达的 wall-clock 时间
        self.last_frame_real_time = None    # 最后一帧实际到达的 wall-clock 时间（每帧更新）

    def start(self, output_filename, start_timestamp=None):
        """开始录制 — 打开原始 MJPEG 文件

        Args:
            output_filename: 输出文件名
            start_timestamp: 可选，前端传入的统一时间戳（Unix秒）。
                             如果提供，优先使用；否则使用本地 time.time()。
        """
        if self.recording:
            print(f'[FrameRecorder] [{self.side}] 已在录制中')
            return True

        # 路径遍历防护：拒绝包含 ../ 或绝对路径的文件名
        safe_name = os.path.basename(output_filename)
        if safe_name != output_filename or '..' in output_filename:
            print(f'[FrameRecorder] [{self.side}] ⚠️ 拒绝可疑文件名: {output_filename}')
            return False

        self.output_path = self.output_dir / safe_name
        self.raw_path = self.output_path.with_suffix('.mjpeg')
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self.raw_file = open(self.raw_path, 'wb')
        except Exception as e:
            print(f'[FrameRecorder] [{self.side}] \u274C 无法创建文件: {e}')
            return False

        self.recording = True
        # 优先使用传入的统一时间戳，保证与 EMG 时间基准一致
        if start_timestamp is not None:
            self.recording_started_at = float(start_timestamp)
        else:
            self.recording_started_at = time.time()
        self.frame_count = 0
        print(f'[FrameRecorder] [{self.side}] \u25B6 开始录制: {self.output_path}')
        print(f'[FrameRecorder] [{self.side}]   原始MJPEG: {self.raw_path}')
        return True

    def write_frame(self, frame_bytes):
        """写入一帧 MJPEG 数据（由 CameraCapture._read_frames 线程调用）"""
        if self.recording and self.raw_file:
            try:
                # 记录第一帧和最后一帧的实际到达时间（消除摄像头启动延迟 + 帧率偏差）
                now = time.time()
                if self.first_frame_real_time is None:
                    self.first_frame_real_time = now
                self.last_frame_real_time = now  # 每帧更新，停止时即为最后一帧的真实时间
                self.raw_file.write(frame_bytes)
                self.frame_count += 1
            except Exception as e:
                print(f'[FrameRecorder] [{self.side}] 写入帧失败: {e}')

    def stop_and_save(self):
        """停止录制，将 MJPEG → AVI 转换"""
        if not self.recording:
            return {'success': False, 'error': '录制器未在运行'}

        self.recording = False
        self.recording_stopped_at = time.time()

        if self.raw_file:
            self.raw_file.close()
            self.raw_file = None

        if not self.raw_path or not self.raw_path.exists():
            return {'success': False, 'error': f'MJPEG文件未生成: {self.raw_path}'}

        raw_size = os.path.getsize(self.raw_path)
        print(f'[FrameRecorder] [{self.side}] raw MJPEG: {self.raw_path} '
              f'({raw_size} bytes, {self.frame_count} frames, '
              f'{self.recording_stopped_at - self.recording_started_at:.1f}s elapsed)')

        # 用 ffmpeg 将 MJPEG 流封装为 AVI（保持原始 1920x1080@30fps）
        try:
            result = subprocess.run([
                self.ffmpeg_path,
                '-f', 'mjpeg',
                '-i', str(self.raw_path),
                '-r', '30',
                '-c:v', 'mjpeg',
                '-q:v', '12',
                '-y',
                str(self.output_path)
            ], capture_output=True, text=True, timeout=60,
               creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)

            if not self.output_path.exists():
                stderr_tail = result.stderr[-500:] if result.stderr else '(none)'
                print(f'[FrameRecorder] [{self.side}] \u274C ffmpeg 封装失败')
                print(f'[FrameRecorder] [{self.side}]   stderr: {stderr_tail}')
                return {'success': False, 'error': f'AVI封装失败: {stderr_tail[:200]}'}

            avi_size = os.path.getsize(self.output_path)
            print(f'[FrameRecorder] [{self.side}] \u2705 AVI已封装: {self.output_path} '
                  f'({avi_size} bytes, {avi_size/(1024*1024):.1f} MB)')

            # 清理原始 MJPEG 文件
            try:
                os.remove(str(self.raw_path))
                print(f'[FrameRecorder] [{self.side}]   已清理 raw MJPEG')
            except Exception as e:
                print(f'[FrameRecorder] [{self.side}]   清理 raw MJPEG 失败: {e}')

            # 提取时间戳
            timing = self._extract_timing(self.output_path)

            return {
                'success': True,
                'path': str(self.output_path),
                'size': avi_size,
                'frame_count': self.frame_count,
                'timing': timing
            }

        except Exception as e:
            print(f'[FrameRecorder] [{self.side}] \u274C 封装异常: {e}')
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': str(e)}

    def _extract_timing(self, avi_path):
        """提取视频时间戳

        优先使用 Python 端记录的 wall-clock 时间戳作为可靠基准。
        ffprobe 仅在可用时提供更精确的 PTS 修正值。
        """
        rec_start = self.recording_started_at or 0
        rec_stop = self.recording_stopped_at or 0
        # 优先使用实际帧到达时间（而非命令/停止时间），消除摄像头启动延迟 + 帧率偏差
        first_frame_time = self.first_frame_real_time or rec_start
        last_frame_time = self.last_frame_real_time or rec_stop
        wall_duration = max(0, last_frame_time - first_frame_time)

        timing = {
            'recording_started_at': rec_start,
            'recording_stopped_at': rec_stop,
            'duration': round(wall_duration, 3),
            'first_pts': 0,
            'first_frame_real_time': round(first_frame_time, 3) if self.first_frame_real_time else None,
            'last_frame_real_time': round(last_frame_time, 3) if self.last_frame_real_time else None,
            'first_frame_unix': round(first_frame_time, 3),
            'last_frame_unix': round(last_frame_time, 3),
            'frame_count': self.frame_count,
            'timing_source': 'wall_clock',
        }

        # 尝试用 ffprobe 获取更精确的 PTS 时间戳
        ffprobe_path = self._find_ffprobe()
        if ffprobe_path:
            try:
                timing = self._refine_timing_with_ffprobe(
                    timing, ffprobe_path, avi_path)
            except Exception as e:
                print(f'[FrameRecorder] [{self.side}] ffprobe 精化时间戳失败: {e}')
                import traceback
                traceback.print_exc()
        else:
            print(f'[FrameRecorder] [{self.side}] ⚠️ ffprobe 未找到，'
                  f'使用 wall-clock 时间戳 (duration={wall_duration:.1f}s)')

        # 写出 .timing.json
        timing_path = avi_path.with_suffix(avi_path.suffix + '.timing.json')
        try:
            with open(timing_path, 'w', encoding='utf-8') as f:
                json.dump(timing, f, indent=2, ensure_ascii=False)
            timing['sidecar'] = str(timing_path)
        except Exception as e:
            print(f'[FrameRecorder] [{self.side}] 写入时间戳文件失败: {e}')

        return timing

    def _find_ffprobe(self):
        """查找 ffprobe 可执行文件"""
        if not self.ffmpeg_path:
            return None
        # 从 ffmpeg_path 推导
        if sys.platform == 'win32':
            ffprobe_path = self.ffmpeg_path.replace('ffmpeg.exe', 'ffprobe.exe')
        else:
            ffprobe_path = self.ffmpeg_path.replace('ffmpeg', 'ffprobe')
        if os.path.exists(ffprobe_path):
            return ffprobe_path

        # PATH 搜索
        alt = shutil.which('ffprobe')
        if alt:
            return alt

        # 与 find_ffmpeg() 一致的备用路径搜索
        if sys.platform == 'win32':
            local_appdata = os.environ.get('LOCALAPPDATA', '')
            search_roots = []
            if local_appdata:
                search_roots.append(
                    Path(local_appdata) / 'Microsoft/WinGet/Packages')
            search_roots.extend([
                Path('C:/ffmpeg/bin'),
                Path('C:/Program Files/ffmpeg/bin'),
                Path('C:/tools/ffmpeg/bin'),
            ])
            search_roots.append(
                Path.home() / 'AppData/Local/Microsoft/WinGet/Packages')

            win_pkg_names = ['Gyan.FFmpeg', 'Gyan.FFmpeg.Essentials',
                             'Gyan.FFmpeg.Shared']
            for root in search_roots:
                root_str = str(root)
                if 'WinGet' in root_str and 'Packages' in root_str:
                    for pkg in win_pkg_names:
                        pattern = str(
                            root / f'{pkg}*' / 'ffmpeg-*' / 'bin' / 'ffprobe.exe')
                        matches = glob.glob(pattern)
                        if matches:
                            return matches[0]
                else:
                    exe_path = root / 'ffprobe.exe'
                    if exe_path.exists():
                        return str(exe_path)

        return None

    def _refine_timing_with_ffprobe(self, timing, ffprobe_path, avi_path):
        """用 ffprobe 精化时间戳（PTS 级别的精度）"""
        cflags = (subprocess.CREATE_NO_WINDOW
                  if sys.platform == 'win32' else 0)

        # 获取视频时长（容器级别）
        result = subprocess.run(
            [ffprobe_path,
             '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1',
             str(avi_path)],
            capture_output=True, text=True, timeout=10,
            creationflags=cflags
        )
        ffprobe_duration = None
        if result.returncode == 0 and result.stdout.strip():
            try:
                ffprobe_duration = float(result.stdout.strip())
            except ValueError:
                pass

        # 获取第一帧 PTS
        result2 = subprocess.run(
            [ffprobe_path,
             '-v', 'error', '-show_entries', 'packet=pts_time',
             '-of', 'default=noprint_wrappers=1:nokey=1',
             '-read_intervals', '%+#1',
             str(avi_path)],
            capture_output=True, text=True, timeout=10,
            creationflags=cflags
        )
        first_pts = 0.0
        if result2.returncode == 0 and result2.stdout.strip():
            try:
                first_pts = float(result2.stdout.strip().split('\n')[0])
            except ValueError:
                pass

        # 只有当 ffprobe 返回了有效 duration (>0) 时才用 PTS 元数据补充
        # 关键：不覆盖 first_frame_unix / last_frame_unix！
        # 这两个必须保持 wall-clock 值（来自真实帧到达时间），
        # 否则 effective_fps 会被锁死在容器帧率 (30fps)，导致视频-EMG 漂移。
        if ffprobe_duration and ffprobe_duration > 0:
            timing['duration'] = round(ffprobe_duration, 3)
            timing['first_pts'] = round(first_pts, 3)
            timing['timing_source'] = 'ffprobe'
            print(f'[FrameRecorder] [{self.side}] ffprobe 精化: '
                  f'container_dur={ffprobe_duration:.3f}s, first_pts={first_pts:.3f}s '
                  f'(wall_clock first={timing["first_frame_unix"]:.3f}, '
                  f'last={timing["last_frame_unix"]:.3f}, '
                  f'wall_dur={timing["last_frame_unix"] - timing["first_frame_unix"]:.3f}s)')
        else:
            print(f'[FrameRecorder] [{self.side}] ffprobe 未返回有效 duration '
                  f'(dur={ffprobe_duration}, pts={first_pts}), '
                  f'保持 wall_clock 时间戳')

        return timing



# ==================== Camera Server 主类 ====================

class CameraServer:
    def __init__(self):
        self.cameras = {}           # {side: {device_name, device_id}}
        self.captures = {}          # {side: CameraCapture}   MJPEG实时采集
        self.recorders = {}         # {side: FrameRecorder}   帧录制（复用MJPEG管道）
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
        print('[CameraServer] 模式: MJPEG实时预览 + 帧录制 (预览不中断)')

    # ==================== 帧广播任务 ====================

    async def start_broadcast_task(self):
        """启动帧广播后台任务"""
        asyncio.create_task(self._broadcast_loop())

    async def _broadcast_loop(self):
        """从队列取帧，广播给订阅的前端客户端"""
        broadcast_count = 0
        last_log_time = time.time()
        while True:
            try:
                frame_data = await self.frame_queue.get()
                side = frame_data['side']
                frame_b64 = frame_data['frame']
                frame_ts = frame_data.get('timestamp', 0)

                # 帧时间戳日志（前10帧 + 之后每30帧）
                broadcast_count += 1
                now = time.time()
                if broadcast_count <= 10 or broadcast_count % 30 == 0:
                    queue_lag = now - frame_ts if frame_ts else 0
                    fps = broadcast_count / (now - last_log_time) if (now - last_log_time) > 0 else 0
                    print(f'[CameraServer] 📤 广播帧#{broadcast_count} {side} | '
                          f'队列积压={queue_lag*1000:.0f}ms | 大小={len(frame_b64)/1024:.1f}KB | '
                          f'平均fps={fps:.1f}')
                    if broadcast_count % 30 == 0:
                        broadcast_count = 0
                        last_log_time = now

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
        recording_sides = [s for s in ['left', 'right'] if s in self.recorders and self.recorders[s].recording]
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
        elif command == 'capture_snapshot':
            return self._cmd_get_preview_frame(data)  # 复用同一个实现（cache + oneshot回退）
        elif command == 'get_preview_frame':
            return self._cmd_get_preview_frame(data)
        elif command == 'get_server_time':
            return self._cmd_get_server_time()
        elif command == 'get_status':
            return self._cmd_get_status()
        elif command == 'start_recording':
            # 兼容旧接口
            return self._cmd_mark_recording_start(data)
        elif command == 'stop_recording':
            # 兼容旧接口
            side = data.get('side')
            if side and side in self.recorders:
                timestamp = datetime.now().strftime('%y%m%d_%H%M%S')
                output_filename = f'recording_{side}_{timestamp}.avi'
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
            pending_device = None  # 等待其 Alternative name 的设备

            def _flush_pending():
                """将待定设备加入列表，同名设备用 alt_name 区分"""
                nonlocal pending_device
                if not pending_device:
                    return
                name = pending_device['name']
                alt = pending_device.get('alt_name')
                # 同名设备：优先用 @device_pnp 作为唯一 id；无 alt_name 则保留名称
                existing_names = [d['name'] for d in devices]
                if name in existing_names:
                    # 同名第 N 个设备，强制用 alt_name 作为 id 以区分
                    pending_device['id'] = alt if alt else f'{name}#{existing_names.count(name)+1}'
                else:
                    pending_device['id'] = alt if alt else name
                devices.append(pending_device)
                pending_device = None

            for line in output.split('\n'):
                # 新格式: [in#0 @ xxx] "设备名称" (video)
                new_match = re.search(r'\[in#\d+.*?\]\s*"([^"]+)"\s*\(video\)', line)
                if new_match and 'Alternative name' not in line:
                    _flush_pending()
                    device_name = new_match.group(1)
                    pending_device = {'name': device_name, 'id': device_name, 'alt_name': None}
                    print(f'[CameraServer]   [新格式] {device_name}')
                    continue

                # 旧格式: DirectShow video devices 段落中的 "设备名称"
                old_match = re.search(r'^\s*"([^"]+)"\s*\(video\)', line)
                if old_match and 'Alternative name' not in line:
                    device_name = old_match.group(1)
                    # 去重（旧格式优先，已存在的跳过）
                    if device_name in {d['name'] for d in devices}:
                        continue
                    _flush_pending()
                    pending_device = {'name': device_name, 'id': device_name, 'alt_name': None}
                    print(f'[CameraServer]   [旧格式] {device_name}')
                    continue

                # 捕获 Alternative name — 属于当前 pending_device
                # 只取 @device_pnp_ 路径（视频设备），过滤音频/软件路径
                #   @device_pnp_  → 物理即插即用视频设备 ✓
                #   @device_cm_   → Kernel Streaming 音频 ✗
                #   @device_sw_   → 软件虚拟设备 ✗
                alt_path_match = re.search(r'Alternative name\s+"(@device_pnp_[^"]+)"', line)
                if alt_path_match and pending_device:
                    pending_device['alt_name'] = alt_path_match.group(1)
                    print(f'[CameraServer]     备用路径(视频): {pending_device["alt_name"]}')
                    continue

            # 刷新最后一个待定设备
            _flush_pending()

            print(f'[CameraServer] 找到 {len(devices)} 个摄像头设备')
            # 缓存设备列表供 set_camera 查找 alt_name
            self._device_list_cache = devices
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
        """设置摄像头配置（不启动采集，只保存配置）

        device_id 可以是友好名称或 @device_pnp_... 路径。
        当两个摄像头同名时，前端通过 device_id（@device_pnp 路径）区分。
        """
        side = data.get('side')
        device_name = data.get('device_name')
        device_id = data.get('device_id')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        # 从缓存的设备列表中查找匹配的设备
        alt_name = None
        resolved_name = device_name
        if hasattr(self, '_device_list_cache') and self._device_list_cache:
            if device_id:
                for dev in self._device_list_cache:
                    if dev.get('id') == device_id:
                        alt_name = dev.get('alt_name')
                        resolved_name = dev.get('name')
                        if alt_name:
                            print(f'[CameraServer]   {side} 匹配设备: {resolved_name}'
                                  f' (备用路径: {alt_name[:60]}...)')
                        break
            # 如果 device_id 没匹配到，尝试用 device_name 匹配
            if not alt_name and not resolved_name:
                for dev in self._device_list_cache:
                    if dev.get('name') == device_name:
                        alt_name = dev.get('alt_name')
                        resolved_name = dev.get('name')
                        if alt_name:
                            print(f'[CameraServer]   {side} 备用路径: {alt_name[:60]}...')
                        break

        self.cameras[side] = {
            'device_name': resolved_name,
            'device_id': device_id or resolved_name,
            'alt_name': alt_name
        }

        print(f'[CameraServer] 摄像头配置已保存: {side} -> {resolved_name}')
        return {
            'success': True,
            'side': side,
            'device_name': resolved_name,
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

        # 如果正在帧录制，不能打开新的MJPEG（摄像头只能被一个进程占用）
        if side in self.recorders and self.recorders[side].recording:
            return {'success': False, 'error': f'{side}侧正在录制中，不能同时重新打开摄像头'}

        camera = self.cameras[side]
        alt_name = camera.get('alt_name')  # 备用设备路径（@device_pnp_...），用于 fallback
        capture = CameraCapture(side, camera['device_name'], self.ffmpeg_path,
                                self.frame_queue, alt_name=alt_name)

        # 在 executor 中运行 start()，避免阻塞事件循环
        # （start() 内部有 sleep 轮询，阻塞期间无法处理其他 WebSocket 消息）
        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(None, capture.start)

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
        """启动帧录制（由realtimeEngine在采集开始时调用）

        与旧架构不同：不停止MJPEG预览，直接从预览管道保存帧。
        预览在录制期间持续可用，且不存在dshow设备释放问题。
        """
        side = data.get('side')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        if side not in self.cameras:
            return {'success': False, 'error': f'{side}侧摄像头未配置'}

        if not self.ffmpeg_path:
            return {'success': False, 'error': 'ffmpeg未安装'}

        # 确保MJPEG预览在运行（不会停止它）
        if side not in self.captures or not self.captures[side].running:
            return {'success': False, 'error': f'{side}侧MJPEG预览未启动，请先open_camera'}

        output_filename = data.get('output_filename')
        if not output_filename:
            return {'success': False, 'error': '缺少 output_filename 参数'}

        start_timestamp = data.get('start_timestamp')  # 前端传入的统一时间戳

        # 创建帧录制器（复用MJPEG管道，不创建新的ffmpeg进程）
        recorder = FrameRecorder(side, self.ffmpeg_path, self.output_dir)
        success = recorder.start(output_filename, start_timestamp=start_timestamp)

        if success:
            self.recorders[side] = recorder
            # 将录制器挂到 CameraCapture 上，_read_frames 线程会开始写帧
            self.captures[side].frame_recorder = recorder
            # 推送录制状态
            await self._push_recording_status()
            print(f'[CameraServer] [{side}] 帧录制已启动（MJPEG预览不中断）')
            return {'success': True, 'side': side, 'message': f'{side}侧录制已启动'}
        else:
            return {'success': False, 'error': f'{side}侧录制启动失败'}

    def _cmd_mark_recording_start(self, data):
        """标记录制起始点（兼容旧接口）

        FrameRecorder架构下，录制在 start_continuous_recording 时已开始，
        帧持续从MJPEG管道写入，无需单独标记起始点。
        """
        side = data.get('side')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        if side not in self.recorders:
            return {'success': False, 'error': f'{side}侧录制未启动'}

        # FrameRecorder 架构无需 mark_start，录制在 start 时已开始
        return {
            'success': True,
            'side': side,
            'message': '录制已在运行中（FrameRecorder持续保存帧）'
        }

    async def _cmd_stop_and_save(self, data):
        """停止帧录制并保存AVI"""
        side = data.get('side')
        output_filename = data.get('output_filename')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        return await self._do_stop_and_save(side, output_filename)

    async def _do_stop_and_save(self, side, output_filename):
        """执行停止录制和封装（不阻塞事件循环）"""
        if side not in self.recorders:
            return {'success': False, 'error': f'{side}侧录制未启动'}

        recorder = self.recorders[side]

        # 解除 CameraCapture 对录制器的引用（停止写帧）
        if side in self.captures:
            self.captures[side].frame_recorder = None

        # stop_and_save 包含 subprocess.run（ffmpeg封装），放到 executor 避免阻塞
        loop = asyncio.get_running_loop()
        save_result = await loop.run_in_executor(None, recorder.stop_and_save)

        if save_result and save_result.get('success'):
            del self.recorders[side]
            result = {
                'success': True,
                'side': side,
                'output_path': save_result.get('path', ''),
                'filename': output_filename,
                'file_size': save_result.get('size', 0),
                'timing': save_result.get('timing', {})
            }
        else:
            error_detail = (save_result or {}).get('error', '录制保存失败(无详细错误)')
            del self.recorders[side]
            result = {
                'success': False,
                'error': f'录制保存失败: {error_detail}'
            }

        # 推送录制结束状态
        await self._push_recording_status()

        # MJPEG 预览一直未停止，无需恢复
        print(f'[CameraServer] [{side}] 录制结束，MJPEG预览持续运行中')

        return result

    def _capture_one_shot(self, side):
        """一次性抓取单帧（用于按需拍照模式）

        运行独立 ffmpeg 进程捕获一帧后立即退出，无需预先打开 CameraCapture。
        耗时约 1-2 秒（ffmpeg 启动 + 摄像头初始化）。
        """
        if side not in self.cameras:
            return None, f'{side}侧摄像头未配置'
        if not self.ffmpeg_path:
            return None, 'ffmpeg未安装'

        device_name = self.cameras[side]['device_name']
        alt_name = self.cameras[side].get('alt_name')
        friendly_name = re.sub(r'\s*\([0-9a-fA-F:]+\)\s*$', '', device_name).strip()

        # 候选设备ID列表：优先友好名称，后备 @device_pnp 路径
        candidates = ['video=' + friendly_name]
        candidate_labels = ['友好名称']
        if alt_name:
            candidates.append('video=' + alt_name)
            candidate_labels.append('设备路径')

        # 三种采集模式（与 CameraCapture.start() 保持一致）
        mode_configs = [
            ('auto', []),                                                   # DShow 自动协商
            ('1080p30', ['-video_size', '1920x1080', '-framerate', '30']),  # 显式参数
            ('yuy2', ['-pixel_format', 'yuyv422',                           # YUY2 格式
                      '-video_size', '1920x1080', '-framerate', '30']),
        ]

        for candidate_idx, video_arg in enumerate(candidates):
            label = candidate_labels[candidate_idx]
            for mode_name, mode_params in mode_configs:
                cmd = [
                    self.ffmpeg_path,
                    '-f', 'dshow',
                    '-rtbufsize', '2M',
                ] + mode_params + [
                    '-i', video_arg,
                    '-vframes', '1',
                    '-vcodec', 'mjpeg',
                    '-q:v', '8',
                    '-f', 'image2pipe',
                    'pipe:1'
                ]

                print(f'[CameraServer] 📸 {side}侧 单帧拍照 ({label}, 模式={mode_name}): {video_arg}')
                try:
                    proc = subprocess.run(
                        cmd,
                        capture_output=True,
                        timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                    )
                    if proc.stdout and len(proc.stdout) > 0:
                        start = proc.stdout.find(b'\xff\xd8')
                        end = proc.stdout.find(b'\xff\xd9', start + 2) if start != -1 else -1
                        if start != -1 and end != -1:
                            frame = proc.stdout[start:end + 2]
                            b64 = base64.b64encode(frame).decode()
                            print(f'[CameraServer] 📸 {side}侧 拍照成功 '
                                  f'({len(frame)} bytes, {len(b64)/1024:.1f}KB b64)')
                            return b64, None
                        else:
                            # 此组合未产生有效帧，尝试下一个
                            continue
                    else:
                        stderr_tail = proc.stderr[-500:].decode('utf-8', errors='ignore') if proc.stderr else '(无输出)'
                        print(f'[CameraServer] 📸 {side}侧 {label}/{mode_name} 失败: {stderr_tail[:200]}')
                        continue  # 尝试下一个组合
                except subprocess.TimeoutExpired:
                    print(f'[CameraServer] 📸 {side}侧 {label}/{mode_name} 超时')
                    continue
                except Exception as e:
                    print(f'[CameraServer] 📸 {side}侧 {label}/{mode_name} 异常: {e}')
                    continue
        # 所有候选都不成功
        return None, f'{side}摄像头抓帧失败: 全部候选标识符均无法打开'

    def _cmd_get_preview_frame(self, data):
        """获取单帧预览（按需拍照模式）

        优先从 CameraCapture 取缓存帧（即时）。
        **绝不**在 CameraCapture 运行时回退到 one-shot ffmpeg，
        防止两个 ffmpeg 抢同一个 DirectShow 摄像头导致 I/O 冲突。
        CameraCapture 未启动时才使用 one-shot 抓帧。
        """
        side = data.get('side')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        # CameraCapture 正在运行 → 只用缓存帧，不回退 one-shot
        capture = self.captures.get(side)
        if capture and capture.running:
            if capture.latest_frame_b64:
                print(f'[CameraServer] 📸 {side}侧 返回缓存帧 ({(len(capture.latest_frame_b64)/1024):.1f}KB)')
                return {
                    'success': True,
                    'side': side,
                    'frame': capture.latest_frame_b64,
                    'source': 'cache'
                }
            else:
                # 正在初始化，还没有帧，前端会在下次定时器重试
                return {'success': False, 'error': f'{side}侧摄像头初始化中，稍后重试'}

        # CameraCapture 未运行 → 回退：一次性 ffmpeg 抓帧
        b64, error = self._capture_one_shot(side)
        if b64:
            return {
                'success': True,
                'side': side,
                'frame': b64,
                'source': 'oneshot'
            }
        return {'success': False, 'error': error or f'{side}侧无可用预览帧'}

    def _cmd_get_server_time(self):
        """返回 Python time.time() 作为统一时钟源

        realtimeEngine 在采集开始时调用此命令，获取与数据时间戳
        （EMG: ble_server.py time.time(), 视频: camera_server.py time.time()）
        同源的会话起始时间，消除 Node.js Date.now() 与 Python time.time()
        之间的潜在时钟偏差。
        """
        return {'success': True, 'server_time': time.time()}

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
                side: rec.recording if rec else False
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

        # 先停止所有帧录制（detach from captures before clearing captures）
        for side, recorder in list(self.recorders.items()):
            if recorder.recording:
                try:
                    # 解除 CameraCapture 的录制器引用
                    if side in self.captures:
                        self.captures[side].frame_recorder = None
                    recorder.stop_and_save()
                except:
                    pass
        self.recorders.clear()

        # 再停止所有MJPEG采集
        for side, capture in list(self.captures.items()):
            capture.stop()
        self.captures.clear()

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
