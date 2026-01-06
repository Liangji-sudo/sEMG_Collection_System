/**
 * collection-selector.js - 采集选择流程控制器 (v2.0)
 * 
 * 功能：
 * 1. 分步选择界面：任务类型 → 大类 → 大场景 → 人群 → 受试者信息
 * 2. 【新】跳过分类3（子场景）选择，因为子场景按顺序执行
 * 3. 从配置模板读取选项
 * 4. 验证并保存选择结果
 * 5. 与采集控制器协同工作
 * 
 * 选择流程：
 * - 分类0：采集任务（离散手势/连续手势1/2）
 * - 分类1：大类（静态/动态采集）
 * - 分类2：大场景（坐姿/卧姿）
 * - 【跳过】分类3：子场景（不选择，按顺序执行）
 * - 分类4：人群（正常/运动力竭）
 * - 分类5：受试者信息填写
 */

(function() {
    'use strict';

    console.log('[CollectionSelector] 模块加载开始...');

    // 选择步骤定义（不包含category3，因为它按顺序执行不需要选择）
    const STEPS = [
        { id: 'task', label: '采集任务', category: 'tasks', icon: 'fa-tasks' },
        { id: 'category1', label: '大类', category: 'category1', icon: 'fa-layer-group' },
        { id: 'category2', label: '大场景', category: 'category2', icon: 'fa-map-marker-alt' },
        // 注意：这里跳过了category3（子场景），它在采集时按顺序执行
        { id: 'category4', label: '人群', category: 'category4', icon: 'fa-users' },
        { id: 'subject', label: '受试者信息', category: 'subject', icon: 'fa-user-edit' }
    ];

    // 默认受试者字段配置（更完整的字段列表）
    const DEFAULT_SUBJECT_FIELDS = [
        { id: 'name', label: '姓名', type: 'text', required: true, placeholder: '请输入姓名' },
        { id: 'id', label: '编号', type: 'text', required: true, placeholder: '如 S001' },
        { id: 'age', label: '年龄', type: 'number', required: true, min: 1, max: 120 },
        { id: 'gender', label: '性别', type: 'select', required: true, options: [
            { value: 'male', label: '男' }, { value: 'female', label: '女' }
        ]},
        { id: 'hand', label: '惯用手', type: 'select', required: true, options: [
            { value: 'right', label: '右手' }, { value: 'left', label: '左手' }, { value: 'both', label: '双手' }
        ]},
        { id: 'height', label: '身高(cm)', type: 'number', required: false, min: 50, max: 250 },
        { id: 'weight', label: '体重(kg)', type: 'number', required: false, min: 20, max: 300 },
        { id: 'bmi', label: 'BMI', type: 'number', required: false, readonly: true, placeholder: '自动计算' },
        { id: 'skinColor', label: '肤色', type: 'select', required: false, options: [
            { value: 'light', label: '浅色' }, { value: 'medium', label: '中等' }, { value: 'dark', label: '深色' }
        ]},
        { id: 'hairLevel', label: '毛发程度', type: 'select', required: false, options: [
            { value: 'none', label: '无' }, { value: 'light', label: '少' }, { value: 'heavy', label: '多' }
        ]},
        { id: 'armCircumference', label: '前臂围(cm)', type: 'number', required: false, min: 10, max: 60, placeholder: '前臂最粗处周长' },
        { id: 'wristCircumference', label: '手腕围(cm)', type: 'number', required: false, min: 10, max: 30, placeholder: '手腕周长' },
        { id: 'occupation', label: '职业', type: 'text', required: false, placeholder: '如：学生、工程师' },
        { id: 'healthCondition', label: '健康状况', type: 'select', required: false, options: [
            { value: 'healthy', label: '健康' }, { value: 'minor_issue', label: '有轻微问题' }, { value: 'chronic', label: '有慢性病' }
        ]},
        { id: 'exerciseFrequency', label: '运动频率', type: 'select', required: false, options: [
            { value: 'rarely', label: '很少运动' }, { value: 'weekly', label: '每周1-2次' }, 
            { value: 'frequent', label: '每周3-5次' }, { value: 'daily', label: '每天运动' }
        ]},
        { id: 'note', label: '备注', type: 'textarea', required: false, placeholder: '其他需要记录的信息' }
    ];

    class CollectionSelector {
        constructor() {
            this.currentStep = 0;
            this.selections = {};
            this.template = null;
            this.isOpen = false;
            this._bindingDone = false;
        }

        init() {
            if (this._bindingDone) return;
            console.log('[CollectionSelector] 初始化...');
            this.bindEvents();
            this._bindingDone = true;
            console.log('[CollectionSelector] 初始化完成');
        }

        bindEvents() {
            const closeBtn = document.getElementById('closeSelectorModal');
            if (closeBtn) {
                closeBtn.addEventListener('click', () => this.close());
            }

            const overlay = document.getElementById('collectionSelectorModal');
            if (overlay) {
                overlay.addEventListener('click', (e) => {
                    if (e.target === overlay) this.close();
                });
            }

            const prevBtn = document.getElementById('selectorPrevBtn');
            if (prevBtn) {
                prevBtn.addEventListener('click', () => this.prevStep());
            }

            const nextBtn = document.getElementById('selectorNextBtn');
            if (nextBtn) {
                nextBtn.addEventListener('click', () => this.nextStep());
            }
        }

        open() {
            console.log('[CollectionSelector] 打开选择器');
            
            if (!this._bindingDone) this.init();
            
            this.loadTemplate();
            this.currentStep = 0;
            this.selections = {};
            
            const modal = document.getElementById('collectionSelectorModal');
            if (modal) {
                modal.classList.add('visible');
                this.isOpen = true;
            } else {
                console.error('[CollectionSelector] 未找到模态框 #collectionSelectorModal');
                return;
            }
            
            this.renderStep();
            this.updateNavigation();
        }

        close() {
            const modal = document.getElementById('collectionSelectorModal');
            if (modal) {
                modal.classList.remove('visible');
                this.isOpen = false;
            }
        }

        loadTemplate() {
            if (window.templateConfigManager && window.templateConfigManager.currentTemplate) {
                this.template = window.templateConfigManager.currentTemplate;
            } else {
                const saved = localStorage.getItem('emg_collection_template');
                if (saved) {
                    try {
                        this.template = JSON.parse(saved);
                    } catch (e) {
                        this.template = this.getDefaultTemplate();
                    }
                } else {
                    this.template = this.getDefaultTemplate();
                }
            }
            console.log('[CollectionSelector] 加载模板:', this.template.templateName);
        }

        getDefaultTemplate() {
            return {
                templateName: '默认模板',
                categoryLabels: { category1: '大类', category2: '大场景', category3: '子场景', category4: '人群' },
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
                    { id: 'palm_up', name: '手心朝上', enabled: true },
                    { id: 'palm_inward', name: '手心朝内', enabled: true }
                ],
                category4: [
                    { id: 'normal', name: '正常状态', enabled: true },
                    { id: 'exercise', name: '运动/力竭', enabled: true }
                ],
                subjectFields: DEFAULT_SUBJECT_FIELDS
            };
        }

        renderStep() {
            const step = STEPS[this.currentStep];
            const container = document.getElementById('selectorStepContent');
            if (!container) return;

            this.updateStepIndicator();

            const title = document.getElementById('selectorStepTitle');
            if (title) {
                const label = this.getStepLabel(step);
                title.innerHTML = `<i class="fas ${step.icon}"></i> 选择${label}`;
            }

            if (step.id === 'subject') {
                container.innerHTML = this.renderSubjectForm();
                this.bindSubjectFormEvents();
            } else {
                container.innerHTML = this.renderOptionsGrid(step);
                this.bindOptionEvents();
            }
        }

        getStepLabel(step) {
            if (step.id === 'task') return '采集任务';
            if (step.id === 'subject') return '受试者信息';
            if (this.template?.categoryLabels?.[step.id]) {
                return this.template.categoryLabels[step.id];
            }
            return step.label;
        }

        updateStepIndicator() {
            const indicator = document.getElementById('selectorStepIndicator');
            if (!indicator) return;

            let html = '';
            STEPS.forEach((step, index) => {
                const isActive = index === this.currentStep;
                const isCompleted = index < this.currentStep;
                const label = this.getStepLabel(step);
                
                html += `
                    <div class="step-item ${isActive ? 'active' : ''} ${isCompleted ? 'completed' : ''}">
                        <div class="step-number">
                            ${isCompleted ? '<i class="fas fa-check"></i>' : (index + 1)}
                        </div>
                        <div class="step-label">${label}</div>
                    </div>
                `;
                if (index < STEPS.length - 1) {
                    html += '<div class="step-connector"></div>';
                }
            });
            indicator.innerHTML = html;
        }

        renderOptionsGrid(step) {
            const options = this.getOptionsForStep(step);
            if (!options || options.length === 0) {
                return '<div class="selector-empty">暂无可选项</div>';
            }

            let html = '<div class="selector-options-grid">';
            options.forEach(opt => {
                const isSelected = this.selections[step.id] === opt.id;
                const icon = this.getOptionIcon(opt.id);
                
                html += `
                    <div class="selector-option ${isSelected ? 'selected' : ''}" 
                         data-id="${opt.id}" data-step="${step.id}">
                        <div class="option-icon">${icon}</div>
                        <div class="option-name">${opt.name}</div>
                        ${opt.description ? `<div class="option-desc">${opt.description}</div>` : ''}
                    </div>
                `;
            });
            
            html += '</div>';
            return html;
        }

        getOptionsForStep(step) {
            if (step.id === 'task') {
                return (this.template.tasks || []).filter(t => t.enabled);
            }
            return (this.template[step.id] || []).filter(c => c.enabled);
        }

        getOptionIcon(optionId) {
            const iconMap = {
                'discrete_gesture': '<i class="fas fa-hand-paper"></i>',
                'continual_gesture_1': '<i class="fas fa-hand-point-up"></i>',
                'continual_gesture_2': '<i class="fas fa-hand-peace"></i>',
                'static': '<i class="fas fa-pause-circle"></i>',
                'dynamic': '<i class="fas fa-running"></i>',
                'sitting': '<i class="fas fa-chair"></i>',
                'lying': '<i class="fas fa-bed"></i>',
                'standing': '<i class="fas fa-male"></i>',
                'normal': '<i class="fas fa-user"></i>',
                'exercise': '<i class="fas fa-dumbbell"></i>',
                'cold': '<i class="fas fa-snowflake"></i>'
            };
            return iconMap[optionId] || '<i class="fas fa-circle"></i>';
        }

        renderSubjectForm() {
            // 获取字段配置，如果模板中没有则使用默认字段
            let fields = this.template.subjectFields;
            
            // 如果模板中没有subjectFields，使用默认字段
            if (!fields || fields.length === 0) {
                fields = DEFAULT_SUBJECT_FIELDS;
                console.log('[CollectionSelector] 使用默认受试者字段配置');
            }
            
            const saved = JSON.parse(localStorage.getItem('emg_current_user') || '{}');

            let html = '<div class="selector-subject-form">';
            
            // 显示当前选择的分类摘要
            html += this.renderSelectionSummary();
            
            // 分组渲染字段（两列布局）
            html += '<div class="subject-fields-container">';
            for (let i = 0; i < fields.length; i += 2) {
                html += '<div class="form-row">';
                html += this.renderFormField(fields[i], saved);
                if (fields[i + 1]) html += this.renderFormField(fields[i + 1], saved);
                html += '</div>';
            }
            html += '</div>';
            html += '</div>';
            return html;
        }

        /**
         * 渲染已选择的分类摘要
         */
        renderSelectionSummary() {
            let html = '<div class="selection-summary">';
            html += '<div class="summary-title"><i class="fas fa-clipboard-check"></i> 当前选择</div>';
            html += '<div class="summary-items">';
            
            // 显示已选择的各个分类
            if (this.selections.task) {
                const task = this.template.tasks?.find(t => t.id === this.selections.task);
                html += `<span class="summary-item"><i class="fas fa-tasks"></i> ${task?.name || this.selections.task}</span>`;
            }
            if (this.selections.category1) {
                const cat1 = this.template.category1?.find(c => c.id === this.selections.category1);
                html += `<span class="summary-item"><i class="fas fa-layer-group"></i> ${cat1?.name || this.selections.category1}</span>`;
            }
            if (this.selections.category2) {
                const cat2 = this.template.category2?.find(c => c.id === this.selections.category2);
                html += `<span class="summary-item"><i class="fas fa-map-marker-alt"></i> ${cat2?.name || this.selections.category2}</span>`;
            }
            if (this.selections.category4) {
                const cat4 = this.template.category4?.find(c => c.id === this.selections.category4);
                html += `<span class="summary-item"><i class="fas fa-users"></i> ${cat4?.name || this.selections.category4}</span>`;
            }
            
            html += '</div></div>';
            return html;
        }

        renderFormField(field, saved = {}) {
            const value = saved[field.id] || '';
            const req = field.required ? '<span class="required">*</span>' : '';
            let input = '';
            
            if (field.type === 'select') {
                const opts = (field.options || []).map(opt => {
                    const v = typeof opt === 'object' ? opt.value : opt;
                    const l = typeof opt === 'object' ? opt.label : opt;
                    return `<option value="${v}" ${value === v ? 'selected' : ''}>${l}</option>`;
                }).join('');
                input = `<select id="subject_${field.id}" ${field.required ? 'required' : ''}><option value="">请选择</option>${opts}</select>`;
            } else if (field.type === 'number') {
                const readonly = field.readonly ? 'readonly' : '';
                input = `<input type="number" id="subject_${field.id}" value="${value}" 
                         ${field.min !== undefined ? `min="${field.min}"` : ''} 
                         ${field.max !== undefined ? `max="${field.max}"` : ''} 
                         ${field.placeholder ? `placeholder="${field.placeholder}"` : ''}
                         ${field.required ? 'required' : ''} ${readonly}>`;
            } else if (field.type === 'textarea') {
                input = `<textarea id="subject_${field.id}" rows="2" 
                         ${field.placeholder ? `placeholder="${field.placeholder}"` : ''}
                         ${field.required ? 'required' : ''}>${value}</textarea>`;
            } else {
                input = `<input type="text" id="subject_${field.id}" value="${value}" 
                         ${field.placeholder ? `placeholder="${field.placeholder}"` : ''}
                         ${field.required ? 'required' : ''}>`;
            }
            
            return `<div class="form-group"><label>${field.label} ${req}</label>${input}</div>`;
        }

        bindOptionEvents() {
            document.querySelectorAll('.selector-option').forEach(opt => {
                opt.addEventListener('click', (e) => {
                    const id = e.currentTarget.dataset.id;
                    const stepId = e.currentTarget.dataset.step;
                    this.selections[stepId] = id;
                    
                    document.querySelectorAll(`.selector-option[data-step="${stepId}"]`).forEach(o => {
                        o.classList.toggle('selected', o.dataset.id === id);
                    });
                    this.updateNavigation();
                });
            });
        }

        bindSubjectFormEvents() {
            // 常规输入事件
            document.querySelectorAll('.selector-subject-form input, .selector-subject-form select, .selector-subject-form textarea').forEach(input => {
                input.addEventListener('input', () => this.updateNavigation());
                input.addEventListener('change', () => {
                    this.updateNavigation();
                    // BMI自动计算
                    this.calculateBMI();
                });
            });
            
            // 初始化时计算BMI
            this.calculateBMI();
        }

        /**
         * 自动计算BMI
         */
        calculateBMI() {
            const heightInput = document.getElementById('subject_height');
            const weightInput = document.getElementById('subject_weight');
            const bmiInput = document.getElementById('subject_bmi');
            
            if (heightInput && weightInput && bmiInput) {
                const height = parseFloat(heightInput.value);
                const weight = parseFloat(weightInput.value);
                
                if (height > 0 && weight > 0) {
                    const heightM = height / 100;
                    const bmi = (weight / (heightM * heightM)).toFixed(1);
                    bmiInput.value = bmi;
                }
            }
        }

        prevStep() {
            if (this.currentStep > 0) {
                this.currentStep--;
                this.renderStep();
                this.updateNavigation();
            }
        }

        nextStep() {
            if (!this.validateCurrentStep()) return;
            
            if (this.currentStep === STEPS.length - 1) {
                this.complete();
                return;
            }
            
            this.currentStep++;
            this.renderStep();
            this.updateNavigation();
        }

        validateCurrentStep() {
            const step = STEPS[this.currentStep];
            
            if (step.id === 'subject') {
                const fields = this.template.subjectFields || DEFAULT_SUBJECT_FIELDS;
                const subjectData = {};
                
                for (const field of fields) {
                    const input = document.getElementById(`subject_${field.id}`);
                    if (!input) continue;
                    const value = input.value.trim();
                    if (field.required && !value) {
                        this.showToast(`请填写${field.label}`, 'warning');
                        input.focus();
                        return false;
                    }
                    subjectData[field.id] = value;
                }
                this.selections.subject = subjectData;
                return true;
            } else {
                if (!this.selections[step.id]) {
                    this.showToast('请选择一个选项', 'warning');
                    return false;
                }
                return true;
            }
        }

        updateNavigation() {
            const prevBtn = document.getElementById('selectorPrevBtn');
            const nextBtn = document.getElementById('selectorNextBtn');
            
            if (prevBtn) {
                prevBtn.style.visibility = this.currentStep > 0 ? 'visible' : 'hidden';
            }
            
            if (nextBtn) {
                const isLast = this.currentStep === STEPS.length - 1;
                nextBtn.innerHTML = isLast ? '<i class="fas fa-check"></i> 确认并开始采集' : '下一步 <i class="fas fa-arrow-right"></i>';
                nextBtn.disabled = !this.canProceed();
            }
        }

        canProceed() {
            const step = STEPS[this.currentStep];
            if (step.id === 'subject') {
                const fields = this.template.subjectFields || DEFAULT_SUBJECT_FIELDS;
                for (const field of fields) {
                    if (!field.required) continue;
                    const input = document.getElementById(`subject_${field.id}`);
                    if (input && !input.value.trim()) return false;
                }
                return true;
            }
            return !!this.selections[step.id];
        }

        complete() {
            console.log('[CollectionSelector] 完成选择:', this.selections);
            
            // 保存用户信息
            if (this.selections.subject) {
                const userData = { ...this.selections.subject, timestamp: new Date().toISOString() };
                localStorage.setItem('emg_current_user', JSON.stringify(userData));
            }

            // 辅助函数：根据id获取name（用于中文目录）
            const getNameById = (category, id) => {
                const items = this.template[category] || [];
                const item = items.find(i => i.id === id);
                return item ? item.name : id;  // 如果找到则返回name，否则返回id本身
            };
            
            // 获取任务名称
            const getTaskName = (taskId) => {
                const tasks = this.template.tasks || [];
                console.log('[CollectionSelector] 查找任务名称, taskId:', taskId);
                console.log('[CollectionSelector] 可用的tasks:', tasks);
                const task = tasks.find(t => t.id === taskId);
                console.log('[CollectionSelector] 找到的task:', task);
                return task ? task.name : taskId;
            };

            // 保存采集配置（包含所有分类信息，用于目录结构）
            // 注意：所有分类都保存 name（中文）用于目录命名
            const config = {
                // 保存id用于程序逻辑
                task_id: this.selections.task,
                category1_id: this.selections.category1,
                category2_id: this.selections.category2,
                category4_id: this.selections.category4,
                // 保存name用于目录命名（支持中文）
                task: getTaskName(this.selections.task),
                category1: getNameById('category1', this.selections.category1),
                category2: getNameById('category2', this.selections.category2),
                category4: getNameById('category4', this.selections.category4),
                // category3 不需要选择，从模板中获取全部启用的子场景
                category3List: (this.template.category3 || []).filter(c => c.enabled),
                subject: this.selections.subject,
                templateName: this.template.templateName,
                timestamp: new Date().toISOString()
            };
            window.currentCollectionConfig = config;
            localStorage.setItem('emg_current_collection_config', JSON.stringify(config));

            console.log('[CollectionSelector] 采集配置已保存:', config);
            console.log('[CollectionSelector] 目录结构将为:', 
                `${config.task}/${config.category1}/${config.category2}/${config.category4}/`);

            this.close();
            this.showToast('配置完成，即将开始采集', 'success');

            // 启动BLE
            if (window.BleControl?.isConnected) {
                window.BleControl.startAll();
            }

            // 切换到采集页面
            setTimeout(() => {
                if (window.pageSwitchController) {
                    window.pageSwitchController.currentUser = this.selections.subject;
                    window.pageSwitchController.updateUserDisplay();
                    window.pageSwitchController.showCollection();
                }
                
                // 切换任务类型
                if (window.collectionController) {
                    const taskMap = { 'discrete_gesture': 'discrete', 'continual_gesture_1': 'continuous1', 'continual_gesture_2': 'continuous2' };
                    window.collectionController.selectTask(taskMap[this.selections.task] || 'discrete');
                }
            }, 500);
        }

        showToast(message, type = 'info') {
            const toast = document.getElementById('toast');
            if (toast) {
                toast.className = 'toast' + (type === 'error' ? ' error' : type === 'warning' ? ' warning' : '');
                toast.innerHTML = `<i class="fas fa-${type === 'success' ? 'check' : type === 'warning' ? 'exclamation-triangle' : 'info'}-circle"></i> ${message}`;
                toast.classList.add('visible');
                setTimeout(() => toast.classList.remove('visible'), 2500);
            }
        }
    }

    // 创建全局实例
    window.CollectionSelector = CollectionSelector;
    
    function initSelector() {
        const selector = new CollectionSelector();
        window.collectionSelector = selector;
        setTimeout(() => selector.init(), 100);
        console.log('[CollectionSelector] 实例已创建');
    }
    
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initSelector);
    } else {
        initSelector();
    }

    console.log('[CollectionSelector] 模块加载完成');
})();
