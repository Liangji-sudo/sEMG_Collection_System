
import h5py
import numpy as np
import os
import argparse

def print_h5_structure(group, prefix=""):
    """
    递归打印 HDF5 文件结构（组 + 数据集）
    :param group: 当前遍历的 HDF5 组对象
    :param prefix: 缩进前缀（用于格式化输出）
    """
    # 遍历当前组下的所有成员（组/数据集）
    for name, obj in group.items():
        if isinstance(obj, h5py.Group):
            # 若是组，递归打印子内容
            print(f"{prefix}📁 组: {name}")
            print_h5_structure(obj, prefix + "  ")
        elif isinstance(obj, h5py.Dataset):
            # 若是数据集，打印基本信息
            print(f"{prefix}📊 数据集: {name}")
            print(f"{prefix}  - 形状: {obj.shape}")
            print(f"{prefix}  - 数据类型: {obj.dtype}")
            print(f"{prefix}  - 数据条数: {obj.size}")

def print_h5_dataset_data(dataset, max_show=10):
    """
    打印数据集的具体数据（控制显示条数，避免数据量过大）
    :param dataset: HDF5 数据集对象
    :param max_show: 最多显示前 N 条数据（默认10条）
    """
    try:
        # 读取数据集全部数据
        data = dataset[:]
        
        # 根据数据类型处理显示格式
        if np.issubdtype(dataset.dtype, np.string_) or np.issubdtype(dataset.dtype, np.unicode_):
            # 字符串类型：解码为普通字符串
            data = [item.decode('utf-8').strip() if isinstance(item, bytes) else item for item in data]
        elif np.issubdtype(dataset.dtype, np.floating):
            # 浮点型：保留4位小数
            data = np.round(data, 4)
        
        # 控制显示条数
        if len(data) > max_show:
            print(f"  前{max_show}条数据: {data[:max_show]}")
            print(f"  （剩余{len(data)-max_show}条数据未显示）")
        else:
            print(f"  全部数据: {data}")
    except Exception as e:
        print(f"  ❌ 读取数据失败: {str(e)}")

def read_h5_file(file_path, show_data=True, max_show=10):
    """
    读取 HDF5 文件的主函数
    :param file_path: HDF5 文件路径
    :param show_data: 是否打印数据集内容（默认True）
    :param max_show: 数据集最多显示条数（默认10）
    """
    # 校验文件是否存在
    if not os.path.exists(file_path):
        print(f"❌ 错误：文件 {file_path} 不存在！")
        return

    try:
        # 以只读模式打开 HDF5 文件
        with h5py.File(file_path, 'r') as h5_file:
            print("="*80)
            print(f"📄 正在读取 HDF5 文件: {os.path.abspath(file_path)}")
            print("="*80)
            
            # 1. 打印文件整体结构
            print("\n📋 文件结构:")
            print_h5_structure(h5_file)
            
            # 2. 打印所有数据集的具体数据（若开启）
            if show_data:
                print("\n" + "-"*80)
                print("📈 数据集数据内容:")
                print("-"*80)
                # 递归遍历所有数据集并打印数据
                def traverse_dataset(group, prefix=""):
                    for name, obj in group.items():
                        if isinstance(obj, h5py.Group):
                            traverse_dataset(obj, prefix + "  ")
                        elif isinstance(obj, h5py.Dataset):
                            print(f"\n{prefix}📊 数据集 {name}:")
                            print_h5_dataset_data(obj, max_show)
                traverse_dataset(h5_file)
        
        print("\n" + "="*80)
        print("✅ HDF5 文件读取完成！")
        print("="*80)

    except Exception as e:
        print(f"\n❌ 读取文件失败: {str(e)}")

if __name__ == "__main__":
    # 命令行参数解析（支持指定文件路径/显示条数）
    parser = argparse.ArgumentParser(description="读取并打印 HDF5 文件的结构和数据")
    parser.add_argument("file_path", help="HDF5 文件的路径（如 ./storage/sensor_2025.h5）")
    parser.add_argument("--no-data", action="store_false", dest="show_data", help="不打印数据集内容，仅显示结构")
    parser.add_argument("--max-show", type=int, default=10, help="数据集最多显示的条数（默认10）")
    
    args = parser.parse_args()
    
    # 调用读取函数
    read_h5_file(args.file_path, args.show_data, args.max_show)
