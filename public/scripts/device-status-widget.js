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

            // 拖拽相关
            this.isDragging = false;
            this.dragStartX = 0;
            this.dragStartY = 0;
            this.elementStartX = 0;
            this.elementStartY = 0;

            // 设备状态缓存
            this.device1Status = {
                connected: false,
                battery: 0,
                stream_mode: 'idle',
                num_imus: null,
                hw_version: null
            };
            this.device2Status = {
                connected: false,
                battery: 0,
                stream_mode: 'idle',
                num_imus: null,
                hw_version: null
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
                this.toggleBtn.addEventListener('click', (e) => {
                    e.stopPropagation();  // 阻止冒泡到标题栏的拖拽事件
                    this.toggle();
                });
            }

            // 绑定拖拽事件
            this.setupDragging();
            this.ensureImuElements();

            console.log('[DeviceStatusWidget] 初始化完成');
        }

        /**
         * 设置拖拽功能
         */
        setupDragging() {
            const header = this.floatElement.querySelector('.device-status-header');
            if (!header) return;

            // 鼠标按下
            header.addEventListener('mousedown', (e) => {
                // 如果点击的是按钮，不触发拖拽
                if (e.target.closest('.status-toggle-btn')) {
                    return;
                }

                this.isDragging = true;
                this.dragStartX = e.clientX;
                this.dragStartY = e.clientY;

                const rect = this.floatElement.getBoundingClientRect();
                this.elementStartX = rect.left;
                this.elementStartY = rect.top;

                // 添加拖拽样式
                this.floatElement.style.cursor = 'grabbing';
                header.style.cursor = 'grabbing';

                e.preventDefault();
            });

            // 鼠标移动
            document.addEventListener('mousemove', (e) => {
                if (!this.isDragging) return;

                const deltaX = e.clientX - this.dragStartX;
                const deltaY = e.clientY - this.dragStartY;

                let newX = this.elementStartX + deltaX;
                let newY = this.elementStartY + deltaY;

                // 限制在视口内
                const rect = this.floatElement.getBoundingClientRect();
                const maxX = window.innerWidth - rect.width;
                const maxY = window.innerHeight - rect.height;

                newX = Math.max(0, Math.min(newX, maxX));
                newY = Math.max(0, Math.min(newY, maxY));

                // 移除fixed的top/right定位，改用transform
                this.floatElement.style.top = newY + 'px';
                this.floatElement.style.right = 'auto';
                this.floatElement.style.left = newX + 'px';

                e.preventDefault();
            });

            // 鼠标释放
            document.addEventListener('mouseup', () => {
                if (this.isDragging) {
                    this.isDragging = false;
                    this.floatElement.style.cursor = '';
                    header.style.cursor = 'move';
                }
            });

            // 设置标题栏光标样式
            header.style.cursor = 'move';
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
                    stream_mode: status.stream_mode || 'idle',
                    num_imus: Number.isFinite(status.num_imus) ? status.num_imus : null,
                    hw_version: status.hw_version || null
                };
                this.renderDevice('left', this.device1Status);
            } else if (deviceId === 2) {
                this.device2Status = {
                    connected: status.connected || false,
                    battery: status.battery_percent || 0,
                    stream_mode: status.stream_mode || 'idle',
                    num_imus: Number.isFinite(status.num_imus) ? status.num_imus : null,
                    hw_version: status.hw_version || null
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
            this.updateImuCount(prefix, status.num_imus, status.connected);
        }

        /**
         * 更新电池显示
         */
        ensureImuElements() {
            ['left', 'right'].forEach((prefix) => {
                const rowEl = document.getElementById(`${prefix}RingRow`);
                if (!rowEl || document.getElementById(`${prefix}ImuCount`)) return;

                const imuEl = document.createElement('span');
                imuEl.className = 'imu-status';
                imuEl.id = `${prefix}ImuCount`;
                imuEl.title = 'Detected IMU count';
                imuEl.innerHTML = '<i class="fas fa-microchip" style="font-size: 6px;"></i><span>IMU --</span>';
                rowEl.appendChild(imuEl);
            });
        }

        updateImuCount(prefix, numImus, connected) {
            const imuEl = document.getElementById(`${prefix}ImuCount`);
            const rowEl = document.getElementById(`${prefix}RingRow`);
            if (!imuEl) return;

            const textEl = imuEl.querySelector('span');
            const value = Number.isFinite(numImus) ? numImus : null;
            imuEl.classList.remove('ok', 'warning');
            if (rowEl) rowEl.classList.remove('imu-warning');

            if (!connected) {
                if (textEl) textEl.textContent = 'IMU --';
                return;
            }

            if (value === null || value === 0) {
                if (textEl) textEl.textContent = 'IMU ?/3';
                return;
            }

            if (textEl) textEl.textContent = `IMU ${value}/3`;
            if (value === 3) {
                imuEl.classList.add('ok');
            } else {
                imuEl.classList.add('warning');
                if (rowEl) rowEl.classList.add('imu-warning');
            }
        }

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
            const rowEl = document.getElementById(`${prefix}RingRow`);
            if (!streamEl) return;

            const textEl = streamEl.querySelector('span');

            // 清除行级状态类
            if (rowEl) rowEl.classList.remove('ring-preview', 'ring-collection');

            if (!connected) {
                streamEl.className = 'stream-mode';
                textEl.textContent = '空闲';
                return;
            }

            let modeText = '空闲';
            let modeClass = 'stream-mode';

            if (mode === 'preview') {
                modeText = '预览中';
                modeClass = 'stream-mode preview';
                if (rowEl) rowEl.classList.add('ring-preview');
            } else if (mode === 'collection') {
                modeText = '采集中';
                modeClass = 'stream-mode collection';
                if (rowEl) rowEl.classList.add('ring-collection');
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
