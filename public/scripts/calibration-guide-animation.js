/**
 * calibration-guide-animation.js - 标定指导动画模块
 *
 * 作用：显示简单的倒计时提示，指导用户做出标准动作
 * 采用非遮挡式设计，在屏幕角落显示倒计时
 * 保留视频接口，便于后续替换为本地视频
 */

(function() {
    'use strict';

    console.log('[CalibrationGuideAnimation] 模块开始加载...');

    class CalibrationGuideAnimation {
        constructor() {
            this.container = null;
            this.videoElement = null;  // 保留视频接口

            this.isShowing = false;
            this.currentTaskId = null;
            this.currentPhase = null;
            this.onComplete = null;
            this.onSkip = null;

            this.countdownTimer = null;
            this.remainingSeconds = 0;

            // 内容配置 - 可后续扩展为视频路径
            this.contentConfig = {
                'continual_gesture_1': {
                    name: '食指弯曲',
                    icon: '👆',
                    calibrate: {
                        title: '标定动作范围',
                        instruction: '请做完整动作：食指从伸直到弯曲',
                        duration: 5000,
                        videoUrl: null
                    }
                },
                'continual_gesture_2': {
                    name: '拇指食指捏合',
                    icon: '🤏',
                    calibrate: {
                        title: '标定动作范围',
                        instruction: '请做完整动作：拇指食指从分开到捏合',
                        duration: 5000,
                        videoUrl: null
                    }
                },
                'continual_gesture_3': {
                    name: '手掌翻转',
                    icon: '🤚',
                    calibrate: {
                        title: '标定动作范围',
                        instruction: '请做完整动作：手掌从向下到向上翻转',
                        duration: 5000,
                        videoUrl: null
                    }
                }
            };

            this.styles = {
                primaryColor: '#3b82f6',
                successColor: '#10b981',
                warningColor: '#f59e0b',
                textColor: '#1f2937',
                bgColor: 'rgba(255, 255, 255, 0.95)'
            };
        }

        /**
         * 显示标定指导
         * @param {string} taskId - 任务ID
         * @param {string} phase - 阶段: 'demo' | 'calibrate_min' | 'calibrate_max'
         * @param {function} onComplete - 完成回调
         * @param {function} onSkip - 跳过回调（仅demo阶段有效）
         */
        show(taskId, phase, onComplete, onSkip = null) {
            console.log(`[CalibrationGuideAnimation] 显示: ${taskId}, ${phase}`);

            this.currentTaskId = taskId;
            this.currentPhase = phase;
            this.onComplete = onComplete;
            this.onSkip = onSkip;

            const config = this.contentConfig[taskId]?.[phase];
            if (!config) {
                console.warn(`[CalibrationGuideAnimation] 未找到配置: ${taskId}/${phase}`);
                if (onComplete) onComplete();
                return;
            }

            // 检查是否有视频配置
            if (config.videoUrl) {
                this._playVideo(config.videoUrl, onComplete);
                return;
            }

            // 使用简单倒计时
            this._showCountdown(taskId, phase, config);
        }

        /**
         * 显示简单倒计时（非遮挡式）
         */
        _showCountdown(taskId, phase, config) {
            this._createContainer();

            const taskConfig = this.contentConfig[taskId];

            // 标定阶段使用主色调
            const accentColor = this.styles.primaryColor;

            this.remainingSeconds = Math.ceil(config.duration / 1000);

            this.container.innerHTML = `
                <div style="display: flex; align-items: center; gap: 12px;">
                    <div style="font-size: 28px;">${taskConfig.icon}</div>
                    <div style="flex: 1;">
                        <div style="font-size: 14px; font-weight: 600; color: ${accentColor}; margin-bottom: 2px;">
                            ${config.title}
                        </div>
                        <div style="font-size: 13px; color: ${this.styles.textColor};">
                            ${config.instruction}
                        </div>
                    </div>
                    <div id="calibrationCountdown" style="font-size: 24px; font-weight: 700; color: ${accentColor}; min-width: 40px; text-align: center;">
                        ${this.remainingSeconds}
                    </div>
                </div>
            `;

            this.container.style.display = 'block';
            this.isShowing = true;

            // 启动倒计时
            this._startCountdown();
        }

        /**
         * 启动倒计时
         */
        _startCountdown() {
            const countdownEl = document.getElementById('calibrationCountdown');

            this.countdownTimer = setInterval(() => {
                this.remainingSeconds--;

                if (countdownEl) {
                    countdownEl.textContent = this.remainingSeconds > 0 ? this.remainingSeconds : '✓';
                }

                if (this.remainingSeconds <= 0) {
                    this._onCountdownComplete();
                }
            }, 1000);
        }

        /**
         * 倒计时完成
         */
        _onCountdownComplete() {
            if (this.countdownTimer) {
                clearInterval(this.countdownTimer);
                this.countdownTimer = null;
            }

            // 短暂延迟后自动继续
            setTimeout(() => {
                if (this.isShowing) {
                    this.hide();
                    if (this.onComplete) this.onComplete();
                }
            }, 300);
        }

        /**
         * 创建容器（非遮挡式，显示在右上角）
         */
        _createContainer() {
            if (this.container) return;

            this.container = document.createElement('div');
            this.container.id = 'calibrationGuideContainer';
            this.container.style.cssText = `
                position: fixed;
                top: 80px;
                right: 20px;
                width: 320px;
                padding: 16px;
                background: ${this.styles.bgColor};
                border-radius: 12px;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
                z-index: 9999;
                display: none;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            `;

            document.body.appendChild(this.container);
        }

        /**
         * 播放视频（保留接口，后续实现）
         */
        _playVideo(videoUrl, onComplete) {
            console.log(`[CalibrationGuideAnimation] 播放视频: ${videoUrl}`);
            // TODO: 实现视频播放逻辑
            // 目前直接调用完成回调
            if (onComplete) onComplete();
        }

        /**
         * 隐藏
         */
        hide() {
            if (this.countdownTimer) {
                clearInterval(this.countdownTimer);
                this.countdownTimer = null;
            }

            if (this.container) {
                this.container.style.display = 'none';
            }

            this.isShowing = false;
            this.currentTaskId = null;
            this.currentPhase = null;
        }

        /**
         * 销毁
         */
        destroy() {
            this.hide();
            if (this.container && this.container.parentElement) {
                this.container.parentElement.removeChild(this.container);
            }
            this.container = null;
            this.videoElement = null;
        }

        /**
         * 设置视频路径（供外部配置）
         */
        setVideoUrl(taskId, phase, videoUrl) {
            if (this.contentConfig[taskId]?.[phase]) {
                this.contentConfig[taskId][phase].videoUrl = videoUrl;
                console.log(`[CalibrationGuideAnimation] 设置视频: ${taskId}/${phase} -> ${videoUrl}`);
            }
        }
    }

    const calibrationGuideAnimation = new CalibrationGuideAnimation();
    window.calibrationGuideAnimation = calibrationGuideAnimation;

    console.log('[CalibrationGuideAnimation] 模块加载完成');

})();
