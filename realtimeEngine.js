// realtimeEngine.js
const WebSocket = require('ws');
const EventEmitter = require('events');

class RealtimeEngine extends EventEmitter {
    constructor() {
        super();
        // 实时引擎websocket服务器，用于向各个模块，前端广播各自需要的数据
        this.wss = null;
        this.clients = new Set();
        this.isRunning = false;
        this.dataBuffer = [];
        this.maxBufferSize = 1000;
        this.broadcastInterval = null;

        // ===== 新增：Python WebSocket 客户端配置 =====
        this.ble_client = null; // 连接 Python 的 WebSocket 客户端实例
        this.ble_clientUrl = 'ws://localhost:8766'; // Python 服务端的 WebSocket 地址（替换为你的实际地址）
        this.reconnectInterval = 3000; // 重连间隔（3秒）
        this.maxReconnectTimes = 3;
        this.currentReconnectTimes = 0; // 当前重连次数
        this.reconnectTimer = null; // 重连计时器

        this.connectTimeoutTimer = null;
        this.emg16_packet_count=0;

    }

    // 启动实时引擎
    start(port = 8080) {
        return new Promise((resolve, reject) => {
            try {
                //启动realtimeEngine 广播服务器
                this.wss = new WebSocket.Server({ port });
                
                this.connectTimeoutTimer = setTimeout(() => {
                    this.ble_client_connect();
                }, 1000); // 延迟 1000 毫秒（1秒）

                this.wss.on('connection', (ws) => {
                    console.log('[realtimeEngine] 前端client连接已建立');
                    this.clients.add(ws);
                    
                    // 发送连接确认和当前状态
                    const connectMsg = JSON.stringify({
                        type: 'connection_established',
                        message: '实时数据连接已建立',
                        timestamp: Date.now()
                    });
                    // 打印连接确认消息
                    console.log(`[realtimeEngine] [${new Date().toISOString()}] 发送连接确认给前端:`, JSON.parse(connectMsg));
                    ws.send(connectMsg);


                    // 发送缓冲的最新数据
                    
                    //this.sendBufferedData(ws);

                    ws.on('close', () => {
                        console.log('[realtimeEngine] 前端WebSocket连接已关闭');
                        this.clients.delete(ws);
                    });

                    ws.on('error', (error) => {
                        console.error('[realtimeEngine] WebSocket错误:', error);
                        this.clients.delete(ws);
                    });
                });

                this.wss.on('listening', () => {
                    console.log(`实时引擎启动成功，WebSocket服务运行在端口 ${port}`);
                    this.isRunning = true;
                    
                    // 启动数据广播
                    //this.startDataBroadcast();
                    
                    resolve();
                });

                this.wss.on('error', (error) => {
                    console.error('启动WebSocket服务器失败:', error);
                    reject(error);
                });

                //启动realtimeEngine 的 ble_client  
                //realtimeEngine.ble_client_connect();

            } catch (error) {
                console.error('[realtimeEngine] 启动实时引擎失败:', error);
                reject(error);
            }
        });
    }


    // 接受来自ble_server数据，并即时广播出去
 receiveEMGData(emgData) {
    if (!this.isRunning) return;

    try {
        // 获取 raw_data，假设它是一个包含多个十六进制字符串的数组
        let rawData = emgData.raw_data;

        // 打印 rawData 的类型和内容
        //console.log("rawData type:", typeof rawData);  // 打印类型
        //console.log("Raw Data Array: ", rawData);  // 打印原始数据（数组）

        // 确保 rawData 是数组类型，且包含 5 组数据
        if (!Array.isArray(rawData)) {
            console.error("[realtimeEngine] rawData 不是一个数组");
            return;
        }

        //console.log("EMG Data Groups: ", rawData);  // 调试输出原始的5组数据

        // 确保是 5 组数据
        if (rawData.length !== 5) {
            console.error('[realtimeEngine] emg 数据组数不匹配，应该是 5 组');
            return;
        }

        // 统计包的数量（即32字节包的个数）
        const packetCount = rawData.length;

        // 基于时间戳进行递增，每个包间隔 0.5ms
        let timestamp = emgData.timestamp;  // 初始时间戳

        // 遍历每一组数据并构建对应的包
        rawData.forEach((groupData, index) => {
            // 解析 groupData：将每两个字符（1个字节）转化为一个 uint16_t（2字节）
            const channels = [];
            for (let i = 0; i < groupData.length; i += 4) {  // 每4个字符（即2个字节）为1个 uint16_t
                // 从 groupData 提取 4 个字符并转为整数
                const channelValue = parseInt(groupData.slice(i, i + 4), 16); // 每2字节转为 uint16_t
                channels.push(channelValue/1000);// /1000 模拟实际大小
            }

            //console.log("Parsed Channels: ", channels); // 输出解析后的通道数据

            // 构建数据包
            const dataPacket = {
                type: 'emg_data',
                data: {
                    channels: channels,  // 将解析后的 16 个通道数据放入 channels
                    timestamp: timestamp + index * 0.0005,  // 每组时间戳递增0.5ms
                    packetCount: packetCount,
                    interval: null // 现在不需要interval，可以以后加
                },
                serverTime: Date.now()  // 当前服务器时间
            };

            if(this.emg16_packet_count <= 10000)
            {                   // 广播给所有客户端
                this.broadcastToClients(dataPacket);
                this.emg16_packet_count++;
                console.log('realtimeEngine.js : emg16_package_count = ', this.emg16_packet_count);
            }


            

        });

    } catch (error) {
        console.error('[realtimeEngine] 处理EMG数据时发生错误:', error);
    }
}


    // 提供方法：作为client连接ble_python服务器
    ble_client_connect() {
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
                        //console.log(`[realtimeEngine] 来自ble_server的emg消息: ${packet.raw_data}`);
                        this.receiveEMGData(packet);
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


/**
   * 断线重连逻辑
   */
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


 

    // 添加到数据缓冲区
    addToBuffer(dataPacket) {
        this.dataBuffer.push(dataPacket);
        
        // 限制缓冲区大小
        if (this.dataBuffer.length > this.maxBufferSize) {
            this.dataBuffer.shift();
        }
    }

    // 广播数据给所有客户端
    broadcastToClients(dataPacket) {
        const message = JSON.stringify(dataPacket);
        // 打印广播数据（包含时间戳和数据详情）
        //console.log(`[${new Date().toISOString()}] 广播数据给所有前端 (客户端数量: ${this.clients.size}):`);
        //console.log(JSON.stringify(dataPacket, null, 2)); // 格式化输出，方便查看结构
        
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

    // 发送缓冲数据给新连接的客户端
    // sendBufferedData(ws) {
    //     if (this.dataBuffer.length === 0) return;

    //     // 发送最近的一些数据点（例如最后50个）
    //     const recentData = this.dataBuffer.slice(-50);
    //     //console.log(`[${new Date().toISOString()}] 向新连接客户端发送缓冲数据 (共 ${recentData.length} 条)`);
        
    //     recentData.forEach((dataPacket, index) => {
    //         if (ws.readyState === WebSocket.OPEN) {
    //             // 打印单条缓冲数据
    //             //console.log(`  缓冲数据 #${index + 1}:`, JSON.stringify(dataPacket, null, 2));
    //             ws.send(JSON.stringify(dataPacket));
    //         }
    //     });
    // }

    // 停止数据广播
    // stopDataBroadcast() {
    //     if (this.broadcastInterval) {
    //         clearInterval(this.broadcastInterval);
    //         this.broadcastInterval = null;
    //     }
    // }

    // 获取引擎状态
    getStatus() {
        return {
            isRunning: this.isRunning,
            clientCount: this.clients.size,
            bufferSize: this.dataBuffer.length,
            maxBufferSize: this.maxBufferSize,
            port: this.wss ? this.wss.address().port : null
        };
    }

    // 停止实时引擎
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
        if (this.wss) {
            const closeTimeout = setTimeout(() => {
                console.warn('服务器关闭超时，强制退出');
                resolve();
            }, 3000); // 3秒超时

            this.wss.close(() => {
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


    // 发送控制命令到前端, 未使用
    sendControlCommand(command, data = {}) {
        const commandPacket = {
            type: 'control_command',
            command: command,
            data: data,
            timestamp: Date.now()
        };

        this.broadcastToClients(commandPacket);
    }
}

// 创建单例实例
const realtimeEngine = new RealtimeEngine();

module.exports = realtimeEngine;
