# sEMG Collection System

表面肌电信号采集系统 - 基于 Electron + Node.js + Python

## 项目结构

```
sEMG_Collection_System/
├── main.js                 # Electron 主进程
├── server.js               # Express 服务器
├── public/                 # 前端静态资源
├── python_dist/            # Python 打包后的 exe（构建后生成）
├── storage/                # 数据存储目录
├── config/                 # 配置文件目录
├── log/                    # 日志目录
├── dist/                   # Electron 打包输出（构建后生成）
└── tools/                  # 工具脚本
```

## 环境要求

- Node.js >= 16
- Python >= 3.8
- pip 依赖：见 `requirements.txt`

## 快速开始

### 1. 安装依赖

```bash
# Node.js 依赖
npm install

# Python 依赖
pip install -r requirements.txt
```

### 2. 本地调试

```bash
npm start
```

然后在浏览器打开 http://localhost:3000

---

## 构建与部署

### 方式一：npm scripts（推荐）

| 命令 | 说明 |
|------|------|
| `npm start` | 本地调试 |
| `npm run setup:electron` | 安装 Electron 环境（首次部署） |
| `npm run exe` | 打包 Python 脚本为 exe |
| `npm run package` | 打包 Electron 应用 |
| `npm run build` | 完整构建（exe + package） |
| `npm run clean` | 清理构建产物 |

### 方式二：Makefile（Git Bash）

| 命令 | 说明 |
|------|------|
| `make` | 本地调试 |
| `make electron` | 安装 Electron 环境 |
| `make exe` | 打包 Python 脚本 |
| `make package` | 打包 Electron 应用 |
| `make build` | 完整构建 |
| `make clean` | 清理构建产物 |
| `make help` | 显示帮助 |

### 完整部署流程

```bash
# 1. 首次部署，安装 Electron 环境
npm run setup:electron

# 2. 一键构建
npm run build

# 3. 输出在 dist/数据采集系统-win32-x64/ 目录
```

或分步执行：

```bash
npm run exe        # 先打包 Python → python_dist/
npm run package    # 再打包 Electron → dist/
```

---

## 目录说明

### 打包后的目录结构

```
数据采集系统-win32-x64/
├── 数据采集系统.exe      # 主程序
├── config/               # 配置文件（可写）
├── storage/              # 数据存储（可写）
├── log/                  # 日志（可写）
├── resources/
│   └── app/              # 源码（只读）
│       ├── python_dist/  # Python exe
│       └── ...
└── ...
```

### 数据目录

- `storage/` - 采集的 HDF5 数据文件
- `config/` - 采集配置模板
- `log/` - 运行日志

---

## 工具

### HDF5 数据查看器

```bash
cd tools
python hdf5_stage_viewer.py [file.h5]

# 打包成独立 exe
python build_viewer.py
```

---

## 开发说明

### Python 脚本

| 脚本 | 功能 |
|------|------|
| `ble_server.py` | BLE 蓝牙通信服务 |
| `storage_server.py` | HDF5 数据存储服务 |
| `mocap_server.py` | 动捕数据服务 |

### 路径管理

- 开发环境：直接使用源码目录
- 打包环境：`storage/`、`config/`、`log/` 在 exe 同级目录

详见 `paths.js`

---

## 常见问题

### Q: npm run package 超时

设置代理或使用镜像：

```bash
set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
npm run package
```

### Q: Python exe 启动失败

确保已运行 `npm run exe` 或 `python build_python.py` 打包 Python 脚本。

### Q: 找不到 storage 目录

程序首次运行会自动创建 `storage/`、`config/`、`log/` 目录。
