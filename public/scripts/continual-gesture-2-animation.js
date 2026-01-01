/**
 * continual-gesture-2-animation.js - 连续手势2采集动画模块
 * 
 * 这个文件专门负责连续手势2（continual_gesture_2）任务的Stage动画
 * 主要是手腕相关的连续动作
 * 
 * 动画特点：
 * - 提示从右向左滚动
 * - 约1秒一个提示经过指示线
 * - 通过prompt数量来控制stage时长
 * - 橙色/琥珀色主题，区别于其他任务
 */

(function() {
    'use strict';

    console.log('[ContinualGesture2Animation] 连续手势2动画模块开始加载...');

    class ContinualGesture2AnimationController {
        constructor() {
            this.canvas = null;
            this.ctx = null;
            this.containerElement = null;
            
            this.animationId = null;
            this.isRunning = false;
            this.prompts = [];
            this.nextPromptIndex = 0;
            this.executedCount = 0;
            this.totalPromptCount = 12;
            
            this.currentStage = null;
            this.currentTaskId = 'continual_gesture_2';
            this.onComplete = null;
            this.onPromptTriggered = null;
            
            this.config = {
                canvasWidth: 0,
                canvasHeight: 0,
                indicatorX: 0,
                scrollSpeed: 2,
                promptSpacing: 120,
                promptLength: 60,
                promptThickness: 10,
                labelOffset: 80,
                centerY: 0,
                colors: {
                    active: '#10b981',
                    passed: '#9ca3af',
                    normal: '#f59e0b',
                    indicator: '#ef4444'
                }
            };
            
            this.gestureTypes = {
                'wrist_rotation': { label: '手腕旋转', icon: '🔄', color: '#f59e0b' },
                'wrist_updown': { label: '手腕上下', icon: '↕', color: '#f59e0b' },
                'wrist_leftright': { label: '手腕左右', icon: '↔', color: '#f59e0b' },
                'fist_rotation': { label: '握拳旋转', icon: '👊', color: '#f59e0b' }
            };
        }

        loadConfig() {
            if (window.COLLECTION_CONSTANTS && window.COLLECTION_CONSTANTS.CONTINUAL_GESTURE_2) {
                const taskConfig = window.COLLECTION_CONSTANTS.CONTINUAL_GESTURE_2;
                const animConfig = taskConfig.ANIMATION;
                
                this.config.scrollSpeed = taskConfig.SCROLL_SPEED || 2;
                this.config.promptSpacing = taskConfig.PROMPT_SPACING || 120;
                this.config.promptLength = animConfig.PROMPT_LENGTH || 60;
                this.config.promptThickness = animConfig.PROMPT_THICKNESS || 10;
                this.config.labelOffset = animConfig.LABEL_OFFSET || 80;
                this.config.indicatorPosition = animConfig.INDICATOR_POSITION || 0.3;
                
                if (animConfig.COLORS) {
                    this.config.colors = { ...animConfig.COLORS };
                }
                
                Object.keys(taskConfig.STAGES).forEach(key => {
                    const stage = taskConfig.STAGES[key];
                    this.gestureTypes[key] = {
                        label: stage.label,
                        icon: stage.icon,
                        color: stage.color
                    };
                });
                
                console.log('[ContinualGesture2Animation] 配置已加载');
            }
        }

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
                    pointer-events: none;
                    z-index: 50;
                    background: linear-gradient(135deg, rgba(255, 251, 235, 0.98) 0%, rgba(254, 243, 199, 0.95) 100%);
                `;
                this.containerElement.appendChild(this.canvas);
            }
            
            this.ctx = this.canvas.getContext('2d');
            this.resizeCanvas();
            
            this._resizeHandler = () => this.resizeCanvas();
            window.addEventListener('resize', this._resizeHandler);
            
            console.log('[ContinualGesture2Animation] 初始化完成');
            return true;
        }

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
                this.config.indicatorX = rect.width * (this.config.indicatorPosition || 0.3);
                this.config.centerY = rect.height / 2;
                
                return true;
            }
            return false;
        }

        createPrompt(type, startX, index) {
            return {
                type: type,
                x: startX,
                index: index,
                isActive: false,
                isPassed: false,
                promptSent: false
            };
        }

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
            this.onPromptTriggered = onPromptTriggered;
            
            if (window.CollectionTiming) {
                this.totalPromptCount = window.CollectionTiming.getPromptCount(
                    this.currentTaskId, 
                    stage.name
                );
            } else {
                this.totalPromptCount = 12;
            }
            
            this.prompts = [];
            this.nextPromptIndex = 0;
            this.executedCount = 0;
            this.isRunning = true;
            
            this.resizeCanvas();
            this.canvas.style.display = 'block';
            this.createNextPrompt();
            this.animate();
        }

        createNextPrompt() {
            if (this.nextPromptIndex >= this.totalPromptCount) return;
            
            const startX = this.config.canvasWidth + 50;
            const prompt = this.createPrompt(
                this.currentStage.name, 
                startX, 
                this.nextPromptIndex
            );
            this.prompts.push(prompt);
            this.nextPromptIndex++;
        }

        animate() {
            if (!this.isRunning) return;
            
            if (this.executedCount >= this.totalPromptCount) {
                const allGone = this.prompts.every(p => p.x < -150);
                if (allGone || this.prompts.length === 0) {
                    this.stop();
                    if (this.onComplete) this.onComplete();
                    return;
                }
            }
            
            if (!this.config.canvasWidth || !this.config.canvasHeight) {
                if (!this.resizeCanvas()) {
                    this.animationId = requestAnimationFrame(() => this.animate());
                    return;
                }
            }
            
            this.ctx.clearRect(0, 0, this.config.canvasWidth, this.config.canvasHeight);
            
            this.prompts.forEach(p => {
                p.x -= this.config.scrollSpeed;
            });
            
            this.prompts = this.prompts.filter(p => p.x > -150);
            
            if (this.nextPromptIndex < this.totalPromptCount) {
                const last = this.prompts[this.prompts.length - 1];
                if (!last || last.x < this.config.canvasWidth - this.config.promptSpacing) {
                    this.createNextPrompt();
                }
            }
            
            this.updatePrompts();
            this.drawStageInfo();
            this.drawIndicator();
            this.prompts.forEach(p => this.drawPrompt(p));
            this.drawProgress();
            
            this.animationId = requestAnimationFrame(() => this.animate());
        }

        updatePrompts() {
            this.prompts.forEach(prompt => {
                if (!prompt.isPassed) {
                    const dist = prompt.x - this.config.indicatorX;
                    
                    if (Math.abs(dist) <= 20) {
                        prompt.isActive = true;
                        
                        if (!prompt.promptSent) {
                            prompt.promptSent = true;
                            this.triggerPrompt(prompt);
                        }
                    }
                    
                    if (dist < -25) {
                        prompt.isActive = false;
                        prompt.isPassed = true;
                        this.executedCount++;
                    }
                }
            });
        }

        triggerPrompt(prompt) {
            if (this.onPromptTriggered) {
                this.onPromptTriggered(prompt.type, prompt.index, this.currentStage.name);
            }
            
            if (window.animationController && window.animationController.sendPrompt) {
                window.animationController.sendPrompt(prompt.type, this.currentStage.name);
            }
        }

        drawStageInfo() {
            const ctx = this.ctx;
            const gesture = this.gestureTypes[this.currentStage.name] || {
                label: this.currentStage.label || this.currentStage.name,
                icon: '●',
                color: '#f59e0b'
            };
            
            ctx.save();
            
            // 橙色主题标题
            ctx.fillStyle = 'rgba(217, 119, 6, 0.95)';
            ctx.font = '700 24px ui-sans-serif, system-ui, -apple-system';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'top';
            ctx.fillText(`手腕动作: ${gesture.label}`, 20, 20);
            
            const stageConfig = this.currentStage;
            if (stageConfig.instruction) {
                ctx.fillStyle = 'rgba(107, 114, 128, 0.9)';
                ctx.font = '500 16px ui-sans-serif, system-ui';
                ctx.fillText(stageConfig.instruction, 20, 52);
            }
            
            ctx.restore();
        }

        drawIndicator() {
            const ctx = this.ctx;
            const x = this.config.indicatorX;
            const y = this.config.centerY;
            
            ctx.save();
            ctx.strokeStyle = this.config.colors.indicator;
            ctx.lineWidth = 4;
            ctx.setLineDash([10, 5]);
            ctx.beginPath();
            ctx.moveTo(x, y - 100);
            ctx.lineTo(x, y + 100);
            ctx.stroke();
            ctx.setLineDash([]);
            
            ctx.fillStyle = this.config.colors.indicator;
            ctx.font = '600 12px ui-sans-serif, system-ui';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';
            ctx.fillText('采集点', x, y - 110);
            
            ctx.restore();
        }

        drawPrompt(prompt) {
            const gesture = this.gestureTypes[prompt.type] || {
                label: prompt.type,
                icon: '●',
                color: '#f59e0b'
            };
            
            let color = this.config.colors.normal;
            if (prompt.isPassed) {
                color = this.config.colors.passed;
            } else if (prompt.isActive) {
                color = this.config.colors.active;
            }
            
            const ctx = this.ctx;
            const x = prompt.x;
            const y = this.config.centerY;
            
            ctx.save();
            
            // 绘制竖线
            ctx.strokeStyle = color;
            ctx.lineWidth = this.config.promptThickness;
            ctx.lineCap = 'round';
            ctx.beginPath();
            ctx.moveTo(x, y - this.config.promptLength / 2);
            ctx.lineTo(x, y + this.config.promptLength / 2);
            ctx.stroke();
            
            // 绘制六边形气泡（手腕动作用六边形，区别于其他任务）
            const size = 35;
            const gap = 12;
            const cy = y - (this.config.promptLength / 2) - gap - size;
            
            // 六边形
            ctx.fillStyle = 'rgba(255, 255, 255, 0.95)';
            ctx.strokeStyle = color;
            ctx.lineWidth = 3;
            ctx.beginPath();
            for (let i = 0; i < 6; i++) {
                const angle = (Math.PI / 3) * i - Math.PI / 2;
                const px = x + size * Math.cos(angle);
                const py = cy + size * Math.sin(angle);
                if (i === 0) {
                    ctx.moveTo(px, py);
                } else {
                    ctx.lineTo(px, py);
                }
            }
            ctx.closePath();
            ctx.fill();
            ctx.stroke();
            
            ctx.fillStyle = color;
            ctx.font = '900 28px ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji"';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(gesture.icon, x, cy);
            
            ctx.font = '600 12px ui-sans-serif, system-ui';
            ctx.textBaseline = 'top';
            ctx.fillText(`#${prompt.index + 1}`, x, y + this.config.labelOffset);
            
            ctx.restore();
        }

        drawProgress() {
            const ctx = this.ctx;
            
            ctx.save();
            
            ctx.fillStyle = 'rgba(217, 119, 6, 0.9)';
            ctx.font = '700 18px ui-sans-serif, system-ui';
            ctx.textAlign = 'right';
            ctx.textBaseline = 'bottom';
            ctx.fillText(
                `${this.executedCount} / ${this.totalPromptCount}`, 
                this.config.canvasWidth - 20, 
                this.config.canvasHeight - 20
            );
            
            const barWidth = 150;
            const barHeight = 8;
            const barX = this.config.canvasWidth - 20 - barWidth;
            const barY = this.config.canvasHeight - 45;
            const progress = this.executedCount / this.totalPromptCount;
            
            ctx.fillStyle = '#fef3c7';
            ctx.beginPath();
            ctx.roundRect(barX, barY, barWidth, barHeight, 4);
            ctx.fill();
            
            ctx.fillStyle = '#f59e0b';
            ctx.beginPath();
            ctx.roundRect(barX, barY, barWidth * progress, barHeight, 4);
            ctx.fill();
            
            ctx.restore();
        }

        stop() {
            console.log('[ContinualGesture2Animation] 停止动画');
            this.isRunning = false;
            
            if (this.animationId) {
                cancelAnimationFrame(this.animationId);
                this.animationId = null;
            }
            
            if (this.ctx && this.config.canvasWidth && this.config.canvasHeight) {
                this.ctx.clearRect(0, 0, this.config.canvasWidth, this.config.canvasHeight);
            }
            
            if (this.canvas) {
                this.canvas.style.display = 'none';
            }
        }

        destroy() {
            this.stop();
            
            if (this._resizeHandler) {
                window.removeEventListener('resize', this._resizeHandler);
            }
            
            if (this.canvas && this.canvas.parentElement) {
                this.canvas.parentElement.removeChild(this.canvas);
            }
            
            this.canvas = null;
            this.ctx = null;
            this.containerElement = null;
        }

        isAnimationRunning() {
            return this.isRunning;
        }

        getProgress() {
            return {
                executed: this.executedCount,
                total: this.totalPromptCount,
                percent: this.totalPromptCount > 0 ? 
                    (this.executedCount / this.totalPromptCount * 100) : 0
            };
        }
    }

    const continualGesture2Animation = new ContinualGesture2AnimationController();
    window.continualGesture2Animation = continualGesture2Animation;
    
    console.log('[ContinualGesture2Animation] 连续手势2动画模块加载完成');

})();
