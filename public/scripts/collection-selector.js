/**
 * collection-selector.js - 采集选择流程控制器
 * 
 * 功能：
 * 1. 分步选择界面：任务类型 → 大类 → 大场景 → 人群 → 受试者信息
 * 2. 从配置模板读取选项
 * 3. 验证并保存选择结果
 * 4. 与采集控制器协同工作
 */

(function() {
    'use strict';

    console.log('[CollectionSelector] 模块加载开始...');

    // 选择步骤定义
    const STEPS = [
        { id: 'task', label: '采集任务', category: 'tasks', icon: 'fa-tasks' },
        { id: 'category1', label: '大类', category: 'category1', icon: 'fa-layer-group' },
        { id: 'category2', label: '大场景', category: 'category2', icon: 'fa-map-marker-alt' },
        { id: 'category4', label: '人群', category: 'category4', icon: 'fa-users' },
        { id: 'subject', label: '受试者信息', category: 'subject', icon: 'fa-user-edit' }
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
                subjectFields: [
                    { id: 'name', label: '姓名', type: 'text', required: true },
                    { id: 'id', label: '编号', type: 'text', required: true },
                    { id: 'age', label: '年龄', type: 'number', required: true, min: 1, max: 120 },
                    { id: 'gender', label: '性别', type: 'select', required: true, options: [
                        { value: 'male', label: '男' }, { value: 'female', label: '女' }
                    ]},
                    { id: 'hand', label: '惯用手', type: 'select', required: true, options: [
                        { value: 'right', label: '右手' }, { value: 'left', label: '左手' }, { value: 'both', label: '双手' }
                    ]},
                    { id: 'note', label: '备注', type: 'text', required: false }
                ]
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
                    html += '<div class="step-line"></div>';
                }
            });
            indicator.innerHTML = html;
        }

        renderOptionsGrid(step) {
            let options = step.id === 'task' ? (this.template.tasks || []) : (this.template[step.category] || []);
            const enabledOptions = options.filter(opt => opt.enabled !== false);
            
            if (enabledOptions.length === 0) {
                return `<div class="selector-empty"><i class="fas fa-exclamation-circle"></i><p>没有可用的选项</p></div>`;
            }

            const selectedId = this.selections[step.id];
            let html = '<div class="selector-options-grid">';
            
            enabledOptions.forEach(option => {
                const isSelected = selectedId === option.id;
                const icon = this.getOptionIcon(option.id);
                html += `
                    <div class="selector-option ${isSelected ? 'selected' : ''}" data-id="${option.id}" data-step="${step.id}">
                        <div class="option-icon">${icon}</div>
                        <div class="option-name">${option.name}</div>
                        <div class="option-check"><i class="fas fa-check-circle"></i></div>
                    </div>
                `;
            });
            
            html += '</div>';
            return html;
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
                'normal': '<i class="fas fa-user"></i>',
                'exercise': '<i class="fas fa-dumbbell"></i>'
            };
            return iconMap[optionId] || '<i class="fas fa-circle"></i>';
        }

        renderSubjectForm() {
            const fields = this.template.subjectFields || [];
            const saved = JSON.parse(localStorage.getItem('emg_current_user') || '{}');

            let html = '<div class="selector-subject-form">';
            for (let i = 0; i < fields.length; i += 2) {
                html += '<div class="form-row">';
                html += this.renderFormField(fields[i], saved);
                if (fields[i + 1]) html += this.renderFormField(fields[i + 1], saved);
                html += '</div>';
            }
            html += '</div>';
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
                input = `<input type="number" id="subject_${field.id}" value="${value}" ${field.min !== undefined ? `min="${field.min}"` : ''} ${field.max !== undefined ? `max="${field.max}"` : ''} ${field.required ? 'required' : ''}>`;
            } else {
                input = `<input type="text" id="subject_${field.id}" value="${value}" ${field.required ? 'required' : ''}>`;
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
            document.querySelectorAll('.selector-subject-form input, .selector-subject-form select').forEach(input => {
                input.addEventListener('input', () => this.updateNavigation());
                input.addEventListener('change', () => this.updateNavigation());
            });
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
                const fields = this.template.subjectFields || [];
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
                const fields = this.template.subjectFields || [];
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

            // 保存采集配置
            const config = {
                task: this.selections.task,
                category1: this.selections.category1,
                category2: this.selections.category2,
                category4: this.selections.category4,
                subject: this.selections.subject,
                templateName: this.template.templateName,
                timestamp: new Date().toISOString()
            };
            window.currentCollectionConfig = config;
            localStorage.setItem('emg_current_collection_config', JSON.stringify(config));

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
