/**
 * camera-ui.js - 摄像头UI交互控制
 *
 * 功能：
 * 1. 绑定摄像头推流按钮事件
 * 2. 管理摄像头配置弹窗
 * 3. 管理摄像头预览弹窗
 * 4. 更新设备状态显示
 * 5. 与后端API通信
 */

(function() {
    'use strict';

    console.log('[CameraUI] 脚本加载开始...');

    let isCameraStreaming = false;
    let updateStatusInterval = null;

    // 等待DOM加载完成
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    function init() {
        console.log('[CameraUI] 初始化开始...');

        // 绑定摄像头推流按钮
        const cameraStreamBtn = document.getElementById('cameraStreamBtn');
        if (cameraStreamBtn) {
            cameraStreamBtn.addEventListener('click', handleCameraStreamToggle);
        }

        // 绑定摄像头预览按钮
        const cameraPreviewBtn = document.getElementById('cameraPreviewBtn');
        if (cameraPreviewBtn) {
            cameraPreviewBtn.addEventListener('click', openCameraPreview);
        }

        // 绑定预览弹窗关闭按钮
        const closeCameraPreviewBtn = document.getElementById('closeCameraPreviewBtn');
        if (closeCameraPreviewBtn) {
            closeCameraPreviewBtn.addEventListener('click', closeCameraPreview);
        }

        // 绑定配置弹窗关闭按钮
        const closeCameraConfigBtn = document.getElementById('closeCameraConfigBtn');
        if (closeCameraConfigBtn) {
            closeCameraConfigBtn.addEventListener('click', closeCameraConfig);
        }

        // 绑定刷新摄像头列表按钮
        const refreshCamerasBtn = document.getElementById('refreshCamerasBtn');
        if (refreshCamerasBtn) {
            refreshCamerasBtn.addEventListener('click', refreshCameraList);
        }

        // 绑定应用配置按钮
        const applyCameraConfigBtn = document.getElementById('applyCameraConfigBtn');
        if (applyCameraConfigBtn) {
            applyCameraConfigBtn.addEventListener('click', applyCameraConfig);
        }

        // 启动状态更新定时器
        startStatusUpdates();

        console.log('[CameraUI] 初始化完成');
    }

    /**
     * 处理摄像头推流开关
     */
    async function handleCameraStreamToggle() {
        console.log('[CameraUI] 摄像头推流按钮被点击');

        if (!isCameraStreaming) {
            // 启动推流：先打开配置弹窗
            await openCameraConfig();
        } else {
            // 停止推流
            await stopCameraStreaming();
        }
    }

    /**
     * 打开摄像头配置弹窗
     */
    async function openCameraConfig() {
        console.log('[CameraUI] 打开摄像头配置弹窗');

        const modal = document.getElementById('cameraConfigModal');
        if (!modal) return;

        // 枚举摄像头
        await refreshCameraList();

        // 显示弹窗
        modal.style.display = 'flex';
    }

    /**
     * 关闭摄像头配置弹窗
     */
    function closeCameraConfig() {
        const modal = document.getElementById('cameraConfigModal');
        if (modal) {
            modal.style.display = 'none';
        }
    }

    /**
     * 刷新摄像头列表
     */
    async function refreshCameraList() {
        console.log('[CameraUI] 刷新摄像头列表...');

        if (!window.cameraControl) {
            console.error('[CameraUI] cameraControl未初始化');
            return;
        }

        const cameras = await window.cameraControl.enumerateCameras();
        console.log(`[CameraUI] 找到 ${cameras.length} 个摄像头`);

        // 更新下拉列表
        const leftSelect = document.getElementById('leftCameraSelect');
        const rightSelect = document.getElementById('rightCameraSelect');

        if (leftSelect && rightSelect) {
            leftSelect.innerHTML = '<option value="">请选择摄像头...</option>';
            rightSelect.innerHTML = '<option value="">请选择摄像头...</option>';

            cameras.forEach((camera, index) => {
                const option1 = document.createElement('option');
                option1.value = camera.deviceId;
                option1.textContent = camera.label || `摄像头 ${index + 1}`;
                leftSelect.appendChild(option1);

                const option2 = document.createElement('option');
                option2.value = camera.deviceId;
                option2.textContent = camera.label || `摄像头 ${index + 1}`;
                rightSelect.appendChild(option2);
            });

            // 自动选择前两个摄像头
            if (cameras.length >= 1) {
                leftSelect.value = cameras[0].deviceId;
            }
            if (cameras.length >= 2) {
                rightSelect.value = cameras[1].deviceId;
            }
        }
    }

    /**
     * 应用摄像头配置并启动推流
     */
    async function applyCameraConfig() {
        console.log('[CameraUI] 应用摄像头配置...');

        const leftSelect = document.getElementById('leftCameraSelect');
        const rightSelect = document.getElementById('rightCameraSelect');

        const leftDeviceId = leftSelect?.value;
        const rightDeviceId = rightSelect?.value;

        if (!leftDeviceId && !rightDeviceId) {
            alert('请至少选择一个摄像头');
            return;
        }

        if (!window.cameraControl) {
            console.error('[CameraUI] cameraControl未初始化');
            return;
        }

        // 设置摄像头映射
        if (leftDeviceId) {
            const success = await window.cameraControl.setCameraMapping('left', leftDeviceId);
            if (!success) {
                console.error('[CameraUI] 左手摄像头映射失败');
                return;
            }
        }

        if (rightDeviceId) {
            const success = await window.cameraControl.setCameraMapping('right', rightDeviceId);
            if (!success) {
                console.error('[CameraUI] 右手摄像头映射失败');
                return;
            }
        }

        // 关闭配置弹窗
        closeCameraConfig();

        // 启动推流
        await startCameraStreaming();
    }

    /**
     * 启动摄像头推流
     */
    async function startCameraStreaming() {
        console.log('[CameraUI] 启动摄像头推流...');

        if (!window.cameraControl) {
            console.error('[CameraUI] cameraControl未初始化');
            return;
        }

        const result = await window.cameraControl.startStreaming('both');
        console.log('[CameraUI] 推流结果:', result);

        if (result.left?.success || result.right?.success) {
            isCameraStreaming = true;
            updateCameraStreamButton(true);
            updateCameraStatus('推流中', 'streaming');
            showToast('摄像头推流已启动 📹', 'success');
        } else {
            console.error('[CameraUI] 推流失败');
            showToast('摄像头推流启动失败', 'error');
        }
    }

    /**
     * 停止摄像头推流
     */
    async function stopCameraStreaming() {
        console.log('[CameraUI] 停止摄像头推流...');

        if (!window.cameraControl) {
            console.error('[CameraUI] cameraControl未初始化');
            return;
        }

        const result = await window.cameraControl.stopStreaming('both');
        console.log('[CameraUI] 停止结果:', result);

        isCameraStreaming = false;
        updateCameraStreamButton(false);
        updateCameraStatus('未启动', 'idle');
        showToast('摄像头推流已停止', 'info');
    }

    /**
     * 更新摄像头推流按钮状态
     */
    function updateCameraStreamButton(streaming) {
        const btn = document.getElementById('cameraStreamBtn');
        if (!btn) return;

        const icon = btn.querySelector('i');
        const title = btn.querySelector('.btn-title');
        const desc = btn.querySelector('.btn-desc');

        if (streaming) {
            btn.style.background = 'linear-gradient(145deg, #ef4444 0%, #dc2626 100%)';
            btn.style.boxShadow = '0 8px 24px rgba(239, 68, 68, 0.35)';
            if (icon) icon.className = 'fas fa-stop-circle';
            if (title) title.textContent = '停止推流';
            if (desc) desc.textContent = '点击停止视频流';
        } else {
            btn.style.background = 'white';
            btn.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.08)';
            if (icon) icon.className = 'fas fa-camera';
            if (title) title.textContent = '摄像头推流';
            if (desc) desc.textContent = '启动/停止视频流';
        }
    }

    /**
     * 更新设备状态窗口中的摄像头状态
     */
    function updateCameraStatus(statusText, mode) {
        const statusEl = document.getElementById('cameraStatus');
        const modeEl = document.getElementById('cameraMode');

        if (statusEl) {
            const span = statusEl.querySelector('span');
            const icon = statusEl.querySelector('i');
            if (span) span.textContent = statusText;

            if (mode === 'streaming') {
                statusEl.className = 'connection-status connected';
            } else if (mode === 'recording') {
                statusEl.className = 'connection-status recording';
            } else {
                statusEl.className = 'connection-status';
            }
        }

        if (modeEl) {
            const span = modeEl.querySelector('span');
            if (span) {
                if (mode === 'streaming') {
                    span.textContent = '推流中';
                } else if (mode === 'recording') {
                    span.textContent = '录制中';
                } else {
                    span.textContent = '--';
                }
            }
        }
    }

    /**
     * 打开摄像头预览弹窗
     */
    function openCameraPreview() {
        console.log('[CameraUI] 打开摄像头预览');

        if (!isCameraStreaming) {
            showToast('摄像头未推流', 'warning');
            return;
        }

        const modal = document.getElementById('cameraPreviewModal');
        if (!modal) return;

        // 绑定视频流到video元素
        const leftVideo = document.getElementById('leftCameraPreview');
        const rightVideo = document.getElementById('rightCameraPreview');

        if (leftVideo && window.cameraControl) {
            window.cameraControl.attachStreamToVideo('left', leftVideo);
        }

        if (rightVideo && window.cameraControl) {
            window.cameraControl.attachStreamToVideo('right', rightVideo);
        }

        // 显示弹窗
        modal.style.display = 'flex';
    }

    /**
     * 关闭摄像头预览弹窗
     */
    function closeCameraPreview() {
        const modal = document.getElementById('cameraPreviewModal');
        if (modal) {
            modal.style.display = 'none';
        }

        // 停止video播放
        const leftVideo = document.getElementById('leftCameraPreview');
        const rightVideo = document.getElementById('rightCameraPreview');

        if (leftVideo) {
            leftVideo.srcObject = null;
        }
        if (rightVideo) {
            rightVideo.srcObject = null;
        }
    }

    /**
     * 启动状态更新定时器
     */
    function startStatusUpdates() {
        // 每2秒更新一次磁盘空间
        updateStatusInterval = setInterval(async () => {
            try {
                const response = await fetch('/api/storage-volume');
                const data = await response.json();

                if (data.storage) {
                    const diskSpaceEl = document.getElementById('diskSpace');
                    const diskPercentEl = document.getElementById('diskPercent');

                    if (diskSpaceEl) {
                        diskSpaceEl.textContent = `${data.storage.volume} GB`;
                    }
                    if (diskPercentEl) {
                        diskPercentEl.querySelector('span').textContent = `${data.storage.free_Percent} %`;
                    }
                }
            } catch (error) {
                console.error('[CameraUI] 获取磁盘空间失败:', error);
            }
        }, 2000);
    }

    /**
     * 显示Toast提示
     */
    function showToast(message, type = 'info') {
        if (window.collectionController && typeof window.collectionController.showToast === 'function') {
            window.collectionController.showToast(message, type);
        } else {
            console.log(`[Toast] ${message}`);
        }
    }

    console.log('[CameraUI] 脚本加载完成');

})();
