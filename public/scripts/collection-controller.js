/**
 * collection-controller.js - 采集任务控制器（v3 - 新采集流程）
 * 
 * 新流程说明：
 * 1. 进入采集界面后，根据选择的配置显示当前任务类型
 * 2. 上方有Stage切换下拉菜单（对应配置中的category3/子场景）
 * 3. 左侧显示手势库列表（而不是Stage列表）
 * 4. 离散手势采集：顺序采集手势库中的每个手势，每个手势重复N次
 * 5. 连续手势采集：保留原有的animation流程
 * 
 * 采集逻辑：
 * - 离散手势：手势1(5次) → 休息30s → 手势2(5次) → 休息30s → ... → 所有手势完成
 * - 完成当前Stage后，可以切换到下一个Stage继续采集
 * 
 * 数据存储目录结构：
 * storage/
 * └── {task}/{category1}/{category2}/{category4}/
 *     └── {user_id}_{date}_{time}.h5
 */

console.log('[Collection] ====== 脚本开始加载 (v3) ======');

(function() {
    'use strict';

    class CollectionController {
        constructor() {
            console.log('[Collection] 构造函数开始');
            
            // 当前采集配置（从collectionSelector获取）
            this.collectionConfig = null;
            
            // 任务类型
            this.currentTaskId = 'discrete_gesture';
            
            // Stage相关（来自配置的category3）
            this.stages = [];
            this.currentStageIndex = 0;
            
            // 手势库（来自配置）
            this.gestures = [];
            this.currentGestureIndex = 0;
            this.gestureRepeatCount = 0;  // 当前手势已重复次数
            
            // 执行参数 - 按任务类型存储
            this.executionParams = {
                // 离散手势默认参数
                discrete_gesture: {
                    repeatPerGesture: 5,
                    intervalBetweenRepeat: 1.0,
                    restBetweenGestures: 30.0,
                    preparationTime: 3.0,
                    gestureDisplayTime: 2.0
                },
                // 连续手势1默认参数
                continual_gesture_1: {
                    trialsPerStage: 10,
                    stageTimeout: 120,
                    dwellTime: 0.5,
                    preparationTime: 3.0,
                    targetSize: 0.12
                },
                // 连续手势2默认参数
                continual_gesture_2: {
                    trialsPerStage: 10,
                    stageTimeout: 120,
                    dwellTime: 0.5,
                    preparationTime: 3.0,
                    targetSize: 0.12
                }
            };
            
            // 当前任务的执行参数（便捷访问）
            this.currentExecutionParams = this.executionParams.discrete_gesture;
            
            // 状态
            this._isRunning = false;
            this._isPaused = false;
            this.currentPhase = null; // 'intro' | 'prepare' | 'gesture' | 'rest' | 'complete' | 'continual'
            
            // 连续手势相关
            this.continualTrialCount = 0;  // 当前Stage的试次完成数
            this.continualProgressTimer = null;  // 进度更新定时器
            
            // 定时器
            this.phaseTimer = null;
            this.countdownTimer = null;
            
            console.log('[Collection] 构造函数结束');
        }

        init() {
            console.log('[Collection] init() 开始');
            
            try {
                this.bindEvents();
                this.loadCollectionConfig();
                this.updateUI();
                console.log('[Collection] init() 完成 ✓');
            } catch (error) {
                console.error('[Collection] init() 错误:', error);
            }
        }

        bindEvents() {
            console.log('[Collection] bindEvents() 开始');
            
            // Stage切换下拉菜单
            const stageSelect = document.getElementById('stageSwitchSelect');
            if (stageSelect) {
                stageSelect.addEventListener('change', (e) => {
                    if (!this._isRunning) {
                        this.switchStage(parseInt(e.target.value));
                    } else {
                        // 采集中不允许切换，恢复原值
                        e.target.value = this.currentStageIndex;
                        this.showToast('采集进行中，无法切换Stage', 'warning');
                    }
                });
            }

            // 开始按钮
            const startBtn = document.getElementById('startTaskBtn');
            if (startBtn) {
                startBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    console.log('[Collection] ★★★ 开始按钮被点击 ★★★');
                    this.startTask();
                });
            }

            // 暂停按钮
            const pauseBtn = document.getElementById('pauseTaskBtn');
            if (pauseBtn) {
                pauseBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    this.togglePause();
                });
            }

            // 停止按钮
            const stopBtn = document.getElementById('stopTaskBtn');
            if (stopBtn) {
                stopBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    this.stopTask();
                });
            }

            // 下一个Stage按钮
            const nextStageBtn = document.getElementById('nextStageBtn');
            if (nextStageBtn) {
                nextStageBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    this.goToNextStage();
                });
            }

            console.log('[Collection] bindEvents() 完成 ✓');
        }

        // ==================== 配置加载 ====================

        loadCollectionConfig() {
            // 从全局变量或localStorage加载配置
            this.collectionConfig = window.currentCollectionConfig || 
                JSON.parse(localStorage.getItem('emg_current_collection_config') || 'null');
            
            if (this.collectionConfig) {
                console.log('[Collection] 加载采集配置:', this.collectionConfig);
                
                // 设置任务类型
                this.currentTaskId = this.collectionConfig.task_id || this.collectionConfig.task || 'discrete_gesture';
                
                // 加载模板配置
                const template = this.getTemplate();
                
                // 加载Stage列表（category3 - 从配置中获取）
                if (this.collectionConfig.category3List && this.collectionConfig.category3List.length > 0) {
                    this.stages = this.collectionConfig.category3List;
                } else {
                    this.stages = (template.category3 || []).filter(s => s.enabled);
                }
                
                // 加载手势库
                this.gestures = (template.gestures?.discrete || []).filter(g => g.enabled);
                
                // 加载执行参数 - 按任务类型读取
                if (template.execution) {
                    // 检查是否为新版本格式（按任务类型分类）
                    if (template.execution[this.currentTaskId]) {
                        // 新版本格式
                        this.executionParams = template.execution;
                        this.currentExecutionParams = template.execution[this.currentTaskId];
                        console.log('[Collection] 加载执行参数(新格式):', this.currentTaskId, this.currentExecutionParams);
                    } else if (template.execution.repeatPerGesture !== undefined) {
                        // 旧版本格式 - 兼容处理
                        console.log('[Collection] 检测到旧版本执行参数格式，进行兼容处理');
                        this.executionParams.discrete_gesture = { ...template.execution };
                        this.currentExecutionParams = this.executionParams[this.currentTaskId] || this.executionParams.discrete_gesture;
                    }
                }
                
                console.log('[Collection] Stages:', this.stages.length, '个');
                console.log('[Collection] Gestures:', this.gestures.length, '个');
                console.log('[Collection] 当前任务执行参数:', this.currentExecutionParams);
                console.log('[Collection] 目录结构:', 
                    `${this.collectionConfig.task}/${this.collectionConfig.category1}/${this.collectionConfig.category2}/${this.collectionConfig.category4}/`);
            } else {
                console.warn('[Collection] 未找到采集配置，使用默认值');
                this.loadDefaultConfig();
            }
        }

        getTemplate() {
            if (window.templateConfigManager?.currentTemplate) {
                return window.templateConfigManager.currentTemplate;
            }
            const saved = localStorage.getItem('emg_collection_template');
            if (saved) {
                try {
                    return JSON.parse(saved);
                } catch (e) {}
            }
            return this.getDefaultTemplate();
        }

        getDefaultTemplate() {
            return {
                category3: [
                    { id: 'palm_up', name: '手心朝上', enabled: true },
                    { id: 'palm_inward', name: '手心朝内', enabled: true },
                    { id: 'hand_on_knee', name: '手放膝盖', enabled: true },
                    { id: 'hand_on_desk', name: '手放桌上', enabled: true }
                ],
                gestures: {
                    discrete: [
                        { id: 'pinch', name: '捏合', icon: '🤏', enabled: true },
                        { id: 'spread', name: '张开', icon: '🖐️', enabled: true },
                        { id: 'fist', name: '握拳', icon: '✊', enabled: true },
                        { id: 'release', name: '松开', icon: '✋', enabled: true }
                    ]
                },
                execution: {
                    discrete_gesture: {
                        repeatPerGesture: 5,
                        intervalBetweenRepeat: 1.0,
                        restBetweenGestures: 30.0,
                        preparationTime: 3.0,
                        gestureDisplayTime: 2.0
                    },
                    continual_gesture_1: {
                        trialsPerStage: 10,
                        stageTimeout: 120,
                        dwellTime: 0.5,
                        preparationTime: 3.0,
                        targetSize: 0.12
                    },
                    continual_gesture_2: {
                        trialsPerStage: 10,
                        stageTimeout: 120,
                        dwellTime: 0.5,
                        preparationTime: 3.0,
                        targetSize: 0.12
                    }
                }
            };
        }

        loadDefaultConfig() {
            const template = this.getDefaultTemplate();
            this.stages = template.category3;
            this.gestures = template.gestures.discrete;
            this.executionParams = template.execution;
            this.currentExecutionParams = this.executionParams[this.currentTaskId] || this.executionParams.discrete_gesture;
        }

        // ==================== 页面显示 ====================

        onPageShow() {
            console.log('[Collection] 页面显示');
            this.loadCollectionConfig();
            this.updateUI();
        }

        updateUI() {
            this.updateTaskHeader();
            this.updateStageSelect();
            this.updateGestureList();
            this.updateControlButtons(false);
            this.updateNextStageButton();
            this.resetDisplay();
        }

        updateTaskHeader() {
            const taskNameEl = document.getElementById('currentTaskName');
            const configBadgeEl = document.getElementById('taskConfigBadge');
            
            // 任务名称
            const taskConfig = this.getTaskConfig();
            if (taskNameEl) {
                taskNameEl.textContent = taskConfig.name || this.currentTaskId;
            }
            
            // 配置信息徽章
            if (configBadgeEl && this.collectionConfig) {
                const cat1Name = this.getCategoryName('category1', this.collectionConfig.category1);
                const cat2Name = this.getCategoryName('category2', this.collectionConfig.category2);
                const cat4Name = this.getCategoryName('category4', this.collectionConfig.category4);
                
                configBadgeEl.innerHTML = `
                    <span class="badge badge-primary">${cat1Name}</span>
                    <span class="badge badge-info">${cat2Name}</span>
                    <span class="badge badge-secondary">${cat4Name}</span>
                `;
                configBadgeEl.style.display = 'flex';
            }
        }

        getCategoryName(category, id) {
            const template = this.getTemplate();
            const item = template[category]?.find(c => c.id === id);
            return item?.name || id || '未知';
        }

        updateStageSelect() {
            const select = document.getElementById('stageSwitchSelect');
            if (!select) return;

            select.innerHTML = '';
            this.stages.forEach((stage, index) => {
                const option = document.createElement('option');
                option.value = index;
                option.textContent = `${index + 1}. ${stage.name}`;
                if (index === this.currentStageIndex) {
                    option.selected = true;
                }
                select.appendChild(option);
            });

            // 显示Stage信息
            const stageInfoEl = document.getElementById('currentStageInfo');
            if (stageInfoEl && this.stages[this.currentStageIndex]) {
                const stage = this.stages[this.currentStageIndex];
                stageInfoEl.innerHTML = `
                    <div class="stage-name">${stage.name}</div>
                    ${stage.instruction ? `<div class="stage-instruction">${stage.instruction}</div>` : ''}
                `;
            }
        }

        updateGestureList() {
            // 使用正确的ID: gestureList
            const gestureList = document.getElementById('gestureList');
            if (!gestureList) {
                console.warn('[Collection] 未找到 #gestureList 元素');
                return;
            }

            // 离散手势：显示手势列表
            if (this.currentTaskId === 'discrete_gesture') {
                // 更新标题
                const titleEl = gestureList.querySelector('.gesture-list-title');
                if (titleEl) {
                    titleEl.innerHTML = '<i class="fas fa-hand-paper"></i> 手势库';
                }
                
                let html = '';
                
                // 添加进度摘要
                html += `
                    <div class="gesture-progress-summary" style="font-size: 12px; padding: 5px 8px; margin-bottom: 5px;">
                        <span>进度: ${this.currentGestureIndex}/${this.gestures.length} 手势</span>
                        <span>Stage: ${this.currentStageIndex + 1}/${this.stages.length}</span>
                    </div>
                `;
                
                this.gestures.forEach((gesture, index) => {
                    let status = 'pending';
                    let progressText = '';
                    
                    // 判断当前是否正在执行手势（不在休息期间）
                    const isActivelyCollecting = this._isRunning && this.currentPhase === 'gesture';
                    
                    if (index < this.currentGestureIndex) {
                        // 已完成的手势
                        status = 'completed';
                        progressText = `<span class="gesture-progress" style="font-size: 11px;">✓ 完成</span>`;
                    } else if (index === this.currentGestureIndex && isActivelyCollecting) {
                        // 当前正在采集的手势（仅在gesture阶段显示进度）
                        status = 'current';
                        progressText = `<span class="gesture-progress" style="font-size: 11px;">${this.gestureRepeatCount}/${this.currentExecutionParams.repeatPerGesture}</span>`;
                    } else {
                        // 待采集的手势（包括休息期间的下一个手势）
                        status = 'pending';
                        progressText = `<span class="gesture-progress" style="font-size: 11px; color: #999;">${this.currentExecutionParams.repeatPerGesture}次</span>`;
                    }
                    
                    const iconClass = status === 'completed' ? 'check-circle' : 
                                     status === 'current' ? 'circle-notch fa-spin' : 'circle';
                    
                    html += `
                        <div class="gesture-item ${status}" data-index="${index}" style="padding: 6px 8px; font-size: 13px; display: flex; align-items: center; gap: 6px;">
                            <span class="gesture-icon" style="font-size: 14px;">${gesture.icon || '✋'}</span>
                            <span class="gesture-name" style="flex: 1; font-size: 13px;">${gesture.name}</span>
                            ${progressText}
                            <i class="fas fa-${iconClass} status-icon" style="font-size: 12px;"></i>
                        </div>
                    `;
                });
                
                // 在标题后面插入内容
                const titleElement = gestureList.querySelector('.gesture-list-title');
                if (titleElement) {
                    // 移除旧内容（除了标题）
                    const children = Array.from(gestureList.children);
                    children.forEach(child => {
                        if (!child.classList.contains('gesture-list-title')) {
                            child.remove();
                        }
                    });
                    // 插入新内容
                    titleElement.insertAdjacentHTML('afterend', html);
                } else {
                    gestureList.innerHTML = html;
                }
            } else {
                // 连续手势：显示Stage列表和试次进度
                const titleEl = gestureList.querySelector('.gesture-list-title');
                if (titleEl) {
                    titleEl.innerHTML = '<i class="fas fa-list-ol"></i> Stage列表';
                }
                
                // 获取当前任务的执行参数
                const trialsPerStage = this.currentExecutionParams.trialsPerStage || 10;
                
                let html = '';
                
                // 添加进度摘要
                html += `
                    <div class="gesture-progress-summary" style="font-size: 12px; padding: 5px 8px; margin-bottom: 5px; background: #f0f9ff; border-radius: 4px;">
                        <span>Stage: ${this.currentStageIndex + 1}/${this.stages.length}</span>
                        <span>每Stage: ${trialsPerStage}次</span>
                    </div>
                `;
                
                this.stages.forEach((stage, index) => {
                    let status = 'pending';
                    let progressText = '';
                    let trialProgress = 0;
                    
                    if (index < this.currentStageIndex) {
                        // 已完成的Stage
                        status = 'completed';
                        progressText = `<span class="gesture-progress" style="font-size: 11px; color: #10b981;">✓ 完成</span>`;
                    } else if (index === this.currentStageIndex) {
                        if (this._isRunning) {
                            // 当前正在进行的Stage - 获取实时进度
                            status = 'current';
                            trialProgress = this.getCurrentTrialProgress();
                            progressText = `<span class="gesture-progress" style="font-size: 11px; color: #3b82f6;">${trialProgress}/${trialsPerStage}</span>`;
                        } else {
                            // 当前Stage但未开始
                            status = 'pending';
                            progressText = `<span class="gesture-progress" style="font-size: 11px; color: #999;">0/${trialsPerStage}</span>`;
                        }
                    } else {
                        // 未开始的Stage
                        status = 'pending';
                        progressText = `<span class="gesture-progress" style="font-size: 11px; color: #999;">0/${trialsPerStage}</span>`;
                    }
                    
                    const iconClass = status === 'completed' ? 'check-circle' : 
                                     status === 'current' ? 'circle-notch fa-spin' : 'circle';
                    const iconColor = status === 'completed' ? '#10b981' : 
                                     status === 'current' ? '#3b82f6' : '#9ca3af';
                    
                    html += `
                        <div class="gesture-item ${status}" data-index="${index}" style="padding: 8px 10px; font-size: 13px; display: flex; align-items: center; gap: 8px; ${status === 'current' ? 'background: #eff6ff; border-radius: 6px;' : ''}">
                            <i class="fas fa-${iconClass}" style="font-size: 14px; color: ${iconColor};"></i>
                            <span style="flex: 1; font-size: 13px; ${status === 'current' ? 'font-weight: 600; color: #1e40af;' : ''}">${stage.name}</span>
                            ${progressText}
                        </div>
                    `;
                });
                
                const titleElement = gestureList.querySelector('.gesture-list-title');
                if (titleElement) {
                    const children = Array.from(gestureList.children);
                    children.forEach(child => {
                        if (!child.classList.contains('gesture-list-title')) {
                            child.remove();
                        }
                    });
                    titleElement.insertAdjacentHTML('afterend', html);
                } else {
                    gestureList.innerHTML = html;
                }
            }
        }

        /**
         * 获取当前Stage的试次进度（用于连续手势）
         */
        getCurrentTrialProgress() {
            // 尝试从对应的动画模块获取进度
            if (this.currentTaskId === 'continual_gesture_1' && window.continualGesture1Animation) {
                const progress = window.continualGesture1Animation.getProgress();
                return progress.trial || 0;
            } else if (this.currentTaskId === 'continual_gesture_2' && window.continualGesture2Animation) {
                const progress = window.continualGesture2Animation.getProgress();
                return progress.trial || 0;
            }
            return this.continualTrialCount || 0;
        }

        updateNextStageButton() {
            const nextStageBtn = document.getElementById('nextStageBtn');
            if (!nextStageBtn) return;

            // 显示/隐藏下一Stage按钮
            if (this.currentStageIndex < this.stages.length - 1 && !this._isRunning) {
                nextStageBtn.style.display = 'inline-flex';
                nextStageBtn.textContent = `进入下一Stage: ${this.stages[this.currentStageIndex + 1]?.name || ''}`;
            } else {
                nextStageBtn.style.display = 'none';
            }
        }

        getTaskConfig() {
            if (window.TaskConfig?.DEFINITIONS?.[this.currentTaskId]) {
                return window.TaskConfig.DEFINITIONS[this.currentTaskId];
            }
            
            const taskNames = {
                'discrete_gesture': '离散手势采集',
                'continual_gesture_1': '连续手势采集1',
                'continual_gesture_2': '连续手势采集2'
            };
            
            return { name: taskNames[this.currentTaskId] || this.currentTaskId };
        }

        // ==================== Stage切换 ====================

        switchStage(stageIndex) {
            if (stageIndex < 0 || stageIndex >= this.stages.length) return;
            
            console.log('[Collection] 切换Stage:', stageIndex, this.stages[stageIndex]?.name);
            
            this.currentStageIndex = stageIndex;
            this.currentGestureIndex = 0;
            this.gestureRepeatCount = 0;
            
            // 更新UI
            this.updateStageSelect();
            this.updateGestureList();
            this.updateNextStageButton();
            this.resetDisplay();
            
            // 通知realtimeEngine
            this.sendToRealtimeEngine('stage_change', {
                stageIndex: stageIndex,
                stageName: this.stages[stageIndex]?.name || this.stages[stageIndex]?.id  // 优先使用name
            });
        }

        goToNextStage() {
            if (this.currentStageIndex < this.stages.length - 1) {
                this.switchStage(this.currentStageIndex + 1);
                this.showToast(`已切换到Stage: ${this.stages[this.currentStageIndex]?.name}`, 'success');
            }
        }

        // ==================== 采集控制 ====================

        startTask() {
            if (this._isRunning) return;
            
            console.log('[Collection] ===== 开始采集任务 =====');
            console.log('[Collection] 任务类型:', this.currentTaskId);
            console.log('[Collection] 当前Stage:', this.stages[this.currentStageIndex]?.name);
            
            this._isRunning = true;
            this._isPaused = false;
            this.currentGestureIndex = 0;
            this.gestureRepeatCount = 0;
            
            this.updateControlButtons(true);
            
            // 禁用Stage切换
            const stageSelect = document.getElementById('stageSwitchSelect');
            if (stageSelect) stageSelect.disabled = true;
            
            // 隐藏下一Stage按钮
            this.updateNextStageButton();
            
            // 通知realtimeEngine开始采集
            const currentStage = this.stages[this.currentStageIndex];
            const userData = JSON.parse(localStorage.getItem('emg_current_user') || '{}');
            
            // 【重要】确保userId不为空，按优先级获取
            const userId = userData.id || 
                           this.collectionConfig?.subject?.id || 
                           `S${Date.now().toString().slice(-6)}`;  // 自动生成6位编号
            
            console.log('[Collection] 用户ID:', userId);
            console.log('[Collection] 目录结构:', 
                `${this.currentTaskId}/${this.collectionConfig?.category1}/${this.collectionConfig?.category2}/${this.collectionConfig?.category4}/`);
            
            this.sendToRealtimeEngine('collection_start', {
                taskId: this.currentTaskId,
                stageName: currentStage?.name || currentStage?.id || 'stage_1',  // 优先使用name（中文）
                userId: userId,
                config: this.collectionConfig
            });
            
            // 开始采集流程
            if (this.currentTaskId === 'discrete_gesture') {
                this.startDiscreteGestureCollection();
            } else {
                this.startContinualGestureCollection();
            }
        }

        stopTask() {
            if (!this._isRunning) return;
            
            console.log('[Collection] ===== 停止采集任务 =====');
            
            this._isRunning = false;
            this._isPaused = false;
            
            // 清除所有定时器
            if (this.phaseTimer) {
                clearTimeout(this.phaseTimer);
                this.phaseTimer = null;
            }
            if (this.countdownTimer) {
                clearInterval(this.countdownTimer);
                this.countdownTimer = null;
            }
            if (this.continualProgressTimer) {
                clearInterval(this.continualProgressTimer);
                this.continualProgressTimer = null;
            }
            
            // 停止离散手势动画
            if (window.discreteGestureAnimation) {
                window.discreteGestureAnimation.stop();
            }
            
            // 停止连续手势1动画
            if (window.continualGesture1Animation) {
                window.continualGesture1Animation.stop();
            }
            
            // 停止连续手势2动画
            if (window.continualGesture2Animation) {
                window.continualGesture2Animation.stop();
            }
            
            // 停止其他动画控制器
            if (window.animationController) {
                window.animationController.stop();
            }
            
            // 启用Stage切换
            const stageSelect = document.getElementById('stageSwitchSelect');
            if (stageSelect) stageSelect.disabled = false;
            
            this.updateControlButtons(false);
            this.updateNextStageButton();
            this.updateStatus('已停止');
            
            // 通知realtimeEngine停止采集
            this.sendToRealtimeEngine('collection_stop', { completed: false });
        }

        togglePause() {
            if (!this._isRunning) return;
            
            this._isPaused = !this._isPaused;
            
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

        // ==================== 离散手势采集流程 ====================

        startDiscreteGestureCollection() {
            console.log('[Collection] 开始离散手势顺序采集');
            
            // 先显示准备阶段
            this.currentPhase = 'prepare';
            this.showPreparation(() => {
                // 准备完成后开始第一个手势
                this.startNextGesture();
            });
        }

        showPreparation(callback) {
            const prepTime = this.currentExecutionParams.preparationTime;
            const currentStage = this.stages[this.currentStageIndex];
            
            this.updateGestureDisplay({
                name: '准备开始',
                instruction: currentStage?.instruction || `采集即将开始，请保持 ${currentStage?.name || ''} 姿势...`,
                showCountdown: true,
                countdownValue: prepTime
            });
            
            let countdown = prepTime;
            const countdownEl = document.getElementById('countdown');
            
            this.countdownTimer = setInterval(() => {
                countdown--;
                if (countdownEl) countdownEl.textContent = countdown;
                
                if (countdown <= 0) {
                    clearInterval(this.countdownTimer);
                    this.countdownTimer = null;
                    if (countdownEl) countdownEl.style.display = 'none';
                    callback();
                }
            }, 1000);
        }

        startNextGesture() {
            if (!this._isRunning || this._isPaused) return;
            
            if (this.currentGestureIndex >= this.gestures.length) {
                // 所有手势完成
                this.onAllGesturesComplete();
                return;
            }
            
            const gesture = this.gestures[this.currentGestureIndex];
            const currentStage = this.stages[this.currentStageIndex];
            console.log(`[Collection] 开始手势 ${this.currentGestureIndex + 1}/${this.gestures.length}: ${gesture.name}`);
            
            this.currentPhase = 'gesture';
            this.gestureRepeatCount = 0;
            
            // 更新列表显示
            this.updateGestureList();
            
            // 发送prompt_start
            this.sendToRealtimeEngine('prompt_start', {
                promptName: gesture.id || gesture.name,
                promptIndex: this.currentGestureIndex
            });
            
            // 构建promptSequence - 当前手势重复N次
            const repeatCount = this.currentExecutionParams.repeatPerGesture;
            const promptSequence = [];
            for (let i = 0; i < repeatCount; i++) {
                promptSequence.push(gesture.id || gesture.name);
            }
            
            // 构建动画需要的stage配置
            const stageConfig = {
                name: currentStage?.name || currentStage?.id || 'stage',  // 优先使用name
                label: currentStage?.name || 'Stage',
                instruction: currentStage?.instruction || '请按照提示进行手势动作',
                promptSequence: promptSequence
            };
            
            // 构建promptLibrary - 当前手势的定义
            this.setupPromptLibrary(gesture);
            
            // 使用discreteGestureAnimation播放动画
            if (window.discreteGestureAnimation) {
                window.discreteGestureAnimation.start(
                    stageConfig,
                    // 完成回调
                    () => {
                        this.onGestureAnimationComplete();
                    },
                    // prompt触发回调
                    (promptName, stageId) => {
                        this.gestureRepeatCount++;
                        console.log(`[Collection] 手势 ${gesture.name} - 第 ${this.gestureRepeatCount}/${repeatCount} 次`);
                        this.updateGestureList();
                    }
                );
            } else {
                // 如果动画模块未加载，使用简单的定时器
                console.warn('[Collection] discreteGestureAnimation 未加载，使用简单模式');
                this.doGestureRepeatSimple();
            }
        }

        /**
         * 设置Prompt库（用于动画显示）
         */
        setupPromptLibrary(gesture) {
            // 将当前手势添加到DISCRETE_GESTURE_CONFIG.PROMPT_LIBRARY
            if (!window.DISCRETE_GESTURE_CONFIG) {
                window.DISCRETE_GESTURE_CONFIG = { PROMPT_LIBRARY: {} };
            }
            if (!window.DISCRETE_GESTURE_CONFIG.PROMPT_LIBRARY) {
                window.DISCRETE_GESTURE_CONFIG.PROMPT_LIBRARY = {};
            }
            
            // 添加或更新当前手势的定义
            const gestureId = gesture.id || gesture.name;
            window.DISCRETE_GESTURE_CONFIG.PROMPT_LIBRARY[gestureId] = {
                label: gesture.name,
                icon: gesture.icon || '✋',
                color: '#3b82f6'
            };
        }

        /**
         * 手势动画完成回调
         */
        onGestureAnimationComplete() {
            const gesture = this.gestures[this.currentGestureIndex];
            console.log(`[Collection] 手势 ${gesture.name} 动画完成`);
            
            // 发送prompt_end
            this.sendToRealtimeEngine('prompt_end', {
                promptName: gesture.id || gesture.name,
                promptIndex: this.currentGestureIndex
            });
            
            this.currentGestureIndex++;
            this.updateGestureList();
            
            if (this.currentGestureIndex >= this.gestures.length) {
                // 所有手势完成
                this.onAllGesturesComplete();
            } else {
                // 休息后进入下一个手势
                this.showRestPeriod(() => {
                    this.startNextGesture();
                });
            }
        }

        /**
         * 简单模式的手势重复（当动画模块不可用时）
         */
        doGestureRepeatSimple() {
            if (!this._isRunning || this._isPaused) return;
            
            const gesture = this.gestures[this.currentGestureIndex];
            const repeatMax = this.currentExecutionParams.repeatPerGesture;
            
            if (this.gestureRepeatCount >= repeatMax) {
                // 当前手势重复完成
                this.onGestureAnimationComplete();
                return;
            }
            
            this.gestureRepeatCount++;
            console.log(`[Collection] 手势 ${gesture.name} - 第 ${this.gestureRepeatCount}/${repeatMax} 次`);
            
            // 发送prompt信号
            this.sendToRealtimeEngine('prompt', {
                name: gesture.id || gesture.name,
                stageName: this.stages[this.currentStageIndex]?.name || this.stages[this.currentStageIndex]?.id,  // 优先使用name
                timestamp: Date.now()
            });
            
            // 更新显示
            this.updateGestureDisplay({
                name: `${gesture.icon || '✋'} ${gesture.name}`,
                instruction: `请执行手势动作 (${this.gestureRepeatCount}/${repeatMax})`,
                showCountdown: false
            });
            
            // 更新列表
            this.updateGestureList();
            
            // 等待显示时间后进行下一次重复
            const displayTime = this.currentExecutionParams.gestureDisplayTime * 1000;
            const intervalTime = this.currentExecutionParams.intervalBetweenRepeat * 1000;
            
            this.phaseTimer = setTimeout(() => {
                // 间隔时间
                this.updateGestureDisplay({
                    name: '准备下一次',
                    instruction: '...',
                    showCountdown: false
                });
                
                this.phaseTimer = setTimeout(() => {
                    this.doGestureRepeatSimple();
                }, intervalTime);
            }, displayTime);
        }

        showRestPeriod(callback) {
            const restTime = this.currentExecutionParams.restBetweenGestures;
            const nextGesture = this.gestures[this.currentGestureIndex];
            
            this.currentPhase = 'rest';
            
            // 重置重复计数，避免休息期间显示错误的进度
            this.gestureRepeatCount = 0;
            
            // 更新手势列表显示
            this.updateGestureList();
            
            this.updateGestureDisplay({
                name: '休息时间',
                instruction: `下一个手势: ${nextGesture?.icon || '✋'} ${nextGesture?.name || ''}`,
                showCountdown: true,
                countdownValue: restTime
            });
            
            let countdown = restTime;
            const countdownEl = document.getElementById('countdown');
            if (countdownEl) {
                countdownEl.style.display = 'block';
                countdownEl.textContent = countdown;
            }
            
            this.countdownTimer = setInterval(() => {
                if (this._isPaused) return;
                
                countdown--;
                if (countdownEl) countdownEl.textContent = countdown;
                
                if (countdown <= 0) {
                    clearInterval(this.countdownTimer);
                    this.countdownTimer = null;
                    if (countdownEl) countdownEl.style.display = 'none';
                    callback();
                }
            }, 1000);
        }

        onAllGesturesComplete() {
            console.log('[Collection] ===== 当前Stage所有手势采集完成 =====');
            
            this.currentPhase = 'complete';
            
            const hasMoreStages = this.currentStageIndex < this.stages.length - 1;
            const nextStageName = hasMoreStages ? this.stages[this.currentStageIndex + 1]?.name : '';
            
            this.updateGestureDisplay({
                name: '🎉 Stage采集完成！',
                instruction: hasMoreStages ? 
                    `可以点击"进入下一Stage"继续采集: ${nextStageName}` : 
                    '所有Stage已完成！',
                showCountdown: false
            });
            
            // 发送stage结束
            const currentStage = this.stages[this.currentStageIndex];
            this.sendToRealtimeEngine('stage_end', {
                stageName: currentStage?.name || currentStage?.id  // 优先使用name
            });
            
            // 通知采集完成
            this.sendToRealtimeEngine('collection_stop', { completed: true });
            
            this._isRunning = false;
            
            // 启用Stage切换
            const stageSelect = document.getElementById('stageSwitchSelect');
            if (stageSelect) stageSelect.disabled = false;
            
            this.updateControlButtons(false);
            this.updateNextStageButton();
            this.updateStatus('采集完成');
            
            this.showToast('当前Stage采集完成！', 'success');
        }

        // ==================== 连续手势采集流程 ====================

        startContinualGestureCollection() {
            console.log('[Collection] 开始连续手势采集');
            console.log('[Collection] 任务类型:', this.currentTaskId);
            
            const currentStage = this.stages[this.currentStageIndex];
            this.continualTrialCount = 0;  // 重置试次计数
            
            // 先显示准备阶段
            this.currentPhase = 'prepare';
            this.showContinualPreparation(() => {
                // 准备完成后开始连续手势动画
                this.startContinualAnimation();
            });
        }

        /**
         * 连续手势准备阶段
         */
        showContinualPreparation(callback) {
            const prepTime = this.currentExecutionParams.preparationTime || 3;
            const currentStage = this.stages[this.currentStageIndex];
            
            this.updateGestureDisplay({
                name: '准备开始',
                instruction: currentStage?.instruction || `即将开始光标移动任务，请准备...`,
                showCountdown: true,
                countdownValue: prepTime
            });
            
            let countdown = prepTime;
            const countdownEl = document.getElementById('countdown');
            
            this.countdownTimer = setInterval(() => {
                countdown--;
                if (countdownEl) countdownEl.textContent = countdown;
                
                if (countdown <= 0) {
                    clearInterval(this.countdownTimer);
                    this.countdownTimer = null;
                    if (countdownEl) countdownEl.style.display = 'none';
                    callback();
                }
            }, 1000);
        }

        /**
         * 启动连续手势动画
         */
        startContinualAnimation() {
            console.log('[Collection] 启动连续手势动画:', this.currentTaskId);
            
            const currentStage = this.stages[this.currentStageIndex];
            this.currentPhase = 'continual';
            
            // 更新UI
            this.updateGestureDisplay({
                name: '光标移动任务',
                instruction: '请通过滚轮移动光标到目标位置',
                showCountdown: false
            });
            
            // 构建stage配置
            const stageConfig = {
                name: currentStage?.name || currentStage?.id || 'stage',
                label: currentStage?.name || 'Stage',
                instruction: currentStage?.instruction || '请将光标移动到目标区域'
            };
            
            // 根据任务类型选择对应的动画模块
            let animationModule = null;
            if (this.currentTaskId === 'continual_gesture_1') {
                animationModule = window.continualGesture1Animation;
            } else if (this.currentTaskId === 'continual_gesture_2') {
                animationModule = window.continualGesture2Animation;
            }
            
            if (animationModule) {
                // 重新加载配置（确保使用最新的参数）
                animationModule.loadConfig();
                
                // 设置进度更新回调（用于更新左侧列表）
                this.setupContinualProgressUpdater(animationModule);
                
                // 启动动画
                animationModule.start(
                    stageConfig,
                    // 完成回调
                    () => {
                        this.onContinualAnimationComplete();
                    },
                    // 试次完成回调（可选）
                    (trialIndex) => {
                        this.onContinualTrialComplete(trialIndex);
                    }
                );
                
                console.log('[Collection] 连续手势动画已启动');
            } else {
                console.error('[Collection] 未找到连续手势动画模块:', this.currentTaskId);
                this.showToast('动画模块未加载', 'error');
                this.stopTask();
            }
            
            this.updateStatus('采集中');
            this.updateGestureList();
        }

        /**
         * 设置连续手势进度更新器
         */
        setupContinualProgressUpdater(animationModule) {
            // 定期更新左侧列表显示
            this.continualProgressTimer = setInterval(() => {
                if (this._isRunning && animationModule.isAnimationRunning()) {
                    this.updateGestureList();
                } else {
                    clearInterval(this.continualProgressTimer);
                    this.continualProgressTimer = null;
                }
            }, 500);  // 每500ms更新一次
        }

        /**
         * 连续手势试次完成回调
         */
        onContinualTrialComplete(trialIndex) {
            this.continualTrialCount = trialIndex + 1;
            console.log(`[Collection] 试次完成: ${this.continualTrialCount}`);
            
            // 发送试次完成信号到realtimeEngine
            this.sendToRealtimeEngine('trial_complete', {
                trialIndex: trialIndex,
                stageName: this.stages[this.currentStageIndex]?.name
            });
            
            // 更新显示
            this.updateGestureList();
        }

        /**
         * 连续手势动画完成回调
         */
        onContinualAnimationComplete() {
            console.log('[Collection] 连续手势动画完成');
            
            // 清除进度更新定时器
            if (this.continualProgressTimer) {
                clearInterval(this.continualProgressTimer);
                this.continualProgressTimer = null;
            }
            
            // 调用原有的Stage完成处理
            this.onContinualStageComplete();
        }

        onContinualStageComplete() {
            console.log('[Collection] 连续手势Stage完成');
            
            const currentStage = this.stages[this.currentStageIndex];
            this.sendToRealtimeEngine('stage_end', {
                stageName: currentStage?.name || currentStage?.id  // 优先使用name
            });
            
            // 检查是否还有下一个Stage
            if (this.currentStageIndex < this.stages.length - 1) {
                this.updateGestureDisplay({
                    name: 'Stage完成',
                    instruction: '可以点击开始进行下一个Stage',
                    showCountdown: false
                });
            } else {
                this.sendToRealtimeEngine('collection_stop', { completed: true });
            }
            
            this._isRunning = false;
            
            const stageSelect = document.getElementById('stageSwitchSelect');
            if (stageSelect) stageSelect.disabled = false;
            
            this.updateControlButtons(false);
            this.updateNextStageButton();
            this.updateGestureList();
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
            if (window.waveformController?.dataReceiver?.ws) {
                return window.waveformController.dataReceiver.ws;
            }
            return null;
        }

        // ==================== UI更新 ====================

        updateGestureDisplay({ name, instruction, showCountdown, countdownValue }) {
            const gestureNameEl = document.getElementById('gestureName');
            const gestureInstructionEl = document.getElementById('gestureInstruction');
            const countdownEl = document.getElementById('countdown');
            const gestureIcon = document.getElementById('gestureIcon');
            
            if (gestureIcon?.parentElement) {
                gestureIcon.parentElement.style.display = 'none';
            }
            
            if (gestureNameEl) gestureNameEl.textContent = name || '';
            if (gestureInstructionEl) gestureInstructionEl.textContent = instruction || '';
            
            if (countdownEl) {
                countdownEl.style.display = showCountdown ? 'block' : 'none';
                if (showCountdown && countdownValue !== undefined) {
                    countdownEl.textContent = countdownValue;
                }
            }
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

        updateProgress() {
            const total = this.gestures.length;
            const current = this.currentGestureIndex;
            const percent = total > 0 ? (current / total) * 100 : 0;

            const progressFill = document.getElementById('progressFill');
            const progressText = document.getElementById('progressText');

            if (progressFill) progressFill.style.width = `${percent}%`;
            if (progressText) {
                progressText.textContent = `${current} / ${total} 手势`;
            }
        }

        resetDisplay() {
            const gestureNameEl = document.getElementById('gestureName');
            const gestureInstructionEl = document.getElementById('gestureInstruction');
            const gestureIcon = document.getElementById('gestureIcon');
            const progressFill = document.getElementById('progressFill');
            const progressText = document.getElementById('progressText');
            const countdownEl = document.getElementById('countdown');

            if (gestureNameEl) gestureNameEl.textContent = '点击开始';
            if (gestureInstructionEl) {
                const stageName = this.stages[this.currentStageIndex]?.name || '';
                gestureInstructionEl.textContent = `当前Stage: ${stageName}，点击开始按钮开始采集`;
            }
            if (gestureIcon?.parentElement) gestureIcon.parentElement.style.display = '';
            if (progressFill) progressFill.style.width = '0%';
            if (progressText) progressText.textContent = `0 / ${this.gestures.length} 手势`;
            if (countdownEl) countdownEl.style.display = 'none';

            this.updateStatus('准备就绪');
        }

        showToast(message, type = 'info') {
            const toast = document.getElementById('toast');
            if (toast) {
                const icon = type === 'success' ? 'check' : type === 'warning' ? 'exclamation-triangle' : 'info';
                toast.className = `toast ${type}`;
                toast.innerHTML = `<i class="fas fa-${icon}-circle"></i> ${message}`;
                toast.classList.add('visible');
                setTimeout(() => toast.classList.remove('visible'), 3000);
            }
        }

        // ==================== 外部接口 ====================

        selectTask(htmlTaskId) {
            // 保留兼容性，但在新流程中不使用
            const taskIdMap = {
                'discrete': 'discrete_gesture',
                'continuous1': 'continual_gesture_1',
                'continuous2': 'continual_gesture_2'
            };
            
            this.currentTaskId = taskIdMap[htmlTaskId] || htmlTaskId;
            console.log('[Collection] 设置任务类型:', this.currentTaskId);
            
            // 更新当前任务的执行参数
            this.currentExecutionParams = this.executionParams[this.currentTaskId] || this.executionParams.discrete_gesture;
            console.log('[Collection] 当前任务执行参数:', this.currentExecutionParams);
            
            // 通知animationController
            if (window.animationController) {
                window.animationController.setCurrentTask(this.currentTaskId);
            }
            
            this.updateUI();
        }

        getCurrentTaskId() {
            return this.currentTaskId;
        }
        
        /**
         * 获取当前任务的执行参数
         */
        getCurrentExecutionParams() {
            return this.currentExecutionParams;
        }
    }

    // ==================== 初始化 ====================
    console.log('[Collection] 准备初始化控制器 (v3)');
    
    function initController() {
        console.log('[Collection] ====== 开始初始化控制器 (v3) ======');
        
        try {
            const controller = new CollectionController();
            window.collectionController = controller;
            console.log('[Collection] 控制器已创建并挂载到 window.collectionController');
            
            setTimeout(() => {
                console.log('[Collection] 延迟初始化开始...');
                controller.init();
            }, 100);
            
        } catch (error) {
            console.error('[Collection] 初始化失败:', error);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initController);
    } else {
        initController();
    }

    console.log('[Collection] 脚本主体执行完毕 (v3)');

})();

console.log('[Collection] ====== 脚本加载结束 (v3) ======');
