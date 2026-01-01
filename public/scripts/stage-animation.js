/**
 * stage-animation.js - Stage内容动画模块
 * 
 * 这个文件负责在每个Stage期间播放的内容动画
 * 移植自lab.js的手势识别采集动画
 * 
 * 动画特点：
 * - 提示从右向左滚动
 * - 约1秒一个提示经过指示线
 * - 可自定义提示内容、颜色等
 * 
 * 使用方式：
 * 由animation-controller.js调用，在playStageContent时启动
 */

(function() {
    'use strict';

    console.log('[StageAnimation] Stage动画模块开始加载...');

    class StageAnimationController {
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
            this.executedCount = 0;
            
            // 当前stage信息
            this.currentStage = null;
            this.onComplete = null;
            this.startTime = null;
            this.duration = 5000; // 默认5秒
            
            // 配置
            this.config = {
                indicatorX: 0,
                scrollSpeed: 2,           // 像素/帧，60fps下约120像素/秒
                promptSpacing: 120,       // 提示间距，约1秒一个
                promptLength: 60,         // 提示竖线长度
                promptThickness: 10,      // 提示竖线粗细
                labelOffset: 80,          // 标签偏移
                centerY: 0,
                activeColor: '#10b981',   // 激活颜色（绿色）
                passedColor: '#9ca3af',   // 已过颜色（灰色）
                normalColor: '#3b82f6',   // 普通颜色（蓝色）
                indicatorColor: '#ef4444' // 指示线颜色（红色）
            };
            
            // 手势类型定义（可在task-config.js中自定义）
            this.gestureTypes = {
                'palm_up': { label: '手心向上', arrow: '↑', color: '#3b82f6' },
                'palm_inward': { label: '手心向内', arrow: '→', color: '#3b82f6' },
                'hand_on_knee': { label: '手放膝盖', arrow: '↓', color: '#3b82f6' },
                'hand_on_desk': { label: '手放桌上', arrow: '◐', color: '#3b82f6' },
                // 连续手势
                'finger_spread': { label: '手指张合', arrow: '✋', color: '#10b981' },
                'finger_tap': { label: '手指点击', arrow: '👆', color: '#10b981' },
                'finger_extend': { label: '手指伸展', arrow: '🖐', color: '#10b981' },
                'finger_curl': { label: '手指弯曲', arrow: '✊', color: '#10b981' },
                'wrist_rotation': { label: '手腕旋转', arrow: '🔄', color: '#f59e0b' },
                'wrist_updown': { label: '手腕上下', arrow: '↕', color: '#f59e0b' },
                'wrist_leftright': { label: '手腕左右', arrow: '↔', color: '#f59e0b' },
                'fist_rotation': { label: '握拳旋转', arrow: '👊', color: '#f59e0b' }
            };
            
            // 提示序列
            this.promptSequence = [];
        }

        /**
         * 初始化Canvas
         */
        init(containerSelector) {
            // 优先查找animation-area（整个动画区域），而不是gestureDisplay（小区域）
            this.containerElement = document.querySelector('.animation-area') ||
                                    document.querySelector(containerSelector) || 
                                    document.getElementById('gestureDisplay');
            
            if (!this.containerElement) {
                console.warn('[StageAnimation] 未找到容器元素');
                return false;
            }
            
            // 确保容器有相对定位
            const containerStyle = window.getComputedStyle(this.containerElement);
            if (containerStyle.position === 'static') {
                this.containerElement.style.position = 'relative';
            }
            
            // 创建Canvas
            this.canvas = document.getElementById('stageAnimationCanvas');
            if (!this.canvas) {
                this.canvas = document.createElement('canvas');
                this.canvas.id = 'stageAnimationCanvas';
                this.canvas.style.cssText = `
                    position: absolute;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    pointer-events: none;
                    z-index: 50;
                    background: rgba(255, 255, 255, 0.98);
                `;
                this.containerElement.appendChild(this.canvas);
            }
            
            this.ctx = this.canvas.getContext('2d');
            this.resizeCanvas();
            
            // 监听窗口大小变化
            this._resizeHandler = () => this.resizeCanvas();
            window.addEventListener('resize', this._resizeHandler);
            
            console.log('[StageAnimation] 初始化完成，容器:', this.containerElement.className);
            return true;
        }

        /**
         * 调整Canvas大小 - 修复变形问题
         */
        resizeCanvas() {
            if (!this.canvas || !this.containerElement) return false;
            
            const rect = this.containerElement.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                // 关键：Canvas的width/height属性必须与实际显示尺寸一致
                // 否则会导致拉伸变形
                const dpr = window.devicePixelRatio || 1;
                
                // 设置Canvas的实际像素尺寸
                this.canvas.width = rect.width * dpr;
                this.canvas.height = rect.height * dpr;
                
                // 设置Canvas的CSS显示尺寸
                this.canvas.style.width = rect.width + 'px';
                this.canvas.style.height = rect.height + 'px';
                
                // 缩放绘图上下文以适应高DPI屏幕
                this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
                
                // 更新配置中的动态值（使用CSS尺寸，不是Canvas像素尺寸）
                this.config.canvasWidth = rect.width;
                this.config.canvasHeight = rect.height;
                this.config.indicatorX = rect.width * 0.3;
                this.config.centerY = rect.height / 2;
                
                console.log('[StageAnimation] Canvas尺寸:', rect.width, 'x', rect.height, 'DPR:', dpr);
                return true;
            }
            return false;
        }

        /**
         * 生成提示序列
         * @param {Object} stage - stage配置
         * @param {number} duration - 动画持续时间（毫秒）
         */
        generatePromptSequence(stage, duration) {
            // 计算需要多少个提示
            // 约1秒一个提示，但要确保有足够的提示填满整个动画时间
            const durationSeconds = duration / 1000;
            const promptCount = Math.ceil(durationSeconds) + 2; // 额外添加2个以确保平滑
            
            this.promptSequence = [];
            
            // 如果stage有特定的prompt序列，使用它
            if (stage.promptSequence && Array.isArray(stage.promptSequence)) {
                this.promptSequence = stage.promptSequence.slice(0, promptCount);
            } else {
                // 否则，重复当前stage的名称作为提示
                for (let i = 0; i < promptCount; i++) {
                    this.promptSequence.push(stage.name);
                }
            }
        }

        /**
         * Prompt类
         */
        createPrompt(type, startX) {
            return {
                type: type,
                x: startX,
                isActive: false,
                isPassed: false,
                executed: false
            };
        }

        /**
         * 开始Stage动画
         * @param {Object} stage - stage配置
         * @param {number} duration - 动画持续时间（毫秒）
         * @param {Function} onComplete - 完成回调
         */
        start(stage, duration, onComplete) {
            console.log('[StageAnimation] 开始Stage动画:', stage.name);
            
            // 确保Canvas已初始化（使用animation-area作为容器）
            if (!this.canvas) {
                if (!this.init('.animation-area')) {
                    console.error('[StageAnimation] Canvas初始化失败');
                    if (onComplete) onComplete();
                    return;
                }
            }
            
            this.currentStage = stage;
            this.duration = duration;
            this.onComplete = onComplete;
            this.startTime = Date.now();
            
            // 重置状态
            this.prompts = [];
            this.nextPromptIndex = 0;
            this.executedCount = 0;
            this.isRunning = true;
            
            // 生成提示序列
            this.generatePromptSequence(stage, duration);
            
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
            if (this.nextPromptIndex >= this.promptSequence.length) return;
            
            const type = this.promptSequence[this.nextPromptIndex];
            const startX = this.config.canvasWidth + 50;
            this.prompts.push(this.createPrompt(type, startX));
            this.nextPromptIndex++;
        }

        /**
         * 动画循环
         */
        animate() {
            if (!this.isRunning) return;
            
            // 检查是否超时
            const elapsed = Date.now() - this.startTime;
            if (elapsed >= this.duration) {
                this.stop();
                if (this.onComplete) this.onComplete();
                return;
            }
            
            // 确保canvas尺寸有效
            if (!this.config.canvasWidth || !this.config.canvasHeight) {
                if (!this.resizeCanvas()) {
                    this.animationId = requestAnimationFrame(() => this.animate());
                    return;
                }
            }
            
            // 清除画布（使用CSS尺寸）
            this.ctx.clearRect(0, 0, this.config.canvasWidth, this.config.canvasHeight);
            
            // 移动所有提示
            this.prompts.forEach(p => {
                p.x -= this.config.scrollSpeed;
            });
            
            // 移除已经完全离开画布的提示
            this.prompts = this.prompts.filter(p => p.x > -150);
            
            // 创建新提示（如果需要）
            if (this.nextPromptIndex < this.promptSequence.length) {
                const last = this.prompts[this.prompts.length - 1];
                if (!last || last.x < this.config.canvasWidth - this.config.promptSpacing) {
                    this.createNextPrompt();
                }
            }
            
            // 更新提示状态
            this.updatePrompts();
            
            // 绘制指示线
            this.drawIndicator();
            
            // 绘制所有提示
            this.prompts.forEach(p => this.drawPrompt(p));
            
            // 绘制剩余时间
            this.drawTimer(elapsed);
            
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
                            this.sendPromptSignal(prompt.type);
                        }
                    }
                    
                    // 检查是否已经通过指示线
                    if (dist < -25) {
                        prompt.isActive = false;
                        prompt.isPassed = true;
                        this.executedCount++;
                    }
                }
            });
        }

        /**
         * 发送Prompt信号到realtimeEngine
         */
        sendPromptSignal(promptName) {
            if (window.animationController && window.animationController.sendPrompt) {
                window.animationController.sendPrompt(promptName, this.currentStage ? this.currentStage.name : '');
            }
        }

        /**
         * 绘制指示线
         */
        drawIndicator() {
            const ctx = this.ctx;
            const x = this.config.indicatorX;
            const y = this.config.centerY;
            
            ctx.save();
            ctx.strokeStyle = this.config.indicatorColor;
            ctx.lineWidth = 4;
            ctx.setLineDash([10, 5]);
            ctx.beginPath();
            ctx.moveTo(x, y - 80);
            ctx.lineTo(x, y + 80);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.restore();
        }

        /**
         * 绘制单个提示
         */
        drawPrompt(prompt) {
            const gesture = this.gestureTypes[prompt.type] || {
                label: prompt.type,
                arrow: '●',
                color: '#3b82f6'
            };
            
            let color = this.config.normalColor;
            if (prompt.isPassed) {
                color = this.config.passedColor;
            } else if (prompt.isActive) {
                color = this.config.activeColor;
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
            
            // 绘制箭头气泡
            const badgeW = 80;
            const badgeH = 80;
            const badgeR = 16;
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
            
            // 箭头/图标
            ctx.fillStyle = color;
            ctx.font = '900 40px ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji"';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(gesture.arrow, x, by + badgeH / 2);
            
            // 标签
            ctx.font = '700 14px ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial';
            ctx.textBaseline = 'top';
            ctx.fillText(gesture.label, x, y + this.config.labelOffset);
            
            ctx.restore();
        }

        /**
         * 绘制剩余时间
         */
        drawTimer(elapsed) {
            const remaining = Math.max(0, this.duration - elapsed);
            const seconds = Math.ceil(remaining / 1000);
            
            const ctx = this.ctx;
            ctx.save();
            
            // 右下角显示剩余时间（使用CSS尺寸）
            ctx.fillStyle = 'rgba(0, 0, 0, 0.6)';
            ctx.font = '700 20px ui-sans-serif, system-ui';
            ctx.textAlign = 'right';
            ctx.textBaseline = 'bottom';
            ctx.fillText(`${seconds}s`, this.config.canvasWidth - 20, this.config.canvasHeight - 20);
            
            ctx.restore();
        }

        /**
         * 停止动画
         */
        stop() {
            console.log('[StageAnimation] 停止动画');
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
         * 注册自定义手势类型
         */
        registerGestureType(name, config) {
            this.gestureTypes[name] = config;
        }

        /**
         * 批量注册手势类型
         */
        registerGestureTypes(types) {
            Object.assign(this.gestureTypes, types);
        }
    }

    // 创建全局实例
    const stageAnimationController = new StageAnimationController();
    window.stageAnimationController = stageAnimationController;
    
    console.log('[StageAnimation] Stage动画模块加载完成');

})();
