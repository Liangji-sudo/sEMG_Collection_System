/**
 * camera-control.js - 前端摄像头控制模块
 *
 * 功能：
 * 1. 枚举和识别USB摄像头（过滤系统自带摄像头）
 * 2. 管理视频流预览（MediaStream）
 * 3. 与后端 camera_server 同步摄像头配置
 * 4. 提供预览功能
 * 5. 通过后端 API 控制录制（录制由 camera_server 的 ffmpeg 完成）
 *
 * 注意：
 * - 视频录制完全由后端 camera_server 通过 ffmpeg 完成
 * - 前端只负责预览和配置同步
 * - 录制和预览使用独立的摄像头访问方式，避免冲突
 */

(function() {
    'use strict';

    console.log('[CameraControl] 脚本加载开始...');

    class CameraControl {
        constructor() {
            // 摄像头设备列表
            this.availableCameras = [];

            // 当前选中的摄像头映射
            this.selectedCameras = {
                left: null,
                right: null
            };

            // MediaStream 对象
            this.streams = {
                left: null,
                right: null
            };

            // 录制状态
            this.isStreaming = false;
            this.isRecording = false;

            // 当前录制的元数据
            this.currentRecordingMetadata = null;

            console.log('[CameraControl] 构造函数完成');
        }

        /**
         * 初始化
         */
        async init() {
            console.log('[CameraControl] 初始化开始...');

            // 检查浏览器支持
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                console.error('[CameraControl] 浏览器不支持 getUserMedia API');
                return false;
            }

            // 请求摄像头权限
            try {
                await navigator.mediaDevices.getUserMedia({ video: true });
                console.log('[CameraControl] 摄像头权限已获取');
            } catch (error) {
                console.error('[CameraControl] 无法获取摄像头权限:', error);
                return false;
            }

            console.log('[CameraControl] 初始化完成');
            return true;
        }

        /**
         * 枚举所有可用的摄像头设备（仅USB摄像头）
         */
        async enumerateCameras() {
            console.log('[CameraControl] 枚举摄像头设备...');

            try {
                const devices = await navigator.mediaDevices.enumerateDevices();
                const allCameras = devices.filter(device => device.kind === 'videoinput');

                // 过滤掉系统自带摄像头
                this.availableCameras = allCameras.filter(camera => {
                    const label = camera.label.toLowerCase();

                    // 排除常见的内置摄像头标识（增强版）
                    const builtInKeywords = [
                        'integrated',
                        'built-in',
                        'builtin',
                        'facetime',
                        '内置',
                        '前置',
                        '后置',
                        'front camera',
                        'rear camera',
                        'webcam',  // 笔记本自带摄像头常用词
                        'hd camera', // 笔记本HD摄像头
                        'ir camera'  // 红外摄像头
                    ];

                    const isBuiltIn = builtInKeywords.some(keyword => label.includes(keyword));

                    // 额外检查：如果设备标签为空或太短，也可能是内置的
                    if (!label || label.length < 3) {
                        console.warn(`[CameraControl] 跳过未标记的设备: ${camera.deviceId}`);
                        return false;
                    }

                    return !isBuiltIn;
                });

                console.log(`[CameraControl] 找到 ${allCameras.length} 个摄像头设备 (过滤后 ${this.availableCameras.length} 个USB摄像头):`);
                allCameras.forEach((camera, index) => {
                    const filtered = !this.availableCameras.includes(camera) ? ' [已过滤]' : '';
                    console.log(`  [${index}] ${camera.label || `未命名`}${filtered}`);
                });

                return this.availableCameras;
            } catch (error) {
                console.error('[CameraControl] 枚举设备失败:', error);
                return [];
            }
        }

        /**
         * 设置摄像头映射
         * @param {string} side - 'left' 或 'right'
         * @param {string} deviceId - 摄像头设备ID
         */
        async setCameraMapping(side, deviceId) {
            if (!['left', 'right'].includes(side)) {
                console.error('[CameraControl] 无效的side参数:', side);
                return false;
            }

            const camera = this.availableCameras.find(c => c.deviceId === deviceId);
            if (!camera) {
                console.error('[CameraControl] 找不到设备:', deviceId);
                return false;
            }

            this.selectedCameras[side] = camera;
            console.log(`[CameraControl] 摄像头映射已设置: ${side} -> ${camera.label}`);

            // 同步到后端
            try {
                // 先获取摄像头的实际分辨率和帧率
                const stream = await navigator.mediaDevices.getUserMedia({
                    video: { deviceId: { exact: deviceId } }
                });
                const videoTrack = stream.getVideoTracks()[0];
                const settings = videoTrack.getSettings();

                // 立即停止这个临时流
                stream.getTracks().forEach(track => track.stop());

                const cameraInfo = {
                    deviceId: camera.deviceId,
                    label: camera.label,
                    resolution: {
                        width: settings.width || 1280,
                        height: settings.height || 720
                    },
                    fps: settings.frameRate || 30
                };

                const response = await fetch('/api/camera/set-mapping', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ side, cameraInfo })
                });

                const result = await response.json();
                console.log('[CameraControl] 后端映射结果:', result);

                return result.success;
            } catch (error) {
                console.error('[CameraControl] 设置映射失败:', error);
                return false;
            }
        }

        /**
         * 开始视频流推流
         * @param {string} side - 'left', 'right', 或 'both'
         */
        async startStreaming(side = 'both') {
            console.log(`[CameraControl] 开始推流: ${side}`);

            const sides = side === 'both' ? ['left', 'right'] : [side];
            const results = {};

            for (const s of sides) {
                if (!this.selectedCameras[s]) {
                    console.warn(`[CameraControl] ${s}侧摄像头未配置`);
                    results[s] = { success: false, error: '摄像头未配置' };
                    continue;
                }

                try {
                    // 启动视频流
                    const stream = await navigator.mediaDevices.getUserMedia({
                        video: {
                            deviceId: { exact: this.selectedCameras[s].deviceId },
                            width: { ideal: 1280 },
                            height: { ideal: 720 },
                            frameRate: { ideal: 30 }
                        }
                    });

                    this.streams[s] = stream;
                    console.log(`[CameraControl] ${s}侧摄像头推流成功`);
                    results[s] = { success: true };

                    // 触发事件
                    this._emitEvent('streaming-started', { side: s, stream });

                } catch (error) {
                    console.error(`[CameraControl] ${s}侧摄像头推流失败:`, error);
                    results[s] = { success: false, error: error.message };
                }
            }

            // 同步到后端
            try {
                const response = await fetch('/api/camera/start-streaming', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ side })
                });
                await response.json();
            } catch (error) {
                console.error('[CameraControl] 后端同步失败:', error);
            }

            this.isStreaming = Object.values(results).some(r => r.success);
            return side === 'both' ? results : results[side];
        }

        /**
         * 停止视频流推流
         * @param {string} side - 'left', 'right', 或 'both'
         */
        async stopStreaming(side = 'both') {
            console.log(`[CameraControl] 停止推流: ${side}`);

            const sides = side === 'both' ? ['left', 'right'] : [side];

            for (const s of sides) {
                // 停止视频流
                if (this.streams[s]) {
                    this.streams[s].getTracks().forEach(track => {
                        track.stop();
                        console.log(`[CameraControl] ${s}侧摄像头轨道已停止`);
                    });
                    this.streams[s] = null;
                    console.log(`[CameraControl] ${s}侧摄像头推流已停止`);

                    // 触发事件
                    this._emitEvent('streaming-stopped', { side: s });
                }
            }

            // 同步到后端
            try {
                const response = await fetch('/api/camera/stop-streaming', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ side })
                });
                await response.json();
            } catch (error) {
                console.error('[CameraControl] 后端同步失败:', error);
            }

            this.isStreaming = this.streams.left !== null || this.streams.right !== null;
            return { success: true };
        }

        /**
         * 开始录制视频
         * @param {object|string} pathOrConfig - 输出文件基础路径，或 {left: binFileName, right: binFileName, taskId}
         * @param {object} metadata - 元数据
         */
        async startRecording(pathOrConfig, metadata = {}) {
            console.log('[CameraControl] 开始录制视频（后端录制）');
            console.log('[CameraControl] 路径配置:', pathOrConfig);
            console.log('[CameraControl] 元数据:', metadata);

            // 注意：这里不再检查 isStreaming
            // 因为后端 camera_server 通过 ffmpeg 独立访问摄像头，不依赖前端推流

            this.currentRecordingMetadata = metadata;
            const results = {};

            // 判断是新格式（bin文件名）还是旧格式（路径）
            const isNewFormat = typeof pathOrConfig === 'object' && pathOrConfig.left !== undefined;

            // 构建请求参数
            const recordings = [];

            for (const side of ['left', 'right']) {
                // 检查该侧摄像头是否已配置
                if (!this.selectedCameras[side]) {
                    console.warn(`[CameraControl] ${side}侧摄像头未配置，跳过录制`);
                    continue;
                }

                // 构建输出文件名
                let outputFilename;
                if (isNewFormat) {
                    // 新格式：使用bin文件名
                    const binFileName = pathOrConfig[side];
                    if (!binFileName) {
                        console.warn(`[CameraControl] ${side}侧未提供bin文件名，跳过录制`);
                        continue;
                    }
                    // 例如：R001_L_260614_153129.mp4
                    outputFilename = `${binFileName}.mp4`;
                    console.log(`[CameraControl] ${side}侧输出文件名: ${outputFilename}`);
                } else {
                    // 旧格式：使用传统路径（不应该再使用）
                    console.warn('[CameraControl] 使用旧格式路径，建议更新代码');
                    outputFilename = `${pathOrConfig.split('/').pop()}.mp4`;
                }

                recordings.push({
                    side: side,
                    output_filename: outputFilename
                });
            }

            if (recordings.length === 0) {
                console.error('[CameraControl] 没有可录制的摄像头');
                return { success: false, error: '没有可录制的摄像头' };
            }

            // 调用后端 API 启动录制
            try {
                const response = await fetch('/api/camera/start-recording', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ recordings, metadata })
                });

                const backendResult = await response.json();
                console.log('[CameraControl] 后端录制启动结果:', backendResult);

                // 解析后端返回结果
                if (backendResult.success) {
                    for (const recording of recordings) {
                        results[recording.side] = {
                            success: true,
                            fileName: recording.output_filename
                        };
                    }
                    this.isRecording = true;
                } else {
                    return { success: false, error: backendResult.error || '后端录制启动失败' };
                }

            } catch (error) {
                console.error('[CameraControl] 后端录制启动失败:', error);
                return { success: false, error: error.message };
            }

            return results;
        }

        /**
         * 停止录制视频
         */
        async stopRecording() {
            console.log('[CameraControl] 停止录制视频（后端录制）');

            if (!this.isRecording) {
                console.warn('[CameraControl] 当前没有正在录制的视频');
                return { success: false, error: '没有正在录制的视频' };
            }

            // 调用后端 API 停止录制
            try {
                const response = await fetch('/api/camera/stop-recording', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                const backendResult = await response.json();
                console.log('[CameraControl] 后端录制停止结果:', backendResult);

                this.isRecording = false;
                this.currentRecordingMetadata = null;

                return backendResult;
            } catch (error) {
                console.error('[CameraControl] 后端录制停止失败:', error);
                return { success: false, error: error.message };
            }
        }

        /**
         * 获取视频预览流
         * @param {string} side - 'left' 或 'right'
         */
        getPreviewStream(side) {
            return this.streams[side];
        }

        /**
         * 将视频流绑定到video元素
         * @param {string} side - 'left' 或 'right'
         * @param {HTMLVideoElement} videoElement - video元素
         */
        attachStreamToVideo(side, videoElement) {
            if (!this.streams[side]) {
                console.warn(`[CameraControl] ${side}侧摄像头未推流`);
                return false;
            }

            videoElement.srcObject = this.streams[side];
            videoElement.play();
            console.log(`[CameraControl] ${side}侧视频流已绑定到video元素`);
            return true;
        }

        /**
         * 触发自定义事件
         */
        _emitEvent(eventName, detail) {
            const event = new CustomEvent(`camera-${eventName}`, { detail });
            window.dispatchEvent(event);
        }

        /**
         * 获取当前状态
         */
        getStatus() {
            return {
                isStreaming: this.isStreaming,
                isRecording: this.isRecording,
                availableCameras: this.availableCameras.length,
                selectedCameras: {
                    left: this.selectedCameras.left?.label || null,
                    right: this.selectedCameras.right?.label || null
                }
            };
        }
    }

    // ==================== 全局初始化 ====================
    let cameraControl = null;

    async function initCameraControl() {
        if (!cameraControl) {
            cameraControl = new CameraControl();
            const success = await cameraControl.init();

            if (success) {
                window.cameraControl = cameraControl;
                console.log('[CameraControl] 控制器已挂载到 window.cameraControl');
            } else {
                console.error('[CameraControl] 控制器初始化失败');
            }
        }
    }

    // 如果DOM已经加载，直接初始化
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initCameraControl);
    } else {
        initCameraControl();
    }

    console.log('[CameraControl] 脚本加载完成');

})();
