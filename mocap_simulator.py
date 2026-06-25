"""
动捕设备模拟器 (Motion Capture Simulator) - 带实时可视化
==========================================================
模拟动捕设备 SDK，读取 TRC 文件并按照时间戳频率循环发送数据。
同时提供 3D 可视化窗口，实时显示当前帧的手部姿态。

功能：
- 读取 TRC 文件中的 marker 点数据
- 按照 200Hz 频率（5ms 间隔）循环发送
- 通过 WebSocket 发送数据，模拟真实动捕设备
- 实时 3D 可视化显示当前帧

使用方法：
    python mocap_simulator.py [--port 8768] [--file motion_marker_data/gnx-rt-0127-11-right_hand.trc] [--no-viz]

参数：
    --port: WebSocket 端口 (默认: 8768)
    --file: TRC 文件路径
    --no-viz: 禁用可视化窗口
"""

import asyncio
import json
import time
import argparse
import os
import sys
import threading
import math

try:
    import websockets
except ImportError:
    print("请安装 websockets: pip install websockets")
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    print("请安装 numpy: pip install numpy")
    sys.exit(1)

# Marker 点名称
MARKER_NAMES = ['rt1', 'rt2', 'rt3', 'ri1', 'ri2', 'ri3',
                'rm1', 'rm2', 'rm3', 'm1', 'm2', 'm3']

# 默认配置
DEFAULT_PORT = 8768
DEFAULT_TRC_FILE = "motion_marker_data/gnx-rt-0127-11-right_hand.trc"
SAMPLE_RATE = 200  # Hz
FRAME_INTERVAL = 1.0 / SAMPLE_RATE  # 5ms

# 可视化配置
FINGER_COLORS = {
    'thumb': '#FF6B6B',   # 红色 - 拇指
    'index': '#4ECDC4',   # 青色 - 食指
    'middle': '#45B7D1',  # 蓝色 - 中指
    'palm': '#FFD93D',    # 黄色 - 掌部/腕部
}

MARKER_COLORS = [
    FINGER_COLORS['thumb'], FINGER_COLORS['thumb'], FINGER_COLORS['thumb'],
    FINGER_COLORS['index'], FINGER_COLORS['index'], FINGER_COLORS['index'],
    FINGER_COLORS['middle'], FINGER_COLORS['middle'], FINGER_COLORS['middle'],
    FINGER_COLORS['palm'], FINGER_COLORS['palm'], FINGER_COLORS['palm'],
]

FINGER_CONNECTIONS = [
    (0, 1, 2),       # 拇指: rt1 -> rt2 -> rt3
    (3, 4, 5),       # 食指: ri1 -> ri2 -> ri3
    (6, 7, 8),       # 中指: rm1 -> rm2 -> rm3
    (5, 9),          # 食指根: ri3 -> m1
    (8, 10),         # 中指根: rm3 -> m2
    (9, 10, 11),     # 掌部: m1 -> m2 -> m3
]


def fit_line_direction(p1, p2, p3):
    """用三个点拟合直线方向"""
    direction = p3 - p1
    norm = np.linalg.norm(direction)
    if norm < 1e-6:
        return np.array([0, 0, 1])
    return direction / norm


def fit_plane_normal(p1, p2, p3):
    """用三个点拟合平面法向量"""
    v1 = p2 - p1
    v2 = p3 - p1
    normal = np.cross(v1, v2)
    norm = np.linalg.norm(normal)
    if norm < 1e-6:
        return np.array([0, 0, 1])
    return normal / norm


def calculate_gestures(frame_data):
    """计算三种手势参数"""
    rt1 = np.array(frame_data[0])  # 拇指指尖
    ri1 = np.array(frame_data[3])  # 食指指尖
    ri2 = np.array(frame_data[4])  # 食指第一关节
    ri3 = np.array(frame_data[5])  # 食指第二关节
    m1 = np.array(frame_data[9])   # 食指指根
    m2 = np.array(frame_data[10])  # 中指指根
    m3 = np.array(frame_data[11])  # 腕部

    # 食指角度
    index_dir = fit_line_direction(ri1, ri2, ri3)
    palm_normal = fit_plane_normal(m1, m2, m3)
    cos_angle = abs(np.dot(index_dir, palm_normal))
    cos_angle = np.clip(cos_angle, -1, 1)
    finger_angle = 90 - math.degrees(math.acos(cos_angle))

    # 捏合距离
    pinch_dist = np.linalg.norm(rt1 - ri1)

    # 翻转角度 - 使用手掌平面法向量与向下方向的夹角
    # 掌心向下时法向量指向下方，与[0,0,-1]夹角≈0°
    # 掌心向上时法向量指向上方，与[0,0,-1]夹角≈180°
    downward = np.array([0, 0, -1])  # 向下方向
    cos_palm = np.dot(palm_normal, downward)
    cos_palm = np.clip(cos_palm, -1, 1)
    palm_angle = math.degrees(math.acos(cos_palm))  # 0°=掌心向下, 180°=掌心向上

    return finger_angle, pinch_dist, palm_angle


class MocapSimulator:
    """动捕设备模拟器 - 带可视化"""

    def __init__(self, trc_file, port=DEFAULT_PORT, enable_viz=True):
        self.trc_file = trc_file
        self.port = port
        self.enable_viz = enable_viz
        self.frames = []
        self.times = []
        self.clients = set()
        self.running = False
        self.current_frame = 0
        self.loop_count = 0
        self._lock = threading.Lock()

    def parse_trc_file(self):
        """解析 TRC 文件"""
        print(f"[MocapSim] 加载 TRC 文件: {self.trc_file}")

        with open(self.trc_file, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        data_lines = lines[6:]

        for line in data_lines:
            line = line.strip()
            if not line:
                continue

            parts = line.split('\t')
            if len(parts) < 3 + 12 * 3:
                continue

            try:
                frame_num = int(parts[0])
                time_val = float(parts[1])

                coords = []
                for i in range(12):
                    base_idx = 3 + i * 3
                    x = float(parts[base_idx])
                    y = float(parts[base_idx + 1])
                    z = float(parts[base_idx + 2])
                    coords.append([x, y, z])

                self.frames.append(coords)
                self.times.append(time_val)

            except (ValueError, IndexError):
                continue

        print(f"[MocapSim] 加载完成: {len(self.frames)} 帧, 时长 {self.times[-1]:.2f} 秒")

    def get_frame_data(self, frame_idx):
        """获取指定帧的数据"""
        coords = self.frames[frame_idx]
        markers = {}
        for i, name in enumerate(MARKER_NAMES):
            markers[name] = coords[i]

        return {
            "type": "mocap_data",
            "frame": frame_idx,
            "time": self.times[frame_idx],
            "loop": self.loop_count,
            "markers": markers
        }

    async def broadcast(self, data):
        """广播数据到所有客户端"""
        if not self.clients:
            return

        message = json.dumps(data, ensure_ascii=False)
        disconnected = set()

        for client in self.clients:
            try:
                await client.send(message)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(client)
            except Exception as e:
                print(f"[MocapSim] 发送错误: {e}")
                disconnected.add(client)

        self.clients -= disconnected

    async def data_sender(self):
        """数据发送循环 - 使用精确计时"""
        print(f"[MocapSim] 数据发送器启动 (频率: {SAMPLE_RATE}Hz)")

        start_time = time.perf_counter()
        frame_count = 0

        while self.running:
            if self.clients:
                with self._lock:
                    current = self.current_frame

                data = self.get_frame_data(current)
                await self.broadcast(data)

                with self._lock:
                    self.current_frame += 1
                    frame_count += 1

                    if self.current_frame >= len(self.frames):
                        self.current_frame = 0
                        self.loop_count += 1
                        elapsed = time.perf_counter() - start_time
                        actual_fps = frame_count / elapsed if elapsed > 0 else 0
                        print(f"[MocapSim] 循环播放第 {self.loop_count + 1} 次 (实际帧率: {actual_fps:.1f}Hz)")

                next_frame_time = start_time + frame_count * FRAME_INTERVAL
                sleep_time = next_frame_time - time.perf_counter()

                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
            else:
                await asyncio.sleep(0.1)
                start_time = time.perf_counter()
                frame_count = 0

    async def handle_client(self, websocket, path=None):
        """处理客户端连接"""
        self.clients.add(websocket)
        client_addr = websocket.remote_address
        print(f"[MocapSim] 客户端连接: {client_addr} (总数: {len(self.clients)})")

        welcome = {
            "type": "welcome",
            "message": "MocapSimulator connected",
            "total_frames": len(self.frames),
            "duration": self.times[-1],
            "sample_rate": SAMPLE_RATE,
            "markers": MARKER_NAMES
        }
        try:
            await websocket.send(json.dumps(welcome, ensure_ascii=False))
        except Exception:
            pass

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    action = data.get('action', '')

                    if action == 'status':
                        status = {
                            "type": "status",
                            "current_frame": self.current_frame,
                            "loop_count": self.loop_count,
                            "clients": len(self.clients),
                            "running": self.running
                        }
                        await websocket.send(json.dumps(status, ensure_ascii=False))

                    elif action == 'reset':
                        with self._lock:
                            self.current_frame = 0
                            self.loop_count = 0
                        print("[MocapSim] 重置到第一帧")

                    elif action == 'goto':
                        frame = data.get('frame', 0)
                        if 0 <= frame < len(self.frames):
                            with self._lock:
                                self.current_frame = frame
                            print(f"[MocapSim] 跳转到帧 {frame}")

                except json.JSONDecodeError:
                    pass

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            print(f"[MocapSim] 客户端断开: {client_addr} (剩余: {len(self.clients)})")

    def run_visualizer_vispy(self):
        """使用 vispy 运行高性能可视化"""
        try:
            from vispy import app, scene
            from vispy.scene import visuals
        except ImportError:
            print("[MocapSim] vispy 未安装，尝试使用 pygame...")
            self.run_visualizer_pygame()
            return

        print("[MocapSim] 启动 vispy 3D 可视化窗口...")

        # 创建画布
        canvas = scene.SceneCanvas(keys='interactive', title='MocapSimulator 3D', size=(1000, 700), show=True)
        view = canvas.central_widget.add_view()
        view.camera = scene.TurntableCamera(fov=45, distance=300, elevation=30, azimuth=45)

        # 创建可视化元素
        scatter = visuals.Markers(parent=view.scene)
        lines = []
        for _ in FINGER_CONNECTIONS:
            line = visuals.Line(parent=view.scene, color='black', width=3)
            lines.append(line)

        # 文本标签
        text = visuals.Text(parent=canvas.scene, color='white', font_size=12, anchor_x='left', anchor_y='top')
        text.pos = (10, 20)

        # 颜色映射
        colors_rgb = []
        for c in MARKER_COLORS:
            r = int(c[1:3], 16) / 255
            g = int(c[3:5], 16) / 255
            b = int(c[5:7], 16) / 255
            colors_rgb.append([r, g, b, 1.0])
        colors_rgb = np.array(colors_rgb)

        last_frame = -1

        def update(event):
            nonlocal last_frame
            if not self.running:
                app.quit()
                return

            with self._lock:
                current = self.current_frame

            if current != last_frame:
                last_frame = current
                frame_data = np.array(self.frames[current])

                # 更新散点
                scatter.set_data(frame_data, face_color=colors_rgb, size=15)

                # 更新连接线
                for i, indices in enumerate(FINGER_CONNECTIONS):
                    line_points = np.array([frame_data[idx] for idx in indices])
                    lines[i].set_data(pos=line_points)

                # 计算手势参数
                finger_angle, pinch_dist, palm_angle = calculate_gestures(frame_data)

                # 更新文本
                info = (f"Frame: {current}/{len(self.frames)-1}  |  "
                       f"Time: {self.times[current]:.2f}s  |  Loop: {self.loop_count + 1}\n"
                       f"食指角度: {finger_angle:.1f}°  |  捏合距离: {pinch_dist:.1f}mm  |  翻转角度: {palm_angle:.1f}°")
                text.text = info

        timer = app.Timer(interval=0.03, connect=update, start=True)  # ~30 FPS

        try:
            app.run()
        except Exception:
            pass
        print("[MocapSim] vispy 可视化窗口已关闭")

    def run_visualizer_pygame(self):
        """使用 pygame + OpenGL 运行可视化"""
        try:
            import pygame
            from pygame.locals import DOUBLEBUF, OPENGL, QUIT, MOUSEBUTTONDOWN, MOUSEMOTION
            from OpenGL.GL import (glEnable, glClear, glClearColor, glMatrixMode, glLoadIdentity,
                                   glTranslatef, glRotatef, glBegin, glEnd, glColor3f, glVertex3f,
                                   glPointSize, glLineWidth, GL_DEPTH_TEST, GL_POINT_SMOOTH,
                                   GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT, GL_PROJECTION,
                                   GL_MODELVIEW, GL_POINTS, GL_LINE_STRIP)
            from OpenGL.GLU import gluPerspective
        except ImportError:
            print("[MocapSim] pygame/PyOpenGL 未安装，使用终端输出模式...")
            self.run_visualizer_terminal()
            return

        print("[MocapSim] 启动 pygame+OpenGL 3D 可视化窗口...")

        pygame.init()
        display = (1000, 700)
        pygame.display.set_mode(display, DOUBLEBUF | OPENGL)
        pygame.display.set_caption("MocapSimulator 3D")

        # 计算场景中心
        all_coords = np.array(self.frames)
        center = all_coords.mean(axis=(0, 1))

        # 设置 OpenGL
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_POINT_SMOOTH)
        glPointSize(10)
        glLineWidth(3)

        # 相机参数
        rot_x, rot_y = 30, 45
        zoom = 250
        dragging = False
        last_pos = (0, 0)

        # 颜色
        gl_colors = []
        for c in MARKER_COLORS:
            r = int(c[1:3], 16) / 255
            g = int(c[3:5], 16) / 255
            b = int(c[5:7], 16) / 255
            gl_colors.append((r, g, b))

        clock = pygame.time.Clock()
        last_frame = -1

        while self.running:
            for event in pygame.event.get():
                if event.type == QUIT:
                    self.running = False
                elif event.type == MOUSEBUTTONDOWN:
                    if event.button == 1:
                        dragging = True
                        last_pos = event.pos
                    elif event.button == 4:
                        zoom = max(100, zoom - 20)
                    elif event.button == 5:
                        zoom = min(500, zoom + 20)
                elif event.type == pygame.MOUSEBUTTONUP:
                    if event.button == 1:
                        dragging = False
                elif event.type == MOUSEMOTION and dragging:
                    dx = event.pos[0] - last_pos[0]
                    dy = event.pos[1] - last_pos[1]
                    rot_y += dx * 0.5
                    rot_x += dy * 0.5
                    rot_x = max(-90, min(90, rot_x))
                    last_pos = event.pos

            with self._lock:
                current = self.current_frame

            frame_data = np.array(self.frames[current])

            # 清屏
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glClearColor(0.1, 0.1, 0.15, 1.0)

            # 设置投影
            glMatrixMode(GL_PROJECTION)
            glLoadIdentity()
            gluPerspective(45, display[0]/display[1], 1, 1000)

            # 设置模型视图
            glMatrixMode(GL_MODELVIEW)
            glLoadIdentity()
            glTranslatef(0, 0, -zoom)
            glRotatef(rot_x, 1, 0, 0)
            glRotatef(rot_y, 0, 1, 0)
            glTranslatef(-center[0], -center[1], -center[2])

            # 绘制点
            glBegin(GL_POINTS)
            for i, (x, y, z) in enumerate(frame_data):
                glColor3f(*gl_colors[i])
                glVertex3f(x, y, z)
            glEnd()

            # 绘制连接线
            glColor3f(0.3, 0.3, 0.3)
            for indices in FINGER_CONNECTIONS:
                glBegin(GL_LINE_STRIP)
                for idx in indices:
                    glVertex3f(*frame_data[idx])
                glEnd()

            pygame.display.flip()

            # 计算并打印手势参数（每秒一次）
            if current != last_frame and current % 200 == 0:
                finger_angle, pinch_dist, palm_angle = calculate_gestures(frame_data)
                print(f"[Viz] Frame:{current:>5} | 食指:{finger_angle:>5.1f}° | 捏合:{pinch_dist:>5.1f}mm | 翻转:{palm_angle:>5.1f}°")
            last_frame = current

            clock.tick(60)  # 60 FPS

        pygame.quit()
        print("[MocapSim] pygame 可视化窗口已关闭")

    def run_visualizer_terminal(self):
        """终端输出模式（无图形界面）- 带进度条可视化"""
        print("[MocapSim] 使用终端输出模式...")
        print("-" * 70)
        print("食指角度: [====进度条====] 0°=放下, 90°=抬起")
        print("捏合距离: [====进度条====] 0mm=捏合, 100mm=张开")
        print("翻转角度: [====进度条====] 0°=掌心向下, 180°=掌心向上")
        print("-" * 70)

        last_frame = -1

        def make_bar(value, min_val, max_val, width=20):
            """生成进度条"""
            ratio = (value - min_val) / (max_val - min_val) if max_val > min_val else 0
            ratio = max(0, min(1, ratio))
            filled = int(ratio * width)
            return '█' * filled + '░' * (width - filled)

        while self.running:
            with self._lock:
                current = self.current_frame

            if current != last_frame:  # 每一帧都打印
                last_frame = current
                frame_data = np.array(self.frames[current])
                finger_angle, pinch_dist, palm_angle = calculate_gestures(frame_data)

                # 生成进度条
                finger_bar = make_bar(finger_angle, 0, 90)
                pinch_bar = make_bar(pinch_dist, 0, 100)
                palm_bar = make_bar(palm_angle, 0, 180)

                # 清除上一行并打印新数据（使用 \r 回到行首）
                output = (f"\rFrame:{current:>5} | "
                         f"食指[{finger_bar}]{finger_angle:>5.1f}° | "
                         f"捏合[{pinch_bar}]{pinch_dist:>5.1f}mm | "
                         f"翻转[{palm_bar}]{palm_angle:>5.1f}°")
                print(output, end='', flush=True)

            time.sleep(0.001)  # 减少睡眠时间以跟上200Hz的帧率

        print("\n[MocapSim] 终端输出已停止")

    def run_visualizer(self):
        """运行可视化 - 自动选择最佳方案"""
        # 优先尝试 vispy（最高效）
        try:
            import vispy
            self.run_visualizer_vispy()
            return
        except ImportError:
            pass

        # 其次尝试 pygame + OpenGL
        try:
            import pygame
            from OpenGL.GL import glEnable
            self.run_visualizer_pygame()
            return
        except ImportError:
            pass

        # 最后使用终端输出
        self.run_visualizer_terminal()

    async def start(self):
        """启动模拟器"""
        self.parse_trc_file()

        if not self.frames:
            print("[MocapSim] 错误: 没有加载到任何帧数据")
            return

        self.running = True

        print("=" * 60)
        print("  动捕设备模拟器 (MocapSimulator)")
        print("=" * 60)
        print(f"  WebSocket 端口: ws://localhost:{self.port}")
        print(f"  TRC 文件: {self.trc_file}")
        print(f"  总帧数: {len(self.frames)}")
        print(f"  时长: {self.times[-1]:.2f} 秒")
        print(f"  发送频率: {SAMPLE_RATE} Hz")
        print(f"  帧间隔: {FRAME_INTERVAL*1000:.1f} ms")
        print(f"  单次循环时长: {len(self.frames) * FRAME_INTERVAL:.2f} 秒")
        print(f"  可视化: {'启用' if self.enable_viz else '禁用'}")
        print("=" * 60)
        print("  等待客户端连接...")
        print("  按 Ctrl+C 停止")
        print("=" * 60)

        # 启动可视化线程（始终启动，--no-viz 时使用终端模式）
        viz_thread = None
        if self.enable_viz:
            viz_thread = threading.Thread(target=self.run_visualizer, daemon=True)
            viz_thread.start()
        else:
            # 即使禁用图形可视化，也启动终端输出
            viz_thread = threading.Thread(target=self.run_visualizer_terminal, daemon=True)
            viz_thread.start()

        # 启动 WebSocket 服务器
        server = await websockets.serve(
            self.handle_client,
            "localhost",
            self.port,
            max_size=1 * 1024 * 1024
        )

        # 启动数据发送任务
        sender_task = asyncio.create_task(self.data_sender())

        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        finally:
            self.running = False
            sender_task.cancel()
            server.close()
            await server.wait_closed()
            if viz_thread and viz_thread.is_alive():
                viz_thread.join(timeout=1)
            print("[MocapSim] 服务已停止")


def main():
    parser = argparse.ArgumentParser(description='动捕设备模拟器 (带可视化)')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT,
                        help=f'WebSocket 端口 (默认: {DEFAULT_PORT})')
    parser.add_argument('--file', type=str, default=DEFAULT_TRC_FILE,
                        help=f'TRC 文件路径 (默认: {DEFAULT_TRC_FILE})')
    parser.add_argument('--no-viz', action='store_true',
                        help='禁用可视化窗口')

    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    trc_file = os.path.join(script_dir, args.file)

    if not os.path.exists(trc_file):
        print(f"错误: TRC 文件不存在: {trc_file}")
        sys.exit(1)

    simulator = MocapSimulator(trc_file, args.port, enable_viz=not args.no_viz)

    try:
        asyncio.run(simulator.start())
    except KeyboardInterrupt:
        print("\n[MocapSim] 用户中断")


if __name__ == "__main__":
    main()
