import h5py
import zmq
import json
import os
import sys
import io
from datetime import datetime
import numpy as np
from threading import Lock
import pandas as pd

# ================= 基础配置（解决编码和超时问题）=================
# 强制stdout/stderr使用UTF-8编码（解决中文乱码）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# ===================== 核心配置（完全匹配Meta格式） =====================
# Data数据集
DATA_DTYPE = np.dtype([("emg", "<f4", (16,)), ("time", "<f8")])
TASK_ATTR = "discrete_gestures"

# 字符串存储配置（Meta格式：VLARRAY object类型）
STR_VLEN_DTYPE = h5py.special_dtype(vlen=str)  # 变长字符串（Meta格式）
# 备用：固定长度字符串（兼容fallback）
STR_FIXED_LEN = 64
STR_FIXED_DTYPE = f"S{STR_FIXED_LEN}"

# 调试日志函数
def debug_log(message):
    print(f"[storage_server] {message}\n", file=sys.stderr)

class HDF5StorageServer:
    def __init__(self, host="127.0.0.1", port=5555):
        # 初始化 ZeroMQ 上下文和套接字
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REP)
        self.socket.bind(f"tcp://{host}:{port}")
        debug_log(f"HDF5 存储服务已启动，监听 {host}:{port}")

        # HDF5 文件相关变量
        self.file_path = None  # 文件名
        self.overwrite = True
        self.f = None          # 文件句柄
        self.lock = Lock()

    def create_and_open(self, params):
        """创建完全匹配Meta格式的HDF5结构并保持文件打开"""
        self.file_path = params.get("file_name")

        if self.overwrite and os.path.exists(self.file_path):
            os.remove(self.file_path)
        
        try:
            self.f = h5py.File(self.file_path, "a", libver='latest')
            
            if "data" not in self.f:
                # ===================== 1. 创建Data数据集（完全匹配Meta） =====================
                data_ds = self.f.create_dataset(
                    "data", shape=(0,), dtype=DATA_DTYPE,
                    chunks=(10000,), maxshape=(None,), compression=None
                )
                # Data数据集核心属性（完全匹配Meta）
                data_ds.attrs["task"] = TASK_ATTR
                data_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                data_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                data_ds.attrs["TITLE"] = np.bytes_(b'')
                data_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                data_ds.attrs["transposed"] = np.uint8(0)

                # ===================== 2. 创建Prompts组（完全匹配Meta格式） =====================
                prompts = self.f.create_group("prompts")

                # 2.1 prompts/axis0（列名轴：name/time）- Meta格式
                axis0_data = np.array([b"name", b"time"], dtype="S4")
                axis0_ds = prompts.create_dataset(
                    "axis0", data=axis0_data, dtype="S4"
                )
                # Meta格式完整属性
                axis0_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                axis0_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                axis0_ds.attrs["TITLE"] = np.bytes_(b'')
                axis0_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                axis0_ds.attrs["kind"] = np.bytes_(b'string')
                axis0_ds.attrs["name"] = np.bytes_(b'N.')
                axis0_ds.attrs["transposed"] = np.uint8(1)

                # 2.2 prompts/axis1（行索引轴）- Meta格式
                axis1_ds = prompts.create_dataset(
                    "axis1", shape=(0,), dtype=np.int64, 
                    chunks=(1000,), maxshape=(None,)
                )
                axis1_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                axis1_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                axis1_ds.attrs["TITLE"] = np.bytes_(b'')
                axis1_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                axis1_ds.attrs["kind"] = np.bytes_(b'integer')
                axis1_ds.attrs["name"] = np.bytes_(b'N.')
                axis1_ds.attrs["transposed"] = np.uint8(1)

                # 2.3 prompts/block0_items（name列标识）- Meta特有
                block0_items_ds = prompts.create_dataset(
                    "block0_items", data=np.array([b"name"], dtype="S4"), dtype="S4"
                )
                block0_items_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                block0_items_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                block0_items_ds.attrs["TITLE"] = np.bytes_(b'')
                block0_items_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                block0_items_ds.attrs["kind"] = np.bytes_(b'string')
                block0_items_ds.attrs["name"] = np.bytes_(b'N.')
                block0_items_ds.attrs["transposed"] = np.uint8(1)

                # 2.4 prompts/block0_values（name列值）- Meta核心：VLARRAY object类型
                block0_values_ds = prompts.create_dataset(
                    "block0_values", shape=(0,), dtype=STR_VLEN_DTYPE,
                    chunks=(1000,), maxshape=(None,)
                )
                block0_values_ds.attrs["CLASS"] = np.bytes_(b'VLARRAY')
                block0_values_ds.attrs["PSEUDOATOM"] = np.bytes_(b'object')
                block0_values_ds.attrs["TITLE"] = np.bytes_(b'')
                block0_values_ds.attrs["VERSION"] = np.bytes_(b'1.4')
                block0_values_ds.attrs["transposed"] = np.uint8(1)

                # 2.5 prompts/block1_items（time列标识）- Meta特有
                block1_items_ds = prompts.create_dataset(
                    "block1_items", data=np.array([b"time"], dtype="S4"), dtype="S4"
                )
                block1_items_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                block1_items_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                block1_items_ds.attrs["TITLE"] = np.bytes_(b'')
                block1_items_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                block1_items_ds.attrs["kind"] = np.bytes_(b'string')
                block1_items_ds.attrs["name"] = np.bytes_(b'N.')
                block1_items_ds.attrs["transposed"] = np.uint8(1)

                # 2.6 prompts/block1_values（time列值）- Meta格式
                block1_values_ds = prompts.create_dataset(
                    "block1_values", shape=(0, 1), dtype=np.float64,
                    chunks=(1000, 1), maxshape=(None, 1)
                )
                block1_values_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                block1_values_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                block1_values_ds.attrs["TITLE"] = np.bytes_(b'')
                block1_values_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                block1_values_ds.attrs["kind"] = np.bytes_(b'float')
                block1_values_ds.attrs["name"] = np.bytes_(b'N.')
                block1_values_ds.attrs["transposed"] = np.uint8(1)

                # ===================== 3. 创建Stages组（完全匹配Meta格式） =====================
                stages = self.f.create_group("stages")

                # 3.1 stages/axis0（列名轴：start/end/name）- Meta格式
                stages_axis0_data = np.array([b"start", b"end", b"name"], dtype="S5")
                stages_axis0_ds = stages.create_dataset(
                    "axis0", data=stages_axis0_data, dtype="S5"
                )
                stages_axis0_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                stages_axis0_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                stages_axis0_ds.attrs["TITLE"] = np.bytes_(b'')
                stages_axis0_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                stages_axis0_ds.attrs["kind"] = np.bytes_(b'string')
                stages_axis0_ds.attrs["name"] = np.bytes_(b'N.')
                stages_axis0_ds.attrs["transposed"] = np.uint8(1)

                # 3.2 stages/axis1（行索引轴）- Meta格式
                stages_axis1_ds = stages.create_dataset(
                    "axis1", shape=(0,), dtype=np.int64,
                    chunks=(100,), maxshape=(None,)
                )
                stages_axis1_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                stages_axis1_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                stages_axis1_ds.attrs["TITLE"] = np.bytes_(b'')
                stages_axis1_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                stages_axis1_ds.attrs["kind"] = np.bytes_(b'integer')
                stages_axis1_ds.attrs["name"] = np.bytes_(b'N.')
                stages_axis1_ds.attrs["transposed"] = np.uint8(1)

                # 3.3 stages/block0_items（start/end列标识）- Meta特有
                stages_block0_items_ds = stages.create_dataset(
                    "block0_items", data=np.array([b"start", b"end"], dtype="S5"), dtype="S5"
                )
                stages_block0_items_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                stages_block0_items_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                stages_block0_items_ds.attrs["TITLE"] = np.bytes_(b'')
                stages_block0_items_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                stages_block0_items_ds.attrs["kind"] = np.bytes_(b'string')
                stages_block0_items_ds.attrs["name"] = np.bytes_(b'N.')
                stages_block0_items_ds.attrs["transposed"] = np.uint8(1)

                # 3.4 stages/block0_values（start+end值）- Meta格式
                stages_block0_values_ds = stages.create_dataset(
                    "block0_values", shape=(0, 2), dtype=np.float64,
                    chunks=(100, 2), maxshape=(None, 2)
                )
                stages_block0_values_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                stages_block0_values_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                stages_block0_values_ds.attrs["TITLE"] = np.bytes_(b'')
                stages_block0_values_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                stages_block0_values_ds.attrs["kind"] = np.bytes_(b'float')
                stages_block0_values_ds.attrs["name"] = np.bytes_(b'N.')
                stages_block0_values_ds.attrs["transposed"] = np.uint8(1)

                # 3.5 stages/block1_items（name列标识）- Meta特有
                stages_block1_items_ds = stages.create_dataset(
                    "block1_items", data=np.array([b"name"], dtype="S4"), dtype="S4"
                )
                stages_block1_items_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                stages_block1_items_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                stages_block1_items_ds.attrs["TITLE"] = np.bytes_(b'')
                stages_block1_items_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                stages_block1_items_ds.attrs["kind"] = np.bytes_(b'string')
                stages_block1_items_ds.attrs["name"] = np.bytes_(b'N.')
                stages_block1_items_ds.attrs["transposed"] = np.uint8(1)

                # 3.6 stages/block1_values（name列值）- Meta核心：VLARRAY object类型
                stages_block1_values_ds = stages.create_dataset(
                    "block1_values", shape=(0,), dtype=STR_VLEN_DTYPE,
                    chunks=(100,), maxshape=(None,)
                )
                stages_block1_values_ds.attrs["CLASS"] = np.bytes_(b'VLARRAY')
                stages_block1_values_ds.attrs["PSEUDOATOM"] = np.bytes_(b'object')
                stages_block1_values_ds.attrs["TITLE"] = np.bytes_(b'')
                stages_block1_values_ds.attrs["VERSION"] = np.bytes_(b'1.4')
                stages_block1_values_ds.attrs["transposed"] = np.uint8(1)
            
            debug_log(f"✅ Meta格式HDF5文件创建并保持打开: {self.file_path}")

            return {
                "status": "success",
                "msg": f"创建Meta格式文件成功：{self.file_path}",
                "file_path": self.file_path
            }
        except Exception as e:
            debug_log(f"❌ 创建失败: {e}")
            self.close()
            return {"status": "error", "msg": f"创建文件失败：{str(e)}"}

    def append_data(self, data):
        """追加Data数据集（完全匹配Meta格式）"""
        with self.lock:
            ds = self.f["data"]
            current_len = ds.shape[0]
            new_len = current_len + len(data)
            ds.resize(new_len, axis=0)
            ds[current_len:new_len] = data
            return len(data)
        
    def append_prompts(self, np_struct):
        """追加Prompts（完全匹配Meta格式：block0_values/block1_values）"""
        with self.lock:
            # 空值判断
            if np_struct["name"][0] is None or np_struct["name"][0] == "":
                return 0
            
            prompts = self.f["prompts"]
            n_rows = len(np_struct)
            
            # 1. 追加axis1（行索引）
            idx_ds = prompts["axis1"]
            current_idx = idx_ds.shape[0]
            new_idx = np.arange(current_idx, current_idx + n_rows, dtype=np.int64)
            idx_ds.resize(current_idx + n_rows, axis=0)
            idx_ds[current_idx:] = new_idx

            # 2. 追加block0_values（name列：VLARRAY object类型）
            name_ds = prompts["block0_values"]
            # 转换为变长字符串
            names = [
                str(name).strip() if name is not None else "" 
                for name in np_struct["name"]
            ]
            # 扩展并写入
            name_ds.resize(name_ds.shape[0] + n_rows, axis=0)
            name_ds[-n_rows:] = names

            # 3. 追加block1_values（time列：(N,1)形状）
            time_ds = prompts["block1_values"]
            times = np_struct["time"].reshape(-1, 1)  # 转为(N,1)匹配Meta格式
            time_ds.resize(time_ds.shape[0] + n_rows, axis=0)
            time_ds[-n_rows:] = times
            
            return n_rows
        
    def append_stages(self, stage_name, start_time, end_time):
        """追加Stages（完全匹配Meta格式）"""
        with self.lock:
            if not stage_name or start_time <= 0 or end_time <= 0:
                return 0
            
            stages = self.f["stages"]
            n_rows = 1  # 每次追加1行
            
            # 1. 追加axis1（行索引）
            idx_ds = stages["axis1"]
            current_idx = idx_ds.shape[0]
            new_idx = np.array([current_idx], dtype=np.int64)
            idx_ds.resize(current_idx + n_rows, axis=0)
            idx_ds[current_idx:] = new_idx

            # 2. 追加block0_values（start+end：(N,2)形状）
            se_ds = stages["block0_values"]
            se_data = np.array([[start_time, end_time]], dtype=np.float64)
            se_ds.resize(se_ds.shape[0] + n_rows, axis=0)
            se_ds[-n_rows:] = se_data

            # 3. 追加block1_values（name列：VLARRAY object类型）
            name_ds = stages["block1_values"]
            name_ds.resize(name_ds.shape[0] + n_rows, axis=0)
            name_ds[-n_rows:] = [stage_name.strip()]
            
            return n_rows

    def flush(self):
        """刷盘"""
        if self.f is not None:
            self.f.flush()
        debug_log("🔄 数据已刷盘")

    def close(self):
        """关闭文件"""
        self.flush()
        if self.f is not None:
            self.f.close()
            self.f = None
        debug_log("✅ 文件已关闭")

    def get_stats(self):
        """获取统计信息"""
        if self.f is None:
            return {"error": "文件未打开"}
        return {
            "data_total": self.f["data"].shape[0],
            "prompts_total": self.f["prompts/axis1"].shape[0],
            "stages_total": self.f["stages/axis1"].shape[0]
        }

    # ===================== 数据解析与生成 =====================
    def parse_raw_str(self, raw_str):
        """64字符十六进制字符串→16通道float32数组"""
        raw_str = raw_str.ljust(64, '0')[:64]
        bytes_data = bytes.fromhex(raw_str)
        return np.frombuffer(bytes_data, dtype=np.uint16).astype(np.float32)

    def gen_data_struct(self, receive_package):
        """生成Data结构化数组（匹配Meta格式）"""
        # 解析5条raw_data为16通道数组
        emg_batch = []
        for raw_str in receive_package['big_bag_raw_data']:
            emg_channels = self.parse_raw_str(raw_str)
            # 确保是16通道
            if len(emg_channels) < 16:
                emg_channels = np.pad(emg_channels, (0, 16 - len(emg_channels)), mode='constant')
            elif len(emg_channels) > 16:
                emg_channels = emg_channels[:16]
            emg_batch.append(emg_channels)

        # 构造结构化数组
        emg_np = np.array(emg_batch, dtype=np.float32)
        time_np = np.array(receive_package['timestamp'], dtype=np.float64)

        data_struct = np.empty(len(emg_batch), dtype=DATA_DTYPE)
        data_struct["emg"] = emg_np
        data_struct["time"] = time_np

        return data_struct

    def gen_prompt_struct(self, receive_package):
        """生成Prompts结构化数组（匹配Meta格式）"""
        prompt_name = receive_package.get('prompt_name', None)
        prompt_time = receive_package.get('prompt_time', 0.0)
        
        # 构造numpy结构化数组
        prompt_struct = np.empty(1, dtype=[
            ("name", object),
            ("time", np.float64)
        ])
        prompt_struct["name"][0] = prompt_name
        prompt_struct["time"][0] = prompt_time
        
        return prompt_struct

    # ===================== 指令处理 =====================
    def handle_append(self, params):
        """处理写入数据指令（支持data/prompt/stage）"""
        try:
            if not self.file_path or self.f is None:
                return {"status": "error", "msg": "请先创建 HDF5 文件"}

            data_pkg = params['data']
            result = {"status": "success", "data": 0, "prompts": 0, "stages": 0}

            # 1. 处理Data数据
            if 'big_bag_raw_data' in data_pkg and 'timestamp' in data_pkg:
                data_struct = self.gen_data_struct(data_pkg)
                result["data"] = self.append_data(data_struct)

            # 2. 处理Prompts数据
            if 'prompt_name' in data_pkg and 'prompt_time' in data_pkg:
                prompt_struct = self.gen_prompt_struct(data_pkg)
                result["prompts"] = self.append_prompts(prompt_struct)

            # 3. 处理Stages数据
            if 'stage_name' in data_pkg and 'stage_start' in data_pkg and 'stage_end' in data_pkg:
                result["stages"] = self.append_stages(
                    data_pkg['stage_name'],
                    data_pkg['stage_start'],
                    data_pkg['stage_end']
                )

            result["msg"] = f"写入成功 - Data:{result['data']}条, Prompts:{result['prompts']}条, Stages:{result['stages']}条"
            return result
            
        except Exception as e:
            debug_log(f"❌ 写入失败: {str(e)}")
            return {"status": "error", "msg": f"写入数据失败：{str(e)}"}

    def handle_close(self, params):
        """处理关闭文件指令"""
        try:
            if self.f:
                stats = self.get_stats()
                self.close()
                msg = f"文件已保存并关闭：{self.file_path} | 统计：{stats}"
                debug_log(msg)
                return {
                    "status": "success", 
                    "msg": msg, 
                    "file_path": self.file_path,
                    "stats": stats
                }
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
                #debug_log(f"\n收到请求：{request['cmd']}")

                # 解析指令类型和参数
                cmd = request.get("cmd")
                params = request.get("params", {})

                # 处理不同指令
                if cmd == "create":
                    response = self.create_and_open(params)
                elif cmd == "append":
                    response = self.handle_append(params)
                elif cmd == "close":
                    response = self.handle_close(params)
                elif cmd == "stats":
                    response = {"status": "success", "data": self.get_stats()}
                else:
                    response = {"status": "error", "msg": f"未知指令：{cmd}，支持的指令：create/append/close/stats"}

                # 发送响应给客户端
                self.socket.send_json(response)
        except KeyboardInterrupt:
            debug_log("\n⚠️ 服务正在关闭...")
        finally:
            # 清理资源
            self.close()
            self.socket.close()
            self.context.term()
            debug_log("✅ 服务已关闭")

if __name__ == "__main__":
    # 启动服务
    server = HDF5StorageServer()
    server.run()