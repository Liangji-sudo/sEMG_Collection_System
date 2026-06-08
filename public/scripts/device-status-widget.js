/**
 * device-status-widget.js - 设备状态悬浮窗口控制器
 *
 * 职责：
 * 1. 显示左右手环的连接状态
 * 2. 显示左右手环的电池电量
 * 3. 显示左右手环的流模式（idle/preview/collection）
 * 4. 支持收起/展开功能
 */

(function() {
    'use strict';

    console.log('[DeviceStatusWidget] 脚本加载开始...');

    class DeviceStatusWidget {
        constructor() {
            this.floatElement = null;
            this.toggleBtn = null;
            this.isCollapsed = false;

            // 设备状态缓存
            this.device1Status = {
                connected: false,
                battery: 0,
                stream_mode: 'idle'
            };
            this.device2Status = {
                connected: false,
                battery: 0,
                stream_mode: 'idle'
            };

            this.init();
        }

        init() {
            console.log('[DeviceStatusWidget] 初始化开始...');

            // 等待DOM加载完成
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', () => this.setup());
            } else {
                this.setup();
            }
        }

        setup() {
            this.floatElement = document.getElementById('deviceStatusFloat');
            this.toggleBtn = document.getElementById('deviceStatusToggle');

            if (!this.floatElement) {
                console.error('[DeviceStatusWidget] 找不到悬浮窗口元素');
                return;
            }

            // 绑定收起/展开按钮
            if (this.toggleBtn) {
                this.toggleBtn.addEventListener('click', () => this.toggle());
            }

            // 也可以点击标题栏收起/展开
            const header = this.floatElement.querySelector('.device-status-header');
            if (header) {
                header.addEventListener('click', () => this.toggle());
            }

            console.log('[DeviceStatusWidget] 初始化完成');
        }

        /**
         * 切换收起/展开状态
         */
        toggle() {
            this.isCollapsed = !this.isCollapsed;
            if (this.isCollapsed) {
                this.floatElement.classList.add('collapsed');
            } else {
                this.floatElement.classList.remove('collapsed');
            }
        }

        /**
         * 更新设备状态
         * @param {number} deviceId - 设备ID (1=左手, 2=右手)
         * @param {object} status - 状态对象 {connected, battery_percent, stream_mode}
         */
        updateDevice(deviceId, status) {
            if (deviceId === 1) {
                this.device1Status = {
                    connected: status.connected || false,
                    battery: status.battery_percent || 0,
                    stream_mode: status.stream_mode || 'idle'
                };
                this.renderDevice('left', this.device1Status);
            } else if (deviceId === 2) {
                this.device2Status = {
                    connected: status.connected || false,
                    battery: status.battery_percent || 0,
                    stream_mode: status.stream_mode || 'idle'
                };
                this.renderDevice('right', this.device2Status);
            }
        }

        /**
         * 渲染单个设备状态
         * @param {string} side - 'left' 或 'right'
         * @param {object} status - 状态对象
         */
        renderDevice(side, status) {
            const prefix = side === 'left' ? 'left' : 'right';

            // 更新电池
            this.updateBattery(prefix, status.battery, status.connected);

            // 更新连接状态
            this.updateConnection(prefix, status.connected);

            // 更新流模式
            this.updateStreamMode(prefix, status.stream_mode, status.connected);
        }

        /**
         * 更新电池显示
         */
        updateBattery(prefix, percent, connected) {
            const batteryEl = document.getElementById(`${prefix}Battery`);
            const iconEl = document.getElementById(`${prefix}BatteryIcon`);

            if (!batteryEl || !iconEl) return;

            if (!connected) {
                batteryEl.textContent = '--';
                iconEl.className = 'fas fa-battery-empty';
                iconEl.style.color = '#cbd5e1';
                return;
            }

            batteryEl.textContent = `${percent}%`;

            // 根据电量设置图标和颜色
            let iconClass = 'fas ';
            let colorClass = '';

            if (percent >= 90) {
                iconClass += 'fa-battery-full';
                colorClass = 'battery-full';
            } else if (percent >= 60) {
                iconClass += 'fa-battery-three-quarters';
                colorClass = 'battery-high';
            } else if (percent >= 30) {
                iconClass += 'fa-battery-half';
                colorClass = 'battery-medium';
            } else if (percent >= 10) {
                iconClass += 'fa-battery-quarter';
                colorClass = 'battery-low';
            } else {
                iconClass += 'fa-battery-empty';
                colorClass = 'battery-critical';
            }

            iconEl.className = iconClass + ' ' + colorClass;
        }

        /**
         * 更新连接状态
         */
        updateConnection(prefix, connected) {
            const connEl = document.getElementById(`${prefix}Connection`);
            if (!connEl) return;

            const iconEl = connEl.querySelector('i');
            const textEl = connEl.querySelector('span');

            if (connected) {
                connEl.className = 'connection-status connected';
                textEl.textContent = '已连接';
            } else {
                connEl.className = 'connection-status disconnected';
                textEl.textContent = '未连接';
            }
        }

        /**
         * 更新流模式
         */
        updateStreamMode(prefix, mode, connected) {
            const streamEl = document.getElementById(`${prefix}Stream`);
            if (!streamEl) return;

            const textEl = streamEl.querySelector('span');

            if (!connected) {
                streamEl.className = 'stream-mode';
                textEl.textContent = '空闲';
                return;
            }

            let modeText = '空闲';
            let modeClass = 'stream-mode';

            if (mode === 'preview') {
                modeText = '预览';
                modeClass = 'stream-mode preview';
            } else if (mode === 'collection') {
                modeText = '采集中';
                modeClass = 'stream-mode collection';
            } else {
                modeText = '空闲';
                modeClass = 'stream-mode';
            }

            streamEl.className = modeClass;
            textEl.textContent = modeText;
        }

        /**
         * 批量更新两个设备状态（从backend-manager获取的状态）
         */
        updateFromBackendStatus(status) {
            if (!status) return;

            if (status.device1) {
                this.updateDevice(1, status.device1);
            }
            if (status.device2) {
                this.updateDevice(2, status.device2);
            }
        }
    }

    // ==================== 初始化 ====================
    let deviceStatusWidget = null;

    function initWidget() {
        if (!deviceStatusWidget) {
            deviceStatusWidget = new DeviceStatusWidget();
            window.deviceStatusWidget = deviceStatusWidget;
            console.log('[DeviceStatusWidget] 控制器已挂载到 window.deviceStatusWidget');
        }
    }

    // 如果DOM已经加载，直接初始化
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initWidget);
    } else {
        initWidget();
    }

    console.log('[DeviceStatusWidget] 脚本加载完成');

})();
