/**
 * continual-gesture-1-animation.js - 连续手势1采集动画模块
 * 
 * 【同心圆版】
 * 动画说明：
 * - 画面中心有一个半径100的外圆（目标圆）
 * - 引导同心圆：半径从0逐渐增加到100，引导区域是 r±5
 * - 用户通过鼠标滚轮控制另一个同心圆的半径（滚轮向后=半径增大）
 * - 受试者需要让自己控制的圆尽量保持在引导区域内
 * - 一次采集周期：引导圆从0→100（扩张），保持1秒，然后100→0（收缩）
 * 
 * Stage结构：
 * - 每个Stage包含多个Trial（采集周期）
 * - 每个Trial = 扩张阶段 + 保持阶段 + 收缩阶段
 */

(function() {
    'use strict';

    console.log('[ContinualGesture1Animation] 同心圆版动画模块开始加载...');

    class ContinualGesture1AnimationController {
        constructor() {
            // Canvas相关
            this.canvas = null;
            this.ctx = null;
            this.containerElement = null;
            
            // 任务状态
            this.isRunning = false;
            this.animationId = null;
            this.stageTimer = null;
            
            // 当前Stage信息
            this.currentStage = null;
            this.currentTaskId = 'continual_gesture_1';
            this.onComplete = null;
            this.onTrialComplete = null;
            
            // 同心圆参数（可配置）
            this.maxRadius = 150;              // 外圆最大半径
            this.guideBandWidth = 10;          // 引导区域宽度（半径±10）
            this.guideRadius = 0;              // 当前引导圆半径
            this.userRadius = 0;               // 用户控制的圆半径
            
            // 动画阶段
            this.phase = 'idle';               // 'idle' | 'expand' | 'hold' | 'contract'
            this.phaseStartTime = 0;           // 当前阶段开始时间
            this.expandDuration = 3000;        // 扩张阶段时长（ms）
            this.holdDuration = 1000;          // 保持阶段时长（ms）
            this.contractDuration = 3000;      // 收缩阶段时长（ms）
            
            // Trial计数
            this.trial = 0;                    // 当前Trial索引
            this.maxTrials = 5;                // 每个Stage的Trial数量
            this.stageTimeout = 120000;        // Stage超时时间（120秒）
            
            // 计时相关
            this.startTime = null;             // Stage开始时间
            this.remainingTime = 0;            // 剩余时间
            
            // 跟踪精度统计
            this.trackingData = [];            // 当前Trial的跟踪数据
            this.trialAccuracy = [];           // 每个Trial的平均精度
            
            // 配置
            this.config = {
                canvasWidth: 0,
                canvasHeight: 0,
                centerX: 0,
                centerY: 0,
                colors: {
                    background: 'rgba(248, 250, 252, 0.98)',
                    outerCircle: 'rgba(15, 23, 42, 0.12)',
                    outerCircleBorder: 'rgba(15, 23, 42, 0.25)',
                    guideZone: 'rgba(59, 130, 246, 0.20)',
                    guideZoneBorder: 'rgba(59, 130, 246, 0.5)',
                    userCircle: '#ef4444',
                    userCircleBorder: '#991b1b',
                    userCircleOnTarget: '#10b981',
                    userCircleOnTargetBorder: '#065f46',
                    text: '#1e40af',
                    muted: 'rgba(107, 114, 128, 0.9)',
                    success: '#10b981',
                    warning: '#f59e0b'
                }
            };
            
            // 绑定事件处理器
            this._wheelHandler = this.handleWheel.bind(this);
            this._resizeHandler = this.resizeCanvas.bind(this);

            // 输入接口
            this.inputInterface = null;
        }

        /**
         * 从配置加载
         */
        loadConfig() {
            console.log('[ContinualGesture1Animation] loadConfig() 开始');
            
            let templateConfig = null;
            
            // 第一优先级：直接从localStorage读取最新配置
            const saved = localStorage.getItem('emg_collection_template');
            if (saved) {
                try {
                    const template = JSON.parse(saved);
                    if (template.execution?.continual_gesture_1) {
                        templateConfig = template.execution.continual_gesture_1;
                        console.log('[ContinualGesture1Animation] 从localStorage读取配置:', templateConfig);
                    }
                } catch (e) {
                    console.warn('[ContinualGesture1Animation] 解析localStorage失败:', e);
                }
            }
            
            // 第二优先级：从templateConfigManager读取
            if (!templateConfig && window.templateConfigManager?.currentTemplate?.execution?.continual_gesture_1) {
                templateConfig = window.templateConfigManager.currentTemplate.execution.continual_gesture_1;
                console.log('[ContinualGesture1Animation] 从templateConfigManager读取配置');
            }
            
            // 第三优先级：从collectionController获取
            if (!templateConfig && window.collectionController?.currentExecutionParams) {
                const params = window.collectionController.currentExecutionParams;
                if (params.trialsPerStage !== undefined) {
                    templateConfig = params;
                    console.log('[ContinualGesture1Animation] 从collectionController读取配置');
                }
            }
            
            if (templateConfig) {
                this.maxTrials = templateConfig.trialsPerStage || 5;
                this.stageTimeout = (templateConfig.stageTimeout || 120) * 1000;
                this.restBetweenTrials = templateConfig.restBetweenTrials || 1;  // 【新增】试次间隔休息时间

                // 同心圆动画配置
                if (templateConfig.expandDuration !== undefined) {
                    this.expandDuration = templateConfig.expandDuration * 1000;
                }
                if (templateConfig.holdDuration !== undefined) {
                    this.holdDuration = templateConfig.holdDuration * 1000;
                }
                if (templateConfig.contractDuration !== undefined) {
                    this.contractDuration = templateConfig.contractDuration * 1000;
                }
                if (templateConfig.guideBandWidth !== undefined) {
                    this.guideBandWidth = templateConfig.guideBandWidth;
                }
                if (templateConfig.maxRadius !== undefined) {
                    this.maxRadius = templateConfig.maxRadius;
                }

                console.log('[ContinualGesture1Animation] 配置加载完成:', {
                    maxTrials: this.maxTrials,
                    stageTimeout: this.stageTimeout,
                    restBetweenTrials: this.restBetweenTrials,  // 【新增】
                    expandDuration: this.expandDuration,
                    holdDuration: this.holdDuration,
                    contractDuration: this.contractDuration,
                    guideBandWidth: this.guideBandWidth,
                    maxRadius: this.maxRadius
                });
            } else {
                console.log('[ContinualGesture1Animation] 未找到配置，使用默认值');
            }
        }

        /**
         * 初始化Canvas
         */
        init(containerSelector) {
            this.loadConfig();
            
            this.containerElement = document.querySelector('.animation-area') ||
                                    document.querySelector(containerSelector) || 
                                    document.getElementById('gestureDisplay');
            
            if (!this.containerElement) {
                console.warn('[ContinualGesture1Animation] 未找到容器元素');
                return false;
            }
            
            const containerStyle = window.getComputedStyle(this.containerElement);
            if (containerStyle.position === 'static') {
                this.containerElement.style.position = 'relative';
            }
            
            // 创建或获取Canvas
            this.canvas = document.getElementById('continualGesture1Canvas');
            if (!this.canvas) {
                this.canvas = document.createElement('canvas');
                this.canvas.id = 'continualGesture1Canvas';
                this.canvas.style.cssText = `
                    position: absolute;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    z-index: 50;
                    background: ${this.config.colors.background};
                `;
                this.containerElement.appendChild(this.canvas);
            }
            
            // 让canvas可以获取焦点以接收滚轮事件
            this.canvas.tabIndex = 0;
            this.canvas.style.outline = 'none';
            
            this.ctx = this.canvas.getContext('2d');
            this.resizeCanvas();
            
            // 绑定事件
            window.addEventListener('resize', this._resizeHandler);
            
            console.log('[ContinualGesture1Animation] 初始化完成');
            return true;
        }

        /**
         * 调整Canvas大小
         */
        resizeCanvas() {
            if (!this.canvas || !this.containerElement) return false;
            
            const rect = this.containerElement.getBoundingClientRect();
            
            if (rect.width > 0 && rect.height > 0) {
                const dpr = window.devicePixelRatio || 1;
                
                this.canvas.width = rect.width * dpr;
                this.canvas.height = rect.height * dpr;
                this.canvas.style.width = rect.width + 'px';
                this.canvas.style.height = rect.height + 'px';
                this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
                
                this.config.canvasWidth = rect.width;
                this.config.canvasHeight = rect.height;

                // 计算圆心位置（画布中心偏上一点，留出底部信息区域）
                this.config.centerX = rect.width / 2;
                this.config.centerY = (rect.height - 60) / 2 + 30; // 上方留30px，下方留60px

                // 根据画布大小计算可用的最大半径
                const maxPossibleRadius = Math.min(
                    this.config.centerX - 20,           // 左右边距20px
                    this.config.centerY - 50,           // 上边距50px（留给标题）
                    rect.height - this.config.centerY - 70  // 下边距70px（留给进度信息）
                );
                // 【修复】不再硬编码150的上限，使用配置的maxRadius或画布允许的最大值
                // 如果配置的maxRadius超过画布允许的范围，则使用画布允许的最大值
                const configuredMaxRadius = this.maxRadius || 150;
                this.maxRadius = Math.max(80, Math.min(configuredMaxRadius, maxPossibleRadius));

                return true;
            }
            return false;
        }

        /**
         * 重置状态
         */
        reset() {
            console.log('[ContinualGesture1Animation] 重置状态');
            this.trial = 0;
            this.guideRadius = 0;
            this.userRadius = 0;
            this.phase = 'idle';
            this.trackingData = [];
            this.trialAccuracy = [];
        }

        /**
         * 开始Stage动画
         */
        start(stage, onComplete, onTrialComplete, executionParams) {
            console.log('[ContinualGesture1Animation] ====== 开始Stage动画 ======');
            console.log('[ContinualGesture1Animation] stage:', stage.name);

            // 获取输入接口
            this.inputInterface = window.animationInputInterface || null;
            if (this.inputInterface) {
                this.inputInterface.setCurrentTask('continual_gesture_1');
            }

            if (!this.canvas) {
                if (!this.init('.animation-area')) {
                    console.error('[ContinualGesture1Animation] Canvas初始化失败');
                    if (onComplete) onComplete();
                    return;
                }
            }
            
            // 先加载配置
            this.loadConfig();
            
            // 如果传入了executionParams，使用它来覆盖配置
            if (executionParams) {
                console.log('[ContinualGesture1Animation] 使用传入的executionParams:', executionParams);
                if (executionParams.trialsPerStage !== undefined) {
                    this.maxTrials = executionParams.trialsPerStage;
                }
                if (executionParams.stageTimeout !== undefined) {
                    this.stageTimeout = executionParams.stageTimeout * 1000;
                }
                if (executionParams.restBetweenTrials !== undefined) {
                    this.restBetweenTrials = executionParams.restBetweenTrials;  // 【新增】
                }
                if (executionParams.expandDuration !== undefined) {
                    this.expandDuration = executionParams.expandDuration * 1000;
                }
                if (executionParams.holdDuration !== undefined) {
                    this.holdDuration = executionParams.holdDuration * 1000;
                }
                if (executionParams.contractDuration !== undefined) {
                    this.contractDuration = executionParams.contractDuration * 1000;
                }
                if (executionParams.guideBandWidth !== undefined) {
                    this.guideBandWidth = executionParams.guideBandWidth;
                }
                if (executionParams.maxRadius !== undefined) {
                    this.maxRadius = executionParams.maxRadius;
                }
            }

            console.log('[ContinualGesture1Animation] ★★★ 最终参数 ★★★');
            console.log('[ContinualGesture1Animation] maxTrials:', this.maxTrials);
            console.log('[ContinualGesture1Animation] restBetweenTrials:', this.restBetweenTrials);  // 【新增】
            console.log('[ContinualGesture1Animation] maxRadius:', this.maxRadius);
            console.log('[ContinualGesture1Animation] guideBandWidth:', this.guideBandWidth);
            
            this.currentStage = stage;
            this.onComplete = onComplete;
            this.onTrialComplete = onTrialComplete;
            
            // 重置状态
            this.reset();
            this.isRunning = true;
            this.startTime = Date.now();
            this.remainingTime = this.stageTimeout;
            
            // 调整Canvas
            this.resizeCanvas();
            this.canvas.style.display = 'block';
            this.canvas.focus();
            
            // 绑定滚轮事件
            this.canvas.addEventListener('wheel', this._wheelHandler, { passive: false });

            // 【修复】延迟开始第一个Trial，等待H5文件打开
            // stage_start 命令是异步的，需要等文件创建完成
            setTimeout(() => {
                if (this.isRunning) {
                    this.startTrial();
                }
            }, 200);
            
            // 设置超时计时器
            this.stageTimer = setTimeout(() => {
                console.log('[ContinualGesture1Animation] Stage超时');
                this.completeStage();
            }, this.stageTimeout);
            
            // 开始动画循环
            this.animate();
        }

        /**
         * 开始一个新的Trial（采集周期）
         */
        startTrial() {
            console.log(`[ContinualGesture1Animation] 开始Trial ${this.trial + 1}/${this.maxTrials}`);

            // 【新增】发送 prompt: start
            if (window.collectionController) {
                window.collectionController.sendToRealtimeEngine('prompt', {
                    name: 'start',
                    stageName: this.currentStage?.name || 'unknown',
                    trialIndex: this.trial,
                    timestamp: Date.now()
                });
            }

            this.phase = 'expand';
            this.phaseStartTime = performance.now();
            this.guideRadius = 0;
            this.trackingData = [];
        }

        /**
         * 更新引导圆半径（根据当前阶段和时间）
         */
        updateGuideRadius() {
            const now = performance.now();
            const elapsed = now - this.phaseStartTime;
            
            switch (this.phase) {
                case 'expand':
                    // 从0扩张到maxRadius
                    const expandProgress = Math.min(1, elapsed / this.expandDuration);
                    this.guideRadius = this.maxRadius * this.easeInOutSine(expandProgress);
                    
                    if (elapsed >= this.expandDuration) {
                        this.phase = 'hold';
                        this.phaseStartTime = now;
                        this.guideRadius = this.maxRadius;
                    }
                    break;
                    
                case 'hold':
                    // 保持在maxRadius
                    this.guideRadius = this.maxRadius;
                    
                    if (elapsed >= this.holdDuration) {
                        this.phase = 'contract';
                        this.phaseStartTime = now;
                    }
                    break;
                    
                case 'contract':
                    // 从maxRadius收缩到0
                    const contractProgress = Math.min(1, elapsed / this.contractDuration);
                    this.guideRadius = this.maxRadius * (1 - this.easeInOutSine(contractProgress));
                    
                    if (elapsed >= this.contractDuration) {
                        this.completeTrial();
                    }
                    break;
            }
        }

        /**
         * 缓动函数：easeInOutSine
         */
        easeInOutSine(t) {
            return -(Math.cos(Math.PI * t) - 1) / 2;
        }

        /**
         * 完成当前Trial
         */
        completeTrial() {
            // 【新增】发送 prompt: end
            if (window.collectionController) {
                window.collectionController.sendToRealtimeEngine('prompt', {
                    name: 'end',
                    stageName: this.currentStage?.name || 'unknown',
                    trialIndex: this.trial,
                    timestamp: Date.now()
                });
            }

            // 计算本次Trial的平均跟踪精度
            let accuracy = 0;
            if (this.trackingData.length > 0) {
                const totalError = this.trackingData.reduce((sum, d) => sum + Math.abs(d.error), 0);
                accuracy = 1 - (totalError / this.trackingData.length / this.maxRadius);
                accuracy = Math.max(0, Math.min(1, accuracy));
            }
            this.trialAccuracy.push(accuracy);

            console.log(`[ContinualGesture1Animation] Trial ${this.trial + 1} 完成, 精度: ${(accuracy * 100).toFixed(1)}%`);

            this.trial++;

            // 调用试次完成回调
            if (this.onTrialComplete) {
                this.onTrialComplete(this.trial - 1);
            }

            // 检查是否完成所有Trial
            if (this.trial >= this.maxTrials) {
                console.log('[ContinualGesture1Animation] 所有Trial完成');
                this.completeStage();
            } else {
                // 【修改】使用配置的试次间隔休息时间
                this.phase = 'rest';
                const restTime = (this.restBetweenTrials || 1) * 1000;  // 默认1秒
                console.log(`[ContinualGesture1Animation] 休息 ${restTime/1000} 秒后开始下一个Trial`);

                setTimeout(() => {
                    if (this.isRunning) {
                        this.startTrial();
                    }
                }, restTime);
            }
        }

        /**
         * 完成Stage
         */
        completeStage() {
            const avgAccuracy = this.trialAccuracy.length > 0 
                ? this.trialAccuracy.reduce((a, b) => a + b, 0) / this.trialAccuracy.length 
                : 0;
            console.log(`[ContinualGesture1Animation] Stage完成: ${this.trial} Trials, 平均精度 ${(avgAccuracy * 100).toFixed(1)}%`);
            this.stop();
            if (this.onComplete) {
                this.onComplete();
            }
        }

        /**
         * 处理滚轮事件
         */
        handleWheel(e) {
            if (!this.isRunning) return;
            
            e.preventDefault();
            e.stopPropagation();
            
            // 标准化delta
            let multiplier = 0.5; // pixels
            if (e.deltaMode === 1) multiplier = 5; // lines
            if (e.deltaMode === 2) multiplier = 20; // pages
            
            const rawDelta = e.deltaY * multiplier;
            const delta = Math.max(-5, Math.min(5, rawDelta));
            
            // 滚轮向下（正值）= 半径增大
            this.userRadius = Math.max(0, Math.min(this.maxRadius, this.userRadius + delta));
            
            // 记录跟踪数据
            if (this.phase !== 'idle') {
                const error = this.userRadius - this.guideRadius;
                this.trackingData.push({
                    time: performance.now(),
                    guideRadius: this.guideRadius,
                    userRadius: this.userRadius,
                    error: error
                });
            }
        }

        /**
         * 检查用户圆是否在引导区域内
         */
        isOnTarget() {
            if (this.phase === 'idle') return false;
            const diff = Math.abs(this.userRadius - this.guideRadius);
            return diff <= this.guideBandWidth;
        }

        /**
         * 动画循环
         */
        animate() {
            if (!this.isRunning) return;

            // 更新剩余时间
            this.remainingTime = Math.max(0, this.stageTimeout - (Date.now() - this.startTime));

            // 更新引导圆半径
            this.updateGuideRadius();

            // 从输入接口更新用户控制的半径
            if (this.inputInterface && this.inputInterface.isCalibrated()) {
                const normalizedInput = this.inputInterface.getNormalizedInput();
                this.userRadius = normalizedInput * this.maxRadius;
            }

            // 清除画布
            this.ctx.clearRect(0, 0, this.config.canvasWidth, this.config.canvasHeight);

            // 绘制所有元素
            this.drawStageInfo();
            this.drawOuterCircle();
            this.drawGuideZone();
            this.drawUserCircle();
            this.drawProgress();
            this.drawInstructions();

            // 继续下一帧
            this.animationId = requestAnimationFrame(() => this.animate());
        }

        /**
         * 绘制Stage信息
         */
        drawStageInfo() {
            const ctx = this.ctx;
            const stageConfig = this.currentStage;
            
            let stageLabel = stageConfig?.label || stageConfig?.name || '连续手势采集';
            let stageIcon = stageConfig?.icon || '🎯';
            
            ctx.save();
            
            // 左上角显示当前Stage名称
            ctx.fillStyle = this.config.colors.text;
            ctx.font = '700 22px ui-sans-serif, system-ui, -apple-system';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'top';
            ctx.fillText(`${stageIcon} ${stageLabel}`, 20, 20);
            
            // 显示指导文字
            ctx.fillStyle = this.config.colors.muted;
            ctx.font = '500 14px ui-sans-serif, system-ui';
            ctx.fillText('滚动滚轮控制圆的大小，跟随蓝色引导区域', 20, 48);
            
            // 显示当前阶段
            const phaseText = {
                'idle': '准备中...',
                'expand': '扩张阶段 ↗',
                'hold': '保持阶段 ●',
                'contract': '收缩阶段 ↘'
            };
            ctx.fillStyle = this.config.colors.text;
            ctx.font = '600 16px ui-sans-serif, system-ui';
            ctx.fillText(phaseText[this.phase] || '', 20, 72);
            
            // 右上角显示倒计时
            const seconds = Math.ceil(this.remainingTime / 1000);
            const timeColor = seconds <= 10 ? this.config.colors.warning : this.config.colors.muted;
            ctx.fillStyle = timeColor;
            ctx.font = '600 18px ui-sans-serif, system-ui';
            ctx.textAlign = 'right';
            ctx.fillText(`⏱ ${seconds}s`, this.config.canvasWidth - 20, 24);
            
            ctx.restore();
        }

        /**
         * 绘制外圆（目标边界）
         */
        drawOuterCircle() {
            const ctx = this.ctx;
            const cx = this.config.centerX;
            const cy = this.config.centerY;
            
            ctx.save();
            
            // 外圆背景
            ctx.fillStyle = this.config.colors.outerCircle;
            ctx.strokeStyle = this.config.colors.outerCircleBorder;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(cx, cy, this.maxRadius, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
            
            // 中心点
            ctx.fillStyle = this.config.colors.outerCircleBorder;
            ctx.beginPath();
            ctx.arc(cx, cy, 3, 0, Math.PI * 2);
            ctx.fill();
            
            ctx.restore();
        }

        /**
         * 绘制引导区域（带状区域）
         */
        drawGuideZone() {
            if (this.phase === 'idle') return;
            
            const ctx = this.ctx;
            const cx = this.config.centerX;
            const cy = this.config.centerY;
            
            const innerR = Math.max(0, this.guideRadius - this.guideBandWidth);
            const outerR = Math.min(this.maxRadius, this.guideRadius + this.guideBandWidth);
            
            ctx.save();
            
            // 绘制引导带（环形区域）
            ctx.fillStyle = this.config.colors.guideZone;
            ctx.strokeStyle = this.config.colors.guideZoneBorder;
            ctx.lineWidth = 2;
            
            ctx.beginPath();
            // 外圆（顺时针）
            ctx.arc(cx, cy, outerR, 0, Math.PI * 2);
            // 内圆（逆时针，形成环形）
            ctx.arc(cx, cy, innerR, Math.PI * 2, 0, true);
            ctx.closePath();
            ctx.fill();
            
            // 绘制引导圆的中心线
            ctx.strokeStyle = this.config.colors.guideZoneBorder;
            ctx.lineWidth = 2;
            ctx.setLineDash([5, 5]);
            ctx.beginPath();
            ctx.arc(cx, cy, this.guideRadius, 0, Math.PI * 2);
            ctx.stroke();
            ctx.setLineDash([]);
            
            // 显示引导圆半径值
            ctx.fillStyle = this.config.colors.text;
            ctx.font = '600 12px ui-sans-serif, system-ui';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            const labelX = cx + this.guideRadius + 10;
            const labelY = cy;
            if (labelX < this.config.canvasWidth - 50) {
                ctx.fillText(`r=${this.guideRadius.toFixed(0)}`, labelX, labelY);
            }
            
            ctx.restore();
        }

        /**
         * 绘制用户控制的圆
         */
        drawUserCircle() {
            const ctx = this.ctx;
            const cx = this.config.centerX;
            const cy = this.config.centerY;
            
            const isOnTarget = this.isOnTarget();
            
            ctx.save();
            
            // 用户圆
            ctx.strokeStyle = isOnTarget 
                ? this.config.colors.userCircleOnTargetBorder 
                : this.config.colors.userCircleBorder;
            ctx.lineWidth = 3;
            
            // 添加发光效果（在目标区域内时）
            if (isOnTarget) {
                ctx.shadowColor = this.config.colors.userCircleOnTarget;
                ctx.shadowBlur = 10;
            }
            
            ctx.beginPath();
            ctx.arc(cx, cy, this.userRadius, 0, Math.PI * 2);
            ctx.stroke();
            
            // 重置阴影
            ctx.shadowColor = 'transparent';
            ctx.shadowBlur = 0;
            
            // 在圆上绘制一个小标记点（方便看到圆的位置，即使半径很小）
            const markerAngle = -Math.PI / 2; // 顶部
            const markerX = cx + this.userRadius * Math.cos(markerAngle);
            const markerY = cy + this.userRadius * Math.sin(markerAngle);
            
            ctx.fillStyle = isOnTarget 
                ? this.config.colors.userCircleOnTarget 
                : this.config.colors.userCircle;
            ctx.beginPath();
            ctx.arc(markerX, markerY, 6, 0, Math.PI * 2);
            ctx.fill();
            
            ctx.restore();
        }

        /**
         * 绘制进度信息
         */
        drawProgress() {
            const ctx = this.ctx;
            const bottomY = this.config.canvasHeight - 20;
            
            ctx.save();
            
            // 右下角显示进度
            ctx.fillStyle = 'rgba(0, 0, 0, 0.75)';
            ctx.font = '700 16px ui-sans-serif, system-ui';
            ctx.textAlign = 'right';
            ctx.textBaseline = 'bottom';
            ctx.fillText(`Trial ${this.trial} / ${this.maxTrials}`, this.config.canvasWidth - 20, bottomY);
            
            // 进度条
            const barWidth = 150;
            const barHeight = 8;
            const barX = this.config.canvasWidth - 20 - barWidth;
            const barY = bottomY - 30;
            const progress = this.maxTrials > 0 ? this.trial / this.maxTrials : 0;
            
            // 进度条背景
            ctx.fillStyle = '#e5e7eb';
            ctx.beginPath();
            ctx.roundRect(barX, barY, barWidth, barHeight, 4);
            ctx.fill();
            
            // 进度条填充
            ctx.fillStyle = this.config.colors.success;
            ctx.beginPath();
            ctx.roundRect(barX, barY, barWidth * progress, barHeight, 4);
            ctx.fill();
            
            // 左下角显示当前状态信息
            ctx.fillStyle = this.config.colors.muted;
            ctx.font = '500 14px ui-sans-serif, system-ui';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'bottom';
            
            const isOnTarget = this.isOnTarget();
            const statusText = isOnTarget ? '✓ 在引导区域内' : '○ 调整圆的大小';
            const errorText = this.phase !== 'idle' 
                ? `误差: ${(this.userRadius - this.guideRadius).toFixed(1)}` 
                : '';
            
            ctx.fillText(`用户: ${this.userRadius.toFixed(0)} | 引导: ${this.guideRadius.toFixed(0)} | ${errorText}`, 20, bottomY - 20);
            
            ctx.fillStyle = isOnTarget ? this.config.colors.success : this.config.colors.muted;
            ctx.fillText(statusText, 20, bottomY);
            
            ctx.restore();
        }

        /**
         * 绘制操作说明
         */
        drawInstructions() {
            const ctx = this.ctx;
            const cx = this.config.centerX;
            const bottomY = this.config.centerY + this.maxRadius + 20;
            
            // 如果说明文字会超出画布，就不绘制
            if (bottomY > this.config.canvasHeight - 80) return;
            
            ctx.save();
            
            ctx.fillStyle = this.config.colors.muted;
            ctx.font = '500 12px ui-sans-serif, system-ui';
            ctx.textAlign = 'center';
            
            ctx.fillText('↓ 滚轮向下：圆变大 | ↑ 滚轮向上：圆变小', cx, bottomY);
            
            ctx.restore();
        }

        /**
         * 停止动画
         */
        stop() {
            console.log('[ContinualGesture1Animation] 停止动画');
            this.isRunning = false;
            
            // 清除计时器
            if (this.animationId) {
                cancelAnimationFrame(this.animationId);
                this.animationId = null;
            }
            if (this.stageTimer) {
                clearTimeout(this.stageTimer);
                this.stageTimer = null;
            }
            
            // 移除事件监听
            if (this.canvas) {
                this.canvas.removeEventListener('wheel', this._wheelHandler);
            }
            
            // 清除画布
            if (this.ctx && this.config.canvasWidth && this.config.canvasHeight) {
                this.ctx.clearRect(0, 0, this.config.canvasWidth, this.config.canvasHeight);
            }
            
            // 隐藏Canvas
            if (this.canvas) {
                this.canvas.style.display = 'none';
            }
        }

        /**
         * 销毁
         */
        destroy() {
            this.stop();
            
            window.removeEventListener('resize', this._resizeHandler);
            
            if (this.canvas && this.canvas.parentElement) {
                this.canvas.parentElement.removeChild(this.canvas);
            }
            
            this.canvas = null;
            this.ctx = null;
            this.containerElement = null;
        }

        /**
         * 检查动画是否在运行
         */
        isAnimationRunning() {
            return this.isRunning;
        }

        /**
         * 获取进度信息
         */
        getProgress() {
            return {
                trial: this.trial,
                maxTrials: this.maxTrials,
                phase: this.phase,
                percent: this.maxTrials > 0 ? (this.trial / this.maxTrials * 100) : 0,
                remainingTime: this.remainingTime,
                accuracy: this.trialAccuracy
            };
        }
    }

    // 创建全局实例
    const continualGesture1Animation = new ContinualGesture1AnimationController();
    window.continualGesture1Animation = continualGesture1Animation;
    
    console.log('[ContinualGesture1Animation] 同心圆版动画模块加载完成');

})();
