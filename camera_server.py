"""
camera_server.py - USB摄像头管理服务器（HLS预录制版本）

功能：
1. 枚举USB摄像头设备
2. 持续HLS录制（配置摄像头后立即开始）
3. 按需保存（space -> H5结束的精确片段）
4. 提供静态帧预览（不占用摄像头）
5. 通过WebSocket提供控制接口

WebSocket端口: 8768
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

def find_ffmpeg():
    """查找 ffmpeg 可执行文件"""
    # 1. 先尝试系统 PATH
    ffmpeg_path = shutil.which('ffmpeg')
    if ffmpeg_path:
        return ffmpeg_path

    # 2. 查找 Windows WinGet 安装的 ffmpeg
    if sys.platform == 'win32':
        user_home = Path.home()
        winget_pattern = user_home / 'AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe'
        matches = glob.glob(str(winget_pattern))
        if matches:
            print(f'[CameraServer] 找到 WinGet 安装的 ffmpeg: {matches[0]}')
            return matches[0]

    # 3. 未找到
    return None

class HLSRecorder:
    """HLS持续录制器"""
    def __init__(self, side, device_name, ffmpeg_path, temp_dir):
        self.side = side
        self.device_name = device_name
        self.ffmpeg_path = ffmpeg_path
        self.temp_dir = temp_dir / side
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self.process = None
        self.running = False
        self.current_segment = 0
        self.mark_segment = None  # 标记的录制起始分段
        self.monitor_thread = None

    def start(self):
        """启动持续HLS录制"""
        if self.running:
            print(f'[HLSRecorder] [{self.side}] 已在运行中')
            return

        # 清理旧的临时文件
        for f in self.temp_dir.glob('*.ts'):
            f.unlink()
        for f in self.temp_dir.glob('*.m3u8'):
            f.unlink()

        # 清理设备名称
        clean_device_name = re.sub(r'\s*\([0-9a-fA-F:]+\)\s*$', '', self.device_name).strip()

        # HLS录制命令
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
            '-g', '30',  # 每秒一个关键帧
            '-f', 'hls',
            '-hls_time', '1',  # 每1秒一个分段
            '-hls_list_size', '0',  # 保留所有分段
            '-hls_flags', 'append_list',  # 只追加列表，不删除分段
            '-hls_segment_filename', segment_pattern,
            str(m3u8_path)
        ]

        print(f'[HLSRecorder] [{self.side}] 启动HLS录制')
        print(f'[HLSRecorder] [{self.side}]   设备: {clean_device_name}')
        print(f'[HLSRecorder] [{self.side}]   临时目录: {self.temp_dir}')

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

            # 启动监控线程
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
                # 读取 m3u8 文件，获取最新分段索引
                m3u8_path = self.temp_dir / 'stream.m3u8'
                if m3u8_path.exists():
                    with open(m3u8_path, 'r') as f:
                        content = f.read()
                        # 查找所有分段文件名
                        segments = re.findall(r'segment_(\d+)\.ts', content)
                        if segments:
                            latest = max([int(s) for s in segments])
                            if latest > self.current_segment:
                                self.current_segment = latest
                                # print(f'[HLSRecorder] [{self.side}] 当前分段: {self.current_segment}')

                time.sleep(0.5)

            except Exception as e:
                print(f'[HLSRecorder] [{self.side}] 监控分段出错: {e}')
                time.sleep(1)

    def mark_start(self):
        """标记录制起始点（按空格键时调用）"""
        self.mark_segment = self.current_segment
        print(f'[HLSRecorder] [{self.side}] 🎬 标记录制起始: 分段 {self.mark_segment}')
        return self.mark_segment

    def stop_and_save(self, output_path):
        """停止录制并保存标记片段到MP4"""
        if not self.running:
            return False

        end_segment = self.current_segment
        print(f'[HLSRecorder] [{self.side}] 停止录制并保存')
        print(f'[HLSRecorder] [{self.side}]   起始分段: {self.mark_segment}')
        print(f'[HLSRecorder] [{self.side}]   结束分段: {end_segment}')

        try:
            # 发送 'q' 让 ffmpeg 优雅退出
            if self.process:
                self.process.stdin.write(b'q')
                self.process.stdin.flush()
                self.process.wait(timeout=5)
        except Exception as e:
            print(f'[HLSRecorder] [{self.side}] 停止ffmpeg出错: {e}')
            if self.process:
                self.process.kill()

        self.running = False

        # 合并分段
        if self.mark_segment is not None:
            success = self._merge_segments(self.mark_segment, end_segment, output_path)

            # 清理临时文件
            self._cleanup_temp_files()

            return success
        else:
            print(f'[HLSRecorder] [{self.side}] ⚠️  未标记起始点，无法保存')
            return False

    def _merge_segments(self, start_seg, end_seg, output_path):
        """合并分段为MP4"""
        print(f'[HLSRecorder] [{self.side}] 合并分段 {start_seg} -> {end_seg}')

        # 生成分段列表
        segment_files = []
        for i in range(start_seg, end_seg + 1):
            seg_path = self.temp_dir / f'segment_{i:05d}.ts'
            if seg_path.exists():
                segment_files.append(seg_path)

        if not segment_files:
            print(f'[HLSRecorder] [{self.side}] ❌ 没有找到分段文件')
            return False

        print(f'[HLSRecorder] [{self.side}]   找到 {len(segment_files)} 个分段')

        # 使用 concat demuxer 合并
        concat_file = self.temp_dir / 'concat.txt'
        with open(concat_file, 'w') as f:
            for seg in segment_files:
                f.write(f"file '{seg.absolute()}'\n")

        # 合并命令
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
                print(f'[HLSRecorder] [{self.side}] ✅ MP4已保存: {output_path}')
                return True
            else:
                print(f'[HLSRecorder] [{self.side}] ❌ 合并失败:')
                print(result.stderr.decode('utf-8', errors='ignore'))
                return False

        except Exception as e:
            print(f'[HLSRecorder] [{self.side}] ❌ 合并出错: {e}')
            import traceback
            traceback.print_exc()
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

    def get_preview_frame(self):
        """获取预览帧（从最新分段提取）"""
        try:
            # 找到最新的分段文件
            latest_seg = self.temp_dir / f'segment_{self.current_segment:05d}.ts'
            if not latest_seg.exists():
                # 如果最新分段还未生成，尝试前一个
                latest_seg = self.temp_dir / f'segment_{self.current_segment - 1:05d}.ts'

            if not latest_seg.exists():
                return None

            # 提取一帧
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
                import base64
                return base64.b64encode(result.stdout).decode()
            else:
                return None

        except Exception as e:
            print(f'[HLSRecorder] [{self.side}] 获取预览帧失败: {e}')
            return None

class CameraServer:
    def __init__(self):
        self.cameras = {}  # 摄像头配置 {side: {device_name, device_id}}
        self.recorders = {}  # HLS录制器 {side: HLSRecorder}
        self.recording_status = {
            'left': False,
            'right': False
        }
        self.output_dir = Path('storage/video')
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 临时目录
        self.temp_dir = Path('storage/video/temp')
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # 查找 ffmpeg
        self.ffmpeg_path = find_ffmpeg()
        if self.ffmpeg_path:
            print(f'[CameraServer] 使用 ffmpeg: {self.ffmpeg_path}')
        else:
            print('[CameraServer] ⚠️  警告: 未找到 ffmpeg，视频录制功能将不可用')

        print('[CameraServer] 摄像头服务器初始化完成（HLS预录制模式）')
        print(f'[CameraServer] 视频输出目录: {self.output_dir.absolute()}')

    async def handle_client(self, websocket):
        """处理客户端连接"""
        client_addr = websocket.remote_address
        print(f'[CameraServer] 客户端已连接: {client_addr}')

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    command = data.get('command')

                    print(f'[CameraServer] 收到命令: {command}')

                    if command == 'set_camera':
                        response = self.set_camera(data)
                    elif command == 'start_continuous_recording':
                        # 【新增】启动持续HLS录制
                        response = self.start_continuous_recording(data)
                    elif command == 'mark_recording_start':
                        # 【新增】标记录制起始点（按空格键）
                        response = self.mark_recording_start(data)
                    elif command == 'stop_and_save':
                        # 【新增】停止并保存MP4
                        response = await self.stop_and_save(data)
                    elif command == 'get_preview_frame':
                        # 【新增】获取预览帧
                        response = self.get_preview_frame(data)
                    elif command == 'start_recording':
                        # 兼容旧接口
                        response = await self.start_recording_legacy(data)
                    elif command == 'stop_recording':
                        # 兼容旧接口
                        response = await self.stop_recording_legacy(data)
                    elif command == 'get_status':
                        response = self.get_status()
                    elif command == 'list_cameras':
                        response = await self.list_cameras()
                    else:
                        response = {
                            'success': False,
                            'error': f'未知命令: {command}'
                        }

                    await websocket.send(json.dumps(response))

                except json.JSONDecodeError as e:
                    error_response = {
                        'success': False,
                        'error': f'JSON解析错误: {str(e)}'
                    }
                    await websocket.send(json.dumps(error_response))
                except Exception as e:
                    print(f'[CameraServer] 处理消息时出错: {e}')
                    import traceback
                    traceback.print_exc()
                    error_response = {
                        'success': False,
                        'error': str(e)
                    }
                    await websocket.send(json.dumps(error_response))

        except websockets.exceptions.ConnectionClosed:
            print(f'[CameraServer] 客户端断开连接: {client_addr}')
        except Exception as e:
            print(f'[CameraServer] 连接错误: {e}')
            import traceback
            traceback.print_exc()

    def set_camera(self, data):
        """设置摄像头配置并启动持续录制"""
        side = data.get('side')
        device_name = data.get('device_name')
        device_id = data.get('device_id')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        self.cameras[side] = {
            'device_name': device_name,
            'device_id': device_id
        }

        print(f'[CameraServer] 摄像头配置已设置: {side} -> {device_name}')

        # 立即启动持续HLS录制
        if self.ffmpeg_path:
            recorder = HLSRecorder(side, device_name, self.ffmpeg_path, self.temp_dir)
            success = recorder.start()
            if success:
                self.recorders[side] = recorder
                print(f'[CameraServer] ✅ {side}侧持续HLS录制已启动')
            else:
                print(f'[CameraServer] ❌ {side}侧HLS录制启动失败')

        return {
            'success': True,
            'side': side,
            'device_name': device_name
        }

    def start_continuous_recording(self, data):
        """启动持续HLS录制（如果尚未启动）"""
        side = data.get('side')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        if side in self.recorders and self.recorders[side].running:
            return {'success': True, 'message': '已在录制中'}

        if side not in self.cameras:
            return {'success': False, 'error': f'{side}侧摄像头未配置'}

        camera = self.cameras[side]
        recorder = HLSRecorder(side, camera['device_name'], self.ffmpeg_path, self.temp_dir)
        success = recorder.start()

        if success:
            self.recorders[side] = recorder
            return {'success': True, 'side': side}
        else:
            return {'success': False, 'error': '启动HLS录制失败'}

    def mark_recording_start(self, data):
        """标记录制起始点（按空格键时调用）"""
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

    async def stop_and_save(self, data):
        """停止HLS录制并保存MP4"""
        side = data.get('side')
        output_filename = data.get('output_filename')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        if side not in self.recorders:
            return {'success': False, 'error': f'{side}侧HLS录制未启动'}

        output_path = self.output_dir / output_filename
        recorder = self.recorders[side]

        success = recorder.stop_and_save(output_path)

        if success:
            del self.recorders[side]
            return {
                'success': True,
                'side': side,
                'output_path': str(output_path),
                'filename': output_filename
            }
        else:
            return {
                'success': False,
                'error': '保存MP4失败'
            }

    def get_preview_frame(self, data):
        """获取预览帧"""
        side = data.get('side')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        if side not in self.recorders:
            return {'success': False, 'error': f'{side}侧HLS录制未启动'}

        recorder = self.recorders[side]
        frame_data = recorder.get_preview_frame()

        if frame_data:
            return {
                'success': True,
                'side': side,
                'frame': frame_data  # base64编码的JPEG
            }
        else:
            return {
                'success': False,
                'error': '获取预览帧失败'
            }

    # ===== 兼容旧接口 =====

    async def start_recording_legacy(self, data):
        """兼容旧的start_recording接口"""
        side = data.get('side')
        output_filename = data.get('output_filename')

        # 标记起始点
        result = self.mark_recording_start({'side': side})

        if result['success']:
            self.recording_status[side] = True
            return {
                'success': True,
                'side': side,
                'output_filename': output_filename,
                'message': '已标记录制起始点'
            }
        else:
            return result

    async def stop_recording_legacy(self, data):
        """兼容旧的stop_recording接口"""
        side = data.get('side')

        if not self.recording_status.get(side):
            return {'success': True, 'message': '未在录制中'}

        # 需要output_filename，但旧接口没有
        # 生成一个临时文件名
        timestamp = datetime.now().strftime('%y%m%d_%H%M%S')
        output_filename = f'recording_{side}_{timestamp}.mp4'

        result = await self.stop_and_save({
            'side': side,
            'output_filename': output_filename
        })

        self.recording_status[side] = False
        return result

    def get_status(self):
        """获取服务器状态"""
        return {
            'success': True,
            'cameras': self.cameras,
            'recording_status': {
                side: (side in self.recorders and self.recorders[side].running)
                for side in ['left', 'right']
            }
        }

    async def list_cameras(self):
        """枚举可用的摄像头设备"""
        print('[CameraServer] 枚举摄像头设备...')

        if not self.ffmpeg_path:
            error_msg = 'ffmpeg 未安装，无法枚举摄像头'
            print(f'[CameraServer] ❌ {error_msg}')
            return {
                'success': False,
                'error': error_msg,
                'devices': []
            }

        try:
            result = subprocess.run(
                [self.ffmpeg_path, '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy'],
                capture_output=True,
                encoding='utf-8',
                errors='ignore',
                timeout=10
            )

            output = result.stderr
            devices = []

            for line in output.split('\n'):
                match = re.search(r'\[in#\d+.*?\] "([^"]+)"\s+\(video\)', line)
                if match:
                    device_name = match.group(1)
                    if 'Alternative name' not in line:
                        devices.append({
                            'name': device_name,
                            'id': device_name
                        })

            print(f'[CameraServer] 找到 {len(devices)} 个摄像头设备')
            for device in devices:
                print(f'[CameraServer]   - {device["name"]}')

            return {
                'success': True,
                'devices': devices
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

    async def cleanup(self):
        """清理资源"""
        print('[CameraServer] 正在清理资源...')

        # 停止所有HLS录制
        for side, recorder in list(self.recorders.items()):
            if recorder.running:
                try:
                    recorder.process.kill()
                    recorder.running = False
                except:
                    pass

        print('[CameraServer] 清理完成')

async def main():
    camera_server = CameraServer()

    # 启动WebSocket服务器
    port = 8768
    server = await websockets.serve(camera_server.handle_client, 'localhost', port)

    print(f'[CameraServer] WebSocket服务器已启动: ws://localhost:{port}')
    print('[CameraServer] 等待客户端连接...')

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
