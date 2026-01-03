/**
 * continual-gesture-1-animation.js - 连续手势1采集动画模块
 * 
 * 概念说明：
 * - Stage: 一个采集阶段（如"滚轮光标任务"）
 * - 与离散手势不同，这里不使用Prompt序列，而是使用滚轮控制光标任务
 * - 每个Stage包含多个目标点（Trial），用户通过滚轮移动光标到目标点
 * 
 * 任务规则：
 * - 目标点出现在10个不同位置
 * - 用户通过滚轮移动光标到目标点
 * - 光标停留在目标区域500ms视为命中
 * - 完成所有目标或120s超时后进入下一个Stage
 * - Stage内不向realtimeEngine发送prompt消息
 */

(function() {
    'use strict';

    console.log('[ContinualGesture1Animation] 连续手势1动画模块开始加载...');

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
            
            // 滚轮光标任务状态
            this.cursorPos = 0.5;           // 光标位置 (0-1)
            this.targetPos = null;          // 目标位置 (0-1)
            this.trial = 0;                 // 当前Trial索引
            this.hits = 0;                  // 命中数
            this.maxTrials = 10;            // 每个Stage的目标数量
            this.stageTimeout = 120000;     // Stage超时时间（120秒）
            this.dwellMs = 500;             // 停留时间阈值
            this.onTargetSince = null;      // 开始停留的时间
            this.dwellTimer = null;         // 停留计时器
            this.fillTimer = null;          // 填充动画计时器
            this.fillProgress = 0;          // 填充进度 (0-1)
            
            // 目标区域配置
            this.targetFrac = 0.12;         // 目标高度占轨道的比例
            this.targetH = 0;               // 目标高度（像素）
            
            // 轨道配置
            this.trackPadding = 20;         // 轨道上下内边距
            this.trackWidth = 40;           // 轨道宽度
            this.cursorSize = 20;           // 光标大小
            
            // 计时相关
            this.startTime = null;          // Stage开始时间
            this.remainingTime = 0;         // 剩余时间
            
            // 配置
            this.config = {
                canvasWidth: 0,
                canvasHeight: 0,
                trackTop: 0,
                trackBottom: 0,
                trackHeight: 0,
                trackCenterX: 0,
                colors: {
                    track: 'rgba(15, 23, 42, 0.06)',
                    trackBorder: 'rgba(15, 23, 42, 0.15)',
                    target: 'rgba(59, 130, 246, 0.25)',
                    targetActive: 'rgba(59, 130, 246, 0.4)',
                    targetBorder: 'transparent',
                    targetBorderActive: '#111827',
                    cursor: '#ef4444',
                    cursorBorder: '#991b1b',
                    cursorFill: 'rgba(0, 0, 0, 0.85)',
                    text: '#1e40af',
                    muted: 'rgba(107, 114, 128, 0.9)',
                    success: '#10b981',
                    warning: '#f59e0b'
                }
            };
            
            // 绑定事件处理器
            this._wheelHandler = this.handleWheel.bind(this);
            this._resizeHandler = this.resizeCanvas.bind(this);
        }

        /**
         * 从配置加载
         */
        loadConfig() {
            if (window.CONTINUAL_GESTURE_1_CONFIG) {
                const taskConfig = window.CONTINUAL_GESTURE_1_CONFIG;
                
                // 可以从配置读取自定义参数
                if (taskConfig.WHEEL_TASK) {
                    const wt = taskConfig.WHEEL_TASK;
                    this.maxTrials = wt.MAX_TRIALS || 10;
                    this.stageTimeout = wt.STAGE_TIMEOUT || 120000;
                    this.dwellMs = wt.DWELL_MS || 500;
                    this.targetFrac = wt.TARGET_FRAC || 0.12;
                }
                
                console.log('[ContinualGesture1Animation] 配置已加载');
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
                    background: rgba(248, 250, 252, 0.98);
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
                
                // 计算轨道位置
                this.config.trackTop = this.trackPadding + 80; // 留出顶部信息区域
                this.config.trackBottom = rect.height - this.trackPadding - 60; // 留出底部信息区域
                this.config.trackHeight = this.config.trackBottom - this.config.trackTop;
                this.config.trackCenterX = rect.width / 2;
                
                // 计算目标高度
                this.targetH = Math.max(36, Math.round(this.config.trackHeight * this.targetFrac));
                
                return true;
            }
            return false;
        }

        /**
         * 开始Stage动画
         * @param {Object} stage - stage配置
         * @param {Function} onComplete - 完成回调
         * @param {Function} onPromptTriggered - prompt触发回调（连续手势不使用）
         */
        start(stage, onComplete, onPromptTriggered) {
            console.log('[ContinualGesture1Animation] 开始Stage动画:', stage.name);
            
            if (!this.canvas) {
                if (!this.init('.animation-area')) {
                    console.error('[ContinualGesture1Animation] Canvas初始化失败');
                    if (onComplete) onComplete();
                    return;
                }
            }
            
            this.currentStage = stage;
            this.onComplete = onComplete;
            
            // 重置状态
            this.cursorPos = 0.5;
            this.targetPos = null;
            this.trial = 0;
            this.hits = 0;
            this.onTargetSince = null;
            this.fillProgress = 0;
            this.isRunning = true;
            this.startTime = Date.now();
            this.remainingTime = this.stageTimeout;
            
            // 调整Canvas
            this.resizeCanvas();
            this.canvas.style.display = 'block';
            this.canvas.focus();
            
            // 绑定滚轮事件
            this.canvas.addEventListener('wheel', this._wheelHandler, { passive: false });
            
            // 设置第一个目标
            this.setRandomTarget();
            
            // 设置超时计时器
            this.stageTimer = setTimeout(() => {
                console.log('[ContinualGesture1Animation] Stage超时');
                this.completeStage();
            }, this.stageTimeout);
            
            // 开始动画循环
            this.animate();
        }

        /**
         * 随机选择目标位置
         */
        pickRandomTargetPos(prev) {
            const minEdge = 0.08;
            const maxEdge = 0.92;
            const minDeltaFromPrev = 0.20;
            
            for (let i = 0; i < 80; i++) {
                const v = minEdge + Math.random() * (maxEdge - minEdge);
                if (prev == null || Math.abs(v - prev) >= minDeltaFromPrev) return v;
            }
            return minEdge + Math.random() * (maxEdge - minEdge);
        }

        /**
         * 设置随机目标
         */
        setRandomTarget() {
            this.targetPos = this.pickRandomTargetPos(this.targetPos);
            this.stopDwell();
            console.log(`[ContinualGesture1Animation] 新目标: Trial ${this.trial + 1}, 位置 ${this.targetPos.toFixed(2)}`);
        }

        /**
         * 处理滚轮事件
         */
        handleWheel(e) {
            if (!this.isRunning) return;
            
            e.preventDefault();
            e.stopPropagation();
            
            // 标准化delta
            let multiplier = 0.001; // pixels
            if (e.deltaMode === 1) multiplier = 0.015; // lines
            if (e.deltaMode === 2) multiplier = 0.25;  // pages
            
            const rawDelta = e.deltaY * multiplier;
            const delta = Math.max(-0.06, Math.min(0.06, rawDelta));
            
            this.cursorPos = Math.max(0, Math.min(1, this.cursorPos + delta));
            
            // 检查是否在目标区域
            if (this.isOnTarget()) {
                this.startDwell();
            } else {
                this.stopDwell();
            }
        }

        /**
         * 获取光标屏幕Y坐标
         */
        getCursorScreenY() {
            return this.config.trackTop + this.cursorPos * this.config.trackHeight;
        }

        /**
         * 获取目标区域的上下边界
         */
        getTargetBounds() {
            if (this.targetPos == null) return null;
            
            const targetCenterY = this.config.trackTop + this.targetPos * this.config.trackHeight;
            const halfH = this.targetH / 2;
            
            return {
                top: Math.max(this.config.trackTop, targetCenterY - halfH),
                bottom: Math.min(this.config.trackBottom, targetCenterY + halfH),
                centerY: targetCenterY
            };
        }

        /**
         * 检查光标是否在目标区域内
         */
        isOnTarget() {
            const bounds = this.getTargetBounds();
            if (!bounds) return false;
            
            const cursorY = this.getCursorScreenY();
            return cursorY >= bounds.top && cursorY <= bounds.bottom;
        }

        /**
         * 开始停留计时
         */
        startDwell() {
            if (this.onTargetSince != null) return;
            
            this.onTargetSince = performance.now();
            
            // 填充动画
            this.fillTimer = setInterval(() => {
                if (this.onTargetSince == null) return;
                const elapsed = performance.now() - this.onTargetSince;
                this.fillProgress = Math.max(0, Math.min(1, elapsed / this.dwellMs));
            }, 20);
            
            // 停留成功计时器
            this.dwellTimer = setTimeout(() => {
                // 确认仍在目标区域
                if (!this.isOnTarget()) {
                    this.stopDwell();
                    return;
                }
                
                // 命中！
                this.hits++;
                this.trial++;
                console.log(`[ContinualGesture1Animation] 命中! Trial ${this.trial}/${this.maxTrials}, Hits ${this.hits}`);
                
                this.stopDwell();
                
                // 检查是否完成所有目标
                if (this.trial >= this.maxTrials) {
                    console.log('[ContinualGesture1Animation] 所有目标完成');
                    this.completeStage();
                } else {
                    // 设置下一个目标
                    this.setRandomTarget();
                }
            }, this.dwellMs);
        }

        /**
         * 停止停留计时
         */
        stopDwell() {
            this.onTargetSince = null;
            this.fillProgress = 0;
            
            if (this.dwellTimer) {
                clearTimeout(this.dwellTimer);
                this.dwellTimer = null;
            }
            if (this.fillTimer) {
                clearInterval(this.fillTimer);
                this.fillTimer = null;
            }
        }

        /**
         * 完成Stage
         */
        completeStage() {
            console.log(`[ContinualGesture1Animation] Stage完成: ${this.hits}/${this.trial} 命中`);
            this.stop();
            if (this.onComplete) {
                this.onComplete();
            }
        }

        /**
         * 动画循环
         */
        animate() {
            if (!this.isRunning) return;
            
            // 更新剩余时间
            this.remainingTime = Math.max(0, this.stageTimeout - (Date.now() - this.startTime));
            
            // 清除画布
            this.ctx.clearRect(0, 0, this.config.canvasWidth, this.config.canvasHeight);
            
            // 绘制所有元素
            this.drawStageInfo();
            this.drawTrack();
            this.drawTarget();
            this.drawCursor();
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
            
            let stageLabel = stageConfig.label || stageConfig.name;
            let stageIcon = stageConfig.icon || '🎯';
            
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
            ctx.fillText('滚动滚轮移动光标到目标区域并保持500ms', 20, 48);
            
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
         * 绘制轨道
         */
        drawTrack() {
            const ctx = this.ctx;
            const x = this.config.trackCenterX - this.trackWidth / 2;
            const y = this.config.trackTop;
            const w = this.trackWidth;
            const h = this.config.trackHeight;
            
            ctx.save();
            
            // 轨道背景
            ctx.fillStyle = this.config.colors.track;
            ctx.strokeStyle = this.config.colors.trackBorder;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.roundRect(x, y, w, h, 12);
            ctx.fill();
            ctx.stroke();
            
            ctx.restore();
        }

        /**
         * 绘制目标区域
         */
        drawTarget() {
            if (this.targetPos == null) return;
            
            const ctx = this.ctx;
            const bounds = this.getTargetBounds();
            if (!bounds) return;
            
            const x = this.config.trackCenterX - this.trackWidth / 2;
            const w = this.trackWidth;
            const h = bounds.bottom - bounds.top;
            const y = bounds.top;
            
            const isActive = this.onTargetSince != null;
            
            ctx.save();
            
            // 目标区域
            ctx.fillStyle = isActive ? this.config.colors.targetActive : this.config.colors.target;
            ctx.strokeStyle = isActive ? this.config.colors.targetBorderActive : this.config.colors.targetBorder;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.roundRect(x, y, w, h, 8);
            ctx.fill();
            if (isActive) ctx.stroke();
            
            // 目标标签
            ctx.fillStyle = this.config.colors.muted;
            ctx.font = '600 12px ui-sans-serif, system-ui';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(`T${this.trial + 1}`, this.config.trackCenterX, bounds.centerY);
            
            ctx.restore();
        }

        /**
         * 绘制光标
         */
        drawCursor() {
            const ctx = this.ctx;
            const y = this.getCursorScreenY();
            const x = this.config.trackCenterX;
            const r = this.cursorSize / 2;
            
            ctx.save();
            
            // 光标外圈
            ctx.fillStyle = this.config.colors.cursor;
            ctx.strokeStyle = this.config.colors.cursorBorder;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(x, y, r, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
            
            // 填充进度（从左到右）
            if (this.fillProgress > 0) {
                ctx.save();
                ctx.beginPath();
                ctx.arc(x, y, r - 2, 0, Math.PI * 2);
                ctx.clip();
                
                ctx.fillStyle = this.config.colors.cursorFill;
                const fillWidth = (r - 2) * 2 * this.fillProgress;
                ctx.fillRect(x - (r - 2), y - (r - 2), fillWidth, (r - 2) * 2);
                
                ctx.restore();
            }
            
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
            ctx.fillText(`${this.trial} / ${this.maxTrials}`, this.config.canvasWidth - 20, bottomY);
            
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
            
            // 左下角显示命中信息
            ctx.fillStyle = this.config.colors.muted;
            ctx.font = '500 14px ui-sans-serif, system-ui';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'bottom';
            ctx.fillText(`命中: ${this.hits} | 光标: ${this.cursorPos.toFixed(2)}`, 20, bottomY);
            
            ctx.restore();
        }

        /**
         * 绘制操作说明
         */
        drawInstructions() {
            const ctx = this.ctx;
            const centerX = this.config.canvasWidth / 2;
            
            ctx.save();
            
            // 在轨道两侧显示提示
            ctx.fillStyle = this.config.colors.muted;
            ctx.font = '500 12px ui-sans-serif, system-ui';
            ctx.textAlign = 'center';
            
            // 上方提示
            ctx.fillText('↑ 滚轮上滑', centerX, this.config.trackTop - 10);
            
            // 下方提示
            ctx.fillText('↓ 滚轮下滑', centerX, this.config.trackBottom + 20);
            
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
            
            this.stopDwell();
            
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
                hits: this.hits,
                maxTrials: this.maxTrials,
                percent: this.maxTrials > 0 ? (this.trial / this.maxTrials * 100) : 0,
                remainingTime: this.remainingTime
            };
        }
    }

    // 创建全局实例
    const continualGesture1Animation = new ContinualGesture1AnimationController();
    window.continualGesture1Animation = continualGesture1Animation;
    
    console.log('[ContinualGesture1Animation] 连续手势1动画模块加载完成');

})();
