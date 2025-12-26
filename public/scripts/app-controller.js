/**
 * app-controller.js - 应用控制器
 * 
 * 管理页面跳转、用户信息和任务引导系统
 */

(function() {
    'use strict';

    // ==================== 任务配置 ====================
    const TASK_CONFIG = {
        discrete: {
            name: '离散手势',
            gestures: [
                { name: '握拳', icon: 'fa-fist-raised', duration: 3000 },
                { name: '张手', icon: 'fa-hand-paper', duration: 3000 },
                { name: '竖大拇指', icon: 'fa-thumbs-up', duration: 3000 },
                { name: '竖食指', icon: 'fa-hand-point-up', duration: 3000 },
                { name: '竖中指', icon: 'fa-hand-middle-finger', duration: 3000 },
                { name: '竖小指', icon: 'fa-hand-point-right', duration: 3000 },
                { name: 'OK手势', icon: 'fa-hand-peace', duration: 3000 },
                { name: '枪手势', icon: 'fa-hand-pointer', duration: 3000 },
                { name: '捏', icon: 'fa-hand-lizard', duration: 3000 },
                { name: '握笔', icon: 'fa-pen', duration: 3000 },
                { name: '抓握', icon: 'fa-hand-rock', duration: 3000 },
                { name: '放松', icon: 'fa-hand-sparkles', duration: 3000 }
            ],
            restDuration: 2000,
            repetitions: 5
        },
        continuous1: {
            name: '连续手势1',
            gestures: [
                { name: '手指张合', icon: 'fa-hands', duration: 5000, instruction: '反复张开和握紧手指' },
                { name: '手指交替点击', icon: 'fa-hand-point-down', duration: 5000, instruction: '依次点击大拇指与其他手指' },
                { name: '手指伸展', icon: 'fa-hand-scissors', duration: 5000, instruction: '手指依次伸展开' },
                { name: '手指弯曲', icon: 'fa-hand-rock', duration: 5000, instruction: '手指依次弯曲' }
            ],
            restDuration: 3000,
            repetitions: 3
        },
        continuous2: {
            name: '连续手势2',
            gestures: [
                { name: '手腕旋转', icon: 'fa-sync-alt', duration: 5000, instruction: '顺时针和逆时针旋转手腕' },
                { name: '手腕上下', icon: 'fa-arrows-alt-v', duration: 5000, instruction: '手腕上下摆动' },
                { name: '手腕左右', icon: 'fa-arrows-alt-h', duration: 5000, instruction: '手腕左右摆动' },
                { name: '握拳旋转', icon: 'fa-fist-raised', duration: 5000, instruction: '握拳状态下旋转前臂' }
            ],
            restDuration: 3000,
            repetitions: 3
        }
    };

    // ==================== 应用控制器 ====================
    class AppController {
        constructor() {
            this.currentUser = null;
            this.currentTask = 'discrete';
            this.taskRunner = null;
            this.waveformStarted = false;
            
            this.init();
        }

        init() {
            this.bindEvents();
            this.loadSavedUser();
        }

        bindEvents() {
            // 开始采集按钮
            document.getElementById('startCollectionBtn').addEventListener('click', () => {
                this.showUserModal();
            });

            // 用户表单提交
            document.getElementById('userForm').addEventListener('submit', (e) => {
                e.preventDefault();
                this.submitUserInfo();
            });

            // 返回按钮
            document.getElementById('backBtn').addEventListener('click', () => {
                this.backToWelcome();
            });

            // 任务按钮
            document.querySelectorAll('.task-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    this.selectTask(btn.dataset.task);
                });
            });

            // 任务控制按钮
            document.getElementById('startTaskBtn').addEventListener('click', () => {
                this.startTask();
            });

            document.getElementById('pauseTaskBtn').addEventListener('click', () => {
                this.pauseTask();
            });

            document.getElementById('stopTaskBtn').addEventListener('click', () => {
                this.stopTask();
            });
        }

        // ==================== 页面切换 ====================
        showWelcome() {
            document.getElementById('welcomeScreen').classList.remove('hidden');
            document.getElementById('collectionScreen').style.display = 'none';
            this.stopWaveform();
        }

        showCollection() {
            document.getElementById('welcomeScreen').classList.add('hidden');
            document.getElementById('collectionScreen').style.display = 'flex';
            this.startWaveform();
            this.updateGestureList();
        }

        showUserModal() {
            document.getElementById('userModal').classList.add('visible');
        }

        hideUserModal() {
            document.getElementById('userModal').classList.remove('visible');
        }

        showToast(message) {
            const toast = document.getElementById('toast');
            toast.innerHTML = `<i class="fas fa-check-circle"></i> ${message}`;
            toast.classList.add('visible');
            setTimeout(() => {
                toast.classList.remove('visible');
            }, 3000);
        }

        // ==================== 用户信息管理 ====================
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

            // 保存到本地存储
            this.saveUser(user);
            this.currentUser = user;

            // 更新界面
            this.updateUserDisplay();
            
            // 隐藏弹窗并显示采集界面
            this.hideUserModal();
            this.showToast('用户信息保存成功！');
            
            // 发送 start_all 命令开始采集
            if (window.BleControl && window.BleControl.isConnected) {
                window.BleControl.startAll();
                console.log('[App] 发送 start_all 命令');
            } else {
                console.warn('[App] BLE 未连接，无法发送 start_all');
            }
            
            setTimeout(() => {
                this.showCollection();
            }, 500);
        }

        saveUser(user) {
            // 保存当前用户
            localStorage.setItem('emg_current_user', JSON.stringify(user));
            
            // 添加到用户历史
            let userHistory = JSON.parse(localStorage.getItem('emg_user_history') || '[]');
            userHistory.push(user);
            localStorage.setItem('emg_user_history', JSON.stringify(userHistory));
        }

        loadSavedUser() {
            const saved = localStorage.getItem('emg_current_user');
            if (saved) {
                this.currentUser = JSON.parse(saved);
            }
        }

        updateUserDisplay() {
            if (this.currentUser) {
                const genderText = this.currentUser.gender === 'male' ? '男' : '女';
                document.getElementById('headerUserInfo').textContent = 
                    `用户: ${this.currentUser.name} (${this.currentUser.id}) | ${genderText} | ${this.currentUser.age}岁`;
            }
        }

        backToWelcome() {
            if (this.taskRunner && this.taskRunner.isRunning) {
                if (!confirm('采集任务正在进行中，确定要返回吗？')) {
                    return;
                }
                this.stopTask();
            }
            
            // 发送 stop_all 命令停止采集
            if (window.BleControl && window.BleControl.isConnected) {
                window.BleControl.stopAll();
                console.log('[App] 发送 stop_all 命令');
            } else {
                console.warn('[App] BLE 未连接，无法发送 stop_all');
            }
            
            this.showWelcome();
        }

        // ==================== 波形控制 ====================
        startWaveform() {
            if (!this.waveformStarted && window.waveformController) {
                // 使用实时数据模式（连接realtimeEngine.js的WebSocket）
                window.waveformController.startRealtime();
                this.waveformStarted = true;
                console.log('[App] 启动实时波形显示');
            }
        }

        stopWaveform() {
            if (this.waveformStarted && window.waveformController) {
                window.waveformController.stop();
                this.waveformStarted = false;
            }
        }

        // ==================== 任务管理 ====================
        selectTask(taskType) {
            this.currentTask = taskType;
            
            // 更新按钮样式
            document.querySelectorAll('.task-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.task === taskType);
            });

            // 更新手势列表
            this.updateGestureList();

            // 重置显示
            this.resetGestureDisplay();
        }

        updateGestureList() {
            const config = TASK_CONFIG[this.currentTask];
            const listContainer = document.getElementById('gestureList');
            
            let html = `<div class="gesture-list-title">${config.name} (${config.repetitions}次重复)</div>`;
            
            config.gestures.forEach((gesture, index) => {
                html += `
                    <div class="gesture-item pending" data-index="${index}">
                        <i class="fas ${gesture.icon}"></i>
                        <span>${gesture.name}</span>
                    </div>
                `;
            });
            
            listContainer.innerHTML = html;
        }

        resetGestureDisplay() {
            document.getElementById('gestureIcon').className = 'fas fa-hand-paper';
            document.getElementById('gestureName').textContent = '点击开始';
            document.getElementById('gestureInstruction').textContent = '选择任务类型并点击开始按钮';
            document.getElementById('countdown').classList.remove('visible');
            document.getElementById('progressFill').style.width = '0%';
            document.getElementById('progressText').textContent = '0 / 0 完成';
            document.getElementById('statusDot').className = 'status-dot idle';
            document.getElementById('statusText').textContent = '准备就绪';
        }

        startTask() {
            const config = TASK_CONFIG[this.currentTask];
            
            this.taskRunner = new TaskRunner(config, {
                onGestureStart: (gesture, index, rep) => this.onGestureStart(gesture, index, rep),
                onGestureEnd: (gesture, index, rep) => this.onGestureEnd(gesture, index, rep),
                onRest: (duration) => this.onRest(duration),
                onCountdown: (count) => this.onCountdown(count),
                onProgress: (current, total) => this.onProgress(current, total),
                onComplete: () => this.onTaskComplete()
            });

            this.taskRunner.start();

            // 更新按钮状态
            document.getElementById('startTaskBtn').disabled = true;
            document.getElementById('pauseTaskBtn').disabled = false;
            document.getElementById('stopTaskBtn').disabled = false;
        }

        pauseTask() {
            if (this.taskRunner) {
                if (this.taskRunner.isPaused) {
                    this.taskRunner.resume();
                    document.getElementById('pauseTaskBtn').innerHTML = '<i class="fas fa-pause"></i> 暂停';
                } else {
                    this.taskRunner.pause();
                    document.getElementById('pauseTaskBtn').innerHTML = '<i class="fas fa-play"></i> 继续';
                }
            }
        }

        stopTask() {
            if (this.taskRunner) {
                this.taskRunner.stop();
                this.taskRunner = null;
            }

            document.getElementById('startTaskBtn').disabled = false;
            document.getElementById('pauseTaskBtn').disabled = true;
            document.getElementById('stopTaskBtn').disabled = true;
            document.getElementById('pauseTaskBtn').innerHTML = '<i class="fas fa-pause"></i> 暂停';

            this.resetGestureDisplay();
            this.updateGestureList();
        }

        // ==================== 任务回调 ====================
        onGestureStart(gesture, index, rep) {
            const config = TASK_CONFIG[this.currentTask];
            
            document.getElementById('gestureIcon').className = `fas ${gesture.icon}`;
            document.getElementById('gestureName').textContent = gesture.name;
            document.getElementById('gestureInstruction').textContent = 
                gesture.instruction || `请做出 ${gesture.name} 动作`;
            document.getElementById('countdown').classList.remove('visible');
            
            document.getElementById('statusDot').className = 'status-dot recording';
            document.getElementById('statusText').textContent = `采集中 (第${rep + 1}/${config.repetitions}次)`;

            // 更新列表
            document.querySelectorAll('.gesture-item').forEach((item, i) => {
                if (i < index) {
                    item.className = 'gesture-item completed';
                } else if (i === index) {
                    item.className = 'gesture-item current';
                } else {
                    item.className = 'gesture-item pending';
                }
            });
        }

        onGestureEnd(gesture, index, rep) {
            // 手势结束处理
        }

        onRest(duration) {
            document.getElementById('gestureIcon').className = 'fas fa-coffee';
            document.getElementById('gestureName').textContent = '休息';
            document.getElementById('gestureInstruction').textContent = '请放松手部，准备下一个动作';
            document.getElementById('statusDot').className = 'status-dot idle';
            document.getElementById('statusText').textContent = '休息中';
        }

        onCountdown(count) {
            const countdownEl = document.getElementById('countdown');
            countdownEl.textContent = count;
            countdownEl.classList.add('visible');
            
            if (count <= 0) {
                countdownEl.classList.remove('visible');
            }
        }

        onProgress(current, total) {
            const percent = (current / total) * 100;
            document.getElementById('progressFill').style.width = `${percent}%`;
            document.getElementById('progressText').textContent = `${current} / ${total} 完成`;
        }

        onTaskComplete() {
            document.getElementById('gestureIcon').className = 'fas fa-check-circle';
            document.getElementById('gestureName').textContent = '采集完成！';
            document.getElementById('gestureInstruction').textContent = '所有手势采集已完成';
            document.getElementById('statusDot').className = 'status-dot idle';
            document.getElementById('statusText').textContent = '已完成';

            document.getElementById('startTaskBtn').disabled = false;
            document.getElementById('pauseTaskBtn').disabled = true;
            document.getElementById('stopTaskBtn').disabled = true;

            // 标记所有手势为完成
            document.querySelectorAll('.gesture-item').forEach(item => {
                item.className = 'gesture-item completed';
            });

            this.showToast('采集任务完成！');
        }
    }

    // ==================== 任务执行器 ====================
    class TaskRunner {
        constructor(config, callbacks) {
            this.config = config;
            this.callbacks = callbacks;
            this.currentGestureIndex = 0;
            this.currentRepetition = 0;
            this.isRunning = false;
            this.isPaused = false;
            this.timeoutId = null;
            this.totalActions = config.gestures.length * config.repetitions;
            this.completedActions = 0;
        }

        start() {
            this.isRunning = true;
            this.isPaused = false;
            this.currentGestureIndex = 0;
            this.currentRepetition = 0;
            this.completedActions = 0;
            
            this.runCountdown(3);
        }

        runCountdown(count) {
            if (!this.isRunning || this.isPaused) return;

            this.callbacks.onCountdown(count);

            if (count > 0) {
                this.timeoutId = setTimeout(() => {
                    this.runCountdown(count - 1);
                }, 1000);
            } else {
                this.runNextGesture();
            }
        }

        runNextGesture() {
            if (!this.isRunning) return;

            const gesture = this.config.gestures[this.currentGestureIndex];
            
            this.callbacks.onGestureStart(gesture, this.currentGestureIndex, this.currentRepetition);

            this.timeoutId = setTimeout(() => {
                this.onGestureComplete();
            }, gesture.duration);
        }

        onGestureComplete() {
            if (!this.isRunning) return;

            const gesture = this.config.gestures[this.currentGestureIndex];
            this.callbacks.onGestureEnd(gesture, this.currentGestureIndex, this.currentRepetition);
            
            this.completedActions++;
            this.callbacks.onProgress(this.completedActions, this.totalActions);

            // 移动到下一个
            this.currentGestureIndex++;
            
            if (this.currentGestureIndex >= this.config.gestures.length) {
                this.currentGestureIndex = 0;
                this.currentRepetition++;
                
                if (this.currentRepetition >= this.config.repetitions) {
                    this.complete();
                    return;
                }
            }

            // 休息
            this.callbacks.onRest(this.config.restDuration);
            
            this.timeoutId = setTimeout(() => {
                if (this.isRunning && !this.isPaused) {
                    this.runCountdown(3);
                }
            }, this.config.restDuration);
        }

        pause() {
            this.isPaused = true;
            if (this.timeoutId) {
                clearTimeout(this.timeoutId);
            }
        }

        resume() {
            this.isPaused = false;
            this.runNextGesture();
        }

        stop() {
            this.isRunning = false;
            this.isPaused = false;
            if (this.timeoutId) {
                clearTimeout(this.timeoutId);
            }
        }

        complete() {
            this.isRunning = false;
            this.callbacks.onComplete();
        }
    }

    // ==================== 初始化 ====================
    let appController = null;

    document.addEventListener('DOMContentLoaded', () => {
        appController = new AppController();
        window.appController = appController;
        
        console.log('App Controller initialized.');
    });

})();
