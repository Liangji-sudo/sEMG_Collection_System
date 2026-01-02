/**
 * paths.js - 路径管理模块
 * 
 * 解决 Electron 打包后路径问题：
 * - 开发环境：使用源码目录
 * - 打包环境：storage/config/log 放在 exe 同级目录
 */

const path = require('path');
const fs = require('fs');

// 判断是否在 Electron 打包环境中运行
const isPackaged = (function() {
    // 方法1：检查是否存在 app.asar
    if (process.resourcesPath && process.resourcesPath.includes('resources')) {
        return true;
    }
    // 方法2：检查 electron 的 app.isPackaged
    try {
        const { app } = require('electron');
        return app && app.isPackaged;
    } catch (e) {
        // 不在 Electron 环境中
    }
    // 方法3：检查 __dirname 是否包含 app.asar
    if (__dirname.includes('app.asar')) {
        return true;
    }
    return false;
})();

// 获取应用根目录
function getAppRoot() {
    if (isPackaged) {
        // 打包后：exe 所在目录
        // process.execPath 是 exe 的完整路径
        return path.dirname(process.execPath);
    } else {
        // 开发环境：源码目录
        return __dirname;
    }
}

// 获取源码目录（用于读取静态资源如 public/）
function getSourceRoot() {
    return __dirname;
}

// 获取数据目录（storage/config/log 等可写目录）
function getDataRoot() {
    return getAppRoot();
}

// 各个目录的路径
const PATHS = {
    // 源码相关（只读）
    source: getSourceRoot(),
    public: path.join(getSourceRoot(), 'public'),
    
    // 数据相关（可读写）- 打包后在 exe 同级
    data: getDataRoot(),
    storage: path.join(getDataRoot(), 'storage'),
    config: path.join(getDataRoot(), 'config'),
    log: path.join(getDataRoot(), 'log'),
};

// 确保必要的目录存在
function ensureDirectories() {
    const dirs = [PATHS.storage, PATHS.config, PATHS.log];
    dirs.forEach(dir => {
        if (!fs.existsSync(dir)) {
            fs.mkdirSync(dir, { recursive: true });
            console.log(`[Paths] 创建目录: ${dir}`);
        }
    });
}

// 初始化时创建目录
ensureDirectories();

// 打印路径信息（调试用）
console.log(`[Paths] 运行环境: ${isPackaged ? 'Electron 打包' : '开发环境'}`);
console.log(`[Paths] 源码目录: ${PATHS.source}`);
console.log(`[Paths] 数据目录: ${PATHS.data}`);
console.log(`[Paths] Storage: ${PATHS.storage}`);
console.log(`[Paths] Config: ${PATHS.config}`);
console.log(`[Paths] Log: ${PATHS.log}`);

module.exports = {
    isPackaged,
    getAppRoot,
    getSourceRoot,
    getDataRoot,
    PATHS,
    ensureDirectories
};
