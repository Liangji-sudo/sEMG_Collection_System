import h5py
import zmq
import json
import os
import sys
import io
from datetime import datetime
import numpy as np

# ================= 基础配置（解决编码和超时问题）=================
# 强制stdout/stderr使用UTF-8编码（解决中文乱码）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# 调试日志函数
def debug_log(message):
    print(f"[ble_server] {message}\n", file=sys.stderr)

class HDF5StorageServer:
    def __init__(self, host="127.0.0.1", port=5555):
        # 初始化 ZeroMQ 上下文和套接字
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REP)
        self.socket.bind(f"tcp://{host}:{port}")
        debug_log(f"HDF5 存储服务已启动，监听 {host}:{port}")

        # HDF5 文件相关变量
        self.h5_file = None
        self.h5_group = None
        self.file_path = None
        self.datasets = {}  # 存储已创建的数据集 {dataset_name: dataset_obj}

    def handle_create(self, params):
        """处理创建 HDF5 文件指令"""
        try:
            # 获取参数（支持自定义文件名、组名）
            file_name = params.get("file_name", f"sensor_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.h5")
            group_name = params.get("group_name", "sensor_data")
            self.file_path = os.path.join(os.getcwd(), file_name)

            # 检查文件是否已存在
            if os.path.exists(self.file_path):
                return {"status": "error", "msg": f"文件 {self.file_path} 已存在"}

            # 创建 HDF5 文件和根组
            self.h5_file = h5py.File(self.file_path, "w")
            self.h5_group = self.h5_file.create_group(group_name)
            debug_log(f"成功创建 HDF5 文件：{self.file_path}，组：{group_name}")

            return {
                "status": "success",
                "msg": f"创建文件成功：{self.file_path}",
                "file_path": self.file_path,
                "group_name": group_name
            }
        except Exception as e:
            return {"status": "error", "msg": f"创建文件失败：{str(e)}"}

    def handle_write(self, params):
        """处理写入数据指令"""
        try:
            if not self.h5_file or not self.h5_group:
                return {"status": "error", "msg": "请先创建 HDF5 文件"}

            # 获取写入参数
            dataset_name = params.get("dataset_name", "sensor_0")
            data = params.get("data")
            dtype = params.get("dtype", "float64")

            if data is None:
                return {"status": "error", "msg": "写入数据不能为空"}

            # 转换数据为 numpy 数组
            try:
                data_array = np.array(data, dtype=dtype)
            except Exception as e:
                return {"status": "error", "msg": f"数据转换失败：{str(e)}"}

            # 创建数据集（首次写入）或追加数据
            if dataset_name not in self.datasets:
                # 动态扩展的数据集（maxshape=(None,) 表示一维数据可追加）
                self.datasets[dataset_name] = self.h5_group.create_dataset(
                    name=dataset_name,
                    shape=(0,),
                    maxshape=(None,),
                    dtype=dtype
                )
                debug_log(f"创建数据集：{dataset_name}")

            # 追加数据
            dataset = self.datasets[dataset_name]
            current_len = dataset.shape[0]
            new_len = current_len + len(data_array)
            dataset.resize(new_len, axis=0)
            dataset[current_len:new_len] = data_array

            return {
                "status": "success",
                "msg": f"写入 {len(data_array)} 条数据到 {dataset_name}",
                "dataset_name": dataset_name,
                "total_count": new_len
            }
        except Exception as e:
            return {"status": "error", "msg": f"写入数据失败：{str(e)}"}

    def handle_close(self, params):
        """处理关闭保存文件指令"""
        try:
            if self.h5_file:
                self.h5_file.close()
                self.h5_file = None
                self.h5_group = None
                self.datasets = {}
                msg = f"文件已保存并关闭：{self.file_path}"
                debug_log(msg)
                return {"status": "success", "msg": msg, "file_path": self.file_path}
            else:
                return {"status": "warning", "msg": "无已打开的 HDF5 文件"}
        except Exception as e:
            return {"status": "error", "msg": f"关闭文件失败：{str(e)}"}

    def run(self):
        """启动服务，循环处理客户端请求"""
        try:
            while True:
                # 接收客户端请求（JSON 格式）
                request = self.socket.recv_json()
                debug_log(f"\n收到请求：{request}")

                # 解析指令类型和参数
                cmd = request.get("cmd")
                params = request.get("params", {})

                # 处理不同指令
                if cmd == "create":
                    response = self.handle_create(params)
                elif cmd == "write":
                    response = self.handle_write(params)
                elif cmd == "close":
                    response = self.handle_close(params)
                else:
                    response = {"status": "error", "msg": f"未知指令：{cmd}，支持的指令：create/write/close"}

                # 发送响应给客户端
                self.socket.send_json(response)
        except KeyboardInterrupt:
            debug_log("\n服务正在关闭...")
        finally:
            # 清理资源
            if self.h5_file:
                self.h5_file.close()
            self.socket.close()
            self.context.term()
            debug_log("服务已关闭")

if __name__ == "__main__":
    server = HDF5StorageServer()
    server.run()
