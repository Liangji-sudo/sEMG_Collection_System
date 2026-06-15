"""
简化版 camera-control.js
前端不直接访问摄像头，所有操作通过后端 camera_server
"""

class CameraControl {
    constructor() {
        this.selectedCameras = {
            left: null,
            right: null
        };
    }

    /**
     * 设置摄像头配置（发送给后端）
     */
    async setCamera(side, deviceName, deviceId) {
        console.log(`[CameraControl] 配置${side}侧摄像头:`, deviceName);

        try {
            const response = await fetch('/api/camera/set-camera', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    side: side,
                    device_name: deviceName,
                    device_id: deviceId
                })
            });

            const result = await response.json();

            if (result.success) {
                this.selectedCameras[side] = {
                    name: deviceName,
                    id: deviceId
                };
                console.log(`[CameraControl] ✅ ${side}侧摄像头配置成功`);
            }

            return result;

        } catch (error) {
            console.error(`[CameraControl] 配置${side}侧摄像头失败:`, error);
            return { success: false, error: error.message };
        }
    }

    /**
     * 获取已配置的摄像头
     */
    getSelectedCamera(side) {
        return this.selectedCameras[side];
    }
}

// 创建全局实例
window.cameraControl = new CameraControl();
console.log('[CameraControl] 初始化完成（后端模式）');
