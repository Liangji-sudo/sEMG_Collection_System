"""
测试 camera_server 的配置和录制功能
"""
import asyncio
import websockets
import json
import sys

# 设置输出编码
sys.stdout.reconfigure(encoding='utf-8')

async def test_camera_server():
    uri = "ws://localhost:8768"

    try:
        async with websockets.connect(uri) as websocket:
            print("[OK] 已连接到 camera_server")

            # 1. 枚举摄像头
            print("\n[1] 枚举摄像头设备...")
            await websocket.send(json.dumps({
                'command': 'list_cameras'
            }))
            response = await websocket.recv()
            result = json.loads(response)
            print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")

            if result.get('success') and result.get('devices'):
                devices = result['devices']
                print(f"\n找到 {len(devices)} 个摄像头:")
                for i, dev in enumerate(devices):
                    print(f"  [{i}] {dev['name']}")

                if len(devices) > 0:
                    # 2. 配置第一个摄像头为左侧
                    first_camera = devices[0]
                    print(f"\n[2] 配置左侧摄像头: {first_camera['name']}")
                    await websocket.send(json.dumps({
                        'command': 'set_camera',
                        'side': 'left',
                        'device_name': first_camera['name'],
                        'device_id': first_camera['id']
                    }))
                    response = await websocket.recv()
                    result = json.loads(response)
                    print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")

                    # 3. 获取状态
                    print("\n[3] 获取状态...")
                    await websocket.send(json.dumps({
                        'command': 'get_status'
                    }))
                    response = await websocket.recv()
                    result = json.loads(response)
                    print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")

                    # 4. 开始录制
                    print("\n[4] 开始录制...")
                    await websocket.send(json.dumps({
                        'command': 'start_recording',
                        'side': 'left',
                        'output_filename': 'test_video.mp4'
                    }))
                    response = await websocket.recv()
                    result = json.loads(response)
                    print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")

                    if result.get('success'):
                        print("\n[WAIT] 录制5秒...")
                        await asyncio.sleep(5)

                        # 5. 停止录制
                        print("\n[5] 停止录制...")
                        await websocket.send(json.dumps({
                            'command': 'stop_recording',
                            'side': 'left'
                        }))
                        response = await websocket.recv()
                        result = json.loads(response)
                        print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")

                        print("\n[OK] 测试完成！检查 storage/video/ 目录是否有 test_video.mp4")
                    else:
                        print(f"[ERROR] 录制启动失败: {result.get('error')}")
                else:
                    print("[ERROR] 没有找到摄像头设备")
            else:
                print(f"[ERROR] 枚举失败: {result.get('error')}")

    except Exception as e:
        print(f"[ERROR] 测试失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(test_camera_server())
