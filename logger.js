/**
 * logger.js - 日志管理模块
 * 
 * 功能：
 * 1. 自动创建 log 目录
 * 2. 日志文件大小限制（默认 20MB）
 * 3. 日志文件数量限制（默认 10 个）
 * 4. 自动轮询：超过大小创建新文件，超过数量删除最旧文件
 * 5. 同时输出到终端和文件
 */

const fs = require('fs');
const path = require('path');

class Logger {
    constructor(options = {}) {
        this.logDir = options.logDir || path.join(__dirname, 'log');
        this.maxFileSize = options.maxFileSize || 20 * 1024 * 1024;  // 默认 20MB
        this.maxFiles = options.maxFiles || 10;  // 默认保留 10 个文件
        this.filePrefix = options.filePrefix || 'server';
        
        this.currentLogFile = null;
        this.currentStream = null;
        this.currentFileSize = 0;
        
        // 保存原始的 console 方法
        this.originalLog = console.log;
        this.originalError = console.error;
        this.originalWarn = console.warn;
        
        this.init();
    }

    /**
     * 初始化日志系统
     */
    init() {
        // 创建 log 目录
        if (!fs.existsSync(this.logDir)) {
            fs.mkdirSync(this.logDir, { recursive: true });
        }

        // 清理旧日志文件（启动时检查）
        this.cleanOldLogs();

        // 创建或打开当前日志文件
        this.openLogFile();

        // 重写 console 方法
        this.overrideConsole();

        this.originalLog(`[Logger] 日志系统已启动，日志目录: ${this.logDir}`);
        this.originalLog(`[Logger] 单文件大小限制: ${(this.maxFileSize / 1024 / 1024).toFixed(1)}MB, 最大文件数: ${this.maxFiles}`);
    }

    /**
     * 生成日志文件名
     */
    generateFileName() {
        const now = new Date();
        const timestamp = now.toISOString()
            .replace(/[:.]/g, '-')
            .replace('T', '_')
            .slice(0, 19);
        return `${this.filePrefix}_${timestamp}.log`;
    }

    /**
     * 打开新的日志文件
     */
    openLogFile() {
        // 关闭之前的流
        if (this.currentStream) {
            this.currentStream.end();
        }

        this.currentLogFile = path.join(this.logDir, this.generateFileName());
        this.currentStream = fs.createWriteStream(this.currentLogFile, { flags: 'a' });
        this.currentFileSize = 0;

        // 如果文件已存在，获取其大小
        if (fs.existsSync(this.currentLogFile)) {
            const stat = fs.statSync(this.currentLogFile);
            this.currentFileSize = stat.size;
        }
    }

    /**
     * 写入日志
     */
    write(message) {
        const timestamp = new Date().toISOString();
        const logLine = `[${timestamp}] ${message}\n`;
        const lineSize = Buffer.byteLength(logLine, 'utf8');

        // 检查是否需要轮询
        if (this.currentFileSize + lineSize > this.maxFileSize) {
            this.rotate();
        }

        // 写入文件
        if (this.currentStream) {
            this.currentStream.write(logLine);
            this.currentFileSize += lineSize;
        }
    }

    /**
     * 日志轮询
     */
    rotate() {
        this.originalLog(`[Logger] 日志文件达到大小限制，正在轮询...`);
        
        // 打开新文件
        this.openLogFile();
        
        // 清理旧文件
        this.cleanOldLogs();
    }

    /**
     * 清理旧日志文件
     */
    cleanOldLogs() {
        try {
            const files = fs.readdirSync(this.logDir)
                .filter(f => f.startsWith(this.filePrefix) && f.endsWith('.log'))
                .map(f => ({
                    name: f,
                    path: path.join(this.logDir, f),
                    mtime: fs.statSync(path.join(this.logDir, f)).mtime.getTime()
                }))
                .sort((a, b) => b.mtime - a.mtime);  // 按修改时间降序

            // 删除超出数量限制的旧文件
            if (files.length > this.maxFiles) {
                const toDelete = files.slice(this.maxFiles);
                toDelete.forEach(file => {
                    fs.unlinkSync(file.path);
                    this.originalLog(`[Logger] 删除旧日志: ${file.name}`);
                });
            }
        } catch (err) {
            this.originalError(`[Logger] 清理旧日志失败: ${err.message}`);
        }
    }

    /**
     * 重写 console 方法
     */
    overrideConsole() {
        const self = this;

        console.log = function(...args) {
            const message = args.map(arg => 
                typeof arg === 'object' ? JSON.stringify(arg, null, 2) : String(arg)
            ).join(' ');
            
            self.write(message);
            self.originalLog.apply(console, args);
        };

        console.error = function(...args) {
            const message = '[ERROR] ' + args.map(arg => 
                typeof arg === 'object' ? JSON.stringify(arg, null, 2) : String(arg)
            ).join(' ');
            
            self.write(message);
            self.originalError.apply(console, args);
        };

        console.warn = function(...args) {
            const message = '[WARN] ' + args.map(arg => 
                typeof arg === 'object' ? JSON.stringify(arg, null, 2) : String(arg)
            ).join(' ');
            
            self.write(message);
            self.originalWarn.apply(console, args);
        };
    }

    /**
     * 关闭日志系统
     */
    close() {
        if (this.currentStream) {
            this.currentStream.end();
            this.currentStream = null;
        }
        
        // 恢复原始 console
        console.log = this.originalLog;
        console.error = this.originalError;
        console.warn = this.originalWarn;
    }
}

// 创建单例
let loggerInstance = null;

function initLogger(options) {
    if (!loggerInstance) {
        loggerInstance = new Logger(options);
    }
    return loggerInstance;
}

function getLogger() {
    return loggerInstance;
}

module.exports = {
    initLogger,
    getLogger,
    Logger
};
