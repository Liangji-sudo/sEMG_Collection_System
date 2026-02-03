#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mocap_server_test.py - 连续手势3角度测试工具
=============================================
单独运行，实时显示手掌翻转角度的进度条

使用方法：
    python mocap_server_test.py -s 10.1.1.198
"""

import sys
import io
import time
import math
import threading

# 编码配置
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

try:
    import numpy as np
except ImportError:
    print("错误: 请安装 numpy: pip install numpy", file=sys.stderr)
    sys.exit(1)


# ==================== 配置 ====================
NOKOV_SERVER_IP = "10.1.1.198"
MARKER_NAMES = ['rt1', 'rt2', 'rt3', 'ri1', 'ri2', 'ri3', 'rm1', 'rm2', 'rm3', 'm1', 'm2', 'm3']


# ==================== 手势计算函数 ====================

# 用于保存上一次有效的角度
_last_valid_angle = 90.0  # 默认中间值
_last_valid_raw_angle = 0.0
_last_valid_x = 0.0
_last_valid_z = 0.0
_last_valid_rt2 = np.array([0, 0, 0])
_last_valid_ri2 = np.array([0, 0, 0])

INVALID_COORD_THRESHOLD = 100000  # 超过这个值认为是无效坐标


def is_valid_marker(marker):
    """检查 marker 坐标是否有效（不是 9999999 之类的异常值）"""
    return all(abs(v) < INVALID_COORD_THRESHOLD for v in marker)


def calculate_palm_rotation_angle(markers):
    """计算手掌翻转角度（连续手势3）"""
    global _last_valid_angle, _last_valid_raw_angle, _last_valid_x, _last_valid_z
    global _last_valid_rt2, _last_valid_ri2

    rt2 = np.array(markers.get("rt2", [0, 0, 0]))
    ri2 = np.array(markers.get("ri2", [0, 0, 0]))

    # 检查坐标是否有效
    if not is_valid_marker(rt2) or not is_valid_marker(ri2):
        # 返回上一次有效的值
        return (_last_valid_angle, _last_valid_raw_angle, _last_valid_x,
                _last_valid_z, _last_valid_rt2, _last_valid_ri2, False)

    vec = ri2 - rt2
    x_component = vec[0]
    z_component = vec[2]

    raw_angle = math.degrees(math.atan2(x_component, z_component))

    angle = (raw_angle + 360) % 360
    if angle > 180:
        angle = 360 - angle

    # 保存有效值
    _last_valid_angle = angle
    _last_valid_raw_angle = raw_angle
    _last_valid_x = x_component
    _last_valid_z = z_component
    _last_valid_rt2 = rt2
    _last_valid_ri2 = ri2

    return angle, raw_angle, x_component, z_component, rt2, ri2, True


def print_progress_bar(angle, raw_angle, x, z, rt2, ri2, is_valid=True):
    """打印进度条形式的角度显示"""
    bar_width = 50

    # 角度范围 0-180，映射到进度条
    progress = angle / 180.0
    filled = int(bar_width * progress)
    empty = bar_width - filled

    # 构建进度条
    bar = '█' * filled + '░' * empty

    # 状态指示
    status = "正常" if is_valid else "保持 (marker丢失)"
    status_color = "" if is_valid else " [!]"

    # 清屏并打印
    print('\033[2J\033[H', end='')  # 清屏并移动光标到左上角

    print("=" * 60)
    print("  连续手势3 - 手掌翻转角度测试")
    print("=" * 60)
    print()
    print(f"  状态: {status}{status_color}")
    print()
    print(f"  rt2: [{rt2[0]:>8.2f}, {rt2[1]:>8.2f}, {rt2[2]:>8.2f}]")
    print(f"  ri2: [{ri2[0]:>8.2f}, {ri2[1]:>8.2f}, {ri2[2]:>8.2f}]")
    print()
    print(f"  向量 X分量: {x:>8.2f}")
    print(f"  向量 Z分量: {z:>8.2f}")
    print()
    print(f"  atan2原始角度: {raw_angle:>8.2f}°")
    print()
    print("-" * 60)
    print()
    print(f"  最终角度: {angle:>6.1f}° / 180°")
    print()
    print(f"  0° [{bar}] 180°")
    print()
    print(f"  掌心向下 ←――――――――――――――――――――――→ 掌心向上")
    print()
    print("-" * 60)
    print("  按 Ctrl+C 退出")
    print()


# ==================== Nokov SDK 接收器 ====================

class NokovSDKReceiver:
    def __init__(self, server_ip):
        self.server_ip = server_ip
        self.sdk_client = None
        self.connected = False
        self.latest_markers = {}
        self.latest_frame = 0
        self._lock = threading.Lock()

    def _data_callback(self, pFrameOfMocapData, pUserData):
        if pFrameOfMocapData is None:
            return

        try:
            frameData = pFrameOfMocapData.contents
            frame_no = frameData.iFrame

            if frame_no == self.latest_frame:
                return

            self.latest_frame = frame_no
            markers = {}
            marker_index = 0

            for iMarkerSet in range(frameData.nMarkerSets):
                markerset = frameData.MocapData[iMarkerSet]
                for iMarker in range(markerset.nMarkers):
                    if marker_index < len(MARKER_NAMES):
                        marker_name = MARKER_NAMES[marker_index]
                        x = markerset.Markers[iMarker][0]
                        y = markerset.Markers[iMarker][1]
                        z = markerset.Markers[iMarker][2]
                        markers[marker_name] = [x, y, z]
                        marker_index += 1

            with self._lock:
                self.latest_markers = markers

        except Exception as e:
            print(f"数据回调错误: {e}")

    def connect(self):
        try:
            from nokov.nokovsdk import PySDKClient

            print(f"正在连接到 Nokov 服务器 {self.server_ip}...")

            self.sdk_client = PySDKClient()
            ver = self.sdk_client.PyNokovVersion()
            print(f"SDK版本: {ver[0]}.{ver[1]}.{ver[2]}.{ver[3]}")

            ret = self.sdk_client.Initialize(bytes(self.server_ip, encoding="utf8"))

            if ret == 0:
                print("连接成功!")
                self.connected = True
                self.sdk_client.PySetDataCallback(self._data_callback, None)
                self.sdk_client.PySetVerbosityLevel(0)
                return True
            else:
                print(f"连接失败，错误码: {ret}")
                return False

        except ImportError:
            print("错误: 未安装 nokov SDK")
            return False
        except Exception as e:
            print(f"连接错误: {e}")
            return False

    def get_markers(self):
        with self._lock:
            return self.latest_markers.copy()

    def disconnect(self):
        self.sdk_client = None
        self.connected = False


# ==================== 主程序 ====================

def main():
    import argparse

    parser = argparse.ArgumentParser(description='连续手势3角度测试工具')
    parser.add_argument('--server', '-s', default=NOKOV_SERVER_IP, help='Nokov服务器IP地址')
    args = parser.parse_args()

    receiver = NokovSDKReceiver(args.server)

    if not receiver.connect():
        print("无法连接到动捕服务器，退出")
        sys.exit(1)

    print("连接成功，开始显示角度...")
    time.sleep(1)

    try:
        while True:
            markers = receiver.get_markers()

            if markers and 'rt2' in markers and 'ri2' in markers:
                angle, raw_angle, x, z, rt2, ri2, is_valid = calculate_palm_rotation_angle(markers)
                print_progress_bar(angle, raw_angle, x, z, rt2, ri2, is_valid)
            else:
                print('\033[2J\033[H', end='')
                print("等待动捕数据...")
                print(f"当前 markers: {list(markers.keys()) if markers else '无'}")

            time.sleep(0.05)  # 20Hz 刷新率

    except KeyboardInterrupt:
        print("\n\n退出测试")
    finally:
        receiver.disconnect()


if __name__ == '__main__':
    main()
