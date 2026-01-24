/**
 * Python 可执行文件路径解析器
 *
 * 策略：
 * - 优先使用打包好的 exe 文件（无需安装 Python 环境）
 * - 如果 exe 不存在，才尝试使用 Python 运行 .py 脚本
 *
 * 部署说明：
 * - 运行 python build_python.py 打包 Python 脚本为 exe
 * - exe 文件会输出到 python_dist/ 目录
 */

const path = require('path');
const fs = require('fs');

// Python exe 输出目录（备用）
const PYTHON_DIST_DIR = path.join(__dirname, 'python_dist');

/**
 * 获取 Python 脚本的执行命令和参数
 * @param {string} scriptName - 脚本名称（不含扩展名），如 'ble_server'
 * @param {string[]} extraArgs - 额外的命令行参数
 * @returns {{command: string, args: string[]}}
 */
function getPythonCommand(scriptName, extraArgs = []) {
    const exePath = path.join(PYTHON_DIST_DIR, `${scriptName}.exe`);
    const pyPath = path.join(__dirname, `${scriptName}.py`);

    // 优先使用打包好的 exe（无需 Python 环境）
    if (fs.existsSync(exePath)) {
        console.log(`[pythonPath] 使用 exe: ${exePath}`);
        return {
            command: exePath,
            args: [...extraArgs]
        };
    }

    // 备用：使用 Python 脚本（需要 Python 环境）
    if (fs.existsSync(pyPath)) {
        console.log(`[pythonPath] 使用 Python 脚本: ${pyPath}`);
        return {
            command: 'python',
            args: [pyPath, ...extraArgs]
        };
    }

    throw new Error(`找不到: ${scriptName}.exe 或 ${scriptName}.py`);
}

module.exports = { getPythonCommand, PYTHON_DIST_DIR };
