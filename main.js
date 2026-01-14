const { app, BrowserWindow } = require('electron');
const path = require('path');

let mainWindow;

// 直接在主进程中启动服务器（不再 spawn 外部 node）
function startServer() {
  return new Promise((resolve, reject) => {
    try {
      // 设置环境变量，禁用浏览器自动打开
      process.env.ELECTRON_MODE = '1';
      
      // 直接 require server.js，在 Electron 内置的 Node 环境中运行
      // 注意：需要修改 server.js 导出 startServer 或监听端口
      const PORT = process.env.PORT || 3000;
      
      // 动态加载 server.js
      require(path.join(__dirname, 'server.js'));
      
      // 给服务一点启动时间，然后返回地址
      // 可以根据实际情况调整延时，或改用更可靠的检测方式
      setTimeout(() => {
        resolve(`http://localhost:${PORT}`);
      }, 3000); // 等待3秒让服务启动完成
      
    } catch (error) {
      reject(error);
    }
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
      width: 1200,
      height: 800,
      webPreferences: {
        contextIsolation: true,
        sandbox: false
      }
    });

    // 加载服务地址
    mainWindow.loadURL(serverUrl);

    // 窗口关闭时的处理
    mainWindow.on('closed', () => {
      mainWindow = null;
    });
  } catch (error) {
    console.error('启动失败：', error.message);
    app.quit();
  }
}

// 应用就绪后启动
app.on('ready', createWindow);

// 所有窗口关闭时退出
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// macOS 激活时重建窗口
app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});
