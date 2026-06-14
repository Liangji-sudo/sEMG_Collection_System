// realtimeEngine.js - v4.2 (新增动捕数据支持)
// 修改: 新增mocap_server连接和数据转发

const WebSocket = require('ws');
const EventEmitter = require('events');
const zmq = require('zeromq');
const express = require('express');
const cors = require('cors');
const app = express();
app.use(cors());
app.use(express.json());

const { discrete_gesture_prompt_name, collection_task_name } = require('./constants.js');

function getSysTimeNode() {
    const nsTimestamp = process.hrtime.bigint();
    const sTimestamp = Number(nsTimestamp) / 1000000000.0;
    return Math.round(sTimestamp * 1000000000) / 1000000000;
}

class RealtimeEngine extends EventEmitter {
    constructor() {
        super();
        this.websocket_server = null;
        this.clients = new Set();
        this.isRunning = false;
        this.dataBuffer = [];
        this.maxBufferSize = 1000;

        // BLE服务器
        this.ble_client = null;
        this.ble_clientUrl = 'ws://localhost:8766';
        this.reconnectInterval = 2000;   // 重连间隔2秒
        this.maxReconnectTimes = 10;     // 最多重连10次
        this.currentReconnectTimes = 0;
        this.reconnectTimer = null;
        this.connectTimeoutTimer = null;

        // 【新增】Mocap服务器
        this.mocap_client = null;
        this.mocap_clientUrl = 'ws://localhost:8767';
        this.mocap_reconnectInterval = 2000;
        this.mocap_maxReconnectTimes = 10;
        this.mocap_currentReconnectTimes = 0;
        this.mocap_reconnectTimer = null;
        this.mocap_connected = false;
        this.mocap_activeChannel = null;

        // 数据包计数
        this.emg_packet_count = 0;
        this.emg_5_packets_count = 0;
        this.dev1_packet_count = 0;
        this.dev2_packet_count = 0;
        this.mocap_packet_count = 0;

        // 【优化】批量发送缓冲区
        this.realtimeDataBuffer = [];
        this.realtimeDataBufferLimit = 3;  // 每3个数据包发送一次（约100ms间隔）
        this.realtimeDataTimer = null;
        this.realtimeDataMaxDelay = 50;    // 最大延迟50ms

        // Storage Server
        this.storage_server_socket = new zmq.Request();  // REP socket 用于控制命令
        this.storage_push_socket = new zmq.Push();       // 【新增】PUSH socket 用于数据发送
        this.storage_server_host = '127.0.0.1';
        this.storage_server_port = 5555;
        this.storage_data_port = 5556;                   // 【新增】数据端口
        this.storage_connected = false;
        this.storage_push_connected = false;             // 【新增】PUSH连接状态
        this.storageRequestQueue = [];
        this.isStorageRequestPending = false;

        // 采集状态
        this.currentTaskId = null;
        this.currentUser = null;
        this.isCollecting = false;
        this.collectionPaused = false;
        this.collectionConfig = null;
        this.isTestMode = false;  // 【新增】测试模式标志（不保存H5文件）

        // Stage状态
        this.currentStageName = null;
        this.stageFileOpen = false;
        this.stage_start_time = 0;
        this.currentStageNeedMocap = false;  // 【新增】当前stage是否需要动捕数据
        
        // Session状态
        this.currentSessionIndex = 0;
        this.currentSessionNumber = 1;
        this.sessionCount = 3;
        this.isClosingStageFile = false;

        // 动捕数据存储
        this.saveMocapData = false;

        // 【新增】SD卡bin文件名（用于HDF5溯源）
        this.sd_filenames = { dev1: null, dev2: null };
        // 【新增】BLE设备名称（用于HDF5追溯数据来源）
        this.device_names = { dev1: null, dev2: null };

        // 【新增】Stream mode 状态（preview/collection 切流方案）
        this.streamMode = 'idle';  // 'idle' | 'preview' | 'collection'
        this.collectionStreamId = null;  // collection stream 的唯一标识（ISO timestamp）
        this.collectionBinFilenames = { dev1: null, dev2: null };  // collection stream 产生的 bin
        this.collectionDataStartTs = 0;
        this.collectionDroppedStaleBlePackets = 0;
        this.streamSwitchDelayMs = 3000;  // STOP→START 延迟（与 ble_server.py 保持一致）
        this.timestampToStartDelayMs = 200;

        // 【新增】录像同步相关
        this.recordingSessionId = null;  // 录像会话ID
        this.isMultiSession = false;     // 是否为多轮次采集

        // 【新增】异常中断冻结状态
        this.abortFreezeActive = false;       // 是否已冻结写入
        this.pendingAbortFreeze = null;       // { interruptedAt, progress }
    }

    start(port = 8080) {
        return new Promise((resolve, reject) => {
            try {
                // 延迟连接BLE服务器，等待ble_server启动完成（包括蓝牙适配器预热）
                this.connectTimeoutTimer = setTimeout(() => {
                    this.ble_server_connect();
                }, 5000);  // 从3秒改为5秒，给ble_server更多启动时间

                // 【新增】延迟连接Mocap服务器
                setTimeout(() => {
                    this.mocap_server_connect();
                }, 5500);

                this.websocket_server = new WebSocket.Server({ port });

                // 【新增】客户端ID计数器
                let clientIdCounter = 0;

                this.websocket_server.on('connection', (ws, req) => {
                    // 【新增】为每个客户端分配唯一ID
                    const clientId = ++clientIdCounter;
                    ws.clientId = clientId;
                    ws.clientName = `未知客户端#${clientId}`;  // 默认名称，等待客户端自报
                    ws.connectedAt = new Date().toISOString();

                    console.log(`[realtimeEngine] 前端client连接已建立 (ID: ${clientId}, 当前总数: ${this.clients.size + 1})`);
                    this.clients.add(ws);

                    ws.send(JSON.stringify({
                        type: 'connection_established',
                        message: '实时数据连接已建立',
                        timestamp: Date.now(),
                        mocap_connected: this.mocap_connected,
                        clientId: clientId  // 【新增】告知客户端其ID
                    }));

                    ws.on('message', (message) => {
                        this.handleFrontendMessage(message, ws);
                    });

                    ws.on('close', () => {
                        console.log(`[realtimeEngine] 前端WebSocket连接已关闭 (ID: ${ws.clientId}, 名称: ${ws.clientName})`);
                        this.clients.delete(ws);
                    });

                    ws.on('error', (error) => {
                        console.error(`[realtimeEngine] WebSocket错误 (ID: ${ws.clientId}):`, error);
                        this.clients.delete(ws);
                    });
                });

                this.websocket_server.on('listening', () => {
                    console.log(`[realtimeEngine] WebSocket服务运行在端口 ${port}`);
                    this.isRunning = true;
                    resolve();
                });

                this.websocket_server.on('error', (error) => {
                    console.error('[realtimeEngine] WebSocket服务器启动失败:', error);
                    reject(error);
                });

                this.storage_server_connect();

            } catch (error) {
                console.error('[realtimeEngine] 启动失败:', error);
                reject(error);
            }
        });
    }

    handleFrontendMessage(rawMessage, ws) {
        try {
            const message = JSON.parse(rawMessage.toString());

            // 【新增】处理客户端自报身份
            if (message.type === 'client_identify') {
                if (ws && message.clientName) {
                    ws.clientName = message.clientName;
                    console.log(`[realtimeEngine] 客户端 #${ws.clientId} 自报身份: ${message.clientName}`);
                }
                return;
            }

            if (message.type !== 'control_command') return;

            const { action, data } = message;
            // 【修改】打印时包含客户端信息
            const clientInfo = ws ? `(来自: ${ws.clientName})` : '';
            console.log(`[realtimeEngine] <<< 收到前端命令: ${action} ${clientInfo}`, data);

            switch (action) {
                case 'task_change': this.onTaskChange(data.taskId); break;
                case 'collection_start': this.onCollectionStart(data); break;
                case 'collection_pause': this.onCollectionPause(); break;
                case 'collection_resume': this.onCollectionResume(); break;
                case 'collection_stop': this.onCollectionStop(data.completed); break;
                case 'session_change': this.onSessionChange(data.sessionIndex, data.sessionNumber); break;
                case 'stage_change': this.onStageChange(data.stageIndex, data.stageName); break;
                case 'stage_start': this.onStageStart(data.stageName, data.stageIndex, data.timestamp, data.needMocap); break;
                case 'stage_end': this.onStageEnd(data.stageName, data.timestamp); break;
                case 'prompt_start': this.onPromptStart(data.promptName, data.promptIndex); break;
                case 'prompt_end': this.onPromptEnd(data.promptName, data.promptIndex); break;
                case 'prompt': this.onPrompt(data.name, data.stageName, data.timestamp); break;
                case 'video_recording_started': this.onVideoRecordingStarted(data); break; // 【新增】处理视频录制信息
                case 'abnormal_interrupt_freeze': this.onAbnormalInterruptFreeze(data); break;
                case 'abnormal_interrupt': this.onAbnormalInterrupt(data); break;

                // 【新增】Mocap命令
                case 'mocap_set_channel': this.onMocapSetChannel(data.channel); break;
                case 'mocap_reset_channel': this.onMocapResetChannel(data.channel, data.value); break;
                case 'mocap_get_status': this.onMocapGetStatus(); break;
                case 'mocap_set_save':
                    this.saveMocapData = data.save === true;
                    console.log(`[realtimeEngine] 动捕数据存储: ${this.saveMocapData ? '开启' : '关闭'}`);
                    break;
                case 'mocap_sdk_connect': this.onMocapSdkConnect(); break;
                case 'mocap_sdk_disconnect': this.onMocapSdkDisconnect(); break;
                case 'mocap_sdk_get_status': this.onMocapSdkGetStatus(); break;

                default: console.log(`[realtimeEngine] 未知命令: ${action}`);
            }

        } catch (error) {
            console.error('[realtimeEngine] 解析前端消息失败:', error);
        }
    }

    onTaskChange(taskId) {
        console.log(`[realtimeEngine] ========== 任务切换: ${taskId} ==========`);
        this.currentTaskId = taskId;

        // 【修改】通道映射（删除continual_gesture_3，通道名不带后缀，左右手都会计算）
        const channelMapping = {
            'continual_gesture_1': 'finger_joint_angle',
            'continual_gesture_2': 'thumb_index_distance'
        };
        if (channelMapping[taskId]) {
            this.onMocapSetChannel(channelMapping[taskId]);
        }
    }

    onSessionChange(sessionIndex, sessionNumber) {
        this.currentSessionIndex = sessionIndex ?? 0;
        this.currentSessionNumber = sessionNumber ?? (sessionIndex + 1);
    }

    async onCollectionStart(data) {
        console.log(`[realtimeEngine] ========== 开始采集会话 ==========`);
        const { taskId, stageName, userId, config, sessionIndex, sessionNumber, sessionCount, isTestMode, recordingSessionId, isMultiSession } = data;

        this.currentTaskId = taskId;
        this.currentUser = { id: userId, ...config?.subject };
        this.collectionConfig = config;
        this.isCollecting = true;
        this.collectionPaused = false;
        this.currentStageName = stageName;
        this.currentSessionIndex = sessionIndex ?? 0;
        this.currentSessionNumber = sessionNumber ?? 1;
        this.sessionCount = sessionCount ?? 3;
        this.collectionDataStartTs = Date.now() / 1000;
        this.collectionDroppedStaleBlePackets = 0;
        this.realtimeDataBuffer = [];
        if (this.realtimeDataTimer) {
            clearTimeout(this.realtimeDataTimer);
            this.realtimeDataTimer = null;
        }

        // 【新增】保存测试模式状态
        this.isTestMode = isTestMode || false;
        if (this.isTestMode) {
            console.log(`[realtimeEngine] ★★★ 测试模式：不会创建H5文件 ★★★`);
        }

        // 【新增】保存录像会话信息
        this.recordingSessionId = recordingSessionId || null;
        this.isMultiSession = isMultiSession || false;
        if (this.recordingSessionId) {
            console.log(`[realtimeEngine] 录像会话ID: ${this.recordingSessionId}`);
            console.log(`[realtimeEngine] 多轮次模式: ${this.isMultiSession}`);
        }

        // 【新增】重置视频录制标志
        this.videoRecordingStarted = false;
        this.collectionBins = null;

        // 【Phase 2】保存续采模式元数据
        this.isResume = data.isResume || false;
        this.resumeSegmentIndex = data.resumeSegmentIndex || 1;
        this.resumeFromInterruptedAt = data.resumeFromInterruptedAt || null;
        this.resumeReason = data.resumeReason || null;
        this.resumeParentRecordingSessionId = data.resumeParentRecordingSessionId || null;
        this.resumeParentSegmentIndex = data.resumeParentSegmentIndex || null;  // Phase 3
        if (this.isResume) {
            console.log(`[realtimeEngine] ★ 续采模式 ★`);
            console.log(`  segmentIndex: ${this.resumeSegmentIndex}`);
            console.log(`  resumeFrom: ${this.resumeFromInterruptedAt}`);
            console.log(`  resumeReason: ${this.resumeReason}`);
        }

        // 【修复 Issue 3】直接从 collection_start payload 获取 collection_bins
        // 不依赖异步 broadcast sd_filenames_updated 事件
        if (data.collectionBins) {
            this.streamMode = data.streamMode || 'collection';
            this.collectionBins = data.collectionBins;  // 【新增】保存collectionBins供视频录制使用
            this.collectionBinFilenames = {
                dev1: data.collectionBins?.dev1 || null,
                dev2: data.collectionBins?.dev2 || null
            };
            if (data.collectionDeviceNames) {
                this.device_names = {
                    dev1: data.collectionDeviceNames?.dev1 || this.device_names.dev1,
                    dev2: data.collectionDeviceNames?.dev2 || this.device_names.dev2
                };
            }
            this.collectionStreamId = data.collectionStreamId || new Date().toISOString();
            console.log(`[realtimeEngine] ★ collection bins 来自 payload（同步）:`);
            console.log(`  dev1: ${this.collectionBinFilenames.dev1 || '无'}`);
            console.log(`  dev2: ${this.collectionBinFilenames.dev2 || '无'}`);
            console.log(`  streamMode: ${this.streamMode}`);
            console.log(`  collectionStreamId: ${this.collectionStreamId}`);
        } else {
            console.log(`[realtimeEngine] ⚠️ collection_start payload 中无 collectionBins，将等待 sd_filenames_updated 事件`);
        }

        // sd_filenames_updated 事件作为兜底（见 onSdFilenamesUpdated）
    }

    onCollectionPause() { this.collectionPaused = true; }
    onCollectionResume() { this.collectionPaused = false; }

    async onCollectionStop(completed) {
        // 【新增】停止视频录制
        if (this.videoRecordingStarted && this.cameraManager) {
            console.log('[realtimeEngine] 🎥 停止视频录制...');

            // 停止左手摄像头
            if (this.collectionBins?.dev1) {
                await this.cameraManager.stopRecording('left').catch(err => {
                    console.error('[realtimeEngine] 停止左手摄像头录制失败:', err);
                });
            }

            // 停止右手摄像头
            if (this.collectionBins?.dev2) {
                await this.cameraManager.stopRecording('right').catch(err => {
                    console.error('[realtimeEngine] 停止右手摄像头录制失败:', err);
                });
            }

            this.videoRecordingStarted = false;
        }

        if (this.stageFileOpen && !this.isClosingStageFile) {
            // 显式传 collection_status：
            // completed === true  → "completed"（Stage 正常完成）
            // completed === false → "manual_stopped"（工作人员手动点停止）
            await this.closeStageFile({
                collection_status: completed ? 'completed' : 'manual_stopped'
            });
        }
        this.isCollecting = false;
        this.collectionPaused = false;
        // 【新增】重置测试模式标志
        this.isTestMode = false;
        // 【新增】重置 collection stream 状态（新 collection 需要新 stream）
        // 注意：collectionBinFilenames 在新 collection stream 就绪时会更新
        this.collectionStreamId = null;
        this.collectionBinFilenames = { dev1: null, dev2: null };
        this.collectionBins = null;  // 【新增】重置collectionBins
        this.collectionDataStartTs = 0;
        // 【修复】不在 stop 时清空 sd_filenames（由 sd_filenames_updated 事件管理）
    }

    // 【新增】异常中断冻结 — 立即停止 append，不关闭 H5
    onAbnormalInterruptFreeze(data) {
        const { interruptedAt, progress } = data || {};
        console.log(`[realtimeEngine] ========== 异常中断冻结 ==========`);
        console.log(`[realtimeEngine] 时间: ${interruptedAt || '未知'}`);

        // 立即停止写入
        this.isCollecting = false;
        this.collectionPaused = true;
        this.abortFreezeActive = true;
        this.pendingAbortFreeze = { interruptedAt, progress };

        console.log(`[realtimeEngine] H5 数据写入已冻结 (文件保持 open)`);
        console.log(`[realtimeEngine] isCollecting=${this.isCollecting}, collectionPaused=${this.collectionPaused}`);
    }

    // 【新增】异常中断处理 — 关闭 H5 并标记 abnormal_interrupted
    async onAbnormalInterrupt(data) {
        const { reason, interruptedAt, progress, breakpointState } = data || {};
        console.log(`[realtimeEngine] ========== 异常中断 ==========`);
        console.log(`[realtimeEngine] 原因: ${reason || '未知'}`);
        console.log(`[realtimeEngine] 时间: ${interruptedAt || '未知'}`);

        if (this.stageFileOpen && !this.isClosingStageFile) {
            // 关闭当前 H5，写入异常中断标记
            // 注意：不传 segment_index，保留 create_file 写入的值
            // （续采 H5 的 segment_index 由 create_file 写入，close 不覆盖）
            await this.closeStageFile({
                collection_status: 'abnormal_interrupted',
                interrupted_at: interruptedAt || new Date().toISOString(),
                interrupt_reason: reason || '未知',
                resume_progress: progress ? JSON.stringify(progress) : null,
                breakpoint_state: breakpointState ? JSON.stringify(breakpointState) : null
            });
        } else {
            console.log('[realtimeEngine] 没有打开的 H5 文件，跳过关闭');
        }

        // 重置采集状态（但不改变 sd_filenames）
        this.isCollecting = false;
        this.collectionPaused = false;
        this.isTestMode = false;
        // 清理 freeze 状态
        this.abortFreezeActive = false;
        this.pendingAbortFreeze = null;
        // 【新增】清理 collection stream 状态
        this.collectionStreamId = null;
        this.collectionBinFilenames = { dev1: null, dev2: null };
        this.collectionDataStartTs = 0;
        this.streamMode = 'idle';
        // 注意：不调用 onCollectionStop，避免覆盖 H5 标记
    }

    // 【新增】处理SD卡文件名和设备名称更新事件
    // 此事件由ble_server.py在start_all成功后发送，包含当前实际连接设备的文件名和设备名称
    onSdFilenamesUpdated(sd_filenames, device_names, stream_mode, collection_stream_id) {
        // 完全替换，只保存当前实际连接设备的文件名
        this.sd_filenames = {
            dev1: sd_filenames?.dev1 || null,
            dev2: sd_filenames?.dev2 || null
        };
        // 【新增】保存BLE设备名称
        this.device_names = {
            dev1: device_names?.dev1 || null,
            dev2: device_names?.dev2 || null
        };

        // 【新增】根据 stream_mode 更新 collection bin 记录
        if (stream_mode === 'collection') {
            this.streamMode = 'collection';
            this.collectionBinFilenames = { ...this.sd_filenames };
            // 【修复 Issue 2】优先使用 ble_server 传入的 collection_stream_id；只有未设置时才生成
            if (collection_stream_id) {
                this.collectionStreamId = collection_stream_id;
            } else if (!this.collectionStreamId) {
                this.collectionStreamId = new Date().toISOString();
            }
            console.log(`[realtimeEngine] ★ collection stream 已就绪 (event 兜底路径) ★`);
            console.log(`[realtimeEngine]   collection_bins: dev1=${this.collectionBinFilenames.dev1 || '无'}, dev2=${this.collectionBinFilenames.dev2 || '无'}`);
            console.log(`[realtimeEngine]   collection_stream_id: ${this.collectionStreamId}`);
        } else if (stream_mode === 'preview') {
            this.streamMode = 'preview';
            console.log(`[realtimeEngine] preview stream (bin 不参与 H5 同步)`);
        } else {
            this.streamMode = stream_mode || 'unknown';
        }

        console.log(`[realtimeEngine] SD卡文件名已更新: dev1=${this.sd_filenames.dev1 || '无'}, dev2=${this.sd_filenames.dev2 || '无'}, stream_mode=${this.streamMode}`);
        console.log(`[realtimeEngine] BLE设备名称已更新: dev1=${this.device_names.dev1 || '无'}, dev2=${this.device_names.dev2 || '无'}`);
    }

    onStageChange(stageIndex, stageName) { this.currentStageName = stageName; }

    async onStageStart(stageName, stageIndex, timestamp, needMocap = false) {
        this.currentStageName = stageName;
        this.stage_start_time = timestamp || Date.now();
        // 【新增】保存当前stage是否需要动捕数据
        this.currentStageNeedMocap = needMocap;
        console.log(`[realtimeEngine] Stage开始: ${stageName}, needMocap: ${needMocap}`);
        await this.openStageFile(stageName, stageIndex);
    }

    async onStageEnd(stageName, timestamp) {
        if (this.stageFileOpen && !this.isClosingStageFile) {
            await this.closeStageFile();
        }
    }

    onPromptStart(promptName, promptIndex) {}
    onPromptEnd(promptName, promptIndex) {}

    onPrompt(name, stageName, timestamp) {
        // 【新增】第一个space按下时，启动视频录制
        if (name === 'space' && !this.videoRecordingStarted) {
            console.log('[realtimeEngine] 🎥 检测到space，准备启动视频录制...');
            this._startVideoRecording(timestamp, stageName);
            this.videoRecordingStarted = true;
        }

        // 【新增】异常中断冻结状态下跳过 prompt
        if (this.abortFreezeActive) {
            console.log(`[realtimeEngine] 冻结状态：跳过保存 prompt (${name})`);
            return;
        }
        // 【新增】测试模式下不保存 prompt
        if (this.isTestMode) {
            console.log(`[realtimeEngine] 测试模式：跳过保存 prompt (${name})`);
            return;
        }

        const promptTime = timestamp || Date.now();
        // 【修复】不再设置 pending_prompt，直接保存
        // 之前的问题：设置了 pending_prompt 后立即保存，但没有清除
        // 导致 saveDataToStorage() 又保存了一次，造成重复

        // 立即保存 prompt 到 storage，不等待 EMG 数据
        // 如果文件还没打开，等待一小段时间后重试
        let retryCount = 0;
        const savePrompt = () => {
            if (this.stageFileOpen && !this.isClosingStageFile) {
                this.sendStorageCommand('append', {
                    data: {
                        prompt_name: name,
                        prompt_time: promptTime,
                        prompt_stage: stageName || this.currentStageName
                    }
                }).catch(err => {
                    console.error('[realtimeEngine] 保存 prompt 失败:', err);
                });
            } else {
                // 文件还没打开，100ms 后重试（最多重试 5 次）
                retryCount++;
                if (retryCount <= 5) {
                    console.log(`[realtimeEngine] 文件未打开，100ms 后重试保存 prompt (${retryCount}/5)`);
                    setTimeout(savePrompt, 100);
                } else {
                    console.warn('[realtimeEngine] 保存 prompt 失败：文件未打开（已重试 5 次）');
                }
            }
        };

        savePrompt();
    }

    // 【新增】处理视频录制信息
    onVideoRecordingStarted(data) {
        console.log('[realtimeEngine] 📹 收到视频录制信息:', data);

        // 测试模式下不保存
        if (this.isTestMode) {
            console.log('[realtimeEngine] 测试模式：跳过保存视频信息');
            return;
        }

        // 检查文件是否打开
        if (!this.stageFileOpen || this.isClosingStageFile) {
            console.warn('[realtimeEngine] 文件未打开，无法保存视频信息');
            return;
        }

        // 发送视频信息到 storage_server
        this.sendStorageCommand('video_recording_started', {
            video_left: data.video_left || null,
            video_right: data.video_right || null,
            video_start_timestamp: data.video_start_timestamp || null,
            h5_file_name: data.h5_file_name || null
        }).then(() => {
            console.log('[realtimeEngine] ✅ 视频信息已保存到H5文件');
        }).catch(err => {
            console.error('[realtimeEngine] ❌ 保存视频信息失败:', err);
        });
    }

    // 【新增】启动视频录制（后端ffmpeg录制）
    async _startVideoRecording(timestamp, stageName) {
        console.log('[realtimeEngine] 🎥 启动后端视频录制...');

        if (!this.cameraManager) {
            console.warn('[realtimeEngine] cameraManager未初始化');
            return;
        }

        // 获取 collection bins（用于生成视频文件名）
        const binFileNameLeft = this.collectionBins?.dev1;
        const binFileNameRight = this.collectionBins?.dev2;

        if (!binFileNameLeft && !binFileNameRight) {
            console.warn('[realtimeEngine] 未找到collection bins，无法生成视频文件名');
            return;
        }

        console.log('[realtimeEngine] Collection bins:', this.collectionBins);

        // 构建输出路径
        const results = {};

        // 启动左手摄像头录制
        if (binFileNameLeft) {
            const outputPath = `storage/${this.currentTaskId}/${binFileNameLeft}`;
            const metadata = {
                binFileName: binFileNameLeft,
                stageName: stageName,
                timestamp: timestamp,
                taskId: this.currentTaskId
            };

            console.log('[realtimeEngine] 启动左手摄像头录制:', outputPath);
            const result = await this.cameraManager.startRecording('left', outputPath, metadata);
            results.left = result;

            if (result.success) {
                console.log('[realtimeEngine] ✅ 左手摄像头录制已启动:', result.fileName);
            } else {
                console.error('[realtimeEngine] ❌ 左手摄像头录制失败:', result.error);
            }
        }

        // 启动右手摄像头录制
        if (binFileNameRight) {
            const outputPath = `storage/${this.currentTaskId}/${binFileNameRight}`;
            const metadata = {
                binFileName: binFileNameRight,
                stageName: stageName,
                timestamp: timestamp,
                taskId: this.currentTaskId
            };

            console.log('[realtimeEngine] 启动右手摄像头录制:', outputPath);
            const result = await this.cameraManager.startRecording('right', outputPath, metadata);
            results.right = result;

            if (result.success) {
                console.log('[realtimeEngine] ✅ 右手摄像头录制已启动:', result.fileName);
            } else {
                console.error('[realtimeEngine] ❌ 右手摄像头录制失败:', result.error);
            }
        }

        // 保存视频信息到H5
        if (this.stageFileOpen && !this.isClosingStageFile) {
            const videoInfo = {
                video_left: results.left?.fileName || null,
                video_right: results.right?.fileName || null,
                video_start_timestamp: timestamp
            };

            await this.sendStorageCommand('video_recording_started', videoInfo).catch(err => {
                console.error('[realtimeEngine] 保存视频信息到H5失败:', err);
            });
        }
    }

    // 【新增】Mocap命令处理
    onMocapSetChannel(channel) {
        console.log(`[realtimeEngine] 设置Mocap通道: ${channel}`);
        this.mocap_activeChannel = channel;
        
        if (this.mocap_client && this.mocap_client.readyState === WebSocket.OPEN) {
            this.mocap_client.send(JSON.stringify({ cmd: 'set_channel', channel }));
        }
    }
    
    onMocapResetChannel(channel, value) {
        if (this.mocap_client && this.mocap_client.readyState === WebSocket.OPEN) {
            this.mocap_client.send(JSON.stringify({ cmd: 'reset_channel', channel, value }));
        }
    }
    
    onMocapGetStatus() {
        this.broadcastToClients({
            type: 'mocap_status',
            connected: this.mocap_connected,
            activeChannel: this.mocap_activeChannel,
            packetCount: this.mocap_packet_count
        });
    }

    // 【新增】动捕SDK连接控制
    onMocapSdkConnect() {
        console.log('[realtimeEngine] 请求连接动捕SDK');
        if (this.mocap_client && this.mocap_client.readyState === WebSocket.OPEN) {
            this.mocap_client.send(JSON.stringify({ cmd: 'sdk_connect' }));
        } else {
            this.broadcastToClients({
                type: 'mocap_sdk_status',
                connected: false,
                error: 'mocap_server未连接'
            });
        }
    }

    onMocapSdkDisconnect() {
        console.log('[realtimeEngine] 请求断开动捕SDK');
        if (this.mocap_client && this.mocap_client.readyState === WebSocket.OPEN) {
            this.mocap_client.send(JSON.stringify({ cmd: 'sdk_disconnect' }));
        }
    }

    onMocapSdkGetStatus() {
        if (this.mocap_client && this.mocap_client.readyState === WebSocket.OPEN) {
            this.mocap_client.send(JSON.stringify({ cmd: 'sdk_get_status' }));
        } else {
            this.broadcastToClients({
                type: 'mocap_sdk_status',
                connected: false,
                sdk_connected: false
            });
        }
    }

    async openStageFile(stageName, stageIndex) {
        console.log(`[realtimeEngine] 尝试打开Stage文件: ${stageName}`);
        console.log(`[realtimeEngine] storage_connected = ${this.storage_connected}`);

        // 【新增】测试模式下跳过创建H5文件
        if (this.isTestMode) {
            console.log(`[realtimeEngine] ★ 测试模式：跳过创建H5文件 ★`);
            this.stageFileOpen = false;  // 确保不会尝试写入
            return;
        }

        if (!this.storage_connected) {
            console.warn('[realtimeEngine] ⚠️ Storage未连接，无法打开文件');
            return;
        }

        // 【修复】等待sd_filenames_updated事件到达（最多等待500ms）
        // 因为sd_filenames_updated事件是从ble_server.py的start_all发送的，
        // 可能在stage_start命令之后才到达
        if (!this.sd_filenames.dev1 && !this.sd_filenames.dev2) {
            console.log('[realtimeEngine] 等待SD卡文件名...');
            await new Promise(resolve => setTimeout(resolve, 300));
            console.log(`[realtimeEngine] SD卡文件名: dev1=${this.sd_filenames.dev1 || '无'}, dev2=${this.sd_filenames.dev2 || '无'}`);
        }

        try {
            const config = this.collectionConfig || {};
            const taskName = collection_task_name[this.currentTaskId] || this.currentTaskId;
            const userId = this.currentUser?.id || 'unknown';
            const sessionNum = this.currentSessionNumber || 1;

            const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
            const filename = `${userId}_${taskName}_session${sessionNum}_${stageName}_${timestamp}.h5`;

            const category1 = config.category1 || 'unknown';
            const category2 = config.category2 || 'unknown';
            const category4 = config.category4 || '';

            let subdirectory = category4
                ? `${category1}/${category2}/${userId}/${category4}`
                : `${category1}/${category2}/${userId}`;

            console.log(`[realtimeEngine] 准备打开文件: ${filename}`);
            console.log(`[realtimeEngine] 子目录: ${subdirectory}`);

            // 使用中文任务名称作为 task_id，这样文件夹名称就是中文的
            const taskIdForFolder = config.task || this.currentTaskId;

            const response = await this.sendStorageCommand('create', {
                filename,
                subdirectory,
                task_id: taskIdForFolder,  // 使用中文任务名称
                user_id: userId,
                stage_name: stageName,
                stage_index: stageIndex,
                session_index: this.currentSessionIndex,
                session_number: sessionNum,
                session_count: this.sessionCount,
                category1: category1,
                category2: category2,
                category4: category4,
                template_name: config.templateName || 'default',
                subject_info: this.currentUser,
                start_time: this.stage_start_time,
                // 【新增】传递 collection stream 的 SD 卡 bin 文件名（用于 HDF5 溯源）
                // 使用 collectionBinFilenames（优先）或 sd_filenames
                sd_bin_dev1: this.collectionBinFilenames.dev1 || this.sd_filenames.dev1,
                sd_bin_dev2: this.collectionBinFilenames.dev2 || this.sd_filenames.dev2,
                // 【新增】IMU bin 文件（兼容未来扩展）
                sd_imu_bin_dev1: null,  // 当前 IMU bin 与 EMG bin 同名
                sd_imu_bin_dev2: null,
                // 【新增】传递BLE设备名称，用于追溯数据来源
                ble_dev1: this.device_names.dev1,  // 例如 "WristBand_3A76"
                ble_dev2: this.device_names.dev2,  // 例如 "WristBand_5B12"
                // 【新增】stream mode 元数据（preview/collection 切流方案）
                stream_mode: this.streamMode,  // "collection" | "preview" | "idle"
                collection_stream_id: this.collectionStreamId,
                stream_switch_delay_ms: this.streamSwitchDelayMs,
                timestamp_to_start_delay_ms: this.timestampToStartDelayMs,
                bin_pair_source: (this.streamMode === 'collection') ? 'collection_stream' : 'unknown',
                // 【新增】录像同步信息
                recording_session_id: this.recordingSessionId,  // 例如 "rec_20260314_153045_5"
                is_multi_session: this.isMultiSession,          // 是否为多轮次采集
                // 【Phase 2】续采模式元数据
                is_resumed: this.isResume || false,
                segment_index: this.resumeSegmentIndex || 1,
                resume_from_interrupted_at: this.resumeFromInterruptedAt || null,
                resume_reason: this.resumeReason || null,
                resume_parent_recording_session_id: this.resumeParentRecordingSessionId || null,
                // Phase 3: 父 segment 序号
                parent_segment_index: this.resumeParentSegmentIndex || null
            });

            if (response.status === 'success') {
                this.stageFileOpen = true;
                console.log(`[realtimeEngine] ✅ 文件已打开: ${filename}`);
            } else {
                console.error(`[realtimeEngine] ❌ 打开文件失败:`, response);
            }
        } catch (error) {
            console.error('[realtimeEngine] 打开Stage文件失败:', error);
        }
    }

    async closeStageFile(extraParams = {}) {
        if (!this.stageFileOpen || this.isClosingStageFile) return;

        this.isClosingStageFile = true;

        try {
            const params = { end_time: Date.now() / 1000, ...extraParams };
            const response = await this.sendStorageCommand('close', params);
            this.stageFileOpen = false;
            if (response.status === 'success') {
                const status = params.collection_status || 'completed';
                console.log(`[realtimeEngine] ✅ 文件已关闭 (collection_status: ${status})`);
            }
            return response;
        } catch (error) {
            console.error('[realtimeEngine] 关闭Stage文件失败:', error);
            return { status: 'error', msg: error.message };
        } finally {
            this.isClosingStageFile = false;
        }
    }

    broadcastToClients(dataPacket) {
        const message = JSON.stringify(dataPacket);
        this.clients.forEach((client) => {
            if (client.readyState === WebSocket.OPEN) {
                try { client.send(message); } 
                catch (error) { this.clients.delete(client); }
            }
        });
    }

    ble_server_connect() {
        // 【修复】清理旧连接时先清除事件处理器，避免触发重连
        if (this.ble_client) {
            this.ble_client.onopen = null;
            this.ble_client.onclose = null;
            this.ble_client.onerror = null;
            this.ble_client.onmessage = null;
            try { this.ble_client.close(); } catch (e) {}
            this.ble_client = null;
        }

        try {
            console.log(`[realtimeEngine] 正在连接BLE数据端: ${this.ble_clientUrl} (尝试 ${this.currentReconnectTimes + 1}/${this.maxReconnectTimes})`);
            this.ble_client = new WebSocket(this.ble_clientUrl);

            this.ble_client.onopen = () => {
                console.log(`[realtimeEngine] ✅ BLE数据端连接成功 (${this.ble_clientUrl})`);
                this.currentReconnectTimes = 0;
                clearTimeout(this.reconnectTimer);
                this.broadcastToClients({ type: 'ble_connection_status', connected: true, message: 'BLE服务器已连接' });
            };

            this.ble_client.onmessage = (event) => {
                try {
                    const packet = JSON.parse(event.data);

                    // 调试：打印收到的数据类型
                    if (packet.type === 'data') {
                        this.handleBleDataPacket(packet);
                        return;
                    }
                    if (packet.type === 'emg_packet') { this.attributeEMGData(packet); return; }
                    // 【新增】监听sd_filenames_updated事件（包含设备名称、stream_mode、collection_stream_id）
                    if (packet.type === 'event' && packet.event === 'sd_filenames_updated') {
                        this.onSdFilenamesUpdated(packet.sd_filenames, packet.device_names, packet.stream_mode, packet.collection_stream_id);
                        return;
                    }
                    // 【新增】监听collection_stopped事件
                    if (packet.type === 'event' && packet.event === 'collection_stopped') {
                        console.log(`[realtimeEngine] collection stream 已停止: ${JSON.stringify(packet.sd_filenames)}`);
                        this.streamMode = 'idle';
                        return;
                    }

                    // 打印欢迎消息
                    if (packet.type === 'welcome') {
                        console.log(`[realtimeEngine] 收到数据端欢迎消息:`, packet.message);
                    }
                } catch (error) {}
            };

            this.ble_client.onerror = (error) => {
                // 【修复】onerror后通常会触发onclose，这里不重复处理
                console.log(`[realtimeEngine] BLE数据端连接错误`);
            };
            this.ble_client.onclose = (event) => {
                console.log(`[realtimeEngine] BLE数据端连接关闭, code: ${event.code}`);
                this.ble_client = null;  // 【修复】清理引用
                this.broadcastToClients({ type: 'ble_connection_status', connected: false, message: 'BLE服务器连接已断开' });
                if (event.code !== 1000) this.handleReconnect();
            };

        } catch (error) { this.handleReconnect('创建连接失败'); }
    }

    handleReconnect(reason = '连接断开') {
        if (this.currentReconnectTimes >= this.maxReconnectTimes) return;
        this.currentReconnectTimes++;
        this.reconnectTimer = setTimeout(() => { this.ble_server_connect(); }, this.reconnectInterval);
    }

    // 【新增】Mocap Server连接
    mocap_server_connect() {
        // 【修复】清理旧连接时先清除事件处理器，避免触发重连
        if (this.mocap_client) {
            this.mocap_client.onopen = null;
            this.mocap_client.onclose = null;
            this.mocap_client.onerror = null;
            this.mocap_client.onmessage = null;
            try { this.mocap_client.close(); } catch (e) {}
            this.mocap_client = null;
        }

        try {
            console.log(`[realtimeEngine] 正在连接Mocap服务器: ${this.mocap_clientUrl} (尝试 ${this.mocap_currentReconnectTimes + 1}/${this.mocap_maxReconnectTimes})`);
            this.mocap_client = new WebSocket(this.mocap_clientUrl);

            this.mocap_client.onopen = () => {
                console.log(`[realtimeEngine] ✅ Mocap服务器连接成功`);
                this.mocap_currentReconnectTimes = 0;
                this.mocap_connected = true;
                clearTimeout(this.mocap_reconnectTimer);

                this.broadcastToClients({ type: 'mocap_connection_status', connected: true, message: 'Mocap服务器已连接' });

                if (this.mocap_activeChannel) {
                    this.mocap_client.send(JSON.stringify({ cmd: 'set_channel', channel: this.mocap_activeChannel }));
                }
            };

            this.mocap_client.onmessage = (event) => {
                try {
                    const packet = JSON.parse(event.data);
                    if (packet.type === 'mocap') { this.handleMocapDataPacket(packet); }
                    // 【新增】转发SDK状态响应给前端
                    else if (packet.type === 'response' && packet.cmd && packet.cmd.startsWith('sdk_')) {
                        this.broadcastToClients({
                            type: 'mocap_sdk_status',
                            cmd: packet.cmd,
                            status: packet.status,
                            sdk_connected: packet.sdk_connected,
                            message: packet.message
                        });
                    }
                } catch (error) {}
            };

            this.mocap_client.onerror = (error) => {
                // 【修复】onerror后通常会触发onclose，这里不重复处理
                console.log(`[realtimeEngine] Mocap服务器连接错误`);
            };
            this.mocap_client.onclose = (event) => {
                console.log(`[realtimeEngine] Mocap服务器连接关闭, code: ${event.code}`);
                this.mocap_connected = false;
                this.mocap_client = null;  // 【修复】清理引用
                this.broadcastToClients({ type: 'mocap_connection_status', connected: false, message: 'Mocap服务器连接已断开' });
                if (event.code !== 1000) this.handleMocapReconnect();
            };

        } catch (error) { this.handleMocapReconnect('创建连接失败'); }
    }

    handleMocapReconnect(reason = '连接断开') {
        if (this.mocap_currentReconnectTimes >= this.mocap_maxReconnectTimes) return;
        this.mocap_currentReconnectTimes++;
        this.mocap_reconnectTimer = setTimeout(() => { this.mocap_server_connect(); }, this.mocap_reconnectInterval);
    }
    
    // 【新增】处理Mocap数据包
    handleMocapDataPacket(packet) {
        if (!this.isRunning) return;

        try {
            this.mocap_packet_count++;
            // 【始终】广播给前端（用于实时显示）
            this.broadcastToClients({ type: 'mocap_data', data: packet });

            // 【修改】采集时批量保存 mocap 原始数据到 storage
            // 【新增】只有当前stage需要动捕数据时才保存
            if (this.isCollecting && !this.collectionPaused && this.stageFileOpen && !this.isClosingStageFile && this.currentStageNeedMocap) {
                // 获取批量帧数据
                const frames = packet.frames;  // [{markers, frame, time, sys_time}, ...]

                if (frames && frames.length > 0) {
                    // 【修改】优先使用 mocap_server 传过来的 sys_time（更精确）
                    // 如果没有 sys_time（兼容旧版），则使用本地时间估算
                    const fallbackSysTime = getSysTimeNode();
                    const framesWithSysTime = frames.map((f, idx) => ({
                        ...f,
                        // 优先使用 mocap_server 的 sys_time，否则用本地估算
                        sys_time: f.sys_time || (fallbackSysTime + idx * 0.005)
                    }));

                    // 批量发送所有帧到 storage
                    this.saveDataToStorage({
                        mocap_frames: framesWithSysTime,  // 批量帧数据（带系统时间戳）
                        mocap_batch_size: framesWithSysTime.length
                    });
                }
            }
        } catch (error) {
            console.error('[realtimeEngine] 处理Mocap数据包错误:', error);
        }
    }

    // V1/V2 统一的 IMU 数据规范化
    // V1: dev.imu = [[acc,gyr,mag], [acc,gyr,mag]]  (2 chips, ICM-20948)
    // V2: dev.imu = [[acc,gyr], [acc,gyr], [acc,gyr]]  (0-3 chips, LSM6DSV32X, no mag)
    normalizeImuData(dev) {
        if (!dev || !dev.imu) return { hwVersion: dev?.hw_version || "V1", numImus: 0, imus: [] };

        const hwVersion = dev.hw_version || "V1";
        const numImus = dev.num_imus || dev.imu.length || 0;
        const imus = [];

        for (let i = 0; i < dev.imu.length; i++) {
            const chip = dev.imu[i];
            imus.push({
                index: i,
                acc: chip[0] || [0, 0, 0],
                gyr: chip[1] || [0, 0, 0],
                mag: hwVersion === "V1" ? (chip[2] || [0, 0, 0]) : null,  // V2 no mag
            });
        }

        return { hwVersion, numImus, imus };
    }

    // Extract a single IMU chip for legacy {acc, gyr, mag} format
    imuChipToLegacy(chip, hwVersion) {
        return {
            acc: chip[0] || [0, 0, 0],
            gyr: chip[1] || [0, 0, 0],
            mag: hwVersion === "V1" ? (chip[2] || [0, 0, 0]) : null,
        };
    }

    handleBleDataPacket(packet) {
        if (!this.isRunning) return;

        try {
            if (!packet.dev1 && !packet.dev2) return;

            // Normalize to arrays, matching ble_server.py data_sender_thread batch behavior
            const dev1List = Array.isArray(packet.dev1) ? packet.dev1 : (packet.dev1 ? [packet.dev1] : []);
            const dev2List = Array.isArray(packet.dev2) ? packet.dev2 : (packet.dev2 ? [packet.dev2] : []);
            const maxLen = Math.max(dev1List.length, dev2List.length);

            for (let i = 0; i < maxLen; i++) {
                const dev1 = dev1List[i] || null;
                const dev2 = dev2List[i] || null;

                if (!dev1 && !dev2) continue;

                let emg1Data = null, emg2Data = null;
                let emg1RawData = null, emg2RawData = null;
                let emg1Timestamps = null, emg2Timestamps = null;
                let emg1FrameIds = null, emg2FrameIds = null;
                let imu1Norm = null, imu2Norm = null;
                let imu1Timestamps = null, imu2Timestamps = null;
                let imu1aData = null, imu1bData = null;
                let imu2aData = null, imu2bData = null;
                let imu1All = null, imu2All = null;
                let imu1HwVersion = null, imu2HwVersion = null;
                let imu1NumImus = null, imu2NumImus = null;
                // Prefer sub-packet's own timestamp (set by ble_server create_notification_handler)
                let timestamp = (dev1 && dev1.t) || (dev2 && dev2.t) || packet.ts;
                let stats1 = null, stats2 = null;
                let framesInPacket = 9;

                // ===== Process dev1 sub-packet =====
                if (dev1) {
                    if (dev1.uv?.length > 0) emg1Data = this.transposeEMG(dev1.uv);
                    if (dev1.raw?.length > 0) emg1RawData = this.transposeEMG(dev1.raw);
                    if (dev1.emg_t?.length > 0) emg1Timestamps = dev1.emg_t;
                    if (dev1.frame_ids?.length > 0) emg1FrameIds = dev1.frame_ids;
                    if (dev1.imu_t?.length > 0) imu1Timestamps = dev1.imu_t;
                    stats1 = dev1.s ? { total: dev1.s[0], lost: dev1.s[1] } : null;
                    framesInPacket = dev1.n || 9;
                    this.dev1_packet_count += framesInPacket;

                    imu1Norm = this.normalizeImuData(dev1);
                    imu1HwVersion = imu1Norm.hwVersion;
                    imu1NumImus = imu1Norm.numImus;
                    imu1All = imu1Norm.imus;

                    if (imu1HwVersion === "V1") {
                        if (dev1.imu?.[0]) imu1aData = this.imuChipToLegacy(dev1.imu[0], imu1HwVersion);
                        if (dev1.imu?.[1]) imu1bData = this.imuChipToLegacy(dev1.imu[1], imu1HwVersion);
                    }
                }

                // ===== Process dev2 sub-packet =====
                if (dev2) {
                    if (dev2.uv?.length > 0) emg2Data = this.transposeEMG(dev2.uv);
                    if (dev2.raw?.length > 0) emg2RawData = this.transposeEMG(dev2.raw);
                    if (dev2.emg_t?.length > 0) emg2Timestamps = dev2.emg_t;
                    if (dev2.frame_ids?.length > 0) emg2FrameIds = dev2.frame_ids;
                    if (dev2.imu_t?.length > 0) imu2Timestamps = dev2.imu_t;
                    stats2 = dev2.s ? { total: dev2.s[0], lost: dev2.s[1] } : null;
                    this.dev2_packet_count += (dev2.n || 9);
                    // Use max when both devices have data in the same pair
                    if (dev2.n && dev2.n > framesInPacket) framesInPacket = dev2.n;

                    imu2Norm = this.normalizeImuData(dev2);
                    imu2HwVersion = imu2Norm.hwVersion;
                    imu2NumImus = imu2Norm.numImus;
                    imu2All = imu2Norm.imus;

                    if (imu2HwVersion === "V1") {
                        if (dev2.imu?.[0]) imu2aData = this.imuChipToLegacy(dev2.imu[0], imu2HwVersion);
                        if (dev2.imu?.[1]) imu2bData = this.imuChipToLegacy(dev2.imu[1], imu2HwVersion);
                    }
                }

                this.emg_packet_count += framesInPacket;

                const dataItem = {
                    emg1: emg1Data, emg2: emg2Data,
                    imu1: imu1Norm?.imus || null, imu2: imu2Norm?.imus || null,
                    timestamp, packetCount: this.emg_packet_count, framesInPacket,
                    stats1, stats2, activeDevices: packet.active || []
                };
                this.realtimeDataBuffer.push(dataItem);

                if (!this.realtimeDataTimer) {
                    this.realtimeDataTimer = setTimeout(() => {
                        this.flushRealtimeDataBuffer();
                    }, this.realtimeDataMaxDelay);
                }

                if (this.realtimeDataBuffer.length >= this.realtimeDataBufferLimit) {
                    this.flushRealtimeDataBuffer();
                }

                const storagePacketTs = Math.max(
                    Number(dev1?.t) || 0,
                    Number(dev2?.t) || 0,
                    Number(timestamp) || 0
                );
                const isFreshCollectionPacket =
                    !this.collectionDataStartTs ||
                    !storagePacketTs ||
                    storagePacketTs >= (this.collectionDataStartTs - 0.05);

                // Send raw data to storage_server for this sub-packet pair
                if (this.isCollecting && !this.collectionPaused && this.stageFileOpen && !this.isClosingStageFile && isFreshCollectionPacket) {
                    this.saveDataToStorage({
                        emg1: emg1RawData, emg2: emg2RawData, emg1_t: emg1Timestamps, emg2_t: emg2Timestamps,
                        emg1_frame_ids: emg1FrameIds, emg2_frame_ids: emg2FrameIds,
                        imu1a: imu1aData, imu1b: imu1bData, imu1_t: imu1Timestamps,
                        imu2a: imu2aData, imu2b: imu2bData, imu2_t: imu2Timestamps,
                        imu1_all: imu1All, imu2_all: imu2All,
                        imu1_hw_version: imu1HwVersion, imu2_hw_version: imu2HwVersion,
                        imu1_num_imus: imu1NumImus, imu2_num_imus: imu2NumImus,
                    });
                } else if (this.isCollecting && this.stageFileOpen && !isFreshCollectionPacket) {
                    this.collectionDroppedStaleBlePackets++;
                    if (this.collectionDroppedStaleBlePackets <= 3) {
                        console.log(`[realtimeEngine] drop stale BLE packet before collection_start: packet_ts=${storagePacketTs}, start_ts=${this.collectionDataStartTs}`);
                    }
                }
            }

        } catch (error) {
            console.error('[realtimeEngine] 处理BLE数据包错误:', error);
        }
    }

    // 【新增】批量发送缓冲区数据给前端
    flushRealtimeDataBuffer() {
        if (this.realtimeDataTimer) {
            clearTimeout(this.realtimeDataTimer);
            this.realtimeDataTimer = null;
        }

        if (this.realtimeDataBuffer.length === 0) return;

        // 批量发送所有缓冲的数据
        this.broadcastToClients({
            type: 'realtime_data_batch',
            batch: this.realtimeDataBuffer
        });

        this.realtimeDataBuffer = [];
    }

    transposeEMG(uvData) {
        if (!uvData || uvData.length === 0) return null;
        const numFrames = uvData.length;
        const numChannels = uvData[0].length;
        const transposed = [];
        for (let ch = 0; ch < numChannels; ch++) {
            const channelData = [];
            for (let frame = 0; frame < numFrames; frame++) {
                channelData.push(uvData[frame][ch]);
            }
            transposed.push(channelData);
        }
        return transposed;
    }

    async storage_server_connect() {
        try {
            // 连接 REP socket（用于控制命令）
            const address = `tcp://${this.storage_server_host}:${this.storage_server_port}`;
            await this.storage_server_socket.connect(address);
            this.storage_connected = true;
            console.log(`[realtimeEngine] 已连接到storage_server控制端: ${address}`);

            // 【新增】连接 PUSH socket（用于数据发送）
            const dataAddress = `tcp://${this.storage_server_host}:${this.storage_data_port}`;
            await this.storage_push_socket.connect(dataAddress);
            this.storage_push_connected = true;
            console.log(`[realtimeEngine] 已连接到storage_server数据端: ${dataAddress} (PUSH模式)`);
        } catch (err) {
            this.storage_connected = false;
            this.storage_push_connected = false;
            console.error('[realtimeEngine] 连接storage_server失败:', err);
        }
    }

    async sendStorageCommand(cmd, params = {}) {
        return new Promise((resolve, reject) => {
            this.storageRequestQueue.push({ cmd, params, resolve, reject });
            if (!this.isStorageRequestPending) this._processStorageQueue();
        });
    }
    
    async _processStorageQueue() {
        if (this.isStorageRequestPending || this.storageRequestQueue.length === 0) return;
        
        this.isStorageRequestPending = true;
        
        while (this.storageRequestQueue.length > 0) {
            const { cmd, params, resolve, reject } = this.storageRequestQueue.shift();
            try {
                const request = JSON.stringify({ cmd, params });
                await this.storage_server_socket.send(request);
                const [responseBuffer] = await this.storage_server_socket.receive();
                const response = JSON.parse(responseBuffer.toString('utf8'));
                resolve(response);
            } catch (err) {
                reject(new Error(`Storage命令失败(${cmd}): ${err.message}`));
            }
        }
        
        this.isStorageRequestPending = false;
    }

    async saveDataToStorage(sensorData) {
        // 【新增】preview stream 数据不写入 H5（仅 collection stream 写入）
        if (this.streamMode !== 'collection') {
            return;
        }
        if (this.isClosingStageFile || !this.stageFileOpen) return;

        try {
            // 【优化】使用 PUSH socket 发送数据，非阻塞，不等待响应
            if (this.storage_push_connected) {
                const request = JSON.stringify({ cmd: 'append', params: { data: sensorData } });
                await this.storage_push_socket.send(request);
            } else {
                // 回退到 REP socket（如果 PUSH 未连接）
                await this.sendStorageCommand('append', { data: sensorData });
            }
        } catch (error) {
            // 静默失败，不阻塞主流程
        }
    }

    async attributeEMGData(emgData) {
        if (!this.isRunning) return;
        try {
            if (!Array.isArray(emgData.big_bag_raw_data) || emgData.big_bag_raw_data.length !== 5) return;

            this.emg_packet_count += 5;
            this.emg_5_packets_count++;

            this.broadcastToClients({
                type: 'realtime_data',
                data: { emg: emgData.big_bag_raw_data, imu: null, timestamp: Date.now(), packetCount: this.emg_packet_count, framesInPacket: 5 }
            });
        } catch (error) {}
    }

    getStatus() {
        const WebSocket = require('ws');
        const bleConnected = this.ble_client && this.ble_client.readyState === WebSocket.OPEN;

        // 【新增】获取已连接客户端列表
        const connectedClients = [];
        this.clients.forEach(client => {
            connectedClients.push({
                id: client.clientId,
                name: client.clientName,
                connectedAt: client.connectedAt
            });
        });

        return {
            isRunning: this.isRunning, isCollecting: this.isCollecting, collectionPaused: this.collectionPaused,
            currentTaskId: this.currentTaskId, currentStageName: this.currentStageName, stageFileOpen: this.stageFileOpen,
            clientCount: this.clients.size, packetCount: this.emg_packet_count, mocapPacketCount: this.mocap_packet_count,
            storageConnected: this.storage_connected, mocapConnected: this.mocap_connected,
            bleConnected: bleConnected,
            pendingStorageRequests: this.storageRequestQueue.length,
            connectedClients: connectedClients  // 【新增】客户端列表
        };
    }

    stop() {
        return new Promise(async (resolve) => {
            this.isRunning = false;
            
            if (this.stageFileOpen && !this.isClosingStageFile) await this.closeStageFile();
            this.isCollecting = false;

            this.clients.forEach(client => {
                if (client.readyState === WebSocket.OPEN) client.close(1001, '服务器关闭');
            });
            this.clients.clear();

            if (this.websocket_server) {
                const closeTimeout = setTimeout(() => resolve(), 3000);
                this.websocket_server.close(() => { clearTimeout(closeTimeout); resolve(); });
            } else {
                resolve();
            }

            if (this.ble_client) { this.ble_client.close(1000); this.ble_client = null; }
            if (this.mocap_client) { this.mocap_client.close(1000); this.mocap_client = null; }

            // 【新增】关闭 ZMQ sockets
            try {
                if (this.storage_push_socket) { this.storage_push_socket.close(); }
                if (this.storage_server_socket) { this.storage_server_socket.close(); }
            } catch (e) {}

            clearTimeout(this.reconnectTimer);
            clearTimeout(this.mocap_reconnectTimer);
        });
    }
}

const realtimeEngine = new RealtimeEngine();
module.exports = realtimeEngine;
