// realtimeEngine.js
const WebSocket = require('ws');
const EventEmitter = require('events');

class RealtimeEngine extends EventEmitter {
    constructor() {
        super();
        this.wss = null;
        this.clients = new Set();
        this.isRunning = false;
        this.dataBuffer = [];
        this.maxBufferSize = 1000;
        this.broadcastInterval = null;
    }

    // 启动实时引擎
    start(port = 8080) {
        return new Promise((resolve, reject) => {
            try {
                this.wss = new WebSocket.Server({ port });
                
                this.wss.on('connection', (ws) => {
                    console.log('前端WebSocket连接已建立');
                    this.clients.add(ws);
                    
                    // 发送连接确认和当前状态
                    const connectMsg = JSON.stringify({
                        type: 'connection_established',
                        message: '实时数据连接已建立',
                        timestamp: Date.now()
                    });
                    // 打印连接确认消息
                    console.log(`[${new Date().toISOString()}] 发送连接确认给前端:`, JSON.parse(connectMsg));
                    ws.send(connectMsg);

                    // 发送缓冲的最新数据
                    this.sendBufferedData(ws);

                    ws.on('close', () => {
                        console.log('前端WebSocket连接已关闭');
                        this.clients.delete(ws);
                    });

                    ws.on('error', (error) => {
                        console.error('WebSocket错误:', error);
                        this.clients.delete(ws);
                    });
                });

                this.wss.on('listening', () => {
                    console.log(`实时引擎启动成功，WebSocket服务运行在端口 ${port}`);
                    this.isRunning = true;
                    
                    // 启动数据广播
                    this.startDataBroadcast();
                    
                    resolve();
                });

                this.wss.on('error', (error) => {
                    console.error('启动WebSocket服务器失败:', error);
                    reject(error);
                });

            } catch (error) {
                console.error('启动实时引擎失败:', error);
                reject(error);
            }
        });
    }

    // 接收来自设备协同模块的EMG数据
    receiveEMGData(emgData) {
        if (!this.isRunning) return;

        // 构建数据包
        const dataPacket = {
            type: 'emg_data',
            data: {
                channels: emgData.channels,
                timestamp: emgData.timestamp,
                packetCount: emgData.packetCount,
                interval: emgData.interval
            },
            serverTime: Date.now()
        };

        // 添加到缓冲区
        this.addToBuffer(dataPacket);

        // 立即广播给所有客户端
        this.broadcastToClients(dataPacket);
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
                    console.error('发送数据到客户端失败:', error);
                    this.clients.delete(client);
                }
            }
        });
    }

    // 发送缓冲数据给新连接的客户端
    sendBufferedData(ws) {
        if (this.dataBuffer.length === 0) return;

        // 发送最近的一些数据点（例如最后50个）
        const recentData = this.dataBuffer.slice(-50);
        //console.log(`[${new Date().toISOString()}] 向新连接客户端发送缓冲数据 (共 ${recentData.length} 条)`);
        
        recentData.forEach((dataPacket, index) => {
            if (ws.readyState === WebSocket.OPEN) {
                // 打印单条缓冲数据
                //console.log(`  缓冲数据 #${index + 1}:`, JSON.stringify(dataPacket, null, 2));
                ws.send(JSON.stringify(dataPacket));
            }
        });
    }

    // 启动定时数据广播（如果需要批量发送）
    startDataBroadcast() {
        // 如果希望批量发送数据，可以启用这个定时器
        // this.broadcastInterval = setInterval(() => {
        //     if (this.dataBuffer.length > 0) {
        //         const latestData = this.dataBuffer[this.dataBuffer.length - 1];
        //         this.broadcastToClients(latestData);
        //     }
        // }, 50); // 每50ms发送一次
    }

    // 停止数据广播
    stopDataBroadcast() {
        if (this.broadcastInterval) {
            clearInterval(this.broadcastInterval);
            this.broadcastInterval = null;
        }
    }

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
            this.stopDataBroadcast();

            // 关闭所有客户端连接
            this.clients.forEach(client => {
                client.close();
            });
            this.clients.clear();

            // 关闭WebSocket服务器
            if (this.wss) {
                this.wss.close(() => {
                    console.log('实时引擎已停止');
                    resolve();
                });
            } else {
                resolve();
            }
        });
    }

    // 发送控制命令到前端
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
