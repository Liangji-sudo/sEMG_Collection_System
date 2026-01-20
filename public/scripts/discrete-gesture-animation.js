/**
 * discrete-gesture-animation.js - 离散手势采集动画模块
 * 
 * 概念说明：
 * - Stage: 一个采集阶段（如"手心朝上"姿势）
 * - Prompt: Stage内的具体手势动作（如"拇指上滑"、"食指点击"等）
 * 
 * 动画特点：
 * - 在一个Stage内，按顺序播放多个不同的Prompt
 * - 每个Prompt约1秒经过指示线
 * - Prompt定义从DISCRETE_GESTURE_CONFIG.PROMPT_LIBRARY读取
 * - Prompt序列从Stage的promptSequence数组读取
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
            this.prompts = [];           // 当前在屏幕上的prompt对象
            this.nextPromptIndex = 0;    // 下一个要创建的prompt在序列中的索引
            this.executedCount = 0;      // 已经通过指示线的prompt数
            
            // 当前Stage的Prompt序列
            this.promptSequence = [];    // 从配置读取的prompt名称数组
            this.promptLibrary = {};     // prompt定义库
            
            // 当前stage信息
            this.currentStage = null;
            this.currentTaskId = 'discrete_gesture';
            this.onComplete = null;
            this.onPromptTriggered = null;
            
            // 配置
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
                indicatorPosition: 0.3,
                colors: {
                    active: '#10b981',
                    passed: '#9ca3af',
                    normal: '#3b82f6',
                    indicator: '#ef4444'
                }
            };
        }

        /**
         * 从配置加载
         */
        loadConfig() {
            if (window.DISCRETE_GESTURE_CONFIG) {
                const taskConfig = window.DISCRETE_GESTURE_CONFIG;
                const animConfig = taskConfig.ANIMATION;
                
                // 加载Prompt库
                this.promptLibrary = taskConfig.PROMPT_LIBRARY || {};
                
                // 加载动画配置
                if (animConfig) {
                    this.config.scrollSpeed = animConfig.SCROLL_SPEED || 2;
                    this.config.promptSpacing = animConfig.PROMPT_SPACING || 120;
                    this.config.promptLength = animConfig.PROMPT_LENGTH || 60;
                    this.config.promptThickness = animConfig.PROMPT_THICKNESS || 10;
                    this.config.labelOffset = animConfig.LABEL_OFFSET || 80;
                    this.config.indicatorPosition = animConfig.INDICATOR_POSITION || 0.3;
                    
                    if (animConfig.COLORS) {
                        this.config.colors = { ...animConfig.COLORS };
                    }
                }
                
                console.log('[DiscreteGestureAnimation] 配置已加载，Prompt库:', 
                    Object.keys(this.promptLibrary).length, '个动作');
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
                console.warn('[DiscreteGestureAnimation] 未找到容器元素');
                return false;
            }
            
            const containerStyle = window.getComputedStyle(this.containerElement);
            if (containerStyle.position === 'static') {
                this.containerElement.style.position = 'relative';
            }
            
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
                
                this.canvas.width = rect.width * dpr;
                this.canvas.height = rect.height * dpr;
                this.canvas.style.width = rect.width + 'px';
                this.canvas.style.height = rect.height + 'px';
                this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
                
                this.config.canvasWidth = rect.width;
                this.config.canvasHeight = rect.height;
                this.config.indicatorX = rect.width * this.config.indicatorPosition;
                this.config.centerY = rect.height / 2;
                
                return true;
            }
            return false;
        }

        /**
         * 获取Prompt定义
         */
        getPromptDef(promptName) {
            return this.promptLibrary[promptName] || {
                label: promptName,
                icon: '●',
                color: '#6b7280'
            };
        }

        /**
         * 创建Prompt对象
         */
        createPromptObject(promptName, startX, index) {
            const def = this.getPromptDef(promptName);
            return {
                name: promptName,
                label: def.label,
                icon: def.icon,
                color: def.color,
                x: startX,
                index: index,
                isActive: false,
                isPassed: false,
                promptSent: false
            };
        }

        /**
         * 【新增】开始单个手势的重复采集动画
         * 这是给 collection-controller.js 调用的接口
         * @param {Object} gesture - 手势配置 {id, name, icon, ...}
         * @param {Object} executionParams - 执行参数 {repeatPerGesture, ...}
         * @param {Function} onComplete - 完成回调
         */
        startGesture(gesture, executionParams, onComplete) {
            console.log('[DiscreteGestureAnimation] ★★★ startGesture 被调用 ★★★');
            console.log('[DiscreteGestureAnimation] 手势对象:', JSON.stringify(gesture));
            console.log('[DiscreteGestureAnimation] 手势名称:', gesture.name);
            console.log('[DiscreteGestureAnimation] 手势图标:', gesture.icon);
            console.log('[DiscreteGestureAnimation] 执行参数:', executionParams);

            // 【修复】先初始化Canvas（如果需要），避免后续init()覆盖promptLibrary
            if (!this.canvas) {
                this.init('.animation-area');
            }

            // 从executionParams获取重复次数
            const repeatCount = executionParams?.repeatPerGesture || 5;

            // 【修复】直接使用gesture.name作为gestureId，确保显示用户定义的名称
            const gestureId = gesture.name;

            // 【修复】确保icon不为空，检查多种可能的空值情况
            let icon = gesture.icon;
            if (!icon || icon === '' || icon === 'undefined' || icon === 'null') {
                icon = '✋';  // 默认图标
            }

            this.promptLibrary[gestureId] = {
                label: gesture.name,
                icon: icon,
                color: gesture.color || '#3b82f6'
            };

            console.log('[DiscreteGestureAnimation] 添加到promptLibrary:', this.promptLibrary[gestureId]);
            
            // 创建重复的promptSequence
            const promptSequence = [];
            for (let i = 0; i < repeatCount; i++) {
                promptSequence.push(gestureId);
            }
            
            // 获取当前Stage信息
            let stageName = 'gesture_stage';
            let stageLabel = '手势采集';
            let stageIcon = '🤲';
            let stageInstruction = `请执行 ${gesture.name} 手势`;
            
            if (window.collectionController) {
                const ctrl = window.collectionController;
                const currentStage = ctrl.stages?.[ctrl.currentStageIndex];
                if (currentStage) {
                    stageName = currentStage.name || currentStage.id || stageName;
                    stageLabel = currentStage.name || stageLabel;
                    stageInstruction = currentStage.instruction || stageInstruction;
                }
            }
            
            // 构造stage配置
            const stageConfig = {
                name: stageName,
                label: stageLabel,
                icon: stageIcon,
                instruction: stageInstruction,
                promptSequence: promptSequence
            };
            
            console.log('[DiscreteGestureAnimation] 生成的promptSequence:', promptSequence);
            console.log('[DiscreteGestureAnimation] Stage配置:', stageConfig);
            
            // 调用原有的start方法
            this.start(stageConfig, onComplete, (promptName, index, stageName) => {
                // 触发prompt时通知后端
                if (window.collectionController) {
                    window.collectionController.gestureRepeatCount = index + 1;
                    window.collectionController.updateGestureList?.();
                    
                    // 发送prompt信号到后端
                    window.collectionController.sendToRealtimeEngine?.('prompt', {
                        name: promptName,
                        stageName: stageName,
                        repeatIndex: index,
                        timestamp: Date.now()
                    });
                }
            });
        }

        /**
         * 开始Stage动画
         * @param {Object} stage - stage配置
         * @param {Function} onComplete - 完成回调
         * @param {Function} onPromptTriggered - prompt触发回调
         */
        start(stage, onComplete, onPromptTriggered) {
            console.log('[DiscreteGestureAnimation] 开始Stage动画:', stage.name);
            
            if (!this.canvas) {
                if (!this.init('.animation-area')) {
                    console.error('[DiscreteGestureAnimation] Canvas初始化失败');
                    if (onComplete) onComplete();
                    return;
                }
            }
            
            // 重新加载Prompt库（支持动态添加的手势）
            this.reloadPromptLibrary();
            
            this.currentStage = stage;
            this.onComplete = onComplete;
            this.onPromptTriggered = onPromptTriggered;
            
            // 获取这个Stage的Prompt序列
            // 优先使用传入的promptSequence（新模式：单手势重复N次）
            if (stage.promptSequence && stage.promptSequence.length > 0) {
                this.promptSequence = [...stage.promptSequence];
                console.log('[DiscreteGestureAnimation] 使用传入的promptSequence');
            } else if (window.CollectionTiming) {
                // 兼容旧模式：从CollectionTiming获取
                this.promptSequence = window.CollectionTiming.getPromptSequence(
                    this.currentTaskId, 
                    stage.name
                );
                console.log('[DiscreteGestureAnimation] 使用CollectionTiming的promptSequence');
            } else {
                this.promptSequence = [];
            }
            
            console.log('[DiscreteGestureAnimation] Prompt序列:', this.promptSequence);
            
            // 如果promptSequence为空，直接完成
            if (this.promptSequence.length === 0) {
                console.warn('[DiscreteGestureAnimation] promptSequence为空，跳过动画');
                if (onComplete) onComplete();
                return;
            }
            
            // 重置状态
            this.prompts = [];
            this.nextPromptIndex = 0;
            this.executedCount = 0;
            this.isRunning = true;
            
            // 调整Canvas
            this.resizeCanvas();
            this.canvas.style.display = 'block';
            
            // 创建第一个提示
            this.createNextPrompt();
            
            // 开始动画循环
            this.animate();
        }

        /**
         * 重新加载Prompt库（合并模式，不覆盖已有的动态添加的手势）
         */
        reloadPromptLibrary() {
            if (window.DISCRETE_GESTURE_CONFIG && window.DISCRETE_GESTURE_CONFIG.PROMPT_LIBRARY) {
                // 【修复】使用合并模式：先加载默认库，再保留已有的动态添加的手势
                const defaultLibrary = window.DISCRETE_GESTURE_CONFIG.PROMPT_LIBRARY;
                // 将默认库合并到现有库（已有的不会被覆盖）
                this.promptLibrary = { ...defaultLibrary, ...this.promptLibrary };
                console.log('[DiscreteGestureAnimation] Prompt库已重新加载，共', 
                    Object.keys(this.promptLibrary).length, '个动作');
            }
        }

        /**
         * 创建下一个提示
         */
        createNextPrompt() {
            if (this.nextPromptIndex >= this.promptSequence.length) return;
            
            const promptName = this.promptSequence[this.nextPromptIndex];
            const startX = this.config.canvasWidth + 50;
            const prompt = this.createPromptObject(promptName, startX, this.nextPromptIndex);
            this.prompts.push(prompt);
            this.nextPromptIndex++;
        }

        /**
         * 动画循环
         */
        animate() {
            if (!this.isRunning) return;
            
            // 检查是否所有prompt都已经通过
            if (this.executedCount >= this.promptSequence.length) {
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
            
            // 清除画布
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
            
            // 绘制
            this.drawStageInfo();
            this.drawIndicator();
            this.prompts.forEach(p => this.drawPrompt(p));
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
                        console.log(`[DiscreteGestureAnimation] Prompt完成: ${prompt.label} (${this.executedCount}/${this.promptSequence.length})`);
                    }
                }
            });
        }

        /**
         * 触发Prompt信号
         */
        triggerPrompt(prompt) {
            console.log(`[DiscreteGestureAnimation] 触发Prompt: ${prompt.name} - ${prompt.label}`);

            // 通过回调通知 collection-controller，由它统一发送 prompt
            if (this.onPromptTriggered) {
                this.onPromptTriggered(prompt.name, prompt.index, this.currentStage.name);
            }

            // 【移除】不再通过 animationController 重复发送，避免双重 prompt
            // if (window.animationController && window.animationController.sendPrompt) {
            //     window.animationController.sendPrompt(prompt.name, this.currentStage.name);
            // }
        }

        /**
         * 绘制Stage信息
         */
        drawStageInfo() {
            const ctx = this.ctx;
            const stageConfig = this.currentStage;
            
            // 从配置获取Stage信息
            let stageLabel = stageConfig.label || stageConfig.name;
            let stageIcon = stageConfig.icon || '🤲';
            
            if (window.DISCRETE_GESTURE_CONFIG && window.DISCRETE_GESTURE_CONFIG.STAGES[stageConfig.name]) {
                const configStage = window.DISCRETE_GESTURE_CONFIG.STAGES[stageConfig.name];
                stageLabel = configStage.label || stageLabel;
                stageIcon = configStage.icon || stageIcon;
            }
            
            ctx.save();
            
            // 左上角显示当前Stage名称
            ctx.fillStyle = 'rgba(30, 64, 175, 0.95)';
            ctx.font = '700 24px ui-sans-serif, system-ui, -apple-system';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'top';
            ctx.fillText(`${stageIcon} 姿势: ${stageLabel}`, 20, 20);
            
            // 显示指导文字
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
            
            ctx.fillStyle = this.config.colors.indicator;
            ctx.font = '600 12px ui-sans-serif, system-ui';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';
            ctx.fillText('采集点', x, y - 110);
            
            ctx.restore();
        }

        /**
         * 绘制单个Prompt
         */
        drawPrompt(prompt) {
            let color = prompt.color || this.config.colors.normal;
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
            
            ctx.fillStyle = 'rgba(255, 255, 255, 0.95)';
            ctx.strokeStyle = color;
            ctx.lineWidth = 3;
            ctx.beginPath();
            ctx.roundRect(bx, by, badgeW, badgeH, badgeR);
            ctx.fill();
            ctx.stroke();
            
            // 图标 - 使用DOM元素绘制emoji以确保兼容性
            this.drawEmojiIcon(prompt.icon, x, by + badgeH / 2, color);
            
            // 动作名称
            ctx.font = '600 11px ui-sans-serif, system-ui';
            ctx.fillStyle = color;
            ctx.textBaseline = 'top';
            ctx.fillText(prompt.label, x, y + this.config.labelOffset);
            
            ctx.restore();
        }

        /**
         * 绘制emoji图标（兼容Electron）
         */
        drawEmojiIcon(icon, x, y, color) {
            const ctx = this.ctx;
            
            // 检测是否为emoji
            const isEmoji = /[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\u{1F600}-\u{1F64F}]|[\u{1F680}-\u{1F6FF}]|[\u{1F1E0}-\u{1F1FF}]|[\u{1F900}-\u{1F9FF}]|[\u{1FA00}-\u{1FA6F}]|[\u{2300}-\u{23FF}]|[\u{2B50}]|[\u{270A}-\u{270D}]|[\u{1F44D}-\u{1F44F}]|[\u{1F91A}-\u{1F91F}]|[\u{1F90C}-\u{1F90F}]|[\u{261D}]|[\u{1F446}-\u{1F449}]|[\u{1F590}]|[\u{1F595}-\u{1F596}]|[\u270C]/u.test(icon);
            
            ctx.save();
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            
            if (isEmoji) {
                // 对于emoji，创建临时canvas来绘制
                const tempCanvas = document.createElement('canvas');
                const tempCtx = tempCanvas.getContext('2d');
                const size = 40;
                tempCanvas.width = size;
                tempCanvas.height = size;
                
                tempCtx.font = `${size - 8}px "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif`;
                tempCtx.textAlign = 'center';
                tempCtx.textBaseline = 'middle';
                tempCtx.fillText(icon, size / 2, size / 2);
                
                // 将临时canvas绘制到主canvas
                ctx.drawImage(tempCanvas, x - size / 2, y - size / 2, size, size);
            } else {
                // 非emoji使用普通方式绘制
                ctx.fillStyle = color;
                ctx.font = '900 32px ui-sans-serif, system-ui, -apple-system';
                ctx.fillText(icon, x, y);
            }
            
            ctx.restore();
        }

        /**
         * 绘制进度信息
         */
        drawProgress() {
            const ctx = this.ctx;
            const total = this.promptSequence.length;
            
            ctx.save();
            
            // 右下角显示进度
            ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
            ctx.font = '700 18px ui-sans-serif, system-ui';
            ctx.textAlign = 'right';
            ctx.textBaseline = 'bottom';
            ctx.fillText(
                `${this.executedCount} / ${total}`, 
                this.config.canvasWidth - 20, 
                this.config.canvasHeight - 20
            );
            
            // 进度条
            const barWidth = 150;
            const barHeight = 8;
            const barX = this.config.canvasWidth - 20 - barWidth;
            const barY = this.config.canvasHeight - 45;
            const progress = total > 0 ? this.executedCount / total : 0;
            
            ctx.fillStyle = '#e5e7eb';
            ctx.beginPath();
            ctx.roundRect(barX, barY, barWidth, barHeight, 4);
            ctx.fill();
            
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
                total: this.promptSequence.length,
                percent: this.promptSequence.length > 0 ? 
                    (this.executedCount / this.promptSequence.length * 100) : 0
            };
        }
    }

    // 创建全局实例
    const discreteGestureAnimation = new DiscreteGestureAnimationController();
    window.discreteGestureAnimation = discreteGestureAnimation;
    
    console.log('[DiscreteGestureAnimation] 离散手势动画模块加载完成');

})();