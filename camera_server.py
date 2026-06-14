"""
camera_server.py - USB摄像头管理服务器

功能：
1. 枚举USB摄像头设备
2. 管理视频录制（使用ffmpeg后端录制）
3. 通过WebSocket提供控制接口
4. 录制视频到 storage/video/ 目录

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
import sys
from datetime import datetime
from pathlib import Path
import shutil
import glob

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

class CameraServer:
    def __init__(self):
        self.cameras = {}  # 摄像头配置 {side: {device_name, device_id}}
        self.recording_processes = {}  # ffmpeg进程 {side: process}
        self.recording_status = {
            'left': False,
            'right': False
        }
        self.output_dir = Path('storage/video')
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 查找 ffmpeg
        self.ffmpeg_path = find_ffmpeg()
        if self.ffmpeg_path:
            print(f'[CameraServer] 使用 ffmpeg: {self.ffmpeg_path}')
        else:
            print('[CameraServer] ⚠️  警告: 未找到 ffmpeg，视频录制功能将不可用')

        print('[CameraServer] 摄像头服务器初始化完成')
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
                        # 设置摄像头配置
                        response = self.set_camera(data)
                    elif command == 'start_recording':
                        # 开始录制
                        response = await self.start_recording(data)
                    elif command == 'stop_recording':
                        # 停止录制
                        response = await self.stop_recording(data)
                    elif command == 'get_status':
                        # 获取状态
                        response = self.get_status()
                    elif command == 'list_cameras':
                        # 枚举摄像头（Windows: 使用ffmpeg -list_devices）
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
        """设置摄像头配置"""
        side = data.get('side')  # 'left' or 'right'
        device_name = data.get('device_name')
        device_id = data.get('device_id')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        self.cameras[side] = {
            'device_name': device_name,
            'device_id': device_id
        }

        print(f'[CameraServer] 摄像头配置已设置: {side} -> {device_name}')

        return {
            'success': True,
            'side': side,
            'device_name': device_name
        }

    async def start_recording(self, data):
        """开始录制视频"""
        side = data.get('side')  # 'left' or 'right'
        output_filename = data.get('output_filename')  # 例如: R003_L_260614_162119.mp4

        print(f'[CameraServer] >>> start_recording 被调用')
        print(f'[CameraServer]     side: {side}')
        print(f'[CameraServer]     output_filename: {output_filename}')
        print(f'[CameraServer]     当前配置的摄像头: {list(self.cameras.keys())}')

        if not side or side not in ['left', 'right']:
            error_msg = '无效的side参数'
            print(f'[CameraServer] ❌ {error_msg}')
            return {'success': False, 'error': error_msg}

        if self.recording_status[side]:
            error_msg = f'{side}侧摄像头已在录制中'
            print(f'[CameraServer] ❌ {error_msg}')
            return {'success': False, 'error': error_msg}

        if side not in self.cameras:
            error_msg = f'{side}侧摄像头未配置'
            print(f'[CameraServer] ❌ {error_msg}')
            print(f'[CameraServer]     需要先调用 set_camera 配置摄像头！')
            return {'success': False, 'error': error_msg}

        camera = self.cameras[side]
        device_name = camera['device_name']

        # 构建输出路径
        output_path = self.output_dir / output_filename

        print(f'[CameraServer] 开始录制: {side}')
        print(f'[CameraServer]   设备: {device_name}')
        print(f'[CameraServer]   输出: {output_path}')

        # 构建 ffmpeg 命令
        if not self.ffmpeg_path:
            error_msg = 'ffmpeg 未安装，无法录制视频'
            print(f'[CameraServer] ❌ {error_msg}')
            return {'success': False, 'error': error_msg}

        ffmpeg_cmd = [
            self.ffmpeg_path,
            '-f', 'dshow',                    # Windows DirectShow
            '-video_size', '1280x720',
            '-framerate', '30',
            '-i', f'video={device_name}',
            '-c:v', 'libx264',                # H.264 编码
            '-preset', 'ultrafast',           # 快速编码
            '-crf', '23',                     # 质量
            '-pix_fmt', 'yuv420p',
            '-y',                             # 覆盖输出文件
            str(output_path)
        ]

        print(f'[CameraServer] ffmpeg命令: {" ".join(ffmpeg_cmd)}')

        try:
            # 启动 ffmpeg 进程
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )

            self.recording_processes[side] = process
            self.recording_status[side] = True

            print(f'[CameraServer] ✅ {side}侧录制已启动, PID: {process.pid}')

            # 启动异步任务监听进程输出
            asyncio.create_task(self._monitor_ffmpeg_output(side, process))

            return {
                'success': True,
                'side': side,
                'output_path': str(output_path),
                'filename': output_filename,
                'pid': process.pid
            }

        except Exception as e:
            print(f'[CameraServer] ❌ 启动录制失败: {e}')
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'error': str(e)
            }

    async def _monitor_ffmpeg_output(self, side, process):
        """监听ffmpeg进程输出"""
        try:
            while True:
                line = process.stderr.readline()
                if not line:
                    break
                line_str = line.decode('utf-8', errors='ignore').strip()

                # 只打印关键信息
                if 'frame=' in line_str or 'error' in line_str.lower():
                    print(f'[CameraServer] [{side}] ffmpeg: {line_str}')

        except Exception as e:
            print(f'[CameraServer] 监听ffmpeg输出时出错: {e}')

    async def stop_recording(self, data):
        """停止录制视频"""
        side = data.get('side')

        if not side or side not in ['left', 'right']:
            return {'success': False, 'error': '无效的side参数'}

        if not self.recording_status[side]:
            return {'success': False, 'error': f'{side}侧摄像头未在录制'}

        process = self.recording_processes.get(side)
        if not process:
            return {'success': False, 'error': f'{side}侧录制进程不存在'}

        print(f'[CameraServer] 停止录制: {side}')

        try:
            # 发送 'q' 让 ffmpeg 优雅退出
            process.communicate(input=b'q', timeout=3)
        except subprocess.TimeoutExpired:
            print(f'[CameraServer] ffmpeg未响应，强制终止')
            process.kill()
        except Exception as e:
            print(f'[CameraServer] 停止录制时出错: {e}')
            process.kill()

        # 等待进程退出
        process.wait()

        self.recording_status[side] = False
        self.recording_processes[side] = None

        print(f'[CameraServer] ✅ {side}侧录制已停止')

        return {
            'success': True,
            'side': side
        }

    def get_status(self):
        """获取服务器状态"""
        return {
            'success': True,
            'cameras': self.cameras,
            'recording_status': self.recording_status
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
            # 使用 ffmpeg -list_devices true -f dshow -i dummy
            result = subprocess.run(
                [self.ffmpeg_path, '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy'],
                capture_output=True,
                encoding='utf-8',
                errors='ignore',  # 忽略编码错误
                timeout=10
            )

            # ffmpeg 的设备列表输出在 stderr 中
            output = result.stderr

            # 解析设备列表
            # 新格式: [in#0 @ xxx] "设备名称" (video)
            # 旧格式: DirectShow video devices 后面的 "设备名称"
            devices = []
            import re

            for line in output.split('\n'):
                # 新格式匹配: [in#0 @ xxx] "设备名称" (video)
                match = re.search(r'\[in#\d+.*?\] "([^"]+)"\s+\(video\)', line)
                if match:
                    device_name = match.group(1)
                    # 过滤掉 Alternative name 行
                    if 'Alternative name' not in line:
                        devices.append({
                            'name': device_name,
                            'id': device_name  # DirectShow 使用名称作为ID
                        })

            print(f'[CameraServer] 找到 {len(devices)} 个摄像头设备')
            for device in devices:
                print(f'[CameraServer]   - {device["name"]}')

            return {
                'success': True,
                'devices': devices
            }

        except FileNotFoundError:
            error_msg = 'ffmpeg 未安装或不在系统 PATH 中。请安装 ffmpeg: https://ffmpeg.org/download.html'
            print(f'[CameraServer] ❌ {error_msg}')
            return {
                'success': False,
                'error': error_msg,
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

    async def cleanup(self):
        """清理资源"""
        print('[CameraServer] 正在清理资源...')

        # 停止所有录制
        for side in ['left', 'right']:
            if self.recording_status[side]:
                await self.stop_recording({'side': side})

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
    await asyncio.Future()  # 永久运行

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\n[CameraServer] 服务器已停止')
