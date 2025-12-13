# test.py
import os
import sys
import glob
import numpy as np
import matplotlib.pyplot as plt

# 将load.py所在目录加入系统路径（如果test.py和load.py在同一目录可省略）
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 从load.py导入核心函数和类
from load import load_data, DiscreteGesturesData

# ====================== 配置参数 ======================
# HDF5文件所在文件夹（根据实际路径修改）
DATA_FOLDER = "../hdf5/"
# 目标任务名称
TARGET_TASK = "discrete_gestures"

# ====================== 辅助函数：查找目标任务的HDF5文件 ======================
def find_task_hdf5_files(folder: str, task: str) -> list[str]:
    """查找指定文件夹下包含目标任务名称的HDF5文件"""
    # 扩展用户目录（处理~符号）
    folder = os.path.expanduser(folder)
    # 查找所有hdf5文件
    all_hdf5 = glob.glob(os.path.join(folder, "*.hdf5"))
    # 筛选包含任务名称的文件
    task_files = [f for f in all_hdf5 if task in f.lower()]
    
    if not task_files:
        raise FileNotFoundError(
            f"未找到{task}任务的HDF5文件！\n"
            f"查找路径：{folder}\n"
            f"请检查文件路径和任务名称是否正确。"
        )
    return task_files

# ====================== 主函数：加载并探索数据 ======================
def main():
    try:
        # 1. 查找目标文件
        print(f"正在查找{TARGET_TASK}任务的HDF5文件...")
        task_files = find_task_hdf5_files(DATA_FOLDER, TARGET_TASK)
        # 取第一个匹配的文件
        target_file = task_files[0]
        print(f"找到目标文件：{os.path.basename(target_file)}")
        print("-" * 50)

        # 2. 加载数据（自动匹配对应的Loader）
        print("正在加载数据...")
        data = load_data(target_file)
        
        # 验证数据类型（确保是DiscreteGesturesData）
        assert isinstance(data, DiscreteGesturesData), \
            f"加载的数据类型错误！预期DiscreteGesturesData，实际{type(data)}"
        print("数据加载成功！")
        print("-" * 50)

        # 3. 探索数据基本信息
        print("=== 数据基本信息 ===")
        print(f"任务名称：{data.task}")
        print(f"文件路径：{data.hdf5_path}")
        print(f"EMG数据形状：{data.emg.shape} (时间步 × 通道数)")
        print(f"时间戳范围：{data.time.min():.2f} ~ {data.time.max():.2f} 秒")
        print(f"总时长：{data.time.max() - data.time.min():.2f} 秒")
        print(f"Stages数据行数：{len(data.stages)}")
        print(f"Prompts数据行数：{len(data.prompts)}")
        print("-" * 50)

        # 4. 展示Stages和Prompts的前几行
        print("=== Stages数据（前5行） ===")
        print(data.stages.head())
        print("\n=== Prompts数据（前5行） ===")
        print(data.prompts.head())
        print("-" * 50)

        # 5. 数据切片示例（提取0-5秒的EMG数据）
        print("=== 数据切片示例（0-5秒） ===")
        sliced_data = data.partition(start_t=0.0, end_t=5.0)
        print(f"0-5秒EMG数据形状：{sliced_data['emg'].shape}")
        print("-" * 50)

        # 6. 可视化EMG数据（前2个通道，前1000个时间步）
        print("生成EMG数据可视化图...")
        plt.figure(figsize=(12, 6))
        # 取前1000个时间步，前2个通道
        plot_time = data.time[:1000]
        plot_emg = data.emg[:1000, :2]
        
        for ch in range(plot_emg.shape[1]):
            plt.plot(plot_time, plot_emg[:, ch], label=f"EMG Channel {ch+1}")
        
        plt.title(f"{TARGET_TASK} - EMG Data (First 2 Channels, First 1000 Timesteps)")
        plt.xlabel("Time (s)")
        plt.ylabel("EMG Voltage (V)")
        plt.legend()
        plt.grid(alpha=0.3)
        plt.tight_layout()
        # 保存图片（可选）
        plt.savefig(f"{TARGET_TASK}_emg_plot.png")
        print(f"可视化图已保存为：{TARGET_TASK}_emg_plot.png")
        # 显示图片
        plt.show()

        print("\n✅ 数据加载和探索完成！")

    except FileNotFoundError as e:
        print(f"\n❌ 错误：{e}")
        sys.exit(1)
    except AssertionError as e:
        print(f"\n❌ 验证错误：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 未知错误：{e}")
        sys.exit(1)

# ====================== 运行入口 ======================
if __name__ == "__main__":
    main()