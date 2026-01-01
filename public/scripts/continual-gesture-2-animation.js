/**
 * continual-gesture-2-animation.js - 连续手势2采集动画模块
 * 
 * 概念说明：
 * - Stage: 一个采集阶段（如"手腕控制任务"）
 * - 与离散手势不同，这里使用滚轮控制光标任务（与连续手势1类似）
 * - 每个Stage包含多个目标点（Trial），用户通过滚轮移动光标到目标点
 * 
 * 任务规则：
 * - 目标点出现在10个不同位置
 * - 用户通过滚轮移动光标到目标点
 * - 光标停留在目标区域500ms视为命中
 * - 完成所有目标或120s超时后进入下一个Stage
 * - Stage内不向realtimeEngine发送prompt消息
 * 
 * 与连续手势1的区别：
 * - 视觉主题颜色不同（橙色系）
 * - 可配置不同的任务参数
 */

(function() {
    'use strict';

    console.log('[ContinualGesture2Animation] 连续手势2动画模块开始加载...');

    class ContinualGesture2AnimationController {
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
            this.currentTaskId = 'continual_gesture_2';
            this.onComplete = null;
            
            // 滚轮光标任务状态
            this.cursorPos = 0.5;
            this.targetPos = null;
            this.trial = 0;
            this.hits = 0;
            this.maxTrials = 10;
            this.stageTimeout = 120000;
            this.dwellMs = 500;
            this.onTargetSince = null;
            this.dwellTimer = null;
            this.fillTimer = null;
            this.fillProgress = 0;
            
            // 目标区域配置
            this.targetFrac = 0.12;
            this.targetH = 0;
            
            // 轨道配置
            this.trackPadding = 20;
            this.trackWidth = 40;
            this.cursorSize = 20;
            
            // 计时相关
            this.startTime = null;
            this.remainingTime = 0;
            
            // 配置 - 连续手势2使用橙色主题
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
                    target: 'rgba(245, 158, 11, 0.25)',      // 橙色目标
                    targetActive: 'rgba(245, 158, 11, 0.45)',
                    targetBorder: 'transparent',
                    targetBorderActive: '#92400e',           // 深橙色边框
                    cursor: '#ef4444',
                    cursorBorder: '#991b1b',
                    cursorFill: 'rgba(0, 0, 0, 0.85)',
                    text: '#92400e',                         // 橙色文字
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
            if (window.CONTINUAL_GESTURE_2_CONFIG) {
                const taskConfig = window.CONTINUAL_GESTURE_2_CONFIG;
                
                if (taskConfig.WHEEL_TASK) {
                    const wt = taskConfig.WHEEL_TASK;
                    this.maxTrials = wt.MAX_TRIALS || 10;
                    this.stageTimeout = wt.STAGE_TIMEOUT || 120000;
                    this.dwellMs = wt.DWELL_MS || 500;
                    this.targetFrac = wt.TARGET_FRAC || 0.12;
                }
                
                // 加载颜色配置
                if (taskConfig.ANIMATION && taskConfig.ANIMATION.COLORS) {
                    Object.assign(this.config.colors, taskConfig.ANIMATION.COLORS);
                }
                
                console.log('[ContinualGesture2Animation] 配置已加载');
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
                console.warn('[ContinualGesture2Animation] 未找到容器元素');
                return false;
            }
            
            const containerStyle = window.getComputedStyle(this.containerElement);
            if (containerStyle.position === 'static') {
                this.containerElement.style.position = 'relative';
            }
            
            // 创建或获取Canvas
            this.canvas = document.getElementById('continualGesture2Canvas');
            if (!this.canvas) {
                this.canvas = document.createElement('canvas');
                this.canvas.id = 'continualGesture2Canvas';
                this.canvas.style.cssText = `
                    position: absolute;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    z-index: 50;
                    background: rgba(255, 251, 235, 0.98);
                `;
                this.containerElement.appendChild(this.canvas);
            }
            
            this.canvas.tabIndex = 0;
            this.canvas.style.outline = 'none';
            
            this.ctx = this.canvas.getContext('2d');
            this.resizeCanvas();
            
            window.addEventListener('resize', this._resizeHandler);
            
            console.log('[ContinualGesture2Animation] 初始化完成');
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
                
                this.config.trackTop = this.trackPadding + 80;
                this.config.trackBottom = rect.height - this.trackPadding - 60;
                this.config.trackHeight = this.config.trackBottom - this.config.trackTop;
                this.config.trackCenterX = rect.width / 2;
                
                this.targetH = Math.max(36, Math.round(this.config.trackHeight * this.targetFrac));
                
                return true;
            }
            return false;
        }

        /**
         * 开始Stage动画
         */
        start(stage, onComplete, onPromptTriggered) {
            console.log('[ContinualGesture2Animation] 开始Stage动画:', stage.name);
            
            if (!this.canvas) {
                if (!this.init('.animation-area')) {
                    console.error('[ContinualGesture2Animation] Canvas初始化失败');
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
            
            this.resizeCanvas();
            this.canvas.style.display = 'block';
            this.canvas.focus();
            
            this.canvas.addEventListener('wheel', this._wheelHandler, { passive: false });
            
            this.setRandomTarget();
            
            this.stageTimer = setTimeout(() => {
                console.log('[ContinualGesture2Animation] Stage超时');
                this.completeStage();
            }, this.stageTimeout);
            
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
            console.log(`[ContinualGesture2Animation] 新目标: Trial ${this.trial + 1}, 位置 ${this.targetPos.toFixed(2)}`);
        }

        /**
         * 处理滚轮事件
         */
        handleWheel(e) {
            if (!this.isRunning) return;
            
            e.preventDefault();
            e.stopPropagation();
            
            let multiplier = 0.001;
            if (e.deltaMode === 1) multiplier = 0.015;
            if (e.deltaMode === 2) multiplier = 0.25;
            
            const rawDelta = e.deltaY * multiplier;
            const delta = Math.max(-0.06, Math.min(0.06, rawDelta));
            
            this.cursorPos = Math.max(0, Math.min(1, this.cursorPos + delta));
            
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
         * 获取目标区域的边界
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
            
            this.fillTimer = setInterval(() => {
                if (this.onTargetSince == null) return;
                const elapsed = performance.now() - this.onTargetSince;
                this.fillProgress = Math.max(0, Math.min(1, elapsed / this.dwellMs));
            }, 20);
            
            this.dwellTimer = setTimeout(() => {
                if (!this.isOnTarget()) {
                    this.stopDwell();
                    return;
                }
                
                this.hits++;
                this.trial++;
                console.log(`[ContinualGesture2Animation] 命中! Trial ${this.trial}/${this.maxTrials}, Hits ${this.hits}`);
                
                this.stopDwell();
                
                if (this.trial >= this.maxTrials) {
                    console.log('[ContinualGesture2Animation] 所有目标完成');
                    this.completeStage();
                } else {
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
            console.log(`[ContinualGesture2Animation] Stage完成: ${this.hits}/${this.trial} 命中`);
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
            
            this.remainingTime = Math.max(0, this.stageTimeout - (Date.now() - this.startTime));
            
            this.ctx.clearRect(0, 0, this.config.canvasWidth, this.config.canvasHeight);
            
            this.drawStageInfo();
            this.drawTrack();
            this.drawTarget();
            this.drawCursor();
            this.drawProgress();
            this.drawInstructions();
            
            this.animationId = requestAnimationFrame(() => this.animate());
        }

        /**
         * 绘制Stage信息
         */
        drawStageInfo() {
            const ctx = this.ctx;
            const stageConfig = this.currentStage;
            
            let stageLabel = stageConfig.label || stageConfig.name;
            let stageIcon = stageConfig.icon || '🔄';
            
            ctx.save();
            
            ctx.fillStyle = this.config.colors.text;
            ctx.font = '700 22px ui-sans-serif, system-ui, -apple-system';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'top';
            ctx.fillText(`${stageIcon} ${stageLabel}`, 20, 20);
            
            ctx.fillStyle = this.config.colors.muted;
            ctx.font = '500 14px ui-sans-serif, system-ui';
            ctx.fillText('手腕动作控制 - 滚动滚轮移动光标到目标区域', 20, 48);
            
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
            
            ctx.fillStyle = isActive ? this.config.colors.targetActive : this.config.colors.target;
            ctx.strokeStyle = isActive ? this.config.colors.targetBorderActive : this.config.colors.targetBorder;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.roundRect(x, y, w, h, 8);
            ctx.fill();
            if (isActive) ctx.stroke();
            
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
            
            ctx.fillStyle = this.config.colors.cursor;
            ctx.strokeStyle = this.config.colors.cursorBorder;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(x, y, r, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
            
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
            
            ctx.fillStyle = 'rgba(0, 0, 0, 0.75)';
            ctx.font = '700 16px ui-sans-serif, system-ui';
            ctx.textAlign = 'right';
            ctx.textBaseline = 'bottom';
            ctx.fillText(`${this.trial} / ${this.maxTrials}`, this.config.canvasWidth - 20, bottomY);
            
            const barWidth = 150;
            const barHeight = 8;
            const barX = this.config.canvasWidth - 20 - barWidth;
            const barY = bottomY - 30;
            const progress = this.maxTrials > 0 ? this.trial / this.maxTrials : 0;
            
            ctx.fillStyle = '#e5e7eb';
            ctx.beginPath();
            ctx.roundRect(barX, barY, barWidth, barHeight, 4);
            ctx.fill();
            
            ctx.fillStyle = this.config.colors.warning; // 橙色进度条
            ctx.beginPath();
            ctx.roundRect(barX, barY, barWidth * progress, barHeight, 4);
            ctx.fill();
            
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
            
            ctx.fillStyle = this.config.colors.muted;
            ctx.font = '500 12px ui-sans-serif, system-ui';
            ctx.textAlign = 'center';
            
            ctx.fillText('↑ 手腕上抬/滚轮上滑', centerX, this.config.trackTop - 10);
            ctx.fillText('↓ 手腕下压/滚轮下滑', centerX, this.config.trackBottom + 20);
            
            ctx.restore();
        }

        /**
         * 停止动画
         */
        stop() {
            console.log('[ContinualGesture2Animation] 停止动画');
            this.isRunning = false;
            
            if (this.animationId) {
                cancelAnimationFrame(this.animationId);
                this.animationId = null;
            }
            if (this.stageTimer) {
                clearTimeout(this.stageTimer);
                this.stageTimer = null;
            }
            
            this.stopDwell();
            
            if (this.canvas) {
                this.canvas.removeEventListener('wheel', this._wheelHandler);
            }
            
            if (this.ctx && this.config.canvasWidth && this.config.canvasHeight) {
                this.ctx.clearRect(0, 0, this.config.canvasWidth, this.config.canvasHeight);
            }
            
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
    const continualGesture2Animation = new ContinualGesture2AnimationController();
    window.continualGesture2Animation = continualGesture2Animation;
    
    console.log('[ContinualGesture2Animation] 连续手势2动画模块加载完成');

})();
