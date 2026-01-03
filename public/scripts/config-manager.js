/**
 * config-manager.js - 采集任务配置管理器（纯浏览器版）
 * 
 * 功能：
 * 1. 通过文件选择对话框加载本地JSON配置文件
 * 2. 验证配置文件格式
 * 3. 预览当前配置
 * 4. 将配置应用到采集系统
 * 
 * 使用方式：
 * - npm start 打开网页即可使用
 * - 点击"加载"按钮选择本地JSON配置文件
 * - 不需要Electron，纯浏览器环境支持
 */

(function() {
    'use strict';

    console.log('[ConfigManager] ====== 模块开始加载 ======');

    class ConfigManager {
        constructor() {
            this.currentConfig = null;
            this.configFileName = null;
            this.isLoaded = false;
        }

        /**
         * 初始化配置管理器
         */
        init() {
            console.log('[ConfigManager] 初始化开始');
            
            // 加载内置默认配置
            this.loadDefaultConfig();
            
            // 绑定UI事件
            this.bindEvents();
            
            // 更新UI状态
            this.updateConfigStatus();
            
            console.log('[ConfigManager] 初始化完成 ✓');
        }

        /**
         * 加载内置默认配置（从现有的全局变量构建）
         */
        loadDefaultConfig() {
            this.currentConfig = this.buildConfigFromGlobals();
            this.configFileName = '内置默认';
            this.isLoaded = true;
            console.log('[ConfigManager] 默认配置已加载');
        }

        /**
         * 从现有的全局常量构建配置对象
         */
        buildConfigFromGlobals() {
            const config = {
                configVersion: '1.0.0',
                configName: '内置默认配置',
                description: '系统内置的标准采集配置',

                globalSettings: {
                    intro: {
                        duration: window.COLLECTION_CONSTANTS?.INTRO?.DURATION || 10000,
                        type: window.COLLECTION_CONSTANTS?.INTRO?.TYPE || 'countdown'
                    },
                    stagePrepare: {
                        countdownSeconds: window.COLLECTION_CONSTANTS?.STAGE_PREPARE?.COUNTDOWN_SECONDS || 3
                    },
                    debug: {
                        enabled: window.COLLECTION_CONSTANTS?.DEBUG?.ENABLED || false,
                        fastMode: window.COLLECTION_CONSTANTS?.DEBUG?.FAST_MODE || false
                    }
                },

                promptLibrary: window.DISCRETE_GESTURE_CONFIG?.PROMPT_LIBRARY || {},
                tasks: {},
                taskIdMap: window.TaskConfig?.ID_MAP || {
                    'discrete': 'discrete_gesture',
                    'continuous1': 'continual_gesture_1',
                    'continuous2': 'continual_gesture_2'
                }
            };

            // 构建任务配置
            if (window.DISCRETE_GESTURE_CONFIG) {
                config.tasks.discrete_gesture = this.extractTaskConfig('discrete_gesture', window.DISCRETE_GESTURE_CONFIG);
            }
            if (window.CONTINUAL_GESTURE_1_CONFIG) {
                config.tasks.continual_gesture_1 = this.extractTaskConfig('continual_gesture_1', window.CONTINUAL_GESTURE_1_CONFIG);
            }
            if (window.CONTINUAL_GESTURE_2_CONFIG) {
                config.tasks.continual_gesture_2 = this.extractTaskConfig('continual_gesture_2', window.CONTINUAL_GESTURE_2_CONFIG);
            }

            return config;
        }

        /**
         * 从全局配置对象提取任务配置
         */
        extractTaskConfig(taskId, sourceConfig) {
            const taskConfig = {
                id: taskId,
                name: sourceConfig.NAME || taskId,
                taskType: sourceConfig.TASK_TYPE || 'prompt_sequence',
                stages: []
            };

            if (sourceConfig.WHEEL_TASK) {
                taskConfig.wheelTaskConfig = { ...sourceConfig.WHEEL_TASK };
            }

            if (sourceConfig.STAGES) {
                for (const [stageName, stageData] of Object.entries(sourceConfig.STAGES)) {
                    taskConfig.stages.push({
                        name: stageData.name || stageName,
                        label: stageData.label,
                        instruction: stageData.instruction,
                        icon: stageData.icon,
                        color: stageData.color,
                        promptSequence: stageData.promptSequence || [],
                        maxTrials: stageData.maxTrials,
                        timeout: stageData.timeout
                    });
                }
            }

            return taskConfig;
        }

        /**
         * 绑定UI事件
         */
        bindEvents() {
            // 加载配置按钮 - 改为显示配置文件列表弹窗
            const loadBtn = document.getElementById('loadConfigBtn');
            if (loadBtn) {
                loadBtn.addEventListener('click', () => this.showConfigSelectModal());
            }

            // 预览配置按钮
            const previewBtn = document.getElementById('previewConfigBtn');
            if (previewBtn) {
                previewBtn.addEventListener('click', () => this.showPreviewModal());
            }

            // 文件输入变化事件（保留作为备用）
            const fileInput = document.getElementById('configFileInput');
            if (fileInput) {
                fileInput.addEventListener('change', (e) => this.handleFileSelect(e));
            }

            // 弹窗关闭按钮
            document.querySelectorAll('.config-modal-close').forEach(btn => {
                btn.addEventListener('click', () => this.closeModal());
            });

            // 点击遮罩关闭
            const modal = document.getElementById('configPreviewModal');
            if (modal) {
                modal.addEventListener('click', (e) => {
                    if (e.target === modal) {
                        this.closeModal();
                    }
                });
            }

            // 配置选择弹窗遮罩关闭
            const selectModal = document.getElementById('configSelectModal');
            if (selectModal) {
                selectModal.addEventListener('click', (e) => {
                    if (e.target === selectModal) {
                        this.closeConfigSelectModal();
                    }
                });
            }

            console.log('[ConfigManager] 事件绑定完成');
        }

        /**
         * 显示配置文件选择弹窗
         */
        async showConfigSelectModal() {
            // 创建弹窗（如果不存在）
            let modal = document.getElementById('configSelectModal');
            if (!modal) {
                modal = this.createConfigSelectModal();
                document.body.appendChild(modal);
            }

            // 显示弹窗
            modal.classList.add('visible');

            // 加载配置文件列表
            await this.loadConfigFileList();
        }

        /**
         * 创建配置文件选择弹窗
         */
        createConfigSelectModal() {
            const modal = document.createElement('div');
            modal.id = 'configSelectModal';
            modal.className = 'config-select-modal';
            modal.innerHTML = `
                <div class="config-select-content">
                    <div class="config-select-header">
                        <h3><i class="fas fa-folder-open"></i> 选择配置文件</h3>
                        <button class="config-select-close" onclick="window.configManager.closeConfigSelectModal()">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="config-select-body">
                        <div class="config-file-path">
                            <i class="fas fa-folder"></i>
                            <span>config/</span>
                        </div>
                        <div class="config-file-list" id="configFileList">
                            <div class="config-file-loading">
                                <i class="fas fa-spinner fa-spin"></i> 加载中...
                            </div>
                        </div>
                    </div>
                </div>
            `;
            return modal;
        }

        /**
         * 加载配置文件列表
         */
        async loadConfigFileList() {
            const listContainer = document.getElementById('configFileList');
            if (!listContainer) return;

            listContainer.innerHTML = '<div class="config-file-loading"><i class="fas fa-spinner fa-spin"></i> 加载中...</div>';

            try {
                const response = await fetch('/api/config/files');
                const data = await response.json();

                if (data.success && data.files.length > 0) {
                    listContainer.innerHTML = data.files.map(file => `
                        <div class="config-file-item" data-filename="${file.name}">
                            <div class="config-file-icon">
                                <i class="fas fa-file-code"></i>
                            </div>
                            <div class="config-file-info">
                                <div class="config-file-name">${file.name}</div>
                                <div class="config-file-meta">
                                    <span>${this.formatFileSize(file.size)}</span>
                                    <span>${this.formatDateTime(new Date(file.lastModified))}</span>
                                </div>
                            </div>
                        </div>
                    `).join('');

                    // 绑定点击事件
                    listContainer.querySelectorAll('.config-file-item').forEach(item => {
                        item.addEventListener('click', () => {
                            const filename = item.dataset.filename;
                            this.loadConfigFromServer(filename);
                        });
                    });
                } else if (data.success && data.files.length === 0) {
                    listContainer.innerHTML = `
                        <div class="config-file-empty">
                            <i class="fas fa-folder-open"></i>
                            <p>config/ 目录中没有 JSON 配置文件</p>
                        </div>
                    `;
                } else {
                    listContainer.innerHTML = `
                        <div class="config-file-error">
                            <i class="fas fa-exclamation-triangle"></i>
                            <p>${data.error}</p>
                        </div>
                    `;
                }
            } catch (err) {
                listContainer.innerHTML = `
                    <div class="config-file-error">
                        <i class="fas fa-exclamation-triangle"></i>
                        <p>无法连接到服务器</p>
                    </div>
                `;
            }
        }

        /**
         * 从服务端加载配置文件
         */
        async loadConfigFromServer(filename) {
            console.log('[ConfigManager] 从服务端加载配置:', filename);

            try {
                const response = await fetch(`/api/config/load/${encodeURIComponent(filename)}`);
                const data = await response.json();

                if (data.success) {
                    // 验证配置
                    const validation = this.validateConfig(data.config);
                    if (!validation.valid) {
                        this.showToast(`配置验证失败: ${validation.errors[0]}`, 'error');
                        return;
                    }

                    // 应用配置
                    this.currentConfig = data.config;
                    this.configFileName = filename;
                    this.isLoaded = true;

                    // 应用到采集系统
                    this.applyConfigToSystem(data.config);

                    // 更新UI
                    this.updateConfigStatus();

                    // 关闭选择弹窗
                    this.closeConfigSelectModal();

                    this.showToast(`配置 "${filename}" 加载成功！`, 'success');
                    console.log('[ConfigManager] 配置加载成功:', data.config.configName);
                } else {
                    this.showToast(`加载失败: ${data.error}`, 'error');
                }
            } catch (err) {
                this.showToast('加载配置文件失败', 'error');
                console.error('[ConfigManager] 加载失败:', err);
            }
        }

        /**
         * 关闭配置选择弹窗
         */
        closeConfigSelectModal() {
            const modal = document.getElementById('configSelectModal');
            if (modal) {
                modal.classList.remove('visible');
            }
        }

        /**
         * 格式化文件大小
         */
        formatFileSize(bytes) {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
        }

        /**
         * 格式化日期时间
         */
        formatDateTime(date) {
            const month = String(date.getMonth() + 1).padStart(2, '0');
            const day = String(date.getDate()).padStart(2, '0');
            const hour = String(date.getHours()).padStart(2, '0');
            const min = String(date.getMinutes()).padStart(2, '0');
            return `${month}-${day} ${hour}:${min}`;
        }

        /**
         * 处理文件选择
         */
        handleFileSelect(event) {
            const file = event.target.files[0];
            if (!file) return;

            // 检查文件类型
            if (!file.name.endsWith('.json')) {
                this.showToast('请选择JSON格式的配置文件', 'error');
                return;
            }

            console.log('[ConfigManager] 选择的文件:', file.name);

            const reader = new FileReader();
            
            reader.onload = (e) => {
                try {
                    const content = e.target.result;
                    const config = JSON.parse(content);
                    
                    // 验证配置
                    const validation = this.validateConfig(config);
                    if (!validation.valid) {
                        this.showToast(`配置验证失败: ${validation.errors[0]}`, 'error');
                        return;
                    }

                    // 应用配置
                    this.currentConfig = config;
                    this.configFileName = file.name;
                    this.isLoaded = true;

                    // 应用到采集系统
                    this.applyConfigToSystem(config);

                    // 更新UI
                    this.updateConfigStatus();

                    this.showToast(`配置加载成功: ${config.configName || file.name}`, 'success');
                    console.log('[ConfigManager] 配置已加载:', config);

                } catch (error) {
                    console.error('[ConfigManager] 解析配置错误:', error);
                    this.showToast('配置文件格式错误，请检查JSON语法', 'error');
                }
            };

            reader.onerror = () => {
                this.showToast('读取文件失败', 'error');
            };

            reader.readAsText(file);

            // 清空文件输入，允许重复选择同一文件
            event.target.value = '';
        }

        /**
         * 验证配置文件格式
         */
        validateConfig(config) {
            const errors = [];

            if (!config.tasks || typeof config.tasks !== 'object') {
                errors.push('缺少tasks配置');
            }

            if (!config.globalSettings) {
                errors.push('缺少globalSettings配置');
            }

            if (config.tasks) {
                for (const [taskId, taskConfig] of Object.entries(config.tasks)) {
                    if (!taskConfig.stages || !Array.isArray(taskConfig.stages)) {
                        errors.push(`任务 ${taskId} 缺少stages数组`);
                    }
                    if (!taskConfig.taskType) {
                        errors.push(`任务 ${taskId} 缺少taskType`);
                    }
                }
            }

            return {
                valid: errors.length === 0,
                errors: errors
            };
        }

        /**
         * 将配置应用到采集系统
         */
        applyConfigToSystem(config) {
            console.log('[ConfigManager] 应用配置到系统...');

            // 更新 COLLECTION_CONSTANTS
            if (config.globalSettings && window.COLLECTION_CONSTANTS) {
                const gs = config.globalSettings;
                if (gs.intro) {
                    window.COLLECTION_CONSTANTS.INTRO.DURATION = gs.intro.duration || 10000;
                    window.COLLECTION_CONSTANTS.INTRO.TYPE = gs.intro.type || 'countdown';
                }
                if (gs.stagePrepare) {
                    window.COLLECTION_CONSTANTS.STAGE_PREPARE.COUNTDOWN_SECONDS = gs.stagePrepare.countdownSeconds || 3;
                }
                if (gs.debug) {
                    window.COLLECTION_CONSTANTS.DEBUG.ENABLED = gs.debug.enabled || false;
                    window.COLLECTION_CONSTANTS.DEBUG.FAST_MODE = gs.debug.fastMode || false;
                }
            }

            // 更新 promptLibrary
            if (config.promptLibrary && window.DISCRETE_GESTURE_CONFIG) {
                window.DISCRETE_GESTURE_CONFIG.PROMPT_LIBRARY = config.promptLibrary;
            }

            // 更新各任务配置
            if (config.tasks.discrete_gesture) {
                this.applyTaskConfig(config.tasks.discrete_gesture, window.DISCRETE_GESTURE_CONFIG);
            }
            if (config.tasks.continual_gesture_1) {
                this.applyTaskConfig(config.tasks.continual_gesture_1, window.CONTINUAL_GESTURE_1_CONFIG);
            }
            if (config.tasks.continual_gesture_2) {
                this.applyTaskConfig(config.tasks.continual_gesture_2, window.CONTINUAL_GESTURE_2_CONFIG);
            }

            // 更新 TaskConfig
            if (window.TaskConfig && config.tasks) {
                for (const [taskId, taskConfig] of Object.entries(config.tasks)) {
                    window.TaskConfig.DEFINITIONS[taskId] = {
                        id: taskId,
                        name: taskConfig.name,
                        description: taskConfig.description || '',
                        icon: taskConfig.icon,
                        taskType: taskConfig.taskType,
                        stages: taskConfig.stages.map(stage => ({
                            name: stage.name,
                            label: stage.label,
                            instruction: stage.instruction,
                            icon: stage.icon,
                            color: stage.color,
                            maxTrials: stage.maxTrials,
                            timeout: stage.timeout
                        }))
                    };
                }
            }

            // 通知采集控制器更新UI
            if (window.collectionController) {
                window.collectionController.updateStageList();
            }

            console.log('[ConfigManager] 配置已应用到系统 ✓');
        }

        /**
         * 应用单个任务的配置
         */
        applyTaskConfig(taskConfig, targetConfig) {
            if (!targetConfig) return;

            // 更新滚轮任务参数
            if (taskConfig.wheelTaskConfig && targetConfig.WHEEL_TASK) {
                Object.assign(targetConfig.WHEEL_TASK, {
                    MAX_TRIALS: taskConfig.wheelTaskConfig.maxTrials,
                    STAGE_TIMEOUT: taskConfig.wheelTaskConfig.stageTimeout,
                    DWELL_MS: taskConfig.wheelTaskConfig.dwellMs,
                    TARGET_FRAC: taskConfig.wheelTaskConfig.targetFrac,
                    MIN_TARGET_DISTANCE: taskConfig.wheelTaskConfig.minTargetDistance
                });
            }

            // 更新stages（转换数组格式回对象格式）
            if (taskConfig.stages && targetConfig.STAGES) {
                const newStages = {};
                taskConfig.stages.forEach(stage => {
                    newStages[stage.name] = {
                        name: stage.name,
                        label: stage.label,
                        instruction: stage.instruction,
                        icon: stage.icon,
                        color: stage.color,
                        promptSequence: stage.promptSequence || [],
                        maxTrials: stage.maxTrials,
                        timeout: stage.timeout
                    };
                });
                targetConfig.STAGES = newStages;
            }

            // 更新动画配置
            if (taskConfig.animation && targetConfig.ANIMATION) {
                Object.assign(targetConfig.ANIMATION, taskConfig.animation);
            }
        }

        /**
         * 显示预览弹窗
         */
        showPreviewModal() {
            const modal = document.getElementById('configPreviewModal');
            const content = document.getElementById('configPreviewContent');
            
            if (!modal || !content) {
                console.error('[ConfigManager] 预览弹窗元素未找到');
                return;
            }

            content.innerHTML = this.generatePreviewHTML();
            modal.classList.add('visible');
        }

        /**
         * 关闭弹窗
         */
        closeModal() {
            const modal = document.getElementById('configPreviewModal');
            if (modal) {
                modal.classList.remove('visible');
            }
        }

        /**
         * 生成配置预览HTML
         */
        generatePreviewHTML() {
            const config = this.currentConfig;
            if (!config) {
                return '<div class="preview-empty">暂无配置信息</div>';
            }

            let html = `
                <div class="preview-section">
                    <h3 class="preview-section-title">
                        <i class="fas fa-info-circle"></i> 基本信息
                    </h3>
                    <div class="preview-grid">
                        <div class="preview-item">
                            <span class="preview-label">配置名称</span>
                            <span class="preview-value">${config.configName || '未命名'}</span>
                        </div>
                        <div class="preview-item">
                            <span class="preview-label">版本</span>
                            <span class="preview-value">${config.configVersion || '1.0.0'}</span>
                        </div>
                        <div class="preview-item">
                            <span class="preview-label">来源文件</span>
                            <span class="preview-value">${this.configFileName || '内置默认'}</span>
                        </div>
                        <div class="preview-item">
                            <span class="preview-label">描述</span>
                            <span class="preview-value">${config.description || '无'}</span>
                        </div>
                    </div>
                </div>

                <div class="preview-section">
                    <h3 class="preview-section-title">
                        <i class="fas fa-cog"></i> 全局设置
                    </h3>
                    <div class="preview-grid">
                        <div class="preview-item">
                            <span class="preview-label">开场动画时长</span>
                            <span class="preview-value">${(config.globalSettings?.intro?.duration || 10000) / 1000}秒</span>
                        </div>
                        <div class="preview-item">
                            <span class="preview-label">Stage准备时间</span>
                            <span class="preview-value">${config.globalSettings?.stagePrepare?.countdownSeconds || 3}秒</span>
                        </div>
                        <div class="preview-item">
                            <span class="preview-label">调试模式</span>
                            <span class="preview-value">${config.globalSettings?.debug?.enabled ? '✓ 开启' : '✗ 关闭'}</span>
                        </div>
                        <div class="preview-item">
                            <span class="preview-label">快速模式</span>
                            <span class="preview-value">${config.globalSettings?.debug?.fastMode ? '✓ 开启' : '✗ 关闭'}</span>
                        </div>
                    </div>
                </div>
            `;

            // 任务配置
            if (config.tasks) {
                for (const [taskId, task] of Object.entries(config.tasks)) {
                    const stageCount = task.stages?.length || 0;
                    const taskType = task.taskType === 'wheel_cursor' ? '滚轮光标' : 'Prompt序列';
                    
                    html += `
                        <div class="preview-section">
                            <h3 class="preview-section-title">
                                <i class="fas fa-hand-paper"></i> ${task.name || taskId}
                            </h3>
                            <div class="preview-task-info">
                                <span class="preview-badge">${taskType}</span>
                                <span class="preview-badge">${stageCount} 个Stage</span>
                            </div>
                            <div class="preview-stages">
                    `;
                    
                    if (task.stages) {
                        task.stages.forEach((stage, index) => {
                            const promptCount = stage.promptSequence?.length || stage.maxTrials || 0;
                            html += `
                                <div class="preview-stage-item">
                                    <span class="preview-stage-num">${index + 1}</span>
                                    <span class="preview-stage-icon">${stage.icon || '●'}</span>
                                    <span class="preview-stage-name">${stage.label || stage.name}</span>
                                    <span class="preview-stage-count">${promptCount}次</span>
                                </div>
                            `;
                        });
                    }

                    html += `</div></div>`;
                }
            }

            // Prompt库
            if (config.promptLibrary && Object.keys(config.promptLibrary).length > 0) {
                const promptCount = Object.keys(config.promptLibrary).length;
                html += `
                    <div class="preview-section">
                        <h3 class="preview-section-title">
                            <i class="fas fa-list"></i> Prompt库 (${promptCount}个)
                        </h3>
                        <div class="preview-prompt-list">
                `;
                
                for (const [promptId, prompt] of Object.entries(config.promptLibrary)) {
                    html += `
                        <span class="preview-prompt-tag" style="border-left-color: ${prompt.color}">
                            ${prompt.icon} ${prompt.label}
                        </span>
                    `;
                }
                
                html += `</div></div>`;
            }

            return html;
        }

        /**
         * 更新配置状态显示
         */
        updateConfigStatus() {
            const statusEl = document.getElementById('configStatus');
            const nameEl = document.getElementById('configName');
            
            if (statusEl) {
                statusEl.className = 'status-badge ' + (this.isLoaded ? 'connected' : 'disconnected');
                statusEl.textContent = this.isLoaded ? '已加载' : '未加载';
            }
            
            if (nameEl) {
                const displayName = this.currentConfig?.configName || this.configFileName || '默认配置';
                nameEl.textContent = displayName.length > 12 ? displayName.substring(0, 12) + '...' : displayName;
                nameEl.title = displayName;
            }
        }

        /**
         * 显示Toast提示
         */
        showToast(message, type = 'success') {
            let toast = document.getElementById('configToast');
            if (!toast) {
                toast = document.createElement('div');
                toast.id = 'configToast';
                document.body.appendChild(toast);
            }

            const icon = type === 'success' ? 'fa-check-circle' : 'fa-exclamation-circle';
            toast.className = `toast ${type}`;
            toast.innerHTML = `<i class="fas ${icon}"></i> ${message}`;
            toast.classList.add('visible');

            setTimeout(() => {
                toast.classList.remove('visible');
            }, 3000);
        }

        /**
         * 获取当前配置
         */
        getCurrentConfig() {
            return this.currentConfig;
        }
    }

    // ==================== 初始化 ====================
    function initConfigManager() {
        console.log('[ConfigManager] 开始初始化...');
        
        const manager = new ConfigManager();
        window.configManager = manager;
        
        // 延迟初始化，确保DOM和其他模块已加载
        setTimeout(() => {
            manager.init();
        }, 300);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initConfigManager);
    } else {
        initConfigManager();
    }

    console.log('[ConfigManager] ====== 模块加载完成 ======');

})();
