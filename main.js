const { app, BrowserWindow } = require('electron');
const path = require('path');
const { spawn, execSync } = require('child_process');

let mainWindow;
let serverModule = null;
let gracefulQuitStarted = false;

// 需要在退出时杀掉的进程名列表
const PYTHON_PROCESSES = ['ble_server.exe', 'storage_server.exe', 'mocap_server.exe'];

/**
 * 强制杀掉所有 Python 子进程（Windows）
 */
function killAllPythonProcesses() {
  console.log('[Main] 正在清理子进程...');

  for (const procName of PYTHON_PROCESSES) {
    try {
      // Windows 下使用 taskkill 强制杀进程
      execSync(`taskkill /F /IM ${procName} 2>nul`, { stdio: 'ignore' });
      console.log(`[Main] 已终止: ${procName}`);
    } catch (e) {
      // 进程不存在时会报错，忽略
    }
  }

  console.log('[Main] 子进程清理完成');
}

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
      serverModule = require(path.join(__dirname, 'server.js'));

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
        contextIsolation: false,
        nodeIntegration: false,
        sandbox: false
      }
    });

    // 加载服务地址
    mainWindow.loadURL(serverUrl);

    // 确保窗口获得焦点（修复偶发输入框无法键入问题）
    mainWindow.once('ready-to-show', () => {
      mainWindow.show();
      mainWindow.focus();
      mainWindow.webContents.focus();
    });

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

// 应用退出前先让服务优雅关闭，确保 H5 文件完成 close
app.on('before-quit', async (event) => {
  if (gracefulQuitStarted) return;
  gracefulQuitStarted = true;
  event.preventDefault();

  console.log('[Main] 应用即将退出，先优雅关闭采集服务...');
  try {
    const timeout = new Promise((resolve) => setTimeout(resolve, 8000));
    const shutdown = serverModule && typeof serverModule.shutdown === 'function'
      ? serverModule.shutdown('electron-before-quit')
      : Promise.resolve();
    await Promise.race([shutdown, timeout]);
  } catch (error) {
    console.error('[Main] 优雅关闭采集服务失败:', error);
  }

  killAllPythonProcesses();
  app.quit();
});

// 应用退出时再次确保清理
app.on('quit', () => {
  killAllPythonProcesses();
});

// 捕获未处理的异常，确保清理
process.on('uncaughtException', (error) => {
  console.error('[Main] 未捕获的异常:', error);
  killAllPythonProcesses();
  app.quit();
});

// 捕获 SIGINT (Ctrl+C)
process.on('SIGINT', () => {
  console.log('[Main] 收到 SIGINT 信号');
  killAllPythonProcesses();
  app.quit();
});
