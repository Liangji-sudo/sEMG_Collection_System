/**
 * camera-ui.js - 摄像头UI交互控制
 * ================================
 *
 * 对标 ble_control.js 的 UI 模式：
 * - 通过 window.CameraControl (WebSocket直连) 与后端通信
 * - 实时预览帧由后端主动推送
 * - 配置弹窗、预览弹窗的管理
 */

(function() {
    'use strict';

    console.log('[CameraUI] 脚本加载开始...');

    let isCameraStreaming = false;  // 是否有摄像头已打开
    let updateStatusInterval = null;

    // 等待DOM加载完成
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    function init() {
        console.log('[CameraUI] 初始化开始...');

        // 等待 CameraControl 模块加载（不要求WebSocket已连接）
        waitForCameraControl(() => {
            // 设置回调
            setupCallbacks();

            // 绑定UI事件（无论是否连接，先绑定）
            bindEvents();

            // 启动状态更新
            startStatusUpdates();

            console.log('[CameraUI] 初始化完成');
        });
    }

    function waitForCameraControl(callback) {
        // 只要 CameraControl 模块存在就立即绑定事件（不要求WS已连接）
        if (window.CameraControl) {
            callback();
            return;
        }

        let attempts = 0;
        const maxAttempts = 50;
        const checkInterval = setInterval(() => {
            attempts++;
            if (window.CameraControl) {
                clearInterval(checkInterval);
                console.log('[CameraUI] CameraControl 模块已就绪');
                callback();
            } else if (attempts >= maxAttempts) {
                clearInterval(checkInterval);
                console.error('[CameraUI] CameraControl 模块加载超时，使用降级模式');
                // 模块不可用时绑定降级事件
                bindEventsFallback();
                startStatusUpdates();
            }
        }, 100);
    }

    function setupCallbacks() {
        if (!window.CameraControl) return;

        // 预览帧回调
        window.CameraControl.onPreviewFrame = function(side, frameBase64) {
            updatePreviewImage(side, frameBase64);
        };

        // 扫描结果回调
        window.CameraControl.onScanResult = function(devices) {
            updateCameraSelects(devices);
        };

        // 连接状态变化
        window.CameraControl.onStatusChange = function(status) {
            if (status.connected) {
                console.log('[CameraUI] Camera Server 已连接');
            } else {
                console.log('[CameraUI] Camera Server 断开');
                // 更新UI状态
                updateCameraStatus('left', '未连接', false);
                updateCameraStatus('right', '未连接', false);
                isCameraStreaming = false;
                updateCameraStreamButton(false);
            }
        };
    }

    // ==================== 事件绑定 ====================

    function bindEvents() {
        // 摄像头推流按钮
        const cameraStreamBtn = document.getElementById('cameraStreamBtn');
        if (cameraStreamBtn) {
            cameraStreamBtn.addEventListener('click', handleCameraStreamToggle);
        }

        // 预览按钮
        const leftPreviewBtn = document.getElementById('leftCameraPreviewBtn');
        if (leftPreviewBtn) {
            leftPreviewBtn.addEventListener('click', () => openCameraPreview('left'));
        }

        const rightPreviewBtn = document.getElementById('rightCameraPreviewBtn');
        if (rightPreviewBtn) {
            rightPreviewBtn.addEventListener('click', () => openCameraPreview('right'));
        }

        // 预览弹窗关闭
        const closePreviewBtn = document.getElementById('closeCameraPreviewBtn');
        if (closePreviewBtn) {
            closePreviewBtn.addEventListener('click', closeCameraPreview);
        }

        // 配置弹窗关闭
        const closeConfigBtn = document.getElementById('closeCameraConfigBtn');
        if (closeConfigBtn) {
            closeConfigBtn.addEventListener('click', closeCameraConfig);
        }

        // 刷新摄像头列表
        const refreshBtn = document.getElementById('refreshCamerasBtn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', refreshCameraList);
        }

        // 应用配置并打开摄像头
        const applyBtn = document.getElementById('applyCameraConfigBtn');
        if (applyBtn) {
            applyBtn.addEventListener('click', applyCameraConfig);
        }
    }

    function bindEventsFallback() {
        // 降级模式：CameraControl 模块不可用，使用 HTTP API
        console.log('[CameraUI] 使用HTTP降级模式');

        // 绑定推流按钮
        const cameraStreamBtn = document.getElementById('cameraStreamBtn');
        if (cameraStreamBtn) {
            cameraStreamBtn.addEventListener('click', handleCameraStreamToggleFallback);
        }

        // 绑定配置弹窗按钮
        const closeConfigBtn = document.getElementById('closeCameraConfigBtn');
        if (closeConfigBtn) {
            closeConfigBtn.addEventListener('click', closeCameraConfig);
        }

        const refreshBtn = document.getElementById('refreshCamerasBtn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', refreshCameraListFallback);
        }

        const applyBtn = document.getElementById('applyCameraConfigBtn');
        if (applyBtn) {
            applyBtn.addEventListener('click', applyCameraConfigFallback);
        }

        // 绑定预览弹窗按钮
        const closePreviewBtn = document.getElementById('closeCameraPreviewBtn');
        if (closePreviewBtn) {
            closePreviewBtn.addEventListener('click', closeCameraPreview);
        }

        const leftPreviewBtn = document.getElementById('leftCameraPreviewBtn');
        if (leftPreviewBtn) {
            leftPreviewBtn.addEventListener('click', () => openCameraPreviewHTTP('left'));
        }

        const rightPreviewBtn = document.getElementById('rightCameraPreviewBtn');
        if (rightPreviewBtn) {
            rightPreviewBtn.addEventListener('click', () => openCameraPreviewHTTP('right'));
        }
    }

    // ==================== HTTP降级处理函数 ====================

    async function refreshCameraListFallback() {
        try {
            const response = await fetch('/api/camera/list');
            const result = await response.json();
            if (result.success && result.devices) {
                updateCameraSelects(result.devices);
                showToast(`找到 ${result.devices.length} 个摄像头`, 'info');
            } else {
                showToast('扫描失败: ' + (result.error || '未知错误'), 'error');
            }
        } catch (err) {
            showToast('Camera Server 未连接', 'error');
        }
    }

    async function applyCameraConfigFallback() {
        const leftSelect = document.getElementById('leftCameraSelect');
        const rightSelect = document.getElementById('rightCameraSelect');
        const leftDeviceId = leftSelect?.value;
        const rightDeviceId = rightSelect?.value;

        if (!leftDeviceId && !rightDeviceId) {
            showToast('请至少选择一个摄像头', 'warning');
            return;
        }

        let hasError = false;

        for (const side of ['left', 'right']) {
            const select = side === 'left' ? leftSelect : rightSelect;
            const deviceId = side === 'left' ? leftDeviceId : rightDeviceId;
            if (!deviceId) continue;

            const deviceName = select.options[select.selectedIndex].text;
            try {
                const resp = await fetch('/api/camera/set-camera', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ side, device_name: deviceName, device_id: deviceId })
                });
                const result = await resp.json();
                if (result.success) {
                    updateCameraStatus(side, '已配置', true);
                    console.log(`[CameraUI] ✅ ${side}摄像头配置成功（HTTP）`);
                } else {
                    showToast(`${side}摄像头配置失败: ${result.error}`, 'error');
                    hasError = true;
                }
            } catch (err) {
                showToast(`${side}摄像头配置失败`, 'error');
                hasError = true;
            }
        }

        if (!hasError) {
            isCameraStreaming = true;
            updateCameraStreamButton(true);
            showToast('摄像头配置完成 🎬', 'success');
        }

        closeCameraConfig();
    }

    async function openCameraPreviewHTTP(side) {
        const modal = document.getElementById('cameraPreviewModal');
        if (!modal) return;
        modal.style.display = 'flex';
        await refreshPreviewFrameHTTP(side);
    }

    // ==================== 推流开关 ====================

    async function handleCameraStreamToggle() {
        console.log('[CameraUI] 摄像头推流按钮被点击');

        if (!isCameraStreaming) {
            // 打开配置弹窗
            await openCameraConfig();
        } else {
            // 关闭所有摄像头
            await stopAllCameras();
        }
    }

    async function handleCameraStreamToggleFallback() {
        // HTTP降级模式
        console.log('[CameraUI] 降级模式：使用HTTP API');
        if (!isCameraStreaming) {
            await openCameraConfigFallback();
        }
    }

    // ==================== 配置弹窗 ====================

    async function openCameraConfig() {
        console.log('[CameraUI] 打开摄像头配置弹窗');

        const modal = document.getElementById('cameraConfigModal');
        if (!modal) return;

        // 枚举摄像头：优先WebSocket直连，降级HTTP
        if (window.CameraControl && window.CameraControl.isConnected()) {
            const result = await window.CameraControl.scanCameras();
            if (result.success) {
                updateCameraSelects(result.devices);
            } else {
                showToast('扫描摄像头失败: ' + (result.error || '未知错误'), 'error');
            }
        } else {
            // HTTP降级扫描
            console.log('[CameraUI] WS未连接，使用HTTP降级扫描');
            await refreshCameraListFallback();
        }

        modal.style.display = 'flex';
    }

    async function openCameraConfigFallback() {
        // HTTP降级：通过后端API扫描
        const modal = document.getElementById('cameraConfigModal');
        if (!modal) return;

        try {
            const response = await fetch('/api/camera/list');
            const result = await response.json();
            if (result.success && result.devices) {
                updateCameraSelects(result.devices);
            }
        } catch (err) {
            showToast('扫描摄像头失败', 'error');
        }

        modal.style.display = 'flex';
    }

    function closeCameraConfig() {
        const modal = document.getElementById('cameraConfigModal');
        if (modal) {
            modal.style.display = 'none';
        }
    }

    async function refreshCameraList() {
        console.log('[CameraUI] 刷新摄像头列表...');

        if (window.CameraControl && window.CameraControl.isConnected()) {
            const result = await window.CameraControl.scanCameras();
            if (result.success) {
                updateCameraSelects(result.devices);
                showToast(`找到 ${result.devices.length} 个摄像头`, 'info');
            } else {
                showToast('扫描失败: ' + (result.error || '未知错误'), 'error');
            }
        } else {
            // HTTP降级
            try {
                const response = await fetch('/api/camera/list');
                const result = await response.json();
                if (result.success && result.devices) {
                    updateCameraSelects(result.devices);
                }
            } catch (err) {
                showToast('Camera Server 未连接', 'error');
            }
        }
    }

    function updateCameraSelects(devices) {
        const leftSelect = document.getElementById('leftCameraSelect');
        const rightSelect = document.getElementById('rightCameraSelect');

        if (leftSelect && rightSelect) {
            // 保存当前选择
            const prevLeft = leftSelect.value;
            const prevRight = rightSelect.value;

            leftSelect.innerHTML = '<option value="">请选择摄像头...</option>';
            rightSelect.innerHTML = '<option value="">请选择摄像头...</option>';

            devices.forEach((camera, index) => {
                const name = camera.name || `USB摄像头 ${index + 1}`;
                const id = camera.id || camera.name;

                const opt1 = document.createElement('option');
                opt1.value = id;
                opt1.textContent = name;
                leftSelect.appendChild(opt1);

                const opt2 = document.createElement('option');
                opt2.value = id;
                opt2.textContent = name;
                rightSelect.appendChild(opt2);
            });

            // 自动分配
            if (devices.length === 1) {
                leftSelect.value = devices[0].id || devices[0].name;
                console.log('[CameraUI] 1个摄像头，已分配给左手');
            } else if (devices.length >= 2) {
                leftSelect.value = devices[0].id || devices[0].name;
                rightSelect.value = devices[1].id || devices[1].name;
                console.log('[CameraUI] 多个摄像头，已自动分配前两个');
            }

            // 恢复之前的选择
            if (prevLeft && devices.find(d => (d.id || d.name) === prevLeft)) {
                leftSelect.value = prevLeft;
            }
            if (prevRight && devices.find(d => (d.id || d.name) === prevRight)) {
                rightSelect.value = prevRight;
            }

            // 防重复选择
            leftSelect.onchange = () => {
                if (leftSelect.value && leftSelect.value === rightSelect.value) {
                    alert('同一摄像头不能同时分配给左手和右手');
                    leftSelect.value = '';
                }
            };
            rightSelect.onchange = () => {
                if (rightSelect.value && rightSelect.value === leftSelect.value) {
                    alert('同一摄像头不能同时分配给左手和右手');
                    rightSelect.value = '';
                }
            };
        }
    }

    // ==================== 应用配置并打开摄像头 ====================

    async function applyCameraConfig() {
        console.log('[CameraUI] 应用摄像头配置并打开...');

        const leftSelect = document.getElementById('leftCameraSelect');
        const rightSelect = document.getElementById('rightCameraSelect');

        const leftDeviceId = leftSelect?.value;
        const rightDeviceId = rightSelect?.value;

        if (!leftDeviceId && !rightDeviceId) {
            showToast('请至少选择一个摄像头', 'warning');
            return;
        }

        const useDirectWS = window.CameraControl && window.CameraControl.isConnected();
        let hasError = false;

        if (!useDirectWS) {
            console.log('[CameraUI] WS未连接，使用HTTP降级配置摄像头');
        }

        // 配置并打开左手摄像头
        if (leftDeviceId) {
            const leftDeviceName = leftSelect.options[leftSelect.selectedIndex].text;

            if (useDirectWS) {
                const setResult = await window.CameraControl.setCamera('left', leftDeviceName, leftDeviceId);
                if (!setResult.success) {
                    showToast('左手摄像头配置失败: ' + setResult.error, 'error');
                    hasError = true;
                } else {
                    const openResult = await window.CameraControl.openCamera('left');
                    if (openResult.success) {
                        updateCameraStatus('left', '预览中', true);
                        console.log('[CameraUI] ✅ 左手摄像头已打开（WS）');
                    } else {
                        showToast('左手摄像头打开失败: ' + openResult.error, 'error');
                        hasError = true;
                    }
                }
            } else {
                // HTTP 降级模式：只配置，不打开（旧HLS模式，由realtimeEngine控制录制）
                try {
                    const resp = await fetch('/api/camera/set-camera', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ side: 'left', device_name: leftDeviceName, device_id: leftDeviceId })
                    });
                    const result = await resp.json();
                    if (result.success) {
                        updateCameraStatus('left', '已配置（HTTP）', true);
                        console.log('[CameraUI] ✅ 左手摄像头配置成功（HTTP）');
                    } else {
                        showToast('左手摄像头配置失败: ' + (result.error || ''), 'error');
                        hasError = true;
                    }
                } catch (err) {
                    showToast('左手摄像头配置失败', 'error');
                    hasError = true;
                }
            }
        }

        // 配置并打开右手摄像头
        if (rightDeviceId) {
            const rightDeviceName = rightSelect.options[rightSelect.selectedIndex].text;

            if (useDirectWS) {
                const setResult = await window.CameraControl.setCamera('right', rightDeviceName, rightDeviceId);
                if (!setResult.success) {
                    showToast('右手摄像头配置失败: ' + setResult.error, 'error');
                    hasError = true;
                } else {
                    const openResult = await window.CameraControl.openCamera('right');
                    if (openResult.success) {
                        updateCameraStatus('right', '预览中', true);
                        console.log('[CameraUI] ✅ 右手摄像头已打开（WS）');
                    } else {
                        showToast('右手摄像头打开失败: ' + openResult.error, 'error');
                        hasError = true;
                    }
                }
            } else {
                try {
                    const resp = await fetch('/api/camera/set-camera', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ side: 'right', device_name: rightDeviceName, device_id: rightDeviceId })
                    });
                    const result = await resp.json();
                    if (result.success) {
                        updateCameraStatus('right', '已配置（HTTP）', true);
                        console.log('[CameraUI] ✅ 右手摄像头配置成功（HTTP）');
                    } else {
                        showToast('右手摄像头配置失败: ' + (result.error || ''), 'error');
                        hasError = true;
                    }
                } catch (err) {
                    showToast('右手摄像头配置失败', 'error');
                    hasError = true;
                }
            }
        }

        if (!hasError) {
            isCameraStreaming = true;
            updateCameraStreamButton(true);
            if (useDirectWS) {
                showToast('摄像头已打开，实时预览中 🎥', 'success');
            } else {
                showToast('摄像头已配置（HTTP模式），采集时自动录制', 'success');
            }
        }

        closeCameraConfig();
    }

    // ==================== 停止所有摄像头 ====================

    async function stopAllCameras() {
        console.log('[CameraUI] 停止所有摄像头...');

        if (window.CameraControl && window.CameraControl.isConnected()) {
            const results = await Promise.all([
                window.CameraControl.closeCamera('left').catch(() => ({ success: false })),
                window.CameraControl.closeCamera('right').catch(() => ({ success: false }))
            ]);
            console.log('[CameraUI] 关闭结果:', results);
        }

        // 清空所有预览帧，避免残留上一次的画面
        clearPreviewImages();

        isCameraStreaming = false;
        updateCameraStreamButton(false);
        updateCameraStatus('left', '已配置', false);
        updateCameraStatus('right', '已配置', false);
        showToast('摄像头已关闭', 'info');
    }

    function clearPreviewImages() {
        // 清空 DOM 中的预览图
        ['left', 'right'].forEach(side => {
            const img = document.getElementById(`${side}CameraPreview`);
            if (img) img.src = '';
        });
        // 清空 CameraControl 中缓存的帧
        if (window.CameraControl) {
            window.CameraControl.clearPreviewFrames();
        }
    }

    // ==================== 预览弹窗 ====================

    async function openCameraPreview(side) {
        console.log('[CameraUI] 打开摄像头预览:', side);

        const modal = document.getElementById('cameraPreviewModal');
        if (!modal) return;

        modal.style.display = 'flex';

        // 确保已订阅预览
        if (window.CameraControl && window.CameraControl.isConnected()) {
            window.CameraControl.subscribePreview(side);
        } else {
            // HTTP降级：手动获取一帧
            await refreshPreviewFrameHTTP(side);
        }
    }

    function closeCameraPreview() {
        const modal = document.getElementById('cameraPreviewModal');
        if (modal) {
            modal.style.display = 'none';
        }

        // 取消预览订阅（但不关闭摄像头）
        if (window.CameraControl) {
            window.CameraControl.unsubscribePreview('left');
            window.CameraControl.unsubscribePreview('right');
        }

        // 清空预览图
        const leftImg = document.getElementById('leftCameraPreview');
        const rightImg = document.getElementById('rightCameraPreview');
        if (leftImg) leftImg.src = '';
        if (rightImg) rightImg.src = '';

        console.log('[CameraUI] 预览窗口已关闭');
    }

    function updatePreviewImage(side, frameBase64) {
        const imgElement = document.getElementById(`${side}CameraPreview`);
        if (imgElement && frameBase64) {
            imgElement.src = `data:image/jpeg;base64,${frameBase64}`;
        }
    }

    async function refreshPreviewFrameHTTP(side) {
        // HTTP降级：手动请求预览帧
        try {
            const response = await fetch('/api/camera/get-preview-frame', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ side })
            });
            const result = await response.json();
            if (result.success && result.frame) {
                updatePreviewImage(side, result.frame);
            }
        } catch (err) {
            console.error(`[CameraUI] HTTP预览帧获取失败:`, err);
        }
    }

    // 暴露到全局供HTML onclick使用（降级模式）
    window.refreshPreviewFrame = refreshPreviewFrameHTTP;

    // ==================== 状态显示 ====================

    function updateCameraStreamButton(streaming) {
        const btn = document.getElementById('cameraStreamBtn');
        const btnText = document.getElementById('cameraStreamBtnText');
        const badge = document.getElementById('cameraStreamStatus');
        const info = document.getElementById('cameraStreamInfo');

        if (!btn) return;

        if (streaming) {
            btn.className = 'config-btn load-btn';
            btn.style.background = '#ef4444';
            btn.style.color = 'white';
            if (btnText) btnText.textContent = '关闭摄像头';
            if (badge) {
                badge.className = 'status-badge connected';
                badge.textContent = '预览中';
            }
            if (info) info.textContent = '摄像头已打开，预览中';
        } else {
            btn.className = 'config-btn load-btn';
            btn.style.background = '';
            btn.style.color = '';
            if (btnText) btnText.textContent = '打开摄像头';
            if (badge) {
                badge.className = 'status-badge disconnected';
                badge.textContent = '未打开';
            }
            if (info) info.textContent = '点击按钮配置并打开摄像头';
        }
    }

    function updateCameraStatus(side, statusText, showPreview) {
        console.log(`[CameraUI] updateCameraStatus: ${side}, ${statusText}, showPreview=${showPreview}`);

        const statusEl = document.getElementById(`${side}CameraStatus`);
        const previewBtn = document.getElementById(`${side}CameraPreviewBtn`);

        if (statusEl) {
            const span = statusEl.querySelector('span');
            if (span) {
                span.textContent = statusText;
            }
        }

        if (previewBtn) {
            previewBtn.style.display = showPreview ? 'inline-block' : 'none';
        }
    }

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
                        const span = diskPercentEl.querySelector('span');
                        if (span) span.textContent = `${data.storage.free_Percent} %`;
                    }
                }
            } catch (error) {
                // 静默处理
            }
        }, 2000);
    }

    function showToast(message, type = 'info') {
        if (window.collectionController && typeof window.collectionController.showToast === 'function') {
            window.collectionController.showToast(message, type);
        } else {
            console.log(`[Toast] ${message}`);
        }
    }

    console.log('[CameraUI] 脚本加载完成');

})();
