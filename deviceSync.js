// deviceSync.js
const { SerialPort } = require('serialport');
const EventEmitter = require('events');
const realtimeEngine = require('./realtimeEngine');

class DeviceSync extends EventEmitter {
    constructor() {
        super();
        this.port = null;
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

    // 初始化串口连接
    async initialize() {
        return new Promise((resolve, reject) => {
            try {
                console.log('正在初始化串口连接...');
                
                this.port = new SerialPort({
                    path: '/dev/pts/12',
                    baudRate: 921600,
                    dataBits: 8,
                    stopBits: 1,
                    parity: 'none'
                }, (err) => {
                    if (err) {
                        console.error('创建串口实例失败:', err.message);
                        reject(err);
                    }
                });

                this.port.on('open', () => {
                    console.log('虚拟串口 /dev/pts/12 已打开（921600波特率）');
                    this.isConnected = true;
                    this.startTime = Date.now();
                    resolve();
                });

                this.port.on('data', (data) => {
                    this.handleData(data);
                });

                this.port.on('error', (error) => {
                    console.error('串口错误:', error.message);
                    this.isConnected = false;
                    this.emit('error', error);
                });

                this.port.on('close', () => {
                    console.log('串口连接已关闭');
                    this.isConnected = false;
                    this.emit('disconnected');
                });

            } catch (error) {
                console.error('初始化串口时发生错误:', error);
                reject(error);
            }
        });
    }

    // 处理接收到的数据
    handleData(data) {
        try {
            this.packetCount++;
            this.totalBytesReceived += data.length;
            // 获取高精度时间戳
            const timestamp = this.getHighPrecisionTimestamp();
            
            // 计算与上一包的时间间隔（毫秒）
            const interval = this.lastTimestamp > 0 ? 
                (parseFloat(timestamp) - parseFloat(this.lastTimestamp)) * 1000 : 0;
            this.lastInterval = interval;
            
            // 解析EMG数据
            const dataString = data.toString();
            const emgValues = this.parseEMGData(dataString);
            
            if (emgValues && emgValues.length === 16) {
                // 更新最新的通道值
                this.lastValues = emgValues;
                
                // 更新EMG数据缓存
                this.updateEMGData(emgValues, timestamp);
                
                // 构建数据包
                const emgDataPacket = {
                    channels: emgValues,
                    timestamp: timestamp,
                    packetCount: this.packetCount,
                    interval: interval,
                    rawData: dataString
                };
                
                // 触发事件（保持向后兼容）
                this.emit('emgData', emgDataPacket);
                
                // 发送到实时引擎
                realtimeEngine.receiveEMGData(emgDataPacket);
                
                // 打印数据包信息
                //console.log('handleData = ',dataString,timestamp, this.packetCount);
                if (this.packetCount % 100 === 0) {
                    this.printDataInfo();
                }
            }
            
            // 计算数据速率
            this.updateDataRate();
            
            // 更新上次时间戳
            this.lastTimestamp = timestamp;
            this.lastDataTime = Date.now();
            
        } catch (error) {
            console.error('处理数据时发生错误:', error);
            this.emit('error', error);
        }
    }


        // 新增：计算并返回当前每秒传输的数据量（字节/秒）
    getCurrentThroughput() {
        const currentTime = Date.now();
        
        // 首次调用时初始化基准值
        if (!this.lastThroughputCheckTime) {
            this.lastThroughputCheckTime = currentTime;
            this.lastBytesCount = this.totalBytesReceived;
            return 0;
        }
        
        // 计算时间差（毫秒）
        const timeDiffMs = currentTime - this.lastThroughputCheckTime;
        if (timeDiffMs <= 0) {
            return this.currentThroughput; // 避免除以零
        }
        
        // 计算字节差和时间差（转换为秒）
        const bytesDiff = this.totalBytesReceived - this.lastBytesCount;
        const timeDiffSec = timeDiffMs / 1000;
        
        // 计算吞吐量（字节/秒）
        this.currentThroughput = bytesDiff / timeDiffSec;
        
        // 更新基准值，用于下次计算
        this.lastThroughputCheckTime = currentTime;
        this.lastBytesCount = this.totalBytesReceived;
        
        return this.currentThroughput;
    }


    // 解析EMG数据包格式
    parseEMGData(dataString) {
        try {
            const cleanString = dataString.trim();
            
            // 空数据检查
            if (cleanString.length === 0) {
                return null;
            }
            
            // 处理多行数据
            if (cleanString.includes('\n')) {
                const lines = cleanString.split('\n').filter(line => line.trim().length > 0);
                for (let i = lines.length - 1; i >= 0; i--) {
                    const values = this.parseSingleLine(lines[i]);
                    if (values && values.length === 16) {
                        return values;
                    }
                }
                return null;
            }
            
            // 处理单行数据
            return this.parseSingleLine(cleanString);
            
        } catch (error) {
            console.error('解析EMG数据失败:', error);
            return null;
        }
    }

    // 解析单行数据
    parseSingleLine(line) {
        const cleanLine = line.trim();
        
        // CSV格式
        if (cleanLine.includes(',')) {
            const values = cleanLine.split(',').map(val => {
                const num = parseFloat(val.trim());
                return isNaN(num) ? 0 : num;
            });
            return values.length === 16 ? values : null;
        }
        
        // 空格分隔格式
        if (cleanLine.includes(' ')) {
            const values = cleanLine.split(/\s+/).map(val => {
                const num = parseFloat(val.trim());
                return isNaN(num) ? 0 : num;
            });
            return values.length === 16 ? values : null;
        }
        
        return null;
    }

    // 更新EMG数据缓存
    updateEMGData(values, timestamp) {
        const currentTime = Date.now();
        
        for (let i = 0; i < values.length; i++) {
            // 添加新数据点
            this.emgData[i].push({
                value: values[i],
                timestamp: currentTime
            });
            
            // 限制数据点数
            if (this.emgData[i].length > this.maxDataPoints) {
                this.emgData[i].shift();
            }
        }
    }

    // 获取最新的EMG数据
    getLatestEMGData() {
        return this.lastValues;
    }

    // 获取EMG数据历史
    getEMGDataHistory() {
        return this.emgData;
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
        //console.log(`📦 数据包 #${this.packetCount.toString().padStart(6)} | 间隔: ${this.lastInterval.toFixed(3).padStart(8)}ms | 速率: ${this.dataRate.toFixed(2).padStart(6)} 包/秒`);
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
            if (this.port && this.port.isOpen) {
                this.port.close((error) => {
                    if (error) {
                        console.error('关闭串口时发生错误:', error);
                        this.emit('error', error);
                    } else {
                        console.log('设备协同模块已关闭');
                        this.isConnected = false;
                        this.emit('disconnected');
                    }
                    resolve();
                });
            } else {
                console.log('串口未打开，无需关闭');
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
        return this.isConnected && this.port && this.port.isOpen;
    }

    // 获取连接信息
    getConnectionInfo() {
        return {
            isConnected: this.isConnected,
            portPath: '/dev/pts/12',
            baudRate: 921600,
            dataBits: 8,
            stopBits: 1,
            parity: 'none'
        };
    }
}

// 创建单例实例
const deviceSync = new DeviceSync();

// 错误处理事件
deviceSync.on('error', (error) => {
    console.error('设备协同模块错误:', error.message);
});

deviceSync.on('disconnected', () => {
    console.log('设备连接已断开');
});

// 模块信息
console.log('DeviceSync模块加载完成');
console.log('支持16通道EMG数据实时采集');
console.log('串口配置: /dev/pts/12 @ 921600 baud');

module.exports = deviceSync;
