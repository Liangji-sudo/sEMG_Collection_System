// realtimeEngine.js
const WebSocket = require('ws');
const EventEmitter = require('events');
// 新版 zeromq (v6+) 适配代码
const zmq = require('zeromq');
const { promisify } = require('util');


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


        // ===== 新增：storage_server zetomq连接配置 =====
        this.storage_server_socket = new zmq.Request();
        this.storage_server_host = '127.0.0.1';
        this.storage_server_port = 5555;

    }

    // 0.0 启动 realtimeEngine 实时引擎模块
    start(port = 8080) {
        return new Promise((resolve, reject) => {
            try {
                /**
                 * ===== 1. 连接ble_server  =====
                 */
                this.connectTimeoutTimer = setTimeout(() => {
                    this.ble_server_connect();
                }, 1000); // 延迟 1000 毫秒（1秒）


                /**
                 * ===== 2. 启动realtimeEngine >>> index.html websocket广播服务器 =====
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
                 * ===== 3. 连接storage_server  =====
                 */
                this.storage_server_connect();


            } catch (error) {
                console.error('[realtimeEngine] 启动实时引擎失败:', error);
                reject(error);
            }
        });
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
                    
                    // 处理连接确认消息（与后端realtimeEngine的connection_established对应）
                    if (packet.type === 'emg') {
                        console.log(`[realtimeEngine] 来自ble_server的emg 大包，大包timestamp : ${packet.timestamp}`);
                        this.attributeEMGData(packet);
                        return;
                    }
                } catch (error) {
                    console.error('[realtimeEngine] ble_server的emg消息解析失败:', error);
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

    // 1.2 接受来自ble_server 的大包数据（5*32）数据，并即时广播出去
    attributeEMGData(emgData) {
        if (!this.isRunning) return;

        try {
            // 获取 raw_data，假设它是一个包含5个十六进制字符串的数组
            let rawData = emgData.raw_data;

            // 确保 rawData 是数组类型，且包含 5 组数据
            if (!Array.isArray(rawData)) {
                console.error("[realtimeEngine] rawData 不是一个数组");
                return;
            }

            // 确保是 5 组数据
            if (rawData.length !== 5) {
                console.error('[realtimeEngine] emg 数据组数不匹配，应该是 5 组');
                return;
            }

            // 统计小包数量 + 5
            this.emg_packet_count += rawData.length;
            this.emg_5_packets_count++;

            // 基于时间戳进行递增，每个包间隔 0.5ms
            //let timestamp = emgData.timestamp;  // 初始时间戳

            let timestamp_array = [emgData.timestamp,
                            emgData.timestamp + this.emg_interval * 1,
                            emgData.timestamp + this.emg_interval * 2,
                            emgData.timestamp + this.emg_interval * 3,
                            emgData.timestamp + this.emg_interval * 4];


            const dataPacket = {
                type: 'emg_data',
                data: {
                    big_bag_raw_data: rawData,  // [32byte, 32byte, 32byte, 32byte, 32byte]//大包的rawData[5]数组放入 big_bag_raw_data
                    timestamp: timestamp_array, // [time, time, time, time, time] // 计算好大包内的5组的时间戳
                    packetCount: this.emg_packet_count,
                    interval: null // 现在不需要interval，可以以后加
                }
            };

            // 广播出去
            this.broadcastToClients(dataPacket);
            console.log('realtimeEngine.js 发送一个大包，大包统计：小包统计：', this.emg_5_packets_count,this.emg_packet_count);

        } catch (error) {
            console.error('[realtimeEngine] 处理EMG数据时发生错误:', error);
        }
    }

    // 1.3 断线重连逻辑
    handleReconnect() {
        // 检查是否达到最大重连次数
        if (this.maxReconnectTimes > 0 && this.currentReconnectTimes >= this.maxReconnectTimes) {
        console.error(`[realtimeEngine] ❌ 已达到最大重连次数（${this.maxReconnectTimes}），停止重连`);
        return;
        }

        this.currentReconnectTimes++;
        console.log(`[realtimeEngine] 🔄 正在进行第 ${this.currentReconnectTimes} 次重连目标服务器...`);

        // 延迟重连（避免频繁连接）
        this.reconnectTimer = setTimeout(() => {
        this.connectTargetServer();
        }, this.reconnectInterval);
    }






    // 2.1 连接storage_server
    storage_server_connect() {
        try {
            this.storage_server_socket.connect(`tcp://${this.storage_server_host}:${this.storage_server_port}`);
            console.log(`已连接到 HDF5 存储服务：${this.storage_server_host}:${this.storage_server_port}`);
        } catch (err) {
            throw new Error(`连接失败：${err.message}`);
        }
    }

    /**
     * 发送指令到 Python 服务端（适配新版 API）
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
}

// 创建单例实例
const realtimeEngine = new RealtimeEngine();

module.exports = realtimeEngine;
