const { app, BrowserWindow } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let mainWindow;
let serverProcess; // 保存服务进程引用

// 启动 server.js 服务
function startServer() {
  return new Promise((resolve, reject) => {
    // 启动 server.js，并传入环境变量 ELECTRON_MODE=1（用于禁用浏览器自动打开）
    serverProcess = spawn('node', [path.join(__dirname, 'server.js')], {
      stdio: 'pipe', // 捕获输出日志
      env: { ...process.env, ELECTRON_MODE: '1' } // 传递环境变量
    });

    // 监听服务输出日志，判断是否启动完成
    serverProcess.stdout.on('data', (data) => {
      const output = data.toString();
      console.log('服务输出：', output);
      // 匹配 server.js 中启动成功的标志性日志（根据你的 server.js 输出修改）
      if (output.includes('数据采集系统已启动，访问地址：http://localhost:')) {
        // 提取端口（默认 3000，也可从日志中解析）
        const portMatch = output.match(/http:\/\/localhost:(\d+)/);
        const port = portMatch ? portMatch[1] : 3000;
        resolve(`http://localhost:${port}`);
      }
    });

    // 监听服务错误
    serverProcess.stderr.on('data', (data) => {
      console.error('服务错误：', data.toString());
    });

    // 服务进程退出时的处理
    serverProcess.on('exit', (code) => {
      if (!resolveCalled) {
        reject(new Error(`服务意外退出，代码：${code}`));
      }
    });

    // 超时保护（15秒未启动成功则报错）
    let resolveCalled = false;
    setTimeout(() => {
      if (!resolveCalled) {
        resolveCalled = true;
        reject(new Error('服务启动超时（15秒）'));
      }
    }, 15000);
  });
}

// 创建 Electron 窗口
async function createWindow() {
  try {
    // 等待服务完全启动，获取访问地址
    const serverUrl = await startServer();
    console.log('服务启动成功，地址：', serverUrl);

    // 创建窗口
    mainWindow = new BrowserWindow({
      width: 1200, // 调整为适合你页面的宽度
      height: 800, // 调整高度
      webPreferences: {
        contextIsolation: true, // 安全设置（无需 nodeIntegration）
        sandbox: false // 避免影响服务请求
      }
    });

    // 加载服务地址（如 http://localhost:3000）
    mainWindow.loadURL(serverUrl);

    // 窗口关闭时，终止服务进程
    mainWindow.on('closed', () => {
      if (serverProcess) {
        serverProcess.kill('SIGINT'); // 发送中断信号，触发 server.js 的优雅关闭
      }
      mainWindow = null;
    });
  } catch (error) {
    console.error('启动失败：', error.message);
    app.quit(); // 启动失败则退出应用
  }
}

// 应用就绪后启动
app.on('ready', createWindow);

// 所有窗口关闭时，确保服务进程退出
app.on('window-all-closed', () => {
  if (serverProcess) {
    serverProcess.kill('SIGINT');
  }
  if (process.platform !== 'darwin') app.quit();
});

// macOS 激活时重建窗口
app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});