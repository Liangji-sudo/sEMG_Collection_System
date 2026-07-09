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
    let _cameraConfigBusy = false;
    let _cameraWatchdogTimer = null;
    let _cameraEmergencyVisible = false;
    let _cameraDisconnectAlerted = false;
    let _cameraStatusPollInFlight = false;
    let _encodingHideTimer = null;
    let _cameraEncodingActive = false;
    let _cameraEncodingDrag = null;
    const _announcedEncodingDone = new Set();
    const _lastPreviewFrameAt = { left: 0, right: 0 };
    const _thumbFailureCount = { left: 0, right: 0 };
    const CAMERA_FRAME_STALE_MS = 7000;

    // 等待DOM加载完成
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    function init() {
        console.log('[CameraUI] 初始化开始...');
        ensureCameraRuntimeUi();

        // 等待 CameraControl 模块加载（不要求WebSocket已连接）
        waitForCameraControl(() => {
            // 设置回调
            setupCallbacks();

            // 绑定UI事件（无论是否连接，先绑定）
            bindEvents();

            // 启动状态更新
            startStatusUpdates();
            startCameraWatchdog();

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

    function ensureCameraRuntimeUi() {
        if (!document.getElementById('cameraRuntimeStyles')) {
            const style = document.createElement('style');
            style.id = 'cameraRuntimeStyles';
            style.textContent = `
                .camera-busy-panel {
                    display: none;
                    align-items: center;
                    gap: 12px;
                    margin: 0 0 18px 0;
                    padding: 12px 14px;
                    border: 1px solid #bfdbfe;
                    border-radius: 8px;
                    background: #eff6ff;
                    color: #1e3a8a;
                    font-size: 13px;
                    line-height: 1.45;
                }
                .camera-busy-panel.active { display: flex; }
                .camera-spinner {
                    width: 18px;
                    height: 18px;
                    border: 3px solid #bfdbfe;
                    border-top-color: #2563eb;
                    border-radius: 999px;
                    flex: 0 0 auto;
                    animation: camera-spin 0.85s linear infinite;
                }
                .camera-busy-message { font-weight: 700; color: #1d4ed8; }
                .camera-busy-detail { margin-top: 2px; color: #475569; }
                .camera-emergency-overlay {
                    position: fixed;
                    inset: 0;
                    z-index: 12000;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    background: rgba(15, 23, 42, 0.72);
                }
                .camera-emergency-card {
                    width: min(520px, calc(100vw - 32px));
                    border-radius: 10px;
                    background: #fff;
                    box-shadow: 0 24px 72px rgba(15, 23, 42, 0.35);
                    overflow: hidden;
                    border: 1px solid #fecaca;
                }
                .camera-emergency-head {
                    padding: 16px 18px;
                    background: #fef2f2;
                    border-bottom: 1px solid #fecaca;
                    color: #991b1b;
                    font-weight: 800;
                    font-size: 16px;
                }
                .camera-emergency-body {
                    padding: 18px;
                    color: #374151;
                    font-size: 14px;
                    line-height: 1.6;
                }
                .camera-emergency-actions {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 10px;
                    justify-content: flex-end;
                    padding: 0 18px 18px;
                }
                .camera-emergency-actions button {
                    border: none;
                    border-radius: 6px;
                    padding: 9px 14px;
                    cursor: pointer;
                    font-size: 13px;
                    font-weight: 700;
                }
                .camera-emergency-abort { background: #dc2626; color: #fff; }
                .camera-emergency-reconnect { background: #2563eb; color: #fff; }
                .camera-emergency-dismiss { background: #e5e7eb; color: #374151; }
                .camera-encoding-panel {
                    position: fixed;
                    right: 24px;
                    bottom: 24px;
                    z-index: 10000;
                    display: none;
                    width: min(520px, calc(100vw - 48px));
                    min-height: 128px;
                    padding: 18px 20px;
                    border: 2px solid #f59e0b;
                    border-radius: 8px;
                    background: #fffbeb;
                    box-shadow: 0 16px 42px rgba(15, 23, 42, 0.24);
                    color: #78350f;
                    font-size: 16px;
                    line-height: 1.45;
                }
                .camera-encoding-panel.active { display: block; }
                .camera-encoding-panel.working { animation: camera-panel-pulse 1.8s ease-in-out infinite; }
                .camera-encoding-head {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    gap: 16px;
                    margin-bottom: 12px;
                    cursor: move;
                    user-select: none;
                }
                .camera-encoding-title { font-weight: 900; font-size: 20px; color: #78350f; }
                .camera-encoding-dot {
                    width: 14px;
                    height: 14px;
                    border-radius: 999px;
                    background: #f59e0b;
                }
                .camera-encoding-panel.working .camera-encoding-dot {
                    animation: camera-dot-pulse 0.9s ease-in-out infinite;
                }
                .camera-encoding-percent {
                    font-size: 32px;
                    font-weight: 900;
                    color: #92400e;
                    margin-bottom: 8px;
                }
                .camera-encoding-detail { color: #92400e; font-weight: 700; }
                .camera-encoding-bar {
                    position: relative;
                    height: 14px;
                    margin: 12px 0 10px;
                    overflow: hidden;
                    border-radius: 999px;
                    background: #fde68a;
                }
                .camera-encoding-fill {
                    width: 0%;
                    height: 100%;
                    border-radius: inherit;
                    background: #f59e0b;
                    transition: width 0.35s ease;
                }
                .camera-encoding-panel.working .camera-encoding-fill::after {
                    content: '';
                    display: block;
                    width: 100%;
                    height: 100%;
                    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.45), transparent);
                    animation: camera-progress-shimmer 1.1s linear infinite;
                }
                @keyframes camera-panel-pulse {
                    0%, 100% { box-shadow: 0 16px 42px rgba(15, 23, 42, 0.24); }
                    50% { box-shadow: 0 18px 50px rgba(245, 158, 11, 0.35); }
                }
                @keyframes camera-dot-pulse {
                    0%, 100% { transform: scale(0.85); opacity: 0.55; }
                    50% { transform: scale(1.2); opacity: 1; }
                }
                @keyframes camera-progress-shimmer {
                    from { transform: translateX(-100%); }
                    to { transform: translateX(100%); }
                }
                @keyframes camera-spin { to { transform: rotate(360deg); } }
            `;
            document.head.appendChild(style);
        }

        const modal = document.getElementById('cameraConfigModal');
        const card = modal ? modal.firstElementChild : null;
        if (card && !document.getElementById('cameraConfigBusyPanel')) {
            const panel = document.createElement('div');
            panel.id = 'cameraConfigBusyPanel';
            panel.className = 'camera-busy-panel';
            panel.innerHTML = `
                <div class="camera-spinner" aria-hidden="true"></div>
                <div>
                    <div id="cameraConfigBusyMessage" class="camera-busy-message">正在处理摄像头...</div>
                    <div id="cameraConfigBusyDetail" class="camera-busy-detail">请稍候，正在等待后端返回。</div>
                </div>
            `;
            const header = card.firstElementChild;
            if (header && header.nextSibling) {
                card.insertBefore(panel, header.nextSibling);
            } else {
                card.insertBefore(panel, card.firstChild);
            }
        }

        if (card && !document.getElementById('swapCameraSidesBtn')) {
            const applyBtn = document.getElementById('applyCameraConfigBtn');
            const actions = applyBtn ? applyBtn.parentElement : null;
            if (actions) {
                const swapBtn = document.createElement('button');
                swapBtn.id = 'swapCameraSidesBtn';
                swapBtn.type = 'button';
                swapBtn.title = '交换左手和右手摄像头选择';
                swapBtn.style.cssText = 'background: #0f766e; color: white; border: none; border-radius: 6px; padding: 8px 16px; cursor: pointer; font-size: 13px;';
                swapBtn.innerHTML = '<i class="fas fa-exchange-alt"></i> 左右互换';
                actions.insertBefore(swapBtn, applyBtn);
            }
        }

        if (!document.getElementById('cameraEncodingPanel')) {
            const panel = document.createElement('div');
            panel.id = 'cameraEncodingPanel';
            panel.className = 'camera-encoding-panel';
            panel.innerHTML = `
                <div class="camera-encoding-head">
                    <div class="camera-encoding-title">视频后台压缩中</div>
                    <div class="camera-encoding-dot" aria-hidden="true"></div>
                </div>
                <div id="cameraEncodingPercent" class="camera-encoding-percent">0.0%</div>
                <div class="camera-encoding-bar" aria-hidden="true">
                    <div id="cameraEncodingFill" class="camera-encoding-fill"></div>
                </div>
                <div id="cameraEncodingDetail" class="camera-encoding-detail">正在计算剩余时间...</div>
            `;
            document.body.appendChild(panel);
            setupCameraEncodingPanelDrag(panel);
        } else {
            setupCameraEncodingPanelDrag(document.getElementById('cameraEncodingPanel'));
        }
    }

    function setupCameraEncodingPanelDrag(panel) {
        if (!panel || panel.dataset.dragReady === '1') return;
        panel.dataset.dragReady = '1';

        const saved = loadCameraEncodingPanelPosition();
        if (saved) {
            applyCameraEncodingPanelPosition(panel, saved.left, saved.top);
        }

        const handle = panel.querySelector('.camera-encoding-head') || panel;
        handle.title = '拖动移动视频压缩面板';

        handle.addEventListener('mousedown', (event) => {
            if (event.button !== 0) return;
            const rect = panel.getBoundingClientRect();
            _cameraEncodingDrag = {
                startX: event.clientX,
                startY: event.clientY,
                left: rect.left,
                top: rect.top
            };
            panel.style.transition = 'none';
            event.preventDefault();
        });

        document.addEventListener('mousemove', (event) => {
            if (!_cameraEncodingDrag) return;
            const nextLeft = _cameraEncodingDrag.left + (event.clientX - _cameraEncodingDrag.startX);
            const nextTop = _cameraEncodingDrag.top + (event.clientY - _cameraEncodingDrag.startY);
            applyCameraEncodingPanelPosition(panel, nextLeft, nextTop);
            event.preventDefault();
        });

        document.addEventListener('mouseup', () => {
            if (!_cameraEncodingDrag) return;
            _cameraEncodingDrag = null;
            const rect = panel.getBoundingClientRect();
            saveCameraEncodingPanelPosition(rect.left, rect.top);
            panel.style.transition = '';
        });

        window.addEventListener('resize', () => {
            const rect = panel.getBoundingClientRect();
            applyCameraEncodingPanelPosition(panel, rect.left, rect.top);
        });
    }

    function applyCameraEncodingPanelPosition(panel, left, top) {
        if (!panel) return;
        const rect = panel.getBoundingClientRect();
        const margin = 8;
        const maxLeft = Math.max(margin, window.innerWidth - rect.width - margin);
        const maxTop = Math.max(margin, window.innerHeight - rect.height - margin);
        const safeLeft = Math.max(margin, Math.min(Number(left) || margin, maxLeft));
        const safeTop = Math.max(margin, Math.min(Number(top) || margin, maxTop));
        panel.style.left = `${safeLeft}px`;
        panel.style.top = `${safeTop}px`;
        panel.style.right = 'auto';
        panel.style.bottom = 'auto';
    }

    function loadCameraEncodingPanelPosition() {
        try {
            const raw = localStorage.getItem('cameraEncodingPanelPosition');
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            if (!Number.isFinite(parsed.left) || !Number.isFinite(parsed.top)) return null;
            return parsed;
        } catch (_) {
            return null;
        }
    }

    function saveCameraEncodingPanelPosition(left, top) {
        try {
            localStorage.setItem('cameraEncodingPanelPosition', JSON.stringify({ left, top }));
        } catch (_) {}
    }

    function setCameraConfigBusy(isBusy, message = '正在处理摄像头...', detail = '请稍候，正在等待后端返回。') {
        ensureCameraRuntimeUi();
        _cameraConfigBusy = isBusy;

        const panel = document.getElementById('cameraConfigBusyPanel');
        const msgEl = document.getElementById('cameraConfigBusyMessage');
        const detailEl = document.getElementById('cameraConfigBusyDetail');
        if (panel) panel.classList.toggle('active', isBusy);
        if (msgEl) msgEl.textContent = message;
        if (detailEl) detailEl.textContent = detail;

        ['leftCameraSelect', 'rightCameraSelect', 'swapCameraSidesBtn', 'refreshCamerasBtn', 'applyCameraConfigBtn'].forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.disabled = isBusy || (id === 'swapCameraSidesBtn' && !canSwapCameraSides());
                el.style.opacity = isBusy ? '0.65' : '';
                el.style.cursor = isBusy ? 'wait' : '';
            }
        });
        if (!isBusy) updateSwapCameraButtonState();
    }

    function setCameraConfigMessage(message, detail) {
        setCameraConfigBusy(true, message, detail || '请勿反复点击，正在等待后端返回。');
    }

    function setupCallbacks() {
        if (!window.CameraControl) return;

        // 帧接收时间统计（调试用）
        let _lastFrameTime = 0;
        let _frameLogCount = 0;

        // 预览帧回调
        window.CameraControl.onPreviewFrame = function(side, frameBase64) {
            const now = performance.now();
            markCameraFrame(side);
            const delta = _lastFrameTime ? (now - _lastFrameTime).toFixed(0) : 0;
            _lastFrameTime = now;
            _frameLogCount++;

            // 前10帧 + 之后每30帧打印一次时间戳
            if (_frameLogCount <= 10 || _frameLogCount % 30 === 0) {
                const sizeKB = (frameBase64.length / 1024).toFixed(1);
                console.log(`[CameraUI] 📷 帧#${_frameLogCount} ${side} | 间隔=${delta}ms | 大小=${sizeKB}KB | time=${now.toFixed(0)}`);
            }
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
                _cameraDisconnectAlerted = false;
            } else {
                console.log('[CameraUI] Camera Server 断开');
                updateCameraStatus('left', '未连接', false);
                updateCameraStatus('right', '未连接', false);
                handleCameraDisconnect(status.code ? `Camera Server 连接已断开（code ${status.code}）` : 'Camera Server 连接已断开');
                isCameraStreaming = false;
                updateCameraStreamButton(false);
            }
        };

        window.CameraControl.onError = function(message) {
            console.error('[CameraUI] CameraControl 错误:', message);
            handleCameraDisconnect(message || 'Camera Server 连接错误');
        };

        // 录制状态变化（camera_server主动推送）
        window.CameraControl.onRecordingStatus = function(status) {
            console.log('[CameraUI] 录制状态更新:', status);
            syncCameraAvailabilityStatus(status);
            if (status.recording) {
                // 正在录制中 → 显示"写盘中"，禁用预览
                status.recording_sides.forEach(side => {
                    updateCameraStatus(side, '写盘中', false);
                });
                // 不在录制的side保持"预览中"
                ['left', 'right'].forEach(side => {
                    if (!status.recording_sides.includes(side) && window.CameraControl) {
                        const camState = window.CameraControl.getCameraState(side);
                        if (camState && camState.opened) {
                            updateCameraStatus(side, '预览中', true);
                        }
                    }
                });
            } else {
                // 录制已停止 → 恢复"预览中"
                status.preview_available.forEach(side => {
                    updateCameraStatus(side, '预览中', true);
                });
            }
        };

        window.CameraControl.onCameraStateChange = function(status) {
            syncCameraAvailabilityStatus(status);
            updateCameraEncodingStatus(status.encoding_details || [], status);
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

        const swapBtn = document.getElementById('swapCameraSidesBtn');
        if (swapBtn) {
            swapBtn.addEventListener('click', swapCameraSides);
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

        const swapBtn = document.getElementById('swapCameraSidesBtn');
        if (swapBtn) {
            swapBtn.addEventListener('click', swapCameraSides);
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
        setCameraConfigBusy(true, '正在扫描摄像头...', 'Camera Server 正在枚举 USB 摄像头，请稍候。');
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
        } finally {
            setCameraConfigBusy(false);
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

            const deviceName = select.options[select.selectedIndex].dataset.deviceName || select.options[select.selectedIndex].text;
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
        modal.style.display = 'flex';
        setCameraConfigBusy(true, '正在扫描摄像头...', '已打开配置窗口，正在等待后端返回摄像头列表。');

        try {
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
        } finally {
            setCameraConfigBusy(false);
        }
    }

    async function openCameraConfigFallback() {
        // HTTP降级：通过后端API扫描
        const modal = document.getElementById('cameraConfigModal');
        if (!modal) return;
        modal.style.display = 'flex';
        setCameraConfigBusy(true, '正在扫描摄像头...', 'Camera Server 正在枚举 USB 摄像头，请稍候。');

        try {
            const response = await fetch('/api/camera/list');
            const result = await response.json();
            if (result.success && result.devices) {
                updateCameraSelects(result.devices);
            }
        } catch (err) {
            showToast('扫描摄像头失败', 'error');
        } finally {
            setCameraConfigBusy(false);
        }
    }

    function closeCameraConfig() {
        setCameraConfigBusy(false);
        const modal = document.getElementById('cameraConfigModal');
        if (modal) {
            modal.style.display = 'none';
        }
    }

    async function refreshCameraList() {
        console.log('[CameraUI] 刷新摄像头列表...');
        setCameraConfigBusy(true, '正在刷新摄像头列表...', '正在等待 Camera Server 返回最新设备列表。');

        try {
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
                const response = await fetch('/api/camera/list');
                const result = await response.json();
                if (result.success && result.devices) {
                    updateCameraSelects(result.devices);
                }
            }
        } catch (err) {
            showToast('Camera Server 未连接', 'error');
        } finally {
            setCameraConfigBusy(false);
        }
    }

    function canSwapCameraSides() {
        const leftSelect = document.getElementById('leftCameraSelect');
        const rightSelect = document.getElementById('rightCameraSelect');
        return !!(leftSelect && rightSelect && leftSelect.value && rightSelect.value);
    }

    function updateSwapCameraButtonState() {
        const swapBtn = document.getElementById('swapCameraSidesBtn');
        if (!swapBtn) return;
        const enabled = canSwapCameraSides() && !_cameraConfigBusy;
        swapBtn.disabled = !enabled;
        swapBtn.style.opacity = enabled ? '' : '0.5';
        swapBtn.style.cursor = enabled ? 'pointer' : 'not-allowed';
    }

    function swapCameraSides() {
        const leftSelect = document.getElementById('leftCameraSelect');
        const rightSelect = document.getElementById('rightCameraSelect');
        if (!leftSelect || !rightSelect || !canSwapCameraSides()) {
            showToast('左右手摄像头都选择后才能互换', 'warning');
            return;
        }

        const leftValue = leftSelect.value;
        leftSelect.value = rightSelect.value;
        rightSelect.value = leftValue;
        updateSwapCameraButtonState();
        showToast('已互换左右手摄像头', 'info');
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
                const displayName = camera.display_name || camera.name || `USB摄像头 ${index + 1}`;
                const name = camera.name || `USB摄像头 ${index + 1}`;
                const id = camera.id || camera.name;

                const opt1 = document.createElement('option');
                opt1.value = id;
                opt1.textContent = displayName;
                opt1.dataset.deviceName = name;
                opt1.dataset.deviceId = id;
                opt1.title = `${name}\nID: ${id}`;
                leftSelect.appendChild(opt1);

                const opt2 = document.createElement('option');
                opt2.value = id;
                opt2.textContent = displayName;
                opt2.dataset.deviceName = name;
                opt2.dataset.deviceId = id;
                opt2.title = `${name}\nID: ${id}`;
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
                updateSwapCameraButtonState();
            };
            rightSelect.onchange = () => {
                if (rightSelect.value && rightSelect.value === leftSelect.value) {
                    alert('同一摄像头不能同时分配给左手和右手');
                    rightSelect.value = '';
                }
                updateSwapCameraButtonState();
            };
            updateSwapCameraButtonState();
        }
    }

    // ==================== 应用配置并打开摄像头 ====================

    /**
     * 检查相机/BLE side 匹配约束：
     * - 如果只连接了一侧 BLE 腕带，相机只能开对应侧
     * - 如果两侧都连了或都没连，不限制
     * @returns {string|null} 错误消息，null 表示通过
     */
    function checkCameraBleSideMatch(targetCameraSide) {
        // 获取 BLE 连接状态
        let bleLeft = false, bleRight = false;
        if (window.BleControl && window.BleControl.devices) {
            bleLeft = !!(window.BleControl.devices[1] && window.BleControl.devices[1].connected);
            bleRight = !!(window.BleControl.devices[2] && window.BleControl.devices[2].connected);
        }

        // 两侧都连了或都没连 → 不限制
        if (bleLeft === bleRight) return null;

        // 只有一侧 BLE 连接 → 相机必须匹配
        const bleSide = bleLeft ? '左手(设备1)' : '右手(设备2)';
        const expectedCamSide = bleLeft ? 'left' : 'right';

        if (targetCameraSide !== expectedCamSide) {
            return `蓝牙腕带只连接了${bleSide}，只能打开${bleLeft ? '左手' : '右手'}摄像头`;
        }
        return null;
    }

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

        // === side 匹配检查 ===
        if (leftDeviceId) {
            const err = checkCameraBleSideMatch('left');
            if (err) { showToast(err, 'warning'); return; }
        }
        if (rightDeviceId) {
            const err = checkCameraBleSideMatch('right');
            if (err) { showToast(err, 'warning'); return; }
        }

        const useDirectWS = window.CameraControl && window.CameraControl.isConnected();
        let hasError = false;
        setCameraConfigBusy(true, '正在打开摄像头...', '正在向 Camera Server 发送配置命令，请勿重复点击。');

        if (!useDirectWS) {
            console.log('[CameraUI] WS未连接，使用HTTP降级配置摄像头');
        }

        // 配置并打开左手摄像头
        if (leftDeviceId) {
            const leftDeviceName = leftSelect.options[leftSelect.selectedIndex].dataset.deviceName || leftSelect.options[leftSelect.selectedIndex].text;

            if (useDirectWS) {
                setCameraConfigMessage('正在配置左手摄像头...', leftDeviceName);
                const setResult = await window.CameraControl.setCamera('left', leftDeviceName, leftDeviceId);
                if (!setResult.success) {
                    showToast('左手摄像头配置失败: ' + setResult.error, 'error');
                    hasError = true;
                } else {
                    setCameraConfigMessage('正在打开左手摄像头...', '后端正在初始化视频采集，请稍候。');
                    const openResult = await window.CameraControl.openCamera('left');
                    if (openResult.success) {
                        markCameraFrame('left');
                        updateCameraStatus('left', '预览中', true);
                        startCameraThumbTimer();
                        console.log('[CameraUI] ✅ 左手摄像头已打开（WS）');
                    } else {
                        showToast('左手摄像头打开失败: ' + openResult.error, 'error');
                        hasError = true;
                    }
                }
            } else {
                // HTTP 降级模式：只配置，不打开（旧HLS模式，由realtimeEngine控制录制）
                try {
                    setCameraConfigMessage('正在配置左手摄像头...', leftDeviceName);
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
            const rightDeviceName = rightSelect.options[rightSelect.selectedIndex].dataset.deviceName || rightSelect.options[rightSelect.selectedIndex].text;

            if (useDirectWS) {
                setCameraConfigMessage('正在配置右手摄像头...', rightDeviceName);
                const setResult = await window.CameraControl.setCamera('right', rightDeviceName, rightDeviceId);
                if (!setResult.success) {
                    showToast('右手摄像头配置失败: ' + setResult.error, 'error');
                    hasError = true;
                } else {
                    setCameraConfigMessage('正在打开右手摄像头...', '后端正在初始化视频采集，请稍候。');
                    const openResult = await window.CameraControl.openCamera('right');
                    if (openResult.success) {
                        markCameraFrame('right');
                        updateCameraStatus('right', '预览中', true);
                        startCameraThumbTimer();
                        console.log('[CameraUI] ✅ 右手摄像头已打开（WS）');
                    } else {
                        showToast('右手摄像头打开失败: ' + openResult.error, 'error');
                        hasError = true;
                    }
                }
            } else {
                try {
                    setCameraConfigMessage('正在配置右手摄像头...', rightDeviceName);
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
                showToast('摄像头已打开，实时预览中', 'success');
            } else {
                showToast('摄像头已配置（HTTP模式），采集时自动录制', 'success');
            }
        }

        setCameraConfigBusy(false);
        closeCameraConfig();
    }

    // ==================== 停止所有摄像头 ====================

    async function stopAllCameras() {
        console.log('[CameraUI] 停止所有摄像头...');
        const btn = document.getElementById('cameraStreamBtn');
        const btnText = document.getElementById('cameraStreamBtnText');
        const oldText = btnText ? btnText.textContent : '';
        if (btn) {
            btn.disabled = true;
            btn.style.cursor = 'wait';
        }
        if (btnText) btnText.textContent = '正在关闭摄像头...';

        if (window.CameraControl && window.CameraControl.isConnected()) {
            const results = await Promise.all([
                window.CameraControl.closeCamera('left').catch(() => ({ success: false })),
                window.CameraControl.closeCamera('right').catch(() => ({ success: false }))
            ]);
            console.log('[CameraUI] 关闭结果:', results);
            const status = await window.CameraControl.getStatus?.();
            if (status && status.success !== false) {
                updateCameraEncodingStatus(status.encoding_details || [], status);
                if ((status.encoding_jobs || 0) > 0) {
                    const queued = status.encoding_queued_jobs || 0;
                    const active = status.encoding_active_jobs || 0;
                    showToast(`摄像头已断开，视频任务 ${status.encoding_jobs} 个（压缩中 ${active}，排队 ${queued}）`, 'info');
                }
            }
        }

        // 清空所有预览帧，避免残留上一次的画面
        clearPreviewImages();
        stopCameraThumbTimer();  // 停止缩略图定时器
        ['left', 'right'].forEach(side => {
            _lastPreviewFrameAt[side] = 0;
            _thumbFailureCount[side] = 0;
        });
        _cameraDisconnectAlerted = false;

        isCameraStreaming = false;
        updateCameraStreamButton(false);
        if (btn) {
            btn.disabled = false;
            btn.style.cursor = '';
        }
        if (btnText && oldText && isCameraStreaming) btnText.textContent = oldText;
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

    // ==================== 预览弹窗（按需拍照模式） ====================

    let _previewRefreshTimer = null;   // HTTP 降级自动刷新定时器
    let _previewFrameCount = 0;        // 帧计数
    let _previewFpsTimer = null;       // FPS 计算定时器
    let _previewSide = null;           // 当前预览的 side

    async function openCameraPreview(side) {
        console.log('[CameraUI] 打开摄像头预览:', side);

        const modal = document.getElementById('cameraPreviewModal');
        if (!modal) return;

        modal.style.display = 'flex';
        _previewFrameCount = 0;
        _previewSide = side;

        // 设置预览标题
        const title = document.getElementById('cameraPreviewTitle');
        if (title) title.textContent = `摄像头预览 - ${side === 'left' ? '左手' : '右手'}`;

        // 显示按需拍照按钮，隐藏实时流相关
        updatePreviewStatus(side, '点击「拍照」获取画面');
        showSnapshotButtons(true);

        // 自动拍第一张（如果摄像头已打开且有缓存帧）
        autoTakeFirstSnapshot(side);
    }

    function showSnapshotButtons(show) {
        const btnContainer = document.getElementById('snapshotBtnContainer');
        if (btnContainer) {
            btnContainer.style.display = show ? 'flex' : 'none';
        }
    }

    async function autoTakeFirstSnapshot(side) {
        // 如果摄像头已打开，后端 CameraCapture 应该已经有缓存帧了
        if (window.CameraControl && window.CameraControl.isConnected()) {
            const cached = window.CameraControl.getPreviewFrame(side);
            if (cached) {
                updatePreviewImage(side, cached);
                updatePreviewStatus(side, '已自动加载预览');
                return;
            }
            // 没有缓存帧，自动拍一张
            await takeSnapshot(side);
        } else {
            // HTTP 降级
            await refreshPreviewFrameHTTP(side);
        }
    }

    /** 按需拍照：通过 WebSocket 或 HTTP 获取一帧 */
    async function takeSnapshot(side) {
        if (!side) side = _previewSide || 'left';

        updatePreviewStatus(side, '拍照中...');

        if (window.CameraControl && window.CameraControl.isConnected()) {
            const result = await window.CameraControl.captureSnapshot(side);
            if (result.success && result.frame) {
                updatePreviewImage(side, result.frame);
                const source = result.source === 'cache' ? '实时缓存' : '摄像头抓帧';
                updatePreviewStatus(side, `${source} · ${(result.frame.length/1024).toFixed(1)}KB`);
                console.log(`[CameraUI] 📸 ${side}侧 拍照成功 (${result.source})`);
            } else {
                updatePreviewStatus(side, `拍照失败: ${result.error || '未知'}`);
                console.error(`[CameraUI] ${side}拍照失败:`, result.error);
            }
        } else {
            await refreshPreviewFrameHTTP(side);
        }
    }

    function closeCameraPreview() {
        // 停止 HTTP 自动刷新
        if (_previewRefreshTimer) {
            clearInterval(_previewRefreshTimer);
            _previewRefreshTimer = null;
        }
        if (_previewFpsTimer) {
            clearInterval(_previewFpsTimer);
            _previewFpsTimer = null;
        }
        _previewSide = null;

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

        // 清空状态文字
        ['left', 'right'].forEach(s => updatePreviewStatus(s, ''));

        console.log('[CameraUI] 预览窗口已关闭');
    }

    function updatePreviewStatus(side, text) {
        const el = document.getElementById(`${side}PreviewStatus`);
        if (el) el.textContent = text;
    }

    function updatePreviewImage(side, frameBase64) {
        const imgElement = document.getElementById(`${side}CameraPreview`);
        if (imgElement && frameBase64) {
            imgElement.src = `data:image/jpeg;base64,${frameBase64}`;
            _previewFrameCount++;
        }
    }

    async function refreshPreviewFrameHTTP(side) {
        // HTTP降级：手动请求预览帧
        updatePreviewStatus(side, '拍照中 (HTTP)...');
        try {
            const response = await fetch('/api/camera/get-preview-frame', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ side })
            });
            const result = await response.json();
            if (result.success && result.frame) {
                updatePreviewImage(side, result.frame);
                updatePreviewStatus(side, `HTTP拍照 · ${(result.frame.length/1024).toFixed(1)}KB`);
            } else {
                updatePreviewStatus(side, `拍照失败: ${result.error || '未知'}`);
            }
        } catch (err) {
            updatePreviewStatus(side, '拍照失败: 网络错误');
            console.error(`[CameraUI] HTTP预览帧获取失败:`, err);
        }
    }

    // 暴露到全局供HTML onclick使用
    window.refreshPreviewFrame = refreshPreviewFrameHTTP;
    window.takeSnapshot = takeSnapshot;  // 供 HTML 按钮调用

    // ==================== 状态显示 ====================

    function updateCameraStreamButton(streaming) {
        const btn = document.getElementById('cameraStreamBtn');
        const btnText = document.getElementById('cameraStreamBtnText');
        const badge = document.getElementById('cameraStreamStatus');

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
        } else {
            btn.className = 'config-btn load-btn';
            btn.style.background = '';
            btn.style.color = '';
            if (btnText) btnText.textContent = '打开摄像头';
            if (badge) {
                badge.className = 'status-badge disconnected';
                badge.textContent = '未打开';
            }
        }

        updateCameraCardState();
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
            // 根据状态文字设置颜色类名（文字+行级背景）
            statusEl.classList.remove('camera-preview', 'camera-recording');
            const rowEl = document.getElementById(`${side}CameraRow`);
            if (rowEl) rowEl.classList.remove('camera-preview', 'camera-recording');
            if (statusText === '预览中') {
                statusEl.classList.add('camera-preview');
                if (rowEl) rowEl.classList.add('camera-preview');
            } else if (statusText === '写盘中') {
                statusEl.classList.add('camera-recording');
                if (rowEl) rowEl.classList.add('camera-recording');
            }
        }

        if (previewBtn) {
            previewBtn.style.display = showPreview ? 'inline-block' : 'none';
        }

        // 同步更新下方相机卡片的左右手状态指示
        updateCameraCardState();
    }

    function formatDuration(seconds) {
        if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) {
            return '估算中';
        }
        const total = Math.max(0, Math.round(Number(seconds)));
        const min = Math.floor(total / 60);
        const sec = total % 60;
        if (min <= 0) return `${sec} 秒`;
        return `${min} 分 ${sec} 秒`;
    }

    function formatBytes(bytes) {
        const value = Number(bytes || 0);
        if (!Number.isFinite(value) || value <= 0) return '0 MB';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let size = value;
        let idx = 0;
        while (size >= 1024 && idx < units.length - 1) {
            size /= 1024;
            idx += 1;
        }
        return `${size.toFixed(idx >= 2 ? 1 : 0)} ${units[idx]}`;
    }

    function updateCameraEncodingStatus(details, status) {
        ensureCameraRuntimeUi();
        const panel = document.getElementById('cameraEncodingPanel');
        const percentEl = document.getElementById('cameraEncodingPercent');
        const fillEl = document.getElementById('cameraEncodingFill');
        const detailEl = document.getElementById('cameraEncodingDetail');
        if (!panel || !detailEl) return;

        const jobs = Array.isArray(details) ? details : [];
        if (jobs.length === 0) {
            _cameraEncodingActive = false;
            panel.classList.remove('working');
            panel.classList.remove('active');
            return;
        }

        const active = jobs.filter(job => job.status === 'encoding');
        const queued = jobs.filter(job => job.status === 'queued');
        const failed = jobs.filter(job => job.status === 'failed');
        const done = jobs.filter(job => job.status === 'done');
        _cameraEncodingActive = active.length > 0;

        panel.classList.add('active');
        if (_encodingHideTimer) {
            clearTimeout(_encodingHideTimer);
            _encodingHideTimer = null;
        }
        if (active.length > 0) {
            const first = active[0];
            const percent = Number(first.progress_percent || 0).toFixed(1);
            const eta = formatDuration(first.eta_seconds);
            const extra = active.length > 1 ? `，另有 ${active.length - 1} 个任务排队/压缩中` : '';
            panel.classList.add('working');
            if (percentEl) percentEl.textContent = `${percent}%`;
            if (fillEl) fillEl.style.width = `${Math.max(1, Math.min(100, Number(percent)))}%`;
            detailEl.textContent = `${first.side || ''} ${percent}% · 预计剩余 ${eta}${extra}`;
        } else if (queued.length > 0) {
            const queuedRawBytes = status && status.encoding_raw_bytes !== undefined
                ? status.encoding_raw_bytes
                : queued.reduce((sum, job) => sum + Number(job.raw_size || 0), 0);
            const freeBytes = status ? status.disk_free_bytes : null;
            const grace = status && status.encoding_idle_grace_seconds !== undefined
                ? status.encoding_idle_grace_seconds
                : (queued[0].idle_grace_seconds || 0);
            panel.classList.remove('working');
            if (percentEl) percentEl.textContent = `${queued.length} 个排队`;
            if (fillEl) fillEl.style.width = '1%';
            const freeText = freeBytes === null || freeBytes === undefined ? '' : `，磁盘剩余 ${formatBytes(freeBytes)}`;
            detailEl.textContent = `采集优先：${queued.length} 个视频等待空闲 ${grace}s 后单线程压缩，原始占用 ${formatBytes(queuedRawBytes)}${freeText}`;
        } else if (failed.length > 0) {
            panel.classList.remove('working');
            if (percentEl) percentEl.textContent = '失败';
            if (fillEl) fillEl.style.width = '100%';
            detailEl.textContent = `视频压缩失败：${failed[0].error || '请查看 camera_server 日志'}`;
        } else if (done.length > 0) {
            const newlyDone = done.filter(job => {
                const key = job.output_path || `${job.side || 'video'}:${job.started_at || ''}`;
                if (_announcedEncodingDone.has(key)) return false;
                _announcedEncodingDone.add(key);
                return true;
            });
            if (newlyDone.length > 0) {
                panel.classList.remove('working');
                if (percentEl) percentEl.textContent = '100%';
                if (fillEl) fillEl.style.width = '100%';
                detailEl.textContent = '视频压缩已完成，临时 MJPEG 已清理';
                _encodingHideTimer = setTimeout(() => {
                    const current = document.getElementById('cameraEncodingPanel');
                    if (current) current.classList.remove('active');
                }, 8000);
            }
        }
    }

    /**
     * 更新相机卡片整体状态：绿色边框/背景 + 左右手连接指示
     */
    function updateCameraCardState() {
        const card = document.getElementById('cameraCard');
        const leftSlot = document.getElementById('cameraLeftSlot');
        const rightSlot = document.getElementById('cameraRightSlot');

        // 获取左右手摄像头状态
        let leftState = null, rightState = null;
        if (window.CameraControl) {
            leftState = window.CameraControl.getCameraState('left');
            rightState = window.CameraControl.getCameraState('right');
        }

        const leftStreaming = !!(leftState && leftState.opened);
        const rightStreaming = !!(rightState && rightState.opened);
        const leftConfigured = !!(leftState && leftState.configured);
        const rightConfigured = !!(rightState && rightState.configured);

        // 更新左手槽位
        if (leftSlot) {
            leftSlot.classList.toggle('streaming', leftStreaming);
            leftSlot.classList.toggle('configured', leftConfigured && !leftStreaming);
            const text = leftSlot.querySelector('span:last-child');
            if (leftStreaming) {
                if (text) text.textContent = '左手: 预览中';
            } else if (leftConfigured) {
                if (text) text.textContent = '左手: 已配置';
            } else {
                if (text) text.textContent = '左手: 未配置';
            }
        }

        // 更新右手槽位
        if (rightSlot) {
            rightSlot.classList.toggle('streaming', rightStreaming);
            rightSlot.classList.toggle('configured', rightConfigured && !rightStreaming);
            const text = rightSlot.querySelector('span:last-child');
            if (rightStreaming) {
                if (text) text.textContent = '右手: 预览中';
            } else if (rightConfigured) {
                if (text) text.textContent = '右手: 已配置';
            } else {
                if (text) text.textContent = '右手: 未配置';
            }
        }

        // 更新卡片整体绿显
        if (card) {
            if (leftStreaming || rightStreaming) {
                card.classList.add('camera-connected');
            } else {
                card.classList.remove('camera-connected');
            }
        }
    }

    function startStatusUpdates() {
        // 每2秒更新一次磁盘空间
        updateStatusInterval = setInterval(async () => {
            try {
                const response = await fetch('/api/storage-volume');
                const data = await response.json();
                if (data.storage) {
                    const freePercent = parseFloat(data.storage.free_Percent) || 0;
                    const usedPercent = Math.max(0, 100 - freePercent);

                    // 总容量
                    const diskSpaceEl = document.getElementById('diskSpace');
                    if (diskSpaceEl) {
                        diskSpaceEl.textContent = `${data.storage.volume} GB`;
                    }

                    // 进度条 (红=已用)
                    const usedBar = document.getElementById('diskUsedBar');
                    if (usedBar) {
                        usedBar.style.width = `${usedPercent}%`;
                    }

                    // 百分比标签
                    const freeEl = document.getElementById('diskFreePercent');
                    if (freeEl) freeEl.textContent = `${freePercent.toFixed(1)}% 可用`;

                    const usedEl = document.getElementById('diskUsedPercent');
                    if (usedEl) usedEl.textContent = `${usedPercent.toFixed(1)}% 已用`;
                }
            } catch (error) {
                // 静默处理
            }

            let didCameraStatusPoll = false;
            try {
                if (!_cameraStatusPollInFlight && window.CameraControl && window.CameraControl.isConnected() && window.CameraControl.getStatus) {
                    _cameraStatusPollInFlight = true;
                    didCameraStatusPoll = true;
                    const cameraStatus = await window.CameraControl.getStatus();
                    if (cameraStatus && cameraStatus.success !== false) {
                        syncCameraAvailabilityStatus(cameraStatus);
                        updateCameraEncodingStatus(cameraStatus.encoding_details || [], cameraStatus);
                    }
                }
            } catch (error) {
                // 静默处理
            } finally {
                if (didCameraStatusPoll) _cameraStatusPollInFlight = false;
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

    function syncCameraAvailabilityStatus(status) {
        if (!status) return;

        const recordingSet = new Set(Array.isArray(status.recording_sides) ? status.recording_sides : []);
        const previewSet = new Set(Array.isArray(status.preview_available) ? status.preview_available : []);

        if (status.recording && typeof status.recording === 'object') {
            ['left', 'right'].forEach(side => {
                if (status.recording[side]) recordingSet.add(side);
            });
        }

        if (status.captures && typeof status.captures === 'object') {
            ['left', 'right'].forEach(side => {
                const capture = status.captures[side];
                if (capture && capture.running) previewSet.add(side);
            });
        }

        const recordingSides = Array.from(recordingSet);
        const previewSides = Array.from(previewSet);

        recordingSides.forEach(side => {
            markCameraFrame(side);
            updateCameraStatus(side, '写盘中', false);
        });

        previewSides.forEach(side => {
            markCameraFrame(side);
            if (!recordingSides.includes(side)) {
                updateCameraStatus(side, '预览中', true);
            }
        });

        if (recordingSides.length > 0 || previewSides.length > 0) {
            _cameraDisconnectAlerted = false;
        }
    }

    function markCameraFrame(side) {
        if (!side) return;
        _lastPreviewFrameAt[side] = performance.now();
        if (_thumbFailureCount[side] !== undefined) {
            _thumbFailureCount[side] = 0;
        }
        _cameraDisconnectAlerted = false;
    }

    function startCameraWatchdog() {
        if (_cameraWatchdogTimer) return;
        _cameraWatchdogTimer = setInterval(() => {
            if (!window.CameraControl || !window.CameraControl.isConnected()) return;

            const camState = window.CameraControl.getCameraState();
            const openedSides = ['left', 'right'].filter(side => camState[side] && camState[side].opened);
            if (openedSides.length === 0) return;

            const now = performance.now();
            openedSides.forEach(side => {
                const lastFrameAt = _lastPreviewFrameAt[side] || 0;
                if (lastFrameAt > 0 && now - lastFrameAt > CAMERA_FRAME_STALE_MS) {
                    updateCameraStatus(side, '无画面/疑似掉线', false);
                    handleCameraDisconnect(`${side === 'left' ? '左手' : '右手'}摄像头超过 ${Math.round(CAMERA_FRAME_STALE_MS / 1000)} 秒没有画面更新`);
                }
            });
        }, 2000);
    }

    function isCollectionRunning() {
        return !!(window.collectionController &&
            typeof window.collectionController.isRunning === 'function' &&
            window.collectionController.isRunning());
    }

    function handleCameraDisconnect(reason) {
        const collecting = isCollectionRunning();
        if (!collecting) {
            if (isCameraStreaming && !_cameraDisconnectAlerted) {
                _cameraDisconnectAlerted = true;
                showToast(reason || '摄像头预览异常，请重新连接摄像头', 'warning');
            }
            return;
        }
        if (_cameraDisconnectAlerted) return;
        _cameraDisconnectAlerted = true;
        showCameraEmergencyModal(reason || '摄像头连接异常');
    }

    function dismissCameraEmergencyModal() {
        const modal = document.getElementById('cameraEmergencyModal');
        if (modal) modal.remove();
        _cameraEmergencyVisible = false;
    }

    function showCameraEmergencyModal(reason) {
        if (_cameraEmergencyVisible) return;
        _cameraEmergencyVisible = true;

        const modal = document.createElement('div');
        modal.id = 'cameraEmergencyModal';
        modal.className = 'camera-emergency-overlay';
        modal.innerHTML = `
            <div class="camera-emergency-card" role="dialog" aria-modal="true" aria-labelledby="cameraEmergencyTitle">
                <div class="camera-emergency-head" id="cameraEmergencyTitle">
                    <i class="fas fa-exclamation-triangle"></i> 摄像头疑似掉线
                </div>
                <div class="camera-emergency-body">
                    <div style="font-weight: 700; color: #991b1b; margin-bottom: 8px;">${escapeHtml(reason || '摄像头连接异常')}</div>
                    <div>请现场工作人员立即中断当前采集，检查 USB 连接和相机占用情况，然后重新连接摄像头设备。</div>
                </div>
                <div class="camera-emergency-actions">
                    <button type="button" class="camera-emergency-abort" id="cameraEmergencyAbortBtn">
                        <i class="fas fa-stop-circle"></i> 紧急中断采集
                    </button>
                    <button type="button" class="camera-emergency-reconnect" id="cameraEmergencyReconnectBtn">
                        <i class="fas fa-plug"></i> 重新连接摄像头
                    </button>
                    <button type="button" class="camera-emergency-dismiss" id="cameraEmergencyDismissBtn">我知道了</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        const abortBtn = document.getElementById('cameraEmergencyAbortBtn');
        if (abortBtn) {
            abortBtn.addEventListener('click', async () => {
                try {
                    if (window.collectionController && typeof window.collectionController.abortTask === 'function') {
                        await window.collectionController.abortTask('camera_disconnected');
                    }
                    dismissCameraEmergencyModal();
                } catch (err) {
                    console.error('[CameraUI] 紧急中断采集失败:', err);
                    showToast('紧急中断采集失败，请手动点击中断按钮', 'error');
                }
            });
        }

        const reconnectBtn = document.getElementById('cameraEmergencyReconnectBtn');
        if (reconnectBtn) {
            reconnectBtn.addEventListener('click', async () => {
                dismissCameraEmergencyModal();
                await openCameraConfig();
            });
        }

        const dismissBtn = document.getElementById('cameraEmergencyDismissBtn');
        if (dismissBtn) {
            dismissBtn.addEventListener('click', dismissCameraEmergencyModal);
        }
    }

    function escapeHtml(text) {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    // ==================== 摄像头缩略图自动刷新（左下角小窗，1s间隔） ====================

    let _thumbTimer = null;
    let _thumbLastTime = 0;
    let _thumbFrameCount = 0;

    /** 启动缩略图自动刷新（摄像头打开后调用） */
    function startCameraThumbTimer() {
        // 显示容器
        const container = document.getElementById('cameraThumbContainer');
        if (container) {
            container.classList.add('active');
            setupThumbDrag(container);
        }

        _thumbFrameCount = 0;
        _thumbLastTime = performance.now();

        if (_thumbTimer) return; // 已在运行

        console.log('[CameraUI] 🔄 启动缩略图自动刷新 (1s)');
        _thumbTimer = setInterval(refreshThumbFrames, 1000);

        // 立即刷新一次
        refreshThumbFrames();
    }

    /** 停止缩略图自动刷新（摄像头关闭后调用） */
    function stopCameraThumbTimer() {
        if (_thumbTimer) {
            clearInterval(_thumbTimer);
            _thumbTimer = null;
            console.log('[CameraUI] ⏸ 缩略图自动刷新已停止');
        }

        // 隐藏容器
        const container = document.getElementById('cameraThumbContainer');
        if (container) container.classList.remove('active');

        // 重置缩略图状态
        ['left', 'right'].forEach(side => {
            const cell = document.getElementById(`thumb${capitalize(side)}Cell`);
            const img = document.getElementById(`thumb${capitalize(side)}Img`);
            const statusEl = document.getElementById(`thumb${capitalize(side)}Status`);
            if (cell) cell.classList.add('no-signal');
            if (img) img.removeAttribute('src');
            if (statusEl) {
                statusEl.textContent = '未连接设备';
                statusEl.style.color = '#94a3b8';
            }
        });
        const fpsLabel = document.getElementById('thumbFpsLabel');
        if (fpsLabel) fpsLabel.textContent = '';
    }

    function capitalize(str) {
        return str.charAt(0).toUpperCase() + str.slice(1);
    }

    async function refreshThumbFrames() {
        if (!window.CameraControl || !window.CameraControl.isConnected()) return;

        // 检查哪些摄像头已打开
        const camState = window.CameraControl.getCameraState();
        const openedSides = ['left', 'right'].filter(s => camState[s] && camState[s].opened);

        if (openedSides.length === 0) {
            // 没有摄像头打开，停止定时器
            stopCameraThumbTimer();
            return;
        }

        if (_cameraEncodingActive && !isCollectionRunning()) {
            openedSides.forEach(side => {
                const status = document.getElementById(`thumb${capitalize(side)}Status`);
                if (status) {
                    status.textContent = '视频压缩中，暂停缩略图刷新';
                    status.style.color = '#92400e';
                }
            });
            return;
        }

        // 并发拍照
        const results = await Promise.all(
            openedSides.map(side =>
                window.CameraControl.captureSnapshot(side)
                    .then(r => ({ side, ...r }))
                    .catch(() => ({ side, success: false }))
            )
        );

        for (const r of results) {
            updateThumbCell(r.side, r);
        }

        // 更新 FPS 计数
        _thumbFrameCount++;
        const now = performance.now();
        const elapsed = now - _thumbLastTime;
        if (elapsed >= 5000) {
            const fps = (_thumbFrameCount / (elapsed / 1000)).toFixed(1);
            const fpsLabel = document.getElementById('thumbFpsLabel');
            if (fpsLabel) fpsLabel.textContent = `${fps} fps`;
            _thumbFrameCount = 0;
            _thumbLastTime = now;
        }
    }

    function updateThumbCell(side, result) {
        const img = document.getElementById(`thumb${capitalize(side)}Img`);
        const status = document.getElementById(`thumb${capitalize(side)}Status`);
        const cell = document.getElementById(`thumb${capitalize(side)}Cell`);

        if (result && result.success && result.frame) {
            markCameraFrame(side);
            if (cell) cell.classList.remove('no-signal');
            if (img) img.src = `data:image/jpeg;base64,${result.frame}`;
            if (status) {
                const source = result.source === 'cache' ? '' : ' (抓帧)';
                status.textContent = `预览中 · ${(result.frame.length/1024).toFixed(1)}KB${source}`;
                status.style.color = '#10b981';
            }
        } else {
            if (_thumbFailureCount[side] !== undefined) {
                _thumbFailureCount[side]++;
                if (_thumbFailureCount[side] >= 3) {
                    handleCameraDisconnect(`${side === 'left' ? '左手' : '右手'}摄像头连续抓帧失败`);
                }
            }
            if (cell) cell.classList.add('no-signal');
            if (img) img.removeAttribute('src');
            if (status) {
                status.textContent = result && result.error ? result.error : '无画面';
                status.style.color = '#ef4444';
            }
        }
    }

    // ==================== 缩略图全屏拖拽 ====================

    let _thumbDragData = null;

    function setupThumbDrag(container) {
        const header = container.querySelector('.camera-thumb-header');
        if (!header || header.dataset.dragSetup) return;
        header.dataset.dragSetup = '1';
        header.style.cursor = 'move';

        header.addEventListener('mousedown', (e) => {
            if (e.button !== 0) return;
            e.preventDefault();

            const rect = container.getBoundingClientRect();
            _thumbDragData = {
                el: container,
                startX: e.clientX,
                startY: e.clientY,
                left: rect.left,
                top: rect.top
            };
            container.style.cursor = 'grabbing';
            header.style.cursor = 'grabbing';
        });

        document.addEventListener('mousemove', (e) => {
            if (!_thumbDragData) return;
            const d = _thumbDragData;
            let newX = d.left + (e.clientX - d.startX);
            let newY = d.top + (e.clientY - d.startY);

            // 限制在视口内
            const rect = d.el.getBoundingClientRect();
            newX = Math.max(0, Math.min(newX, window.innerWidth - rect.width));
            newY = Math.max(0, Math.min(newY, window.innerHeight - rect.height));

            d.el.style.left = newX + 'px';
            d.el.style.top = newY + 'px';
            d.el.style.right = 'auto';
            d.el.style.bottom = 'auto';
        });

        document.addEventListener('mouseup', () => {
            if (_thumbDragData) {
                _thumbDragData.el.style.cursor = '';
                header.style.cursor = 'move';
                _thumbDragData = null;
            }
        });
    }

    console.log('[CameraUI] 脚本加载完成');

})();
