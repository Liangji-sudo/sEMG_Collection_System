/**
 * cameraManager.js - USB摄像头管理模块
 *
 * 功能：
 * 1. 枚举和识别USB摄像头设备
 * 2. 管理视频流的开启/关闭
 * 3. 控制视频录制（开始/停止）
 * 4. 同步录制到H5采集任务
 * 5. 提供摄像头状态信息（分辨率、帧率、推流状态）
 */

const EventEmitter = require('events');
const path = require('path');
const fs = require('fs').promises;

class CameraManager extends EventEmitter {
    constructor() {
        super();

        // 摄像头设备列表
        this.cameras = {
            left: null,   // 左手摄像头
            right: null   // 右手摄像头
        };

        // 摄像头状态
        this.cameraStatus = {
            left: {
                deviceId: null,
                label: '',
                streaming: false,
                recording: false,
                resolution: { width: 0, height: 0 },
                fps: 0
            },
            right: {
                deviceId: null,
                label: '',
                streaming: false,
                recording: false,
                resolution: { width: 0, height: 0 },
                fps: 0
            }
        };

        // 录制会话
        this.recordingSessions = {
            left: null,
            right: null
        };

        // 当前录制文件路径
        this.currentRecordingFiles = {
            left: null,
            right: null
        };

        // 录制参数
        this.recordingConfig = {
            videoFormat: 'webm',           // 视频格式
            videoBitsPerSecond: 2500000,   // 2.5 Mbps
            mimeType: 'video/webm;codecs=vp8'
        };

        console.log('[CameraManager] 模块初始化完成');
    }

    /**
     * 枚举所有可用的摄像头设备
     * 注意：此方法需要在渲染进程（前端）调用 navigator.mediaDevices.enumerateDevices()
     * 后端只提供API接口，实际枚举由前端完成并通过IPC通信传递给后端
     */
    async enumerateCameras() {
        console.log('[CameraManager] 枚举摄像头设备（需前端支持）');
        // 由前端通过IPC调用并返回结果
        return {
            success: true,
            message: '请前端调用 navigator.mediaDevices.enumerateDevices()'
        };
    }

    /**
     * 设置摄像头映射
     * @param {string} side - 'left' 或 'right'
     * @param {object} cameraInfo - {deviceId, label, resolution, fps}
     */
    setCameraMapping(side, cameraInfo) {
        if (!['left', 'right'].includes(side)) {
            console.error('[CameraManager] 无效的side参数:', side);
            return false;
        }

        this.cameras[side] = cameraInfo.deviceId;
        this.cameraStatus[side].deviceId = cameraInfo.deviceId;
        this.cameraStatus[side].label = cameraInfo.label || `摄像头${side}`;
        this.cameraStatus[side].resolution = cameraInfo.resolution || { width: 1280, height: 720 };
        this.cameraStatus[side].fps = cameraInfo.fps || 30;

        console.log(`[CameraManager] 摄像头映射已设置: ${side} -> ${cameraInfo.label}`);
        this.emit('camera-mapped', { side, cameraInfo });
        return true;
    }

    /**
     * 开始视频流推流
     * @param {string} side - 'left' 或 'right'
     */
    startStreaming(side) {
        if (!['left', 'right'].includes(side)) {
            return { success: false, error: '无效的side参数' };
        }

        if (!this.cameras[side]) {
            return { success: false, error: `${side}侧摄像头未配置` };
        }

        this.cameraStatus[side].streaming = true;
        console.log(`[CameraManager] ${side}侧摄像头开始推流`);
        this.emit('streaming-started', { side });

        return { success: true };
    }

    /**
     * 停止视频流推流
     * @param {string} side - 'left' 或 'right'
     */
    stopStreaming(side) {
        if (!['left', 'right'].includes(side)) {
            return { success: false, error: '无效的side参数' };
        }

        // 如果正在录制，先停止录制
        if (this.cameraStatus[side].recording) {
            this.stopRecording(side);
        }

        this.cameraStatus[side].streaming = false;
        console.log(`[CameraManager] ${side}侧摄像头停止推流`);
        this.emit('streaming-stopped', { side });

        return { success: true };
    }

    /**
     * 开始录制视频
     * @param {string} side - 'left' 或 'right'
     * @param {string} outputPath - 输出文件路径（不含扩展名）
     * @param {object} metadata - 元数据 {h5FileName, subjectId, sessionIndex, stageName}
     */
    async startRecording(side, outputPath, metadata = {}) {
        if (!['left', 'right'].includes(side)) {
            return { success: false, error: '无效的side参数' };
        }

        if (!this.cameraStatus[side].streaming) {
            return { success: false, error: `${side}侧摄像头未推流` };
        }

        if (this.cameraStatus[side].recording) {
            return { success: false, error: `${side}侧摄像头已在录制中` };
        }

        // 生成完整文件路径
        const videoFileName = `${path.basename(outputPath)}_${side}.${this.recordingConfig.videoFormat}`;
        const fullPath = path.join(path.dirname(outputPath), videoFileName);

        // 确保输出目录存在
        const outputDir = path.dirname(fullPath);
        try {
            await fs.mkdir(outputDir, { recursive: true });
        } catch (error) {
            console.error(`[CameraManager] 创建输出目录失败:`, error);
            return { success: false, error: '创建输出目录失败' };
        }

        // 更新状态
        this.cameraStatus[side].recording = true;
        this.currentRecordingFiles[side] = fullPath;
        this.recordingSessions[side] = {
            startTime: Date.now(),
            outputPath: fullPath,
            metadata: metadata
        };

        console.log(`[CameraManager] ${side}侧摄像头开始录制: ${videoFileName}`);
        console.log(`[CameraManager] 完整路径: ${fullPath}`);
        console.log(`[CameraManager] 元数据:`, metadata);

        this.emit('recording-started', {
            side,
            outputPath: fullPath,
            fileName: videoFileName,
            metadata
        });

        return {
            success: true,
            outputPath: fullPath,
            fileName: videoFileName
        };
    }

    /**
     * 停止录制视频
     * @param {string} side - 'left' 或 'right'
     */
    async stopRecording(side) {
        if (!['left', 'right'].includes(side)) {
            return { success: false, error: '无效的side参数' };
        }

        if (!this.cameraStatus[side].recording) {
            return { success: false, error: `${side}侧摄像头未在录制` };
        }

        const session = this.recordingSessions[side];
        const duration = Date.now() - session.startTime;

        // 更新状态
        this.cameraStatus[side].recording = false;
        const outputPath = this.currentRecordingFiles[side];

        // 清除会话
        this.recordingSessions[side] = null;
        this.currentRecordingFiles[side] = null;

        console.log(`[CameraManager] ${side}侧摄像头停止录制`);
        console.log(`[CameraManager] 录制时长: ${(duration / 1000).toFixed(2)}秒`);
        console.log(`[CameraManager] 输出文件: ${outputPath}`);

        this.emit('recording-stopped', {
            side,
            outputPath,
            duration
        });

        return {
            success: true,
            outputPath,
            duration
        };
    }

    /**
     * 获取摄像头状态
     * @param {string} side - 'left', 'right', 或 null（返回全部）
     */
    getCameraStatus(side = null) {
        if (side) {
            return this.cameraStatus[side] || null;
        }
        return this.cameraStatus;
    }

    /**
     * 检查是否有摄像头正在录制
     */
    isAnyRecording() {
        return this.cameraStatus.left.recording || this.cameraStatus.right.recording;
    }

    /**
     * 检查是否有摄像头正在推流
     */
    isAnyStreaming() {
        return this.cameraStatus.left.streaming || this.cameraStatus.right.streaming;
    }

    /**
     * 停止所有摄像头
     */
    async stopAll() {
        console.log('[CameraManager] 停止所有摄像头');

        // 停止录制
        if (this.cameraStatus.left.recording) {
            await this.stopRecording('left');
        }
        if (this.cameraStatus.right.recording) {
            await this.stopRecording('right');
        }

        // 停止推流
        if (this.cameraStatus.left.streaming) {
            this.stopStreaming('left');
        }
        if (this.cameraStatus.right.streaming) {
            this.stopStreaming('right');
        }

        return { success: true };
    }

    /**
     * 重置管理器
     */
    reset() {
        console.log('[CameraManager] 重置管理器');
        this.stopAll();

        this.cameras = { left: null, right: null };
        this.cameraStatus = {
            left: {
                deviceId: null,
                label: '',
                streaming: false,
                recording: false,
                resolution: { width: 0, height: 0 },
                fps: 0
            },
            right: {
                deviceId: null,
                label: '',
                streaming: false,
                recording: false,
                resolution: { width: 0, height: 0 },
                fps: 0
            }
        };
    }
}

// 创建单例
const cameraManager = new CameraManager();

cameraManager.on('error', (error) => {
    console.error('[CameraManager] 错误:', error);
});

console.log('[CameraManager] 模块加载完成');

module.exports = cameraManager;
