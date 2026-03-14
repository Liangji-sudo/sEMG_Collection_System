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
            
            // 动捕通道映射：任务类型 → 动捕通道名（基础名，实际使用时加 _L/_R 后缀）
            this.channelMapping = {
                'continual_gesture_1': 'finger_joint_angle',
                'continual_gesture_2': 'thumb_index_distance'
            };

            this.currentTaskId = null;

            // ==================== 原始数据（双手） ====================
            this.rawValue_L = 0;  // 左手原始值
            this.rawValue_R = 0;  // 右手原始值
            this.rawValue = 0;    // 兼容旧接口（取左手值）
            this.mouseWheelValue = 0;
            
            // ==================== 标定状态（双手独立） ====================
            this.calibration = {
                isCalibrating: false,
                phase: null,
                currentTask: null,
                // 左手标定数据
                rawValues_L: [],
                min_L: null,
                max_L: null,
                isCalibrated_L: false,
                // 右手标定数据
                rawValues_R: [],
                min_R: null,
                max_R: null,
                isCalibrated_R: false,
                // 兼容旧接口
                rawValues: [],
                min: null,
                max: null,
                isCalibrated: false,
                startTime: null,
                endTime: null
            };

            // ==================== 各任务的标定数据缓存（双手独立） ====================
            this.calibrationCache = {
                'continual_gesture_1': {
                    min_L: null, max_L: null, isCalibrated_L: false,
                    min_R: null, max_R: null, isCalibrated_R: false,
                    min: null, max: null, isCalibrated: false  // 兼容
                },
                'continual_gesture_2': {
                    min_L: null, max_L: null, isCalibrated_L: false,
                    min_R: null, max_R: null, isCalibrated_R: false,
                    min: null, max: null, isCalibrated: false
                }
            };

            // ==================== 归一化输出（双手） ====================
            this.normalizedValue_L = 0;  // 左手归一化值
            this.normalizedValue_R = 0;  // 右手归一化值
            this.normalizedValue = 0;    // 兼容旧接口（取左手值）
            
            // ==================== 事件回调 ====================
            this.onDataUpdate = null;
            this.onCalibrationComplete = null;
            
            // ==================== 鼠标滚轮监听 ====================
            this._wheelHandler = this._handleLocalWheel.bind(this);
            this._isLocalWheelListening = false;
            
            // ==================== 数据平滑（双手） ====================
            this.smoothingEnabled = true;
            this.smoothingFactor = 0.3;
            this._smoothedValue_L = 0;
            this._smoothedValue_R = 0;
            this._smoothedValue = 0;  // 兼容

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
            // 【修复】防止重复连接
            if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
                console.log('[AnimationInputInterface] WebSocket已连接或正在连接，跳过');
                return;
            }

            // 【修复】清理旧连接
            if (this.ws) {
                this.ws.onopen = null;
                this.ws.onclose = null;
                this.ws.onerror = null;
                this.ws.onmessage = null;
                if (this.ws.readyState === WebSocket.OPEN) {
                    this.ws.close();
                }
                this.ws = null;
            }

            this.wsUrl = 'ws://localhost:8080';
            this.reconnectInterval = 3000;
            this.maxReconnectAttempts = 10;
            this._reconnectAttempts = this._reconnectAttempts || 0;

            // 【修复】限制重连次数
            if (this._reconnectAttempts >= this.maxReconnectAttempts) {
                console.warn('[AnimationInputInterface] 达到最大重连次数，停止重连');
                return;
            }

            try {
                console.log(`[AnimationInputInterface] 正在连接 ${this.wsUrl}... (尝试 ${this._reconnectAttempts + 1})`);
                this.ws = new WebSocket(this.wsUrl);

                this.ws.onopen = () => {
                    console.log('[AnimationInputInterface] ✓ WebSocket连接成功');
                    this.isConnected = true;
                    this._reconnectAttempts = 0;  // 重置重连计数
                    clearTimeout(this.reconnectTimer);

                    // 【新增】自报身份
                    this.ws.send(JSON.stringify({
                        type: 'client_identify',
                        clientName: 'AnimationInput'
                    }));
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
                    console.error('[AnimationInputInterface] WebSocket错误');
                };

                this.ws.onclose = (event) => {
                    console.log(`[AnimationInputInterface] WebSocket连接关闭 (code: ${event.code})`);
                    this.isConnected = false;
                    this.ws = null;

                    // 【修复】只在非主动关闭时重连
                    if (event.code !== 1000) {
                        this._reconnectAttempts++;
                        if (this._reconnectAttempts < this.maxReconnectAttempts) {
                            clearTimeout(this.reconnectTimer);
                            this.reconnectTimer = setTimeout(() => {
                                this._connectWebSocket();
                            }, this.reconnectInterval);
                        }
                    }
                };

            } catch (error) {
                console.error('[AnimationInputInterface] 创建WebSocket失败:', error);
                this._reconnectAttempts++;
                if (this._reconnectAttempts < this.maxReconnectAttempts) {
                    clearTimeout(this.reconnectTimer);
                    this.reconnectTimer = setTimeout(() => {
                        this._connectWebSocket();
                    }, this.reconnectInterval);
                }
            }
        }
        
        setCurrentTask(taskId) {
            if (this.channelMapping[taskId]) {
                this.currentTaskId = taskId;

                const cached = this.calibrationCache[taskId];
                if (cached) {
                    // 恢复左手标定数据
                    if (cached.isCalibrated_L) {
                        this.calibration.min_L = cached.min_L;
                        this.calibration.max_L = cached.max_L;
                        this.calibration.isCalibrated_L = true;
                    } else {
                        this.calibration.min_L = null;
                        this.calibration.max_L = null;
                        this.calibration.isCalibrated_L = false;
                    }
                    // 恢复右手标定数据
                    if (cached.isCalibrated_R) {
                        this.calibration.min_R = cached.min_R;
                        this.calibration.max_R = cached.max_R;
                        this.calibration.isCalibrated_R = true;
                    } else {
                        this.calibration.min_R = null;
                        this.calibration.max_R = null;
                        this.calibration.isCalibrated_R = false;
                    }
                    // 兼容旧接口（使用左手数据）
                    this.calibration.min = this.calibration.min_L;
                    this.calibration.max = this.calibration.max_L;
                    this.calibration.isCalibrated = this.calibration.isCalibrated_L && this.calibration.isCalibrated_R;

                    if (this.calibration.isCalibrated_L || this.calibration.isCalibrated_R) {
                        console.log(`[AnimationInputInterface] 恢复任务 ${taskId} 的标定数据: L=[${cached.min_L?.toFixed(2)}, ${cached.max_L?.toFixed(2)}], R=[${cached.min_R?.toFixed(2)}, ${cached.max_R?.toFixed(2)}]`);
                    }
                } else {
                    this.calibration.min_L = null;
                    this.calibration.max_L = null;
                    this.calibration.isCalibrated_L = false;
                    this.calibration.min_R = null;
                    this.calibration.max_R = null;
                    this.calibration.isCalibrated_R = false;
                    this.calibration.min = null;
                    this.calibration.max = null;
                    this.calibration.isCalibrated = false;
                }

                this._notifyChannelChange(this.channelMapping[taskId]);
                console.log(`[AnimationInputInterface] 当前任务: ${taskId}, 通道: ${this.channelMapping[taskId]}_L/_R`);
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

        // ==================== 动捕数据接收（双手） ====================

        onMocapData(data) {
            // 调试：打印收到的数据
            if (this._debugCounter === undefined) this._debugCounter = 0;
            this._debugCounter++;
            if (this._debugCounter % 50 === 0) {
                console.log(`[AnimationInputInterface] 收到mocap数据 #${this._debugCounter}:`,
                    'inputSource=', this.inputSource,
                    'currentTaskId=', this.currentTaskId,
                    'channels=', data.channels ? Object.keys(data.channels) : 'none');
            }

            if (this.inputSource !== 'mocap') return;
            if (!this.currentTaskId) return;

            const baseChannelName = this.channelMapping[this.currentTaskId];
            if (!baseChannelName) return;

            // 解析左手和右手通道数据
            const channelName_L = `${baseChannelName}_L`;
            const channelName_R = `${baseChannelName}_R`;

            let hasData = false;

            if (data.channels) {
                // 左手数据
                if (data.channels[channelName_L] !== undefined) {
                    const channelData_L = data.channels[channelName_L];
                    this.rawValue_L = typeof channelData_L === 'object' ? channelData_L.value : channelData_L;
                    hasData = true;

                    if (this.calibration.isCalibrating) {
                        this.calibration.rawValues_L.push(this.rawValue_L);
                        this.calibration.rawValues.push(this.rawValue_L);  // 兼容
                    }
                }

                // 右手数据
                if (data.channels[channelName_R] !== undefined) {
                    const channelData_R = data.channels[channelName_R];
                    this.rawValue_R = typeof channelData_R === 'object' ? channelData_R.value : channelData_R;
                    hasData = true;

                    if (this.calibration.isCalibrating) {
                        this.calibration.rawValues_R.push(this.rawValue_R);
                    }
                }

                // 兼容旧接口（使用左手值）
                this.rawValue = this.rawValue_L;
            }

            if (hasData) {
                // 调试：打印解析的值
                if (this._debugCounter % 50 === 0) {
                    console.log(`[AnimationInputInterface] 通道 ${baseChannelName}: L=${this.rawValue_L?.toFixed(2)}, R=${this.rawValue_R?.toFixed(2)}`);
                }

                this._updateNormalizedValue();

                if (this.onDataUpdate) {
                    this.onDataUpdate({
                        rawValue_L: this.rawValue_L,
                        rawValue_R: this.rawValue_R,
                        normalizedValue_L: this.normalizedValue_L,
                        normalizedValue_R: this.normalizedValue_R,
                        isCalibrated_L: this.calibration.isCalibrated_L,
                        isCalibrated_R: this.calibration.isCalibrated_R,
                        // 兼容旧接口
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
         * 双手同时标定
         */
        startCalibration(taskId) {
            console.log(`[AnimationInputInterface] 开始标定: ${taskId} (双手)`);

            this.calibration.isCalibrating = true;
            this.calibration.phase = 'calibrating';
            this.calibration.currentTask = taskId;
            this.calibration.startTime = Date.now();

            // 重置左手标定数据
            this.calibration.rawValues_L = [];
            this.calibration.min_L = null;
            this.calibration.max_L = null;
            this.calibration.isCalibrated_L = false;

            // 重置右手标定数据
            this.calibration.rawValues_R = [];
            this.calibration.min_R = null;
            this.calibration.max_R = null;
            this.calibration.isCalibrated_R = false;

            // 兼容旧接口
            this.calibration.rawValues = [];
            this.calibration.min = null;
            this.calibration.max = null;
            this.calibration.isCalibrated = false;

            this.setCurrentTask(taskId);
        }

        /**
         * 结束标定（从采集的数据中自动提取 min 和 max）
         * 双手独立计算标定范围
         */
        endCalibration() {
            if (!this.calibration.isCalibrating) {
                console.warn('[AnimationInputInterface] 未在标定状态');
                return null;
            }

            this.calibration.endTime = Date.now();
            this.calibration.isCalibrating = false;

            const values_L = this.calibration.rawValues_L;
            const values_R = this.calibration.rawValues_R;

            // 处理左手标定数据
            if (values_L.length > 0) {
                const result_L = this._calculateCalibrationRange(values_L);
                this.calibration.min_L = result_L.min;
                this.calibration.max_L = result_L.max;
                this.calibration.isCalibrated_L = true;
                console.log(`[AnimationInputInterface] 左手标定完成: min=${this.calibration.min_L.toFixed(2)}, max=${this.calibration.max_L.toFixed(2)} (采样${values_L.length}个)`);
            } else {
                console.warn('[AnimationInputInterface] 左手标定期间未收到数据');
            }

            // 处理右手标定数据
            if (values_R.length > 0) {
                const result_R = this._calculateCalibrationRange(values_R);
                this.calibration.min_R = result_R.min;
                this.calibration.max_R = result_R.max;
                this.calibration.isCalibrated_R = true;
                console.log(`[AnimationInputInterface] 右手标定完成: min=${this.calibration.min_R.toFixed(2)}, max=${this.calibration.max_R.toFixed(2)} (采样${values_R.length}个)`);
            } else {
                console.warn('[AnimationInputInterface] 右手标定期间未收到数据');
            }

            // 兼容旧接口（使用左手数据）
            this.calibration.min = this.calibration.min_L;
            this.calibration.max = this.calibration.max_L;
            this.calibration.isCalibrated = this.calibration.isCalibrated_L && this.calibration.isCalibrated_R;

            // 缓存标定数据
            if (this.calibration.currentTask) {
                this.calibrationCache[this.calibration.currentTask] = {
                    min_L: this.calibration.min_L,
                    max_L: this.calibration.max_L,
                    isCalibrated_L: this.calibration.isCalibrated_L,
                    min_R: this.calibration.min_R,
                    max_R: this.calibration.max_R,
                    isCalibrated_R: this.calibration.isCalibrated_R,
                    // 兼容
                    min: this.calibration.min,
                    max: this.calibration.max,
                    isCalibrated: this.calibration.isCalibrated
                };
                this._saveCalibrationCache();
            }

            if (this.onCalibrationComplete) {
                this.onCalibrationComplete({
                    taskId: this.calibration.currentTask,
                    min_L: this.calibration.min_L,
                    max_L: this.calibration.max_L,
                    min_R: this.calibration.min_R,
                    max_R: this.calibration.max_R,
                    // 兼容
                    min: this.calibration.min,
                    max: this.calibration.max
                });
            }

            const result = {
                sampleCount_L: values_L.length,
                sampleCount_R: values_R.length,
                duration: this.calibration.endTime - this.calibration.startTime,
                calibratedMin_L: this.calibration.min_L,
                calibratedMax_L: this.calibration.max_L,
                calibratedMin_R: this.calibration.min_R,
                calibratedMax_R: this.calibration.max_R,
                isCalibrated_L: this.calibration.isCalibrated_L,
                isCalibrated_R: this.calibration.isCalibrated_R,
                // 兼容
                sampleCount: values_L.length,
                calibratedMin: this.calibration.min,
                calibratedMax: this.calibration.max,
                isCalibrated: this.calibration.isCalibrated
            };

            this.calibration.phase = null;
            return result;
        }

        /**
         * 计算标定范围（去除噪声后的 min/max）
         */
        _calculateCalibrationRange(values) {
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
            let min = Math.min(calibratedMin, calibratedMax);
            let max = Math.max(calibratedMin, calibratedMax);

            // 检查范围是否有效
            if (max - min < 0.01) {
                console.warn('[AnimationInputInterface] 标定范围过小，使用默认扩展');
                max = min + 10;
            }

            return { min, max };
        }
        
        resetCalibration(taskId = null) {
            const targetTask = taskId || this.currentTaskId;

            if (targetTask) {
                this.calibrationCache[targetTask] = {
                    min_L: null, max_L: null, isCalibrated_L: false,
                    min_R: null, max_R: null, isCalibrated_R: false,
                    min: null, max: null, isCalibrated: false
                };

                if (targetTask === this.currentTaskId) {
                    this.calibration.min_L = null;
                    this.calibration.max_L = null;
                    this.calibration.isCalibrated_L = false;
                    this.calibration.min_R = null;
                    this.calibration.max_R = null;
                    this.calibration.isCalibrated_R = false;
                    this.calibration.min = null;
                    this.calibration.max = null;
                    this.calibration.isCalibrated = false;
                }

                this._saveCalibrationCache();
                console.log(`[AnimationInputInterface] 已重置任务 ${targetTask} 的标定数据（双手）`);
            }
        }
        
        isCalibrated() {
            return this.calibration.isCalibrated;
        }

        getCalibrationStatus() {
            return {
                isCalibrating: this.calibration.isCalibrating,
                phase: this.calibration.phase,
                currentTask: this.calibration.currentTask,
                // 左手
                isCalibrated_L: this.calibration.isCalibrated_L,
                min_L: this.calibration.min_L,
                max_L: this.calibration.max_L,
                // 右手
                isCalibrated_R: this.calibration.isCalibrated_R,
                min_R: this.calibration.min_R,
                max_R: this.calibration.max_R,
                // 兼容
                isCalibrated: this.calibration.isCalibrated,
                min: this.calibration.min,
                max: this.calibration.max
            };
        }

        // ==================== 归一化计算（双手） ====================

        _updateNormalizedValue() {
            // 左手归一化
            let normalized_L = this._calculateNormalized(
                this.rawValue_L,
                this.calibration.min_L,
                this.calibration.max_L,
                this.calibration.isCalibrated_L
            );

            // 右手归一化
            let normalized_R = this._calculateNormalized(
                this.rawValue_R,
                this.calibration.min_R,
                this.calibration.max_R,
                this.calibration.isCalibrated_R
            );

            // 应用平滑
            if (this.smoothingEnabled) {
                this._smoothedValue_L = this._smoothedValue_L * (1 - this.smoothingFactor) +
                                        normalized_L * this.smoothingFactor;
                this._smoothedValue_R = this._smoothedValue_R * (1 - this.smoothingFactor) +
                                        normalized_R * this.smoothingFactor;
                this.normalizedValue_L = this._smoothedValue_L;
                this.normalizedValue_R = this._smoothedValue_R;
            } else {
                this.normalizedValue_L = normalized_L;
                this.normalizedValue_R = normalized_R;
            }

            // 兼容旧接口（使用左手值）
            this.normalizedValue = this.normalizedValue_L;
            this._smoothedValue = this._smoothedValue_L;
        }

        /**
         * 计算单个值的归一化
         */
        _calculateNormalized(rawValue, min, max, isCalibrated) {
            let normalized;

            if (isCalibrated && min !== null && max !== null) {
                const range = max - min;
                if (range > 0) {
                    normalized = (rawValue - min) / range;
                } else {
                    normalized = 0;
                }
            } else {
                // 未标定时使用默认范围
                normalized = rawValue / 100;
            }

            return Math.max(0, Math.min(1, normalized));
        }
        
        getNormalizedInput() {
            return this.normalizedValue;
        }

        /**
         * 获取双手归一化输入
         */
        getNormalizedInputDual() {
            return {
                left: this.normalizedValue_L,
                right: this.normalizedValue_R
            };
        }

        getRawInput() {
            return this.rawValue;
        }

        /**
         * 获取双手原始输入
         */
        getRawInputDual() {
            return {
                left: this.rawValue_L,
                right: this.rawValue_R
            };
        }

        getMappedInput(min, max) {
            return min + this.normalizedValue * (max - min);
        }

        /**
         * 获取双手映射输入
         */
        getMappedInputDual(min, max) {
            return {
                left: min + this.normalizedValue_L * (max - min),
                right: min + this.normalizedValue_R * (max - min)
            };
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
                'continual_gesture_1': {
                    min_L: null, max_L: null, isCalibrated_L: false,
                    min_R: null, max_R: null, isCalibrated_R: false,
                    min: null, max: null, isCalibrated: false
                },
                'continual_gesture_2': {
                    min_L: null, max_L: null, isCalibrated_L: false,
                    min_R: null, max_R: null, isCalibrated_R: false,
                    min: null, max: null, isCalibrated: false
                }
            };
            this.calibration.min_L = null;
            this.calibration.max_L = null;
            this.calibration.isCalibrated_L = false;
            this.calibration.min_R = null;
            this.calibration.max_R = null;
            this.calibration.isCalibrated_R = false;
            this.calibration.min = null;
            this.calibration.max = null;
            this.calibration.isCalibrated = false;
            localStorage.removeItem('emg_animation_calibration');
            console.log('[AnimationInputInterface] 已清除所有标定缓存（双手）');
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

    // 【新增】页面刷新/关闭前主动断开WebSocket连接
    window.addEventListener('beforeunload', () => {
        if (animationInputInterface) {
            animationInputInterface.destroy();
        }
    });

    console.log('[AnimationInputInterface] 模块加载完成');

})();
