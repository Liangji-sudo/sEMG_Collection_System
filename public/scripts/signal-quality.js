/**
 * signal-quality.js - 信号质量实时监测与颜色指示
 *
 * 移植自供应商 V3 上位机:
 *   wband_emg_V3/signal_quality.py  → RealTimeQualityMonitor
 *   wband_emg_V3/custom_widgets.py   → ChannelStatusRow 颜色映射
 *
 * 输入: 已滤波 μV 数据 (来自 realtimeEngine → waveform.js data.emgN)
 *       格式 [channel][sample], mapped 通道顺序
 *
 * 窗口: 0.25s @ 250Hz = 63 samples
 * 输出: 每通道 rms/dead/clipped + 颜色映射
 */

(function() {
    'use strict';

    // ==================== 常量 ====================

    var NUM_CHANNELS = 16;
    var DEFAULT_FS = 250;
    var DEFAULT_WINDOW_S = 0.25;
    var RMS_MAX = 50.0;              // uV, RMS 颜色映射上限
    var DEAD_VARIANCE_THRESHOLD = 0.1;

    // clipLimitUv 计算 (与 ble_server.py 一致)
    // BASE_LSB_24BIT = 0.476837 (对齐供应商 V3 / bin_sync_tool)
    // lsb_uv = 0.476837 / (12 * 10) = 0.003974 uV/LSB (gain=12)
    // clip_limit_uv = lsb_uv * 8388607 ≈ 33333 uV
    var BASE_LSB_24BIT = 0.476837;
    var DEFAULT_GAIN = 12;
    var HARDWARE_FRONTEND_GAIN = 10;

    function calcClipLimitUv(gain) {
        var lsbUv = BASE_LSB_24BIT / (gain * HARDWARE_FRONTEND_GAIN);
        return lsbUv * 8388607;
    }
    var DEFAULT_CLIP_LIMIT_UV = calcClipLimitUv(DEFAULT_GAIN);

    // ==================== RMS 颜色映射 ====================

    /**
     * 将 RMS (uV) 映射为 CSS rgb() 背景色
     * 绿 (≤25uV) → 黄 (25-50uV) → 红 (>50uV)
     *
     * @param {number} rmsUv
     * @returns {string} e.g. "rgb(76,175,80)"
     */
    function rmsToColor(rmsUv) {
        var rms = Math.min(rmsUv, RMS_MAX);
        var ratio = rms / RMS_MAX;
        var r, g, b;

        if (ratio < 0.5) {
            var t = ratio * 2;
            r = Math.round(76  + (255 - 76)  * t);
            g = Math.round(175 + (235 - 175) * t);
            b = Math.round(80  + (59  - 80)  * t);
        } else {
            var t2 = (ratio - 0.5) * 2;
            r = Math.round(255 + (244 - 255) * t2);
            g = Math.round(235 + (67  - 235) * t2);
            b = Math.round(59  + (54  - 59)  * t2);
        }
        return 'rgb(' + r + ',' + g + ',' + b + ')';
    }

    // ==================== QualityMonitor ====================

    /**
     * 实时信号质量监测器 (滑动窗口)
     *
     * @param {number} numChannels - 通道数 (默认 16)
     * @param {number} fs          - 采样率 Hz (默认 250)
     * @param {number} windowS     - 窗口秒数 (默认 0.25)
     */
    function QualityMonitor(numChannels, fs, windowS) {
        this.numChannels = numChannels || NUM_CHANNELS;
        this.fs = fs || DEFAULT_FS;
        this.windowS = windowS || DEFAULT_WINDOW_S;
        this.bufferSize = Math.max(1, Math.floor(this.fs * this.windowS));
        this._clipLimitUv = DEFAULT_CLIP_LIMIT_UV;
        this.reset();
    }

    QualityMonitor.prototype.reset = function() {
        /** @type {number[][]} [sample][channel] */
        this._buffer = [];
    };

    /**
     * 设置削波限值 (后续可接入 realtimeEngine 传来的 gain/lsb_uv)
     * @param {number} clipLimitUv
     */
    QualityMonitor.prototype.setClipLimitUv = function(clipLimitUv) {
        this._clipLimitUv = clipLimitUv;
    };

    QualityMonitor.prototype.getClipLimitUv = function() {
        return this._clipLimitUv;
    };

    /**
     * 喂入 EMG 数据
     *
     * @param {number[][]} emgChunk - [channel][sample] 格式, μV 值
     * @returns {?{rms: number[], dead: boolean[], clipped: boolean[]}}
     *          窗口满时返回指标，否则返回 null
     */
    QualityMonitor.prototype.feed = function(emgChunk) {
        if (!emgChunk || emgChunk.length === 0) return null;

        var numCh = emgChunk.length;
        if (numCh !== this.numChannels) return null;

        var numSamples = emgChunk[0].length;
        if (numSamples === 0) return null;

        // 转置: [channel][sample] → [sample][channel]
        for (var s = 0; s < numSamples; s++) {
            var row = new Array(this.numChannels);
            for (var ch = 0; ch < this.numChannels; ch++) {
                row[ch] = emgChunk[ch][s];
            }
            this._buffer.push(row);
        }

        // 缓冲区未满，等待更多数据
        if (this._buffer.length < this.bufferSize) {
            return null;
        }

        // 取窗口数据并滑出
        var procData = this._buffer.slice(0, this.bufferSize);
        this._buffer = this._buffer.slice(this.bufferSize);

        var numProc = procData.length;
        var nc = this.numChannels;

        // 计算每通道 RMS、均值、最大绝对值
        var rms    = new Array(nc);
        var means  = new Array(nc);
        var maxAbs = new Array(nc);

        for (var ch2 = 0; ch2 < nc; ch2++) {
            var sumSq2 = 0, sum2 = 0, maxVal2 = 0;
            for (var s2 = 0; s2 < numProc; s2++) {
                var v2 = procData[s2][ch2];
                sumSq2 += v2 * v2;
                sum2 += v2;
                var av2 = v2 < 0 ? -v2 : v2;
                if (av2 > maxVal2) maxVal2 = av2;
            }
            rms[ch2]    = Math.sqrt(sumSq2 / numProc);
            means[ch2]  = sum2 / numProc;
            maxAbs[ch2] = maxVal2;
        }

        // 计算方差 (二次遍历)
        var variances = new Array(nc);
        for (var ch3 = 0; ch3 < nc; ch3++) {
            var sumSqDiff3 = 0, m3 = means[ch3];
            for (var s3 = 0; s3 < numProc; s3++) {
                var d3 = procData[s3][ch3] - m3;
                sumSqDiff3 += d3 * d3;
            }
            variances[ch3] = sumSqDiff3 / numProc;
        }

        // dead: 方差 < 阈值 → 全平/脱落
        var dead = new Array(nc);
        for (var ch4 = 0; ch4 < nc; ch4++) {
            dead[ch4] = variances[ch4] < DEAD_VARIANCE_THRESHOLD;
        }

        // clipped: |max| > clipLimit * 0.99
        var clipThresh = this._clipLimitUv * 0.99;
        var clipped = new Array(nc);
        for (var ch5 = 0; ch5 < nc; ch5++) {
            clipped[ch5] = maxAbs[ch5] > clipThresh;
        }

        return { rms: rms, dead: dead, clipped: clipped };
    };

    // ==================== ChannelStatusDisplay ====================

    /**
     * 更新一条通道状态行 (16 个颜色格) 的 DOM
     *
     * @param {string} rowId   - DOM 元素 id, e.g. 'emg1-channel-status'
     * @param {?{rms: number[], dead: boolean[], clipped: boolean[]}} metrics
     */
    function updateChannelStatusRow(rowId, metrics) {
        var row = document.getElementById(rowId);
        if (!row) return;

        var dots = row.querySelectorAll('.channel-status-dot');
        if (dots.length !== NUM_CHANNELS) return;

        if (!metrics) {
            for (var i = 0; i < NUM_CHANNELS; i++) {
                dots[i].style.backgroundColor = '#9ca3af';
                dots[i].style.border = 'none';
                dots[i].dataset.clipped = '0';
            }
            return;
        }

        var rms = metrics.rms, dead = metrics.dead, clipped = metrics.clipped;

        for (var i2 = 0; i2 < NUM_CHANNELS; i2++) {
            if (dead[i2]) {
                dots[i2].style.backgroundColor = '#555555';
            } else {
                dots[i2].style.backgroundColor = rmsToColor(rms[i2]);
            }
            dots[i2].dataset.clipped = clipped[i2] ? '1' : '0';
            // 非削波时清除残留红色边框
            if (!clipped[i2]) {
                dots[i2].style.border = 'none';
            }
        }
    }

    // ==================== 削波闪烁定时器 (4 Hz) ====================

    var _flashTimer = null;
    var _flashState = false;

    function _startFlashTimer() {
        if (_flashTimer) return;
        _flashTimer = setInterval(function() {
            _flashState = !_flashState;
            var clippedDots = document.querySelectorAll('.channel-status-dot[data-clipped="1"]');
            for (var i = 0; i < clippedDots.length; i++) {
                clippedDots[i].style.border = _flashState ? '2px solid red' : 'none';
            }
        }, 250);
    }

    function _stopFlashTimer() {
        if (_flashTimer) {
            clearInterval(_flashTimer);
            _flashTimer = null;
        }
        _flashState = false;
    }

    // 页面就绪后启动 (仅在浏览器环境)
    if (typeof document !== 'undefined') {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', _startFlashTimer);
        } else {
            _startFlashTimer();
        }
        // 页面卸载时清理
        window.addEventListener('beforeunload', _stopFlashTimer);
    }

    // ==================== 导出 ====================

    var exports = {
        QualityMonitor: QualityMonitor,
        updateChannelStatusRow: updateChannelStatusRow,
        calcClipLimitUv: calcClipLimitUv,
        DEFAULT_CLIP_LIMIT_UV: DEFAULT_CLIP_LIMIT_UV,
        rmsToColor: rmsToColor,
        NUM_CHANNELS: NUM_CHANNELS,
    };

    // 浏览器环境
    if (typeof window !== 'undefined') {
        window.SignalQuality = exports;
    }
    // Node.js 环境 (用于测试)
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = exports;
    }

})();
