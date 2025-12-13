import h5py
import pandas as pd
import numpy as np
import os
import sys
import traceback

# ===================== 核心工具函数 =====================
def print_hdf5_structure(file_path):
    """打印HDF5文件的完整结构"""
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
                print(f"{indent}📊 数据集: {name}")
                print(f"{indent}   ├─ 形状: {obj.shape}")
                print(f"{indent}   ├─ 数据类型: {obj.dtype}")
                print(f"{indent}   └─ 属性: {dict(obj.attrs) if obj.attrs else '无'}")
        
        f.visititems(print_node)

def read_vlarray_string(ds):
    """读取VLARRAY类型的object字符串数据"""
    try:
        # 读取原始数据
        data = ds[:]
        if len(data) == 0:
            return []
        
        # 处理单元素object数组
        if isinstance(data[0], np.ndarray):
            # 嵌套数组处理
            str_list = []
            for arr in data:
                for item in arr:
                    if isinstance(item, bytes):
                        str_list.append(item.decode('utf-8').strip('\x00'))
                    else:
                        str_list.append(str(item).strip('\x00'))
            return str_list
        elif isinstance(data[0], bytes):
            return [item.decode('utf-8').strip('\x00') for item in data]
        elif isinstance(data[0], (np.bytes_, np.str_)):
            return [str(item).strip('\x00') for item in data]
        else:
            # 直接读取VLARRAY内容
            str_list = []
            for item in data:
                try:
                    str_list.append(str(item).strip('\x00'))
                except:
                    str_list.append("unknown")
            return str_list
    except Exception as e:
        print(f"⚠️ 读取字符串数据时警告: {e}")
        return ["unknown"] * len(ds[:])

# ===================== 数据读取函数 =====================
def read_data_dataset(file_path, print_rows=5):
    """读取data数据集"""
    print("\n" + "="*80)
    print("📈 Data数据集内容（16通道EMG原始数据）")
    print("="*80)
    
    with h5py.File(file_path, "r") as f:
        if "data" not in f:
            print("❌ 未找到data数据集")
            return
        
        ds = f["data"]
        total_rows = ds.shape[0]
        print(f"总条数: {total_rows}")
        print(f"每条包含: 16通道EMG数据（float32） + 时间戳（float64）")
        print(f"任务属性: {ds.attrs.get('task', '无')}")
        
        if total_rows > 0:
            print(f"\n前{min(print_rows, total_rows)}行完整数据:")
            print("-" * 120)
            for i in range(min(print_rows, total_rows)):
                row = ds[i]
                print(f"第{i}行 | 时间戳: {row['time']:.6f}")
                print(f"      | 16通道EMG原始数据: {row['emg']}")
                print("-" * 120)
        else:
            print("📭 data数据集为空")

def read_prompts_dataset(file_path, print_rows=10):
    """读取prompts组 - 完全适配你的文件结构"""
    print("\n" + "="*80)
    print("📋 Prompts数据集内容")
    print("="*80)
    
    try:
        with h5py.File(file_path, "r") as f:
            if "prompts" not in f:
                print("❌ 未找到prompts组")
                return
            
            prompts = f["prompts"]
            
            # 强制读取所有需要的字段（基于你的文件结构）
            idx = prompts["axis1"][:].flatten()  # (1900,)
            block0_vals = prompts["block0_values"]  # (1,) object
            block1_vals = prompts["block1_values"][:].flatten()  # (1900,1) -> (1900,)
            
            # 读取名称（处理VLARRAY object类型）
            names = read_vlarray_string(block0_vals)
            
            # 如果名称只有1个，复制到和索引同长度
            if len(names) == 1 and len(idx) > 1:
                names = names * len(idx)
            
            # 确保长度匹配
            max_len = len(idx)
            names = names[:max_len] if len(names) > max_len else names + ["unknown"] * (max_len - len(names))
            times = block1_vals[:max_len] if len(block1_vals) > max_len else np.pad(block1_vals, (0, max_len - len(block1_vals)), mode='constant')
            
            # 构建DataFrame
            df = pd.DataFrame({
                "index": idx,
                "name": names,
                "time": times
            })
            
            # 打印结果
            print(f"✅ 成功解析prompts数据")
            print(f"总条数: {len(df)}")
            print(f"名称分布: {df['name'].value_counts().to_dict()}")
            
            if len(df) > 0:
                print(f"\n前{min(print_rows, len(df))}行数据:")
                print(df.head(print_rows).to_string(index=False))
            else:
                print("📭 prompts数据集为空")
                
    except Exception as e:
        print(f"❌ 解析prompts失败: {str(e)}")
        traceback.print_exc()

def read_stages_dataset(file_path, print_rows=10):
    """读取stages组 - 完全适配你的文件结构"""
    print("\n" + "="*80)
    print("📌 Stages数据集内容")
    print("="*80)
    
    try:
        with h5py.File(file_path, "r") as f:
            if "stages" not in f:
                print("❌ 未找到stages组")
                return
            
            stages = f["stages"]
            
            # 强制读取所有需要的字段（基于你的文件结构）
            idx = stages["axis1"][:].flatten()  # (16,)
            block0_vals = stages["block0_values"][:]  # (16,2) float64
            block1_vals = stages["block1_values"]  # (1,) object
            
            # 读取阶段名称（处理VLARRAY object类型）
            names = read_vlarray_string(block1_vals)
            
            # 如果名称只有1个，复制到和索引同长度
            if len(names) == 1 and len(idx) > 1:
                names = names * len(idx)
            
            # 确保长度匹配
            max_len = len(idx)
            names = names[:max_len] if len(names) > max_len else names + ["unknown"] * (max_len - len(names))
            
            # 构建DataFrame
            df = pd.DataFrame({
                "index": idx,
                "start": block0_vals[:max_len, 0],
                "end": block0_vals[:max_len, 1],
                "name": names
            })
            
            # 打印结果
            print(f"✅ 成功解析stages数据")
            print(f"总条数: {len(df)}")
            print(f"名称分布: {df['name'].value_counts().to_dict()}")
            
            if len(df) > 0:
                print(f"\n前{min(print_rows, len(df))}行数据:")
                print(df.head(print_rows).to_string(index=False))
            else:
                print("📭 stages数据集为空")
                
    except Exception as e:
        print(f"❌ 解析stages失败: {str(e)}")
        traceback.print_exc()

def full_read_hdf5(file_path):
    """完整读取HDF5文件"""
    try:
        print_hdf5_structure(file_path)
        read_data_dataset(file_path, print_rows=5)
        read_prompts_dataset(file_path, print_rows=10)
        read_stages_dataset(file_path, print_rows=10)
        
        print("\n" + "="*80)
        print("✅ 所有数据读取完成！")
        print("="*80)
        
    except Exception as e:
        print(f"\n❌ 读取失败: {str(e)}")
        traceback.print_exc()

# ===================== 主函数 =====================
if __name__ == "__main__":
    # 检查参数
    if len(sys.argv) != 2:
        print("❌ 使用方式错误！正确用法：")
        print(f"   python {sys.argv[0]} <HDF5文件路径>")
        print("   示例：python hdf5_viewer.py ./discrete_gestures_user_000_dataset_000.hdf5")
        sys.exit(1)
    
    # 获取文件路径
    HDF5_FILE_PATH = sys.argv[1]
    
    # 检查文件存在性
    if not os.path.exists(HDF5_FILE_PATH):
        print(f"❌ 文件不存在: {HDF5_FILE_PATH}")
        sys.exit(1)
    
    # 执行完整读取
    full_read_hdf5(HDF5_FILE_PATH)