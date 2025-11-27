// deviceSync.js
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
                console.log('正在启动蓝牙数据脚本...');
                
                // 启动Python子进程，连接固定设备
                this.pythonProcess = spawn('python', [path.join(__dirname, 'scan-connect.py')]);
                
                this.pythonProcess.on('spawn', () => {
                    console.log('蓝牙数据脚本已启动');
                    this.isConnected = true;
                    this.startTime = Date.now();
                    resolve();
                });

                // 接收Python脚本输出的数据
                this.pythonProcess.stdout.on('data', (data) => {
                    this.handleData(data);
                });

                // 处理Python脚本错误输出
                this.pythonProcess.stderr.on('data', (data) => {
                    console.error('Python脚本错误:', data.toString());
                });

                this.pythonProcess.on('error', (error) => {
                    console.error('Python进程错误:', error.message);
                    this.isConnected = false;
                    this.emit('error', error);
                    reject(error);
                });

                this.pythonProcess.on('close', (code) => {
                    console.log(`Python进程已退出，退出码: ${code}`);
                    this.isConnected = false;
                    this.emit('disconnected');
                });

            } catch (error) {
                console.error('初始化Python进程时发生错误:', error);
                reject(error);
            }
        });
    }

    // 处理接收到的数据（更新为解析JSON格式）
    handleData(data) {
        try {
            this.packetCount++;
            this.totalBytesReceived += data.length;
            const timestamp = this.getHighPrecisionTimestamp();
            const interval = this.lastTimestamp > 0 ? 
                (parseFloat(timestamp) - parseFloat(this.lastTimestamp)) * 1000 : 0;
            this.lastInterval = interval;
            
            const dataString = data.toString().trim();
            if (!dataString) return;
            
            // 解析JSON数据
            const emgData = JSON.parse(dataString);
            
            // 检查是否为emg类型且包含frames数据
            if (emgData.type === 'emg' && emgData.frames && emgData.frames.length > 0) {
                // 取第一组数据并转换（除以1e9）
                const firstFrame = emgData.frames[0];
                const emgValues = firstFrame.map(value => value / 1e9);
                
                if (emgValues.length === 16) {
                    this.lastValues = emgValues;
                    this.updateEMGData(emgValues, timestamp);
                    
                    const emgDataPacket = {
                        channels: emgValues,
                        timestamp: timestamp,
                        packetCount: this.packetCount,
                        interval: interval
                    };
                    
                    realtimeEngine.receiveEMGData(emgDataPacket);
                    
                    if (this.packetCount % 100 === 0) {
                        this.printDataInfo();
                    }
                }
            }
            
            this.updateDataRate();
            this.lastTimestamp = timestamp;
            this.lastDataTime = Date.now();
            
        } catch (error) {
            console.error('处理数据时发生错误:', error);
            this.emit('error', error);
        }
    }

    // 计算当前吞吐量
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

    // 更新EMG数据缓存
    updateEMGData(values, timestamp) {
        const currentTime = Date.now();
        
        for (let i = 0; i < values.length; i++) {
            this.emgData[i].push({
                value: values[i],
                timestamp: currentTime
            });
            
            if (this.emgData[i].length > this.maxDataPoints) {
                this.emgData[i].shift();
            }
        }
    }

    // 获取高精度时间戳
    getHighPrecisionTimestamp() {
        const now = process.hrtime();
        const seconds = now[0];
        const nanoseconds = now[1];
        const fractionalSeconds = (nanoseconds / 1e9).toFixed(8);
        return `${seconds}.${fractionalSeconds.split('.')[1]}`;
    }

    // 更新数据速率
    updateDataRate() {
        if (this.startTime) {
            const elapsedSeconds = (Date.now() - this.startTime) / 1000;
            this.dataRate = elapsedSeconds > 0 ? this.packetCount / elapsedSeconds : 0;
        }
    }

    // 打印数据信息
    printDataInfo() {
        console.log(`📦 数据包 #${this.packetCount.toString().padStart(6)} | 间隔: ${this.lastInterval.toFixed(3).padStart(8)}ms | 速率: ${this.dataRate.toFixed(2).padStart(6)} 包/秒`);
    }

    // 获取模块状态
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
                console.log('【蓝牙数据进程已关闭】');
                this.emit('disconnected');
                resolve();
            } else {
                console.log('蓝牙进程未启动，无需关闭');
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
        console.log('设备协同模块状态已重置');
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
    console.error('设备协同模块错误:', error.message);
});

deviceSync.on('disconnected', () => {
    console.log('设备连接已断开');
});

console.log('DeviceSync模块加载完成');
console.log('支持16通道EMG蓝牙数据实时接收');
console.log('连接设备MAC: dc:b4:d9:1f:52:be');

module.exports = deviceSync;