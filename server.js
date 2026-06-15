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
const { spawn } = require('child_process');
const app = express();
const PORT = process.env.PORT || 3000;

// 引入设备协同模块
const deviceSync = require('./deviceSync');

// 引入实时引擎模块
const realtimeEngine = require('./realtimeEngine');

// 引入数据存储模块
const dataStorage = require('./dataStorage');

// 【新增】Python 环境配置（与 deviceSync 一致）
const { getPythonCommand } = require('./pythonPath');
const PYTHON_ENV = {
    ...process.env,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1'
};

// 【新增】Camera Server 进程管理
let cameraServerProcess = null;
const CAMERA_SERVER_SCRIPT = 'camera_server';


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

// ===================== 保存配置文件 API =====================
app.post('/api/config/save', (req, res) => {
    const { filename, config } = req.body;
    
    if (!filename || !config) {
        return res.json({ success: false, error: '缺少文件名或配置内容' });
    }
    
    // 安全检查：防止路径遍历攻击
    if (filename.includes('..') || filename.includes('/') || filename.includes('\\')) {
        return res.json({ success: false, error: '无效的文件名' });
    }
    
    // 确保文件名以.json结尾
    const safeFilename = filename.endsWith('.json') ? filename : filename + '.json';
    const configPath = path.join(PATHS.config, safeFilename);
    
    // 确保config目录存在
    if (!fs.existsSync(PATHS.config)) {
        try {
            fs.mkdirSync(PATHS.config, { recursive: true });
        } catch (err) {
            return res.json({ success: false, error: '创建配置目录失败: ' + err.message });
        }
    }
    
    try {
        const content = JSON.stringify(config, null, 2);
        fs.writeFileSync(configPath, content, 'utf-8');
        console.log(`[server.js] 配置文件已保存: ${safeFilename}`);
        res.json({ success: true, filename: safeFilename, message: '配置保存成功' });
    } catch (err) {
        res.json({ success: false, error: '保存配置文件失败: ' + err.message });
    }
});

// ===================== 删除配置文件 API =====================
app.delete('/api/config/delete/:filename', (req, res) => {
    const { filename } = req.params;
    
    // 安全检查：防止路径遍历攻击
    if (filename.includes('..') || filename.includes('/') || filename.includes('\\')) {
        return res.json({ success: false, error: '无效的文件名' });
    }
    
    const configPath = path.join(PATHS.config, filename);
    
    if (!fs.existsSync(configPath)) {
        return res.json({ success: false, error: '配置文件不存在' });
    }
    
    try {
        fs.unlinkSync(configPath);
        console.log(`[server.js] 配置文件已删除: ${filename}`);
        res.json({ success: true, message: '配置删除成功' });
    } catch (err) {
        res.json({ success: false, error: '删除配置文件失败: ' + err.message });
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

// ===================== 摄像头管理 API =====================

// 设置摄像头映射
app.post('/api/camera/set-mapping', async (req, res) => {
    const { side, cameraInfo } = req.body;
    try {
        // 【修改】通过 realtimeEngine 转发给 camera_server
        await realtimeEngine.onCameraSetConfig({
            side: side,
            device_name: cameraInfo.label,
            device_id: cameraInfo.deviceId
        });
        res.json({ success: true });
    } catch (err) {
        res.json({ success: false, error: err.message });
    }
});

// 开始推流
app.post('/api/camera/start-streaming', (req, res) => {
    const { side } = req.body;
    try {
        const result = deviceSync.startCameraStreaming(side || 'both');
        res.json(result);
    } catch (err) {
        res.json({ success: false, error: err.message });
    }
});

// 停止推流
app.post('/api/camera/stop-streaming', (req, res) => {
    const { side } = req.body;
    try {
        const result = deviceSync.stopCameraStreaming(side || 'both');
        res.json(result);
    } catch (err) {
        res.json({ success: false, error: err.message });
    }
});

// 开始录制
app.post('/api/camera/start-recording', async (req, res) => {
    const { recordings, metadata } = req.body;
    try {
        const result = await deviceSync.startCameraRecording(recordings, metadata);
        res.json(result);
    } catch (err) {
        res.json({ success: false, error: err.message });
    }
});

// 停止录制
app.post('/api/camera/stop-recording', async (req, res) => {
    try {
        const result = await deviceSync.stopCameraRecording();
        res.json(result);
    } catch (err) {
        res.json({ success: false, error: err.message });
    }
});

// 获取预览帧（静态图片）
app.post('/api/camera/get-preview-frame', async (req, res) => {
    try {
        const { side } = req.body;

        const result = await realtimeEngine.sendCameraCommand('get_preview_frame', {
            side: side
        });

        res.json(result);
    } catch (error) {
        console.error('[server.js] 获取预览帧失败:', error);
        res.json({ success: false, error: error.message });
    }
});

// 枚举摄像头设备
app.get('/api/camera/list', async (req, res) => {
    try {
        const result = await realtimeEngine.sendCameraCommand('list_cameras', {});
        res.json(result);
    } catch (error) {
        console.error('[server.js] 枚举摄像头失败:', error);
        res.json({ success: false, error: error.message, devices: [] });
    }
});

// 获取摄像头状态
app.get('/api/camera/status', (req, res) => {
    try {
        const status = deviceSync.getCameraStatus();
        res.json({ success: true, cameras: status });
    } catch (err) {
        res.json({ success: false, error: err.message });
    }
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
            await stopCameraServer();  // 【新增】停止 camera_server

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

// ==================== Camera Server 管理 ====================

function startCameraServer() {
    return new Promise((resolve, reject) => {
        try {
            console.log('[server.js] 正在启动 camera_server...');

            const { command, args } = getPythonCommand(CAMERA_SERVER_SCRIPT);
            cameraServerProcess = spawn(command, args, { env: PYTHON_ENV });

            cameraServerProcess.on('spawn', () => {
                console.log('[server.js] ✅ camera_server 已启动');
                resolve();
            });

            // 接收 Python 脚本的输出
            cameraServerProcess.stdout.on('data', (data) => {
                const output = data.toString().trim();
                if (output) console.log(`[camera_server] ${output}`);
            });

            cameraServerProcess.stderr.on('data', (data) => {
                const output = data.toString().trim();
                if (output) console.log(`[camera_server] ${output}`);
            });

            cameraServerProcess.on('error', (error) => {
                console.error('[server.js] ❌ camera_server 启动失败:', error);
                reject(error);
            });

            cameraServerProcess.on('exit', (code, signal) => {
                console.log(`[server.js] camera_server 进程退出, code: ${code}, signal: ${signal}`);
                cameraServerProcess = null;
            });

        } catch (error) {
            console.error('[server.js] 启动 camera_server 时出错:', error);
            reject(error);
        }
    });
}

function stopCameraServer() {
    return new Promise((resolve) => {
        if (cameraServerProcess) {
            console.log('[server.js] 正在停止 camera_server...');
            cameraServerProcess.kill('SIGTERM');
            setTimeout(() => {
                if (cameraServerProcess) {
                    cameraServerProcess.kill('SIGKILL');
                }
                resolve();
            }, 2000);
        } else {
            resolve();
        }
    });
}

// ==================== End Camera Server ====================

// 启动服务器
async function startServer() {
    try {


        // 启动realtimeEngine模块
        await realtimeEngine.start(8080);
        console.log('[server.js] realtimeEngine 启动成功');

        // 启动deviceSync模块（deviceSync启动ble_server模块）
        await deviceSync.initialize();
        console.log('[server.js] deviceSync 启动成功');

        // 【新增】启动 camera_server
        await startCameraServer();
        console.log('[server.js] camera_server 启动成功');

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

    // 8秒后打印各模块连接状态汇总（等待所有连接建立）
    setTimeout(() => {
        printSystemStatus();
    }, 8000);
}).catch(error => {
    console.error('服务器启动失败:', error);
});

// 打印系统状态汇总
function printSystemStatus() {
    console.log('\n');
    console.log('╔══════════════════════════════════════════════════════════════════════════════╗');
    console.log('║                         系统模块连接架构图                                   ║');
    console.log('╠══════════════════════════════════════════════════════════════════════════════╣');
    console.log('║                                                                              ║');
    console.log('║   [前端浏览器]                                                               ║');
    console.log('║       │                                                                      ║');
    console.log('║       ├──(HTTP)──────► [Express Server :3000]                                ║');
    console.log('║       │                                                                      ║');
    console.log('║       └──(WS :8080)──► [realtimeEngine]                                      ║');
    console.log('║                             │                                                ║');
    console.log('║                             ├──(WS :8766)──► [ble_server 数据端]             ║');
    console.log('║                             │                                                ║');
    console.log('║                             ├──(WS :8767)──► [mocap_server]                  ║');
    console.log('║                             │                                                ║');
    console.log('║                             └──(ZMQ :5555)─► [storage_server]                ║');
    console.log('║                                                                              ║');
    console.log('║   [前端 ble_control.js]                                                      ║');
    console.log('║       │                                                                      ║');
    console.log('║       └──(WS :8764)──► [ble_server 控制端]                                   ║');
    console.log('║                                                                              ║');
    console.log('╠══════════════════════════════════════════════════════════════════════════════╣');
    console.log('║                         各端口连接状态                                       ║');
    console.log('╠══════════════════════════════════════════════════════════════════════════════╣');

    // realtimeEngine 状态
    const rtStatus = realtimeEngine.getStatus();
    const bleDataConn = rtStatus.bleConnected ? '✓ 已连接' : '✗ 未连接';
    const storageConn = rtStatus.storageConnected ? '✓ 已连接' : '✗ 未连接';
    const mocapConn = rtStatus.mocapConnected ? '✓ 已连接' : '✗ 未连接';
    const frontendCount = rtStatus.clientCount || 0;

    console.log('║                                                                              ║');
    console.log(`║  :8080  realtimeEngine ← 前端WebSocket        ${(frontendCount + ' 个客户端').padEnd(25)} ║`);

    // 【新增】显示已连接的客户端列表
    if (rtStatus.connectedClients && rtStatus.connectedClients.length > 0) {
        const clientNames = rtStatus.connectedClients.map(c => c.name).join(', ');
        console.log(`║         已连接: ${clientNames.padEnd(55)} ║`);
    }
    // 【新增】显示需要手动触发才连接的客户端
    console.log(`║         待连接: Waveform (进入采集页面后连接)                                ║`);

    console.log(`║  :8766  realtimeEngine → ble_server(数据端)   ${bleDataConn.padEnd(25)} ║`);
    console.log(`║  :8767  realtimeEngine → mocap_server         ${mocapConn.padEnd(25)} ║`);
    console.log(`║  :5555  realtimeEngine → storage_server(ZMQ)  ${storageConn.padEnd(25)} ║`);
    console.log('║                                                                              ║');

    // deviceSync 状态 (ble_server进程)
    const deviceSyncStatus = deviceSync.getStatus();
    const bleProcessRunning = deviceSyncStatus.isConnected ? '✓ 进程运行中' : '✗ 进程未启动';
    console.log(`║  :8764  ble_server(控制端) ← 前端ble_control  ${bleProcessRunning.padEnd(25)} ║`);
    console.log(`║  :8766  ble_server(数据端) ← realtimeEngine   ${bleDataConn.padEnd(25)} ║`);
    console.log('║                                                                              ║');

    // storage 状态
    const dsStatus = dataStorage.getStatus();
    const storageRunning = dsStatus.isRunning ? '✓ 进程运行中' : '✗ 进程未启动';
    console.log(`║  :5555  storage_server(ZMQ) ← realtimeEngine  ${storageRunning.padEnd(25)} ║`);
    console.log('║                                                                              ║');

    console.log('╠══════════════════════════════════════════════════════════════════════════════╣');
    console.log('║  提示: 如果 :8766 数据端未连接，波形将无法显示                               ║');
    console.log('╚══════════════════════════════════════════════════════════════════════════════╝');
    console.log('\n');
}
