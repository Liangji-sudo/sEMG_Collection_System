"""
HDF5整合工具 - 结合查看和同步功能
功能：
1. 选择目录，批量加载子目录下所有h5文件
2. 查看标签页：完整的h5文件查看功能
3. 同步标签页：将250Hz数据与SD卡bin文件同步补全为2kHz
"""

import sys
import os
import json
import h5py
import numpy as np
from datetime import datetime
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
    from bin_sync_tool import (EMGBinParser, IMUBinParser, sync_h5_with_bin,
                               diagnose_frame_ids, clear_sync_outputs,
                               append_sync_history)
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

    def __init__(self, h5_files, bin_dir, devices, validate_data):
        super().__init__()
        self.h5_files = h5_files
        self.bin_dir = bin_dir  # bin文件所在目录
        self.devices = devices
        self.validate_data = validate_data

    def _find_bin_files(self, h5_path, device_id):
        """
        从H5文件属性中读取bin文件前缀，在目录中查找对应的bin文件

        Args:
            h5_path: H5文件路径
            device_id: 设备ID (1 或 2)

        Returns:
            tuple: (emg_bin_path, imu_bin_path) 或 (None, None)
        """
        try:
            with h5py.File(h5_path, 'r') as f:
                # 读取对应设备的bin文件前缀
                attr_name = f'sd_bin_dev{device_id}'
                bin_prefix = f.attrs.get(attr_name, None)

                if bin_prefix is None:
                    return None, None

                # 处理字节字符串
                if isinstance(bin_prefix, bytes):
                    bin_prefix = bin_prefix.decode('utf-8')

                # 构建完整的bin文件路径
                emg_bin_name = f"{bin_prefix}_emg.bin"
                imu_bin_name = f"{bin_prefix}_imu.bin"

                emg_bin_path = os.path.join(self.bin_dir, emg_bin_name)
                imu_bin_path = os.path.join(self.bin_dir, imu_bin_name)

                # 检查文件是否存在
                emg_exists = os.path.exists(emg_bin_path)
                imu_exists = os.path.exists(imu_bin_path)

                return (emg_bin_path if emg_exists else None,
                        imu_bin_path if imu_exists else None)

        except Exception as e:
            self.log.emit(f"    读取H5属性失败: {str(e)}")
            return None, None

    def run(self):
        if not HAS_SYNC_TOOL:
            self.finished_signal.emit(False, "同步功能不可用：无法导入bin_sync_tool")
            return

        try:
            total = len(self.h5_files)
            success_count = 0
            skipped_count = 0

            # 根据勾选的设备确定需要处理的device_id列表
            device_ids = set()
            if 'emg1' in self.devices or 'imu1' in self.devices:
                device_ids.add(1)
            if 'emg2' in self.devices or 'imu2' in self.devices:
                device_ids.add(2)

            # 检查是否需要双设备同步（两个设备都被勾选）
            require_both_devices = len(device_ids) == 2

            for i, h5_file in enumerate(self.h5_files):
                self.progress.emit(i + 1, total, os.path.basename(h5_file))
                self.log.emit(f"\n处理文件: {os.path.basename(h5_file)}")

                # 【新增】如果需要双设备同步，先检查H5文件是否有两个设备的bin文件配置
                if require_both_devices:
                    # 检查H5文件中是否配置了两个设备的bin文件前缀
                    try:
                        with h5py.File(h5_file, 'r') as f:
                            sd_bin_dev1 = f.attrs.get('sd_bin_dev1', None)
                            sd_bin_dev2 = f.attrs.get('sd_bin_dev2', None)

                            # 如果H5文件配置了两个设备的bin文件，则必须两个都能找到
                            if sd_bin_dev1 and sd_bin_dev2:
                                # 检查两个设备的bin文件是否都存在
                                missing_devices = []
                                for dev_id in [1, 2]:
                                    emg_bin_path, imu_bin_path = self._find_bin_files(h5_file, dev_id)
                                    # 检查EMG bin文件（主要数据）
                                    if f'emg{dev_id}' in self.devices and not emg_bin_path:
                                        missing_devices.append(dev_id)

                                if missing_devices:
                                    self.log.emit(f"  ⚠️ 警告: 此H5文件需要两个设备的bin文件，但设备{missing_devices}的bin文件缺失")
                                    self.log.emit(f"  ✗ 跳过此文件: 双设备模式要求两个设备的bin文件都存在")
                                    skipped_count += 1
                                    continue
                    except Exception as e:
                        self.log.emit(f"  ✗ 检查bin文件配置失败: {str(e)}")
                        continue

                file_success = False
                # 判断此H5文件需要同步几个设备
                devices_to_sync = []
                for device_id in sorted(device_ids):
                    emg_bin_path, imu_bin_path = self._find_bin_files(h5_file, device_id)
                    emg_bin = emg_bin_path if (f'emg{device_id}' in self.devices) else None
                    imu_bin = imu_bin_path if (f'imu{device_id}' in self.devices) else None
                    if emg_bin:
                        devices_to_sync.append((device_id, emg_bin, imu_bin))

                total_devices = len(devices_to_sync)

                for idx, (device_id, emg_bin, imu_bin) in enumerate(devices_to_sync):
                    try:
                        self.log.emit(f"  设备{device_id}:")
                        self.log.emit(f"    EMG bin: {os.path.basename(emg_bin)}")
                        if imu_bin:
                            self.log.emit(f"    IMU bin: {os.path.basename(imu_bin)}")

                        # 判断是否是最后一个设备，只有最后一个设备同步完才设置synced
                        is_last_device = (idx == total_devices - 1)

                        result = sync_h5_with_bin(
                            h5_path=h5_file,
                            emg_bin_path=emg_bin,
                            imu_bin_path=imu_bin,
                            device_id=device_id,
                            verify=self.validate_data,
                            set_synced=is_last_device  # 只有最后一个设备同步完才设置synced
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
                                self.log.emit(f"    - IMU: 未找到bin文件，跳过")
                        elif result.get('status') == 'skipped':
                            self.log.emit(f"    - 跳过: {result.get('reason', '已同步')}")
                        else:
                            self.log.emit(f"    ✗ 失败: {result.get('reason', '未知错误')}")

                    except Exception as e:
                        self.log.emit(f"    ✗ 错误: {str(e)}")

                # 如果没有找到任何可同步的设备
                if total_devices == 0:
                    for device_id in sorted(device_ids):
                        if f'emg{device_id}' in self.devices:
                            self.log.emit(f"  设备{device_id}:")
                            self.log.emit(f"    ✗ 找不到EMG bin文件 (sd_bin_dev{device_id}属性缺失或文件不存在)")
                        else:
                            self.log.emit(f"  设备{device_id}:")
                            self.log.emit(f"    - EMG未勾选，跳过")

                if file_success:
                    success_count += 1

            # 构建完成消息
            if skipped_count > 0:
                msg = f"完成: {success_count}/{total} 个文件同步成功, {skipped_count} 个文件因bin文件不全被跳过"
            else:
                msg = f"完成: {success_count}/{total} 个文件同步成功"
            self.finished_signal.emit(True, msg)

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


def extract_segment_metadata(h5_f):
    """Phase 4: 从 H5 文件提取 segment/bin 元数据（纯函数，可独立测试）

    Args:
        h5_f: h5py.File 对象

    Returns:
        dict: {
            'collection_status', 'is_resumed', 'segment_index',
            'collection_session_id', 'interruption_id',
            'start_time', 'end_time', 'duration',
            'session_info', 'stage_info',
            'emg1_range', 'emg2_range',
            'bin_info', 'abnormal_detail', 'resume_detail',
            ...
        }，缺失字段值为 None 或 '-'
    """
    def _str(v):
        if v is None:
            return None
        if isinstance(v, bytes):
            return v.decode('utf-8')
        return str(v)

    def _float_or_none(v):
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    result = {
        'collection_status': _str(h5_f.attrs.get('collection_status')),
        'is_resumed': bool(h5_f.attrs.get('is_resumed', False)),
        'segment_index': h5_f.attrs.get('segment_index', 1),
        'collection_session_id': _str(h5_f.attrs.get('collection_session_id') or h5_f.attrs.get('recording_session_id')),
        'interruption_id': _str(h5_f.attrs.get('interruption_id')),
        'resumed_by_segment_index': h5_f.attrs.get('resumed_by_segment_index', -1),
        'resumed_by_file': _str(h5_f.attrs.get('resumed_by_file')),
        'start_time': _float_or_none(h5_f.attrs.get('start_time')),
        'end_time': _float_or_none(h5_f.attrs.get('end_time')),
    }

    start = result['start_time']
    end = result['end_time']
    result['duration'] = round(end - start, 1) if (start and end) else None

    # session/stage
    result['session_info'] = {
        'session_index': h5_f.attrs.get('session_index'),
        'session_number': h5_f.attrs.get('session_number'),
        'session_count': h5_f.attrs.get('session_count'),
        'is_multi_session': bool(h5_f.attrs.get('is_multi_session', False)),
    }
    result['stage_info'] = {
        'stage_index': h5_f.attrs.get('stage_index'),
        'stage_name': _str(h5_f.attrs.get('stage_name')),
        'task_id': _str(h5_f.attrs.get('task_id')),
        'user_id': _str(h5_f.attrs.get('user_id')),
    }

    # EMG frame range
    for dev in ('emg1', 'emg2'):
        result[f'{dev}_range'] = {
            'frame_count': h5_f.attrs.get(f'{dev}_frame_count', 0),
            'frame_id_min': h5_f.attrs.get(f'{dev}_frame_id_min', -1),
            'frame_id_max': h5_f.attrs.get(f'{dev}_frame_id_max', -1),
            'time_min': _float_or_none(h5_f.attrs.get(f'{dev}_time_min')),
            'time_max': _float_or_none(h5_f.attrs.get(f'{dev}_time_max')),
        }

    # bin info
    result['bin_info'] = {}
    for dev_label in ('dev1', 'dev2'):
        info = {}
        for key, attr in [('sd_bin', f'sd_bin_{dev_label}'),
                          ('ble_device', f'ble_device_{dev_label}'),
                          ('has_bin', f'segment_has_{dev_label}_bin')]:
            val = h5_f.attrs.get(attr)
            info[key] = _str(val) if val is not None else None
        info['has_bin'] = bool(h5_f.attrs.get(f'segment_has_{dev_label}_bin', False))
        result['bin_info'][dev_label] = info

    result['segment_device_count'] = h5_f.attrs.get('segment_device_count', 0)

    # 【新增】stream mode 信息（preview/collection 切流方案）
    stream_mode = _str(h5_f.attrs.get('stream_mode'))
    stream_fmt_ver = h5_f.attrs.get('stream_format_version')
    bin_pair_source = _str(h5_f.attrs.get('bin_pair_source'))
    result['stream_info'] = {
        'stream_mode': stream_mode or 'unknown',  # "collection" | "preview" | "idle" | "unknown"
        'stream_format_version': int(stream_fmt_ver) if stream_fmt_ver is not None else 1,
        'bin_pair_source': bin_pair_source or 'unknown',  # "collection_stream" | "preview_stream" | "legacy" | "unknown"
        'collection_stream_id': _str(h5_f.attrs.get('collection_stream_id')),
        'stream_switch_delay_ms': h5_f.attrs.get('stream_switch_delay_ms'),
        'timestamp_to_start_delay_ms': h5_f.attrs.get('timestamp_to_start_delay_ms'),
        'collection_stream_stopped_at': _str(h5_f.attrs.get('collection_stream_stopped_at')),
        'collection_bin_finalized': _str(h5_f.attrs.get('collection_bin_finalized')),
        'preview_bin_dev1': _str(h5_f.attrs.get('preview_bin_dev1')),
        'preview_bin_dev2': _str(h5_f.attrs.get('preview_bin_dev2')),
        'sd_imu_bin_dev1': _str(h5_f.attrs.get('sd_imu_bin_dev1')),
        'sd_imu_bin_dev2': _str(h5_f.attrs.get('sd_imu_bin_dev2')),
    }

    # segment_bin_summary JSON
    bin_summary_raw = _str(h5_f.attrs.get('segment_bin_summary'))
    if bin_summary_raw:
        try:
            result['bin_summary_parsed'] = json.loads(bin_summary_raw)
        except (json.JSONDecodeError, TypeError):
            result['bin_summary_parsed'] = None
            result['bin_summary_raw'] = bin_summary_raw
    else:
        result['bin_summary_parsed'] = None

    # abnormal detail
    if result['collection_status'] == 'abnormal_interrupted':
        progress_raw = _str(h5_f.attrs.get('resume_progress'))
        progress_parsed = None
        if progress_raw:
            try:
                progress_parsed = json.loads(progress_raw)
            except (json.JSONDecodeError, TypeError):
                pass
        result['abnormal_detail'] = {
            'interrupted_at': _str(h5_f.attrs.get('interrupted_at')),
            'interrupt_reason': _str(h5_f.attrs.get('interrupt_reason')),
            'resume_progress_raw': progress_raw,
            'resume_progress_parsed': progress_parsed,
        }
    else:
        result['abnormal_detail'] = None

    # resume detail
    if result['is_resumed']:
        result['resume_detail'] = {
            'resume_from_interrupted_at': _str(h5_f.attrs.get('resume_from_interrupted_at')),
            'resume_reason': _str(h5_f.attrs.get('resume_reason')),
            'resume_parent_recording_session_id': _str(h5_f.attrs.get('resume_parent_recording_session_id')),
            'parent_segment_index': h5_f.attrs.get('parent_segment_index'),
        }
    else:
        result['resume_detail'] = None

    return result


def scan_segment_chain(current_h5_path):
    """Phase 5: 扫描同目录下共享 collection_session_id 的 segment 链

    Args:
        current_h5_path: 当前打开的 H5 文件绝对路径

    Returns:
        list[dict]: 按 segment_index 排序的 segment 元数据列表，
                    每个元素是 extract_segment_metadata + file_name + file_path + sync_status
    """
    directory = os.path.dirname(current_h5_path)
    current_file = os.path.basename(current_h5_path)

    # helper: safe int
    def _safe_int(v, default=0):
        try:
            return int(v)
        except (ValueError, TypeError):
            return default

    # helper: safe str
    def _safe_str(v):
        if v is None:
            return None
        if isinstance(v, bytes):
            return v.decode('utf-8')
        return str(v)

    # 1) 读取当前文件的 collection_session_id
    sid = None
    try:
        with h5py.File(current_h5_path, 'r') as f:
            sid = _safe_str(f.attrs.get('collection_session_id') or
                            f.attrs.get('recording_session_id'))
    except Exception:
        pass

    if not sid:
        return []

    # 2) 扫描同目录 .h5 文件
    chain = []
    try:
        for fname in sorted(os.listdir(directory)):
            if not fname.endswith('.h5'):
                continue
            fpath = os.path.join(directory, fname)
            try:
                with h5py.File(fpath, 'r') as f:
                    f_sid = _safe_str(f.attrs.get('collection_session_id') or
                                      f.attrs.get('recording_session_id'))
                    if f_sid != sid:
                        continue
                    meta = extract_segment_metadata(f)
                    meta['file_name'] = fname
                    meta['file_path'] = fpath
                    meta['is_current'] = (fname == current_file)
                    # Phase 5 fix: read real sync_status
                    sync = _safe_str(f.attrs.get('sync_status'))
                    meta['sync_status'] = sync if sync else 'unknown'
                    chain.append(meta)
            except Exception:
                continue
    except Exception:
        pass

    # 3) 按 segment_index 排序（safe int）
    chain.sort(key=lambda m: (_safe_int(m.get('segment_index'), 0), m.get('file_name', '')))
    return chain


def format_segment_chain_summary(chain, current_path=None):
    """Phase 5: 格式化 segment 链摘要文本

    Args:
        chain: scan_segment_chain() 返回值
        current_path: 当前文件路径（用于标记 >>）

    Returns:
        str: 多行格式化摘要
    """
    def _safe_int(v, default=0):
        try:
            return int(v)
        except (ValueError, TypeError):
            return default

    if not chain:
        return "无 segment 链路（缺少 collection_session_id）"

    total = len(chain)
    abnormal_count = sum(1 for m in chain if m.get('collection_status') == 'abnormal_interrupted')
    resumed_count = sum(1 for m in chain if m.get('is_resumed'))
    completed_count = sum(1 for m in chain if m.get('collection_status') == 'completed')

    lines = [
        f"会话共有 {total} 个 segment，异常中断 {abnormal_count} 个，续采 {resumed_count} 个，完成 {completed_count} 个",
        "-" * 80,
    ]

    header = f"{'':3s} {'文件':40s} {'状态':14s} {'续采':4s} {'回合':6s} {'Stage':10s} {'Dev1 Bin':20s} {'Dev2 Bin':20s} {'sync':8s}"
    lines.append(header)
    lines.append("-" * 80)

    for m in chain:
        marker = ">> " if m.get('is_current') else "   "
        seg = _safe_int(m.get('segment_index'), 0)
        fname = m.get('file_name', '?')[:38]
        cs = m.get('collection_status', '?') or '?'
        resumed = 'Y' if m.get('is_resumed') else 'N'
        si = m.get('session_info', {})
        sess = f"{si.get('session_number','?')}/{si.get('session_count','?')}"
        stage = m.get('stage_info', {}).get('stage_name', '?') or '?'
        dev1_bin = (m.get('bin_info', {}).get('dev1', {}).get('sd_bin') or '-')[:18]
        dev2_bin = (m.get('bin_info', {}).get('dev2', {}).get('sd_bin') or '-')[:18]
        sync = m.get('sync_status', 'unknown') or 'unknown'

        tag = ''
        if cs == 'abnormal_interrupted':
            tag = ' [!]'
        elif cs == 'manual_stopped':
            tag = ' [M]'
        if m.get('is_resumed'):
            tag += ' [R]'

        lines.append(
            f"{marker}{seg:<2d} {fname:<40s} {cs+tag:<14s} {resumed:<4s} "
            f"{sess:<6s} {stage:<10s} {dev1_bin:<20s} {dev2_bin:<20s} {sync:<8s}"
        )

    lines.append("-" * 80)

    # 关系提示
    cur = next((m for m in chain if m.get('is_current')), None)
    if cur:
        if cur.get('collection_status') == 'abnormal_interrupted':
            resumed_by = _safe_int(cur.get('resumed_by_segment_index'), 0)
            if resumed_by > 0:
                lines.append(f">> 当前文件已被 segment {resumed_by} 续采")
            else:
                lines.append(">> 当前文件是异常中断段，尚未被续采")
        if cur.get('is_resumed'):
            parent = cur.get('resume_detail', {})
            if parent:
                pseg = parent.get('parent_segment_index', '?')
                lines.append(f">> 当前文件是续采段，父 segment={pseg}")

    return '\n'.join(lines)


def scan_breakpoints(storage_root):
    """Phase 6: 递归扫描 storage_root，找出所有 abnormal_interrupted 且未被续采的 H5

    Returns:
        list[dict]: 每个元素包含 file_path, recoverable, meta, summary 等字段
    """
    results = []
    for root, dirs, files in os.walk(storage_root):
        for fname in files:
            if not fname.endswith('.h5'):
                continue
            fpath = os.path.join(root, fname)
            try:
                with h5py.File(fpath, 'r') as f:
                    cs = f.attrs.get('collection_status')
                    if isinstance(cs, bytes):
                        cs = cs.decode('utf-8')
                    if cs != 'abnormal_interrupted':
                        continue

                    resumed_by = f.attrs.get('resumed_by_segment_index', -1)
                    try:
                        resumed_by = int(resumed_by)
                    except (ValueError, TypeError):
                        resumed_by = -1

                    resumed_file = f.attrs.get('resumed_by_file', '')
                    if isinstance(resumed_file, bytes):
                        resumed_file = resumed_file.decode('utf-8')
                    already_resumed = (resumed_by > 0) or bool(resumed_file)

                    # Phase 6 fix: prefer breakpoint_state (full), fallback to resume_progress (partial)
                    bp_raw = f.attrs.get('breakpoint_state') or f.attrs.get('resume_progress')
                    if isinstance(bp_raw, bytes):
                        bp_raw = bp_raw.decode('utf-8')
                    progress_parsed = None
                    has_config = False
                    has_full = False  # has collectionConfig + gesturesSnapshot
                    if bp_raw:
                        try:
                            progress_parsed = json.loads(bp_raw)
                            has_config = bool(progress_parsed.get('collectionConfig')) if isinstance(progress_parsed, dict) else False
                            has_full = has_config and bool(progress_parsed.get('gesturesSnapshot'))
                        except (json.JSONDecodeError, TypeError):
                            pass

                    recoverable = (not already_resumed) and progress_parsed is not None and has_config

                    seg_idx = f.attrs.get('segment_index', 1)
                    try:
                        seg_idx = int(seg_idx)
                    except (ValueError, TypeError):
                        seg_idx = 1

                    meta_raw = extract_segment_metadata(f)
                    entry = {
                        'file_path': fpath,
                        'file_name': fname,
                        'directory': root,
                        'collection_status': cs,
                        'segment_index': seg_idx,
                        'resumed_by_segment_index': resumed_by,
                        'resumed_by_file': resumed_file,
                        'already_resumed': already_resumed,
                        'recoverable': recoverable,
                        'has_config': has_config,
                        'progress_parsed': progress_parsed,
                        'progress_raw': bp_raw,
                        'meta': meta_raw,
                        'summary': _bp_summary_line(meta_raw, progress_parsed, seg_idx, recoverable, already_resumed),
                    }
                    results.append(entry)
            except Exception:
                continue

    results.sort(key=lambda r: (r['file_path'], r.get('segment_index', 1)))
    return results


def _bp_summary_line(meta, progress, seg_idx, recoverable, already_resumed):
    """Phase 6 helper: single-line string summary for breakpoint list display"""
    user = meta.get('stage_info', {}).get('user_id', '?') or '?'
    task = meta.get('stage_info', {}).get('task_id', '?') or '?'
    stage = meta.get('stage_info', {}).get('stage_name', '?') or '?'
    si = meta.get('session_info', {})
    sess = f"{si.get('session_number','?')}/{si.get('session_count','?')}"

    gi = progress.get('currentGestureIndex', '?') if progress else '?'
    reason = meta.get('abnormal_detail', {})
    int_reason = (reason.get('interrupt_reason') if reason else None) or '?'
    int_at = (reason.get('interrupted_at') if reason else None) or '?'

    status = 'RECOVERABLE' if recoverable else ('RESUMED' if already_resumed else 'DIAGNOSE_ONLY')
    return (f"[{status}] seg={seg_idx} user={user} task={task} sess={sess} stage={stage} "
            f"gesture={gi} reason={int_reason} at={int_at}")


def generate_breakpoint_json(h5_path):
    """Phase 6: 从 abnormal_interrupted H5 生成前端兼容的 breakpoint state JSON

    Returns:
        dict with keys: json_str, recoverable, warnings
    """
    try:
        with h5py.File(h5_path, 'r') as f:
            cs = f.attrs.get('collection_status')
            if isinstance(cs, bytes):
                cs = cs.decode('utf-8')
            if cs != 'abnormal_interrupted':
                return {'json_str': None, 'recoverable': False, 'warnings': ['not abnormal_interrupted']}

            # Phase 6 fix: prefer breakpoint_state (full), fallback to resume_progress
            raw = f.attrs.get('breakpoint_state') or f.attrs.get('resume_progress')
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8')
            if not raw:
                return {'json_str': None, 'recoverable': False, 'warnings': ['no breakpoint_state or resume_progress']}

            try:
                progress = json.loads(raw)
            except Exception:
                return {'json_str': None, 'recoverable': False, 'warnings': ['breakpoint_state parse failed']}

            # Build breakpoint state
            bp = {
                'version': 1,
                'status': 'abnormal_interrupted',
                'interruptedAt': f.attrs.get('interrupted_at') or progress.get('interruptedAt', ''),
                'interruptReason': f.attrs.get('interrupt_reason') or progress.get('interruptReason', ''),
                'currentTaskId': progress.get('currentTaskId', f.attrs.get('task_id', 'discrete_gesture')),
                'currentSessionIndex': progress.get('currentSessionIndex', f.attrs.get('session_index', 0)),
                'currentStageIndex': progress.get('currentStageIndex', 0),
                'currentGestureIndex': progress.get('currentGestureIndex', 0),
                'gestureRepeatCount': progress.get('gestureRepeatCount', 0),
                'continualTrialCount': progress.get('continualTrialCount', 0),
                'currentPhase': progress.get('currentPhase', 'gesture'),
                '_shuffleMode': progress.get('_shuffleMode', False),
                'segmentIndex': int(f.attrs.get('segment_index', 1)),
                'isAllSessionsMode': progress.get('isAllSessionsMode', False),
                'sessionCount': progress.get('sessionCount', f.attrs.get('session_count', 3)),
                'recordingSessionId': f.attrs.get('recording_session_id', ''),
                'stages': progress.get('stages', []),
                'gesturesSnapshot': progress.get('gesturesSnapshot', []),
                'collectionConfig': progress.get('collectionConfig', {}),
                'source_h5_path': h5_path,
            }

            # Fix types
            for k in ('interruptedAt', 'interruptReason', 'currentTaskId', 'recordingSessionId'):
                if isinstance(bp[k], bytes):
                    bp[k] = bp[k].decode('utf-8')

            has_config = bool(bp.get('collectionConfig'))
            has_snapshot = bool(bp.get('gesturesSnapshot'))
            recoverable = has_config
            warnings = []
            if not has_config:
                warnings.append('缺少 collectionConfig，仅可诊断')
            if not has_snapshot:
                warnings.append('缺少 gesturesSnapshot，恢复后手势库可能不完整')

            bp['collectionConfig'] = bp.get('collectionConfig') or {}
            bp['gesturesSnapshot'] = bp.get('gesturesSnapshot') or []

            return {'json_str': json.dumps(bp, indent=2, ensure_ascii=False),
                    'recoverable': recoverable,
                    'warnings': warnings,
                    'bp': bp}
    except Exception as e:
        return {'json_str': None, 'recoverable': False, 'warnings': [str(e)]}


class StatisticsPanel(QFrame):
    """统计信息面板"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        self.setFrameStyle(QFrame.StyledPanel)
        layout = QGridLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        self.labels = {}
        # 定义字段及其颜色分组
        # 颜色方案：
        #   - 基本信息：蓝色 #0066cc
        #   - 同步状态：动态（synced绿色，pending橙色）
        #   - Session相关：紫色 #9933cc
        #   - SD卡bin索引：绿色 #009900
        #   - BLE设备：深蓝色 #0066cc 加粗
        #   - 数据集形状：蓝色 #0066cc
        stats = [
            # 基本文件信息
            ('文件名', '文件名'), ('文件大小', '文件大小'),
            ('创建时间', '创建时间'), ('sync_status', '同步状态'),
            # Session相关（紫色）
            ('session_index', 'Session索引'), ('session_count', 'Session总数'),
            ('recording_session_id', '录制会话ID'), ('is_multi_session', '多Session'),
            # 任务信息
            ('task_id', '任务ID'), ('user_id', '用户ID'),
            ('stage_name', 'Stage名称'), ('template_name', '模板名称'),
            # EMG数据集
            ('emg1_250hz', 'EMG1 250Hz'), ('emg1_2khz', 'EMG1 2kHz'),
            ('emg2_250hz', 'EMG2 250Hz'), ('emg2_2khz', 'EMG2 2kHz'),
            # IMU数据集（每设备2个IMU传感器）
            ('imu1a_ble', 'IMU1A BLE'), ('imu1a_100hz', 'IMU1A 100Hz'),
            ('imu1b_ble', 'IMU1B BLE'), ('imu1b_100hz', 'IMU1B 100Hz'),
            ('imu2a_ble', 'IMU2A BLE'), ('imu2a_100hz', 'IMU2A 100Hz'),
            ('imu2b_ble', 'IMU2B BLE'), ('imu2b_100hz', 'IMU2B 100Hz'),
            # V1/V2 通用IMU数据集（可变IMU数量）
            ('imu1_all_ble', 'IMU1 All BLE'), ('imu2_all_ble', 'IMU2 All BLE'),
            # V2 设备版本元数据
            ('imu1_hw_version', 'IMU1 硬件版本'), ('imu2_hw_version', 'IMU2 硬件版本'),
            ('imu1_num_imus', 'IMU1 IMU数量'), ('imu2_num_imus', 'IMU2 IMU数量'),
            ('total_imu1_all_frames', 'IMU1 All帧数'), ('total_imu2_all_frames', 'IMU2 All帧数'),
            ('mocap', 'Mocap'),
            # SD卡bin文件索引（绿色）
            ('sd_bin_dev1', 'SD Bin(设备1)'), ('sd_bin_dev2', 'SD Bin(设备2)'),
            # BLE设备名称
            ('ble_device_dev1', 'BLE设备(设备1)'), ('ble_device_dev2', 'BLE设备(设备2)'),
            # ===== Preview/Collection 流信息 =====
            ('stream_mode', '流模式'), ('stream_format_version', '流格式版本'),
            ('bin_pair_source', 'Bin来源'), ('collection_stream_id', '采集流ID'),
            ('stream_switch_delay_ms', '切换延迟(ms)'),
            # ===== Phase 4: segment/bin 元数据 =====
            ('collection_status', '采集状态'),
            ('is_resumed', '续采段'),
            ('segment_index', 'Segment序号'),
            ('collection_session_id', '会话ID'),
            ('parent_segment_index', '父Segment'),
            ('start_time', '开始时间'),
            ('end_time', '结束时间'),
            ('duration', '持续(秒)'),
            ('session_number', '轮次编号'),
            ('stage_index', 'Stage序号'),
            ('emg1_frame_count', 'EMG1帧数'),
            ('emg1_frame_range', 'EMG1帧号范围'),
            ('emg2_frame_count', 'EMG2帧数'),
            ('emg2_frame_range', 'EMG2帧号范围'),
            ('segment_has_dev1_bin', 'Dev1有Bin'),
            ('segment_has_dev2_bin', 'Dev2有Bin'),
            ('segment_device_count', '设备数'),
        ]

        # Session相关字段（紫色）
        session_keys = {'session_index', 'session_count', 'recording_session_id', 'is_multi_session',
                        'session_number'}
        # SD卡bin索引字段（绿色）
        sd_bin_keys = {'sd_bin_dev1', 'sd_bin_dev2'}
        # Phase 4: 状态颜色映射
        self._status_keys = {'collection_status', 'is_resumed', 'segment_index'}

        for i, (key, name) in enumerate(stats):
            row = i // 2
            col = (i % 2) * 2
            name_label = QLabel(f'{name}:')
            name_label.setMinimumHeight(20)
            name_label.setStyleSheet('font-weight: bold; color: #495057;')
            value_label = QLabel('-')
            value_label.setMinimumHeight(20)
            # 根据字段类型设置不同颜色
            if key == 'sync_status':
                value_label.setStyleSheet('color: #666;')  # 同步状态初始灰色，动态更新
            elif key in session_keys:
                value_label.setStyleSheet('color: #9933cc; font-weight: bold;')  # Session相关紫色
            elif key in sd_bin_keys:
                value_label.setStyleSheet('color: #009900; font-weight: bold;')  # bin文件名绿色
            elif key.startswith('ble_device'):
                value_label.setStyleSheet('color: #0066cc; font-weight: bold;')  # BLE设备名蓝色加粗
            else:
                value_label.setStyleSheet('color: #0066cc;')
            layout.addWidget(name_label, row, col)
            layout.addWidget(value_label, row, col + 1)
            self.labels[key] = value_label

        # Phase 5: segment chain summary
        chain_label = QLabel('Segment 链路:')
        chain_label.setStyleSheet('font-weight: bold; color: #7c3aed; font-size: 12px; padding-top: 10px;')
        layout.addWidget(chain_label, row + 1, 0, 1, 4)
        self.chain_text = QTextEdit()
        self.chain_text.setReadOnly(True)
        self.chain_text.setMaximumHeight(120)
        self.chain_text.setFont(QFont('Consolas', 7))
        self.chain_text.setStyleSheet('background: #f8f9fa; border: 1px solid #e5e7eb;')
        layout.addWidget(self.chain_text, row + 2, 0, 1, 4)

    def update_stats(self, file_path):
        try:
            import time
            self.labels['文件名'].setText(os.path.basename(file_path))
            size = os.path.getsize(file_path)
            self.labels['文件大小'].setText(f"{size / 1024 / 1024:.2f} MB")
            mtime = os.path.getmtime(file_path)
            self.labels['创建时间'].setText(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime)))

            with h5py.File(file_path, 'r') as f:
                # 读取同步状态
                sync_status = f.attrs.get('sync_status', 'unknown')
                if isinstance(sync_status, bytes):
                    sync_status = sync_status.decode('utf-8')
                self.labels['sync_status'].setText(sync_status)
                # 根据状态设置颜色
                if sync_status == 'synced':
                    self.labels['sync_status'].setStyleSheet('color: #009900; font-weight: bold;')  # 绿色
                elif sync_status == 'pending':
                    self.labels['sync_status'].setStyleSheet('color: #ff6600; font-weight: bold;')  # 橙色
                else:
                    self.labels['sync_status'].setStyleSheet('color: #666;')

                # 读取Session相关字段（紫色）
                session_index = f.attrs.get('session_index', None)
                if session_index is not None:
                    self.labels['session_index'].setText(str(session_index))
                else:
                    self.labels['session_index'].setText('-')

                session_count = f.attrs.get('session_count', None)
                if session_count is not None:
                    self.labels['session_count'].setText(str(session_count))
                else:
                    self.labels['session_count'].setText('-')

                recording_session_id = f.attrs.get('recording_session_id', None)
                if recording_session_id:
                    if isinstance(recording_session_id, bytes):
                        recording_session_id = recording_session_id.decode('utf-8')
                    self.labels['recording_session_id'].setText(str(recording_session_id))
                else:
                    self.labels['recording_session_id'].setText('-')

                is_multi_session = f.attrs.get('is_multi_session', None)
                if is_multi_session is not None:
                    self.labels['is_multi_session'].setText('是' if is_multi_session else '否')
                else:
                    self.labels['is_multi_session'].setText('-')

                # 读取任务信息字段
                task_id = f.attrs.get('task_id', None)
                if task_id:
                    if isinstance(task_id, bytes):
                        task_id = task_id.decode('utf-8')
                    self.labels['task_id'].setText(str(task_id))
                else:
                    self.labels['task_id'].setText('-')

                user_id = f.attrs.get('user_id', None)
                if user_id:
                    if isinstance(user_id, bytes):
                        user_id = user_id.decode('utf-8')
                    self.labels['user_id'].setText(str(user_id))
                else:
                    self.labels['user_id'].setText('-')

                stage_name = f.attrs.get('stage_name', None)
                if stage_name:
                    if isinstance(stage_name, bytes):
                        stage_name = stage_name.decode('utf-8')
                    self.labels['stage_name'].setText(str(stage_name))
                else:
                    self.labels['stage_name'].setText('-')

                template_name = f.attrs.get('template_name', None)
                if template_name:
                    if isinstance(template_name, bytes):
                        template_name = template_name.decode('utf-8')
                    self.labels['template_name'].setText(str(template_name))
                else:
                    self.labels['template_name'].setText('-')

                # 读取SD卡bin文件名（绿色）
                sd_bin_dev1 = f.attrs.get('sd_bin_dev1', None)
                if sd_bin_dev1:
                    if isinstance(sd_bin_dev1, bytes):
                        sd_bin_dev1 = sd_bin_dev1.decode('utf-8')
                    self.labels['sd_bin_dev1'].setText(sd_bin_dev1)
                else:
                    self.labels['sd_bin_dev1'].setText('-')

                sd_bin_dev2 = f.attrs.get('sd_bin_dev2', None)
                if sd_bin_dev2:
                    if isinstance(sd_bin_dev2, bytes):
                        sd_bin_dev2 = sd_bin_dev2.decode('utf-8')
                    self.labels['sd_bin_dev2'].setText(sd_bin_dev2)
                else:
                    self.labels['sd_bin_dev2'].setText('-')

                # 读取BLE设备名称
                ble_device_dev1 = f.attrs.get('ble_device_dev1', None)
                if ble_device_dev1:
                    if isinstance(ble_device_dev1, bytes):
                        ble_device_dev1 = ble_device_dev1.decode('utf-8')
                    self.labels['ble_device_dev1'].setText(ble_device_dev1)
                else:
                    self.labels['ble_device_dev1'].setText('-')

                ble_device_dev2 = f.attrs.get('ble_device_dev2', None)
                if ble_device_dev2:
                    if isinstance(ble_device_dev2, bytes):
                        ble_device_dev2 = ble_device_dev2.decode('utf-8')
                    self.labels['ble_device_dev2'].setText(ble_device_dev2)
                else:
                    self.labels['ble_device_dev2'].setText('-')

                # 【新增】读取 stream 信息
                stream_mode = f.attrs.get('stream_mode', None)
                if stream_mode:
                    if isinstance(stream_mode, bytes):
                        stream_mode = stream_mode.decode('utf-8')
                    self.labels['stream_mode'].setText(str(stream_mode))
                else:
                    self.labels['stream_mode'].setText('unknown (旧格式)')

                stream_fmt_ver = f.attrs.get('stream_format_version', None)
                if stream_fmt_ver is not None:
                    label_text = f"v{stream_fmt_ver}"
                    if int(stream_fmt_ver) >= 2:
                        label_text += ' (一对一)'
                    else:
                        label_text += ' (旧格式/多对一)'
                    self.labels['stream_format_version'].setText(label_text)
                else:
                    self.labels['stream_format_version'].setText('v1 (旧格式)')

                bin_pair_source = f.attrs.get('bin_pair_source', None)
                if bin_pair_source:
                    if isinstance(bin_pair_source, bytes):
                        bin_pair_source = bin_pair_source.decode('utf-8')
                    self.labels['bin_pair_source'].setText(str(bin_pair_source))
                else:
                    self.labels['bin_pair_source'].setText('unknown (旧格式)')

                collection_stream_id = f.attrs.get('collection_stream_id', None)
                if collection_stream_id:
                    if isinstance(collection_stream_id, bytes):
                        collection_stream_id = collection_stream_id.decode('utf-8')
                    # 缩短显示
                    self.labels['collection_stream_id'].setText(str(collection_stream_id)[:26])
                else:
                    self.labels['collection_stream_id'].setText('-')

                stream_delay = f.attrs.get('stream_switch_delay_ms', None)
                if stream_delay is not None:
                    self.labels['stream_switch_delay_ms'].setText(str(stream_delay))
                else:
                    self.labels['stream_switch_delay_ms'].setText('-')

                # 读取数据集形状
                for key in ['emg1_250hz', 'emg1_2khz', 'emg2_250hz', 'emg2_2khz',
                           'imu1a_ble', 'imu1a_100hz', 'imu1b_ble', 'imu1b_100hz',
                           'imu2a_ble', 'imu2a_100hz', 'imu2b_ble', 'imu2b_100hz']:
                    adc_key = key.replace('hz', 'hz_adc')
                    if adc_key in f:
                        self.labels[key].setText(str(f[adc_key].shape))
                    elif key in f:
                        self.labels[key].setText(str(f[key].shape))
                    else:
                        self.labels[key].setText('-')

                # V1/V2 通用IMU数据集形状
                for key in ['imu1_all_ble', 'imu2_all_ble']:
                    if key in f:
                        self.labels[key].setText(str(f[key].shape))
                    else:
                        self.labels[key].setText('-')

                # V2 设备版本元数据
                for attr_name in ['imu1_hw_version', 'imu2_hw_version',
                                  'imu1_num_imus', 'imu2_num_imus']:
                    val = f.attrs.get(attr_name, None)
                    if val is not None:
                        if isinstance(val, bytes):
                            val = val.decode('utf-8')
                        self.labels[attr_name].setText(str(val))
                    else:
                        self.labels[attr_name].setText('-')

                # V2 统计信息
                for attr_name in ['total_imu1_all_frames', 'total_imu2_all_frames']:
                    val = f.attrs.get(attr_name, None)
                    if val is not None:
                        self.labels[attr_name].setText(str(val))
                    else:
                        self.labels[attr_name].setText('-')

                if 'mocap' in f:
                    self.labels['mocap'].setText(str(f['mocap'].shape))
                else:
                    self.labels['mocap'].setText('-')

                # ===== Phase 4: segment/bin 元数据 =====
                meta = extract_segment_metadata(f)

                # collection_status with color
                cs = meta['collection_status'] or 'unknown'
                self.labels['collection_status'].setText(cs)
                if cs == 'abnormal_interrupted':
                    self.labels['collection_status'].setStyleSheet('color: #dc2626; font-weight: bold;')
                elif cs == 'manual_stopped':
                    self.labels['collection_status'].setStyleSheet('color: #f97316; font-weight: bold;')
                elif cs == 'completed':
                    self.labels['collection_status'].setStyleSheet('color: #16a34a; font-weight: bold;')
                elif cs == 'running':
                    self.labels['collection_status'].setStyleSheet('color: #3b82f6;')
                else:
                    self.labels['collection_status'].setStyleSheet('color: #666;')

                self.labels['is_resumed'].setText('是' if meta['is_resumed'] else '否')
                if meta['is_resumed']:
                    self.labels['is_resumed'].setStyleSheet('color: #7c3aed; font-weight: bold;')
                else:
                    self.labels['is_resumed'].setStyleSheet('color: #0066cc;')

                seg_idx = meta['segment_index']
                self.labels['segment_index'].setText(str(seg_idx))
                if seg_idx > 1:
                    self.labels['segment_index'].setStyleSheet('color: #7c3aed; font-weight: bold;')
                else:
                    self.labels['segment_index'].setStyleSheet('color: #0066cc;')

                self.labels['collection_session_id'].setText(meta['collection_session_id'] or '-')
                self.labels['parent_segment_index'].setText(
                    str(meta['resume_detail']['parent_segment_index']) if meta['resume_detail'] and meta['resume_detail'].get('parent_segment_index') is not None else '-'
                )

                # time
                if meta['start_time']:
                    self.labels['start_time'].setText(datetime.fromtimestamp(meta['start_time']).strftime('%H:%M:%S'))
                else:
                    self.labels['start_time'].setText('-')
                if meta['end_time']:
                    self.labels['end_time'].setText(datetime.fromtimestamp(meta['end_time']).strftime('%H:%M:%S'))
                else:
                    self.labels['end_time'].setText('-')
                self.labels['duration'].setText(f"{meta['duration']}s" if meta['duration'] is not None else '-')

                # session
                si = meta['session_info']
                self.labels['session_number'].setText(
                    f"{si['session_number']}/{si['session_count']}" if si['session_number'] is not None else '-'
                )
                self.labels['stage_index'].setText(str(meta['stage_info']['stage_index']) if meta['stage_info']['stage_index'] is not None else '-')

                # EMG frame range
                for dev in ('emg1', 'emg2'):
                    r = meta[f'{dev}_range']
                    count = r.get('frame_count', 0)
                    self.labels[f'{dev}_frame_count'].setText(str(count))
                    if count > 0:
                        self.labels[f'{dev}_frame_range'].setText(
                            f"[{r['frame_id_min']}, {r['frame_id_max']}]"
                        )
                    else:
                        self.labels[f'{dev}_frame_range'].setText('-')

                # bin has
                self.labels['segment_has_dev1_bin'].setText(
                    '✅' if meta['bin_info']['dev1']['has_bin'] else '❌'
                )
                self.labels['segment_has_dev2_bin'].setText(
                    '✅' if meta['bin_info']['dev2']['has_bin'] else '❌'
                )
                self.labels['segment_device_count'].setText(str(meta['segment_device_count']))

                # ===== Phase 5: segment 链路 =====
                chain = scan_segment_chain(file_path)
                summary = format_segment_chain_summary(chain, file_path)
                if hasattr(self, 'chain_text'):
                    self.chain_text.setPlainText(summary)
                else:
                    debug_log(f"segment chain ({len(chain)} files)")

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
        self.stats_panel.setMaximumHeight(840)  # Phase 5 新增 segment 链路展示
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
        has_imu_index = 'imu_index' in dtype.names     # V2 IMU_ALL_BLE_DTYPE
        has_has_mag_flag = 'has_mag' in dtype.names     # V2 IMU_ALL_BLE_DTYPE

        # 构建表头
        headers = ['帧序号']
        if has_imu_index:
            headers.append('IMU索引')
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
        if has_has_mag_flag:
            headers.append('Has Mag')
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

            # IMU索引 (V2 IMU_ALL_BLE_DTYPE)
            if has_imu_index:
                item = QTableWidgetItem(str(row['imu_index']))
                item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(QColor(240, 240, 255))  # 浅紫背景
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

            # 磁力计 (V2 has_mag=0 时显示 "-")
            if has_mag:
                row_has_mag = row['has_mag'] if has_has_mag_flag else 1
                for j, val in enumerate(row['mag']):
                    if row_has_mag == 0:
                        item = QTableWidgetItem('-')
                        item.setForeground(QColor(180, 180, 180))  # 灰色
                    else:
                        item = QTableWidgetItem(f'{val:.6f}')
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.data_table.setItem(i, col, item)
                    col += 1

            # Has Mag 标志 (V2 IMU_ALL_BLE_DTYPE)
            if has_has_mag_flag:
                item = QTableWidgetItem(str(row['has_mag']))
                item.setTextAlignment(Qt.AlignCenter)
                if row['has_mag'] == 0:
                    item.setBackground(QColor(255, 240, 240))
                else:
                    item.setBackground(QColor(240, 255, 240))
                self.data_table.setItem(i, col, item)
                col += 1

            # 时间戳
            if has_time:
                item = QTableWidgetItem(f'{row["time"]:.9f}')
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.data_table.setItem(i, col, item)

            # 文本预览
            parts = []
            if has_imu_index:
                parts.append(f'IMU[{row["imu_index"]}]')
            if has_frame_id:
                parts.append(f'BLE={row["frame_id"]}')
            if has_sd_frame_id:
                parts.append(f'SD={row["sd_frame_id"]}')
            if has_acc:
                parts.append(f'Acc=[{row["acc"][0]:8.4f}, {row["acc"][1]:8.4f}, {row["acc"][2]:8.4f}]')
            if has_gyr and gyr_key:
                parts.append(f'Gyr=[{row[gyr_key][0]:8.4f}, {row[gyr_key][1]:8.4f}, {row[gyr_key][2]:8.4f}]')
            if has_mag:
                row_has_mag = row['has_mag'] if has_has_mag_flag else 1
                if row_has_mag == 0:
                    parts.append('Mag=[  -,    -,    -  ]')
                else:
                    parts.append(f'Mag=[{row["mag"][0]:8.4f}, {row["mag"][1]:8.4f}, {row["mag"][2]:8.4f}]')
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
        self.bin_dir = None  # 改为存储bin目录路径
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

        # Bin目录选择（改为选择目录而非单个文件）
        bin_group = QGroupBox("SD卡Bin文件目录")
        bin_layout = QVBoxLayout(bin_group)

        # 说明文字
        hint_label = QLabel("同步时将根据H5文件中的sd_bin_dev1/dev2属性自动查找对应的bin文件")
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: #666; font-size: 10px; padding: 5px;")
        bin_layout.addWidget(hint_label)

        # Bin目录选择
        bin_dir_layout = QHBoxLayout()
        dir_label = QLabel("Bin目录:")
        dir_label.setFixedWidth(70)
        bin_dir_layout.addWidget(dir_label)
        self.bin_dir_label = QLabel("未选择")
        self.bin_dir_label.setStyleSheet("color: gray; padding: 5px; background: #f8f9fa; border: 1px solid #ddd;")
        self.bin_dir_label.setWordWrap(True)
        self.bin_dir_btn = QPushButton("选择目录...")
        self.bin_dir_btn.setFixedWidth(90)
        self.bin_dir_btn.clicked.connect(self.select_bin_dir)
        self.bin_dir_btn.setStyleSheet("""
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
        bin_dir_layout.addWidget(self.bin_dir_label, 1)
        bin_dir_layout.addWidget(self.bin_dir_btn)
        bin_layout.addLayout(bin_dir_layout)

        left_layout.addWidget(bin_group)

        # 同步选项
        options_group = QGroupBox("同步选项")
        options_layout = QVBoxLayout(options_group)

        self.emg1_check = QCheckBox("EMG1 (emg1_250hz → emg1_2khz)")
        self.emg1_check.setChecked(True)
        self.emg2_check = QCheckBox("EMG2 (emg2_250hz → emg2_2khz)")
        self.emg2_check.setChecked(True)
        # 【修改】每个设备有2个IMU传感器（A和B）
        self.imu1_check = QCheckBox("IMU1 A+B (imu1a/1b_ble → 100hz)")
        self.imu1_check.setChecked(True)
        self.imu2_check = QCheckBox("IMU2 A+B (imu2a/2b_ble → 100hz)")
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

    def select_bin_dir(self):
        """选择bin文件所在目录"""
        dir_path = QFileDialog.getExistingDirectory(
            self, "选择Bin文件所在目录"
        )
        if dir_path:
            self.bin_dir = dir_path
            # 显示目录名（如果路径太长则截断）
            display_path = dir_path
            if len(display_path) > 50:
                display_path = "..." + display_path[-47:]
            self.bin_dir_label.setText(display_path)
            self.bin_dir_label.setStyleSheet("color: #009900; padding: 5px; background: #f0fff0; border: 1px solid #90EE90;")
            self.bin_dir_label.setToolTip(dir_path)

            # 统计目录中的bin文件数量
            emg_count = len([f for f in os.listdir(dir_path) if f.endswith('_emg.bin')])
            imu_count = len([f for f in os.listdir(dir_path) if f.endswith('_imu.bin')])
            self.log_text.append(f"已选择目录: {dir_path}")
            self.log_text.append(f"  找到 {emg_count} 个EMG bin文件, {imu_count} 个IMU bin文件")

    def start_sync(self):
        if not self.h5_files:
            QMessageBox.warning(self, "警告", "请先添加H5文件")
            return
        if not self.bin_dir:
            QMessageBox.warning(self, "警告", "请选择Bin文件所在目录")
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
        self.log_text.append(f"开始同步... (bin目录: {self.bin_dir})")

        self.worker = SyncWorker(
            self.h5_files, self.bin_dir,
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


class BreakpointTab(QWidget):
    """Phase 6: 历史断点管理标签页"""

    def __init__(self):
        super().__init__()
        self.breakpoints = []
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # ---- top bar ----
        top = QHBoxLayout()
        scan_btn = QPushButton("扫描历史断点")
        scan_btn.clicked.connect(self.scan)
        scan_btn.setStyleSheet("font-weight: bold; padding: 10px 20px; background: #dc2626; color: white; border: none; border-radius: 6px;")
        top.addWidget(scan_btn)
        top.addStretch()
        self.count_label = QLabel("未扫描")
        top.addWidget(self.count_label)
        layout.addLayout(top)

        # ---- list ----
        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setSelectionMode(QListWidget.SingleSelection)
        layout.addWidget(self.list_widget)

        # ---- detail ----
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(150)
        self.detail.setFont(QFont("Consolas", 8))
        layout.addWidget(self.detail)

        # ---- buttons ----
        btn_row = QHBoxLayout()
        self.export_btn = QPushButton("导出断点恢复 JSON")
        self.export_btn.clicked.connect(self.export_json)
        self.export_btn.setEnabled(False)
        btn_row.addWidget(self.export_btn)

        self.copy_btn = QPushButton("复制 JSON 到剪贴板")
        self.copy_btn.clicked.connect(self.copy_json)
        self.copy_btn.setEnabled(False)
        btn_row.addWidget(self.copy_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # connect selection
        self.list_widget.itemSelectionChanged.connect(self.on_selection_changed)

    def scan(self):
        directory = QFileDialog.getExistingDirectory(self, "选择 storage 根目录扫描断点")
        if not directory:
            return
        self.breakpoints = scan_breakpoints(directory)
        self.list_widget.clear()
        recoverable_count = 0
        for bp in self.breakpoints:
            if bp['recoverable']:
                marker = "[RECOVERABLE]"
                color = QColor(0x16, 0xa3, 0x4a)
                recoverable_count += 1
            elif bp['already_resumed']:
                marker = "[RESUMED]"
                color = QColor(0x7c, 0x3a, 0xed)
            elif bp.get('progress_parsed'):
                marker = "[OLD_FMT]"
                color = QColor(0xf5, 0x9e, 0x0b)
            else:
                marker = "[DIAG_ONLY]"
                color = QColor(0xf9, 0x73, 0x16)
            item = QListWidgetItem(f"{marker} {bp['file_name']}")
            item.setData(Qt.UserRole, bp)
            item.setForeground(color)
            self.list_widget.addItem(item)
        self.count_label.setText(f"共 {len(self.breakpoints)} 个异常中断 H5，可恢复 {recoverable_count} 个")

    def on_selection_changed(self):
        items = self.list_widget.selectedItems()
        if not items:
            self.export_btn.setEnabled(False)
            self.copy_btn.setEnabled(False)
            self.detail.clear()
            return
        bp = items[0].data(Qt.UserRole)
        self.export_btn.setEnabled(bp['recoverable'])
        self.copy_btn.setEnabled(bp['recoverable'])

        # build detail text
        m = bp['meta']
        p = bp.get('progress_parsed', {}) or {}
        lines = [
            f"文件: {bp['file_path']}",
            f"状态: {bp['collection_status']}  segment_index={bp['segment_index']}",
            f"可恢复: {'是' if bp['recoverable'] else '否'}  已续采: {'是' if bp['already_resumed'] else '否'}",
        ]
        if bp.get('progress_parsed') and not bp['recoverable'] and not bp['already_resumed']:
            lines.append("注意: 旧格式断点，缺少 breakpoint_state/collectionConfig，仅可诊断")
        lines.extend([
            f"用户: {m.get('stage_info',{}).get('user_id','?')}  任务: {m.get('stage_info',{}).get('task_id','?')}",
            f"轮次: {m.get('session_info',{}).get('session_number','?')}/{m.get('session_info',{}).get('session_count','?')}",
            f"Stage: {m.get('stage_info',{}).get('stage_name','?')}",
        ])
        if p:
            lines.append(f"手势进度: {p.get('currentGestureIndex','?')}  乱序: {p.get('_shuffleMode','?')}")
        if bp.get('resumed_by_file'):
            lines.append(f"已被续采: segment {bp['resumed_by_segment_index']} ({bp['resumed_by_file']})")
        self.detail.setPlainText('\n'.join(lines))

    def export_json(self):
        items = self.list_widget.selectedItems()
        if not items:
            return
        bp = items[0].data(Qt.UserRole)
        result = generate_breakpoint_json(bp['file_path'])
        if not result['json_str']:
            QMessageBox.warning(self, "导出失败", '; '.join(result.get('warnings', [])))
            return

        default_name = os.path.splitext(bp['file_name'])[0] + '.breakpoint.json'
        save_path, _ = QFileDialog.getSaveFileName(self, "导出断点恢复 JSON", default_name, "JSON Files (*.json)")
        if not save_path:
            return
        with open(save_path, 'w', encoding='utf-8') as f:
            f.write(result['json_str'])
        QMessageBox.information(self, "导出成功", f"已保存到:\n{save_path}")

    def copy_json(self):
        items = self.list_widget.selectedItems()
        if not items:
            return
        bp = items[0].data(Qt.UserRole)
        result = generate_breakpoint_json(bp['file_path'])
        if not result['json_str']:
            QMessageBox.warning(self, "复制失败", '; '.join(result.get('warnings', [])))
            return
        QApplication.clipboard().setText(result['json_str'])
        QMessageBox.information(self, "已复制", "JSON 已复制到剪贴板")


class SyncToolsTab(QWidget):
    """Phase 1-3: 同步工具标签页 — 诊断、清除、重新同步"""

    def __init__(self):
        super().__init__()
        self.current_h5 = None
        self.worker = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # file selection
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("H5 文件:"))
        self.file_label = QLabel("未选择")
        self.file_label.setStyleSheet("color: #666;")
        file_row.addWidget(self.file_label, 1)
        select_btn = QPushButton("选择...")
        select_btn.clicked.connect(self.select_file)
        file_row.addWidget(select_btn)
        layout.addLayout(file_row)

        # diagnose area
        diag_group = QGroupBox("同步诊断")
        diag_layout = QVBoxLayout(diag_group)
        self.diag_text = QTextEdit()
        self.diag_text.setReadOnly(True)
        self.diag_text.setMaximumHeight(180)
        self.diag_text.setFont(QFont("Consolas", 8))
        diag_layout.addWidget(self.diag_text)
        diag_btn = QPushButton("诊断同步风险")
        diag_btn.clicked.connect(self.run_diagnose)
        diag_layout.addWidget(diag_btn)
        layout.addWidget(diag_group)

        # actions
        action_group = QGroupBox("操作")
        action_layout = QVBoxLayout(action_group)
        btn_row = QHBoxLayout()
        self.clear_btn = QPushButton("清除同步结果")
        self.clear_btn.clicked.connect(self.run_clear)
        self.clear_btn.setEnabled(False)
        btn_row.addWidget(self.clear_btn)
        self.resync_btn = QPushButton("清除并重新同步")
        self.resync_btn.clicked.connect(self.run_resync)
        self.resync_btn.setEnabled(False)
        self.resync_btn.setStyleSheet("font-weight: bold; background: #f97316; color: white;")
        btn_row.addWidget(self.resync_btn)
        action_layout.addLayout(btn_row)

        self.bin_label = QLabel("Bin 目录: 将根据 H5 attrs 自动查找，或手动选择")
        self.bin_label.setStyleSheet("color: #666; font-size: 10px;")
        action_layout.addWidget(self.bin_label)
        bin_sel_row = QHBoxLayout()
        self.bin_dir_btn = QPushButton("手动选择 Bin 目录...")
        self.bin_dir_btn.clicked.connect(self.select_bin_dir)
        bin_sel_row.addWidget(self.bin_dir_btn)
        bin_sel_row.addStretch()
        action_layout.addLayout(bin_sel_row)
        layout.addWidget(action_group)

        # log
        log_group = QGroupBox("操作日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 8))
        self.log_text.setStyleSheet("background: #1e1e1e; color: #d4d4d4;")
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group)

    def log(self, msg):
        self.log_text.append(msg)

    def select_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 H5 文件", "", "HDF5 Files (*.h5 *.hdf5)")
        if path:
            self.current_h5 = path
            self.file_label.setText(os.path.basename(path))
            self.file_label.setToolTip(path)
            self.clear_btn.setEnabled(True)
            self.resync_btn.setEnabled(True)
            self.run_diagnose()

    def select_bin_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择 Bin 文件所在目录")
        if d:
            self.bin_dir = d
            self.bin_label.setText(f"Bin 目录: {d}")
            self.bin_label.setToolTip(d)

    def run_diagnose(self):
        if not self.current_h5:
            return
        self.diag_text.clear()
        self.log_text.clear()
        self.log("正在诊断...")
        try:
            res = diagnose_frame_ids(self.current_h5)
            lines = [
                f"sync_status: {res.get('sync_status','?')}",
                f"has_2khz: {res.get('has_2khz_any')}",
                f"sd_bin_dev1: {res.get('bin_dev1') or '-'}",
                f"sd_bin_dev2: {res.get('bin_dev2') or '-'}",
            ]
            for dev in ('emg1', 'emg2'):
                d = res.get(dev, {})
                risk_icon = {'ok': 'OK', 'medium': 'WARN', 'high': 'HIGH RISK'}.get(d.get('risk', 'ok'), '?')
                lines.append(f"")
                lines.append(f"--- {dev} ---")
                lines.append(f"  250Hz frames: {d.get('count', 0)}")
                lines.append(f"  2kHz dataset: {'YES' if d.get('has_2khz') else 'NO'}")
                lines.append(f"  duplicates: {d.get('duplicates', 0)}  gaps: {d.get('gap_count', 0)}")
                lines.append(f"  monotonic: {d.get('is_monotonic', '?')}  overlap_ratio: {d.get('overlap_ratio', 1.0)}")
                lines.append(f"  RISK: {risk_icon}  {d.get('risk_reason', '')}")

            # sync history audit
            with h5py.File(self.current_h5, 'r') as f:
                lines.append("")
                lines.append("--- sync history ---")
                for key, label in [('last_sync_attempt_time', '上次同步尝试'), ('last_sync_success_time', '上次同步成功'),
                                   ('last_sync_clear_time', '上次清除'), ('last_sync_error_time', '上次同步错误')]:
                    v = f.attrs.get(key)
                    lines.append(f"  {label}: {v if v else '-'}")
                for key, label in [('sync_attempt_count', '同步次数'), ('sync_clear_count', '清除次数')]:
                    v = f.attrs.get(key, 0)
                    lines.append(f"  {label}: {v}")
                raw = f.attrs.get('sync_history')
                if raw:
                    try:
                        if isinstance(raw, bytes):
                            raw = raw.decode('utf-8')
                        hist = json.loads(raw)
                        recent = hist[-3:] if len(hist) > 3 else hist
                        lines.append(f"  最近 {len(recent)}/{len(hist)} 条记录:")
                        for h in recent:
                            lines.append(f"    {h.get('time','?')[:19]} {h.get('action','?')}/{h.get('status','?')}")
                    except Exception:
                        lines.append("  sync_history: invalid format")
                else:
                    lines.append("  sync_history: -")

            self.diag_text.setPlainText('\n'.join(lines))
            self.log("诊断完成")
        except Exception as e:
            self.log(f"诊断失败: {e}")
            self.diag_text.setPlainText(f"ERROR: {e}")

    def run_clear(self):
        if not self.current_h5:
            return
        reply = QMessageBox.warning(self, "确认清除",
            "将清除此 H5 中的同步产物:\n"
            "- 删除 emg1_2khz_adc / emg2_2khz_adc\n"
            "- 删除 imu*_100hz\n"
            "- 清除 sync_status / sync_time / sync_error\n"
            "- 保留 250Hz 原始数据不变\n\n"
            "清除前会自动备份 (.bak_时间戳)。\n确定继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        self.log("正在清除同步产物...")
        QApplication.processEvents()
        try:
            res = clear_sync_outputs(self.current_h5, backup=True)
            if res['success']:
                self.log(f"清除成功。备份: {res.get('backup_path', 'N/A')}")
                self.log(f"已删除 datasets: {res['removed_datasets']}")
                self.log(f"已清除 attrs: {res['removed_attrs']}")
            else:
                self.log(f"清除失败: {res['errors']}")
            self.run_diagnose()  # refresh
        except Exception as e:
            self.log(f"清除异常: {e}")

    def run_resync(self):
        if not self.current_h5:
            return

        # ---- Phase 1: read H5 attrs and resolve bin directory ----
        with h5py.File(self.current_h5, 'r') as f:
            cm = f.attrs.get('channel_map_name', 'V2')
            if isinstance(cm, bytes):
                cm = cm.decode('utf-8')
            dev_prefixes = {}
            for dev_id in (1, 2):
                p = f.attrs.get(f'sd_bin_dev{dev_id}')
                if isinstance(p, bytes):
                    p = p.decode('utf-8')
                dev_prefixes[dev_id] = p

        bin_dir = getattr(self, 'bin_dir', None)
        if not bin_dir:
            h5_dir = os.path.dirname(self.current_h5)
            found_any = False
            for dev_id, prefix in dev_prefixes.items():
                if prefix and os.path.exists(os.path.join(h5_dir, f'{prefix}_emg.bin')):
                    found_any = True
                    break
            if found_any:
                bin_dir = h5_dir
                self.log(f"自动使用 H5 同目录作为 bin 目录: {bin_dir}")
            elif any(dev_prefixes.values()):
                QMessageBox.warning(self, "未找到 bin 文件",
                    f"H5 记录了 sd_bin_dev1/2，但在 H5 同目录下未找到对应 emg bin。\n请先手动选择 bin 目录。")
                return
            else:
                QMessageBox.warning(self, "缺少 bin 信息",
                    "H5 中未找到 sd_bin_dev1/sd_bin_dev2 attrs，无法定位 bin 文件。\n请先手动选择 bin 目录。")
                return

        # ---- Phase 2: strict pre-check ----
        # H5 声明的每个设备对应的 emg bin 都必须存在，否则不清除
        sync_jobs = []
        missing = []
        for dev_id in (1, 2):
            prefix = dev_prefixes.get(dev_id)
            if not prefix:
                continue
            emg_bin = os.path.join(bin_dir, f'{prefix}_emg.bin')
            if not os.path.exists(emg_bin):
                missing.append(f"dev{dev_id}: {os.path.basename(emg_bin)}")
                continue
            imu_bin = os.path.join(bin_dir, f'{prefix}_imu.bin')
            sync_jobs.append((dev_id, emg_bin, imu_bin if os.path.exists(imu_bin) else None))

        if missing:
            self.log("ERROR: H5 声明了以下设备但 emg bin 缺失，不执行清除:")
            for m in missing:
                self.log(f"  {m}")
            QMessageBox.warning(self, "bin 文件不完整",
                "H5 已声明以下设备但 emg bin 文件缺失:\n" + '\n'.join(missing) +
                "\n\n请确认 bin 目录正确。所有必需 bin 文件存在后才能重新同步。")
            return

        if not sync_jobs:
            self.log("ERROR: H5 没有记录任何 sd_bin_dev，无法同步")
            return

        self.log(f"预检查通过: 可同步 {len(sync_jobs)} 个设备")
        for dev_id, emg, imu in sync_jobs:
            self.log(f"  dev{dev_id}: {os.path.basename(emg)}" + (f" + {os.path.basename(imu)}" if imu else ""))
            if not imu:
                self.log(f"    (IMU bin 缺失，仅同步 EMG)")

        reply = QMessageBox.warning(self, "确认重新同步",
            f"将先清除旧同步产物（自动备份），再对 {len(sync_jobs)} 个设备重新同步。\n确定继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        self.log("=== 开始重新同步 ===")
        QApplication.processEvents()

        # ---- Phase 3: clear ----
        self.log("清除旧同步产物...")
        cres = clear_sync_outputs(self.current_h5, backup=True)
        if not cres['success']:
            self.log(f"清除失败: {cres['errors']}")
            return
        self.log(f"已备份: {cres.get('backup_path')}")

        # ---- Phase 4: re-sync (all set_synced=False, final status set manually) ----
        self.log("调用新版同步...")
        QApplication.processEvents()
        try:
            results = []
            for dev_id, emg_bin, imu_bin in sync_jobs:
                self.log(f"  dev{dev_id}: {os.path.basename(emg_bin)}")
                QApplication.processEvents()

                r = sync_h5_with_bin(
                    self.current_h5, emg_bin, imu_bin,
                    device_id=dev_id, verify=True,
                    set_synced=False,  # 不自动写 synced
                    channel_map_name=cm or 'V2',
                )
                results.append((dev_id, r))
                status = r.get('status', '?')
                if status == 'success':
                    self.log(f"    EMG: {r.get('frames_2khz', 0)} frames")
                    if r.get('imu_status') == 'success':
                        self.log(f"    IMU: {r.get('imu_frames', 0)} frames")
                elif status == 'skipped':
                    self.log(f"    已同步，跳过")
                else:
                    self.log(f"    失败: {r.get('reason', '?')}")

            # 最终状态：全部成功才写 synced
            all_ok = all(r.get('status') == 'success' for _, r in results)
            if all_ok:
                with h5py.File(self.current_h5, 'a') as f:
                    f.attrs['sync_status'] = 'synced'
                    f.attrs['sync_time'] = datetime.now().isoformat()
                    append_sync_history(f, action='resync', status='synced',
                                        details={'devices': len(results)})
                self.log("=== 重新同步完成，sync_status=synced ===")
            elif results:
                failures = [f"dev{did}: {r.get('reason','?')}" for did, r in results if r.get('status') != 'success']
                with h5py.File(self.current_h5, 'a') as f:
                    f.attrs['sync_status'] = 'sync_failed'
                    f.attrs['sync_time'] = datetime.now().isoformat()
                    f.attrs['sync_error'] = '; '.join(failures)
                    append_sync_history(f, action='resync', status='sync_failed',
                                        details={'failures': failures})
                self.log("=== 重新同步失败，sync_status=sync_failed ===")
                for fl in failures:
                    self.log(f"  失败: {fl}")
            else:
                self.log("=== 未同步任何设备 ===")

            self.run_diagnose()
        except Exception as e:
            self.log(f"重新同步异常: {e}")
            # 尝试将 H5 标记为 sync_failed
            try:
                with h5py.File(self.current_h5, 'a') as f:
                    f.attrs['sync_status'] = 'sync_failed'
                    f.attrs['sync_time'] = datetime.now().isoformat()
                    f.attrs['sync_error'] = f"resync exception: {e}"
                    append_sync_history(f, action='resync', status='exception',
                                        details={'error': str(e)})
                self.log("已将 sync_status 标记为 sync_failed")
            except Exception as we:
                self.log(f"写入 sync_failed 状态失败: {we}")
            self.run_diagnose()


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

        # 文件数量和全选按钮
        count_layout = QHBoxLayout()
        self.file_count_label = QLabel("共 0 个文件")
        self.file_count_label.setStyleSheet("color: #666;")
        count_layout.addWidget(self.file_count_label)
        count_layout.addStretch()

        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.setFixedWidth(60)
        self.select_all_btn.clicked.connect(self.select_all_files)
        self.select_all_btn.setStyleSheet("""
            QPushButton {
                padding: 4px 8px;
                background: #e9ecef;
                border: 1px solid #ccc;
                border-radius: 3px;
                font-size: 11px;
            }
            QPushButton:hover {
                background: #dee2e6;
            }
        """)
        count_layout.addWidget(self.select_all_btn)
        file_layout.addLayout(count_layout)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QListWidget.ExtendedSelection)  # 支持多选
        self.file_list.itemClicked.connect(self.on_file_selected)
        self.file_list.itemDoubleClicked.connect(self.on_file_double_clicked)
        self.file_list.itemSelectionChanged.connect(self.on_selection_changed)  # 选择变化事件
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

        self.breakpoint_tab = BreakpointTab()
        self.tabs.addTab(self.breakpoint_tab, "历史断点")

        self.sync_tools_tab = SyncToolsTab()
        self.tabs.addTab(self.sync_tools_tab, "同步工具")

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

    def select_all_files(self):
        """全选/取消全选文件列表"""
        if self.file_list.count() == 0:
            return

        # 检查是否已经全选
        all_selected = len(self.file_list.selectedItems()) == self.file_list.count()

        if all_selected:
            # 取消全选
            self.file_list.clearSelection()
            self.select_all_btn.setText("全选")
        else:
            # 全选
            self.file_list.selectAll()
            self.select_all_btn.setText("取消全选")

    def on_selection_changed(self):
        """选择变化时更新按钮状态和文字"""
        selected_count = len(self.file_list.selectedItems())
        total_count = self.file_list.count()

        # 更新全选按钮文字
        if selected_count == total_count and total_count > 0:
            self.select_all_btn.setText("取消全选")
        else:
            self.select_all_btn.setText("全选")

        if selected_count > 0:
            self.view_btn.setEnabled(True)
            self.add_to_sync_btn.setEnabled(True)
            if selected_count == 1:
                self.add_to_sync_btn.setText("添加到同步列表")
            else:
                self.add_to_sync_btn.setText(f"添加 {selected_count} 个文件到同步列表")
        else:
            self.view_btn.setEnabled(False)
            self.add_to_sync_btn.setEnabled(False)
            self.add_to_sync_btn.setText("添加到同步列表")

    def on_file_double_clicked(self, item):
        file_path = item.data(Qt.UserRole)
        if file_path:
            self.viewer_tab.load_file(file_path)
            self.tabs.setCurrentIndex(0)

    def view_selected_file(self):
        items = self.file_list.selectedItems()
        if items:
            # 查看第一个选中的文件
            file_path = items[0].data(Qt.UserRole)
            self.viewer_tab.load_file(file_path)
            self.tabs.setCurrentIndex(0)

    def add_to_sync_list(self):
        """批量添加选中的文件到同步列表"""
        items = self.file_list.selectedItems()
        if items:
            file_paths = [item.data(Qt.UserRole) for item in items]
            self.sync_tab.add_files_from_list(file_paths)
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
