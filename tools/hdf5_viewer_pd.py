import h5py
import pandas as pd
import numpy as np
import sys
import os

def print_hdf5_structure(file_path, target_task="discrete_gestures"):
    """
    详细打印discrete_gestures类型HDF5文件的内部结构
    :param file_path: HDF5文件路径
    :param target_task: 目标任务类型（固定为discrete_gestures）
    """
    print("="*80)
    print(f"开始解析HDF5文件: {file_path}")
    print("="*80)

    # 打开HDF5文件（只读模式）
    with h5py.File(file_path, "r") as f:
        # 1. 验证文件是否为目标任务类型
        if "data" not in f:
            print("[错误] 文件中未找到核心数据集 'data'")
            return
        
        # 读取task属性并验证
        task_attr = f["data"].attrs.get("task", "未知")
        print(f"1. 文件任务类型: {task_attr}")
        if task_attr != target_task:
            print(f"[警告] 该文件不是{target_task}类型，以下为实际结构")
        
        # 2. 递归打印所有组/数据集的基础信息
        print("\n2. 文件完整层级结构:")
        def print_item(name, obj):
            indent = "  " * (name.count("/"))
            if isinstance(obj, h5py.Group):
                print(f"{indent}📁 组: {name}")
            elif isinstance(obj, h5py.Dataset):
                # 数据集基础信息
                print(f"{indent}📊 数据集: {name}")
                print(f"{indent}   - 数据类型: {obj.dtype}")
                print(f"{indent}   - 数据形状: {obj.shape}")
                print(f"{indent}   - 数据大小: {obj.size} 个元素")
                print(f"{indent}   - 占用空间: {obj.nbytes / 1024 / 1024:.2f} MB")
                
                # 如果是结构化数组，打印字段详情
                if obj.dtype.names:
                    print(f"{indent}   - 结构化字段:")
                    for field in obj.dtype.names:
                        field_dtype = obj.dtype[field]
                        print(f"{indent}     * {field}: {field_dtype}")
                
                # 打印数据集属性
                if obj.attrs:
                    print(f"{indent}   - 数据集属性:")
                    for k, v in obj.attrs.items():
                        print(f"{indent}     * {k}: {v}")
        
        f.visititems(print_item)

        # 3. 解析discrete_gestures核心数据详情（data数据集）
        print("\n3. discrete_gestures核心数据详情 (data数据集):")
        data_ds = f["data"]
        # 3.1 基础字段验证
        required_fields = ["emg", "time"]
        optional_fields = []  # discrete_gestures无额外必选字段
        print(f"   必选字段检查:")
        for field in required_fields:
            if field in data_ds.dtype.names:
                field_data = data_ds[field]
                print(f"     ✅ {field}: 存在 | 形状: {field_data.shape} | 数据范围: [{np.min(field_data):.6f}, {np.max(field_data):.6f}]")
            else:
                print(f"     ❌ {field}: 缺失")
        
        # 3.2 EMG信号详情（核心字段）
        if "emg" in data_ds.dtype.names:
            emg_data = data_ds["emg"]
            print(f"   EMG信号详情:")
            print(f"     - 总时长: {data_ds['time'][-1] - data_ds['time'][0]:.2f} 秒")
            print(f"     - 采样点数: {emg_data.shape[0]}")
            print(f"     - 通道数: {emg_data.shape[1]}")
            print(f"     - 采样率: {emg_data.shape[0] / (data_ds['time'][-1] - data_ds['time'][0]):.2f} Hz")
            print(f"     - 各通道范围:")
            for ch in range(emg_data.shape[1]):
                ch_data = emg_data[:, ch]
                print(f"       通道{ch+1}: [{np.min(ch_data):.6f}, {np.max(ch_data):.6f}] V")

        # 4. 解析辅助表格数据（stages/prompts）
        print("\n4. 辅助表格数据详情:")
        # 4.1 stages表格
        try:
            stages_df = pd.read_hdf(file_path, key="stages")
            print(f"   stages表格:")
            print(f"     - 行数: {len(stages_df)} | 列数: {len(stages_df.columns)}")
            print(f"     - 列名: {list(stages_df.columns)}")
            print(f"     - 数据:\n{stages_df}")
        except Exception as e:
            print(f"   stages表格: 读取失败 - {str(e)}")
        
        # 4.2 prompts表格（discrete_gestures必选）
        try:
            prompts_df = pd.read_hdf(file_path, key="prompts")
            print(f"   prompts表格:")
            print(f"     - 行数: {len(prompts_df)} | 列数: {len(prompts_df.columns)}")
            print(f"     - 列名: {list(prompts_df.columns)}")
            print(f"     - 手势类型统计:")
            if "gesture" in prompts_df.columns:
                gesture_counts = prompts_df["gesture"].value_counts()
                print(gesture_counts)
            print(f"     - 数据:\n{prompts_df}")
        except Exception as e:
            print(f"   prompts表格: 读取失败 - {str(e)}")

    print("\n" + "="*80)
    print("HDF5文件结构解析完成")
    print("="*80)

# 主函数：指定文件路径运行
if __name__ == "__main__":
    # 请替换为你的discrete_gestures类型HDF5文件路径
    #hdf5_file_path = "../hdf5/discrete_gestures_user_000_dataset_000.hdf5"  # 替换成实际文件路径
    # 检查参数
    if len(sys.argv) != 2:
        print("❌ 使用方式错误！正确用法：")
        print(f"   python {sys.argv[0]} <HDF5文件路径>")
        print("   示例：python hdf5_viewer.py ./discrete_gestures_user_000_dataset_000.hdf5")
        sys.exit(1)
    
    # 获取文件路径
    hdf5_file_path = sys.argv[1]
    
    # 检查文件存在性
    if not os.path.exists(hdf5_file_path):
        print(f"❌ 文件不存在: {hdf5_file_path}")
        sys.exit(1)


    # 检查文件是否存在
    import os
    if not os.path.exists(hdf5_file_path):
        print(f"[错误] 文件不存在: {hdf5_file_path}")
        print("请修改代码中的 hdf5_file_path 为实际的文件路径")
    else:
        print_hdf5_structure(hdf5_file_path)
