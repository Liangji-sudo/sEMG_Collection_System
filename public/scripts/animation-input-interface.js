/**
 * animation-input-interface.js - 动画输入接口层
 * 
 * 作用：
 * 1. 接收动捕数据（或鼠标滚轮模拟数据）
 * 2. 进行标定（记录最小值、最大值）
 * 3. 将原始输入归一化为 0-1 范围
 * 4. 提供统一接口给 continual-gesture-X-animation.js 使用
 * 
 * 数据流：
 * mocap_server.py → realtimeEngine.js → 前端WebSocket → AnimationInputInterface
 *                                                              ↓
 *                                        continual-gesture-X-animation.js
 */

(function() {
    'use strict';

    console.log('[AnimationInputInterface] 模块开始加载...');

    class AnimationInputInterface {
        constructor() {
            // ==================== 输入源配置 ====================
            this.inputSource = 'mocap';  // 'mocap' | 'mouse'
            
            // 动捕通道映射：任务类型 → 动捕通道名
            this.channelMapping = {
                'continual_gesture_1': 'finger_joint_angle',
                'continual_gesture_2': 'thumb_index_distance',
                'continual_gesture_3': 'palm_rotation_angle'
            };
            
            this.currentTaskId = null;
            
            // ==================== 原始数据 ====================
            this.rawValue = 0;
            this.mouseWheelValue = 0;
            
            // ==================== 标定状态 ====================
            this.calibration = {
                isCalibrating: false,
                phase: null,
                currentTask: null,
                rawValues: [],
                min: null,
                max: null,
                isCalibrated: false,
                startTime: null,
                endTime: null
            };
            
            // ==================== 各任务的标定数据缓存 ====================
            this.calibrationCache = {
                'continual_gesture_1': { min: null, max: null, isCalibrated: false },
                'continual_gesture_2': { min: null, max: null, isCalibrated: false },
                'continual_gesture_3': { min: null, max: null, isCalibrated: false }
            };
            
            // ==================== 归一化输出 ====================
            this.normalizedValue = 0;
            
            // ==================== 事件回调 ====================
            this.onDataUpdate = null;
            this.onCalibrationComplete = null;
            
            // ==================== 鼠标滚轮监听 ====================
            this._wheelHandler = this._handleLocalWheel.bind(this);
            this._isLocalWheelListening = false;
            
            // ==================== 数据平滑 ====================
            this.smoothingEnabled = true;
            this.smoothingFactor = 0.3;
            this._smoothedValue = 0;
            
            this.debugMode = false;
        }

        // ==================== 初始化 ====================
        
        init() {
            console.log('[AnimationInputInterface] 初始化...');
            this._loadCalibrationCache();
            this._connectWebSocket();
            console.log('[AnimationInputInterface] 初始化完成');
            return true;
        }

        /**
         * 连接到 realtimeEngine.js 的 WebSocket 服务（独立连接）
         */
        _connectWebSocket() {
            this.ws = null;
            this.wsUrl = 'ws://localhost:8080';
            this.reconnectInterval = 3000;
            this.reconnectTimer = null;
            this.isConnected = false;

            try {
                console.log(`[AnimationInputInterface] 正在连接 ${this.wsUrl}...`);
                this.ws = new WebSocket(this.wsUrl);

                this.ws.onopen = () => {
                    console.log('[AnimationInputInterface] ✓ WebSocket连接成功');
                    this.isConnected = true;
                    clearTimeout(this.reconnectTimer);
                };

                this.ws.onmessage = (event) => {
                    try {
                        const packet = JSON.parse(event.data);

                        // 只处理 mocap 数据
                        if (packet.type === 'mocap_data' && packet.data) {
                            this.onMocapData(packet.data);
                        }

                        // 处理 mocap 连接状态
                        if (packet.type === 'mocap_connection_status') {
                            console.log('[AnimationInputInterface] Mocap连接状态:', packet.connected ? '已连接' : '已断开');
                        }
                    } catch (error) {
                        console.error('[AnimationInputInterface] 消息解析失败:', error);
                    }
                };

                this.ws.onerror = (error) => {
                    console.error('[AnimationInputInterface] WebSocket错误:', error);
                };

                this.ws.onclose = (event) => {
                    console.log(`[AnimationInputInterface] WebSocket连接关闭 (code: ${event.code})`);
                    this.isConnected = false;

                    // 自动重连
                    this.reconnectTimer = setTimeout(() => {
                        this._connectWebSocket();
                    }, this.reconnectInterval);
                };

            } catch (error) {
                console.error('[AnimationInputInterface] 创建WebSocket失败:', error);
                this.reconnectTimer = setTimeout(() => {
                    this._connectWebSocket();
                }, this.reconnectInterval);
            }
        }
        
        setCurrentTask(taskId) {
            if (this.channelMapping[taskId]) {
                this.currentTaskId = taskId;
                
                const cached = this.calibrationCache[taskId];
                if (cached && cached.isCalibrated) {
                    this.calibration.min = cached.min;
                    this.calibration.max = cached.max;
                    this.calibration.isCalibrated = true;
                    console.log(`[AnimationInputInterface] 恢复任务 ${taskId} 的标定数据: min=${cached.min}, max=${cached.max}`);
                } else {
                    this.calibration.min = null;
                    this.calibration.max = null;
                    this.calibration.isCalibrated = false;
                }
                
                this._notifyChannelChange(this.channelMapping[taskId]);
                console.log(`[AnimationInputInterface] 当前任务: ${taskId}, 通道: ${this.channelMapping[taskId]}`);
            } else {
                console.warn(`[AnimationInputInterface] 未知任务类型: ${taskId}`);
            }
        }

        // ==================== 输入源管理 ====================
        
        setInputSource(source) {
            if (source === 'mocap' || source === 'mouse') {
                this.inputSource = source;
                console.log(`[AnimationInputInterface] 输入源: ${source}`);
                
                if (source === 'mouse' && !this._isLocalWheelListening) {
                    this._startLocalWheelListener();
                } else if (source === 'mocap' && this._isLocalWheelListening) {
                    this._stopLocalWheelListener();
                }
            }
        }
        
        _startLocalWheelListener() {
            if (this._isLocalWheelListening) return;
            document.addEventListener('wheel', this._wheelHandler, { passive: false });
            this._isLocalWheelListening = true;
            console.log('[AnimationInputInterface] 本地滚轮监听已启动');
        }
        
        _stopLocalWheelListener() {
            if (!this._isLocalWheelListening) return;
            document.removeEventListener('wheel', this._wheelHandler);
            this._isLocalWheelListening = false;
            console.log('[AnimationInputInterface] 本地滚轮监听已停止');
        }
        
        _handleLocalWheel(e) {
            if (this.inputSource !== 'mouse') return;
            
            let multiplier = 1.0;
            if (e.deltaMode === 1) multiplier = 10;
            if (e.deltaMode === 2) multiplier = 50;
            
            this.mouseWheelValue += e.deltaY * multiplier * 0.1;
            this.mouseWheelValue = Math.max(0, Math.min(100, this.mouseWheelValue));
            this.rawValue = this.mouseWheelValue;
            
            if (this.calibration.isCalibrating) {
                this.calibration.rawValues.push(this.rawValue);
            }
            
            this._updateNormalizedValue();
        }

        // ==================== 动捕数据接收 ====================
        
        onMocapData(data) {
            if (this.inputSource !== 'mocap') return;
            if (!this.currentTaskId) return;
            
            const channelName = this.channelMapping[this.currentTaskId];
            if (!channelName) return;
            
            if (data.channels && data.channels[channelName] !== undefined) {
                const channelData = data.channels[channelName];
                this.rawValue = typeof channelData === 'object' ? channelData.value : channelData;
                
                if (this.calibration.isCalibrating) {
                    this.calibration.rawValues.push(this.rawValue);
                }
                
                this._updateNormalizedValue();
                
                if (this.onDataUpdate) {
                    this.onDataUpdate({
                        rawValue: this.rawValue,
                        normalizedValue: this.normalizedValue,
                        isCalibrated: this.calibration.isCalibrated
                    });
                }
            }
        }
        
        _notifyChannelChange(channelName) {
            if (window.dispatchEvent) {
                window.dispatchEvent(new CustomEvent('mocap_channel_change', {
                    detail: { channel: channelName }
                }));
            }
        }

        // ==================== 标定功能 ====================

        /**
         * 开始标定（单阶段：受试者做完整动作范围，系统自动提取 min/max）
         */
        startCalibration(taskId) {
            console.log(`[AnimationInputInterface] 开始标定: ${taskId}`);

            this.calibration.isCalibrating = true;
            this.calibration.phase = 'calibrating';
            this.calibration.currentTask = taskId;
            this.calibration.rawValues = [];
            this.calibration.startTime = Date.now();
            this.calibration.min = null;
            this.calibration.max = null;
            this.calibration.isCalibrated = false;

            this.setCurrentTask(taskId);
        }

        /**
         * 结束标定（从采集的数据中自动提取 min 和 max）
         */
        endCalibration() {
            if (!this.calibration.isCalibrating) {
                console.warn('[AnimationInputInterface] 未在标定状态');
                return null;
            }

            this.calibration.endTime = Date.now();
            this.calibration.isCalibrating = false;

            const values = this.calibration.rawValues;

            if (values.length === 0) {
                console.warn('[AnimationInputInterface] 标定期间未收到数据');
                return null;
            }

            // 排序数据
            const sortedValues = [...values].sort((a, b) => a - b);

            // 为了避免极端噪声，去掉前后 5% 的数据
            const trimPercent = 0.05;
            const trimCount = Math.floor(sortedValues.length * trimPercent);
            const trimmedValues = sortedValues.slice(trimCount, sortedValues.length - trimCount);

            // 从修剪后的数据中提取 min 和 max
            const calibratedMin = trimmedValues[0] || sortedValues[0];
            const calibratedMax = trimmedValues[trimmedValues.length - 1] || sortedValues[sortedValues.length - 1];

            // 确保 min < max
            this.calibration.min = Math.min(calibratedMin, calibratedMax);
            this.calibration.max = Math.max(calibratedMin, calibratedMax);

            // 检查范围是否有效
            if (this.calibration.max - this.calibration.min < 0.01) {
                console.warn('[AnimationInputInterface] 标定范围过小，使用默认扩展');
                this.calibration.max = this.calibration.min + 10;
            }

            this.calibration.isCalibrated = true;

            // 缓存标定数据
            if (this.calibration.currentTask) {
                this.calibrationCache[this.calibration.currentTask] = {
                    min: this.calibration.min,
                    max: this.calibration.max,
                    isCalibrated: true
                };
                this._saveCalibrationCache();
            }

            console.log(`[AnimationInputInterface] 标定完成: min=${this.calibration.min.toFixed(2)}, max=${this.calibration.max.toFixed(2)} (采样${values.length}个)`);

            if (this.onCalibrationComplete) {
                this.onCalibrationComplete({
                    taskId: this.calibration.currentTask,
                    min: this.calibration.min,
                    max: this.calibration.max
                });
            }

            const result = {
                sampleCount: values.length,
                duration: this.calibration.endTime - this.calibration.startTime,
                rawMin: Math.min(...values),
                rawMax: Math.max(...values),
                calibratedMin: this.calibration.min,
                calibratedMax: this.calibration.max,
                isCalibrated: true
            };

            this.calibration.phase = null;
            return result;
        }
        
        resetCalibration(taskId = null) {
            const targetTask = taskId || this.currentTaskId;
            
            if (targetTask) {
                this.calibrationCache[targetTask] = { min: null, max: null, isCalibrated: false };
                
                if (targetTask === this.currentTaskId) {
                    this.calibration.min = null;
                    this.calibration.max = null;
                    this.calibration.isCalibrated = false;
                }
                
                this._saveCalibrationCache();
                console.log(`[AnimationInputInterface] 已重置任务 ${targetTask} 的标定数据`);
            }
        }
        
        isCalibrated() {
            return this.calibration.isCalibrated;
        }
        
        getCalibrationStatus() {
            return {
                isCalibrating: this.calibration.isCalibrating,
                phase: this.calibration.phase,
                isCalibrated: this.calibration.isCalibrated,
                min: this.calibration.min,
                max: this.calibration.max,
                currentTask: this.calibration.currentTask
            };
        }

        // ==================== 归一化计算 ====================
        
        _updateNormalizedValue() {
            let normalized;
            
            if (this.calibration.isCalibrated && 
                this.calibration.min !== null && 
                this.calibration.max !== null) {
                const range = this.calibration.max - this.calibration.min;
                if (range > 0) {
                    normalized = (this.rawValue - this.calibration.min) / range;
                } else {
                    normalized = 0;
                }
            } else {
                normalized = this.rawValue / 100;
            }
            
            normalized = Math.max(0, Math.min(1, normalized));
            
            if (this.smoothingEnabled) {
                this._smoothedValue = this._smoothedValue * (1 - this.smoothingFactor) + 
                                     normalized * this.smoothingFactor;
                this.normalizedValue = this._smoothedValue;
            } else {
                this.normalizedValue = normalized;
            }
        }
        
        getNormalizedInput() {
            return this.normalizedValue;
        }
        
        getRawInput() {
            return this.rawValue;
        }
        
        getMappedInput(min, max) {
            return min + this.normalizedValue * (max - min);
        }

        // ==================== 缓存管理 ====================
        
        _saveCalibrationCache() {
            try {
                localStorage.setItem('emg_animation_calibration', JSON.stringify(this.calibrationCache));
            } catch (e) {
                console.warn('[AnimationInputInterface] 保存标定缓存失败:', e);
            }
        }
        
        _loadCalibrationCache() {
            try {
                const saved = localStorage.getItem('emg_animation_calibration');
                if (saved) {
                    const parsed = JSON.parse(saved);
                    Object.assign(this.calibrationCache, parsed);
                    console.log('[AnimationInputInterface] 已加载标定缓存');
                }
            } catch (e) {
                console.warn('[AnimationInputInterface] 加载标定缓存失败:', e);
            }
        }
        
        clearCalibrationCache() {
            this.calibrationCache = {
                'continual_gesture_1': { min: null, max: null, isCalibrated: false },
                'continual_gesture_2': { min: null, max: null, isCalibrated: false },
                'continual_gesture_3': { min: null, max: null, isCalibrated: false }
            };
            this.calibration.min = null;
            this.calibration.max = null;
            this.calibration.isCalibrated = false;
            localStorage.removeItem('emg_animation_calibration');
            console.log('[AnimationInputInterface] 已清除所有标定缓存');
        }

        // ==================== 清理 ====================

        destroy() {
            this._stopLocalWheelListener();

            // 关闭 WebSocket 连接
            if (this.ws) {
                clearTimeout(this.reconnectTimer);
                this.ws.close(1000, '用户主动断开');
                this.ws = null;
                this.isConnected = false;
            }

            this.onDataUpdate = null;
            this.onCalibrationComplete = null;
            console.log('[AnimationInputInterface] 已销毁');
        }
    }

    // ==================== 创建全局实例 ====================
    
    const animationInputInterface = new AnimationInputInterface();
    animationInputInterface.init();
    
    window.animationInputInterface = animationInputInterface;
    
    console.log('[AnimationInputInterface] 模块加载完成');

})();
