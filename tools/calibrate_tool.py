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
    QSplitter, QComboBox, QCheckBox, QMessageBox, QScrollArea
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
from matplotlib.lines import Line2D


# ============== EMG滤波参数（对齐供应商 wband_emg_V3）==============
# LSB: 优先使用 H5 dataset attr lsb_uv；无 attr 时用供应商基准值
BASE_LSB_24BIT_VENDOR = 0.476837    # 供应商固件: 4.0V ref / 2^23 * 1e6 (μV)
BASE_LSB_24BIT_LEGACY = 0.2861      # 旧版 ble_server: 2.4V ref（仅 fallback）
HARDWARE_FRONTEND_GAIN = 10         # 硬件前端增益（供应商固件固定）
DEFAULT_GAIN = 12                   # 默认增益
SAMPLE_RATE = 2000                  # EMG采样率 2kHz (同步后的SD卡数据)
SAMPLE_RATE_BLE = 250               # BLE传输采样率 250Hz

# 窗口默认：对齐供应商 5 秒视图
WINDOW_2KHZ = 10000                 # 2kHz 下 5 秒
WINDOW_250HZ = 1250                 # 250Hz 下 5 秒

# 滤波器参数（对齐供应商 signalfilter.py 离线参数）
FILTER_LOWCUT = 20                  # 带通下限 (Hz)
FILTER_HIGHCUT = 100                # 带通上限 (Hz)
FILTER_NOTCH_FREQ = 50              # 工频频率 (Hz)
FILTER_NOTCH_Q_OFFLINE = 50         # 离线陷波 Q 值（供应商 q_offline=50）
FILTER_NOTCH_Q_ONLINE = 15          # 在线陷波 Q 值（供应商 q_online=15）

# IMU 缩放系数
SCALE_ACCEL = 16.0 / 32768.0       # 加速度 ±2g
SCALE_GYRO = 2000.0 / 32768.0      # 角速度 ±2000°/s
SCALE_MAG = 0.15                   # 磁力计 μT


def calculate_lsb_uv(gain=DEFAULT_GAIN, is_16bit=False, shift=4, base_lsb=None):
    """计算ADC原始值到μV的转换系数。base_lsb 为 None 时使用供应商默认值。"""
    if base_lsb is None:
        base_lsb = BASE_LSB_24BIT_VENDOR
    lsb_uv = base_lsb / (gain * HARDWARE_FRONTEND_GAIN)
    if is_16bit:
        lsb_uv = lsb_uv * (2 ** shift)
    return lsb_uv


class EMGFilter:
    """EMG信号滤波器（对齐供应商 signalfilter.py 离线参数：Q=50, filtfilt）"""

    def __init__(self, sample_rate=SAMPLE_RATE, num_channels=16, notch_q=FILTER_NOTCH_Q_OFFLINE):
        self.sample_rate = sample_rate
        self.num_channels = num_channels
        self.notch_q = notch_q

        # 设计带通滤波器 (20-100Hz, 4th-order Butterworth)
        nyq = sample_rate / 2
        low = FILTER_LOWCUT / nyq
        high = FILTER_HIGHCUT / nyq
        low = max(0.001, min(low, 0.99))
        high = max(low + 0.001, min(high, 0.99))
        self.b_bandpass, self.a_bandpass = scipy_signal.butter(4, [low, high], btype='band')

        # 设计工频陷波滤波器 (50Hz及其谐波，Q 对齐供应商离线值)
        self.notch_filters = []
        for harmonic in range(1, 4):  # 50Hz, 100Hz, 150Hz
            freq = FILTER_NOTCH_FREQ * harmonic
            if freq < nyq:
                b, a = scipy_signal.iirnotch(freq, self.notch_q, sample_rate)
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
        self.resize(1200, 800)
        self.setMinimumSize(900, 600)

        # 数据存储
        self.h5_file = None
        self.h5_path = None
        self.emg1_data = None
        self.emg2_data = None
        self.imu1a_data = None
        self.imu1b_data = None
        self.imu1c_data = None
        self.imu2a_data = None
        self.imu2b_data = None
        self.imu2c_data = None

        # IMU 角速度数据 (gyr) — 供应商风格显示
        self.imu1a_gyr_data = None
        self.imu1b_gyr_data = None
        self.imu1c_gyr_data = None
        self.imu2a_gyr_data = None
        self.imu2b_gyr_data = None
        self.imu2c_gyr_data = None

        # IMU 时间数组 (用于窗口对齐)
        self.imu1a_time = None
        self.imu1b_time = None
        self.imu1c_time = None
        self.imu2a_time = None
        self.imu2b_time = None
        self.imu2c_time = None

        # IMU 数量（2 或 3），从 H5 attrs 或 all_ble 推断
        self.imu1_imu_count = 2
        self.imu2_imu_count = 2

        # IMU 供应商风格堆叠参数
        self.imu_acc_offset = 4.0       # Acc offset (g)
        self.imu_gyr_offset = 600.0     # Gyr offset (deg/s)

        # Prompt标签数据
        self.prompt_names = None
        self.prompt_times = None  # 相对时间（秒）
        self.current_prompt_idx = 0  # 当前prompt索引
        self.emg_start_time = None  # EMG数据起始时间戳

        # 滤波器（对齐供应商离线 Q=50）
        self.emg_filter_2k = EMGFilter(sample_rate=2000, notch_q=FILTER_NOTCH_Q_OFFLINE)
        self.emg_filter_250 = EMGFilter(sample_rate=250, notch_q=FILTER_NOTCH_Q_OFFLINE)

        # LSB 系数（加载数据后从 H5 attrs 读取，默认使用供应商值）
        self.emg1_lsb_uv = calculate_lsb_uv()
        self.emg2_lsb_uv = calculate_lsb_uv()

        # 显示参数
        self.window_size = WINDOW_2KHZ  # 默认 5s@2kHz，加载后根据数据源调整
        self.current_pos = 0            # 当前位置

        # 供应商风格视图参数
        self.view_mode = 'stacked'       # 'stacked' (供应商) / 'subplot' (独立子图)
        self.offset_uv = 300             # 通道堆叠 offset (μV)
        self.clamp_enabled = False       # 是否裁剪波形防止重叠

        # 滑块平滑更新相关
        self.update_timer = QTimer()
        self.update_timer.setSingleShot(True)
        self.update_timer.timeout.connect(self._do_update_plots)
        self.pending_update = False
        self.is_dragging = False  # 是否正在拖动滑块

        # 降采样参数：拖动时限制绘图点数以提升流畅度
        self.max_plot_points_fast = 800
        self.max_plot_points_normal = 2500

        self.init_ui()

    def init_ui(self):
        """初始化UI"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # === 顶部控制栏 ===
        control_widget = QWidget()
        control_layout = QHBoxLayout(control_widget)
        control_layout.setContentsMargins(0, 0, 0, 0)

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

        # 显示模式
        control_layout.addWidget(QLabel('显示:'))
        self.combo_view_mode = QComboBox()
        self.combo_view_mode.addItems(['供应商堆叠', '分通道子图'])
        self.combo_view_mode.blockSignals(True)
        self.combo_view_mode.setCurrentIndex(0 if self.view_mode == 'stacked' else 1)
        self.combo_view_mode.blockSignals(False)
        self.combo_view_mode.currentIndexChanged.connect(self.on_view_mode_changed)
        control_layout.addWidget(self.combo_view_mode)

        # Offset (堆叠视图)
        control_layout.addWidget(QLabel('Offset(uV):'))
        self.spin_offset = QSpinBox()
        self.spin_offset.setRange(50, 5000)
        self.spin_offset.setSingleStep(50)
        self.spin_offset.setValue(self.offset_uv)
        self.spin_offset.valueChanged.connect(self.on_offset_changed)
        control_layout.addWidget(self.spin_offset)

        # Clamp
        self.chk_clamp = QCheckBox('Clamp')
        self.chk_clamp.setChecked(self.clamp_enabled)
        self.chk_clamp.stateChanged.connect(self.on_clamp_changed)
        control_layout.addWidget(self.chk_clamp)

        # 窗口大小
        control_layout.addWidget(QLabel(' 窗口:'))
        self.spin_window = QSpinBox()
        self.spin_window.setRange(100, 50000)
        self.spin_window.setValue(self.window_size)
        self.spin_window.setSingleStep(1000)
        self.spin_window.valueChanged.connect(self.on_window_changed)
        control_layout.addWidget(self.spin_window)
        self.lbl_window_sec = QLabel('')
        control_layout.addWidget(self.lbl_window_sec)

        # 滤波开关
        self.chk_filter = QCheckBox('滤波')
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

        control_scroll = QScrollArea()
        control_scroll.setWidget(control_widget)
        control_scroll.setWidgetResizable(False)
        control_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        control_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_layout.addWidget(control_scroll)

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

        # IMU Acc 图表
        imu_acc_widget = QWidget()
        imu_acc_layout = QVBoxLayout(imu_acc_widget)
        imu_acc_layout.setContentsMargins(0, 0, 0, 0)
        # Acc offset control
        acc_ctrl = QHBoxLayout()
        acc_ctrl.addWidget(QLabel('Acc Offset(g):'))
        self.spin_imu_acc_offset = QSpinBox()
        self.spin_imu_acc_offset.setRange(1, 50)
        self.spin_imu_acc_offset.setValue(int(self.imu_acc_offset))
        self.spin_imu_acc_offset.setSingleStep(1)
        self.spin_imu_acc_offset.valueChanged.connect(self.on_imu_offset_changed)
        acc_ctrl.addWidget(self.spin_imu_acc_offset)
        acc_ctrl.addStretch()
        imu_acc_layout.addLayout(acc_ctrl)
        self.fig_imu_acc = Figure(figsize=(16, 2.5), dpi=100)
        self.canvas_imu_acc = FigureCanvas(self.fig_imu_acc)
        self.toolbar_imu_acc = NavigationToolbar(self.canvas_imu_acc, self)
        imu_acc_layout.addWidget(self.toolbar_imu_acc)
        imu_acc_layout.addWidget(self.canvas_imu_acc)
        splitter.addWidget(imu_acc_widget)

        # IMU Gyr 图表
        imu_gyr_widget = QWidget()
        imu_gyr_layout = QVBoxLayout(imu_gyr_widget)
        imu_gyr_layout.setContentsMargins(0, 0, 0, 0)
        # Gyr offset control
        gyr_ctrl = QHBoxLayout()
        gyr_ctrl.addWidget(QLabel('Gyr Offset(deg/s):'))
        self.spin_imu_gyr_offset = QSpinBox()
        self.spin_imu_gyr_offset.setRange(10, 5000)
        self.spin_imu_gyr_offset.setValue(int(self.imu_gyr_offset))
        self.spin_imu_gyr_offset.setSingleStep(50)
        self.spin_imu_gyr_offset.valueChanged.connect(self.on_imu_offset_changed)
        gyr_ctrl.addWidget(self.spin_imu_gyr_offset)
        gyr_ctrl.addStretch()
        imu_gyr_layout.addLayout(gyr_ctrl)
        self.fig_imu_gyr = Figure(figsize=(16, 2.5), dpi=100)
        self.canvas_imu_gyr = FigureCanvas(self.fig_imu_gyr)
        self.toolbar_imu_gyr = NavigationToolbar(self.canvas_imu_gyr, self)
        imu_gyr_layout.addWidget(self.toolbar_imu_gyr)
        imu_gyr_layout.addWidget(self.canvas_imu_gyr)
        splitter.addWidget(imu_gyr_widget)

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
        """初始化图表（默认供应商堆叠视图）"""
        self._init_emg_axes()
        self._init_imu_axes()

    def _init_emg_axes(self):
        """创建 EMG 图表 axes（根据 view_mode）"""
        self.fig_emg.clear()
        if self.view_mode == 'stacked':
            # 供应商堆叠：左右两列，每列 1 个 Axes
            self.ax_emg1_stacked = self.fig_emg.add_subplot(1, 2, 1)
            self.ax_emg2_stacked = self.fig_emg.add_subplot(1, 2, 2)
            self.ax_emg1_channels = []  # 子图模式下才用
            self.ax_emg2_channels = []
        else:
            # 分通道子图：左右两列，每列 16 个 Axes
            from matplotlib.gridspec import GridSpec
            gs = GridSpec(16, 2, figure=self.fig_emg, hspace=0, wspace=0.1)
            self.ax_emg1_channels = []
            self.ax_emg2_channels = []
            self.ax_emg1_stacked = None
            self.ax_emg2_stacked = None
            for i in range(16):
                ax1 = self.fig_emg.add_subplot(gs[i, 0])
                self.ax_emg1_channels.append(ax1)
                ax1.set_ylabel(f'{i}', fontsize=7, rotation=0, labelpad=10)
                ax1.tick_params(axis='y', labelsize=5, length=2)
                ax1.tick_params(axis='x', labelsize=5)
                ax1.spines['top'].set_visible(False)
                ax1.spines['right'].set_visible(False)
                if i < 15:
                    ax1.set_xticklabels([])
                    ax1.spines['bottom'].set_visible(False)
                    ax1.tick_params(axis='x', length=0)
                ax2 = self.fig_emg.add_subplot(gs[i, 1])
                self.ax_emg2_channels.append(ax2)
                ax2.tick_params(axis='y', labelsize=5, length=2)
                ax2.tick_params(axis='x', labelsize=5)
                ax2.set_yticklabels([])
                ax2.spines['top'].set_visible(False)
                ax2.spines['right'].set_visible(False)
                ax2.spines['left'].set_visible(False)
                if i < 15:
                    ax2.set_xticklabels([])
                    ax2.spines['bottom'].set_visible(False)
                    ax2.tick_params(axis='x', length=0)
            self.ax_emg1_channels[0].set_title('EMG1 (16通道)', fontsize=10, pad=5)
            self.ax_emg2_channels[0].set_title('EMG2 (16通道)', fontsize=10, pad=5)
            self.ax_emg1_channels[-1].set_xlabel('时间 (秒)', fontsize=8)
            self.ax_emg2_channels[-1].set_xlabel('时间 (秒)', fontsize=8)
        self.canvas_emg.draw_idle()

    def _init_imu_axes(self):
        """创建供应商风格 IMU 图表：Acc 堆叠图 + Gyr 堆叠图"""
        self.fig_imu_acc.clear()
        self.fig_imu_gyr.clear()
        self.ax_imu_acc_dev1 = self.fig_imu_acc.add_subplot(1, 2, 1)
        self.ax_imu_acc_dev2 = self.fig_imu_acc.add_subplot(1, 2, 2)
        self.ax_imu_gyr_dev1 = self.fig_imu_gyr.add_subplot(1, 2, 1)
        self.ax_imu_gyr_dev2 = self.fig_imu_gyr.add_subplot(1, 2, 2)
        # keep legacy ref for compatibility
        self.ax_imu_channels = {}

    def on_view_mode_changed(self, idx):
        """切换 EMG 显示模式"""
        self.view_mode = 'stacked' if idx == 0 else 'subplot'
        self._init_emg_axes()
        self._update_window_sec_label()
        self.update_plots()

    def on_offset_changed(self, value):
        """Offset spinbox 改变"""
        self.offset_uv = value
        if self.view_mode == 'stacked':
            self.update_plots()

    def on_clamp_changed(self, state):
        """Clamp checkbox 改变"""
        self.clamp_enabled = (state == Qt.Checked)
        if self.view_mode == 'stacked':
            self.update_plots()

    def on_imu_offset_changed(self):
        """IMU Acc/Gyr offset 改变"""
        self.imu_acc_offset = self.spin_imu_acc_offset.value()
        self.imu_gyr_offset = self.spin_imu_gyr_offset.value()
        self.update_plots()

    def _update_window_sec_label(self):
        """更新窗口标签显示秒数"""
        sr = getattr(self, 'emg1_sample_rate', 2000)
        sec = self.window_size / sr if sr > 0 else 0
        self.lbl_window_sec.setText(f'≈{sec:.1f}s')

    def _get_lsb_uv_for_dataset(self, ds_name):
        """从 H5 dataset attrs 读取 lsb_uv，无则 fallback 供应商值"""
        if self.h5_file and ds_name and ds_name in self.h5_file:
            ds_lsb = self.h5_file[ds_name].attrs.get('lsb_uv')
            if ds_lsb is not None:
                try:
                    return float(ds_lsb)
                except (ValueError, TypeError):
                    pass
        # fallback: 供应商基准值
        return calculate_lsb_uv()

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
        sfv = f.attrs.get('stream_format_version')
        sm = _s('sync_mode')
        sync_alignment = _s('sync_time_alignment')

        if sync not in ('-', 'unknown'):
            parts.append(f'sync: {sync}')
        if sm not in ('-',):
            parts.append(f'mode: {sm}')
        if sync_alignment not in ('-',):
            parts.append(f'align: {sync_alignment}')
        if cs != '-':
            parts.append(f'collection: {cs}')
        if resumed or seg > 1:
            parts.append(f'seg: {seg}{resumed}')
        if sfv is not None:
            parts.append(f'fmt: v{sfv}')
        else:
            parts.append('fmt: legacy')
        if cm != '-':
            parts.append(f'chan: {cm}')
        # IMU counts
        for dev in (1, 2):
            ni = f.attrs.get(f'imu{dev}_num_imus')
            if ni is not None:
                parts.append(f'imu{dev}: {int(ni)}IMU')
        # show which data source is loaded + LSB
        ds_info = []
        if getattr(self, 'emg1_loaded_name', None):
            src = self.emg1_loaded_name.replace('_adc','')
            lsb1 = getattr(self, 'emg1_lsb_uv', None)
            if lsb1:
                src += f'({lsb1:.4f})'
            ds_info.append(src)
        if getattr(self, 'emg2_loaded_name', None):
            src = self.emg2_loaded_name.replace('_adc','')
            lsb2 = getattr(self, 'emg2_lsb_uv', None)
            if lsb2:
                src += f'({lsb2:.4f})'
            ds_info.append(src)
        if ds_info:
            parts.append('src: ' + ','.join(ds_info))

        # LSB 信息
        lsb1 = getattr(self, 'emg1_lsb_uv', None)
        if lsb1:
            parts.append(f'lsb={lsb1:.4f}uV')
        # 滤波档位显示
        parts.append(f'Q={FILTER_NOTCH_Q_OFFLINE}')
        # 窗口显示信息
        sr = getattr(self, 'emg1_sample_rate', 2000)
        parts.append(f'{self.window_size}pt/{self.window_size/sr:.1f}s')

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

        # ── 2kHz / legacy frame_id 风险提示（不弹窗阻塞，仅状态栏 WARN）──
        has_2khz_loaded = (getattr(self, 'emg1_loaded_name', '') and '2khz' in self.emg1_loaded_name) or \
                          (getattr(self, 'emg2_loaded_name', '') and '2khz' in self.emg2_loaded_name)
        reliable_sync_modes = ('one_to_many_adc_search', 'one_to_one')

        # 2kHz 未可靠同步 → 状态栏警告（不弹窗）
        if has_2khz_loaded and sync != 'synced' and sm not in reliable_sync_modes:
            self.lbl_status.setText(
                (self.lbl_status.text() or '') + ' | WARN: 2kHz data, sync not verified')

        # diagnose legacy frame_id bug（新版同步不依赖 frame_id，不弹窗）
        try:
            from bin_sync_tool import diagnose_frame_ids
            diag = diagnose_frame_ids(self.h5_path)
            frame_risk = None
            for dev in ('emg1', 'emg2'):
                d = diag.get(dev, {})
                risk = d.get('risk')
                reason = d.get('risk_reason', '')
                if risk == 'high':
                    frame_risk = (dev, risk, reason)
                    break
                elif risk == 'medium' and frame_risk is None:
                    frame_risk = (dev, risk, reason)

            if frame_risk:
                dev, risk, reason = frame_risk
                is_reliably_synced = (sync == 'synced' and sm in reliable_sync_modes)

                if is_reliably_synced:
                    # 新版 sync（ADC search / one_to_one）不依赖 frame_id
                    print(f'[CalibrateTool] legacy frame_id {risk} ({dev}: {reason}), '
                          f'ignored: sync_mode={sm} via ADC search')
                    self.lbl_status.setText(
                        (self.lbl_status.text() or '') + ' | legacy frame_id ignored; synced by ADC search')
                elif has_2khz_loaded:
                    # 2kHz + 不可靠同步 → 明显警告
                    self.lbl_status.setText(
                        (self.lbl_status.text() or '') + f' | WARN: 2kHz may be unreliable (frame_id {risk})')
                else:
                    # 250Hz + frame_id 问题 → 轻提示（250Hz 原始值可正常查看）
                    self.lbl_status.setText(
                        (self.lbl_status.text() or '') + f' | WARN: frame_id {risk}, 250Hz ok')
        except Exception:
            pass

    def load_emg_data(self):
        """加载 EMG 数据：synced→优先2kHz，未同步→优先250Hz"""
        self.emg1_data = None
        self.emg2_data = None
        self.emg1_sample_rate = 2000
        self.emg2_sample_rate = 2000
        self.emg_start_time = None
        self.emg1_loaded_name = None
        self.emg2_loaded_name = None

        # 判断是否已可靠同步 → 优先 2kHz
        sync = self.h5_file.attrs.get('sync_status')
        if isinstance(sync, bytes):
            sync = sync.decode('utf-8')
        synced = (sync == 'synced')
        has_2khz_emg1 = 'emg1_2khz_adc' in self.h5_file or 'emg1_2khz' in self.h5_file
        has_2khz_emg2 = 'emg2_2khz_adc' in self.h5_file or 'emg2_2khz' in self.h5_file

        if synced and (has_2khz_emg1 or has_2khz_emg2):
            # 已同步 → 优先 2kHz
            emg1_names = ['emg1_2khz_adc', 'emg1_2khz', 'emg1_250hz_adc', 'emg1_250hz', 'emg1']
            emg2_names = ['emg2_2khz_adc', 'emg2_2khz', 'emg2_250hz_adc', 'emg2_250hz', 'emg2']
        else:
            # 未同步 → 优先 250Hz
            emg1_names = ['emg1_250hz_adc', 'emg1_250hz', 'emg1_2khz_adc', 'emg1_2khz', 'emg1']
            emg2_names = ['emg2_250hz_adc', 'emg2_250hz', 'emg2_2khz_adc', 'emg2_2khz', 'emg2']

        for name in emg1_names:
            if name in self.h5_file:
                raw_data = self.h5_file[name][:]
                if len(raw_data) == 0:
                    print(f'[CalibrateTool] 跳过空数据集 {name}')
                    continue
                self.emg1_data = self._extract_emg_channels(raw_data)
                self.emg1_loaded_name = name
                if raw_data.dtype.names is not None and 'time' in raw_data.dtype.names:
                    self.emg_start_time = raw_data['time'][0]
                self.emg1_sample_rate = 250 if '250hz' in name else 2000
                self.emg1_lsb_uv = self._get_lsb_uv_for_dataset(name)
                print(f'[CalibrateTool] 已加载 {name}: shape={self.emg1_data.shape}, '
                      f'rate={self.emg1_sample_rate}, lsb={self.emg1_lsb_uv:.6f}')
                break

        for name in emg2_names:
            if name in self.h5_file:
                raw_data = self.h5_file[name][:]
                if len(raw_data) == 0:
                    print(f'[CalibrateTool] 跳过空数据集 {name}')
                    continue
                self.emg2_data = self._extract_emg_channels(raw_data)
                self.emg2_loaded_name = name
                if self.emg_start_time is None and raw_data.dtype.names is not None and 'time' in raw_data.dtype.names:
                    self.emg_start_time = raw_data['time'][0]
                self.emg2_sample_rate = 250 if '250hz' in name else 2000
                self.emg2_lsb_uv = self._get_lsb_uv_for_dataset(name)
                print(f'[CalibrateTool] 已加载 {name}: shape={self.emg2_data.shape}, '
                      f'rate={self.emg2_sample_rate}, lsb={self.emg2_lsb_uv:.6f}')
                break

        # 设置默认窗口（5 秒）
        sr = self.emg1_sample_rate if self.emg1_sample_rate else self.emg2_sample_rate
        if sr == 250:
            self.window_size = WINDOW_250HZ
        else:
            self.window_size = WINDOW_2KHZ
        self.spin_window.setValue(self.window_size)
        self._update_window_sec_label()

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
        """加载IMU数据（支持 a/b/c、100hz/BLE/legacy、imu*_all_ble 拆分）"""
        self.imu1a_data = None
        self.imu1b_data = None
        self.imu1c_data = None
        self.imu2a_data = None
        self.imu2b_data = None
        self.imu2c_data = None
        self.imu1a_gyr_data = None
        self.imu1b_gyr_data = None
        self.imu1c_gyr_data = None
        self.imu2a_gyr_data = None
        self.imu2b_gyr_data = None
        self.imu2c_gyr_data = None
        self.imu1a_time = None
        self.imu1b_time = None
        self.imu1c_time = None
        self.imu2a_time = None
        self.imu2b_time = None
        self.imu2c_time = None

        # ── 1. 独立 dataset：100hz（同步后）> BLE 原始 > bare name ──
        imu_mapping = {
            'imu1a': ['imu1a_100hz', 'imu1a_ble', 'imu1a'],
            'imu1b': ['imu1b_100hz', 'imu1b_ble', 'imu1b'],
            'imu1c': ['imu1c_100hz', 'imu1c_ble', 'imu1c'],
            'imu2a': ['imu2a_100hz', 'imu2a_ble', 'imu2a'],
            'imu2b': ['imu2b_100hz', 'imu2b_ble', 'imu2b'],
            'imu2c': ['imu2c_100hz', 'imu2c_ble', 'imu2c'],
        }

        for attr_name, possible_names in imu_mapping.items():
            for name in possible_names:
                if name in self.h5_file:
                    raw = self.h5_file[name][:]
                    acc_data = self._extract_imu_acc(raw)
                    gyr_data = self._extract_imu_gyr(raw)
                    # 读取 time 字段用于窗口对齐
                    imu_time = None
                    if hasattr(raw, 'dtype') and raw.dtype.names and 'time' in raw.dtype.names:
                        imu_time = raw['time'][:].astype(np.float64)
                    if acc_data is not None and len(acc_data) > 0:
                        setattr(self, f'{attr_name}_data', acc_data)
                    if gyr_data is not None and len(gyr_data) > 0:
                        setattr(self, f'{attr_name}_gyr_data', gyr_data)
                    if imu_time is not None and len(imu_time) > 0:
                        setattr(self, f'{attr_name}_time', imu_time)
                    if acc_data is not None or gyr_data is not None:
                        t_info = f't={imu_time[0]:.1f}..{imu_time[-1]:.1f}' if imu_time is not None and len(imu_time) > 0 else 't=N/A'
                        print(f'[CalibrateTool] 已加载 {name}: acc={acc_data.shape if acc_data is not None else None}, '
                              f'gyr={gyr_data.shape if gyr_data is not None else None}, {t_info}')
                    break

        # ── 2. imu*_all_ble 按 imu_index 拆分（仅填充未由独立 dataset 加载的项） ──
        self._load_all_ble(1)
        self._load_all_ble(2)

        # ── 3. 推断 IMU 数量 ──
        self._infer_imu_counts()

    def _load_all_ble(self, device_id):
        """从 imu{device}_all_ble 按 imu_index 拆分到 a/b/c"""
        all_name = f'imu{device_id}_all_ble'
        if all_name not in self.h5_file:
            return

        ds = self.h5_file[all_name]
        if not hasattr(ds, 'dtype') or ds.dtype.names is None:
            return
        if 'imu_index' not in ds.dtype.names or 'acc' not in ds.dtype.names:
            return

        labels = ['a', 'b', 'c', 'd']
        for idx, label in enumerate(labels):
            attr_name = f'imu{device_id}{label}_data'
            # 独立 dataset 优先，不覆盖
            if getattr(self, attr_name, None) is not None:
                continue
            try:
                mask = ds['imu_index'][:] == idx
                if np.any(mask):
                    subset = ds[mask]
                    acc_data = self._extract_imu_acc(subset)
                    gyr_data = self._extract_imu_gyr(subset)
                    imu_time = None
                    if hasattr(subset, 'dtype') and subset.dtype.names and 'time' in subset.dtype.names:
                        imu_time = subset['time'][:].astype(np.float64)
                    if acc_data is not None and len(acc_data) > 0:
                        setattr(self, attr_name, acc_data)
                    if gyr_data is not None and len(gyr_data) > 0:
                        setattr(self, f'{attr_name}_gyr_data', gyr_data)
                    if imu_time is not None and len(imu_time) > 0:
                        setattr(self, f'{attr_name}_time', imu_time)
                    if acc_data is not None or gyr_data is not None:
                        print(f'[CalibrateTool] 从 {all_name} 拆分 imu_index={idx} -> {attr_name}: '
                              f'acc={acc_data.shape if acc_data is not None else None}, '
                              f'gyr={gyr_data.shape if gyr_data is not None else None}')
            except Exception as e:
                print(f'[CalibrateTool] 拆分 {all_name} imu_index={idx} 失败: {e}')

    def _infer_imu_counts(self):
        """推断 IMU 数量：H5 attrs > all_ble imu_index > 已加载 c 数据"""
        for dev in (1, 2):
            count = 2  # 默认 2 IMU
            # 优先从 H5 attrs
            for attr_key in (f'imu{dev}_num_imus', 'num_imus'):
                val = self.h5_file.attrs.get(attr_key)
                if val is not None:
                    try:
                        count = max(1, min(4, int(val)))
                        break
                    except (ValueError, TypeError):
                        pass
            else:
                # 从 all_ble imu_index 推断
                all_name = f'imu{dev}_all_ble'
                if all_name in self.h5_file:
                    ds = self.h5_file[all_name]
                    if hasattr(ds, 'dtype') and ds.dtype.names and 'imu_index' in ds.dtype.names:
                        try:
                            max_idx = int(ds['imu_index'][:].max())
                            count = max(1, min(4, max_idx + 1))
                        except Exception:
                            pass
                # fallback: 从 c 数据是否已加载推断
                if count < 3:
                    c_data = getattr(self, f'imu{dev}c_data', None)
                    if c_data is not None and len(c_data) > 0:
                        count = 3
            self.__dict__[f'imu{dev}_imu_count'] = count
            print(f'[CalibrateTool] IMU{dev} imu_count={count}')

    def _extract_imu_acc(self, data):
        """从结构化数组或普通数组中提取IMU加速度数据；返回 (N, 3) 或 None"""
        if data is None or len(data) == 0:
            return None

        print(f'[CalibrateTool] IMU数据类型: {data.dtype}, 字段: {data.dtype.names}')

        if hasattr(data, 'dtype') and data.dtype.names is not None:
            if 'acc' in data.dtype.names:
                acc_data = data['acc']
                print(f'[CalibrateTool] acc字段 shape: {acc_data.shape}')
                if acc_data.ndim == 1:
                    # 结构化 acc 字段，每行是 tuple/list
                    result = np.array([list(row) for row in acc_data])
                else:
                    result = np.array(acc_data)
                print(f'[CalibrateTool] 提取后 shape: {result.shape}')
                return result
            # 尝试其他字段名
            for name in data.dtype.names:
                if 'acc' in name.lower():
                    return np.array(data[name])

        # 普通数组：假设前 3 列是加速度
        data_arr = np.array(data)
        if data_arr.ndim == 2 and data_arr.shape[1] >= 3:
            return data_arr[:, :3]
        if data_arr.ndim == 1:
            return data_arr.reshape(-1, 1)

        return data_arr

    def _extract_imu_gyr(self, data):
        """从结构化数组或普通数组中提取IMU角速度数据；返回 (N, 3) 或 None"""
        if data is None or len(data) == 0:
            return None

        if hasattr(data, 'dtype') and data.dtype.names is not None:
            if 'gyr' in data.dtype.names:
                gyr_data = data['gyr']
                if gyr_data.ndim == 1:
                    result = np.array([list(row) for row in gyr_data])
                else:
                    result = np.array(gyr_data)
                return result
            # all_ble 格式: acc_x, acc_y, acc_z, gyr_x, gyr_y, gyr_z...
            # 取第 3-5 列作为 gyr
            pass

        # 普通数组：取第 3-5 列（跳过前 3 列 acc）
        data_arr = np.array(data)
        if data_arr.ndim == 2 and data_arr.shape[1] >= 6:
            return data_arr[:, 3:6]
        if data_arr.ndim == 2 and data_arr.shape[1] >= 3:
            return data_arr[:, :3]  # assume first 3 cols (may be acc+gyr merged)
        if data_arr.ndim == 1:
            return data_arr.reshape(-1, 1)
        return data_arr

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
        """计算全局Y轴范围（用于固定纵轴），使用 per-device LSB"""
        lsb_uv_1 = getattr(self, 'emg1_lsb_uv', calculate_lsb_uv())
        lsb_uv_2 = getattr(self, 'emg2_lsb_uv', calculate_lsb_uv())

        # EMG Y轴范围
        self.emg1_ylim = None
        self.emg2_ylim = None
        self.imu_ylim = None

        # 计算EMG1的范围
        if self.emg1_data is not None and len(self.emg1_data) > 0:
            data_uv = self.emg1_data * lsb_uv_1
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
            data_uv = self.emg2_data * lsb_uv_2
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

        # 计算IMU的范围（Acc + Gyr 所有数据）
        all_imu_data = []
        for attr_pattern in ['imu{a}{b}_data', 'imu{a}{b}_gyr_data']:
            for dev in [1, 2]:
                for label in ['a', 'b', 'c']:
                    attr = attr_pattern.format(a=dev, b=label)
                    data = getattr(self, attr, None)
                    if data is not None and len(data) > 0:
                        all_imu_data.append(data)

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
        """滑块值改变 - debounce 防抖，拖动 100ms / 点按 40ms"""
        self.current_pos = int(value)
        self.lbl_pos.setText(f'位置: {self.current_pos}')
        delay = 100 if self.is_dragging else 40
        self.update_timer.start(delay)

    def _do_update_plots(self):
        """执行实际的图表更新"""
        self.update_plots()

    def on_window_changed(self, value):
        """窗口大小改变"""
        self.window_size = value
        self._update_window_sec_label()
        max_len = self.get_max_data_length()
        self.slider.setMaximum(max(0, max_len - self.window_size))
        self.update_timer.start(40)

    def update_plots(self):
        """更新所有图表"""
        # 没有打开文件时不绘制
        if self.h5_file is None:
            return
        fast_mode = self.is_dragging
        self.update_emg_plot(fast_mode)
        self.update_imu_plot(fast_mode)

    def _downsample_for_plot(self, data, max_points):
        """降采样到最多 max_points 个点，返回 (data_downsampled, step)"""
        if data is None or len(data) == 0:
            return data, 1
        step = max(1, int(np.ceil(len(data) / max_points)))
        return data[::step], step

    def update_emg_plot(self, fast_mode=False):
        """更新EMG图表 — 根据 view_mode 分发"""
        if self.view_mode == 'stacked':
            self._draw_emg_stacked(fast_mode)
        else:
            self._draw_emg_subplots(fast_mode)

    def _draw_emg_stacked(self, fast_mode=False):
        """供应商风格堆叠视图：每设备 1 个 Axes，16 通道同轴堆叠 Offset=offset_uv"""
        ax1 = getattr(self, 'ax_emg1_stacked', None)
        ax2 = getattr(self, 'ax_emg2_stacked', None)
        if ax1 is None and ax2 is None:
            return
        if ax1: ax1.clear()
        if ax2: ax2.clear()

        start = self.current_pos
        end = start + self.window_size
        sample_rate = getattr(self, 'emg1_sample_rate', 2000)
        time_start = start / sample_rate
        time_end = end / sample_rate
        use_filter = self.chk_filter.isChecked() and not fast_mode
        filter_padding = 0 if not use_filter else min(1000, max(50, int(sample_rate * 0.25)))
        max_plot_points = self.max_plot_points_fast if fast_mode else self.max_plot_points_normal
        colors = plt.cm.tab20(np.linspace(0, 1, 16))
        offset = self.offset_uv
        clip_limit = offset * 0.48  # 供应商 clamp 阈值

        def _draw_device(ax, data, lsb, label, invert_channels=False):
            if ax is None or data is None or len(data) == 0:
                return
            if use_filter:
                pad_start = max(0, start - filter_padding)
                pad_end = min(len(data), end + filter_padding)
                region = data[pad_start:pad_end]
            else:
                region = data[start:end]
            if len(region) == 0:
                return
            data_uv = region.astype(np.float64) * lsb
            if use_filter and len(data_uv) > 50:
                try:
                    filt = self.emg_filter_2k if sample_rate == 2000 else self.emg_filter_250
                    data_uv = filt.filter(data_uv)
                except Exception as e:
                    print(f'[CalibrateTool] {label} 滤波失败: {e}')
            if use_filter and filter_padding > 0:
                actual_start = start - (start - filter_padding if start >= filter_padding else start)
                actual_end = actual_start + (end - start)
                data_uv = data_uv[actual_start:actual_end]
            data_uv, step = self._downsample_for_plot(data_uv, max_plot_points)
            x = np.linspace(time_start, time_start + len(data_uv) * step / sample_rate, len(data_uv))
            num_ch = min(16, data_uv.shape[1] if data_uv.ndim > 1 else 1)
            ch_order = range(num_ch - 1, -1, -1) if invert_channels else range(num_ch)
            for i, ch in enumerate(ch_order):
                ch_data = data_uv[:, ch] if data_uv.ndim > 1 else data_uv
                y = ch_data + (15 - i) * offset
                if self.clamp_enabled:
                    y = np.clip(y, (15 - i) * offset - clip_limit, (15 - i) * offset + clip_limit)
                ax.plot(x, y, color=colors[ch], linewidth=0.5)

            ax.set_xlim(time_start, time_end)
            ax.set_xlabel('时间 (秒)', fontsize=8)
            # Y 轴：CH1-CH16 标签
            y_ticks = [i * offset for i in range(16)]
            y_labels = [f'CH{i+1}' for i in range(16)]
            ax.set_yticks(y_ticks)
            ax.set_yticklabels(y_labels, fontsize=6)
            total_height = 16 * offset
            ax.set_ylim(-offset * 0.3, total_height - offset * 0.7)
            title_suffix = '滤波后' if use_filter else '原始'
            ax.set_title(f'{label} — {title_suffix} (Offset={offset}uV)', fontsize=10, pad=5)
            self.draw_prompt_markers(ax, time_start, time_end, show_text=True)

        _draw_device(ax1, self.emg1_data, self.emg1_lsb_uv, 'EMG1')
        _draw_device(ax2, self.emg2_data, self.emg2_lsb_uv, 'EMG2')
        self.canvas_emg.draw_idle()

    def _draw_emg_subplots(self, fast_mode=False):
        """分通道子图模式（保留原逻辑）"""
        # 清除所有通道的图表
        for ax in self.ax_emg1_channels + self.ax_emg2_channels:
            ax.clear()

        start = self.current_pos
        end = start + self.window_size
        lsb_uv_1 = getattr(self, 'emg1_lsb_uv', calculate_lsb_uv())
        lsb_uv_2 = getattr(self, 'emg2_lsb_uv', calculate_lsb_uv())
        use_filter = self.chk_filter.isChecked() and not fast_mode
        sample_rate = getattr(self, 'emg1_sample_rate', 2000)
        time_start = start / sample_rate
        time_end = end / sample_rate
        filter_padding = 0 if not use_filter else min(1000, max(50, int(sample_rate * 0.25)))
        max_plot_points = self.max_plot_points_fast if fast_mode else self.max_plot_points_normal
        colors = plt.cm.tab20(np.linspace(0, 1, 16))

        def _draw_channels(data, axes_list, lsb):
            if data is None or len(data) == 0:
                return
            pad_start = max(0, start - filter_padding)
            pad_end = min(len(data), end + filter_padding)
            data_padded = data[pad_start:pad_end]
            if len(data_padded) == 0:
                return
            data_uv_padded = data_padded * lsb
            if use_filter and len(data_uv_padded) > 50:
                try:
                    filt = self.emg_filter_2k if sample_rate == 2000 else self.emg_filter_250
                    data_uv_padded = filt.filter(data_uv_padded)
                except Exception as e:
                    print(f'[CalibrateTool] 滤波失败: {e}')
            actual_start = start - pad_start
            actual_end = actual_start + (end - start)
            data_uv = data_uv_padded[actual_start:actual_end]
            data_uv, step = self._downsample_for_plot(data_uv, max_plot_points)
            x = np.linspace(time_start, time_start + len(data_uv) * step / sample_rate, len(data_uv))
            num_channels = min(16, data_uv.shape[1] if data_uv.ndim > 1 else 1)
            for ch in range(num_channels):
                ax = axes_list[ch]
                if data_uv.ndim > 1:
                    ax.plot(x, data_uv[:, ch], color=colors[ch], linewidth=0.5)
                else:
                    ax.plot(x, data_uv, color=colors[0], linewidth=0.5)

        _draw_channels(self.emg1_data, self.ax_emg1_channels, lsb_uv_1)
        _draw_channels(self.emg2_data, self.ax_emg2_channels, lsb_uv_2)

        # 设置每个通道的属性
        for i in range(16):
            ax1 = self.ax_emg1_channels[i]
            ax2 = self.ax_emg2_channels[i]
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
            self.draw_prompt_markers(ax1, time_start, time_end, show_text=(i == 0))
            self.draw_prompt_markers(ax2, time_start, time_end, show_text=(i == 0))

        title_suffix = '滤波后' if use_filter else '原始'
        self.ax_emg1_channels[0].set_title(f'EMG1 (16通道) - {title_suffix}', fontsize=10, pad=5)
        self.ax_emg2_channels[0].set_title(f'EMG2 (16通道) - {title_suffix}', fontsize=10, pad=5)
        self.ax_emg1_channels[-1].set_xlabel('时间 (秒)', fontsize=8)
        self.ax_emg2_channels[-1].set_xlabel('时间 (秒)', fontsize=8)
        self.canvas_emg.draw_idle()

    def update_imu_plot(self, fast_mode=False):
        """供应商风格 IMU 显示：Acc + Gyr 堆叠图，每图同轴 XYZ 通道 × 多 IMU"""
        # 清除
        for ax_name in ('ax_imu_acc_dev1', 'ax_imu_acc_dev2', 'ax_imu_gyr_dev1', 'ax_imu_gyr_dev2'):
            ax = getattr(self, ax_name, None)
            if ax: ax.clear()

        start = self.current_pos
        emg_sample_rate = getattr(self, 'emg1_sample_rate', 2000)
        # 显示窗口的绝对时间范围（秒，相对于 EMG 起始）
        time_start = start / emg_sample_rate
        time_end = time_start + self.window_size / emg_sample_rate
        max_plot_points = self.max_plot_points_fast if fast_mode else self.max_plot_points_normal

        axis_colors = ['#d62728', '#2ca02c', '#1f77b4']  # XYZ: 红绿蓝
        axis_names = ['X', 'Y', 'Z']
        # IMU 线型: solid / dashed / dotted
        imu_styles = ['-', '--', ':']

        def _get_imu_window(data_attr):
            """根据 time 字段获取当前窗口内的 IMU 数据切片。
            优先用 IMU time 字段做 mask；无 time 字段时回退到 index 映射。"""
            labels = ['a', 'b', 'c']
            for dev_id in [1, 2]:
                for label in labels:
                    time_attr = f'imu{dev_id}{label}_time'
                    imu_time = getattr(self, time_attr, None)
                    if imu_time is not None and len(imu_time) > 0:
                        # 使用绝对时间字段
                        mask = (imu_time >= time_start) & (imu_time <= time_end)
                        indices = np.where(mask)[0]
                        if len(indices) > 0:
                            return indices, imu_time[indices]
            # fallback: index 映射（无 time 字段时）
            imu_sample_rate = 100
            imu_start = int(start * imu_sample_rate / emg_sample_rate)
            imu_end = int((start + self.window_size) * imu_sample_rate / emg_sample_rate)
            indices = np.arange(imu_start, imu_end)
            return indices, None

        imu_indices, imu_times = _get_imu_window('data')
        if len(imu_indices) == 0:
            self.canvas_imu_acc.draw_idle()
            self.canvas_imu_gyr.draw_idle()
            return
        imu_sample_rate = 100
        # 诊断日志: 当前显示窗口信息
        imu_t0 = imu_times[0] if imu_times is not None else imu_indices[0] / imu_sample_rate
        imu_t1 = imu_times[-1] if imu_times is not None else imu_indices[-1] / imu_sample_rate
        print(f'[CalibrateTool] IMU window: EMG pos={start} ({time_start:.1f}s-{time_end:.1f}s), '
              f'IMU idx=[{imu_indices[0]}:{imu_indices[-1]}] ({imu_t0:.1f}s-{imu_t1:.1f}s), '
              f'{len(imu_indices)} frames')

        def _draw_imu_device(ax, dev_id, imu_count, data_attr, offset):
            """在 ax 上画 dev_id 的 acc 或 gyr 数据 (供应商堆叠，自动 Y 轴范围)"""
            if ax is None:
                return
            labels = ['a', 'b', 'c']
            all_y_min = []
            all_y_max = []
            for imu_idx in range(imu_count):
                label = labels[imu_idx]
                attr = f'imu{dev_id}{label}_{data_attr}'
                data = getattr(self, attr, None)
                if data is None or len(data) == 0:
                    continue
                if len(imu_indices) > len(data):
                    continue
                chunk = data[imu_indices]
                if len(chunk) == 0:
                    continue
                chunk, step = self._downsample_for_plot(chunk, max_plot_points)
                # X 轴：优先用 IMU time，否则线性插值
                if imu_times is not None and len(imu_times) > 0:
                    t = imu_times[::step] if step > 1 else imu_times
                    x = t[:len(chunk)]
                else:
                    x = np.linspace(time_start, time_start + len(chunk) * step / imu_sample_rate, len(chunk))
                num_axis = min(3, chunk.shape[1] if chunk.ndim > 1 else 1)
                for axis_idx in range(num_axis):
                    ch_data = chunk[:, axis_idx] if chunk.ndim > 1 else chunk
                    y = ch_data + (2 - axis_idx) * offset  # X=2*offset(top), Z=0*offset(bottom)
                    ax.plot(x, y, color=axis_colors[axis_idx],
                            linestyle=imu_styles[imu_idx % 3], linewidth=0.8)
                    all_y_min.append(float(np.min(y)))
                    all_y_max.append(float(np.max(y)))

            ax.set_xlim(time_start, time_end)
            ax.set_xlabel('时间 (秒)', fontsize=8)
            # Y轴标签: 在各通道基线处
            y_ticks = [i * offset for i in range(3)]
            y_labels = [f'{axis_names[i]}' for i in range(3)]
            ax.set_yticks(y_ticks)
            ax.set_yticklabels(y_labels, fontsize=7)
            # 自动 Y 轴范围 — 根据实际数据缩放，加 10% margin
            if all_y_min and all_y_max:
                ymin, ymax = min(all_y_min), max(all_y_max)
                margin = max((ymax - ymin) * 0.1, offset * 0.1)
                ax.set_ylim(ymin - margin, ymax + margin)
            else:
                ax.set_ylim(-offset * 0.2, 3 * offset - offset * 0.2)
            dev_label = f'设备{dev_id}'
            # 线型图例
            legend_lines = []
            legend_labels_list = []
            for imu_idx in range(imu_count):
                legend_lines.append(Line2D([0], [0], color='gray', linestyle=imu_styles[imu_idx % 3], linewidth=1))
                legend_labels_list.append(f'IMU{imu_idx+1}')
            if legend_lines:
                ax.legend(legend_lines, legend_labels_list, fontsize=6, loc='upper right')
            ax.set_title(f'{dev_label} ({imu_count}IMU)', fontsize=9, pad=3)
            self.draw_prompt_markers(ax, time_start, time_end, show_text=True)

        # ── Acc 图 ──
        _draw_imu_device(getattr(self, 'ax_imu_acc_dev1', None), 1, self.imu1_imu_count, 'data', self.imu_acc_offset)
        _draw_imu_device(getattr(self, 'ax_imu_acc_dev2', None), 2, self.imu2_imu_count, 'data', self.imu_acc_offset)
        self.fig_imu_acc.suptitle('加速度 (g)', fontsize=11, fontweight='bold')
        self.canvas_imu_acc.draw_idle()

        # ── Gyr 图 ──
        _draw_imu_device(getattr(self, 'ax_imu_gyr_dev1', None), 1, self.imu1_imu_count, 'gyr_data', self.imu_gyr_offset)
        _draw_imu_device(getattr(self, 'ax_imu_gyr_dev2', None), 2, self.imu2_imu_count, 'gyr_data', self.imu_gyr_offset)
        self.fig_imu_gyr.suptitle('角速度 (deg/s)', fontsize=11, fontweight='bold')
        self.canvas_imu_gyr.draw_idle()

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
