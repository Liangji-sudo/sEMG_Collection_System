"""
sEMG 数据采集系统 - 环境安装程序
带 GUI 界面，支持安装/卸载 Python 依赖
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import subprocess
import sys
import threading
import os

# 需要安装的依赖包
PACKAGES = [
    ('websockets', 'WebSocket 通信'),
    ('msgpack', '消息序列化'),
    ('bleak', 'BLE 蓝牙通信'),
    ('numpy', '数值计算'),
    ('scipy', '信号滤波'),
    ('h5py', 'HDF5 数据存储'),
    ('pyzmq', 'ZeroMQ 通信'),
]

class SetupApp:
    def __init__(self, root):
        self.root = root
        self.root.title("sEMG 数据采集系统 - 环境配置")
        self.root.geometry("600x500")
        self.root.resizable(False, False)

        # 设置图标（如果存在）
        try:
            self.root.iconbitmap("icon.ico")
        except:
            pass

        self.create_widgets()
        self.check_environment()

    def create_widgets(self):
        # 标题
        title_frame = tk.Frame(self.root, bg="#2563eb", height=60)
        title_frame.pack(fill=tk.X)
        title_frame.pack_propagate(False)

        title_label = tk.Label(
            title_frame,
            text="sEMG 数据采集系统 - 环境配置",
            font=("Microsoft YaHei", 16, "bold"),
            fg="white",
            bg="#2563eb"
        )
        title_label.pack(expand=True)

        # 环境状态区域
        status_frame = ttk.LabelFrame(self.root, text="环境状态", padding=10)
        status_frame.pack(fill=tk.X, padx=10, pady=10)

        # Python 状态
        self.python_status = tk.StringVar(value="检测中...")
        tk.Label(status_frame, text="Python:").grid(row=0, column=0, sticky=tk.W)
        self.python_label = tk.Label(status_frame, textvariable=self.python_status)
        self.python_label.grid(row=0, column=1, sticky=tk.W, padx=10)

        # pip 状态
        self.pip_status = tk.StringVar(value="检测中...")
        tk.Label(status_frame, text="pip:").grid(row=1, column=0, sticky=tk.W)
        self.pip_label = tk.Label(status_frame, textvariable=self.pip_status)
        self.pip_label.grid(row=1, column=1, sticky=tk.W, padx=10)

        # 依赖包列表
        pkg_frame = ttk.LabelFrame(self.root, text="依赖包状态", padding=10)
        pkg_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # 创建表格
        columns = ('package', 'description', 'status')
        self.tree = ttk.Treeview(pkg_frame, columns=columns, show='headings', height=7)
        self.tree.heading('package', text='包名')
        self.tree.heading('description', text='说明')
        self.tree.heading('status', text='状态')
        self.tree.column('package', width=100)
        self.tree.column('description', width=200)
        self.tree.column('status', width=150)
        self.tree.pack(fill=tk.BOTH, expand=True)

        # 日志区域
        log_frame = ttk.LabelFrame(self.root, text="安装日志", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=6, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 按钮区域
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        self.install_btn = ttk.Button(
            btn_frame,
            text="安装所有依赖",
            command=self.install_all,
            width=20
        )
        self.install_btn.pack(side=tk.LEFT, padx=5)

        self.uninstall_btn = ttk.Button(
            btn_frame,
            text="卸载所有依赖",
            command=self.uninstall_all,
            width=20
        )
        self.uninstall_btn.pack(side=tk.LEFT, padx=5)

        self.refresh_btn = ttk.Button(
            btn_frame,
            text="刷新状态",
            command=self.check_environment,
            width=15
        )
        self.refresh_btn.pack(side=tk.RIGHT, padx=5)

    def log(self, message):
        """添加日志"""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.root.update()

    def clear_log(self):
        """清空日志"""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

    def check_environment(self):
        """检测环境"""
        self.clear_log()
        self.log("正在检测环境...")

        # 清空表格
        for item in self.tree.get_children():
            self.tree.delete(item)

        # 检测 Python
        try:
            result = subprocess.run(
                [sys.executable, '--version'],
                capture_output=True, text=True, timeout=10
            )
            version = result.stdout.strip() or result.stderr.strip()
            self.python_status.set(f"✓ {version}")
            self.python_label.config(fg="green")
            self.log(f"Python: {version}")
        except Exception as e:
            self.python_status.set("✗ 未安装")
            self.python_label.config(fg="red")
            self.log(f"Python 未安装: {e}")

        # 检测 pip
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', '--version'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                self.pip_status.set("✓ 已安装")
                self.pip_label.config(fg="green")
                self.log("pip: 已安装")
            else:
                raise Exception("pip not found")
        except Exception as e:
            self.pip_status.set("✗ 未安装")
            self.pip_label.config(fg="red")
            self.log(f"pip 未安装: {e}")

        # 检测依赖包
        self.log("正在检测依赖包...")
        for pkg_name, pkg_desc in PACKAGES:
            status = self.check_package(pkg_name)
            self.tree.insert('', tk.END, values=(pkg_name, pkg_desc, status))

        self.log("环境检测完成")

    def check_package(self, package_name):
        """检测单个包是否安装"""
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'show', package_name],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                # 提取版本号
                for line in result.stdout.split('\n'):
                    if line.startswith('Version:'):
                        version = line.split(':')[1].strip()
                        return f"✓ 已安装 ({version})"
                return "✓ 已安装"
            else:
                return "✗ 未安装"
        except:
            return "? 检测失败"

    def install_all(self):
        """安装所有依赖"""
        if messagebox.askyesno("确认", "确定要安装所有依赖包吗？"):
            self.install_btn.config(state=tk.DISABLED)
            self.uninstall_btn.config(state=tk.DISABLED)
            threading.Thread(target=self._install_all_thread, daemon=True).start()

    def _install_all_thread(self):
        """安装线程"""
        self.clear_log()
        self.log("开始安装依赖...")

        # 先升级 pip
        self.log("\n[1/8] 升级 pip...")
        self.run_pip_command(['install', '--upgrade', 'pip'])

        # 安装每个包
        for i, (pkg_name, pkg_desc) in enumerate(PACKAGES, 2):
            self.log(f"\n[{i}/8] 安装 {pkg_name} ({pkg_desc})...")
            success = self.run_pip_command(['install', pkg_name])
            if success:
                self.log(f"  ✓ {pkg_name} 安装成功")
            else:
                self.log(f"  ✗ {pkg_name} 安装失败")

        self.log("\n安装完成！")
        self.root.after(0, self.check_environment)
        self.root.after(0, lambda: self.install_btn.config(state=tk.NORMAL))
        self.root.after(0, lambda: self.uninstall_btn.config(state=tk.NORMAL))
        self.root.after(0, lambda: messagebox.showinfo("完成", "依赖安装完成！"))

    def uninstall_all(self):
        """卸载所有依赖"""
        if messagebox.askyesno("确认", "确定要卸载所有依赖包吗？\n这将移除所有 sEMG 系统相关的 Python 包。"):
            self.install_btn.config(state=tk.DISABLED)
            self.uninstall_btn.config(state=tk.DISABLED)
            threading.Thread(target=self._uninstall_all_thread, daemon=True).start()

    def _uninstall_all_thread(self):
        """卸载线程"""
        self.clear_log()
        self.log("开始卸载依赖...")

        for i, (pkg_name, pkg_desc) in enumerate(PACKAGES, 1):
            self.log(f"\n[{i}/{len(PACKAGES)}] 卸载 {pkg_name}...")
            success = self.run_pip_command(['uninstall', '-y', pkg_name])
            if success:
                self.log(f"  ✓ {pkg_name} 已卸载")
            else:
                self.log(f"  - {pkg_name} 未安装或卸载失败")

        self.log("\n卸载完成！")
        self.root.after(0, self.check_environment)
        self.root.after(0, lambda: self.install_btn.config(state=tk.NORMAL))
        self.root.after(0, lambda: self.uninstall_btn.config(state=tk.NORMAL))
        self.root.after(0, lambda: messagebox.showinfo("完成", "依赖卸载完成！"))

    def run_pip_command(self, args):
        """运行 pip 命令"""
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip'] + args,
                capture_output=True, text=True, timeout=300
            )
            return result.returncode == 0
        except Exception as e:
            self.log(f"  错误: {e}")
            return False


def main():
    root = tk.Tk()
    app = SetupApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
