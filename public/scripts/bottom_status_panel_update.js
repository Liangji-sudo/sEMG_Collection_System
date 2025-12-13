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

async function fetchStorageVolumeStatus() {
    try {
        const response = await fetch('/api/storage-volume');
        const data = await response.json();
        
        // 更新传输吞吐量
        document.getElementById('storage-volume-value').textContent = data.storage.volume;
        document.getElementById('storage-volume-percent').textContent = data.storage.free_Percent;
        document.getElementById('storage-volume-bar').style.width = `${100 - data.storage.free_Percent}%`;
        } catch (error) {
        console.error('获取设备状态失败：', error);
    }

}

// 每2秒更新一次设备状态
setInterval(fetchDeviceStatus, 2000);
setInterval(fetchStorageVolumeStatus, 10 * 60 * 1000); //10min update
fetchDeviceStatus(); // 初始加载
fetchStorageVolumeStatus()
