/**
 * page-switch.js - 页面切换控制器
 * 
 * 职责：
 * 1. 页面切换（欢迎页 ↔ 采集页 ↔ 后台页）
 * 2. 用户信息管理（表单、保存、显示）
 * 3. 波形显示控制
 * 4. Toast提示
 * 
 * 修改：点击"开始采集"按钮时，打开采集选择流程（而不是直接显示用户信息弹窗）
 */

(function() {
    'use strict';

    console.log('[PageSwitch] 脚本加载开始...');

    class PageSwitchController {
        constructor() {
            this.currentUser = null;
            this.waveformStarted = false;
            
            this.init();
        }

        init() {
            console.log('[PageSwitch] 初始化开始...');
            this.bindEvents();
            this.loadSavedUser();
            this.updateUserDisplay();
            console.log('[PageSwitch] 初始化完成');
        }

        bindEvents() {
            // 开始采集按钮（首页）- 使用新的采集选择流程
            const startCollectionBtn = document.getElementById('startCollectionBtn');
            if (startCollectionBtn) {
                startCollectionBtn.addEventListener('click', () => {
                    console.log('[PageSwitch] 点击开始采集');

                    // 清除续采按钮标记（用户选择普通新任务）
                    delete window.__showBreakpointResumeAfterAbort;

                    // 使用新的采集选择流程
                    if (window.collectionSelector) {
                        window.collectionSelector.open();
                    } else {
                        // 降级到旧的用户信息弹窗
                        console.warn('[PageSwitch] 采集选择器未加载，使用旧模式');
                        this.showUserModal();
                    }
                });
            }

            // 后台按钮（首页）
            const backendBtn = document.getElementById('backendBtn');
            if (backendBtn) {
                backendBtn.addEventListener('click', () => {
                    console.log('[PageSwitch] 点击后台按钮');
                    this.showBackend();
                });
            }

            // 【Phase 2】断点续采按钮（首页）
            const resumeBtn = document.getElementById('resumeBreakpointBtn');
            if (resumeBtn) {
                resumeBtn.addEventListener('click', () => {
                    console.log('[PageSwitch] 点击断点续采');
                    this.resumeBreakpoint();
                });
            }

            // 【Phase 6】导入断点 JSON（首页，隐藏 file input）
            const importInput = document.getElementById('importBreakpointInput');
            if (importInput) {
                importInput.addEventListener('change', (e) => {
                    this._handleImportBreakpoint(e);
                });
            }
            const importBtn = document.getElementById('importBreakpointBtn');
            if (importBtn) {
                importBtn.addEventListener('click', () => {
                    if (importInput) importInput.click();
                });
            }

            // 用户表单提交（旧模式降级用）
            const userForm = document.getElementById('userForm');
            if (userForm) {
                userForm.addEventListener('submit', (e) => {
                    e.preventDefault();
                    this.submitUserInfo();
                });
            }

            // 返回按钮（采集页）
            const backBtn = document.getElementById('backBtn');
            if (backBtn) {
                backBtn.addEventListener('click', () => {
                    console.log('[PageSwitch] 点击返回按钮（采集页）');
                    this.backToWelcome();
                });
            }

            // 返回按钮（后台页）
            const backBtn2 = document.getElementById('back-to-initial-2');
            if (backBtn2) {
                backBtn2.addEventListener('click', () => {
                    console.log('[PageSwitch] 点击返回按钮（后台页）');
                    this.backToWelcomeFromBackend();
                });
            }

            // 教程按钮（采集页）
            const tutorialBtn = document.getElementById('tutorialBtn');
            if (tutorialBtn) {
                tutorialBtn.addEventListener('click', () => {
                    console.log('[PageSwitch] 点击教程按钮');
                    this.showTutorialModal();
                });
            }

            // 教程弹窗关闭按钮
            const closeTutorialBtn = document.getElementById('closeTutorialModal');
            if (closeTutorialBtn) {
                closeTutorialBtn.addEventListener('click', () => {
                    this.hideTutorialModal();
                });
            }

            // 教程弹窗跳过按钮
            const tutorialSkipBtn = document.getElementById('tutorialSkipBtn');
            if (tutorialSkipBtn) {
                tutorialSkipBtn.addEventListener('click', () => {
                    this.hideTutorialModal();
                });
            }

            // 教程弹窗重播按钮
            const tutorialReplayBtn = document.getElementById('tutorialReplayBtn');
            if (tutorialReplayBtn) {
                tutorialReplayBtn.addEventListener('click', () => {
                    this.replayTutorialVideo();
                });
            }

            // 点击遮罩层关闭教程弹窗
            const tutorialModal = document.getElementById('tutorialModal');
            if (tutorialModal) {
                tutorialModal.addEventListener('click', (e) => {
                    if (e.target === tutorialModal) {
                        this.hideTutorialModal();
                    }
                });
            }

            // 关闭用户模态框按钮（如果有）
            const closeModalBtn = document.getElementById('closeUserModal');
            if (closeModalBtn) {
                closeModalBtn.addEventListener('click', () => {
                    this.hideUserModal();
                });
            }
        }

        // ==================== 页面切换 ====================
        
        /**
         * 显示欢迎页面
         */
        showWelcome() {
            console.log('[PageSwitch] 切换到欢迎页面');

            const welcomeScreen = document.getElementById('welcomeScreen');
            const collectionScreen = document.getElementById('collectionScreen');
            const backendPage = document.getElementById('backend-page');

            if (welcomeScreen) welcomeScreen.classList.remove('hidden');
            if (collectionScreen) collectionScreen.style.display = 'none';
            if (backendPage) backendPage.classList.add('hidden');

            // 【修复】清理 toast
            const toast = document.getElementById('toast');
            if (toast) {
                toast.classList.remove('visible');
            }

            this.stopWaveform();

            // 【新增】返回首页时停止所有活跃的 stream（preview 或 collection）
            if (window.BleControl && window.BleControl.isConnected) {
                console.log('[PageSwitch] 停止所有 stream（返回首页）');
                window.BleControl.stopAnyStream();
            }

            // 【新增】离开采集页后隐藏质量颜色指示
            if (window.waveformController) {
                window.waveformController.refreshQualityVisibility();
            }

            // 【新增 Phase 1】检测断点状态
            this._checkBreakpointState();
        }

        /**
         * 显示采集页面
         */
        showCollection() {
            console.log('[PageSwitch] 切换到采集页面');

            const welcomeScreen = document.getElementById('welcomeScreen');
            const collectionScreen = document.getElementById('collectionScreen');
            const backendPage = document.getElementById('backend-page');

            if (welcomeScreen) welcomeScreen.classList.add('hidden');
            if (collectionScreen) collectionScreen.style.display = 'flex';
            if (backendPage) backendPage.classList.add('hidden');

            this.startWaveform();

            // 【新增】进入采集页后刷新质量颜色指示（如果设备已连接且未采集则显示）
            if (window.waveformController) {
                window.waveformController.refreshQualityVisibility();
            }

            // 【新增】如果设备已连接且未在 streaming，自动启动 preview stream
            if (window.BleControl && window.BleControl.isConnected) {
                // 延迟启动，给页面渲染和 WebSocket 一些时间
                setTimeout(() => {
                    console.log('[PageSwitch] 启动 preview stream（进入采集页）');
                    window.BleControl.startPreviewStream();
                }, 300);
            }

            // 通知采集控制器页面已显示
            if (window.collectionController) {
                window.collectionController.onPageShow();
            }
        }

        /**
         * 显示后台页面
         */
        showBackend() {
            console.log('[PageSwitch] 切换到后台页面');
            
            const welcomeScreen = document.getElementById('welcomeScreen');
            const collectionScreen = document.getElementById('collectionScreen');
            const backendPage = document.getElementById('backend-page');
            
            if (welcomeScreen) welcomeScreen.classList.add('hidden');
            if (collectionScreen) collectionScreen.style.display = 'none';
            if (backendPage) backendPage.classList.remove('hidden');
            
            // 【新增】离开采集页后隐藏质量颜色指示
            if (window.waveformController) {
                window.waveformController.refreshQualityVisibility();
            }

            // 通知后台管理器页面已显示（每次进入都刷新）
            if (window.backendManager) {
                if (!window.backendManager._bindingDone) {
                    window.backendManager.init();
                    window.backendManager._bindingDone = true;
                }
                window.backendManager.onPageShow();
            }
        }

        /**
         * 从后台返回欢迎页面
         */
        backToWelcomeFromBackend() {
            this.showWelcome();
        }

        /**
         * 显示用户信息模态框（旧模式）
         */
        showUserModal() {
            const modal = document.getElementById('userModal');
            if (modal) {
                modal.classList.add('visible');
            }
        }

        /**
         * 隐藏用户信息模态框
         */
        hideUserModal() {
            const modal = document.getElementById('userModal');
            if (modal) {
                modal.classList.remove('visible');
            }
        }

        /**
         * 返回欢迎页面（从采集页）
         */
        backToWelcome() {
            // 检查采集控制器是否正在运行
            if (window.collectionController && window.collectionController.isRunning()) {
                if (!confirm('采集任务正在进行中，确定要返回吗？')) {
                    return;
                }
                // 【修复 Issue 5】停止采集任务，但禁止自动切回 preview（我们要返回首页）
                window.collectionController.stopTask({ restartPreview: false });
            }

            // 停止所有活跃的 stream（preview 或 collection）
            // stopAnyStream 会发送 STOP 到 ESP32，不启动新 stream
            if (window.BleControl && window.BleControl.isConnected) {
                window.BleControl.stopAnyStream();
                console.log('[PageSwitch] 发送 stop_any_stream 命令');
            }
            
            this.showWelcome();
        }

        // ==================== 用户信息管理 ====================

        /**
         * 提交用户信息（旧模式）
         */
        submitUserInfo() {
            const user = {
                name: document.getElementById('userName').value.trim(),
                id: document.getElementById('userId').value.trim(),
                age: parseInt(document.getElementById('userAge').value),
                gender: document.getElementById('userGender').value,
                hand: document.getElementById('userHand').value,
                note: document.getElementById('userNote').value.trim(),
                timestamp: new Date().toISOString()
            };

            // 验证必填字段
            if (!user.name || !user.id || !user.age || !user.gender || !user.hand) {
                alert('请填写所有必填项！');
                return;
            }

            // 保存用户信息
            this.saveUser(user);
            this.currentUser = user;
            this.updateUserDisplay();
            this.hideUserModal();
            this.showToast('用户信息保存成功！');

            // 【修复】不再调用 startAll()，连接/录入后只进入采集页
            // preview stream 由 showCollection() 自动启动
            console.log('[PageSwitch] 用户信息已保存，进入采集页（不主动 start stream）');

            // 延迟切换到采集页面
            setTimeout(() => {
                this.showCollection();
            }, 500);
        }

        /**
         * 保存用户信息到本地存储
         */
        saveUser(user) {
            localStorage.setItem('emg_current_user', JSON.stringify(user));
            
            // 添加到用户历史
            let userHistory = JSON.parse(localStorage.getItem('emg_user_history') || '[]');
            userHistory.push(user);
            localStorage.setItem('emg_user_history', JSON.stringify(userHistory));
        }

        /**
         * 从本地存储加载用户信息
         */
        loadSavedUser() {
            const saved = localStorage.getItem('emg_current_user');
            if (saved) {
                this.currentUser = JSON.parse(saved);
            }
        }

        /**
         * 获取当前用户
         */
        getCurrentUser() {
            return this.currentUser;
        }

        /**
         * 更新顶部用户信息显示
         */
        updateUserDisplay() {
            if (this.currentUser) {
                const genderText = this.currentUser.gender === 'male' ? '男' : '女';
                const headerInfo = document.getElementById('headerUserInfo');
                if (headerInfo) {
                    headerInfo.textContent = `用户: ${this.currentUser.name} (${this.currentUser.id}) | ${genderText} | ${this.currentUser.age}岁`;
                }
            }
        }

        // ==================== 波形控制 ====================

        /**
         * 启动波形显示
         */
        startWaveform() {
            if (!this.waveformStarted && window.waveformController) {
                window.waveformController.startRealtime();
                this.waveformStarted = true;
                console.log('[PageSwitch] 启动实时波形显示');
            }
        }

        /**
         * 停止波形显示
         */
        stopWaveform() {
            if (this.waveformStarted && window.waveformController) {
                window.waveformController.stop();
                this.waveformStarted = false;
                console.log('[PageSwitch] 停止波形显示');
            }
        }

        // ==================== UI辅助 ====================

        /**
         * 显示Toast提示
         */
        showToast(message, type = 'success') {
            const toast = document.getElementById('toast');
            if (toast) {
                const iconMap = { success: 'check-circle', error: 'times-circle', warning: 'exclamation-triangle', info: 'info-circle' };
                const icon = iconMap[type] || 'check-circle';
                toast.className = `toast ${type}`;
                toast.innerHTML = `<i class="fas fa-${icon}"></i> ${message}`;
                toast.classList.add('visible');
                setTimeout(() => {
                    toast.classList.remove('visible');
                }, 3000);
            }
        }

        // ==================== 教程视频 ====================

        /**
         * 获取当前任务类型对应的视频文件名
         */
        getTutorialVideoFile() {
            // 从 collectionController 获取当前任务类型
            if (window.collectionController && window.collectionController.currentTaskId) {
                const taskId = window.collectionController.currentTaskId;
                // 任务ID到视频文件名的映射
                const videoMap = {
                    'discrete_gesture': 'discrete.mp4',
                    'continual_gesture_1': 'continual_1.mp4',
                    'continual_gesture_2': 'continual_2.mp4',
                    'continual_gesture_3': 'continual_3.mp4'
                };
                const videoFile = videoMap[taskId];
                if (videoFile) {
                    return `tutorial/video/${videoFile}`;
                }
            }
            return null;
        }

        /**
         * 获取当前任务类型的中文名称
         */
        getTutorialTitle() {
            if (window.collectionController && window.collectionController.currentTaskId) {
                const taskId = window.collectionController.currentTaskId;
                const titles = {
                    'discrete_gesture': '离散手势采集教程',
                    'continual_gesture_1': '连续手势1采集教程',
                    'continual_gesture_2': '连续手势2采集教程',
                    'continual_gesture_3': '连续手势3采集教程'
                };
                return titles[taskId] || '任务教程';
            }
            return '任务教程';
        }

        /**
         * 显示教程视频弹窗
         */
        showTutorialModal() {
            const modal = document.getElementById('tutorialModal');
            const video = document.getElementById('tutorialVideo');
            const source = document.getElementById('tutorialVideoSource');
            const title = document.getElementById('tutorialModalTitle');

            if (!modal || !video || !source) {
                console.warn('[PageSwitch] 教程弹窗元素未找到');
                return;
            }

            const videoFile = this.getTutorialVideoFile();
            if (!videoFile) {
                this.showToast('请先选择采集任务', 'warning');
                return;
            }

            // 设置标题
            if (title) {
                title.textContent = this.getTutorialTitle();
            }

            // 设置视频源
            source.src = videoFile;
            video.load();

            // 显示弹窗
            modal.classList.add('active');

            // 自动播放
            video.play().catch(err => {
                console.warn('[PageSwitch] 视频自动播放失败:', err);
            });

            console.log('[PageSwitch] 显示教程视频:', videoFile);
        }

        /**
         * 隐藏教程视频弹窗
         */
        hideTutorialModal() {
            const modal = document.getElementById('tutorialModal');
            const video = document.getElementById('tutorialVideo');

            if (modal) {
                modal.classList.remove('active');
            }

            if (video) {
                video.pause();
                video.currentTime = 0;
            }

            console.log('[PageSwitch] 关闭教程视频弹窗');
        }

        /**
         * 重播教程视频
         */
        replayTutorialVideo() {
            const video = document.getElementById('tutorialVideo');
            if (video) {
                video.currentTime = 0;
                video.play().catch(err => {
                    console.warn('[PageSwitch] 视频重播失败:', err);
                });
            }
        }

        /**
         * 【新增 Phase 1】检测断点状态
         * 如果存在 emg_breakpoint_exists，在控制台和 toast 提示
         * Phase 1 不实现续采按钮，只做检测提示
         */
        _checkBreakpointState() {
            const resumeBtn = document.getElementById('resumeBreakpointBtn');
            const breakpointExists = localStorage.getItem('emg_breakpoint_exists');

            // 只在 localStorage 有断点 AND 通过异常中断自动返回首页时显示按钮
            if (breakpointExists !== 'true' || !window.__showBreakpointResumeAfterAbort) {
                if (resumeBtn) resumeBtn.style.display = 'none';
                return;
            }

            // 尝试解析断点状态
            try {
                const breakpointState = JSON.parse(localStorage.getItem('emg_breakpoint_state') || '{}');

                // 校验必要字段
                if (!breakpointState.version || !breakpointState.collectionConfig || !breakpointState.currentTaskId) {
                    console.warn('[PageSwitch] 断点状态不完整，隐藏续采按钮');
                    if (resumeBtn) resumeBtn.style.display = 'none';
                    return;
                }

                console.log('[PageSwitch] ⚠️ 检测到异常中断断点状态');
                console.log('[PageSwitch] 断点信息:', {
                    interruptedAt: breakpointState.interruptedAt,
                    reason: breakpointState.interruptReason,
                    taskId: breakpointState.currentTaskId,
                    session: (breakpointState.currentSessionIndex || 0) + 1,
                    stage: breakpointState.currentStageIndex,
                    gesture: breakpointState.currentGestureIndex
                });

                // Phase 2: 显示"断点续采"按钮
                if (resumeBtn) {
                    resumeBtn.style.display = '';
                    console.log('[PageSwitch] "断点续采"按钮已显示');
                }

            } catch (e) {
                console.error('[PageSwitch] 断点状态解析失败:', e);
                if (resumeBtn) resumeBtn.style.display = 'none';
            }
        }

        /**
         * 【Phase 2】断点续采 — 确认弹窗 + 恢复流程
         */
        resumeBreakpoint() {
            let breakpointState;
            try {
                breakpointState = JSON.parse(localStorage.getItem('emg_breakpoint_state') || '{}');
            } catch (e) {
                this.showToast('断点数据损坏，无法续采', 'error');
                return;
            }

            if (!breakpointState.version || !breakpointState.collectionConfig) {
                this.showToast('断点数据不完整，无法续采', 'error');
                return;
            }

            // 任务名称映射
            const taskNames = {
                'discrete_gesture': '离散手势采集',
                'continual_gesture_1': '连续手势采集1',
                'continual_gesture_2': '连续手势采集2',
                'continual_gesture_3': '连续手势采集3'
            };

            const taskName = taskNames[breakpointState.currentTaskId] || breakpointState.currentTaskId;
            const sessionNum = (breakpointState.currentSessionIndex || 0) + 1;
            const stageName = breakpointState.stages?.[breakpointState.currentStageIndex]?.name || '未知';
            const gestureNum = (breakpointState.currentGestureIndex || 0) + 1;

            // ---- 确认弹窗 ----
            const confirmed = confirm(
                '════════ 断点续采确认 ════════\n\n' +
                `中断时间: ${new Date(breakpointState.interruptedAt).toLocaleString()}\n` +
                `中断原因: ${breakpointState.interruptReason || '未知'}\n` +
                `任务: ${taskName}\n` +
                `轮次: 第 ${sessionNum}/${breakpointState.sessionCount || '?'} 轮\n` +
                `Stage: ${stageName}\n` +
                `手势进度: 第 ${gestureNum} 个\n\n` +
                '请确认两只腕带已重新连接并正常。\n' +
                '确认后将进入采集界面继续采集中断的任务。\n\n' +
                '════════════════════════════'
            );

            if (!confirmed) {
                console.log('[PageSwitch] 用户取消断点续采');
                return;
            }

            console.log('[PageSwitch] ===== 开始断点续采恢复 =====');

            // 清除续采按钮一次性标记
            delete window.__showBreakpointResumeAfterAbort;

            // ---- 1. 恢复采集配置到全局和 localStorage ----
            window.currentCollectionConfig = breakpointState.collectionConfig;
            localStorage.setItem('emg_current_collection_config', JSON.stringify(breakpointState.collectionConfig));

            // ---- 2. 恢复用户信息 ----
            if (breakpointState.collectionConfig.subject) {
                this.currentUser = breakpointState.collectionConfig.subject;
                this.updateUserDisplay();
                localStorage.setItem('emg_current_user', JSON.stringify(breakpointState.collectionConfig.subject));
            }

            // ---- 3. 切换到采集页面 ----
            console.log('[PageSwitch] 切换到采集页面...');

            // 启动 preview stream（仅腕带 streaming，用于波形预览）
            // H5 记录在用户点击"开始续采"后由 startTask() 触发（内部走 switch_preview_to_collection）
            if (window.BleControl && window.BleControl.isConnected) {
                window.BleControl.startPreviewStream();
                console.log('[PageSwitch] preview stream 已启动（仅 preview，未开始 H5 记录）');
            }

            setTimeout(() => {
                this.showCollection();

                // ---- 4. 恢复 collectionController 状态 ----
                if (window.collectionController) {
                    window.collectionController.selectTask(
                        breakpointState.currentTaskId === 'discrete_gesture' ? 'discrete' :
                        breakpointState.currentTaskId === 'continual_gesture_1' ? 'continuous1' :
                        breakpointState.currentTaskId === 'continual_gesture_2' ? 'continuous2' :
                        breakpointState.currentTaskId === 'continual_gesture_3' ? 'continuous3' :
                        'discrete'
                    );
                    window.collectionController.loadBreakpointState(breakpointState);
                } else {
                    console.error('[PageSwitch] collectionController 未找到');
                    this.showToast('采集控制器未加载，恢复失败', 'error');
                }
            }, 500);

            console.log('[PageSwitch] 断点续采恢复完成');
        }

        /**
         * 【Phase 6】从 .breakpoint.json 文件导入断点
         */
        _handleImportBreakpoint(event) {
            const file = event.target.files?.[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = (e) => {
                try {
                    const bp = JSON.parse(e.target.result);

                    // 校验
                    if (!bp.version || bp.version !== 1) {
                        this.showToast('断点文件版本不支持', 'error');
                        return;
                    }
                    if (bp.status !== 'abnormal_interrupted') {
                        this.showToast('该文件不是异常中断断点', 'error');
                        return;
                    }
                    if (!bp.collectionConfig || !bp.currentTaskId) {
                        this.showToast('断点数据不完整，缺少 collectionConfig', 'error');
                        return;
                    }

                    // 写入 localStorage
                    localStorage.setItem('emg_breakpoint_state', JSON.stringify(bp));
                    localStorage.setItem('emg_breakpoint_exists', 'true');
                    window.__showBreakpointResumeAfterAbort = true;

                    console.log('[PageSwitch] 断点已从文件导入:', bp.interruptedAt);
                    console.log('[PageSwitch] 来源:', bp.source_h5_path || file.name);

                    // 刷新首页按钮
                    this.showWelcome();
                    this.showToast('断点已导入，可点击"断点续采"继续', 'success');

                } catch (err) {
                    console.error('[PageSwitch] 导入断点失败:', err);
                    this.showToast('断点文件解析失败', 'error');
                }
            };
            reader.readAsText(file);

            // reset input so same file can be imported again
            event.target.value = '';
        }
    }

    // ==================== 初始化 ====================
    let pageSwitchController = null;

    document.addEventListener('DOMContentLoaded', () => {
        console.log('[PageSwitch] DOM加载完成，开始初始化...');
        pageSwitchController = new PageSwitchController();
        window.pageSwitchController = pageSwitchController;
        console.log('[PageSwitch] 控制器已挂载到 window.pageSwitchController');
    });

    console.log('[PageSwitch] 脚本加载完成');

})();
