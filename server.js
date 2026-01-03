/*
server.js
负责启动采集模式的所有模块，包括（deviceSync, ble_server, realtimeEngine, taskManager, storage）
*/ 

// ===================== 路径管理（必须最先执行）=====================
const { PATHS, isPackaged } = require('./paths');

// ===================== 日志系统初始化 =====================
const { initLogger } = require('./logger');
const logger = initLogger({
    logDir: PATHS.log,               // 使用统一的日志目录
    maxFileSize: 20 * 1024 * 1024,   // 20MB
    maxFiles: 10,                     // 最多保留 10 个日志文件
    filePrefix: 'server'              // 日志文件前缀
});

// ===================== 其他模块引入 =====================
const express = require('express');
const cors = require('cors');
const path = require('path');
const fs = require('fs');
const app = express();
const PORT = process.env.PORT || 3000;

// 引入设备协同模块
const deviceSync = require('./deviceSync');

// 引入实时引擎模块
const realtimeEngine = require('./realtimeEngine');

// 引入设备协同模块
const dataStorage = require('./dataStorage');


// 中间件配置， 用于给前端获取数据的接口
app.use(cors());
app.use(express.json());
app.use(express.static(PATHS.public));


// ===================== Storage 文件列表 API =====================
app.get('/api/storage/files', (req, res) => {
    const storageDir = PATHS.storage;
    
    if (!fs.existsSync(storageDir)) {
        return res.json({ success: false, error: 'storage 目录不存在', files: [] });
    }

    const files = [];
    
    function readDirRecursive(dir, relativePath = '') {
        const items = fs.readdirSync(dir);
        for (const item of items) {
            const fullPath = path.join(dir, item);
            const relPath = relativePath ? `${relativePath}/${item}` : item;
            const stat = fs.statSync(fullPath);
            
            if (stat.isDirectory()) {
                readDirRecursive(fullPath, relPath);
            } else if (item.endsWith('.h5') || item.endsWith('.hdf5')) {
                files.push({
                    name: item,
                    path: relPath,
                    size: stat.size,
                    lastModified: stat.mtimeMs
                });
            }
        }
    }
    
    try {
        readDirRecursive(storageDir);
        res.json({ success: true, files: files, count: files.length });
    } catch (err) {
        res.json({ success: false, error: err.message, files: [] });
    }
});

// ===================== Config 配置文件列表 API =====================
app.get('/api/config/files', (req, res) => {
    const configDir = PATHS.config;
    
    if (!fs.existsSync(configDir)) {
        return res.json({ success: false, error: 'config 目录不存在', files: [] });
    }

    try {
        const items = fs.readdirSync(configDir);
        const files = items
            .filter(item => item.endsWith('.json'))
            .map(item => {
                const fullPath = path.join(configDir, item);
                const stat = fs.statSync(fullPath);
                return {
                    name: item,
                    size: stat.size,
                    lastModified: stat.mtimeMs
                };
            });
        
        res.json({ success: true, files: files, count: files.length });
    } catch (err) {
        res.json({ success: false, error: err.message, files: [] });
    }
});

// ===================== 读取单个配置文件内容 API =====================
app.get('/api/config/load/:filename', (req, res) => {
    const { filename } = req.params;
    const configPath = path.join(PATHS.config, filename);
    
    // 安全检查：防止路径遍历攻击
    if (filename.includes('..') || filename.includes('/') || filename.includes('\\')) {
        return res.json({ success: false, error: '无效的文件名' });
    }
    
    if (!fs.existsSync(configPath)) {
        return res.json({ success: false, error: '配置文件不存在' });
    }

    try {
        const content = fs.readFileSync(configPath, 'utf-8');
        const config = JSON.parse(content);
        res.json({ success: true, config: config, filename: filename });
    } catch (err) {
        res.json({ success: false, error: '读取配置文件失败: ' + err.message });
    }
});

// ===================== 接收前端按钮点击的POST请求 =====================
app.post('/button-click', (req, res) => {
    // 获取前端传递的按钮名称
    const buttonName = req.body.buttonName;
    // 后端打印按钮名称
    //console.log(`收到按钮点击：${buttonName}`);
    realtimeEngine.taskManager_get_command(buttonName);
    // 向前端返回成功响应
    res.json({ 
        code: 0, 
        msg: `成功接收：${buttonName}`,
        data: { buttonName }
    });
});

// API路由 - 获取设备状态
app.get('/api/device-status', (req, res) => {
    // 获取设备协同模块状态
    const syncStatus = deviceSync.getStatus();
    
    // 原有的模拟数据
    const bluetoothDb = Math.floor(Math.random() * 30) - 70;
    const bluetoothStrength = Math.max(0, Math.min(100, 100 - (bluetoothDb + 70) * 3.33));

    // 设备协同模块提供的传输数据量
    const throughput = (deviceSync.getCurrentThroughput()/1000).toFixed(4);
    const throughputPercent = throughput/10;

    res.json({
        // 添加设备协同模块状态
        deviceSync: {
            connected: syncStatus.isConnected,
            dataPackets: syncStatus.dataCount,
            dataRate: syncStatus.currentRate.toFixed(2),
            lastTimestamp: syncStatus.lastTimestamp,
            emgData: syncStatus.emgData || Array(16).fill(0)
        }
    });
});


// 文件列表API， 模拟数据 
// TODO
app.get('/api/files', (req, res) => {
    const files = [
        {
            name: 'data_20251107_1430.hdf5',
            createTime: '2025-11-07 14:30',
            size: '128.5 MB',
            semgChannels: 16,
            imuChannels: 9
        },
        {
            name: 'data_20251107_1500.hdf5',
            createTime: '2025-11-07 15:00',
            size: '135.2 MB',
            semgChannels: 16,
            imuChannels: 9
        },
        {
            name: 'data_20251107_1530.hdf5',
            createTime: '2025-11-07 15:30',
            size: '142.8 MB',
            semgChannels: 16,
            imuChannels: 9
        }
    ];
    res.json(files);
});

// 存储空间状态 
app.get('/api/storage-volume', (req, res) => {
    (async () => {
        // 接收函数返回值（核心赋值语句）
        const diskInfo = await deviceSync.getStorageVolumeInfo();

        // 判断是否获取成功
        if (typeof diskInfo === 'object' && diskInfo !== null) {
            // 直接赋值使用
            const freeGB = diskInfo.freeGB;
            const freePercent = diskInfo.freePercent;

            res.json({
                storage: {
                    free_Percent: freePercent,
                    volume: freeGB
                }
            });
        } else {
            // 打印错误信息
            //console.log(diskInfo);
            res.json({
                storage: {
                    free_Percent: 0,
                    volume: 0
                }
            });
        }
    })();
});



// 文件预览API， 模拟数据
// TODO
app.get('/api/preview-file/:filename', (req, res) => {
    const { filename } = req.params;
    
    // 生成模拟预览数据
    res.json({
        filename,
        createTime: '2025-11-07 14:30:22',
        size: '128.5 MB',
        dataStats: {
            semgRange: '±2.56 mV',
            imuAccRange: '±1.25 g',
            imuGyroRange: '±15.3 °/s',
            completeness: '100%'
        },
        semgData: [
            Array(100).fill().map(() => Math.random() * 5 - 2.5),
            Array(100).fill().map(() => Math.random() * 5 - 2.5),
            Array(100).fill().map(() => Math.random() * 5 - 2.5),
            Array(100).fill().map(() => Math.random() * 5 - 2.5)
        ],
        imuData: [
            Array(100).fill().map(() => Math.random() * 2 - 1),
            Array(100).fill().map(() => Math.random() * 2 - 1),
            Array(100).fill().map(() => Math.random() * 2 - 1),
            Array(100).fill().map(() => Math.random() * 20 - 10)
        ]
    });
});


// 所有路由都指向index.html（支持前端路由）
app.get('*', (req, res) => {
    res.sendFile(path.join(PATHS.public, 'index.html'));
});


// 自动打开浏览器函数
function openBrowser() {
    try {
        const { exec } = require('child_process');
        const url = `http://localhost:${PORT}`;
        
        switch (process.platform) {
            case 'win32':
                exec(`start ${url}`);
                break;
            case 'darwin':
                exec(`open ${url}`);
                break;
            case 'linux':
                exec(`xdg-open ${url}`);
                break;
            default:
                console.log(`请手动打开浏览器访问: ${url}`);
        }
    } catch (error) {
        console.log('自动打开浏览器失败，请手动访问');
    }
}

// 优雅关闭处理
function setupGracefulShutdown() {
    const shutdown = async (signal) => {
        console.log(`\n收到 ${signal} 信号，正在关闭服务器...`);
        
        try {
            await deviceSync.close();
            await realtimeEngine.stop();
            await dataStorage.close();
            
            // 关闭日志系统
            if (logger) {
                logger.close();
            }
            
            console.log('服务器关闭完成');
            process.exit(0);
        } catch (error) {
            console.error('关闭过程中发生错误:', error);
            process.exit(1);
        }
    };

    process.on('SIGINT', () => shutdown('SIGINT'));
    process.on('SIGTERM', () => shutdown('SIGTERM'));
}

// 启动服务器
async function startServer() {
    try {


        // 启动realtimeEngine模块
        await realtimeEngine.start(8080);
        console.log('[server.js] realtimeEngine 启动成功');
        

        // 启动deviceSync模块（deviceSync启动ble_server模块）
        await deviceSync.initialize();
        console.log('[server.js] deviceSync 启动成功');

        // 启动dataStorage模块(dataStorage模块启动storage_server模块)
        await dataStorage.initialize();
        console.log('[server.js] dataStorage 启动成功');
        
        // 启动HTTP服务器
        const server = app.listen(PORT, () => {
            console.log(`数据采集系统已启动，访问地址：http://localhost:${PORT}`);
            //console.log('EMG数据API: http://localhost:' + PORT + '/api/emg-data');
            console.log('设备状态API: http://localhost:' + PORT + '/api/device-status');
            
            //仅在非 Electron 环境下自动打开浏览器
            if (!process.env.ELECTRON_MODE) {
            openBrowser(); // 非 Electron 启动时（如 npm start 单独运行服务）才打开浏览器
            }
        });
        
        return server;
        
    } catch (error) {
        console.error('服务器启动失败:', error);
        process.exit(1);
    }
}

// 启动服务器
setupGracefulShutdown();
startServer().then(server => {
    console.log('服务器启动完成');
}).catch(error => {
    console.error('服务器启动失败:', error);
});
