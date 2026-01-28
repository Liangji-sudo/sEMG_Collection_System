"""
动捕数据接收测试客户端
======================
用于测试 mocap_simulator.py 是否正常工作

使用方法：
    1. 先启动模拟器: python mocap_simulator.py
    2. 再运行此脚本: python mocap_client_demo.py

三种连续手势定义：
- 连续手势1 (食指上抬): ri1-ri2-ri3 拟合直线与 m1-m2-m3 平面的夹角 (0°=垂直, 90°=平行)
- 连续手势2 (拇指食指捏合): rt1 与 ri1 的距离
- 连续手势3 (手掌翻转): m1-m2-m3 平面与水平面的夹角 (0°=掌心向下)
"""

import asyncio
import json
import sys
import math
import numpy as np

try:
    import websockets
except ImportError:
    print("请安装 websockets: pip install websockets")
    sys.exit(1)

# 配置
MOCAP_SERVER_URL = "ws://localhost:8768"


def fit_line_direction(p1, p2, p3):
    """
    用三个点拟合直线，返回方向向量（单位向量）
    使用 p1 到 p3 的方向作为主方向
    """
    p1, p2, p3 = np.array(p1), np.array(p2), np.array(p3)
    # 使用指尖到指根的方向
    direction = p3 - p1
    norm = np.linalg.norm(direction)
    if norm < 1e-6:
        return np.array([0, 0, 1])
    return direction / norm


def fit_plane_normal(p1, p2, p3):
    """
    用三个点拟合平面，返回法向量（单位向量）
    法向量方向：从掌心指向手背
    """
    p1, p2, p3 = np.array(p1), np.array(p2), np.array(p3)
    v1 = p2 - p1
    v2 = p3 - p1
    normal = np.cross(v1, v2)
    norm = np.linalg.norm(normal)
    if norm < 1e-6:
        return np.array([0, 0, 1])
    return normal / norm


def calculate_line_plane_angle(line_dir, plane_normal):
    """
    计算直线与平面的夹角（度）
    返回 0° 表示直线垂直于平面，90° 表示直线平行于平面
    """
    # 直线与法向量的夹角的余角就是直线与平面的夹角
    cos_angle = abs(np.dot(line_dir, plane_normal))
    # 限制在 [-1, 1] 范围内
    cos_angle = np.clip(cos_angle, -1, 1)
    # 直线与法向量的夹角
    angle_with_normal = math.degrees(math.acos(cos_angle))
    # 直线与平面的夹角 = 90° - 与法向量的夹角
    angle_with_plane = 90 - angle_with_normal
    return angle_with_plane


def calculate_plane_horizontal_angle(plane_normal):
    """
    计算平面与水平面的夹角（度）
    水平面法向量为 [0, 0, 1]（假设 Z 轴向上）
    返回 0° 表示掌心向下，90° 表示手掌竖直，180° 表示掌心向上
    """
    horizontal_normal = np.array([0, 0, 1])
    cos_angle = np.dot(plane_normal, horizontal_normal)
    cos_angle = np.clip(cos_angle, -1, 1)
    angle = math.degrees(math.acos(cos_angle))
    return angle


def calculate_distance(p1, p2):
    """计算两点之间的距离"""
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(p1, p2)))


def analyze_gestures(markers):
    """
    分析三种手势
    返回: (食指角度, 捏合距离, 翻转角度)
    """
    # 获取关键点
    rt1 = np.array(markers.get("rt1", [0, 0, 0]))  # 拇指指尖
    ri1 = np.array(markers.get("ri1", [0, 0, 0]))  # 食指指尖
    ri2 = np.array(markers.get("ri2", [0, 0, 0]))  # 食指第一关节
    ri3 = np.array(markers.get("ri3", [0, 0, 0]))  # 食指第二关节
    m1 = np.array(markers.get("m1", [0, 0, 0]))    # 食指指根
    m2 = np.array(markers.get("m2", [0, 0, 0]))    # 中指指根
    m3 = np.array(markers.get("m3", [0, 0, 0]))    # 腕部

    # 手势1: 食指上抬角度
    # 食指方向向量 (从指尖到指根)
    index_dir = fit_line_direction(ri1, ri2, ri3)
    # 手掌平面法向量
    palm_normal = fit_plane_normal(m1, m2, m3)
    # 食指与手掌平面的夹角
    index_angle = calculate_line_plane_angle(index_dir, palm_normal)

    # 手势2: 拇指食指捏合距离
    pinch_distance = calculate_distance(rt1, ri1)

    # 手势3: 手掌翻转角度
    palm_angle = calculate_plane_horizontal_angle(palm_normal)

    return index_angle, pinch_distance, palm_angle


async def receive_mocap_data():
    """接收动捕数据"""
    print("=" * 70)
    print("  动捕数据接收测试客户端 - 三种手势检测")
    print("=" * 70)
    print(f"  连接到: {MOCAP_SERVER_URL}")
    print("-" * 70)
    print("  手势1 (食指上抬): 0°=垂直手掌, 90°=平行手掌")
    print("  手势2 (捏合距离): 距离越小=捏合, 距离越大=张开")
    print("  手势3 (手掌翻转): 0°=掌心向下, 90°=手掌竖直, 180°=掌心向上")
    print("=" * 70)

    frame_count = 0

    try:
        async with websockets.connect(MOCAP_SERVER_URL) as ws:
            print("[Client] ✅ 已连接到动捕模拟器")

            async for message in ws:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type", "")

                    if msg_type == "welcome":
                        print(f"[Client] 收到欢迎消息:")
                        print(f"         总帧数: {data.get('total_frames')}")
                        print(f"         时长: {data.get('duration'):.2f} 秒")
                        print("-" * 70)
                        print("开始接收数据... (按 Ctrl+C 停止)")
                        print("-" * 70)
                        print(f"{'Frame':>6} | {'Time':>6} | {'食指角度':>8} | {'捏合距离':>8} | {'翻转角度':>8} | 检测到的手势")
                        print("-" * 70)

                    elif msg_type == "mocap_data":
                        frame_count += 1
                        frame = data.get("frame", 0)
                        time_val = data.get("time", 0)
                        markers = data.get("markers", {})

                        # 分析三种手势
                        index_angle, pinch_dist, palm_angle = analyze_gestures(markers)

                        # 每 40 帧打印一次（约 200ms）
                        if frame_count % 40 == 0:
                            # 判断当前主要手势
                            gestures = []

                            # 食指上抬检测 (角度 > 30° 认为在抬起)
                            if index_angle > 45:
                                gestures.append(f"食指抬起({index_angle:.0f}°)")
                            elif index_angle < 15:
                                gestures.append(f"食指放下({index_angle:.0f}°)")

                            # 捏合检测
                            if pinch_dist < 40:
                                gestures.append(f"捏合✊")
                            elif pinch_dist > 90:
                                gestures.append(f"张开✋")

                            # 翻转检测
                            if palm_angle < 30:
                                gestures.append("掌心↓")
                            elif palm_angle > 150:
                                gestures.append("掌心↑")
                            elif 60 < palm_angle < 120:
                                gestures.append("手掌竖直")

                            gesture_str = ", ".join(gestures) if gestures else "过渡中..."

                            print(f"{frame:>6} | {time_val:>5.2f}s | {index_angle:>6.1f}° | {pinch_dist:>6.1f}mm | {palm_angle:>6.1f}° | {gesture_str}")

                except json.JSONDecodeError as e:
                    print(f"[Client] JSON 解析错误: {e}")

    except websockets.exceptions.ConnectionRefused:
        print(f"[Client] ❌ 无法连接到 {MOCAP_SERVER_URL}")
        print("         请确保 mocap_simulator.py 已启动")
    except websockets.exceptions.ConnectionClosed:
        print("[Client] 连接已关闭")
    except Exception as e:
        print(f"[Client] 错误: {e}")


async def interactive_client():
    """交互式客户端，可发送控制命令"""
    print("=" * 60)
    print("  动捕数据交互式客户端")
    print("=" * 60)
    print(f"  连接到: {MOCAP_SERVER_URL}")
    print("=" * 60)

    try:
        async with websockets.connect(MOCAP_SERVER_URL) as ws:
            print("[Client] ✅ 已连接到动捕模拟器")
            print("\n可用命令:")
            print("  status - 获取状态")
            print("  reset  - 重置到第一帧")
            print("  goto N - 跳转到第 N 帧")
            print("  quit   - 退出")
            print("-" * 60)

            # 启动接收任务
            async def receiver():
                frame_count = 0
                async for message in ws:
                    try:
                        data = json.loads(message)
                        msg_type = data.get("type", "")

                        if msg_type == "welcome":
                            print(f"[收到] 欢迎消息: {data.get('message')}")

                        elif msg_type == "status":
                            print(f"[状态] 当前帧: {data.get('current_frame')}, "
                                  f"循环次数: {data.get('loop_count')}, "
                                  f"客户端数: {data.get('clients')}")

                        elif msg_type == "mocap_data":
                            frame_count += 1
                            if frame_count % 100 == 0:
                                markers = data.get("markers", {})
                                index_angle, pinch_dist, palm_angle = analyze_gestures(markers)
                                print(f"[数据] Frame {data.get('frame'):>5} | "
                                      f"食指:{index_angle:>5.1f}° | "
                                      f"捏合:{pinch_dist:>5.1f}mm | "
                                      f"翻转:{palm_angle:>5.1f}°")

                    except json.JSONDecodeError:
                        pass

            # 启动接收协程
            recv_task = asyncio.create_task(receiver())

            # 命令输入循环
            try:
                while True:
                    cmd = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: input("\n> ").strip().lower()
                    )

                    if cmd == "quit" or cmd == "q":
                        break
                    elif cmd == "status":
                        await ws.send(json.dumps({"action": "status"}))
                    elif cmd == "reset":
                        await ws.send(json.dumps({"action": "reset"}))
                        print("[发送] 重置命令")
                    elif cmd.startswith("goto "):
                        try:
                            frame = int(cmd.split()[1])
                            await ws.send(json.dumps({"action": "goto", "frame": frame}))
                            print(f"[发送] 跳转到帧 {frame}")
                        except (ValueError, IndexError):
                            print("用法: goto N (N 为帧号)")
                    elif cmd:
                        print("未知命令")

            finally:
                recv_task.cancel()

    except websockets.exceptions.ConnectionRefused:
        print(f"[Client] ❌ 无法连接到 {MOCAP_SERVER_URL}")
        print("         请确保 mocap_simulator.py 已启动")
    except Exception as e:
        print(f"[Client] 错误: {e}")


def main():
    print("\n选择模式:")
    print("  [1] 简单接收模式 - 显示三种手势数据")
    print("  [2] 交互模式 - 可发送控制命令")
    print()

    choice = input("请选择 (1/2, 默认 1): ").strip()

    if choice == "2":
        asyncio.run(interactive_client())
    else:
        try:
            asyncio.run(receive_mocap_data())
        except KeyboardInterrupt:
            print("\n[Client] 用户中断")


if __name__ == "__main__":
    main()

