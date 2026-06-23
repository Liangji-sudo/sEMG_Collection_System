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
    QAbstractItemView, QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QSplitter, QLabel, QPushButton,
    QFileDialog, QGroupBox, QTextEdit, QTabWidget, QHeaderView,
    QMessageBox, QListWidget, QListWidgetItem, QProgressBar,
    QCheckBox, QSpinBox, QComboBox, QFrame, QScrollArea, QGridLayout
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
                               sync_h5_one_to_one, sync_h5_one_to_many_adc_search,
                               find_bin_offset_by_adc,
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

    def __init__(self, h5_files, bin_dir, devices, validate_data, sync_mode='one_to_one'):
        super().__init__()
        self.h5_files = h5_files
        self.bin_dir = bin_dir
        self.devices = devices
        self.validate_data = validate_data
        self.sync_mode = sync_mode  # 'one_to_one' | 'one_to_many' | 'legacy'

    def _find_bin_files(self, h5_path, device_id):
        """
        从H5文件属性中读取bin文件前缀，在目录（含子目录）中查找对应的bin文件

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

                # 在 bin_dir 及子目录中搜索
                emg_bin_name = f"{bin_prefix}_emg.bin"
                imu_bin_name = f"{bin_prefix}_imu.bin"

                emg_path = None
                imu_path = None
                for root, dirs, files in os.walk(self.bin_dir):
                    for f in files:
                        if f == emg_bin_name:
                            emg_path = os.path.join(root, f)
                        elif f == imu_bin_name:
                            imu_path = os.path.join(root, f)
                        if emg_path and imu_path:
                            break
                    if emg_path and imu_path:
                        break

                return (emg_path, imu_path)

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

                        if self.sync_mode == 'one_to_one':
                            # 新格式：一个 H5 对一对 collection bin
                            self.log.emit(f"    [one_to_one] bin_offset=0, auto_anchor")
                            result = sync_h5_one_to_one(
                                h5_path=h5_file,
                                emg_bin_path=emg_bin,
                                imu_bin_path=imu_bin,
                                device_id=device_id,
                                verify=self.validate_data,
                                set_synced=is_last_device,
                            )
                        elif self.sync_mode == 'one_to_many':
                            # 旧格式：ADC 搜索 offset
                            result = sync_h5_one_to_many_adc_search(
                                h5_path=h5_file,
                                emg_bin_path=emg_bin,
                                imu_bin_path=imu_bin,
                                device_id=device_id,
                                verify=self.validate_data,
                                set_synced=is_last_device,
                            )
                        else:
                            # legacy 兼容
                            self.log.emit(f"    [legacy] using sync_h5_with_bin")
                            result = sync_h5_with_bin(
                                h5_path=h5_file,
                                emg_bin_path=emg_bin,
                                imu_bin_path=imu_bin,
                                device_id=device_id,
                                verify=self.validate_data,
                                set_synced=is_last_device,
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
    """统计信息面板 — 可滚动、分组展示"""

    # 字段分组定义 — 按设备类型分门别类
    SECTIONS = [
        ('H5 基础信息', [
            ('文件名', '文件名'), ('文件大小', '文件大小'),
            ('创建时间', '创建时间'), ('sync_status', '同步状态'),
            ('collection_status', '采集状态'),
            ('is_resumed', '续采段'), ('segment_index', 'Segment序号'),
            ('session_index', 'Session索引'), ('session_count', 'Session总数'),
            ('session_number', '当前轮次'),
            ('recording_session_id', '录制会话ID'), ('is_multi_session', '多Session'),
            ('task_id', '任务ID'), ('user_id', '用户ID'),
            ('stage_name', 'Stage名称'), ('stage_index', 'Stage序号'),
            ('template_name', '模板名称'),
            ('collection_session_id', '采集会话ID'),
            ('parent_segment_index', '父Segment'),
            ('start_time', '开始时间'), ('end_time', '结束时间'),
            ('duration', '持续(秒)'),
            ('segment_device_count', '设备数'),
        ]),
        ('蓝牙手环 — 设备1', [
            ('ble_device_dev1', 'BLE设备名称'),
            ('sd_bin_dev1', 'SD Bin文件'),
            ('segment_has_dev1_bin', '含Bin数据'),
            ('emg1_250hz', 'EMG 250Hz'), ('emg1_2khz', 'EMG 2kHz'),
            ('emg1_frame_count', 'EMG采集帧数'), ('emg1_frame_range', 'EMG帧号范围'),
            ('imu1a_ble', 'IMU-A BLE'), ('imu1a_100hz', 'IMU-A 100Hz'),
            ('imu1b_ble', 'IMU-B BLE'), ('imu1b_100hz', 'IMU-B 100Hz'),
            ('imu1c_100hz', 'IMU-C 100Hz'),
            ('imu1_all_ble', 'IMU All BLE'), ('total_imu1_all_frames', 'IMU总帧数'),
            ('imu1_hw_version', '硬件版本'), ('imu1_num_imus', 'IMU传感器数'),
        ]),
        ('蓝牙手环 — 设备2', [
            ('ble_device_dev2', 'BLE设备名称'),
            ('sd_bin_dev2', 'SD Bin文件'),
            ('segment_has_dev2_bin', '含Bin数据'),
            ('emg2_250hz', 'EMG 250Hz'), ('emg2_2khz', 'EMG 2kHz'),
            ('emg2_frame_count', 'EMG采集帧数'), ('emg2_frame_range', 'EMG帧号范围'),
            ('imu2a_ble', 'IMU-A BLE'), ('imu2a_100hz', 'IMU-A 100Hz'),
            ('imu2b_ble', 'IMU-B BLE'), ('imu2b_100hz', 'IMU-B 100Hz'),
            ('imu2c_100hz', 'IMU-C 100Hz'),
            ('imu2_all_ble', 'IMU All BLE'), ('total_imu2_all_frames', 'IMU总帧数'),
            ('imu2_hw_version', '硬件版本'), ('imu2_num_imus', 'IMU传感器数'),
        ]),
        ('数据流信息', [
            ('stream_mode', '流模式'), ('stream_format_version', '流格式版本'),
            ('bin_pair_source', 'Bin配对来源'), ('collection_stream_id', '采集流ID'),
            ('stream_switch_delay_ms', '流切换延迟(ms)'),
        ]),
        ('相机', [
            ('video_left', '左手视频文件'), ('video_right', '右手视频文件'),
        ]),
        ('动捕', [
            ('mocap', 'Mocap数据集'),
        ]),
        ('同步信息', [
            ('sync_mode', '同步模式'),
            ('sync_bin_offset_dev1', 'Dev1 Bin偏移'),
            ('sync_bin_offset_dev2', 'Dev2 Bin偏移'),
            ('sync_offset_match_rate_dev1', 'Dev1 匹配率'),
            ('sync_offset_match_rate_dev2', 'Dev2 匹配率'),
            ('sync_frame_id_mode', 'FrameID模式'),
            ('sync_bin_offset_mode', 'Offset模式'),
            ('sync_time_alignment', '时间对齐方式'),
            ('sync_250hz_anchor_position', '250Hz锚点位置'),
        ]),
    ]

    # 颜色分组 key 集合
    _session_keys = {'session_index', 'session_count', 'recording_session_id', 'is_multi_session', 'session_number',
                     'collection_session_id'}
    _sd_bin_keys = {'sd_bin_dev1', 'sd_bin_dev2'}
    _status_keys = {'collection_status', 'is_resumed', 'segment_index'}
    _video_keys = {'video_left', 'video_right'}
    _mocap_keys = {'mocap'}
    _wristband_dev1_keys = {'ble_device_dev1', 'emg1_250hz', 'emg1_2khz', 'emg1_frame_count', 'emg1_frame_range',
                            'imu1a_ble', 'imu1a_100hz', 'imu1b_ble', 'imu1b_100hz', 'imu1c_100hz',
                            'imu1_all_ble', 'total_imu1_all_frames', 'imu1_hw_version', 'imu1_num_imus',
                            'segment_has_dev1_bin'}
    _wristband_dev2_keys = {'ble_device_dev2', 'emg2_250hz', 'emg2_2khz', 'emg2_frame_count', 'emg2_frame_range',
                            'imu2a_ble', 'imu2a_100hz', 'imu2b_ble', 'imu2b_100hz', 'imu2c_100hz',
                            'imu2_all_ble', 'total_imu2_all_frames', 'imu2_hw_version', 'imu2_num_imus',
                            'segment_has_dev2_bin'}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.labels = {}
        self.init_ui()

    def init_ui(self):
        self.setFrameStyle(QFrame.StyledPanel)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(2)
        scroll.setWidget(content)

        LABEL_FONT = 'font-size: 9pt;'

        first_section = True
        for section_title, fields in self.SECTIONS:
            # 区块间距（首个 section 不加）
            if not first_section:
                spacer = QWidget()
                spacer.setFixedHeight(10)
                spacer.setStyleSheet('background: transparent;')
                content_layout.addWidget(spacer)
            first_section = False

            # section header
            header = QLabel(section_title)
            header.setStyleSheet(f'font-weight: bold; color: #374151; {LABEL_FONT} padding-top: 3px; padding-bottom: 3px; border-bottom: 1px solid #e5e7eb;')
            header.setMinimumHeight(24)
            content_layout.addWidget(header)

            # grid for this section
            grid = QGridLayout()
            grid.setVerticalSpacing(2)
            grid.setHorizontalSpacing(10)
            grid.setContentsMargins(4, 2, 4, 2)

            for i, (key, name) in enumerate(fields):
                row = i // 2
                col = (i % 2) * 2
                name_label = QLabel(f'{name}:')
                name_label.setMinimumHeight(22)
                name_label.setStyleSheet(f'font-weight: bold; color: #6b7280; {LABEL_FONT}')
                value_label = QLabel('-')
                value_label.setMinimumHeight(22)
                value_label.setWordWrap(False)
                # colour — 按设备类别区分
                if key in self._sd_bin_keys:
                    value_label.setStyleSheet(f'color: #009900; font-weight: bold; {LABEL_FONT}')
                elif key in self._wristband_dev1_keys:
                    value_label.setStyleSheet(f'color: #2563eb; font-weight: bold; {LABEL_FONT}')
                elif key in self._wristband_dev2_keys:
                    value_label.setStyleSheet(f'color: #7c3aed; font-weight: bold; {LABEL_FONT}')
                elif key in self._video_keys:
                    value_label.setStyleSheet(f'color: #0891b2; font-weight: bold; {LABEL_FONT}')
                elif key in self._mocap_keys:
                    value_label.setStyleSheet(f'color: #d97706; font-weight: bold; {LABEL_FONT}')
                elif key in self._session_keys:
                    value_label.setStyleSheet(f'color: #6d28d9; {LABEL_FONT}')
                elif key == 'sync_status':
                    value_label.setStyleSheet(f'color: #666; {LABEL_FONT}')
                else:
                    value_label.setStyleSheet(f'color: #0066cc; {LABEL_FONT}')
                grid.addWidget(name_label, row, col)
                grid.addWidget(value_label, row, col + 1)
                self.labels[key] = value_label
            content_layout.addLayout(grid)

        # 采集链路 — Segment 链（与前一个 section 保持间距）
        chain_spacer = QWidget()
        chain_spacer.setFixedHeight(10)
        chain_spacer.setStyleSheet('background: transparent;')
        content_layout.addWidget(chain_spacer)

        chain_header = QLabel('采集链路 — Segment 链')
        chain_header.setStyleSheet(f'font-weight: bold; color: #0891b2; {LABEL_FONT} padding-top: 3px; padding-bottom: 3px; border-bottom: 1px solid #e5e7eb;')
        chain_header.setMinimumHeight(24)
        content_layout.addWidget(chain_header)
        self.chain_text = QTextEdit()
        self.chain_text.setReadOnly(True)
        self.chain_text.setMaximumHeight(160)
        self.chain_text.setFont(QFont('Consolas', 7))
        self.chain_text.setStyleSheet('background: #f8f9fa; border: 1px solid #e5e7eb;')
        content_layout.addWidget(self.chain_text)

        content_layout.addStretch()

    def update_stats(self, file_path):
        try:
            import time
            self.labels['文件名'].setText(os.path.basename(file_path))
            size = os.path.getsize(file_path)
            self.labels['文件大小'].setText(f"{size / 1024 / 1024:.2f} MB")
            mtime = os.path.getmtime(file_path)
            self.labels['创建时间'].setText(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime)))

            with h5py.File(file_path, 'r') as f:
                # 读取同步状态 — 中文显示 + 颜色
                sync_status = f.attrs.get('sync_status', 'unknown')
                if isinstance(sync_status, bytes):
                    sync_status = sync_status.decode('utf-8')
                SYNC_LABELS = {
                    'synced': '✅ 已同步', 'pending': '⏳ 待同步',
                    'syncing': '🔄 同步中', 'failed': '❌ 同步失败',
                    'unknown': '❓ 未知',
                }
                sync_label = SYNC_LABELS.get(sync_status, sync_status)
                self.labels['sync_status'].setText(sync_label)
                if sync_status == 'synced':
                    self.labels['sync_status'].setStyleSheet('color: #16a34a; font-weight: bold; font-size: 10pt;')
                elif sync_status == 'pending':
                    self.labels['sync_status'].setStyleSheet('color: #f97316; font-weight: bold; font-size: 10pt;')
                elif sync_status == 'syncing':
                    self.labels['sync_status'].setStyleSheet('color: #3b82f6; font-weight: bold; font-size: 10pt;')
                elif sync_status == 'failed':
                    self.labels['sync_status'].setStyleSheet('color: #dc2626; font-weight: bold; font-size: 10pt;')
                else:
                    self.labels['sync_status'].setStyleSheet('color: #9ca3af; font-size: 10pt;')

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

                # 读取视频文件名
                for key in ['video_left', 'video_right']:
                    val = f.attrs.get(key, None)
                    if val:
                        if isinstance(val, bytes):
                            val = val.decode('utf-8')
                        self.labels[key].setText(str(val))
                    else:
                        self.labels[key].setText('-')

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

                # 【新增】读取同步状态 attrs
                sync_mode = f.attrs.get('sync_mode', None)
                if sync_mode:
                    if isinstance(sync_mode, bytes): sync_mode = sync_mode.decode('utf-8')
                    self.labels['sync_mode'].setText(str(sync_mode))
                else:
                    self.labels['sync_mode'].setText('-')

                for dev_id in [1, 2]:
                    for key, label_key in [('sync_bin_offset_dev', 'sync_bin_offset_dev'),
                                           ('sync_offset_match_rate_dev', 'sync_offset_match_rate_dev')]:
                        val = f.attrs.get(f'{key}{dev_id}', None)
                        if val is not None:
                            if isinstance(val, float):
                                self.labels[f'{label_key}{dev_id}'].setText(f'{val:.4f}')
                            else:
                                self.labels[f'{label_key}{dev_id}'].setText(str(val))
                        else:
                            self.labels[f'{label_key}{dev_id}'].setText('-')

                for key in ['sync_frame_id_mode', 'sync_bin_offset_mode', 'sync_time_alignment']:
                    val = f.attrs.get(key, None)
                    if val:
                        if isinstance(val, bytes): val = val.decode('utf-8')
                        self.labels[key].setText(str(val))
                    else:
                        self.labels[key].setText('-')

                anchor_pos = f.attrs.get('sync_250hz_anchor_position', None)
                if anchor_pos is not None:
                    self.labels['sync_250hz_anchor_position'].setText(str(anchor_pos))
                else:
                    self.labels['sync_250hz_anchor_position'].setText('-')

                # 读取数据集形状
                for key in ['emg1_250hz', 'emg1_2khz', 'emg2_250hz', 'emg2_2khz',
                           'imu1a_ble', 'imu1a_100hz', 'imu1b_ble', 'imu1b_100hz', 'imu1c_100hz',
                           'imu2a_ble', 'imu2a_100hz', 'imu2b_ble', 'imu2b_100hz', 'imu2c_100hz']:
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

        # 主分割器 - 可拖动 (树 | 属性+预览)
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

        # 外层垂直分割器 — 统计面板 | 下方详情区（均可拖动）
        viewer_splitter = QSplitter(Qt.Vertical)
        viewer_splitter.setHandleWidth(5)
        viewer_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #ddd;
            }
            QSplitter::handle:hover {
                background-color: #aaa;
            }
        """)
        viewer_splitter.addWidget(self.stats_panel)

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

        # 右侧分割器（垂直）— 属性 | 数据预览
        right_splitter = QSplitter(Qt.Vertical)
        right_splitter.setHandleWidth(5)
        right_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #ddd;
            }
            QSplitter::handle:hover {
                background-color: #aaa;
            }
        """)

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

        main_splitter.addWidget(right_splitter)
        main_splitter.setSizes([300, 700])

        right_splitter.setSizes([150, 400])

        viewer_splitter.addWidget(main_splitter)
        viewer_splitter.setSizes([300, 500])

        layout.addWidget(viewer_splitter)

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
                if item.shape == ():  # 标量 dataset：直接显示值
                    val = item[()]
                    tree_item.setText(2, f"{val:.6g}" if isinstance(val, (int, float)) else str(val))
                else:
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
                elif isinstance(obj, h5py.Group) and (path == 'video_timing' or path.endswith('/video_timing')):
                    self._show_video_timing_group(f)
        except Exception as e:
            print(f"读取错误: {e}")

    def _show_video_timing_group(self, f):
        """显示 video_timing group 的汇总视图"""
        try:
            vt = f['video_timing']
            sides_raw = vt['sides'][()]
            firsts = vt['first_frame_unix'][()]
            lasts = vt['last_frame_unix'][()]
            durs = vt['duration'][()]

            # 确保都是1D数组
            sides = [s.decode('utf-8') if isinstance(s, bytes) else str(s)
                     for s in np.atleast_1d(sides_raw)]
            firsts = np.atleast_1d(firsts)
            lasts = np.atleast_1d(lasts)
            durs = np.atleast_1d(durs)

            n = len(sides)
            self.data_table.clear()
            self.data_table.setRowCount(n)
            self.data_table.setColumnCount(5)
            self.data_table.setHorizontalHeaderLabels([
                '摄像头', '视频首帧 Unix', '首帧时间', '视频末帧 Unix', '末帧时间'
            ])

            text_lines = [
                '【video_timing - 视频帧时间戳汇总】',
                '═' * 80,
                '  对应 H5 数据中 EMG/IMU 时间戳，用于视频帧与生理数据的时间对齐。',
                ''
            ]

            for i in range(n):
                side = sides[i]
                first = float(firsts[i])
                last = float(lasts[i])
                dur = float(durs[i])

                try:
                    first_str = datetime.fromtimestamp(first).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                except (ValueError, OSError):
                    first_str = 'N/A'
                try:
                    last_str = datetime.fromtimestamp(last).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                except (ValueError, OSError):
                    last_str = 'N/A'

                self.data_table.setItem(i, 0, self._make_cell(side, Qt.AlignCenter))
                self.data_table.setItem(i, 1, self._make_cell(f'{first:.3f}', Qt.AlignRight))
                self.data_table.setItem(i, 2, self._make_cell(first_str, Qt.AlignLeft))
                self.data_table.setItem(i, 3, self._make_cell(f'{last:.3f}', Qt.AlignRight))
                self.data_table.setItem(i, 4, self._make_cell(last_str, Qt.AlignLeft))

                text_lines.append(f'  📹 {side}侧摄像头:')
                text_lines.append(f'     首帧 Unix: {first:.3f}  →  {first_str}')
                text_lines.append(f'     末帧 Unix: {last:.3f}  →  {last_str}')
                text_lines.append(f'     视频时长:  {dur:.3f}s ({dur/60:.2f}min)')
                text_lines.append('')

            self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.text_view.setText('\n'.join(text_lines))

        except Exception as e:
            self.text_view.setText(f"video_timing 读取错误: {e}")

    def _make_cell(self, text, align=Qt.AlignLeft):
        """创建表格单元格"""
        item = QTableWidgetItem(text)
        item.setTextAlignment(align | Qt.AlignVCenter)
        return item

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
            elif 'video_timing' in path.lower():
                data_type = 'video_timing'

            # 根据数据类型选择显示方式
            if data_type == 'emg':
                self.show_emg_data(data, dtype, path)
            elif data_type == 'imu':
                self.show_imu_data(data, dtype, path)
            elif data_type == 'prompts':
                self.show_prompt_data(data, path)
            elif data_type == 'video_timing':
                self.show_video_timing_data(data, dtype, path)
            else:
                # 普通数组
                self.update_table_view(data, is_emg)
                self.update_text_view(data, is_emg)

            # 波形图
            self.waveform.plot_data(data, path.split('/')[-1])

        except Exception as e:
            self.text_view.setText(f"预览错误: {e}")

    def _get_h5_format_info(self):
        """读取当前 H5 的格式信息用于表头决策。返回 {stream_fmt_ver, bin_pair_source, sync_time_alignment}"""
        info = {'stream_fmt_ver': None, 'bin_pair_source': None, 'sync_time_alignment': None}
        if not self.current_file:
            return info
        try:
            with h5py.File(self.current_file, 'r') as f:
                v = f.attrs.get('stream_format_version')
                if v is not None: info['stream_fmt_ver'] = int(v)
                bp = f.attrs.get('bin_pair_source')
                if isinstance(bp, bytes): bp = bp.decode('utf-8')
                info['bin_pair_source'] = bp
                ta = f.attrs.get('sync_time_alignment')
                if isinstance(ta, bytes): ta = ta.decode('utf-8')
                info['sync_time_alignment'] = ta
        except Exception:
            pass
        return info

    def show_emg_data(self, data, dtype, path):
        """显示EMG结构化数据（智能表头：根据数据集类型和H5格式区分）"""
        precision = self.emg_precision
        max_rows = min(len(data), self.preview_rows)
        preview_data = data[:max_rows]

        fmt = self._get_h5_format_info()
        is_new = (fmt['stream_fmt_ver'] is not None and fmt['stream_fmt_ver'] >= 2)
        is_2khz = '_2khz' in path.lower()
        is_250hz = '_250hz' in path.lower()

        # format hint
        hint_text = ''
        if is_2khz:
            hint_text = '2kHz 数据: sd_frame_id 为同步后写入的 bin 帧号'
        elif is_250hz:
            if is_new:
                hint_text = '新格式 H5: 同步(一对一)使用 bin offset=0, 不依赖 250Hz frame_id'
            else:
                hint_text = '旧格式 H5: 250Hz frame_id/sd_frame_id 可能为历史推算值, 仅供诊断'

        text_lines = [
            f'【{path} - EMG数据预览 (前{max_rows}帧，共{len(data)}帧)】',
            f'精度: {precision}位小数',
        ]
        if hint_text:
            text_lines.append(f'  {hint_text}')
        text_lines.append('═' * 100)

        # 表格设置
        self.data_table.clear()
        self.data_table.setRowCount(max_rows)

        if 'channels' in dtype.names:
            n_channels = data['channels'].shape[1] if len(data['channels'].shape) > 1 else 16

            # 构建表头
            headers = ['行号']
            has_frame_id = 'frame_id' in dtype.names
            has_sd_frame_id = 'sd_frame_id' in dtype.names

            if has_frame_id:
                if is_new:
                    headers.append('BLE帧ID(诊断)')
                elif is_250hz:
                    headers.append('旧frame_id(不可信)')
                else:
                    headers.append('BLE帧ID')
            if has_sd_frame_id:
                if is_2khz:
                    headers.append('bin帧号')
                elif is_250hz:
                    headers.append('旧推算SD(不可信)' if not is_new else '推算SD(诊断)')
                else:
                    headers.append('推算SD')

            headers += [f'Ch{i}' for i in range(n_channels)]
            if 'time' in dtype.names:
                headers.append('时间戳')

            self.data_table.setColumnCount(len(headers))
            self.data_table.setHorizontalHeaderLabels(headers)

            for i, row in enumerate(preview_data):
                col = 0
                item = QTableWidgetItem(str(i))
                item.setTextAlignment(Qt.AlignCenter)
                self.data_table.setItem(i, col, item)
                col += 1

                if has_frame_id:
                    item = QTableWidgetItem(str(row['frame_id']))
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setBackground(QColor(230, 245, 255))
                    self.data_table.setItem(i, col, item)
                    col += 1

                if has_sd_frame_id:
                    item = QTableWidgetItem(str(row['sd_frame_id']))
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setBackground(QColor(255, 245, 230))
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
        is_100hz = '_100hz' in path.lower()
        is_ble = '_ble' in path.lower()

        headers = ['行号']
        if has_imu_index:
            headers.append('IMU索引')
        if has_frame_id:
            headers.append('BLE帧ID(诊断)')
        if has_sd_frame_id:
            if is_100hz:
                headers.append('IMU bin帧号')
            else:
                headers.append('推算SD(诊断)')
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

    def show_video_timing_data(self, data, dtype, path):
        """显示 video_timing 数据集（标量或小数组）"""
        ds_name = path.split('/')[-1]
        text_lines = [
            f'【video_timing / {ds_name}】',
            '═' * 60
        ]

        # 拉平数据用于表格显示
        flat = np.atleast_1d(np.asarray(data)).flatten()

        self.data_table.clear()
        self.data_table.setRowCount(len(flat))
        self.data_table.setColumnCount(2)
        self.data_table.setHorizontalHeaderLabels(['索引', ds_name])

        for i, val in enumerate(flat):
            item_idx = QTableWidgetItem(str(i))
            item_idx.setTextAlignment(Qt.AlignCenter)
            self.data_table.setItem(i, 0, item_idx)

            if isinstance(val, bytes):
                val_str = val.decode('utf-8', errors='replace')
            elif isinstance(val, np.floating):
                val_str = f'{val:.9f}'
            elif isinstance(val, (np.integer, int)):
                val_str = str(val)
            else:
                val_str = str(val)

            item_val = QTableWidgetItem(val_str)
            item_val.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.data_table.setItem(i, 1, item_val)

            # 时间戳格式化显示
            if 'unix' in ds_name.lower() and isinstance(val, (int, float, np.floating, np.integer)):
                try:
                    ts = float(val)
                    dt_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                    text_lines.append(f'  [{i}] {val_str}  →  {dt_str}')
                except (ValueError, OSError):
                    text_lines.append(f'  [{i}] {val_str}')
            else:
                text_lines.append(f'  [{i}] {val_str}')

        if 'duration' in ds_name.lower():
            text_lines.append(f'\n  ⏱ 视频时长: {flat[0]:.3f}s ({flat[0]/60:.2f}min)')

        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.text_view.setText('\n'.join(text_lines))

    def update_table_view(self, data, is_emg):
        """更新表格视图"""
        precision = self.emg_precision if is_emg else 4
        max_rows = self.preview_rows

        self.data_table.clear()

        if data.ndim == 0:
            # 标量 dataset
            val = data[()]
            self.data_table.setColumnCount(1)
            self.data_table.setHorizontalHeaderLabels(["Value"])
            self.data_table.setRowCount(1)
            if isinstance(val, (np.floating, float)):
                text = f"{val:.{precision}f}"
            else:
                text = str(val)
            self.data_table.setItem(0, 0, QTableWidgetItem(text))

        elif data.ndim == 1:
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

        if data.ndim == 0:
            # 标量 dataset
            val = data[()]
            if isinstance(val, (float, np.floating)):
                lines.append(f"Value: {val:.{precision}f}")
            else:
                lines.append(f"Value: {val}")
        elif np.issubdtype(data.dtype, np.number):
            lines.append(f"Min: {np.min(data):.{precision}f}")
            lines.append(f"Max: {np.max(data):.{precision}f}")
            lines.append(f"Mean: {np.mean(data):.{precision}f}")
            lines.append(f"Std: {np.std(data):.{precision}f}")
        lines.append("")
        lines.append("Data preview (first 20 rows):")
        lines.append("-" * 50)

        preview_data = data[:min(20, len(data))]
        if data.ndim == 0:
            lines.append(str(preview_data))
        elif data.ndim == 2:
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
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #ddd;
            }
            QSplitter::handle:hover {
                background-color: #aaa;
            }
        """)

        # 左侧：设置面板
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        # 提示
        hint = QLabel(
            "📋 适用于新采集数据：一个 H5 对应一对 collection bin。\n"
            "默认 bin_offset=0，通过 H5 attrs (sd_bin_dev1/dev2) 自动定位 bin。\n"
            "stream_format_version>=2 且 bin_pair_source=collection_stream 时推荐使用。\n"
            "旧格式数据（长 bin 多 H5）请使用\"同步（旧版本）\"标签页。"
        )
        hint.setStyleSheet("color: #1e40af; background: #e0e7ff; padding: 8px; border-radius: 6px; font-size: 11px;")
        hint.setWordWrap(True)
        left_layout.addWidget(hint)

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

        self.auto_bin_btn = QPushButton("自动查找")
        self.auto_bin_btn.setFixedWidth(75)
        self.auto_bin_btn.setToolTip("根据已添加H5文件的位置自动推测Bin目录")
        self.auto_bin_btn.clicked.connect(self._auto_detect_bin_dir)
        self.auto_bin_btn.setStyleSheet("""
            QPushButton {
                padding: 6px 10px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #10b981, stop:1 #059669);
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #34d399, stop:1 #10b981);
            }
            QPushButton:pressed {
                background: #047857;
            }
        """)
        bin_dir_layout.addWidget(self.auto_bin_btn)
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
        self.imu1_check = QCheckBox("IMU1 A/B/C (按设备IMU数量 → 100hz)")
        self.imu1_check.setChecked(True)
        self.imu2_check = QCheckBox("IMU2 A/B/C (按设备IMU数量 → 100hz)")
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
        if self.bin_dir:
            self._refresh_pairing_status()

    def add_files_from_list(self, files):
        for f in files:
            if f not in self.h5_files:
                self.h5_files.append(f)
                item = QListWidgetItem(os.path.basename(f))
                item.setToolTip(f)
                self.h5_list.addItem(item)
        self.h5_count_label.setText(f"共 {len(self.h5_files)} 个文件")
        if self.bin_dir:
            self._refresh_pairing_status()

    def clear_h5_files(self):
        self.h5_files.clear()
        self.h5_list.clear()
        self.h5_count_label.setText("共 0 个文件")
        self.h5_count_label.setStyleSheet("color: #666;")

    def _check_bin_pairing(self, file_path, bin_dir, device_id=1):
        """检查单个 H5 文件与 bin 目录的配对状态

        Returns:
            tuple: (status_str, emg_path_or_None, imu_path_or_None)
                status: 'paired' | 'missing_emg' | 'missing_imu' | 'no_attr' | 'synced'
        """
        try:
            with h5py.File(file_path, 'r') as f:
                # 已同步的跳过
                sync_st = f.attrs.get('sync_status', 'unknown')
                if isinstance(sync_st, bytes):
                    sync_st = sync_st.decode('utf-8')
                if sync_st == 'synced':
                    return ('synced', None, None)

                # 读取 bin 前缀
                attr_name = f'sd_bin_dev{device_id}'
                bin_prefix = f.attrs.get(attr_name, None)
                if bin_prefix is None:
                    return ('no_attr', None, None)
                if isinstance(bin_prefix, bytes):
                    bin_prefix = bin_prefix.decode('utf-8')

                # 在 bin_dir 及子目录中搜索
                search_dirs = [bin_dir]
                try:
                    for entry in os.listdir(bin_dir):
                        full = os.path.join(bin_dir, entry)
                        if os.path.isdir(full):
                            search_dirs.append(full)
                except OSError:
                    pass

                emg_found = None
                imu_found = None
                emg_name = f"{bin_prefix}_emg.bin"
                imu_name = f"{bin_prefix}_imu.bin"

                for d in search_dirs:
                    if emg_found is None:
                        p = os.path.join(d, emg_name)
                        if os.path.exists(p):
                            emg_found = p
                    if imu_found is None:
                        p = os.path.join(d, imu_name)
                        if os.path.exists(p):
                            imu_found = p
                    if emg_found and imu_found:
                        break

                if emg_found:
                    return ('paired', emg_found, imu_found)
                else:
                    return ('missing_emg', None, None)
        except Exception:
            return ('error', None, None)

    def _auto_detect_bin_dir(self):
        """自动推测 Bin 目录：从 H5 文件路径出发，搜索常见位置"""
        candidates = set()
        # 从已加载 H5 的位置推测
        for h5_path in self.h5_files:
            h5_dir = os.path.dirname(os.path.abspath(h5_path))
            # 同目录
            candidates.add(h5_dir)
            # 上级目录的 bin/ 子目录
            parent = os.path.dirname(h5_dir)
            candidates.add(os.path.join(parent, 'bin'))
            # 上上级
            grandparent = os.path.dirname(parent)
            candidates.add(os.path.join(grandparent, 'bin'))
            # 同级 _bin 目录
            for item in os.listdir(parent):
                full = os.path.join(parent, item)
                if os.path.isdir(full) and item.endswith('_bin'):
                    candidates.add(full)
            # storage 根目录下查找 bin 目录
            for item in os.listdir(grandparent):
                full = os.path.join(grandparent, item)
                if os.path.isdir(full) and ('bin' in item.lower()):
                    candidates.add(full)

        # 找到第一个包含 _emg.bin 文件的目录
        for d in sorted(candidates):
            if os.path.isdir(d):
                try:
                    for f in os.listdir(d):
                        if f.endswith('_emg.bin') and 'PREVIEW_' not in f:
                            self.bin_dir = d
                            display_path = d
                            if len(display_path) > 50:
                                display_path = "..." + display_path[-47:]
                            self.bin_dir_label.setText(display_path)
                            self.bin_dir_label.setStyleSheet(
                                "color: #009900; padding: 5px; background: #f0fff0; border: 1px solid #90EE90;")
                            self.bin_dir_label.setToolTip(d)
                            self.log_text.append(f"🔍 自动发现Bin目录: {d}")
                            if self.h5_files:
                                self._refresh_pairing_status()
                            return
                except OSError:
                    continue

        self.log_text.append("⚠️ 自动查找Bin目录失败，请手动选择")

    def _refresh_pairing_status(self):
        """扫描 H5 列表，更新每个文件的 bin 配对状态显示"""
        if not self.bin_dir or not self.h5_files:
            return

        paired_count = 0
        missing_count = 0
        synced_count = 0

        for i, h5_path in enumerate(self.h5_files):
            status, _, _ = self._check_bin_pairing(h5_path, self.bin_dir, device_id=1)
            item = self.h5_list.item(i)

            if status == 'synced':
                item.setForeground(QColor(128, 128, 128))  # gray
                item.setText(f"✓ {item.text().replace('✓ ', '').replace('✗ ', '').replace('? ', '')}")
                synced_count += 1
            elif status == 'paired':
                item.setForeground(QColor(0, 153, 0))  # green
                item.setText(f"✓ {item.text().replace('✓ ', '').replace('✗ ', '').replace('? ', '')}")
                paired_count += 1
            elif status == 'missing_emg':
                item.setForeground(QColor(220, 38, 38))  # red
                item.setText(f"✗ {item.text().replace('✓ ', '').replace('✗ ', '').replace('? ', '')}")
                missing_count += 1
            else:
                item.setForeground(QColor(150, 150, 150))  # light gray
                item.setText(f"? {item.text().replace('✓ ', '').replace('✗ ', '').replace('? ', '')}")
                missing_count += 1

        self.h5_count_label.setText(
            f"共 {len(self.h5_files)} 个文件 | "
            f"可同步: {paired_count} | 缺Bin: {missing_count} | 已完成: {synced_count}"
        )
        if paired_count > 0:
            self.h5_count_label.setStyleSheet("color: #009900; font-weight: bold;")
        elif missing_count > 0:
            self.h5_count_label.setStyleSheet("color: #dc2626; font-weight: bold;")

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
            all_emg = []
            all_imu = []
            for root, dirs, files in os.walk(dir_path):
                for f in files:
                    if f.endswith('_emg.bin') and 'PREVIEW_' not in f:
                        all_emg.append(os.path.join(root, f))
                    elif f.endswith('_imu.bin') and 'PREVIEW_' not in f:
                        all_imu.append(os.path.join(root, f))
            self.log_text.append(f"已选择Bin目录: {dir_path}")
            self.log_text.append(f"  找到 {len(all_emg)} EMG bin + {len(all_imu)} IMU bin (含子目录)")

            # 自动预检配对状态
            if self.h5_files:
                self._refresh_pairing_status()
                self.log_text.append(f"  配对预检完成，见H5列表颜色标记")

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


class OneToManySyncTab(QWidget):
    """一对多同步标签页 - 旧格式 H5 批量 ADC 搜索同步"""

    COL_FILE = 0; COL_STATUS = 1; COL_MODE = 2; COL_CMAP = 3; COL_RANGE = 4
    COLUMNS = 5

    def __init__(self):
        super().__init__()
        self.h5_paths = []       # ordered list matching table rows
        self.bin_dir = None
        self.worker = None
        self._syncing = False
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        hint = QLabel(
            "适用于旧格式：一个长 bin 对应多个 H5\n"
            "通过 250Hz 原始 ADC 值自动定位 bin 片段，不依赖旧 frame_id"
        )
        hint.setStyleSheet("color: #92400e; background: #fef3c7; padding: 8px; border-radius: 6px; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # H5 文件列表 (table with status columns)
        h5_group = QGroupBox("待同步的 H5 文件")
        h5_layout = QVBoxLayout(h5_group)
        h5_btn_row = QHBoxLayout()
        self.count_label = QLabel("共 0 个文件")
        h5_btn_row.addWidget(self.count_label)
        h5_btn_row.addStretch()
        clear_btn = QPushButton("清空列表")
        clear_btn.clicked.connect(self.clear_h5_list)
        h5_btn_row.addWidget(clear_btn)
        h5_layout.addLayout(h5_btn_row)

        self.table = QTableWidget(0, self.COLUMNS)
        self.table.setHorizontalHeaderLabels(["文件", "同步状态", "同步模式", "通道映射", "范围模式"])
        self.table.setMaximumHeight(120)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, self.COLUMNS):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h5_layout.addWidget(self.table)
        layout.addWidget(h5_group)

        # Bin 目录
        bin_row = QHBoxLayout()
        bin_row.addWidget(QLabel("长 Bin 目录:"))
        self.bin_label = QLabel("未选择")
        self.bin_label.setStyleSheet("color: #666;")
        bin_row.addWidget(self.bin_label, 1)
        bin_sel_btn = QPushButton("选择...")
        bin_sel_btn.clicked.connect(self.select_bin_dir)
        bin_row.addWidget(bin_sel_btn)
        layout.addLayout(bin_row)

        # 设备选择
        dev_row = QHBoxLayout()
        dev_row.addWidget(QLabel("同步设备:"))
        self.cb_emg1 = QCheckBox("EMG1"); self.cb_emg1.setChecked(True)
        self.cb_emg2 = QCheckBox("EMG2"); self.cb_emg2.setChecked(True)
        self.cb_imu = QCheckBox("IMU"); self.cb_imu.setChecked(True)
        dev_row.addWidget(self.cb_emg1); dev_row.addWidget(self.cb_emg2); dev_row.addWidget(self.cb_imu)
        dev_row.addStretch()
        layout.addLayout(dev_row)

        # 高级参数
        adv_group = QGroupBox("高级参数")
        adv_group.setCheckable(True); adv_group.setChecked(False)
        adv_layout = QHBoxLayout(adv_group)
        adv_layout.addWidget(QLabel("锚点数:"))
        self.anchors_spin = QSpinBox(); self.anchors_spin.setRange(10, 200); self.anchors_spin.setValue(40)
        adv_layout.addWidget(self.anchors_spin)
        adv_layout.addWidget(QLabel("匹配阈值:"))
        self.threshold_spin = QSpinBox(); self.threshold_spin.setRange(50, 100); self.threshold_spin.setValue(95); self.threshold_spin.setSuffix("%")
        adv_layout.addWidget(self.threshold_spin)
        adv_layout.addStretch()
        layout.addWidget(adv_group)

        # 按钮 + 进度条
        btn_row = QHBoxLayout()
        self.sync_btn = QPushButton("开始同步")
        self.sync_btn.clicked.connect(self.run_sync)
        self.sync_btn.setEnabled(False)
        self.sync_btn.setStyleSheet("font-weight: bold; background: #f97316; color: white; padding: 8px 24px; font-size: 13px;")
        btn_row.addWidget(self.sync_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(self.status_label)

        # 日志
        log_group = QGroupBox("同步日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 8))
        self.log_text.setStyleSheet("background: #1e1e1e; color: #d4d4d4;")
        self.log_text.setMaximumHeight(180)
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group)

    # ---- file list management ----

    def _read_file_attrs(self, h5_path):
        """Read sync-relevant attrs from H5. Returns dict with keys for table columns."""
        info = {'status': 'unknown', 'mode': '-', 'cmap': '-', 'range_mode': '-'}
        try:
            with h5py.File(h5_path, 'r') as f:
                st = f.attrs.get('sync_status')
                if isinstance(st, bytes): st = st.decode('utf-8')
                info['status'] = st or 'unknown'
                sm = f.attrs.get('sync_mode')
                if isinstance(sm, bytes): sm = sm.decode('utf-8')
                info['mode'] = sm or '-'
                cm = f.attrs.get('sync_adc_search_channel_map') or f.attrs.get('channel_map_name')
                if isinstance(cm, bytes): cm = cm.decode('utf-8')
                info['cmap'] = cm or '-'
                rm = f.attrs.get('sync_range_mode') or f.attrs.get('sync_bin_offset_mode')
                if isinstance(rm, bytes): rm = rm.decode('utf-8')
                info['range_mode'] = rm or '-'
        except Exception:
            pass
        return info

    def add_h5_file(self, h5_path):
        if h5_path in self.h5_paths:
            return
        self.h5_paths.append(h5_path)
        info = self._read_file_attrs(h5_path)
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._set_row(row, os.path.basename(h5_path), info)
        self._update_ui()

    def _set_row(self, row, fname, info):
        items = [
            QTableWidgetItem(fname),
            QTableWidgetItem(info['status']),
            QTableWidgetItem(info['mode']),
            QTableWidgetItem(info['cmap']),
            QTableWidgetItem(info['range_mode']),
        ]
        for c, it in enumerate(items):
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, c, it)
        # colour synced rows green
        if info['status'] == 'synced':
            for c in range(self.COLUMNS):
                self.table.item(row, c).setBackground(QColor(200, 255, 200))
        elif info['status'] == 'sync_failed':
            for c in range(self.COLUMNS):
                self.table.item(row, c).setBackground(QColor(255, 220, 220))

    def refresh_file_status(self, h5_path):
        """Refresh a row after sync completes."""
        if h5_path not in self.h5_paths:
            return
        row = self.h5_paths.index(h5_path)
        info = self._read_file_attrs(h5_path)
        self._set_row(row, os.path.basename(h5_path), info)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

    def _get_synced_files(self):
        """Return list of (idx, path) for files already synced."""
        return [(i, p) for i, p in enumerate(self.h5_paths)
                if self._read_file_attrs(p)['status'] == 'synced']

    def clear_h5_list(self):
        self.h5_paths.clear()
        self.table.setRowCount(0)
        self._update_ui()

    def _update_ui(self):
        n = len(self.h5_paths)
        self.count_label.setText(f"共 {n} 个文件")
        has_files = n > 0 and self.bin_dir is not None and not self._syncing
        self.sync_btn.setEnabled(has_files)

    def select_bin_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择长 Bin 文件所在目录")
        if d:
            self.bin_dir = d
            self.bin_label.setText(d)
            self.bin_label.setToolTip(d)
            self._update_ui()

    def _find_bin_files(self, h5_path, device_id):
        try:
            with h5py.File(h5_path, 'r') as f:
                prefix = f.attrs.get(f'sd_bin_dev{device_id}')
                if prefix and self.bin_dir:
                    if isinstance(prefix, bytes): prefix = prefix.decode('utf-8')
                    emg_name = f'{prefix}_emg.bin'
                    imu_name = f'{prefix}_imu.bin'
                    # 搜索 bin_dir 及子目录
                    for root, dirs, files in os.walk(self.bin_dir):
                        ep = os.path.join(root, emg_name)
                        ip = os.path.join(root, imu_name)
                        if os.path.exists(ep):
                            return ep, ip if os.path.exists(ip) else None
            # fallback: 找第一个非 preview emg bin
            if self.bin_dir:
                for root, dirs, files in os.walk(self.bin_dir):
                    for fn in sorted(files):
                        if fn.endswith('_emg.bin') and 'PREVIEW_' not in fn:
                            return os.path.join(root, fn), None
        except Exception as e:
            self.log(f"  查找 bin 失败: {e}")
        return None, None

    def log(self, msg):
        self.log_text.append(msg)

    # ---- sync flow ----

    def run_sync(self):
        if not self.h5_paths or not self.bin_dir or self._syncing:
            return
        synced = self._get_synced_files()
        files_to_sync = list(self.h5_paths)
        clear_first = set()

        if synced:
            names = '\n'.join(f"  {os.path.basename(p)}" for _, p in synced[:5])
            extra = f"\n  ... 等共 {len(synced)} 个" if len(synced) > 5 else ""
            reply = QMessageBox.question(self, "已同步文件",
                f"列表中有 {len(synced)} 个已同步文件:\n{names}{extra}\n\n"
                "是否清除旧同步结果并重新同步？\n"
                "  [Yes] 清除旧结果 → 重新同步所有文件\n"
                "  [No]  跳过已同步文件，只同步未完成的",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                from bin_sync_tool import clear_sync_outputs
                for _, p in synced:
                    self.log(f"清除旧同步: {os.path.basename(p)}")
                    try:
                        clear_sync_outputs(p, backup=True)
                        clear_first.add(p)
                        self.refresh_file_status(p)
                    except Exception as e:
                        self.log(f"  清除失败: {e}")
            else:
                files_to_sync = [p for p in files_to_sync if p not in {sp for _, sp in synced}]
                for _, p in synced:
                    self.log(f"跳过已同步: {os.path.basename(p)}")

        if not files_to_sync:
            self.log("没有需要同步的文件")
            return

        self._syncing = True
        self._update_ui()
        self.progress_bar.setVisible(True)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setRange(0, 0)  # indeterminate while searching
        self.progress_bar.setFormat("正在搜索 ADC offset，请稍候...")
        self.log_text.clear()
        self.log(f"=== 开始一对多 ADC 搜索同步 ===")
        self.log(f"Bin 目录: {self.bin_dir}")
        self.log(f"待同步: {len(files_to_sync)} 个文件")

        self.worker = OneToManySyncWorker(
            files_to_sync, self.bin_dir,
            emg1=self.cb_emg1.isChecked(), emg2=self.cb_emg2.isChecked(),
            imu=self.cb_imu.isChecked(),
            num_anchors=self.anchors_spin.value(),
            match_threshold=self.threshold_spin.value() / 100.0,
        )
        self.worker.file_started.connect(self._on_file_started)
        self.worker.file_finished.connect(self._on_file_finished)
        self.worker.progress_text.connect(self.status_label.setText)
        self.worker.log.connect(self.log)
        self.worker.finished.connect(self._on_all_finished)
        self.worker.start()

    def _on_file_started(self, idx, total, fname):
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setFormat(f"正在同步 {idx+1}/{total}: {fname}")
        self.status_label.setText(f"正在搜索 ADC offset / 同步当前文件，请稍候...")

    def _on_file_finished(self, idx, total, fname, status, summary):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(idx + 1)
        self.progress_bar.setFormat(f"已完成 {idx+1}/{total}")
        self.status_label.setText(f"完成: {fname} ({status})")
        # refresh table row
        for p in self.h5_paths:
            if os.path.basename(p) == fname:
                self.refresh_file_status(p)
                break
        if summary:
            self.log(f"  {status} {fname}: {summary}")

    def _on_all_finished(self, success_count, total, results):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(total)
        self.progress_bar.setFormat(f"完成 {success_count}/{total}")
        self.progress_bar.setVisible(False)
        self._syncing = False
        self._update_ui()
        self.status_label.setText(f"完成: {success_count}/{total} 成功")
        self.log(f"\n=== 同步结束: {success_count}/{total} 成功 ===")
        for r in results:
            if r.get('status') != 'success' and r.get('status') != 'skipped':
                self.log(f"  FAIL {r['file']}: {r.get('reason','?')}")


class OneToManySyncWorker(QThread):
    """一对多同步工作线程"""
    file_started = pyqtSignal(int, int, str)
    file_finished = pyqtSignal(int, int, str, str, str)  # idx, total, fname, status, summary
    progress_text = pyqtSignal(str)
    log = pyqtSignal(str)
    finished = pyqtSignal(int, int, list)

    def __init__(self, h5_paths, bin_dir, emg1=True, emg2=True, imu=True,
                 num_anchors=40, match_threshold=0.95):
        super().__init__()
        self.h5_paths = h5_paths
        self.bin_dir = bin_dir
        self.emg1 = emg1; self.emg2 = emg2; self.imu = imu
        self.num_anchors = num_anchors
        self.match_threshold = match_threshold

    def _find_bin(self, h5_path, device_id):
        try:
            with h5py.File(h5_path, 'r') as f:
                prefix = f.attrs.get(f'sd_bin_dev{device_id}')
                if prefix and self.bin_dir:
                    if isinstance(prefix, bytes): prefix = prefix.decode('utf-8')
                    emg_name = f'{prefix}_emg.bin'
                    imu_name = f'{prefix}_imu.bin'
                    for root, dirs, files in os.walk(self.bin_dir):
                        ep = os.path.join(root, emg_name)
                        ip = os.path.join(root, imu_name)
                        if os.path.exists(ep):
                            return ep, ip if os.path.exists(ip) else None
            # fallback: 第一个非 preview emg bin
            for root, dirs, files in os.walk(self.bin_dir):
                for fn in sorted(files):
                    if fn.endswith('_emg.bin') and 'PREVIEW_' not in fn:
                        return os.path.join(root, fn), None
        except Exception:
            pass
        return None, None

    def run(self):
        from bin_sync_tool import sync_h5_one_to_many_adc_search
        total = len(self.h5_paths)
        results = []
        success_count = 0

        for idx, h5_path in enumerate(self.h5_paths):
            fname = os.path.basename(h5_path)
            self.file_started.emit(idx, total, fname)
            self.log.emit(f"\n--- {fname} ---")

            devices = []
            if self.emg1: devices.append(1)
            if self.emg2: devices.append(2)
            ndev = len(devices)
            file_ok = True
            parts = []

            for di, did in enumerate(devices):
                emg_bin, imu_bin = self._find_bin(h5_path, did)
                if not emg_bin:
                    self.log.emit(f"  Dev{did}: 未找到 bin, 跳过")
                    file_ok = False
                    continue
                is_last = (di == ndev - 1)
                ini = imu_bin if self.imu else None
                self.progress_text.emit(f"正在同步 {idx+1}/{total}: {fname} Dev{did}...")
                self.log.emit(f"  Dev{did}: {os.path.basename(emg_bin)}")
                try:
                    r = sync_h5_one_to_many_adc_search(
                        h5_path, emg_bin, ini, device_id=did,
                        verify=True, set_synced=is_last,
                        num_anchors=self.num_anchors,
                        match_threshold=self.match_threshold,
                    )
                    if r.get('status') == 'success':
                        cm = r.get('search_result', {}).get('channel_map_name', '?') if 'search_result' in r else r.get('channel_map_name', '?')
                        mr = r.get('match_rate', '?')
                        rm = r.get('search_result', {}).get('range_mode', '?') if 'search_result' in r else r.get('range_mode', '?')
                        imu_s = r.get('imu_status', '?')
                        imu_f = r.get('imu_frames', 0)
                        parts.append(f"D{did}=OK(map={cm},rate={mr},range={rm},IMU={imu_s}/{imu_f}f)")
                        self.log.emit(f"    OK offset={r.get('offset')} channel_map={cm} rate={mr} range={rm} IMU={imu_s}({imu_f}f)")
                    else:
                        parts.append(f"D{did}=FAIL")
                        self.log.emit(f"    FAIL: {r.get('reason','?')}")
                        file_ok = False
                except Exception as e:
                    parts.append(f"D{did}=ERR")
                    self.log.emit(f"    ERROR: {e}")
                    file_ok = False

            status = 'success' if file_ok else 'failed'
            summary = '; '.join(parts) if parts else 'no devices'
            self.file_finished.emit(idx, total, fname, status, summary)
            if file_ok:
                success_count += 1
                results.append({'file': fname, 'status': 'success'})
            else:
                results.append({'file': fname, 'status': 'failed', 'reason': summary})

        self.finished.emit(success_count, total, results)

class SyncToolsTab(QWidget):
    """擦除同步标签页 - 清除同步结果，保留 250Hz 原始数据"""

    def __init__(self):
        super().__init__()
        self.h5_paths = []
        self.worker = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        hint = QLabel(
            "擦除同步工具：清除 emg*_2khz_adc / imu*_100hz 等同步产物\n"
            "不会删除 250Hz 原始数据，不会影响采集元数据。\n"
            "重新同步请使用 [同步(一对一)] 或 [同步(一对多)] 标签页。"
        )
        hint.setStyleSheet("color: #666; background: #f0f0f0; padding: 8px; border-radius: 6px; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # H5 文件列表
        h5_group = QGroupBox("待擦除的 H5 文件")
        h5_layout = QVBoxLayout(h5_group)
        h5_btn_row = QHBoxLayout()
        self.count_label = QLabel("共 0 个文件")
        h5_btn_row.addWidget(self.count_label)
        h5_btn_row.addStretch()
        self.clear_list_btn = QPushButton("清空列表")
        self.clear_list_btn.clicked.connect(self.clear_h5_list)
        h5_btn_row.addWidget(self.clear_list_btn)
        h5_layout.addLayout(h5_btn_row)
        self.list_widget = QListWidget()
        self.list_widget.setMaximumHeight(90)
        h5_layout.addWidget(self.list_widget)
        layout.addWidget(h5_group)

        # 按钮 + 进度条
        btn_row = QHBoxLayout()
        self.clear_btn = QPushButton("清除同步结果")
        self.clear_btn.clicked.connect(self.run_clear)
        self.clear_btn.setEnabled(False)
        self.clear_btn.setStyleSheet("font-weight: bold; background: #dc3545; color: white; padding: 8px 24px;")
        btn_row.addWidget(self.clear_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(self.status_label)

        # 简洁日志
        log_group = QGroupBox("操作日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 8))
        self.log_text.setStyleSheet("background: #1e1e1e; color: #d4d4d4;")
        self.log_text.setMaximumHeight(150)
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group)

    def log(self, msg):
        self.log_text.append(msg)

    def add_h5_file(self, h5_path):
        if h5_path not in self.h5_paths:
            self.h5_paths.append(h5_path)
            self.list_widget.addItem(os.path.basename(h5_path))
            self._update_ui()

    def clear_h5_list(self):
        self.h5_paths.clear()
        self.list_widget.clear()
        self._update_ui()

    def _update_ui(self):
        n = len(self.h5_paths)
        self.count_label.setText(f"共 {n} 个文件")
        self.clear_btn.setEnabled(n > 0)

    def run_clear(self):
        if not self.h5_paths:
            return
        reply = QMessageBox.warning(self, "确认擦除",
            f"将清除 {len(self.h5_paths)} 个 H5 文件的同步结果:\n"
            "  - 删除 emg*_2khz_adc / imu*_100hz 等同步产物\n"
            "  - 清除 sync_* attrs\n"
            "  - 自动备份 .bak 文件\n"
            "  - 保留 250Hz 原始数据\n\n确定继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        self.clear_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(self.h5_paths))
        self.log_text.clear()
        self.log("=== 开始擦除同步 ===")

        self.worker = ClearSyncWorker(self.h5_paths)
        self.worker.progress.connect(self.on_progress)
        self.worker.log.connect(self.log)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def on_progress(self, current, total, message):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_label.setText(message)

    def on_finished(self, success_count, total, errors):
        self.progress_bar.setVisible(False)
        self.clear_btn.setEnabled(True)
        self.status_label.setText(f"完成: {success_count}/{total} 成功")
        self.log(f"\n=== 擦除结束: {success_count}/{total} 成功 ===")
        for e in errors:
            self.log(f"  ERROR: {e}")


class ClearSyncWorker(QThread):
    """擦除同步工作线程"""
    progress = pyqtSignal(int, int, str)
    log = pyqtSignal(str)
    finished = pyqtSignal(int, int, list)

    def __init__(self, h5_paths):
        super().__init__()
        self.h5_paths = h5_paths

    def run(self):
        from bin_sync_tool import clear_sync_outputs
        total = len(self.h5_paths)
        success = 0
        errs = []
        for idx, h5_path in enumerate(self.h5_paths):
            fn = os.path.basename(h5_path)
            self.progress.emit(idx, total, f"擦除中: {fn}")
            self.log.emit(f"  {fn}...")
            try:
                r = clear_sync_outputs(h5_path, backup=True)
                if r['success']:
                    success += 1
                    self.log.emit(f"    OK: 已备份, 已删除 {r['removed_datasets']}")
                else:
                    errs.append(f"{fn}: {'; '.join(r.get('errors', ['unknown']))}")
                    self.log.emit(f"    FAIL: {r.get('errors')}")
            except Exception as e:
                errs.append(f"{fn}: {e}")
                self.log.emit(f"    ERROR: {e}")
            self.progress.emit(idx + 1, total, f"完成: {fn}")
        self.finished.emit(success, total, errs)

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
        self.tabs.addTab(self.sync_tab, "同步（新版本）")

        self.one_to_many_tab = OneToManySyncTab()
        self.tabs.addTab(self.one_to_many_tab, "同步（旧版本）")

        self.sync_tools_tab = SyncToolsTab()
        self.tabs.addTab(self.sync_tools_tab, "擦除同步")

        self.breakpoint_tab = BreakpointTab()
        self.tabs.addTab(self.breakpoint_tab, "历史断点")

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
        """批量添加选中的文件到同步列表（根据当前标签页分发）"""
        items = self.file_list.selectedItems()
        if not items:
            return
        file_paths = [item.data(Qt.UserRole) for item in items]
        current_tab = self.tabs.currentWidget()
        if current_tab is self.sync_tab:
            self.sync_tab.add_files_from_list(file_paths)
            # 如果还没选 Bin 目录，自动尝试查找
            if not self.sync_tab.bin_dir:
                self.sync_tab._auto_detect_bin_dir()
            self.tabs.setCurrentIndex(1)
        elif current_tab is self.one_to_many_tab:
            for fp in file_paths:
                self.one_to_many_tab.add_h5_file(fp)
            # 同样尝试自动查找 bin 目录
            if not self.one_to_many_tab.bin_dir:
                parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(file_paths[0])))
                for d in [os.path.join(parent_dir, 'bin'), os.path.join(os.path.dirname(parent_dir), 'bin')]:
                    if os.path.isdir(d) and any(f.endswith('_emg.bin') for f in (os.listdir(d) if os.path.exists(d) else [])):
                        self.one_to_many_tab.bin_dir = d
                        self.one_to_many_tab.bin_label.setText(d)
                        self.one_to_many_tab._update_ui()
                        break
            self.tabs.setCurrentIndex(2)
        elif current_tab is self.sync_tools_tab:
            for fp in file_paths:
                self.sync_tools_tab.add_h5_file(fp)
            self.tabs.setCurrentIndex(3)
        else:
            self.sync_tab.add_files_from_list(file_paths)
            if not self.sync_tab.bin_dir:
                self.sync_tab._auto_detect_bin_dir()
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
