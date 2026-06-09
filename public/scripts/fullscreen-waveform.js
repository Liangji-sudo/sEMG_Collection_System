/**
 * fullscreen-waveform.js - 信号全屏查看控制器
 *
 * 功能：
 * - 点击信号窗口的全屏按钮，弹出全屏模态窗口
 * - 在全屏窗口中实时显示该信号的波形
 * - 支持EMG1、EMG2、IMU1、IMU2全屏查看
 * - 复用现有的WaveformRenderer进行渲染
 */

(function() {
    'use strict';

    console.log('[FullscreenWaveform] 脚本加载开始...');

    class FullscreenWaveformController {
        constructor() {
            this.modal = null;
            this.canvas = null;
            this.container = null;
            this.pointer = null;
            this.closeBtn = null;
            this.controlsContainer = null;
            this.titleElement = null;

            this.currentSignal = null;  // 当前显示的信号类型: emg1, emg2, imu1, imu2
            this.renderer = null;       // WaveformRenderer 实例
            this.dataListener = null;   // 数据监听器

            this.init();
        }

        init() {
            console.log('[FullscreenWaveform] 初始化开始...');

            // 等待DOM加载完成
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', () => this.setup());
            } else {
                this.setup();
            }
        }

        setup() {
            // 获取DOM元素
            this.modal = document.getElementById('fullscreenWaveformModal');
            this.canvas = document.getElementById('fullscreenCanvas');
            this.container = document.getElementById('fullscreenCanvasContainer');
            this.pointer = document.getElementById('fullscreenPointer');
            this.closeBtn = document.getElementById('fullscreenCloseBtn');
            this.controlsContainer = document.getElementById('fullscreenControls');
            this.titleElement = document.getElementById('fullscreenModalTitle');

            if (!this.modal || !this.canvas) {
                console.error('[FullscreenWaveform] 找不到必要的DOM元素');
                return;
            }

            // 绑定事件
            this.bindEvents();

            console.log('[FullscreenWaveform] 初始化完成');
        }

        bindEvents() {
            // 关闭按钮
            if (this.closeBtn) {
                this.closeBtn.addEventListener('click', () => this.close());
            }

            // ESC键关闭
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && this.modal.classList.contains('active')) {
                    this.close();
                }
            });

            // 点击背景关闭
            this.modal.addEventListener('click', (e) => {
                if (e.target === this.modal) {
                    this.close();
                }
            });

            // 绑定所有全屏按钮
            document.querySelectorAll('.fullscreen-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const signal = btn.getAttribute('data-signal');
                    this.open(signal);
                });
            });
        }

        /**
         * 打开全屏窗口
         * @param {string} signal - 信号类型: emg1, emg2, imu1, imu2
         */
        open(signal) {
            console.log('[FullscreenWaveform] 打开全屏窗口:', signal);

            this.currentSignal = signal;

            // 更新标题
            const titles = {
                'emg1': 'EMG1 信号全屏查看 (16通道)',
                'emg2': 'EMG2 信号全屏查看 (16通道)',
                'imu1': 'IMU1 传感器全屏查看',
                'imu2': 'IMU2 传感器全屏查看'
            };
            if (this.titleElement) {
                this.titleElement.textContent = titles[signal] || '信号全屏查看';
            }

            // 创建控制项
            this.createControls(signal);

            // 创建渲染器
            this.createRenderer(signal);

            // 显示模态窗口
            this.modal.classList.add('active');

            // 开始监听数据
            this.startDataListener(signal);
        }

        /**
         * 关闭全屏窗口
         */
        close() {
            console.log('[FullscreenWaveform] 关闭全屏窗口');

            // 停止数据监听
            this.stopDataListener();

            // 清理渲染器
            if (this.renderer) {
                this.renderer.clear();
                this.renderer = null;
            }

            // 隐藏模态窗口
            this.modal.classList.remove('active');

            this.currentSignal = null;
        }

        /**
         * 创建控制项
         */
        createControls(signal) {
            if (!this.controlsContainer) return;

            this.controlsContainer.innerHTML = '';

            if (signal === 'emg1' || signal === 'emg2') {
                // EMG信号控制项
                this.controlsContainer.innerHTML = `
                    <div class="fullscreen-control-group">
                        <label>Offset:</label>
                        <input type="number" id="fullscreen-offset" value="300" min="50" max="10000" step="50">
                    </div>
                    <div class="fullscreen-control-group">
                        <label><input type="checkbox" id="fullscreen-clamp" checked> Clamp</label>
                    </div>
                    <div class="fullscreen-control-group">
                        <label>通道:</label>
                        <select id="fullscreen-channel">
                            <option value="all">全部</option>
                            <option value="1-8">1-8</option>
                            <option value="9-16">9-16</option>
                        </select>
                    </div>
                `;
            } else {
                // IMU信号控制项
                this.controlsContainer.innerHTML = `
                    <div class="fullscreen-control-group">
                        <label>加速度计 Offset:</label>
                        <input type="number" id="fullscreen-acc-offset" value="4.0" step="0.1" min="0" max="10">
                    </div>
                    <div class="fullscreen-control-group">
                        <label>陀螺仪 Offset:</label>
                        <input type="number" id="fullscreen-gyr-offset" value="600" step="10" min="0" max="2000">
                    </div>
                `;
            }
        }

        /**
         * 创建渲染器
         */
        createRenderer(signal) {
            if (signal === 'emg1' || signal === 'emg2') {
                // EMG渲染器
                this.renderer = new WaveformRenderer(
                    'fullscreenCanvas',
                    'fullscreenCanvasContainer',
                    'fullscreenPointer',
                    {
                        channels: 16,
                        colors: window.RENDERER_CONFIG.COLORS.emg,
                        offsetInputId: 'fullscreen-offset',
                        channelSelectId: 'fullscreen-channel',
                        clampCheckboxId: 'fullscreen-clamp',
                        stackedMode: true,
                        type: 'emg'
                    }
                );
            } else {
                // IMU渲染器 - 显示加速度计和陀螺仪
                // 暂时简化为只显示加速度计
                this.renderer = new WaveformRenderer(
                    'fullscreenCanvas',
                    'fullscreenCanvasContainer',
                    'fullscreenPointer',
                    {
                        channels: 3,
                        colors: window.RENDERER_CONFIG.COLORS.imu,
                        offsetInputId: 'fullscreen-acc-offset',
                        imuStackedMode: true,
                        signalKind: 'acc',
                        type: 'imu'
                    }
                );
            }

            console.log('[FullscreenWaveform] 渲染器创建完成');
        }

        /**
         * 开始监听数据
         */
        startDataListener(signal) {
            if (!window.waveformController) {
                console.warn('[FullscreenWaveform] waveformController 不存在');
                return;
            }

            const rm = window.waveformController.rendererManager;
            if (!rm) {
                console.warn('[FullscreenWaveform] rendererManager 不存在');
                return;
            }

            // 获取原始渲染器
            let sourceRenderer = null;
            if (signal === 'emg1') {
                sourceRenderer = rm.get('emg1');
            } else if (signal === 'emg2') {
                sourceRenderer = rm.get('emg2');
            } else if (signal === 'imu1') {
                sourceRenderer = rm.get('imu1Acc');
            } else if (signal === 'imu2') {
                sourceRenderer = rm.get('imu2Acc');
            }

            if (!sourceRenderer) {
                console.warn('[FullscreenWaveform] 找不到源渲染器:', signal);
                return;
            }

            // 拦截renderPoints方法
            const originalRenderPoints = sourceRenderer.renderPoints.bind(sourceRenderer);

            this.dataListener = (data) => {
                if (this.renderer && this.modal.classList.contains('active')) {
                    this.renderer.renderPoints(data);
                }
            };

            // 替换renderPoints方法
            sourceRenderer.renderPoints = (data) => {
                originalRenderPoints(data);
                this.dataListener(data);
            };

            // 保存原始方法以便恢复
            sourceRenderer._originalRenderPoints = originalRenderPoints;

            console.log('[FullscreenWaveform] 数据监听已启动');
        }

        /**
         * 停止数据监听
         */
        stopDataListener() {
            if (!this.currentSignal || !window.waveformController) return;

            const rm = window.waveformController.rendererManager;
            if (!rm) return;

            // 恢复原始renderPoints方法
            let sourceRenderer = null;
            if (this.currentSignal === 'emg1') {
                sourceRenderer = rm.get('emg1');
            } else if (this.currentSignal === 'emg2') {
                sourceRenderer = rm.get('emg2');
            } else if (this.currentSignal === 'imu1') {
                sourceRenderer = rm.get('imu1Acc');
            } else if (this.currentSignal === 'imu2') {
                sourceRenderer = rm.get('imu2Acc');
            }

            if (sourceRenderer && sourceRenderer._originalRenderPoints) {
                sourceRenderer.renderPoints = sourceRenderer._originalRenderPoints;
                delete sourceRenderer._originalRenderPoints;
            }

            this.dataListener = null;

            console.log('[FullscreenWaveform] 数据监听已停止');
        }
    }

    // ==================== 初始化 ====================
    let fullscreenController = null;

    function initController() {
        if (!fullscreenController) {
            fullscreenController = new FullscreenWaveformController();
            window.fullscreenWaveformController = fullscreenController;
            console.log('[FullscreenWaveform] 控制器已挂载到 window.fullscreenWaveformController');
        }
    }

    // 如果DOM已经加载，直接初始化
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initController);
    } else {
        initController();
    }

    console.log('[FullscreenWaveform] 脚本加载完成');

})();
