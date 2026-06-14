/**
 * camera-control.js - 前端摄像头控制模块
 *
 * 功能：
 * 1. 枚举和识别USB摄像头
 * 2. 管理视频流（MediaStream）
 * 3. 控制视频录制（MediaRecorder）
 * 4. 与后端API同步状态
 * 5. 提供预览功能
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

            // MediaRecorder 对象
            this.recorders = {
                left: null,
                right: null
            };

            // 录制数据缓存
            this.recordedChunks = {
                left: [],
                right: []
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
         * 枚举所有可用的摄像头设备
         */
        async enumerateCameras() {
            console.log('[CameraControl] 枚举摄像头设备...');

            try {
                const devices = await navigator.mediaDevices.enumerateDevices();
                this.availableCameras = devices.filter(device => device.kind === 'videoinput');

                console.log(`[CameraControl] 找到 ${this.availableCameras.length} 个摄像头设备:`);
                this.availableCameras.forEach((camera, index) => {
                    console.log(`  [${index}] ${camera.label || `摄像头 ${index + 1}`} (${camera.deviceId})`);
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
                // 先停止录制
                if (this.recorders[s]) {
                    await this.stopRecording(s);
                }

                // 停止视频流
                if (this.streams[s]) {
                    this.streams[s].getTracks().forEach(track => track.stop());
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
         * @param {string} outputBasePath - 输出文件基础路径（不含扩展名和_left/_right后缀）
         * @param {object} metadata - 元数据
         */
        async startRecording(outputBasePath, metadata = {}) {
            console.log('[CameraControl] 开始录制视频');
            console.log('[CameraControl] 输出路径:', outputBasePath);
            console.log('[CameraControl] 元数据:', metadata);

            if (!this.isStreaming) {
                console.error('[CameraControl] 摄像头未推流，无法录制');
                return { success: false, error: '摄像头未推流' };
            }

            this.currentRecordingMetadata = metadata;
            const results = {};

            for (const side of ['left', 'right']) {
                if (!this.streams[side]) {
                    console.warn(`[CameraControl] ${side}侧摄像头未推流，跳过录制`);
                    continue;
                }

                try {
                    // 创建 MediaRecorder
                    const options = {
                        mimeType: 'video/webm;codecs=vp8',
                        videoBitsPerSecond: 2500000  // 2.5 Mbps
                    };

                    const recorder = new MediaRecorder(this.streams[side], options);

                    // 重置数据缓存
                    this.recordedChunks[side] = [];

                    // 数据可用事件
                    recorder.ondataavailable = (event) => {
                        if (event.data && event.data.size > 0) {
                            this.recordedChunks[side].push(event.data);
                        }
                    };

                    // 录制停止事件
                    recorder.onstop = async () => {
                        console.log(`[CameraControl] ${side}侧录制已停止，正在保存...`);
                        await this._saveRecording(side, outputBasePath, metadata);
                    };

                    // 开始录制（每1秒触发一次 dataavailable）
                    recorder.start(1000);
                    this.recorders[side] = recorder;

                    console.log(`[CameraControl] ${side}侧录制已开始`);
                    results[side] = { success: true };

                } catch (error) {
                    console.error(`[CameraControl] ${side}侧录制启动失败:`, error);
                    results[side] = { success: false, error: error.message };
                }
            }

            // 同步到后端
            try {
                const response = await fetch('/api/camera/start-recording', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ outputPath: outputBasePath, metadata })
                });
                const backendResult = await response.json();
                console.log('[CameraControl] 后端录制启动结果:', backendResult);
            } catch (error) {
                console.error('[CameraControl] 后端同步失败:', error);
            }

            this.isRecording = Object.values(results).some(r => r.success);
            return results;
        }

        /**
         * 停止录制视频
         */
        async stopRecording() {
            console.log('[CameraControl] 停止录制视频');

            const results = {};

            for (const side of ['left', 'right']) {
                if (this.recorders[side] && this.recorders[side].state !== 'inactive') {
                    this.recorders[side].stop();
                    results[side] = { success: true };
                }
            }

            // 同步到后端
            try {
                const response = await fetch('/api/camera/stop-recording', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                const backendResult = await response.json();
                console.log('[CameraControl] 后端录制停止结果:', backendResult);
            } catch (error) {
                console.error('[CameraControl] 后端同步失败:', error);
            }

            this.isRecording = false;
            return results;
        }

        /**
         * 保存录制的视频
         */
        async _saveRecording(side, outputBasePath, metadata) {
            if (this.recordedChunks[side].length === 0) {
                console.warn(`[CameraControl] ${side}侧无录制数据`);
                return;
            }

            try {
                // 合并所有数据块
                const blob = new Blob(this.recordedChunks[side], { type: 'video/webm' });
                console.log(`[CameraControl] ${side}侧录制数据大小: ${(blob.size / 1024 / 1024).toFixed(2)} MB`);

                // 生成文件名
                const fileName = `${outputBasePath.split(/[/\\]/).pop()}_${side}.webm`;

                // 下载文件（浏览器环境）
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.style.display = 'none';
                a.href = url;
                a.download = fileName;
                document.body.appendChild(a);
                a.click();

                // 清理
                setTimeout(() => {
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                }, 100);

                console.log(`[CameraControl] ${side}侧视频已保存: ${fileName}`);

                // 清空缓存
                this.recordedChunks[side] = [];
                this.recorders[side] = null;

            } catch (error) {
                console.error(`[CameraControl] ${side}侧视频保存失败:`, error);
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
