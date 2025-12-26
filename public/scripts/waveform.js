/**
 * waveform.js - 主入口文件
 * 
 * 整合渲染器和数据生成器，提供完整的波形显示功能。
 * 支持两种数据源：
 *   1. WebSocket实时数据（来自realtimeEngine.js，端口8080）
 *   2. 模拟数据生成器（用于测试）
 */

(function() {
    'use strict';

    // ==================== WebSocket 数据接收器 ====================
    class RealtimeDataReceiver {
        constructor(controller) {
            this.controller = controller;
            this.ws = null;
            this.wsUrl = 'ws://localhost:8080';  // realtimeEngine.js 的WebSocket端口
            this.reconnectInterval = 3000;
            this.reconnectTimer = null;
            this.isConnected = false;
            this.maxReconnectAttempts = 10;
            this.reconnectAttempts = 0;
        }

        /**
         * 连接到 realtimeEngine.js 的 WebSocket 服务
         */
        connect() {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                console.log('[Waveform] WebSocket已连接');
                return;
            }

            try {
                console.log(`[Waveform] 正在连接 ${this.wsUrl}...`);
                this.ws = new WebSocket(this.wsUrl);

                this.ws.onopen = () => {
                    console.log('[Waveform] ✓ WebSocket连接成功');
                    this.isConnected = true;
                    this.reconnectAttempts = 0;
                    clearTimeout(this.reconnectTimer);
                    
                    // 更新UI状态
                    this.updateConnectionStatus(true);
                };

                this.ws.onmessage = (event) => {
                    this.handleMessage(event.data);
                };

                this.ws.onerror = (error) => {
                    console.error('[Waveform] WebSocket错误:', error);
                };

                this.ws.onclose = (event) => {
                    console.log(`[Waveform] WebSocket连接关闭 (code: ${event.code})`);
                    this.isConnected = false;
                    this.updateConnectionStatus(false);
                    
                    // 自动重连
                    if (this.reconnectAttempts < this.maxReconnectAttempts) {
                        this.scheduleReconnect();
                    }
                };

            } catch (error) {
                console.error('[Waveform] 创建WebSocket失败:', error);
                this.scheduleReconnect();
            }
        }

        /**
         * 断开WebSocket连接
         */
        disconnect() {
            clearTimeout(this.reconnectTimer);
            this.reconnectAttempts = this.maxReconnectAttempts; // 阻止重连
            
            if (this.ws) {
                this.ws.close(1000, '用户主动断开');
                this.ws = null;
            }
            this.isConnected = false;
            this.updateConnectionStatus(false);
        }

        /**
         * 安排重连
         */
        scheduleReconnect() {
            this.reconnectAttempts++;
            console.log(`[Waveform] ${this.reconnectInterval/1000}秒后尝试第${this.reconnectAttempts}次重连...`);
            
            this.reconnectTimer = setTimeout(() => {
                this.connect();
            }, this.reconnectInterval);
        }

        /**
         * 处理收到的消息
         */
        handleMessage(rawData) {
            try {
                const packet = JSON.parse(rawData);
                
                // 处理连接确认消息
                if (packet.type === 'connection_established') {
                    console.log('[Waveform] 收到连接确认:', packet.message);
                    return;
                }

                // 处理实时数据包 (来自realtimeEngine.js的新格式)
                if (packet.type === 'realtime_data') {
                    this.renderRealtimeData(packet.data);
                    return;
                }

                // 兼容旧格式 emg_data
                if (packet.type === 'emg_data') {
                    // 旧格式处理（如果需要）
                    console.log('[Waveform] 收到旧格式数据包');
                    return;
                }

            } catch (error) {
                console.error('[Waveform] 消息解析失败:', error);
            }
        }

        /**
         * 渲染实时数据
         * @param {Object} data - 数据包
         *   data.emg: [[ch0_p1, ch0_p2, ...], [ch1_p1, ...], ...] 16通道×9帧
         *   data.imu: { acc: [ax,ay,az], gyr: [gx,gy,gz], mag: [mx,my,mz] }
         */
        renderRealtimeData(data) {
            if (!this.controller || !this.controller.rendererManager) return;

            const rm = this.controller.rendererManager;

            // ===== EMG数据渲染 =====
            if (data.emg && data.emg.length > 0) {
                // data.emg 格式: [通道][帧] - 16通道×9帧
                // 渲染器需要的格式也是 [通道][帧]
                
                // EMG1 和 EMG2 显示相同数据
                const emg1 = rm.get('emg1');
                const emg2 = rm.get('emg2');
                
                if (emg1) emg1.renderPoints(data.emg);
                if (emg2) emg2.renderPoints(data.emg);
            }

            // ===== IMU数据渲染 =====
            if (data.imu) {
                // 加速度计: acc = [ax, ay, az]
                // 渲染器需要格式: [[ax], [ay], [az]] 或 [[ax,...], [ay,...], [az,...]]
                if (data.imu.acc) {
                    const accData = data.imu.acc.map(v => [v]);  // 转为 [[ax], [ay], [az]]
                    const imu1Acc = rm.get('imu1Acc');
                    const imu2Acc = rm.get('imu2Acc');
                    if (imu1Acc) imu1Acc.renderPoints(accData);
                    if (imu2Acc) imu2Acc.renderPoints(accData);
                }

                // 陀螺仪: gyr = [gx, gy, gz]
                if (data.imu.gyr) {
                    const gyrData = data.imu.gyr.map(v => [v]);
                    const imu1Gyr = rm.get('imu1Gyr');
                    const imu2Gyr = rm.get('imu2Gyr');
                    if (imu1Gyr) imu1Gyr.renderPoints(gyrData);
                    if (imu2Gyr) imu2Gyr.renderPoints(gyrData);
                }

                // 磁力计: mag = [mx, my, mz]
                if (data.imu.mag) {
                    const magData = data.imu.mag.map(v => [v]);
                    const imu1Mag = rm.get('imu1Mag');
                    const imu2Mag = rm.get('imu2Mag');
                    if (imu1Mag) imu1Mag.renderPoints(magData);
                    if (imu2Mag) imu2Mag.renderPoints(magData);
                }
            }

            // ===== 更新统计信息 =====
            if (data.stats) {
                this.controller.updateStats(data.stats, data.packetCount);
            }
            
            // 更新帧计数
            this.controller.frameCount += (data.framesInPacket || 9);
            this.controller.updateFrameCount();
        }

        /**
         * 更新连接状态显示
         */
        updateConnectionStatus(connected) {
            // 可以在这里更新UI上的连接状态指示器
            const statusElement = document.getElementById('ws-status');
            if (statusElement) {
                statusElement.textContent = connected ? '已连接' : '未连接';
                statusElement.className = connected ? 'status-connected' : 'status-disconnected';
            }
        }
    }

    // ==================== 主控制器 ====================
    class WaveformController {
        constructor() {
            this.rendererManager = new RendererManager();
            this.dataGenerator = new DataGenerator();
            this.dataReceiver = new RealtimeDataReceiver(this);
            
            this.isRunning = false;
            this.useRealData = false;  // 是否使用真实数据
            this.frameCount = 0;
            this.intervalId = null;
            
            this.init();
        }

        init() {
            // 创建EMG渲染器
            this.rendererManager.createEMGRenderer(
                'emg1', 'emg1-canvas', 'emg1-container', 'emg1-pointer', 
                'emg1-offset', 'emg1-channel'
            );
            this.rendererManager.createEMGRenderer(
                'emg2', 'emg2-canvas', 'emg2-container', 'emg2-pointer', 
                'emg2-offset', 'emg2-channel'
            );

            // 创建IMU渲染器 - IMU1
            this.rendererManager.createIMURenderer(
                'imu1Acc', 'imu1-acc-canvas', 'imu1-acc-container', 'imu1-acc-pointer', 'imu1-acc-offset'
            );
            this.rendererManager.createIMURenderer(
                'imu1Gyr', 'imu1-gyr-canvas', 'imu1-gyr-container', 'imu1-gyr-pointer', 'imu1-gyr-offset'
            );
            this.rendererManager.createIMURenderer(
                'imu1Mag', 'imu1-mag-canvas', 'imu1-mag-container', 'imu1-mag-pointer', 'imu1-mag-offset'
            );

            // 创建IMU渲染器 - IMU2
            this.rendererManager.createIMURenderer(
                'imu2Acc', 'imu2-acc-canvas', 'imu2-acc-container', 'imu2-acc-pointer', 'imu2-acc-offset'
            );
            this.rendererManager.createIMURenderer(
                'imu2Gyr', 'imu2-gyr-canvas', 'imu2-gyr-container', 'imu2-gyr-pointer', 'imu2-gyr-offset'
            );
            this.rendererManager.createIMURenderer(
                'imu2Mag', 'imu2-mag-canvas', 'imu2-mag-container', 'imu2-mag-pointer', 'imu2-mag-offset'
            );

            // 更新时间显示
            this.updateTimeDisplay();
            setInterval(() => this.updateTimeDisplay(), 1000);

            // 不自动开始，等待用户操作
            // this.start();
        }

        updateTimeDisplay() {
            const timeElement = document.getElementById('currentTime');
            if (timeElement) {
                const now = new Date();
                const timeStr = now.getFullYear() + '-' + 
                    String(now.getMonth() + 1).padStart(2, '0') + '-' +
                    String(now.getDate()).padStart(2, '0') + ' ' +
                    String(now.getHours()).padStart(2, '0') + ':' +
                    String(now.getMinutes()).padStart(2, '0') + ':' +
                    String(now.getSeconds()).padStart(2, '0');
                timeElement.textContent = timeStr;
            }
        }

        /**
         * 启动显示（使用模拟数据）
         */
        start() {
            if (this.isRunning) return;
            this.isRunning = true;
            this.useRealData = false;
            
            // 100Hz更新频率
            const interval = 10;
            
            this.intervalId = setInterval(() => {
                this.update();
            }, interval);
            
            console.log('[Waveform] 启动模拟数据显示');
        }

        /**
         * 启动显示（使用真实BLE数据）
         */
        startRealtime() {
            if (this.isRunning && this.useRealData) return;
            
            // 停止模拟数据
            this.stop();
            
            this.isRunning = true;
            this.useRealData = true;
            
            // 连接WebSocket接收真实数据
            this.dataReceiver.connect();
            
            console.log('[Waveform] 启动实时数据显示');
        }

        /**
         * 停止显示
         */
        stop() {
            this.isRunning = false;
            
            if (this.intervalId) {
                clearInterval(this.intervalId);
                this.intervalId = null;
            }
            
            if (this.useRealData) {
                this.dataReceiver.disconnect();
            }
            
            this.useRealData = false;
            console.log('[Waveform] 停止显示');
        }

        /**
         * 更新显示（模拟数据模式）
         */
        update() {
            if (this.useRealData) return; // 真实数据模式下不使用此函数
            
            // 生成模拟数据
            const emgData = this.dataGenerator.generateEMGPacket();
            const imuAccData = this.dataGenerator.generateIMUAccPacket();
            const imuGyrData = this.dataGenerator.generateIMUGyrPacket();
            const imuMagData = this.dataGenerator.generateIMUMagPacket();

            // EMG1和EMG2显示相同数据
            this.rendererManager.get('emg1').renderPoints(emgData);
            this.rendererManager.get('emg2').renderPoints(emgData);

            // IMU1和IMU2显示相同数据
            this.rendererManager.get('imu1Acc').renderPoints(imuAccData);
            this.rendererManager.get('imu1Gyr').renderPoints(imuGyrData);
            this.rendererManager.get('imu1Mag').renderPoints(imuMagData);

            this.rendererManager.get('imu2Acc').renderPoints(imuAccData);
            this.rendererManager.get('imu2Gyr').renderPoints(imuGyrData);
            this.rendererManager.get('imu2Mag').renderPoints(imuMagData);

            // 更新帧计数
            this.frameCount++;
            this.updateFrameCount();
        }

        /**
         * 更新帧计数显示
         */
        updateFrameCount() {
            const emg1Frames = document.getElementById('emg1-frames');
            const emg2Frames = document.getElementById('emg2-frames');
            if (emg1Frames) emg1Frames.textContent = this.frameCount;
            if (emg2Frames) emg2Frames.textContent = this.frameCount;
        }

        /**
         * 更新统计信息显示
         */
        updateStats(stats, packetCount) {
            // 更新丢包率等统计信息（如果UI中有对应元素）
            const lostElement = document.getElementById('lost-frames');
            const totalElement = document.getElementById('total-frames');
            
            if (lostElement && stats.lost !== undefined) {
                lostElement.textContent = stats.lost;
            }
            if (totalElement && stats.total !== undefined) {
                totalElement.textContent = stats.total;
            }
        }

        /**
         * 清除所有显示
         */
        clearAll() {
            this.rendererManager.clearAll();
            this.dataGenerator.reset();
            this.frameCount = 0;
            this.updateFrameCount();
        }

        /**
         * 获取渲染器管理器
         */
        getRendererManager() {
            return this.rendererManager;
        }

        /**
         * 获取数据生成器
         */
        getDataGenerator() {
            return this.dataGenerator;
        }

        /**
         * 获取数据接收器
         */
        getDataReceiver() {
            return this.dataReceiver;
        }

        /**
         * 检查是否正在使用真实数据
         */
        isUsingRealData() {
            return this.useRealData && this.dataReceiver.isConnected;
        }
    }

    // ==================== 初始化 ====================
    let controller = null;

    document.addEventListener('DOMContentLoaded', () => {
        controller = new WaveformController();
        
        // 暴露全局接口
        window.waveformController = controller;
        
        // 便捷方法
        window.startSimulation = () => controller.start();
        window.startRealtime = () => controller.startRealtime();
        window.stopWaveform = () => controller.stop();
        window.clearWaveform = () => controller.clearAll();
        
        console.log('[Waveform] 系统初始化完成');
        console.log('[Waveform] 可用命令:');
        console.log('  - startSimulation(): 启动模拟数据显示');
        console.log('  - startRealtime(): 启动实时BLE数据显示');
        console.log('  - stopWaveform(): 停止显示');
        console.log('  - clearWaveform(): 清除显示');
    });

})();
