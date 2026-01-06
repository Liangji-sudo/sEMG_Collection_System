/**
 * template-config.js - 采集模板配置管理器
 * 
 * 功能：
 * 1. 后台配置页面：创建/编辑采集模板
 * 2. 分类层级管理：大类、大场景、子场景、人群
 * 3. 手势库管理
 * 4. 受试者字段配置
 * 5. 执行参数配置
 * 6. 模板导入/导出
 */

(function() {
    'use strict';

    console.log('[TemplateConfig] 模块开始加载...');

    // ==================== 默认配置模板 ====================
    const DEFAULT_TEMPLATE = {
        templateName: '标准采集模板',
        version: '2.0',
        created: new Date().toISOString().split('T')[0],
        lastModified: new Date().toISOString(),

        // 分类标签（可自定义名称）
        categoryLabels: {
            category1: '大类',
            category2: '大场景',
            category3: '子场景',
            category4: '人群'
        },

        // 采集任务选项
        tasks: [
            { id: 'discrete_gesture', name: '离散手势采集', enabled: true },
            { id: 'continual_gesture_1', name: '连续手势采集1', enabled: true },
            { id: 'continual_gesture_2', name: '连续手势采集2', enabled: true }
        ],

        // 分类1: 大类
        category1: [
            { id: 'static', name: '静态采集', enabled: true },
            { id: 'dynamic', name: '动态采集', enabled: true }
        ],

        // 分类2: 大场景
        category2: [
            { id: 'sitting', name: '坐姿', enabled: true },
            { id: 'lying', name: '卧姿', enabled: true },
            { id: 'standing', name: '站姿', enabled: false }
        ],

        // 分类3: 子场景/Stage（顺序执行，不选择）
        category3: [
            { id: 'palm_up', name: '手心朝上', instruction: '请保持手心朝上的姿势', enabled: true },
            { id: 'palm_inward', name: '手心朝内', instruction: '请保持手心朝内的姿势', enabled: true },
            { id: 'hand_on_knee', name: '手放膝盖', instruction: '请将手放在膝盖上', enabled: true },
            { id: 'hand_on_desk', name: '手放桌上', instruction: '请将手放在桌面上', enabled: true },
            { id: 'arm_straight', name: '手臂伸直', instruction: '请将手臂向前伸直', enabled: false },
            { id: 'arm_bent', name: '手臂弯曲', instruction: '请将手臂弯曲90度', enabled: false }
        ],

        // 分类4: 人群
        category4: [
            { id: 'normal', name: '正常状态', description: '正常静态状态', enabled: true },
            { id: 'exercise', name: '运动/力竭', description: '运动后或力竭状态', enabled: true },
            { id: 'cold', name: '低温环境', description: '低温环境下采集', enabled: false }
        ],

        // 手势库
        gestures: {
            discrete: [
                { id: 'thumb_up', name: '拇指上滑', icon: '👆', enabled: true },
                { id: 'thumb_down', name: '拇指下滑', icon: '👇', enabled: true },
                { id: 'thumb_left', name: '拇指左滑', icon: '👈', enabled: true },
                { id: 'thumb_right', name: '拇指右滑', icon: '👉', enabled: true },
                { id: 'thumb_press', name: '拇指按压', icon: '👍', enabled: true },
                { id: 'index_tap', name: '食指点击', icon: '☝️', enabled: true },
                { id: 'index_double_tap', name: '食指双击', icon: '✌️', enabled: true },
                { id: 'middle_tap', name: '中指点击', icon: '🖕', enabled: true },
                { id: 'pinch', name: '捏合', icon: '🤏', enabled: true },
                { id: 'spread', name: '张开', icon: '🖐️', enabled: true },
                { id: 'fist', name: '握拳', icon: '✊', enabled: true },
                { id: 'release', name: '松开', icon: '✋', enabled: true },
                { id: 'wrist_up', name: '手腕上抬', icon: '⬆️', enabled: true },
                { id: 'wrist_down', name: '手腕下压', icon: '⬇️', enabled: true },
                { id: 'wrist_rotate_cw', name: '手腕顺时针', icon: '🔃', enabled: true },
                { id: 'wrist_rotate_ccw', name: '手腕逆时针', icon: '🔄', enabled: true },
                { id: 'rest', name: '保持/休息', icon: '⏸️', enabled: true }
            ],
            continual_1: [],
            continual_2: []
        },

        // 执行参数
        execution: {
            repeatPerGesture: 5,           // 每个手势重复次数
            intervalBetweenRepeat: 1.0,    // 重复间隔（秒）
            restBetweenGestures: 30.0,     // 手势间休息时间（秒）
            preparationTime: 3.0,          // Stage开始前准备时间（秒）
            gestureDisplayTime: 2.0        // 手势提示显示时间（秒）
        },

        // 受试者信息字段
        subjectFields: [
            { id: 'name', label: '姓名', type: 'text', required: true, placeholder: '请输入姓名' },
            { id: 'id', label: '编号', type: 'text', required: true, placeholder: '如 S001' },
            { id: 'age', label: '年龄', type: 'number', required: true, min: 1, max: 120 },
            { id: 'gender', label: '性别', type: 'select', required: true, options: [
                { value: 'male', label: '男' },
                { value: 'female', label: '女' }
            ]},
            { id: 'hand', label: '惯用手', type: 'select', required: true, options: [
                { value: 'right', label: '右手' },
                { value: 'left', label: '左手' },
                { value: 'both', label: '双手' }
            ]},
            { id: 'height', label: '身高(cm)', type: 'number', required: false, min: 50, max: 250 },
            { id: 'weight', label: '体重(kg)', type: 'number', required: false, min: 20, max: 300 },
            { id: 'bmi', label: 'BMI', type: 'number', required: false, readonly: true },
            { id: 'skinColor', label: '肤色', type: 'select', required: false, options: [
                { value: 'light', label: '浅色' },
                { value: 'medium', label: '中等' },
                { value: 'dark', label: '深色' }
            ]},
            { id: 'hairLevel', label: '毛发程度', type: 'select', required: false, options: [
                { value: 'none', label: '无' },
                { value: 'light', label: '少' },
                { value: 'heavy', label: '多' }
            ]},
            { id: 'armCircumference', label: '前臂围(cm)', type: 'number', required: false, min: 10, max: 60 },
            { id: 'note', label: '备注', type: 'textarea', required: false, placeholder: '其他信息' }
        ]
    };

    // ==================== 配置管理器类 ====================
    class TemplateConfigManager {
        constructor() {
            this.currentTemplate = null;
            this.isDirty = false;  // 是否有未保存的修改
            this.activeTab = 'categories';  // 当前激活的配置标签页
        }

        /**
         * 初始化
         */
        init() {
            console.log('[TemplateConfig] 初始化...');
            this.loadTemplate();
            this.bindEvents();
            this.render();
            console.log('[TemplateConfig] 初始化完成');
        }

        /**
         * 加载模板（从localStorage或使用默认）
         */
        loadTemplate() {
            const saved = localStorage.getItem('emg_collection_template');
            if (saved) {
                try {
                    this.currentTemplate = JSON.parse(saved);
                    console.log('[TemplateConfig] 已加载保存的模板:', this.currentTemplate.templateName);
                    
                    // 确保所有必要字段都存在（兼容旧模板）
                    this.ensureTemplateFields();
                } catch (e) {
                    console.warn('[TemplateConfig] 解析模板失败，使用默认模板');
                    this.currentTemplate = JSON.parse(JSON.stringify(DEFAULT_TEMPLATE));
                }
            } else {
                this.currentTemplate = JSON.parse(JSON.stringify(DEFAULT_TEMPLATE));
                console.log('[TemplateConfig] 使用默认模板');
            }
        }

        /**
         * 确保模板包含所有必要字段（用于兼容旧版本模板）
         */
        ensureTemplateFields() {
            // 检查并补充 subjectFields
            if (!this.currentTemplate.subjectFields || !Array.isArray(this.currentTemplate.subjectFields)) {
                console.log('[TemplateConfig] 补充缺失的 subjectFields');
                this.currentTemplate.subjectFields = DEFAULT_TEMPLATE.subjectFields.map(f => ({...f}));
            }
            
            // 检查并补充 categoryLabels
            if (!this.currentTemplate.categoryLabels) {
                console.log('[TemplateConfig] 补充缺失的 categoryLabels');
                this.currentTemplate.categoryLabels = {...DEFAULT_TEMPLATE.categoryLabels};
            }
            
            // 检查并补充 execution
            if (!this.currentTemplate.execution) {
                console.log('[TemplateConfig] 补充缺失的 execution');
                this.currentTemplate.execution = {...DEFAULT_TEMPLATE.execution};
            }
            
            // 检查并补充 gestures
            if (!this.currentTemplate.gestures) {
                console.log('[TemplateConfig] 补充缺失的 gestures');
                this.currentTemplate.gestures = JSON.parse(JSON.stringify(DEFAULT_TEMPLATE.gestures));
            }
        }

        /**
         * 保存模板到localStorage
         */
        saveTemplate() {
            this.currentTemplate.lastModified = new Date().toISOString();
            localStorage.setItem('emg_collection_template', JSON.stringify(this.currentTemplate));
            this.isDirty = false;
            this.showToast('模板已保存到本地', 'success');
            console.log('[TemplateConfig] 模板已保存到localStorage');
        }

        /**
         * 保存模板到服务器 config/ 目录
         */
        async saveTemplateToServer() {
            this.currentTemplate.lastModified = new Date().toISOString();
            
            // 生成文件名：模板名称_日期.json
            const safeName = this.currentTemplate.templateName.replace(/[^a-zA-Z0-9\u4e00-\u9fa5_-]/g, '_');
            const dateStr = new Date().toISOString().split('T')[0].replace(/-/g, '');
            const filename = `${safeName}_${dateStr}.json`;
            
            try {
                const response = await fetch('/api/config/save', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        filename: filename,
                        config: this.currentTemplate
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    // 同时保存到localStorage
                    localStorage.setItem('emg_collection_template', JSON.stringify(this.currentTemplate));
                    this.isDirty = false;
                    this.showToast(`已保存到 config/${result.filename}`, 'success');
                    console.log('[TemplateConfig] 模板已保存到服务器:', result.filename);
                } else {
                    this.showToast('保存失败: ' + result.error, 'error');
                }
            } catch (err) {
                console.error('[TemplateConfig] 保存到服务器失败:', err);
                // 回退到本地保存
                this.saveTemplate();
                this.showToast('服务器保存失败，已保存到本地', 'warning');
            }
        }

        /**
         * 导出模板为JSON文件
         */
        exportTemplate() {
            const dataStr = JSON.stringify(this.currentTemplate, null, 2);
            const blob = new Blob([dataStr], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            
            const a = document.createElement('a');
            a.href = url;
            a.download = `${this.currentTemplate.templateName}_${new Date().toISOString().split('T')[0]}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            
            this.showToast('模板已导出', 'success');
        }

        /**
         * 导入模板
         */
        importTemplate(file) {
            const reader = new FileReader();
            reader.onload = (e) => {
                try {
                    const template = JSON.parse(e.target.result);
                    // 验证模板格式
                    if (!template.templateName || !template.category1) {
                        throw new Error('无效的模板格式');
                    }
                    this.currentTemplate = template;
                    this.isDirty = true;
                    this.render();
                    this.showToast('模板已导入', 'success');
                } catch (err) {
                    this.showToast('导入失败: ' + err.message, 'error');
                }
            };
            reader.readAsText(file);
        }

        /**
         * 重置为默认模板
         */
        resetToDefault() {
            if (confirm('确定要重置为默认模板吗？当前的修改将丢失。')) {
                this.currentTemplate = JSON.parse(JSON.stringify(DEFAULT_TEMPLATE));
                this.isDirty = true;
                this.render();
                this.showToast('已重置为默认模板', 'success');
            }
        }

        /**
         * 绑定事件
         */
        bindEvents() {
            // 标签页切换
            document.querySelectorAll('.config-tab-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const tab = e.currentTarget.dataset.tab;
                    this.switchTab(tab);
                });
            });

            // 保存按钮 - 保存到服务器
            const saveBtn = document.getElementById('saveTemplateBtn');
            if (saveBtn) {
                saveBtn.addEventListener('click', () => this.saveTemplateToServer());
            }

            // 导出按钮
            const exportBtn = document.getElementById('exportTemplateBtn');
            if (exportBtn) {
                exportBtn.addEventListener('click', () => this.exportTemplate());
            }

            // 导入按钮
            const importBtn = document.getElementById('importTemplateBtn');
            const importInput = document.getElementById('importTemplateInput');
            if (importBtn && importInput) {
                importBtn.addEventListener('click', () => importInput.click());
                importInput.addEventListener('change', (e) => {
                    if (e.target.files.length > 0) {
                        this.importTemplate(e.target.files[0]);
                        e.target.value = '';  // 清空以便再次选择同一文件
                    }
                });
            }

            // 重置按钮
            const resetBtn = document.getElementById('resetTemplateBtn');
            if (resetBtn) {
                resetBtn.addEventListener('click', () => this.resetToDefault());
            }

            // 模板名称输入
            const nameInput = document.getElementById('templateNameInput');
            if (nameInput) {
                nameInput.addEventListener('change', (e) => {
                    this.currentTemplate.templateName = e.target.value.trim() || '未命名模板';
                    this.isDirty = true;
                });
            }
        }

        /**
         * 切换标签页
         */
        switchTab(tabName) {
            this.activeTab = tabName;
            
            // 更新标签按钮状态
            document.querySelectorAll('.config-tab-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.tab === tabName);
            });

            // 更新内容区域
            document.querySelectorAll('.config-tab-content').forEach(content => {
                content.classList.toggle('active', content.dataset.tab === tabName);
            });

            // 渲染对应内容
            this.renderTabContent(tabName);
        }

        /**
         * 主渲染函数
         */
        render() {
            // 渲染模板名称
            const nameInput = document.getElementById('templateNameInput');
            if (nameInput) {
                nameInput.value = this.currentTemplate.templateName;
            }

            // 渲染当前标签页内容
            this.renderTabContent(this.activeTab);
        }

        /**
         * 渲染标签页内容
         */
        renderTabContent(tabName) {
            switch (tabName) {
                case 'categories':
                    this.renderCategoriesTab();
                    break;
                case 'gestures':
                    this.renderGesturesTab();
                    break;
                case 'execution':
                    this.renderExecutionTab();
                    break;
                case 'subject':
                    this.renderSubjectTab();
                    break;
            }
        }

        /**
         * 渲染分类配置标签页
         */
        renderCategoriesTab() {
            const container = document.getElementById('categoriesTabContent');
            if (!container) return;

            const template = this.currentTemplate;
            
            container.innerHTML = `
                <!-- 采集任务及手势库 -->
                <div class="config-section">
                    <div class="config-section-header">
                        <h3><i class="fa fa-tasks"></i> 采集任务与手势库</h3>
                    </div>
                    
                    <!-- 离散手势采集任务 -->
                    <div class="task-config-block">
                        <div class="task-header">
                            <label class="config-item-checkbox task-checkbox">
                                <input type="checkbox" data-category="tasks" data-id="discrete_gesture" 
                                       ${template.tasks.find(t => t.id === 'discrete_gesture')?.enabled ? 'checked' : ''}>
                                <span class="checkbox-label task-name">离散手势采集</span>
                            </label>
                            <span class="task-gesture-count">${template.gestures.discrete.filter(g => g.enabled).length}/${template.gestures.discrete.length} 个手势已启用</span>
                        </div>
                        <div class="task-gestures-grid" id="discreteGesturesGrid">
                            ${template.gestures.discrete.map((gesture, index) => `
                                <div class="gesture-chip ${gesture.enabled ? 'enabled' : 'disabled'}" 
                                     data-gesture-type="discrete" data-index="${index}">
                                    <span class="gesture-chip-icon">${gesture.icon}</span>
                                    <span class="gesture-chip-name">${gesture.name}</span>
                                    <button class="gesture-chip-toggle" title="${gesture.enabled ? '点击禁用' : '点击启用'}">
                                        <i class="fa ${gesture.enabled ? 'fa-check-circle' : 'fa-circle-o'}"></i>
                                    </button>
                                </div>
                            `).join('')}
                            <button class="gesture-chip add-gesture-chip" data-gesture-type="discrete">
                                <i class="fa fa-plus"></i> 添加手势
                            </button>
                        </div>
                    </div>

                    <!-- 连续手势采集1 -->
                    <div class="task-config-block">
                        <div class="task-header">
                            <label class="config-item-checkbox task-checkbox">
                                <input type="checkbox" data-category="tasks" data-id="continual_gesture_1" 
                                       ${template.tasks.find(t => t.id === 'continual_gesture_1')?.enabled ? 'checked' : ''}>
                                <span class="checkbox-label task-name">连续手势采集1（滚轮控制）</span>
                            </label>
                        </div>
                        <p class="task-description">滚轮控制光标移动到目标位置的任务，不需要配置手势库</p>
                    </div>

                    <!-- 连续手势采集2 -->
                    <div class="task-config-block">
                        <div class="task-header">
                            <label class="config-item-checkbox task-checkbox">
                                <input type="checkbox" data-category="tasks" data-id="continual_gesture_2" 
                                       ${template.tasks.find(t => t.id === 'continual_gesture_2')?.enabled ? 'checked' : ''}>
                                <span class="checkbox-label task-name">连续手势采集2（手腕控制）</span>
                            </label>
                        </div>
                        <p class="task-description">手腕动作控制光标移动到目标位置的任务，不需要配置手势库</p>
                    </div>
                </div>

                <!-- 分类1: 大类 -->
                <div class="config-section">
                    <div class="config-section-header">
                        <h3><i class="fa fa-layer-group"></i> 
                            <input type="text" class="category-label-input" data-category-label="category1" 
                                   value="${template.categoryLabels.category1}" placeholder="分类1名称">
                        </h3>
                        <button class="config-add-btn" data-add-category="category1">
                            <i class="fa fa-plus"></i> 添加
                        </button>
                    </div>
                    <div class="config-items-list" data-category="category1">
                        ${template.category1.map((item, index) => this.renderCategoryItem('category1', item, index)).join('')}
                    </div>
                </div>

                <!-- 分类2: 大场景 -->
                <div class="config-section">
                    <div class="config-section-header">
                        <h3><i class="fa fa-map-marker-alt"></i>
                            <input type="text" class="category-label-input" data-category-label="category2"
                                   value="${template.categoryLabels.category2}" placeholder="分类2名称">
                        </h3>
                        <button class="config-add-btn" data-add-category="category2">
                            <i class="fa fa-plus"></i> 添加
                        </button>
                    </div>
                    <div class="config-items-list" data-category="category2">
                        ${template.category2.map((item, index) => this.renderCategoryItem('category2', item, index)).join('')}
                    </div>
                </div>

                <!-- 分类3: 子场景/Stage -->
                <div class="config-section">
                    <div class="config-section-header">
                        <h3><i class="fa fa-hand-paper"></i>
                            <input type="text" class="category-label-input" data-category-label="category3"
                                   value="${template.categoryLabels.category3}" placeholder="分类3名称">
                        </h3>
                        <button class="config-add-btn" data-add-category="category3">
                            <i class="fa fa-plus"></i> 添加
                        </button>
                    </div>
                    <p class="config-hint">子场景将按顺序执行，采集时不需要选择</p>
                    <div class="config-items-list sortable" data-category="category3">
                        ${template.category3.map((item, index) => this.renderStageItem(item, index)).join('')}
                    </div>
                </div>

                <!-- 分类4: 人群 -->
                <div class="config-section">
                    <div class="config-section-header">
                        <h3><i class="fa fa-users"></i>
                            <input type="text" class="category-label-input" data-category-label="category4"
                                   value="${template.categoryLabels.category4}" placeholder="分类4名称">
                        </h3>
                        <button class="config-add-btn" data-add-category="category4">
                            <i class="fa fa-plus"></i> 添加
                        </button>
                    </div>
                    <div class="config-items-list" data-category="category4">
                        ${template.category4.map((item, index) => this.renderCategoryItem('category4', item, index)).join('')}
                    </div>
                </div>
            `;

            // 绑定分类配置事件
            this.bindCategoryEvents(container);
        }

        /**
         * 渲染分类项
         */
        renderCategoryItem(category, item, index) {
            return `
                <div class="config-item" data-index="${index}">
                    <div class="config-item-drag">
                        <i class="fa fa-grip-vertical"></i>
                    </div>
                    <label class="config-item-checkbox">
                        <input type="checkbox" data-category="${category}" data-index="${index}" 
                               data-field="enabled" ${item.enabled ? 'checked' : ''}>
                    </label>
                    <input type="text" class="config-item-input" data-category="${category}" 
                           data-index="${index}" data-field="name" value="${item.name}" placeholder="名称">
                    <button class="config-item-delete" data-category="${category}" data-index="${index}">
                        <i class="fa fa-trash"></i>
                    </button>
                </div>
            `;
        }

        /**
         * 渲染Stage项（带指导语）
         */
        renderStageItem(item, index) {
            return `
                <div class="config-item config-item-stage" data-index="${index}">
                    <div class="config-item-drag">
                        <i class="fa fa-grip-vertical"></i>
                    </div>
                    <label class="config-item-checkbox">
                        <input type="checkbox" data-category="category3" data-index="${index}" 
                               data-field="enabled" ${item.enabled ? 'checked' : ''}>
                    </label>
                    <div class="config-item-fields">
                        <input type="text" class="config-item-input" data-category="category3" 
                               data-index="${index}" data-field="name" value="${item.name}" placeholder="名称">
                        <input type="text" class="config-item-input config-item-instruction" data-category="category3" 
                               data-index="${index}" data-field="instruction" value="${item.instruction || ''}" placeholder="指导语">
                    </div>
                    <button class="config-item-delete" data-category="category3" data-index="${index}">
                        <i class="fa fa-trash"></i>
                    </button>
                </div>
            `;
        }

        /**
         * 绑定分类配置事件
         */
        bindCategoryEvents(container) {
            // 复选框变化
            container.querySelectorAll('input[type="checkbox"]').forEach(checkbox => {
                checkbox.addEventListener('change', (e) => {
                    const { category, id, index, field } = e.target.dataset;
                    
                    if (category === 'tasks') {
                        const task = this.currentTemplate.tasks.find(t => t.id === id);
                        if (task) task.enabled = e.target.checked;
                    } else if (index !== undefined) {
                        this.currentTemplate[category][index][field || 'enabled'] = e.target.checked;
                    }
                    this.isDirty = true;
                });
            });

            // 文本输入变化
            container.querySelectorAll('input[type="text"].config-item-input').forEach(input => {
                input.addEventListener('change', (e) => {
                    const { category, index, field } = e.target.dataset;
                    if (category && index !== undefined && field) {
                        const newValue = e.target.value.trim();
                        this.currentTemplate[category][index][field] = newValue;
                        
                        // 如果修改的是name字段，同步更新id（支持中文目录）
                        if (field === 'name' && newValue) {
                            this.currentTemplate[category][index].id = newValue;
                        }
                        
                        this.isDirty = true;
                    }
                });
            });

            // 分类标签名称变化
            container.querySelectorAll('.category-label-input').forEach(input => {
                input.addEventListener('change', (e) => {
                    const categoryLabel = e.target.dataset.categoryLabel;
                    if (categoryLabel) {
                        this.currentTemplate.categoryLabels[categoryLabel] = e.target.value.trim();
                        this.isDirty = true;
                    }
                });
            });

            // 添加按钮
            container.querySelectorAll('[data-add-category]').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const category = e.currentTarget.dataset.addCategory;
                    this.addCategoryItem(category);
                });
            });

            // 删除按钮
            container.querySelectorAll('.config-item-delete').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const { category, index } = e.currentTarget.dataset;
                    this.deleteCategoryItem(category, parseInt(index));
                });
            });

            // ========== 手势相关事件 ==========
            
            // 手势卡片点击切换启用状态
            container.querySelectorAll('.gesture-chip:not(.add-gesture-chip)').forEach(chip => {
                chip.addEventListener('click', (e) => {
                    // 如果点击的是删除按钮区域，不处理
                    if (e.target.closest('.gesture-chip-toggle')) {
                        const gestureType = chip.dataset.gestureType;
                        const index = parseInt(chip.dataset.index);
                        
                        if (gestureType === 'discrete') {
                            this.currentTemplate.gestures.discrete[index].enabled = 
                                !this.currentTemplate.gestures.discrete[index].enabled;
                            this.isDirty = true;
                            this.renderCategoriesTab();
                        }
                    }
                });
            });

            // 添加手势按钮
            container.querySelectorAll('.add-gesture-chip').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const gestureType = e.currentTarget.dataset.gestureType;
                    this.showAddGestureDialog(gestureType);
                });
            });
        }

        /**
         * 显示添加手势对话框
         */
        showAddGestureDialog(gestureType) {
            const name = prompt('请输入手势名称：');
            if (name && name.trim()) {
                const trimmedName = name.trim();
                const icon = prompt('请输入手势图标（emoji）：', '✋');
                
                this.currentTemplate.gestures[gestureType].push({
                    id: trimmedName,  // 使用名称作为id
                    name: trimmedName,
                    icon: icon || '✋',
                    enabled: true
                });
                this.isDirty = true;
                this.renderCategoriesTab();
                this.showToast('手势已添加', 'success');
            }
        }

        /**
         * 添加分类项
         */
        addCategoryItem(category) {
            // 弹出对话框让用户输入名称
            const name = prompt('请输入名称：');
            if (!name || !name.trim()) {
                return; // 用户取消或未输入
            }
            
            const trimmedName = name.trim();
            
            // 使用名称作为id（支持中文），同时添加时间戳确保唯一性
            const newItem = {
                id: trimmedName,  // 直接使用名称作为id，支持中文目录
                name: trimmedName,
                enabled: true
            };

            if (category === 'category3') {
                const instruction = prompt('请输入指导语（可选）：', '');
                newItem.instruction = instruction || '';
            }
            if (category === 'category4') {
                newItem.description = '';
            }

            this.currentTemplate[category].push(newItem);
            this.isDirty = true;
            this.renderCategoriesTab();
            this.showToast(`已添加: ${trimmedName}`, 'success');
        }

        /**
         * 删除分类项
         */
        deleteCategoryItem(category, index) {
            if (confirm('确定要删除这个项目吗？')) {
                this.currentTemplate[category].splice(index, 1);
                this.isDirty = true;
                this.renderCategoriesTab();
            }
        }

        /**
         * 渲染手势库标签页
         */
        renderGesturesTab() {
            const container = document.getElementById('gesturesTabContent');
            if (!container) return;

            const gestures = this.currentTemplate.gestures.discrete;

            container.innerHTML = `
                <div class="config-section">
                    <div class="config-section-header">
                        <h3><i class="fa fa-hand-paper"></i> 离散手势库</h3>
                        <button class="config-add-btn" id="addGestureBtn">
                            <i class="fa fa-plus"></i> 添加手势
                        </button>
                    </div>
                    <p class="config-hint">勾选启用的手势，每个手势将按顺序采集</p>
                    <div class="config-gestures-grid">
                        ${gestures.map((gesture, index) => `
                            <div class="config-gesture-item ${gesture.enabled ? 'enabled' : 'disabled'}">
                                <label class="gesture-checkbox">
                                    <input type="checkbox" data-index="${index}" ${gesture.enabled ? 'checked' : ''}>
                                    <span class="gesture-icon">${gesture.icon}</span>
                                </label>
                                <input type="text" class="gesture-name-input" data-index="${index}" 
                                       value="${gesture.name}" placeholder="手势名称">
                                <button class="gesture-delete-btn" data-index="${index}">
                                    <i class="fa fa-times"></i>
                                </button>
                            </div>
                        `).join('')}
                    </div>
                </div>

                <div class="config-section">
                    <div class="config-section-header">
                        <h3><i class="fa fa-info-circle"></i> 手势统计</h3>
                    </div>
                    <div class="gesture-stats">
                        <div class="stat-item">
                            <span class="stat-value">${gestures.length}</span>
                            <span class="stat-label">总手势数</span>
                        </div>
                        <div class="stat-item">
                            <span class="stat-value">${gestures.filter(g => g.enabled).length}</span>
                            <span class="stat-label">已启用</span>
                        </div>
                        <div class="stat-item">
                            <span class="stat-value">${gestures.filter(g => !g.enabled).length}</span>
                            <span class="stat-label">已禁用</span>
                        </div>
                    </div>
                </div>
            `;

            // 绑定手势事件
            this.bindGestureEvents(container);
        }

        /**
         * 绑定手势配置事件
         */
        bindGestureEvents(container) {
            // 复选框
            container.querySelectorAll('.gesture-checkbox input').forEach(checkbox => {
                checkbox.addEventListener('change', (e) => {
                    const index = parseInt(e.target.dataset.index);
                    this.currentTemplate.gestures.discrete[index].enabled = e.target.checked;
                    this.isDirty = true;
                    this.renderGesturesTab();
                });
            });

            // 名称输入
            container.querySelectorAll('.gesture-name-input').forEach(input => {
                input.addEventListener('change', (e) => {
                    const index = parseInt(e.target.dataset.index);
                    this.currentTemplate.gestures.discrete[index].name = e.target.value.trim();
                    this.isDirty = true;
                });
            });

            // 删除按钮
            container.querySelectorAll('.gesture-delete-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const index = parseInt(e.currentTarget.dataset.index);
                    if (confirm('确定要删除这个手势吗？')) {
                        this.currentTemplate.gestures.discrete.splice(index, 1);
                        this.isDirty = true;
                        this.renderGesturesTab();
                    }
                });
            });

            // 添加手势
            const addBtn = document.getElementById('addGestureBtn');
            if (addBtn) {
                addBtn.addEventListener('click', () => {
                    this.currentTemplate.gestures.discrete.push({
                        id: `gesture_${Date.now()}`,
                        name: '新手势',
                        icon: '✋',
                        enabled: true
                    });
                    this.isDirty = true;
                    this.renderGesturesTab();
                });
            }
        }

        /**
         * 渲染执行参数标签页
         */
        renderExecutionTab() {
            const container = document.getElementById('executionTabContent');
            if (!container) return;

            const exec = this.currentTemplate.execution;

            container.innerHTML = `
                <div class="config-section">
                    <div class="config-section-header">
                        <h3><i class="fa fa-clock"></i> 时间参数</h3>
                    </div>
                    <div class="config-params-grid">
                        <div class="config-param-item">
                            <label>每个手势重复次数</label>
                            <input type="number" id="repeatPerGesture" value="${exec.repeatPerGesture}" min="1" max="20">
                            <span class="param-unit">次</span>
                        </div>
                        <div class="config-param-item">
                            <label>重复间隔时间</label>
                            <input type="number" id="intervalBetweenRepeat" value="${exec.intervalBetweenRepeat}" min="0.5" max="10" step="0.5">
                            <span class="param-unit">秒</span>
                        </div>
                        <div class="config-param-item">
                            <label>手势间休息时间</label>
                            <input type="number" id="restBetweenGestures" value="${exec.restBetweenGestures}" min="5" max="120" step="5">
                            <span class="param-unit">秒</span>
                        </div>
                        <div class="config-param-item">
                            <label>Stage开始前准备时间</label>
                            <input type="number" id="preparationTime" value="${exec.preparationTime}" min="1" max="10" step="1">
                            <span class="param-unit">秒</span>
                        </div>
                        <div class="config-param-item">
                            <label>手势提示显示时间</label>
                            <input type="number" id="gestureDisplayTime" value="${exec.gestureDisplayTime}" min="1" max="10" step="0.5">
                            <span class="param-unit">秒</span>
                        </div>
                    </div>
                </div>

                <div class="config-section">
                    <div class="config-section-header">
                        <h3><i class="fa fa-calculator"></i> 时间估算</h3>
                    </div>
                    <div class="time-estimation">
                        ${this.renderTimeEstimation()}
                    </div>
                </div>
            `;

            // 绑定参数输入事件
            container.querySelectorAll('.config-param-item input').forEach(input => {
                input.addEventListener('change', (e) => {
                    const field = e.target.id;
                    this.currentTemplate.execution[field] = parseFloat(e.target.value);
                    this.isDirty = true;
                    // 更新时间估算
                    const estimation = container.querySelector('.time-estimation');
                    if (estimation) {
                        estimation.innerHTML = this.renderTimeEstimation();
                    }
                });
            });
        }

        /**
         * 渲染时间估算
         */
        renderTimeEstimation() {
            const exec = this.currentTemplate.execution;
            const enabledGestures = this.currentTemplate.gestures.discrete.filter(g => g.enabled).length;
            const enabledStages = this.currentTemplate.category3.filter(s => s.enabled).length;

            // 单个手势时间 = 重复次数 * (提示时间 + 间隔) + 休息时间
            const singleGestureTime = exec.repeatPerGesture * (exec.gestureDisplayTime + exec.intervalBetweenRepeat) + exec.restBetweenGestures;
            
            // 单个Stage时间 = 所有手势时间 + 准备时间
            const singleStageTime = enabledGestures * singleGestureTime + exec.preparationTime;
            
            // 总时间
            const totalTime = enabledStages * singleStageTime;

            const formatTime = (seconds) => {
                if (seconds < 60) return `${Math.round(seconds)}秒`;
                if (seconds < 3600) return `${Math.round(seconds / 60)}分钟`;
                return `${(seconds / 3600).toFixed(1)}小时`;
            };

            return `
                <div class="estimation-grid">
                    <div class="estimation-item">
                        <span class="estimation-label">启用的手势数</span>
                        <span class="estimation-value">${enabledGestures} 个</span>
                    </div>
                    <div class="estimation-item">
                        <span class="estimation-label">启用的Stage数</span>
                        <span class="estimation-value">${enabledStages} 个</span>
                    </div>
                    <div class="estimation-item">
                        <span class="estimation-label">单个手势采集时间</span>
                        <span class="estimation-value">${formatTime(singleGestureTime)}</span>
                    </div>
                    <div class="estimation-item">
                        <span class="estimation-label">单个Stage时间</span>
                        <span class="estimation-value">${formatTime(singleStageTime)}</span>
                    </div>
                    <div class="estimation-item estimation-total">
                        <span class="estimation-label">单个受试者总时间</span>
                        <span class="estimation-value">${formatTime(totalTime)}</span>
                    </div>
                </div>
            `;
        }

        /**
         * 渲染受试者字段标签页
         */
        renderSubjectTab() {
            const container = document.getElementById('subjectTabContent');
            if (!container) {
                console.warn('[TemplateConfig] subjectTabContent container not found');
                return;
            }

            // 确保 subjectFields 存在，如果不存在则使用默认值
            if (!this.currentTemplate.subjectFields || !Array.isArray(this.currentTemplate.subjectFields)) {
                console.warn('[TemplateConfig] subjectFields not found, using default');
                this.currentTemplate.subjectFields = DEFAULT_TEMPLATE.subjectFields.map(f => ({...f}));
            }

            const fields = this.currentTemplate.subjectFields;
            console.log('[TemplateConfig] Rendering subject fields:', fields.length, 'fields');

            container.innerHTML = `
                <div class="config-section">
                    <div class="config-section-header">
                        <h3><i class="fa fa-user"></i> 受试者信息字段</h3>
                        <button class="config-add-btn" id="addFieldBtn">
                            <i class="fa fa-plus"></i> 添加字段
                        </button>
                    </div>
                    <p class="config-hint">配置采集时需要填写的受试者信息</p>
                    <div class="config-fields-list">
                        ${fields.map((field, index) => this.renderSubjectField(field, index)).join('')}
                    </div>
                </div>
            `;

            // 绑定字段事件
            this.bindSubjectFieldEvents(container);
        }

        /**
         * 渲染受试者字段项
         */
        renderSubjectField(field, index) {
            return `
                <div class="config-field-item" data-index="${index}">
                    <div class="field-row">
                        <div class="field-drag">
                            <i class="fa fa-grip-vertical"></i>
                        </div>
                        <input type="text" class="field-label-input" data-field="label" value="${field.label}" placeholder="字段名称">
                        <select class="field-type-select" data-field="type">
                            <option value="text" ${field.type === 'text' ? 'selected' : ''}>文本</option>
                            <option value="number" ${field.type === 'number' ? 'selected' : ''}>数字</option>
                            <option value="select" ${field.type === 'select' ? 'selected' : ''}>下拉选择</option>
                            <option value="textarea" ${field.type === 'textarea' ? 'selected' : ''}>多行文本</option>
                        </select>
                        <label class="field-required">
                            <input type="checkbox" data-field="required" ${field.required ? 'checked' : ''}>
                            <span>必填</span>
                        </label>
                        <button class="field-delete-btn" data-index="${index}">
                            <i class="fa fa-trash"></i>
                        </button>
                    </div>
                </div>
            `;
        }

        /**
         * 绑定受试者字段事件
         */
        bindSubjectFieldEvents(container) {
            // 字段属性变化
            container.querySelectorAll('.config-field-item').forEach(item => {
                const index = parseInt(item.dataset.index);
                
                item.querySelectorAll('input, select').forEach(input => {
                    input.addEventListener('change', (e) => {
                        const field = e.target.dataset.field;
                        let value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
                        this.currentTemplate.subjectFields[index][field] = value;
                        this.isDirty = true;
                    });
                });
            });

            // 删除字段
            container.querySelectorAll('.field-delete-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const index = parseInt(e.currentTarget.dataset.index);
                    if (confirm('确定要删除这个字段吗？')) {
                        this.currentTemplate.subjectFields.splice(index, 1);
                        this.isDirty = true;
                        this.renderSubjectTab();
                    }
                });
            });

            // 添加字段
            const addBtn = document.getElementById('addFieldBtn');
            if (addBtn) {
                addBtn.addEventListener('click', () => {
                    this.currentTemplate.subjectFields.push({
                        id: `field_${Date.now()}`,
                        label: '新字段',
                        type: 'text',
                        required: false,
                        placeholder: ''
                    });
                    this.isDirty = true;
                    this.renderSubjectTab();
                });
            }
        }

        /**
         * 获取当前模板
         */
        getTemplate() {
            return this.currentTemplate;
        }

        /**
         * 获取启用的分类
         */
        getEnabledCategories() {
            return {
                tasks: this.currentTemplate.tasks.filter(t => t.enabled),
                category1: this.currentTemplate.category1.filter(c => c.enabled),
                category2: this.currentTemplate.category2.filter(c => c.enabled),
                category3: this.currentTemplate.category3.filter(c => c.enabled),
                category4: this.currentTemplate.category4.filter(c => c.enabled),
                gestures: this.currentTemplate.gestures.discrete.filter(g => g.enabled)
            };
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

            const icon = type === 'success' ? 'fa-check-circle' : 'fa-exclamation-circle';
            toast.className = `toast ${type}`;
            toast.innerHTML = `<i class="fa ${icon}"></i> ${message}`;
            toast.classList.add('visible');

            setTimeout(() => {
                toast.classList.remove('visible');
            }, 2500);
        }
    }

    // ==================== 初始化 ====================
    window.TemplateConfigManager = TemplateConfigManager;
    
    // 创建全局实例
    document.addEventListener('DOMContentLoaded', () => {
        window.templateConfigManager = new TemplateConfigManager();
        console.log('[TemplateConfig] 管理器实例已创建');
    });

    console.log('[TemplateConfig] 模块加载完成');

})();
