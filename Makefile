# ============================================
# sEMG Collection System - Makefile
# ============================================
#
# 使用方法:
#   make          - 本地调试 (npm start)
#   make dev      - 本地调试 (同上)
#   make electron - 安装 Electron 环境
#   make exe      - 打包 Python 脚本为 exe
#   make package  - 打包 Electron 应用
#   make build    - 完整构建 (exe + package)
#   make clean    - 清理构建产物
#   make help     - 显示帮助
#
# Windows 用户需要安装 make:
#   choco install make
#   或使用 Git Bash
# ============================================

.PHONY: all dev electron exe package build clean help

# 默认目标：本地调试
all: dev

# 本地调试
dev:
	@echo ========================================
	@echo  启动本地调试服务器
	@echo ========================================
	npm start

# 安装 Electron 环境
electron:
	@echo ========================================
	@echo  安装 Electron 环境
	@echo ========================================
	npm install electron --save-dev
	npm install electron-packager --save-dev
	@echo [OK] Electron 环境安装完成

# 打包 Python 脚本为 exe
exe:
	@echo ========================================
	@echo  打包 Python 脚本
	@echo ========================================
	python build_python.py
	@echo [OK] Python 打包完成，输出目录: python_dist/

# 打包 Electron 应用
package:
	@echo ========================================
	@echo  打包 Electron 应用
	@echo ========================================
	npm run package
	@echo [OK] 打包完成，输出目录: dist/

# 完整构建流程
build: exe package
	@echo ========================================
	@echo  构建完成!
	@echo  Python exe: python_dist/
	@echo  Electron:   dist/
	@echo ========================================

# 清理构建产物
clean:
	@echo ========================================
	@echo  清理构建产物
	@echo ========================================
	-rmdir /s /q dist 2>nul || rm -rf dist
	-rmdir /s /q python_dist 2>nul || rm -rf python_dist
	-rmdir /s /q build_temp 2>nul || rm -rf build_temp
	-rmdir /s /q __pycache__ 2>nul || rm -rf __pycache__
	@echo [OK] 清理完成

# 显示帮助
help:
	@echo ============================================
	@echo  sEMG Collection System - 构建命令
	@echo ============================================
	@echo.
	@echo  make          本地调试 (npm start)
	@echo  make dev      本地调试 (同上)
	@echo  make electron 安装 Electron 环境
	@echo  make exe      打包 Python 脚本为 exe
	@echo  make package  打包 Electron 应用
	@echo  make build    完整构建 (exe + package)
	@echo  make clean    清理构建产物
	@echo  make help     显示此帮助
	@echo.
