// realtimeEngine.js - v4.2 (新增动捕数据支持)
// 修改: 新增mocap_server连接和数据转发

const WebSocket = require('ws');
const EventEmitter = require('events');
const zmq = require('zeromq');
const express = require('express');
const cors = require('cors');
const app = express();
app.use(cors());
app.use(express.json());

const { discrete_gesture_prompt_name, collection_task_name } = require('./constants.js');

function getSysTimeNode() {
    const nsTimestamp = process.hrtime.bigint();
    const sTimestamp = Number(nsTimestamp) / 1000000000.0;
    return Math.round(sTimestamp * 1000000000) / 1000000000;
}

class RealtimeEngine extends EventEmitter {
    constructor() {
        super();
        this.websocket_server = null;
        this.clients = new Set();
        this.isRunning = false;
        this.dataBuffer = [];
        this.maxBufferSize = 1000;

        // BLE服务器
        this.ble_client = null;
        this.ble_clientUrl = 'ws://localhost:8766';
        this.reconnectInterval = 3000;
        this.maxReconnectTimes = 3;
        this.currentReconnectTimes = 0;
        this.reconnectTimer = null;
        this.connectTimeoutTimer = null;

        // 【新增】Mocap服务器
        this.mocap_client = null;
        this.mocap_clientUrl = 'ws://localhost:8767';
        this.mocap_reconnectInterval = 3000;
        this.mocap_maxReconnectTimes = 3;
        this.mocap_currentReconnectTimes = 0;
        this.mocap_reconnectTimer = null;
        this.mocap_connected = false;
        this.mocap_activeChannel = null;

        // 数据包计数
        this.emg_packet_count = 0;
        this.emg_5_packets_count = 0;
        this.dev1_packet_count = 0;
        this.dev2_packet_count = 0;
        this.mocap_packet_count = 0;

        // Storage Server
        this.storage_server_socket = new zmq.Request();
        this.storage_server_host = '127.0.0.1';
        this.storage_server_port = 5555;
        this.storage_connected = false;
        this.storageRequestQueue = [];
        this.isStorageRequestPending = false;

        // 采集状态
        this.currentTaskId = null;
        this.currentUser = null;
        this.isCollecting = false;
        this.collectionPaused = false;
        this.collectionConfig = null;

        // Stage状态
        this.currentStageName = null;
        this.stageFileOpen = false;
        this.stage_start_time = 0;
        
        // Session状态
        this.currentSessionIndex = 0;
        this.currentSessionNumber = 1;
        this.sessionCount = 3;
        this.isClosingStageFile = false;

        // 动捕数据存储
        this.saveMocapData = false;
    }

    start(port = 8080) {
        return new Promise((resolve, reject) => {
            try {
                this.connectTimeoutTimer = setTimeout(() => {
                    this.ble_server_connect();
                }, 3000);
                
                // 【新增】延迟连接Mocap服务器
                setTimeout(() => {
                    this.mocap_server_connect();
                }, 3500);

                this.websocket_server = new WebSocket.Server({ port });

                this.websocket_server.on('connection', (ws) => {
                    console.log('[realtimeEngine] 前端client连接已建立');
                    this.clients.add(ws);

                    ws.send(JSON.stringify({
                        type: 'connection_established',
                        message: '实时数据连接已建立',
                        timestamp: Date.now(),
                        mocap_connected: this.mocap_connected
                    }));

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

                this.storage_server_connect();

            } catch (error) {
                console.error('[realtimeEngine] 启动失败:', error);
                reject(error);
            }
        });
    }

    handleFrontendMessage(rawMessage) {
        try {
            const message = JSON.parse(rawMessage.toString());
            if (message.type !== 'control_command') return;

            const { action, data } = message;
            console.log(`[realtimeEngine] <<< 收到前端命令: ${action}`, data);

            switch (action) {
                case 'task_change': this.onTaskChange(data.taskId); break;
                case 'collection_start': this.onCollectionStart(data); break;
                case 'collection_pause': this.onCollectionPause(); break;
                case 'collection_resume': this.onCollectionResume(); break;
                case 'collection_stop': this.onCollectionStop(data.completed); break;
                case 'session_change': this.onSessionChange(data.sessionIndex, data.sessionNumber); break;
                case 'stage_change': this.onStageChange(data.stageIndex, data.stageName); break;
                case 'stage_start': this.onStageStart(data.stageName, data.stageIndex, data.timestamp); break;
                case 'stage_end': this.onStageEnd(data.stageName, data.timestamp); break;
                case 'prompt_start': this.onPromptStart(data.promptName, data.promptIndex); break;
                case 'prompt_end': this.onPromptEnd(data.promptName, data.promptIndex); break;
                case 'prompt': this.onPrompt(data.name, data.stageName, data.timestamp); break;
                
                // 【新增】Mocap命令
                case 'mocap_set_channel': this.onMocapSetChannel(data.channel); break;
                case 'mocap_reset_channel': this.onMocapResetChannel(data.channel, data.value); break;
                case 'mocap_get_status': this.onMocapGetStatus(); break;
                case 'mocap_set_save':
                    this.saveMocapData = data.save === true;
                    console.log(`[realtimeEngine] 动捕数据存储: ${this.saveMocapData ? '开启' : '关闭'}`);
                    break;
                case 'mocap_sdk_connect': this.onMocapSdkConnect(); break;
                case 'mocap_sdk_disconnect': this.onMocapSdkDisconnect(); break;
                case 'mocap_sdk_get_status': this.onMocapSdkGetStatus(); break;

                default: console.log(`[realtimeEngine] 未知命令: ${action}`);
            }

        } catch (error) {
            console.error('[realtimeEngine] 解析前端消息失败:', error);
        }
    }

    onTaskChange(taskId) {
        console.log(`[realtimeEngine] ========== 任务切换: ${taskId} ==========`);
        this.currentTaskId = taskId;
        
        const channelMapping = {
            'continual_gesture_1': 'finger_joint_angle',
            'continual_gesture_2': 'thumb_index_distance',
            'continual_gesture_3': 'palm_rotation_angle'
        };
        if (channelMapping[taskId]) {
            this.onMocapSetChannel(channelMapping[taskId]);
        }
    }

    onSessionChange(sessionIndex, sessionNumber) {
        this.currentSessionIndex = sessionIndex ?? 0;
        this.currentSessionNumber = sessionNumber ?? (sessionIndex + 1);
    }

    async onCollectionStart(data) {
        console.log(`[realtimeEngine] ========== 开始采集会话 ==========`);
        const { taskId, stageName, userId, config, sessionIndex, sessionNumber, sessionCount } = data;
        
        this.currentTaskId = taskId;
        this.currentUser = { id: userId, ...config?.subject };
        this.collectionConfig = config;
        this.isCollecting = true;
        this.collectionPaused = false;
        this.currentStageName = stageName;
        this.currentSessionIndex = sessionIndex ?? 0;
        this.currentSessionNumber = sessionNumber ?? 1;
        this.sessionCount = sessionCount ?? 3;
    }

    onCollectionPause() { this.collectionPaused = true; }
    onCollectionResume() { this.collectionPaused = false; }

    async onCollectionStop(completed) {
        if (this.stageFileOpen && !this.isClosingStageFile) {
            await this.closeStageFile();
        }
        this.isCollecting = false;
        this.collectionPaused = false;
    }

    onStageChange(stageIndex, stageName) { this.currentStageName = stageName; }

    async onStageStart(stageName, stageIndex, timestamp) {
        this.currentStageName = stageName;
        this.stage_start_time = timestamp || Date.now();
        await this.openStageFile(stageName, stageIndex);
    }

    async onStageEnd(stageName, timestamp) {
        if (this.stageFileOpen && !this.isClosingStageFile) {
            await this.closeStageFile();
        }
    }

    onPromptStart(promptName, promptIndex) {}
    onPromptEnd(promptName, promptIndex) {}

    onPrompt(name, stageName, timestamp) {
        const promptTime = timestamp || Date.now();
        // 【修复】不再设置 pending_prompt，直接保存
        // 之前的问题：设置了 pending_prompt 后立即保存，但没有清除
        // 导致 saveDataToStorage() 又保存了一次，造成重复

        // 立即保存 prompt 到 storage，不等待 EMG 数据
        // 如果文件还没打开，等待一小段时间后重试
        let retryCount = 0;
        const savePrompt = () => {
            if (this.stageFileOpen && !this.isClosingStageFile) {
                this.sendStorageCommand('append', {
                    data: {
                        prompt_name: name,
                        prompt_time: promptTime,
                        prompt_stage: stageName || this.currentStageName
                    }
                }).catch(err => {
                    console.error('[realtimeEngine] 保存 prompt 失败:', err);
                });
            } else {
                // 文件还没打开，100ms 后重试（最多重试 5 次）
                retryCount++;
                if (retryCount <= 5) {
                    console.log(`[realtimeEngine] 文件未打开，100ms 后重试保存 prompt (${retryCount}/5)`);
                    setTimeout(savePrompt, 100);
                } else {
                    console.warn('[realtimeEngine] 保存 prompt 失败：文件未打开（已重试 5 次）');
                }
            }
        };

        savePrompt();
    }
    
    // 【新增】Mocap命令处理
    onMocapSetChannel(channel) {
        console.log(`[realtimeEngine] 设置Mocap通道: ${channel}`);
        this.mocap_activeChannel = channel;
        
        if (this.mocap_client && this.mocap_client.readyState === WebSocket.OPEN) {
            this.mocap_client.send(JSON.stringify({ cmd: 'set_channel', channel }));
        }
    }
    
    onMocapResetChannel(channel, value) {
        if (this.mocap_client && this.mocap_client.readyState === WebSocket.OPEN) {
            this.mocap_client.send(JSON.stringify({ cmd: 'reset_channel', channel, value }));
        }
    }
    
    onMocapGetStatus() {
        this.broadcastToClients({
            type: 'mocap_status',
            connected: this.mocap_connected,
            activeChannel: this.mocap_activeChannel,
            packetCount: this.mocap_packet_count
        });
    }

    // 【新增】动捕SDK连接控制
    onMocapSdkConnect() {
        console.log('[realtimeEngine] 请求连接动捕SDK');
        if (this.mocap_client && this.mocap_client.readyState === WebSocket.OPEN) {
            this.mocap_client.send(JSON.stringify({ cmd: 'sdk_connect' }));
        } else {
            this.broadcastToClients({
                type: 'mocap_sdk_status',
                connected: false,
                error: 'mocap_server未连接'
            });
        }
    }

    onMocapSdkDisconnect() {
        console.log('[realtimeEngine] 请求断开动捕SDK');
        if (this.mocap_client && this.mocap_client.readyState === WebSocket.OPEN) {
            this.mocap_client.send(JSON.stringify({ cmd: 'sdk_disconnect' }));
        }
    }

    onMocapSdkGetStatus() {
        if (this.mocap_client && this.mocap_client.readyState === WebSocket.OPEN) {
            this.mocap_client.send(JSON.stringify({ cmd: 'sdk_get_status' }));
        } else {
            this.broadcastToClients({
                type: 'mocap_sdk_status',
                connected: false,
                sdk_connected: false
            });
        }
    }

    async openStageFile(stageName, stageIndex) {
        console.log(`[realtimeEngine] 尝试打开Stage文件: ${stageName}`);
        console.log(`[realtimeEngine] storage_connected = ${this.storage_connected}`);

        if (!this.storage_connected) {
            console.warn('[realtimeEngine] ⚠️ Storage未连接，无法打开文件');
            return;
        }

        try {
            const config = this.collectionConfig || {};
            const taskName = collection_task_name[this.currentTaskId] || this.currentTaskId;
            const userId = this.currentUser?.id || 'unknown';
            const sessionNum = this.currentSessionNumber || 1;

            const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
            const filename = `${userId}_${taskName}_session${sessionNum}_${stageName}_${timestamp}.h5`;

            const category1 = config.category1 || 'unknown';
            const category2 = config.category2 || 'unknown';
            const category4 = config.category4 || '';

            let subdirectory = category4
                ? `${category1}/${category2}/${userId}/${category4}`
                : `${category1}/${category2}/${userId}`;

            console.log(`[realtimeEngine] 准备打开文件: ${filename}`);
            console.log(`[realtimeEngine] 子目录: ${subdirectory}`);

            // 使用中文任务名称作为 task_id，这样文件夹名称就是中文的
            const taskIdForFolder = config.task || this.currentTaskId;

            const response = await this.sendStorageCommand('create', {
                filename,
                subdirectory,
                task_id: taskIdForFolder,  // 使用中文任务名称
                user_id: userId,
                stage_name: stageName,
                stage_index: stageIndex,
                session_index: this.currentSessionIndex,
                session_number: sessionNum,
                session_count: this.sessionCount,
                category1: category1,
                category2: category2,
                category4: category4,
                template_name: config.templateName || 'default',
                subject_info: this.currentUser,
                start_time: this.stage_start_time
            });

            if (response.status === 'success') {
                this.stageFileOpen = true;
                console.log(`[realtimeEngine] ✅ 文件已打开: ${filename}`);
            } else {
                console.error(`[realtimeEngine] ❌ 打开文件失败:`, response);
            }
        } catch (error) {
            console.error('[realtimeEngine] 打开Stage文件失败:', error);
        }
    }

    async closeStageFile() {
        if (!this.stageFileOpen || this.isClosingStageFile) return;

        this.isClosingStageFile = true;

        try {
            const response = await this.sendStorageCommand('close', { end_time: Date.now() });
            this.stageFileOpen = false;
            if (response.status === 'success') console.log(`[realtimeEngine] ✅ 文件已关闭`);
            return response;
        } catch (error) {
            console.error('[realtimeEngine] 关闭Stage文件失败:', error);
            return { status: 'error', msg: error.message };
        } finally {
            this.isClosingStageFile = false;
        }
    }

    broadcastToClients(dataPacket) {
        const message = JSON.stringify(dataPacket);
        this.clients.forEach((client) => {
            if (client.readyState === WebSocket.OPEN) {
                try { client.send(message); } 
                catch (error) { this.clients.delete(client); }
            }
        });
    }

    ble_server_connect() {
        if (this.ble_client) { this.ble_client.close(); this.ble_client = null; }

        try {
            this.ble_client = new WebSocket(this.ble_clientUrl);

            this.ble_client.onopen = () => {
                console.log(`[realtimeEngine] ✅ BLE服务器连接成功`);
                this.currentReconnectTimes = 0;
                clearTimeout(this.reconnectTimer);
                this.broadcastToClients({ type: 'ble_connection_status', connected: true, message: 'BLE服务器已连接' });
            };

            this.ble_client.onmessage = (event) => {
                try {
                    const packet = JSON.parse(event.data);
                    if (packet.type === 'data') { this.handleBleDataPacket(packet); return; }
                    if (packet.type === 'emg_packet') { this.attributeEMGData(packet); return; }
                } catch (error) {}
            };

            this.ble_client.onerror = (error) => { this.handleReconnect(); };
            this.ble_client.onclose = (event) => {
                this.broadcastToClients({ type: 'ble_connection_status', connected: false, message: 'BLE服务器连接已断开' });
                if (event.code !== 1000) this.handleReconnect();
            };

        } catch (error) { this.handleReconnect('创建连接失败'); }
    }

    handleReconnect(reason = '连接断开') {
        if (this.currentReconnectTimes >= this.maxReconnectTimes) return;
        this.currentReconnectTimes++;
        this.reconnectTimer = setTimeout(() => { this.ble_server_connect(); }, this.reconnectInterval);
    }
    
    // 【新增】Mocap Server连接
    mocap_server_connect() {
        if (this.mocap_client) { this.mocap_client.close(); this.mocap_client = null; }

        try {
            this.mocap_client = new WebSocket(this.mocap_clientUrl);

            this.mocap_client.onopen = () => {
                console.log(`[realtimeEngine] ✅ Mocap服务器连接成功`);
                this.mocap_currentReconnectTimes = 0;
                this.mocap_connected = true;
                clearTimeout(this.mocap_reconnectTimer);
                
                this.broadcastToClients({ type: 'mocap_connection_status', connected: true, message: 'Mocap服务器已连接' });
                
                if (this.mocap_activeChannel) {
                    this.mocap_client.send(JSON.stringify({ cmd: 'set_channel', channel: this.mocap_activeChannel }));
                }
            };

            this.mocap_client.onmessage = (event) => {
                try {
                    const packet = JSON.parse(event.data);
                    if (packet.type === 'mocap') { this.handleMocapDataPacket(packet); }
                    // 【新增】转发SDK状态响应给前端
                    else if (packet.type === 'response' && packet.cmd && packet.cmd.startsWith('sdk_')) {
                        this.broadcastToClients({
                            type: 'mocap_sdk_status',
                            cmd: packet.cmd,
                            status: packet.status,
                            sdk_connected: packet.sdk_connected,
                            message: packet.message
                        });
                    }
                } catch (error) {}
            };

            this.mocap_client.onerror = (error) => { this.handleMocapReconnect(); };
            this.mocap_client.onclose = (event) => {
                this.mocap_connected = false;
                this.broadcastToClients({ type: 'mocap_connection_status', connected: false, message: 'Mocap服务器连接已断开' });
                if (event.code !== 1000) this.handleMocapReconnect();
            };

        } catch (error) { this.handleMocapReconnect('创建连接失败'); }
    }

    handleMocapReconnect(reason = '连接断开') {
        if (this.mocap_currentReconnectTimes >= this.mocap_maxReconnectTimes) return;
        this.mocap_currentReconnectTimes++;
        this.mocap_reconnectTimer = setTimeout(() => { this.mocap_server_connect(); }, this.mocap_reconnectInterval);
    }
    
    // 【新增】处理Mocap数据包
    handleMocapDataPacket(packet) {
        if (!this.isRunning) return;

        try {
            this.mocap_packet_count++;
            this.broadcastToClients({ type: 'mocap_data', data: packet });

            // 【修改】采集时批量保存 mocap 原始数据到 storage
            if (this.isCollecting && !this.collectionPaused && this.stageFileOpen && !this.isClosingStageFile) {
                // 获取批量帧数据
                const frames = packet.frames;  // [{markers, frame, time}, ...]

                if (frames && frames.length > 0) {
                    // 为每帧添加系统时间戳（与蓝牙数据、prompt保持一致）
                    const sysTime = getSysTimeNode();
                    const framesWithSysTime = frames.map((f, idx) => ({
                        ...f,
                        sys_time: sysTime + idx * 0.005  // 每帧间隔5ms (200Hz)
                    }));

                    // 批量发送所有帧到 storage
                    this.saveDataToStorage({
                        mocap_frames: framesWithSysTime,  // 批量帧数据（带系统时间戳）
                        mocap_batch_size: framesWithSysTime.length
                    });
                }
            }
        } catch (error) {
            console.error('[realtimeEngine] 处理Mocap数据包错误:', error);
        }
    }

    handleBleDataPacket(packet) {
        if (!this.isRunning) return;

        try {
            if (!packet.dev1 && !packet.dev2) return;

            // 用于前端显示的滤波后数据
            let emg1Data = null, emg2Data = null;
            // 用于存储的原始数据
            let emg1RawData = null, emg2RawData = null;
            let emg1Timestamps = null, emg2Timestamps = null;
            let emg1FrameIds = null, emg2FrameIds = null;  // 【新增】BLE帧号
            let imu1Data = null, imu2Data = null;
            let imu1Timestamps = null, imu2Timestamps = null;
            let timestamp = packet.ts;
            let stats1 = null, stats2 = null;
            let framesInPacket = 9;

            if (packet.dev1) {
                const dev1 = Array.isArray(packet.dev1) ? packet.dev1[0] : packet.dev1;
                if (dev1) {
                    // uv: 滤波后数据，用于前端显示
                    if (dev1.uv?.length > 0) emg1Data = this.transposeEMG(dev1.uv);
                    // raw: 原始ADC数据，用于存储
                    if (dev1.raw?.length > 0) emg1RawData = this.transposeEMG(dev1.raw);
                    if (dev1.emg_t?.length > 0) emg1Timestamps = dev1.emg_t;
                    if (dev1.frame_ids?.length > 0) emg1FrameIds = dev1.frame_ids;  // 【新增】
                    if (dev1.imu?.[0]) imu1Data = { acc: dev1.imu[0][0], gyr: dev1.imu[0][1], mag: dev1.imu[0][2] };
                    if (dev1.imu_t?.length > 0) imu1Timestamps = dev1.imu_t;
                    stats1 = dev1.s ? { total: dev1.s[0], lost: dev1.s[1] } : null;
                    framesInPacket = dev1.n || 9;
                    this.dev1_packet_count += framesInPacket;
                }
            }

            if (packet.dev2) {
                const dev2 = Array.isArray(packet.dev2) ? packet.dev2[0] : packet.dev2;
                if (dev2) {
                    // uv: 滤波后数据，用于前端显示
                    if (dev2.uv?.length > 0) emg2Data = this.transposeEMG(dev2.uv);
                    // raw: 原始ADC数据，用于存储
                    if (dev2.raw?.length > 0) emg2RawData = this.transposeEMG(dev2.raw);
                    if (dev2.emg_t?.length > 0) emg2Timestamps = dev2.emg_t;
                    if (dev2.frame_ids?.length > 0) emg2FrameIds = dev2.frame_ids;  // 【新增】
                    if (dev2.imu?.[0]) imu2Data = { acc: dev2.imu[0][0], gyr: dev2.imu[0][1], mag: dev2.imu[0][2] };
                    if (dev2.imu_t?.length > 0) imu2Timestamps = dev2.imu_t;
                    stats2 = dev2.s ? { total: dev2.s[0], lost: dev2.s[1] } : null;
                    this.dev2_packet_count += (dev2.n || 9);
                }
            }

            this.emg_packet_count += framesInPacket;

            // 发送滤波后的 uv 数据给前端显示
            this.broadcastToClients({
                type: 'realtime_data',
                data: {
                    emg1: emg1Data, emg2: emg2Data, imu1: imu1Data, imu2: imu2Data,
                    timestamp, packetCount: this.emg_packet_count, framesInPacket,
                    stats1, stats2, activeDevices: packet.active || []
                }
            });

            // 发送原始 raw 数据给 storage_server 存储
            if (this.isCollecting && !this.collectionPaused && this.stageFileOpen && !this.isClosingStageFile) {
                this.saveDataToStorage({
                    emg1: emg1RawData, emg2: emg2RawData, emg1_t: emg1Timestamps, emg2_t: emg2Timestamps,
                    emg1_frame_ids: emg1FrameIds, emg2_frame_ids: emg2FrameIds,  // 【新增】传递帧号
                    imu1: imu1Data, imu2: imu2Data, imu1_t: imu1Timestamps, imu2_t: imu2Timestamps
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

    async storage_server_connect() {
        try {
            const address = `tcp://${this.storage_server_host}:${this.storage_server_port}`;
            await this.storage_server_socket.connect(address);
            this.storage_connected = true;
            console.log(`[realtimeEngine] 已连接到storage_server: ${address}`);
        } catch (err) {
            this.storage_connected = false;
        }
    }

    async sendStorageCommand(cmd, params = {}) {
        return new Promise((resolve, reject) => {
            this.storageRequestQueue.push({ cmd, params, resolve, reject });
            if (!this.isStorageRequestPending) this._processStorageQueue();
        });
    }
    
    async _processStorageQueue() {
        if (this.isStorageRequestPending || this.storageRequestQueue.length === 0) return;
        
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
        if (this.isClosingStageFile || !this.stageFileOpen) return;

        try {
            // 【修复】移除 pending_prompt 处理，prompt 现在在 onPrompt() 中直接保存
            // 不再通过 EMG 数据附带保存，避免重复
            await this.sendStorageCommand('append', { data: sensorData });
        } catch (error) {}
    }

    async attributeEMGData(emgData) {
        if (!this.isRunning) return;
        try {
            if (!Array.isArray(emgData.big_bag_raw_data) || emgData.big_bag_raw_data.length !== 5) return;

            this.emg_packet_count += 5;
            this.emg_5_packets_count++;

            this.broadcastToClients({
                type: 'realtime_data',
                data: { emg: emgData.big_bag_raw_data, imu: null, timestamp: Date.now(), packetCount: this.emg_packet_count, framesInPacket: 5 }
            });
        } catch (error) {}
    }

    getStatus() {
        return {
            isRunning: this.isRunning, isCollecting: this.isCollecting, collectionPaused: this.collectionPaused,
            currentTaskId: this.currentTaskId, currentStageName: this.currentStageName, stageFileOpen: this.stageFileOpen,
            clientCount: this.clients.size, packetCount: this.emg_packet_count, mocapPacketCount: this.mocap_packet_count,
            storageConnected: this.storage_connected, mocapConnected: this.mocap_connected,
            pendingStorageRequests: this.storageRequestQueue.length
        };
    }

    stop() {
        return new Promise(async (resolve) => {
            this.isRunning = false;
            
            if (this.stageFileOpen && !this.isClosingStageFile) await this.closeStageFile();
            this.isCollecting = false;

            this.clients.forEach(client => {
                if (client.readyState === WebSocket.OPEN) client.close(1001, '服务器关闭');
            });
            this.clients.clear();

            if (this.websocket_server) {
                const closeTimeout = setTimeout(() => resolve(), 3000);
                this.websocket_server.close(() => { clearTimeout(closeTimeout); resolve(); });
            } else {
                resolve();
            }

            if (this.ble_client) { this.ble_client.close(1000); this.ble_client = null; }
            if (this.mocap_client) { this.mocap_client.close(1000); this.mocap_client = null; }

            clearTimeout(this.reconnectTimer);
            clearTimeout(this.mocap_reconnectTimer);
        });
    }
}

const realtimeEngine = new RealtimeEngine();
module.exports = realtimeEngine;
