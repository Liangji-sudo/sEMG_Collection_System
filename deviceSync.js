/*
 deviceSync.js
 负责启动ble_server, 以及监控ble_server的传输信息（数据不接受，只接收一些统计信息，为前端提供api接口）
*/

const { spawn } = require('child_process');
const EventEmitter = require('events');
const realtimeEngine = require('./realtimeEngine');
const path = require('path');

class DeviceSync extends EventEmitter {
    constructor() {
        super();
        this.pythonProcess = null; // Python子进程
        this.packetCount = 0;
        this.lastTimestamp = 0;
        this.isConnected = false;
        this.startTime = null;
        this.dataRate = 0;
        this.emgData = Array(16).fill().map(() => []); // 16通道EMG数据缓存
        this.maxDataPoints = 200; // 每个通道显示的数据点数
        this.lastDataTime = Date.now();
        this.lastValues = Array(16).fill(0); // 最新的16通道值
        this.lastInterval = 0; // 最后的数据包间隔

        this.totalBytesReceived = 0;    // 累计接收的总字节数
        this.lastThroughputCheckTime = null;  // 上次计算吞吐量的时间
        this.lastBytesCount = 0;        // 上次计算时的总字节数
        this.currentThroughput = 0;     // 当前吞吐量（字节/秒）
    }

    // 初始化Python进程连接
    async initialize() {
        return new Promise((resolve, reject) => {
            try {
                console.log('[deviceSync] 正在启动ble_server......');
                
                // 启动Python子进程，连接固定设备（无需传参，脚本内已固定MAC）
                this.pythonProcess = spawn('python', [path.join(__dirname, 'ble_server.py')]);
                
                this.pythonProcess.on('spawn', () => {
                    console.log('[deviceSync] ble_server已启动');
                    this.isConnected = true;
                    this.startTime = Date.now();
                    resolve();
                });

               // 接收Python脚本的调试日志（stderr）
                this.pythonProcess.stderr.on('data', (data) => {
                    const log = data.toString().trim();
                    if (log) {
                        console.log(`${log}`);
                    }
                });


                this.pythonProcess.on('error', (error) => {
                    console.error('[deviceSync] ble_server发生错误:', error.message);
                    this.isConnected = false;
                    this.emit('error', error);
                    reject(error);
                });

                this.pythonProcess.on('close', (code) => {
                    console.log(`[deviceSync] ble_server已关闭，退出码: ${code}`);
                    this.isConnected = false;
                    this.emit('disconnected');
                });

            } catch (error) {
                console.error('[deviceSync] 启动ble_server失败:', error);
                reject(error);
            }
        });
    }

    // api: 获取ble_server的传输吞吐量
    getCurrentThroughput() {
        const currentTime = Date.now();
        
        if (!this.lastThroughputCheckTime) {
            this.lastThroughputCheckTime = currentTime;
            this.lastBytesCount = this.totalBytesReceived;
            return 0;
        }
        
        const timeDiffMs = currentTime - this.lastThroughputCheckTime;
        if (timeDiffMs <= 0) {
            return this.currentThroughput;
        }
        
        const bytesDiff = this.totalBytesReceived - this.lastBytesCount;
        const timeDiffSec = timeDiffMs / 1000;
        this.currentThroughput = bytesDiff / timeDiffSec;
        
        this.lastThroughputCheckTime = currentTime;
        this.lastBytesCount = this.totalBytesReceived;
        
        return this.currentThroughput;
    }

    // api: 获取模块状态
    getStatus() {
        return {
            isConnected: this.isConnected,
            dataCount: this.packetCount,
            currentRate: this.dataRate,
            lastTimestamp: this.lastTimestamp,
            lastInterval: this.lastInterval,
            emgData: this.lastValues,
            lastDataTime: this.lastDataTime,
            dataHistory: this.emgData.map(channel => channel.length)
        };
    }

    // 关闭连接
    async close() {
        return new Promise((resolve) => {
            if (this.pythonProcess) {
                this.pythonProcess.kill();
                this.pythonProcess = null;
                this.isConnected = false;
                console.log('[deviceSync] ble_server关闭');
                this.emit('disconnected');
                resolve();
            } else {
                console.log('[deviceSync] ble_server未启动，无需关闭');
                resolve();
            }
        });
    }

    // 重置模块状态
    reset() {
        this.packetCount = 0;
        this.lastTimestamp = 0;
        this.dataRate = 0;
        this.lastInterval = 0;
        this.emgData = Array(16).fill().map(() => []);
        this.lastValues = Array(16).fill(0);
        this.lastDataTime = Date.now();
        console.log('[deviceSync] deviceSync状态已重置');
    }

    // 检查连接状态
    checkConnection() {
        return this.isConnected && this.pythonProcess !== null;
    }

    // 获取连接信息
    getConnectionInfo() {
        return {
            isConnected: this.isConnected,
            source: '蓝牙设备',
            deviceMac: 'dc:b4:d9:1f:52:be',
            scriptName: 'scan-connect.py',
            dataFormat: 'JSON格式，包含5组16通道数据'
        };
    }
}

// 创建单例实例
const deviceSync = new DeviceSync();

deviceSync.on('error', (error) => {
    console.error('[deviceSync] deviceSync错误:', error.message);
});

deviceSync.on('disconnected', () => {
    console.log('[deviceSync] deviceSync已断开');
});

console.log('[deviceSync] deviceSync模块加载完成');

module.exports = deviceSync;