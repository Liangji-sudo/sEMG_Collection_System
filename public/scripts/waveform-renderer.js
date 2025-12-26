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
        // 渲染频率 (Hz) - 用于计算总点数
        RENDER_RATE: 100,
        // EMG每次渲染的数据点数 - 从9增加到18（加倍）
        EMG_POINTS_PER_RENDER: 18,
        // IMU每次渲染的数据点数
        IMU_POINTS_PER_RENDER: 1,
        // 显示窗口时长 (秒)
        WINDOW_DURATION: 5,
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
        }
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
            
            // 计算总数据点数
            if (this.type === 'emg') {
                this.totalPoints = RENDERER_CONFIG.RENDER_RATE * RENDERER_CONFIG.EMG_POINTS_PER_RENDER * RENDERER_CONFIG.WINDOW_DURATION;
            } else {
                this.totalPoints = RENDERER_CONFIG.RENDER_RATE * RENDERER_CONFIG.IMU_POINTS_PER_RENDER * RENDERER_CONFIG.WINDOW_DURATION;
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
            const resizeObserver = new ResizeObserver(() => {
                clearTimeout(resizeTimeout);
                resizeTimeout = setTimeout(() => {
                    this.resize();
                    this.clear();
                }, 100);
            });
            resizeObserver.observe(this.container);
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
         * 更新时间指针位置
         */
        updatePointer() {
            if (this.pointer) {
                const x = (this.writeIndex / this.totalPoints) * this.displayWidth;
                this.pointer.style.left = x + 'px';
            }
        }

        /**
         * 渲染数据点 - 核心接口
         * 
         * @param {Array<Array<number>>} data - 多通道数据
         *   格式: [[ch0_p1, ch0_p2, ...], [ch1_p1, ch1_p2, ...], ...]
         *   EMG: 16个通道，每个通道18个点（加倍后）
         *   IMU: 3个通道(xyz)，每个通道1个点
         */
        renderPoints(data) {
            if (!data || data.length === 0) return;
            
            const offset = this.getOffset();
            const visibleChannels = this.getVisibleChannels();
            const channelCount = visibleChannels.end - visibleChannels.start;
            
            const totalHeight = this.displayHeight;
            const channelSpacing = totalHeight / channelCount;
            const channelHeight = channelSpacing * 0.8;
            
            const pointsCount = data[0] ? data[0].length : 0;
            if (pointsCount === 0) return;
            
            const dpr = window.devicePixelRatio || 1;
            const ctx = this.ctx;
            
            // 逐点绘制
            for (let i = 0; i < pointsCount; i++) {
                const currentIndex = this.writeIndex;
                const currentX = (currentIndex / this.totalPoints) * this.displayWidth;
                
                // 清除当前位置前方的区域
                const clearWidth = Math.max(3, (this.displayWidth / this.totalPoints) * 2);
                ctx.setTransform(1, 0, 0, 1, 0, 0);
                ctx.clearRect(currentX * dpr, 0, clearWidth * dpr, this.canvas.height);
                ctx.scale(dpr, dpr);
                
                // 绘制每个可见通道
                for (let ch = visibleChannels.start; ch < visibleChannels.end; ch++) {
                    const value = data[ch] ? data[ch][i] : 0;
                    
                    const displayIndex = ch - visibleChannels.start;
                    const centerY = channelSpacing * (displayIndex + 0.5);
                    const scale = channelHeight / (2 * offset);
                    const y = centerY - value * scale;
                    
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
            
            this.updatePointer();
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
         * 创建EMG渲染器
         */
        createEMGRenderer(name, canvasId, containerId, pointerId, offsetInputId, channelSelectId) {
            this.renderers[name] = new WaveformRenderer(canvasId, containerId, pointerId, {
                channels: RENDERER_CONFIG.EMG_CHANNELS,
                colors: RENDERER_CONFIG.COLORS.emg,
                offsetInputId: offsetInputId,
                channelSelectId: channelSelectId,
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
