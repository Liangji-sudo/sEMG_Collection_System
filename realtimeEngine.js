// realtimeEngine.js - v4.1 (修复BLE数据处理 + 支持多级目录结构)
const WebSocket = require('ws');
const EventEmitter = require('events');
const zmq = require('zeromq');
const { promisify } = require('util');
const express = require('express');
const cors = require('cors');
const app = express();
app.use(cors());
app.use(express.json());

const { discrete_gesture_prompt_name, collection_task_name } = require('./constants.js');

// 获取系统时间戳（秒，高精度）
function getSysTimeNode() {
    const nsTimestamp = process.hrtime.bigint();
    const sTimestamp = Number(nsTimestamp) / 1000000000.0;
    return Math.round(sTimestamp * 1000000000) / 1000000000;
}

class RealtimeEngine extends EventEmitter {
    constructor() {
        super();
        // WebSocket服务器（给前端）
        this.websocket_server = null;
        this.clients = new Set();
        this.isRunning = false;
        this.dataBuffer = [];
        this.maxBufferSize = 1000;

        // ===== BLE服务器客户端配置 =====
        this.ble_client = null;
        this.ble_clientUrl = 'ws://localhost:8766';
        this.reconnectInterval = 3000;
        this.maxReconnectTimes = 3;
        this.currentReconnectTimes = 0;
        this.reconnectTimer = null;
        this.connectTimeoutTimer = null;

        // ===== 数据包计数 =====
        this.emg_packet_count = 0;
        this.emg_5_packets_count = 0;
        this.dev1_packet_count = 0;
        this.dev2_packet_count = 0;

        // ===== Storage Server配置 =====
        this.storage_server_socket = new zmq.Request();
        this.storage_server_host = '127.0.0.1';
        this.storage_server_port = 5555;
        this.storage_connected = false;
        
        // 【新增】ZMQ请求队列（解决并发问题）
        this.storageRequestQueue = [];
        this.isStorageRequestPending = false;

        // ===== 采集状态 =====
        this.currentTaskId = null;
        this.currentUser = null;
        this.isCollecting = false;
        this.collectionPaused = false;
        
        // ===== 采集配置信息（用于文件命名）=====
        this.collectionConfig = null;  // 包含 category1, category2, category4 等

        // ===== Stage状态 =====
        this.currentStageName = null;
        this.stageFileOpen = false;  // 当前Stage是否有打开的文件
        this.stage_start_time = 0;
        
        // 【新增】防止重复关闭的标志
        this.isClosingStageFile = false;

        // ===== Prompt状态 =====
        this.pending_prompt = null;  // { name, time, stageName }
    }

    // ==================== 启动 ====================
    start(port = 8080) {
        return new Promise((resolve, reject) => {
            try {
                // 1. 延迟连接BLE服务器
                this.connectTimeoutTimer = setTimeout(() => {
                    this.ble_server_connect();
                }, 3000);

                // 2. 启动WebSocket服务器（给前端）
                this.websocket_server = new WebSocket.Server({ port });

                this.websocket_server.on('connection', (ws) => {
                    console.log('[realtimeEngine] 前端client连接已建立');
                    this.clients.add(ws);

                    // 发送连接确认
                    ws.send(JSON.stringify({
                        type: 'connection_established',
                        message: '实时数据连接已建立',
                        timestamp: Date.now()
                    }));

                    // 监听前端消息
                    ws.on('message', (message) => {
                        this.handleFrontendMessage(message);
                    });

                    ws.on('close', () => {
                        console.log('[realtimeEngine] 前端WebSocket连接已关闭');
                        this.clients.delete(ws);
                    });

                    ws.on('error', (error) => {
                        console.error('[realtimeEngine] WebSocket错误:', error);
                        this.clients.delete(ws);
                    });
                });

                this.websocket_server.on('listening', () => {
                    console.log(`[realtimeEngine] WebSocket服务运行在端口 ${port}`);
                    this.isRunning = true;
                    resolve();
                });

                this.websocket_server.on('error', (error) => {
                    console.error('[realtimeEngine] WebSocket服务器启动失败:', error);
                    reject(error);
                });

                // 3. 连接Storage Server
                this.storage_server_connect();

            } catch (error) {
                console.error('[realtimeEngine] 启动失败:', error);
                reject(error);
            }
        });
    }

    // ==================== 前端消息处理 ====================
    handleFrontendMessage(rawMessage) {
        try {
            const message = JSON.parse(rawMessage.toString());

            if (message.type !== 'control_command') {
                return;
            }

            const { action, data } = message;
            console.log(`[realtimeEngine] <<< 收到前端命令: ${action}`, data);

            switch (action) {
                case 'task_change':
                    this.onTaskChange(data.taskId);
                    break;
                case 'collection_start':
                    this.onCollectionStart(data);
                    break;
                case 'collection_pause':
                    this.onCollectionPause();
                    break;
                case 'collection_resume':
                    this.onCollectionResume();
                    break;
                case 'collection_stop':
                    this.onCollectionStop(data.completed);
                    break;
                case 'stage_change':
                    this.onStageChange(data.stageIndex, data.stageName);
                    break;
                case 'stage_start':
                    this.onStageStart(data.stageName, data.stageIndex, data.timestamp);
                    break;
                case 'stage_end':
                    this.onStageEnd(data.stageName, data.timestamp);
                    break;
                case 'prompt_start':
                    this.onPromptStart(data.promptName, data.promptIndex);
                    break;
                case 'prompt_end':
                    this.onPromptEnd(data.promptName, data.promptIndex);
                    break;
                case 'prompt':
                    this.onPrompt(data.name, data.stageName, data.timestamp);
                    break;
                default:
                    console.log(`[realtimeEngine] 未知命令: ${action}`);
            }

        } catch (error) {
            console.error('[realtimeEngine] 解析前端消息失败:', error);
        }
    }

    // ==================== 采集控制回调 ====================

    onTaskChange(taskId) {
        console.log(`[realtimeEngine] ========== 任务切换: ${taskId} ==========`);
        this.currentTaskId = taskId;
    }

    async onCollectionStart(data) {
        console.log(`[realtimeEngine] ========== 开始采集会话 ==========`);
        
        const { taskId, stageName, userId, config } = data;
        
        console.log(`[realtimeEngine] 任务: ${taskId}`);
        console.log(`[realtimeEngine] 用户ID: ${userId}`);
        console.log(`[realtimeEngine] 配置:`, config);

        this.currentTaskId = taskId;
        this.currentUser = { id: userId, ...config?.subject };
        this.collectionConfig = config;
        this.isCollecting = true;
        this.collectionPaused = false;
        this.currentStageName = stageName;
        
        // 自动开始第一个Stage的文件
        if (stageName) {
            await this.createStageFile(stageName);
        }
    }

    onCollectionPause() {
        console.log(`[realtimeEngine] ========== 暂停采集 ==========`);
        this.collectionPaused = true;
    }

    onCollectionResume() {
        console.log(`[realtimeEngine] ========== 继续采集 ==========`);
        this.collectionPaused = false;
    }

    async onCollectionStop(completed = false) {
        console.log(`[realtimeEngine] ========== 停止采集 ==========`);
        console.log(`[realtimeEngine] 完成状态: ${completed ? '正常完成' : '手动停止'}`);

        if (this.stageFileOpen && !this.isClosingStageFile) {
            await this.closeStageFile();
        }

        this.isCollecting = false;
        this.collectionPaused = false;
    }

    onStageChange(stageIndex, stageName) {
        console.log(`[realtimeEngine] ========== Stage切换 ==========`);
        console.log(`[realtimeEngine] 新Stage: ${stageName} (索引: ${stageIndex})`);
        this.currentStageName = stageName;
    }

    async onStageStart(stageName, stageIndex, timestamp) {
        console.log(`[realtimeEngine] ========== Stage开始 ==========`);
        console.log(`[realtimeEngine] Stage: ${stageName} (索引: ${stageIndex})`);
        console.log(`[realtimeEngine] 时间戳: ${timestamp}`);

        if (this.stageFileOpen && !this.isClosingStageFile) {
            console.log(`[realtimeEngine] 关闭上一个Stage的文件...`);
            await this.closeStageFile();
        }

        this.currentStageName = stageName;
        this.stage_start_time = timestamp;

        await this.createStageFile(stageName);
    }

    async onStageEnd(stageName, timestamp) {
        console.log(`[realtimeEngine] ========== Stage结束 ==========`);
        console.log(`[realtimeEngine] Stage: ${stageName}`);
        console.log(`[realtimeEngine] 时间戳: ${timestamp}`);

        if (this.stageFileOpen && !this.isClosingStageFile) {
            await this.closeStageFile();
        }
    }

    onPromptStart(promptName, promptIndex) {
        console.log(`[realtimeEngine] ========== Prompt开始 ==========`);
        console.log(`[realtimeEngine] Prompt: ${promptName} (索引: ${promptIndex})`);
        
        this.pending_prompt = {
            name: promptName,
            time: getSysTimeNode(),
            stageName: this.currentStageName,
            index: promptIndex
        };
    }

    onPromptEnd(promptName, promptIndex) {
        console.log(`[realtimeEngine] ========== Prompt结束 ==========`);
        console.log(`[realtimeEngine] Prompt: ${promptName} (索引: ${promptIndex})`);
    }

    onPrompt(name, stageName, timestamp) {
        console.log(`[realtimeEngine] ========== Prompt信号 ==========`);
        console.log(`[realtimeEngine] Prompt: ${name}, Stage: ${stageName}, Time: ${timestamp}`);

        this.pending_prompt = {
            name: name,
            time: timestamp || getSysTimeNode(),
            stageName: stageName || this.currentStageName
        };
    }

    // ==================== Stage文件管理 ====================

    async createStageFile(stageName) {
        try {
            const config = this.collectionConfig || {};
            const userId = this.currentUser?.id || 'unknown';
            
            const createParams = {
                task_id: this.currentTaskId || 'discrete_gesture',
                user_id: userId,
                stage_name: stageName,
                category1: config.category1 || 'default',
                category2: config.category2 || 'default', 
                category4: config.category4 || 'default',
                subject_info: this.currentUser || {},
                template_name: config.templateName || 'default'
            };

            console.log(`[realtimeEngine] 创建Stage文件:`, createParams);

            const response = await this.sendStorageCommand('create', createParams);
            
            if (response.status === 'success') {
                this.stageFileOpen = true;
                console.log(`[realtimeEngine] ✅ Stage文件创建成功: ${response.file_path}`);
            } else {
                console.error(`[realtimeEngine] ❌ Stage文件创建失败: ${response.msg}`);
            }
            
            return response;
        } catch (error) {
            console.error('[realtimeEngine] 创建Stage文件失败:', error);
            return { status: 'error', msg: error.message };
        }
    }

    async closeStageFile() {
        if (this.isClosingStageFile) {
            console.log('[realtimeEngine] Stage文件正在关闭中，跳过');
            return { status: 'skipped', msg: '正在关闭中' };
        }
        
        this.isClosingStageFile = true;
        
        try {
            console.log(`[realtimeEngine] 关闭Stage文件...`);
            
            const response = await this.sendStorageCommand('close', {});
            
            if (response.status === 'success') {
                this.stageFileOpen = false;
                console.log(`[realtimeEngine] ✅ Stage文件已关闭: ${response.file_path}`);
                console.log(`[realtimeEngine] 📊 统计:`, response.stats);
            } else {
                console.error(`[realtimeEngine] ❌ 关闭文件失败: ${response.msg}`);
            }
            
            return response;
        } catch (error) {
            console.error('[realtimeEngine] 关闭Stage文件失败:', error);
            return { status: 'error', msg: error.message };
        } finally {
            this.isClosingStageFile = false;
        }
    }

    // ==================== WebSocket广播 ====================
    broadcastToClients(dataPacket) {
        const message = JSON.stringify(dataPacket);

        this.clients.forEach((client) => {
            if (client.readyState === WebSocket.OPEN) {
                try {
                    client.send(message);
                } catch (error) {
                    console.error('[realtimeEngine] 发送数据到客户端失败:', error);
                    this.clients.delete(client);
                }
            }
        });
    }

    // ==================== BLE Server连接 ====================
    ble_server_connect() {
        if (this.ble_client) {
            this.ble_client.close();
            this.ble_client = null;
        }

        try {
            this.ble_client = new WebSocket(this.ble_clientUrl);
            console.log("[realtimeEngine] 创建BLE客户端连接...");

            this.ble_client.onopen = () => {
                console.log(`[realtimeEngine] ✅ BLE服务器连接成功`);
                this.currentReconnectTimes = 0;
                clearTimeout(this.reconnectTimer);
                clearTimeout(this.connectTimeoutTimer);
                
                // 通知前端
                this.broadcastToClients({
                    type: 'ble_connection_status',
                    connected: true,
                    message: 'BLE服务器已连接'
                });
            };

            // 【关键修复】正确处理BLE消息
            this.ble_client.onmessage = (event) => {
                try {
                    const packet = JSON.parse(event.data);

                    // 处理 type: 'data' 的数据包
                    if (packet.type === 'data') {
                        this.handleBleDataPacket(packet);
                        return;
                    }

                    // 兼容旧格式
                    if (packet.type === 'emg_packet') {
                        this.attributeEMGData(packet);
                        return;
                    }
                } catch (error) {
                    console.error('[realtimeEngine] 解析BLE数据失败:', error);
                }
            };

            this.ble_client.onerror = (error) => {
                console.error('[realtimeEngine] BLE连接错误:', error.message || error);
                this.handleReconnect();
            };

            this.ble_client.onclose = (event) => {
                const code = event.code || 0;
                console.log(`[realtimeEngine] BLE连接关闭: code=${code}`);
                
                this.broadcastToClients({
                    type: 'ble_connection_status',
                    connected: false,
                    message: 'BLE服务器连接已断开'
                });
                
                if (code !== 1000) {
                    this.handleReconnect();
                }
            };

        } catch (error) {
            console.error('[realtimeEngine] 创建BLE连接失败:', error);
            this.handleReconnect('创建连接失败');
        }
    }

    handleReconnect(reason = '连接断开') {
        if (this.currentReconnectTimes >= this.maxReconnectTimes) {
            console.error(`[realtimeEngine] 达到最大重连次数，停止重连`);
            return;
        }

        this.currentReconnectTimes++;
        console.log(`[realtimeEngine] ${reason}，${this.reconnectInterval/1000}秒后重连(${this.currentReconnectTimes}/${this.maxReconnectTimes})...`);

        this.reconnectTimer = setTimeout(() => {
            this.ble_server_connect();
        }, this.reconnectInterval);
    }

    // ==================== 处理BLE数据包（关键修复：使用正确的字段名） ====================
    handleBleDataPacket(packet) {
        if (!this.isRunning) return;

        try {
            if (!packet.dev1 && !packet.dev2) {
                return;
            }

            // 准备数据容器
            let emg1Data = null, emg2Data = null;
            let emg1Timestamps = null, emg2Timestamps = null;
            let imu1Data = null, imu2Data = null;
            let imu1Timestamps = null, imu2Timestamps = null;
            let timestamp = packet.ts;
            let stats1 = null, stats2 = null;
            let framesInPacket = 9;

            // ========== 处理设备1数据 ==========
            if (packet.dev1) {
                const dev1 = Array.isArray(packet.dev1) ? packet.dev1[0] : packet.dev1;

                if (dev1) {
                    // EMG数据（字段名: uv）
                    if (dev1.uv && dev1.uv.length > 0) {
                        emg1Data = this.transposeEMG(dev1.uv);
                    }
                    // EMG时间戳（字段名: emg_t）
                    if (dev1.emg_t && dev1.emg_t.length > 0) {
                        emg1Timestamps = dev1.emg_t;
                    }
                    // IMU数据
                    if (dev1.imu && dev1.imu[0]) {
                        imu1Data = {
                            acc: dev1.imu[0][0],
                            gyr: dev1.imu[0][1],
                            mag: dev1.imu[0][2]
                        };
                    }
                    // IMU时间戳
                    if (dev1.imu_t && dev1.imu_t.length > 0) {
                        imu1Timestamps = dev1.imu_t;
                    }
                    // 统计信息
                    stats1 = dev1.s ? { total: dev1.s[0], lost: dev1.s[1] } : null;
                    framesInPacket = dev1.n || 9;
                    this.dev1_packet_count += framesInPacket;
                }
            }

            // ========== 处理设备2数据 ==========
            if (packet.dev2) {
                const dev2 = Array.isArray(packet.dev2) ? packet.dev2[0] : packet.dev2;

                if (dev2) {
                    if (dev2.uv && dev2.uv.length > 0) {
                        emg2Data = this.transposeEMG(dev2.uv);
                    }
                    if (dev2.emg_t && dev2.emg_t.length > 0) {
                        emg2Timestamps = dev2.emg_t;
                    }
                    if (dev2.imu && dev2.imu[0]) {
                        imu2Data = {
                            acc: dev2.imu[0][0],
                            gyr: dev2.imu[0][1],
                            mag: dev2.imu[0][2]
                        };
                    }
                    if (dev2.imu_t && dev2.imu_t.length > 0) {
                        imu2Timestamps = dev2.imu_t;
                    }
                    stats2 = dev2.s ? { total: dev2.s[0], lost: dev2.s[1] } : null;
                    this.dev2_packet_count += (dev2.n || 9);
                }
            }

            this.emg_packet_count += framesInPacket;
            this.emg_5_packets_count++;

            // ========== 构造广播数据包（给前端显示） ==========
            const displayPacket = {
                type: 'realtime_data',
                data: {
                    emg1: emg1Data,
                    emg2: emg2Data,
                    emg1_t: emg1Timestamps,
                    emg2_t: emg2Timestamps,
                    imu1: imu1Data,
                    imu2: imu2Data,
                    imu1_t: imu1Timestamps,
                    imu2_t: imu2Timestamps,
                    timestamp: timestamp,
                    packetCount: this.emg_packet_count,
                    framesInPacket: framesInPacket,
                    stats1: stats1,
                    stats2: stats2,
                    activeDevices: packet.active || []
                }
            };

            // 广播给前端
            this.broadcastToClients(displayPacket);

            // ========== 存储数据（如果正在采集且文件已打开） ==========
            if (this.isCollecting && !this.collectionPaused && this.stageFileOpen && !this.isClosingStageFile) {
                this.saveDataToStorage({
                    emg1: emg1Data,
                    emg2: emg2Data,
                    emg1_t: emg1Timestamps,
                    emg2_t: emg2Timestamps,
                    imu1: imu1Data,
                    imu2: imu2Data,
                    imu1_t: imu1Timestamps,
                    imu2_t: imu2Timestamps
                });
            }

        } catch (error) {
            console.error('[realtimeEngine] 处理BLE数据包错误:', error);
        }
    }

    transposeEMG(uvData) {
        if (!uvData || uvData.length === 0) return null;

        const numFrames = uvData.length;
        const numChannels = uvData[0].length;

        const transposed = [];
        for (let ch = 0; ch < numChannels; ch++) {
            const channelData = [];
            for (let frame = 0; frame < numFrames; frame++) {
                channelData.push(uvData[frame][ch]);
            }
            transposed.push(channelData);
        }
        return transposed;
    }

    // ==================== 存储相关 ====================

    async storage_server_connect() {
        try {
            const address = `tcp://${this.storage_server_host}:${this.storage_server_port}`;
            await this.storage_server_socket.connect(address);
            this.storage_connected = true;
            console.log(`[realtimeEngine] 已连接到storage_server: ${address}`);
        } catch (err) {
            console.error(`[realtimeEngine] 连接storage_server失败: ${err.message}`);
            this.storage_connected = false;
        }
    }

    /**
     * 发送存储命令（使用队列防止并发）
     */
    async sendStorageCommand(cmd, params = {}) {
        return new Promise((resolve, reject) => {
            this.storageRequestQueue.push({ cmd, params, resolve, reject });
            
            if (!this.isStorageRequestPending) {
                this._processStorageQueue();
            }
        });
    }
    
    async _processStorageQueue() {
        if (this.isStorageRequestPending || this.storageRequestQueue.length === 0) {
            return;
        }
        
        this.isStorageRequestPending = true;
        
        while (this.storageRequestQueue.length > 0) {
            const { cmd, params, resolve, reject } = this.storageRequestQueue.shift();
            
            try {
                const request = JSON.stringify({ cmd, params });
                await this.storage_server_socket.send(request);

                const [responseBuffer] = await this.storage_server_socket.receive();
                const response = JSON.parse(responseBuffer.toString('utf8'));
                resolve(response);
            } catch (err) {
                reject(new Error(`Storage命令失败(${cmd}): ${err.message}`));
            }
        }
        
        this.isStorageRequestPending = false;
    }

    async saveDataToStorage(sensorData) {
        if (this.isClosingStageFile || !this.stageFileOpen) {
            return;
        }
        
        try {
            const storageData = {
                emg1: sensorData.emg1,
                emg2: sensorData.emg2,
                emg1_t: sensorData.emg1_t,
                emg2_t: sensorData.emg2_t,
                imu1: sensorData.imu1,
                imu2: sensorData.imu2,
                imu1_t: sensorData.imu1_t,
                imu2_t: sensorData.imu2_t
            };

            if (this.pending_prompt) {
                storageData.prompt_name = this.pending_prompt.name;
                storageData.prompt_time = this.pending_prompt.time;
                storageData.prompt_stage = this.pending_prompt.stageName;
                this.pending_prompt = null;
            }

            const response = await this.sendStorageCommand('append', {
                data: storageData
            });

            if (response.status !== 'success') {
                console.warn('[realtimeEngine] 存储响应警告:', response.msg);
            }

        } catch (error) {
            console.error('[realtimeEngine] 存储数据失败:', error.message);
        }
    }

    // ==================== 兼容旧格式 ====================
    async attributeEMGData(emgData) {
        if (!this.isRunning) return;

        try {
            if (!Array.isArray(emgData.big_bag_raw_data)) {
                console.error("[realtimeEngine] rawData不是数组");
                return;
            }

            if (emgData.big_bag_raw_data.length !== 5) {
                console.error('[realtimeEngine] EMG数据组数不匹配');
                return;
            }

            this.emg_packet_count += 5;
            this.emg_5_packets_count++;

            const dataPacket = {
                type: 'realtime_data',
                data: {
                    emg: emgData.big_bag_raw_data,
                    imu: null,
                    timestamp: Date.now(),
                    packetCount: this.emg_packet_count,
                    framesInPacket: 5
                }
            };

            this.broadcastToClients(dataPacket);

        } catch (error) {
            console.error('[realtimeEngine] 处理EMG数据错误:', error);
        }
    }

    // ==================== 状态和控制 ====================

    getStatus() {
        return {
            isRunning: this.isRunning,
            isCollecting: this.isCollecting,
            collectionPaused: this.collectionPaused,
            currentTaskId: this.currentTaskId,
            currentStageName: this.currentStageName,
            stageFileOpen: this.stageFileOpen,
            clientCount: this.clients.size,
            packetCount: this.emg_packet_count,
            storageConnected: this.storage_connected,
            pendingStorageRequests: this.storageRequestQueue.length
        };
    }

    stop() {
        return new Promise(async (resolve) => {
            this.isRunning = false;
            
            if (this.stageFileOpen && !this.isClosingStageFile) {
                await this.closeStageFile();
            }
            
            this.isCollecting = false;

            this.clients.forEach(client => {
                if (client.readyState === WebSocket.OPEN) {
                    client.close(1001, '服务器关闭');
                }
            });
            this.clients.clear();

            if (this.websocket_server) {
                const closeTimeout = setTimeout(() => {
                    console.warn('[realtimeEngine] 服务器关闭超时');
                    resolve();
                }, 3000);

                this.websocket_server.close(() => {
                    clearTimeout(closeTimeout);
                    console.log('[realtimeEngine] 已停止');
                    resolve();
                });
            } else {
                resolve();
            }

            if (this.ble_client) {
                this.ble_client.close(1000, '服务关闭');
                this.ble_client = null;
            }

            clearTimeout(this.reconnectTimer);
        });
    }
}

// 创建单例
const realtimeEngine = new RealtimeEngine();

module.exports = realtimeEngine;
