/**
 * backend-manager.js - 后台数据统计模块（左右分栏版）
 * 
 * 功能：
 * 1. 左侧：统计总览（文件数、受试者、任务分类）
 * 2. 右侧：文件列表（支持排序）
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
            this.sortMode = 'time-desc'; // 默认按时间降序
            this.currentPage = 1;
            this.pageSize = 15;
        }

        /**
         * 初始化
         */
        init() {
            console.log('[BackendManager] 初始化开始');
            this.bindEvents();
            this.renderEmptyState();
            console.log('[BackendManager] 初始化完成 ✓');
        }

        /**
         * 绑定事件
         */
        bindEvents() {
            // 选择文件夹按钮
            const selectFolderBtn = document.getElementById('selectFolderBtn');
            if (selectFolderBtn) {
                selectFolderBtn.addEventListener('click', () => this.openFolderSelector());
            }

            // 刷新按钮
            const refreshBtn = document.getElementById('refreshBtn');
            if (refreshBtn) {
                refreshBtn.addEventListener('click', () => this.openFolderSelector());
            }

            // 排序按钮
            const sortBtn = document.getElementById('sortBtn');
            if (sortBtn) {
                sortBtn.addEventListener('click', () => this.toggleSort());
            }

            // 创建隐藏的文件输入
            if (!document.getElementById('folderInput')) {
                const input = document.createElement('input');
                input.type = 'file';
                input.id = 'folderInput';
                input.webkitdirectory = true;
                input.directory = true;
                input.multiple = true;
                input.style.display = 'none';
                input.addEventListener('change', (e) => this.handleFolderSelected(e));
                document.body.appendChild(input);
            }

            console.log('[BackendManager] 事件绑定完成');
        }

        /**
         * 打开文件夹选择器
         */
        openFolderSelector() {
            const input = document.getElementById('folderInput');
            if (input) {
                input.click();
            }
        }

        /**
         * 处理文件夹选择
         */
        handleFolderSelected(event) {
            const allFiles = Array.from(event.target.files);
            if (allFiles.length === 0) return;

            console.log('[BackendManager] 扫描到', allFiles.length, '个文件');

            // 只过滤h5文件
            const h5Files = allFiles.filter(f => 
                f.name.endsWith('.h5') || f.name.endsWith('.hdf5')
            );

            console.log('[BackendManager] 找到', h5Files.length, '个h5文件');

            // 保存文件列表
            this.files = h5Files.map(file => ({
                name: file.name,
                size: file.size,
                lastModified: file.lastModified,
                path: file.webkitRelativePath || file.name
            }));

            // 解析并统计
            this.parseAndAnalyze();
            
            // 渲染
            this.render();

            // 清空input
            event.target.value = '';
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
                        <p>请选择 storage 文件夹</p>
                        <p class="text-sm">点击右上角"选择文件夹"按钮</p>
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
            this.showToast(`已扫描 ${this.stats.totalFiles} 个文件`, 'success');
        }

        /**
         * 渲染左侧统计面板
         */
        renderStats() {
            const container = document.getElementById('stats-panel');
            if (!container) return;

            if (this.files.length === 0) {
                container.innerHTML = `
                    <div class="empty-state">
                        <i class="fa fa-exclamation-circle"></i>
                        <p>未找到 .h5 文件</p>
                    </div>
                `;
                return;
            }

            const subjectCount = Object.keys(this.stats.subjects).length;
            const taskCount = Object.keys(this.stats.tasks).length;

            let html = `
                <!-- 总览卡片 -->
                <div class="stats-cards">
                    <div class="stat-card total">
                        <div class="stat-icon"><i class="fa fa-database"></i></div>
                        <div class="stat-info">
                            <div class="stat-value">${this.stats.totalFiles}</div>
                            <div class="stat-label">数据文件</div>
                        </div>
                    </div>
                    <div class="stat-card subjects">
                        <div class="stat-icon"><i class="fa fa-users"></i></div>
                        <div class="stat-info">
                            <div class="stat-value">${subjectCount}</div>
                            <div class="stat-label">受试者</div>
                        </div>
                    </div>
                    <div class="stat-card tasks">
                        <div class="stat-icon"><i class="fa fa-tasks"></i></div>
                        <div class="stat-info">
                            <div class="stat-value">${taskCount}</div>
                            <div class="stat-label">任务类型</div>
                        </div>
                    </div>
                    <div class="stat-card size">
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
                        if (!manager._initialized) {
                            manager.init();
                            manager._initialized = true;
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
