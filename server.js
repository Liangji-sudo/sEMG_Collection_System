// server.js
const express = require('express');
const cors = require('cors');
const path = require('path');
const app = express();
const PORT = process.env.PORT || 3000;

// 引入设备协同模块
const deviceSync = require('./deviceSync');
const realtimeEngine = require('./realtimeEngine');

// 中间件配置
app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// API路由 - 获取设备状态
app.get('/api/device-status', (req, res) => {
    // 获取设备协同模块状态
    const syncStatus = deviceSync.getStatus();
    
    // 原有的模拟数据
    const bluetoothDb = Math.floor(Math.random() * 30) - 70;
    const bluetoothStrength = Math.max(0, Math.min(100, 100 - (bluetoothDb + 70) * 3.33));
    //const throughput = (Math.random() * 1.5 + 2.5).toFixed(2);

    const throughput = (deviceSync.getCurrentThroughput()/1000).toFixed(4);
    const throughputPercent = throughput/10;

    res.json({
        bluetooth: {
            connected: true,
            db: bluetoothDb,
            strength: bluetoothStrength
        },
        battery: {
            level: 78,
            remainingTime: '5.2小时'
        },
        camera: {
            connected: true,
            model: 'Sony IMX586',
            status: '运行中'
        },
        storage: {
            usedPercent: 42,
            used: '87.3GB',
            total: '208GB'
        },
        throughput: {
            value: throughput,
            percent: throughputPercent
        },
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



// 文件列表API
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

// 文件预览API
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
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
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

        // 启动实时引擎
        await realtimeEngine.start(8080);

        // 先启动设备协同模块
        console.log('正在启动设备协同模块...');
        await deviceSync.initialize();
        console.log('设备协同模块启动成功');
        
        // 然后启动HTTP服务器
        const server = app.listen(PORT, () => {
            console.log(`数据采集系统已启动，访问地址：http://localhost:${PORT}`);
            //console.log('EMG数据API: http://localhost:' + PORT + '/api/emg-data');
            console.log('设备状态API: http://localhost:' + PORT + '/api/device-status');
            
            // 自动打开浏览器（可选）
            openBrowser();
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

