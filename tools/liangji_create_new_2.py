import h5py
import pandas as pd
import numpy as np
import os
import warnings
import time
from threading import Lock

# ===================== 核心配置 =====================
DATA_DTYPE = np.dtype([("emg", "<f4", (16,)), ("time", "<f8")])
TASK_ATTR = "discrete_gestures"
STR_MAX_LEN = 32
NAME_DTYPE = f"S{STR_MAX_LEN}"

class HDF5HighFreqWriter:
    """高频追加专用HDF5写入器（极简版，无缓存，确保实时）"""
    def __init__(self, file_path, overwrite=True):
        self.file_path = file_path
        self.overwrite = overwrite
        self.f = None
        self.lock = Lock()

    def create_and_open(self):
        """创建结构并保持文件打开"""
        if self.overwrite and os.path.exists(self.file_path):
            os.remove(self.file_path)
        
        try:
            self.f = h5py.File(self.file_path, "a", libver='latest')
            if "data" not in self.f:
                # 创建Data数据集
                self.f.create_dataset(
                    "data", shape=(0,), dtype=DATA_DTYPE,
                    chunks=(10000,), maxshape=(None,), compression=None
                ).attrs["task"] = TASK_ATTR

                # 创建Prompts组
                prompts = self.f.create_group("prompts")
                prompts.create_dataset("axis0", data=np.array([b"name", b"time"], dtype="S4"))
                prompts.create_dataset("axis1", shape=(0,), dtype=np.int64, chunks=(1000,), maxshape=(None,))
                prompts.create_dataset("block0", shape=(0,), dtype=NAME_DTYPE, chunks=(1000,), maxshape=(None,))
                prompts.create_dataset("block1", shape=(0,), dtype=np.float64, chunks=(1000,), maxshape=(None,))

                # 创建Stages组（预留）
                stages = self.f.create_group("stages")
                stages.create_dataset("axis0", data=np.array([b"start", b"end", b"name"], dtype="S5"))
                stages.create_dataset("axis1", shape=(0,), dtype=np.int64, chunks=(100,), maxshape=(None,))
                stages.create_dataset("block0", shape=(0, 2), dtype=np.float64, chunks=(100, 2), maxshape=(None, 2))
                stages.create_dataset("block1", shape=(0,), dtype=NAME_DTYPE, chunks=(100,), maxshape=(None,))
            
            print(f"✅ 文件创建并保持打开: {self.file_path}")
            return True
        except Exception as e:
            print(f"❌ 创建失败: {e}")
            self.close()
            return False

    def append_data(self, data):
        """直接追加Data（无缓存，实时写入）"""
        with self.lock:
            ds = self.f["data"]
            current_len = ds.shape[0]
            new_len = current_len + len(data)
            ds.resize(new_len, axis=0)
            ds[current_len:new_len] = data
            return len(data)

    def append_prompts(self, df):
        """直接追加Prompts（无缓存，实时写入）"""
        with self.lock:
            prompts = self.f["prompts"]
            # 追加索引
            idx_ds = prompts["axis1"]
            current_idx = idx_ds.shape[0]
            new_idx = np.arange(len(df)) + current_idx
            idx_ds.resize(current_idx + len(new_idx), axis=0)
            idx_ds[current_idx:] = new_idx

            # 追加name
            name_ds = prompts["block0"]
            names = df["name"].astype(str).str.encode('utf-8').astype(NAME_DTYPE)
            name_ds.resize(name_ds.shape[0]+len(names), axis=0)
            name_ds[-len(names):] = names

            # 追加time
            time_ds = prompts["block1"]
            times = df["time"].values
            time_ds.resize(time_ds.shape[0]+len(times), axis=0)
            time_ds[-len(times):] = times
            return len(df)

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
def gen_data_batch(base_time, batch_idx):
    """生成5条16通道Data数据"""
    time_batch = np.array([
        base_time + batch_idx * 0.01 + i * 0.002 
        for i in range(5)
    ], dtype=np.float64)
    emg_batch = np.random.uniform(-2000, 3000, (5, 16)).astype(np.float32)
    
    data = np.empty(5, dtype=DATA_DTYPE)
    data["emg"] = emg_batch
    data["time"] = time_batch
    return data

def gen_prompt(base_time, batch_idx):
    """生成1条Prompts数据"""
    return pd.DataFrame({
        "name": [np.random.choice(["middle_press", "index_release", "thumb_up"])],
        "time": [base_time + batch_idx * 0.01]
    })

# ===================== 固定次数循环测试（彻底解决超时问题） =====================
def test_strict_cycle():
    warnings.filterwarnings("ignore")
    FILE_PATH = "high_freq_gestures.h5"
    
    # 核心参数（固定次数，不依赖时间判断）
    TOTAL_BATCHES = 500    # 500次循环 = 5秒（10ms/次）
    BATCH_INTERVAL = 0.01  # 10ms
    TRIGGER_BATCH_2 = 100  # 每100次触发第二种大包

    # 1. 创建写入器
    writer = HDF5HighFreqWriter(FILE_PATH, overwrite=True)
    if not writer.create_and_open():
        return

    # 2. 初始化
    base_time = 1633014930.533361
    total_data = 0
    total_prompts = 0
    start_time = time.time()

    print(f"\n🚀 开始固定次数测试（{TOTAL_BATCHES}次循环，{BATCH_INTERVAL*1000}ms/次）")
    print("-" * 70)

    # 3. 固定次数循环（核心：不用时间判断，确保执行500次）
    for batch_idx in range(TOTAL_BATCHES):
        batch_start = time.time()
        
        # 判断包类型（严格按计数，每100次触发）
        if (batch_idx + 1) % TRIGGER_BATCH_2 == 0:
            # 第二种大包：Data + Prompts
            data = gen_data_batch(base_time, batch_idx)
            prompt = gen_prompt(base_time, batch_idx)
            
            # 写入
            data_add = writer.append_data(data)
            prompt_add = writer.append_prompts(prompt)
            
            total_data += data_add
            total_prompts += prompt_add
            
            print(f"📦 第{batch_idx+1}批（第二种）| Data+{data_add} | Prompts+{prompt_add} | "
                  f"累计Data:{total_data} | 累计Prompts:{total_prompts}")
        else:
            # 第一种大包：仅Data
            data = gen_data_batch(base_time, batch_idx)
            data_add = writer.append_data(data)
            total_data += data_add
            
            print(f"📦 第{batch_idx+1}批（第一种）| Data+{data_add} | "
                  f"累计Data:{total_data} | 累计Prompts:{total_prompts}")

        # 精准控制间隔（补偿执行耗时）
        elapsed = time.time() - batch_start
        sleep_time = BATCH_INTERVAL - elapsed
        if sleep_time > 0 and sleep_time < BATCH_INTERVAL:
            time.sleep(sleep_time)

    # 4. 最终统计
    writer.flush()
    stats = writer.get_stats()
    actual_duration = time.time() - start_time

    print("-" * 70)
    print(f"\n✅ 测试完成！")
    print(f"📊 最终统计:")
    print(f"   实际执行批次: {TOTAL_BATCHES} (预期: {TOTAL_BATCHES})")
    print(f"   Data总条数: {stats['data_total']} (预期: {TOTAL_BATCHES*5})")
    print(f"   Prompts总条数: {stats['prompts_total']} (预期: {TOTAL_BATCHES//TRIGGER_BATCH_2})")
    print(f"   实际耗时: {actual_duration:.2f}秒 (预期: {TOTAL_BATCHES*BATCH_INTERVAL}秒)")

    # 5. 关闭文件
    writer.close()

if __name__ == "__main__":
    test_strict_cycle()