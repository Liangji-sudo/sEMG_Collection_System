/**
 * bleControl.js - 前端蓝牙控制模块
 * ==================================
 * 
 * 与 ble_server_final.py 的控制端口 (8765) 通信
 * 用于 index.html 控制蓝牙设备的连接/断开/采集
 * 
 * 使用方法：
 *   在 HTML 中引入: <script src="bleControl.js"></script>
 *   会自动初始化并绑定到页面元素
 */

(function() {
    'use strict';

    // ================= 配置 =================
    const WS_URL = 'ws://localhost:8764';  // 控制端口
    const RECONNECT_DELAY = 1000;  // 1秒重连，加快连接速度
    const HEARTBEAT_INTERVAL = 30000;
    const MAX_RECONNECT_ATTEMPTS = 10;  // 【新增】最大重连次数

    // ================= 状态 =================
    const BleState = {
        ws: null,
        connected: false,
        reconnecting: false,
        reconnectAttempts: 0,  // 【新增】重连次数计数
        heartbeatTimer: null,
        
        // 设备状态
        devices: {
            1: { connected: false, streaming: false, mac: null, name: null, rssi: null },
            2: { connected: false, streaming: false, mac: null, name: null, rssi: null },
        },
        
        // 扫描结果
        scannedDevices: [],
        
        // 回调
        onStatusChange: null,
        onDeviceChange: null,
        onScanResult: null,
        onError: null,
    };

    // ================= WebSocket 连接 =================

    function connect() {
        // 如果已经连接或正在连接，直接返回
        if (BleState.ws && (BleState.ws.readyState === WebSocket.OPEN || BleState.ws.readyState === WebSocket.CONNECTING)) {
            console.log('[BLE] 已经连接或正在连接，跳过');
            return;
        }

        // 【修复】如果正在重连中，跳过
        if (BleState.reconnecting) {
            console.log('[BLE] 正在重连中，跳过');
            return;
        }

        // 清理旧的 WebSocket 对象
        if (BleState.ws) {
            console.log('[BLE] 清理旧连接, readyState:', BleState.ws.readyState);
            BleState.ws.onopen = null;
            BleState.ws.onclose = null;
            BleState.ws.onerror = null;
            BleState.ws.onmessage = null;
            BleState.ws = null;
        }

        console.log('[BLE] 连接服务器:', WS_URL);
        updateServerStatus('connecting');

        try {
            BleState.ws = new WebSocket(WS_URL);

            BleState.ws.onopen = () => {
                console.log('[BLE] ✅ 已连接到BLE服务器');
                BleState.connected = true;
                BleState.reconnecting = false;
                BleState.reconnectAttempts = 0;  // 【修复】重置重连计数
                updateServerStatus('connected');
                startHeartbeat();
            };

            BleState.ws.onclose = (event) => {
                console.log('[BLE] 连接关闭, code:', event.code, 'reason:', event.reason);
                BleState.connected = false;
                BleState.ws = null;
                updateServerStatus('disconnected');
                stopHeartbeat();
                // 【修复】只在非主动关闭时重连
                if (event.code !== 1000) {
                    scheduleReconnect();
                }
            };

            BleState.ws.onerror = (err) => {
                console.error('[BLE] WebSocket 错误');
                if (BleState.onError) {
                    BleState.onError('WebSocket 连接错误');
                }
            };

            BleState.ws.onmessage = (event) => {
                handleMessage(event.data);
            };

        } catch (err) {
            console.error('[BLE] 连接失败:', err);
            BleState.ws = null;
            scheduleReconnect();
        }
    }

    function disconnect() {
        BleState.reconnecting = false;
        stopHeartbeat();
        if (BleState.ws) {
            BleState.ws.onclose = null; // 防止触发重连
            BleState.ws.close();
            BleState.ws = null;
        }
        BleState.connected = false;
    }

    function scheduleReconnect() {
        if (BleState.reconnecting) {
            console.log('[BLE] 已在重连队列中，跳过');
            return;
        }

        // 【修复】检查重连次数
        if (BleState.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
            console.warn('[BLE] 达到最大重连次数，停止重连');
            return;
        }

        BleState.reconnecting = true;
        BleState.reconnectAttempts++;
        console.log(`[BLE] ${RECONNECT_DELAY/1000}秒后重连... (尝试 ${BleState.reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})`);
        setTimeout(() => {
            BleState.reconnecting = false; // 重置标志，允许下次重连
            connect();
        }, RECONNECT_DELAY);
    }
    
    function startHeartbeat() {
        stopHeartbeat();
        BleState.heartbeatTimer = setInterval(() => {
            if (BleState.connected) {
                send({ action: 'status' });
            }
        }, HEARTBEAT_INTERVAL);
    }
    
    function stopHeartbeat() {
        if (BleState.heartbeatTimer) {
            clearInterval(BleState.heartbeatTimer);
            BleState.heartbeatTimer = null;
        }
    }

    // ================= 消息处理 =================
    
    function send(data) {
        if (!BleState.ws || BleState.ws.readyState !== WebSocket.OPEN) {
            console.warn('[BLE] 未连接，无法发送, readyState:', BleState.ws?.readyState);
            return false;
        }
        console.log('[BLE] 发送命令:', data);
        BleState.ws.send(JSON.stringify(data));
        return true;
    }
    
async function handleMessage(rawData) {
    try {
        let msg;
        
        console.log('liangji start handleMessage');
        // 支持 MessagePack 和 JSON
        if (rawData instanceof Blob) {
            console.log('liangji rawData = Blob');
            const buffer = await rawData.arrayBuffer();
            msg = await decodeData(buffer); // 抽离解码逻辑，提高复用性
        } else if (rawData instanceof ArrayBuffer) {
            console.log('liangji rawData = ArrayBuffer');
            msg = await decodeData(rawData);
        } else {
            console.log('liangji rawData = JSON');
            // 先判断是否是字符串，避免非字符串类型传入 JSON.parse
            if (typeof rawData === 'string') {
                msg = JSON.parse(rawData);
            } else {
                throw new Error('非字符串类型无法解析为 JSON');
            }
        }
        
        console.log('[BLE] 收到:', msg);
        
        if (msg.type === 'response') {
            handleResponse(msg);
        } else if (msg.type === 'event') {
            handleEvent(msg);
        } else if (msg.type === 'welcome') {
            handleWelcome(msg);
        }
        
    } catch (err) {
        console.error('[BLE] 消息解析错误:', err);
    }
}

// 抽离解码逻辑，单独处理 msgpack 和 JSON 降级
async function decodeData(buffer) {
    // 1. 优先尝试 msgpack 解码（核心修复：增加 msgpack 解码失败的容错）
    if (typeof msgpack !== 'undefined' && msgpack?.decode) {
        try {
            return msgpack.decode(new Uint8Array(buffer));
        } catch (msgpackErr) {
            console.warn('[BLE] msgpack 解码失败，尝试 JSON 解析:', msgpackErr);
        }
    } else {
        console.warn('[BLE] msgpack 库未加载，直接尝试 JSON 解析');
    }
    
    // 2. msgpack 解码失败/库不存在时，降级为 JSON 解析
    try {
        const text = new TextDecoder().decode(buffer);
        return JSON.parse(text);
    } catch (jsonErr) {
        throw new Error(`JSON 解析也失败：${jsonErr.message}，原始数据长度：${buffer.byteLength}`);
    }
}
    
    function handleWelcome(msg) {
        console.log('[BLE] 欢迎消息:', msg.message);
        
        // 更新设备状态
        if (msg.dev1) updateDeviceState(1, msg.dev1);
        if (msg.dev2) updateDeviceState(2, msg.dev2);
        
        // 更新 UI
        updateAllUI();
    }
    
    function handleResponse(msg) {
        const action = msg.action;
        
        // 扫描结果
        if (action === 'scan') {
            console.log('liangji scan over');
            if (msg.success) {
                console.log('liangji scan success');
                BleState.scannedDevices = msg.devices || [];
                updateDeviceSelects();
                showToast(`发现 ${msg.count} 个设备`);
            } else {
                console.log('liangji scan fail');
                showToast('扫描失败: ' + msg.error, 'error');
            }
            console.log('liangji updateScanButton');
            updateScanButton(false);
        }
        
        // 连接结果
        else if (action.startsWith('connect')) {
            const deviceId = parseInt(action.slice(-1));
            
            if (msg.success === true) {
                BleState.devices[deviceId] = {
                    connected: true,
                    streaming: false,
                    mac: msg.mac,
                    name: msg.name,
                    rssi: msg.rssi,
                };
                updateDeviceUI(deviceId);
                showToast(`设备 ${deviceId} 连接成功`);
            } else if (msg.success === false) {
                updateDeviceUI(deviceId);
                showToast(`设备 ${deviceId} 连接失败: ${msg.error}`, 'error');
            } else {
                // 连接中
                updateDeviceStatus(deviceId, 'connecting');
            }
            updateConnectButton(deviceId, msg.success !== null);
        }
        
        // 断开结果
        else if (action.startsWith('disconnect')) {
            const deviceId = parseInt(action.slice(-1));
            
            if (msg.success) {
                BleState.devices[deviceId] = {
                    connected: false,
                    streaming: false,
                    mac: null,
                    name: null,
                    rssi: null,
                };
                updateDeviceUI(deviceId);
                showToast(`设备 ${deviceId} 已断开`);
            } else {
                showToast('断开失败: ' + msg.error, 'error');
            }
            updateConnectButton(deviceId, true);
        }
        
        // 开始采集
        else if (action.startsWith('start')) {
            const deviceId = action === 'start_all' ? 0 : parseInt(action.slice(-1));
            
            if (msg.success) {
                if (deviceId === 0) {
                    // start_all
                    (msg.started || []).forEach(id => {
                        BleState.devices[id].streaming = true;
                    });
                } else {
                    BleState.devices[deviceId].streaming = true;
                }
                updateAllUI();
                showToast('采集已开始');
            } else {
                showToast('启动失败: ' + msg.error, 'error');
            }
        }
        
        // 停止采集
        else if (action.startsWith('stop')) {
            const deviceId = action === 'stop_all' ? 0 : parseInt(action.slice(-1));
            
            if (msg.success) {
                if (deviceId === 0) {
                    BleState.devices[1].streaming = false;
                    BleState.devices[2].streaming = false;
                } else {
                    BleState.devices[deviceId].streaming = false;
                }
                updateAllUI();
                showToast('采集已停止');
            } else {
                showToast('停止失败: ' + msg.error, 'error');
            }
        }
        
        // 状态
        else if (action === 'status') {
            if (msg.dev1) updateDeviceState(1, msg.dev1);
            if (msg.dev2) updateDeviceState(2, msg.dev2);
            updateAllUI();
        }

        else if (action === 'welcome') {
            console.log('welcome msg: connected to websocket server', msg);
        }
        
        // 回调
        if (BleState.onStatusChange) {
            BleState.onStatusChange(action, msg);
        }
    }
    
    function handleEvent(msg) {
        const event = msg.event;
        console.log('[BLE] 事件:', event, msg);
        
        if (event === 'device_connected') {
            // 外部连接事件（其他控制端触发）
        } else if (event === 'device_disconnected') {
            // 外部断开事件
        } else if (event === 'stream_started' || event === 'stream_stopped') {
            // 采集状态变更
            updateAllUI();
        }
    }
    
    function updateDeviceState(deviceId, data) {
        BleState.devices[deviceId] = {
            connected: data.connected,
            streaming: data.streaming,
            mac: data.mac,
            name: data.name,
            rssi: data.rssi,
        };
    }

    // ================= UI 更新 =================
    
    function updateServerStatus(status) {
        const el = document.getElementById('serverStatus');
        if (el) {
            el.className = 'status-badge ' + status;
            el.textContent = {
                connected: '已连接',
                connecting: '连接中',
                disconnected: '未连接'
            }[status] || status;
        }
    }
    
    function updateScanButton(scanning) {
        const btn = document.getElementById('scanAllBtn');
        if (btn) {
            btn.disabled = scanning;
            btn.innerHTML = scanning 
                ? '<i class="fas fa-spinner fa-spin"></i> 扫描中'
                : '<i class="fas fa-search"></i> 扫描';
        }
    }
    
    function updateDeviceSelects() {
        const devices = BleState.scannedDevices;
        
        const opts = '<option value="">请选择设备...</option>' + 
            devices.map(d => {
                const isTarget = d.name === 'ESP32S3_EMG' ? ' ★' : '';
                return `<option value="${d.mac}">${d.name} (${d.rssi}dBm)${isTarget}</option>`;
            }).join('');
        
        const select1 = document.getElementById('device1Select');
        const select2 = document.getElementById('device2Select');
        
        if (select1) {
            select1.innerHTML = opts;
            select1.disabled = false;
        }
        if (select2) {
            select2.innerHTML = opts;
            select2.disabled = false;
        }
        
        // 回调
        if (BleState.onScanResult) {
            BleState.onScanResult(devices);
        }
    }
    
    function updateDeviceUI(deviceId) {
        const dev = BleState.devices[deviceId];
        
        updateDeviceStatus(deviceId, dev.connected ? 'connected' : 'disconnected');
        updateDeviceInfo(deviceId, dev);
        updateConnectButton(deviceId, true);
    }
    
    function updateDeviceStatus(deviceId, status) {
        const el = document.getElementById(`slot${deviceId}Status`);
        const slotEl = document.getElementById(`btSlot${deviceId}`);
        
        if (el) {
            el.className = 'status-badge ' + status;
            el.textContent = {
                connected: '已连接',
                connecting: '连接中',
                disconnected: '未连接'
            }[status] || status;
        }
        
        if (slotEl) {
            slotEl.classList.remove('connected');
            if (status === 'connected') {
                slotEl.classList.add('connected');
            }
        }
    }
    
    function updateDeviceInfo(deviceId, dev) {
        const nameEl = document.getElementById(`device${deviceId}Name`);
        const rssiEl = document.getElementById(`device${deviceId}RSSI`);
        const signalEl = document.getElementById(`device${deviceId}Signal`);
        
        if (dev && dev.connected) {
            if (nameEl) nameEl.textContent = dev.name || '--';
            if (rssiEl) rssiEl.textContent = dev.rssi ? `${dev.rssi} dBm` : '-- dBm';
            if (signalEl) {
                const pct = dev.rssi ? Math.max(0, Math.min(100, ((dev.rssi + 90) / 60) * 100)) : 0;
                signalEl.style.width = pct + '%';
            }
        } else {
            if (nameEl) nameEl.textContent = '--';
            if (rssiEl) rssiEl.textContent = '-- dBm';
            if (signalEl) signalEl.style.width = '0%';
        }
    }
    
    function updateConnectButton(deviceId, enabled) {
        const btn = document.getElementById(`connect${deviceId}Btn`);
        const dev = BleState.devices[deviceId];
        
        if (btn) {
            btn.disabled = !enabled;
            
            if (dev.connected) {
                btn.innerHTML = '<i class="fas fa-unlink"></i> 断开';
                btn.className = 'bt-connect-btn disconnect';
            } else {
                btn.innerHTML = '<i class="fas fa-link"></i> 连接';
                btn.className = 'bt-connect-btn connect';
            }
        }
    }
    
    function updateAllUI() {
        updateDeviceUI(1);
        updateDeviceUI(2);
    }
    
    function showToast(msg, type = 'success') {
        // 使用页面已有的 toast 函数
        if (typeof window.showToast === 'function') {
            window.showToast(msg, type);
        } else {
            // 简易 toast
            const toast = document.getElementById('toast');
            if (toast) {
                const msgEl = document.getElementById('toastMessage');
                const icon = toast.querySelector('i');
                
                toast.className = `toast ${type}`;
                if (msgEl) msgEl.textContent = msg;
                if (icon) {
                    icon.className = type === 'success' ? 'fas fa-check-circle' : 
                                    type === 'error' ? 'fas fa-times-circle' : 
                                    'fas fa-exclamation-circle';
                }
                toast.classList.add('visible');
                setTimeout(() => toast.classList.remove('visible'), 2500);
            } else {
                console.log('[Toast]', type, msg);
            }
        }
    }

    // ================= 公开 API =================
    
    const BleControl = {
        // 连接服务器
        connect: connect,
        disconnect: disconnect,
        
        // 发送命令
        send: send,
        
        // 扫描
        scan: () => {
            updateScanButton(true);
            const result = send({ action: 'scan' });
            if (!result) {
                updateScanButton(false);
                showToast('未连接到BLE服务器，无法扫描', 'error');
            }
            return result;
        },
        
        // 连接设备
        connectDevice: (deviceId, mac) => {
            updateConnectButton(deviceId, false);
            updateDeviceStatus(deviceId, 'connecting');
            return send({ action: `connect${deviceId}`, mac: mac });
        },
        
        // 断开设备
        disconnectDevice: (deviceId) => {
            updateConnectButton(deviceId, false);
            return send({ action: `disconnect${deviceId}` });
        },
        
        // 开始采集
        startStream: (deviceId) => {
            return send({ action: `start${deviceId}` });
        },
        
        // 停止采集
        stopStream: (deviceId) => {
            return send({ action: `stop${deviceId}` });
        },
        
        // 同时开始/停止
        startAll: () => {
            // 【新增】在开始采集前，先发送会话ID
            const sessionIdInput = document.getElementById('sessionIdInput');
            if (sessionIdInput && sessionIdInput.value.trim()) {
                const sessionId = sessionIdInput.value.trim();
                send({ action: 'set_session_id', session_id: sessionId });
                console.log('[BLE] 已发送会话ID:', sessionId);
            }
            return send({ action: 'start_all' });
        },
        stopAll: () => send({ action: 'stop_all' }),

        // 【新增】设置会话ID
        setSessionId: (sessionId) => {
            return send({ action: 'set_session_id', session_id: sessionId || '' });
        },
        
        // 获取状态
        getStatus: () => send({ action: 'status' }),
        
        // 状态访问
        get state() { return BleState; },
        get isConnected() { return BleState.connected; },
        get devices() { return BleState.devices; },
        get scannedDevices() { return BleState.scannedDevices; },
        
        // 回调设置
        onStatusChange: (fn) => { BleState.onStatusChange = fn; },
        onDeviceChange: (fn) => { BleState.onDeviceChange = fn; },
        onScanResult: (fn) => { BleState.onScanResult = fn; },
        onError: (fn) => { BleState.onError = fn; },
    };

    // ================= 初始化 =================
    
    function init() {
        console.log('[BLE] 初始化蓝牙控制模块');
        
        // 绑定扫描按钮
        const scanBtn = document.getElementById('scanAllBtn');
        if (scanBtn) {
            scanBtn.addEventListener('click', () => BleControl.scan());
        }
        
        // 绑定设备选择
        const select1 = document.getElementById('device1Select');
        const select2 = document.getElementById('device2Select');
        
        if (select1) {
            select1.addEventListener('change', (e) => {
                const btn = document.getElementById('connect1Btn');
                if (btn) btn.disabled = !e.target.value;
            });
        }
        
        if (select2) {
            select2.addEventListener('change', (e) => {
                const btn = document.getElementById('connect2Btn');
                if (btn) btn.disabled = !e.target.value;
            });
        }
        
        // 绑定连接按钮
        const connect1Btn = document.getElementById('connect1Btn');
        const connect2Btn = document.getElementById('connect2Btn');
        
        if (connect1Btn) {
            connect1Btn.addEventListener('click', () => {
                if (BleState.devices[1].connected) {
                    BleControl.disconnectDevice(1);
                } else {
                    const mac = document.getElementById('device1Select')?.value;
                    if (mac) {
                        BleControl.connectDevice(1, mac);
                    } else {
                        showToast('请先选择设备', 'warning');
                    }
                }
            });
        }
        
        if (connect2Btn) {
            connect2Btn.addEventListener('click', () => {
                if (BleState.devices[2].connected) {
                    BleControl.disconnectDevice(2);
                } else {
                    const mac = document.getElementById('device2Select')?.value;
                    if (mac) {
                        BleControl.connectDevice(2, mac);
                    } else {
                        showToast('请先选择设备', 'warning');
                    }
                }
            });
        }
        
        // 检查重复设备选择
        if (select1 && select2) {
            const checkDuplicate = () => {
                const v1 = select1.value;
                const v2 = select2.value;
                
                if (v1 && v1 === v2) {
                    showToast('不能选择相同的设备', 'warning');
                    select2.value = '';
                    const btn = document.getElementById('connect2Btn');
                    if (btn) btn.disabled = true;
                }
            };
            
            select1.addEventListener('change', checkDuplicate);
            select2.addEventListener('change', checkDuplicate);
        }

        // 自动连接服务器
        connect();
    }

    // DOM 加载完成后初始化
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // 【新增】页面刷新/关闭前主动断开WebSocket连接
    window.addEventListener('beforeunload', () => {
        disconnect();
    });

    // 导出到全局
    window.BleControl = BleControl;

})();
