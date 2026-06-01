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
# 【修正】与供应商代码保持一致
BASE_LSB_24BIT = 0.2861            # 2.4V ref / 2^23 * 1e6 (μV)
HARDWARE_FRONTEND_GAIN = 10        # 硬件前端增益
DEFAULT_GAIN = 12                  # 默认增益
SAMPLE_RATE = 2000                 # EMG采样率 2kHz (同步后的SD卡数据)
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

        # Prompt标签数据
        self.prompt_names = None
        self.prompt_times = None  # 相对时间（秒）
        self.current_prompt_idx = 0  # 当前prompt索引
        self.emg_start_time = None  # EMG数据起始时间戳

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
        self.lbl_status = QLabel('')
        self.lbl_status.setStyleSheet('color: #666; font-size: 9px;')
        control_layout.addWidget(self.lbl_status)

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

        # Prompt跳转按钮
        control_layout.addWidget(QLabel('  |  '))
        self.btn_prev_prompt = QPushButton('◀ 上一个Prompt')
        self.btn_prev_prompt.clicked.connect(self.goto_prev_prompt)
        self.btn_prev_prompt.setEnabled(False)
        control_layout.addWidget(self.btn_prev_prompt)

        self.lbl_prompt_info = QLabel('Prompt: -/-')
        self.lbl_prompt_info.setMinimumWidth(120)
        control_layout.addWidget(self.lbl_prompt_info)

        self.btn_next_prompt = QPushButton('下一个Prompt ▶')
        self.btn_next_prompt.clicked.connect(self.goto_next_prompt)
        self.btn_next_prompt.setEnabled(False)
        control_layout.addWidget(self.btn_next_prompt)

        main_layout.addLayout(control_layout)

        # === 图表区域 ===
        splitter = QSplitter(Qt.Vertical)

        # EMG图表
        emg_widget = QWidget()
        emg_layout = QVBoxLayout(emg_widget)
        emg_layout.setContentsMargins(0, 0, 0, 0)

        self.fig_emg = Figure(figsize=(16, 10), dpi=100)
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
        # EMG图表：左右两列，每列16行（EMG1和EMG2）
        # 使用 GridSpec 实现更灵活的布局
        from matplotlib.gridspec import GridSpec

        gs = GridSpec(16, 2, figure=self.fig_emg, hspace=0, wspace=0.1)

        self.ax_emg1_channels = []
        self.ax_emg2_channels = []

        for i in range(16):
            # EMG1 左列
            ax1 = self.fig_emg.add_subplot(gs[i, 0])
            self.ax_emg1_channels.append(ax1)
            ax1.set_ylabel(f'{i}', fontsize=7, rotation=0, labelpad=10)
            ax1.tick_params(axis='y', labelsize=5, length=2)
            ax1.tick_params(axis='x', labelsize=5)
            # 去掉边框，只保留左边和底部
            ax1.spines['top'].set_visible(False)
            ax1.spines['right'].set_visible(False)
            if i < 15:
                ax1.set_xticklabels([])
                ax1.spines['bottom'].set_visible(False)
                ax1.tick_params(axis='x', length=0)

            # EMG2 右列
            ax2 = self.fig_emg.add_subplot(gs[i, 1])
            self.ax_emg2_channels.append(ax2)
            ax2.tick_params(axis='y', labelsize=5, length=2)
            ax2.tick_params(axis='x', labelsize=5)
            ax2.set_yticklabels([])  # 右列不显示y轴标签
            # 去掉边框
            ax2.spines['top'].set_visible(False)
            ax2.spines['right'].set_visible(False)
            ax2.spines['left'].set_visible(False)
            if i < 15:
                ax2.set_xticklabels([])
                ax2.spines['bottom'].set_visible(False)
                ax2.tick_params(axis='x', length=0)

        # 设置标题
        self.ax_emg1_channels[0].set_title('EMG1 (16通道)', fontsize=10, pad=5)
        self.ax_emg2_channels[0].set_title('EMG2 (16通道)', fontsize=10, pad=5)

        # 设置底部x轴标签
        self.ax_emg1_channels[-1].set_xlabel('时间 (秒)', fontsize=8)
        self.ax_emg2_channels[-1].set_xlabel('时间 (秒)', fontsize=8)

        # IMU图表：使用GridSpec，每个IMU 3行（X/Y/Z），共4列
        from matplotlib.gridspec import GridSpec as GridSpecIMU
        gs_imu = GridSpecIMU(3, 4, figure=self.fig_imu, hspace=0.05, wspace=0.15)

        # IMU1A (列0), IMU1B (列1), IMU2A (列2), IMU2B (列3)
        self.ax_imu_channels = {
            'imu1a': [], 'imu1b': [], 'imu2a': [], 'imu2b': []
        }
        imu_names = ['imu1a', 'imu1b', 'imu2a', 'imu2b']
        imu_titles = ['IMU1A', 'IMU1B', 'IMU2A', 'IMU2B']
        axis_labels = ['X', 'Y', 'Z']
        axis_colors = ['#d62728', '#2ca02c', '#1f77b4']  # 红、绿、蓝

        for col, (imu_name, imu_title) in enumerate(zip(imu_names, imu_titles)):
            for row in range(3):
                ax = self.fig_imu.add_subplot(gs_imu[row, col])
                self.ax_imu_channels[imu_name].append(ax)

                # 设置样式
                ax.tick_params(axis='y', labelsize=6, length=2)
                ax.tick_params(axis='x', labelsize=6)
                ax.spines['top'].set_visible(False)
                ax.spines['right'].set_visible(False)

                if row == 0:
                    ax.set_title(imu_title, fontsize=9, pad=3)
                if row < 2:
                    ax.set_xticklabels([])
                    ax.spines['bottom'].set_visible(False)
                    ax.tick_params(axis='x', length=0)
                else:
                    ax.set_xlabel('时间(秒)', fontsize=7)

                # 左侧显示轴标签
                if col == 0:
                    ax.set_ylabel(axis_labels[row], fontsize=8, rotation=0, labelpad=10, color=axis_colors[row])

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

            # 读取数据（必须先加载 EMG 以获取 dataset 名，供 _load_status_attrs 使用）
            self.load_emg_data()
            self.load_imu_data()

            # 读取 H5 attrs（依赖 emg*_loaded_name）
            self._load_status_attrs()
            self.load_prompt_data()

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

    def _load_status_attrs(self):
        """Phase 6: 读取并显示 H5 同步/采集状态 attrs"""
        f = self.h5_file
        parts = []

        def _s(k, d='-'):
            v = f.attrs.get(k)
            if isinstance(v, bytes): v = v.decode('utf-8')
            return str(v) if v is not None else d

        def _safe_int(v, d=1):
            try: return int(v)
            except (ValueError, TypeError): return d

        def _safe_bool(v, d=False):
            if isinstance(v, bool): return v
            if isinstance(v, (int, float)): return bool(v)
            if isinstance(v, str): return v.lower() in ('true', '1', 'yes')
            if isinstance(v, bytes): return v.decode('utf-8').lower() in ('true', '1', 'yes')
            return d

        sync = _s('sync_status')
        cs = _s('collection_status')
        seg = _safe_int(f.attrs.get('segment_index'), 1)
        resumed = 'R' if _safe_bool(f.attrs.get('is_resumed')) else ''
        cm = _s('channel_map_name')

        if sync not in ('-', 'unknown'):
            parts.append(f'sync: {sync}')
        if cs != '-':
            parts.append(f'collection: {cs}')
        if resumed or seg > 1:
            parts.append(f'seg: {seg}{resumed}')
        if cm != '-':
            parts.append(f'chan: {cm}')
        # show which data source is loaded
        ds_info = []
        if getattr(self, 'emg1_loaded_name', None):
            ds_info.append(self.emg1_loaded_name.replace('_adc',''))
        if getattr(self, 'emg2_loaded_name', None):
            ds_info.append(self.emg2_loaded_name.replace('_adc',''))
        if ds_info:
            parts.append('src: ' + ','.join(ds_info))

        for k in ('last_sync_success_time', 'last_sync_error_time'):
            v = f.attrs.get(k)
            if v:
                if isinstance(v, bytes): v = v.decode('utf-8')
                parts.append(v[:19])

        self.lbl_status.setText(' | '.join(parts) if parts else '')
        if sync == 'synced':
            self.lbl_status.setStyleSheet('color: #16a34a; font-size: 9px;')
        elif sync in ('sync_failed',):
            self.lbl_status.setStyleSheet('color: #dc2626; font-size: 9px;')
        elif sync == 'pending':
            self.lbl_status.setStyleSheet('color: #f59e0b; font-size: 9px;')
        else:
            self.lbl_status.setStyleSheet('color: #666; font-size: 9px;')

        # 2kHz warning
        has_2khz_loaded = (getattr(self, 'emg1_loaded_name', '') and '2khz' in self.emg1_loaded_name) or \
                          (getattr(self, 'emg2_loaded_name', '') and '2khz' in self.emg2_loaded_name)
        if has_2khz_loaded and sync != 'synced':
            QMessageBox.warning(self, '2kHz 数据警告',
                '当前查看的是 2kHz 同步数据，但 sync_status 不是 synced，结果可能不可信。\n'
                '建议优先查看 250Hz 原始数据，或使用 hdf5_tool 同步工具重新同步。')

        # diagnose old bug risk
        try:
            from bin_sync_tool import diagnose_frame_ids
            diag = diagnose_frame_ids(self.h5_path)
            for dev in ('emg1', 'emg2'):
                d = diag.get(dev, {})
                if d.get('risk') == 'high':
                    QMessageBox.warning(self, '旧同步风险',
                        f'{dev}: {d.get("risk_reason", "")}\n\n'
                        '此 H5 疑似使用旧版包号映射采集，2kHz 同步数据不可信。\n'
                        '建议使用 hdf5_tool 同步工具重新同步。\n\n250Hz 原始数据仍可正常查看。')
                    break
                elif d.get('risk') == 'medium':
                    self.lbl_status.setText(
                        (self.lbl_status.text() or '') + f' | WARN: {dev} frame_id issue')
        except Exception:
            pass

    def load_emg_data(self):
        """加载EMG数据"""
        self.emg1_data = None
        self.emg2_data = None
        self.emg1_sample_rate = 2000  # 默认采样率
        self.emg2_sample_rate = 2000
        self.emg_start_time = None

        # 默认优先 250Hz 原始数据，2kHz 为 fallback
        emg1_names = ['emg1_250hz_adc', 'emg1_250hz', 'emg1_2khz_adc', 'emg1_2khz', 'emg1']
        emg2_names = ['emg2_250hz_adc', 'emg2_250hz', 'emg2_2khz_adc', 'emg2_2khz', 'emg2']
        self.emg1_loaded_name = None
        self.emg2_loaded_name = None

        for name in emg1_names:
            if name in self.h5_file:
                raw_data = self.h5_file[name][:]
                self.emg1_data = self._extract_emg_channels(raw_data)
                self.emg1_loaded_name = name
                if raw_data.dtype.names is not None and 'time' in raw_data.dtype.names:
                    self.emg_start_time = raw_data['time'][0]
                self.emg1_sample_rate = 250 if '250hz' in name else 2000
                print(f'[CalibrateTool] 已加载 {name}: shape={self.emg1_data.shape}, rate={self.emg1_sample_rate}')
                break

        for name in emg2_names:
            if name in self.h5_file:
                self.emg2_data = self._extract_emg_channels(self.h5_file[name][:])
                self.emg2_loaded_name = name
                self.emg2_sample_rate = 250 if '250hz' in name else 2000
                print(f'[CalibrateTool] 已加载 {name}: shape={self.emg2_data.shape}, rate={self.emg2_sample_rate}')
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
            # legacy single-IMU names
            'imu1_legacy': ['imu1_100hz', 'imu1_ble', 'imu1'],
            'imu2_legacy': ['imu2_100hz', 'imu2_ble', 'imu2'],
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

    def load_prompt_data(self):
        """加载Prompt标签数据"""
        self.prompt_names = None
        self.prompt_times = None

        try:
            if 'prompts' in self.h5_file:
                prompts_group = self.h5_file['prompts']
                if 'names' in prompts_group and 'times' in prompts_group:
                    # 读取names（可能是字符串数组）
                    names_data = prompts_group['names'][:]
                    # 处理字节字符串
                    if names_data.dtype.kind == 'S' or names_data.dtype.kind == 'O':
                        self.prompt_names = [n.decode('utf-8') if isinstance(n, bytes) else str(n) for n in names_data]
                    else:
                        self.prompt_names = [str(n) for n in names_data]

                    # 读取times（绝对时间戳数组）
                    raw_times = prompts_group['times'][:]

                    # 转换为相对时间（相对于EMG起始时间）
                    if self.emg_start_time is not None:
                        self.prompt_times = raw_times - self.emg_start_time
                        print(f'[CalibrateTool] Prompt时间已转换为相对时间 (起始时间: {self.emg_start_time})')
                    else:
                        self.prompt_times = raw_times
                        print(f'[CalibrateTool] 警告: 未找到EMG起始时间，使用原始时间戳')

                    print(f'[CalibrateTool] 已加载 {len(self.prompt_names)} 个Prompt标签')
                    for i, (name, time) in enumerate(zip(self.prompt_names[:5], self.prompt_times[:5])):
                        print(f'  [{i}] {name}: {time:.3f}s')
                    if len(self.prompt_names) > 5:
                        print(f'  ... 共 {len(self.prompt_names)} 个')

                    # 重置prompt索引并更新UI
                    self.current_prompt_idx = 0
                    self._update_prompt_info()
        except Exception as e:
            print(f'[CalibrateTool] 加载Prompt数据失败: {e}')

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
        # 清除所有通道的图表
        for ax in self.ax_emg1_channels + self.ax_emg2_channels:
            ax.clear()

        start = self.current_pos
        end = start + self.window_size

        # LSB转换系数
        lsb_uv = calculate_lsb_uv()
        use_filter = self.chk_filter.isChecked()

        # 计算时间轴（秒），相对于数据开始的时间
        sample_rate = getattr(self, 'emg1_sample_rate', 2000)
        time_start = start / sample_rate  # 窗口开始时间（秒）
        time_end = end / sample_rate      # 窗口结束时间（秒）

        # 滤波时使用的padding大小（避免边缘效应）
        filter_padding = 500  # 前后各取500个采样点

        # 快速模式：降采样以提升绘制速度
        downsample = 4 if fast_mode else 1

        # 绘制EMG1的16个通道（左列）
        # 使用不同颜色区分通道
        colors = plt.cm.tab20(np.linspace(0, 1, 16))

        if self.emg1_data is not None and len(self.emg1_data) > 0:
            pad_start = max(0, start - filter_padding)
            pad_end = min(len(self.emg1_data), end + filter_padding)
            data_padded = self.emg1_data[pad_start:pad_end]

            if len(data_padded) > 0:
                data_uv_padded = data_padded * lsb_uv

                if use_filter and len(data_uv_padded) > 50:
                    try:
                        data_uv_padded = self.emg_filter_2k.filter(data_uv_padded)
                    except Exception as e:
                        print(f'[CalibrateTool] EMG1滤波失败: {e}')

                actual_start = start - pad_start
                actual_end = actual_start + (end - start)
                data_uv = data_uv_padded[actual_start:actual_end]

                if downsample > 1:
                    data_uv = data_uv[::downsample]

                x = np.linspace(time_start, time_start + len(data_uv) * downsample / sample_rate, len(data_uv))

                num_channels = min(16, data_uv.shape[1] if data_uv.ndim > 1 else 1)
                for ch in range(num_channels):
                    ax = self.ax_emg1_channels[ch]
                    if data_uv.ndim > 1:
                        ax.plot(x, data_uv[:, ch], color=colors[ch], linewidth=0.5)
                    else:
                        ax.plot(x, data_uv, color=colors[0], linewidth=0.5)

        # 绘制EMG2的16个通道（右列）
        if self.emg2_data is not None and len(self.emg2_data) > 0:
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

                actual_start = start - pad_start
                actual_end = actual_start + (end - start)
                data_uv = data_uv_padded[actual_start:actual_end]

                if downsample > 1:
                    data_uv = data_uv[::downsample]

                x = np.linspace(time_start, time_start + len(data_uv) * downsample / sample_rate, len(data_uv))

                num_channels = min(16, data_uv.shape[1] if data_uv.ndim > 1 else 1)
                for ch in range(num_channels):
                    ax = self.ax_emg2_channels[ch]
                    if data_uv.ndim > 1:
                        ax.plot(x, data_uv[:, ch], color=colors[ch], linewidth=0.5)
                    else:
                        ax.plot(x, data_uv, color=colors[0], linewidth=0.5)

        # 设置每个通道的属性
        for i in range(16):
            ax1 = self.ax_emg1_channels[i]
            ax2 = self.ax_emg2_channels[i]

            # EMG1 左列
            ax1.set_ylabel(f'{i}', fontsize=7, rotation=0, labelpad=10)
            ax1.tick_params(axis='y', labelsize=5, length=2)
            ax1.tick_params(axis='x', labelsize=5)
            ax1.set_xlim(time_start, time_end)
            ax1.spines['top'].set_visible(False)
            ax1.spines['right'].set_visible(False)
            if i < 15:
                ax1.set_xticklabels([])
                ax1.spines['bottom'].set_visible(False)
                ax1.tick_params(axis='x', length=0)

            # EMG2 右列
            ax2.tick_params(axis='y', labelsize=5, length=2)
            ax2.tick_params(axis='x', labelsize=5)
            ax2.set_yticklabels([])
            ax2.set_xlim(time_start, time_end)
            ax2.spines['top'].set_visible(False)
            ax2.spines['right'].set_visible(False)
            ax2.spines['left'].set_visible(False)
            if i < 15:
                ax2.set_xticklabels([])
                ax2.spines['bottom'].set_visible(False)
                ax2.tick_params(axis='x', length=0)

            # 绘制Prompt标签（只在第一个通道显示文字）
            self.draw_prompt_markers(ax1, time_start, time_end, show_text=(i == 0))
            self.draw_prompt_markers(ax2, time_start, time_end, show_text=(i == 0))

        # 设置标题和标签
        title_suffix = "滤波后" if use_filter else "原始"
        self.ax_emg1_channels[0].set_title(f'EMG1 (16通道) - {title_suffix}', fontsize=10, pad=5)
        self.ax_emg2_channels[0].set_title(f'EMG2 (16通道) - {title_suffix}', fontsize=10, pad=5)
        self.ax_emg1_channels[-1].set_xlabel('时间 (秒)', fontsize=8)
        self.ax_emg2_channels[-1].set_xlabel('时间 (秒)', fontsize=8)

        self.canvas_emg.draw_idle()

    def update_imu_plot(self, fast_mode=False):
        """更新IMU图表

        Args:
            fast_mode: 快速模式，降采样绘制以提升流畅度
        """
        # 清除所有IMU通道
        for imu_name in self.ax_imu_channels:
            for ax in self.ax_imu_channels[imu_name]:
                ax.clear()

        start = self.current_pos
        # EMG采样率
        emg_sample_rate = getattr(self, 'emg1_sample_rate', 2000)
        # IMU采样率 100Hz
        imu_sample_rate = 100

        # 计算时间轴（秒）
        time_start = start / emg_sample_rate
        time_end = time_start + self.window_size / emg_sample_rate

        # IMU采样率较低，按比例调整索引
        imu_ratio = imu_sample_rate / emg_sample_rate
        imu_start = int(start * imu_ratio)
        imu_end = int((start + self.window_size) * imu_ratio)

        axis_colors = ['#d62728', '#2ca02c', '#1f77b4']  # X红、Y绿、Z蓝
        axis_labels = ['X', 'Y', 'Z']

        imu_data_map = {
            'imu1a': self.imu1a_data,
            'imu1b': self.imu1b_data,
            'imu2a': self.imu2a_data,
            'imu2b': self.imu2b_data,
        }
        imu_titles = {'imu1a': 'IMU1A', 'imu1b': 'IMU1B', 'imu2a': 'IMU2A', 'imu2b': 'IMU2B'}

        for col_idx, imu_name in enumerate(['imu1a', 'imu1b', 'imu2a', 'imu2b']):
            imu_data = imu_data_map[imu_name]
            axes = self.ax_imu_channels[imu_name]

            if imu_data is not None and len(imu_data) > 0:
                data = imu_data[imu_start:imu_end]
                if len(data) > 0:
                    x = np.linspace(time_start, time_start + len(data) / imu_sample_rate, len(data))
                    num_axes = min(3, data.shape[1] if data.ndim > 1 else 1)
                    for i in range(num_axes):
                        ax = axes[i]
                        if data.ndim > 1:
                            ax.plot(x, data[:, i], color=axis_colors[i], linewidth=0.8)
                        else:
                            ax.plot(x, data, color=axis_colors[0], linewidth=0.8)

            # 设置每个轴的属性
            for row, ax in enumerate(axes):
                ax.set_xlim(time_start, time_end)
                ax.tick_params(axis='y', labelsize=6, length=2)
                ax.tick_params(axis='x', labelsize=6)
                ax.spines['top'].set_visible(False)
                ax.spines['right'].set_visible(False)

                if row == 0:
                    ax.set_title(imu_titles[imu_name], fontsize=9, pad=3)
                if row < 2:
                    ax.set_xticklabels([])
                    ax.spines['bottom'].set_visible(False)
                    ax.tick_params(axis='x', length=0)
                else:
                    ax.set_xlabel('时间(秒)', fontsize=7)

                # 左侧显示轴标签
                if col_idx == 0:
                    ax.set_ylabel(axis_labels[row], fontsize=8, rotation=0, labelpad=10, color=axis_colors[row])

                # 绘制Prompt标签（只在第一行显示文字）
                self.draw_prompt_markers(ax, time_start, time_end, show_text=(row == 0))

        self.canvas_imu.draw_idle()

    def draw_prompt_markers(self, ax, time_start, time_end, show_text=True):
        """在图表上绘制Prompt标签

        Args:
            ax: matplotlib axes对象
            time_start: 显示窗口开始时间（秒）
            time_end: 显示窗口结束时间（秒）
            show_text: 是否显示文字标签
        """
        if self.prompt_names is None or self.prompt_times is None:
            return

        # 获取当前Y轴范围
        ylim = ax.get_ylim()

        # 遍历所有prompt，绘制在当前时间窗口内的
        for name, time in zip(self.prompt_names, self.prompt_times):
            if time_start <= time <= time_end:
                # 绘制垂直线
                ax.axvline(x=time, color='red', linestyle='--', linewidth=1, alpha=0.7)
                # 在顶部添加标签文字
                if show_text:
                    # 长文本换行处理（每8个字符换行）
                    max_chars = 8
                    if len(name) > max_chars:
                        wrapped_name = '\n'.join([name[i:i+max_chars] for i in range(0, len(name), max_chars)])
                    else:
                        wrapped_name = name
                    ax.text(time, ylim[1], wrapped_name, rotation=0, verticalalignment='bottom',
                           horizontalalignment='left', fontsize=11, color='red', alpha=0.9,
                           fontweight='bold')

    def goto_prev_prompt(self):
        """跳转到上一个Prompt"""
        if self.prompt_times is None or len(self.prompt_times) == 0:
            return

        if self.current_prompt_idx > 0:
            self.current_prompt_idx -= 1
            self._jump_to_prompt(self.current_prompt_idx)

    def goto_next_prompt(self):
        """跳转到下一个Prompt"""
        if self.prompt_times is None or len(self.prompt_times) == 0:
            return

        if self.current_prompt_idx < len(self.prompt_times) - 1:
            self.current_prompt_idx += 1
            self._jump_to_prompt(self.current_prompt_idx)

    def _jump_to_prompt(self, idx):
        """跳转到指定索引的Prompt位置"""
        if self.prompt_times is None or idx < 0 or idx >= len(self.prompt_times):
            return

        # 获取prompt时间（秒）
        prompt_time = self.prompt_times[idx]

        # 转换为采样点位置（EMG 2kHz）
        sample_rate = getattr(self, 'emg1_sample_rate', 2000)
        sample_pos = int(prompt_time * sample_rate)

        # 将prompt放在窗口中间偏左的位置
        target_pos = sample_pos - self.window_size // 4

        # 限制范围
        max_pos = self.slider.maximum()
        target_pos = max(0, min(target_pos, max_pos))

        # 更新滑块位置（会触发update_plots）
        self.slider.setValue(target_pos)

        # 更新prompt信息显示
        self._update_prompt_info()

    def _update_prompt_info(self):
        """更新Prompt信息显示"""
        if self.prompt_times is None or len(self.prompt_times) == 0:
            self.lbl_prompt_info.setText('Prompt: -/-')
            self.btn_prev_prompt.setEnabled(False)
            self.btn_next_prompt.setEnabled(False)
            return

        total = len(self.prompt_times)
        current = self.current_prompt_idx + 1
        name = self.prompt_names[self.current_prompt_idx] if self.prompt_names else ''

        # 截断过长的名称
        if len(name) > 15:
            name = name[:12] + '...'

        self.lbl_prompt_info.setText(f'Prompt: {current}/{total} ({name})')
        self.btn_prev_prompt.setEnabled(self.current_prompt_idx > 0)
        self.btn_next_prompt.setEnabled(self.current_prompt_idx < total - 1)

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
