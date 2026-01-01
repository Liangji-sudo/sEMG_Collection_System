/**
 * collection-controller.js - 采集任务控制器（重构版）
 * 
 * 职责：
 * 1. 采集任务类型切换
 * 2. Stage列表显示和管理
 * 3. 采集流程控制（开场动画 → 倒计时 → stage动画 → 倒计时 → ...）
 * 4. 通过WebSocket与realtimeEngine.js通信
 * 
 * 依赖：
 * - task-config.js (任务定义)
 * - collection-constants.js (常量配置)
 * - animation-controller.js (动画控制)
 */

console.log('[Collection] ====== 脚本开始加载 ======');

(function() {
    'use strict';

    class CollectionController {
        constructor() {
            console.log('[Collection] 构造函数开始');
            this.currentTaskId = 'discrete_gesture';
            this._isRunning = false;
            this._isPaused = false;
            this.currentStageIndex = 0;
            this.stageTimer = null;
            this.currentPhase = null; // 'intro' | 'prepare' | 'stage' | 'complete'
            console.log('[Collection] 构造函数结束');
        }

        init() {
            console.log('[Collection] init() 开始');
            
            try {
                // 检查依赖
                this.checkDependencies();
                
                this.bindEvents();
                this.updateStageList();
                this.updateControlButtons(false);
                console.log('[Collection] init() 完成 ✓');
            } catch (error) {
                console.error('[Collection] init() 错误:', error);
            }
        }

        /**
         * 检查依赖是否加载
         */
        checkDependencies() {
            if (!window.TaskConfig) {
                console.warn('[Collection] 警告: TaskConfig 未加载，使用内置配置');
            }
            if (!window.COLLECTION_CONSTANTS) {
                console.warn('[Collection] 警告: COLLECTION_CONSTANTS 未加载，使用默认值');
            }
            if (!window.animationController) {
                console.warn('[Collection] 警告: animationController 未加载');
            }
        }

        bindEvents() {
            console.log('[Collection] bindEvents() 开始');
            
            // 任务切换按钮
            const taskBtns = document.querySelectorAll('.task-btn');
            console.log('[Collection] 找到 .task-btn 按钮数量:', taskBtns.length);
            
            taskBtns.forEach((btn, index) => {
                console.log(`[Collection] 绑定任务按钮 ${index}: data-task="${btn.dataset.task}"`);
                btn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const htmlTaskId = btn.dataset.task;
                    console.log('[Collection] ★ 任务按钮被点击:', htmlTaskId);
                    this.selectTask(htmlTaskId);
                });
            });

            // 开始按钮
            const startBtn = document.getElementById('startTaskBtn');
            if (startBtn) {
                startBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    console.log('[Collection] ★★★ 开始按钮被点击 ★★★');
                    this.startTask();
                });
            }

            // 暂停按钮
            const pauseBtn = document.getElementById('pauseTaskBtn');
            if (pauseBtn) {
                pauseBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    console.log('[Collection] ★ 暂停按钮被点击');
                    this.togglePause();
                });
            }

            // 停止按钮
            const stopBtn = document.getElementById('stopTaskBtn');
            if (stopBtn) {
                stopBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    console.log('[Collection] ★ 停止按钮被点击');
                    this.stopTask();
                });
            }

            console.log('[Collection] bindEvents() 完成 ✓');
        }

        onPageShow() {
            console.log('[Collection] 页面显示');
            this.updateStageList();
            this.resetDisplay();
        }

        // ==================== 任务选择 ====================

        selectTask(htmlTaskId) {
            if (this._isRunning) {
                console.log('[Collection] 采集中，无法切换任务');
                return;
            }

            // 使用TaskConfig映射
            const taskIdMap = window.TaskConfig ? window.TaskConfig.ID_MAP : {
                'discrete': 'discrete_gesture',
                'continuous1': 'continual_gesture_1',
                'continuous2': 'continual_gesture_2'
            };
            
            const taskId = taskIdMap[htmlTaskId] || htmlTaskId;
            
            if (!this.getTaskConfig(taskId)) {
                console.error('[Collection] 未知任务类型:', taskId);
                return;
            }

            console.log('[Collection] 切换任务:', taskId);
            this.currentTaskId = taskId;
            this.currentStageIndex = 0;

            document.querySelectorAll('.task-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.task === htmlTaskId);
            });

            this.updateStageList();
            this.resetDisplay();
            this.sendToRealtimeEngine('task_change', { taskId: taskId });
        }

        /**
         * 获取任务配置（优先使用外部配置）
         */
        getTaskConfig(taskId) {
            if (window.TaskConfig && window.TaskConfig.DEFINITIONS[taskId]) {
                return window.TaskConfig.DEFINITIONS[taskId];
            }
            // 内置后备配置
            return this.getBuiltinTaskConfig(taskId);
        }

        /**
         * 内置后备配置
         */
        getBuiltinTaskConfig(taskId) {
            const builtinConfigs = {
                discrete_gesture: {
                    id: 'discrete_gesture',
                    name: '离散手势',
                    stages: [
                        { name: 'palm_up', label: '手心向上' },
                        { name: 'palm_inward', label: '手心向内' },
                        { name: 'hand_on_knee', label: '手放膝盖' },
                        { name: 'hand_on_desk', label: '手放桌上' }
                    ]
                }
            };
            return builtinConfigs[taskId] || null;
        }

        getCurrentTaskConfig() {
            return this.getTaskConfig(this.currentTaskId);
        }

        getCurrentTaskId() {
            return this.currentTaskId;
        }

        // ==================== 采集流程控制 ====================

        /**
         * 开始采集任务
         * 流程: 开场动画 → 准备倒计时 → Stage1动画 → 准备倒计时 → Stage2动画 → ... → 完成
         */
        startTask() {
            console.log('[Collection] startTask() 被调用');
            
            if (this._isRunning) {
                console.log('[Collection] 已在运行中，忽略');
                return;
            }

            console.log('[Collection] 开始采集任务:', this.currentTaskId);

            this._isRunning = true;
            this._isPaused = false;
            this.currentStageIndex = 0;
            this.currentPhase = 'intro';

            this.updateControlButtons(true);
            this.updateStatus('准备中');

            // 获取用户信息
            const user = window.pageSwitchController ? 
                window.pageSwitchController.getCurrentUser() : null;

            // 发送采集开始命令
            this.sendToRealtimeEngine('collection_start', { 
                taskId: this.currentTaskId, 
                user: user 
            });

            // 播放开场动画
            this.playIntroAnimation();
        }

        /**
         * 播放开场动画
         */
        playIntroAnimation() {
            console.log('[Collection] 播放开场动画');
            this.currentPhase = 'intro';
            this.updateStatus('开场准备');

            if (window.animationController) {
                window.animationController.playIntroAnimation(() => {
                    this.onIntroComplete();
                });
            } else {
                // 没有动画控制器，直接开始
                const duration = window.CollectionTiming ? 
                    window.CollectionTiming.getIntroDuration() : 10000;
                setTimeout(() => this.onIntroComplete(), duration);
            }
        }

        /**
         * 开场动画完成
         */
        onIntroComplete() {
            console.log('[Collection] 开场动画完成');
            
            if (!this._isRunning) return;
            
            // 进入准备倒计时，然后开始第一个Stage
            this.playPrepareCountdown(() => {
                this.startStage();
            });
        }

        /**
         * 播放准备倒计时
         */
        playPrepareCountdown(onComplete) {
            console.log('[Collection] 播放准备倒计时');
            this.currentPhase = 'prepare';
            this.updateStatus('准备中');

            const seconds = window.CollectionTiming ? 
                window.CollectionTiming.getPrepareCountdown() : 3;

            if (window.animationController) {
                window.animationController.playCountdown(seconds, onComplete);
            } else {
                // 简单倒计时
                this.simpleCountdown(seconds, onComplete);
            }
        }

        /**
         * 简单倒计时（后备方案）
         */
        simpleCountdown(seconds, callback) {
            const countdown = document.getElementById('countdown');
            let count = seconds;

            const tick = () => {
                if (countdown) {
                    countdown.textContent = count;
                    countdown.classList.add('visible');
                }

                if (count <= 0) {
                    if (countdown) countdown.classList.remove('visible');
                    callback();
                } else {
                    count--;
                    setTimeout(tick, 1000);
                }
            };

            tick();
        }

        /**
         * 开始当前Stage
         */
        startStage() {
            if (!this._isRunning) return;

            const config = this.getCurrentTaskConfig();
            const stage = config.stages[this.currentStageIndex];

            if (!stage) {
                this.onAllStagesComplete();
                return;
            }

            console.log(`[Collection] 开始Stage: ${stage.name} (${this.currentStageIndex + 1}/${config.stages.length})`);
            this.currentPhase = 'stage';
            this.updateStatus('采集中');

            // 发送stage开始命令
            this.sendToRealtimeEngine('stage_start', {
                stageName: stage.name,
                stageIndex: this.currentStageIndex,
                timestamp: Date.now() / 1000
            });

            this.updateStageList();
            this.updateProgress();

            // 播放Stage内容动画
            this.playStageAnimation(stage);
        }

        /**
         * 播放Stage内容动画
         */
        playStageAnimation(stage) {
            console.log('[Collection] 播放Stage动画:', stage.name);

            if (window.animationController) {
                window.animationController.playStageAnimation(stage, () => {
                    this.onStageComplete();
                });
            } else {
                // 没有动画控制器，使用定时器
                const duration = window.CollectionTiming ? 
                    window.CollectionTiming.getStageDuration() : 5000;
                
                this.updateStageDisplay(stage);
                this.stageTimer = setTimeout(() => {
                    this.onStageComplete();
                }, duration);
            }
        }

        /**
         * Stage完成
         */
        onStageComplete() {
            if (!this._isRunning) return;

            const config = this.getCurrentTaskConfig();
            const stage = config.stages[this.currentStageIndex];

            if (stage) {
                console.log(`[Collection] Stage完成: ${stage.name}`);
                this.sendToRealtimeEngine('stage_end', {
                    stageName: stage.name,
                    stageIndex: this.currentStageIndex,
                    timestamp: Date.now() / 1000
                });
            }

            this.currentStageIndex++;

            if (this.currentStageIndex < config.stages.length) {
                // 还有更多Stage，播放准备倒计时
                this.playPrepareCountdown(() => {
                    this.startStage();
                });
            } else {
                // 所有Stage完成
                this.onAllStagesComplete();
            }
        }

        /**
         * 所有Stage完成
         */
        onAllStagesComplete() {
            console.log('[Collection] 所有Stage完成');

            this._isRunning = false;
            this._isPaused = false;
            this.currentPhase = 'complete';

            this.updateControlButtons(false);
            this.updateStatus('采集完成');
            this.updateStageList();

            const progressFill = document.getElementById('progressFill');
            if (progressFill) progressFill.style.width = '100%';

            const gestureName = document.getElementById('gestureName');
            const gestureInstruction = document.getElementById('gestureInstruction');
            if (gestureName) gestureName.textContent = '采集完成！';
            if (gestureInstruction) gestureInstruction.textContent = '所有Stage已完成';

            // 停止动画
            if (window.animationController) {
                window.animationController.stop();
            }

            this.sendToRealtimeEngine('collection_stop', { completed: true });

            if (window.pageSwitchController) {
                window.pageSwitchController.showToast('采集任务完成！');
            }
        }

        /**
         * 停止采集
         */
        stopTask() {
            console.log('[Collection] stopTask() 被调用');
            
            if (!this._isRunning) {
                console.log('[Collection] 未在运行，忽略');
                return;
            }

            console.log('[Collection] 停止采集');

            // 停止定时器
            if (this.stageTimer) {
                clearTimeout(this.stageTimer);
                this.stageTimer = null;
            }

            // 停止动画
            if (window.animationController) {
                window.animationController.stop();
            }

            // 发送当前stage结束命令
            const config = this.getCurrentTaskConfig();
            if (config && config.stages[this.currentStageIndex]) {
                this.sendToRealtimeEngine('stage_end', {
                    stageName: config.stages[this.currentStageIndex].name,
                    stageIndex: this.currentStageIndex,
                    timestamp: Date.now() / 1000
                });
            }

            this._isRunning = false;
            this._isPaused = false;
            this.currentPhase = null;

            this.updateControlButtons(false);
            this.resetDisplay();
            this.sendToRealtimeEngine('collection_stop', { completed: false });
        }

        /**
         * 暂停/继续
         */
        togglePause() {
            console.log('[Collection] togglePause() 被调用');
            
            if (!this._isRunning) return;

            this._isPaused = !this._isPaused;
            console.log('[Collection] 暂停状态:', this._isPaused);

            const pauseBtn = document.getElementById('pauseTaskBtn');
            if (pauseBtn) {
                pauseBtn.innerHTML = this._isPaused ? 
                    '<i class="fas fa-play"></i> 继续' : 
                    '<i class="fas fa-pause"></i> 暂停';
            }

            this.updateStatus(this._isPaused ? '已暂停' : '采集中');

            if (this._isPaused) {
                this.sendToRealtimeEngine('collection_pause', {});
            } else {
                this.sendToRealtimeEngine('collection_resume', {});
            }
        }

        isRunning() {
            return this._isRunning;
        }

        isPaused() {
            return this._isPaused;
        }

        // ==================== WebSocket通信 ====================

        sendToRealtimeEngine(action, data) {
            console.log(`[Collection] >>> realtimeEngine: ${action}`, data);
            
            const ws = this.getWebSocket();
            
            if (ws && ws.readyState === WebSocket.OPEN) {
                const message = JSON.stringify({
                    type: 'control_command',
                    action: action,
                    data: data,
                    timestamp: Date.now()
                });
                ws.send(message);
                console.log(`[Collection] 命令已发送: ${action}`);
            } else {
                console.log(`[Collection] WebSocket未连接，命令未发送: ${action}`);
            }
        }

        getWebSocket() {
            if (window.waveformController && 
                window.waveformController.dataReceiver && 
                window.waveformController.dataReceiver.ws) {
                return window.waveformController.dataReceiver.ws;
            }
            return null;
        }

        // ==================== UI更新 ====================

        updateStageList() {
            const config = this.getCurrentTaskConfig();
            const gestureList = document.getElementById('gestureList');
            
            if (!gestureList || !config) return;

            let html = `<div class="gesture-list-title">${config.name} - Stage列表</div>`;
            
            config.stages.forEach((stage, index) => {
                let status = 'pending';
                if (index < this.currentStageIndex) {
                    status = 'completed';
                } else if (index === this.currentStageIndex && this._isRunning && this.currentPhase === 'stage') {
                    status = 'current';
                }
                
                const iconClass = status === 'completed' ? 'check-circle' : 'circle';
                
                html += `
                    <div class="gesture-item ${status}" data-index="${index}">
                        <i class="fas fa-${iconClass}"></i>
                        <span>${stage.label}</span>
                    </div>
                `;
            });
            
            gestureList.innerHTML = html;
        }

        updateControlButtons(running) {
            const startBtn = document.getElementById('startTaskBtn');
            const pauseBtn = document.getElementById('pauseTaskBtn');
            const stopBtn = document.getElementById('stopTaskBtn');

            if (startBtn) startBtn.disabled = running;
            if (pauseBtn) pauseBtn.disabled = !running;
            if (stopBtn) stopBtn.disabled = !running;

            if (pauseBtn) {
                pauseBtn.innerHTML = '<i class="fas fa-pause"></i> 暂停';
            }
        }

        updateStatus(text) {
            const statusText = document.getElementById('statusText');
            const statusDot = document.getElementById('statusDot');

            if (statusText) statusText.textContent = text;
            if (statusDot) {
                let dotClass = 'idle';
                if (this._isRunning) {
                    dotClass = this._isPaused ? 'idle' : 'recording';
                }
                statusDot.className = 'status-dot ' + dotClass;
            }
        }

        updateStageDisplay(stage) {
            const gestureName = document.getElementById('gestureName');
            const gestureInstruction = document.getElementById('gestureInstruction');
            const gestureIcon = document.getElementById('gestureIcon');

            if (gestureIcon && gestureIcon.parentElement) {
                gestureIcon.parentElement.style.display = 'none';
            }

            if (gestureName) {
                gestureName.textContent = stage.label || stage.name;
            }

            if (gestureInstruction) {
                gestureInstruction.textContent = stage.instruction || '请按照提示进行手势动作';
            }
        }

        updateProgress() {
            const config = this.getCurrentTaskConfig();
            if (!config) return;

            const totalStages = config.stages.length;
            const percent = (this.currentStageIndex / totalStages) * 100;

            const progressFill = document.getElementById('progressFill');
            const progressText = document.getElementById('progressText');

            if (progressFill) progressFill.style.width = `${percent}%`;
            if (progressText) {
                progressText.textContent = `${this.currentStageIndex} / ${totalStages} Stage`;
            }
        }

        resetDisplay() {
            const gestureName = document.getElementById('gestureName');
            const gestureInstruction = document.getElementById('gestureInstruction');
            const gestureIcon = document.getElementById('gestureIcon');
            const progressFill = document.getElementById('progressFill');
            const progressText = document.getElementById('progressText');

            if (gestureName) {
                gestureName.textContent = '点击开始';
            }

            if (gestureInstruction) {
                gestureInstruction.textContent = '选择任务类型并点击开始按钮';
            }

            if (gestureIcon && gestureIcon.parentElement) {
                gestureIcon.parentElement.style.display = '';
            }

            if (progressFill) progressFill.style.width = '0%';
            if (progressText) progressText.textContent = '0 / 0 完成';

            // 重置动画控制器
            if (window.animationController) {
                window.animationController.reset();
            }

            this.updateStatus('准备就绪');
            this.updateStageList();
        }
    }

    // ==================== 初始化 ====================
    console.log('[Collection] 准备初始化控制器');
    
    function initController() {
        console.log('[Collection] ====== 开始初始化控制器 ======');
        
        try {
            const controller = new CollectionController();
            window.collectionController = controller;
            console.log('[Collection] 控制器已创建并挂载到 window.collectionController');
            
            // 延迟100ms后执行init，确保所有DOM元素都已渲染
            setTimeout(() => {
                console.log('[Collection] 延迟初始化开始...');
                controller.init();
            }, 100);
            
        } catch (error) {
            console.error('[Collection] 初始化失败:', error);
        }
    }

    // 检查DOM状态
    if (document.readyState === 'loading') {
        console.log('[Collection] DOM还在加载中，等待DOMContentLoaded');
        document.addEventListener('DOMContentLoaded', initController);
    } else {
        console.log('[Collection] DOM已加载，直接初始化');
        initController();
    }

    console.log('[Collection] 脚本主体执行完毕');

})();

console.log('[Collection] ====== 脚本加载结束 ======');
