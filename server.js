const express = require('express');
const cors = require('cors');
const path = require('path');
const app = express();
const PORT = process.env.PORT || 3000;

// 引入设备协同模块
const deviceSync = require('./deviceSync');

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
    const throughput = (Math.random() * 1.5 + 2.5).toFixed(2);
    const throughputPercent = Math.max(0, Math.min(100, (throughput - 2.5) / 1.5 * 100));

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
            lastTimestamp: syncStatus.lastTimestamp
        }
    });
});

// 其他API路由保持不变...
app.get('/api/files', (req, res) => {
    const files = [
        {
            name: 'data_20251107_1430.hdf5',
            createTime: '2025-11-07 14:30',
            size: '128.5 MB',
            semgChannels: 16,
            imuChannels: 9
        }
        // ... 其他文件数据
    ];
    res.json(files);
});

app.get('/api/preview-file/:filename', (req, res) => {
    const { filename } = req.params;
    // 生成模拟数据
    res.json({
        filename,
        createTime: '2025-11-07 14:30:22',
        size: '128.5 MB',
        // ... 其他预览数据
    });
});

// 所有路由都指向index.html（支持前端路由）
app.get('*', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// 启动服务器
async function startServer() {
    try {
        // 先启动设备协同模块
        console.log('正在启动设备协同模块...');
        await deviceSync.initialize();
        console.log('设备协同模块启动成功');
        
        // 然后启动HTTP服务器
        app.listen(PORT, () => {
            console.log(`数据采集系统已启动，访问地址：http://localhost:${PORT}`);
            
            // 自动打开浏览器（可选）
            openBrowser();
        });
        
    } catch (error) {
        console.error('服务器启动失败:', error);
        process.exit(1);
    }
}

// 自动打开浏览器函数
function openBrowser() {
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
}

// 优雅关闭处理
function setupGracefulShutdown() {
    const shutdown = async (signal) => {
        console.log(`\n收到 ${signal} 信号，正在关闭服务器...`);
        
        try {
            await deviceSync.close();
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
setupGracefulShutdown();
startServer();