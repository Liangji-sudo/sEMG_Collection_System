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
        this.reconnectInterval = 2000;   // 重连间隔2秒
        this.maxReconnectTimes = 10;     // 最多重连10次
        this.currentReconnectTimes = 0;
        this.reconnectTimer = null;
        this.connectTimeoutTimer = null;

        // 【新增】Mocap服务器
        this.mocap_client = null;
        this.mocap_clientUrl = 'ws://localhost:8767';
        this.mocap_reconnectInterval = 2000;
        this.mocap_maxReconnectTimes = 10;
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

        // 【优化】批量发送缓冲区
        this.realtimeDataBuffer = [];
        this.realtimeDataBufferLimit = 3;  // 每3个数据包发送一次（约100ms间隔）
        this.realtimeDataTimer = null;
        this.realtimeDataMaxDelay = 50;    // 最大延迟50ms

        // Storage Server
        this.storage_server_socket = new zmq.Request();  // REP socket 用于控制命令
        this.storage_push_socket = new zmq.Push();       // 【新增】PUSH socket 用于数据发送
        this.storage_server_host = '127.0.0.1';
        this.storage_server_port = 5555;
        this.storage_data_port = 5556;                   // 【新增】数据端口
        this.storage_connected = false;
        this.storage_push_connected = false;             // 【新增】PUSH连接状态
        this.storageRequestQueue = [];
        this.isStorageRequestPending = false;

        // 采集状态
        this.currentTaskId = null;
        this.currentUser = null;
        this.isCollecting = false;
        this.collectionPaused = false;
        this.collectionConfig = null;
        this.isTestMode = false;  // 【新增】测试模式标志（不保存H5文件）

        // Stage状态
        this.currentStageName = null;
        this.stageFileOpen = false;
        this.stage_start_time = 0;
        this.currentStageNeedMocap = false;  // 【新增】当前stage是否需要动捕数据
        
        // Session状态
        this.currentSessionIndex = 0;
        this.currentSessionNumber = 1;
        this.sessionCount = 3;
        this.isClosingStageFile = false;

        // 动捕数据存储
        this.saveMocapData = false;

        // 【新增】SD卡bin文件名（用于HDF5溯源）
        this.sd_filenames = { dev1: null, dev2: null };
        // 【新增】BLE设备名称（用于HDF5追溯数据来源）
        this.device_names = { dev1: null, dev2: null };
    }

    start(port = 8080) {
        return new Promise((resolve, reject) => {
            try {
                // 延迟连接BLE服务器，等待ble_server启动完成（包括蓝牙适配器预热）
                this.connectTimeoutTimer = setTimeout(() => {
                    this.ble_server_connect();
                }, 5000);  // 从3秒改为5秒，给ble_server更多启动时间

                // 【新增】延迟连接Mocap服务器
                setTimeout(() => {
                    this.mocap_server_connect();
                }, 5500);

                this.websocket_server = new WebSocket.Server({ port });

                // 【新增】客户端ID计数器
                let clientIdCounter = 0;

                this.websocket_server.on('connection', (ws, req) => {
                    // 【新增】为每个客户端分配唯一ID
                    const clientId = ++clientIdCounter;
                    ws.clientId = clientId;
                    ws.clientName = `未知客户端#${clientId}`;  // 默认名称，等待客户端自报
                    ws.connectedAt = new Date().toISOString();

                    console.log(`[realtimeEngine] 前端client连接已建立 (ID: ${clientId}, 当前总数: ${this.clients.size + 1})`);
                    this.clients.add(ws);

                    ws.send(JSON.stringify({
                        type: 'connection_established',
                        message: '实时数据连接已建立',
                        timestamp: Date.now(),
                        mocap_connected: this.mocap_connected,
                        clientId: clientId  // 【新增】告知客户端其ID
                    }));

                    ws.on('message', (message) => {
                        this.handleFrontendMessage(message, ws);
                    });

                    ws.on('close', () => {
                        console.log(`[realtimeEngine] 前端WebSocket连接已关闭 (ID: ${ws.clientId}, 名称: ${ws.clientName})`);
                        this.clients.delete(ws);
                    });

                    ws.on('error', (error) => {
                        console.error(`[realtimeEngine] WebSocket错误 (ID: ${ws.clientId}):`, error);
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

    handleFrontendMessage(rawMessage, ws) {
        try {
            const message = JSON.parse(rawMessage.toString());

            // 【新增】处理客户端自报身份
            if (message.type === 'client_identify') {
                if (ws && message.clientName) {
                    ws.clientName = message.clientName;
                    console.log(`[realtimeEngine] 客户端 #${ws.clientId} 自报身份: ${message.clientName}`);
                }
                return;
            }

            if (message.type !== 'control_command') return;

            const { action, data } = message;
            // 【修改】打印时包含客户端信息
            const clientInfo = ws ? `(来自: ${ws.clientName})` : '';
            console.log(`[realtimeEngine] <<< 收到前端命令: ${action} ${clientInfo}`, data);

            switch (action) {
                case 'task_change': this.onTaskChange(data.taskId); break;
                case 'collection_start': this.onCollectionStart(data); break;
                case 'collection_pause': this.onCollectionPause(); break;
                case 'collection_resume': this.onCollectionResume(); break;
                case 'collection_stop': this.onCollectionStop(data.completed); break;
                case 'session_change': this.onSessionChange(data.sessionIndex, data.sessionNumber); break;
                case 'stage_change': this.onStageChange(data.stageIndex, data.stageName); break;
                case 'stage_start': this.onStageStart(data.stageName, data.stageIndex, data.timestamp, data.needMocap); break;
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
        const { taskId, stageName, userId, config, sessionIndex, sessionNumber, sessionCount, isTestMode } = data;

        this.currentTaskId = taskId;
        this.currentUser = { id: userId, ...config?.subject };
        this.collectionConfig = config;
        this.isCollecting = true;
        this.collectionPaused = false;
        this.currentStageName = stageName;
        this.currentSessionIndex = sessionIndex ?? 0;
        this.currentSessionNumber = sessionNumber ?? 1;
        this.sessionCount = sessionCount ?? 3;

        // 【新增】保存测试模式状态
        this.isTestMode = isTestMode || false;
        if (this.isTestMode) {
            console.log(`[realtimeEngine] ★★★ 测试模式：不会创建H5文件 ★★★`);
        }

        // 注意：不在这里重置sd_filenames，因为sd_filenames_updated事件会在start_all之后到达
        // sd_filenames的管理完全由onSdFilenamesUpdated负责
    }

    onCollectionPause() { this.collectionPaused = true; }
    onCollectionResume() { this.collectionPaused = false; }

    async onCollectionStop(completed) {
        if (this.stageFileOpen && !this.isClosingStageFile) {
            await this.closeStageFile();
        }
        this.isCollecting = false;
        this.collectionPaused = false;
        // 【新增】重置测试模式标志
        this.isTestMode = false;
        // 【修复】不再清空 sd_filenames
        // 原因：在同一个采集会话中（从进入采集界面到离开），ESP32 持续录制到同一个 bin 文件
        // sd_filenames 由 sd_filenames_updated 事件更新，只有在 start_all 时才会变化
        // 如果在这里清空，第二次点击采集按钮时 sd_filenames 为空，导致 H5 文件缺少 bin 字段
    }

    // 【新增】处理SD卡文件名和设备名称更新事件
    // 此事件由ble_server.py在start_all成功后发送，包含当前实际连接设备的文件名和设备名称
    onSdFilenamesUpdated(sd_filenames, device_names) {
        // 完全替换，只保存当前实际连接设备的文件名
        this.sd_filenames = {
            dev1: sd_filenames?.dev1 || null,
            dev2: sd_filenames?.dev2 || null
        };
        // 【新增】保存BLE设备名称
        this.device_names = {
            dev1: device_names?.dev1 || null,
            dev2: device_names?.dev2 || null
        };
        console.log(`[realtimeEngine] SD卡文件名已更新: dev1=${this.sd_filenames.dev1 || '无'}, dev2=${this.sd_filenames.dev2 || '无'}`);
        console.log(`[realtimeEngine] BLE设备名称已更新: dev1=${this.device_names.dev1 || '无'}, dev2=${this.device_names.dev2 || '无'}`);
    }

    onStageChange(stageIndex, stageName) { this.currentStageName = stageName; }

    async onStageStart(stageName, stageIndex, timestamp, needMocap = false) {
        this.currentStageName = stageName;
        this.stage_start_time = timestamp || Date.now();
        // 【新增】保存当前stage是否需要动捕数据
        this.currentStageNeedMocap = needMocap;
        console.log(`[realtimeEngine] Stage开始: ${stageName}, needMocap: ${needMocap}`);
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

        // 【新增】测试模式下跳过创建H5文件
        if (this.isTestMode) {
            console.log(`[realtimeEngine] ★ 测试模式：跳过创建H5文件 ★`);
            this.stageFileOpen = false;  // 确保不会尝试写入
            return;
        }

        if (!this.storage_connected) {
            console.warn('[realtimeEngine] ⚠️ Storage未连接，无法打开文件');
            return;
        }

        // 【修复】等待sd_filenames_updated事件到达（最多等待500ms）
        // 因为sd_filenames_updated事件是从ble_server.py的start_all发送的，
        // 可能在stage_start命令之后才到达
        if (!this.sd_filenames.dev1 && !this.sd_filenames.dev2) {
            console.log('[realtimeEngine] 等待SD卡文件名...');
            await new Promise(resolve => setTimeout(resolve, 300));
            console.log(`[realtimeEngine] SD卡文件名: dev1=${this.sd_filenames.dev1 || '无'}, dev2=${this.sd_filenames.dev2 || '无'}`);
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
                start_time: this.stage_start_time,
                // 【新增】传递SD卡bin文件名，用于HDF5溯源
                sd_bin_dev1: this.sd_filenames.dev1,  // 例如 "S001_L_260312_143025"
                sd_bin_dev2: this.sd_filenames.dev2,  // 例如 "S001_R_260312_143025"
                // 【新增】传递BLE设备名称，用于追溯数据来源
                ble_dev1: this.device_names.dev1,  // 例如 "WristBand_3A76"
                ble_dev2: this.device_names.dev2   // 例如 "WristBand_5B12"
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
        // 【修复】清理旧连接时先清除事件处理器，避免触发重连
        if (this.ble_client) {
            this.ble_client.onopen = null;
            this.ble_client.onclose = null;
            this.ble_client.onerror = null;
            this.ble_client.onmessage = null;
            try { this.ble_client.close(); } catch (e) {}
            this.ble_client = null;
        }

        try {
            console.log(`[realtimeEngine] 正在连接BLE数据端: ${this.ble_clientUrl} (尝试 ${this.currentReconnectTimes + 1}/${this.maxReconnectTimes})`);
            this.ble_client = new WebSocket(this.ble_clientUrl);

            this.ble_client.onopen = () => {
                console.log(`[realtimeEngine] ✅ BLE数据端连接成功 (${this.ble_clientUrl})`);
                this.currentReconnectTimes = 0;
                clearTimeout(this.reconnectTimer);
                this.broadcastToClients({ type: 'ble_connection_status', connected: true, message: 'BLE服务器已连接' });
            };

            this.ble_client.onmessage = (event) => {
                try {
                    const packet = JSON.parse(event.data);

                    // 调试：打印收到的数据类型
                    if (packet.type === 'data') {
                        this.handleBleDataPacket(packet);
                        return;
                    }
                    if (packet.type === 'emg_packet') { this.attributeEMGData(packet); return; }
                    // 【新增】监听sd_filenames_updated事件（包含设备名称）
                    if (packet.type === 'event' && packet.event === 'sd_filenames_updated') {
                        this.onSdFilenamesUpdated(packet.sd_filenames, packet.device_names);
                        return;
                    }

                    // 打印欢迎消息
                    if (packet.type === 'welcome') {
                        console.log(`[realtimeEngine] 收到数据端欢迎消息:`, packet.message);
                    }
                } catch (error) {}
            };

            this.ble_client.onerror = (error) => {
                // 【修复】onerror后通常会触发onclose，这里不重复处理
                console.log(`[realtimeEngine] BLE数据端连接错误`);
            };
            this.ble_client.onclose = (event) => {
                console.log(`[realtimeEngine] BLE数据端连接关闭, code: ${event.code}`);
                this.ble_client = null;  // 【修复】清理引用
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
        // 【修复】清理旧连接时先清除事件处理器，避免触发重连
        if (this.mocap_client) {
            this.mocap_client.onopen = null;
            this.mocap_client.onclose = null;
            this.mocap_client.onerror = null;
            this.mocap_client.onmessage = null;
            try { this.mocap_client.close(); } catch (e) {}
            this.mocap_client = null;
        }

        try {
            console.log(`[realtimeEngine] 正在连接Mocap服务器: ${this.mocap_clientUrl} (尝试 ${this.mocap_currentReconnectTimes + 1}/${this.mocap_maxReconnectTimes})`);
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

            this.mocap_client.onerror = (error) => {
                // 【修复】onerror后通常会触发onclose，这里不重复处理
                console.log(`[realtimeEngine] Mocap服务器连接错误`);
            };
            this.mocap_client.onclose = (event) => {
                console.log(`[realtimeEngine] Mocap服务器连接关闭, code: ${event.code}`);
                this.mocap_connected = false;
                this.mocap_client = null;  // 【修复】清理引用
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
            // 【始终】广播给前端（用于实时显示）
            this.broadcastToClients({ type: 'mocap_data', data: packet });

            // 【修改】采集时批量保存 mocap 原始数据到 storage
            // 【新增】只有当前stage需要动捕数据时才保存
            if (this.isCollecting && !this.collectionPaused && this.stageFileOpen && !this.isClosingStageFile && this.currentStageNeedMocap) {
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
            // 【修改】每个设备有2个IMU传感器（a和b）
            let imu1aData = null, imu1bData = null;  // 设备1的两个IMU
            let imu2aData = null, imu2bData = null;  // 设备2的两个IMU
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
                    // 【修改】提取两个IMU数据：imu[0]=IMU_A, imu[1]=IMU_B
                    if (dev1.imu?.[0]) imu1aData = { acc: dev1.imu[0][0], gyr: dev1.imu[0][1], mag: dev1.imu[0][2] };
                    if (dev1.imu?.[1]) imu1bData = { acc: dev1.imu[1][0], gyr: dev1.imu[1][1], mag: dev1.imu[1][2] };
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
                    // 【修改】提取两个IMU数据：imu[0]=IMU_A, imu[1]=IMU_B
                    if (dev2.imu?.[0]) imu2aData = { acc: dev2.imu[0][0], gyr: dev2.imu[0][1], mag: dev2.imu[0][2] };
                    if (dev2.imu?.[1]) imu2bData = { acc: dev2.imu[1][0], gyr: dev2.imu[1][1], mag: dev2.imu[1][2] };
                    if (dev2.imu_t?.length > 0) imu2Timestamps = dev2.imu_t;
                    stats2 = dev2.s ? { total: dev2.s[0], lost: dev2.s[1] } : null;
                    this.dev2_packet_count += (dev2.n || 9);
                }
            }

            this.emg_packet_count += framesInPacket;

            // 【优化】批量发送数据给前端，减少 WebSocket 发送次数
            // 【修改】前端显示仍使用imu1/imu2（使用IMU_A的数据）
            const dataItem = {
                emg1: emg1Data, emg2: emg2Data,
                imu1: imu1aData, imu2: imu2aData,  // 前端显示使用IMU_A
                timestamp, packetCount: this.emg_packet_count, framesInPacket,
                stats1, stats2, activeDevices: packet.active || []
            };
            this.realtimeDataBuffer.push(dataItem);

            // 设置定时器，确保数据不会延迟太久
            if (!this.realtimeDataTimer) {
                this.realtimeDataTimer = setTimeout(() => {
                    this.flushRealtimeDataBuffer();
                }, this.realtimeDataMaxDelay);
            }

            // 达到缓冲区限制时立即发送
            if (this.realtimeDataBuffer.length >= this.realtimeDataBufferLimit) {
                this.flushRealtimeDataBuffer();
            }

            // 发送原始 raw 数据给 storage_server 存储
            // 【修改】存储4个IMU数据（imu1a, imu1b, imu2a, imu2b）
            if (this.isCollecting && !this.collectionPaused && this.stageFileOpen && !this.isClosingStageFile) {
                this.saveDataToStorage({
                    emg1: emg1RawData, emg2: emg2RawData, emg1_t: emg1Timestamps, emg2_t: emg2Timestamps,
                    emg1_frame_ids: emg1FrameIds, emg2_frame_ids: emg2FrameIds,
                    imu1a: imu1aData, imu1b: imu1bData, imu1_t: imu1Timestamps,
                    imu2a: imu2aData, imu2b: imu2bData, imu2_t: imu2Timestamps
                });
            }

        } catch (error) {
            console.error('[realtimeEngine] 处理BLE数据包错误:', error);
        }
    }

    // 【新增】批量发送缓冲区数据给前端
    flushRealtimeDataBuffer() {
        if (this.realtimeDataTimer) {
            clearTimeout(this.realtimeDataTimer);
            this.realtimeDataTimer = null;
        }

        if (this.realtimeDataBuffer.length === 0) return;

        // 批量发送所有缓冲的数据
        this.broadcastToClients({
            type: 'realtime_data_batch',
            batch: this.realtimeDataBuffer
        });

        this.realtimeDataBuffer = [];
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
            // 连接 REP socket（用于控制命令）
            const address = `tcp://${this.storage_server_host}:${this.storage_server_port}`;
            await this.storage_server_socket.connect(address);
            this.storage_connected = true;
            console.log(`[realtimeEngine] 已连接到storage_server控制端: ${address}`);

            // 【新增】连接 PUSH socket（用于数据发送）
            const dataAddress = `tcp://${this.storage_server_host}:${this.storage_data_port}`;
            await this.storage_push_socket.connect(dataAddress);
            this.storage_push_connected = true;
            console.log(`[realtimeEngine] 已连接到storage_server数据端: ${dataAddress} (PUSH模式)`);
        } catch (err) {
            this.storage_connected = false;
            this.storage_push_connected = false;
            console.error('[realtimeEngine] 连接storage_server失败:', err);
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
            // 【优化】使用 PUSH socket 发送数据，非阻塞，不等待响应
            if (this.storage_push_connected) {
                const request = JSON.stringify({ cmd: 'append', params: { data: sensorData } });
                await this.storage_push_socket.send(request);
            } else {
                // 回退到 REP socket（如果 PUSH 未连接）
                await this.sendStorageCommand('append', { data: sensorData });
            }
        } catch (error) {
            // 静默失败，不阻塞主流程
        }
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
        const WebSocket = require('ws');
        const bleConnected = this.ble_client && this.ble_client.readyState === WebSocket.OPEN;

        // 【新增】获取已连接客户端列表
        const connectedClients = [];
        this.clients.forEach(client => {
            connectedClients.push({
                id: client.clientId,
                name: client.clientName,
                connectedAt: client.connectedAt
            });
        });

        return {
            isRunning: this.isRunning, isCollecting: this.isCollecting, collectionPaused: this.collectionPaused,
            currentTaskId: this.currentTaskId, currentStageName: this.currentStageName, stageFileOpen: this.stageFileOpen,
            clientCount: this.clients.size, packetCount: this.emg_packet_count, mocapPacketCount: this.mocap_packet_count,
            storageConnected: this.storage_connected, mocapConnected: this.mocap_connected,
            bleConnected: bleConnected,
            pendingStorageRequests: this.storageRequestQueue.length,
            connectedClients: connectedClients  // 【新增】客户端列表
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

            // 【新增】关闭 ZMQ sockets
            try {
                if (this.storage_push_socket) { this.storage_push_socket.close(); }
                if (this.storage_server_socket) { this.storage_server_socket.close(); }
            } catch (e) {}

            clearTimeout(this.reconnectTimer);
            clearTimeout(this.mocap_reconnectTimer);
        });
    }
}

const realtimeEngine = new RealtimeEngine();
module.exports = realtimeEngine;
