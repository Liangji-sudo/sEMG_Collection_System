const { app, BrowserWindow } = require('electron');
const path = require('path');
const { spawn, execSync } = require('child_process');

let mainWindow;

// 【修复 CRITICAL-N3】追踪子进程PID，退出时按PID终止（适用于打包和开发模式）
const childPids = new Set();

// 导出PID注册函数供 server.js 的 spawn 回调使用
module.exports.registerChildPid = (pid) => {
  childPids.add(pid);
  console.log(`[Main] 注册子进程 PID: ${pid}`);
};

module.exports.unregisterChildPid = (pid) => {
  childPids.delete(pid);
  console.log(`[Main] 注销子进程 PID: ${pid}`);
};

// 需要在退出时杀掉的进程名列表（仅用于打包模式 .exe）
const PYTHON_PROCESSES = ['ble_server.exe', 'storage_server.exe', 'mocap_server.exe', 'camera_server.exe'];

/**
 * 强制杀掉所有 Python 子进程（Windows）
 */
function killAllPythonProcesses() {
  console.log('[Main] 正在清理子进程...');

  // 优先：按 PID 终止（开发和打包模式均适用）
  for (const pid of childPids) {
    try {
      process.kill(pid, 'SIGTERM');
      console.log(`[Main] 已发送 SIGTERM 到 PID: ${pid}`);
    } catch (e) {
      // 进程已退出，忽略
    }
  }
  childPids.clear();

  // 备用：按进程名强制终止（打包模式 .exe 进程名）
  for (const procName of PYTHON_PROCESSES) {
    try {
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
      const PORT = process.env.PORT || 3000;

      // 动态加载 server.js（同步加载模块，异步启动服务）
      require(path.join(__dirname, 'server.js'));

      // 【修复 H-N4】轮询 HTTP 服务就绪，替代盲目 3s 等待
      const MAX_WAIT_MS = 15000;
      const POLL_INTERVAL_MS = 200;
      const startTime = Date.now();
      const http = require('http');

      const poll = () => {
        const elapsed = Date.now() - startTime;
        if (elapsed > MAX_WAIT_MS) {
          reject(new Error(`HTTP 服务启动超时 (${MAX_WAIT_MS}ms)`));
          return;
        }

        http.get(`http://localhost:${PORT}/api/health`, (res) => {
          if (res.statusCode === 200) {
            console.log(`[Main] HTTP 服务就绪 (${elapsed}ms)`);
            resolve(`http://localhost:${PORT}`);
          } else {
            setTimeout(poll, POLL_INTERVAL_MS);
          }
        }).on('error', () => {
          setTimeout(poll, POLL_INTERVAL_MS);
        });
      };

      // 首次轮询给予最小 500ms 缓冲
      setTimeout(poll, 500);

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

// 应用退出前清理子进程
app.on('before-quit', () => {
  console.log('[Main] 应用即将退出，清理子进程...');
  killAllPythonProcesses();
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
