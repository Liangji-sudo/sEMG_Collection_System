/**
 * discrete-gesture-animation.js - 离散手势采集动画模块
 * 
 * 这个文件专门负责离散手势（discrete_gesture）任务的Stage动画
 * 
 * 动画特点：
 * - 提示从右向左滚动
 * - 约1秒一个提示经过指示线
 * - 通过prompt数量来控制stage时长（不再使用秒数倒计时）
 * - 每个prompt触发时发送信号给realtimeEngine
 * 
 * 使用方式：
 * 由animation-controller.js调用，在playStageContent时根据任务类型启动
 */

(function() {
    'use strict';

    console.log('[DiscreteGestureAnimation] 离散手势动画模块开始加载...');

    class DiscreteGestureAnimationController {
        constructor() {
            // Canvas相关
            this.canvas = null;
            this.ctx = null;
            this.containerElement = null;
            
            // 动画状态
            this.animationId = null;
            this.isRunning = false;
            this.prompts = [];
            this.nextPromptIndex = 0;
            this.executedCount = 0;        // 已经通过指示线的prompt数
            this.totalPromptCount = 10;    // 总共需要的prompt数
            
            // 当前stage信息
            this.currentStage = null;
            this.currentTaskId = 'discrete_gesture';
            this.onComplete = null;
            this.onPromptTriggered = null; // prompt触发回调
            
            // 配置（将从COLLECTION_CONSTANTS读取）
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
                    normal: '#3b82f6',
                    indicator: '#ef4444'
                }
            };
            
            // 手势类型定义
            this.gestureTypes = {
                'palm_up': { label: '手心向上', icon: '↑', color: '#3b82f6' },
                'palm_inward': { label: '手心向内', icon: '→', color: '#3b82f6' },
                'hand_on_knee': { label: '手放膝盖', icon: '↓', color: '#3b82f6' },
                'hand_on_desk': { label: '手放桌上', icon: '◐', color: '#3b82f6' }
            };
        }

        /**
         * 从常量配置中加载配置
         */
        loadConfig() {
            if (window.COLLECTION_CONSTANTS && window.COLLECTION_CONSTANTS.DISCRETE_GESTURE) {
                const taskConfig = window.COLLECTION_CONSTANTS.DISCRETE_GESTURE;
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
                
                // 加载手势配置
                Object.keys(taskConfig.STAGES).forEach(key => {
                    const stage = taskConfig.STAGES[key];
                    this.gestureTypes[key] = {
                        label: stage.label,
                        icon: stage.icon,
                        color: stage.color
                    };
                });
                
                console.log('[DiscreteGestureAnimation] 配置已从COLLECTION_CONSTANTS加载');
            }
        }

        /**
         * 初始化Canvas
         * @param {string} containerSelector - 容器选择器
         */
        init(containerSelector) {
            // 加载配置
            this.loadConfig();
            
            // 查找动画区域容器
            this.containerElement = document.querySelector('.animation-area') ||
                                    document.querySelector(containerSelector) || 
                                    document.getElementById('gestureDisplay');
            
            if (!this.containerElement) {
                console.warn('[DiscreteGestureAnimation] 未找到容器元素');
                return false;
            }
            
            // 确保容器有相对定位
            const containerStyle = window.getComputedStyle(this.containerElement);
            if (containerStyle.position === 'static') {
                this.containerElement.style.position = 'relative';
            }
            
            // 创建Canvas
            this.canvas = document.getElementById('discreteGestureCanvas');
            if (!this.canvas) {
                this.canvas = document.createElement('canvas');
                this.canvas.id = 'discreteGestureCanvas';
                this.canvas.style.cssText = `
                    position: absolute;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    pointer-events: none;
                    z-index: 50;
                    background: rgba(248, 250, 252, 0.95);
                `;
                this.containerElement.appendChild(this.canvas);
            }
            
            this.ctx = this.canvas.getContext('2d');
            this.resizeCanvas();
            
            // 监听窗口大小变化
            this._resizeHandler = () => this.resizeCanvas();
            window.addEventListener('resize', this._resizeHandler);
            
            console.log('[DiscreteGestureAnimation] 初始化完成');
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
                
                // 设置Canvas的实际像素尺寸
                this.canvas.width = rect.width * dpr;
                this.canvas.height = rect.height * dpr;
                
                // 设置Canvas的CSS显示尺寸
                this.canvas.style.width = rect.width + 'px';
                this.canvas.style.height = rect.height + 'px';
                
                // 缩放绘图上下文
                this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
                
                // 更新配置
                this.config.canvasWidth = rect.width;
                this.config.canvasHeight = rect.height;
                this.config.indicatorX = rect.width * (this.config.indicatorPosition || 0.3);
                this.config.centerY = rect.height / 2;
                
                return true;
            }
            return false;
        }

        /**
         * 创建Prompt对象
         */
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

        /**
         * 开始Stage动画
         * @param {Object} stage - stage配置
         * @param {Function} onComplete - 完成回调
         * @param {Function} onPromptTriggered - prompt触发回调
         */
        start(stage, onComplete, onPromptTriggered) {
            console.log('[DiscreteGestureAnimation] 开始Stage动画:', stage.name);
            
            // 确保Canvas已初始化
            if (!this.canvas) {
                if (!this.init('.animation-area')) {
                    console.error('[DiscreteGestureAnimation] Canvas初始化失败');
                    if (onComplete) onComplete();
                    return;
                }
            }
            
            this.currentStage = stage;
            this.onComplete = onComplete;
            this.onPromptTriggered = onPromptTriggered;
            
            // 获取这个stage需要的prompt数量
            if (window.CollectionTiming) {
                this.totalPromptCount = window.CollectionTiming.getPromptCount(
                    this.currentTaskId, 
                    stage.name
                );
            } else {
                this.totalPromptCount = 10; // 默认值
            }
            
            console.log('[DiscreteGestureAnimation] Stage prompts:', this.totalPromptCount);
            
            // 重置状态
            this.prompts = [];
            this.nextPromptIndex = 0;
            this.executedCount = 0;
            this.isRunning = true;
            
            // 调整Canvas
            this.resizeCanvas();
            
            // 显示Canvas
            this.canvas.style.display = 'block';
            
            // 创建第一个提示
            this.createNextPrompt();
            
            // 开始动画循环
            this.animate();
        }

        /**
         * 创建下一个提示
         */
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

        /**
         * 动画循环
         */
        animate() {
            if (!this.isRunning) return;
            
            // 检查是否所有prompt都已经通过
            if (this.executedCount >= this.totalPromptCount) {
                // 等待最后一个prompt完全离开屏幕
                const allGone = this.prompts.every(p => p.x < -150);
                if (allGone || this.prompts.length === 0) {
                    this.stop();
                    if (this.onComplete) this.onComplete();
                    return;
                }
            }
            
            // 确保canvas尺寸有效
            if (!this.config.canvasWidth || !this.config.canvasHeight) {
                if (!this.resizeCanvas()) {
                    this.animationId = requestAnimationFrame(() => this.animate());
                    return;
                }
            }
            
            // 清除画布
            this.ctx.clearRect(0, 0, this.config.canvasWidth, this.config.canvasHeight);
            
            // 移动所有提示
            this.prompts.forEach(p => {
                p.x -= this.config.scrollSpeed;
            });
            
            // 移除已经完全离开画布的提示
            this.prompts = this.prompts.filter(p => p.x > -150);
            
            // 创建新提示（如果需要）
            if (this.nextPromptIndex < this.totalPromptCount) {
                const last = this.prompts[this.prompts.length - 1];
                if (!last || last.x < this.config.canvasWidth - this.config.promptSpacing) {
                    this.createNextPrompt();
                }
            }
            
            // 更新提示状态
            this.updatePrompts();
            
            // 绘制背景信息（stage名称等）
            this.drawStageInfo();
            
            // 绘制指示线
            this.drawIndicator();
            
            // 绘制所有提示
            this.prompts.forEach(p => this.drawPrompt(p));
            
            // 绘制进度信息
            this.drawProgress();
            
            // 继续下一帧
            this.animationId = requestAnimationFrame(() => this.animate());
        }

        /**
         * 更新提示状态
         */
        updatePrompts() {
            this.prompts.forEach(prompt => {
                if (!prompt.isPassed) {
                    const dist = prompt.x - this.config.indicatorX;
                    
                    // 检查是否到达指示线区域
                    if (Math.abs(dist) <= 20) {
                        prompt.isActive = true;
                        
                        // 发送Prompt信号（只发送一次）
                        if (!prompt.promptSent) {
                            prompt.promptSent = true;
                            this.triggerPrompt(prompt);
                        }
                    }
                    
                    // 检查是否已经通过指示线
                    if (dist < -25) {
                        prompt.isActive = false;
                        prompt.isPassed = true;
                        this.executedCount++;
                        console.log(`[DiscreteGestureAnimation] Prompt ${prompt.index + 1}/${this.totalPromptCount} 完成`);
                    }
                }
            });
        }

        /**
         * 触发Prompt信号
         */
        triggerPrompt(prompt) {
            console.log(`[DiscreteGestureAnimation] 触发Prompt: ${prompt.type} #${prompt.index + 1}`);
            
            // 调用回调
            if (this.onPromptTriggered) {
                this.onPromptTriggered(prompt.type, prompt.index, this.currentStage.name);
            }
            
            // 发送到realtimeEngine
            if (window.animationController && window.animationController.sendPrompt) {
                window.animationController.sendPrompt(
                    prompt.type, 
                    this.currentStage.name
                );
            }
        }

        /**
         * 绘制Stage信息
         */
        drawStageInfo() {
            const ctx = this.ctx;
            const gesture = this.gestureTypes[this.currentStage.name] || {
                label: this.currentStage.label || this.currentStage.name,
                icon: '●',
                color: '#3b82f6'
            };
            
            ctx.save();
            
            // 在左上角显示当前Stage名称
            ctx.fillStyle = 'rgba(30, 64, 175, 0.9)';
            ctx.font = '700 24px ui-sans-serif, system-ui, -apple-system';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'top';
            ctx.fillText(`当前动作: ${gesture.label}`, 20, 20);
            
            // 显示指导文字
            const stageConfig = this.currentStage;
            if (stageConfig.instruction) {
                ctx.fillStyle = 'rgba(107, 114, 128, 0.9)';
                ctx.font = '500 16px ui-sans-serif, system-ui';
                ctx.fillText(stageConfig.instruction, 20, 52);
            }
            
            ctx.restore();
        }

        /**
         * 绘制指示线
         */
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
            
            // 在指示线上方添加文字
            ctx.fillStyle = this.config.colors.indicator;
            ctx.font = '600 12px ui-sans-serif, system-ui';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';
            ctx.fillText('采集点', x, y - 110);
            
            ctx.restore();
        }

        /**
         * 绘制单个提示
         */
        drawPrompt(prompt) {
            const gesture = this.gestureTypes[prompt.type] || {
                label: prompt.type,
                icon: '●',
                color: '#3b82f6'
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
            
            // 绘制气泡
            const badgeW = 70;
            const badgeH = 70;
            const badgeR = 14;
            const gap = 12;
            const bx = x - badgeW / 2;
            const by = y - (this.config.promptLength / 2) - gap - badgeH;
            
            // 气泡背景
            ctx.fillStyle = 'rgba(255, 255, 255, 0.95)';
            ctx.strokeStyle = color;
            ctx.lineWidth = 3;
            ctx.beginPath();
            ctx.roundRect(bx, by, badgeW, badgeH, badgeR);
            ctx.fill();
            ctx.stroke();
            
            // 图标
            ctx.fillStyle = color;
            ctx.font = '900 32px ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji"';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(gesture.icon, x, by + badgeH / 2);
            
            // 序号
            ctx.font = '600 12px ui-sans-serif, system-ui';
            ctx.textBaseline = 'top';
            ctx.fillText(`#${prompt.index + 1}`, x, y + this.config.labelOffset);
            
            ctx.restore();
        }

        /**
         * 绘制进度信息
         */
        drawProgress() {
            const ctx = this.ctx;
            
            ctx.save();
            
            // 右下角显示进度
            ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
            ctx.font = '700 18px ui-sans-serif, system-ui';
            ctx.textAlign = 'right';
            ctx.textBaseline = 'bottom';
            ctx.fillText(
                `${this.executedCount} / ${this.totalPromptCount}`, 
                this.config.canvasWidth - 20, 
                this.config.canvasHeight - 20
            );
            
            // 进度条
            const barWidth = 150;
            const barHeight = 8;
            const barX = this.config.canvasWidth - 20 - barWidth;
            const barY = this.config.canvasHeight - 45;
            const progress = this.executedCount / this.totalPromptCount;
            
            // 背景
            ctx.fillStyle = '#e5e7eb';
            ctx.beginPath();
            ctx.roundRect(barX, barY, barWidth, barHeight, 4);
            ctx.fill();
            
            // 进度
            ctx.fillStyle = '#22c55e';
            ctx.beginPath();
            ctx.roundRect(barX, barY, barWidth * progress, barHeight, 4);
            ctx.fill();
            
            ctx.restore();
        }

        /**
         * 停止动画
         */
        stop() {
            console.log('[DiscreteGestureAnimation] 停止动画');
            this.isRunning = false;
            
            if (this.animationId) {
                cancelAnimationFrame(this.animationId);
                this.animationId = null;
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
         * 销毁实例
         */
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

        /**
         * 检查是否正在运行
         */
        isAnimationRunning() {
            return this.isRunning;
        }

        /**
         * 获取当前进度
         */
        getProgress() {
            return {
                executed: this.executedCount,
                total: this.totalPromptCount,
                percent: this.totalPromptCount > 0 ? 
                    (this.executedCount / this.totalPromptCount * 100) : 0
            };
        }
    }

    // 创建全局实例
    const discreteGestureAnimation = new DiscreteGestureAnimationController();
    window.discreteGestureAnimation = discreteGestureAnimation;
    
    console.log('[DiscreteGestureAnimation] 离散手势动画模块加载完成');

})();
