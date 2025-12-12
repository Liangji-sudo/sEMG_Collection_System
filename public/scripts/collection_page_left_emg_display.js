
// ==================== 全局变量 ====================
let emgWebSocket = null;
let isCollecting = false;
let isPaused = false; // 保留全局变量，用于兼容外部可能的调用
let dataBuffer = [];
let debugCounter = 0;
let lastDataTime = Date.now();
let all_channels_5_data_series = Array.from({ length: 16 }, () => []);


// ========== EMG大窗口配置常量 ==========
const EMG_CONFIG = {
    CHANNEL_COUNT: 16,          // 总通道数
    TOTAL_POINTS: 1000,         // 窗口总点数
    UPDATE_POINTS: 5,           // 每次更新点数（减少渲染开销）
    EMG_RANGE: 100,             // EMG信号范围(mV)
    UPDATE_INTERVAL: 10,        // 更新间隔(ms)
    AMPLITUDE_SCALE: 1.0        // 振幅缩放系数
};

// ========== 全局状态变量 ==========
let emgState = {
    containerWidth: 0,
    containerHeight: 0,
    channelHeight: 0,
    pointWidth: 0,
    currentPointer: 0,
    channelCanvases: [],
    channelStates: Array(EMG_CONFIG.CHANNEL_COUNT).fill(true),
    isPaused: false,
    lastUpdateTime: 0,
    packetCount: 0,
    sampleRate: 0,
    signalRange: "±0mV",
    connectionStatus: "未连接",
    runtimeStart : Date.now()
};

    // ===================== 更新性能面板 =====================
function updatePerfPanel(elapsed = 0) {
    const runtime = Math.floor((Date.now() - emgState.runtimeStart) / 1000);
    const openChannels = emgState.channelStates.filter(s => s).length;
    document.getElementById('perf-panel').innerHTML = 
        `渲染耗时: ${elapsed.toFixed(2)}ms<br>
        指针位置: ${emgState.currentPointer}/1000<br>
        已运行: ${runtime}s<br>
        开启通道: ${openChannels}/16`;
}


// ========== 初始化EMG大窗口 ==========
function initEMGBigWindow() {
    // 关键修复1：使用requestAnimationFrame确保DOM完全渲染后再计算尺寸
    requestAnimationFrame(() => {
        const emgWindow = document.getElementById('emg-big-window');
        const canvasContainer = document.getElementById('channels-canvas-container');
        
        // 强制刷新布局（解决初始尺寸为0的问题）
        emgWindow.style.display = 'block';
        const computedStyle = getComputedStyle(emgWindow);
        emgState.containerWidth = parseInt(computedStyle.width) || emgWindow.clientWidth;
        emgState.containerHeight = parseInt(computedStyle.height) || emgWindow.clientHeight;
        emgState.channelHeight = emgState.containerHeight / EMG_CONFIG.CHANNEL_COUNT;
        emgState.pointWidth = emgState.containerWidth / EMG_CONFIG.TOTAL_POINTS;

        // 清空原有Canvas
        canvasContainer.innerHTML = '';

        // 动态创建16个通道Canvas
        for (let ch = 0; ch < EMG_CONFIG.CHANNEL_COUNT; ch++) {
            const canvas = document.createElement('canvas');
            canvas.id = `emg-channel-${ch}`;
            // 关键修复2：Canvas宽高必须是像素值（而非百分比），匹配容器实际尺寸
            canvas.width = emgState.containerWidth;
            canvas.height = emgState.channelHeight;
            canvas.style.cssText = `
                position: absolute;
                top: ${ch * emgState.channelHeight}px;
                left: 0;
                width: 100%;
                height: ${emgState.channelHeight}px;
                image-rendering: pixelated;
            `;

            // 初始化Canvas上下文
            const ctx = canvas.getContext('2d');
            ctx.imageSmoothingEnabled = false;
            ctx.fillStyle = '#000';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            // 关键修复3：提高线条宽度，确保可见
            ctx.strokeStyle = `hsl(${(ch * 20) % 360}, 100%, 50%)`; // 更鲜艳的颜色
            ctx.lineWidth = 1.5; // 加粗线条

            canvasContainer.appendChild(canvas);
            emgState.channelCanvases.push({ ctx, canvas, channelIndex: ch });
        }

        // 初始化时间指针（此时尺寸已正确计算）
        updateTimePointer();
    });

    // 监听窗口大小变化
    window.addEventListener('resize', resizeEMGBigWindow);
}

// ========== 调整EMG窗口尺寸 ==========
function resizeEMGBigWindow() {
    const emgWindow = document.getElementById('emg-big-window');
    // 强制刷新布局
    const computedStyle = getComputedStyle(emgWindow);
    emgState.containerWidth = parseInt(computedStyle.width) || emgWindow.clientWidth;
    emgState.containerHeight = parseInt(computedStyle.height) || emgWindow.clientHeight;
    emgState.channelHeight = emgState.containerHeight / EMG_CONFIG.CHANNEL_COUNT;
    emgState.pointWidth = emgState.containerWidth / EMG_CONFIG.TOTAL_POINTS;

    // 更新所有通道Canvas尺寸和位置
    emgState.channelCanvases.forEach(({ ctx, canvas, channelIndex }) => {
        canvas.width = emgState.containerWidth;
        canvas.height = emgState.channelHeight;
        canvas.style.top = `${channelIndex * emgState.channelHeight}px`;
        
        // 重置Canvas背景
        ctx.fillStyle = '#000';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.strokeStyle = `hsl(${(channelIndex * 20) % 360}, 100%, 50%)`;
        ctx.lineWidth = 1.5;
    });

    // 更新指针位置
    updateTimePointer();
}

// ========== 更新时间指针 ==========
function updateTimePointer() {
    const pointer = document.getElementById('global-time-pointer');
    const label = document.getElementById('pointer-time-label');
    // 关键修复4：指针位置计算基于实际的pointWidth（避免初始为0）
    const pointerX = emgState.currentPointer * emgState.pointWidth;
    
    // 确保指针在可视区域内
    if (!isNaN(pointerX) && emgState.containerWidth > 0) {
        pointer.style.left = `${pointerX}px`;
        label.style.left = `${pointerX + 5}px`;
    }
    label.textContent = `时间点 [${emgState.currentPointer}/${EMG_CONFIG.TOTAL_POINTS}]`;
}

// ========== 更新EMG数据（局部渲染，仅更新新增点） ==========
function updateEMGData() {
    if (emgState.isPaused) return;
    // 防护：尺寸未初始化时跳过
    if (emgState.containerWidth === 0 || emgState.containerHeight === 0) return;

    const startTime = performance.now();
    const startPos = emgState.currentPointer;
    const endPos = (startPos + EMG_CONFIG.UPDATE_POINTS) % EMG_CONFIG.TOTAL_POINTS;
    const updatePositions = [];

    // 生成本次需要更新的点位
    for (let i = 0; i < EMG_CONFIG.UPDATE_POINTS; i++) {
        updatePositions.push((startPos + i) % EMG_CONFIG.TOTAL_POINTS);
    }

    // 逐通道更新数据（仅渲染新增点区域）
    emgState.channelCanvases.forEach(({ ctx, canvas, channelIndex }) => {
        // 跳过关闭的通道
        if (!emgState.channelStates[channelIndex]) return;

        ctx.save();
        // 以通道中线为基准点
        ctx.translate(0, emgState.channelHeight / 2);

        // 仅更新新增的几个点，减少渲染开销
        for (let i = 0; i < EMG_CONFIG.UPDATE_POINTS; i++) {
            const pos = updatePositions[i];
            const x = pos * emgState.pointWidth;

            // 清空当前点区域（仅清空需要更新的小区域）
            ctx.clearRect(x, -emgState.channelHeight/2, emgState.pointWidth, emgState.channelHeight);

            // 生成模拟EMG数据（实际使用时替换为真实数据）
            //const emgValue = (Math.random() * 2 - 1) * EMG_CONFIG.EMG_RANGE * 0.8 * EMG_CONFIG.AMPLITUDE_SCALE;
            const emgValue = all_channels_5_data_series[channelIndex][i];
            // 坐标映射，确保波形在通道内可见
            const y = -(emgValue / EMG_CONFIG.EMG_RANGE) * (emgState.channelHeight / 2 * 0.9); // 负号修正Y轴方向
            //const y = emgValue;
            //console.log('liangji get y = ', emgValue);

            // ========== 核心修改：仅绘制单点，移除连线逻辑 ==========
            // 设置点的填充颜色（与通道线条颜色一致）
            ctx.fillStyle = ctx.strokeStyle;
            // 绘制圆形点（增大半径到2px，确保清晰可见）
            ctx.beginPath();
            // arc(圆心X, 圆心Y, 半径, 起始角度, 结束角度)
            ctx.arc(x, y, 1, 0, Math.PI * 2);
            ctx.fill();

        }

        ctx.restore();
    });

    // 更新指针位置
    emgState.currentPointer = endPos;
    updateTimePointer();

    // 更新统计信息
    //updateEMGStats(startTime);

    // 更新性能面板
    updatePerfPanel(performance.now() - startTime);
}

// ========== 更新EMG统计信息 ==========
function updateEMGStats(startTime) {
    // 更新数据包计数
    emgState.packetCount++;
    document.getElementById('packet-count').textContent = emgState.packetCount;

    // 计算采样率（模拟）
    const cost = performance.now() - startTime;
    emgState.sampleRate = cost > 0 ? Math.round(1000 / cost) : 0;
    document.getElementById('sample-rate').textContent = emgState.sampleRate;

    // 更新信号范围
    emgState.signalRange = `±${EMG_CONFIG.EMG_RANGE}mV`;
    document.getElementById('signal-range').textContent = emgState.signalRange;

    // 更新最后更新时间
    const now = new Date();
    const updateTime = `${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}.${now.getMilliseconds().toString().padStart(3, '0')}`;
    document.getElementById('last-update').textContent = updateTime;

    // 更新开启通道数
    const activeChannels = emgState.channelStates.filter(state => state).length;
    document.getElementById('active-channels').textContent = `${activeChannels}/${EMG_CONFIG.CHANNEL_COUNT}`;

    // 更新连接状态（模拟）
    emgState.connectionStatus = "已连接";
    document.getElementById('connection-status').textContent = emgState.connectionStatus;
}

// ========== 绑定EMG控制事件 ==========
function bindEMGEvents() {
    // 暂停/继续按钮
    /*
    document.getElementById('pause-signal').addEventListener('click', function() {
        emgState.isPaused = !emgState.isPaused;
        this.innerHTML = emgState.isPaused ? 
            '<i class="fa fa-play mr-1"></i>继续' : 
            '<i class="fa fa-pause mr-1"></i>暂停';
    });
    */

    // 清空按钮
    document.getElementById('clear-signal').addEventListener('click', function() {
        emgState.channelCanvases.forEach(({ ctx, canvas }) => {
            ctx.fillStyle = '#000';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
        });
        emgState.currentPointer = 0;
        updateTimePointer();
        emgState.packetCount = 0;
        document.getElementById('packet-count').textContent = 0;
    });

    // 通道选择下拉框
    document.getElementById('channel-select').addEventListener('change', function(e) {
        const value = e.target.value;
        for (let ch = 0; ch < EMG_CONFIG.CHANNEL_COUNT; ch++) {
            switch(value) {
                case 'all':
                    emgState.channelStates[ch] = true;
                    break;
                case '1-8':
                    emgState.channelStates[ch] = ch < 8;
                    break;
                case '9-16':
                    emgState.channelStates[ch] = ch >= 8;
                    break;
            }
            // 显示/隐藏对应通道Canvas
            const channelCanvas = document.getElementById(`emg-channel-${ch}`);
            if (channelCanvas) {
                channelCanvas.style.display = emgState.channelStates[ch] ? 'block' : 'none';
            }
        }
        // 更新激活通道统计
        const activeChannels = emgState.channelStates.filter(state => state).length;
        document.getElementById('active-channels').textContent = `${activeChannels}/${EMG_CONFIG.CHANNEL_COUNT}`;
    });

    // 采集开始/停止按钮联动（模拟）
    document.getElementById('start-collect').addEventListener('click', function() {
        emgState.isPaused = false;
        document.getElementById('pause-signal').innerHTML = '<i class="fa fa-pause mr-1"></i>暂停';
        this.classList.add('hidden');
        document.getElementById('stop-collect').classList.remove('hidden');
    });

    document.getElementById('stop-collect').addEventListener('click', function() {
        emgState.isPaused = true;
        document.getElementById('pause-signal').innerHTML = '<i class="fa fa-play mr-1"></i>继续';
        this.classList.add('hidden');
        document.getElementById('start-collect').classList.remove('hidden');
    });
}

// ========== 主更新循环 ==========
// function emgUpdateLoop(timestamp) {
//     if (timestamp - emgState.lastUpdateTime >= EMG_CONFIG.UPDATE_INTERVAL) {
//         updateEMGData();
//         emgState.lastUpdateTime = timestamp;
//     }
//     requestAnimationFrame(emgUpdateLoop);
// }

// ========== 页面加载初始化 ==========
// 关键修复7：确保采集界面显示后再初始化（解决隐藏元素尺寸为0的问题）
function initWhenPageVisible() {
    // 监听采集页面显示事件（如果你的页面切换是通过class控制的）
    const observer = new MutationObserver((mutations) => {
        mutations.forEach(mutation => {
            if (mutation.target.id === 'collect-page' && !mutation.target.classList.contains('hidden')) {
                initEMGBigWindow();
                bindEMGEvents();
                return;
                // requestAnimationFrame(emgUpdateLoop);
                // observer.disconnect(); // 只执行一次
            }
        });
    });

    // 观察collect-page的class变化
    observer.observe(document.getElementById('collect-page'), {
        attributes: true,
        attributeFilter: ['class']
    });

    // 备用：如果页面初始就是显示的
    const collectPage = document.getElementById('collect-page');
    if (!collectPage.classList.contains('hidden')) {
        initEMGBigWindow();
        bindEMGEvents();
        updatePerfPanel();
        return;
        // requestAnimationFrame(emgUpdateLoop);
        // observer.disconnect();
    }
}


// ==================== 左侧EMG信号显示模块 ====================
const EMGDisplayModule = {
    isInitialized: false,       // 初始化标志
    isConnected: false,         // 连接状态标志
    isConnecting: false,        // 连接中标志
    isPaused: false,            // 模块内暂停状态（核心控制）
    graphs: {},                 // 存储16个通道的Dygraphs实例
    dataSeries: {},             // 存储每个通道的数据系列
    emgWebSocket: null,         // WebSocket实例（模块内维护）
    reconnectTimer: null,       // 重连定时器
    packet_5_Counter: 0,           // 数据大包计数器（模块内维护）
    windowPoints: 500,          // 窗口内固定显示的点数（可调整）
    prediv: 1000,
    big_bag_interval: 0,
    one_bag_interval: 0,
    big_bag_last_timestamp: 0,

    // 初始化信号显示
    initialize() {
        if (this.isInitialized) {
            console.log('EMG模块已初始化，跳过重复执行。');
            return;
        }

        console.log('📊 初始化EMG信号显示');

        this.connectToRealtimeEngine();
        initWhenPageVisible();
        this.isInitialized = true;
    },


    /**
 * 从emg_data数组中提取指定通道的uint16_t数组
 * @param {string[]} emg_data - 包含5个64字符16进制字符串的数组
 * @param {number} i - 通道号（0~15，超出范围抛错）
 * @returns {number[]} 长度为5的uint16_t数组（每个元素是对应位置的通道i数值）
 */
    getChannelUint16Array(emg_data, channel) {
    // 1. 校验输入合法性
    if (!Array.isArray(emg_data) || emg_data.length !== 5) {
        throw new Error('emg_data必须是包含5个元素的数组');
    }
    if (typeof channel !== 'number') {
        throw new Error('通道号i必须是0~15之间的整数');
    }

    // 2. 初始化结果数组
    const result = new Array(5);

    // 3. 遍历每个64字符的字符串，解析指定通道的uint16_t
    emg_data.forEach((hexStr, index) => {
        // 校验单个字符串长度
        if (typeof hexStr !== 'string' || hexStr.length !== 64) {
            throw new Error(`第${index}个元素不是64字符的16进制字符串`);
        }

        // 计算该通道在字符串中的起始索引（1通道=4个16进制字符）
        const startIdx = (channel - 1) * 4;
        // 截取4个字符（对应2字节=1个uint16_t）
        const channelHex = hexStr.substring(startIdx, startIdx + 4);

        // 4. 转换为uint16_t（默认小端序，若硬件是大端序则交换字节）
        // 小端序：低位字节在前，高位字节在后（如 "e332" → 0x32e3）
        // 大端序：高位字节在前，低位字节在后（如 "e332" → 0xe332）
        const isLittleEndian = true; // 根据硬件协议调整
        let uint16Value;

        if (isLittleEndian) {
            // 小端序处理：拆分高低字节并交换
            const lowByte = channelHex.substring(2, 4); // 后2字符=低位字节
            const highByte = channelHex.substring(0, 2); // 前2字符=高位字节
            uint16Value = parseInt(highByte + lowByte, 16);
        } else {
            // 大端序处理：直接解析
            uint16Value = parseInt(channelHex, 16);
        }

        // 确保是uint16_t（0~65535）
        uint16Value = uint16Value & 0xFFFF;

        // 存入结果数组
        // 除以倍数，放缩到合适范围
        result[index] = uint16Value / this.prediv;

    });

    return result;
},

// 更新单个通道数据（核心修复：实现固定窗口平移效果 + 动态Y轴）
// 一个大包，针对第channel个通道，一次更新5个时间点的数据
    updateEMGChannel(channel, emgData) {

        let add_data_array = this.getChannelUint16Array(emgData, channel);
        all_channels_5_data_series[channel - 1].push(add_data_array[0],
                                                    add_data_array[1],
                                                    add_data_array[2],
                                                    add_data_array[3],
                                                    add_data_array[4]);
    },



    // 一个大包更新所有通道
    updateAllEMGChannels(emgData) {
        if (!emgData || !emgData.big_bag_raw_data || emgData.big_bag_raw_data.length !== 5) {
            console.warn('无效的EMG数据');
            return;
        }

        this.packet_5_Counter++;

        //计算时间戳
        this.big_bag_interval = emgData.timestamp[0] - this.big_bag_last_timestamp;
        this.one_bag_interval = emgData.timestamp[1] - emgData.timestamp[0];
        this.big_bag_last_timestamp = emgData.timestamp[0];
        //console.log('big bag and one bag interval = ', this.big_bag_interval, this.one_bag_interval);
        
        all_channels_5_data_series.forEach(subArr => subArr.length = 0);
        // 更新每个通道
        for (let i = 0; i < 16; i++) {
            this.updateEMGChannel(i + 1, emgData.big_bag_raw_data);
        }

        //刷新display局部,渲染
        requestAnimationFrame(updateEMGData);
        
        // 更新统计信息
        this.updateStatistics(emgData);
        
        // 调试输出：每100个数据包打印一次
        debugCounter++;
        if (debugCounter % 100 === 0) {
            //this.debugPrintEMGData(emgData);
        }
    },

    // 调试输出EMG数据
    debugPrintEMGData(emgData) {
        console.log('📦 EMG数据包 #' + emgData.packetCount);
        console.log('📊 16通道数据:');
        
        for (let i = 0; i < 16; i++) {
            console.log(`  通道 ${(i + 1).toString().padStart(2)}: ${emgData.channels[i].toFixed(7).padStart(8)} mV`);
        }
        
        console.log('⏱️  时间戳:', emgData.timestamp);
        console.log('📈 采样率:', emgData.interval > 0 ? Math.round(1000 / emgData.interval) + ' Hz' : 'N/A');
        console.log('----------------------------------------');
    },

    // 更新统计信息
    updateStatistics(emgData) {
        const packetCount = document.getElementById('packet-count');
        const sampleRate = document.getElementById('sample-rate');
        const signalRange = document.getElementById('signal-range');
        const lastUpdate = document.getElementById('last-update');
        
        if (packetCount) packetCount.textContent = this.packet_5_Counter; //收到一个大包
        if (sampleRate) sampleRate.textContent = 5* (1 / this.big_bag_interval);
        if (signalRange && emgData.channels) {
            const min = Math.min(...emgData.channels);
            const max = Math.max(...emgData.channels);
            const range = Math.max(Math.abs(min), Math.abs(max));
            signalRange.textContent = `±${range.toFixed(7)}mV`;
        }
        if (lastUpdate) lastUpdate.textContent = new Date().toLocaleTimeString();
    },

    // WebSocket连接管理
    connectToRealtimeEngine() {
        // 阻止重复连接
        if (this.isConnecting || this.isConnected) {
            console.log(`[EMG连接] 无需重复连接 - 连接中: ${this.isConnecting}, 已连接: ${this.isConnected}`);
            return;
        }

        // 清除现有重连定时器
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }

        try {
            this.isConnecting = true;
            const connectionStartTime = Date.now();
            console.log(`[EMG连接] 开始连接 (${new Date().toISOString()})，时间戳: ${connectionStartTime}`);

            // 关闭可能存在的旧连接
            if (this.emgWebSocket) {
                this.emgWebSocket.close(1001, '替换为新连接');
                this.emgWebSocket = null;
            }

            // 创建新连接（与后端realtimeEngine.js的8080端口对应）
            this.emgWebSocket = new WebSocket('ws://localhost:8080');

            // 连接超时保护（5秒）
            const connectionTimeout = setTimeout(() => {
                if (this.isConnecting) {
                    console.error('[EMG连接] 超时未建立连接（>5秒）');
                    this.emgWebSocket?.close(1006, '连接超时');
                    this.handleConnectionError('连接超时');
                }
            }, 5000);

            // 连接成功回调
            this.emgWebSocket.onopen = () => {
                clearTimeout(connectionTimeout);
                const duration = Date.now() - connectionStartTime;
                
                this.isConnecting = false;
                this.isConnected = true;
                this.updateConnectionStatus('已连接');
                
                console.log(`[EMG连接] 成功（耗时 ${duration}ms）`);
            };

            // 消息接收处理
            this.emgWebSocket.onmessage = (event) => {
                // 关键修复：使用模块内的isPaused状态
                if (this.isPaused) return;

                try {
                    const packet = JSON.parse(event.data);
                    
                    // 处理连接确认消息（与后端realtimeEngine的connection_established对应）
                    if (packet.type === 'connection_established') {
                        console.log(`[EMG消息] 服务器确认: ${packet.message}`);
                        return;
                    }
                    
                    // 处理EMG大包数据（一个大包5*32, 每10ms来一个大包）
                    if (packet.type === 'emg_data') {                       


                        this.updateAllEMGChannels(packet.data);
                        //console.log('interval = ',packet.data.timestamp, this.interval);
                        
                        // 隐藏无信号提示
                        const noSignalEl = document.getElementById('no-signal-message');
                        if (noSignalEl) noSignalEl.classList.add('hidden');
                    }
                } catch (error) {
                    console.error('[EMG消息] 解析失败:', error);
                }
            };

            // 错误处理
            this.emgWebSocket.onerror = (error) => {
                clearTimeout(connectionTimeout);
                console.error('[EMG连接] 错误:', error);
                this.handleConnectionError('连接错误');
            };

            // 关闭处理
            this.emgWebSocket.onclose = (event) => {
                clearTimeout(connectionTimeout);
                console.log(`[EMG连接] 关闭（代码: ${event.code}，原因: ${event.reason || '无'}）`);

                this.isConnecting = false;
                this.isConnected = false;
                this.emgWebSocket = null;

                // 正常关闭（1000）或主动替换（1001）不重连，其他情况重连
                const shouldReconnect = !([1000, 1001].includes(event.code));
                if (shouldReconnect) {
                    this.updateConnectionStatus('重新连接中...');
                    this.reconnectTimer = setTimeout(() => {
                        this.connectToRealtimeEngine();
                    }, 3000);
                } else {
                    this.updateConnectionStatus('已断开（正常关闭）');
                }
            };

        } catch (error) {
            console.error('[EMG连接] 创建失败:', error);
            this.handleConnectionError('创建连接失败');
        }
    },

    // 连接错误统一处理
    handleConnectionError(reason) {
        this.isConnecting = false;
        this.isConnected = false;
        this.emgWebSocket = null;
        this.updateConnectionStatus(reason);

        // 安排重连（避免重复触发）
        if (!this.reconnectTimer) {
            this.reconnectTimer = setTimeout(() => {
                this.connectToRealtimeEngine();
            }, 3000);
        }
    },

    // 更新连接状态显示
    updateConnectionStatus(status) {
        const statusEl = document.getElementById('connection-status');
        if (statusEl) {
            statusEl.textContent = status;
            // 可添加状态样式区分（成功/错误/重连）
            statusEl.className = status.includes('已连接') ? 'status-connected' :
                                status.includes('错误') ? 'status-error' : 'status-reconnecting';
        }
    },



    // 过滤通道显示（核心修复）
    filterChannels(filter) {
        // 选择所有通道项容器
        const channelItems = document.querySelectorAll('.channel-item');
        channelItems.forEach(item => {
            const channelNum = parseInt(item.dataset.channel);
            const shouldShow = filter === 'all' || 
                (filter === '1-8' && channelNum <= 8) ||
                (filter === '9-16' && channelNum >= 9);
            
            item.style.display = shouldShow ? 'block' : 'none';
        });
    }
};

// ==================== 页面初始化 ====================
// 同时检查页面初始化逻辑，修改DOMContentLoaded事件处理
document.addEventListener('DOMContentLoaded', function() {
    // 页面显示时自动初始化
    const collectPage = document.getElementById('collect-page');
    console.log('------------------DOMContentLoaded ----------------------------------------------------------------------------------');
    // 增加直接初始化逻辑，防止MutationObserver失效
    function initializeIfActive() {
        if (!collectPage.classList.contains('hidden')) {
            if (!EMGDisplayModule.isInitialized) {
                console.log('📋 采集页面已显示，初始化所有模块');
                EMGDisplayModule.initialize();
                //CollectionModule.initialize();
            }
        }else {
        // 如果页面被隐藏了，可以在这里执行清理操作
            console.log('📋 采集页面已隐藏，do nothing。');
        }

    }
    
    // 立即检查一次
    //console.log('first initializeIfActive+++++++++++');
    //initializeIfActive();
    //console.log('first initializeIfActive   over +++++++++++');
    
    // 继续使用观察者监听
    const observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                //console.log('mutation start initializeIfActive !!!!!!!!!!!!!!!');
                initializeIfActive();
                //console.log('mutation start initializeIfActive over !!!!!!!!!!!!!!!');
            }
        });
    });
    
    observer.observe(collectPage, { attributes: true });
});
