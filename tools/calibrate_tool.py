#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calibrate_tool.py - H5数据可视化与滤波工具

功能：
- 读取H5文件中的EMG和IMU原始数据
- 使用与ble_server.py相同的滤波方法处理EMG数据
- 可视化EMG（16通道）和IMU（加速度3通道）数据
- 支持滑块拖动查看不同时间段的数据
"""

import sys
import os
import h5py
import numpy as np
from scipy import signal as scipy_signal
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QSlider, QSpinBox, QGroupBox,
    QSplitter, QComboBox, QCheckBox, QMessageBox
)
from PyQt5.QtCore import Qt, QTimer
import matplotlib
matplotlib.use('Qt5Agg')

# 设置中文字体支持
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure


# ============== EMG滤波参数（与ble_server.py保持一致）==============
BASE_LSB_24BIT = 0.476837          # 24-bit 基础 LSB
HARDWARE_FRONTEND_GAIN = 5.9       # 硬件前端增益
DEFAULT_GAIN = 12                  # 默认增益
SAMPLE_RATE = 2000                 # EMG采样率 2kHz (同步后)
SAMPLE_RATE_BLE = 250              # BLE传输采样率 250Hz

# 滤波器参数
FILTER_LOWCUT = 20                 # 带通下限 (Hz)
FILTER_HIGHCUT = 100               # 带通上限 (Hz)
FILTER_NOTCH_FREQ = 50             # 工频频率 (Hz)
FILTER_NOTCH_Q = 15                # 陷波器 Q 值

# IMU 缩放系数
SCALE_ACCEL = 16.0 / 32768.0       # 加速度 ±2g
SCALE_GYRO = 2000.0 / 32768.0      # 角速度 ±2000°/s
SCALE_MAG = 0.15                   # 磁力计 μT


def calculate_lsb_uv(gain=DEFAULT_GAIN, is_16bit=False, shift=4):
    """计算ADC原始值到μV的转换系数"""
    lsb_uv = BASE_LSB_24BIT / (gain * HARDWARE_FRONTEND_GAIN)
    if is_16bit:
        lsb_uv = lsb_uv * (2 ** shift)
    return lsb_uv


class EMGFilter:
    """EMG信号滤波器（离线版本，与ble_server.py算法一致）"""

    def __init__(self, sample_rate=SAMPLE_RATE, num_channels=16):
        self.sample_rate = sample_rate
        self.num_channels = num_channels

        # 设计带通滤波器 (20-100Hz)
        nyq = sample_rate / 2
        low = FILTER_LOWCUT / nyq
        high = FILTER_HIGHCUT / nyq
        # 确保频率在有效范围内
        low = max(0.001, min(low, 0.99))
        high = max(low + 0.001, min(high, 0.99))
        self.b_bandpass, self.a_bandpass = scipy_signal.butter(4, [low, high], btype='band')

        # 设计工频陷波滤波器 (50Hz及其谐波)
        self.notch_filters = []
        for harmonic in range(1, 4):  # 50Hz, 100Hz, 150Hz
            freq = FILTER_NOTCH_FREQ * harmonic
            if freq < nyq:
                b, a = scipy_signal.iirnotch(freq, FILTER_NOTCH_Q, sample_rate)
                self.notch_filters.append((b, a))

    def filter(self, data):
        """
        对EMG数据进行滤波

        Args:
            data: numpy array, shape (N, 16) 或 (N,) 单通道

        Returns:
            滤波后的数据，shape与输入相同
        """
        if data.ndim == 1:
            data = data.reshape(-1, 1)

        filtered = data.copy().astype(np.float64)

        # 应用带通滤波
        filtered = scipy_signal.filtfilt(self.b_bandpass, self.a_bandpass, filtered, axis=0)

        # 应用工频陷波
        for b, a in self.notch_filters:
            filtered = scipy_signal.filtfilt(b, a, filtered, axis=0)

        return filtered


class CalibrateTool(QMainWindow):
    """H5数据可视化工具主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle('H5数据可视化工具 - calibrate_tool')
        self.setGeometry(100, 100, 1600, 900)

        # 数据存储
        self.h5_file = None
        self.h5_path = None
        self.emg1_data = None
        self.emg2_data = None
        self.imu1a_data = None
        self.imu1b_data = None
        self.imu2a_data = None
        self.imu2b_data = None

        # 滤波器
        self.emg_filter_2k = EMGFilter(sample_rate=2000)
        self.emg_filter_250 = EMGFilter(sample_rate=250)

        # 显示参数
        self.window_size = 15000  # 显示窗口大小（采样点数）
        self.current_pos = 0     # 当前位置

        # 滑块平滑更新相关
        self.update_timer = QTimer()
        self.update_timer.setSingleShot(True)
        self.update_timer.timeout.connect(self._do_update_plots)
        self.pending_update = False
        self.is_dragging = False  # 是否正在拖动滑块

        self.init_ui()

    def init_ui(self):
        """初始化UI"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # === 顶部控制栏 ===
        control_layout = QHBoxLayout()

        # 文件选择
        self.btn_open = QPushButton('打开H5文件')
        self.btn_open.clicked.connect(self.open_file)
        control_layout.addWidget(self.btn_open)

        self.lbl_file = QLabel('未选择文件')
        self.lbl_file.setStyleSheet('color: #666; font-style: italic;')
        control_layout.addWidget(self.lbl_file)

        control_layout.addStretch()

        # 窗口大小
        control_layout.addWidget(QLabel('显示窗口:'))
        self.spin_window = QSpinBox()
        self.spin_window.setRange(100, 50000)
        self.spin_window.setValue(self.window_size)
        self.spin_window.setSingleStep(1000)
        self.spin_window.valueChanged.connect(self.on_window_changed)
        control_layout.addWidget(self.spin_window)
        control_layout.addWidget(QLabel('采样点'))

        # 滤波开关
        self.chk_filter = QCheckBox('启用滤波')
        self.chk_filter.setChecked(True)
        self.chk_filter.stateChanged.connect(self.update_plots)
        control_layout.addWidget(self.chk_filter)

        main_layout.addLayout(control_layout)

        # === 图表区域 ===
        splitter = QSplitter(Qt.Vertical)

        # EMG图表
        emg_widget = QWidget()
        emg_layout = QVBoxLayout(emg_widget)
        emg_layout.setContentsMargins(0, 0, 0, 0)

        self.fig_emg = Figure(figsize=(16, 5), dpi=100)
        self.canvas_emg = FigureCanvas(self.fig_emg)
        self.toolbar_emg = NavigationToolbar(self.canvas_emg, self)
        emg_layout.addWidget(self.toolbar_emg)
        emg_layout.addWidget(self.canvas_emg)
        splitter.addWidget(emg_widget)

        # IMU图表
        imu_widget = QWidget()
        imu_layout = QVBoxLayout(imu_widget)
        imu_layout.setContentsMargins(0, 0, 0, 0)

        self.fig_imu = Figure(figsize=(16, 4), dpi=100)
        self.canvas_imu = FigureCanvas(self.fig_imu)
        self.toolbar_imu = NavigationToolbar(self.canvas_imu, self)
        imu_layout.addWidget(self.toolbar_imu)
        imu_layout.addWidget(self.canvas_imu)
        splitter.addWidget(imu_widget)

        main_layout.addWidget(splitter)

        # === 底部滑块 ===
        slider_layout = QHBoxLayout()

        self.lbl_pos = QLabel('位置: 0')
        slider_layout.addWidget(self.lbl_pos)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(0)
        # 平滑更新：使用节流机制
        self.slider.valueChanged.connect(self.on_slider_changed)
        self.slider.sliderPressed.connect(self.on_slider_pressed)
        self.slider.sliderReleased.connect(self.on_slider_released)
        slider_layout.addWidget(self.slider)

        self.lbl_total = QLabel('/ 0')
        slider_layout.addWidget(self.lbl_total)

        main_layout.addLayout(slider_layout)

        # 初始化图表
        self.init_plots()

    def init_plots(self):
        """初始化图表"""
        # EMG图表：2行（EMG1, EMG2），每行16通道
        self.ax_emg1 = self.fig_emg.add_subplot(211)
        self.ax_emg2 = self.fig_emg.add_subplot(212)

        self.ax_emg1.set_title('EMG1 (16通道)', fontsize=10)
        self.ax_emg2.set_title('EMG2 (16通道)', fontsize=10)
        self.ax_emg1.set_ylabel('μV')
        self.ax_emg2.set_ylabel('μV')
        self.ax_emg2.set_xlabel('采样点')

        self.fig_emg.tight_layout()

        # IMU图表：4行（imu1a, imu1b, imu2a, imu2b）
        self.ax_imu1a = self.fig_imu.add_subplot(221)
        self.ax_imu1b = self.fig_imu.add_subplot(222)
        self.ax_imu2a = self.fig_imu.add_subplot(223)
        self.ax_imu2b = self.fig_imu.add_subplot(224)

        self.ax_imu1a.set_title('IMU1A (加速度)', fontsize=10)
        self.ax_imu1b.set_title('IMU1B (加速度)', fontsize=10)
        self.ax_imu2a.set_title('IMU2A (加速度)', fontsize=10)
        self.ax_imu2b.set_title('IMU2B (加速度)', fontsize=10)

        for ax in [self.ax_imu1a, self.ax_imu1b, self.ax_imu2a, self.ax_imu2b]:
            ax.set_ylabel('g')
        self.ax_imu2a.set_xlabel('采样点')
        self.ax_imu2b.set_xlabel('采样点')

        self.fig_imu.tight_layout()

    def open_file(self):
        """打开H5文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, '选择H5文件',
            os.path.join(os.path.dirname(__file__), '..', 'storage'),
            'HDF5 Files (*.h5 *.hdf5);;All Files (*)'
        )

        if file_path:
            self.load_h5_file(file_path)

    def load_h5_file(self, file_path):
        """加载H5文件数据"""
        try:
            if self.h5_file:
                self.h5_file.close()

            self.h5_file = h5py.File(file_path, 'r')
            self.h5_path = file_path

            # 更新文件标签
            filename = os.path.basename(file_path)
            self.lbl_file.setText(filename)
            self.lbl_file.setStyleSheet('color: #333; font-weight: bold;')

            # 读取数据
            self.load_emg_data()
            self.load_imu_data()

            # 计算并保存全局的Y轴范围
            self.calculate_y_limits()

            # 更新滑块范围
            max_len = self.get_max_data_length()
            self.slider.setMaximum(max(0, max_len - self.window_size))
            self.lbl_total.setText(f'/ {max_len}')

            # 重置位置并更新显示
            self.current_pos = 0
            self.slider.setValue(0)
            self.update_plots()

            print(f'[CalibrateTool] 已加载文件: {file_path}')

        except Exception as e:
            QMessageBox.critical(self, '错误', f'加载文件失败:\n{str(e)}')
            print(f'[CalibrateTool] 加载失败: {e}')

    def load_emg_data(self):
        """加载EMG数据"""
        self.emg1_data = None
        self.emg2_data = None
        self.emg1_sample_rate = 2000  # 默认采样率
        self.emg2_sample_rate = 2000

        # 尝试不同的数据集名称（按优先级排序）
        emg1_names = ['emg1_2khz_adc', 'emg1_2khz', 'emg1_250hz_adc', 'emg1_250hz', 'emg1']
        emg2_names = ['emg2_2khz_adc', 'emg2_2khz', 'emg2_250hz_adc', 'emg2_250hz', 'emg2']

        for name in emg1_names:
            if name in self.h5_file:
                self.emg1_data = self._extract_emg_channels(self.h5_file[name][:])
                if '250hz' in name:
                    self.emg1_sample_rate = 250
                print(f'[CalibrateTool] 已加载 {name}: shape={self.emg1_data.shape if self.emg1_data is not None else "None"}')
                break

        for name in emg2_names:
            if name in self.h5_file:
                self.emg2_data = self._extract_emg_channels(self.h5_file[name][:])
                if '250hz' in name:
                    self.emg2_sample_rate = 250
                print(f'[CalibrateTool] 已加载 {name}: shape={self.emg2_data.shape if self.emg2_data is not None else "None"}')
                break

    def _extract_emg_channels(self, data):
        """从结构化数组中提取EMG通道数据"""
        print(f'[CalibrateTool] EMG数据类型: {data.dtype}, 字段: {data.dtype.names}')

        # 检查是否为结构化数组
        if data.dtype.names is not None:
            if 'channels' in data.dtype.names:
                # channels 字段本身是一个数组
                channels_data = data['channels']
                print(f'[CalibrateTool] channels字段 shape: {channels_data.shape}')
                # 确保返回 (N, 16) 的形状
                if channels_data.ndim == 1:
                    # 如果是一维结构化数组，需要转换
                    result = np.array([list(row) for row in channels_data])
                else:
                    result = np.array(channels_data)
                print(f'[CalibrateTool] 提取后 shape: {result.shape}')
                return result
            # 尝试其他可能的字段名
            for name in data.dtype.names:
                if 'ch' in name.lower() or 'channel' in name.lower():
                    return np.array(data[name])

        # 普通数组
        return np.array(data)

    def load_imu_data(self):
        """加载IMU数据"""
        self.imu1a_data = None
        self.imu1b_data = None
        self.imu2a_data = None
        self.imu2b_data = None

        # 尝试不同的数据集名称
        imu_mapping = {
            'imu1a': ['imu1a_100hz', 'imu1a_ble', 'imu1a'],
            'imu1b': ['imu1b_100hz', 'imu1b_ble', 'imu1b'],
            'imu2a': ['imu2a_100hz', 'imu2a_ble', 'imu2a'],
            'imu2b': ['imu2b_100hz', 'imu2b_ble', 'imu2b'],
        }

        for attr_name, possible_names in imu_mapping.items():
            for name in possible_names:
                if name in self.h5_file:
                    imu_data = self._extract_imu_acc(self.h5_file[name][:])
                    setattr(self, f'{attr_name}_data', imu_data)
                    print(f'[CalibrateTool] 已加载 {name}: shape={imu_data.shape}')
                    break

    def _extract_imu_acc(self, data):
        """从结构化数组中提取IMU加速度数据"""
        print(f'[CalibrateTool] IMU数据类型: {data.dtype}, 字段: {data.dtype.names}')

        if data.dtype.names is not None:
            if 'acc' in data.dtype.names:
                acc_data = data['acc']
                print(f'[CalibrateTool] acc字段 shape: {acc_data.shape}')
                # 确保返回 (N, 3) 的形状
                if acc_data.ndim == 1:
                    result = np.array([list(row) for row in acc_data])
                else:
                    result = np.array(acc_data)
                print(f'[CalibrateTool] 提取后 shape: {result.shape}')
                return result
            # 尝试其他可能的字段名
            for name in data.dtype.names:
                if 'acc' in name.lower():
                    return np.array(data[name])

        # 普通数组，假设前3列是加速度
        if data.ndim == 2 and data.shape[1] >= 3:
            return data[:, :3]

        return np.array(data)

    def calculate_y_limits(self):
        """计算全局Y轴范围（用于固定纵轴）"""
        lsb_uv = calculate_lsb_uv()

        # EMG Y轴范围
        self.emg1_ylim = None
        self.emg2_ylim = None
        self.imu_ylim = None

        # 计算EMG1的范围
        if self.emg1_data is not None and len(self.emg1_data) > 0:
            data_uv = self.emg1_data * lsb_uv
            # 对滤波后的数据计算范围（采样部分数据以提高速度）
            sample_size = min(len(data_uv), 50000)
            sample_indices = np.linspace(0, len(data_uv)-1, sample_size, dtype=int)
            sampled_data = data_uv[sample_indices]
            try:
                filtered = self.emg_filter_2k.filter(sampled_data)
                ymin, ymax = np.min(filtered), np.max(filtered)
                margin = (ymax - ymin) * 0.1
                self.emg1_ylim = (ymin - margin, ymax + margin)
            except:
                self.emg1_ylim = (np.min(sampled_data), np.max(sampled_data))
            print(f'[CalibrateTool] EMG1 Y轴范围: {self.emg1_ylim}')

        # 计算EMG2的范围
        if self.emg2_data is not None and len(self.emg2_data) > 0:
            data_uv = self.emg2_data * lsb_uv
            sample_size = min(len(data_uv), 50000)
            sample_indices = np.linspace(0, len(data_uv)-1, sample_size, dtype=int)
            sampled_data = data_uv[sample_indices]
            try:
                filtered = self.emg_filter_2k.filter(sampled_data)
                ymin, ymax = np.min(filtered), np.max(filtered)
                margin = (ymax - ymin) * 0.1
                self.emg2_ylim = (ymin - margin, ymax + margin)
            except:
                self.emg2_ylim = (np.min(sampled_data), np.max(sampled_data))
            print(f'[CalibrateTool] EMG2 Y轴范围: {self.emg2_ylim}')

        # 计算IMU的范围（所有IMU使用相同的范围）
        all_imu_data = []
        for imu_data in [self.imu1a_data, self.imu1b_data, self.imu2a_data, self.imu2b_data]:
            if imu_data is not None and len(imu_data) > 0:
                all_imu_data.append(imu_data)

        if all_imu_data:
            combined = np.vstack(all_imu_data)
            ymin, ymax = np.min(combined), np.max(combined)
            margin = (ymax - ymin) * 0.1
            self.imu_ylim = (ymin - margin, ymax + margin)
            print(f'[CalibrateTool] IMU Y轴范围: {self.imu_ylim}')

    def get_max_data_length(self):
        """获取最大数据长度"""
        lengths = []
        if self.emg1_data is not None and len(self.emg1_data) > 0:
            lengths.append(len(self.emg1_data))
            print(f'[CalibrateTool] EMG1数据长度: {len(self.emg1_data)}')
        if self.emg2_data is not None and len(self.emg2_data) > 0:
            lengths.append(len(self.emg2_data))
            print(f'[CalibrateTool] EMG2数据长度: {len(self.emg2_data)}')
        result = max(lengths) if lengths else 0
        print(f'[CalibrateTool] 最大数据长度: {result}')
        return result

    def on_slider_pressed(self):
        """滑块按下开始拖动"""
        self.is_dragging = True

    def on_slider_released(self):
        """滑块释放结束拖动，执行精确更新"""
        self.is_dragging = False
        # 立即执行完整更新
        self.update_timer.stop()
        self._do_update_plots()

    def on_slider_changed(self, value):
        """滑块值改变 - 使用节流机制提升流畅度"""
        self.current_pos = value
        self.lbl_pos.setText(f'位置: {value}')

        # 使用节流：拖动时每50ms最多更新一次，不拖动时立即更新
        if self.is_dragging:
            # 拖动过程中使用节流
            if not self.update_timer.isActive():
                self.update_timer.start(50)  # 50ms节流间隔
        else:
            # 非拖动（如点击滑槽）立即更新
            self._do_update_plots()

    def _do_update_plots(self):
        """执行实际的图表更新"""
        self.update_plots()

    def on_window_changed(self, value):
        """窗口大小改变"""
        self.window_size = value
        max_len = self.get_max_data_length()
        self.slider.setMaximum(max(0, max_len - self.window_size))
        self.update_plots()

    def update_plots(self):
        """更新所有图表"""
        # 拖动时使用快速模式（降采样绘制）
        fast_mode = self.is_dragging
        self.update_emg_plot(fast_mode)
        self.update_imu_plot(fast_mode)

    def update_emg_plot(self, fast_mode=False):
        """更新EMG图表

        Args:
            fast_mode: 快速模式，降采样绘制以提升流畅度
        """
        self.ax_emg1.clear()
        self.ax_emg2.clear()

        start = self.current_pos
        end = start + self.window_size

        # LSB转换系数
        lsb_uv = calculate_lsb_uv()
        use_filter = self.chk_filter.isChecked()

        # 颜色映射
        colors = plt.cm.tab20(np.linspace(0, 1, 16))

        # 计算时间轴（秒），相对于数据开始的时间
        sample_rate = getattr(self, 'emg1_sample_rate', 2000)
        time_start = start / sample_rate  # 窗口开始时间（秒）
        time_end = end / sample_rate      # 窗口结束时间（秒）

        # 滤波时使用的padding大小（避免边缘效应）
        filter_padding = 500  # 前后各取500个采样点

        # 快速模式：降采样以提升绘制速度
        downsample = 4 if fast_mode else 1

        # 绘制EMG1
        if self.emg1_data is not None and len(self.emg1_data) > 0:
            # 取更大范围的数据用于滤波（加padding）
            pad_start = max(0, start - filter_padding)
            pad_end = min(len(self.emg1_data), end + filter_padding)
            data_padded = self.emg1_data[pad_start:pad_end]

            if len(data_padded) > 0:
                # 转换为μV
                data_uv_padded = data_padded * lsb_uv

                # 应用滤波
                if use_filter and len(data_uv_padded) > 50:
                    try:
                        data_uv_padded = self.emg_filter_2k.filter(data_uv_padded)
                    except Exception as e:
                        print(f'[CalibrateTool] EMG1滤波失败: {e}')

                # 从滤波后的数据中截取实际显示窗口
                actual_start = start - pad_start
                actual_end = actual_start + (end - start)
                data_uv = data_uv_padded[actual_start:actual_end]

                # 快速模式：降采样
                if downsample > 1:
                    data_uv = data_uv[::downsample]

                # 时间轴（秒）
                x = np.linspace(time_start, time_start + len(data_uv) * downsample / sample_rate, len(data_uv))
                for ch in range(min(16, data_uv.shape[1] if data_uv.ndim > 1 else 1)):
                    if data_uv.ndim > 1:
                        self.ax_emg1.plot(x, data_uv[:, ch], color=colors[ch],
                                         linewidth=0.5, alpha=0.8, label=f'Ch{ch}')
                    else:
                        self.ax_emg1.plot(x, data_uv, color=colors[0],
                                         linewidth=0.5, alpha=0.8)

        self.ax_emg1.set_title(f'EMG1 (16通道) - {"滤波后" if use_filter else "原始"}', fontsize=10)
        self.ax_emg1.set_ylabel('μV')
        self.ax_emg1.grid(True, alpha=0.3)
        # 固定Y轴范围
        if self.emg1_ylim:
            self.ax_emg1.set_ylim(self.emg1_ylim)

        # 绘制EMG2
        if self.emg2_data is not None and len(self.emg2_data) > 0:
            # 取更大范围的数据用于滤波（加padding）
            pad_start = max(0, start - filter_padding)
            pad_end = min(len(self.emg2_data), end + filter_padding)
            data_padded = self.emg2_data[pad_start:pad_end]

            if len(data_padded) > 0:
                data_uv_padded = data_padded * lsb_uv

                if use_filter and len(data_uv_padded) > 50:
                    try:
                        data_uv_padded = self.emg_filter_2k.filter(data_uv_padded)
                    except Exception as e:
                        print(f'[CalibrateTool] EMG2滤波失败: {e}')

                # 从滤波后的数据中截取实际显示窗口
                actual_start = start - pad_start
                actual_end = actual_start + (end - start)
                data_uv = data_uv_padded[actual_start:actual_end]

                # 快速模式：降采样
                if downsample > 1:
                    data_uv = data_uv[::downsample]

                # 时间轴（秒）
                x = np.linspace(time_start, time_start + len(data_uv) * downsample / sample_rate, len(data_uv))
                for ch in range(min(16, data_uv.shape[1] if data_uv.ndim > 1 else 1)):
                    if data_uv.ndim > 1:
                        self.ax_emg2.plot(x, data_uv[:, ch], color=colors[ch],
                                         linewidth=0.5, alpha=0.8)

        self.ax_emg2.set_title(f'EMG2 (16通道) - {"滤波后" if use_filter else "原始"}', fontsize=10)
        self.ax_emg2.set_ylabel('μV')
        self.ax_emg2.set_xlabel('时间 (秒)')
        self.ax_emg2.grid(True, alpha=0.3)
        # 固定Y轴范围
        if self.emg2_ylim:
            self.ax_emg2.set_ylim(self.emg2_ylim)

        self.fig_emg.tight_layout()
        self.canvas_emg.draw_idle()  # 使用 draw_idle 提升响应性

    def update_imu_plot(self, fast_mode=False):
        """更新IMU图表

        Args:
            fast_mode: 快速模式，降采样绘制以提升流畅度
        """
        for ax in [self.ax_imu1a, self.ax_imu1b, self.ax_imu2a, self.ax_imu2b]:
            ax.clear()

        start = self.current_pos
        # EMG采样率
        emg_sample_rate = getattr(self, 'emg1_sample_rate', 2000)
        # IMU采样率 100Hz
        imu_sample_rate = 100

        # 计算时间轴（秒）
        time_start = start / emg_sample_rate

        # IMU采样率较低，按比例调整索引
        imu_ratio = imu_sample_rate / emg_sample_rate
        imu_start = int(start * imu_ratio)
        imu_end = int((start + self.window_size) * imu_ratio)

        colors = ['r', 'g', 'b']
        labels = ['X', 'Y', 'Z']

        imu_data_list = [
            (self.imu1a_data, self.ax_imu1a, 'IMU1A'),
            (self.imu1b_data, self.ax_imu1b, 'IMU1B'),
            (self.imu2a_data, self.ax_imu2a, 'IMU2A'),
            (self.imu2b_data, self.ax_imu2b, 'IMU2B'),
        ]

        for imu_data, ax, title in imu_data_list:
            if imu_data is not None and len(imu_data) > 0:
                data = imu_data[imu_start:imu_end]
                if len(data) > 0:
                    # 时间轴（秒）
                    x = np.linspace(time_start, time_start + len(data) / imu_sample_rate, len(data))
                    for i in range(min(3, data.shape[1] if data.ndim > 1 else 1)):
                        if data.ndim > 1:
                            ax.plot(x, data[:, i], color=colors[i],
                                   linewidth=0.8, label=labels[i])
                        else:
                            ax.plot(x, data, color=colors[0], linewidth=0.8)
                    ax.legend(loc='upper right', fontsize=8)

            ax.set_title(f'{title} (加速度)', fontsize=10)
            ax.set_ylabel('g')
            ax.grid(True, alpha=0.3)
            # 固定Y轴范围
            if self.imu_ylim:
                ax.set_ylim(self.imu_ylim)

        self.ax_imu2a.set_xlabel('时间 (秒)')
        self.ax_imu2b.set_xlabel('时间 (秒)')

        self.fig_imu.tight_layout()
        self.canvas_imu.draw_idle()  # 使用 draw_idle 提升响应性

    def closeEvent(self, event):
        """关闭窗口时清理资源"""
        if self.h5_file:
            self.h5_file.close()
        event.accept()


def main():
    """主函数"""
    app = QApplication(sys.argv)

    # 设置应用样式
    app.setStyle('Fusion')

    window = CalibrateTool()
    window.show()

    # 如果命令行传入了文件路径，直接打开
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        if os.path.exists(file_path):
            window.load_h5_file(file_path)

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
