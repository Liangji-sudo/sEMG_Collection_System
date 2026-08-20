/**
 * config-manager.js - 采集配置管理器（v2）
 * 
 * 功能：
 * 1. 加载/导入采集模板配置（JSON文件）
 * 2. 配置预览（思维导图式层级展示）
 * 3. 将模板配置应用到采集系统
 * 4. 与后台配置页面联动
 * 
 * 配置来源：
 * - 后台配置页面创建并保存到localStorage
 * - 从本地JSON文件导入
 */

(function() {
    'use strict';

    console.log('[ConfigManager] ====== 模块开始加载 ======');

    class ConfigManager {
        constructor() {
            this.currentTemplate = null;
            this.templateFileName = null;
            this.isLoaded = false;
        }

        /**
         * 初始化配置管理器
         */
        init() {
            console.log('[ConfigManager] 初始化开始');
            
            // 尝试从localStorage加载已保存的模板
            this.loadFromLocalStorage();
            
            // 绑定UI事件
            this.bindEvents();
            
            // 更新UI状态
            this.updateConfigStatus();
            
            console.log('[ConfigManager] 初始化完成 ✓');
        }

        /**
         * 从localStorage加载模板
         */
        loadFromLocalStorage() {
            const saved = localStorage.getItem('emg_collection_template');
            if (saved) {
                try {
                    this.currentTemplate = JSON.parse(saved);
                    this.templateFileName = '本地保存';
                    this.isLoaded = true;
                    console.log('[ConfigManager] 已从localStorage加载模板:', this.currentTemplate.templateName);
                } catch (e) {
                    console.warn('[ConfigManager] 解析localStorage模板失败:', e);
                    this.loadDefaultTemplate();
                }
            } else {
                this.loadDefaultTemplate();
            }
        }

        /**
         * 加载默认模板
         */
        loadDefaultTemplate() {
            // 如果templateConfigManager已初始化，从它获取默认模板
            if (window.templateConfigManager && window.templateConfigManager.currentTemplate) {
                this.currentTemplate = window.templateConfigManager.currentTemplate;
            } else {
                // 使用内置默认模板
                this.currentTemplate = this.getBuiltInDefaultTemplate();
            }
            this.templateFileName = '内置默认';
            this.isLoaded = true;
            console.log('[ConfigManager] 使用默认模板');
        }

        /**
         * 获取内置默认模板
         */
        getBuiltInDefaultTemplate() {
            return {
                templateName: '标准采集模板',
                version: '2.0',
                
                categoryLabels: {
                    category1: '大类',
                    category2: '大场景',
                    category3: '子场景',
                    category4: '人群'
                },

                tasks: [
                    { id: 'discrete_gesture', name: '离散手势采集', enabled: true },
                    { id: 'continual_gesture_1', name: '连续手势采集1', enabled: true },
                    { id: 'continual_gesture_2', name: '连续手势采集2', enabled: true }
                ],

                category1: [
                    { id: 'static', name: '静态采集', enabled: true },
                    { id: 'dynamic', name: '动态采集', enabled: true }
                ],

                category2: [
                    { id: 'sitting', name: '坐姿', enabled: true },
                    { id: 'lying', name: '卧姿', enabled: true }
                ],

                category3: [
                    { id: 'palm_up', name: '手心朝上', instruction: '请保持手心朝上的姿势', enabled: true },
                    { id: 'palm_inward', name: '手心朝内', instruction: '请保持手心朝内的姿势', enabled: true },
                    { id: 'hand_on_knee', name: '手放膝盖', instruction: '请将手放在膝盖上', enabled: true },
                    { id: 'hand_on_desk', name: '手放桌上', instruction: '请将手放在桌面上', enabled: true }
                ],

                category4: [
                    { id: 'normal', name: '正常状态', enabled: true },
                    { id: 'exercise', name: '运动/力竭', enabled: true }
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
                        gestureDisplayTime: 2.0,
                        orderedShuffleRatio: 0.6
                    }
                }
            };
        }

        /**
         * 绑定UI事件
         */
        bindEvents() {
            // 加载配置按钮
            const loadBtn = document.getElementById('loadConfigBtn');
            if (loadBtn) {
                loadBtn.addEventListener('click', () => this.showLoadConfigModal());
            }

            // 预览配置按钮
            const previewBtn = document.getElementById('previewConfigBtn');
            if (previewBtn) {
                previewBtn.addEventListener('click', () => this.showPreviewModal());
            }

            // 隐藏的文件输入
            let fileInput = document.getElementById('configFileInput');
            if (!fileInput) {
                fileInput = document.createElement('input');
                fileInput.type = 'file';
                fileInput.id = 'configFileInput';
                fileInput.accept = '.json';
                fileInput.style.display = 'none';
                document.body.appendChild(fileInput);
            }
            fileInput.addEventListener('change', (e) => this.handleFileSelect(e));

            console.log('[ConfigManager] 事件绑定完成');
        }

        /**
         * 显示加载配置弹窗
         */
        showLoadConfigModal() {
            // 创建弹窗
            let modal = document.getElementById('loadConfigModal');
            if (modal) {
                modal.remove();
            }
            modal = this.createLoadConfigModal();
            document.body.appendChild(modal);

            modal.classList.add('visible');
            
            // 加载服务器配置文件列表
            this.loadServerConfigList();
        }

        /**
         * 加载服务器配置文件列表
         */
        async loadServerConfigList() {
            const listContainer = document.getElementById('serverConfigList');
            if (!listContainer) return;

            listContainer.innerHTML = '<div class="loading-hint"><i class="fa fa-spinner fa-spin"></i> 加载中...</div>';

            try {
                const response = await fetch('/api/config/files');
                const result = await response.json();

                if (result.success && result.files.length > 0) {
                    listContainer.innerHTML = result.files.map(file => `
                        <div class="server-config-item" onclick="configManager.loadFromServer('${file.name}')">
                            <div class="config-file-icon">
                                <i class="fa fa-file-code"></i>
                            </div>
                            <div class="config-file-info">
                                <span class="config-file-name">${file.name}</span>
                                <span class="config-file-meta">${this.formatFileSize(file.size)} · ${this.formatDate(file.lastModified)}</span>
                            </div>
                            <button class="config-file-delete" onclick="event.stopPropagation(); configManager.deleteFromServer('${file.name}')" title="删除">
                                <i class="fa fa-trash"></i>
                            </button>
                        </div>
                    `).join('');
                } else if (result.success) {
                    listContainer.innerHTML = '<div class="empty-hint">config/ 目录暂无配置文件</div>';
                } else {
                    listContainer.innerHTML = `<div class="error-hint"><i class="fa fa-exclamation-circle"></i> ${result.error}</div>`;
                }
            } catch (err) {
                console.error('[ConfigManager] 加载服务器配置列表失败:', err);
                listContainer.innerHTML = '<div class="error-hint"><i class="fa fa-exclamation-circle"></i> 无法连接服务器</div>';
            }
        }

        /**
         * 从服务器加载配置文件
         */
        async loadFromServer(filename) {
            try {
                const response = await fetch(`/api/config/load/${filename}`);
                const result = await response.json();

                if (result.success) {
                    this.currentTemplate = result.config;
                    this.templateFileName = filename;
                    this.isLoaded = true;

                    // 同步到localStorage
                    localStorage.setItem('emg_collection_template', JSON.stringify(result.config));

                    // 同步到templateConfigManager
                    if (window.templateConfigManager) {
                        window.templateConfigManager.currentTemplate = result.config;
                        window.templateConfigManager.isDirty = false;
                    }

                    this.updateConfigStatus();
                    this.closeLoadModal();
                    this.showToast(`已加载: ${result.config.templateName || filename}`, 'success');
                } else {
                    this.showToast('加载失败: ' + result.error, 'error');
                }
            } catch (err) {
                console.error('[ConfigManager] 从服务器加载失败:', err);
                this.showToast('加载失败: 无法连接服务器', 'error');
            }
        }

        /**
         * 从服务器删除配置文件
         */
        async deleteFromServer(filename) {
            const confirmed = await this.showConfirm(`确定要删除配置文件 "${filename}" 吗？`);
            if (!confirmed) {
                return;
            }

            try {
                const response = await fetch(`/api/config/delete/${filename}`, {
                    method: 'DELETE'
                });
                const result = await response.json();

                if (result.success) {
                    this.showToast('配置文件已删除', 'success');
                    this.loadServerConfigList(); // 刷新列表
                } else {
                    this.showToast('删除失败: ' + result.error, 'error');
                }
            } catch (err) {
                console.error('[ConfigManager] 删除失败:', err);
                this.showToast('删除失败: 无法连接服务器', 'error');
            }
        }

        /**
         * 格式化文件大小
         */
        formatFileSize(bytes) {
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        }

        /**
         * 格式化日期
         */
        formatDate(timestamp) {
            const date = new Date(timestamp);
            return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        }

        /**
         * 创建加载配置弹窗（简化版 - 只显示config/目录下的配置文件）
         */
        createLoadConfigModal() {
            const modal = document.createElement('div');
            modal.id = 'loadConfigModal';
            modal.className = 'config-modal-overlay';
            modal.innerHTML = `
                <div class="config-modal-content load-config-modal" style="max-width: 480px;">
                    <div class="config-modal-header">
                        <h3><i class="fa fa-folder-open"></i> 选择配置文件</h3>
                        <button class="config-modal-close" onclick="configManager.closeLoadModal()">
                            <i class="fa fa-times"></i>
                        </button>
                    </div>
                    <div class="config-modal-body" style="padding: 0;">
                        <!-- 服务器配置文件列表 -->
                        <div class="server-config-section" style="border: none; margin: 0; padding: 16px;">
                            <div class="server-config-header" style="margin-bottom: 12px;">
                                <h4 style="margin: 0; font-size: 14px; color: #666;">
                                    <i class="fa fa-folder"></i> config/ 目录
                                </h4>
                                <button class="refresh-config-btn" onclick="configManager.loadServerConfigList()" title="刷新">
                                    <i class="fa fa-refresh"></i>
                                </button>
                            </div>
                            <div class="server-config-list" id="serverConfigList" style="max-height: 400px; overflow-y: auto;">
                                <div class="loading-hint">
                                    <i class="fa fa-spinner fa-spin"></i> 加载中...
                                </div>
                            </div>
                        </div>
                        
                        <!-- 当前配置信息 -->
                        <div class="current-config-info" style="border-top: 1px solid #e5e7eb; margin: 0; padding: 12px 16px; background: #f8fafc;">
                            <div class="config-info-row" style="display: flex; justify-content: space-between; font-size: 13px;">
                                <span class="config-info-label" style="color: #666;">当前配置：</span>
                                <span class="config-info-value" style="color: #1e40af; font-weight: 500;" id="currentTemplateName">${this.currentTemplate?.templateName || '未加载'}</span>
                            </div>
                        </div>
                    </div>
                </div>
            `;

            // 点击遮罩关闭
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    this.closeLoadModal();
                }
            });

            return modal;
        }

        /**
         * 关闭加载弹窗
         */
        closeLoadModal() {
            const modal = document.getElementById('loadConfigModal');
            if (modal) {
                modal.classList.remove('visible');
            }
        }

        /**
         * 触发文件选择
         */
        triggerFileSelect() {
            const fileInput = document.getElementById('configFileInput');
            if (fileInput) {
                fileInput.click();
            }
        }

        /**
         * 处理文件选择
         */
        handleFileSelect(e) {
            const file = e.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = (event) => {
                try {
                    const template = JSON.parse(event.target.result);
                    
                    // 验证模板格式
                    if (!this.validateTemplate(template)) {
                        this.showToast('配置文件格式无效', 'error');
                        return;
                    }

                    this.currentTemplate = template;
                    this.templateFileName = file.name;
                    this.isLoaded = true;

                    // 同步到localStorage
                    localStorage.setItem('emg_collection_template', JSON.stringify(template));

                    // 同步到templateConfigManager
                    if (window.templateConfigManager) {
                        window.templateConfigManager.currentTemplate = template;
                        window.templateConfigManager.isDirty = false;
                    }

                    this.updateConfigStatus();
                    this.closeLoadModal();
                    this.showToast('配置已加载: ' + template.templateName, 'success');

                } catch (err) {
                    console.error('[ConfigManager] 解析配置文件失败:', err);
                    this.showToast('解析配置文件失败', 'error');
                }
            };
            reader.readAsText(file);
            
            // 清空文件输入以便再次选择同一文件
            e.target.value = '';
        }

        /**
         * 验证模板格式
         */
        validateTemplate(template) {
            // 基本字段检查
            if (!template.templateName) return false;
            if (!template.tasks || !Array.isArray(template.tasks)) return false;
            if (!template.category3 || !Array.isArray(template.category3)) return false;
            return true;
        }

        /**
         * 从localStorage加载
         */
        loadFromStorage() {
            this.loadFromLocalStorage();
            this.updateConfigStatus();
            this.closeLoadModal();
            this.showToast('已加载保存的配置', 'success');
        }

        /**
         * 加载默认并关闭弹窗
         */
        loadDefaultAndClose() {
            this.currentTemplate = this.getBuiltInDefaultTemplate();
            this.templateFileName = '内置默认';
            this.isLoaded = true;
            
            // 同步到localStorage
            localStorage.setItem('emg_collection_template', JSON.stringify(this.currentTemplate));
            
            this.updateConfigStatus();
            this.closeLoadModal();
            this.showToast('已恢复默认配置', 'success');
        }

        /**
         * 显示预览弹窗
         */
        showPreviewModal() {
            // 先确保有模板数据
            if (!this.currentTemplate) {
                this.loadFromLocalStorage();
            }

            let modal = document.getElementById('configPreviewModal');
            if (modal) {
                // 如果弹窗已存在，先移除再重建（确保内容更新）
                modal.remove();
            }
            
            modal = this.createPreviewModal();
            document.body.appendChild(modal);
            modal.classList.add('visible');
        }

        /**
         * 创建预览弹窗
         */
        createPreviewModal() {
            const modal = document.createElement('div');
            modal.id = 'configPreviewModal';
            modal.className = 'config-modal-overlay';
            
            const previewContent = this.generateTreePreviewHTML();
            
            modal.innerHTML = `
                <div class="config-modal-content preview-modal">
                    <div class="config-modal-header">
                        <h3><i class="fa fa-sitemap"></i> 配置预览 - ${this.currentTemplate?.templateName || '未加载'}</h3>
                        <button class="config-modal-close" onclick="configManager.closePreviewModal()">
                            <i class="fa fa-times"></i>
                        </button>
                    </div>
                    <div class="config-modal-body">
                        <div class="preview-tree-container">
                            ${previewContent}
                        </div>
                    </div>
                </div>
            `;

            // 点击遮罩关闭
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    this.closePreviewModal();
                }
            });

            return modal;
        }

        /**
         * 关闭预览弹窗
         */
        closePreviewModal() {
            const modal = document.getElementById('configPreviewModal');
            if (modal) {
                modal.classList.remove('visible');
            }
        }

        /**
         * 生成树形预览HTML（思维导图式）
         */
        generateTreePreviewHTML() {
            const t = this.currentTemplate;
            if (!t) {
                return '<div class="preview-empty">暂无配置，请先加载配置文件</div>';
            }

            const enabledTasks = t.tasks.filter(task => task.enabled);
            const enabledCat1 = t.category1.filter(c => c.enabled);
            const enabledCat2 = t.category2.filter(c => c.enabled);
            const enabledCat3 = t.category3.filter(c => c.enabled);
            const enabledCat4 = t.category4.filter(c => c.enabled);
            const enabledGestures = t.gestures?.discrete?.filter(g => g.enabled) || [];

            // 获取各任务的执行参数
            const discreteExec = t.execution?.discrete_gesture || {};
            const continual1Exec = t.execution?.continual_gesture_1 || {};
            const continual2Exec = t.execution?.continual_gesture_2 || {};
            const continual3Exec = t.execution?.continual_gesture_3 || {};

            // 判断哪些任务类型被启用
            const hasDiscreteGesture = enabledTasks.some(task => task.id === 'discrete_gesture');
            const hasContinualGesture1 = enabledTasks.some(task => task.id === 'continual_gesture_1');
            const hasContinualGesture2 = enabledTasks.some(task => task.id === 'continual_gesture_2');
            const hasContinualGesture3 = enabledTasks.some(task => task.id === 'continual_gesture_3');

            let html = `
                <div class="preview-header-info">
                    <div class="preview-stat">
                        <span class="stat-num">${enabledTasks.length}</span>
                        <span class="stat-label">采集任务</span>
                    </div>
                    <div class="preview-stat">
                        <span class="stat-num">${enabledCat3.length}</span>
                        <span class="stat-label">${t.categoryLabels?.category3 || '子场景'}</span>
                    </div>
                    <div class="preview-stat">
                        <span class="stat-num">${enabledGestures.length}</span>
                        <span class="stat-label">离散手势</span>
                    </div>
                </div>

                <div class="preview-tree">
                    <!-- 根节点：模板名称 -->
                    <div class="tree-node root-node">
                        <div class="node-content">
                            <i class="fa fa-cog"></i>
                            <span>${t.templateName}</span>
                        </div>

                        <!-- Level 0: 采集任务 -->
                        <div class="tree-children">
                            ${enabledTasks.map(task => {
                                // 根据任务类型显示不同的badge信息
                                let badgeInfo = '';
                                if (task.id === 'discrete_gesture') {
                                    badgeInfo = `<span class="node-badge">${enabledGestures.length}个手势</span>`;
                                } else if (task.id === 'continual_gesture_1') {
                                    badgeInfo = `<span class="node-badge">每Stage ${continual1Exec.trialsPerStage || 5}次</span>`;
                                } else if (task.id === 'continual_gesture_2') {
                                    badgeInfo = `<span class="node-badge">每Stage ${continual2Exec.trialsPerStage || 5}次</span>`;
                                } else if (task.id === 'continual_gesture_3') {
                                    badgeInfo = `<span class="node-badge">每Stage ${continual3Exec.trialsPerStage || 10}次</span>`;
                                }
                                return `
                                <div class="tree-node task-node ${task.id}">
                                    <div class="node-content">
                                        <i class="fa fa-hand-paper"></i>
                                        <span>${task.name}</span>
                                        ${badgeInfo}
                                    </div>

                                    <!-- Level 1: 大类 -->
                                    <div class="tree-children">
                                        ${enabledCat1.map(cat1 => `
                                            <div class="tree-node cat1-node">
                                                <div class="node-content">
                                                    <i class="fa fa-layer-group"></i>
                                                    <span>${cat1.name}</span>
                                                </div>

                                                <!-- Level 2: 大场景 -->
                                                <div class="tree-children">
                                                    ${enabledCat2.map(cat2 => `
                                                        <div class="tree-node cat2-node">
                                                            <div class="node-content">
                                                                <i class="fa fa-map-marker-alt"></i>
                                                                <span>${cat2.name}</span>
                                                            </div>

                                                            <!-- Level 3: 子场景 (Stage) - 折叠显示 -->
                                                            <div class="tree-children collapsed-children">
                                                                <div class="node-content stages-summary" onclick="this.parentElement.classList.toggle('expanded')">
                                                                    <i class="fa fa-hand-point-right"></i>
                                                                    <span>${enabledCat3.length}个${t.categoryLabels?.category3 || '子场景'}</span>
                                                                    <i class="fa fa-chevron-down expand-icon"></i>
                                                                </div>
                                                                <div class="stages-list">
                                                                    ${enabledCat3.map(cat3 => `
                                                                        <div class="tree-node cat3-node">
                                                                            <div class="node-content">
                                                                                <i class="fa fa-circle"></i>
                                                                                <span>${cat3.name}</span>
                                                                            </div>
                                                                        </div>
                                                                    `).join('')}
                                                                </div>
                                                            </div>
                                                        </div>
                                                    `).join('')}
                                                </div>
                                            </div>
                                        `).join('')}
                                    </div>
                                </div>
                            `}).join('')}
                        </div>
                    </div>
                </div>

                <!-- 人群配置 -->
                <div class="preview-section-inline">
                    <h4><i class="fa fa-users"></i> ${t.categoryLabels?.category4 || '人群'}配置</h4>
                    <div class="preview-tags">
                        ${enabledCat4.map(cat => `<span class="preview-tag">${cat.name}</span>`).join('')}
                    </div>
                </div>

                <!-- 离散手势库 -->
                ${hasDiscreteGesture && enabledGestures.length > 0 ? `
                <div class="preview-section-inline">
                    <h4><i class="fa fa-hand-paper"></i> 离散手势库 (${enabledGestures.length}个)</h4>
                    <div class="preview-gestures">
                        ${enabledGestures.map(g => `
                            <span class="preview-gesture">${g.icon} ${g.name}</span>
                        `).join('')}
                    </div>
                    <div class="preview-params">
                        <span>每个手势重复 <strong>${discreteExec.repeatPerGesture || 5}</strong> 次</span>
                    </div>
                </div>
                ` : ''}

                <!-- 连续手势1参数 -->
                ${hasContinualGesture1 ? `
                <div class="preview-section-inline">
                    <h4><i class="fa fa-sync-alt"></i> 连续手势采集1</h4>
                    <div class="preview-params">
                        <span>每Stage采集 <strong>${continual1Exec.trialsPerStage || 5}</strong> 次</span>
                    </div>
                </div>
                ` : ''}

                <!-- 连续手势2参数 -->
                ${hasContinualGesture2 ? `
                <div class="preview-section-inline">
                    <h4><i class="fa fa-sync-alt"></i> 连续手势采集2</h4>
                    <div class="preview-params">
                        <span>每Stage采集 <strong>${continual2Exec.trialsPerStage || 5}</strong> 次</span>
                    </div>
                </div>
                ` : ''}

                <!-- 连续手势3参数 -->
                ${hasContinualGesture3 ? `
                <div class="preview-section-inline">
                    <h4><i class="fa fa-sync-alt"></i> 连续手势采集3</h4>
                    <div class="preview-params">
                        <span>每Stage采集 <strong>${continual3Exec.trialsPerStage || 10}</strong> 次</span>
                    </div>
                </div>
                ` : ''}
            `;

            return html;
        }

        /**
         * 估算总采集时间
         */
        estimateTotalTime(template) {
            const exec = template.execution || {};
            const enabledGestures = template.gestures?.discrete?.filter(g => g.enabled).length || 0;
            const enabledStages = template.category3?.filter(s => s.enabled).length || 0;

            const singleGestureTime = (exec.repeatPerGesture || 5) * ((exec.gestureDisplayTime || 2) + (exec.intervalBetweenRepeat || 1)) + (exec.restBetweenGestures || 30);
            const singleStageTime = enabledGestures * singleGestureTime + (exec.preparationTime || 3);
            const totalSeconds = enabledStages * singleStageTime;

            if (totalSeconds < 60) return `${Math.round(totalSeconds)}秒`;
            if (totalSeconds < 3600) return `${Math.round(totalSeconds / 60)}分钟`;
            return `${(totalSeconds / 3600).toFixed(1)}小时`;
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
                const displayName = this.currentTemplate?.templateName || '默认配置';
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
                toast.className = 'toast';
                document.body.appendChild(toast);
            }

            const iconMap = { success: 'fa-check-circle', error: 'fa-times-circle', warning: 'fa-exclamation-triangle', info: 'fa-info-circle' };
            const icon = iconMap[type] || 'fa-exclamation-circle';
            toast.className = `toast ${type}`;
            toast.innerHTML = `<i class="fa ${icon}"></i> ${message}`;
            toast.classList.add('visible');

            setTimeout(() => {
                toast.classList.remove('visible');
            }, 3000);
        }

        /**
         * 获取当前模板
         */
        getCurrentTemplate() {
            return this.currentTemplate;
        }

        /**
         * 获取启用的配置
         */
        getEnabledConfig() {
            const t = this.currentTemplate;
            if (!t) return null;

            return {
                tasks: t.tasks.filter(task => task.enabled),
                category1: t.category1.filter(c => c.enabled),
                category2: t.category2.filter(c => c.enabled),
                category3: t.category3.filter(c => c.enabled),
                category4: t.category4.filter(c => c.enabled),
                gestures: t.gestures?.discrete?.filter(g => g.enabled) || [],
                execution: t.execution,
                categoryLabels: t.categoryLabels
            };
        }

        /**
         * 自定义confirm对话框（兼容Electron环境）
         */
        showConfirm(message) {
            return new Promise((resolve) => {
                const overlay = document.createElement('div');
                overlay.className = 'custom-confirm-overlay';
                overlay.style.cssText = `
                    position: fixed;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    background: rgba(0, 0, 0, 0.5);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    z-index: 10001;
                `;

                const dialog = document.createElement('div');
                dialog.style.cssText = `
                    background: white;
                    border-radius: 12px;
                    padding: 24px;
                    min-width: 300px;
                    max-width: 400px;
                    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                `;

                dialog.innerHTML = `
                    <div style="display: flex; align-items: flex-start; gap: 12px; margin-bottom: 20px;">
                        <div style="
                            width: 40px;
                            height: 40px;
                            border-radius: 50%;
                            background: #fef3c7;
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            flex-shrink: 0;
                        ">
                            <i class="fa fa-exclamation-triangle" style="color: #f59e0b; font-size: 18px;"></i>
                        </div>
                        <div style="font-size: 15px; color: #333; line-height: 1.5; padding-top: 8px;">${message}</div>
                    </div>
                    <div style="display: flex; justify-content: flex-end; gap: 10px;">
                        <button class="confirm-cancel" style="
                            padding: 8px 20px;
                            border: 1px solid #d1d5db;
                            background: white;
                            border-radius: 6px;
                            cursor: pointer;
                            font-size: 14px;
                            color: #666;
                        ">取消</button>
                        <button class="confirm-ok" style="
                            padding: 8px 20px;
                            border: none;
                            background: #ef4444;
                            color: white;
                            border-radius: 6px;
                            cursor: pointer;
                            font-size: 14px;
                        ">确定</button>
                    </div>
                `;

                overlay.appendChild(dialog);
                document.body.appendChild(overlay);

                const cleanup = () => document.body.removeChild(overlay);

                dialog.querySelector('.confirm-ok').addEventListener('click', () => {
                    cleanup();
                    resolve(true);
                });

                dialog.querySelector('.confirm-cancel').addEventListener('click', () => {
                    cleanup();
                    resolve(false);
                });

                overlay.addEventListener('click', (e) => {
                    if (e.target === overlay) {
                        cleanup();
                        resolve(false);
                    }
                });
            });
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
