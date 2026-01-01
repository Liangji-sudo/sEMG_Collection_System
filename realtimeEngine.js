// realtimeEngine.js
const WebSocket = require('ws');
const EventEmitter = require('events');
// 新版 zeromq (v6+) 适配代码
const zmq = require('zeromq');
const { promisify } = require('util');
const express = require('express');
const cors = require('cors');
const app = express();
app.use(cors());
app.use(express.json());


const { discrete_gesture_prompt_name, collection_task_name } = require('./constants.js');

// 简化版（仅Node.js环境，更简洁）
function getSysTimeNode() {
    // Node.js v10.7+ 直接提供纳秒级UNIX时间戳
    const nsTimestamp = process.hrtime.bigint();
    const sTimestamp = Number(nsTimestamp) / 1000000000.0;
    return Math.round(sTimestamp * 1000000000) / 1000000000;
}

class RealtimeEngine extends EventEmitter {
    constructor() {
        super();
        // websocket server，用于index.html client的实时显示
        this.websocket_server = null;
        this.clients = new Set();
        this.isRunning = false;
        this.dataBuffer = [];
        this.maxBufferSize = 1000;

        // ===== 新增：ble_server 客户端配置 =====
        this.ble_client = null; // 连接 Python 的 WebSocket 客户端实例
        this.ble_clientUrl = 'ws://localhost:8766'; // Python 服务端的 WebSocket 地址（替换为你的实际地址）
        this.reconnectInterval = 3000; // 重连间隔（3秒）
        this.maxReconnectTimes = 3;
        this.currentReconnectTimes = 0; // 当前重连次数
        this.reconnectTimer = null; // 重连计时器

        this.connectTimeoutTimer = null;
        this.emg_packet_count=0;
        this.emg_5_packets_count=0;
        this.emg_interval = 0.001; //1ms

        // ===== 新增：双设备帧计数 =====
        this.dev1_packet_count = 0;
        this.dev2_packet_count = 0;

        // ===== 新增：storage_server zetomq连接配置 =====
        this.storage_server_socket = new zmq.Request();
        this.storage_server_host = '127.0.0.1';
        this.storage_server_port = 5555;
        this.file_id = 1;
        this.write_enable = 0;    //1 用于表示当前正在打开文件中

        // ===== 新增：taskManager 发来的指令（stage start 信号， stage end信号，prompt信号）
        this.storage_start_flag = 0;
        this.storage_end_flag = 0;
        this.prompt_flag = 0;
        this.buttomname = 0;

        this.prompt_name = null;
        this.prompt_time = 0;

        this.stage_name = null;
        this.stage_start = 0;
        this.stage_end = 0;

        // ===== 采集任务状态（来自collection-controller.js的命令） =====
        this.currentTaskId = null;      // 当前任务ID
        this.currentUser = null;        // 当前用户信息
        this.isCollecting = false;      // 是否正在采集
        this.collectionPaused = false;  // 是否暂停
        this.currentStageName = null;   // 当前Stage名称
    }

    // 0.0 启动 realtimeEngine 实时引擎模块
    start(port = 8080) {
        return new Promise((resolve, reject) => {
            try {
                /**
                 * ===== 1. 连接ble_server，接受数据  =====
                 */
                this.connectTimeoutTimer = setTimeout(() => {
                    this.ble_server_connect();
                }, 1000); // 延迟 1000 毫秒（1秒）


                /**
                 * ===== 2. 启动realtimeEngine >>> index.html websocket广播服务器， 实时显示 =====
                 */ 
                this.websocket_server = new WebSocket.Server({ port });

                //收到index.html client连接请求
                this.websocket_server.on('connection', (ws) => {
                    console.log('[realtimeEngine] 前端client连接已建立');
                    this.clients.add(ws);
                    
                    // ACK to client : connect_established
                    const connectMsg = JSON.stringify({
                        type: 'connection_established',
                        message: '实时数据连接已建立',
                        timestamp: Date.now()
                    });

                    //console.log(`[realtimeEngine] [${new Date().toISOString()}] 发送连接确认给前端:`, JSON.parse(connectMsg));
                    ws.send(connectMsg);

                    // ===== 新增：监听来自前端的控制命令 =====
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
                    console.log(`实时引擎启动成功，WebSocket服务运行在端口 ${port}`);
                    this.isRunning = true;
                    
                    resolve();
                });

                this.websocket_server.on('error', (error) => {
                    console.error('启动WebSocket服务器失败:', error);
                    reject(error);
                });


                /**
                 * ===== 3. 连接storage_server ， 数据存储 =====
                 */
                this.storage_server_connect();


            } catch (error) {
                console.error('[realtimeEngine] 启动实时引擎失败:', error);
                reject(error);
            }
        });
    }


    // ===== 新增：处理来自前端的控制命令 =====
    handleFrontendMessage(rawMessage) {
        try {
            const message = JSON.parse(rawMessage.toString());
            
            // 只处理 control_command 类型的消息
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

    // ===== 采集控制回调函数（供collection-controller.js调用） =====

    /**
     * 任务切换
     */
    onTaskChange(taskId) {
        console.log(`[realtimeEngine] ========== 任务切换 ==========`);
        console.log(`[realtimeEngine] 新任务: ${taskId}`);
        this.currentTaskId = taskId;
    }

    /**
     * 开始采集
     */
    onCollectionStart(taskId, user) {
        console.log(`[realtimeEngine] ========== 开始采集 ==========`);
        console.log(`[realtimeEngine] 任务: ${taskId}`);
        console.log(`[realtimeEngine] 用户: ${user ? user.name : '未知'} (${user ? user.id : 'N/A'})`);
        
        this.currentTaskId = taskId;
        this.currentUser = user;
        this.isCollecting = true;
        this.collectionPaused = false;
    }

    /**
     * 暂停采集
     */
    onCollectionPause() {
        console.log(`[realtimeEngine] ========== 暂停采集 ==========`);
        this.collectionPaused = true;
    }

    /**
     * 继续采集
     */
    onCollectionResume() {
        console.log(`[realtimeEngine] ========== 继续采集 ==========`);
        this.collectionPaused = false;
    }

    /**
     * 停止采集
     */
    onCollectionStop(completed = false) {
        console.log(`[realtimeEngine] ========== 停止采集 ==========`);
        console.log(`[realtimeEngine] 完成状态: ${completed ? '正常完成' : '手动停止'}`);
        
        this.isCollecting = false;
        this.collectionPaused = false;
        this.currentStageName = null;
    }

    /**
     * Stage开始
     */
    onStageStart(stageName, stageIndex, timestamp) {
        console.log(`[realtimeEngine] ========== Stage开始 ==========`);
        console.log(`[realtimeEngine] Stage: ${stageName} (索引: ${stageIndex})`);
        console.log(`[realtimeEngine] 时间戳: ${timestamp}`);
        
        this.currentStageName = stageName;
        this.stage_name = stageName;
        this.stage_start = timestamp;
    }

    /**
     * Stage结束
     */
    onStageEnd(stageName, stageIndex, timestamp) {
        console.log(`[realtimeEngine] ========== Stage结束 ==========`);
        console.log(`[realtimeEngine] Stage: ${stageName} (索引: ${stageIndex})`);
        console.log(`[realtimeEngine] 时间戳: ${timestamp}`);
        
        this.stage_end = timestamp;
    }

    /**
     * Prompt信号（来自动画控制器，在stage动画播放时发送）
     */
    onPrompt(name, stageName, timestamp) {
        console.log(`[realtimeEngine] ========== Prompt信号 ==========`);
        console.log(`[realtimeEngine] Prompt名称: ${name}`);
        console.log(`[realtimeEngine] 所属Stage: ${stageName}`);
        console.log(`[realtimeEngine] 时间戳: ${timestamp}`);
        
        // 更新prompt相关状态
        this.prompt_flag = 1;
        this.prompt_name = name;
        this.prompt_time = timestamp;
    }


    // 0.1 websocket广播
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

    // 0.2 获取realtimeEngine引擎状态
    getStatus() {
        return {
            isRunning: this.isRunning,
            clientCount: this.clients.size,
            bufferSize: this.dataBuffer.length,
            maxBufferSize: this.maxBufferSize,
            port: this.websocket_server ? this.websocket_server.address().port : null
        };
    }

    // 0.3 停止实时引擎
    stop() {
        return new Promise((resolve) => {
            this.isRunning = false;
            //this.stopDataBroadcast();

            // 强制关闭所有客户端（设置code=1001表示正常退出，避免等待）
            this.clients.forEach(client => {
                if (client.readyState === WebSocket.OPEN) {
                    client.close(1001, '服务器关闭'); // 带状态码的强制关闭
                }
            });
            this.clients.clear(); // 立即清空集合，避免残留

            // 关闭服务器时设置超时，避免无限等待
            if (this.websocket_server) {
                const closeTimeout = setTimeout(() => {
                    console.warn('服务器关闭超时，强制退出');
                    resolve();
                }, 3000); // 3秒超时

                this.websocket_server.close(() => {
                    clearTimeout(closeTimeout); // 成功关闭则清除超时
                    console.log('【实时引擎已停止】');
                    resolve();
                });
            } else {
                resolve();
            }

            // 关闭目标 Client 连接（主动关闭，不触发重连）
            if (this.targetClient) {
                this.targetClient.close(1000, '代理关闭');
                this.targetClient = null;
            }
            // 清除重连计时器
            clearTimeout(this.reconnectTimer);
        });
    }




    /**
     * 1. ble_server 数据传输部分
     * 
     */

    // 1.1 连接ble_server 服务器
    ble_server_connect() {
        //关闭当前已有的连接
        if (this.ble_client) {
                this.ble_client.close();
                this.ble_client = null;
            }
        try {
            // 创建新连接
            this.ble_client = new WebSocket(this.ble_clientUrl);
            console.log("[realtimeEngine] create ble_client successful");

            // 连接成功回调
            this.ble_client.onopen = () => {
                console.log(`[realtimeEngine] ble_server连接成功`);
                this.currentReconnectTimes = 0; // 重置重连次数
                clearTimeout(this.reconnectTimer);
                clearTimeout(this.connectTimeoutTimer);
            };

            // 消息接收处理
            this.ble_client.onmessage = (event) => {
                try {
                    const packet = JSON.parse(event.data);
                    
                    // ===== 调试打印：显示从ble_server接收到的数据包 =====
                    // console.log(`\n[realtimeEngine] ========== 收到 ble_server 数据包 ==========`);
                    // console.log(`[realtimeEngine] 数据包类型: ${packet.type}`);
                    // console.log(`[realtimeEngine] 接收时间: ${new Date().toISOString()}`);
                    // ===== 调试打印结束 =====
                    
                    // 处理新格式的数据包 type: "data"（来自双设备版ble_server）
                    if (packet.type === 'data') {
                        this.handleBleDataPacket(packet);
                        return;
                    }
                    
                    // 兼容旧格式 type: "emg_packet"
                    if (packet.type === 'emg_packet') {
                        this.attributeEMGData(packet);
                        return;
                    }
                } catch (error) {
                    console.error('[realtimeEngine] ble_server的emg消息解析失败:', error);
                    console.error('[realtimeEngine] 原始数据:', event.data);
                }
            };

            // 错误处理
            this.ble_client.onerror = (error) => {
                console.error('[realtimeEngine] 错误:', error);
                this.handleReconnect(); // 触发重连
            };

            // 关闭处理
            this.ble_client.onclose = (event) => {
                //clearTimeout(connectionTimeout);
                const code = event.code || 0;
                const reason = event.reason || '';
                console.log(`[realtimeEngine] ble_server 连接关闭：code=${code}, reason=${reason.toString()}`);
                // 非主动关闭（code !== 1000）且未达到最大重连次数时重连
                if (code !== 1000) {
                    this.handleReconnect();
                }
            };

        } catch (error) {
            console.error('[realtimeEngine] realtimeEngine创建失败:', error);
            this.handleReconnect('创建连接失败');
        }
    }

    /**
     * 1.2 处理新格式的BLE数据包 (type: "data", 来自双设备版ble_server)
     * 
     * 修复：现在正确处理双设备数据，分别发送emg1/emg2和imu1/imu2给前端
     * 新增：支持每帧EMG和每组IMU的独立时间戳
     */
    handleBleDataPacket(packet) {
        if (!this.isRunning) return;

        try {
            // 检查是否有任何设备数据
            if (!packet.dev1 && !packet.dev2) {
                // console.log('[realtimeEngine] 无有效设备数据');
                return;
            }

            // 准备数据容器
            let emg1Data = null;
            let emg2Data = null;
            let emg1Timestamps = null;  // EMG1每帧时间戳
            let emg2Timestamps = null;  // EMG2每帧时间戳
            let imu1Data = null;
            let imu2Data = null;
            let imu1Timestamps = null;  // IMU1每组时间戳
            let imu2Timestamps = null;  // IMU2每组时间戳
            let timestamp = packet.ts;
            let stats1 = null;
            let stats2 = null;
            let framesInPacket = 9;

            // ========== 处理设备1数据 ==========
            if (packet.dev1) {
                const dev1 = Array.isArray(packet.dev1) ? packet.dev1[0] : packet.dev1;
                
                if (dev1) {
                    // EMG数据转置: [帧][通道] -> [通道][帧]
                    if (dev1.uv && dev1.uv.length > 0) {
                        emg1Data = this.transposeEMG(dev1.uv);
                    }
                    
                    // EMG时间戳数组（每帧一个）
                    if (dev1.emg_t && dev1.emg_t.length > 0) {
                        emg1Timestamps = dev1.emg_t;
                    }
                    
                    // IMU数据: imu[0] = [[acc], [gyr], [mag]]
                    if (dev1.imu && dev1.imu[0]) {
                        imu1Data = {
                            acc: dev1.imu[0][0],  // [ax, ay, az]
                            gyr: dev1.imu[0][1],  // [gx, gy, gz]
                            mag: dev1.imu[0][2]   // [mx, my, mz]
                        };
                    }
                    
                    // IMU时间戳数组（每组一个）
                    if (dev1.imu_t && dev1.imu_t.length > 0) {
                        imu1Timestamps = dev1.imu_t;
                    }
                    
                    // 统计信息
                    stats1 = dev1.s ? { total: dev1.s[0], lost: dev1.s[1] } : null;
                    framesInPacket = dev1.n || 9;
                    
                    // 更新计数
                    this.dev1_packet_count += framesInPacket;
                }
            }

            // ========== 处理设备2数据 ==========
            if (packet.dev2) {
                const dev2 = Array.isArray(packet.dev2) ? packet.dev2[0] : packet.dev2;
                
                if (dev2) {
                    // EMG数据转置
                    if (dev2.uv && dev2.uv.length > 0) {
                        emg2Data = this.transposeEMG(dev2.uv);
                    }
                    
                    // EMG时间戳数组
                    if (dev2.emg_t && dev2.emg_t.length > 0) {
                        emg2Timestamps = dev2.emg_t;
                    }
                    
                    // IMU数据
                    if (dev2.imu && dev2.imu[0]) {
                        imu2Data = {
                            acc: dev2.imu[0][0],
                            gyr: dev2.imu[0][1],
                            mag: dev2.imu[0][2]
                        };
                    }
                    
                    // IMU时间戳数组
                    if (dev2.imu_t && dev2.imu_t.length > 0) {
                        imu2Timestamps = dev2.imu_t;
                    }
                    
                    // 统计信息
                    stats2 = dev2.s ? { total: dev2.s[0], lost: dev2.s[1] } : null;
                    
                    // 更新计数
                    this.dev2_packet_count += (dev2.n || 9);
                }
            }

            // 更新总包计数
            this.emg_packet_count += framesInPacket;
            this.emg_5_packets_count++;

            /**
             * 构造广播数据包 - 发送给前端显示
             * 修复：现在包含分离的emg1/emg2和imu1/imu2数据
             * 新增：每帧EMG和每组IMU都有独立的时间戳
             * 注意：不再使用兼容字段emg/imu，避免单设备连接时数据被错误显示到两个窗口
             */
            const dataPacket = {
                type: 'realtime_data',
                data: {
                    // EMG数据：分别为设备1和设备2（null表示该设备未连接/无数据）
                    emg1: emg1Data,  // [通道][帧] 格式，16通道 x N帧，或 null
                    emg2: emg2Data,  // [通道][帧] 格式，16通道 x N帧，或 null
                    
                    // EMG时间戳：每帧一个时间戳
                    emg1_t: emg1Timestamps,  // [t0, t1, ..., t8] 9个时间戳，或 null
                    emg2_t: emg2Timestamps,  // [t0, t1, ..., t8] 9个时间戳，或 null
                    
                    // IMU数据：分别为设备1和设备2
                    imu1: imu1Data,  // { acc: [ax,ay,az], gyr: [gx,gy,gz], mag: [mx,my,mz] } 或 null
                    imu2: imu2Data,  // { acc: [ax,ay,az], gyr: [gx,gy,gz], mag: [mx,my,mz] } 或 null
                    
                    // IMU时间戳：每组一个时间戳
                    imu1_t: imu1Timestamps,  // [t0, t1] 2个时间戳，或 null
                    imu2_t: imu2Timestamps,  // [t0, t1] 2个时间戳，或 null
                    
                    // 元数据
                    timestamp: timestamp,  // 包级别时间戳（保留兼容）
                    packetCount: this.emg_packet_count,
                    framesInPacket: framesInPacket,
                    
                    // 双设备统计
                    stats1: stats1,
                    stats2: stats2,
                    
                    // 活跃设备列表
                    activeDevices: packet.active || []
                }
            };

            // 广播给前端
            this.broadcastToClients(dataPacket);

            /**
             * 存储数据包（如果需要）
             * 注意：这里需要适配storage_manager的输入格式
             */
            // 暂时跳过存储，因为格式不同
            // await this.storage_manager(emgRaw, [timestamp]);

        } catch (error) {
            console.error('[realtimeEngine] 处理BLE数据包时发生错误:', error);
        }
    }

    // 辅助函数：转置EMG数据 [帧][通道] -> [通道][帧]
    transposeEMG(uvData) {
        if (!uvData || uvData.length === 0) return null;
        
        const numFrames = uvData.length;      // 9帧
        const numChannels = uvData[0].length; // 16通道
        
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

    // 1.3 处理重连逻辑
    handleReconnect(reason = '连接断开') {
        if (this.currentReconnectTimes >= this.maxReconnectTimes) {
            console.error(`[realtimeEngine] 达到最大重连次数(${this.maxReconnectTimes})，停止重连`);
            return;
        }

        this.currentReconnectTimes++;
        console.log(`[realtimeEngine] ${reason}，${this.reconnectInterval/1000}秒后进行第${this.currentReconnectTimes}次重连...`);

        this.reconnectTimer = setTimeout(() => {
            this.ble_server_connect();
        }, this.reconnectInterval);
    }

    // 1.4 接受来自ble_server 的大包数据（5*32）数据，并即时广播出去（旧格式兼容）
    async attributeEMGData(emgData) {
        if (!this.isRunning) return;

        try {
            // 确保 rawData 是数组类型，且包含 5 组数据
            if (!Array.isArray(emgData.big_bag_raw_data)) {
                console.error("[realtimeEngine] rawData 不是一个数组");
                return;
            }

            // 确保是 5 组数据
            if (emgData.big_bag_raw_data.length !== 5) {
                console.error('[realtimeEngine] emg 数据组数不匹配，应该是 5 组');
                return;
            }

            // 统计小包数量 + 5
            this.emg_packet_count += 5;
            this.emg_5_packets_count++;

            /**
             * 实时显示广播数据包
             */
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
            console.error('[realtimeEngine] 处理EMG数据时发生错误:', error);
        }
    }


    /**
     * 2. storage_server 数据存储连接部分
     */

    // 2.1 连接storage_server 服务器
    async storage_server_connect() {
        try {
            const address = `tcp://${this.storage_server_host}:${this.storage_server_port}`;
            await this.storage_server_socket.connect(address);
            console.log(`[realtimeEngine] 已连接到 storage_server: ${address}`);
        } catch (err) {
            console.error(`[realtimeEngine] 连接 storage_server 失败: ${err.message}`);
        }
    }

    /**
     * @param {string} cmd 指令类型：create/write/close
     * @param {object} params 指令参数
     * @returns {Promise<object>} 服务端响应
     */
    // 2.2 向storage_server 发送储存指令指令
    storage_server_sendCommand(cmd, params = {}) {
        try {
            // 构造请求数据（JSON 序列化）
            const request = JSON.stringify({ cmd, params });
            // 发送数据（新版 send 支持字符串，自动转 Buffer）
            this.storage_server_socket.send(request);

            // 接收响应（新版需用 iterator 接收，且响应是 Buffer 数组）
            const [responseBuffer] = this.storage_server_socket.receive();
            const response = JSON.parse(responseBuffer.toString('utf8'));
            return response;
        } catch (err) {
            throw new Error(`指令发送失败（${cmd}）：${err.message}`);
        }
    } 


    // 2.3 请求storage_server 创建新的文件
    // 注意：函数必须声明为 async，因为 sendCommand 是异步函数
    async storage_server_create_new_hdf5_file() {
        try {
            if(this.write_enable == 1)
            {
                throw new Error(`已经有正在写的文件`);
            }

            // 1. 生成系统时间戳（毫秒级，避免重复；也可改用秒级：Math.floor(Date.now()/1000)）
            const timestamp = Date.now(); 
            // 2. 拼接文件名：hdf5_ + file_id + 时间戳 + .h5
            const fileName = `./storage/hdf5_${this.file_id}_${timestamp}.h5`;

            // 3. 创建 HDF5 文件
            console.log('\n=== 第一步：创建 HDF5 文件 ===, id = ', this.file_id);
            // 关键：异步函数必须加 await，否则 createResponse 是 Promise 对象
            const createResponse = await this.sendCommand('create', {
                file_name: fileName, // 使用拼接后的文件名
                group_name: 'emg_data'
            });
            console.log("创建响应：", createResponse);

            if (createResponse.status !== 'success') {
                throw new Error(`创建文件失败：${createResponse.msg}`);
            }

            // 可选：返回创建的文件名，方便后续使用
            this.write_enable = 1;
            
            return fileName;
            
        } catch (error) {
            console.error(`创建HDF5文件失败（file_id: ${this.file_id}）：`, error.message);
            throw error; // 向上抛出错误，让调用方处理
        }
    }

    // 2.4 关闭保存
    async storage_server_close_hdf5_file() {
        try {
            if(this.write_enable == 0)
            {
                throw new Error(`当前没有文件在写，无需关闭`);
            }

            // 1. 打印日志（关联 fileId，方便定位）
            console.log(`\n=== 关闭 HDF5 文件 ===, file_id = ${this.file_id}`);

            // 2. 异步发送关闭指令（await 等待响应）
            const closeResponse = await this.sendCommand('close');

            // 3. 打印响应结果
            console.log(`文件 ${this.file_id} 关闭响应：`, closeResponse);

            // 4. 校验关闭结果，失败则主动抛出错误
            if (closeResponse.status !== 'success' && closeResponse.status !== 'warning') {
                throw new Error(`文件 ${this.file_id} 关闭失败：${closeResponse.msg}`);
            }

            // 5. 成功关闭，返回响应结果（供外层调用）
            this.write_enable = 0;
            this.file_id++;
            return closeResponse;

        } catch (error) {
            // 6. 捕获所有错误（网络超时/指令失败等），打印日志后重新抛出
            console.error(`关闭 HDF5 文件失败（file_id: ${this.file_id}）：`, error.message);
            throw error; // 向上抛出，让调用方感知错误（可选择是否抛出）
        }
    }


        /**
     * 异步写入单批次传感器数据到 HDF5 文件
     * @param {string} fileId - 文件ID（用于日志定位，关联对应的HDF5文件）
     * @param {Array} sensorData - 单批次传感器数据（如 [25.1, 25.2, 25.3]）
     * @param {Object} [options] - 可选配置项
     * @param {string} [options.datasetName='temp_sensor_1'] - 数据集名称
     * @param {string} [options.dtype='float64'] - 数据类型（float64/uint8/int32等）
     * @returns {Promise<object>} 写入响应结果（包含status/msg/total_count等）
     * @throws {Error} 参数错误/写入失败时抛出错误
     */
    //2.5 写一次数据
    async storage_server_append_hdf5_data(dataPacket, options = {}) {
        // 1. 默认配置（可通过options覆盖）
        try {
            // 2. 核心参数校验（提前拦截无效调用）
            if (!this.file_id || this.write_enable == 0) {
                // throw new Error('写入失败：fileId 不能为空（需关联具体的HDF5文件）');
                return;
            }
            if (!Array.isArray(dataPacket.data.big_bag_raw_data) || dataPacket.data.big_bag_raw_data.length == 0) {
                throw new Error(`文件 ${this.file_id} 写入失败：传感器数据必须是非空数组`);
            }

            // 3. 打印写入日志（关联fileId，方便溯源）
            //console.log(`\n=== 写入 HDF5 数据 ===, file_id = ${this.file_id}`);
            //console.log(`数据集：${datasetName} | 数据类型：${dtype} | 数据条数：${dataPacket.length}`);
            //console.log(`写入数据：`, dataPacket);



            // 4. 异步发送写入指令（await 等待服务端响应）
            const writeResponse = await this.sendCommand('append', {
                data: dataPacket.data
            });



            // 5. 校验写入结果，失败则主动抛出错误
            if (writeResponse.status !== 'success') {
                throw new Error(`文件 ${this.file_id} 写入失败：${writeResponse.msg || '未知错误'}`);
            }

            // 6. 打印成功日志并返回响应结果
            //console.log(`文件 ${this.file_id} 写入成功 | 累计数据条数：${writeResponse.total_count}`);
            return writeResponse;

        } catch (error) {
            // 7. 捕获所有错误，打印详情后重新抛出（让调用方感知）
            console.error(`[写入错误] file_id = ${this.file_id}：`, error.message);
            throw error;
        }
    }

    /**
     * 发送指令到 Python 服务端（适配新版 API）
     * @param {string} cmd 指令类型：create/write/close
     * @param {object} params 指令参数
     * @returns {Promise<object>} 服务端响应
     */
    // 2.6 发送指令到服务器
    async sendCommand(cmd, params = {}) {
        try {
            // 构造请求数据（JSON 序列化）
            const request = JSON.stringify({ cmd, params });
            // 发送数据（新版 send 支持字符串，自动转 Buffer）
            await this.storage_server_socket.send(request);

            // 接收响应（新版需用 iterator 接收，且响应是 Buffer 数组）
            const [responseBuffer] = await this.storage_server_socket.receive();
            const response = JSON.parse(responseBuffer.toString('utf8'));
            return response;
        } catch (err) {
            throw new Error(`指令发送失败（${cmd}）：${err.message}`);
        }
    }




    /**
     * 
     *  3.1 接收taskManager (lab.js) 的控制储存起始结束按钮
     */

    taskManager_get_command(buttomname)
    {
        //console.log(`realtimeEngine 收到按钮点击`, buttomname);
        this.buttomname = buttomname;
        switch (this.buttomname) {
            case 'start':
                this.taskManager_send_start();
                break;
            case 'stop':
                this.taskManager_send_end();
                break;
            case 'prompt1':
                this.taskManager_send_prompt(0);
                break;
            case 'prompt2':
                this.taskManager_send_prompt(1);
                break;
            case 'prompt3':
                this.taskManager_send_prompt(2);
                break;
            case 'prompt4':
                this.taskManager_send_prompt(3);
                break;
            case 'prompt5':
                this.taskManager_send_prompt(4);
                break;
            default:
                break;
        }


    }
    taskManager_send_start()
    {
        //console.log("realtimeEngine storage start");
        this.storage_start_flag = 1;
        //this.storage_server_create_new_hdf5_file();
    }

    taskManager_send_end()
    {
        //console.log("realtimeEngine storage end");
        this.storage_end_flag = 1;
        //this.storage_server_close_hdf5_file();
    }

    taskManager_send_prompt(i)
    {
        //console.log("realtimeEngine storage prompt = ", discrete_gesture_prompt_name[i]);
        this.prompt_flag = 1;
        this.prompt_name = discrete_gesture_prompt_name[i];
        this.prompt_time = getSysTimeNode();
        
    }


}

// 创建单例实例
const realtimeEngine = new RealtimeEngine();

module.exports = realtimeEngine;
