"""
HDF5整合工具 - 结合查看和同步功能
功能：
1. 选择目录，批量加载子目录下所有h5文件
2. 查看标签页：完整的h5文件查看功能
3. 同步标签页：将250Hz数据与SD卡bin文件同步补全为2kHz
"""

import sys
import os
import h5py
import numpy as np
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
    QSplitter, QLabel, QPushButton, QFileDialog, QGroupBox,
    QTextEdit, QTabWidget, QHeaderView, QMessageBox, QListWidget,
    QListWidgetItem, QProgressBar, QCheckBox, QSpinBox, QComboBox,
    QFrame, QScrollArea, QGridLayout
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QPalette

# 导入bin_sync_tool中的同步功能
try:
    # 确保 tools/ 目录在 sys.path 中（无论从哪里启动）
    _tools_dir = os.path.dirname(os.path.abspath(__file__))
    if _tools_dir not in sys.path:
        sys.path.insert(0, _tools_dir)
    from bin_sync_tool import EMGBinParser, IMUBinParser, sync_h5_with_bin
    HAS_SYNC_TOOL = True
except ImportError:
    HAS_SYNC_TOOL = False
    print("[警告] 无法导入bin_sync_tool，同步功能不可用")

# 尝试导入matplotlib
try:
    import matplotlib
    matplotlib.use('Qt5Agg')
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


class SyncWorker(QThread):
    """同步工作线程"""
    progress = pyqtSignal(int, int, str)
    log = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, h5_files, emg_bin_path, imu_bin_path, devices, validate_data):
        super().__init__()
        self.h5_files = h5_files
        self.emg_bin_path = emg_bin_path  # 现在是文件路径
        self.imu_bin_path = imu_bin_path  # 现在是文件路径
        self.devices = devices
        self.validate_data = validate_data

    def run(self):
        if not HAS_SYNC_TOOL:
            self.finished_signal.emit(False, "同步功能不可用：无法导入bin_sync_tool")
            return

        try:
            total = len(self.h5_files)
            success_count = 0

            # 根据勾选的设备确定需要处理的device_id列表
            device_ids = set()
            if 'emg1' in self.devices or 'imu1' in self.devices:
                device_ids.add(1)
            if 'emg2' in self.devices or 'imu2' in self.devices:
                device_ids.add(2)

            for i, h5_file in enumerate(self.h5_files):
                self.progress.emit(i + 1, total, os.path.basename(h5_file))
                self.log.emit(f"\n处理文件: {os.path.basename(h5_file)}")

                file_success = False
                for device_id in sorted(device_ids):
                    try:
                        self.log.emit(f"  设备{device_id}:")

                        # 只在对应设备被勾选时传入bin路径
                        emg_bin = self.emg_bin_path if (f'emg{device_id}' in self.devices) else None
                        imu_bin = self.imu_bin_path if (f'imu{device_id}' in self.devices) else None

                        if not emg_bin:
                            self.log.emit(f"    - EMG bin未提供，跳过设备{device_id}")
                            continue

                        result = sync_h5_with_bin(
                            h5_path=h5_file,
                            emg_bin_path=emg_bin,
                            imu_bin_path=imu_bin,
                            device_id=device_id,
                            verify=self.validate_data
                        )

                        if result.get('status') == 'success':
                            file_success = True
                            self.log.emit(f"    ✓ EMG: {result['frames_2khz']}帧 "
                                          f"(来自bin:{result['filled_frames']}, "
                                          f"插值:{result['missing_frames']})")
                            if result.get('imu_status') == 'success':
                                self.log.emit(f"    ✓ IMU: {result['imu_frames']}帧 "
                                              f"(来自bin:{result['imu_filled']}, "
                                              f"缺失:{result['imu_missing']})")
                            elif result.get('imu_status') == 'skipped':
                                self.log.emit(f"    - IMU: 未提供bin文件，跳过")
                        elif result.get('status') == 'skipped':
                            self.log.emit(f"    - 跳过: {result.get('reason', '已同步')}")
                        else:
                            self.log.emit(f"    ✗ 失败: {result.get('reason', '未知错误')}")

                    except Exception as e:
                        self.log.emit(f"    ✗ 错误: {str(e)}")

                if file_success:
                    success_count += 1

            self.finished_signal.emit(True, f"完成: {success_count}/{total} 个文件同步成功")

        except Exception as e:
            self.finished_signal.emit(False, f"同步出错: {str(e)}")


class WaveformWidget(QWidget):
    """波形显示组件"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if HAS_MATPLOTLIB:
            self.figure = Figure(figsize=(10, 4), dpi=100)
            self.canvas = FigureCanvas(self.figure)
            layout.addWidget(self.canvas)
        else:
            label = QLabel("需要安装matplotlib才能显示波形图\npip install matplotlib")
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("color: #888; padding: 50px;")
            layout.addWidget(label)

    def plot_data(self, data, title="波形"):
        if not HAS_MATPLOTLIB:
            return
        self.figure.clear()
        ax = self.figure.add_subplot(111)

        if data.ndim == 1:
            ax.plot(data[:min(2000, len(data))], linewidth=0.5)
        elif data.ndim == 2:
            rows = min(2000, data.shape[0])
            cols = min(8, data.shape[1])
            for i in range(cols):
                ax.plot(data[:rows, i], label=f'Ch{i}', linewidth=0.5, alpha=0.8)
            ax.legend(loc='upper right', fontsize=8)

        ax.set_title(title, fontsize=10)
        ax.set_xlabel('Sample')
        ax.set_ylabel('Value')
        ax.grid(True, alpha=0.3)
        self.figure.tight_layout()
        self.canvas.draw()


class StatisticsPanel(QFrame):
    """统计信息面板"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        self.setFrameStyle(QFrame.StyledPanel)
        layout = QGridLayout(self)
        layout.setSpacing(8)

        self.labels = {}
        stats = [
            ('文件名', '文件名'), ('文件大小', '文件大小'), ('创建时间', '创建时间'),
            ('emg1_250hz', 'EMG1 250Hz'), ('emg1_2khz', 'EMG1 2kHz'),
            ('emg2_250hz', 'EMG2 250Hz'), ('emg2_2khz', 'EMG2 2kHz'),
            ('imu1_250hz', 'IMU1 250Hz'), ('imu1_100hz', 'IMU1 100Hz'),
            ('imu2_250hz', 'IMU2 250Hz'), ('imu2_100hz', 'IMU2 100Hz'),
            ('mocap', 'Mocap'),
        ]

        for i, (key, name) in enumerate(stats):
            row = i // 2
            col = (i % 2) * 2
            name_label = QLabel(f'{name}:')
            name_label.setStyleSheet('font-weight: bold; color: #495057;')
            value_label = QLabel('-')
            value_label.setStyleSheet('color: #0066cc;')
            layout.addWidget(name_label, row, col)
            layout.addWidget(value_label, row, col + 1)
            self.labels[key] = value_label

    def update_stats(self, file_path):
        try:
            import time
            self.labels['文件名'].setText(os.path.basename(file_path))
            size = os.path.getsize(file_path)
            self.labels['文件大小'].setText(f"{size / 1024 / 1024:.2f} MB")
            mtime = os.path.getmtime(file_path)
            self.labels['创建时间'].setText(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime)))

            with h5py.File(file_path, 'r') as f:
                for key in ['emg1_250hz', 'emg1_2khz', 'emg2_250hz', 'emg2_2khz',
                           'imu1_250hz', 'imu1_100hz', 'imu2_250hz', 'imu2_100hz']:
                    adc_key = key.replace('hz', 'hz_adc')
                    if adc_key in f:
                        self.labels[key].setText(str(f[adc_key].shape))
                    elif key in f:
                        self.labels[key].setText(str(f[key].shape))
                    else:
                        self.labels[key].setText('-')

                if 'mocap' in f:
                    self.labels['mocap'].setText(str(f['mocap'].shape))
                else:
                    self.labels['mocap'].setText('-')
        except Exception as e:
            print(f"更新统计信息错误: {e}")

    def clear_stats(self):
        for label in self.labels.values():
            label.setText("-")


class ViewerTab(QWidget):
    """查看标签页 - 完整的HDF5查看功能"""
    def __init__(self):
        super().__init__()
        self.current_file = None
        self.emg_precision = 7
        self.preview_rows = 100
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # 统计信息面板
        self.stats_panel = StatisticsPanel()
        self.stats_panel.setMaximumHeight(180)
        layout.addWidget(self.stats_panel)

        # 主分割器 - 可拖动
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setHandleWidth(5)
        main_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #ddd;
            }
            QSplitter::handle:hover {
                background-color: #aaa;
            }
        """)

        # 左侧：文件结构树
        tree_widget = QWidget()
        tree_layout = QVBoxLayout(tree_widget)
        tree_layout.setContentsMargins(0, 0, 0, 0)

        tree_label = QLabel("文件结构")
        tree_label.setStyleSheet("font-weight: bold; font-size: 12px; padding: 5px;")
        tree_layout.addWidget(tree_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名称", "类型", "形状/值"])
        self.tree.setColumnWidth(0, 150)
        self.tree.setColumnWidth(1, 60)
        self.tree.setColumnWidth(2, 100)
        self.tree.itemClicked.connect(self.on_tree_item_clicked)
        self.tree.setAlternatingRowColors(True)
        self.tree.setStyleSheet("""
            QTreeWidget {
                border: 1px solid #ccc;
                alternate-background-color: #f8f9fa;
            }
            QTreeWidget::item:selected {
                background-color: #0078d4;
                color: white;
            }
        """)
        tree_layout.addWidget(self.tree)
        main_splitter.addWidget(tree_widget)

        # 右侧分割器
        right_splitter = QSplitter(Qt.Vertical)
        right_splitter.setHandleWidth(5)

        # 属性信息
        attr_widget = QWidget()
        attr_layout = QVBoxLayout(attr_widget)
        attr_layout.setContentsMargins(0, 0, 0, 0)

        attr_label = QLabel("属性信息")
        attr_label.setStyleSheet("font-weight: bold; font-size: 12px; padding: 5px;")
        attr_layout.addWidget(attr_label)

        self.attr_table = QTableWidget()
        self.attr_table.setColumnCount(2)
        self.attr_table.setHorizontalHeaderLabels(["属性名", "值"])
        self.attr_table.horizontalHeader().setStretchLastSection(True)
        self.attr_table.setAlternatingRowColors(True)
        self.attr_table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #ccc;
                alternate-background-color: #f8f9fa;
            }
            QHeaderView::section {
                background-color: #e9ecef;
                padding: 5px;
                border: 1px solid #dee2e6;
                font-weight: bold;
            }
        """)
        attr_layout.addWidget(self.attr_table)
        right_splitter.addWidget(attr_widget)

        # 数据预览
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)

        # 预览标题和设置
        preview_header = QHBoxLayout()
        preview_label = QLabel("数据预览")
        preview_label.setStyleSheet("font-weight: bold; font-size: 12px; padding: 5px;")
        preview_header.addWidget(preview_label)

        preview_header.addWidget(QLabel("EMG精度:"))
        self.precision_spin = QSpinBox()
        self.precision_spin.setRange(1, 10)
        self.precision_spin.setValue(7)
        self.precision_spin.setFixedWidth(60)
        self.precision_spin.valueChanged.connect(lambda v: setattr(self, 'emg_precision', v))
        preview_header.addWidget(self.precision_spin)

        preview_header.addWidget(QLabel("预览行数:"))
        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(10, 1000)
        self.rows_spin.setValue(100)
        self.rows_spin.setFixedWidth(80)
        self.rows_spin.valueChanged.connect(lambda v: setattr(self, 'preview_rows', v))
        preview_header.addWidget(self.rows_spin)
        preview_header.addStretch()
        preview_layout.addLayout(preview_header)

        # 预览标签页
        self.preview_tabs = QTabWidget()
        self.preview_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #ccc;
            }
            QTabBar::tab {
                padding: 8px 16px;
                margin-right: 2px;
                background-color: #e9ecef;
                border: 1px solid #ccc;
                border-bottom: none;
            }
            QTabBar::tab:selected {
                background-color: white;
                border-bottom: 1px solid white;
            }
        """)

        # 表格视图
        self.data_table = QTableWidget()
        self.data_table.setAlternatingRowColors(True)
        self.data_table.setStyleSheet("""
            QTableWidget {
                border: none;
                alternate-background-color: #f8f9fa;
            }
            QHeaderView::section {
                background-color: #e9ecef;
                padding: 5px;
                border: 1px solid #dee2e6;
                font-weight: bold;
            }
        """)
        self.preview_tabs.addTab(self.data_table, "表格")

        # 文本视图
        self.text_view = QTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setFont(QFont("Consolas", 9))
        self.text_view.setStyleSheet("border: none;")
        self.preview_tabs.addTab(self.text_view, "文本")

        # 波形图
        self.waveform = WaveformWidget()
        self.preview_tabs.addTab(self.waveform, "波形图")

        preview_layout.addWidget(self.preview_tabs)
        right_splitter.addWidget(preview_widget)

        right_splitter.setSizes([150, 400])
        main_splitter.addWidget(right_splitter)
        main_splitter.setSizes([300, 700])

        layout.addWidget(main_splitter)

    def load_file(self, file_path):
        """加载HDF5文件"""
        self.current_file = file_path
        self.tree.clear()
        self.attr_table.setRowCount(0)
        self.data_table.setRowCount(0)
        self.text_view.clear()

        try:
            self.stats_panel.update_stats(file_path)
            with h5py.File(file_path, 'r') as f:
                self.populate_tree(f, self.tree.invisibleRootItem())
            self.tree.expandToDepth(0)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法打开文件: {e}")

    def populate_tree(self, group, parent_item, path=""):
        """递归填充树形结构"""
        for key in group.keys():
            item_path = f"{path}/{key}" if path else key
            item = group[key]

            tree_item = QTreeWidgetItem(parent_item)
            tree_item.setText(0, key)
            tree_item.setData(0, Qt.UserRole, item_path)

            if isinstance(item, h5py.Group):
                tree_item.setText(1, "Group")
                tree_item.setText(2, f"{len(item.keys())} items")
                self.populate_tree(item, tree_item, item_path)
            elif isinstance(item, h5py.Dataset):
                tree_item.setText(1, "Dataset")
                tree_item.setText(2, str(item.shape))

    def on_tree_item_clicked(self, item, column):
        """树形项目点击事件"""
        path = item.data(0, Qt.UserRole)
        if not path or not self.current_file:
            return

        try:
            with h5py.File(self.current_file, 'r') as f:
                obj = f[path]
                self.show_attributes(obj)

                if isinstance(obj, h5py.Dataset):
                    self.show_data_preview(obj, path)
        except Exception as e:
            print(f"读取错误: {e}")

    def show_attributes(self, obj):
        """显示属性"""
        self.attr_table.setRowCount(0)
        attrs = dict(obj.attrs)

        if isinstance(obj, h5py.Dataset):
            attrs['[dtype]'] = str(obj.dtype)
            attrs['[shape]'] = str(obj.shape)
            attrs['[size]'] = str(obj.size)

        self.attr_table.setRowCount(len(attrs))
        for i, (key, value) in enumerate(attrs.items()):
            key_item = QTableWidgetItem(str(key))
            value_item = QTableWidgetItem(str(value))
            self.attr_table.setItem(i, 0, key_item)
            self.attr_table.setItem(i, 1, value_item)

    def show_data_preview(self, dataset, path):
        """显示数据预览"""
        try:
            data = dataset[:]
            dtype = dataset.dtype
            is_emg = 'emg' in path.lower()

            # 检查是否是结构化数组
            is_structured = dtype.names is not None

            # 判断数据类型
            data_type = None
            if is_structured:
                field_names = dtype.names
                if 'channels' in field_names:
                    data_type = 'emg'
                elif 'acc' in field_names or 'gyro' in field_names or 'gyr' in field_names:
                    data_type = 'imu'
            elif 'prompts' in path.lower():
                data_type = 'prompts'

            # 根据数据类型选择显示方式
            if data_type == 'emg':
                self.show_emg_data(data, dtype, path)
            elif data_type == 'imu':
                self.show_imu_data(data, dtype, path)
            elif data_type == 'prompts':
                self.show_prompt_data(data, path)
            else:
                # 普通数组
                self.update_table_view(data, is_emg)
                self.update_text_view(data, is_emg)

            # 波形图
            self.waveform.plot_data(data, path.split('/')[-1])

        except Exception as e:
            self.text_view.setText(f"预览错误: {e}")

    def show_emg_data(self, data, dtype, path):
        """显示EMG结构化数据（带BLE帧号、SD卡帧号等）"""
        precision = self.emg_precision
        max_rows = min(len(data), self.preview_rows)
        preview_data = data[:max_rows]

        text_lines = [
            f'【{path} - EMG数据预览 (前{max_rows}帧，共{len(data)}帧)】',
            f'精度: {precision}位小数',
            '═' * 100
        ]

        # 表格设置
        self.data_table.clear()
        self.data_table.setRowCount(max_rows)

        if 'channels' in dtype.names:
            n_channels = data['channels'].shape[1] if len(data['channels'].shape) > 1 else 16

            # 构建表头
            headers = ['帧序号']
            has_frame_id = 'frame_id' in dtype.names
            has_sd_frame_id = 'sd_frame_id' in dtype.names

            if has_frame_id:
                headers.append('BLE帧号')
            if has_sd_frame_id:
                headers.append('SD卡帧号')

            headers += [f'Ch{i}' for i in range(n_channels)]
            if 'time' in dtype.names:
                headers.append('时间戳')

            self.data_table.setColumnCount(len(headers))
            self.data_table.setHorizontalHeaderLabels(headers)

            for i, row in enumerate(preview_data):
                col = 0

                # 帧序号
                item = QTableWidgetItem(str(i))
                item.setTextAlignment(Qt.AlignCenter)
                self.data_table.setItem(i, col, item)
                col += 1

                # BLE帧号
                if has_frame_id:
                    item = QTableWidgetItem(str(row['frame_id']))
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setBackground(QColor(230, 245, 255))  # 浅蓝色背景
                    self.data_table.setItem(i, col, item)
                    col += 1

                # SD卡帧号
                if has_sd_frame_id:
                    item = QTableWidgetItem(str(row['sd_frame_id']))
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setBackground(QColor(255, 245, 230))  # 浅橙色背景
                    self.data_table.setItem(i, col, item)
                    col += 1

                # 通道数据
                channels = row['channels']
                for j, val in enumerate(channels):
                    item = QTableWidgetItem(f'{val:.{precision}f}')
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    # 根据值设置背景色
                    if abs(val) > 100:
                        item.setBackground(QColor(255, 230, 230))  # 浅红
                    self.data_table.setItem(i, col + j, item)

                # 时间戳
                if 'time' in dtype.names:
                    item = QTableWidgetItem(f'{row["time"]:.9f}')
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.data_table.setItem(i, col + n_channels, item)

                # 文本预览
                ch_str = ', '.join([f'{v:.{precision}f}' for v in channels[:8]])
                if n_channels > 8:
                    ch_str += ', ...'

                frame_info = ''
                if has_frame_id:
                    frame_info += f' BLE={row["frame_id"]}'
                if has_sd_frame_id:
                    frame_info += f' SD={row["sd_frame_id"]}'

                time_str = f' t={row["time"]:.9f}' if 'time' in dtype.names else ''
                text_lines.append(f'帧{i:5d}:{frame_info} [{ch_str}]{time_str}')

        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.text_view.setText('\n'.join(text_lines))

    def show_imu_data(self, data, dtype, path):
        """显示IMU结构化数据（带BLE帧号、SD卡帧号等）"""
        max_rows = min(len(data), self.preview_rows)
        preview_data = data[:max_rows]

        text_lines = [
            f'【{path} - IMU数据预览 (前{max_rows}帧，共{len(data)}帧)】',
            '═' * 100
        ]

        # 表格设置
        self.data_table.clear()
        self.data_table.setRowCount(max_rows)

        # 检查字段
        has_frame_id = 'frame_id' in dtype.names
        has_sd_frame_id = 'sd_frame_id' in dtype.names
        has_acc = 'acc' in dtype.names
        has_gyr = 'gyr' in dtype.names or 'gyro' in dtype.names
        gyr_key = 'gyr' if 'gyr' in dtype.names else 'gyro' if 'gyro' in dtype.names else None
        has_mag = 'mag' in dtype.names
        has_time = 'time' in dtype.names

        # 构建表头
        headers = ['帧序号']
        if has_frame_id:
            headers.append('BLE帧号')
        if has_sd_frame_id:
            headers.append('SD卡帧号')
        if has_acc:
            headers += ['Acc_X', 'Acc_Y', 'Acc_Z']
        if has_gyr:
            headers += ['Gyr_X', 'Gyr_Y', 'Gyr_Z']
        if has_mag:
            headers += ['Mag_X', 'Mag_Y', 'Mag_Z']
        if has_time:
            headers.append('时间戳')

        self.data_table.setColumnCount(len(headers))
        self.data_table.setHorizontalHeaderLabels(headers)

        for i, row in enumerate(preview_data):
            col = 0

            # 帧序号
            item = QTableWidgetItem(str(i))
            item.setTextAlignment(Qt.AlignCenter)
            self.data_table.setItem(i, col, item)
            col += 1

            # BLE帧号
            if has_frame_id:
                item = QTableWidgetItem(str(row['frame_id']))
                item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(QColor(230, 245, 255))  # 浅蓝色背景
                self.data_table.setItem(i, col, item)
                col += 1

            # SD卡帧号
            if has_sd_frame_id:
                item = QTableWidgetItem(str(row['sd_frame_id']))
                item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(QColor(255, 245, 230))  # 浅橙色背景
                self.data_table.setItem(i, col, item)
                col += 1

            # 加速度
            if has_acc:
                for j, val in enumerate(row['acc']):
                    item = QTableWidgetItem(f'{val:.6f}')
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.data_table.setItem(i, col, item)
                    col += 1

            # 角速度
            if has_gyr and gyr_key:
                for j, val in enumerate(row[gyr_key]):
                    item = QTableWidgetItem(f'{val:.6f}')
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.data_table.setItem(i, col, item)
                    col += 1

            # 磁力计
            if has_mag:
                for j, val in enumerate(row['mag']):
                    item = QTableWidgetItem(f'{val:.6f}')
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.data_table.setItem(i, col, item)
                    col += 1

            # 时间戳
            if has_time:
                item = QTableWidgetItem(f'{row["time"]:.9f}')
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.data_table.setItem(i, col, item)

            # 文本预览
            parts = []
            if has_frame_id:
                parts.append(f'BLE={row["frame_id"]}')
            if has_sd_frame_id:
                parts.append(f'SD={row["sd_frame_id"]}')
            if has_acc:
                parts.append(f'Acc=[{row["acc"][0]:8.4f}, {row["acc"][1]:8.4f}, {row["acc"][2]:8.4f}]')
            if has_gyr and gyr_key:
                parts.append(f'Gyr=[{row[gyr_key][0]:8.4f}, {row[gyr_key][1]:8.4f}, {row[gyr_key][2]:8.4f}]')
            if has_time:
                parts.append(f't={row["time"]:.9f}')
            text_lines.append(f'帧{i:5d}: {" ".join(parts)}')

        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.text_view.setText('\n'.join(text_lines))

    def show_prompt_data(self, data, path):
        """显示Prompt数据（正确解码字节字符串）"""
        max_rows = min(len(data), self.preview_rows)
        preview_data = data[:max_rows]

        text_lines = [
            f'【{path} - Prompt数据预览 (前{max_rows}条，共{len(data)}条)】',
            '═' * 80
        ]

        # 表格设置
        self.data_table.clear()
        self.data_table.setRowCount(max_rows)
        self.data_table.setColumnCount(2)
        self.data_table.setHorizontalHeaderLabels(['序号', '值'])

        for i, val in enumerate(preview_data):
            # 序号
            item = QTableWidgetItem(str(i))
            item.setTextAlignment(Qt.AlignCenter)
            self.data_table.setItem(i, 0, item)

            # 处理不同类型的值
            if isinstance(val, bytes):
                val_str = val.decode('utf-8', errors='replace')
            elif isinstance(val, np.floating):
                val_str = f'{val:.9f}'
            elif isinstance(val, np.ndarray):
                # 处理numpy数组中的字节
                if val.dtype.kind == 'S' or val.dtype == object:
                    try:
                        val_str = val.item().decode('utf-8', errors='replace') if isinstance(val.item(), bytes) else str(val.item())
                    except:
                        val_str = str(val)
                else:
                    val_str = str(val)
            else:
                val_str = str(val)

            item = QTableWidgetItem(val_str)
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.data_table.setItem(i, 1, item)
            text_lines.append(f'{i:4d}: {val_str}')

        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.text_view.setText('\n'.join(text_lines))

    def update_table_view(self, data, is_emg):
        """更新表格视图"""
        precision = self.emg_precision if is_emg else 4
        max_rows = self.preview_rows

        self.data_table.clear()

        if data.ndim == 1:
            self.data_table.setColumnCount(1)
            self.data_table.setHorizontalHeaderLabels(["Value"])
            rows = min(len(data), max_rows)
            self.data_table.setRowCount(rows)
            for i in range(rows):
                value = data[i]
                if isinstance(value, (np.floating, float)):
                    text = f"{value:.{precision}f}"
                elif isinstance(value, np.ndarray):
                    text = str(value)
                else:
                    text = str(value)
                item = QTableWidgetItem(text)
                self.data_table.setItem(i, 0, item)

        elif data.ndim == 2:
            rows, cols = data.shape
            display_rows = min(rows, max_rows)
            display_cols = min(cols, 20)

            self.data_table.setColumnCount(display_cols)
            headers = [f"Ch{i}" for i in range(display_cols)]
            self.data_table.setHorizontalHeaderLabels(headers)
            self.data_table.setRowCount(display_rows)

            for i in range(display_rows):
                for j in range(display_cols):
                    value = data[i, j]
                    if isinstance(value, (np.floating, float)):
                        text = f"{value:.{precision}f}"
                    else:
                        text = str(value)
                    item = QTableWidgetItem(text)
                    self.data_table.setItem(i, j, item)

        # 设置行号
        self.data_table.setVerticalHeaderLabels([str(i+1) for i in range(self.data_table.rowCount())])

    def update_text_view(self, data, is_emg):
        """更新文本视图"""
        precision = self.emg_precision if is_emg else 4
        lines = []
        lines.append(f"Shape: {data.shape}")
        lines.append(f"Dtype: {data.dtype}")
        if np.issubdtype(data.dtype, np.number):
            lines.append(f"Min: {np.min(data):.{precision}f}")
            lines.append(f"Max: {np.max(data):.{precision}f}")
            lines.append(f"Mean: {np.mean(data):.{precision}f}")
            lines.append(f"Std: {np.std(data):.{precision}f}")
        lines.append("")
        lines.append("Data preview (first 20 rows):")
        lines.append("-" * 50)

        preview_data = data[:min(20, len(data))]
        if data.ndim == 2:
            for i, row in enumerate(preview_data):
                row_str = ", ".join([f"{v:.{precision}f}" if isinstance(v, (float, np.floating)) else str(v) for v in row[:10]])
                if len(row) > 10:
                    row_str += ", ..."
                lines.append(f"[{i}] {row_str}")
        else:
            lines.append(str(preview_data))

        self.text_view.setText("\n".join(lines))


class SyncTab(QWidget):
    """同步标签页 - 完整的bin同步功能"""
    def __init__(self):
        super().__init__()
        self.h5_files = []
        self.worker = None
        self.emg_bin_dir = None
        self.imu_bin_dir = None
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # 使用分割器
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(5)

        # 左侧：设置面板
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        # H5文件列表
        h5_group = QGroupBox("待同步的H5文件")
        h5_layout = QVBoxLayout(h5_group)

        h5_btn_layout = QHBoxLayout()
        self.add_files_btn = QPushButton("+ 添加文件")
        self.add_files_btn.clicked.connect(self.add_h5_files)
        self.add_files_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #4CAF50, stop:1 #45a049);
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #5CBF60, stop:1 #4CAF50);
            }
            QPushButton:pressed {
                background: #3d8b40;
            }
        """)
        self.clear_files_btn = QPushButton("清空列表")
        self.clear_files_btn.clicked.connect(self.clear_h5_files)
        self.clear_files_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ff6b6b, stop:1 #ee5a5a);
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ff7b7b, stop:1 #ff6b6b);
            }
            QPushButton:pressed {
                background: #d94848;
            }
        """)
        h5_btn_layout.addWidget(self.add_files_btn)
        h5_btn_layout.addWidget(self.clear_files_btn)
        h5_layout.addLayout(h5_btn_layout)

        self.h5_list = QListWidget()
        self.h5_list.setAlternatingRowColors(True)
        self.h5_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ccc;
                alternate-background-color: #f8f9fa;
            }
        """)
        h5_layout.addWidget(self.h5_list)

        self.h5_count_label = QLabel("共 0 个文件")
        self.h5_count_label.setStyleSheet("color: #666;")
        h5_layout.addWidget(self.h5_count_label)
        left_layout.addWidget(h5_group)

        # Bin文件选择
        bin_group = QGroupBox("SD卡Bin文件")
        bin_layout = QVBoxLayout(bin_group)

        # EMG bin文件
        emg_bin_layout = QHBoxLayout()
        emg_label = QLabel("EMG文件:")
        emg_label.setFixedWidth(70)
        emg_bin_layout.addWidget(emg_label)
        self.emg_bin_label = QLabel("未选择")
        self.emg_bin_label.setStyleSheet("color: gray; padding: 5px; background: #f8f9fa; border: 1px solid #ddd;")
        self.emg_bin_btn = QPushButton("选择...")
        self.emg_bin_btn.setFixedWidth(70)
        self.emg_bin_btn.clicked.connect(self.select_emg_bin_dir)
        self.emg_bin_btn.setStyleSheet("""
            QPushButton {
                padding: 6px 12px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #42a5f5, stop:1 #1e88e5);
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #64b5f6, stop:1 #42a5f5);
            }
            QPushButton:pressed {
                background: #1565c0;
            }
        """)
        emg_bin_layout.addWidget(self.emg_bin_label, 1)
        emg_bin_layout.addWidget(self.emg_bin_btn)
        bin_layout.addLayout(emg_bin_layout)

        # IMU bin文件
        imu_bin_layout = QHBoxLayout()
        imu_label = QLabel("IMU文件:")
        imu_label.setFixedWidth(70)
        imu_bin_layout.addWidget(imu_label)
        self.imu_bin_label = QLabel("未选择")
        self.imu_bin_label.setStyleSheet("color: gray; padding: 5px; background: #f8f9fa; border: 1px solid #ddd;")
        self.imu_bin_btn = QPushButton("选择...")
        self.imu_bin_btn.setFixedWidth(70)
        self.imu_bin_btn.clicked.connect(self.select_imu_bin_dir)
        self.imu_bin_btn.setStyleSheet("""
            QPushButton {
                padding: 6px 12px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ab47bc, stop:1 #8e24aa);
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ba68c8, stop:1 #ab47bc);
            }
            QPushButton:pressed {
                background: #7b1fa2;
            }
        """)
        imu_bin_layout.addWidget(self.imu_bin_label, 1)
        imu_bin_layout.addWidget(self.imu_bin_btn)
        bin_layout.addLayout(imu_bin_layout)

        left_layout.addWidget(bin_group)

        # 同步选项
        options_group = QGroupBox("同步选项")
        options_layout = QVBoxLayout(options_group)

        self.emg1_check = QCheckBox("EMG1 (emg1_250hz → emg1_2khz)")
        self.emg1_check.setChecked(True)
        self.emg2_check = QCheckBox("EMG2 (emg2_250hz → emg2_2khz)")
        self.emg2_check.setChecked(True)
        self.imu1_check = QCheckBox("IMU1 (imu1_250hz → imu1_100hz)")
        self.imu1_check.setChecked(True)
        self.imu2_check = QCheckBox("IMU2 (imu2_250hz → imu2_100hz)")
        self.imu2_check.setChecked(True)
        self.validate_check = QCheckBox("数据校验")
        self.validate_check.setChecked(True)

        options_layout.addWidget(self.emg1_check)
        options_layout.addWidget(self.emg2_check)
        options_layout.addWidget(self.imu1_check)
        options_layout.addWidget(self.imu2_check)
        options_layout.addWidget(self.validate_check)

        left_layout.addWidget(options_group)

        # 同步按钮和进度
        self.sync_btn = QPushButton("开始同步")
        self.sync_btn.setStyleSheet("""
            QPushButton {
                font-weight: bold;
                font-size: 14px;
                padding: 14px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ff9800, stop:1 #f57c00);
                color: white;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ffa726, stop:1 #ff9800);
            }
            QPushButton:pressed {
                background: #e65100;
            }
            QPushButton:disabled {
                background: #ccc;
                color: #888;
            }
        """)
        self.sync_btn.clicked.connect(self.start_sync)
        left_layout.addWidget(self.sync_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 4px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #0078d4;
            }
        """)
        left_layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #666;")
        left_layout.addWidget(self.progress_label)

        left_layout.addStretch()
        splitter.addWidget(left_widget)

        # 右侧：日志面板
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        log_label = QLabel("同步日志")
        log_label.setStyleSheet("font-weight: bold; font-size: 12px; padding: 5px;")
        right_layout.addWidget(log_label)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setStyleSheet("""
            QTextEdit {
                border: 1px solid #ccc;
                background-color: #1e1e1e;
                color: #d4d4d4;
            }
        """)
        right_layout.addWidget(self.log_text)

        clear_log_btn = QPushButton("清空日志")
        clear_log_btn.clicked.connect(self.log_text.clear)
        clear_log_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #78909c, stop:1 #607d8b);
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #90a4ae, stop:1 #78909c);
            }
            QPushButton:pressed {
                background: #546e7a;
            }
        """)
        right_layout.addWidget(clear_log_btn)

        splitter.addWidget(right_widget)
        splitter.setSizes([350, 500])

        layout.addWidget(splitter)

    def add_h5_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择H5文件", "", "HDF5文件 (*.h5 *.hdf5);;所有文件 (*)"
        )
        for f in files:
            if f not in self.h5_files:
                self.h5_files.append(f)
                item = QListWidgetItem(os.path.basename(f))
                item.setToolTip(f)
                self.h5_list.addItem(item)
        self.h5_count_label.setText(f"共 {len(self.h5_files)} 个文件")

    def add_files_from_list(self, files):
        for f in files:
            if f not in self.h5_files:
                self.h5_files.append(f)
                item = QListWidgetItem(os.path.basename(f))
                item.setToolTip(f)
                self.h5_list.addItem(item)
        self.h5_count_label.setText(f"共 {len(self.h5_files)} 个文件")

    def clear_h5_files(self):
        self.h5_files.clear()
        self.h5_list.clear()
        self.h5_count_label.setText("共 0 个文件")

    def select_emg_bin_dir(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择EMG Bin文件", "", "Binary Files (*emg*.bin *.bin);;All Files (*)"
        )
        if file_path:
            self.emg_bin_dir = file_path
            self.emg_bin_label.setText(os.path.basename(file_path))
            self.emg_bin_label.setStyleSheet("color: #009900; padding: 5px; background: #f0fff0; border: 1px solid #90EE90;")
            self.emg_bin_label.setToolTip(file_path)

    def select_imu_bin_dir(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择IMU Bin文件", "", "Binary Files (*imu*.bin *.bin);;All Files (*)"
        )
        if file_path:
            self.imu_bin_dir = file_path
            self.imu_bin_label.setText(os.path.basename(file_path))
            self.imu_bin_label.setStyleSheet("color: #cc6600; padding: 5px; background: #fff8f0; border: 1px solid #ffcc99;")
            self.imu_bin_label.setToolTip(file_path)

    def start_sync(self):
        if not self.h5_files:
            QMessageBox.warning(self, "警告", "请先添加H5文件")
            return
        if not self.emg_bin_dir:
            QMessageBox.warning(self, "警告", "请选择EMG Bin文件（同步必须）")
            return

        devices = []
        if self.emg1_check.isChecked(): devices.append('emg1')
        if self.emg2_check.isChecked(): devices.append('emg2')
        if self.imu1_check.isChecked(): devices.append('imu1')
        if self.imu2_check.isChecked(): devices.append('imu2')

        if not devices:
            QMessageBox.warning(self, "警告", "请至少选择一个设备")
            return

        self.sync_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.log_text.append("=" * 50)
        self.log_text.append("开始同步...")

        self.worker = SyncWorker(
            self.h5_files, self.emg_bin_dir, self.imu_bin_dir,
            devices, self.validate_check.isChecked()
        )
        self.worker.progress.connect(self.on_progress)
        self.worker.log.connect(self.on_log)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def on_progress(self, current, total, message):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"处理 {current}/{total}: {message}")

    def on_log(self, message):
        self.log_text.append(message)

    def on_finished(self, success, message):
        self.sync_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_label.setText("")
        self.log_text.append("")
        self.log_text.append(message)
        self.log_text.append("=" * 50)
        if success:
            QMessageBox.information(self, "完成", message)
        else:
            QMessageBox.warning(self, "错误", message)


class HDF5Tool(QMainWindow):
    """HDF5整合工具主窗口"""
    def __init__(self):
        super().__init__()
        self.current_directory = None
        self.h5_files = []
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("HDF5整合工具 - 查看与同步")
        self.setMinimumSize(1300, 800)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # 主分割器 - 可拖动
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setHandleWidth(5)
        main_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #ddd;
            }
            QSplitter::handle:hover {
                background-color: #aaa;
            }
        """)

        # 左侧：文件列表面板
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 目录选择
        dir_group = QGroupBox("目录选择")
        dir_layout = QVBoxLayout(dir_group)

        dir_btn_layout = QHBoxLayout()
        self.dir_btn = QPushButton("选择目录")
        self.dir_btn.clicked.connect(self.select_directory)
        self.dir_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #5c6bc0, stop:1 #3f51b5);
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #7986cb, stop:1 #5c6bc0);
            }
            QPushButton:pressed {
                background: #303f9f;
            }
        """)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.refresh_files)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #26a69a, stop:1 #00897b);
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #4db6ac, stop:1 #26a69a);
            }
            QPushButton:pressed {
                background: #00695c;
            }
        """)
        dir_btn_layout.addWidget(self.dir_btn)
        dir_btn_layout.addWidget(self.refresh_btn)
        dir_layout.addLayout(dir_btn_layout)

        self.dir_label = QLabel("未选择目录")
        self.dir_label.setWordWrap(True)
        self.dir_label.setStyleSheet("color: gray; font-size: 10px; padding: 5px; background: #f8f9fa;")
        dir_layout.addWidget(self.dir_label)

        left_layout.addWidget(dir_group)

        # 文件列表
        file_group = QGroupBox("H5文件列表")
        file_layout = QVBoxLayout(file_group)

        self.file_count_label = QLabel("共 0 个文件")
        self.file_count_label.setStyleSheet("color: #666;")
        file_layout.addWidget(self.file_count_label)

        self.file_list = QListWidget()
        self.file_list.itemClicked.connect(self.on_file_selected)
        self.file_list.itemDoubleClicked.connect(self.on_file_double_clicked)
        self.file_list.setAlternatingRowColors(True)
        self.file_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ccc;
                alternate-background-color: #f8f9fa;
            }
            QListWidget::item:selected {
                background-color: #0078d4;
                color: white;
            }
        """)
        file_layout.addWidget(self.file_list)

        # 操作按钮
        self.view_btn = QPushButton("查看选中文件")
        self.view_btn.clicked.connect(self.view_selected_file)
        self.view_btn.setEnabled(False)
        self.view_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 16px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #42a5f5, stop:1 #1e88e5);
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #64b5f6, stop:1 #42a5f5);
            }
            QPushButton:pressed {
                background: #1565c0;
            }
            QPushButton:disabled {
                background: #ccc;
                color: #888;
            }
        """)
        file_layout.addWidget(self.view_btn)

        self.add_to_sync_btn = QPushButton("添加到同步列表")
        self.add_to_sync_btn.clicked.connect(self.add_to_sync_list)
        self.add_to_sync_btn.setEnabled(False)
        self.add_to_sync_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 16px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ff9800, stop:1 #f57c00);
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ffa726, stop:1 #ff9800);
            }
            QPushButton:pressed {
                background: #e65100;
            }
            QPushButton:disabled {
                background: #ccc;
                color: #888;
            }
        """)
        file_layout.addWidget(self.add_to_sync_btn)

        left_layout.addWidget(file_group)
        main_splitter.addWidget(left_panel)

        # 右侧：标签页
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #ccc;
                background: white;
            }
            QTabBar::tab {
                padding: 10px 20px;
                margin-right: 2px;
                background-color: #e9ecef;
                border: 1px solid #ccc;
                border-bottom: none;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background-color: white;
                border-bottom: 1px solid white;
            }
            QTabBar::tab:hover {
                background-color: #dee2e6;
            }
        """)

        self.viewer_tab = ViewerTab()
        self.tabs.addTab(self.viewer_tab, "查看")

        self.sync_tab = SyncTab()
        self.tabs.addTab(self.sync_tab, "同步")

        main_splitter.addWidget(self.tabs)
        main_splitter.setSizes([250, 1000])

        main_layout.addWidget(main_splitter)

    def select_directory(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择包含H5文件的目录")
        if dir_path:
            self.current_directory = dir_path
            self.dir_label.setText(dir_path)
            self.dir_label.setStyleSheet("color: #0066cc; font-size: 10px; padding: 5px; background: #e6f3ff;")
            self.scan_h5_files()

    def refresh_files(self):
        if self.current_directory:
            self.scan_h5_files()

    def scan_h5_files(self):
        self.file_list.clear()
        self.h5_files = []

        if not self.current_directory:
            return

        for root, dirs, files in os.walk(self.current_directory):
            for file in files:
                if file.endswith(('.h5', '.hdf5')):
                    full_path = os.path.join(root, file)
                    self.h5_files.append(full_path)

                    item = QListWidgetItem(file)
                    item.setData(Qt.UserRole, full_path)
                    item.setToolTip(full_path)
                    self.file_list.addItem(item)

        self.file_count_label.setText(f"共 {len(self.h5_files)} 个文件")

    def on_file_selected(self, item):
        self.view_btn.setEnabled(True)
        self.add_to_sync_btn.setEnabled(True)

    def on_file_double_clicked(self, item):
        file_path = item.data(Qt.UserRole)
        if file_path:
            self.viewer_tab.load_file(file_path)
            self.tabs.setCurrentIndex(0)

    def view_selected_file(self):
        item = self.file_list.currentItem()
        if item:
            file_path = item.data(Qt.UserRole)
            self.viewer_tab.load_file(file_path)
            self.tabs.setCurrentIndex(0)

    def add_to_sync_list(self):
        item = self.file_list.currentItem()
        if item:
            file_path = item.data(Qt.UserRole)
            self.sync_tab.add_files_from_list([file_path])
            self.tabs.setCurrentIndex(1)


def main():
    app = QApplication(sys.argv)
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)

    window = HDF5Tool()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
