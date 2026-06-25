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
            
            this.containerElement = document.getElementById('gestureAnimationViewport') ||
                                    document.querySelector('.animation-area') ||
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
         * @param {string} promptName - prompt名称
         * @param {number} startX - 起始X坐标
         * @param {number} index - 序列索引
         * @param {Object} gestureConfig - 【新增】手势配置（包含gestureType, duration等）
         */
        createPromptObject(promptName, startX, index, gestureConfig = null) {
            const def = this.getPromptDef(promptName);
            // 【新增】从配置获取手势类型，默认为瞬时
            const gestureType = gestureConfig?.gestureType || def.gestureType || 'instant';

            // 【修复】获取持续时间：优先使用手势自身的duration，其次使用def中的duration，最后使用全局sustainedDuration
            // 这样配置为1秒的手势，长方形长度就是1秒对应的距离
            const gestureDuration = gestureConfig?.duration || def.originalGesture?.duration || this.sustainedDuration || 2.0;

            // 【修复】计算长方形宽度：持续时间 * 滚动速度 * 60fps
            // 确保持续性手势的长度与配置的时间一致
            const rectWidth = gestureType === 'sustained' ? gestureDuration * this.config.scrollSpeed * 60 : 0;

            if (gestureType === 'sustained') {
                console.log(`[DiscreteGestureAnimation] 创建持续手势: ${promptName}`);
                console.log(`  - gestureConfig?.duration: ${gestureConfig?.duration}`);
                console.log(`  - def.originalGesture?.duration: ${def.originalGesture?.duration}`);
                console.log(`  - this.sustainedDuration: ${this.sustainedDuration}`);
                console.log(`  - 最终使用的duration: ${gestureDuration}秒`);
                console.log(`  - scrollSpeed: ${this.config.scrollSpeed}px/帧`);
                console.log(`  - 计算得到的rectWidth: ${rectWidth}px`);
            }

            return {
                name: promptName,
                label: def.label,
                icon: def.icon,
                color: def.color,
                x: startX,
                index: index,
                isActive: false,
                isPassed: false,
                promptSent: false,
                // 【新增】持续手势相关属性
                gestureType: gestureType,
                rectWidth: rectWidth,           // 长方形宽度
                startTriggered: false,          // _start 是否已触发
                endTriggered: false,            // _end 是否已触发
                isInProgress: false             // 是否正在执行中（长方形覆盖指示线）
            };
        }

        /**
         * 【新增】开始单个手势的重复采集动画
         * 这是给 collection-controller.js 调用的接口
         * @param {Object} gesture - 手势配置 {id, name, icon, gestureType, ...}
         * @param {Object} executionParams - 执行参数 {repeatPerGesture, sustainedDuration, scrollSpeed, ...}
         * @param {Function} onComplete - 完成回调
         */
        startGesture(gesture, executionParams, onComplete) {
            console.log('[DiscreteGestureAnimation] ★★★ startGesture 被调用 ★★★');
            console.log('[DiscreteGestureAnimation] 手势对象:', JSON.stringify(gesture));
            console.log('[DiscreteGestureAnimation] 手势名称:', gesture.name);
            console.log('[DiscreteGestureAnimation] 手势图标:', gesture.icon);
            console.log('[DiscreteGestureAnimation] 手势类型:', gesture.gestureType || 'instant');
            console.log('[DiscreteGestureAnimation] 执行参数:', executionParams);

            // 【修复】先初始化Canvas（如果需要），避免后续init()覆盖promptLibrary
            if (!this.canvas) {
                this.init('.animation-area');
            }

            // 【重构】从executionParams读取配置参数，实现：距离 = 速度 × 时间
            const scrollSpeed = executionParams?.scrollSpeed || 2;
            const intervalBetweenRepeat = executionParams?.intervalBetweenRepeat || 1.0;

            // 从executionParams获取重复次数和持续时间
            const repeatCount = executionParams?.repeatPerGesture || 5;
            // 【修改】优先使用手势自身的duration，否则使用执行参数中的默认值
            this.sustainedDuration = gesture.duration || executionParams?.sustainedDuration || 2.0;
            // 【新增】保存当前手势配置
            this.currentGestureConfig = gesture;

            // 【核心计算】手势间距 = 滚动速度 × 间隔时间 × 帧率(60fps)
            this.config.scrollSpeed = scrollSpeed;
            this.config.promptSpacing = scrollSpeed * intervalBetweenRepeat * 60;

            console.log('[DiscreteGestureAnimation] 正常模式配置:');
            console.log('  - 滚动速度:', this.config.scrollSpeed, 'px/帧');
            console.log('  - 重复间隔:', intervalBetweenRepeat, '秒');
            console.log('  - 计算得到的手势间距:', this.config.promptSpacing, 'px');
            console.log('  - 持续手势时长:', this.sustainedDuration, '秒');
            console.log('  - 重复次数:', repeatCount);

            // 【修复】直接使用gesture.name作为gestureId，确保显示用户定义的名称
            const gestureId = gesture.name;

            // 【修复】确保icon不为空，检查多种可能的空值情况
            let icon = gesture.icon;
            if (!icon || icon === '' || icon === 'undefined' || icon === 'null') {
                icon = '✋';  // 默认图标
            }

            // 【修改】添加gestureType到promptLibrary
            this.promptLibrary[gestureId] = {
                label: gesture.name,
                icon: icon,
                color: gesture.color || '#3b82f6',
                gestureType: gesture.gestureType || 'instant'  // 【新增】
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

            // 【新增】对于持续手势，修改提示语
            if (gesture.gestureType === 'sustained') {
                stageInstruction = `请执行 ${gesture.name}（持续${this.sustainedDuration}秒）`;
            }

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
            this.start(stageConfig, onComplete, (promptName, index, stageName, promptType) => {
                // 触发prompt时通知后端
                // 【修改】promptType 可以是 'instant', 'start', 'end'
                if (window.collectionController) {
                    window.collectionController.gestureRepeatCount = index + 1;
                    window.collectionController.updateGestureList?.();

                    // 【修改】根据promptType确定发送的prompt名称
                    let finalPromptName = promptName;
                    if (promptType === 'start') {
                        finalPromptName = `${promptName}_start`;
                    } else if (promptType === 'end') {
                        finalPromptName = `${promptName}_end`;
                    }

                    // 发送prompt信号到后端
                    window.collectionController.sendToRealtimeEngine?.('prompt', {
                        name: finalPromptName,
                        stageName: stageName,
                        repeatIndex: index,
                        promptType: promptType,  // 【新增】
                        timestamp: Date.now() / 1000  // 【修改】转换为秒，与ble_server时间戳一致
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
            window.animationPositionManager?.showAnimationPanel();

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
            // 【修改】传递当前手势配置
            const prompt = this.createPromptObject(promptName, startX, this.nextPromptIndex, this.currentGestureConfig);
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
            // 【修复】持续性手势需要考虑矩形右边缘
            this.prompts = this.prompts.filter(p => {
                const rightEdge = p.x + (p.rectWidth || 0);
                return rightEdge > -50;  // 右边缘完全离开画布后才移除
            });
            
            // 创建新提示（如果需要）
            if (this.nextPromptIndex < this.promptSequence.length) {
                const last = this.prompts[this.prompts.length - 1];
                // 【修复】持续性手势需要考虑矩形宽度，否则会重叠
                // 计算最后一个提示的右边缘位置（对于瞬时手势 rectWidth=0）
                const lastRightEdge = last ? (last.x + (last.rectWidth || 0)) : 0;
                if (!last || lastRightEdge < this.config.canvasWidth - this.config.promptSpacing) {
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
         * 【重写】支持瞬时手势和持续手势两种模式
         */
        updatePrompts() {
            this.prompts.forEach(prompt => {
                if (!prompt.isPassed) {
                    // 【修改】根据手势类型使用不同的触发逻辑
                    if (prompt.gestureType === 'sustained') {
                        // ========== 持续手势：长方形模式 ==========
                        // 长方形左边缘位置 = x
                        // 长方形右边缘位置 = x + rectWidth
                        const leftEdge = prompt.x;
                        const rightEdge = prompt.x + prompt.rectWidth;
                        const indicatorX = this.config.indicatorX;

                        // 左边缘碰到指示线：触发 _start
                        if (!prompt.startTriggered && leftEdge <= indicatorX && rightEdge > indicatorX) {
                            prompt.startTriggered = true;
                            prompt.isInProgress = true;
                            prompt.isActive = true;
                            console.log(`[DiscreteGestureAnimation] 持续手势开始: ${prompt.name}_start`);
                            this.triggerPrompt(prompt, 'start');
                        }

                        // 右边缘碰到指示线：触发 _end
                        if (prompt.startTriggered && !prompt.endTriggered && rightEdge <= indicatorX) {
                            prompt.endTriggered = true;
                            prompt.isInProgress = false;
                            console.log(`[DiscreteGestureAnimation] 持续手势结束: ${prompt.name}_end`);
                            this.triggerPrompt(prompt, 'end');
                        }

                        // 完全通过指示线
                        if (rightEdge < indicatorX - 25) {
                            prompt.isActive = false;
                            prompt.isPassed = true;
                            this.executedCount++;
                            console.log(`[DiscreteGestureAnimation] 持续手势完成: ${prompt.label} (${this.executedCount}/${this.promptSequence.length})`);
                        }
                    } else {
                        // ========== 瞬时手势：原有竖线模式 ==========
                        const dist = prompt.x - this.config.indicatorX;

                        if (Math.abs(dist) <= 20) {
                            prompt.isActive = true;

                            if (!prompt.promptSent) {
                                prompt.promptSent = true;
                                this.triggerPrompt(prompt, 'instant');
                            }
                        }

                        if (dist < -25) {
                            prompt.isActive = false;
                            prompt.isPassed = true;
                            this.executedCount++;
                            console.log(`[DiscreteGestureAnimation] Prompt完成: ${prompt.label} (${this.executedCount}/${this.promptSequence.length})`);
                        }
                    }
                }
            });
        }

        /**
         * 触发Prompt信号
         * @param {Object} prompt - prompt对象
         * @param {string} promptType - 触发类型：'instant'(瞬时), 'start'(持续开始), 'end'(持续结束)
         */
        triggerPrompt(prompt, promptType = 'instant') {
            console.log(`[DiscreteGestureAnimation] 触发Prompt: ${prompt.name} - ${prompt.label} (${promptType})`);

            // 通过回调通知 collection-controller，由它统一发送 prompt
            // 【修改】传递promptType参数
            if (this.onPromptTriggered) {
                this.onPromptTriggered(prompt.name, prompt.index, this.currentStage.name, promptType);
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
         * 【重写】支持瞬时手势（竖线）和持续手势（横向长方形）
         */
        drawPrompt(prompt) {
            const ctx = this.ctx;
            const y = this.config.centerY;

            // 【修改】根据手势类型绘制不同形状
            if (prompt.gestureType === 'sustained') {
                // ========== 持续手势：绘制横向长方形 ==========
                this.drawSustainedPrompt(prompt);
            } else {
                // ========== 瞬时手势：原有竖线模式 ==========
                this.drawInstantPrompt(prompt);
            }
        }

        /**
         * 【新增】绘制瞬时手势（竖线 + 气泡）
         */
        drawInstantPrompt(prompt) {
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

            // 绘制气泡（根据emoji数量动态调整宽度）
            const badgeW = this.calculateBadgeWidth(prompt.icon);
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

            // 图标
            this.drawEmojiIcon(prompt.icon, x, by + badgeH / 2, color);

            // 动作名称（自动换行）
            ctx.font = `600 ${this.gestureLabelFontSize || 18}px ui-sans-serif, system-ui`;
            ctx.fillStyle = color;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            this.drawWrappedLabel(prompt.label, x, y + this.config.labelOffset, 130, (this.gestureLabelFontSize || 18) + 4);

            ctx.restore();
        }

        /**
         * 【新增】绘制持续手势（横向长方形 + 气泡）
         */
        drawSustainedPrompt(prompt) {
            const ctx = this.ctx;
            const x = prompt.x;  // 长方形左边缘
            const y = this.config.centerY;
            const rectWidth = prompt.rectWidth;
            const rectHeight = this.config.promptLength;  // 使用与竖线相同的高度

            // 颜色逻辑：
            // - 未开始：蓝色
            // - 进行中（_start已触发）：红色
            // - 已结束（_end已触发）：灰色
            let fillColor, strokeColor;
            if (prompt.isPassed || prompt.endTriggered) {
                fillColor = 'rgba(156, 163, 175, 0.3)';   // 灰色半透明
                strokeColor = this.config.colors.passed;
            } else if (prompt.isInProgress) {
                fillColor = 'rgba(239, 68, 68, 0.3)';     // 红色半透明
                strokeColor = '#ef4444';
            } else {
                fillColor = 'rgba(59, 130, 246, 0.3)';    // 蓝色半透明
                strokeColor = prompt.color || this.config.colors.normal;
            }

            ctx.save();

            // 绘制长方形主体
            ctx.fillStyle = fillColor;
            ctx.strokeStyle = strokeColor;
            ctx.lineWidth = 3;
            ctx.beginPath();
            ctx.roundRect(x, y - rectHeight / 2, rectWidth, rectHeight, 8);
            ctx.fill();
            ctx.stroke();

            // 绘制左边缘标记（_start 触发点）
            ctx.strokeStyle = prompt.startTriggered ? '#ef4444' : strokeColor;
            ctx.lineWidth = 4;
            ctx.beginPath();
            ctx.moveTo(x, y - rectHeight / 2 - 5);
            ctx.lineTo(x, y + rectHeight / 2 + 5);
            ctx.stroke();

            // 绘制右边缘标记（_end 触发点）
            ctx.strokeStyle = prompt.endTriggered ? this.config.colors.passed : strokeColor;
            ctx.lineWidth = 4;
            ctx.beginPath();
            ctx.moveTo(x + rectWidth, y - rectHeight / 2 - 5);
            ctx.lineTo(x + rectWidth, y + rectHeight / 2 + 5);
            ctx.stroke();

            // 绘制气泡（在长方形中央上方，根据emoji数量动态调整宽度）
            const badgeW = this.calculateBadgeWidth(prompt.icon);
            const badgeH = 70;
            const badgeR = 14;
            const gap = 12;
            const centerX = x + rectWidth / 2;
            const bx = centerX - badgeW / 2;
            const by = y - rectHeight / 2 - gap - badgeH;

            ctx.fillStyle = 'rgba(255, 255, 255, 0.95)';
            ctx.strokeStyle = strokeColor;
            ctx.lineWidth = 3;
            ctx.beginPath();
            ctx.roundRect(bx, by, badgeW, badgeH, badgeR);
            ctx.fill();
            ctx.stroke();

            // 图标
            this.drawEmojiIcon(prompt.icon, centerX, by + badgeH / 2, strokeColor);

            // 动作名称（显示在长方形下方，自动换行）
            ctx.font = `600 ${this.gestureLabelFontSize || 18}px ui-sans-serif, system-ui`;
            ctx.fillStyle = strokeColor;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            this.drawWrappedLabel(prompt.label, centerX, y + this.config.labelOffset, 130, (this.gestureLabelFontSize || 18) + 4);

            // 【新增】在长方形内显示状态文字
            ctx.font = '700 18px ui-sans-serif, system-ui';
            ctx.textBaseline = 'middle';
            if (prompt.isInProgress) {
                ctx.fillStyle = '#ef4444';
                ctx.fillText('动作中...', centerX, y);
            } else if (prompt.endTriggered) {
                ctx.fillStyle = this.config.colors.passed;
                ctx.fillText('已完成', centerX, y);
            } else {
                ctx.fillStyle = strokeColor;
                ctx.fillText('准备', centerX, y);
            }

            ctx.restore();
        }

        /**
         * 【新增】绘制自动换行的标签文本
         * @param {string} text - 要绘制的文本
         * @param {number} x - 中心X坐标
         * @param {number} y - 起始Y坐标
         * @param {number} maxWidth - 最大宽度（超过则换行）
         * @param {number} lineHeight - 行高
         */
        drawWrappedLabel(text, x, y, maxWidth = 100, lineHeight = 16) {
            const ctx = this.ctx;

            // 如果文本宽度没有超过最大宽度，直接绘制
            const textWidth = ctx.measureText(text).width;
            if (textWidth <= maxWidth) {
                ctx.fillText(text, x, y);
                return;
            }

            // 需要换行：尝试在中间位置分割
            const chars = text.split('');
            const midIndex = Math.ceil(chars.length / 2);
            const line1 = chars.slice(0, midIndex).join('');
            const line2 = chars.slice(midIndex).join('');

            // 绘制两行
            ctx.fillText(line1, x, y);
            ctx.fillText(line2, x, y + lineHeight);
        }

        /**
         * 计算emoji数量（用于动态调整badge宽度）
         * 使用更通用的方法来检测所有类型的emoji
         */
        countEmojis(str) {
            if (!str) return 0;

            // 使用 Intl.Segmenter 来正确分割emoji（包括组合emoji）
            // 这是最准确的方法，能正确处理变体选择符、ZWJ序列等
            if (typeof Intl !== 'undefined' && Intl.Segmenter) {
                try {
                    const segmenter = new Intl.Segmenter('en', { granularity: 'grapheme' });
                    const segments = [...segmenter.segment(str)];
                    return segments.length;
                } catch (e) {
                    // 如果Segmenter失败，使用后备方案
                }
            }

            // 后备方案：使用展开运算符分割字符
            // 这能正确处理大多数emoji，但可能对某些组合emoji不准确
            const segments = [...str];
            return segments.length;
        }

        /**
         * 计算badge宽度（根据emoji数量动态调整）
         */
        calculateBadgeWidth(icon) {
            const emojiCount = this.countEmojis(icon);
            const baseWidth = 70;
            const extraWidthPerEmoji = 40;  // 与emoji渲染宽度一致
            // 1个emoji: 70, 2个emoji: 110, 3个emoji: 150
            return emojiCount > 1 ? baseWidth + (emojiCount - 1) * extraWidthPerEmoji : baseWidth;
        }

        /**
         * 绘制emoji图标（兼容Electron，支持多emoji）
         */
        drawEmojiIcon(icon, x, y, color) {
            const ctx = this.ctx;

            // 使用更通用的emoji检测
            const isEmoji = /\p{Emoji_Presentation}|\p{Extended_Pictographic}/u.test(icon);

            ctx.save();
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';

            if (isEmoji) {
                // 计算emoji数量，动态调整canvas大小
                const emojiCount = this.countEmojis(icon);
                const singleSize = 40;
                const width = singleSize * emojiCount;
                const height = singleSize;

                // 【修复 CRITICAL-F8】缓存 emoji canvas，避免每帧创建 600+ 个临时 canvas
                if (!this._emojiCache) this._emojiCache = new Map();
                const cacheKey = `${icon}_${emojiCount}`;
                let tempCanvas = this._emojiCache.get(cacheKey);
                if (!tempCanvas) {
                    tempCanvas = document.createElement('canvas');
                    const tempCtx = tempCanvas.getContext('2d');
                    tempCanvas.width = width;
                    tempCanvas.height = height;
                    tempCtx.font = `${singleSize - 8}px "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif`;
                    tempCtx.textAlign = 'center';
                    tempCtx.textBaseline = 'middle';
                    tempCtx.fillText(icon, width / 2, height / 2);
                    this._emojiCache.set(cacheKey, tempCanvas);
                }

                // 将缓存的canvas绘制到主canvas
                ctx.drawImage(tempCanvas, x - width / 2, y - height / 2, width, height);
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

            // 乱序模式：读取当前 prompt 的 _shuffleSegment 决定颜色和标签
            let fillColor = '#22c55e';
            let phaseLabel = null;

            if (this._shuffleModeActive && total > 0 && this.executedCount < total) {
                const index = Math.min(this.executedCount, total - 1);
                const promptName = this.promptSequence[index];
                const gesture = this.promptLibrary[promptName]?.originalGesture;
                const segment = gesture?._shuffleSegment;

                if (segment === 'ordered') {
                    fillColor = '#0d9488';
                    phaseLabel = '顺序';
                } else if (segment === 'shuffled') {
                    fillColor = '#ef4444';
                    phaseLabel = '乱序';
                }
            }

            // 阶段标签（进度条左侧）
            if (phaseLabel) {
                ctx.fillStyle = fillColor;
                ctx.font = '700 16px ui-sans-serif, system-ui';
                ctx.textAlign = 'right';
                ctx.textBaseline = 'middle';
                ctx.fillText(phaseLabel, barX - 12, barY + barHeight / 2);
            }

            // 进度条背景
            ctx.fillStyle = '#e5e7eb';
            ctx.beginPath();
            ctx.roundRect(barX, barY, barWidth, barHeight, 4);
            ctx.fill();

            // 进度条填充
            ctx.fillStyle = fillColor;
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
            // 【修复 CRITICAL-F8】清除 emoji 缓存
            if (this._emojiCache) {
                this._emojiCache.clear();
            }
            window.animationPositionManager?.hideAnimationPanel();
        }

        /**
         * 重置状态（用于页面切换或任务重新开始）
         */
        reset() {
            console.log('[DiscreteGestureAnimation] 重置状态');

            // 停止动画
            this.stop();

            // 重置计数器
            this.executedCount = 0;
            this.nextPromptIndex = 0;
            this.prompts = [];

            // 重置序列和库
            this.promptSequence = [];

            // 重置当前状态
            this.currentStage = null;
            this.currentPhase = null;

            // 重置乱序模式标志
            this._shuffleModeActive = false;

            console.log('[DiscreteGestureAnimation] 状态已重置');
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

        /**
         * 【新增】乱序模式：连续滚动显示所有手势
         * 所有手势同时在屏幕上滚动，每个手势经过采集点时持续约1秒
         * 不进入休息时间，连续执行
         *
         * @param {Array} shuffledGestures - 乱序后的手势数组
         * @param {Object} executionParams - 执行参数
         * @param {Object} stageConfig - Stage配置
         * @param {Function} onComplete - 完成回调
         * @param {Function} onPromptTriggered - Prompt触发回调
         * @param {Function} onUpcomingGesture - 【新增】即将到达的手势回调（用于更新左下角示范GIF）
         */
        startShuffleMode(shuffledGestures, executionParams, stageConfig, onComplete, onPromptTriggered, onUpcomingGesture, startIndex = 0) {
            console.log('[DiscreteGestureAnimation] ★★★ 启动乱序模式 ★★★');
            console.log('[DiscreteGestureAnimation] 手势数量:', shuffledGestures.length);
            console.log('[DiscreteGestureAnimation] startIndex:', startIndex);

            // 初始化Canvas
            if (!this.canvas) {
                if (!this.init('.animation-area')) {
                    console.error('[DiscreteGestureAnimation] Canvas初始化失败');
                    if (onComplete) onComplete();
                    return;
                }
            }

            // 保存回调
            this.onComplete = onComplete;
            this.onPromptTriggered = onPromptTriggered;
            this.onUpcomingGesture = onUpcomingGesture;
            this.currentStage = stageConfig;

            // 【重构】从executionParams读取配置参数，实现：距离 = 速度 × 时间
            // scrollSpeed: 整体移动速度（px/帧），默认2
            // shuffleInterval: 手势间隔时间（秒），默认1.0
            // shuffleIntervalMin/Max: 间隔时间随机范围（秒）
            // sustainedDuration: 持续性手势的持续时间（秒），默认2.0
            // gestureLabelFontSize: 手势名称字体大小（px），默认18
            const scrollSpeed = executionParams?.scrollSpeed || 2;
            this.sustainedDuration = executionParams?.sustainedDuration || 2.0;
            this.gestureLabelFontSize = executionParams?.gestureLabelFontSize || 18;  // 【新增】

            // 【新增】保存随机范围参数，用于动态计算每个手势的间隔
            this.shuffleIntervalMin = executionParams?.shuffleIntervalMin;
            this.shuffleIntervalMax = executionParams?.shuffleIntervalMax;
            const baseShuffleInterval = executionParams?.shuffleInterval || 1.0;

            // 【核心计算】手势间距 = 滚动速度 × 间隔时间 × 帧率(60fps)
            // 注意：如果配置了随机范围，promptSpacing将在创建每个prompt时动态计算
            this.config.scrollSpeed = scrollSpeed;

            // 判断是否使用随机间隔
            const useRandomInterval = this.shuffleIntervalMin !== undefined &&
                                      this.shuffleIntervalMax !== undefined &&
                                      this.shuffleIntervalMin !== this.shuffleIntervalMax;

            if (useRandomInterval) {
                // 随机模式：使用平均值作为基准间距（用于初始布局）
                const avgInterval = (this.shuffleIntervalMin + this.shuffleIntervalMax) / 2;
                this.config.promptSpacing = scrollSpeed * avgInterval * 60;
                console.log('[DiscreteGestureAnimation] 乱序模式配置（随机间隔）:');
                console.log('  - 滚动速度:', this.config.scrollSpeed, 'px/帧');
                console.log('  - 间隔时间范围:', this.shuffleIntervalMin, '-', this.shuffleIntervalMax, '秒');
                console.log('  - 平均间距（用于初始布局）:', this.config.promptSpacing, 'px');
            } else {
                // 固定模式：使用固定间距
                const fixedInterval = (this.shuffleIntervalMin !== undefined && this.shuffleIntervalMax !== undefined)
                    ? this.shuffleIntervalMin
                    : baseShuffleInterval;
                this.config.promptSpacing = scrollSpeed * fixedInterval * 60;
                console.log('[DiscreteGestureAnimation] 乱序模式配置（固定间隔）:');
                console.log('  - 滚动速度:', this.config.scrollSpeed, 'px/帧');
                console.log('  - 间隔时间:', fixedInterval, '秒');
                console.log('  - 计算得到的手势间距:', this.config.promptSpacing, 'px');
            }
            console.log('  - 持续手势时长:', this.sustainedDuration, '秒');

            // 【修复 CRITICAL-F7】仅清除乱序模式条目(shuffle_前缀)，保留非乱序模式下添加的条目
            Object.keys(this.promptLibrary).forEach(k => {
                if (k.startsWith('shuffle_')) delete this.promptLibrary[k];
            });

            // 构建promptSequence，并将每个手势添加到promptLibrary
            // 【Bugfix】每个 prompt 保存 originalIndex（全局索引），续采时用于回调
            this.promptSequence = [];
            shuffledGestures.forEach((gesture, index) => {
                const gestureId = `shuffle_${index}_${gesture.id || gesture.name}`;

                // 确保icon不为空
                let icon = gesture.icon;
                if (!icon || icon === '' || icon === 'undefined' || icon === 'null') {
                    icon = '✋';
                }

                // 添加到promptLibrary
                this.promptLibrary[gestureId] = {
                    label: gesture.name,
                    icon: icon,
                    color: gesture.color || '#3b82f6',
                    gestureType: gesture.gestureType || 'instant',
                    originalGesture: gesture,  // 保存原始手势对象（用于GIF显示）
                    originalIndex: index        // 【Bugfix】全局索引
                };

                this.promptSequence.push(gestureId);
            });

            console.log('[DiscreteGestureAnimation] 乱序序列长度:', this.promptSequence.length);

            // 【Bugfix】续采：从 startIndex 开始，跳过已执行的手势
            // executedCount 是已通过指示线的手势数，nextPromptIndex 是已创建 prompt 的索引
            // 续采时需要把 startIndex 之前的手势视为"已执行"
            const safeStartIndex = Math.min(startIndex, this.promptSequence.length);
            this.prompts = [];
            this.nextPromptIndex = 0;
            this.executedCount = safeStartIndex;  // 跳过 startIndex 个手势
            this.isRunning = true;
            this._shuffleModeActive = true;
            this._lastUpcomingGestureIndex = -1;

            if (safeStartIndex > 0) {
                console.log('[DiscreteGestureAnimation] 续采：跳过前', safeStartIndex, '个手势');
                // 设置 nextPromptIndex，让 createInitialShufflePrompts 从正确位置开始创建
                this.nextPromptIndex = safeStartIndex;
            }

            // 调整Canvas
            this.resizeCanvas();
            this.canvas.style.display = 'block';
            window.animationPositionManager?.showAnimationPanel();

            // 创建初始的多个提示（乱序模式下同时显示多个）
            this.createInitialShufflePrompts();

            // 开始动画循环
            this.animateShuffle();
        }

        /**
         * 【新增】创建乱序模式的初始提示（同时显示多个）
         * 【修复】正确计算持续性手势的位置：下一个手势从上一个手势的右边缘 + 间隔开始
         * 【新增】支持随机间隔：每个手势的间隔在 [min, max] 范围内随机
         */
        createInitialShufflePrompts() {
            // 计算屏幕上能显示多少个手势（估算）
            const visibleCount = Math.ceil(this.config.canvasWidth / this.config.promptSpacing) + 4;

            // 【Bugfix】从 nextPromptIndex 开始创建（续采模式下为 startIndex）
            const startIdx = this.nextPromptIndex;
            const remaining = this.promptSequence.length - startIdx;
            const createCount = Math.min(visibleCount, remaining);

            // 【修复】累积计算位置，考虑持续性手势的宽度和随机间隔
            let currentX = this.config.canvasWidth + 50;

            for (let i = 0; i < createCount; i++) {
                const seqIdx = startIdx + i;
                const promptName = this.promptSequence[seqIdx];
                const gestureConfig = this.promptLibrary[promptName]?.originalGesture;
                // 使用全局索引 seqIdx，保持与 gestures 数组的对应关系
                const prompt = this.createPromptObject(promptName, currentX, seqIdx, gestureConfig);
                this.prompts.push(prompt);
                this.nextPromptIndex = seqIdx + 1;

                // 【新增】计算随机间隔距离
                let spacing = this.config.promptSpacing;  // 默认使用基准间距
                if (this.shuffleIntervalMin !== undefined &&
                    this.shuffleIntervalMax !== undefined &&
                    this.shuffleIntervalMin !== this.shuffleIntervalMax) {
                    // 在 [min, max] 范围内随机选择间隔时间（秒）
                    const randomInterval = this.shuffleIntervalMin +
                        Math.random() * (this.shuffleIntervalMax - this.shuffleIntervalMin);
                    // 转换为像素距离
                    spacing = this.config.scrollSpeed * randomInterval * 60;
                    console.log(`[DiscreteGestureAnimation] 手势 ${seqIdx} 随机间隔: ${randomInterval.toFixed(2)}秒 = ${spacing.toFixed(0)}px`);
                }

                // 【关键】下一个手势的起始位置 = 当前手势的右边缘 + 随机间隔
                currentX = currentX + (prompt.rectWidth || 0) + spacing;
            }

            console.log('[DiscreteGestureAnimation] 初始创建', this.prompts.length, '个提示 (从索引', startIdx, '开始)');
        }

        /**
         * 【新增】乱序模式的动画循环
         */
        animateShuffle() {
            if (!this.isRunning) return;

            // 检查是否所有prompt都已经通过
            if (this.executedCount >= this.promptSequence.length) {
                const allGone = this.prompts.every(p => p.x < -150);
                if (allGone || this.prompts.length === 0) {
                    this.stopShuffleMode();
                    if (this.onComplete) this.onComplete();
                    return;
                }
            }

            if (!this.config.canvasWidth || !this.config.canvasHeight) {
                if (!this.resizeCanvas()) {
                    this.animationId = requestAnimationFrame(() => this.animateShuffle());
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
            this.prompts = this.prompts.filter(p => {
                const rightEdge = p.x + (p.rectWidth || 0);
                return rightEdge > -50;
            });

            // 创建新提示（如果需要）
            // 【修复】新手势的位置基于上一个手势的右边缘 + 随机间隔来计算
            if (this.nextPromptIndex < this.promptSequence.length) {
                const last = this.prompts[this.prompts.length - 1];
                const lastRightEdge = last ? (last.x + (last.rectWidth || 0)) : 0;
                // 当上一个手势的右边缘即将进入画布时，创建新手势
                if (!last || lastRightEdge < this.config.canvasWidth + 50) {
                    const promptName = this.promptSequence[this.nextPromptIndex];

                    // 【新增】计算随机间隔距离
                    let spacing = this.config.promptSpacing;  // 默认使用基准间距
                    if (this.shuffleIntervalMin !== undefined &&
                        this.shuffleIntervalMax !== undefined &&
                        this.shuffleIntervalMin !== this.shuffleIntervalMax) {
                        // 在 [min, max] 范围内随机选择间隔时间（秒）
                        const randomInterval = this.shuffleIntervalMin +
                            Math.random() * (this.shuffleIntervalMax - this.shuffleIntervalMin);
                        // 转换为像素距离
                        spacing = this.config.scrollSpeed * randomInterval * 60;
                        console.log(`[DiscreteGestureAnimation] 手势 ${this.nextPromptIndex} 随机间隔: ${randomInterval.toFixed(2)}秒 = ${spacing.toFixed(0)}px`);
                    }

                    // 【关键修复】新手势的起始位置 = 上一个手势的右边缘 + 随机间隔
                    const startX = last ? (lastRightEdge + spacing) : (this.config.canvasWidth + 50);
                    const gestureConfig = this.promptLibrary[promptName]?.originalGesture;
                    const prompt = this.createPromptObject(promptName, startX, this.nextPromptIndex, gestureConfig);
                    this.prompts.push(prompt);
                    this.nextPromptIndex++;
                }
            }

            // 更新提示状态
            this.updatePrompts();

            // 【新增】检测即将到达的手势，通知更新GIF
            this.checkUpcomingGesture();

            // 绘制
            this.drawShuffleStageInfo();
            this.drawIndicator();
            this.prompts.forEach(p => this.drawPrompt(p));
            this.drawProgress();

            // 继续下一帧
            this.animationId = requestAnimationFrame(() => this.animateShuffle());
        }

        /**
         * 【新增】检测即将到达指示线的手势，并通知回调
         */
        checkUpcomingGesture() {
            if (!this.onUpcomingGesture) return;

            // 找到即将到达指示线的下一个手势（还没通过的，且最接近指示线的）
            let upcomingPrompt = null;
            let minDistance = Infinity;

            this.prompts.forEach(p => {
                // 【修改】对于持续性手势，需要等 endTriggered 后才算完成
                // 对于瞬时手势，使用 isPassed
                const isCompleted = p.gestureType === 'sustained' ? p.endTriggered : p.isPassed;

                if (!isCompleted) {
                    // 【修改】对于持续性手势，使用右边缘位置来计算距离
                    const promptRightEdge = p.gestureType === 'sustained' ? (p.x + p.rectWidth) : p.x;
                    const distance = promptRightEdge - this.config.indicatorX;
                    // 只考虑还在指示线右边的手势
                    if (distance > -50 && distance < minDistance) {
                        minDistance = distance;
                        upcomingPrompt = p;
                    }
                }
            });

            if (upcomingPrompt && upcomingPrompt.index !== this._lastUpcomingGestureIndex) {
                this._lastUpcomingGestureIndex = upcomingPrompt.index;
                const gestureDef = this.promptLibrary[upcomingPrompt.name];
                if (gestureDef && gestureDef.originalGesture) {
                    console.log('[DiscreteGestureAnimation] 即将到达的手势:', gestureDef.originalGesture.name);
                    this.onUpcomingGesture(gestureDef.originalGesture);
                }
            }
        }

        /**
         * 【新增】绘制乱序模式的Stage信息（简化版，不显示具体姿势）
         */
        drawShuffleStageInfo() {
            const ctx = this.ctx;
            const stageConfig = this.currentStage;

            ctx.save();

            // 左上角显示乱序采集模式
            ctx.fillStyle = 'rgba(30, 64, 175, 0.95)';
            ctx.font = '700 24px ui-sans-serif, system-ui, -apple-system';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'top';
            ctx.fillText(`🎲 乱序采集模式`, 20, 20);

            // 显示Stage名称和指导文字
            if (stageConfig) {
                ctx.fillStyle = 'rgba(107, 114, 128, 0.9)';
                ctx.font = '500 16px ui-sans-serif, system-ui';
                ctx.fillText(`Stage: ${stageConfig.name || stageConfig.label}`, 20, 52);

                if (stageConfig.instruction) {
                    ctx.fillText(stageConfig.instruction, 20, 76);
                }
            }

            ctx.restore();
        }

        /**
         * 【新增】停止乱序模式动画
         */
        stopShuffleMode() {
            console.log('[DiscreteGestureAnimation] 停止乱序模式');
            this.isRunning = false;
            this._shuffleModeActive = false;

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
            window.animationPositionManager?.hideAnimationPanel();
        }
    }

    // 创建全局实例
    const discreteGestureAnimation = new DiscreteGestureAnimationController();
    window.discreteGestureAnimation = discreteGestureAnimation;
    
    console.log('[DiscreteGestureAnimation] 离散手势动画模块加载完成');

})();