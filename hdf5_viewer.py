import h5py
import pandas as pd
import numpy as np
import os
import sys

# ===================== 配置项 =====================
# 匹配写入时的字符串格式
STR_MAX_LEN = 32
NAME_DTYPE = f"S{STR_MAX_LEN}"

# ===================== 核心读取函数 =====================
def print_hdf5_structure(file_path):
    """打印HDF5文件的完整结构（组/数据集/形状/类型）"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")
    
    print("="*80)
    print(f"📂 HDF5文件结构: {file_path}")
    print("="*80)
    
    with h5py.File(file_path, "r") as f:
        def print_node(name, obj):
            indent = "  " * name.count("/")
            if isinstance(obj, h5py.Group):
                print(f"{indent}📁 组: {name}")
            elif isinstance(obj, h5py.Dataset):
                # 打印数据集详情
                print(f"{indent}📊 数据集: {name}")
                print(f"{indent}   ├─ 形状: {obj.shape}")
                print(f"{indent}   ├─ 数据类型: {obj.dtype}")
                print(f"{indent}   └─ 属性: {dict(obj.attrs) if obj.attrs else '无'}")
        
        f.visititems(print_node)

def read_data_dataset(file_path, print_rows=5):
    """读取data数据集（完整打印16通道EMG原始数据 + 时间戳）"""
    print("\n" + "="*80)
    print("📈 Data数据集内容（16通道EMG原始数据）")
    print("="*80)
    
    with h5py.File(file_path, "r") as f:
        if "data" not in f:
            print("❌ 未找到data数据集")
            return
        
        ds = f["data"]
        total_rows = ds.shape[0]
        
        # 打印统计信息
        print(f"总条数: {total_rows}")
        print(f"每条包含: 16通道EMG数据（float32） + 时间戳（float64）")
        print(f"任务属性: {ds.attrs.get('task', '无')}")
        
        # 打印前N行完整数据
        if total_rows > 0:
            print(f"\n前{min(print_rows, total_rows)}行完整数据:")
            print("-" * 120)
            for i in range(min(print_rows, total_rows)):
                row = ds[i]
                time_stamp = row["time"]
                emg_data = row["emg"]  # 16通道原始数据
                
                # 打印行信息
                print(f"第{i}行 | 时间戳: {time_stamp:.6f}")
                print(f"      | 16通道EMG原始数据: {emg_data}")
                # 可选：格式化打印每个通道
                # print(f"      | 通道1: {emg_data[0]:.2f}, 通道2: {emg_data[1]:.2f}, ..., 通道16: {emg_data[15]:.2f}")
                print("-" * 120)
        else:
            print("📭 data数据集为空")

def read_prompts_dataset(file_path, print_rows=10):
    """读取prompts数据集（还原为DataFrame并打印）"""
    print("\n" + "="*80)
    print("📋 Prompts数据集内容")
    print("="*80)
    
    with h5py.File(file_path, "r") as f:
        if "prompts" not in f:
            print("❌ 未找到prompts组")
            return
        
        prompts = f["prompts"]
        # 检查必要数据集
        required = ["axis1", "block0", "block1"]
        if not all(k in prompts for k in required):
            print("❌ prompts组结构不完整")
            return
        
        # 读取数据
        idx = prompts["axis1"][:]
        names = prompts["block0"][:]
        times = prompts["block1"][:]
        total_rows = len(idx)
        
        # 转换字符串（bytes -> str）
        names_str = [name.decode('utf-8').strip('\x00') for name in names]
        
        # 构建DataFrame
        df = pd.DataFrame({
            "index": idx,
            "name": names_str,
            "time": times
        })
        
        # 打印统计信息
        print(f"总条数: {total_rows}")
        print(f"名称分布: {df['name'].value_counts().to_dict()}")
        
        # 打印前N行
        if total_rows > 0:
            print(f"\n前{min(print_rows, total_rows)}行数据:")
            print(df.head(print_rows).to_string(index=False))
        else:
            print("📭 prompts数据集为空")

def read_stages_dataset(file_path, print_rows=10):
    """读取stages数据集（还原为DataFrame并打印）"""
    print("\n" + "="*80)
    print("📌 Stages数据集内容")
    print("="*80)
    
    with h5py.File(file_path, "r") as f:
        if "stages" not in f:
            print("❌ 未找到stages组")
            return
        
        stages = f["stages"]
        # 检查必要数据集
        required = ["axis1", "block0", "block1"]
        if not all(k in stages for k in required):
            print("❌ stages组结构不完整")
            return
        
        # 读取数据
        idx = stages["axis1"][:]
        se_data = stages["block0"][:]  # start + end
        names = stages["block1"][:]
        total_rows = len(idx)
        
        # 转换字符串
        names_str = [name.decode('utf-8').strip('\x00') for name in names]
        
        # 构建DataFrame
        df = pd.DataFrame({
            "index": idx,
            "start": se_data[:, 0],
            "end": se_data[:, 1],
            "name": names_str
        })
        
        # 打印统计信息
        print(f"总条数: {total_rows}")
        print(f"名称分布: {df['name'].value_counts().to_dict()}")
        
        # 打印前N行
        if total_rows > 0:
            print(f"\n前{min(print_rows, total_rows)}行数据:")
            print(df.head(print_rows).to_string(index=False))
        else:
            print("📭 stages数据集为空")

def full_read_hdf5(file_path, data_print_rows=5, other_print_rows=10):
    """完整读取HDF5文件（结构+所有内容）"""
    try:
        # 1. 打印结构
        print_hdf5_structure(file_path)
        
        # 2. 读取data（打印更少行数，避免输出过长）
        read_data_dataset(file_path, print_rows=data_print_rows)
        
        # 3. 读取prompts
        read_prompts_dataset(file_path, print_rows=other_print_rows)
        
        # 4. 读取stages
        read_stages_dataset(file_path, print_rows=other_print_rows)
        
        print("\n" + "="*80)
        print("✅ 读取完成！")
        print("="*80)
        
    except Exception as e:
        print(f"\n❌ 读取失败: {str(e)}")
        import traceback
        traceback.print_exc()

# ===================== 主函数 =====================
if __name__ == "__main__":
    # 1. 检查命令行参数
    if len(sys.argv) != 2:
        print("❌ 使用方式错误！正确用法：")
        print(f"   python {sys.argv[0]} <HDF5文件路径>")
        print("   示例：python liangji_read.py ../storage/hdf5_1_1765591474009.h5")
        sys.exit(1)
    # 配置文件路径（与写入脚本的输出文件一致）
    HDF5_FILE_PATH = sys.argv[1]
    
    # 3. 检查文件是否存在
    if not os.path.exists(HDF5_FILE_PATH):
        print(f"❌ 文件不存在: {HDF5_FILE_PATH}")
        sys.exit(1)

    
    # 检查文件是否存在
    if not os.path.exists(HDF5_FILE_PATH):
        print(f"❌ 文件不存在: {HDF5_FILE_PATH}")
        print("请先运行写入脚本生成文件，或修改HDF5_FILE_PATH为正确路径")
    else:
        # 完整读取并打印
        # data_print_rows: data数据集打印行数（建议5行以内，避免输出过长）
        # other_print_rows: prompts/stages打印行数
        full_read_hdf5(HDF5_FILE_PATH, data_print_rows=5, other_print_rows=10)