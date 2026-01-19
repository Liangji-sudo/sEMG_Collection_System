"""
storage_server.py - HDF5数据存储服务 (v4.2)

新特性:
1. 多级目录结构存储（增加受试者编号层级）
2. 目录层级: task -> category1 -> category2 -> category4 -> user_id
3. 文件命名: [受试者编号]_session{N}_[stage]_[年月日]_[时分秒].h5
4. 存储完整的metadata（受试者信息、分类信息、stage信息、session信息）

目录结构示例:
storage/
├── discrete_gesture/              # 采集任务 (task)
│   ├── static/                    # 大类 (category1)
│   │   ├── sitting/               # 大场景 (category2)
│   │   │   ├── normal/            # 人群 (category4)
│   │   │   │   ├── S001/          # 受试者编号 (user_id)
│   │   │   │   │   ├── S001_session1_palm_up_20260105_143000.h5
│   │   │   │   │   ├── S001_session1_palm_inward_20260105_143500.h5
│   │   │   │   │   ├── S001_session2_palm_up_20260105_150000.h5
│   │   │   │   │   └── S001_session2_palm_inward_20260105_150500.h5
│   │   │   │   └── S002/
│   │   │   │       ├── S002_session1_palm_up_20260105_160000.h5
│   │   │   │       └── ...
│   │   │   └── exercise/
│   │   │       └── S001/
│   │   │           └── S001_session1_palm_up_20260105_170000.h5
│   │   └── lying/
│   │       └── ...
│   └── dynamic/
│       └── ...
├── continual_gesture_1/
│   └── ...
└── continual_gesture_2/
    └── ...
"""

import h5py
import zmq
import json
import os
import sys
import io
import argparse
from datetime import datetime
import numpy as np
from threading import Lock

# ================= 基础配置 =================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# ===================== 数据类型定义 =====================
# EMG数据集类型：每帧16通道 + 时间戳
EMG_DTYPE = np.dtype([
    ("channels", "<f4", (16,)),  # 16通道EMG数据
    ("time", "<f8")               # 时间戳
])

# IMU数据集类型：acc(3) + gyr(3) + mag(3) + 时间戳
IMU_DTYPE = np.dtype([
    ("acc", "<f4", (3,)),   # 加速度计 [ax, ay, az]
    ("gyr", "<f4", (3,)),   # 陀螺仪 [gx, gy, gz]
    ("mag", "<f4", (3,)),   # 磁力计 [mx, my, mz]
    ("time", "<f8")         # 时间戳
])

# 字符串存储配置
STR_VLEN_DTYPE = h5py.special_dtype(vlen=str)

def debug_log(message):
    print(f"[storage_server] {message}", file=sys.stderr, flush=True)


class HDF5StorageServer:
    def __init__(self, host="127.0.0.1", port=5555, storage_dir="./storage"):
        # ZeroMQ配置
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REP)
        self.socket.bind(f"tcp://{host}:{port}")
        debug_log(f"HDF5存储服务已启动，监听 {host}:{port}")
        
        # 存储根目录
        self.storage_dir = os.path.abspath(storage_dir)
        debug_log(f"存储根目录设置为: {self.storage_dir}")
        
        # 确保存储目录存在
        if not os.path.exists(self.storage_dir):
            try:
                os.makedirs(self.storage_dir)
                debug_log(f"创建存储目录: {self.storage_dir}")
            except Exception as e:
                debug_log(f"❌ 创建存储目录失败: {e}")
        
        # HDF5文件相关
        self.file_path = None
        self.f = None
        self.lock = Lock()
        
        # 当前采集信息
        self.current_task_id = None
        self.current_user_id = None
        self.current_stage_name = None
        self.current_category1 = None
        self.current_category2 = None
        self.current_category4 = None
        self.is_collecting = False
        
        # 【新增】Session信息
        self.current_session_index = 0    # session索引（从0开始）
        self.current_session_number = 1   # session编号（从1开始）
        self.session_count = 3            # session总数
        
        # 统计信息
        self.stats = {
            "emg1_frames": 0,
            "emg2_frames": 0,
            "imu1_frames": 0,
            "imu2_frames": 0,
            "prompts": 0
        }
    
    def _sanitize_name(self, name):
        """清理文件/目录名中的非法字符"""
        if not name:
            return "unknown"
        # 替换非法字符
        illegal_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|', ' ']
        result = str(name)
        for char in illegal_chars:
            result = result.replace(char, '_')
        return result
    
    def generate_directory_path(self, task_id, category1, category2, category4, user_id):
        """
        生成多级目录路径（包含受试者编号层级）:
        storage/
        └── {task_id}/           # 采集任务
            └── {category1}/     # 大类
                └── {category2}/ # 大场景
                    └── {category4}/ # 人群
                        └── {user_id}/   # 受试者编号 ← 新增
        """
        # 清理每一层的名称
        task_dir = self._sanitize_name(task_id)
        cat1_dir = self._sanitize_name(category1)
        cat2_dir = self._sanitize_name(category2)
        cat4_dir = self._sanitize_name(category4)
        user_dir = self._sanitize_name(user_id)
        
        # 构建完整路径（包含user_id层级）
        dir_path = os.path.join(
            self.storage_dir,
            task_dir,      # 采集任务
            cat1_dir,      # 大类
            cat2_dir,      # 大场景
            cat4_dir,      # 人群
            user_dir       # 受试者编号 ← 新增
        )
        
        # 确保目录存在
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
            debug_log(f"创建多级目录: {dir_path}")
        
        return dir_path
    
    def generate_filename(self, dir_path, user_id, stage_name, session_number):
        """
        生成文件名: {user_id}_session{N}_{stage_name}_{YYYYMMDD}_{HHMMSS}.h5
        例如: S001_session2_palm_up_20260105_143000.h5
        """
        now = datetime.now()
        date_str = now.strftime("%Y%m%d")
        time_str = now.strftime("%H%M%S")
        
        # 清理用户ID和stage名称
        safe_user_id = self._sanitize_name(user_id)
        safe_stage_name = self._sanitize_name(stage_name)
        
        # 文件名包含: 受试者编号_session{N}_stage_日期_时间
        filename = f"{safe_user_id}_session{session_number}_{safe_stage_name}_{date_str}_{time_str}.h5"
        return os.path.join(dir_path, filename)
    
    def create_file(self, params):
        """创建新的HDF5文件（使用多级目录结构，包含受试者层级）"""
        try:
            # 提取参数
            task_id = params.get("task_id", "discrete_gesture")
            user_id = params.get("user_id", "unknown_user")
            stage_name = params.get("stage_name", "unknown_stage")
            category1 = params.get("category1", "static")
            category2 = params.get("category2", "sitting")
            category4 = params.get("category4", "normal")
            subject_info = params.get("subject_info", {})
            template_name = params.get("template_name", "default")
            
            # 【新增】提取Session参数
            session_index = params.get("session_index", 0)
            session_number = params.get("session_number", 1)
            session_count = params.get("session_count", 3)
            
            # 保存当前信息
            self.current_task_id = task_id
            self.current_user_id = user_id
            self.current_stage_name = stage_name
            self.current_category1 = category1
            self.current_category2 = category2
            self.current_category4 = category4
            
            # 【新增】保存Session信息
            self.current_session_index = session_index
            self.current_session_number = session_number
            self.session_count = session_count
            
            debug_log(f"Session信息: session{session_number}/{session_count} (索引: {session_index})")
            
            # 如果有已打开的文件，先关闭
            if self.f:
                debug_log("关闭上一个文件...")
                self.close_file()
            
            # 生成多级目录路径（包含user_id层级）
            dir_path = self.generate_directory_path(task_id, category1, category2, category4, user_id)
            
            # 生成文件名（包含session_number和stage_name）
            self.file_path = self.generate_filename(dir_path, user_id, stage_name, session_number)
            
            # 如果文件已存在，添加序号
            base_path = self.file_path
            counter = 1
            while os.path.exists(self.file_path):
                name, ext = os.path.splitext(base_path)
                self.file_path = f"{name}_{counter}{ext}"
                counter += 1
            
            debug_log(f"准备创建文件: {self.file_path}")
            
            # 创建HDF5文件
            self.f = h5py.File(self.file_path, "a", libver='latest')
            
            # ===================== 创建根属性（Metadata） =====================
            self.f.attrs["task_id"] = task_id
            self.f.attrs["user_id"] = user_id
            self.f.attrs["stage_name"] = stage_name
            self.f.attrs["category1"] = category1
            self.f.attrs["category2"] = category2
            self.f.attrs["category4"] = category4
            self.f.attrs["template_name"] = template_name
            self.f.attrs["created_at"] = datetime.now().isoformat()
            
            # 【新增】保存Session信息到HDF5属性
            self.f.attrs["session_index"] = session_index
            self.f.attrs["session_number"] = session_number
            self.f.attrs["session_count"] = session_count
            
            # ===================== 创建受试者信息组 =====================
            if subject_info:
                subject_grp = self.f.create_group("subject")
                for key, value in subject_info.items():
                    if value is not None:
                        try:
                            subject_grp.attrs[str(key)] = str(value) if not isinstance(value, (int, float)) else value
                        except Exception as e:
                            debug_log(f"保存subject属性失败 {key}: {e}")
            
            # ===================== 创建EMG数据集 =====================
            emg1_ds = self.f.create_dataset(
                "emg1", shape=(0,), dtype=EMG_DTYPE,
                chunks=(1000,), maxshape=(None,), compression="gzip"
            )
            emg1_ds.attrs["device"] = "device_1"
            emg1_ds.attrs["channels"] = 16
            emg1_ds.attrs["description"] = "EMG data from device 1"
            
            emg2_ds = self.f.create_dataset(
                "emg2", shape=(0,), dtype=EMG_DTYPE,
                chunks=(1000,), maxshape=(None,), compression="gzip"
            )
            emg2_ds.attrs["device"] = "device_2"
            emg2_ds.attrs["channels"] = 16
            emg2_ds.attrs["description"] = "EMG data from device 2"
            
            # ===================== 创建IMU数据集 =====================
            imu1_ds = self.f.create_dataset(
                "imu1", shape=(0,), dtype=IMU_DTYPE,
                chunks=(500,), maxshape=(None,), compression="gzip"
            )
            imu1_ds.attrs["device"] = "device_1"
            imu1_ds.attrs["description"] = "IMU data from device 1 (acc, gyr, mag)"
            
            imu2_ds = self.f.create_dataset(
                "imu2", shape=(0,), dtype=IMU_DTYPE,
                chunks=(500,), maxshape=(None,), compression="gzip"
            )
            imu2_ds.attrs["device"] = "device_2"
            imu2_ds.attrs["description"] = "IMU data from device 2 (acc, gyr, mag)"
            
            # ===================== 创建Prompts组 =====================
            prompts = self.f.create_group("prompts")
            
            prompts.create_dataset(
                "names", shape=(0,), dtype=STR_VLEN_DTYPE,
                chunks=(1000,), maxshape=(None,)
            )
            
            prompts.create_dataset(
                "times", shape=(0,), dtype=np.float64,
                chunks=(1000,), maxshape=(None,)
            )
            
            # 重置统计
            self.stats = {
                "emg1_frames": 0,
                "emg2_frames": 0,
                "imu1_frames": 0,
                "imu2_frames": 0,
                "prompts": 0
            }
            self.is_collecting = True
            
            # 显示相对路径
            rel_path = os.path.relpath(self.file_path, self.storage_dir)
            debug_log(f"✅ 文件创建成功: {rel_path}")
            debug_log(f"   任务: {task_id}, 用户: {user_id}, Stage: {stage_name}")
            debug_log(f"   分类: {category1}/{category2}/{category4}")
            
            return {
                "status": "success",
                "msg": f"创建Stage文件成功",
                "file_path": self.file_path,
                "stage_name": stage_name
            }
            
        except Exception as e:
            debug_log(f"❌ 创建文件失败: {e}")
            import traceback
            traceback.print_exc()
            return {"status": "error", "msg": f"创建文件失败：{str(e)}"}
    
    def append_data(self, params):
        """追加数据"""
        if not self.f:
            return {"status": "error", "msg": "文件未打开，请先调用create"}
        
        try:
            data = params.get("data", {})
            
            with self.lock:
                # 追加EMG1
                if data.get("emg1") and data.get("emg1_t"):
                    self._append_emg("emg1", data["emg1"], data["emg1_t"])
                
                # 追加EMG2
                if data.get("emg2") and data.get("emg2_t"):
                    self._append_emg("emg2", data["emg2"], data["emg2_t"])
                
                # 追加IMU1
                if data.get("imu1") and data.get("imu1_t"):
                    self._append_imu("imu1", data["imu1"], data["imu1_t"])
                
                # 追加IMU2
                if data.get("imu2") and data.get("imu2_t"):
                    self._append_imu("imu2", data["imu2"], data["imu2_t"])
                
                # 追加Prompt
                if data.get("prompt_name"):
                    self._append_prompt(
                        data["prompt_name"],
                        data.get("prompt_time", 0)
                    )
            
            return {"status": "success", "msg": "数据已追加"}
            
        except Exception as e:
            debug_log(f"❌ 追加数据失败: {e}")
            return {"status": "error", "msg": f"追加数据失败：{str(e)}"}
    
    def _append_emg(self, dataset_name, emg_data, timestamps):
        """追加EMG数据"""
        try:
            ds = self.f[dataset_name]
            
            if not emg_data or len(emg_data) != 16:
                return 0
            
            num_frames = len(emg_data[0])
            if len(timestamps) != num_frames:
                if len(timestamps) < num_frames:
                    timestamps = list(timestamps) + [timestamps[-1]] * (num_frames - len(timestamps))
                else:
                    timestamps = timestamps[:num_frames]
            
            # 构造结构化数组
            data_struct = np.empty(num_frames, dtype=EMG_DTYPE)
            
            for i in range(num_frames):
                channels = [emg_data[ch][i] for ch in range(16)]
                data_struct[i]["channels"] = np.array(channels, dtype=np.float32)
                data_struct[i]["time"] = timestamps[i]
            
            # 追加到数据集
            current_len = ds.shape[0]
            new_len = current_len + num_frames
            ds.resize(new_len, axis=0)
            ds[current_len:new_len] = data_struct
            
            # 更新统计
            self.stats[f"{dataset_name}_frames"] += num_frames
            
            return num_frames
            
        except Exception as e:
            debug_log(f"❌ 追加{dataset_name}失败: {e}")
            return 0
    
    def _append_imu(self, dataset_name, imu_data, timestamps):
        """追加IMU数据"""
        try:
            ds = self.f[dataset_name]
            
            if not imu_data or "acc" not in imu_data:
                return 0
            
            # 构造结构化数组（每次一帧）
            data_struct = np.empty(1, dtype=IMU_DTYPE)
            data_struct[0]["acc"] = np.array(imu_data.get("acc", [0,0,0])[:3], dtype=np.float32)
            data_struct[0]["gyr"] = np.array(imu_data.get("gyr", [0,0,0])[:3], dtype=np.float32)
            data_struct[0]["mag"] = np.array(imu_data.get("mag", [0,0,0])[:3], dtype=np.float32)
            data_struct[0]["time"] = timestamps[0] if timestamps else 0
            
            # 追加到数据集
            current_len = ds.shape[0]
            ds.resize(current_len + 1, axis=0)
            ds[current_len] = data_struct[0]
            
            # 更新统计
            self.stats[f"{dataset_name}_frames"] += 1
            
            return 1
            
        except Exception as e:
            debug_log(f"❌ 追加{dataset_name}失败: {e}")
            return 0
    
    def _append_prompt(self, name, time):
        """追加Prompt数据"""
        try:
            if not name:
                return 0
            
            prompts = self.f["prompts"]
            
            # 追加name
            names_ds = prompts["names"]
            names_ds.resize(names_ds.shape[0] + 1, axis=0)
            names_ds[-1] = str(name)
            
            # 追加time
            times_ds = prompts["times"]
            times_ds.resize(times_ds.shape[0] + 1, axis=0)
            times_ds[-1] = float(time)
            
            self.stats["prompts"] += 1
            
            return 1
            
        except Exception as e:
            debug_log(f"❌ 追加prompt失败: {e}")
            return 0
    
    def flush(self):
        """刷盘"""
        if self.f:
            self.f.flush()
    
    def close_file(self):
        """关闭文件"""
        try:
            self.flush()
            
            if self.f:
                # 写入最终统计信息
                self.f.attrs["closed_at"] = datetime.now().isoformat()
                self.f.attrs["total_emg1_frames"] = self.stats["emg1_frames"]
                self.f.attrs["total_emg2_frames"] = self.stats["emg2_frames"]
                self.f.attrs["total_imu1_frames"] = self.stats["imu1_frames"]
                self.f.attrs["total_imu2_frames"] = self.stats["imu2_frames"]
                self.f.attrs["total_prompts"] = self.stats["prompts"]
                
                self.f.close()
                self.f = None
            
            self.is_collecting = False
            
            rel_path = os.path.relpath(self.file_path, self.storage_dir) if self.file_path else "N/A"
            debug_log(f"✅ 文件已关闭: {rel_path}")
            debug_log(f"📊 统计: EMG1={self.stats['emg1_frames']}, EMG2={self.stats['emg2_frames']}, "
                     f"IMU1={self.stats['imu1_frames']}, IMU2={self.stats['imu2_frames']}, "
                     f"Prompts={self.stats['prompts']}")
            
            return {
                "status": "success",
                "msg": f"文件已保存并关闭",
                "file_path": self.file_path,
                "stats": self.stats.copy()
            }
            
        except Exception as e:
            debug_log(f"❌ 关闭文件失败: {e}")
            return {"status": "error", "msg": f"关闭文件失败：{str(e)}"}
    
    def get_stats(self):
        """获取统计信息"""
        return {
            "file_path": self.file_path,
            "task_id": self.current_task_id,
            "user_id": self.current_user_id,
            "stage_name": self.current_stage_name,
            "category1": self.current_category1,
            "category2": self.current_category2,
            "category4": self.current_category4,
            "is_collecting": self.is_collecting,
            # 【新增】Session信息
            "session_index": self.current_session_index,
            "session_number": self.current_session_number,
            "session_count": self.session_count,
            **self.stats
        }
    
    def get_directory_tree(self):
        """获取存储目录树结构（用于统计页面）"""
        tree = {}
        
        for root, dirs, files in os.walk(self.storage_dir):
            rel_root = os.path.relpath(root, self.storage_dir)
            
            # 计算深度
            if rel_root == '.':
                depth = 0
                current = tree
            else:
                parts = rel_root.split(os.sep)
                depth = len(parts)
                current = tree
                for part in parts:
                    if part not in current:
                        current[part] = {"_files": [], "_subdirs": {}}
                    current = current[part]["_subdirs"]
            
            # 只统计h5文件
            h5_files = [f for f in files if f.endswith('.h5')]
            if h5_files and rel_root != '.':
                parts = rel_root.split(os.sep)
                current = tree
                for part in parts[:-1]:
                    current = current[part]["_subdirs"]
                if parts[-1] not in current:
                    current[parts[-1]] = {"_files": [], "_subdirs": {}}
                current[parts[-1]]["_files"] = h5_files
        
        return tree
    
    def run(self):
        """启动服务，循环处理客户端请求"""
        debug_log("🚀 存储服务开始运行 (v4.2 - 包含Session信息)...")
        debug_log(f"   目录结构: task/category1/category2/category4/user_id/")
        debug_log(f"   文件命名: [user_id]_session{{N}}_[stage]_[date]_[time].h5")
        
        try:
            while True:
                # 接收请求
                request = self.socket.recv_json()
                
                cmd = request.get("cmd")
                params = request.get("params", {})
                
                # 处理命令
                if cmd == "create":
                    response = self.create_file(params)
                elif cmd == "append":
                    response = self.append_data(params)
                elif cmd == "close":
                    response = self.close_file()
                elif cmd == "stats":
                    response = {"status": "success", "data": self.get_stats()}
                elif cmd == "flush":
                    self.flush()
                    response = {"status": "success", "msg": "数据已刷盘"}
                elif cmd == "tree":
                    response = {"status": "success", "data": self.get_directory_tree()}
                else:
                    response = {
                        "status": "error",
                        "msg": f"未知指令：{cmd}，支持的指令：create/append/close/stats/flush/tree"
                    }
                
                # 发送响应
                self.socket.send_json(response)
                
        except KeyboardInterrupt:
            debug_log("\n⚠️ 服务正在关闭...")
        finally:
            self.close_file()
            self.socket.close()
            self.context.term()
            debug_log("✅ 服务已关闭")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='HDF5 Storage Server v4.2 (包含Session信息)')
    parser.add_argument('--storage_dir', type=str, default='./storage',
                        help='HDF5文件存储目录')
    parser.add_argument('--port', type=int, default=5555,
                        help='ZeroMQ监听端口')
    
    args = parser.parse_args()
    
    server = HDF5StorageServer(port=args.port, storage_dir=args.storage_dir)
    server.run()
