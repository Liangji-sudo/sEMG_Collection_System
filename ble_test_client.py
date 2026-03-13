"""
BLE Server 测试客户端
=====================
用于单独测试 ble_server.py 的数据接收性能

使用方法:
1. 先单独启动 ble_server.py: python ble_server.py
2. 再启动此测试客户端: python ble_test_client.py

此客户端模拟前端的两个WebSocket连接:
- 控制端 (ws://localhost:8764) - 发送控制命令
- 数据端 (ws://localhost:8766) - 接收数据流
"""

import asyncio
import sys
import time
import json
import threading
from datetime import datetime

try:
    import websockets
except ImportError:
    print("请安装 websockets: pip install websockets")
    sys.exit(1)

try:
    import msgpack
except ImportError:
    print("请安装 msgpack: pip install msgpack")
    sys.exit(1)

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QComboBox, QLabel, QGroupBox, QTextEdit, QSplitter,
        QStatusBar
    )
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
except ImportError:
    print("请安装 PyQt5: pip install PyQt5")
    sys.exit(1)

# ================== 配置 ==================
CONTROL_WS_URL = "ws://localhost:8764"
DATA_WS_URL = "ws://localhost:8766"


class SignalEmitter(QObject):
    """用于在异步线程和Qt主线程之间通信"""
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)
    devices_signal = pyqtSignal(list)
    stats_signal = pyqtSignal(dict)


class BleTestClient(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BLE Server 测试客户端")
        self.resize(900, 700)

        # WebSocket连接
        self.control_ws = None
        self.data_ws = None
        self.is_connected = False
        self.is_streaming = False

        # 统计数据
        self.stats = {
            'packets_received': 0,
            'total_frames': 0,
            'lost_frames': 0,
            'last_packet_time': 0,
            'start_time': 0,
            'slow_callbacks': 0,
            'last_callback_time': 0,
        }

        # 设备列表
        self.devices = []

        # 信号发射器
        self.signals = SignalEmitter()
        self.signals.log_signal.connect(self.append_log)
        self.signals.status_signal.connect(self.update_status)
        self.signals.devices_signal.connect(self.update_device_list)
        self.signals.stats_signal.connect(self.update_stats_display)

        # 事件循环
        self.loop = None
        self.async_thread = None

        self.init_ui()
        self.start_async_loop()

        # 定时更新统计
        self.stats_timer = QTimer()
        self.stats_timer.timeout.connect(self.request_stats_update)
        self.stats_timer.start(500)  # 每500ms更新一次

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)

        # ===== 连接控制区 =====
        conn_group = QGroupBox("WebSocket 连接")
        conn_layout = QHBoxLayout()

        self.btn_connect_ws = QPushButton("连接 WebSocket")
        self.btn_connect_ws.clicked.connect(self.connect_websocket)
        conn_layout.addWidget(self.btn_connect_ws)

        self.btn_disconnect_ws = QPushButton("断开 WebSocket")
        self.btn_disconnect_ws.clicked.connect(self.disconnect_websocket)
        self.btn_disconnect_ws.setEnabled(False)
        conn_layout.addWidget(self.btn_disconnect_ws)

        self.lbl_ws_status = QLabel("未连接")
        self.lbl_ws_status.setStyleSheet("color: gray;")
        conn_layout.addWidget(self.lbl_ws_status)
        conn_layout.addStretch()

        conn_group.setLayout(conn_layout)
        main_layout.addWidget(conn_group)

        # ===== 设备控制区 =====
        device_group = QGroupBox("BLE 设备控制")
        device_layout = QVBoxLayout()

        # 扫描和设备选择
        row1 = QHBoxLayout()
        self.btn_scan = QPushButton("扫描设备")
        self.btn_scan.clicked.connect(self.scan_devices)
        self.btn_scan.setEnabled(False)
        row1.addWidget(self.btn_scan)

        self.combo_devices = QComboBox()
        self.combo_devices.setMinimumWidth(300)
        row1.addWidget(self.combo_devices, 1)
        device_layout.addLayout(row1)

        # 连接/断开设备
        row2 = QHBoxLayout()
        self.btn_connect_ble = QPushButton("连接设备")
        self.btn_connect_ble.clicked.connect(self.connect_device)
        self.btn_connect_ble.setEnabled(False)
        row2.addWidget(self.btn_connect_ble)

        self.btn_disconnect_ble = QPushButton("断开设备")
        self.btn_disconnect_ble.clicked.connect(self.disconnect_device)
        self.btn_disconnect_ble.setEnabled(False)
        row2.addWidget(self.btn_disconnect_ble)

        self.lbl_ble_status = QLabel("设备未连接")
        self.lbl_ble_status.setStyleSheet("color: gray;")
        row2.addWidget(self.lbl_ble_status)
        row2.addStretch()
        device_layout.addLayout(row2)

        # 开始/停止采集
        row3 = QHBoxLayout()
        self.btn_start = QPushButton("开始采集")
        self.btn_start.clicked.connect(self.start_stream)
        self.btn_start.setEnabled(False)
        self.btn_start.setStyleSheet("font-weight: bold; font-size: 14px;")
        row3.addWidget(self.btn_start)

        self.btn_stop = QPushButton("停止采集")
        self.btn_stop.clicked.connect(self.stop_stream)
        self.btn_stop.setEnabled(False)
        row3.addWidget(self.btn_stop)
        row3.addStretch()
        device_layout.addLayout(row3)

        device_group.setLayout(device_layout)
        main_layout.addWidget(device_group)

        # ===== 统计信息区 =====
        stats_group = QGroupBox("数据统计 (实时)")
        stats_layout = QHBoxLayout()

        # 左侧: 数据统计
        stats_left = QVBoxLayout()
        self.lbl_packets = QLabel("收到包数: 0")
        self.lbl_packets.setStyleSheet("font-size: 16px; font-weight: bold;")
        stats_left.addWidget(self.lbl_packets)

        self.lbl_frames = QLabel("总帧数: 0")
        stats_left.addWidget(self.lbl_frames)

        self.lbl_lost = QLabel("丢包数: 0 (0.0%)")
        stats_left.addWidget(self.lbl_lost)

        self.lbl_slow = QLabel("慢回调: 0")
        stats_left.addWidget(self.lbl_slow)

        stats_layout.addLayout(stats_left)

        # 右侧: 速率统计
        stats_right = QVBoxLayout()
        self.lbl_rate = QLabel("数据包率: 0 包/秒")
        self.lbl_rate.setStyleSheet("font-size: 16px; font-weight: bold;")
        stats_right.addWidget(self.lbl_rate)

        self.lbl_interval = QLabel("平均包间隔: 0 ms")
        stats_right.addWidget(self.lbl_interval)

        self.lbl_duration = QLabel("采集时长: 0 秒")
        stats_right.addWidget(self.lbl_duration)

        self.lbl_server_stats = QLabel("服务器: -")
        stats_right.addWidget(self.lbl_server_stats)

        stats_layout.addLayout(stats_right)

        stats_group.setLayout(stats_layout)
        main_layout.addWidget(stats_group)

        # ===== 日志区 =====
        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout()

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(200)
        log_layout.addWidget(self.txt_log)

        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(lambda: self.txt_log.clear())
        log_layout.addWidget(btn_clear)

        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪")

    def start_async_loop(self):
        """启动异步事件循环线程"""
        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()

        self.async_thread = threading.Thread(target=run_loop, daemon=True)
        self.async_thread.start()

    def run_async(self, coro):
        """在异步线程中运行协程"""
        if self.loop:
            asyncio.run_coroutine_threadsafe(coro, self.loop)

    def log(self, msg):
        """记录日志"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.signals.log_signal.emit(f"[{timestamp}] {msg}")

    def append_log(self, msg):
        """添加日志到文本框"""
        self.txt_log.append(msg)
        # 滚动到底部
        scrollbar = self.txt_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_status(self, msg):
        """更新状态栏"""
        self.status_bar.showMessage(msg)

    def update_device_list(self, devices):
        """更新设备列表"""
        self.combo_devices.clear()
        self.devices = devices
        for dev in devices:
            name = dev.get('name') or dev.get('display', 'Unknown')
            addr = dev.get('mac', '')
            rssi = dev.get('rssi', '')
            self.combo_devices.addItem(f"{name} ({addr}) [{rssi}dBm]", dev)
        if devices:
            self.btn_connect_ble.setEnabled(True)

    def update_stats_display(self, stats):
        """更新统计显示"""
        packets = stats.get('packets_received', 0)
        frames = stats.get('total_frames', 0)
        lost = stats.get('lost_frames', 0)
        slow = stats.get('slow_callbacks', 0)

        self.lbl_packets.setText(f"收到包数: {packets}")
        self.lbl_frames.setText(f"总帧数: {frames}")

        if frames > 0:
            lost_rate = lost / (frames + lost) * 100 if (frames + lost) > 0 else 0
            self.lbl_lost.setText(f"丢包数: {lost} ({lost_rate:.2f}%)")
        else:
            self.lbl_lost.setText(f"丢包数: {lost}")

        self.lbl_slow.setText(f"慢回调 (>100ms): {slow}")

        # 计算速率
        if stats.get('start_time', 0) > 0:
            duration = time.time() - stats['start_time']
            if duration > 0:
                rate = packets / duration
                self.lbl_rate.setText(f"数据包率: {rate:.1f} 包/秒")
                self.lbl_duration.setText(f"采集时长: {duration:.1f} 秒")

                if packets > 1:
                    avg_interval = duration / packets * 1000
                    self.lbl_interval.setText(f"平均包间隔: {avg_interval:.1f} ms")

        # 服务器统计
        server_total = stats.get('server_total', 0)
        server_lost = stats.get('server_lost', 0)
        if server_total > 0:
            self.lbl_server_stats.setText(f"服务器: {server_total} 帧, 丢包: {server_lost}")

    def request_stats_update(self):
        """请求更新统计信息"""
        if self.is_streaming:
            self.signals.stats_signal.emit(self.stats.copy())

    # ================== WebSocket 操作 ==================

    def connect_websocket(self):
        """连接WebSocket"""
        self.run_async(self._connect_websocket())

    async def _connect_websocket(self):
        """异步连接WebSocket"""
        try:
            self.log("正在连接控制端 WebSocket...")
            self.control_ws = await websockets.connect(CONTROL_WS_URL)
            self.log(f"控制端已连接: {CONTROL_WS_URL}")

            self.log("正在连接数据端 WebSocket...")
            self.data_ws = await websockets.connect(DATA_WS_URL)
            self.log(f"数据端已连接: {DATA_WS_URL}")

            self.is_connected = True
            self.signals.status_signal.emit("WebSocket 已连接")

            # 更新UI状态
            self.btn_connect_ws.setEnabled(False)
            self.btn_disconnect_ws.setEnabled(True)
            self.btn_scan.setEnabled(True)
            self.lbl_ws_status.setText("已连接")
            self.lbl_ws_status.setStyleSheet("color: green;")

            # 启动数据接收任务
            asyncio.create_task(self._receive_control_messages())
            asyncio.create_task(self._receive_data_messages())

        except Exception as e:
            self.log(f"连接失败: {e}")
            self.signals.status_signal.emit(f"连接失败: {e}")

    def disconnect_websocket(self):
        """断开WebSocket"""
        self.run_async(self._disconnect_websocket())

    async def _disconnect_websocket(self):
        """异步断开WebSocket"""
        try:
            if self.control_ws:
                await self.control_ws.close()
            if self.data_ws:
                await self.data_ws.close()

            self.is_connected = False
            self.log("WebSocket 已断开")
            self.signals.status_signal.emit("已断开")

            self.btn_connect_ws.setEnabled(True)
            self.btn_disconnect_ws.setEnabled(False)
            self.btn_scan.setEnabled(False)
            self.btn_connect_ble.setEnabled(False)
            self.btn_disconnect_ble.setEnabled(False)
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.lbl_ws_status.setText("未连接")
            self.lbl_ws_status.setStyleSheet("color: gray;")

        except Exception as e:
            self.log(f"断开失败: {e}")

    async def _receive_control_messages(self):
        """接收控制端消息"""
        try:
            async for message in self.control_ws:
                try:
                    data = json.loads(message)
                    msg_type = data.get('type', '')
                    action = data.get('action', '')

                    # ble_server 返回格式: {'type': 'response', 'action': 'xxx', ...}
                    if msg_type == 'response':
                        if action == 'scan':
                            devices = data.get('devices', [])
                            self.log(f"扫描完成，找到 {len(devices)} 个设备")
                            self.signals.devices_signal.emit(devices)

                        elif action == 'connect1':
                            success = data.get('success', False)
                            if success is True:
                                self.log(f"设备连接成功")
                                self.lbl_ble_status.setText("设备已连接")
                                self.lbl_ble_status.setStyleSheet("color: green;")
                                self.btn_connect_ble.setEnabled(False)
                                self.btn_disconnect_ble.setEnabled(True)
                                self.btn_start.setEnabled(True)
                            elif success is False:
                                self.log(f"设备连接失败: {data.get('error', '')}")
                            else:
                                # success is None 表示连接中
                                self.log(f"设备: {data.get('message', '')}")

                        elif action == 'disconnect1':
                            if data.get('success'):
                                self.log("设备已断开")
                                self.lbl_ble_status.setText("设备未连接")
                                self.lbl_ble_status.setStyleSheet("color: gray;")
                                self.btn_connect_ble.setEnabled(True)
                                self.btn_disconnect_ble.setEnabled(False)
                                self.btn_start.setEnabled(False)
                                self.btn_stop.setEnabled(False)

                        elif action == 'start_all' or action == 'start1':
                            if data.get('success'):
                                self.log("采集已开始")
                                self.is_streaming = True
                                self.btn_start.setEnabled(False)
                                self.btn_stop.setEnabled(True)
                                # 重置统计
                                self.stats = {
                                    'packets_received': 0,
                                    'total_frames': 0,
                                    'lost_frames': 0,
                                    'last_packet_time': 0,
                                    'start_time': time.time(),
                                    'slow_callbacks': 0,
                                    'last_callback_time': 0,
                                }
                            else:
                                self.log(f"启动失败: {data.get('error', '')}")

                        elif action == 'stop_all' or action == 'stop1':
                            if data.get('success'):
                                self.log(f"采集已停止")
                                self.is_streaming = False
                                self.btn_start.setEnabled(True)
                                self.btn_stop.setEnabled(False)

                        elif action == 'status':
                            dev1 = data.get('dev1', {})
                            self.stats['server_total'] = dev1.get('total', 0)
                            self.stats['server_lost'] = dev1.get('lost', 0)

                        elif action == 'welcome':
                            self.log(f"控制端: {data.get('message', '')}")

                        elif action == 'error':
                            self.log(f"错误: {data.get('error', '')}")

                except json.JSONDecodeError:
                    pass

        except websockets.exceptions.ConnectionClosed:
            self.log("控制端连接已关闭")
        except Exception as e:
            self.log(f"控制端接收错误: {e}")

    async def _receive_data_messages(self):
        """接收数据端消息"""
        try:
            async for message in self.data_ws:
                try:
                    now = time.time()

                    # 解析msgpack数据
                    if isinstance(message, bytes):
                        data = msgpack.unpackb(message, raw=False)
                    else:
                        data = json.loads(message)

                    msg_type = data.get('type', '')

                    if msg_type == 'data':
                        self.stats['packets_received'] += 1

                        # 检测回调间隔
                        if self.stats['last_callback_time'] > 0:
                            interval = now - self.stats['last_callback_time']
                            if interval > 0.1:  # 超过100ms
                                self.stats['slow_callbacks'] += 1
                                if self.stats['slow_callbacks'] <= 10 or self.stats['slow_callbacks'] % 50 == 0:
                                    self.log(f"慢回调: {interval*1000:.1f}ms (第{self.stats['slow_callbacks']}次)")
                        self.stats['last_callback_time'] = now

                        # 提取统计信息
                        dev1 = data.get('dev1')
                        if dev1:
                            if isinstance(dev1, list):
                                # 批量数据
                                for pkt in dev1:
                                    s = pkt.get('s', [0, 0])
                                    self.stats['total_frames'] = s[0]
                                    self.stats['lost_frames'] = s[1]
                            else:
                                s = dev1.get('s', [0, 0])
                                self.stats['total_frames'] = s[0]
                                self.stats['lost_frames'] = s[1]

                        # 每100包打印一次
                        if self.stats['packets_received'] % 100 == 0:
                            lost_rate = self.stats['lost_frames'] / (self.stats['total_frames'] + self.stats['lost_frames']) * 100 if self.stats['total_frames'] > 0 else 0
                            self.log(f"已收到 {self.stats['packets_received']} 包, "
                                   f"帧: {self.stats['total_frames']}, "
                                   f"丢包: {self.stats['lost_frames']} ({lost_rate:.2f}%), "
                                   f"慢回调: {self.stats['slow_callbacks']}")

                except Exception as e:
                    pass

        except websockets.exceptions.ConnectionClosed:
            self.log("数据端连接已关闭")
        except Exception as e:
            self.log(f"数据端接收错误: {e}")

    # ================== BLE 设备操作 ==================

    def scan_devices(self):
        """扫描设备"""
        self.run_async(self._send_command("scan"))
        self.log("正在扫描设备...")

    def connect_device(self):
        """连接选中的设备"""
        idx = self.combo_devices.currentIndex()
        if idx >= 0:
            dev = self.combo_devices.itemData(idx)
            mac = dev.get('mac', '')
            # 发送connect1命令（连接设备1），需要传递 mac 地址
            self.run_async(self._send_command("connect1", {"mac": mac}))
            self.log(f"正在连接设备: {mac}")

    def disconnect_device(self):
        """断开设备"""
        self.run_async(self._send_command("disconnect1"))
        self.log("正在断开设备...")

    def start_stream(self):
        """开始采集"""
        # 先设置session_id
        self.run_async(self._send_command("set_session_id", {"session_id": "TEST"}))
        # 然后开始采集
        self.run_async(self._send_command("start_all"))
        self.log("正在开始采集...")

    def stop_stream(self):
        """停止采集"""
        self.run_async(self._send_command("stop_all"))
        self.log("正在停止采集...")

    async def _send_command(self, action, params=None):
        """发送控制命令"""
        if self.control_ws:
            msg = {"action": action}
            if params:
                msg.update(params)
            await self.control_ws.send(json.dumps(msg))

    def closeEvent(self, event):
        """关闭窗口时清理"""
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    window = BleTestClient()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
