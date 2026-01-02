/**
 * page-switch.js - 页面切换控制器
 * 
 * 职责：
 * 1. 页面切换（欢迎页 ↔ 采集页 ↔ 后台页）
 * 2. 用户信息管理（表单、保存、显示）
 * 3. 波形显示控制
 * 4. Toast提示
 * 
 * 不包含：采集任务的具体控制逻辑（由collection-controller.js负责）
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
            // 开始采集按钮（首页）
            const startCollectionBtn = document.getElementById('startCollectionBtn');
            if (startCollectionBtn) {
                startCollectionBtn.addEventListener('click', () => {
                    console.log('[PageSwitch] 点击开始采集');
                    this.showUserModal();
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

            // 用户表单提交
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
            
            this.stopWaveform();
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
         * 显示用户信息模态框
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
                // 停止采集任务
                window.collectionController.stopTask();
            }
            
            // 停止BLE数据流
            if (window.BleControl && window.BleControl.isConnected) {
                window.BleControl.stopAll();
                console.log('[PageSwitch] 发送 stop_all 命令');
            }
            
            this.showWelcome();
        }

        // ==================== 用户信息管理 ====================

        /**
         * 提交用户信息
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

            // 启动BLE数据流
            if (window.BleControl && window.BleControl.isConnected) {
                window.BleControl.startAll();
                console.log('[PageSwitch] 发送 start_all 命令');
            }

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
        showToast(message) {
            const toast = document.getElementById('toast');
            if (toast) {
                toast.innerHTML = `<i class="fas fa-check-circle"></i> ${message}`;
                toast.classList.add('visible');
                setTimeout(() => {
                    toast.classList.remove('visible');
                }, 3000);
            }
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
