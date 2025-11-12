const express = require('express');
const cors = require('cors');
const path = require('path');
const app = express();
const PORT = process.env.PORT || 3000;

// 中间件配置
app.use(cors());
app.use(express.json());
// 托管前端静态资源（public文件夹下的文件可直接访问）
app.use(express.static(path.join(__dirname, 'public')));

// 模拟后端API接口（可选，用于后续扩展真实数据交互）
// 1. 获取设备动态状态数据
app.get('/api/device-status', (req, res) => {
  // 模拟动态生成设备状态数据
  const bluetoothDb = Math.floor(Math.random() * 30) - 70; // -40 ~ -70 dBm
  const bluetoothStrength = Math.max(0, Math.min(100, 100 - (bluetoothDb + 70) * 3.33));
  const throughput = (Math.random() * 1.5 + 2.5).toFixed(2); // 2.5 ~ 4.0 MB/s
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
    }
  });
});

// 2. 获取HDF5文件列表（模拟数据）
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
      name: 'data_20251106_0915.hdf5',
      createTime: '2025-11-06 09:15',
      size: '96.3 MB',
      semgChannels: 16,
      imuChannels: 9
    },
    {
      name: 'data_20251105_1642.hdf5',
      createTime: '2025-11-05 16:42',
      size: '156.8 MB',
      semgChannels: 16,
      imuChannels: 9
    },
    {
      name: 'data_20251104_1128.hdf5',
      createTime: '2025-11-04 11:28',
      size: '87.4 MB',
      semgChannels: 16,
      imuChannels: 9
    },
    {
      name: 'data_20251103_1355.hdf5',
      createTime: '2025-11-03 13:55',
      size: '112.7 MB',
      semgChannels: 16,
      imuChannels: 9
    }
  ];
  res.json(files);
});

// 3. 预览HDF5文件数据（模拟生成信号数据）
app.get('/api/preview-file/:filename', (req, res) => {
  const { filename } = req.params;
  // 生成模拟的多通道信号数据
  const generateSignalData = (length, min, max) => {
    return Array.from({ length }, () => (Math.random() * (max - min) + min));
  };

  // sEMG 16通道数据（100个采样点）
  const semgData = Array.from({ length: 16 }, () => generateSignalData(100, -2.5, 2.5));
  // IMU 9通道数据（100个采样点）
  const imuData = [
    generateSignalData(100, -1, 1), // 加速度计X
    generateSignalData(100, -1, 1), // 加速度计Y
    generateSignalData(100, 8.8, 10.8), // 加速度计Z（含重力）
    generateSignalData(100, -5, 5), // 陀螺仪X
    generateSignalData(100, -5, 5), // 陀螺仪Y
    generateSignalData(100, -5, 5), // 陀螺仪Z
    generateSignalData(100, -50, 50), // 磁力计X
    generateSignalData(100, -50, 50), // 磁力计Y
    generateSignalData(100, -50, 50) // 磁力计Z
  ];

  res.json({
    filename,
    createTime: filename.includes('20251107') ? '2025-11-07 14:30:22' : 
                filename.includes('20251106') ? '2025-11-06 09:15:45' :
                filename.includes('20251105') ? '2025-11-05 16:42:18' :
                filename.includes('20251104') ? '2025-11-04 11:28:33' : '2025-11-03 13:55:09',
    size: filename.includes('20251107') ? '128.5 MB' : 
          filename.includes('20251106') ? '96.3 MB' :
          filename.includes('20251105') ? '156.8 MB' :
          filename.includes('20251104') ? '87.4 MB' : '112.7 MB',
    semgData,
    imuData,
    dataStats: {
      semgRange: '±2.56 mV',
      semgSampleRate: '1000 Hz',
      imuAccRange: '±1.25 g',
      imuGyroRange: '±15.3 °/s',
      completeness: '100%'
    }
  });
});

// 所有路由都指向index.html（支持前端路由，这里仅单页应用）
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// 启动服务
app.listen(PORT, () => {
  console.log(`数据采集系统已启动，访问地址：http://localhost:${PORT}`);
  // 自动打开浏览器（可选）
  const { exec } = require('child_process');
  switch (process.platform) {
    case 'win32':
      exec(`start http://localhost:${PORT}`);
      break;
    case 'darwin':
      exec(`open http://localhost:${PORT}`);
      break;
    case 'linux':
      exec(`xdg-open http://localhost:${PORT}`);
      break;
  }
});
