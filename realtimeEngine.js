// realtimeEngine.js - v2.0 (支持完整存储功能)
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

        // ===== 采集状态 =====
        this.currentTaskId = null;
        this.currentUser = null;
        this.isCollecting = false;
        this.collectionPaused = false;
        this.currentStageName = null;

        // ===== Stage状态 =====
        this.stage_name = null;
        this.stage_start_time = 0;
        this.stage_end_time = 0;
        this.stage_started = false;

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
                }, 1000);

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
                    this.onCollectionStart(data.taskId, data.user);
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
                case 'stage_start':
                    this.onStageStart(data.stageName, data.stageIndex, data.timestamp);
                    break;
                case 'stage_end':
                    this.onStageEnd(data.stageName, data.stageIndex, data.timestamp);
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

    async onCollectionStart(taskId, user) {
        console.log(`[realtimeEngine] ========== 开始采集 ==========`);
        console.log(`[realtimeEngine] 任务: ${taskId}`);
        console.log(`[realtimeEngine] 用户: ${user ? user.name : '未知'} (${user ? user.id : 'N/A'})`);

        this.currentTaskId = taskId;
        this.currentUser = user;
        this.isCollecting = true;
        this.collectionPaused = false;

        // 创建新的HDF5文件
        try {
            const response = await this.sendStorageCommand('create', {
                task_id: taskId,
                user_id: user ? user.id : 'unknown'
            });
            console.log('[realtimeEngine] 创建存储文件响应:', response);
        } catch (error) {
            console.error('[realtimeEngine] 创建存储文件失败:', error);
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

        this.isCollecting = false;
        this.collectionPaused = false;
        this.currentStageName = null;
        this.stage_started = false;

        // 关闭HDF5文件
        try {
            const response = await this.sendStorageCommand('close', {});
            console.log('[realtimeEngine] 关闭存储文件响应:', response);
        } catch (error) {
            console.error('[realtimeEngine] 关闭存储文件失败:', error);
        }
    }

    onStageStart(stageName, stageIndex, timestamp) {
        console.log(`[realtimeEngine] ========== Stage开始 ==========`);
        console.log(`[realtimeEngine] Stage: ${stageName} (索引: ${stageIndex})`);
        console.log(`[realtimeEngine] 时间戳: ${timestamp}`);

        this.currentStageName = stageName;
        this.stage_name = stageName;
        this.stage_start_time = timestamp;
        this.stage_started = true;
    }

    onStageEnd(stageName, stageIndex, timestamp) {
        console.log(`[realtimeEngine] ========== Stage结束 ==========`);
        console.log(`[realtimeEngine] Stage: ${stageName} (索引: ${stageIndex})`);
        console.log(`[realtimeEngine] 时间戳: ${timestamp}`);

        this.stage_end_time = timestamp;
        this.stage_started = false;
    }

    onPrompt(name, stageName, timestamp) {
        console.log(`[realtimeEngine] ========== Prompt信号 ==========`);
        console.log(`[realtimeEngine] Prompt: ${name}, Stage: ${stageName}, Time: ${timestamp}`);

        // 缓存prompt，等待下一次数据包一起发送
        this.pending_prompt = {
            name: name,
            time: timestamp,
            stageName: stageName
        };
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
                console.log(`[realtimeEngine] BLE服务器连接成功`);
                this.currentReconnectTimes = 0;
                clearTimeout(this.reconnectTimer);
                clearTimeout(this.connectTimeoutTimer);
            };

            this.ble_client.onmessage = (event) => {
                try {
                    const packet = JSON.parse(event.data);

                    if (packet.type === 'data') {
                        this.handleBleDataPacket(packet);
                        return;
                    }

                    if (packet.type === 'emg_packet') {
                        this.attributeEMGData(packet);
                        return;
                    }
                } catch (error) {
                    console.error('[realtimeEngine] 解析BLE数据失败:', error);
                }
            };

            this.ble_client.onerror = (error) => {
                console.error('[realtimeEngine] BLE连接错误:', error);
                this.handleReconnect();
            };

            this.ble_client.onclose = (event) => {
                const code = event.code || 0;
                console.log(`[realtimeEngine] BLE连接关闭: code=${code}`);
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

    // ==================== 处理BLE数据包 ====================
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
                    if (dev1.uv && dev1.uv.length > 0) {
                        emg1Data = this.transposeEMG(dev1.uv);
                    }
                    if (dev1.emg_t && dev1.emg_t.length > 0) {
                        emg1Timestamps = dev1.emg_t;
                    }
                    if (dev1.imu && dev1.imu[0]) {
                        imu1Data = {
                            acc: dev1.imu[0][0],
                            gyr: dev1.imu[0][1],
                            mag: dev1.imu[0][2]
                        };
                    }
                    if (dev1.imu_t && dev1.imu_t.length > 0) {
                        imu1Timestamps = dev1.imu_t;
                    }
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

            // ========== 存储数据（如果正在采集） ==========
            if (this.isCollecting && !this.collectionPaused) {
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

    async sendStorageCommand(cmd, params = {}) {
        try {
            const request = JSON.stringify({ cmd, params });
            await this.storage_server_socket.send(request);

            const [responseBuffer] = await this.storage_server_socket.receive();
            const response = JSON.parse(responseBuffer.toString('utf8'));
            return response;
        } catch (err) {
            throw new Error(`Storage命令失败(${cmd}): ${err.message}`);
        }
    }

    async saveDataToStorage(sensorData) {
        try {
            // 构造存储数据包
            const storageData = {
                // EMG数据
                emg1: sensorData.emg1,
                emg2: sensorData.emg2,
                emg1_t: sensorData.emg1_t,
                emg2_t: sensorData.emg2_t,

                // IMU数据
                imu1: sensorData.imu1,
                imu2: sensorData.imu2,
                imu1_t: sensorData.imu1_t,
                imu2_t: sensorData.imu2_t
            };

            // 添加Prompt数据（如果有）
            if (this.pending_prompt) {
                storageData.prompt_name = this.pending_prompt.name;
                storageData.prompt_time = this.pending_prompt.time;
                storageData.prompt_stage = this.pending_prompt.stageName;
                this.pending_prompt = null;  // 清除已发送的prompt
            }

            // 添加Stage Start（如果是新开始的stage）
            if (this.stage_started && this.stage_start_time > 0) {
                storageData.stage_start_name = this.stage_name;
                storageData.stage_start_time = this.stage_start_time;
                // 只发送一次
                this.stage_start_time = 0;
            }

            // 添加Stage End（如果stage刚结束）
            if (!this.stage_started && this.stage_end_time > 0) {
                storageData.stage_end_name = this.stage_name;
                storageData.stage_end_time = this.stage_end_time;
                // 只发送一次
                this.stage_end_time = 0;
            }

            // 发送给storage server
            const response = await this.sendStorageCommand('append', {
                data: storageData
            });

            // 可选：检查响应状态
            if (response.status !== 'success') {
                console.warn('[realtimeEngine] 存储响应警告:', response.msg);
            }

        } catch (error) {
            // 存储错误不应中断数据流，只记录日志
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
            clientCount: this.clients.size,
            packetCount: this.emg_packet_count,
            storageConnected: this.storage_connected
        };
    }

    stop() {
        return new Promise((resolve) => {
            this.isRunning = false;
            this.isCollecting = false;

            // 关闭所有客户端
            this.clients.forEach(client => {
                if (client.readyState === WebSocket.OPEN) {
                    client.close(1001, '服务器关闭');
                }
            });
            this.clients.clear();

            // 关闭WebSocket服务器
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

            // 关闭BLE客户端
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
