/**
 * camera-ui.js - 摄像头UI交互控制
 *
 * 功能：
 * 1. 绑定摄像头推流按钮事件
 * 2. 管理摄像头配置弹窗
 * 3. 管理摄像头预览弹窗
 * 4. 更新设备状态显示（集成到左右手状态中）
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

        // 绑定左手摄像头预览按钮
        const leftCameraPreviewBtn = document.getElementById('leftCameraPreviewBtn');
        if (leftCameraPreviewBtn) {
            leftCameraPreviewBtn.addEventListener('click', () => openCameraPreview('left'));
        }

        // 绑定右手摄像头预览按钮
        const rightCameraPreviewBtn = document.getElementById('rightCameraPreviewBtn');
        if (rightCameraPreviewBtn) {
            rightCameraPreviewBtn.addEventListener('click', () => openCameraPreview('right'));
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
        console.log(`[CameraUI] 找到 ${cameras.length} 个USB摄像头`);

        // 更新下拉列表
        const leftSelect = document.getElementById('leftCameraSelect');
        const rightSelect = document.getElementById('rightCameraSelect');

        if (leftSelect && rightSelect) {
            leftSelect.innerHTML = '<option value="">不使用</option>';
            rightSelect.innerHTML = '<option value="">不使用</option>';

            cameras.forEach((camera, index) => {
                const option1 = document.createElement('option');
                option1.value = camera.deviceId;
                option1.textContent = camera.label || `USB摄像头 ${index + 1}`;
                leftSelect.appendChild(option1);

                const option2 = document.createElement('option');
                option2.value = camera.deviceId;
                option2.textContent = camera.label || `USB摄像头 ${index + 1}`;
                rightSelect.appendChild(option2);
            });

            // 如果只有1个摄像头，默认不选择（用户自己决定给哪只手）
            if (cameras.length === 1) {
                console.log('[CameraUI] 检测到1个USB摄像头，请手动选择分配给左手或右手');
            } else if (cameras.length >= 2) {
                // 多个摄像头，分配前两个
                leftSelect.value = cameras[0].deviceId;
                rightSelect.value = cameras[1].deviceId;
                console.log('[CameraUI] 检测到多个USB摄像头，已自动分配前两个');
            }

            // 添加选择变化监听，防止同一摄像头分配给两只手
            leftSelect.addEventListener('change', () => {
                if (leftSelect.value && leftSelect.value === rightSelect.value) {
                    alert('同一摄像头不能同时分配给左手和右手');
                    leftSelect.value = '';
                }
            });

            rightSelect.addEventListener('change', () => {
                if (rightSelect.value && rightSelect.value === leftSelect.value) {
                    alert('同一摄像头不能同时分配给左手和右手');
                    rightSelect.value = '';
                }
            });
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
            updateCameraStatus('left', '已配置', false);
        }

        if (rightDeviceId) {
            const success = await window.cameraControl.setCameraMapping('right', rightDeviceId);
            if (!success) {
                console.error('[CameraUI] 右手摄像头映射失败');
                return;
            }
            updateCameraStatus('right', '已配置', false);
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

        // 检查哪些摄像头已配置
        const leftConfigured = window.cameraControl.selectedCameras.left !== null;
        const rightConfigured = window.cameraControl.selectedCameras.right !== null;

        if (!leftConfigured && !rightConfigured) {
            showToast('请先配置摄像头', 'warning');
            return;
        }

        // 只启动已配置的摄像头
        let leftSuccess = false;
        let rightSuccess = false;

        if (leftConfigured) {
            const result = await window.cameraControl.startStreaming('left');
            leftSuccess = result.success || result.left?.success;
            if (leftSuccess) {
                updateCameraStatus('left', '推流中', true);
                console.log('[CameraUI] 左手摄像头推流成功');
            }
        }

        if (rightConfigured) {
            const result = await window.cameraControl.startStreaming('right');
            rightSuccess = result.success || result.right?.success;
            if (rightSuccess) {
                updateCameraStatus('right', '推流中', true);
                console.log('[CameraUI] 右手摄像头推流成功');
            }
        }

        if (leftSuccess || rightSuccess) {
            isCameraStreaming = true;
            updateCameraStreamButton(true);
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
        updateCameraStatus('left', '已配置', false);
        updateCameraStatus('right', '已配置', false);
        showToast('摄像头推流已停止', 'info');
    }

    /**
     * 更新摄像头推流按钮状态
     */
    function updateCameraStreamButton(streaming) {
        const btn = document.getElementById('cameraStreamBtn');
        const btnText = document.getElementById('cameraStreamBtnText');
        const statusBadge = document.getElementById('cameraStreamStatus');
        const infoText = document.getElementById('cameraStreamInfo');

        if (!btn) return;

        if (streaming) {
            btn.className = 'config-btn load-btn';
            btn.style.background = '#ef4444';
            btn.style.color = 'white';
            if (btnText) btnText.textContent = '停止推流';
            if (statusBadge) {
                statusBadge.className = 'status-badge connected';
                statusBadge.textContent = '推流中';
            }
            if (infoText) infoText.textContent = '视频流已启动';
        } else {
            btn.className = 'config-btn load-btn';
            btn.style.background = '';
            btn.style.color = '';
            if (btnText) btnText.textContent = '启动推流';
            if (statusBadge) {
                statusBadge.className = 'status-badge disconnected';
                statusBadge.textContent = '未启动';
            }
            if (infoText) infoText.textContent = '点击按钮配置摄像头';
        }
    }

    /**
     * 更新单侧摄像头状态
     * @param {string} side - 'left' 或 'right'
     * @param {string} statusText - 状态文字
     * @param {boolean} showPreview - 是否显示预览按钮
     */
    function updateCameraStatus(side, statusText, showPreview) {
        console.log(`[CameraUI] updateCameraStatus: ${side}, ${statusText}, showPreview=${showPreview}`);

        const statusEl = document.getElementById(`${side}CameraStatus`);
        const previewBtn = document.getElementById(`${side}CameraPreviewBtn`);

        console.log(`[CameraUI] statusEl:`, statusEl);
        console.log(`[CameraUI] previewBtn:`, previewBtn);

        if (statusEl) {
            const span = statusEl.querySelector('span');
            if (span) {
                span.textContent = statusText;
                console.log(`[CameraUI] 已更新${side}状态文字为: ${statusText}`);
            }
        }

        if (previewBtn) {
            previewBtn.style.display = showPreview ? 'inline-block' : 'none';
            console.log(`[CameraUI] 已设置${side}预览按钮display为: ${showPreview ? 'inline-block' : 'none'}`);
        } else {
            console.error(`[CameraUI] 找不到${side}CameraPreviewBtn元素！`);
        }
    }

    /**
     * 打开摄像头预览弹窗
     * @param {string} side - 'left' 或 'right' 或 null（显示全部）
     */
    function openCameraPreview(side = null) {
        console.log('[CameraUI] 打开摄像头预览:', side || 'both');

        if (!isCameraStreaming) {
            showToast('摄像头未推流', 'warning');
            return;
        }

        const modal = document.getElementById('cameraPreviewModal');
        if (!modal) return;

        // 绑定视频流到video元素
        const leftVideo = document.getElementById('leftCameraPreview');
        const rightVideo = document.getElementById('rightCameraPreview');

        if (leftVideo && window.cameraControl && (side === null || side === 'left')) {
            window.cameraControl.attachStreamToVideo('left', leftVideo);
        }

        if (rightVideo && window.cameraControl && (side === null || side === 'right')) {
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
