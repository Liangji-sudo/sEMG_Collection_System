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

# 【新增】EMG 250Hz ADC数据集类型：每帧16通道 + BLE帧号 + SD卡帧号 + 时间戳
# 用于存储250Hz原始ADC数据，后续与SD卡bin文件同步补全为2kHz
# 注意：channels存储的是原始ADC值（int32），不是μV值，需要乘以LSB系数才能转换为μV
EMG_250HZ_ADC_DTYPE = np.dtype([
    ("channels", "<i4", (16,)),   # 16通道EMG原始ADC值（非μV）
    ("frame_id", "<u4"),          # BLE帧号
    ("sd_frame_id", "<u4"),       # 对应的SD卡帧号 (= BLE帧号 * 8 + 7)
    ("time", "<f8")               # 时间戳
])

# 【新增】EMG 2kHz ADC数据集类型：每帧16通道 + SD卡帧号 + 时间戳
# 用于存储同步后的2kHz原始ADC数据
EMG_2KHZ_ADC_DTYPE = np.dtype([
    ("channels", "<i4", (16,)),   # 16通道EMG原始ADC值（非μV）
    ("sd_frame_id", "<u4"),       # SD卡帧号
    ("time", "<f8")               # 时间戳
])

# 【新增】IMU 250Hz数据集类型：acc(3) + gyr(3) + mag(3) + BLE帧号 + SD卡帧号 + 时间戳
# 用于存储250Hz原始数据，后续与SD卡bin文件同步补全为2kHz
IMU_250HZ_DTYPE = np.dtype([
    ("acc", "<f4", (3,)),   # 加速度计 [ax, ay, az]
    ("gyr", "<f4", (3,)),   # 陀螺仪 [gx, gy, gz]
    ("mag", "<f4", (3,)),   # 磁力计 [mx, my, mz]
    ("frame_id", "<u4"),    # BLE帧号
    ("sd_frame_id", "<u4"), # 对应的SD卡帧号 (= BLE帧号 * 8 + 7)
    ("time", "<f8")         # 时间戳
])

# 【新增】IMU 100Hz数据集类型：acc(3) + gyr(3) + mag(3) + SD卡帧号 + 时间戳
# 用于存储同步后的100Hz数据（IMU实际采样率为100Hz，非2kHz）
IMU_100HZ_DTYPE = np.dtype([
    ("acc", "<f4", (3,)),   # 加速度计 [ax, ay, az]
    ("gyr", "<f4", (3,)),   # 陀螺仪 [gx, gy, gz]
    ("mag", "<f4", (3,)),   # 磁力计 [mx, my, mz]
    ("sd_frame_id", "<u4"), # IMU SD卡帧号
    ("time", "<f8")         # 时间戳
])

# 【新增】MOCAP数据集类型：12个marker点的3D坐标 + 帧号 + 时间戳
# marker顺序: rt1, rt2, rt3, ri1, ri2, ri3, rm1, rm2, rm3, m1, m2, m3
MOCAP_MARKER_NAMES = ["rt1", "rt2", "rt3", "ri1", "ri2", "ri3", "rm1", "rm2", "rm3", "m1", "m2", "m3"]
MOCAP_DTYPE = np.dtype([
    ("rt1", "<f4", (3,)),   # 拇指指尖 [x, y, z]
    ("rt2", "<f4", (3,)),   # 拇指第一关节
    ("rt3", "<f4", (3,)),   # 拇指第二关节
    ("ri1", "<f4", (3,)),   # 食指指尖
    ("ri2", "<f4", (3,)),   # 食指第一关节
    ("ri3", "<f4", (3,)),   # 食指第二关节
    ("rm1", "<f4", (3,)),   # 中指指尖
    ("rm2", "<f4", (3,)),   # 中指第一关节
    ("rm3", "<f4", (3,)),   # 中指第二关节
    ("m1", "<f4", (3,)),    # 食指指根
    ("m2", "<f4", (3,)),    # 中指指根
    ("m3", "<f4", (3,)),    # 腕部
    ("frame", "<i4"),       # 帧号
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
            "mocap_frames": 0,  # 【新增】动捕帧数
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

            # 【新增】提取SD卡bin文件名（用于数据溯源）
            sd_bin_dev1 = params.get("sd_bin_dev1")  # 例如 "S001_L_260312_143025"
            sd_bin_dev2 = params.get("sd_bin_dev2")  # 例如 "S001_R_260312_143025"
            
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

            # 【新增】保存SD卡bin文件名到HDF5属性（用于数据溯源）
            # 格式: "S001_L_260312_143025" -> 对应SD卡文件 S001_L_260312_143025_emg.bin 和 S001_L_260312_143025_imu.bin
            if sd_bin_dev1:
                self.f.attrs["sd_bin_dev1"] = sd_bin_dev1
                debug_log(f"   SD卡源文件(设备1/左手): {sd_bin_dev1}_emg.bin, {sd_bin_dev1}_imu.bin")
            if sd_bin_dev2:
                self.f.attrs["sd_bin_dev2"] = sd_bin_dev2
                debug_log(f"   SD卡源文件(设备2/右手): {sd_bin_dev2}_emg.bin, {sd_bin_dev2}_imu.bin")
            
            # ===================== 创建受试者信息组 =====================
            if subject_info:
                subject_grp = self.f.create_group("subject")
                for key, value in subject_info.items():
                    if value is not None:
                        try:
                            subject_grp.attrs[str(key)] = str(value) if not isinstance(value, (int, float)) else value
                        except Exception as e:
                            debug_log(f"保存subject属性失败 {key}: {e}")

            # ===================== 创建EMG 250Hz ADC数据集（采集时写入） =====================
            # 这些数据集存储BLE传输的250Hz原始ADC数据，包含帧号用于后续与SD卡bin文件同步
            emg1_250hz_ds = self.f.create_dataset(
                "emg1_250hz_adc", shape=(0,), dtype=EMG_250HZ_ADC_DTYPE,
                chunks=(1000,), maxshape=(None,), compression="gzip"
            )
            emg1_250hz_ds.attrs["device"] = "device_1"
            emg1_250hz_ds.attrs["channels"] = 16
            emg1_250hz_ds.attrs["sample_rate"] = 250
            emg1_250hz_ds.attrs["data_type"] = "raw_adc"  # 原始ADC值，非μV
            emg1_250hz_ds.attrs["description"] = "250Hz EMG raw ADC data from BLE (not uV, multiply by LSB to convert)"

            emg2_250hz_ds = self.f.create_dataset(
                "emg2_250hz_adc", shape=(0,), dtype=EMG_250HZ_ADC_DTYPE,
                chunks=(1000,), maxshape=(None,), compression="gzip"
            )
            emg2_250hz_ds.attrs["device"] = "device_2"
            emg2_250hz_ds.attrs["channels"] = 16
            emg2_250hz_ds.attrs["sample_rate"] = 250
            emg2_250hz_ds.attrs["data_type"] = "raw_adc"  # 原始ADC值，非μV
            emg2_250hz_ds.attrs["description"] = "250Hz EMG raw ADC data from BLE (not uV, multiply by LSB to convert)"

            # ===================== 创建EMG 2kHz ADC数据集（同步后写入，初始为空） =====================
            emg1_2khz_ds = self.f.create_dataset(
                "emg1_2khz_adc", shape=(0,), dtype=EMG_2KHZ_ADC_DTYPE,
                chunks=(1000,), maxshape=(None,), compression="gzip"
            )
            emg1_2khz_ds.attrs["device"] = "device_1"
            emg1_2khz_ds.attrs["channels"] = 16
            emg1_2khz_ds.attrs["sample_rate"] = 2000
            emg1_2khz_ds.attrs["data_type"] = "raw_adc"  # 原始ADC值，非μV
            emg1_2khz_ds.attrs["description"] = "2kHz EMG raw ADC data (synced from SD card bin, empty until sync)"

            emg2_2khz_ds = self.f.create_dataset(
                "emg2_2khz_adc", shape=(0,), dtype=EMG_2KHZ_ADC_DTYPE,
                chunks=(1000,), maxshape=(None,), compression="gzip"
            )
            emg2_2khz_ds.attrs["device"] = "device_2"
            emg2_2khz_ds.attrs["channels"] = 16
            emg2_2khz_ds.attrs["sample_rate"] = 2000
            emg2_2khz_ds.attrs["data_type"] = "raw_adc"  # 原始ADC值，非μV
            emg2_2khz_ds.attrs["description"] = "2kHz EMG raw ADC data (synced from SD card bin, empty until sync)"

            # ===================== 创建IMU 250Hz数据集（采集时写入） =====================
            imu1_250hz_ds = self.f.create_dataset(
                "imu1_250hz", shape=(0,), dtype=IMU_250HZ_DTYPE,
                chunks=(500,), maxshape=(None,), compression="gzip"
            )
            imu1_250hz_ds.attrs["device"] = "device_1"
            imu1_250hz_ds.attrs["sample_rate"] = 250
            imu1_250hz_ds.attrs["description"] = "250Hz IMU data from BLE for sync with SD card bin"

            imu2_250hz_ds = self.f.create_dataset(
                "imu2_250hz", shape=(0,), dtype=IMU_250HZ_DTYPE,
                chunks=(500,), maxshape=(None,), compression="gzip"
            )
            imu2_250hz_ds.attrs["device"] = "device_2"
            imu2_250hz_ds.attrs["sample_rate"] = 250
            imu2_250hz_ds.attrs["description"] = "250Hz IMU data from BLE for sync with SD card bin"

            # ===================== 创建IMU 100Hz数据集（同步后写入，初始为空） =====================
            imu1_100hz_ds = self.f.create_dataset(
                "imu1_100hz", shape=(0,), dtype=IMU_100HZ_DTYPE,
                chunks=(500,), maxshape=(None,), compression="gzip"
            )
            imu1_100hz_ds.attrs["device"] = "device_1"
            imu1_100hz_ds.attrs["sample_rate"] = 100
            imu1_100hz_ds.attrs["description"] = "100Hz IMU data (synced from SD card bin, empty until sync)"

            imu2_100hz_ds = self.f.create_dataset(
                "imu2_100hz", shape=(0,), dtype=IMU_100HZ_DTYPE,
                chunks=(500,), maxshape=(None,), compression="gzip"
            )
            imu2_100hz_ds.attrs["device"] = "device_2"
            imu2_100hz_ds.attrs["sample_rate"] = 100
            imu2_100hz_ds.attrs["description"] = "100Hz IMU data (synced from SD card bin, empty until sync)"

            # ===================== 同步状态标记 =====================
            # pending: 待同步（250Hz数据，需要与SD卡bin同步补全为2kHz）
            # synced: 已同步（已补全为2kHz数据）
            self.f.attrs["sync_status"] = "pending"
            self.f.attrs["ble_sample_rate"] = 250  # BLE传输采样率
            self.f.attrs["target_sample_rate"] = 2000  # 目标采样率（SD卡存储）

            # ===================== 【新增】创建MOCAP数据集 =====================
            mocap_ds = self.f.create_dataset(
                "mocap", shape=(0,), dtype=MOCAP_DTYPE,
                chunks=(500,), maxshape=(None,), compression="gzip"
            )
            mocap_ds.attrs["description"] = "Motion capture marker data (12 markers x 3D coordinates)"
            mocap_ds.attrs["markers"] = str(MOCAP_MARKER_NAMES)
            mocap_ds.attrs["coordinate_unit"] = "mm"

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
                "mocap_frames": 0,  # 【新增】
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
                # 追加EMG1到250Hz数据集
                if data.get("emg1") and data.get("emg1_t"):
                    frame_ids = data.get("emg1_frame_ids")
                    self._append_emg("emg1", data["emg1"], data["emg1_t"], frame_ids)

                # 追加EMG2到250Hz数据集
                if data.get("emg2") and data.get("emg2_t"):
                    frame_ids = data.get("emg2_frame_ids")
                    self._append_emg("emg2", data["emg2"], data["emg2_t"], frame_ids)

                # 追加IMU1到250Hz数据集
                if data.get("imu1") and data.get("imu1_t"):
                    # IMU帧号使用EMG帧号的第一个（同一个BLE包）
                    frame_id = data.get("emg1_frame_ids", [0])[0] if data.get("emg1_frame_ids") else None
                    self._append_imu("imu1", data["imu1"], data["imu1_t"], frame_id)

                # 追加IMU2到250Hz数据集
                if data.get("imu2") and data.get("imu2_t"):
                    frame_id = data.get("emg2_frame_ids", [0])[0] if data.get("emg2_frame_ids") else None
                    self._append_imu("imu2", data["imu2"], data["imu2_t"], frame_id)

                # 【修改】批量追加MOCAP
                if data.get("mocap_frames"):
                    self._append_mocap_batch(data["mocap_frames"])
                # 兼容单帧模式
                elif data.get("mocap"):
                    self._append_mocap(data["mocap"], data.get("mocap_t", 0), data.get("mocap_frame", 0))

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
    
    def _append_emg(self, dataset_name, emg_data, timestamps, frame_ids=None):
        """追加EMG数据到250Hz ADC数据集

        采集时只保存到250Hz ADC数据集（emg1_250hz_adc/emg2_250hz_adc），
        emg1_2khz_adc/emg2_2khz_adc数据集在同步后由bin_sync_tool填充2kHz数据。

        Args:
            dataset_name: 数据集名称 (emg1 或 emg2)
            emg_data: EMG数据 (16通道 x N帧)
            timestamps: 时间戳列表
            frame_ids: BLE帧号列表（用于与SD卡bin文件同步）
        """
        try:
            if not emg_data or len(emg_data) != 16:
                return 0

            num_frames = len(emg_data[0])
            if len(timestamps) != num_frames:
                if len(timestamps) < num_frames:
                    timestamps = list(timestamps) + [timestamps[-1]] * (num_frames - len(timestamps))
                else:
                    timestamps = timestamps[:num_frames]

            # 确保frame_ids存在且长度匹配
            if frame_ids is None:
                frame_ids = list(range(num_frames))  # 默认从0开始
            elif len(frame_ids) != num_frames:
                if len(frame_ids) < num_frames:
                    last_id = frame_ids[-1] if frame_ids else 0
                    frame_ids = list(frame_ids) + [last_id + j + 1 for j in range(num_frames - len(frame_ids))]
                else:
                    frame_ids = frame_ids[:num_frames]

            # 保存到250Hz ADC数据集
            ds_250hz_name = f"{dataset_name}_250hz_adc"
            if ds_250hz_name in self.f:
                ds_250hz = self.f[ds_250hz_name]

                # 构造250Hz结构化数组
                data_250hz = np.empty(num_frames, dtype=EMG_250HZ_ADC_DTYPE)
                for i in range(num_frames):
                    channels = [emg_data[ch][i] for ch in range(16)]
                    ble_frame_id = frame_ids[i]
                    # 计算对应的SD卡帧号: SD帧号 = BLE帧号 * 8 + 7
                    sd_frame_id = ble_frame_id * 8 + 7

                    data_250hz[i]["channels"] = np.array(channels, dtype=np.int32)
                    data_250hz[i]["frame_id"] = ble_frame_id
                    data_250hz[i]["sd_frame_id"] = sd_frame_id
                    data_250hz[i]["time"] = timestamps[i]

                # 追加到250Hz数据集
                current_len = ds_250hz.shape[0]
                new_len = current_len + num_frames
                ds_250hz.resize(new_len, axis=0)
                ds_250hz[current_len:new_len] = data_250hz

            # 更新统计
            self.stats[f"{dataset_name}_frames"] += num_frames

            return num_frames

        except Exception as e:
            debug_log(f"❌ 追加{dataset_name}失败: {e}")
            return 0

    def _append_imu(self, dataset_name, imu_data, timestamps, frame_id=None):
        """追加IMU数据到250Hz数据集

        采集时只保存到250Hz数据集（imu1_250hz/imu2_250hz），
        imu1/imu2数据集在同步后由bin_sync_tool填充100Hz数据。

        Args:
            dataset_name: 数据集名称 (imu1 或 imu2)
            imu_data: IMU数据 {acc, gyr, mag}
            timestamps: 时间戳列表
            frame_id: BLE帧号（用于与SD卡bin文件同步）
        """
        try:
            if not imu_data or "acc" not in imu_data:
                return 0

            # 保存到250Hz数据集
            ds_250hz_name = f"{dataset_name}_250hz"
            if ds_250hz_name in self.f:
                ds_250hz = self.f[ds_250hz_name]

                # 计算BLE帧号和SD卡帧号
                ble_frame_id = frame_id if frame_id is not None else 0
                # SD帧号 = BLE帧号 * 8 + 7
                sd_frame_id = ble_frame_id * 8 + 7

                # 构造250Hz结构化数组（每次一帧）
                data_250hz = np.empty(1, dtype=IMU_250HZ_DTYPE)
                data_250hz[0]["acc"] = np.array(imu_data.get("acc", [0, 0, 0])[:3], dtype=np.float32)
                data_250hz[0]["gyr"] = np.array(imu_data.get("gyr", [0, 0, 0])[:3], dtype=np.float32)
                data_250hz[0]["mag"] = np.array(imu_data.get("mag", [0, 0, 0])[:3], dtype=np.float32)
                data_250hz[0]["frame_id"] = ble_frame_id
                data_250hz[0]["sd_frame_id"] = sd_frame_id
                data_250hz[0]["time"] = timestamps[0] if timestamps else 0

                # 追加到250Hz数据集
                current_len = ds_250hz.shape[0]
                ds_250hz.resize(current_len + 1, axis=0)
                ds_250hz[current_len] = data_250hz[0]

            # 更新统计
            self.stats[f"{dataset_name}_frames"] += 1

            return 1

        except Exception as e:
            debug_log(f"❌ 追加{dataset_name}失败: {e}")
            return 0

    def _append_mocap(self, markers_data, timestamp, frame):
        """追加MOCAP数据"""
        try:
            ds = self.f["mocap"]

            if not markers_data:
                return 0

            # 构造结构化数组（每次一帧）
            data_struct = np.empty(1, dtype=MOCAP_DTYPE)

            # 填充12个marker点的坐标
            for marker_name in MOCAP_MARKER_NAMES:
                coords = markers_data.get(marker_name, [0, 0, 0])
                data_struct[0][marker_name] = np.array(coords[:3], dtype=np.float32)

            data_struct[0]["frame"] = int(frame) if frame else 0
            data_struct[0]["time"] = float(timestamp) if timestamp else 0

            # 追加到数据集
            current_len = ds.shape[0]
            ds.resize(current_len + 1, axis=0)
            ds[current_len] = data_struct[0]

            # 更新统计
            self.stats["mocap_frames"] += 1

            return 1

        except Exception as e:
            debug_log(f"❌ 追加mocap失败: {e}")
            return 0

    def _append_mocap_batch(self, frames_data):
        """批量追加MOCAP数据"""
        try:
            ds = self.f["mocap"]

            if not frames_data or len(frames_data) == 0:
                return 0

            num_frames = len(frames_data)

            # 构造结构化数组（批量）
            data_struct = np.empty(num_frames, dtype=MOCAP_DTYPE)

            for i, frame_data in enumerate(frames_data):
                markers_data = frame_data.get("markers", {})
                frame_num = frame_data.get("frame", 0)
                # 优先使用系统时间戳 sys_time，与蓝牙数据、prompt保持一致
                timestamp = frame_data.get("sys_time", frame_data.get("time", 0))

                # 填充12个marker点的坐标
                for marker_name in MOCAP_MARKER_NAMES:
                    coords = markers_data.get(marker_name, [0, 0, 0])
                    data_struct[i][marker_name] = np.array(coords[:3], dtype=np.float32)

                data_struct[i]["frame"] = int(frame_num) if frame_num else 0
                data_struct[i]["time"] = float(timestamp) if timestamp else 0

            # 批量追加到数据集
            current_len = ds.shape[0]
            new_len = current_len + num_frames
            ds.resize(new_len, axis=0)
            ds[current_len:new_len] = data_struct

            # 更新统计
            self.stats["mocap_frames"] += num_frames

            return num_frames

        except Exception as e:
            debug_log(f"❌ 批量追加mocap失败: {e}")
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
                     f"MOCAP={self.stats['mocap_frames']}, Prompts={self.stats['prompts']}")
            
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
