/*
 dataStorage.js
 负责启动storage_server, 以及监控storage_server的传输信息（数据不接受，只接收一些统计信息，为前端提供api接口）
 
 修改记录：
 - 引入 paths.js 获取正确的存储路径
 - 启动 Python 时传入 --storage_dir 参数
*/

const { spawn } = require('child_process');
const EventEmitter = require('events');
const path = require('path');
const { PATHS } = require('./paths'); // [新增] 引入路径管理模块

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
                
                // [关键修改] 获取 Python 脚本的绝对路径
                // 在打包后，脚本位于 resources/app/ 下，与 dataStorage.js 同级
                const scriptPath = path.join(__dirname, 'storage_server.py');
                
                // [关键修改] 获取外部可写的 storage 目录 (exe同级目录)
                const storageDir = PATHS.storage;

                console.log(`[dataStorage] Python脚本路径: ${scriptPath}`);
                console.log(`[dataStorage] 数据存储目标路径: ${storageDir}`);

                // [关键修改] 启动参数：脚本路径 + storage路径参数
                this.pythonProcess = spawn('python', [
                    scriptPath, 
                    '--storage_dir', 
                    storageDir
                ]);
                
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

                // 接收标准输出（如果有）
                this.pythonProcess.stdout.on('data', (data) => {
                    const log = data.toString().trim();
                    if (log) {
                         console.log(`[Python STDOUT] ${log}`);
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
                console.log('[dataStorage] storage_server关闭');
                this.emit('disconnected');
                resolve();
            } else {
                console.log('[dataStorage] storage_server未启动，无需关闭');
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