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




# ===================== 核心配置（完全匹配目标结构） =====================
# Data数据集
DATA_DTYPE = np.dtype([("emg", "<f4", (16,)), ("time", "<f8")])
TASK_ATTR = "discrete_gestures"

# Prompts/Stages 字符串存储配置（固定长度，避免变长内存问题）
STR_MAX_LEN = 64  # 足够存储所有手势/阶段名称
NAME_DTYPE = f"S{STR_MAX_LEN}"  # 固定长度字符串





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
        self.file_path = None #文件名
        self.overwrite = True
        self.f = None   #文件句柄
        self.lock = Lock()


    def create_and_open(self, params):
        """创建结构并保持文件打开（修复isinstance错误 + 补充元属性）"""
        self.file_path = params.get("file_name")

        if self.overwrite and os.path.exists(self.file_path):
            os.remove(self.file_path)
        
        try:
            self.f = h5py.File(self.file_path, "a", libver='latest')
            if "data" not in self.f:
                # ===================== 1. 创建Data数据集（带属性） =====================
                data_ds = self.f.create_dataset(
                    "data", shape=(0,), dtype=DATA_DTYPE,
                    chunks=(10000,), maxshape=(None,), compression=None
                )
                # Data数据集的核心属性
                data_ds.attrs["task"] = TASK_ATTR
                # 补充Pandas兼容属性
                data_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                data_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                data_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                data_ds.attrs["transposed"] = np.uint8(0)

                # ===================== 2. 创建Prompts组（带完整元属性） =====================
                prompts = self.f.create_group("prompts")

                # 2.1 prompts/axis0（列名轴：name/time）
                axis0_ds = prompts.create_dataset(
                    "axis0", 
                    data=np.array([b"name", b"time"], dtype="S4"),
                    dtype="S4"
                )
                # 补充axis0的元属性
                axis0_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                axis0_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                axis0_ds.attrs["TITLE"] = np.bytes_(b'')  # 空标题
                axis0_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                axis0_ds.attrs["kind"] = np.bytes_(b'string')
                axis0_ds.attrs["name"] = np.bytes_(b'N.')
                axis0_ds.attrs["transposed"] = np.uint8(1)

                # 2.2 prompts/axis1（行索引轴）
                axis1_ds = prompts.create_dataset(
                    "axis1", shape=(0,), dtype=np.int64, chunks=(1000,), maxshape=(None,)
                )
                # 补充axis1的元属性
                axis1_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                axis1_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                axis1_ds.attrs["TITLE"] = np.bytes_(b'')
                axis1_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                axis1_ds.attrs["kind"] = np.bytes_(b'integer')
                axis1_ds.attrs["name"] = np.bytes_(b'N.')
                axis1_ds.attrs["transposed"] = np.uint8(1)

                # 2.3 prompts/block0（name列值）
                block0_ds = prompts.create_dataset(
                    "block0", shape=(0,), dtype=NAME_DTYPE, chunks=(1000,), maxshape=(None,)
                )
                # 修复：正确判断是否为可变长类型
                is_vlen = isinstance(NAME_DTYPE, h5py.Datatype) and NAME_DTYPE.vlen is not None
                # 补充block0的元属性
                block0_ds.attrs["CLASS"] = np.bytes_(b'VLARRAY' if is_vlen else b'ARRAY')
                block0_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                block0_ds.attrs["TITLE"] = np.bytes_(b'')
                block0_ds.attrs["VERSION"] = np.bytes_(b'1.4' if is_vlen else b'2.4')
                block0_ds.attrs["kind"] = np.bytes_(b'string')
                block0_ds.attrs["name"] = np.bytes_(b'N.')
                block0_ds.attrs["transposed"] = np.uint8(1)
                if is_vlen:
                    block0_ds.attrs["PSEUDOATOM"] = np.bytes_(b'object')

                # 2.4 prompts/block1（time列值）
                block1_ds = prompts.create_dataset(
                    "block1", shape=(0,), dtype=np.float64, chunks=(1000,), maxshape=(None,)
                )
                # 补充block1的元属性
                block1_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                block1_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                block1_ds.attrs["TITLE"] = np.bytes_(b'')
                block1_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                block1_ds.attrs["kind"] = np.bytes_(b'float')
                block1_ds.attrs["name"] = np.bytes_(b'N.')
                block1_ds.attrs["transposed"] = np.uint8(1)

                # ===================== 3. 创建Stages组（带完整元属性） =====================
                stages = self.f.create_group("stages")

                # 3.1 stages/axis0（列名轴：start/end/name）
                stages_axis0_ds = stages.create_dataset(
                    "axis0", 
                    data=np.array([b"start", b"end", b"name"], dtype="S5"),
                    dtype="S5"
                )
                # 补充stages/axis0属性
                stages_axis0_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                stages_axis0_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                stages_axis0_ds.attrs["TITLE"] = np.bytes_(b'')
                stages_axis0_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                stages_axis0_ds.attrs["kind"] = np.bytes_(b'string')
                stages_axis0_ds.attrs["name"] = np.bytes_(b'N.')
                stages_axis0_ds.attrs["transposed"] = np.uint8(1)

                # 3.2 stages/axis1（行索引轴）
                stages_axis1_ds = stages.create_dataset(
                    "axis1", shape=(0,), dtype=np.int64, chunks=(100,), maxshape=(None,)
                )
                # 补充stages/axis1属性
                stages_axis1_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                stages_axis1_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                stages_axis1_ds.attrs["TITLE"] = np.bytes_(b'')
                stages_axis1_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                stages_axis1_ds.attrs["kind"] = np.bytes_(b'integer')
                stages_axis1_ds.attrs["name"] = np.bytes_(b'N.')
                stages_axis1_ds.attrs["transposed"] = np.uint8(1)

                # 3.3 stages/block0（start/end时间列）
                stages_block0_ds = stages.create_dataset(
                    "block0", shape=(0, 2), dtype=np.float64, chunks=(100, 2), maxshape=(None, 2)
                )
                # 补充stages/block0属性
                stages_block0_ds.attrs["CLASS"] = np.bytes_(b'ARRAY')
                stages_block0_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                stages_block0_ds.attrs["TITLE"] = np.bytes_(b'')
                stages_block0_ds.attrs["VERSION"] = np.bytes_(b'2.4')
                stages_block0_ds.attrs["kind"] = np.bytes_(b'float')
                stages_block0_ds.attrs["name"] = np.bytes_(b'N.')
                stages_block0_ds.attrs["transposed"] = np.uint8(1)

                # 3.4 stages/block1（name列值）
                stages_block1_ds = stages.create_dataset(
                    "block1", shape=(0,), dtype=NAME_DTYPE, chunks=(100,), maxshape=(None,)
                )
                # 补充stages/block1属性
                stages_block1_ds.attrs["CLASS"] = np.bytes_(b'VLARRAY' if is_vlen else b'ARRAY')
                stages_block1_ds.attrs["FLAVOR"] = np.bytes_(b'numpy')
                stages_block1_ds.attrs["TITLE"] = np.bytes_(b'')
                stages_block1_ds.attrs["VERSION"] = np.bytes_(b'1.4' if is_vlen else b'2.4')
                stages_block1_ds.attrs["kind"] = np.bytes_(b'string')
                stages_block1_ds.attrs["name"] = np.bytes_(b'N.')
                stages_block1_ds.attrs["transposed"] = np.uint8(1)
                if is_vlen:
                    stages_block1_ds.attrs["PSEUDOATOM"] = np.bytes_(b'object')
            
            debug_log(f"✅ 文件创建并保持打开: {self.file_path}")

            return {
                "status": "success",
                "msg": f"创建文件成功：{self.file_path}",
                "file_path": self.file_path
            }
        except Exception as e:
            debug_log(f"❌ 创建失败: {e}")
            self.close()
            return {"status": "error", "msg": f"创建文件失败：{str(e)}"}
        


    def append_data(self, data):
        """直接追加Data（无缓存，实时写入）"""
        with self.lock:
            ds = self.f["data"]
            current_len = ds.shape[0]
            new_len = current_len + len(data)
            ds.resize(new_len, axis=0)
            ds[current_len:new_len] = data
            return len(data)
        
    def append_prompts(self, np_struct):
        """追加Prompts（适配numpy结构化数组）"""
        with self.lock:
            # 1. 空值判断（numpy数组版）
            if np_struct["name"][0] is None or np_struct["name"][0] == "":
                return 0
            
            prompts = self.f["prompts"]
            # 追加索引
            idx_ds = prompts["axis1"]
            current_idx = idx_ds.shape[0]
            new_idx = np.arange(len(np_struct)) + current_idx
            idx_ds.resize(current_idx + len(new_idx), axis=0)
            idx_ds[current_idx:] = new_idx

            # 2. 追加name（numpy字符串处理，替代Pandas .str）
            name_ds = prompts["block0"]
            # numpy数组→字符串→编码→指定 dtype
            names = np.array([
                str(name).encode('utf-8') if name is not None else b"" 
                for name in np_struct["name"]
            ], dtype=NAME_DTYPE)
            name_ds.resize(name_ds.shape[0] + len(names), axis=0)
            name_ds[-len(names):] = names

            # 3. 追加time（numpy数组取值）
            time_ds = prompts["block1"]
            times = np_struct["time"]  # numpy数组直接取值
            time_ds.resize(time_ds.shape[0] + len(times), axis=0)
            time_ds[-len(times):] = times
            
            return len(np_struct)
        
    def flush(self):
        """刷盘"""
        if self.f is not None:
            self.f.flush()
        print("🔄 刷盘完成")

    def close(self):
        """关闭文件"""
        self.flush()
        if self.f is not None:
            self.f.close()
            self.f = None
        print("✅ 文件已关闭")


    def get_stats(self):
        """获取统计"""
        if self.f is None:
            return {"error": "文件未打开"}
        return {
            "data_total": self.f["data"].shape[0],
            "prompts_total": self.f["prompts/axis1"].shape[0],
            "stages_total": self.f["stages/axis1"].shape[0]
        }


    # ===================== 数据生成 =====================

    # 2. 解析函数（C级效率）
    def parse_raw_str(self, raw_str):
        """64字符十六进制字符串→16通道uint16数组"""
        # 补全64字符（防止截断）
        raw_str = raw_str.ljust(64, '0')[:64]
        # 十六进制→二进制→uint16（小端/大端可调整）
        bytes_data = bytes.fromhex(raw_str)
        return np.frombuffer(bytes_data, dtype=np.uint16).astype(np.float32)


    def gen_data_struct(self, receive_package):
        # 解析5条raw_data为16通道数组
        emg_batch = []
        for raw_str in receive_package['big_bag_raw_data']:
            emg_channels = self.parse_raw_str(raw_str)
            emg_batch.append(emg_channels)

        # 此时emg_batch 已经是二维数组了， 5 x 16
        # 构造HDF5的结构化数据
        emg_np = np.array(emg_batch, dtype=np.float32)
        time_np = np.array(receive_package['timestamp'], dtype=np.float64)

        data_struct = np.empty(5, dtype=[("emg", "<f4", (16,)), ("time", "<f8")])
        data_struct["emg"] = emg_np
        data_struct["time"] = time_np

        return data_struct

    def gen_prompt_struct(self, receive_package):
        """生成1条Prompts结构化numpy数组（避免Pandas Series歧义）"""
        # 提取并处理空值
        prompt_name = receive_package.get('prompt_name', None)
        prompt_time = receive_package.get('prompt_time', 0.0)
        
        # 转为numpy结构化数组（和data_struct格式对齐，便于HDF5写入）
        prompt_struct = np.empty(1, dtype=[
            ("name", object),  # 兼容None/字符串
            ("time", np.float64)
        ])
        prompt_struct["name"][0] = prompt_name
        prompt_struct["time"][0] = prompt_time
        
        return prompt_struct


# params.data == dataPacket.data = 
    
# data: {
#     task: null,     //采集任务（discrete/continual1/con2）

#     // data
#     big_bag_raw_data: rawData,  // [string64, string64, string64, string64, string64]//大包的rawData[5]数组放入 big_bag_raw_data
#     timestamp: timestamp_array, // [.9f, .9f, .9f, .9f, .9f] // 计算好大包内的5组的时间戳

#     // prompt
#     prompt_name: null,
#     prompt_time: 0,

#     // stage
#     stage_name: null,
#     stage_start: 0,
#     stage_end: 0
# }
    def handle_append(self, params):
        """处理写入数据指令"""
        try:
            if not self.file_path:
                return {"status": "error", "msg": "请先创建 HDF5 文件"}

            #debug_log(params)
            # 处理数据
            data = self.gen_data_struct(params['data'])
            prompt = self.gen_prompt_struct(params['data'])
            #stage = get_stage()

            self.append_data(data)
            self.append_prompts(prompt)

            return {
                "status": "success",
            }
        except Exception as e:
            return {"status": "error", "msg": f"写入数据失败：{str(e)}"}


    def handle_close(self, params):
        try:
            if self.f:
                self.flush()
                self.close()
                msg = f"文件已保存并关闭：{self.file_path}"
                debug_log(msg)
                return {"status": "success", "msg": msg, "file_path": self.file_path}
            else:
                return {"status": "warning", "msg": "无已打开的 HDF5 文件"}
        except Exception as e:
            return {"status": "error", "msg": f"关闭文件失败：{str(e)}"}

    # # ===================== 1. 创建基础结构（无变长字符串） =====================
    # def create_hdf5_structure(file_path):
    #     """创建无变长字符串的稳定结构"""
    #     # 覆盖已有文件
    #     if os.path.exists(file_path):
    #         os.remove(file_path)
        
    #     try:
    #         with h5py.File(file_path, "w") as f:
    #             self.h5_file = f
    #             # ---------- Data数据集 ----------
    #             f.create_dataset(
    #                 "data",
    #                 shape=(0,),
    #                 dtype=DATA_DTYPE,
    #                 chunks=(10000,),
    #                 maxshape=(None,)
    #             ).attrs["task"] = TASK_ATTR

    #             # ---------- Prompts组 ----------
    #             prompts = f.create_group("prompts")
    #             # axis0: 列名 [name, time]
    #             prompts.create_dataset("axis0", data=np.array([b"name", b"time"], dtype="S4"))
    #             # axis1: 行索引（可扩展）
    #             prompts.create_dataset("axis1", shape=(0,), dtype=np.int64, chunks=(1000,), maxshape=(None,))
    #             # block0: name列（固定长度字符串）
    #             prompts.create_dataset("block0", shape=(0,), dtype=NAME_DTYPE, chunks=(1000,), maxshape=(None,))
    #             # block1: time列
    #             prompts.create_dataset("block1", shape=(0,), dtype=np.float64, chunks=(1000,), maxshape=(None,))

    #             # ---------- Stages组 ----------
    #             stages = f.create_group("stages")
    #             # axis0: 列名 [start, end, name]
    #             stages.create_dataset("axis0", data=np.array([b"start", b"end", b"name"], dtype="S5"))
    #             # axis1: 行索引（可扩展）
    #             stages.create_dataset("axis1", shape=(0,), dtype=np.int64, chunks=(100,), maxshape=(None,))
    #             # block0: start+end
    #             stages.create_dataset("block0", shape=(0, 2), dtype=np.float64, chunks=(100, 2), maxshape=(None, 2))
    #             # block1: name列（固定长度字符串）
    #             stages.create_dataset("block1", shape=(0,), dtype=NAME_DTYPE, chunks=(100,), maxshape=(None,))

    #         print(f"✅ 成功创建文件: {file_path}")
    #         return True
    #     except Exception as e:
    #         print(f"❌ 创建失败: {e}")
    #         return False





    # # 收到create指令，就直接创建好完整的数据集和文件夹结构，不再根据数据集的信息指定hdf5格式，这个放在handle_create里面
    # # 收到write指令，全部处理为追加数据
    # ## 一个大包过来，收到5个data数据集的数据对，直接解析出来，变换格式，进行追加
    # ## 一个大包过来，收到一个prompt数据集的数据对，直接追加
    # ## 一个大包过来，收到一个stage的start，保存至本地，等待end信号过来，一起储存
    # # 收到close指令，保存关闭hdf5文件
    # # ===================== 2. 追加数据（核心功能） =====================

    # def append_data(file_path, data_dict):
    #     """
    #     追加数据到HDF5
    #     data_dict格式:
    #     {
    #         "data": 结构化数组 (可选),
    #         "prompts": DataFrame(name, time) (可选),
    #         "stages": DataFrame(start, end, name) (可选)
    #     }
    #     """
    #     if not os.path.exists(file_path):
    #         raise FileNotFoundError(f"文件不存在: {file_path}")

    #     try:
    #         with h5py.File(file_path, "a") as f:
    #             # ---------- 追加Data ----------
    #             if "data" in data_dict and data_dict["data"] is not None:
    #                 data = data_dict["data"]
    #                 ds = f["data"]
    #                 new_len = ds.shape[0] + len(data)
    #                 ds.resize(new_len, axis=0)
    #                 ds[-len(data):] = data
    #                 print(f"✅ Data追加: 新增{len(data)}条 | 总计{ds.shape[0]}条")

    #             # ---------- 追加Prompts ----------
    #             if "prompts" in data_dict and data_dict["prompts"] is not None:
    #                 df = data_dict["prompts"]
    #                 prompts = f["prompts"]
                    
    #                 # 追加行索引
    #                 idx_ds = prompts["axis1"]
    #                 new_idx = np.arange(len(df)) + idx_ds.shape[0]
    #                 idx_ds.resize(idx_ds.shape[0]+len(new_idx), axis=0)
    #                 idx_ds[-len(new_idx):] = new_idx

    #                 # 追加name（转固定长度字符串）
    #                 name_ds = prompts["block0"]
    #                 names = df["name"].astype(str).str.encode('utf-8').astype(NAME_DTYPE)
    #                 name_ds.resize(name_ds.shape[0]+len(names), axis=0)
    #                 name_ds[-len(names):] = names

    #                 # 追加time
    #                 time_ds = prompts["block1"]
    #                 times = df["time"].values
    #                 time_ds.resize(time_ds.shape[0]+len(times), axis=0)
    #                 time_ds[-len(times):] = times

    #                 print(f"✅ Prompts追加: 新增{len(df)}条 | 总计{idx_ds.shape[0]}条")

    #             # ---------- 追加Stages ----------
    #             if "stages" in data_dict and data_dict["stages"] is not None:
    #                 df = data_dict["stages"]
    #                 stages = f["stages"]
                    
    #                 # 追加行索引
    #                 idx_ds = stages["axis1"]
    #                 new_idx = np.arange(len(df)) + idx_ds.shape[0]
    #                 idx_ds.resize(idx_ds.shape[0]+len(new_idx), axis=0)
    #                 idx_ds[-len(new_idx):] = new_idx

    #                 # 追加start+end
    #                 se_ds = stages["block0"]
    #                 se_data = df[["start", "end"]].values
    #                 se_ds.resize(se_ds.shape[0]+len(se_data), axis=0)
    #                 se_ds[-len(se_data):] = se_data

    #                 # 追加name（转固定长度字符串）
    #                 name_ds = stages["block1"]
    #                 names = df["name"].astype(str).str.encode('utf-8').astype(NAME_DTYPE)
    #                 name_ds.resize(name_ds.shape[0]+len(names), axis=0)
    #                 name_ds[-len(names):] = names

    #                 print(f"✅ Stages追加: 新增{len(df)}条 | 总计{idx_ds.shape[0]}条")

    #         print("\n✅ 所有数据追加完成！")
    #     except Exception as e:
    #         print(f"❌ 追加失败: {e}")
    #         raise



    # def handle_create(self, params):
    #     """处理创建 HDF5 文件指令"""
    #     try:
    #         # 获取参数（支持自定义文件名
    #         file_name = params.get("file_name")
    #         create_hdf5_structure(file_name)

    #     except Exception as e:
    #         return {"status": "error", "msg": f"创建文件失败：{str(e)}"}

    # # def handle_create(self, params):
    # #     """处理创建 HDF5 文件指令"""
    # #     try:
    # #         # 获取参数（支持自定义文件名、组名）
    # #         file_name = params.get("file_name")
    # #         group_name = params.get("group_name")
    # #         self.file_path = os.path.join(os.getcwd(), file_name)

    # #         # 检查文件是否已存在
    # #         if os.path.exists(self.file_path):
    # #             return {"status": "error", "msg": f"文件 {self.file_path} 已存在"}

    # #         # 创建 HDF5 文件和根组
    # #         self.h5_file = h5py.File(self.file_path, "w")
    # #         self.h5_group = self.h5_file.create_group(group_name)
    # #         debug_log(f"成功创建 HDF5 文件：{self.file_path}，组：{group_name}")

    # #         return {
    # #             "status": "success",
    # #             "msg": f"创建文件成功：{self.file_path}",
    # #             "file_path": self.file_path,
    # #             "group_name": group_name
    # #         }
    # #     except Exception as e:
    # #         return {"status": "error", "msg": f"创建文件失败：{str(e)}"}
        

    # def handle_append(self, params):
    #     """处理追加数据指令"""
    #     try:
    #         #大包数据构造字典数据包
    #         append_data(FILE_PATH, data1)
        
    #     except Exception as e:
    #         return {"status": "error", "msg": f"追加数据失败：{str(e)}"}




    # def handle_write(self, params):
    #     """处理写入数据指令"""
    #     try:
    #         if not self.h5_file or not self.h5_group:
    #             return {"status": "error", "msg": "请先创建 HDF5 文件"}

    #         # 获取写入参数
    #         dataset_name = params.get("dataset_name")
    #         data = params.get("data")
    #         dtype = params.get("dtype")

    #         if data is None:
    #             return {"status": "error", "msg": "写入数据不能为空"}

    #         # 转换数据为 numpy 数组
    #         try:
    #             data_array = np.array(data, dtype=dtype)
    #         except Exception as e:
    #             return {"status": "error", "msg": f"数据转换失败：{str(e)}"}

    #         # 创建数据集（首次写入）或追加数据
    #         if dataset_name not in self.datasets:
    #             # 动态扩展的数据集（maxshape=(None,) 表示一维数据可追加）
    #             self.datasets[dataset_name] = self.h5_group.create_dataset(
    #                 name=dataset_name,
    #                 shape=(0,),
    #                 maxshape=(None,),
    #                 dtype=dtype
    #             )
    #             debug_log(f"创建数据集：{dataset_name}")

    #         # 追加数据
    #         dataset = self.datasets[dataset_name]
    #         current_len = dataset.shape[0]
    #         new_len = current_len + len(data_array)
    #         dataset.resize(new_len, axis=0)
    #         dataset[current_len:new_len] = data_array

    #         return {
    #             "status": "success",
    #             "msg": f"写入 {len(data_array)} 条数据到 {dataset_name}",
    #             "dataset_name": dataset_name,
    #             "total_count": new_len
    #         }
    #     except Exception as e:
    #         return {"status": "error", "msg": f"写入数据失败：{str(e)}"}



    # def handle_close(self, params):
    #     """处理关闭保存文件指令"""
    #     try:
    #         if self.h5_file:
    #             self.h5_file.close()
    #             self.h5_file = None
    #             self.h5_group = None
    #             self.datasets = {}
    #             msg = f"文件已保存并关闭：{self.file_path}"
    #             debug_log(msg)
    #             return {"status": "success", "msg": msg, "file_path": self.file_path}
    #         else:
    #             return {"status": "warning", "msg": "无已打开的 HDF5 文件"}
    #     except Exception as e:
    #         return {"status": "error", "msg": f"关闭文件失败：{str(e)}"}

    def run(self):
        """启动服务，循环处理客户端请求"""
        try:
            while True:
                # 接收客户端请求（JSON 格式）
                request = self.socket.recv_json()
                #debug_log(f"\n收到请求：{request}")

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
                else:
                    response = {"status": "error", "msg": f"未知指令：{cmd}，支持的指令：create/write/close"}

                # 发送响应给客户端
                self.socket.send_json(response)
        except KeyboardInterrupt:
            debug_log("\n服务正在关闭...")
        finally:
            # 清理资源
            if self.f:
                self.f.close()
            self.socket.close()
            self.context.term()
            debug_log("服务已关闭")

if __name__ == "__main__":
    server = HDF5StorageServer()
    server.run()
