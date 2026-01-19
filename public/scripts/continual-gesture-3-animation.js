/**
 * continual-gesture-3-animation.js - 连续手势3采集动画模块
 * 
 * 【半圆弧跟随引导任务】
 * - 目的：引导用户做手掌反转动作（掌心向下 ↔ 掌心向上）
 * - 显示：半圆弧轨道 + 匀速移动的引导区域 + 用户光标
 * - 输入：鼠标滚轮独立控制光标位置
 */

(function() {
    'use strict';

    console.log('[ContinualGesture3Animation] 半圆弧跟随引导动画模块开始加载...');

    class ContinualGesture3AnimationController {
        constructor() {
            this.canvas = null;
            this.ctx = null;
            this.containerElement = null;
            
            this.isRunning = false;
            this.animationId = null;
            this.stageTimer = null;
            
            this.currentStage = null;
            this.currentTaskId = 'continual_gesture_3';
            this.onComplete = null;
            this.onTrialComplete = null;
            
            this.trial = 0;
            this.hits = 0;
            this.maxTrials = 10;
            this.stageTimeout = 120000;
            
            // 半圆弧参数
            this.arcRadius = 100;
            this.arcThickness = 14;
            this.guideSize = 0.15;          // 引导区域大小（占半圆弧比例）
            this.cursorSize = 18;
            
            // 运动状态 - position范围 [0, 1]
            // 0 = 左端, 1 = 右端
            this.guidePosition = 0;
            this.guideSpeed = 0.15;         // 每秒移动的比例
            this.cursorPosition = 0;
            
            // 阶段: 'forward' | 'hold' | 'backward' | 'complete'
            this.phase = 'forward';
            this.holdStartTime = 0;
            this.holdDuration = 1000;       // 停留1秒
            
            this.isFollowing = false;
            this.lastFrameTime = 0;
            this.pulsePhase = 0;
            this.startTime = null;
            this.remainingTime = 0;
            
            this.config = {
                canvasWidth: 0,
                canvasHeight: 0,
                colors: {
                    background: 'rgba(250, 245, 255, 0.98)',
                    arcTrack: 'rgba(139, 92, 246, 0.18)',
                    arcTrackBorder: 'rgba(139, 92, 246, 0.35)',
                    guideArea: 'rgba(16, 185, 129, 0.5)',
                    guideAreaBorder: '#10b981',
                    guideCenter: '#059669',
                    cursor: '#8b5cf6',
                    cursorFollowing: '#10b981',
                    cursorOutside: '#ef4444',
                    text: '#1e1b4b',
                    muted: 'rgba(107, 114, 128, 0.9)',
                    success: '#10b981',
                    hold: '#3b82f6'
                }
            };
            
            this._wheelHandler = this.handleWheel.bind(this);
            this._resizeHandler = this.resizeCanvas.bind(this);
        }

        loadConfig() {
            let templateConfig = null;
            const saved = localStorage.getItem('emg_collection_template');
            if (saved) {
                try {
                    const template = JSON.parse(saved);
                    if (template.execution?.continual_gesture_3) {
                        templateConfig = template.execution.continual_gesture_3;
                    }
                } catch (e) {}
            }

            if (templateConfig) {
                this.maxTrials = templateConfig.trialsPerStage || 10;
                this.stageTimeout = (templateConfig.stageTimeout || 120) * 1000;
                this.guideSpeed = templateConfig.guideSpeed || 0.15;
                this.guideSize = templateConfig.guideSize || 0.15;
                this.holdDuration = (templateConfig.holdDuration || 1.0) * 1000;  // 转换为毫秒
                console.log('[ContinualGesture3Animation] 已加载配置:', {
                    maxTrials: this.maxTrials,
                    stageTimeout: this.stageTimeout,
                    guideSpeed: this.guideSpeed,
                    guideSize: this.guideSize,
                    holdDuration: this.holdDuration
                });
            }
            
            if (window.CONTINUAL_GESTURE_3_CONFIG?.WHEEL_TASK) {
                const wt = window.CONTINUAL_GESTURE_3_CONFIG.WHEEL_TASK;
                this.maxTrials = wt.MAX_TRIALS || this.maxTrials;
                this.stageTimeout = wt.STAGE_TIMEOUT || this.stageTimeout;
            }
        }

        init(containerSelector) {
            this.loadConfig();
            
            this.containerElement = document.querySelector('.animation-area') ||
                                    document.querySelector(containerSelector) || 
                                    document.getElementById('gestureDisplay');
            
            if (!this.containerElement) {
                console.warn('[ContinualGesture3Animation] 未找到容器元素');
                return false;
            }
            
            const containerStyle = window.getComputedStyle(this.containerElement);
            if (containerStyle.position === 'static') {
                this.containerElement.style.position = 'relative';
            }
            
            this.canvas = document.getElementById('continualGesture3Canvas');
            if (!this.canvas) {
                this.canvas = document.createElement('canvas');
                this.canvas.id = 'continualGesture3Canvas';
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
            
            this.canvas.tabIndex = 0;
            this.canvas.style.outline = 'none';
            this.ctx = this.canvas.getContext('2d');
            this.resizeCanvas();
            window.addEventListener('resize', this._resizeHandler);
            
            return true;
        }

        resizeCanvas() {
            if (!this.canvas || !this.containerElement) return;
            
            const rect = this.containerElement.getBoundingClientRect();
            const dpr = window.devicePixelRatio || 1;
            
            this.canvas.width = rect.width * dpr;
            this.canvas.height = rect.height * dpr;
            this.config.canvasWidth = rect.width;
            this.config.canvasHeight = rect.height;
            this.ctx.scale(dpr, dpr);
            
            const minDim = Math.min(rect.width, rect.height);
            this.arcRadius = Math.min(140, minDim * 0.28);
            this.arcThickness = Math.max(12, this.arcRadius * 0.12);
            this.cursorSize = Math.max(16, this.arcRadius * 0.14);
        }

        reset() {
            this.trial = 0;
            this.hits = 0;
        }

        startNewTrial() {
            this.guidePosition = 0;
            this.cursorPosition = 0;
            this.isFollowing = false;
            this.holdStartTime = 0;
            this.phase = 'forward';
            console.log('[ContinualGesture3Animation] 开始Trial:', this.trial + 1, '/', this.maxTrials);
        }

        start(stageConfig, onComplete, onTrialComplete, executionParams) {
            console.log('[ContinualGesture3Animation] start()');

            this.currentStage = stageConfig;
            this.onComplete = onComplete;
            this.onTrialComplete = onTrialComplete;

            if (!this.canvas) this.init();

            // 【关键修复】executionParams 必须在 init() 之后设置
            // 因为 init() 中的 loadConfig() 会从 localStorage 读取默认值
            if (executionParams) {
                if (executionParams.trialsPerStage) this.maxTrials = executionParams.trialsPerStage;
                if (executionParams.stageTimeout) this.stageTimeout = executionParams.stageTimeout * 1000;
                if (executionParams.guideSpeed) this.guideSpeed = executionParams.guideSpeed;
                if (executionParams.guideSize) this.guideSize = executionParams.guideSize;
                if (executionParams.holdDuration) this.holdDuration = executionParams.holdDuration * 1000;
                console.log('[ContinualGesture3Animation] 使用执行参数:', {
                    maxTrials: this.maxTrials,
                    stageTimeout: this.stageTimeout,
                    guideSpeed: this.guideSpeed,
                    guideSize: this.guideSize,
                    holdDuration: this.holdDuration
                });
            }

            this.reset();
            this.startNewTrial();

            if (this.canvas) {
                this.canvas.style.display = 'block';
                this.canvas.focus();
                this.canvas.addEventListener('wheel', this._wheelHandler, { passive: false });
            }

            this.resizeCanvas();

            this.startTime = Date.now();
            this.stageTimer = setTimeout(() => {
                console.log('[ContinualGesture3Animation] Stage超时');
                this.completeStage();
            }, this.stageTimeout);

            this.isRunning = true;
            // 修复：设置为 0 标记第一帧，避免巨大的 deltaTime
            this.lastFrameTime = 0;
            this.animate();
        }

        handleWheel(e) {
            if (!this.isRunning) return;
            e.preventDefault();
            e.stopPropagation();
            
            let delta = e.deltaY;
            if (e.deltaMode === 1) delta *= 40;
            if (e.deltaMode === 2) delta *= 800;
            
            const sensitivity = 0.0006;
            this.cursorPosition += delta * sensitivity;
            this.cursorPosition = Math.max(0, Math.min(1, this.cursorPosition));
        }

        updateGuide(deltaTime) {
            const now = performance.now();
            
            switch (this.phase) {
                case 'forward':
                    this.guidePosition += this.guideSpeed * (deltaTime / 1000);
                    if (this.guidePosition >= 1) {
                        this.guidePosition = 1;
                        this.phase = 'hold';
                        this.holdStartTime = now;
                    }
                    break;
                    
                case 'hold':
                    if (now - this.holdStartTime >= this.holdDuration) {
                        this.phase = 'backward';
                    }
                    break;
                    
                case 'backward':
                    this.guidePosition -= this.guideSpeed * (deltaTime / 1000);
                    if (this.guidePosition <= 0) {
                        this.guidePosition = 0;
                        this.phase = 'complete';
                        this.onTrialDone();
                    }
                    break;
            }
        }

        onTrialDone() {
            this.trial++;
            this.hits++;
            
            console.log(`[ContinualGesture3Animation] Trial ${this.trial}/${this.maxTrials} 完成`);
            
            if (this.onTrialComplete) {
                this.onTrialComplete(this.trial - 1);
            }
            
            if (this.trial >= this.maxTrials) {
                setTimeout(() => this.completeStage(), 500);
            } else {
                setTimeout(() => this.startNewTrial(), 500);
            }
        }

        checkFollowing() {
            const halfSize = this.guideSize / 2;
            const guideStart = Math.max(0, this.guidePosition - halfSize);
            const guideEnd = Math.min(1, this.guidePosition + halfSize);
            this.isFollowing = this.cursorPosition >= guideStart && this.cursorPosition <= guideEnd;
        }

        completeStage() {
            console.log('[ContinualGesture3Animation] Stage完成');
            this.stop();
            if (this.onComplete) this.onComplete();
        }

        animate(timestamp = 0) {
            if (!this.isRunning) return;
            
            const deltaTime = Math.min(timestamp - this.lastFrameTime, 100);
            this.lastFrameTime = timestamp;
            
            this.pulsePhase += deltaTime * 0.004;
            
            if (this.startTime) {
                this.remainingTime = Math.max(0, this.stageTimeout - (Date.now() - this.startTime));
            }
            
            if (this.phase !== 'complete') {
                this.updateGuide(deltaTime);
            }
            
            this.checkFollowing();
            this.draw();
            
            this.animationId = requestAnimationFrame((t) => this.animate(t));
        }

        /**
         * 将位置(0-1)转换为上半圆的角度
         * Canvas坐标系Y轴向下，上半圆角度范围是 [PI, 2*PI]
         * position 0 = 左端 (角度 = PI)
         * position 1 = 右端 (角度 = 2*PI 或 0)
         */
        positionToAngle(position) {
            // 上半圆：从 PI（左）到 2*PI（右）
            return Math.PI + Math.PI * position;
        }

        /**
         * 根据角度获取圆弧上的点坐标
         */
        getPointOnArc(centerX, centerY, radius, angle) {
            return {
                x: centerX + radius * Math.cos(angle),
                y: centerY + radius * Math.sin(angle)
            };
        }

        draw() {
            if (!this.ctx) return;
            
            const ctx = this.ctx;
            const w = this.config.canvasWidth;
            const h = this.config.canvasHeight;
            
            ctx.clearRect(0, 0, w, h);
            ctx.fillStyle = this.config.colors.background;
            ctx.fillRect(0, 0, w, h);
            
            const centerX = w / 2;
            const centerY = h / 2 + 20;
            
            this.drawTitle(centerX, 40);
            this.drawArcTrack(centerX, centerY);
            this.drawGuideArea(centerX, centerY);
            this.drawCursor(centerX, centerY);
            this.drawEndpoints(centerX, centerY);
            this.drawStatus(centerX, h - 90);
            this.drawInstructions(centerX, h - 25);
        }

        drawTitle(centerX, y) {
            const ctx = this.ctx;
            
            ctx.save();
            ctx.fillStyle = this.config.colors.text;
            ctx.font = '700 24px ui-sans-serif, system-ui';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText('手掌反转引导', centerX, y);
            
            ctx.font = '500 14px ui-sans-serif, system-ui';
            ctx.fillStyle = this.config.colors.muted;
            
            let phaseText = '';
            switch (this.phase) {
                case 'forward': phaseText = '→ 掌心向下 → 掌心向上'; break;
                case 'hold': phaseText = '⏸ 停留中...'; break;
                case 'backward': phaseText = '← 掌心向上 → 掌心向下'; break;
                case 'complete': phaseText = '✓ 完成'; break;
            }
            ctx.fillText(phaseText, centerX, y + 26);
            ctx.restore();
        }

        drawArcTrack(centerX, centerY) {
            const ctx = this.ctx;
            const radius = this.arcRadius;

            ctx.save();

            // 上半圆轨道：Canvas Y轴向下，所以上半圆是从 PI 到 2*PI（或等价地 PI 到 0 逆时针）
            // 使用 -PI 到 0 或者 PI 到 2*PI 都可以表示上半圆
            ctx.beginPath();
            ctx.arc(centerX, centerY, radius, Math.PI, 2 * Math.PI, false);  // 顺时针从左到右画上半圆
            ctx.strokeStyle = this.config.colors.arcTrack;
            ctx.lineWidth = this.arcThickness;
            ctx.lineCap = 'round';
            ctx.stroke();

            // 轨道边框
            ctx.beginPath();
            ctx.arc(centerX, centerY, radius, Math.PI, 2 * Math.PI, false);
            ctx.strokeStyle = this.config.colors.arcTrackBorder;
            ctx.lineWidth = 2;
            ctx.stroke();

            ctx.restore();
        }

        drawGuideArea(centerX, centerY) {
            const ctx = this.ctx;
            const radius = this.arcRadius;

            // 计算引导区域的角度
            const guideAngle = this.positionToAngle(this.guidePosition);
            const halfArcSize = (this.guideSize * Math.PI) / 2;

            // 引导区域的起止角度（限制在上半圆范围内）
            // 上半圆：从 PI（左）到 2*PI（右）
            let startAngle = guideAngle - halfArcSize;
            let endAngle = guideAngle + halfArcSize;

            // 限制在上半圆范围 [PI, 2*PI]
            startAngle = Math.max(Math.PI, Math.min(2 * Math.PI, startAngle));
            endAngle = Math.max(Math.PI, Math.min(2 * Math.PI, endAngle));

            ctx.save();

            // 停留时变色
            let guideColor = this.config.colors.guideArea;
            let borderColor = this.config.colors.guideAreaBorder;
            if (this.phase === 'hold') {
                guideColor = 'rgba(59, 130, 246, 0.5)';
                borderColor = this.config.colors.hold;
            }

            // 脉冲效果
            const pulse = 0.85 + 0.15 * Math.sin(this.pulsePhase);

            // 绘制引导区域弧线（顺时针从小角度到大角度）
            ctx.beginPath();
            ctx.arc(centerX, centerY, radius, startAngle, endAngle, false);
            ctx.strokeStyle = guideColor;
            ctx.lineWidth = this.arcThickness * 2 * pulse;
            ctx.lineCap = 'round';
            ctx.stroke();

            // 边框
            ctx.beginPath();
            ctx.arc(centerX, centerY, radius, startAngle, endAngle, false);
            ctx.strokeStyle = borderColor;
            ctx.lineWidth = 3;
            ctx.stroke();
            
            // 中心点
            const guidePoint = this.getPointOnArc(centerX, centerY, radius, guideAngle);
            
            ctx.beginPath();
            ctx.arc(guidePoint.x, guidePoint.y, 10, 0, Math.PI * 2);
            ctx.fillStyle = borderColor;
            ctx.fill();
            
            ctx.beginPath();
            ctx.arc(guidePoint.x, guidePoint.y, 5, 0, Math.PI * 2);
            ctx.fillStyle = 'white';
            ctx.fill();
            
            ctx.restore();
        }

        drawCursor(centerX, centerY) {
            const ctx = this.ctx;
            const radius = this.arcRadius;
            
            // 计算光标位置
            const cursorAngle = this.positionToAngle(this.cursorPosition);
            const cursorPoint = this.getPointOnArc(centerX, centerY, radius, cursorAngle);
            
            ctx.save();
            
            const cursorColor = this.isFollowing 
                ? this.config.colors.cursorFollowing 
                : this.config.colors.cursorOutside;
            
            // 发光效果
            ctx.shadowColor = cursorColor;
            ctx.shadowBlur = this.isFollowing ? 20 : 10;
            
            // 外圈
            ctx.beginPath();
            ctx.arc(cursorPoint.x, cursorPoint.y, this.cursorSize, 0, Math.PI * 2);
            ctx.fillStyle = cursorColor;
            ctx.fill();
            
            // 内圈
            ctx.shadowBlur = 0;
            ctx.beginPath();
            ctx.arc(cursorPoint.x, cursorPoint.y, this.cursorSize * 0.45, 0, Math.PI * 2);
            ctx.fillStyle = 'white';
            ctx.fill();
            
            ctx.restore();
        }

        drawEndpoints(centerX, centerY) {
            const ctx = this.ctx;
            const radius = this.arcRadius;

            ctx.save();

            // 左端点（掌心向下）- 角度 = PI
            const leftPoint = this.getPointOnArc(centerX, centerY, radius, Math.PI);
            ctx.font = '700 28px ui-sans-serif, system-ui';
            ctx.textAlign = 'center';
            ctx.fillText('🤚', leftPoint.x - 40, leftPoint.y);
            ctx.font = '600 12px ui-sans-serif, system-ui';
            ctx.fillStyle = this.config.colors.text;
            ctx.fillText('掌心向下', leftPoint.x - 40, leftPoint.y + 25);

            // 右端点（掌心向上）- 角度 = 2*PI
            const rightPoint = this.getPointOnArc(centerX, centerY, radius, 2 * Math.PI);
            ctx.font = '700 28px ui-sans-serif, system-ui';
            ctx.fillText('✋', rightPoint.x + 40, rightPoint.y);
            ctx.font = '600 12px ui-sans-serif, system-ui';
            ctx.fillStyle = this.config.colors.text;
            ctx.fillText('掌心向上', rightPoint.x + 40, rightPoint.y + 25);

            ctx.restore();
        }

        drawStatus(centerX, y) {
            const ctx = this.ctx;
            
            ctx.save();
            
            // 跟随状态
            ctx.font = '700 20px ui-sans-serif, system-ui';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            
            if (this.isFollowing) {
                ctx.fillStyle = this.config.colors.success;
                ctx.fillText('✓ 跟随中', centerX, y);
            } else {
                ctx.fillStyle = this.config.colors.cursorOutside;
                ctx.fillText('○ 请跟随绿色区域', centerX, y);
            }
            
            // 进度条
            const barWidth = 200;
            const barHeight = 8;
            const barX = centerX - barWidth / 2;
            const barY = y + 30;
            
            ctx.fillStyle = 'rgba(139, 92, 246, 0.2)';
            ctx.beginPath();
            ctx.roundRect(barX, barY, barWidth, barHeight, 4);
            ctx.fill();
            
            const fillWidth = barWidth * (this.trial / this.maxTrials);
            ctx.fillStyle = this.config.colors.success;
            ctx.beginPath();
            ctx.roundRect(barX, barY, fillWidth, barHeight, 4);
            ctx.fill();
            
            ctx.font = '600 14px ui-sans-serif, system-ui';
            ctx.fillStyle = this.config.colors.text;
            ctx.fillText(`完成: ${this.trial} / ${this.maxTrials}`, centerX, barY + 22);
            
            const remainSec = Math.ceil(this.remainingTime / 1000);
            ctx.font = '500 12px ui-sans-serif, system-ui';
            ctx.fillStyle = this.config.colors.muted;
            ctx.fillText(`剩余时间: ${remainSec}秒`, centerX, barY + 40);
            
            ctx.restore();
        }

        drawInstructions(centerX, y) {
            const ctx = this.ctx;
            
            ctx.save();
            ctx.fillStyle = this.config.colors.muted;
            ctx.font = '500 12px ui-sans-serif, system-ui';
            ctx.textAlign = 'center';
            ctx.fillText('滚轮控制: ↓向下滚动=光标右移 | ↑向上滚动=光标左移', centerX, y);
            ctx.restore();
        }

        stop() {
            this.isRunning = false;
            
            if (this.animationId) {
                cancelAnimationFrame(this.animationId);
                this.animationId = null;
            }
            
            if (this.stageTimer) {
                clearTimeout(this.stageTimer);
                this.stageTimer = null;
            }
            
            if (this.canvas) {
                this.canvas.removeEventListener('wheel', this._wheelHandler);
            }
            
            if (this.ctx && this.config.canvasWidth) {
                this.ctx.clearRect(0, 0, this.config.canvasWidth, this.config.canvasHeight);
            }
            
            if (this.canvas) {
                this.canvas.style.display = 'none';
            }
        }

        destroy() {
            this.stop();
            window.removeEventListener('resize', this._resizeHandler);
            if (this.canvas?.parentElement) {
                this.canvas.parentElement.removeChild(this.canvas);
            }
            this.canvas = null;
            this.ctx = null;
        }

        isAnimationRunning() {
            return this.isRunning;
        }

        getProgress() {
            return {
                trial: this.trial,
                hits: this.hits,
                maxTrials: this.maxTrials,
                percent: this.maxTrials > 0 ? (this.trial / this.maxTrials * 100) : 0,
                remainingTime: this.remainingTime,
                isFollowing: this.isFollowing,
                phase: this.phase
            };
        }
    }

    window.continualGesture3Animation = new ContinualGesture3AnimationController();
    console.log('[ContinualGesture3Animation] 模块加载完成');

})();
