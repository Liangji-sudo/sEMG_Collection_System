import h5py
import pandas as pd
import numpy as np
import os
import warnings

# ===================== 核心配置（完全匹配目标结构） =====================
# Data数据集
DATA_DTYPE = np.dtype([("emg", "<f4", (16,)), ("time", "<f8")])
TASK_ATTR = "discrete_gestures"

# Prompts/Stages 字符串存储配置（固定长度，避免变长内存问题）
STR_MAX_LEN = 32  # 足够存储所有手势/阶段名称
NAME_DTYPE = f"S{STR_MAX_LEN}"  # 固定长度字符串

# ===================== 1. 创建基础结构（无变长字符串） =====================
def create_hdf5_structure(file_path):
    """创建无变长字符串的稳定结构"""
    # 覆盖已有文件
    if os.path.exists(file_path):
        os.remove(file_path)
    
    try:
        with h5py.File(file_path, "w") as f:
            # ---------- Data数据集 ----------
            f.create_dataset(
                "data",
                shape=(0,),
                dtype=DATA_DTYPE,
                chunks=(10000,),
                maxshape=(None,)
            ).attrs["task"] = TASK_ATTR

            # ---------- Prompts组 ----------
            prompts = f.create_group("prompts")
            # axis0: 列名 [name, time]
            prompts.create_dataset("axis0", data=np.array([b"name", b"time"], dtype="S4"))
            # axis1: 行索引（可扩展）
            prompts.create_dataset("axis1", shape=(0,), dtype=np.int64, chunks=(1000,), maxshape=(None,))
            # block0: name列（固定长度字符串）
            prompts.create_dataset("block0", shape=(0,), dtype=NAME_DTYPE, chunks=(1000,), maxshape=(None,))
            # block1: time列
            prompts.create_dataset("block1", shape=(0,), dtype=np.float64, chunks=(1000,), maxshape=(None,))

            # ---------- Stages组 ----------
            stages = f.create_group("stages")
            # axis0: 列名 [start, end, name]
            stages.create_dataset("axis0", data=np.array([b"start", b"end", b"name"], dtype="S5"))
            # axis1: 行索引（可扩展）
            stages.create_dataset("axis1", shape=(0,), dtype=np.int64, chunks=(100,), maxshape=(None,))
            # block0: start+end
            stages.create_dataset("block0", shape=(0, 2), dtype=np.float64, chunks=(100, 2), maxshape=(None, 2))
            # block1: name列（固定长度字符串）
            stages.create_dataset("block1", shape=(0,), dtype=NAME_DTYPE, chunks=(100,), maxshape=(None,))

        print(f"✅ 成功创建文件: {file_path}")
        return True
    except Exception as e:
        print(f"❌ 创建失败: {e}")
        return False

# ===================== 2. 追加数据（核心功能） =====================
def append_data(file_path, data_dict):
    """
    追加数据到HDF5
    data_dict格式:
    {
        "data": 结构化数组 (可选),
        "prompts": DataFrame(name, time) (可选),
        "stages": DataFrame(start, end, name) (可选)
    }
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    try:
        with h5py.File(file_path, "a") as f:
            # ---------- 追加Data ----------
            if "data" in data_dict and data_dict["data"] is not None:
                data = data_dict["data"]
                ds = f["data"]
                new_len = ds.shape[0] + len(data)
                ds.resize(new_len, axis=0)
                ds[-len(data):] = data
                print(f"✅ Data追加: 新增{len(data)}条 | 总计{ds.shape[0]}条")

            # ---------- 追加Prompts ----------
            if "prompts" in data_dict and data_dict["prompts"] is not None:
                df = data_dict["prompts"]
                prompts = f["prompts"]
                
                # 追加行索引
                idx_ds = prompts["axis1"]
                new_idx = np.arange(len(df)) + idx_ds.shape[0]
                idx_ds.resize(idx_ds.shape[0]+len(new_idx), axis=0)
                idx_ds[-len(new_idx):] = new_idx

                # 追加name（转固定长度字符串）
                name_ds = prompts["block0"]
                names = df["name"].astype(str).str.encode('utf-8').astype(NAME_DTYPE)
                name_ds.resize(name_ds.shape[0]+len(names), axis=0)
                name_ds[-len(names):] = names

                # 追加time
                time_ds = prompts["block1"]
                times = df["time"].values
                time_ds.resize(time_ds.shape[0]+len(times), axis=0)
                time_ds[-len(times):] = times

                print(f"✅ Prompts追加: 新增{len(df)}条 | 总计{idx_ds.shape[0]}条")

            # ---------- 追加Stages ----------
            if "stages" in data_dict and data_dict["stages"] is not None:
                df = data_dict["stages"]
                stages = f["stages"]
                
                # 追加行索引
                idx_ds = stages["axis1"]
                new_idx = np.arange(len(df)) + idx_ds.shape[0]
                idx_ds.resize(idx_ds.shape[0]+len(new_idx), axis=0)
                idx_ds[-len(new_idx):] = new_idx

                # 追加start+end
                se_ds = stages["block0"]
                se_data = df[["start", "end"]].values
                se_ds.resize(se_ds.shape[0]+len(se_data), axis=0)
                se_ds[-len(se_data):] = se_data

                # 追加name（转固定长度字符串）
                name_ds = stages["block1"]
                names = df["name"].astype(str).str.encode('utf-8').astype(NAME_DTYPE)
                name_ds.resize(name_ds.shape[0]+len(names), axis=0)
                name_ds[-len(names):] = names

                print(f"✅ Stages追加: 新增{len(df)}条 | 总计{idx_ds.shape[0]}条")

        print("\n✅ 所有数据追加完成！")
    except Exception as e:
        print(f"❌ 追加失败: {e}")
        raise

# ===================== 3. 测试函数（可直接运行） =====================
def test():
    warnings.filterwarnings("ignore")
    FILE_PATH = "standard_gestures.h5"

    # 1. 创建基础结构
    if not create_hdf5_structure(FILE_PATH):
        return

    # 2. 生成测试数据
    def gen_test_data():
        # Data数据（5000条）
        time_start = 1633014930.533361
        time_array = np.linspace(time_start, time_start+5000/2000, 5000)
        emg_array = np.random.uniform(-2000, 3000, (5000,16)).astype(np.float32)
        data = np.empty(5000, dtype=DATA_DTYPE)
        data["emg"] = emg_array
        data["time"] = time_array

        # Prompts数据（50条）
        prompts = pd.DataFrame({
            "name": np.random.choice(["middle_press", "index_release", "thumb_up"], 50),
            "time": np.linspace(time_start, time_start+100, 50)
        })

        # Stages数据（3条）
        stages = pd.DataFrame({
            "start": np.linspace(time_start, time_start+100, 3),
            "end": np.linspace(time_start+10, time_start+110, 3),
            "name": np.random.choice(["practice", "static", "dynamic"], 3)
        })

        return {"data": data, "prompts": prompts, "stages": stages}

    # 3. 第一次追加
    print("\n=== 第一次追加 ===")
    data1 = gen_test_data()
    append_data(FILE_PATH, data1)

    # 4. 第二次追加（仅Data+Prompts）
    print("\n=== 第二次追加 ===")
    data2 = gen_test_data()
    data2["stages"] = None  # 不追加Stages
    append_data(FILE_PATH, data2)

    # 5. 验证结果
    print("\n=== 验证结果 ===")
    with h5py.File(FILE_PATH, "r") as f:
        print(f"Data总长度: {f['data'].shape[0]}")
        print(f"Prompts总条数: {f['prompts/axis1'].shape[0]}")
        print(f"Stages总条数: {f['stages/axis1'].shape[0]}")
        print(f"Data任务属性: {f['data'].attrs['task']}")

if __name__ == "__main__":
    test()