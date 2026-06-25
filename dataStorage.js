/*
 dataStorage.js
 负责启动storage_server, 以及监控storage_server的传输信息（数据不接受，只接收一些统计信息，为前端提供api接口）

 修改记录：
 - 引入 paths.js 获取正确的存储路径
 - 启动 Python 时传入 --storage_dir 参数
 - 支持打包后使用 exe 或开发时使用 Python
*/

const { spawn } = require('child_process');
const EventEmitter = require('events');
const path = require('path');
const { PATHS } = require('./paths'); // [新增] 引入路径管理模块
const { getPythonCommand } = require('./pythonPath'); // [新增] Python路径解析

// 【修复 H-N6】添加 PYTHON_ENV 确保 Windows 下 UTF-8 编码正确
const PYTHON_ENV = {
    ...process.env,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1'
};

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

                // [关键修改] 获取外部可写的 storage 目录 (exe同级目录)
                const storageDir = PATHS.storage;
                console.log(`[dataStorage] 数据存储目标路径: ${storageDir}`);

                // 自动判断使用 Python 脚本还是打包后的 exe
                const { command, args } = getPythonCommand('storage_server', [
                    '--storage_dir',
                    storageDir
                ]);

                console.log(`[dataStorage] 启动命令: ${command} ${args.join(' ')}`);
                this.pythonProcess = spawn(command, args, { env: PYTHON_ENV });

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

    // 获取状态
    getStatus() {
        return {
            isRunning: this.pythonProcess !== null
        };
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