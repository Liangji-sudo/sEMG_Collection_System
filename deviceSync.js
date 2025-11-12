const { SerialPort } = require('serialport');

class DeviceSync {
    constructor() {
        this.port = null;
        this.packetCount = 0;
        this.lastTimestamp = 0;
        this.isConnected = false;
        this.startTime = null;
        this.dataRate = 0;
    }

    // 初始化串口连接
    async initialize() {
        return new Promise((resolve, reject) => {
            try {
                this.port = new SerialPort({
                    path: '/dev/pts/12',
                    baudRate: 921600,
                    dataBits: 8,
                    stopBits: 1,
                    parity: 'none'
                });

                // 绑定事件处理
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
                    reject(error);
                });

            } catch (error) {
                reject(error);
            }
        });
    }

    // 处理接收到的数据
    handleData(data) {
        this.packetCount++;
        
        // 获取高精度时间戳
        const timestamp = this.getHighPrecisionTimestamp();
        
        // 计算与上一包的时间间隔（毫秒）
        const interval = this.lastTimestamp > 0 ? 
            (parseFloat(timestamp) - parseFloat(this.lastTimestamp)) * 1000 : 0;
        
        // 计算数据速率
        this.updateDataRate();
        
        // 打印信息
        this.printDataInfo(this.packetCount, timestamp, interval, data.toString().trim());
        
        // 更新上次时间戳
        this.lastTimestamp = timestamp;
    }

    // 获取高精度时间戳（秒，小数点后8位）
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
            this.dataRate = this.packetCount / elapsedSeconds;
        }
    }

    // 打印数据信息
    printDataInfo(count, timestamp, interval, data) {
        // 简洁格式：计数 时间戳 间隔(ms) 数据
        console.log(`${count.toString().padStart(6)} ${timestamp} ${interval.toFixed(3).padStart(8)}ms ${data}`);
        
        // 每100个包打印一次分隔线
        if (count % 100 === 0) {
            console.log('-'.repeat(80));
        }
    }

    // 获取模块状态
    getStatus() {
        return {
            isConnected: this.isConnected,
            dataCount: this.packetCount,
            currentRate: this.dataRate,
            lastTimestamp: this.lastTimestamp
        };
    }

    // 关闭连接
    async close() {
        return new Promise((resolve) => {
            if (this.port && this.port.isOpen) {
                this.port.close((error) => {
                    if (!error) {
                        console.log('设备协同模块已关闭');
                        this.isConnected = false;
                    }
                    resolve();
                });
            } else {
                resolve();
            }
        });
    }
}

// 创建单例实例
const deviceSync = new DeviceSync();

module.exports = deviceSync;