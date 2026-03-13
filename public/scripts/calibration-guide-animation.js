/**
 * calibration-guide-animation.js - 标定指导动画模块
 *
 * 作用：在屏幕中央显示醒目的标定提示，指导用户做出标准动作
 * 【修改】改为全屏居中显示，字体更大更醒目
 * 【新增】同时显示手势示范GIF
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

            // 内容配置 - 可后续扩展为视频路径和GIF路径
            this.contentConfig = {
                'continual_gesture_1': {
                    name: '食指弯曲',
                    icon: '👆',
                    gifUrl: null,  // 【新增】GIF路径
                    calibrate: {
                        title: '标定动作范围',
                        instruction: '请做完整动作：食指从伸直到弯曲',
                        duration: 10000,
                        videoUrl: null
                    }
                },
                'continual_gesture_2': {
                    name: '拇指食指捏合',
                    icon: '🤏',
                    gifUrl: null,
                    calibrate: {
                        title: '标定动作范围',
                        instruction: '请做完整动作：拇指食指从分开到捏合',
                        duration: 10000,
                        videoUrl: null
                    }
                },
                'continual_gesture_3': {
                    name: '手掌翻转',
                    icon: '🤚',
                    gifUrl: null,
                    calibrate: {
                        title: '标定动作范围',
                        instruction: '请做完整动作：手掌从向下到向上翻转',
                        duration: 10000,
                        videoUrl: null
                    }
                }
            };

            this.styles = {
                primaryColor: '#3b82f6',
                successColor: '#10b981',
                warningColor: '#f59e0b',
                textColor: '#1f2937',
                bgColor: 'rgba(255, 255, 255, 0.98)',
                overlayColor: 'rgba(0, 0, 0, 0.4)'
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

            // 【新增】尝试从模板配置获取GIF路径
            this._loadGifFromTemplate(taskId);

            // 使用全屏居中的标定提示
            this._showCenteredCountdown(taskId, phase, config);
        }

        /**
         * 【新增】从模板配置加载GIF路径
         */
        _loadGifFromTemplate(taskId) {
            try {
                const saved = localStorage.getItem('emg_collection_template');
                if (saved) {
                    const template = JSON.parse(saved);
                    // 从连续手势配置中获取GIF路径
                    const gestureIndex = taskId.replace('continual_gesture_', '');
                    const gestures = template.stages?.continual_gestures || [];
                    const gesture = gestures.find(g => g.id === taskId || g.name === this.contentConfig[taskId]?.name);
                    if (gesture && gesture.gif) {
                        this.contentConfig[taskId].gifUrl = gesture.gif;
                        console.log(`[CalibrationGuideAnimation] 加载GIF: ${gesture.gif}`);
                    }
                }
            } catch (e) {
                console.warn('[CalibrationGuideAnimation] 加载GIF配置失败:', e);
            }
        }

        /**
         * 【修改】全屏居中显示标定提示
         */
        _showCenteredCountdown(taskId, phase, config) {
            this._createContainer();

            const taskConfig = this.contentConfig[taskId];
            const accentColor = this.styles.primaryColor;
            this.remainingSeconds = Math.ceil(config.duration / 1000);

            // 获取GIF路径
            const gifUrl = taskConfig.gifUrl || this._getGestureGifUrl(taskId);
            const hasGif = gifUrl && gifUrl.length > 0;

            this.container.innerHTML = `
                <!-- 中央提示面板（无遮罩，位置偏右上避免遮挡左下角GIF） -->
                <div id="calibrationPanel" style="
                    position: fixed;
                    top: 45%;
                    left: 55%;
                    transform: translate(-50%, -50%);
                    background: ${this.styles.bgColor};
                    border-radius: 24px;
                    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.15);
                    border: 2px solid ${this.styles.primaryColor};
                    z-index: 9999;
                    padding: 30px 40px;
                    text-align: center;
                    min-width: 380px;
                    max-width: 500px;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                ">
                    <!-- 图标 -->
                    <div style="font-size: 72px; margin-bottom: 16px;">
                        ${taskConfig.icon}
                    </div>

                    <!-- 标题 -->
                    <div style="
                        font-size: 36px;
                        font-weight: 700;
                        color: ${accentColor};
                        margin-bottom: 12px;
                    ">
                        ${config.title}
                    </div>

                    <!-- 手势名称 -->
                    <div style="
                        font-size: 28px;
                        font-weight: 600;
                        color: ${this.styles.textColor};
                        margin-bottom: 16px;
                    ">
                        ${taskConfig.name}
                    </div>

                    <!-- 指导说明 -->
                    <div style="
                        font-size: 20px;
                        color: #6b7280;
                        margin-bottom: 32px;
                        line-height: 1.5;
                    ">
                        ${config.instruction}
                    </div>

                    <!-- 倒计时 -->
                    <div style="
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        gap: 16px;
                    ">
                        <div style="
                            font-size: 18px;
                            color: #9ca3af;
                        ">
                            剩余时间
                        </div>
                        <div id="calibrationCountdown" style="
                            font-size: 72px;
                            font-weight: 800;
                            color: ${accentColor};
                            min-width: 100px;
                            line-height: 1;
                        ">
                            ${this.remainingSeconds}
                        </div>
                        <div style="
                            font-size: 24px;
                            color: #9ca3af;
                        ">
                            秒
                        </div>
                    </div>

                    <!-- 进度条 -->
                    <div style="
                        margin-top: 24px;
                        background: #e5e7eb;
                        border-radius: 8px;
                        height: 8px;
                        overflow: hidden;
                    ">
                        <div id="calibrationProgress" style="
                            height: 100%;
                            background: linear-gradient(90deg, ${accentColor}, #60a5fa);
                            border-radius: 8px;
                            width: 100%;
                            transition: width 1s linear;
                        "></div>
                    </div>
                </div>

                <!-- 【新增】手势示范GIF（左下角） -->
                ${hasGif ? `
                <div id="calibrationGifContainer" style="
                    position: fixed;
                    bottom: 100px;
                    left: 40px;
                    width: 220px;
                    background: white;
                    border-radius: 16px;
                    box-shadow: 0 8px 30px rgba(0, 0, 0, 0.2);
                    overflow: hidden;
                    z-index: 10000;
                ">
                    <div style="
                        display: flex;
                        align-items: center;
                        gap: 8px;
                        padding: 10px 14px;
                        background: #3b82f6;
                        color: white;
                        font-size: 14px;
                        font-weight: 600;
                    ">
                        <i class="fas fa-hand-point-right"></i>
                        <span>手势示范</span>
                    </div>
                    <div style="
                        padding: 8px;
                        background: #f8fafc;
                    ">
                        <img src="${gifUrl}" alt="手势示范" style="
                            width: 100%;
                            height: auto;
                            max-height: 180px;
                            object-fit: contain;
                            border-radius: 8px;
                        " onerror="this.parentElement.parentElement.style.display='none'">
                    </div>
                </div>
                ` : ''}
            `;

            this.container.style.display = 'block';
            this.isShowing = true;

            // 【新增】同时显示左下角的手势示范GIF组件
            this._showGestureGifWidget(taskId);

            // 启动倒计时
            this._startCountdown();
        }

        /**
         * 【新增】显示左下角手势示范GIF组件（使用现有的gestureGifContainer）
         */
        _showGestureGifWidget(taskId) {
            // 尝试使用现有的手势GIF组件
            if (window.collectionController && typeof window.collectionController.showGestureGif === 'function') {
                // 构造一个临时的gesture对象
                const taskConfig = this.contentConfig[taskId];
                const gifFile = this._getGestureGifFile(taskId);

                if (gifFile) {
                    const gesture = {
                        name: taskConfig?.name || '手势示范',
                        gifFile: gifFile  // 使用 gifFile 字段，与 showGestureGif 方法匹配
                    };
                    console.log('[CalibrationGuideAnimation] 显示手势GIF:', gifFile);
                    window.collectionController.showGestureGif(gesture);
                } else {
                    console.warn('[CalibrationGuideAnimation] 未找到手势GIF配置:', taskId);
                }
            }
        }

        /**
         * 【新增】获取手势GIF文件名（仅文件名，不含路径）
         */
        _getGestureGifFile(taskId) {
            try {
                // 从 collectionController 的 collectionConfig 中获取
                if (window.collectionController) {
                    const gestureKeyMap = {
                        'continual_gesture_1': 'continual_1',
                        'continual_gesture_2': 'continual_2',
                        'continual_gesture_3': 'continual_3'
                    };
                    const gestureKey = gestureKeyMap[taskId];
                    const gestureConfig = window.collectionController.collectionConfig?.gestures?.[gestureKey];

                    if (gestureConfig && gestureConfig.length > 0 && gestureConfig[0].gifFile) {
                        return gestureConfig[0].gifFile;
                    }

                    // 尝试从 template 获取
                    const template = window.collectionController.getLatestTemplate?.();
                    const templateGesture = template?.gestures?.[gestureKey];
                    if (templateGesture && templateGesture.length > 0 && templateGesture[0].gifFile) {
                        return templateGesture[0].gifFile;
                    }
                }
            } catch (e) {
                console.warn('[CalibrationGuideAnimation] 获取GIF文件名失败:', e);
            }
            return null;
        }

        /**
         * 【新增】获取手势GIF路径
         */
        _getGestureGifUrl(taskId) {
            try {
                // 从模板配置获取
                const saved = localStorage.getItem('emg_collection_template');
                if (saved) {
                    const template = JSON.parse(saved);
                    const gestures = template.stages?.continual_gestures || [];

                    // 根据taskId查找对应手势
                    const gestureIndex = parseInt(taskId.replace('continual_gesture_', '')) - 1;
                    if (gestures[gestureIndex] && gestures[gestureIndex].gif) {
                        return gestures[gestureIndex].gif;
                    }

                    // 或者按名称匹配
                    const taskConfig = this.contentConfig[taskId];
                    const gesture = gestures.find(g => g.name === taskConfig?.name);
                    if (gesture && gesture.gif) {
                        return gesture.gif;
                    }
                }
            } catch (e) {
                console.warn('[CalibrationGuideAnimation] 获取GIF路径失败:', e);
            }
            return null;
        }

        /**
         * 启动倒计时
         */
        _startCountdown() {
            const countdownEl = document.getElementById('calibrationCountdown');
            const progressEl = document.getElementById('calibrationProgress');
            const totalSeconds = this.remainingSeconds;

            this.countdownTimer = setInterval(() => {
                this.remainingSeconds--;

                if (countdownEl) {
                    countdownEl.textContent = this.remainingSeconds > 0 ? this.remainingSeconds : '✓';

                    // 最后3秒变红色提醒
                    if (this.remainingSeconds <= 3 && this.remainingSeconds > 0) {
                        countdownEl.style.color = '#ef4444';
                    }
                }

                // 更新进度条
                if (progressEl) {
                    const progress = (this.remainingSeconds / totalSeconds) * 100;
                    progressEl.style.width = `${progress}%`;
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
         * 【修改】创建容器（改为全屏容器）
         */
        _createContainer() {
            // 如果已存在，先清理
            if (this.container) {
                this.container.innerHTML = '';
                return;
            }

            this.container = document.createElement('div');
            this.container.id = 'calibrationGuideContainer';
            this.container.style.cssText = `
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                z-index: 9998;
                display: none;
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
                this.container.innerHTML = '';
            }

            // 【新增】隐藏手势示范GIF组件
            if (window.collectionController && typeof window.collectionController.hideGestureGif === 'function') {
                window.collectionController.hideGestureGif();
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

        /**
         * 【新增】设置GIF路径（供外部配置）
         */
        setGifUrl(taskId, gifUrl) {
            if (this.contentConfig[taskId]) {
                this.contentConfig[taskId].gifUrl = gifUrl;
                console.log(`[CalibrationGuideAnimation] 设置GIF: ${taskId} -> ${gifUrl}`);
            }
        }
    }

    const calibrationGuideAnimation = new CalibrationGuideAnimation();
    window.calibrationGuideAnimation = calibrationGuideAnimation;

    console.log('[CalibrationGuideAnimation] 模块加载完成');

})();
