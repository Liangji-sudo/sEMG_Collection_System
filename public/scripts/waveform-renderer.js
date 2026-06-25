/**
 * waveform-renderer.js - 波形显示渲染模块
 * 
 * 这是一个纯显示接口，不包含数据生成逻辑。
 * 你可以通过调用 renderPoints() 方法来渲染外部数据。
 * 
 * 使用方式：
 *   1. 创建渲染器实例
 *   2. 调用 renderer.renderPoints(data) 渲染数据
 *   3. 数据格式: [[ch0_point1, ch0_point2, ...], [ch1_point1, ch1_point2, ...], ...]
 */

(function() {
    'use strict';

    // ==================== 配置参数 ====================
    const RENDERER_CONFIG = {
        // 渲染频率 (Hz) - 用于计算总点数（旧公式，不再用于时间窗）
        RENDER_RATE: 100,
        // EMG每次渲染的数据点数（旧公式，不再用于时间窗）
        EMG_POINTS_PER_RENDER: 18,
        // IMU每次渲染的数据点数（旧公式，不再用于时间窗）
        IMU_POINTS_PER_RENDER: 1,
        // 显示窗口时长 (秒) — 对齐供应商 5s
        WINDOW_DURATION: 5,
        // EMG 真实显示采样率 (Hz) — BLE 硬件 250Hz，直接写入 Canvas
        EMG_DISPLAY_SAMPLE_RATE: 250,
        // IMU 真实显示采样率 (Hz) — 每个 BLE 包 9 个 EMG 样本 + 1 个 IMU 点
        // 所以 IMU 写入速率 ≈ 250 / 9 ≈ 27.78 Hz
        IMU_DISPLAY_SAMPLE_RATE: 250 / 9,
        // EMG通道数
        EMG_CHANNELS: 16,
        // IMU轴数
        IMU_AXES: 3,
        // 线宽
        LINE_WIDTH: 0.8,
        // 通道颜色
        COLORS: {
            emg: [
                '#e74c3c', '#3498db', '#2ecc71', '#f39c12',
                '#9b59b6', '#1abc9c', '#e67e22', '#34495e',
                '#c0392b', '#2980b9', '#27ae60', '#d35400',
                '#8e44ad', '#16a085', '#c0392b', '#7f8c8d'
            ],
            imu: ['#e74c3c', '#2ecc71', '#3498db'] // X:红, Y:绿, Z:蓝
        },
        LABEL_WIDTH: 34
    };

    // ==================== 波形渲染器类 ====================
    /**
     * WaveformRenderer - 单个波形窗口的渲染器
     * 
     * @param {string} canvasId - Canvas元素ID
     * @param {string} containerId - 容器元素ID
     * @param {string} pointerId - 时间指针元素ID
     * @param {object} options - 配置选项
     *   - channels: 通道数量
     *   - colors: 颜色数组
     *   - offsetInputId: Offset输入框ID
     *   - channelSelectId: 通道选择下拉框ID
     *   - type: 'emg' 或 'imu'
     */
    class WaveformRenderer {
        constructor(canvasId, containerId, pointerId, options = {}) {
            this.canvas = document.getElementById(canvasId);
            this.container = document.getElementById(containerId);
            this.pointer = document.getElementById(pointerId);

            if (!this.canvas || !this.container) {
                console.error(`Canvas or container not found: ${canvasId}, ${containerId}`);
                return;
            }

            this.ctx = this.canvas.getContext('2d');

            // 配置
            this.channels = options.channels || 16;
            this.colors = options.colors || RENDERER_CONFIG.COLORS.emg;
            this.offsetInputId = options.offsetInputId;
            this.channelSelectId = options.channelSelectId;
            this.type = options.type || 'emg';
            this.signalKind = options.signalKind || null;

            // 供应商风格堆叠视图 (仅 EMG)
            this.stackedMode = (this.type === 'emg') && (options.stackedMode !== false);
            this.imuStackedMode = (this.type === 'imu') && (options.imuStackedMode === true);
            this.clampEnabled = options.clampEnabled || false;
            this.clampCheckboxId = options.clampCheckboxId || null;
            this._labelFont = '8px sans-serif';

            // 状态
            this.writeIndex = 0;
            this.totalPoints = 0;

            // 每个通道的上一个点位置（用于连线）
            this.lastX = [];
            this.lastY = [];

            // 初始化
            this.init();
            this.setupResizeHandler();
        }

        init() {
            this.resize();
            this.clear();
        }

        resize() {
            const rect = this.container.getBoundingClientRect();
            const dpr = window.devicePixelRatio || 1;
            
            this.canvas.width = rect.width * dpr;
            this.canvas.height = rect.height * dpr;
            this.canvas.style.width = rect.width + 'px';
            this.canvas.style.height = rect.height + 'px';
            this.ctx.scale(dpr, dpr);
            
            this.displayWidth = rect.width;
            this.displayHeight = rect.height;
            
            // 计算总数据点数 — 按真实信号采样率 × 窗口秒数
            // EMG: 250 Hz × 3s = 750 点; IMU: (250/9) Hz × 3s ≈ 83 点
            if (this.type === 'emg') {
                this.totalPoints = Math.round(RENDERER_CONFIG.EMG_DISPLAY_SAMPLE_RATE * RENDERER_CONFIG.WINDOW_DURATION);
            } else {
                this.totalPoints = Math.round(RENDERER_CONFIG.IMU_DISPLAY_SAMPLE_RATE * RENDERER_CONFIG.WINDOW_DURATION);
            }
            
            this.initState();
        }

        initState() {
            this.lastX = [];
            this.lastY = [];
            for (let ch = 0; ch < this.channels; ch++) {
                this.lastX[ch] = -1;
                this.lastY[ch] = -1;
            }
            this.writeIndex = 0;
        }

        setupResizeHandler() {
            let resizeTimeout;
            this._resizeObserver = new ResizeObserver(() => {
                clearTimeout(resizeTimeout);
                resizeTimeout = setTimeout(() => {
                    this.resize();
                    this.clear();
                }, 100);
            });
            this._resizeObserver.observe(this.container);
        }

        /**
         * 销毁渲染器，清理所有资源
         */
        destroy() {
            if (this._resizeObserver) {
                this._resizeObserver.disconnect();
                this._resizeObserver = null;
            }
        }

        /**
         * 清除画布并重置状态
         */
        clear() {
            const dpr = window.devicePixelRatio || 1;
            this.ctx.setTransform(1, 0, 0, 1, 0, 0);
            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
            this.ctx.scale(dpr, dpr);
            this.initState();
            this.updatePointer();
        }

        /**
         * 获取当前Offset值
         */
        getOffset() {
            if (this.offsetInputId) {
                const input = document.getElementById(this.offsetInputId);
                if (input) {
                    return parseFloat(input.value) || (this.type === 'emg' ? 300 : 4);
                }
            }
            return this.type === 'emg' ? 300 : 4;
        }

        /**
         * 获取可见通道范围
         */
        getVisibleChannels() {
            if (this.channelSelectId && this.type === 'emg') {
                const select = document.getElementById(this.channelSelectId);
                if (select) {
                    const value = select.value;
                    if (value === '1-8') return { start: 0, end: 8 };
                    if (value === '9-16') return { start: 8, end: 16 };
                }
            }
            return { start: 0, end: this.channels };
        }

        /**
         * 获取 Clamp 状态（从 checkbox 或实例属性）
         */
        getClampEnabled() {
            if (this.clampCheckboxId) {
                var cb = document.getElementById(this.clampCheckboxId);
                if (cb) return cb.checked;
            }
            return this.clampEnabled;
        }

        /**
         * 绘制堆叠视图的 CH1-CH16 标签（左侧 overlay）
         */
        drawChannelLabels() {
            var ctx = this.ctx;
            var dpr = window.devicePixelRatio || 1;
            ctx.save();
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            // 清除左侧标签区域
            ctx.clearRect(0, 0, 32 * dpr, this.canvas.height);
            ctx.scale(dpr, dpr);

            var offset = this.getOffset();
            var totalHeight = this.channels * offset;
            var scaleY = this.displayHeight / totalHeight;

            ctx.fillStyle = '#666';
            ctx.font = this._labelFont;
            ctx.textAlign = 'right';
            ctx.textBaseline = 'middle';

            for (var ch = 0; ch < this.channels; ch++) {
                var yBase = (this.channels - ch - 0.5) * offset * scaleY;
                if (yBase >= 0 && yBase <= this.displayHeight) {
                    ctx.fillText('CH' + (ch + 1), 28, yBase);
                }
            }
            ctx.restore();
        }

        /**
         * 更新时间指针位置
         */
        updatePointer() {
            if (this.pointer && this.totalPoints > 0) {
                const metrics = this.getPlotMetrics();
                const x = metrics.left + (this.writeIndex / this.totalPoints) * metrics.width;
                this.pointer.style.left = x + 'px';
            }
        }

        getPlotMetrics() {
            const left = (this.stackedMode || this.imuStackedMode) ? RENDERER_CONFIG.LABEL_WIDTH : 0;
            return {
                left: left,
                width: Math.max(1, this.displayWidth - left)
            };
        }

        getPlotX(index) {
            const metrics = this.getPlotMetrics();
            return metrics.left + (index / this.totalPoints) * metrics.width;
        }

        /**
         * 渲染数据点 - 核心接口
         *
         * @param {Array<Array<number>>} data - 多通道数据
         *   格式: [[ch0_p1, ch0_p2, ...], [ch1_p1, ch1_p2, ...], ...]
         *   EMG: 16个通道，每个通道 N 个点 (250Hz 实时)
         *   IMU: 3个通道(xyz)，每个通道1个点
         */
        renderPoints(data) {
            // 【修复】防止 totalPoints 为 0 时除零导致 NaN
            if (!data || data.length === 0 || this.totalPoints <= 0) return;

            if (this.imuStackedMode) {
                this._renderIMUStacked(data);
            } else if (this.stackedMode) {
                this._renderPointsStacked(data);
            } else {
                this._renderPointsBanded(data);
            }
            this.updatePointer();
        }

        /**
         * 供应商风格堆叠渲染: 16 通道同轴堆叠，Offset 分离
         */
        _normalizeIMUChips(data) {
            if (!data || data.length === 0) return [];

            if (Array.isArray(data[0]) && typeof data[0][0] === 'number') {
                return [{
                    index: 0,
                    values: [data[0][0] || 0, data[1]?.[0] || 0, data[2]?.[0] || 0]
                }];
            }

            return data.map((chip, idx) => {
                const values = chip.values || chip[this.signalKind] || chip.acc || chip.gyr || [0, 0, 0];
                return {
                    index: chip.index !== undefined ? chip.index : idx,
                    values: [
                        Number(values[0]) || 0,
                        Number(values[1]) || 0,
                        Number(values[2]) || 0
                    ]
                };
            });
        }

        _drawIMULabels() {
            const ctx = this.ctx;
            const dpr = window.devicePixelRatio || 1;
            ctx.save();
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.clearRect(0, 0, 34 * dpr, this.canvas.height);
            ctx.scale(dpr, dpr);

            const offset = this.getOffset();
            const scaleY = this.displayHeight / (3 * offset);
            const labels = ['X', 'Y', 'Z'];

            ctx.fillStyle = '#444';
            ctx.font = this._labelFont;
            ctx.textAlign = 'right';
            ctx.textBaseline = 'middle';

            for (let axis = 0; axis < 3; axis++) {
                const yBase = (3 - axis - 0.5) * offset * scaleY;
                ctx.fillText(labels[axis], 28, yBase);
            }
            ctx.restore();
        }

        _renderIMUStacked(data) {
            const chips = this._normalizeIMUChips(data);
            if (chips.length === 0) return;

            const dpr = window.devicePixelRatio || 1;
            const ctx = this.ctx;
            const offset = this.getOffset();
            const scaleY = this.displayHeight / (3 * offset);
            const currentIndex = this.writeIndex;
            const metrics = this.getPlotMetrics();
            const currentX = this.getPlotX(currentIndex);
            const clearWidth = Math.max(3, (metrics.width / this.totalPoints) * 2);
            const dashStyles = [[], [5, 3], [1, 3], [6, 2, 1, 2]];

            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.clearRect(currentX * dpr, 0, clearWidth * dpr, this.canvas.height);
            ctx.scale(dpr, dpr);

            for (let c = 0; c < chips.length; c++) {
                const chip = chips[c];
                const chipIndex = Math.max(0, Math.min(3, chip.index || c));
                const dash = dashStyles[chipIndex] || [];

                for (let axis = 0; axis < 3; axis++) {
                    const value = chip.values[axis] || 0;
                    const yBase = (3 - axis - 0.5) * offset;
                    const y = (yBase - value) * scaleY;
                    const stateIndex = chipIndex * 3 + axis;

                    if (this.lastX[stateIndex] >= 0 && this.lastY[stateIndex] >= 0) {
                        if (Math.abs(currentX - this.lastX[stateIndex]) < this.displayWidth * 0.5) {
                            ctx.beginPath();
                            ctx.strokeStyle = this.colors[axis % this.colors.length];
                            ctx.lineWidth = RENDERER_CONFIG.LINE_WIDTH;
                            ctx.setLineDash(dash);
                            ctx.lineCap = 'round';
                            ctx.lineJoin = 'round';
                            ctx.moveTo(this.lastX[stateIndex], this.lastY[stateIndex]);
                            ctx.lineTo(currentX, y);
                            ctx.stroke();
                            ctx.setLineDash([]);
                        }
                    }

                    this.lastX[stateIndex] = currentX;
                    this.lastY[stateIndex] = y;
                }
            }

            this.writeIndex = (currentIndex + 1) % this.totalPoints;
            this._drawIMULabels();
        }

        _renderPointsStacked(data) {
            var offset = this.getOffset();
            var clampEnabled = this.getClampEnabled();
            var clipLimit = offset * 0.48;  // 供应商 clamp 阈值
            var pointsCount = data[0] ? data[0].length : 0;
            if (pointsCount === 0) return;

            var dpr = window.devicePixelRatio || 1;
            var ctx = this.ctx;
            var totalHeight = this.channels * offset;  // 总 uV 高度
            var scaleY = this.displayHeight / totalHeight;
            var metrics = this.getPlotMetrics();

            var channelSelect = this.getVisibleChannels();

            for (var i = 0; i < pointsCount; i++) {
                var currentIndex = this.writeIndex;
                var currentX = this.getPlotX(currentIndex);

                // Clear the current write column in the plot area.
                var clearWidth = Math.max(3, (metrics.width / this.totalPoints) * 2);
                ctx.setTransform(1, 0, 0, 1, 0, 0);
                ctx.clearRect(currentX * dpr, 0, clearWidth * dpr, this.canvas.height);
                ctx.scale(dpr, dpr);

                for (var ch = channelSelect.start; ch < channelSelect.end; ch++) {
                    var value = (data[ch] && data[ch][i] !== undefined) ? data[ch][i] : 0;

                    // Clamp: limit waveform amplitude within channel spacing
                    if (clampEnabled) {
                        value = Math.max(-clipLimit, Math.min(clipLimit, value));
                    }

                    // Use channel centers so edge channels keep half-row headroom.
                    var yBase = (this.channels - ch - 0.5) * offset;
                    var y = (yBase - value) * scaleY;

                    // 连接上一个点
                    if (this.lastX[ch] >= 0 && this.lastY[ch] >= 0) {
                        if (Math.abs(currentX - this.lastX[ch]) < this.displayWidth * 0.5) {
                            ctx.beginPath();
                            ctx.strokeStyle = this.colors[ch % this.colors.length];
                            ctx.lineWidth = RENDERER_CONFIG.LINE_WIDTH;
                            ctx.lineCap = 'round';
                            ctx.lineJoin = 'round';
                            ctx.moveTo(this.lastX[ch], this.lastY[ch]);
                            ctx.lineTo(currentX, y);
                            ctx.stroke();
                        }
                    }

                    this.lastX[ch] = currentX;
                    this.lastY[ch] = y;
                }

                this.writeIndex = (currentIndex + 1) % this.totalPoints;
            }

            // 重绘通道标签
            this.drawChannelLabels();
        }

        /**
         * 原有 banded 模式: 每个通道独立垂直 band
         */
        _renderPointsBanded(data) {
            var offset = this.getOffset();
            var visibleChannels = this.getVisibleChannels();
            var channelCount = visibleChannels.end - visibleChannels.start;

            var totalHeight = this.displayHeight;
            var channelSpacing = totalHeight / channelCount;
            var channelHeight = channelSpacing * 0.8;

            var pointsCount = data[0] ? data[0].length : 0;
            if (pointsCount === 0) return;

            var dpr = window.devicePixelRatio || 1;
            var ctx = this.ctx;

            for (var i = 0; i < pointsCount; i++) {
                var currentIndex = this.writeIndex;
                var currentX = (currentIndex / this.totalPoints) * this.displayWidth;

                var clearWidth = Math.max(3, (this.displayWidth / this.totalPoints) * 2);
                ctx.setTransform(1, 0, 0, 1, 0, 0);
                ctx.clearRect(currentX * dpr, 0, clearWidth * dpr, this.canvas.height);
                ctx.scale(dpr, dpr);

                for (var ch = visibleChannels.start; ch < visibleChannels.end; ch++) {
                    var value = data[ch] ? data[ch][i] : 0;
                    var displayIndex = ch - visibleChannels.start;
                    var centerY = channelSpacing * (displayIndex + 0.5);
                    var scale = channelHeight / (2 * offset);
                    var y = centerY - value * scale;

                    if (this.lastX[ch] >= 0 && this.lastY[ch] >= 0) {
                        if (Math.abs(currentX - this.lastX[ch]) < this.displayWidth * 0.5) {
                            ctx.beginPath();
                            ctx.strokeStyle = this.colors[ch % this.colors.length];
                            ctx.lineWidth = RENDERER_CONFIG.LINE_WIDTH;
                            ctx.lineCap = 'round';
                            ctx.lineJoin = 'round';
                            ctx.moveTo(this.lastX[ch], this.lastY[ch]);
                            ctx.lineTo(currentX, y);
                            ctx.stroke();
                        }
                    }
                    this.lastX[ch] = currentX;
                    this.lastY[ch] = y;
                }
                this.writeIndex = (currentIndex + 1) % this.totalPoints;
            }
        }

        /**
         * 获取当前写入位置（用于调试）
         */
        getWritePosition() {
            return this.writeIndex;
        }

        /**
         * 获取总点数（用于调试）
         */
        getTotalPoints() {
            return this.totalPoints;
        }
    }

    // ==================== 渲染器管理器 ====================
    /**
     * RendererManager - 管理所有渲染器实例
     */
    class RendererManager {
        constructor() {
            this.renderers = {};
        }

        /**
         * 创建EMG渲染器 (供应商堆叠模式 + clamp 支持)
         */
        createEMGRenderer(name, canvasId, containerId, pointerId, offsetInputId, channelSelectId, clampCheckboxId) {
            this.renderers[name] = new WaveformRenderer(canvasId, containerId, pointerId, {
                channels: RENDERER_CONFIG.EMG_CHANNELS,
                colors: RENDERER_CONFIG.COLORS.emg,
                offsetInputId: offsetInputId,
                channelSelectId: channelSelectId,
                clampCheckboxId: clampCheckboxId || null,
                stackedMode: true,     // 供应商堆叠视图
                type: 'emg'
            });
            return this.renderers[name];
        }

        /**
         * 创建IMU渲染器
         */
        createIMURenderer(name, canvasId, containerId, pointerId, offsetInputId) {
            this.renderers[name] = new WaveformRenderer(canvasId, containerId, pointerId, {
                channels: RENDERER_CONFIG.IMU_AXES,
                colors: RENDERER_CONFIG.COLORS.imu,
                offsetInputId: offsetInputId,
                imuStackedMode: true,
                signalKind: name.toLowerCase().includes('gyr') ? 'gyr' : 'acc',
                type: 'imu'
            });
            return this.renderers[name];
        }

        /**
         * 获取渲染器
         */
        get(name) {
            return this.renderers[name];
        }

        /**
         * 清除所有渲染器
         */
        clearAll() {
            Object.values(this.renderers).forEach(r => r.clear());
        }

        /**
         * 获取所有渲染器
         */
        getAll() {
            return this.renderers;
        }
    }

    // ==================== 导出到全局 ====================
    window.WaveformRenderer = WaveformRenderer;
    window.RendererManager = RendererManager;
    window.RENDERER_CONFIG = RENDERER_CONFIG;

})();
