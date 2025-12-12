/**
 * 
 *  index.html控制ble_server与esp32-s3 ble蓝牙扫描，连接，断开
 * 
 */
// 全局状态管理
const bluetoothState = {
  isConnected: false,
  currentMac: '',
  isScanning: false,
  isConnecting: false
};

// 建立WebSocket连接（使用8766端口避免冲突）
const ws = new WebSocket('ws://localhost:8766');

// DOM元素
const scanBtn = document.getElementById('scanBtn');
const connectBtn = document.getElementById('connectBtn');
const deviceList = document.getElementById('deviceList');
const bluetoothConnectStatus = document.getElementById('bluetooth-connect-status');
const bluetoothStrength = document.getElementById('bluetooth-strength');
const bluetoothDb = document.getElementById('bluetooth-db');
const bluetoothStatusDesc = document.getElementById('bluetooth-status-desc');

// 初始化状态
function initBluetoothUI() {
  updateConnectButton('connect', false);
  deviceList.disabled = true;
  bluetoothStrength.style.width = '0%';
  bluetoothStrength.className = 'bg-danger h-2 rounded-full';
}

// 更新连接按钮状态
function updateConnectButton(type, disabled = false) {
  switch(type) {
    case 'connect':
      connectBtn.innerHTML = '<i class="fa fa-link mr-1"></i>连接';
      connectBtn.className = 'px-3 py-1 text-sm bg-success text-white rounded hover:bg-success/90 transition-colors';
      connectBtn.disabled = disabled;
      break;
    case 'connecting':
      connectBtn.innerHTML = '<i class="fa fa-spinner fa-spin mr-1"></i>连接中';
      connectBtn.className = 'px-3 py-1 text-sm bg-gray-400 text-white rounded cursor-not-allowed';
      connectBtn.disabled = true;
      break;
    case 'disconnect':
      connectBtn.innerHTML = '<i class="fa fa-unlink mr-1"></i>断开';
      connectBtn.className = 'px-3 py-1 text-sm bg-danger text-white rounded hover:bg-danger/90 transition-colors';
      connectBtn.disabled = disabled;
      break;
    case 'reconnect':
      connectBtn.innerHTML = '<i class="fa fa-refresh mr-1"></i>重连';
      connectBtn.className = 'px-3 py-1 text-sm bg-warning text-white rounded hover:bg-warning/90 transition-colors';
      connectBtn.disabled = disabled;
      break;
  }
}

// 更新蓝牙连接状态显示
function updateBluetoothStatus(isConnected, mac = '', rssi = '--') {
  bluetoothState.isConnected = isConnected;
  bluetoothState.currentMac = mac;

  if (isConnected) {
    bluetoothConnectStatus.innerHTML = '<i class="fa fa-check-circle mr-1"></i>已连接';
    bluetoothConnectStatus.className = 'flex items-center text-success';
    bluetoothStatusDesc.textContent = `已连接设备: ${mac}`;
    bluetoothDb.textContent = rssi;
    
    // 根据RSSI更新信号强度（-30dBm最佳，-100dBm最差）
    let strengthPercent = 0;
    if (rssi !== '--' && !isNaN(rssi)) {
      rssi = parseInt(rssi);
      if (rssi >= -50) strengthPercent = 100;
      else if (rssi >= -70) strengthPercent = 75;
      else if (rssi >= -85) strengthPercent = 50;
      else if (rssi >= -100) strengthPercent = 25;
      
      bluetoothStrength.className = strengthPercent >= 50 ? 
        'bg-success h-2 rounded-full' : 
        strengthPercent >= 25 ? 'bg-warning h-2 rounded-full' : 'bg-danger h-2 rounded-full';
    }
    bluetoothStrength.style.width = `${strengthPercent}%`;
    updateConnectButton('disconnect');
  } else {
    bluetoothConnectStatus.innerHTML = '<i class="fa fa-times-circle mr-1"></i>未连接';
    bluetoothConnectStatus.className = 'flex items-center text-danger';
    bluetoothStatusDesc.textContent = mac ? `连接失败: ${mac}` : '未扫描设备';
    bluetoothDb.textContent = '--';
    bluetoothStrength.style.width = '0%';
    bluetoothStrength.className = 'bg-danger h-2 rounded-full';
  }
}

// 扫描按钮点击事件
scanBtn.addEventListener('click', () => {
  if (bluetoothState.isScanning) return;
  
  bluetoothState.isScanning = true;
  scanBtn.innerHTML = '<i class="fa fa-spinner fa-spin mr-1"></i>扫描中';
  scanBtn.disabled = true;
  deviceList.innerHTML = '<option value="">扫描中...</option>';
  deviceList.disabled = true;
  bluetoothStatusDesc.textContent = '正在扫描蓝牙设备...';
  
  // 发送扫描指令
  ws.send(JSON.stringify({ action: 'scan' }));
});

// 连接/断开按钮点击事件
connectBtn.addEventListener('click', () => {
  const selectedMac = deviceList.value;
  
  if (bluetoothState.isConnected) {
    // 断开连接
    updateConnectButton('connecting');
    bluetoothStatusDesc.textContent = '正在断开连接...';
    ws.send(JSON.stringify({ 
      action: 'disconnect', 
      mac: bluetoothState.currentMac 
    }));
  } else {
    if (!selectedMac) {
      alert('请先选择要连接的蓝牙设备');
      return;
    }
    
    // 连接设备
    updateConnectButton('connecting');
    bluetoothStatusDesc.textContent = `正在连接设备: ${selectedMac}...`;
    ws.send(JSON.stringify({ 
      action: 'connect', 
      mac: selectedMac 
    }));
  }
});

// 设备列表变更事件（切换设备后重置按钮状态）
deviceList.addEventListener('change', () => {
  if (deviceList.value && !bluetoothState.isConnected) {
    updateConnectButton('connect');
  }
});

// WebSocket消息处理
ws.onmessage = (event) => {
  try {
    const data = JSON.parse(event.data);
    console.log('收到服务器消息:', data);
    
    switch(data.action) {
      case 'scan_result':
        // 处理扫描结果
        bluetoothState.isScanning = false;
        scanBtn.innerHTML = '<i class="fa fa-search mr-1"></i>扫描';
        scanBtn.disabled = false;
        
        if (data.devices && data.devices.length > 0) {
          // 填充设备列表
          deviceList.innerHTML = '<option value="">请选择设备</option>';
          data.devices.forEach(device => {
            const option = document.createElement('option');
            option.value = device.mac;
            option.textContent = `${device.name || '未知设备'} (${device.mac})`;
            deviceList.appendChild(option);
          });
          deviceList.disabled = false;
          bluetoothStatusDesc.textContent = `找到${data.devices.length}个蓝牙设备`;
          
          // 如果之前有连接过的设备，自动选中
          if (bluetoothState.currentMac && data.devices.some(d => d.mac === bluetoothState.currentMac)) {
            deviceList.value = bluetoothState.currentMac;
          }
        } else {
          deviceList.innerHTML = '<option value="">未找到设备</option>';
          deviceList.disabled = true;
          bluetoothStatusDesc.textContent = '未找到任何蓝牙设备';
          updateConnectButton('connect', true);
        }
        break;
        
      case 'connect_result':
        // 处理连接结果
        if (data.success) {
          // 连接成功
          updateBluetoothStatus(true, data.mac, data.rssi || '--');
        } else {
          // 连接失败
          updateBluetoothStatus(false, data.mac);
          updateConnectButton('reconnect');
          bluetoothStatusDesc.textContent = `连接失败: ${data.message || '未知错误'}`;
        }
        break;
        
      case 'disconnect_result':
        // 处理断开结果
        updateBluetoothStatus(false);
        updateConnectButton('connect', !deviceList.value);
        bluetoothStatusDesc.textContent = data.success ? '已断开连接' : '断开失败';
        break;
        
      case 'device_status':
        // 实时更新设备状态（如信号强度、电量）
        if (data.mac === bluetoothState.currentMac && bluetoothState.isConnected) {
          bluetoothDb.textContent = data.rssi || bluetoothDb.textContent;
          // 可扩展：更新电量显示
          // batteryElement.textContent = data.battery || '78%';
        }
        break;
    }
  } catch (error) {
    console.error('解析WebSocket消息失败:', error);
  }
};

// WebSocket连接状态处理
ws.onopen = () => {
  console.log('WebSocket连接成功');
  initBluetoothUI();
};

ws.onclose = () => {
  console.log('WebSocket连接断开');
  bluetoothStatusDesc.textContent = '通信断开，请刷新页面';
  scanBtn.disabled = true;
  deviceList.disabled = true;
  updateConnectButton('connect', true);
};

ws.onerror = (error) => {
  console.error('WebSocket错误:', error);
  bluetoothStatusDesc.textContent = '通信错误';
};

// 页面加载初始化
window.addEventListener('load', initBluetoothUI);


