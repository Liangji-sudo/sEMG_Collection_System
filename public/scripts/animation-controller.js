/**
 * animation-controller.js - 动画控制器
 * 
 * 这个文件负责管理采集过程中的所有动画：
 * 1. 开场动画（Intro Animation）- 采集开始前播放
 * 2. Stage内容动画（Stage Content Animation）- 每个stage期间播放
 * 3. 倒计时动画（Countdown Animation）- stage切换时的准备倒计时
 * 
 * 后续可以在这里替换为自定义动画或视频
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
            
            // 获取显示区域元素
            this.gestureDisplay = null;
            this.gestureName = null;
            this.gestureInstruction = null;
            this.gestureIcon = null;
            this.countdown = null;
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

        // ==================== WebSocket通信 ====================

        /**
         * 获取WebSocket连接
         */
        getWebSocket() {
            if (window.waveformController && 
                window.waveformController.dataReceiver && 
                window.waveformController.dataReceiver.ws) {
                return window.waveformController.dataReceiver.ws;
            }
            return null;
        }

        /**
         * 发送消息到realtimeEngine
         */
        sendToRealtimeEngine(action, data) {
            console.log(`[Animation] >>> realtimeEngine: ${action}`, data);
            
            const ws = this.getWebSocket();
            
            if (ws && ws.readyState === WebSocket.OPEN) {
                const message = JSON.stringify({
                    type: 'control_command',
                    action: action,
                    data: data,
                    timestamp: Date.now()
                });
                ws.send(message);
                console.log(`[Animation] 消息已发送: ${action}`);
            } else {
                console.log(`[Animation] WebSocket未连接，消息未发送: ${action}`);
            }
        }

        /**
         * 发送Prompt信号到realtimeEngine
         * @param {string} promptName - prompt名称
         * @param {string} stageName - 当前stage名称
         */
        sendPrompt(promptName, stageName) {
            const timestamp = Date.now() / 1000; // 转换为秒
            
            this.sendToRealtimeEngine('prompt', {
                name: promptName,
                stageName: stageName,
                timestamp: timestamp
            });
        }

        // ==================== 开场动画 ====================

        /**
         * 播放开场动画
         * @param {Function} onComplete - 动画完成后的回调
         */
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

        /**
         * 播放开场倒计时动画（默认）
         */
        playIntroCountdown(duration, onComplete) {
            const totalSeconds = Math.ceil(duration / 1000);
            let remaining = totalSeconds;
            
            // 更新显示
            if (this.gestureName) {
                this.gestureName.textContent = '准备开始';
            }
            if (this.gestureInstruction) {
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
            
            // 创建进度环容器（可选的视觉效果）
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

        /**
         * 播放开场视频（预留接口）
         */
        playIntroVideo(duration, onComplete) {
            console.log('[Animation] 播放开场视频（待实现）');
            // TODO: 实现视频播放
            // 目前回退到倒计时动画
            this.playIntroCountdown(duration, onComplete);
        }

        // ==================== Stage内容动画 ====================

        /**
         * 播放Stage内容动画
         * @param {Object} stage - stage配置对象
         * @param {Function} onComplete - 动画完成后的回调
         */
        playStageAnimation(stage, onComplete) {
            console.log('[Animation] 播放Stage动画:', stage.name);
            this.isPlaying = true;
            
            const duration = window.CollectionTiming ? 
                window.CollectionTiming.getStageDuration() : 5000;
            
            // 更新显示内容
            this.updateStageDisplay(stage);
            
            // 发送Prompt信号到realtimeEngine（动画开始时发送）
            this.sendPrompt(stage.name, stage.name);
            
            // 播放内容动画
            this.playStageContent(stage, duration, onComplete);
        }

        /**
         * 更新Stage显示内容
         */
        updateStageDisplay(stage) {
            // 显示手势图标
            if (this.gestureIcon && this.gestureIcon.parentElement) {
                this.gestureIcon.parentElement.style.display = '';
            }
            
            // 隐藏大倒计时
            if (this.countdown) {
                this.countdown.classList.remove('visible');
            }
            
            // 更新文字
            if (this.gestureName) {
                this.gestureName.textContent = stage.label || stage.name;
            }
            if (this.gestureInstruction) {
                this.gestureInstruction.textContent = stage.instruction || '请按照提示进行手势动作';
            }
        }

        /**
         * 播放Stage内容（默认：进度动画）
         * 这里是实际的5秒动画内容，后续可以替换为具体的手势演示动画
         */
        playStageContent(stage, duration, onComplete) {
            const startTime = Date.now();
            
            // 创建小型倒计时显示
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
         * @param {number} seconds - 倒计时秒数
         * @param {Function} onComplete - 完成回调
         */
        playCountdown(seconds, onComplete) {
            console.log('[Animation] 播放准备倒计时:', seconds, '秒');
            
            let remaining = seconds;
            
            // 更新显示
            if (this.gestureName) {
                this.gestureName.textContent = '准备';
            }
            if (this.gestureInstruction) {
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

        /**
         * 显示进度环（开场动画用）
         */
        showProgressRing(totalSeconds) {
            // 创建或获取进度环元素
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
                
                // 添加旋转动画样式
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

        /**
         * 更新进度环
         */
        updateProgressRing(remaining, total) {
            // 可以在这里更新进度环的样式
        }

        /**
         * 隐藏进度环
         */
        hideProgressRing() {
            const ring = document.getElementById('introProgressRing');
            if (ring) {
                ring.style.display = 'none';
            }
        }

        /**
         * 显示Stage计时器
         */
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

        /**
         * 更新Stage计时器
         */
        updateStageTimer(seconds) {
            const timer = document.getElementById('stageTimer');
            if (timer) {
                timer.textContent = seconds + 's';
                
                // 最后3秒变红
                if (seconds <= 3) {
                    timer.style.color = '#ef4444';
                } else {
                    timer.style.color = '#6b7280';
                }
            }
        }

        /**
         * 隐藏Stage计时器
         */
        hideStageTimer() {
            const timer = document.getElementById('stageTimer');
            if (timer) {
                timer.style.display = 'none';
            }
        }

        // ==================== 控制方法 ====================

        /**
         * 停止所有动画
         */
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
            
            this.isPlaying = false;
            this.hideProgressRing();
            this.hideStageTimer();
            
            if (this.countdown) {
                this.countdown.classList.remove('visible');
            }
        }

        /**
         * 检查是否正在播放
         */
        isAnimationPlaying() {
            return this.isPlaying;
        }

        /**
         * 重置显示
         */
        reset() {
            this.stop();
            
            if (this.gestureName) {
                this.gestureName.textContent = '点击开始';
            }
            if (this.gestureInstruction) {
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
