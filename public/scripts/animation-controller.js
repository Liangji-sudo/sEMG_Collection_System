/**
 * animation-controller.js - 动画控制器（重构版）
 * 
 * 这个文件负责管理采集过程中的所有动画：
 * 1. 开场动画（Intro Animation）- 采集开始前播放
 * 2. Stage内容动画 - 根据任务类型选择对应的动画模块
 * 3. 倒计时动画（Countdown Animation）- stage切换时的准备倒计时
 * 
 * 重构说明：
 * - 不再有统一的stage-animation.js
 * - 每种任务有独立的动画模块
 * - 通过prompt数量控制stage时长，不再使用秒数倒计时
 */

(function() {
    'use strict';

    console.log('[Animation] 动画控制器开始加载...');

    class AnimationController {
        constructor() {
            this.currentAnimation = null;
            this.animationTimer = null;
            this.countdownTimer = null;
            this.isPlaying = false;
            
            // 当前任务类型
            this.currentTaskId = 'discrete_gesture';
            
            // DOM元素引用
            this.gestureDisplay = null;
            this.gestureName = null;
            this.gestureInstruction = null;
            this.gestureIcon = null;
            this.countdown = null;
            
            // 动画模块映射
            this.animationModules = {
                'discrete_gesture': () => window.discreteGestureAnimation,
                'continual_gesture_1': () => window.continualGesture1Animation,
                'continual_gesture_2': () => window.continualGesture2Animation
            };
        }

        /**
         * 初始化，绑定DOM元素
         */
        init() {
            this.gestureDisplay = document.getElementById('gestureDisplay');
            this.gestureName = document.getElementById('gestureName');
            this.gestureInstruction = document.getElementById('gestureInstruction');
            this.gestureIcon = document.getElementById('gestureIcon');
            this.countdown = document.getElementById('countdown');
            
            console.log('[Animation] 初始化完成');
        }

        /**
         * 设置当前任务类型
         */
        setCurrentTask(taskId) {
            this.currentTaskId = taskId;
            console.log('[Animation] 当前任务类型:', taskId);
        }

        /**
         * 获取当前任务对应的动画模块
         */
        getCurrentAnimationModule() {
            const getModule = this.animationModules[this.currentTaskId];
            if (getModule) {
                const module = getModule();
                if (module) {
                    return module;
                }
            }
            
            // 后备：使用离散手势动画
            console.warn('[Animation] 未找到任务动画模块，使用默认');
            return window.discreteGestureAnimation || null;
        }

        // ==================== WebSocket通信 ====================

        getWebSocket() {
            if (window.waveformController && 
                window.waveformController.dataReceiver && 
                window.waveformController.dataReceiver.ws) {
                return window.waveformController.dataReceiver.ws;
            }
            return null;
        }

        sendToRealtimeEngine(action, data) {
            console.log(`[Animation] >>> realtimeEngine: ${action}`, data);
            
            const ws = this.getWebSocket();
            
            if (ws && ws.readyState === WebSocket.OPEN) {
                const message = JSON.stringify({
                    type: 'control_command',
                    action: action,
                    data: data,
                    timestamp: Date.now() / 1000  // 【修改】转换为秒，与ble_server时间戳一致
                });
                ws.send(message);
                console.log(`[Animation] 消息已发送: ${action}`);
            } else {
                console.log(`[Animation] WebSocket未连接，消息未发送: ${action}`);
            }
        }

        /**
         * 发送Prompt信号到realtimeEngine
         */
        sendPrompt(promptName, stageName) {
            // 【修改】使用秒时间戳，与ble_server保持一致
            const timestamp = Date.now() / 1000;

            this.sendToRealtimeEngine('prompt', {
                name: promptName,
                stageName: stageName,
                timestamp: timestamp
            });
        }

        // ==================== 开场动画 ====================

        playIntroAnimation(onComplete) {
            console.log('[Animation] 播放开场动画');
            this.isPlaying = true;
            
            const duration = window.CollectionTiming ? 
                window.CollectionTiming.getIntroDuration() : 10000;
            
            const type = window.COLLECTION_CONSTANTS ? 
                window.COLLECTION_CONSTANTS.INTRO.TYPE : 'countdown';
            
            switch (type) {
                case 'video':
                    this.playIntroVideo(duration, onComplete);
                    break;
                case 'countdown':
                default:
                    this.playIntroCountdown(duration, onComplete);
                    break;
            }
        }

        playIntroCountdown(duration, onComplete) {
            const totalSeconds = Math.ceil(duration / 1000);
            let remaining = totalSeconds;
            
            // 显示stage信息框（不隐藏）
            if (this.gestureName) {
                this.gestureName.style.display = '';
                this.gestureName.textContent = '准备开始';
            }
            if (this.gestureInstruction) {
                this.gestureInstruction.style.display = '';
                this.gestureInstruction.textContent = '请做好准备...';
            }
            
            // 隐藏手势图标，显示倒计时
            if (this.gestureIcon && this.gestureIcon.parentElement) {
                this.gestureIcon.parentElement.style.display = 'none';
            }
            if (this.countdown) {
                this.countdown.classList.add('visible');
                this.countdown.style.fontSize = '120px';
                this.countdown.style.color = '#3b82f6';
            }
            
            this.showProgressRing(totalSeconds);
            
            const tick = () => {
                if (remaining <= 0) {
                    this.hideProgressRing();
                    if (this.countdown) {
                        this.countdown.classList.remove('visible');
                        this.countdown.style.fontSize = '';
                        this.countdown.style.color = '';
                    }
                    this.isPlaying = false;
                    if (onComplete) onComplete();
                    return;
                }
                
                if (this.countdown) {
                    this.countdown.textContent = remaining;
                }
                this.updateProgressRing(remaining, totalSeconds);
                
                remaining--;
                this.animationTimer = setTimeout(tick, 1000);
            };
            
            tick();
        }

        playIntroVideo(duration, onComplete) {
            console.log('[Animation] 播放开场视频（待实现）');
            this.playIntroCountdown(duration, onComplete);
        }

        // ==================== Stage内容动画 ====================

        /**
         * 播放Stage动画
         * 根据当前任务类型选择对应的动画模块
         */
        playStageAnimation(stage, onComplete) {
            console.log('[Animation] 播放Stage动画:', stage.name, '任务类型:', this.currentTaskId);
            this.isPlaying = true;
            
            // 更新显示内容（保持stage信息框可见）
            this.updateStageDisplay(stage);
            
            // 获取对应的动画模块
            const animModule = this.getCurrentAnimationModule();
            
            if (animModule) {
                console.log('[Animation] 使用动画模块:', this.currentTaskId);
                
                // 动画模块使用prompt计数，不再使用duration
                animModule.start(
                    stage,
                    () => {
                        this.isPlaying = false;
                        if (onComplete) onComplete();
                    },
                    (promptName, promptIndex, stageName) => {
                        // prompt触发回调
                        console.log(`[Animation] Prompt触发: ${promptName} #${promptIndex + 1}`);
                    }
                );
            } else {
                // 后备方案：简单的倒计时
                console.log('[Animation] 无动画模块，使用简单倒计时');
                this.playFallbackAnimation(stage, onComplete);
            }
        }

        /**
         * 更新Stage显示内容
         * 保持stage信息框可见
         */
        updateStageDisplay(stage) {
            // 显示手势名称
            if (this.gestureName) {
                this.gestureName.style.display = '';
                this.gestureName.textContent = stage.label || stage.name;
            }
            
            // 显示指导说明
            if (this.gestureInstruction) {
                this.gestureInstruction.style.display = '';
                this.gestureInstruction.textContent = stage.instruction || '请按照提示进行手势动作';
            }
            
            // 隐藏手势图标（让Canvas动画显示）
            if (this.gestureIcon && this.gestureIcon.parentElement) {
                this.gestureIcon.parentElement.style.display = 'none';
            }
            
            // 隐藏大倒计时
            if (this.countdown) {
                this.countdown.classList.remove('visible');
            }
        }

        /**
         * 后备动画（当没有动画模块时使用）
         */
        playFallbackAnimation(stage, onComplete) {
            // 估算时长：基于prompt数量
            let duration = 10000; // 默认10秒
            
            if (window.CollectionTiming) {
                duration = window.CollectionTiming.estimateStageDuration(
                    this.currentTaskId, 
                    stage.name
                );
            }
            
            const startTime = Date.now();
            this.showStageTimer(duration);
            
            const updateProgress = () => {
                const elapsed = Date.now() - startTime;
                const remaining = Math.max(0, duration - elapsed);
                const remainingSeconds = Math.ceil(remaining / 1000);
                
                this.updateStageTimer(remainingSeconds);
                
                if (elapsed >= duration) {
                    this.hideStageTimer();
                    this.isPlaying = false;
                    if (onComplete) onComplete();
                    return;
                }
                
                this.animationTimer = setTimeout(updateProgress, 100);
            };
            
            updateProgress();
        }

        // ==================== 倒计时动画 ====================

        /**
         * 播放准备倒计时
         */
        playCountdown(seconds, onComplete) {
            console.log('[Animation] 播放准备倒计时:', seconds, '秒');
            
            let remaining = seconds;
            
            // 保持stage信息框可见，只更新内容
            if (this.gestureName) {
                this.gestureName.style.display = '';
                this.gestureName.textContent = '准备';
            }
            if (this.gestureInstruction) {
                this.gestureInstruction.style.display = '';
                this.gestureInstruction.textContent = '下一个动作即将开始...';
            }
            
            // 显示倒计时数字
            if (this.countdown) {
                this.countdown.classList.add('visible');
                this.countdown.style.fontSize = '72px';
                this.countdown.style.color = '#ef4444';
            }
            
            // 隐藏手势图标
            if (this.gestureIcon && this.gestureIcon.parentElement) {
                this.gestureIcon.parentElement.style.display = 'none';
            }
            
            const tick = () => {
                if (remaining <= 0) {
                    if (this.countdown) {
                        this.countdown.classList.remove('visible');
                    }
                    if (onComplete) onComplete();
                    return;
                }
                
                if (this.countdown) {
                    this.countdown.textContent = remaining;
                    // 添加缩放动画效果
                    this.countdown.style.transform = 'scale(1.2)';
                    setTimeout(() => {
                        if (this.countdown) {
                            this.countdown.style.transform = 'scale(1)';
                        }
                    }, 200);
                }
                
                remaining--;
                this.countdownTimer = setTimeout(tick, 1000);
            };
            
            tick();
        }

        // ==================== 辅助UI元素 ====================

        showProgressRing(totalSeconds) {
            let ring = document.getElementById('introProgressRing');
            if (!ring) {
                ring = document.createElement('div');
                ring.id = 'introProgressRing';
                ring.style.cssText = `
                    position: absolute;
                    width: 200px;
                    height: 200px;
                    border-radius: 50%;
                    border: 8px solid #e5e7eb;
                    border-top-color: #3b82f6;
                    animation: spin 1s linear infinite;
                `;
                
                if (!document.getElementById('spinAnimation')) {
                    const style = document.createElement('style');
                    style.id = 'spinAnimation';
                    style.textContent = `
                        @keyframes spin {
                            0% { transform: rotate(0deg); }
                            100% { transform: rotate(360deg); }
                        }
                    `;
                    document.head.appendChild(style);
                }
                
                if (this.gestureDisplay) {
                    this.gestureDisplay.appendChild(ring);
                }
            }
            ring.style.display = 'block';
        }

        updateProgressRing(remaining, total) {
            // 可以在这里更新进度环的样式
        }

        hideProgressRing() {
            const ring = document.getElementById('introProgressRing');
            if (ring) {
                ring.style.display = 'none';
            }
        }

        showStageTimer(duration) {
            let timer = document.getElementById('stageTimer');
            if (!timer) {
                timer = document.createElement('div');
                timer.id = 'stageTimer';
                timer.style.cssText = `
                    position: absolute;
                    bottom: 100px;
                    left: 50%;
                    transform: translateX(-50%);
                    font-size: 24px;
                    font-weight: bold;
                    color: #6b7280;
                    background: white;
                    padding: 8px 20px;
                    border-radius: 20px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                `;
                
                if (this.gestureDisplay) {
                    this.gestureDisplay.appendChild(timer);
                }
            }
            timer.style.display = 'block';
            timer.textContent = Math.ceil(duration / 1000) + 's';
        }

        updateStageTimer(seconds) {
            const timer = document.getElementById('stageTimer');
            if (timer) {
                timer.textContent = seconds + 's';
                
                if (seconds <= 3) {
                    timer.style.color = '#ef4444';
                } else {
                    timer.style.color = '#6b7280';
                }
            }
        }

        hideStageTimer() {
            const timer = document.getElementById('stageTimer');
            if (timer) {
                timer.style.display = 'none';
            }
        }

        // ==================== 控制方法 ====================

        stop() {
            console.log('[Animation] 停止所有动画');
            
            if (this.animationTimer) {
                clearTimeout(this.animationTimer);
                this.animationTimer = null;
            }
            
            if (this.countdownTimer) {
                clearTimeout(this.countdownTimer);
                this.countdownTimer = null;
            }
            
            // 停止所有动画模块
            Object.keys(this.animationModules).forEach(taskId => {
                const getModule = this.animationModules[taskId];
                const module = getModule();
                if (module && module.stop) {
                    module.stop();
                }
            });
            
            this.isPlaying = false;
            this.hideProgressRing();
            this.hideStageTimer();
            
            if (this.countdown) {
                this.countdown.classList.remove('visible');
            }
        }

        isAnimationPlaying() {
            return this.isPlaying;
        }

        reset() {
            this.stop();
            
            // 恢复所有UI元素的显示
            if (this.gestureName) {
                this.gestureName.style.display = '';
                this.gestureName.textContent = '点击开始';
            }
            if (this.gestureInstruction) {
                this.gestureInstruction.style.display = '';
                this.gestureInstruction.textContent = '选择任务类型并点击开始按钮';
            }
            if (this.gestureIcon && this.gestureIcon.parentElement) {
                this.gestureIcon.parentElement.style.display = '';
            }
        }
    }

    // ==================== 初始化 ====================
    
    const animationController = new AnimationController();
    window.animationController = animationController;
    
    // DOM加载完成后初始化
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            animationController.init();
        });
    } else {
        animationController.init();
    }
    
    console.log('[Animation] 动画控制器加载完成');

})();
