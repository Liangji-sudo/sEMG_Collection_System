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
import time
import h5py
import numpy as np
from scipy import signal as scipy_signal

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QSlider, QSpinBox, QGroupBox,
    QSplitter, QComboBox, QCheckBox, QMessageBox, QScrollArea
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
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


class CalibrateWidget(QWidget):
    """H5数据可视化控件 — 可嵌入其他应用"""

    def __init__(self, parent=None):
        super().__init__(parent)

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

        # 视频预览相关
        self.video_caps = {}            # {'left': cv2.VideoCapture, 'right': cv2.VideoCapture}
        self.video_fps = {}             # {'left': fps, 'right': fps}
        self.video_first_frame_unix = {}  # {'left': unix_ts, 'right': unix_ts}
        self.video_last_frame_unix = {}   # {'left': unix_ts, 'right': unix_ts}
        self.video_duration = {}        # {'left': dur_sec, 'right': dur_sec}
        self.video_frame_count = {}     # {'left': n_frames, 'right': n_frames}
        self.video_enabled = False      # 是否有可用的视频
        self._video_current_frame = {'left': None, 'right': None}   # 当前显示的 QPixmap
        self._video_current_idx = {'left': -1, 'right': -1}         # 当前帧索引
        self._last_video_update = 0     # 上次视频更新时间戳
        self._video_update_throttle_drag = 0.15   # 拖动时节流间隔 (秒)
        self._video_update_throttle_static = 0.05 # 静止时更新间隔
        self.video_label_height = 200   # 视频预览区域高度

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

        # 视频回放相关
        self.is_playing = False           # 是否正在播放
        self.playback_speed = 1.0         # 播放速度倍率 (0.5x / 1x / 2x)
        self.playback_stop_pos = 0        # 播放停止位置
        self.playback_auto_on_prompt = True  # 跳转 Prompt 后自动播放
        self.playback_timer = QTimer()
        self.playback_timer.setInterval(33)  # ~30fps 更新频率
        self.playback_timer.timeout.connect(self._on_playback_tick)

        self.init_ui()

    def _active_emg_sample_rate(self):
        if self.emg1_data is not None and len(self.emg1_data) > 0 and self.emg1_sample_rate:
            return self.emg1_sample_rate
        if self.emg2_data is not None and len(self.emg2_data) > 0 and self.emg2_sample_rate:
            return self.emg2_sample_rate
        return 2000

    def _relative_sensor_time(self, times):
        times = np.asarray(times, dtype=np.float64)
        if self.emg_start_time is None or len(times) == 0:
            return times
        if float(np.nanmedian(times)) > 1e6:
            return times - float(self.emg_start_time)
        return times

    def init_ui(self):
        """初始化UI"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

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

        # 视频回放控制
        control_layout.addWidget(QLabel('  |  '))
        self.btn_play = QPushButton('▶ 播放')
        self.btn_play.setToolTip('播放当前窗口的视频片段 (再次点击暂停)')
        self.btn_play.clicked.connect(self.on_play_clicked)
        self.btn_play.setEnabled(False)
        control_layout.addWidget(self.btn_play)

        control_layout.addWidget(QLabel(' 速度:'))
        self.combo_speed = QComboBox()
        self.combo_speed.addItems(['0.5x', '1x', '2x'])
        self.combo_speed.setCurrentIndex(1)
        self.combo_speed.currentIndexChanged.connect(self.on_speed_changed)
        self.combo_speed.setToolTip('播放速度倍率')
        control_layout.addWidget(self.combo_speed)

        self.chk_auto_play = QCheckBox('跳转后播放')
        self.chk_auto_play.setChecked(self.playback_auto_on_prompt)
        self.chk_auto_play.stateChanged.connect(self.on_auto_play_changed)
        self.chk_auto_play.setToolTip('跳转到 Prompt 后自动播放视频片段')
        control_layout.addWidget(self.chk_auto_play)

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

        # 视频预览面板（左右视频并排）
        video_widget = QWidget()
        video_layout = QHBoxLayout(video_widget)
        video_layout.setContentsMargins(2, 2, 2, 2)
        video_layout.setSpacing(4)

        # 左侧视频
        left_video_group = QGroupBox('📹 左手视频 (Left)')
        left_video_inner = QVBoxLayout(left_video_group)
        left_video_inner.setContentsMargins(2, 2, 2, 2)
        self.lbl_video_left = QLabel()
        self.lbl_video_left.setAlignment(Qt.AlignCenter)
        self.lbl_video_left.setMinimumHeight(self.video_label_height)
        self.lbl_video_left.setStyleSheet(
            'background-color: #1a1a2e; border: 1px solid #333; color: #666; font-size: 11px;'
        )
        self.lbl_video_left.setText('(无视频)')
        left_video_inner.addWidget(self.lbl_video_left)
        self.lbl_video_left_time = QLabel('--:--')
        self.lbl_video_left_time.setAlignment(Qt.AlignCenter)
        self.lbl_video_left_time.setStyleSheet('color: #888; font-size: 9px;')
        left_video_inner.addWidget(self.lbl_video_left_time)
        video_layout.addWidget(left_video_group)

        # 右侧视频
        right_video_group = QGroupBox('📹 右手视频 (Right)')
        right_video_inner = QVBoxLayout(right_video_group)
        right_video_inner.setContentsMargins(2, 2, 2, 2)
        self.lbl_video_right = QLabel()
        self.lbl_video_right.setAlignment(Qt.AlignCenter)
        self.lbl_video_right.setMinimumHeight(self.video_label_height)
        self.lbl_video_right.setStyleSheet(
            'background-color: #1a1a2e; border: 1px solid #333; color: #666; font-size: 11px;'
        )
        self.lbl_video_right.setText('(无视频)')
        right_video_inner.addWidget(self.lbl_video_right)
        self.lbl_video_right_time = QLabel('--:--')
        self.lbl_video_right_time.setAlignment(Qt.AlignCenter)
        self.lbl_video_right_time.setStyleSheet('color: #888; font-size: 9px;')
        right_video_inner.addWidget(self.lbl_video_right_time)
        video_layout.addWidget(right_video_group)

        splitter.addWidget(video_widget)

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
        sr = self._active_emg_sample_rate()
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
            # 停止正在进行的播放
            if self.is_playing:
                self._stop_playback()

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
            self._load_videos()

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

            # 有数据即可启用播放按钮
            self.btn_play.setEnabled(True)

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
        sr = self._active_emg_sample_rate()
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
        self.emg1_sample_rate = None
        self.emg2_sample_rate = None
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
        sr = self._active_emg_sample_rate()
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
                        imu_time = self._relative_sensor_time(raw['time'][:])
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
                        imu_time = self._relative_sensor_time(subset['time'][:])
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

    # ───────────────── 视频预览相关方法 ─────────────────

    def _find_video_files(self):
        """根据 H5 attrs 定位视频文件路径。

        查找策略（按优先级）：
        1. 从 H5 所在目录向上遍历，查找每一层的 video/ 子目录
        2. H5 同目录
        3. 绝对路径
        4. 同时尝试替换后缀（.avi ↔ .mp4 ↔ .mkv ↔ .mov）
        """
        video_files = {}
        if self.h5_file is None or self.h5_path is None:
            return video_files

        h5_dir = os.path.dirname(os.path.abspath(self.h5_path))

        # 构建搜索目录：从 H5 目录向上逐层查找 video/ 子目录
        search_dirs = []
        cur = os.path.abspath(h5_dir)
        project_root = None
        for _ in range(6):  # 最多向上 6 层
            # 当前层的 video/ 子目录
            video_sub = os.path.join(cur, 'video')
            if os.path.isdir(video_sub):
                search_dirs.append(video_sub)
            # 记录 storage/ 的上层目录作为项目根
            if os.path.basename(cur) == 'storage':
                project_root = os.path.dirname(cur)
                storage_video = os.path.join(cur, 'video')
                if os.path.isdir(storage_video) and storage_video not in search_dirs:
                    search_dirs.append(storage_video)
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        # H5 同目录作为兜底
        if h5_dir not in search_dirs:
            search_dirs.append(h5_dir)

        # 备选视频后缀（用于替换不匹配的后缀）
        ALT_EXTENSIONS = ['.avi', '.mp4', '.mkv', '.mov', '.AVI', '.MP4', '.MKV', '.MOV']

        def _try_find_file(base_filename):
            """在搜索目录中定位文件，支持后缀替换"""
            base_name, base_ext = os.path.splitext(base_filename)
            # 候选文件名：原文件名 + 替换后缀的版本
            candidates = [base_filename]
            for alt_ext in ALT_EXTENSIONS:
                alt_name = base_name + alt_ext
                if alt_name not in candidates:
                    candidates.append(alt_name)

            for search_dir in search_dirs:
                for candidate in candidates:
                    full_path = os.path.normpath(os.path.join(search_dir, candidate))
                    if os.path.isfile(full_path):
                        return full_path
                    # 也尝试只用 basename
                    full_path = os.path.normpath(os.path.join(search_dir, os.path.basename(candidate)))
                    if os.path.isfile(full_path):
                        return full_path
            return None

        for side_key, attr_key in [('left', 'video_left'), ('right', 'video_right')]:
            filename = self.h5_file.attrs.get(attr_key)
            if filename is None:
                continue
            if isinstance(filename, bytes):
                filename = filename.decode('utf-8')
            filename = str(filename).strip()
            if not filename or filename == '-':
                continue

            found_path = _try_find_file(filename)

            # 如果文件路径本身就是绝对路径，直接使用
            if found_path is None and os.path.isabs(filename) and os.path.isfile(filename):
                found_path = filename

            if found_path:
                video_files[side_key] = found_path
                print(f'[CalibrateTool] 找到视频文件 ({side_key}): {found_path}')
            else:
                print(f'[CalibrateTool] 未找到视频文件 ({side_key}): {filename} '
                      f'(搜索目录: {[os.path.basename(d) or d for d in search_dirs[:3]]}...)')

        return video_files

    def _load_videos(self):
        """加载 H5 对应的视频文件"""
        self._close_videos()
        self.video_enabled = False

        if not HAS_CV2:
            print('[CalibrateTool] OpenCV (cv2) 未安装，跳过视频加载')
            self.lbl_video_left.setText('(需安装 opencv-python)')
            self.lbl_video_right.setText('(需安装 opencv-python)')
            return

        video_files = self._find_video_files()
        if not video_files:
            print('[CalibrateTool] 未找到关联视频文件')
            self.lbl_video_left.setText('(无视频)')
            self.lbl_video_right.setText('(无视频)')
            return

        # 读取 video_timing 组获取时间对齐信息
        vt_first = {}
        vt_last = {}
        vt_dur = {}
        if 'video_timing' in self.h5_file:
            try:
                vt = self.h5_file['video_timing']
                sides_raw = vt['sides'][()]
                firsts = np.atleast_1d(vt['first_frame_unix'][()])
                lasts = np.atleast_1d(vt['last_frame_unix'][()])
                durs = np.atleast_1d(vt['duration'][()])
                sides = [s.decode('utf-8') if isinstance(s, bytes) else str(s)
                         for s in np.atleast_1d(sides_raw)]
                for i, side in enumerate(sides):
                    if i < len(firsts):
                        vt_first[side] = float(firsts[i])
                        vt_last[side] = float(lasts[i])
                        vt_dur[side] = float(durs[i])
            except Exception as e:
                print(f'[CalibrateTool] 读取 video_timing 失败: {e}')

        for side, video_path in video_files.items():
            try:
                cap = cv2.VideoCapture(video_path)
                if not cap.isOpened():
                    print(f'[CalibrateTool] 无法打开视频 ({side}): {video_path}')
                    continue

                fps = cap.get(cv2.CAP_PROP_FPS)
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                self.video_caps[side] = cap
                self.video_fps[side] = fps if fps > 0 else 30.0
                self.video_frame_count[side] = frame_count
                self.video_first_frame_unix[side] = vt_first.get(side, 0)
                self.video_last_frame_unix[side] = vt_last.get(side, 0)
                self.video_duration[side] = vt_dur.get(side, frame_count / self.video_fps[side] if fps > 0 else 0)
                self._video_current_idx[side] = -1
                self._video_current_frame[side] = None
                self.video_enabled = True

                print(f'[CalibrateTool] 视频已加载 ({side}): {os.path.basename(video_path)}, '
                      f'fps={self.video_fps[side]:.1f}, frames={frame_count}, '
                      f'dur={self.video_duration[side]:.1f}s, '
                      f'first_frame_unix={self.video_first_frame_unix[side]:.3f}')
            except Exception as e:
                print(f'[CalibrateTool] 加载视频失败 ({side}): {e}')

        # 更新 UI 状态
        if not self.video_enabled:
            self.lbl_video_left.setText('(无法加载视频)')
            self.lbl_video_right.setText('(无法加载视频)')
        else:
            for side in ('left', 'right'):
                if side in self.video_caps:
                    lbl = getattr(self, f'lbl_video_{side}')
                    lbl.setText('')

    def _close_videos(self):
        """关闭所有视频文件"""
        for side, cap in self.video_caps.items():
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
        self.video_caps.clear()
        self.video_fps.clear()
        self.video_first_frame_unix.clear()
        self.video_last_frame_unix.clear()
        self.video_duration.clear()
        self.video_frame_count.clear()
        self._video_current_frame.clear()
        self._video_current_idx.clear()
        self.video_enabled = False

    def _seek_video_frame(self, side, target_unix):
        """定位到指定 Unix 时间戳对应的视频帧，返回 (frame_idx, qimage)。

        Args:
            side: 'left' 或 'right'
            target_unix: 目标 Unix 时间戳

        Returns:
            (frame_idx, QImage) 或 (None, None)
        """
        cap = self.video_caps.get(side)
        if cap is None:
            return None, None

        first_unix = self.video_first_frame_unix.get(side, 0)
        fps = self.video_fps.get(side, 30)
        total_frames = self.video_frame_count.get(side, 0)

        if total_frames <= 0 or fps <= 0:
            return None, None

        # 从 Unix 时间戳映射到帧偏移
        time_offset = target_unix - first_unix
        frame_idx = int(round(time_offset * fps))
        frame_idx = max(0, min(frame_idx, total_frames - 1))

        # 缓存检查：同一帧不需要重新 seek
        if frame_idx == self._video_current_idx.get(side, -1):
            cached = self._video_current_frame.get(side)
            if cached is not None:
                return frame_idx, cached

        # seek 到目标帧
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()

        if not ret or frame is None:
            # 回退：seek 到最近的关键帧后逐帧解码
            # 先往回跳 50 帧，再逐帧前进
            fallback_start = max(0, frame_idx - 50)
            cap.set(cv2.CAP_PROP_POS_FRAMES, fallback_start)
            for _ in range(frame_idx - fallback_start + 1):
                ret, frame = cap.read()
                if not ret:
                    break

        if not ret or frame is None:
            return None, None

        # BGR → RGB 转换
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame_rgb.shape
        bytes_per_line = ch * w
        qimage = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()

        # 缓存
        self._video_current_idx[side] = frame_idx
        self._video_current_frame[side] = qimage

        return frame_idx, qimage

    def _update_video_frames(self, target_unix):
        """更新左右视频 QLabel 显示。

        Args:
            target_unix: 当前 EMG/IMU 显示窗口对应的 Unix 时间戳（窗口中间位置）
        """
        if not self.video_enabled:
            return

        # 节流：拖动时减少视频更新频率；播放时不节流
        now = time.time()
        if self.is_playing:
            throttle = 0  # 播放时不节流，确保视频流畅
        else:
            throttle = (self._video_update_throttle_drag if self.is_dragging
                        else self._video_update_throttle_static)
        if now - self._last_video_update < throttle:
            return
        self._last_video_update = now

        for side in ('left', 'right'):
            lbl = getattr(self, f'lbl_video_{side}')
            lbl_time = getattr(self, f'lbl_video_{side}_time')
            if side not in self.video_caps:
                continue

            frame_idx, qimage = self._seek_video_frame(side, target_unix)

            if qimage is not None and frame_idx is not None:
                # 缩放到显示区域大小
                lbl_size = lbl.size()
                # 有效布局尺寸（宽度>50 且高度>50 才认为已布局）
                if lbl_size.width() > 50 and lbl_size.height() > 50:
                    target_w, target_h = lbl_size.width(), lbl_size.height()
                else:
                    # 未完成布局时使用默认高度，保持宽高比
                    target_h = self.video_label_height
                    target_w = int(qimage.width() * target_h / qimage.height())
                scaled = qimage.scaled(
                    target_w, target_h,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                pixmap = QPixmap.fromImage(scaled)
                lbl.setPixmap(pixmap)

                # 更新时间标签
                fps = self.video_fps.get(side, 30)
                frame_time_sec = frame_idx / fps if fps > 0 else 0
                minutes = int(frame_time_sec // 60)
                seconds = int(frame_time_sec % 60)
                ms = int((frame_time_sec % 1) * 100)
                lbl_time.setText(f'Frame #{frame_idx} | {minutes:02d}:{seconds:02d}.{ms:02d}')
            else:
                lbl.setText('(无法定位帧)')
                lbl_time.setText('--:--')

    # ─────────── 视频方法结束 ───────────

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
        # 用户手动拖动时停止自动播放
        if self.is_playing:
            self._stop_playback()
        self.is_dragging = True

    def on_slider_released(self):
        """滑块释放结束拖动，执行精确更新"""
        self.is_dragging = False
        # 重置视频更新节流，确保释放时立即刷新视频帧
        self._last_video_update = 0
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

        # 视频帧同步更新
        if self.video_enabled and self.emg_start_time is not None:
            sample_rate = self._active_emg_sample_rate()
            # 取窗口中间位置的时间
            window_center_offset = (self.current_pos + self.window_size / 2) / sample_rate
            target_unix = float(self.emg_start_time) + window_center_offset
            self._update_video_frames(target_unix)

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
        sample_rate = self._active_emg_sample_rate()
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
        sample_rate = self._active_emg_sample_rate()
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
        emg_sample_rate = self._active_emg_sample_rate()
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
                # 过滤越界索引（不同 IMU 传感器数据长度可能不同）
                valid_mask = imu_indices < len(data)
                imu_idx_subset = imu_indices[valid_mask]
                if len(imu_idx_subset) == 0:
                    continue
                # 同时裁剪 imu_times 保持一致
                imu_t_subset = imu_times[valid_mask] if imu_times is not None else None
                chunk = data[imu_idx_subset]
                if len(chunk) == 0:
                    continue
                chunk, step = self._downsample_for_plot(chunk, max_plot_points)
                # X 轴：优先用 IMU time，否则线性插值
                if imu_t_subset is not None and len(imu_t_subset) > 0:
                    t = imu_t_subset[::step] if step > 1 else imu_t_subset
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
        sample_rate = self._active_emg_sample_rate()
        sample_pos = int(prompt_time * sample_rate)

        # 将prompt放在窗口中间偏左的位置
        target_pos = sample_pos - self.window_size // 4

        # 限制范围
        max_pos = self.slider.maximum()
        target_pos = max(0, min(target_pos, max_pos))

        # 先停止当前播放（如果有的话）
        if self.is_playing:
            self._stop_playback()

        # 更新滑块位置（会触发update_plots）
        self.slider.setValue(target_pos)

        # 更新prompt信息显示
        self._update_prompt_info()

        # 自动播放（如果启用且有视频）
        if self.playback_auto_on_prompt and self.video_enabled:
            # 使用 QTimer.singleShot 确保 UI 先刷新再开始播放
            QTimer.singleShot(100, self._start_playback)

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

    # ───────────────── 视频回放相关方法 ─────────────────

    def on_play_clicked(self):
        """播放/暂停按钮"""
        if self.is_playing:
            self._stop_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        """开始播放：从当前滑块位置播放到窗口末尾或数据末尾"""
        if self.h5_file is None:
            return
        max_len = self.get_max_data_length()
        if max_len == 0:
            return

        # 播放范围：当前窗口大小 × 2（给用户看到 prompt 前后足够内容）
        # 如果从 prompt 跳转来，current_pos 已在 prompt 前 25% window 处
        play_duration_samples = self.window_size * 2
        self.playback_stop_pos = min(self.current_pos + play_duration_samples, max_len)

        self.is_playing = True
        self.btn_play.setText('⏸ 暂停')
        self.btn_play.setEnabled(True)
        self.playback_timer.start()

        # 播放期间禁止视频节流（确保每帧都刷新）
        self._last_video_update = 0

        print(f'[CalibrateTool] 开始播放: pos={self.current_pos} → stop={self.playback_stop_pos}, '
              f'speed={self.playback_speed}x')

    def _stop_playback(self):
        """停止播放"""
        self.is_playing = False
        self.playback_timer.stop()
        self.btn_play.setText('▶ 播放')
        # 停止时精确刷新到最终位置
        self._last_video_update = 0
        self._do_update_plots()
        print(f'[CalibrateTool] 播放停止: pos={self.current_pos}')

    def _on_playback_tick(self):
        """播放定时器触发：推进滑块位置"""
        if not self.is_playing or self.h5_file is None:
            self._stop_playback()
            return

        sample_rate = self._active_emg_sample_rate()
        # 每次 tick 推进的样本数 = 采样率 × 速度倍率 / 30fps
        step = max(1, int(sample_rate * self.playback_speed / 30.0))
        new_pos = self.current_pos + step

        # 检查是否到达停止位置
        if new_pos >= self.playback_stop_pos:
            self.current_pos = self.playback_stop_pos
            self.slider.setValue(min(self.current_pos, self.slider.maximum()))
            self._stop_playback()
            return

        self.current_pos = new_pos
        # 阻塞信号避免重复触发 on_slider_changed
        self.slider.blockSignals(True)
        self.slider.setValue(min(self.current_pos, self.slider.maximum()))
        self.slider.blockSignals(False)
        self.lbl_pos.setText(f'位置: {self.current_pos}')

        # 直接更新图表（绕过 update_timer 延迟）
        self.update_plots()

    def on_speed_changed(self, idx):
        """播放速度改变"""
        speeds = [0.5, 1.0, 2.0]
        if 0 <= idx < len(speeds):
            self.playback_speed = speeds[idx]
            print(f'[CalibrateTool] 播放速度: {self.playback_speed}x')

    def on_auto_play_changed(self, state):
        """自动播放选项改变"""
        self.playback_auto_on_prompt = (state == Qt.Checked)

    # ─────────── 回放方法结束 ───────────

    def closeEvent(self, event):
        """关闭窗口时清理资源"""
        if self.is_playing:
            self._stop_playback()
        self._close_videos()
        if self.h5_file:
            self.h5_file.close()
        event.accept()


class CalibrateTool(QMainWindow):
    """H5数据可视化工具主窗口 — 独立运行（薄包装器）"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle('H5数据可视化工具 - calibrate_tool')
        self.resize(1200, 800)
        self.setMinimumSize(900, 600)

        self._widget = CalibrateWidget()
        self.setCentralWidget(self._widget)

    def closeEvent(self, event):
        """关闭窗口时清理H5资源（委托给内部 widget）"""
        self._widget.closeEvent(event)
        super().closeEvent(event)

    def __getattr__(self, name):
        """将未定义属性访问代理到内部 CalibrateWidget"""
        widget = self.__dict__.get('_widget', None)
        if widget is not None:
            return getattr(widget, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


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
