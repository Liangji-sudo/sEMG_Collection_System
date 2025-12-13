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

    /**
     * 获取程序运行所在磁盘的剩余容量（GB）和剩余占比（%）
     * @returns {Promise<{freeGB: number, freePercent: number, totalGB: number}|string>} 磁盘信息/错误提示
     */
    async getStorageVolumeInfo() {
        try {
            // 仅支持 Node.js 环境（浏览器无权限）
            if (typeof process === 'undefined' || !process.versions?.node) {
                return "仅支持 Node.js 环境获取磁盘信息";
            }

            const fs = require('fs').promises;
            const path = require('path');

            // 获取程序当前运行目录（关键：以此目录定位所在磁盘）
            const currentDir = process.cwd();
            // Windows: 取盘符根路径（如 D:\），Linux/macOS: 取根目录 /
            const diskPath = process.platform === 'win32' 
                ? path.parse(currentDir).root 
                : '/';

            // 获取磁盘分区的容量信息（核心：Node.js v22 原生支持）
            const stat = await fs.statfs(diskPath);
            const GB_UNIT = 1024 * 1024 * 1024; // 1GB = 1024³ 字节

            // 计算总容量、剩余容量（字节 → GB，保留2位小数）
            const totalGB = parseFloat((stat.blocks * stat.bsize / GB_UNIT).toFixed(2));
            const freeGB = parseFloat((stat.bavail * stat.bsize / GB_UNIT).toFixed(2));
            // 计算剩余占比（保留1位小数）
            const freePercent = parseFloat(((stat.bavail / stat.blocks) * 100).toFixed(1));

            // 返回核心信息：剩余容量、剩余占比、总容量
            //console.log(`${freeGB}, ${freePercent}`)
            return {
                freeGB,
                freePercent,
                totalGB
            };

        } catch (error) {
            console.error("获取磁盘信息失败：", error);
            return `获取失败：${error.message}`;
        }
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