"""
HDF5 Viewer - 查看EMG/IMU采集数据的HDF5文件

功能:
1. 浏览HDF5文件结构（树形视图）
2. 查看数据集的属性和形状
3. 预览每个数据集的前N条数据
4. 支持EMG/IMU/Prompts/Stages数据的格式化显示

使用方法:
    python hdf5_viewer.py
    或
    python hdf5_viewer.py <file.h5>
"""

import sys
import os
import numpy as np
import h5py
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QTextEdit, QSplitter, QPushButton,
    QFileDialog, QLabel, QSpinBox, QGroupBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QStatusBar
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor


class HDF5Viewer(QMainWindow):
    def __init__(self, file_path=None):
        super().__init__()
        self.file_path = file_path
        self.h5file = None
        self.preview_count = 10  # 默认预览条数
        
        self.init_ui()
        
        if file_path and os.path.exists(file_path):
            self.load_file(file_path)
    
    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle('HDF5 EMG/IMU Data Viewer')
        self.setGeometry(100, 100, 1400, 900)
        
        # 主布局
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # ========== 顶部工具栏 ==========
        toolbar = QHBoxLayout()
        
        # 打开文件按钮
        self.btn_open = QPushButton('📂 打开HDF5文件')
        self.btn_open.clicked.connect(self.open_file_dialog)
        toolbar.addWidget(self.btn_open)
        
        # 文件路径显示
        self.label_file = QLabel('未加载文件')
        self.label_file.setStyleSheet('color: #666; padding: 5px;')
        toolbar.addWidget(self.label_file, 1)
        
        # 预览条数设置
        toolbar.addWidget(QLabel('预览条数:'))
        self.spin_preview = QSpinBox()
        self.spin_preview.setRange(1, 100)
        self.spin_preview.setValue(10)
        self.spin_preview.valueChanged.connect(self.on_preview_count_changed)
        toolbar.addWidget(self.spin_preview)
        
        # 刷新按钮
        self.btn_refresh = QPushButton('🔄 刷新')
        self.btn_refresh.clicked.connect(self.refresh_view)
        toolbar.addWidget(self.btn_refresh)
        
        main_layout.addLayout(toolbar)
        
        # ========== 主内容区域（分割器） ==========
        splitter = QSplitter(Qt.Horizontal)
        
        # 左侧：文件结构树
        left_panel = QGroupBox('文件结构')
        left_layout = QVBoxLayout(left_panel)
        
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['名称', '类型', '形状/值'])
        self.tree.setColumnWidth(0, 200)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 150)
        self.tree.itemClicked.connect(self.on_tree_item_clicked)
        left_layout.addWidget(self.tree)
        
        splitter.addWidget(left_panel)
        
        # 右侧：数据预览
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        # 选中项信息
        self.info_group = QGroupBox('选中项信息')
        info_layout = QVBoxLayout(self.info_group)
        self.label_info = QTextEdit()
        self.label_info.setReadOnly(True)
        self.label_info.setMaximumHeight(150)
        self.label_info.setFont(QFont('Consolas', 10))
        info_layout.addWidget(self.label_info)
        right_layout.addWidget(self.info_group)
        
        # 数据预览标签页
        self.tabs = QTabWidget()
        
        # 表格视图
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.tabs.addTab(self.table, '📊 表格视图')
        
        # 文本视图
        self.text_preview = QTextEdit()
        self.text_preview.setReadOnly(True)
        self.text_preview.setFont(QFont('Consolas', 10))
        self.tabs.addTab(self.text_preview, '📝 文本视图')
        
        right_layout.addWidget(self.tabs)
        
        splitter.addWidget(right_panel)
        splitter.setSizes([400, 1000])
        
        main_layout.addWidget(splitter)
        
        # ========== 状态栏 ==========
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage('就绪')
        
        # 样式
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f5f5;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #ccc;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            QPushButton {
                padding: 8px 15px;
                border-radius: 4px;
                background-color: #4CAF50;
                color: white;
                border: none;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QTreeWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
            }
            QTableWidget {
                border: 1px solid #ddd;
                gridline-color: #eee;
            }
            QTextEdit {
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: #fafafa;
            }
        """)
    
    def open_file_dialog(self):
        """打开文件对话框"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, '选择HDF5文件', '', 'HDF5 Files (*.h5 *.hdf5);;All Files (*)'
        )
        if file_path:
            self.load_file(file_path)
    
    def load_file(self, file_path):
        """加载HDF5文件"""
        try:
            # 关闭之前的文件
            if self.h5file:
                self.h5file.close()
            
            self.file_path = file_path
            self.h5file = h5py.File(file_path, 'r')
            
            self.label_file.setText(f'📁 {file_path}')
            self.label_file.setStyleSheet('color: #333; padding: 5px; font-weight: bold;')
            
            self.build_tree()
            self.show_file_summary()
            
            self.statusBar.showMessage(f'已加载: {file_path}')
            
        except Exception as e:
            QMessageBox.critical(self, '错误', f'无法加载文件:\n{str(e)}')
            self.statusBar.showMessage(f'加载失败: {str(e)}')
    
    def build_tree(self):
        """构建文件结构树"""
        self.tree.clear()
        
        if not self.h5file:
            return
        
        # 根节点（文件属性）
        root = QTreeWidgetItem(self.tree, ['📁 ' + os.path.basename(self.file_path), 'File', ''])
        root.setData(0, Qt.UserRole, '/')
        
        # 添加文件属性
        if self.h5file.attrs:
            attrs_item = QTreeWidgetItem(root, ['📋 属性', 'Attributes', f'{len(self.h5file.attrs)} 项'])
            attrs_item.setData(0, Qt.UserRole, '/@attrs')
            for key in self.h5file.attrs:
                val = self.h5file.attrs[key]
                val_str = str(val)[:50] + '...' if len(str(val)) > 50 else str(val)
                attr_item = QTreeWidgetItem(attrs_item, [f'  {key}', type(val).__name__, val_str])
                attr_item.setData(0, Qt.UserRole, f'/@attr:{key}')
        
        # 遍历文件内容
        def add_items(parent_item, h5_group, path=''):
            for key in h5_group:
                item_path = f'{path}/{key}'
                obj = h5_group[key]
                
                if isinstance(obj, h5py.Group):
                    # 组
                    group_item = QTreeWidgetItem(parent_item, [f'📂 {key}', 'Group', f'{len(obj)} 项'])
                    group_item.setData(0, Qt.UserRole, item_path)
                    
                    # 组属性
                    if obj.attrs:
                        g_attrs = QTreeWidgetItem(group_item, ['📋 属性', 'Attributes', f'{len(obj.attrs)} 项'])
                        g_attrs.setData(0, Qt.UserRole, f'{item_path}/@attrs')
                    
                    # 递归添加子项
                    add_items(group_item, obj, item_path)
                    
                elif isinstance(obj, h5py.Dataset):
                    # 数据集
                    shape_str = str(obj.shape)
                    dtype_str = str(obj.dtype)
                    
                    # 根据数据类型选择图标
                    if 'emg' in key.lower():
                        icon = '📈'
                    elif 'imu' in key.lower():
                        icon = '🔄'
                    elif 'time' in key.lower() or 'name' in key.lower():
                        icon = '🏷️'
                    else:
                        icon = '📊'
                    
                    ds_item = QTreeWidgetItem(parent_item, [f'{icon} {key}', dtype_str, shape_str])
                    ds_item.setData(0, Qt.UserRole, item_path)
                    
                    # 数据集属性
                    if obj.attrs:
                        ds_attrs = QTreeWidgetItem(ds_item, ['📋 属性', 'Attributes', f'{len(obj.attrs)} 项'])
                        ds_attrs.setData(0, Qt.UserRole, f'{item_path}/@attrs')
        
        add_items(root, self.h5file)
        
        # 展开根节点
        root.setExpanded(True)
        for i in range(root.childCount()):
            root.child(i).setExpanded(True)
    
    def show_file_summary(self):
        """显示文件摘要"""
        if not self.h5file:
            return
        
        summary = []
        summary.append('=' * 50)
        summary.append('文件摘要')
        summary.append('=' * 50)
        
        # 文件属性
        summary.append('\n【文件属性】')
        for key in self.h5file.attrs:
            val = self.h5file.attrs[key]
            summary.append(f'  {key}: {val}')
        
        # 数据统计
        summary.append('\n【数据统计】')
        
        if 'emg1' in self.h5file:
            summary.append(f'  EMG1: {self.h5file["emg1"].shape[0]} 帧')
        if 'emg2' in self.h5file:
            summary.append(f'  EMG2: {self.h5file["emg2"].shape[0]} 帧')
        if 'imu1' in self.h5file:
            summary.append(f'  IMU1: {self.h5file["imu1"].shape[0]} 帧')
        if 'imu2' in self.h5file:
            summary.append(f'  IMU2: {self.h5file["imu2"].shape[0]} 帧')
        
        if 'prompts' in self.h5file:
            prompts = self.h5file['prompts']
            if 'names' in prompts:
                summary.append(f'  Prompts: {prompts["names"].shape[0]} 条')
        
        if 'stages' in self.h5file:
            stages = self.h5file['stages']
            if 'names' in stages:
                summary.append(f'  Stages: {stages["names"].shape[0]} 条')
        
        self.label_info.setText('\n'.join(summary))
        self.text_preview.setText('\n'.join(summary))
    
    def on_tree_item_clicked(self, item, column):
        """树节点点击事件"""
        path = item.data(0, Qt.UserRole)
        if not path:
            return
        
        self.show_item_details(path)
    
    def show_item_details(self, path):
        """显示选中项的详细信息"""
        if not self.h5file:
            return
        
        try:
            # 处理属性路径
            if '/@attr:' in path:
                base_path, attr_name = path.split('/@attr:')
                if base_path == '':
                    attrs = self.h5file.attrs
                else:
                    attrs = self.h5file[base_path].attrs
                self.show_attribute(attr_name, attrs[attr_name])
                return
            
            if path.endswith('/@attrs'):
                base_path = path.replace('/@attrs', '')
                if base_path == '':
                    self.show_attributes(self.h5file.attrs, '文件属性')
                else:
                    self.show_attributes(self.h5file[base_path].attrs, f'{base_path} 属性')
                return
            
            if path == '/':
                self.show_file_summary()
                return
            
            obj = self.h5file[path]
            
            if isinstance(obj, h5py.Group):
                self.show_group_info(path, obj)
            elif isinstance(obj, h5py.Dataset):
                self.show_dataset_preview(path, obj)
                
        except Exception as e:
            self.label_info.setText(f'错误: {str(e)}')
            self.text_preview.setText(f'无法显示: {path}\n错误: {str(e)}')
    
    def show_attributes(self, attrs, title):
        """显示属性列表"""
        info = [f'【{title}】', '-' * 40]
        for key in attrs:
            val = attrs[key]
            info.append(f'{key}: {val}')
        
        self.label_info.setText('\n'.join(info))
        
        # 表格显示
        self.table.clear()
        self.table.setRowCount(len(attrs))
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(['属性名', '类型', '值'])
        
        for i, key in enumerate(attrs):
            val = attrs[key]
            self.table.setItem(i, 0, QTableWidgetItem(str(key)))
            self.table.setItem(i, 1, QTableWidgetItem(type(val).__name__))
            self.table.setItem(i, 2, QTableWidgetItem(str(val)))
        
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.text_preview.setText('\n'.join(info))
    
    def show_attribute(self, name, value):
        """显示单个属性"""
        info = f'属性: {name}\n类型: {type(value).__name__}\n值: {value}'
        self.label_info.setText(info)
        self.text_preview.setText(info)
    
    def show_group_info(self, path, group):
        """显示组信息"""
        info = [f'【组: {path}】', '-' * 40]
        info.append(f'子项数量: {len(group)}')
        info.append(f'属性数量: {len(group.attrs)}')
        info.append('')
        info.append('子项列表:')
        for key in group:
            obj = group[key]
            if isinstance(obj, h5py.Group):
                info.append(f'  📂 {key} (Group)')
            else:
                info.append(f'  📊 {key} {obj.shape} {obj.dtype}')
        
        self.label_info.setText('\n'.join(info))
        self.text_preview.setText('\n'.join(info))
        
        # 清空表格
        self.table.clear()
        self.table.setRowCount(0)
    
    def show_dataset_preview(self, path, dataset):
        """显示数据集预览"""
        n = min(self.preview_count, dataset.shape[0]) if dataset.shape else 0
        
        # 信息面板
        info = [
            f'【数据集: {path}】',
            '-' * 40,
            f'形状: {dataset.shape}',
            f'类型: {dataset.dtype}',
            f'大小: {dataset.size} 元素',
            f'压缩: {dataset.compression or "无"}',
            f'分块: {dataset.chunks}',
        ]
        
        if dataset.attrs:
            info.append('')
            info.append('属性:')
            for key in dataset.attrs:
                info.append(f'  {key}: {dataset.attrs[key]}')
        
        self.label_info.setText('\n'.join(info))
        
        # 根据数据类型选择显示方式
        if n == 0:
            self.text_preview.setText('数据集为空')
            self.table.clear()
            return
        
        data = dataset[:n]
        
        # 判断数据类型并格式化显示
        if 'emg' in path.lower() and dataset.dtype.names:
            self.show_emg_data(path, data, dataset.dtype)
        elif 'imu' in path.lower() and dataset.dtype.names:
            self.show_imu_data(path, data, dataset.dtype)
        elif 'prompts' in path.lower() or 'stages' in path.lower():
            self.show_label_data(path, data)
        else:
            self.show_generic_data(path, data)
    
    def show_emg_data(self, path, data, dtype):
        """显示EMG数据"""
        text_lines = [f'【{path} - EMG数据预览 (前{len(data)}帧)】', '=' * 80]
        
        # 表格设置
        self.table.clear()
        self.table.setRowCount(len(data))
        
        # EMG有channels和time字段
        if 'channels' in dtype.names:
            n_channels = data['channels'].shape[1] if len(data['channels'].shape) > 1 else 16
            headers = ['帧序号'] + [f'Ch{i}' for i in range(n_channels)] + ['时间戳']
            self.table.setColumnCount(len(headers))
            self.table.setHorizontalHeaderLabels(headers)
            
            for i, row in enumerate(data):
                self.table.setItem(i, 0, QTableWidgetItem(str(i)))
                channels = row['channels']
                for j, val in enumerate(channels):
                    self.table.setItem(i, j + 1, QTableWidgetItem(f'{val:.2f}'))
                self.table.setItem(i, n_channels + 1, QTableWidgetItem(f'{row["time"]:.6f}'))
                
                # 文本预览
                ch_str = ', '.join([f'{v:.2f}' for v in channels[:8]]) + '...'
                text_lines.append(f'帧{i:4d}: [{ch_str}] t={row["time"]:.6f}')
        
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.text_preview.setText('\n'.join(text_lines))
    
    def show_imu_data(self, path, data, dtype):
        """显示IMU数据"""
        text_lines = [f'【{path} - IMU数据预览 (前{len(data)}帧)】', '=' * 80]
        
        # 表格设置
        self.table.clear()
        self.table.setRowCount(len(data))
        
        headers = ['帧序号', 'Acc_X', 'Acc_Y', 'Acc_Z', 'Gyr_X', 'Gyr_Y', 'Gyr_Z', 'Mag_X', 'Mag_Y', 'Mag_Z', '时间戳']
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        
        for i, row in enumerate(data):
            self.table.setItem(i, 0, QTableWidgetItem(str(i)))
            
            col = 1
            for sensor in ['acc', 'gyr', 'mag']:
                if sensor in dtype.names:
                    for j, val in enumerate(row[sensor]):
                        self.table.setItem(i, col, QTableWidgetItem(f'{val:.4f}'))
                        col += 1
            
            if 'time' in dtype.names:
                self.table.setItem(i, col, QTableWidgetItem(f'{row["time"]:.6f}'))
            
            # 文本预览
            acc_str = f'Acc=[{row["acc"][0]:.3f}, {row["acc"][1]:.3f}, {row["acc"][2]:.3f}]' if 'acc' in dtype.names else ''
            gyr_str = f'Gyr=[{row["gyr"][0]:.3f}, {row["gyr"][1]:.3f}, {row["gyr"][2]:.3f}]' if 'gyr' in dtype.names else ''
            time_str = f't={row["time"]:.6f}' if 'time' in dtype.names else ''
            text_lines.append(f'帧{i:4d}: {acc_str} {gyr_str} {time_str}')
        
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.text_preview.setText('\n'.join(text_lines))
    
    def show_label_data(self, path, data):
        """显示标签数据（Prompts/Stages的names, times等）"""
        text_lines = [f'【{path} - 标签数据预览 (前{len(data)}条)】', '=' * 80]
        
        self.table.clear()
        self.table.setRowCount(len(data))
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(['序号', '值'])
        
        for i, val in enumerate(data):
            self.table.setItem(i, 0, QTableWidgetItem(str(i)))
            
            # 处理不同类型的值
            if isinstance(val, bytes):
                val_str = val.decode('utf-8', errors='replace')
            elif isinstance(val, np.floating):
                val_str = f'{val:.6f}'
            else:
                val_str = str(val)
            
            self.table.setItem(i, 1, QTableWidgetItem(val_str))
            text_lines.append(f'{i:4d}: {val_str}')
        
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.text_preview.setText('\n'.join(text_lines))
    
    def show_generic_data(self, path, data):
        """显示通用数据"""
        text_lines = [f'【{path} - 数据预览 (前{len(data)}条)】', '=' * 80]
        
        # 尝试转换为表格
        self.table.clear()
        
        if data.ndim == 1:
            self.table.setRowCount(len(data))
            self.table.setColumnCount(2)
            self.table.setHorizontalHeaderLabels(['序号', '值'])
            
            for i, val in enumerate(data):
                self.table.setItem(i, 0, QTableWidgetItem(str(i)))
                if isinstance(val, bytes):
                    val_str = val.decode('utf-8', errors='replace')
                elif isinstance(val, (np.floating, float)):
                    val_str = f'{val:.6f}'
                else:
                    val_str = str(val)
                self.table.setItem(i, 1, QTableWidgetItem(val_str))
                text_lines.append(f'{i:4d}: {val_str}')
                
        elif data.ndim == 2:
            rows, cols = data.shape
            self.table.setRowCount(rows)
            self.table.setColumnCount(cols + 1)
            headers = ['序号'] + [f'Col{j}' for j in range(cols)]
            self.table.setHorizontalHeaderLabels(headers)
            
            for i in range(rows):
                self.table.setItem(i, 0, QTableWidgetItem(str(i)))
                row_vals = []
                for j in range(cols):
                    val = data[i, j]
                    if isinstance(val, (np.floating, float)):
                        val_str = f'{val:.6f}'
                    else:
                        val_str = str(val)
                    self.table.setItem(i, j + 1, QTableWidgetItem(val_str))
                    row_vals.append(val_str)
                text_lines.append(f'{i:4d}: [{", ".join(row_vals)}]')
        
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.text_preview.setText('\n'.join(text_lines))
    
    def on_preview_count_changed(self, value):
        """预览条数改变"""
        self.preview_count = value
    
    def refresh_view(self):
        """刷新视图"""
        if self.file_path:
            self.load_file(self.file_path)
    
    def closeEvent(self, event):
        """关闭窗口时释放资源"""
        if self.h5file:
            self.h5file.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    
    # 检查命令行参数
    file_path = None
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
    
    viewer = HDF5Viewer(file_path)
    viewer.show()
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
