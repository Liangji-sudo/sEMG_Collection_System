/*
 dataStorage.js
 负责启动storage_server, 以及监控storage_server的传输信息（数据不接受，只接收一些统计信息，为前端提供api接口）
*/

const { spawn } = require('child_process');
const EventEmitter = require('events');

class DataStorage extends EventEmitter {
    constructor() {
        super();
        this.pythonProcess = null; // Python子进程
    }

    // 初始化Python进程连接
    async initialize() {
        return new Promise((resolve, reject) => {
            try {
                console.log('[dataStorage] 正在启动storage_server......');
                
                // 启动Python子进程，连接固定设备（无需传参，脚本内已固定MAC）
                this.pythonProcess = spawn('python', ['storage_server.py']);
                
                this.pythonProcess.on('spawn', () => {
                    console.log('[dataStorage] storage_server已启动');
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
                    console.error('[dataStorage] storage_server发生错误:', error.message);
                    this.emit('error', error);
                    reject(error);
                });

                this.pythonProcess.on('close', (code) => {
                    console.log(`[dataStorage] storage_server已关闭，退出码: ${code}`);
                    this.emit('disconnected');
                });

            } catch (error) {
                console.error('[dataStorage] 启动storage_server失败:', error);
                reject(error);
            }
        });
    }

    
    // 关闭连接
    async close() {
        return new Promise((resolve) => {
            if (this.pythonProcess) {
                this.pythonProcess.kill();
                this.pythonProcess = null;
                console.log('[deviceSync] ble_server关闭');
                this.emit('disconnected');
                resolve();
            } else {
                console.log('[deviceSync] ble_server未启动，无需关闭');
                resolve();
            }
        });
    }

}

// 创建单例实例
const dataStorage = new DataStorage();

dataStorage.on('error', (error) => {
    console.error('[dataStorage] dataStorage错误:', error.message);
});

dataStorage.on('disconnected', () => {
    console.log('[dataStorage] dataStorage已断开');
});

console.log('[dataStorage] dataStorage模块加载完成');

module.exports = dataStorage;