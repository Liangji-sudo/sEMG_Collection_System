"""
storage_server.py - HDF5数据存储服务 (v2.0)

功能:
1. 接收来自realtimeEngine.js的双设备EMG+IMU数据
2. 存储stage信息（name, start, end）
3. 存储prompt信息（仅离散手势任务需要）
4. 支持多任务类型（discrete_gesture, continual_gesture_1, continual_gesture_2）

数据结构:
- emg1: 设备1的16通道EMG数据
- emg2: 设备2的16通道EMG数据  
- imu1: 设备1的IMU数据（acc, gyr, mag各3通道）
- imu2: 设备2的IMU数据
- prompts: prompt标签和时间戳（仅离散手势）
- stages: stage名称、开始和结束时间戳

文件命名:
{task_id}_{user_id}_{YYYYMMDD}_{HHMMSS}.h5
"""

import h5py
import zmq
import json
import os
import sys
import io
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
        
        # 存储目录
        self.storage_dir = storage_dir
        if not os.path.exists(storage_dir):
            os.makedirs(storage_dir)
            debug_log(f"创建存储目录: {storage_dir}")
        
        # HDF5文件相关
        self.file_path = None
        self.f = None
        self.lock = Lock()
        
        # 任务信息
        self.current_task_id = None
        self.current_user_id = None
        self.is_collecting = False
        
        # Stage缓存（收到start后缓存，收到end后写入）
        self.pending_stage = None
        
        # 统计信息
        self.stats = {
            "emg1_frames": 0,
            "emg2_frames": 0,
            "imu1_frames": 0,
            "imu2_frames": 0,
            "prompts": 0,
            "stages": 0
        }
    
    def generate_filename(self, task_id, user_id):
        """生成HDF5文件名"""
        now = datetime.now()
        date_str = now.strftime("%Y%m%d")
        time_str = now.strftime("%H%M%S")
        filename = f"{task_id}_{user_id}_{date_str}_{time_str}.h5"
        return os.path.join(self.storage_dir, filename)
    
    def create_file(self, params):
        """创建新的HDF5文件"""
        try:
            task_id = params.get("task_id", "unknown_task")
            user_id = params.get("user_id", "unknown_user")
            
            self.current_task_id = task_id
            self.current_user_id = user_id
            
            # 生成文件名
            self.file_path = self.generate_filename(task_id, user_id)
            
            # 如果文件已存在，删除旧文件
            if os.path.exists(self.file_path):
                os.remove(self.file_path)
            
            # 创建HDF5文件
            self.f = h5py.File(self.file_path, "a", libver='latest')
            
            # ===================== 创建根属性 =====================
            self.f.attrs["task_id"] = task_id
            self.f.attrs["user_id"] = user_id
            self.f.attrs["created_at"] = datetime.now().isoformat()
            
            # ===================== 创建EMG数据集 =====================
            # EMG1 (设备1)
            emg1_ds = self.f.create_dataset(
                "emg1", shape=(0,), dtype=EMG_DTYPE,
                chunks=(1000,), maxshape=(None,), compression="gzip"
            )
            emg1_ds.attrs["device"] = "device_1"
            emg1_ds.attrs["channels"] = 16
            emg1_ds.attrs["description"] = "EMG data from device 1"
            
            # EMG2 (设备2)
            emg2_ds = self.f.create_dataset(
                "emg2", shape=(0,), dtype=EMG_DTYPE,
                chunks=(1000,), maxshape=(None,), compression="gzip"
            )
            emg2_ds.attrs["device"] = "device_2"
            emg2_ds.attrs["channels"] = 16
            emg2_ds.attrs["description"] = "EMG data from device 2"
            
            # ===================== 创建IMU数据集 =====================
            # IMU1 (设备1)
            imu1_ds = self.f.create_dataset(
                "imu1", shape=(0,), dtype=IMU_DTYPE,
                chunks=(500,), maxshape=(None,), compression="gzip"
            )
            imu1_ds.attrs["device"] = "device_1"
            imu1_ds.attrs["description"] = "IMU data from device 1 (acc, gyr, mag)"
            
            # IMU2 (设备2)
            imu2_ds = self.f.create_dataset(
                "imu2", shape=(0,), dtype=IMU_DTYPE,
                chunks=(500,), maxshape=(None,), compression="gzip"
            )
            imu2_ds.attrs["device"] = "device_2"
            imu2_ds.attrs["description"] = "IMU data from device 2 (acc, gyr, mag)"
            
            # ===================== 创建Prompts组 =====================
            prompts = self.f.create_group("prompts")
            
            # prompts/names - prompt名称（变长字符串）
            prompts.create_dataset(
                "names", shape=(0,), dtype=STR_VLEN_DTYPE,
                chunks=(1000,), maxshape=(None,)
            )
            
            # prompts/times - prompt时间戳
            prompts.create_dataset(
                "times", shape=(0,), dtype=np.float64,
                chunks=(1000,), maxshape=(None,)
            )
            
            # prompts/stage_names - 对应的stage名称
            prompts.create_dataset(
                "stage_names", shape=(0,), dtype=STR_VLEN_DTYPE,
                chunks=(1000,), maxshape=(None,)
            )
            
            # ===================== 创建Stages组 =====================
            stages = self.f.create_group("stages")
            
            # stages/names
            stages.create_dataset(
                "names", shape=(0,), dtype=STR_VLEN_DTYPE,
                chunks=(100,), maxshape=(None,)
            )
            
            # stages/start_times
            stages.create_dataset(
                "start_times", shape=(0,), dtype=np.float64,
                chunks=(100,), maxshape=(None,)
            )
            
            # stages/end_times
            stages.create_dataset(
                "end_times", shape=(0,), dtype=np.float64,
                chunks=(100,), maxshape=(None,)
            )
            
            # 重置统计
            self.stats = {
                "emg1_frames": 0,
                "emg2_frames": 0,
                "imu1_frames": 0,
                "imu2_frames": 0,
                "prompts": 0,
                "stages": 0
            }
            self.pending_stage = None
            self.is_collecting = True
            
            debug_log(f"✅ 创建HDF5文件成功: {self.file_path}")
            
            return {
                "status": "success",
                "msg": f"创建文件成功：{self.file_path}",
                "file_path": self.file_path
            }
            
        except Exception as e:
            debug_log(f"❌ 创建文件失败: {e}")
            self.close_file()
            return {"status": "error", "msg": f"创建文件失败：{str(e)}"}
    
    def append_data(self, params):
        """追加数据（EMG/IMU/Prompt/Stage）"""
        try:
            if not self.f or not self.is_collecting:
                return {"status": "error", "msg": "未开始采集或文件未打开"}
            
            data = params.get("data", {})
            result = {
                "status": "success",
                "emg1": 0, "emg2": 0,
                "imu1": 0, "imu2": 0,
                "prompts": 0, "stages": 0
            }
            
            with self.lock:
                # ========== 处理EMG1数据 ==========
                if data.get("emg1") and data.get("emg1_t"):
                    result["emg1"] = self._append_emg("emg1", data["emg1"], data["emg1_t"])
                
                # ========== 处理EMG2数据 ==========
                if data.get("emg2") and data.get("emg2_t"):
                    result["emg2"] = self._append_emg("emg2", data["emg2"], data["emg2_t"])
                
                # ========== 处理IMU1数据 ==========
                if data.get("imu1") and data.get("imu1_t"):
                    result["imu1"] = self._append_imu("imu1", data["imu1"], data["imu1_t"])
                
                # ========== 处理IMU2数据 ==========
                if data.get("imu2") and data.get("imu2_t"):
                    result["imu2"] = self._append_imu("imu2", data["imu2"], data["imu2_t"])
                
                # ========== 处理Prompt数据 ==========
                if data.get("prompt_name") and data.get("prompt_time"):
                    result["prompts"] = self._append_prompt(
                        data["prompt_name"],
                        data["prompt_time"],
                        data.get("prompt_stage", "")
                    )
                
                # ========== 处理Stage Start ==========
                if data.get("stage_start_name") and data.get("stage_start_time"):
                    self.pending_stage = {
                        "name": data["stage_start_name"],
                        "start_time": data["stage_start_time"]
                    }
                    debug_log(f"📝 Stage开始缓存: {self.pending_stage['name']}")
                
                # ========== 处理Stage End ==========
                if data.get("stage_end_name") and data.get("stage_end_time"):
                    if self.pending_stage and self.pending_stage["name"] == data["stage_end_name"]:
                        result["stages"] = self._append_stage(
                            self.pending_stage["name"],
                            self.pending_stage["start_time"],
                            data["stage_end_time"]
                        )
                        self.pending_stage = None
                    else:
                        debug_log(f"⚠️ Stage End不匹配: expected={self.pending_stage}, got={data['stage_end_name']}")
            
            return result
            
        except Exception as e:
            debug_log(f"❌ 追加数据失败: {e}")
            return {"status": "error", "msg": f"追加数据失败：{str(e)}"}
    
    def _append_emg(self, dataset_name, emg_data, timestamps):
        """追加EMG数据
        
        Args:
            dataset_name: "emg1" 或 "emg2"
            emg_data: [通道][帧] 格式，16通道 x N帧
            timestamps: [t0, t1, ..., tN-1] N个时间戳
        """
        try:
            ds = self.f[dataset_name]
            
            # emg_data格式: [16通道][N帧] -> 转置为 [N帧][16通道]
            if not emg_data or len(emg_data) != 16:
                return 0
            
            num_frames = len(emg_data[0])
            if len(timestamps) != num_frames:
                # 如果时间戳数量不匹配，尝试插值或截断
                if len(timestamps) < num_frames:
                    # 复制最后一个时间戳
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
        """追加IMU数据
        
        Args:
            dataset_name: "imu1" 或 "imu2"
            imu_data: { acc: [ax,ay,az], gyr: [gx,gy,gz], mag: [mx,my,mz] }
            timestamps: [t0] 或 [t0, t1] 时间戳列表
        """
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
    
    def _append_prompt(self, name, time, stage_name=""):
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
            
            # 追加stage_name
            stage_ds = prompts["stage_names"]
            stage_ds.resize(stage_ds.shape[0] + 1, axis=0)
            stage_ds[-1] = str(stage_name)
            
            self.stats["prompts"] += 1
            
            return 1
            
        except Exception as e:
            debug_log(f"❌ 追加prompt失败: {e}")
            return 0
    
    def _append_stage(self, name, start_time, end_time):
        """追加Stage数据"""
        try:
            if not name or start_time <= 0:
                return 0
            
            stages = self.f["stages"]
            
            # 追加name
            names_ds = stages["names"]
            names_ds.resize(names_ds.shape[0] + 1, axis=0)
            names_ds[-1] = str(name)
            
            # 追加start_time
            start_ds = stages["start_times"]
            start_ds.resize(start_ds.shape[0] + 1, axis=0)
            start_ds[-1] = float(start_time)
            
            # 追加end_time
            end_ds = stages["end_times"]
            end_ds.resize(end_ds.shape[0] + 1, axis=0)
            end_ds[-1] = float(end_time)
            
            self.stats["stages"] += 1
            debug_log(f"✅ Stage已保存: {name} ({start_time:.3f} -> {end_time:.3f})")
            
            return 1
            
        except Exception as e:
            debug_log(f"❌ 追加stage失败: {e}")
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
                self.f.attrs["total_stages"] = self.stats["stages"]
                
                self.f.close()
                self.f = None
            
            self.is_collecting = False
            debug_log(f"✅ 文件已关闭: {self.file_path}")
            debug_log(f"📊 统计: {self.stats}")
            
            return {
                "status": "success",
                "msg": f"文件已保存并关闭：{self.file_path}",
                "file_path": self.file_path,
                "stats": self.stats
            }
            
        except Exception as e:
            debug_log(f"❌ 关闭文件失败: {e}")
            return {"status": "error", "msg": f"关闭文件失败：{str(e)}"}
    
    def get_stats(self):
        """获取统计信息"""
        if not self.f:
            return {"error": "文件未打开"}
        
        return {
            "file_path": self.file_path,
            "task_id": self.current_task_id,
            "user_id": self.current_user_id,
            "is_collecting": self.is_collecting,
            **self.stats
        }
    
    def run(self):
        """启动服务，循环处理客户端请求"""
        debug_log("🚀 存储服务开始运行...")
        
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
                else:
                    response = {
                        "status": "error",
                        "msg": f"未知指令：{cmd}，支持的指令：create/append/close/stats/flush"
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
    server = HDF5StorageServer()
    server.run()
