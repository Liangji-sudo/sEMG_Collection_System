/**
 * backend-manager.js - 后台数据统计模块（左右分栏版）
 * 
 * 功能：
 * 1. 左侧：统计总览（文件数、受试者、任务分类）+ 变化气泡
 * 2. 右侧：文件列表（支持排序）
 * 3. 自动从服务端 /api/storage/files 加载 storage/ 目录
 * 
 * 文件命名格式：[采集任务名]_[受试者编号]_[年月日]_[时分秒].h5
 */

(function() {
    'use strict';

    console.log('[BackendManager] ====== 模块开始加载 ======');

    class BackendManager {
        constructor() {
            this.files = [];
            this.stats = {
                totalFiles: 0,
                totalSize: 0,
                subjects: {},
                tasks: {}
            };
            this.lastStats = null; // 上次打开时的统计数据
            this.sortMode = 'time-desc'; // 默认按时间降序
            this.currentPage = 1;
            this.pageSize = 15;
        }

        /**
         * 初始化（只执行一次）
         */
        init() {
            console.log('[BackendManager] 初始化开始');
            this.bindEvents();
            console.log('[BackendManager] 初始化完成 ✓');
        }

        /**
         * 每次进入后台页面时调用
         */
        onPageShow() {
            console.log('[BackendManager] 进入后台页面，开始加载数据...');
            this.loadLastStats();  // 先加载上次的统计数据
            this.renderLoadingState();
            this.loadStorageFiles();  // 再加载当前数据
        }

        /**
         * 加载上次保存的统计数据
         */
        loadLastStats() {
            const saved = localStorage.getItem('emg_backend_last_stats');
            if (saved) {
                try {
                    this.lastStats = JSON.parse(saved);
                    console.log('[BackendManager] 已加载上次统计数据:', this.lastStats);
                } catch (e) {
                    console.warn('[BackendManager] 解析上次统计数据失败:', e);
                    this.lastStats = null;
                }
            } else {
                this.lastStats = null;
            }
        }

        /**
         * 保存当前统计数据供下次比较（仅在离开页面时保存）
         */
        saveCurrentStats() {
            const statsToSave = {
                totalFiles: this.stats.totalFiles,
                totalSize: this.stats.totalSize,
                subjectCount: Object.keys(this.stats.subjects).length,
                taskCount: Object.keys(this.stats.tasks).length,
                timestamp: Date.now()
            };
            localStorage.setItem('emg_backend_last_stats', JSON.stringify(statsToSave));
            console.log('[BackendManager] 已保存当前统计数据');
        }

        /**
         * 绑定事件
         */
        bindEvents() {
            // 刷新按钮
            const refreshBtn = document.getElementById('refreshBtn');
            if (refreshBtn) {
                refreshBtn.addEventListener('click', () => this.loadStorageFiles());
            }

            // 排序按钮
            const sortBtn = document.getElementById('sortBtn');
            if (sortBtn) {
                sortBtn.addEventListener('click', () => this.toggleSort());
            }

            console.log('[BackendManager] 事件绑定完成');
        }

        /**
         * 从服务端加载 storage 目录文件列表
         */
        async loadStorageFiles() {
            console.log('[BackendManager] 开始加载 storage 目录...');
            this.renderLoadingState();

            try {
                const response = await fetch('/api/storage/files');
                const data = await response.json();

                if (data.success) {
                    console.log('[BackendManager] 从服务端获取到', data.files.length, '个文件');
                    
                    this.files = data.files;
                    this.parseAndAnalyze();
                    this.render();
                    this.showToast(`已加载 ${data.count} 个文件`, 'success');
                } else {
                    console.error('[BackendManager] 加载失败:', data.error);
                    this.renderErrorState(data.error);
                    this.showToast('加载失败: ' + data.error, 'error');
                }
            } catch (err) {
                console.error('[BackendManager] 请求失败:', err);
                this.renderErrorState('无法连接到服务器');
                this.showToast('无法连接到服务器', 'error');
            }
        }

        /**
         * 解析文件名并统计
         */
        parseAndAnalyze() {
            this.stats = {
                totalFiles: this.files.length,
                totalSize: 0,
                subjects: {},
                tasks: {}
            };

            this.files.forEach(file => {
                this.stats.totalSize += file.size;
                const parsed = this.parseFileName(file.name);
                
                if (parsed) {
                    file.parsed = parsed;
                    
                    // 按受试者统计
                    if (!this.stats.subjects[parsed.subjectId]) {
                        this.stats.subjects[parsed.subjectId] = {
                            count: 0,
                            tasks: {}
                        };
                    }
                    this.stats.subjects[parsed.subjectId].count++;
                    
                    if (!this.stats.subjects[parsed.subjectId].tasks[parsed.taskName]) {
                        this.stats.subjects[parsed.subjectId].tasks[parsed.taskName] = 0;
                    }
                    this.stats.subjects[parsed.subjectId].tasks[parsed.taskName]++;

                    // 按任务统计
                    if (!this.stats.tasks[parsed.taskName]) {
                        this.stats.tasks[parsed.taskName] = {
                            count: 0,
                            subjects: new Set()
                        };
                    }
                    this.stats.tasks[parsed.taskName].count++;
                    this.stats.tasks[parsed.taskName].subjects.add(parsed.subjectId);
                }
            });

            // 应用排序
            this.applySorting();

            console.log('[BackendManager] 统计完成:', this.stats);
        }

        /**
         * 解析文件名
         */
        parseFileName(fileName) {
            const baseName = fileName.replace(/\.(h5|hdf5)$/i, '');
            
            // 标准格式：任务名_受试者ID_日期_时间
            const regex = /^(.+)_(S\d+|s\d+)_(\d{8})_(\d{6})$/;
            const match = baseName.match(regex);

            if (match) {
                return {
                    taskName: match[1],
                    subjectId: match[2].toUpperCase(),
                    date: match[3],
                    time: match[4],
                    dateFormatted: this.formatDateString(match[3]),
                    timeFormatted: this.formatTimeString(match[4])
                };
            }

            // 宽松匹配
            const looseRegex = /^(.+?)_(S\d+|s\d+)_?(.*)$/i;
            const looseMatch = baseName.match(looseRegex);
            
            if (looseMatch) {
                return {
                    taskName: looseMatch[1],
                    subjectId: looseMatch[2].toUpperCase(),
                    date: '',
                    time: '',
                    dateFormatted: '',
                    timeFormatted: ''
                };
            }

            return null;
        }

        formatDateString(dateStr) {
            if (dateStr.length !== 8) return dateStr;
            return `${dateStr.slice(0, 4)}-${dateStr.slice(4, 6)}-${dateStr.slice(6, 8)}`;
        }

        formatTimeString(timeStr) {
            if (timeStr.length !== 6) return timeStr;
            return `${timeStr.slice(0, 2)}:${timeStr.slice(2, 4)}:${timeStr.slice(4, 6)}`;
        }

        /**
         * 切换排序
         */
        toggleSort() {
            const modes = ['time-desc', 'time-asc', 'name-asc', 'name-desc', 'size-desc', 'size-asc'];
            const currentIndex = modes.indexOf(this.sortMode);
            this.sortMode = modes[(currentIndex + 1) % modes.length];
            
            const labels = {
                'time-desc': '时间 ↓',
                'time-asc': '时间 ↑',
                'name-asc': '名称 A-Z',
                'name-desc': '名称 Z-A',
                'size-desc': '大小 ↓',
                'size-asc': '大小 ↑'
            };
            
            this.showToast(`排序: ${labels[this.sortMode]}`, 'success');
            this.applySorting();
            this.currentPage = 1;
            this.renderFileList();
        }

        /**
         * 应用排序
         */
        applySorting() {
            switch (this.sortMode) {
                case 'time-desc':
                    this.files.sort((a, b) => b.lastModified - a.lastModified);
                    break;
                case 'time-asc':
                    this.files.sort((a, b) => a.lastModified - b.lastModified);
                    break;
                case 'name-asc':
                    this.files.sort((a, b) => a.name.localeCompare(b.name));
                    break;
                case 'name-desc':
                    this.files.sort((a, b) => b.name.localeCompare(a.name));
                    break;
                case 'size-desc':
                    this.files.sort((a, b) => b.size - a.size);
                    break;
                case 'size-asc':
                    this.files.sort((a, b) => a.size - b.size);
                    break;
            }
        }

        /**
         * 渲染加载状态
         */
        renderLoadingState() {
            const leftPanel = document.getElementById('stats-panel');
            const rightPanel = document.getElementById('file-list-panel');
            
            if (leftPanel) {
                leftPanel.innerHTML = `
                    <div class="empty-state">
                        <i class="fa fa-spinner fa-spin"></i>
                        <p>正在加载 storage 目录...</p>
                    </div>
                `;
            }
            
            if (rightPanel) {
                rightPanel.innerHTML = `
                    <div class="empty-state">
                        <i class="fa fa-spinner fa-spin"></i>
                        <p>加载中...</p>
                    </div>
                `;
            }
        }

        /**
         * 渲染错误状态
         */
        renderErrorState(error) {
            const leftPanel = document.getElementById('stats-panel');
            const rightPanel = document.getElementById('file-list-panel');
            
            if (leftPanel) {
                leftPanel.innerHTML = `
                    <div class="empty-state">
                        <i class="fa fa-exclamation-triangle"></i>
                        <p>加载失败</p>
                        <p class="text-sm">${error}</p>
                    </div>
                `;
            }
            
            if (rightPanel) {
                rightPanel.innerHTML = `
                    <div class="empty-state">
                        <i class="fa fa-exclamation-triangle"></i>
                        <p>点击刷新重试</p>
                    </div>
                `;
            }
        }

        /**
         * 渲染空状态
         */
        renderEmptyState() {
            const leftPanel = document.getElementById('stats-panel');
            const rightPanel = document.getElementById('file-list-panel');
            
            if (leftPanel) {
                leftPanel.innerHTML = `
                    <div class="empty-state">
                        <i class="fa fa-chart-bar"></i>
                        <p>暂无统计数据</p>
                    </div>
                `;
            }
            
            if (rightPanel) {
                rightPanel.innerHTML = `
                    <div class="empty-state">
                        <i class="fa fa-folder-open"></i>
                        <p>storage/ 目录为空</p>
                        <p class="text-sm">采集数据后自动显示</p>
                    </div>
                `;
            }
        }

        /**
         * 渲染所有内容
         */
        render() {
            this.renderStats();
            this.renderFileList();
        }

        /**
         * 计算统计数据的变化量
         */
        calculateChanges(subjectCount, taskCount) {
            const changes = {
                totalFiles: null,
                subjectCount: null,
                taskCount: null,
                totalSize: null
            };

            if (!this.lastStats) {
                return changes;
            }

            changes.totalFiles = this.stats.totalFiles - this.lastStats.totalFiles;
            changes.subjectCount = subjectCount - this.lastStats.subjectCount;
            changes.taskCount = taskCount - this.lastStats.taskCount;
            changes.totalSize = this.stats.totalSize - this.lastStats.totalSize;

            return changes;
        }

        /**
         * 渲染变化气泡
         */
        renderChangeBadge(change, isSize = false) {
            if (change === null || change === 0) {
                return '';
            }

            const isPositive = change > 0;
            const badgeClass = isPositive ? 'positive' : 'negative';
            const prefix = isPositive ? '+' : '';
            
            let displayValue;
            if (isSize) {
                displayValue = prefix + this.formatFileSize(Math.abs(change));
            } else {
                displayValue = prefix + change;
            }

            return `<span class="stat-change-badge ${badgeClass}">${displayValue}</span>`;
        }

        /**
         * 渲染左侧统计面板
         */
        renderStats() {
            const container = document.getElementById('stats-panel');
            if (!container) return;

            if (this.files.length === 0) {
                this.renderEmptyState();
                return;
            }

            const subjectCount = Object.keys(this.stats.subjects).length;
            const taskCount = Object.keys(this.stats.tasks).length;

            // 计算变化量
            const changes = this.calculateChanges(subjectCount, taskCount);

            let html = `
                <!-- 总览卡片 -->
                <div class="stats-cards">
                    <div class="stat-card total">
                        ${this.renderChangeBadge(changes.totalFiles)}
                        <div class="stat-icon"><i class="fa fa-database"></i></div>
                        <div class="stat-info">
                            <div class="stat-value">${this.stats.totalFiles}</div>
                            <div class="stat-label">数据文件</div>
                        </div>
                    </div>
                    <div class="stat-card subjects">
                        ${this.renderChangeBadge(changes.subjectCount)}
                        <div class="stat-icon"><i class="fa fa-users"></i></div>
                        <div class="stat-info">
                            <div class="stat-value">${subjectCount}</div>
                            <div class="stat-label">受试者</div>
                        </div>
                    </div>
                    <div class="stat-card tasks">
                        ${this.renderChangeBadge(changes.taskCount)}
                        <div class="stat-icon"><i class="fa fa-tasks"></i></div>
                        <div class="stat-info">
                            <div class="stat-value">${taskCount}</div>
                            <div class="stat-label">任务类型</div>
                        </div>
                    </div>
                    <div class="stat-card size">
                        ${this.renderChangeBadge(changes.totalSize, true)}
                        <div class="stat-icon"><i class="fa fa-hdd-o"></i></div>
                        <div class="stat-info">
                            <div class="stat-value">${this.formatFileSize(this.stats.totalSize)}</div>
                            <div class="stat-label">数据总量</div>
                        </div>
                    </div>
                </div>

                <!-- 按任务统计 -->
                <div class="stats-section">
                    <h3 class="section-title">
                        <i class="fa fa-list-alt"></i> 按任务统计
                    </h3>
                    <div class="stats-list">
                        ${this.renderTaskStats()}
                    </div>
                </div>

                <!-- 按受试者统计 -->
                <div class="stats-section">
                    <h3 class="section-title">
                        <i class="fa fa-user"></i> 按受试者统计
                    </h3>
                    <div class="stats-list">
                        ${this.renderSubjectStats()}
                    </div>
                </div>
            `;

            container.innerHTML = html;
        }

        /**
         * 渲染任务统计
         */
        renderTaskStats() {
            const tasks = this.stats.tasks;
            let html = '';

            Object.keys(tasks).sort().forEach(taskName => {
                const task = tasks[taskName];
                const subjectCount = task.subjects.size;
                
                html += `
                    <div class="stats-item">
                        <div class="item-name">
                            <i class="fa fa-file-text-o"></i>
                            <span>${taskName}</span>
                        </div>
                        <div class="item-meta">
                            <span class="badge">${task.count} 文件</span>
                            <span class="badge secondary">${subjectCount} 人</span>
                        </div>
                    </div>
                `;
            });

            return html || '<div class="empty-hint">暂无数据</div>';
        }

        /**
         * 渲染受试者统计
         */
        renderSubjectStats() {
            const subjects = this.stats.subjects;
            let html = '';

            Object.keys(subjects).sort().forEach(subjectId => {
                const subject = subjects[subjectId];
                const taskList = Object.keys(subject.tasks).map(t => {
                    const count = subject.tasks[t];
                    return `${t}(${count})`;
                }).join(', ');
                
                html += `
                    <div class="stats-item">
                        <div class="item-name">
                            <i class="fa fa-user-circle-o"></i>
                            <span>${subjectId}</span>
                        </div>
                        <div class="item-meta">
                            <span class="badge">${subject.count} 文件</span>
                        </div>
                        <div class="item-tasks">${taskList}</div>
                    </div>
                `;
            });

            return html || '<div class="empty-hint">暂无数据</div>';
        }

        /**
         * 渲染右侧文件列表
         */
        renderFileList() {
            const container = document.getElementById('file-list-panel');
            if (!container) return;

            if (this.files.length === 0) {
                container.innerHTML = `
                    <div class="empty-state">
                        <i class="fa fa-folder-open"></i>
                        <p>暂无文件</p>
                    </div>
                `;
                return;
            }

            // 分页
            const totalPages = Math.ceil(this.files.length / this.pageSize);
            const start = (this.currentPage - 1) * this.pageSize;
            const end = start + this.pageSize;
            const pageFiles = this.files.slice(start, end);

            let html = `
                <div class="file-list">
                    ${pageFiles.map((file, index) => this.renderFileItem(file, start + index)).join('')}
                </div>
                ${this.renderPagination(totalPages)}
            `;

            container.innerHTML = html;
            this.bindPaginationEvents();
        }

        /**
         * 渲染单个文件项
         */
        renderFileItem(file, index) {
            const date = new Date(file.lastModified);
            const dateStr = this.formatDateTime(date);
            const sizeStr = this.formatFileSize(file.size);
            
            return `
                <div class="file-item" data-index="${index}">
                    <div class="file-icon">
                        <i class="fa fa-file"></i>
                    </div>
                    <div class="file-info">
                        <div class="file-name">${file.name}</div>
                        <div class="file-meta">
                            <span><i class="fa fa-calendar"></i> ${dateStr}</span>
                            <span><i class="fa fa-hdd-o"></i> ${sizeStr}</span>
                        </div>
                    </div>
                </div>
            `;
        }

        /**
         * 渲染分页
         */
        renderPagination(totalPages) {
            if (totalPages <= 1) return '';

            let html = '<div class="pagination">';
            
            // 上一页
            html += `<button class="page-btn ${this.currentPage <= 1 ? 'disabled' : ''}" data-action="prev">
                <i class="fa fa-angle-left"></i>
            </button>`;

            // 页码
            const maxVisible = 5;
            let startPage = Math.max(1, this.currentPage - Math.floor(maxVisible / 2));
            let endPage = Math.min(totalPages, startPage + maxVisible - 1);
            
            if (endPage - startPage + 1 < maxVisible) {
                startPage = Math.max(1, endPage - maxVisible + 1);
            }

            for (let i = startPage; i <= endPage; i++) {
                html += `<button class="page-btn ${i === this.currentPage ? 'active' : ''}" data-page="${i}">${i}</button>`;
            }

            // 下一页
            html += `<button class="page-btn ${this.currentPage >= totalPages ? 'disabled' : ''}" data-action="next">
                <i class="fa fa-angle-right"></i>
            </button>`;

            html += '</div>';
            return html;
        }

        /**
         * 绑定分页事件
         */
        bindPaginationEvents() {
            const pagination = document.querySelector('#file-list-panel .pagination');
            if (!pagination) return;

            pagination.addEventListener('click', (e) => {
                const btn = e.target.closest('.page-btn');
                if (!btn || btn.classList.contains('disabled')) return;

                const action = btn.dataset.action;
                const page = btn.dataset.page;

                if (action === 'prev') {
                    this.currentPage = Math.max(1, this.currentPage - 1);
                } else if (action === 'next') {
                    this.currentPage++;
                } else if (page) {
                    this.currentPage = parseInt(page);
                }

                this.renderFileList();
            });
        }

        /**
         * 格式化文件大小
         */
        formatFileSize(bytes) {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }

        /**
         * 格式化日期时间
         */
        formatDateTime(date) {
            const year = date.getFullYear();
            const month = String(date.getMonth() + 1).padStart(2, '0');
            const day = String(date.getDate()).padStart(2, '0');
            const hour = String(date.getHours()).padStart(2, '0');
            const min = String(date.getMinutes()).padStart(2, '0');
            return `${year}-${month}-${day} ${hour}:${min}`;
        }

        /**
         * 显示Toast
         */
        showToast(message, type = 'success') {
            let toast = document.getElementById('backendToast');
            if (!toast) {
                toast = document.createElement('div');
                toast.id = 'backendToast';
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
    function initBackendManager() {
        console.log('[BackendManager] 准备初始化...');
        
        const manager = new BackendManager();
        window.backendManager = manager;

        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                    const backendPage = document.getElementById('backend-page');
                    if (backendPage && !backendPage.classList.contains('hidden')) {
                        // 首次初始化
                        if (!manager._bindingDone) {
                            manager.init();
                            manager._bindingDone = true;
                        }
                        // 每次进入页面都刷新数据
                        manager.onPageShow();
                    } else if (backendPage && backendPage.classList.contains('hidden')) {
                        // 离开页面时保存统计数据
                        if (manager.stats.totalFiles > 0) {
                            manager.saveCurrentStats();
                        }
                    }
                }
            });
        });

        const backendPage = document.getElementById('backend-page');
        if (backendPage) {
            observer.observe(backendPage, { attributes: true });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initBackendManager);
    } else {
        initBackendManager();
    }

    console.log('[BackendManager] ====== 模块加载完成 ======');

})();
