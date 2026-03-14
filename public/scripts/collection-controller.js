/**
 * collection-controller.js - 采集任务控制器（v3 - 修复版v3）
 * 
 * 修复内容：
 * 1. 切换Stage时重置动画模块的试次计数
 * 2. 将执行参数直接传递给动画模块的start函数
 * 3. 左侧列表正确显示试次进度
 */

console.log('[Collection] ====== 脚本开始加载 (v3-fixed-v3) ======');

(function() {
    'use strict';

    class CollectionController {
        constructor() {
            console.log('[Collection] 构造函数开始');
            
            this.collectionConfig = null;
            this.currentTaskId = 'discrete_gesture';
            
            // Session相关状态
            this.sessionCount = 3;           // session总数
            this.currentSessionIndex = 0;    // 当前session索引
            
            this.stages = [];
            this.currentStageIndex = 0;
            this.allGestures = [];   // 【新增】全局手势库
            this.gestures = [];      // 当前Stage使用的手势库
            this.currentGestureIndex = 0;
            this.gestureRepeatCount = 0;
            
            this.executionParams = {
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
                },
                continual_gesture_3: {
                    trialsPerStage: 10,
                    stageTimeout: 120,
                    guideSpeed: 0.15,
                    guideSize: 0.15,
                    holdDuration: 1.0,
                    preparationTime: 3.0
                }
            };
            
            this.currentExecutionParams = this.executionParams.discrete_gesture;
            this._isRunning = false;
            this._isPaused = false;
            this.currentPhase = null;
            this.continualTrialCount = 0;
            this.continualProgressTimer = null;
            this.phaseTimer = null;
            this.countdownTimer = null;

            // ===== 标定相关状态 =====
            this.calibrationEnabled = true;     // 是否启用标定流程
            this.isCalibrating = false;         // 是否正在标定
            this.calibrationPhase = null;       // 'demo' | 'min' | 'max' | null
            this.calibrationTimer = null;       // 标定计时器
            this.skipCalibration = false;       // 是否跳过标定

            // ===== 全部轮次采集相关状态 =====
            this._isAllSessionsMode = false;    // 是否为全部轮次采集模式
            this._restBetweenSessions = 30;     // 轮次间休息时间（秒）
            this._restCountdownTimer = null;    // 休息倒计时定时器

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
            
            // Session选择事件
            const sessionSelect = document.getElementById('sessionSwitchSelect');
            if (sessionSelect) {
                sessionSelect.addEventListener('change', (e) => {
                    if (!this._isRunning) {
                        this.switchSession(parseInt(e.target.value));
                    } else {
                        e.target.value = this.currentSessionIndex;
                        this.showToast('采集进行中，无法切换Session', 'warning');
                    }
                });
            }
            
            const stageSelect = document.getElementById('stageSwitchSelect');
            if (stageSelect) {
                stageSelect.addEventListener('change', (e) => {
                    if (!this._isRunning) {
                        this.switchStage(parseInt(e.target.value));
                    } else {
                        e.target.value = this.currentStageIndex;
                        this.showToast('采集进行中，无法切换Stage', 'warning');
                    }
                });
            }

            const startBtn = document.getElementById('startTaskBtn');
            if (startBtn) {
                startBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    console.log('[Collection] ★★★ 开始按钮被点击 ★★★');
                    this.startTask(false);  // 正常采集模式
                });
            }

            // 【修改】测试模式按钮（替换暂停按钮）
            const testBtn = document.getElementById('testModeBtn');
            if (testBtn) {
                testBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    console.log('[Collection] ★★★ 测试模式按钮被点击 ★★★');
                    this.startTask(true);  // 测试模式，不保存H5文件
                });
            }

            // 【新增】全部轮次采集按钮
            const startAllBtn = document.getElementById('startAllSessionsBtn');
            if (startAllBtn) {
                startAllBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    console.log('[Collection] ★★★ 全部轮次采集按钮被点击 ★★★');
                    this.startAllSessions();
                });
            }

            const stopBtn = document.getElementById('stopTaskBtn');
            if (stopBtn) {
                stopBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    this.stopTask();
                });
            }

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
            this.collectionConfig = window.currentCollectionConfig ||
                JSON.parse(localStorage.getItem('emg_current_collection_config') || 'null');

            if (this.collectionConfig) {
                console.log('[Collection] 加载采集配置:', this.collectionConfig);

                this.currentTaskId = this.collectionConfig.task_id || this.collectionConfig.task || 'discrete_gesture';

                // 【关键】强制从localStorage读取最新模板
                const template = this.getLatestTemplate();

                if (this.collectionConfig.category3List && this.collectionConfig.category3List.length > 0) {
                    this.stages = this.collectionConfig.category3List;
                } else {
                    this.stages = (template.category3 || []).filter(s => s.enabled);
                }

                // 加载Session数量（优先从collectionConfig，其次从template）
                if (this.collectionConfig.sessionConfig?.count) {
                    this.sessionCount = this.collectionConfig.sessionConfig.count;
                } else {
                    this.sessionCount = template.sessionConfig?.count || 3;
                }
                console.log('[Collection] Session数量:', this.sessionCount);

                // 【新增】加载轮次间休息时间
                if (this.collectionConfig.sessionConfig?.restBetweenSessions) {
                    this._restBetweenSessions = this.collectionConfig.sessionConfig.restBetweenSessions;
                } else {
                    this._restBetweenSessions = template.sessionConfig?.restBetweenSessions || 30;
                }
                console.log('[Collection] 轮次间休息时间:', this._restBetweenSessions, '秒');

                // 【修改】先加载全局手势库作为后备
                if (this.collectionConfig.gestures?.discrete) {
                    this.allGestures = this.collectionConfig.gestures.discrete.filter(g => g.enabled);
                } else {
                    this.allGestures = (template.gestures?.discrete || []).filter(g => g.enabled);
                }
                console.log('[Collection] 全局手势库数量:', this.allGestures.length);

                // 【新增】根据当前Stage加载对应手势库
                this.loadGesturesForCurrentStage();

                // 【关键修复】优先从collectionConfig.execution读取执行参数
                // 这确保了从选择器保存的配置能正确传递
                const executionSource = this.collectionConfig.execution || template.execution;
                if (executionSource) {
                    if (executionSource[this.currentTaskId]) {
                        this.executionParams = { ...executionSource };
                        this.currentExecutionParams = { ...executionSource[this.currentTaskId] };
                        console.log('[Collection] ★★★ 加载执行参数 ★★★');
                        console.log('[Collection] 参数来源:', this.collectionConfig.execution ? 'collectionConfig' : 'template');
                        console.log('[Collection] 任务类型:', this.currentTaskId);
                        console.log('[Collection] trialsPerStage:', this.currentExecutionParams.trialsPerStage);
                    } else if (executionSource.repeatPerGesture !== undefined) {
                        console.log('[Collection] 检测到旧版本执行参数格式');
                        this.executionParams.discrete_gesture = { ...executionSource };
                        this.currentExecutionParams = this.executionParams[this.currentTaskId] || this.executionParams.discrete_gesture;
                    }
                }

                console.log('[Collection] 当前任务执行参数:', this.currentExecutionParams);
            } else {
                console.warn('[Collection] 未找到采集配置，使用默认值');
                this.loadDefaultConfig();
            }
        }

        /**
         * 【修复】强制从localStorage读取最新的模板配置
         */
        getLatestTemplate() {
            const saved = localStorage.getItem('emg_collection_template');
            if (saved) {
                try {
                    const template = JSON.parse(saved);
                    console.log('[Collection] 从localStorage读取最新模板');
                    console.log('[Collection] execution:', template.execution);
                    return template;
                } catch (e) {
                    console.warn('[Collection] 解析localStorage模板失败:', e);
                }
            }
            
            if (window.templateConfigManager?.currentTemplate) {
                return window.templateConfigManager.currentTemplate;
            }
            
            return this.getDefaultTemplate();
        }

        getTemplate() {
            return this.getLatestTemplate();
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
                    },
                    continual_gesture_3: {
                        trialsPerStage: 10,
                        stageTimeout: 120,
                        guideSpeed: 0.15,
                        guideSize: 0.15,
                        holdDuration: 1.0,
                        preparationTime: 3.0
                    }
                }
            };
        }

        loadDefaultConfig() {
            const template = this.getDefaultTemplate();
            this.stages = template.category3;
            this.allGestures = template.gestures.discrete;  // 【新增】保存全局手势库
            this.gestures = [...this.allGestures];          // 【修改】初始使用全局手势库
            this.executionParams = template.execution;
            this.currentExecutionParams = this.executionParams[this.currentTaskId] || this.executionParams.discrete_gesture;
            // 加载默认Session数量
            this.sessionCount = 3;
        }

        // ==================== 页面显示 ====================

        onPageShow() {
            console.log('[Collection] 页面显示');
            this.loadCollectionConfig();
            // 【修复】重置动画模块状态
            this.resetAnimationModules();
            this.updateUI();
        }

        /**
         * 【新增】重置所有动画模块的状态
         */
        resetAnimationModules() {
            if (window.continualGesture1Animation) {
                window.continualGesture1Animation.reset?.();
            }
            if (window.continualGesture2Animation) {
                window.continualGesture2Animation.reset?.();
            }
            this.continualTrialCount = 0;
        }

        updateUI() {
            this.updateTaskHeader();
            this.updateSessionSelect();
            this.updateStageSelect();
            this.updateGestureList();
            this.updateControlButtons(false);
            this.updateNextStageButton();
            this.resetDisplay();
        }

        updateTaskHeader() {
            const taskNameEl = document.getElementById('currentTaskName');
            const configBadgeEl = document.getElementById('taskConfigBadge');
            
            const taskConfig = this.getTaskConfig();
            if (taskNameEl) {
                taskNameEl.textContent = taskConfig.name || this.currentTaskId;
            }
            
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

        /**
         * 更新Session选择器
         */
        updateSessionSelect() {
            const select = document.getElementById('sessionSwitchSelect');
            if (!select) return;

            select.innerHTML = '';
            for (let i = 0; i < this.sessionCount; i++) {
                const option = document.createElement('option');
                option.value = i;
                option.textContent = `Session ${i + 1}`;
                if (i === this.currentSessionIndex) {
                    option.selected = true;
                }
                select.appendChild(option);
            }
            
            console.log('[Collection] Session选择器已更新, 当前Session:', this.currentSessionIndex + 1);
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
        }

        /**
         * 【修复】更新手势/试次列表
         */
        updateGestureList() {
            const gestureList = document.getElementById('gestureList');
            if (!gestureList) {
                console.warn('[Collection] 未找到 #gestureList 元素');
                return;
            }

            gestureList.style.display = 'block';

            if (this.currentTaskId === 'discrete_gesture') {
                this.renderDiscreteGestureList(gestureList);
            } else {
                this.renderContinualTrialProgress(gestureList);
            }
        }

        renderDiscreteGestureList(gestureList) {
            const titleEl = gestureList.querySelector('.gesture-list-title');
            if (titleEl) {
                titleEl.innerHTML = '<i class="fas fa-hand-paper"></i> 手势库';
            }
            
            let html = '';
            
            html += `
                <div class="gesture-progress-summary" style="font-size: 12px; padding: 5px 8px; margin-bottom: 5px;">
                    <span>Session: ${this.currentSessionIndex + 1}/${this.sessionCount}</span>
                    <span>Stage: ${this.currentStageIndex + 1}/${this.stages.length}</span>
                    <span>手势: ${this.currentGestureIndex}/${this.gestures.length}</span>
                </div>
            `;
            
            this.gestures.forEach((gesture, index) => {
                let status = 'pending';
                let progressText = '';
                
                const isActivelyCollecting = this._isRunning && this.currentPhase === 'gesture';
                
                if (index < this.currentGestureIndex) {
                    status = 'completed';
                    progressText = `<span class="gesture-progress" style="font-size: 11px;">✓ 完成</span>`;
                } else if (index === this.currentGestureIndex && isActivelyCollecting) {
                    status = 'current';
                    progressText = `<span class="gesture-progress" style="font-size: 11px;">${this.gestureRepeatCount}/${this.currentExecutionParams.repeatPerGesture}</span>`;
                } else {
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
            
            this.updateGestureListContent(gestureList, html);
        }

        /**
         * 【修复】渲染连续手势试次进度 - 使用配置中的trialsPerStage
         */
        renderContinualTrialProgress(gestureList) {
            const titleEl = gestureList.querySelector('.gesture-list-title');
            if (titleEl) {
                titleEl.innerHTML = '<i class="fas fa-bullseye"></i> 试次进度';
            }
            
            // 【关键修复】从currentExecutionParams获取trialsPerStage
            const trialsPerStage = this.currentExecutionParams.trialsPerStage || 10;
            
            // 【修复】获取正确的当前进度
            let currentTrial = 0;
            if (this._isRunning) {
                currentTrial = this.getCurrentTrialProgress();
            }
            // 如果没有运行，显示0
            
            let html = '';
            
            html += `
                <div class="gesture-progress-summary" style="font-size: 12px; padding: 8px; margin-bottom: 8px; background: #f0f9ff; border-radius: 6px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                        <span><i class="fas fa-sync-alt"></i> Session ${this.currentSessionIndex + 1}/${this.sessionCount}</span>
                        <span><i class="fas fa-layer-group"></i> Stage ${this.currentStageIndex + 1}/${this.stages.length}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                        <span>试次进度:</span>
                        <span style="font-weight: 600; color: #1e40af;">${currentTrial}/${trialsPerStage}</span>
                    </div>
                </div>
            `;
            
            const percent = trialsPerStage > 0 ? (currentTrial / trialsPerStage * 100) : 0;
            html += `
                <div style="padding: 0 8px; margin-bottom: 10px;">
                    <div style="height: 8px; background: #e5e7eb; border-radius: 4px; overflow: hidden;">
                        <div style="height: 100%; width: ${percent}%; background: linear-gradient(90deg, #3b82f6, #1e40af); border-radius: 4px; transition: width 0.3s;"></div>
                    </div>
                </div>
            `;
            
            const currentStage = this.stages[this.currentStageIndex];
            if (currentStage) {
                html += `
                    <div style="padding: 8px; background: #fafafa; border-radius: 6px; margin-bottom: 8px;">
                        <div style="font-size: 13px; font-weight: 600; color: #1f2937; margin-bottom: 4px;">
                            <i class="fas fa-hand-point-right" style="color: #3b82f6;"></i> ${currentStage.name}
                        </div>
                        ${currentStage.instruction ? `<div style="font-size: 11px; color: #6b7280;">${currentStage.instruction}</div>` : ''}
                    </div>
                `;
            }
            
            let statusText = '准备就绪';
            let statusColor = '#6b7280';
            if (this._isRunning) {
                if (this.currentPhase === 'prepare') {
                    statusText = '准备中...';
                    statusColor = '#f59e0b';
                } else if (this.currentPhase === 'continual') {
                    statusText = '采集中';
                    statusColor = '#10b981';
                }
            }
            
            html += `
                <div style="padding: 6px 8px; font-size: 12px; color: ${statusColor}; display: flex; align-items: center; gap: 6px;">
                    <span style="width: 8px; height: 8px; border-radius: 50%; background: ${statusColor};"></span>
                    ${statusText}
                </div>
            `;
            
            this.updateGestureListContent(gestureList, html);
        }

        updateGestureListContent(gestureList, html) {
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
                gestureList.innerHTML = `<div class="gesture-list-title"></div>` + html;
            }
        }

        getCurrentTrialProgress() {
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

        // ==================== Session切换 ====================

        /**
         * 切换Session
         */
        switchSession(sessionIndex) {
            if (sessionIndex < 0 || sessionIndex >= this.sessionCount) return;
            
            console.log('[Collection] 切换Session:', sessionIndex + 1);
            
            this.currentSessionIndex = sessionIndex;
            // 切换Session时重置Stage为第一个
            this.currentStageIndex = 0;
            this.currentGestureIndex = 0;
            this.gestureRepeatCount = 0;
            this.continualTrialCount = 0;
            
            // 重置动画模块的试次计数
            this.resetAnimationModules();
            
            this.updateSessionSelect();
            this.updateStageSelect();
            this.updateGestureList();
            this.updateNextStageButton();
            this.resetDisplay();
            
            // 发送session变更消息
            this.sendToRealtimeEngine('session_change', {
                sessionIndex: sessionIndex,
                sessionNumber: sessionIndex + 1
            });
            
            this.showToast(`已切换到 Session ${sessionIndex + 1}，请重新穿戴采集设备`, 'info');
        }

        /**
         * 获取当前Session索引
         */
        getCurrentSessionIndex() {
            return this.currentSessionIndex;
        }

        /**
         * 获取Session总数
         */
        getSessionCount() {
            return this.sessionCount;
        }

        // ==================== Stage切换 ====================

        /**
         * 【修复】切换Stage时重置动画模块状态，并加载该Stage的手势库
         */
        switchStage(stageIndex) {
            if (stageIndex < 0 || stageIndex >= this.stages.length) return;

            console.log('[Collection] 切换Stage:', stageIndex, this.stages[stageIndex]?.name);

            this.currentStageIndex = stageIndex;
            this.currentGestureIndex = 0;
            this.gestureRepeatCount = 0;
            this.continualTrialCount = 0;

            // 【新增】加载该Stage对应的手势库
            this.loadGesturesForCurrentStage();

            // 【关键修复】重置动画模块的试次计数
            this.resetAnimationModules();

            this.updateStageSelect();
            this.updateGestureList();
            this.updateNextStageButton();
            this.resetDisplay();

            this.sendToRealtimeEngine('stage_change', {
                stageIndex: stageIndex,
                stageName: this.stages[stageIndex]?.name || this.stages[stageIndex]?.id,
                sessionIndex: this.currentSessionIndex,
                sessionNumber: this.currentSessionIndex + 1
            });
        }

        /**
         * 【新增】加载当前Stage对应的手势库
         * 如果Stage有配置gestures字段，则只加载该Stage指定的手势
         * 否则加载全局手势库
         */
        loadGesturesForCurrentStage() {
            const currentStage = this.stages[this.currentStageIndex];
            const template = this.getLatestTemplate();

            // 获取全局手势库（优先使用已加载的allGestures）
            const allGestures = this.allGestures ||
                (template.gestures?.discrete || []).filter(g => g.enabled);

            if (currentStage?.gestures && currentStage.gestures.length > 0) {
                // Stage有自己的手势配置，根据ID筛选
                const stageGestureIds = currentStage.gestures;
                this.gestures = allGestures.filter(g => stageGestureIds.includes(g.id));
                // 按配置顺序排序
                this.gestures.sort((a, b) => {
                    return stageGestureIds.indexOf(a.id) - stageGestureIds.indexOf(b.id);
                });
                console.log(`[Collection] Stage "${currentStage.name}" 加载专属手势库: ${this.gestures.length}个`);
            } else {
                // Stage没有配置手势，使用全局手势库
                this.gestures = [...allGestures];
                console.log(`[Collection] Stage "${currentStage?.name}" 使用全局手势库: ${this.gestures.length}个`);
            }
        }

        goToNextStage() {
            if (this.currentStageIndex < this.stages.length - 1) {
                this.switchStage(this.currentStageIndex + 1);
                this.showToast(`已切换到Stage: ${this.stages[this.currentStageIndex]?.name}`, 'success');
            }
        }

        // ==================== 采集控制 ====================

        /**
         * 开始采集任务
         * @param {boolean} isTestMode - 是否为测试模式（不保存H5文件）
         */
        startTask(isTestMode = false) {
            if (this._isRunning) return;

            // 【新增】保存测试模式状态
            this._isTestMode = isTestMode;

            console.log('[Collection] ===== 开始采集任务 =====');
            console.log('[Collection] 任务类型:', this.currentTaskId);
            console.log('[Collection] 测试模式:', isTestMode ? '是（不保存H5文件）' : '否');
            console.log('[Collection] 当前Stage:', this.stages[this.currentStageIndex]?.name);

            // 【关键修复】重新从localStorage读取最新配置
            this.loadCollectionConfig();
            console.log('[Collection] ★★★ 重新加载配置后 ★★★');
            console.log('[Collection] currentExecutionParams:', this.currentExecutionParams);
            console.log('[Collection] trialsPerStage:', this.currentExecutionParams.trialsPerStage);

            // 【关键修复】重置动画模块状态
            this.resetAnimationModules();

            this._isRunning = true;
            this._isPaused = false;
            this.currentGestureIndex = 0;
            this.gestureRepeatCount = 0;
            this.continualTrialCount = 0;

            this.updateControlButtons(true);

            // 禁用Session和Stage选择器
            const sessionSelect = document.getElementById('sessionSwitchSelect');
            if (sessionSelect) sessionSelect.disabled = true;
            const stageSelect = document.getElementById('stageSwitchSelect');
            if (stageSelect) stageSelect.disabled = true;

            this.updateNextStageButton();

            const currentStage = this.stages[this.currentStageIndex];
            const userData = JSON.parse(localStorage.getItem('emg_current_user') || '{}');

            const userId = userData.id ||
                           this.collectionConfig?.subject?.id ||
                           `S${Date.now().toString().slice(-6)}`;

            this.sendToRealtimeEngine('collection_start', {
                taskId: this.currentTaskId,
                sessionIndex: this.currentSessionIndex,
                sessionNumber: this.currentSessionIndex + 1,
                sessionCount: this.sessionCount,
                stageName: currentStage?.name || currentStage?.id || 'stage_1',
                stageIndex: this.currentStageIndex,
                userId: userId,
                // 【新增】测试模式标志
                isTestMode: isTestMode,
                // 文件名建议格式: userId_session{N}_{stageName}_{timestamp}
                suggestedFileName: `${userId}_session${this.currentSessionIndex + 1}_${currentStage?.name || currentStage?.id || 'stage'}`,
                config: this.collectionConfig
            });

            if (this.currentTaskId === 'discrete_gesture') {
                this.startDiscreteGestureCollection();
            } else {
                this.startContinualGestureCollection();
            }
        }

        /**
         * 【新增】开始全部轮次采集
         * 始终从 Session 1 开始，自动循环完成所有轮次
         */
        startAllSessions() {
            if (this._isRunning) return;

            console.log('[Collection] ===== 开始全部轮次采集 =====');
            console.log('[Collection] 总Session数:', this.sessionCount);
            console.log('[Collection] 轮次间休息时间:', this._restBetweenSessions, '秒');

            // 【修复】始终从 Session 1 开始
            this.currentSessionIndex = 0;
            this.currentGestureIndex = 0;
            this.gestureRepeatCount = 0;
            this.continualTrialCount = 0;

            // 重置动画模块
            this.resetAnimationModules();

            // 更新UI显示
            this.updateSessionSelect();
            this.updateGestureList();

            console.log('[Collection] 重置到 Session 1 开始采集');

            // 设置全部轮次模式标志
            this._isAllSessionsMode = true;

            // 显示开始提示弹窗
            this.showSessionOverlay({
                title: '开始第 1 轮',
                subtitle: `共 ${this.sessionCount} 轮，准备开始采集...`,
                icon: '🚀',
                type: 'start'
            });

            // 2秒后隐藏弹窗并开始采集
            setTimeout(() => {
                this.hideSessionOverlay();
                this.startTask(false);
            }, 2000);
        }

        /**
         * 【新增】显示休息倒计时并继续下一轮
         */
        showRestCountdownAndContinue() {
            const nextSessionIndex = this.currentSessionIndex + 1;
            let remainingSeconds = this._restBetweenSessions;
            const totalSeconds = this._restBetweenSessions;

            console.log('[Collection] 开始休息倒计时:', remainingSeconds, '秒');
            console.log('[Collection] 下一轮Session:', nextSessionIndex + 1);

            // 【修改】使用全屏居中弹窗显示休息倒计时
            this.showSessionOverlay({
                title: '休息时间',
                subtitle: `第 ${this.currentSessionIndex + 1} 轮完成！\n即将开始第 ${nextSessionIndex + 1}/${this.sessionCount} 轮`,
                icon: '☕',
                countdown: remainingSeconds,
                type: 'rest'
            });

            this.updateStatus(`休息中 - ${remainingSeconds}秒后开始第${nextSessionIndex + 1}轮`);

            // 清理之前的定时器
            if (this._restCountdownTimer) {
                clearInterval(this._restCountdownTimer);
                this._restCountdownTimer = null;
            }

            this._restCountdownTimer = setInterval(() => {
                remainingSeconds--;

                // 更新全屏弹窗的倒计时
                this.updateSessionOverlayCountdown(remainingSeconds, totalSeconds);
                this.updateStatus(`休息中 - ${remainingSeconds}秒后开始第${nextSessionIndex + 1}轮`);

                if (remainingSeconds <= 0) {
                    clearInterval(this._restCountdownTimer);
                    this._restCountdownTimer = null;

                    // 隐藏休息弹窗
                    this.hideSessionOverlay();

                    // 切换到下一个Session并开始采集
                    this.currentSessionIndex = nextSessionIndex;
                    this.currentGestureIndex = 0;
                    this.gestureRepeatCount = 0;
                    this.continualTrialCount = 0;

                    // 重置动画模块
                    this.resetAnimationModules();

                    // 更新UI
                    this.updateSessionSelect();
                    this.updateGestureList();

                    console.log('[Collection] 休息结束，开始第', nextSessionIndex + 1, '轮采集');

                    // 【修改】显示开始新轮次的提示弹窗（短暂显示后自动开始）
                    this.showSessionOverlay({
                        title: `开始第 ${nextSessionIndex + 1} 轮`,
                        subtitle: `共 ${this.sessionCount} 轮，准备开始采集...`,
                        icon: '🚀',
                        type: 'start'
                    });

                    // 2秒后隐藏弹窗并开始采集
                    setTimeout(() => {
                        this.hideSessionOverlay();
                        this.startTask(false);
                    }, 2000);
                }
            }, 1000);
        }

        /**
         * 【新增】取消全部轮次采集模式
         */
        cancelAllSessionsMode() {
            this._isAllSessionsMode = false;
            if (this._restCountdownTimer) {
                clearInterval(this._restCountdownTimer);
                this._restCountdownTimer = null;
            }
            // 隐藏全屏弹窗
            this.hideSessionOverlay();
        }

        stopTask() {
            if (!this._isRunning && !this._isAllSessionsMode) return;

            console.log('[Collection] ===== 停止采集任务 =====');

            this._isRunning = false;
            this._isPaused = false;

            // 【新增】取消全部轮次模式
            this.cancelAllSessionsMode();

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

            // 清理标定状态
            if (this.calibrationTimer) {
                clearInterval(this.calibrationTimer);
                this.calibrationTimer = null;
            }
            this.isCalibrating = false;
            this.calibrationPhase = null;

            // 隐藏标定指导动画
            if (window.calibrationGuideAnimation) {
                window.calibrationGuideAnimation.hide();
            }

            // 【新增】隐藏手势示范 GIF
            this.hideGestureGif();

            if (window.discreteGestureAnimation) {
                window.discreteGestureAnimation.stop();
            }
            if (window.continualGesture1Animation) {
                window.continualGesture1Animation.stop();
            }
            if (window.continualGesture2Animation) {
                window.continualGesture2Animation.stop();
            }
            if (window.animationController) {
                window.animationController.stop();
            }

            // 重新启用Session和Stage选择器
            const sessionSelect = document.getElementById('sessionSwitchSelect');
            if (sessionSelect) sessionSelect.disabled = false;
            const stageSelect = document.getElementById('stageSwitchSelect');
            if (stageSelect) stageSelect.disabled = false;

            this.updateControlButtons(false);
            this.updateNextStageButton();
            this.updateGestureList();
            this.updateStatus('已停止');

            this.sendToRealtimeEngine('collection_stop', { completed: false });
        }

        // 【已移除】togglePause 方法已被测试模式替代

        isRunning() {
            return this._isRunning;
        }

        // ==================== 标定流程（单阶段） ====================

        /**
         * 启动标定流程
         * 流程：demo（可选）→ calibrate（受试者做完整动作范围）→ 完成
         */
        startCalibrationFlow() {
            console.log('[Collection] ====== 启动标定流程 ======');

            this.isCalibrating = true;
            this.currentPhase = 'calibration';
            this.updateGestureList();

            const inputInterface = window.animationInputInterface;
            if (inputInterface) {
                inputInterface.setCurrentTask(this.currentTaskId);
            }

            this.sendToRealtimeEngine('task_change', { taskId: this.currentTaskId });

            // 直接开始标定（跳过 demo）
            this.startCalibration();
        }

        /**
         * 开始标定（单阶段：受试者做完整动作范围）
         */
        startCalibration() {
            console.log('[Collection] 开始标定');

            this.calibrationPhase = 'calibrating';

            const inputInterface = window.animationInputInterface;
            const guideAnimation = window.calibrationGuideAnimation;

            // 开始记录数据
            if (inputInterface) {
                inputInterface.startCalibration(this.currentTaskId);
            }

            // 显示标定指导
            if (guideAnimation) {
                guideAnimation.show(
                    this.currentTaskId,
                    'calibrate',  // 单阶段标定
                    () => { this.endCalibration(); }
                );
            } else {
                // 没有指导动画时，使用简单倒计时
                this.showSimpleCalibrationCountdown('标定', () => { this.endCalibration(); });
            }
        }

        /**
         * 结束标定
         */
        endCalibration() {
            console.log('[Collection] 标定结束');

            const inputInterface = window.animationInputInterface;
            if (inputInterface) {
                const result = inputInterface.endCalibration();
                console.log('[Collection] 标定结果:', result);
            }

            this.onCalibrationComplete();
        }

        /**
         * 标定流程完成
         */
        onCalibrationComplete() {
            console.log('[Collection] ====== 标定流程完成 ======');

            this.isCalibrating = false;
            this.calibrationPhase = null;

            const inputInterface = window.animationInputInterface;
            if (inputInterface) {
                const status = inputInterface.getCalibrationStatus();
                console.log('[Collection] 标定状态:', status);

                // 【修复】不显示 toast，避免退出后仍然显示
                if (status.isCalibrated) {
                    console.log(`[Collection] 标定完成: min=${status.min?.toFixed(1)}, max=${status.max?.toFixed(1)}`);
                }
            }

            this.updateGestureDisplay({
                name: '标定完成',
                instruction: '即将开始正式采集...',
                showCountdown: false
            });

            setTimeout(() => {
                this.currentPhase = 'prepare';
                this.updateGestureList();
                this.showContinualPreparation(() => {
                    this.startContinualAnimation();
                });
            }, 1500);
        }

        /**
         * 显示简单的标定倒计时（当没有guideAnimation时使用）
         */
        showSimpleCalibrationCountdown(label, callback) {
            const duration = 10;

            this.updateGestureDisplay({
                name: `${label}中`,
                instruction: '请做完整的动作范围（从最小到最大）',
                showCountdown: true,
                countdownValue: duration
            });

            let countdown = duration;
            const countdownEl = document.getElementById('countdown');

            this.calibrationTimer = setInterval(() => {
                countdown--;
                if (countdownEl) countdownEl.textContent = countdown;

                if (countdown <= 0) {
                    clearInterval(this.calibrationTimer);
                    this.calibrationTimer = null;
                    if (countdownEl) countdownEl.style.display = 'none';
                    callback();
                }
            }, 1000);
        }

        /**
         * 跳过标定流程
         */
        skipCalibrationFlow() {
            console.log('[Collection] 跳过标定流程');

            if (this.calibrationTimer) {
                clearInterval(this.calibrationTimer);
                this.calibrationTimer = null;
            }

            const guideAnimation = window.calibrationGuideAnimation;
            if (guideAnimation) guideAnimation.hide();

            this.skipCalibration = true;
            this.isCalibrating = false;
            this.calibrationPhase = null;

            this.currentPhase = 'prepare';
            this.updateGestureList();
            this.showContinualPreparation(() => {
                this.startContinualAnimation();
            });
        }

        /**
         * 重置标定
         */
        resetCalibration() {
            const inputInterface = window.animationInputInterface;
            if (inputInterface) {
                inputInterface.resetCalibration(this.currentTaskId);
                this.showToast('标定已重置', 'info');
            }
            this.skipCalibration = false;
        }

        // ==================== 离散手势采集流程 ====================

        startDiscreteGestureCollection() {
            console.log('[Collection] 开始离散手势顺序采集');

            // 【修复】发送 stage_start 命令打开 H5 文件
            const currentStage = this.stages[this.currentStageIndex];
            this.sendToRealtimeEngine('stage_start', {
                stageName: currentStage?.name || currentStage?.id,
                stageIndex: this.currentStageIndex,
                timestamp: Date.now() / 1000  // 【修改】转换为秒，与ble_server时间戳一致
            });

            this.currentPhase = 'prepare';
            this.showPreparation(() => {
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
                this.onAllGesturesComplete();
                return;
            }

            const gesture = this.gestures[this.currentGestureIndex];
            console.log(`[Collection] 开始手势: ${gesture.name} (${this.currentGestureIndex + 1}/${this.gestures.length})`);

            this.currentPhase = 'gesture';
            this.gestureRepeatCount = 0;

            this.updateProgress();
            this.updateGestureList();

            // 【新增】显示手势示范 GIF
            this.showGestureGif(gesture);

            if (window.discreteGestureAnimation) {
                window.discreteGestureAnimation.startGesture(gesture, this.currentExecutionParams, () => {
                    this.onGestureAnimationComplete();
                });
            } else {
                this.doGestureRepeatSimple();
            }
        }

        onGestureAnimationComplete() {
            console.log(`[Collection] 手势 ${this.gestures[this.currentGestureIndex]?.name} 采集完成`);
            
            this.currentGestureIndex++;
            this.updateProgress();
            this.updateGestureList();
            
            if (this.currentGestureIndex >= this.gestures.length) {
                this.onAllGesturesComplete();
            } else {
                this.showRestPeriod(() => {
                    this.startNextGesture();
                });
            }
        }

        doGestureRepeatSimple() {
            if (!this._isRunning || this._isPaused) return;
            
            const gesture = this.gestures[this.currentGestureIndex];
            const repeatMax = this.currentExecutionParams.repeatPerGesture;
            
            if (this.gestureRepeatCount >= repeatMax) {
                this.onGestureAnimationComplete();
                return;
            }
            
            this.gestureRepeatCount++;
            console.log(`[Collection] 手势 ${gesture.name} - 第 ${this.gestureRepeatCount}/${repeatMax} 次`);
            
            this.sendToRealtimeEngine('prompt', {
                name: gesture.id || gesture.name,
                stageName: this.stages[this.currentStageIndex]?.name || this.stages[this.currentStageIndex]?.id,
                timestamp: Date.now() / 1000  // 【修改】转换为秒，与ble_server时间戳一致
            });
            
            this.updateGestureDisplay({
                name: `${gesture.icon || '✋'} ${gesture.name}`,
                instruction: `请执行手势动作 (${this.gestureRepeatCount}/${repeatMax})`,
                showCountdown: false
            });
            
            this.updateGestureList();
            
            const displayTime = this.currentExecutionParams.gestureDisplayTime * 1000;
            const intervalTime = this.currentExecutionParams.intervalBetweenRepeat * 1000;
            
            this.phaseTimer = setTimeout(() => {
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
            this.gestureRepeatCount = 0;
            
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

            // 【新增】隐藏手势示范 GIF
            this.hideGestureGif();

            const currentStage = this.stages[this.currentStageIndex];
            this.sendToRealtimeEngine('stage_end', {
                stageName: currentStage?.name || currentStage?.id
            });

            this.sendToRealtimeEngine('collection_stop', { completed: true });

            this._isRunning = false;

            // 重新启用Session和Stage选择器
            const sessionSelect = document.getElementById('sessionSwitchSelect');
            if (sessionSelect) sessionSelect.disabled = false;
            const stageSelect = document.getElementById('stageSwitchSelect');
            if (stageSelect) stageSelect.disabled = false;

            this.updateControlButtons(false);
            this.updateNextStageButton();
            this.updateGestureList();
            this.updateStatus('采集完成');

            // 【新增】全部轮次模式：检查是否需要继续下一轮
            if (this._isAllSessionsMode) {
                const hasMoreSessions = this.currentSessionIndex < this.sessionCount - 1;
                if (hasMoreSessions) {
                    // 还有更多轮次，显示休息倒计时后自动开始下一轮
                    this.showRestCountdownAndContinue();
                    return;
                } else {
                    // 所有轮次完成
                    this._isAllSessionsMode = false;

                    // 【修改】使用全屏弹窗显示完成信息
                    this.showSessionOverlay({
                        title: '全部轮次采集完成！',
                        subtitle: `已完成所有 ${this.sessionCount} 轮采集\n感谢您的配合！`,
                        icon: '🎉',
                        type: 'complete'
                    });

                    // 5秒后自动隐藏
                    setTimeout(() => {
                        this.hideSessionOverlay();
                    }, 5000);

                    this.updateGestureDisplay({
                        name: '🎉 全部轮次采集完成！',
                        instruction: `已完成所有 ${this.sessionCount} 轮采集`,
                        showCountdown: false
                    });
                    return;
                }
            }

            // 单轮模式：显示正常完成信息
            const hasMoreStages = this.currentStageIndex < this.stages.length - 1;
            const nextStageName = hasMoreStages ? this.stages[this.currentStageIndex + 1]?.name : '';

            this.updateGestureDisplay({
                name: '🎉 Stage采集完成！',
                instruction: hasMoreStages ?
                    `可以点击"进入下一Stage"继续采集: ${nextStageName}` :
                    '所有Stage已完成！',
                showCountdown: false
            });

            this.showToast('当前Stage采集完成！', 'success');
        }

        // ==================== 连续手势采集流程 ====================

        startContinualGestureCollection() {
            console.log('[Collection] 开始连续手势采集');
            console.log('[Collection] 任务类型:', this.currentTaskId);
            console.log('[Collection] ★★★ 执行参数 ★★★:', this.currentExecutionParams);

            const currentStage = this.stages[this.currentStageIndex];
            this.continualTrialCount = 0;

            // 【修改】每次采集前都重新标定（不使用缓存）
            const inputInterface = window.animationInputInterface;
            const needCalibration = this.calibrationEnabled !== false && inputInterface;

            if (needCalibration && !this.skipCalibration) {
                console.log('[Collection] 开始标定流程');
                // 重置标定状态，确保每次都重新标定
                if (inputInterface) {
                    inputInterface.resetCalibration(this.currentTaskId);
                }
                this.startCalibrationFlow();
            } else {
                console.log('[Collection] 跳过标定，直接开始采集');
                this.currentPhase = 'prepare';
                this.updateGestureList();

                this.showContinualPreparation(() => {
                    this.startContinualAnimation();
                });
            }
        }

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
         * 【关键修复】启动连续手势动画 - 传递执行参数给动画模块
         */
        startContinualAnimation() {
            console.log('[Collection] ====== 启动连续手势动画 ======');
            console.log('[Collection] 任务类型:', this.currentTaskId);
            console.log('[Collection] ★★★ trialsPerStage:', this.currentExecutionParams.trialsPerStage);

            const currentStage = this.stages[this.currentStageIndex];
            this.currentPhase = 'continual';

            this.updateGestureDisplay({
                name: '光标移动任务',
                instruction: '请通过滚轮移动光标到目标位置',
                showCountdown: false
            });

            this.updateGestureList();

            // 【新增】显示连续手势的 GIF 示范
            this.showContinualGestureGif();

            const stageConfig = {
                name: currentStage?.name || currentStage?.id || 'stage',
                label: currentStage?.name || 'Stage',
                instruction: currentStage?.instruction || '请将光标移动到目标区域'
            };
            
            let animationModule = null;
            if (this.currentTaskId === 'continual_gesture_1') {
                animationModule = window.continualGesture1Animation;
            } else if (this.currentTaskId === 'continual_gesture_2') {
                animationModule = window.continualGesture2Animation;
            } else if (this.currentTaskId === 'continual_gesture_3') {
                animationModule = window.continualGesture3Animation;
            }
            
            if (animationModule) {
                // 【关键修复】传递执行参数给动画模块的start函数
                console.log('[Collection] 传递执行参数给动画模块:', this.currentExecutionParams);

                // 【修复】发送 stage_start 命令打开 H5 文件
                this.sendToRealtimeEngine('stage_start', {
                    stageName: currentStage?.name || currentStage?.id,
                    stageIndex: this.currentStageIndex,
                    timestamp: Date.now()
                });

                this.setupContinualProgressUpdater(animationModule);

                // 调用start时传入executionParams
                animationModule.start(
                    stageConfig,
                    () => {
                        this.onContinualAnimationComplete();
                    },
                    (trialIndex) => {
                        this.onContinualTrialComplete(trialIndex);
                    },
                    this.currentExecutionParams  // 【关键】传入执行参数
                );

                console.log('[Collection] 连续手势动画已启动');
                console.log('[Collection] 确认 maxTrials =', animationModule.maxTrials);
            } else {
                console.error('[Collection] 未找到连续手势动画模块:', this.currentTaskId);
                this.showToast('动画模块未加载', 'error');
                this.stopTask();
            }

            this.updateStatus('采集中');
        }

        setupContinualProgressUpdater(animationModule) {
            this.continualProgressTimer = setInterval(() => {
                if (this._isRunning && animationModule.isAnimationRunning()) {
                    this.updateGestureList();
                } else {
                    clearInterval(this.continualProgressTimer);
                    this.continualProgressTimer = null;
                }
            }, 500);
        }

        onContinualTrialComplete(trialIndex) {
            this.continualTrialCount = trialIndex + 1;
            console.log(`[Collection] 试次完成: ${this.continualTrialCount}`);
            
            this.sendToRealtimeEngine('trial_complete', {
                trialIndex: trialIndex,
                stageName: this.stages[this.currentStageIndex]?.name
            });
            
            this.updateGestureList();
        }

        onContinualAnimationComplete() {
            console.log('[Collection] 连续手势动画完成');
            
            if (this.continualProgressTimer) {
                clearInterval(this.continualProgressTimer);
                this.continualProgressTimer = null;
            }
            
            this.onContinualStageComplete();
        }

        onContinualStageComplete() {
            console.log('[Collection] 连续手势Stage完成');

            // 【新增】隐藏手势示范 GIF
            this.hideGestureGif();

            const currentStage = this.stages[this.currentStageIndex];
            this.sendToRealtimeEngine('stage_end', {
                stageName: currentStage?.name || currentStage?.id
            });

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

            // 重新启用Session和Stage选择器
            const sessionSelect = document.getElementById('sessionSwitchSelect');
            if (sessionSelect) sessionSelect.disabled = false;
            const stageSelect = document.getElementById('stageSwitchSelect');
            if (stageSelect) stageSelect.disabled = false;

            this.updateControlButtons(false);
            this.updateNextStageButton();
            this.updateGestureList();
            this.updateStatus('采集完成');  // 【修复】更新状态显示
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
                    timestamp: Date.now() / 1000  // 【修改】转换为秒，与ble_server时间戳一致
                });
                ws.send(message);
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
            const testBtn = document.getElementById('testModeBtn');
            const stopBtn = document.getElementById('stopTaskBtn');

            if (startBtn) startBtn.disabled = running;
            if (testBtn) testBtn.disabled = running;  // 测试按钮在运行时禁用
            if (stopBtn) stopBtn.disabled = !running;
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

        // ==================== 全屏Session提示弹窗 ====================

        /**
         * 【新增】显示全屏居中的Session提示弹窗
         * @param {Object} options - 配置选项
         * @param {string} options.title - 标题
         * @param {string} options.subtitle - 副标题
         * @param {string} options.icon - 图标（emoji）
         * @param {number} options.countdown - 倒计时秒数（可选）
         * @param {string} options.type - 类型：'rest' | 'start' | 'complete'
         */
        showSessionOverlay(options) {
            const { title, subtitle, icon = '⏸️', countdown, type = 'rest' } = options;

            // 移除已存在的弹窗
            this.hideSessionOverlay();

            // 根据类型选择颜色
            const colorMap = {
                'rest': { bg: '#3b82f6', light: '#dbeafe' },
                'start': { bg: '#22c55e', light: '#dcfce7' },
                'complete': { bg: '#f59e0b', light: '#fef3c7' }
            };
            const colors = colorMap[type] || colorMap.rest;

            // 创建全屏遮罩和弹窗
            const overlay = document.createElement('div');
            overlay.id = 'sessionOverlay';
            overlay.style.cssText = `
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background: rgba(0, 0, 0, 0.5);
                display: flex;
                align-items: center;
                justify-content: center;
                z-index: 10000;
                animation: fadeIn 0.3s ease;
            `;

            overlay.innerHTML = `
                <style>
                    @keyframes fadeIn {
                        from { opacity: 0; }
                        to { opacity: 1; }
                    }
                    @keyframes scaleIn {
                        from { transform: scale(0.8); opacity: 0; }
                        to { transform: scale(1); opacity: 1; }
                    }
                    @keyframes pulse {
                        0%, 100% { transform: scale(1); }
                        50% { transform: scale(1.05); }
                    }
                </style>
                <div id="sessionOverlayPanel" style="
                    background: white;
                    border-radius: 24px;
                    padding: 48px 64px;
                    text-align: center;
                    box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
                    min-width: 500px;
                    max-width: 700px;
                    animation: scaleIn 0.3s ease;
                ">
                    <!-- 图标 -->
                    <div style="
                        font-size: 80px;
                        margin-bottom: 24px;
                        animation: pulse 2s infinite;
                    ">${icon}</div>

                    <!-- 标题 -->
                    <div style="
                        font-size: 42px;
                        font-weight: 700;
                        color: ${colors.bg};
                        margin-bottom: 16px;
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    ">${title}</div>

                    <!-- 副标题 -->
                    <div style="
                        font-size: 24px;
                        color: #6b7280;
                        margin-bottom: 32px;
                        line-height: 1.5;
                    ">${subtitle}</div>

                    <!-- 倒计时（如果有） -->
                    ${countdown !== undefined ? `
                    <div style="
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        gap: 16px;
                        padding: 24px;
                        background: ${colors.light};
                        border-radius: 16px;
                    ">
                        <span style="font-size: 20px; color: #6b7280;">倒计时</span>
                        <span id="sessionOverlayCountdown" style="
                            font-size: 72px;
                            font-weight: 800;
                            color: ${colors.bg};
                            min-width: 100px;
                            line-height: 1;
                        ">${countdown}</span>
                        <span style="font-size: 24px; color: #6b7280;">秒</span>
                    </div>
                    ` : ''}

                    <!-- 进度条（如果有倒计时） -->
                    ${countdown !== undefined ? `
                    <div style="
                        margin-top: 24px;
                        background: #e5e7eb;
                        border-radius: 8px;
                        height: 8px;
                        overflow: hidden;
                    ">
                        <div id="sessionOverlayProgress" style="
                            height: 100%;
                            background: linear-gradient(90deg, ${colors.bg}, ${colors.bg}88);
                            border-radius: 8px;
                            width: 100%;
                            transition: width 1s linear;
                        "></div>
                    </div>
                    ` : ''}
                </div>
            `;

            document.body.appendChild(overlay);
        }

        /**
         * 【新增】更新Session弹窗的倒计时
         * @param {number} seconds - 剩余秒数
         * @param {number} total - 总秒数
         */
        updateSessionOverlayCountdown(seconds, total) {
            const countdownEl = document.getElementById('sessionOverlayCountdown');
            const progressEl = document.getElementById('sessionOverlayProgress');

            if (countdownEl) {
                countdownEl.textContent = seconds;
                // 最后3秒变红色
                if (seconds <= 3) {
                    countdownEl.style.color = '#ef4444';
                }
            }

            if (progressEl && total > 0) {
                const percent = (seconds / total) * 100;
                progressEl.style.width = `${percent}%`;
            }
        }

        /**
         * 【新增】隐藏Session弹窗
         */
        hideSessionOverlay() {
            const overlay = document.getElementById('sessionOverlay');
            if (overlay) {
                overlay.remove();
            }
        }

        // ==================== GIF 手势示范 ====================

        /**
         * 获取当前任务类型对应的 GIF 目录
         */
        getGifDirectory() {
            const dirMap = {
                'discrete_gesture': 'discrete',
                'continual_gesture_1': 'continual_1',
                'continual_gesture_2': 'continual_2',
                'continual_gesture_3': 'continual_3'
            };
            return dirMap[this.currentTaskId] || 'discrete';
        }

        /**
         * 显示手势示范 GIF
         * @param {Object} gesture - 手势对象，包含 gifFile 字段
         */
        showGestureGif(gesture) {
            const container = document.getElementById('gestureGifContainer');
            const image = document.getElementById('gestureGifImage');
            const placeholder = document.getElementById('gestureGifPlaceholder');

            if (!container || !image || !placeholder) {
                console.warn('[Collection] GIF 显示元素未找到');
                return;
            }

            // 获取 GIF 文件路径
            const gifFile = gesture?.gifFile;
            if (!gifFile) {
                // 没有配置 GIF 文件，显示占位符
                image.classList.remove('loaded');
                placeholder.classList.remove('hidden');
                placeholder.querySelector('span').textContent = gesture?.name || '无示范';
                container.classList.add('active');
                console.log('[Collection] 手势无 GIF 配置:', gesture?.name);
                return;
            }

            const gifDir = this.getGifDirectory();
            const gifPath = `tutorial/gestures/${gifDir}/${gifFile}`;

            console.log('[Collection] 显示 GIF:', gifPath);

            // 加载 GIF
            image.onload = () => {
                image.classList.add('loaded');
                placeholder.classList.add('hidden');
            };

            image.onerror = () => {
                console.warn('[Collection] GIF 加载失败:', gifPath);
                image.classList.remove('loaded');
                placeholder.classList.remove('hidden');
                placeholder.querySelector('span').textContent = '加载失败';
            };

            image.src = gifPath;
            container.classList.add('active');
        }

        /**
         * 隐藏手势示范 GIF
         */
        hideGestureGif() {
            const container = document.getElementById('gestureGifContainer');
            const image = document.getElementById('gestureGifImage');

            if (container) {
                container.classList.remove('active');
            }

            if (image) {
                image.classList.remove('loaded');
                image.src = '';
            }

            console.log('[Collection] 隐藏 GIF 显示');
        }

        /**
         * 显示连续手势的 GIF 示范
         * 连续手势每个任务类型只有一个 GIF
         */
        showContinualGestureGif() {
            // 任务ID到手势配置key的映射
            const gestureKeyMap = {
                'continual_gesture_1': 'continual_1',
                'continual_gesture_2': 'continual_2',
                'continual_gesture_3': 'continual_3'
            };

            const gestureKey = gestureKeyMap[this.currentTaskId];
            if (!gestureKey) {
                console.log('[Collection] 非连续手势任务，跳过 GIF 显示');
                return;
            }

            // 从 collectionConfig 中获取连续手势的 GIF 配置
            const gestureConfig = this.collectionConfig?.gestures?.[gestureKey];

            if (gestureConfig && gestureConfig.length > 0 && gestureConfig[0].gifFile) {
                console.log('[Collection] 显示连续手势 GIF:', gestureKey, gestureConfig[0].gifFile);
                this.showGestureGif(gestureConfig[0]);
            } else {
                // 尝试从 template 中读取
                const template = this.getLatestTemplate();
                const templateGesture = template?.gestures?.[gestureKey];

                if (templateGesture && templateGesture.length > 0 && templateGesture[0].gifFile) {
                    console.log('[Collection] 从 template 显示连续手势 GIF:', gestureKey, templateGesture[0].gifFile);
                    this.showGestureGif(templateGesture[0]);
                } else {
                    console.log('[Collection] 连续手势无 GIF 配置:', gestureKey);
                }
            }
        }

        // ==================== 外部接口 ====================

        selectTask(htmlTaskId) {
            const taskIdMap = {
                'discrete': 'discrete_gesture',
                'continuous1': 'continual_gesture_1',
                'continuous2': 'continual_gesture_2',
                'continuous3': 'continual_gesture_3'
            };
            
            this.currentTaskId = taskIdMap[htmlTaskId] || htmlTaskId;
            console.log('[Collection] 设置任务类型:', this.currentTaskId);
            
            // 重新加载配置
            this.loadCollectionConfig();
            
            if (window.animationController) {
                window.animationController.setCurrentTask(this.currentTaskId);
            }
            
            // 重置动画模块
            this.resetAnimationModules();
            
            this.updateUI();
        }

        getCurrentTaskId() {
            return this.currentTaskId;
        }
        
        getCurrentExecutionParams() {
            return this.currentExecutionParams;
        }
    }

    // ==================== 初始化 ====================
    console.log('[Collection] 准备初始化控制器 (v3-fixed-v3)');
    
    function initController() {
        console.log('[Collection] ====== 开始初始化控制器 (v3-fixed-v3) ======');
        
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

    console.log('[Collection] 脚本主体执行完毕 (v3-fixed-v3)');

})();

console.log('[Collection] ====== 脚本加载结束 (v3-fixed-v3) ======');
