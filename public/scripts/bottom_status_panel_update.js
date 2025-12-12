// 1. 从后端API获取动态设备状态
async function fetchDeviceStatus() {
    try {
    const response = await fetch('/api/device-status');
    const data = await response.json();
    
    // 更新传输吞吐量
    document.getElementById('throughput-value').textContent = data.throughput.value;
    document.getElementById('throughput-bar').style.width = `${data.throughput.percent}%`;
    } catch (error) {
    console.error('获取设备状态失败：', error);
    }
}

// 每2秒更新一次设备状态
setInterval(fetchDeviceStatus, 2000);
fetchDeviceStatus(); // 初始加载
