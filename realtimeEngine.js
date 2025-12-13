// realtimeEngine.js
const WebSocket = require('ws');
const EventEmitter = require('events');
// 新版 zeromq (v6+) 适配代码
const zmq = require('zeromq');
const { promisify } = require('util');
const express = require('express');
const cors = require('cors');
const app = express();
app.use(cors());
app.use(express.json());


const { discrete_gesture_prompt_name, collection_task_name } = require('./constants.js');

// 简化版（仅Node.js环境，更简洁）
function getSysTimeNode() {
    // Node.js v10.7+ 直接提供纳秒级UNIX时间戳
    const nsTimestamp = process.hrtime.bigint();
    const sTimestamp = Number(nsTimestamp) / 1000000000.0;
    return Math.round(sTimestamp * 1000000000) / 1000000000;
}

class RealtimeEngine extends EventEmitter {
    constructor() {
        super();
        // websocket server，用于index.html client的实时显示
        this.websocket_server = null;
        this.clients = new Set();
        this.isRunning = false;
        this.dataBuffer = [];
        this.maxBufferSize = 1000;

        // ===== 新增：ble_server 客户端配置 =====
        this.ble_client = null; // 连接 Python 的 WebSocket 客户端实例
        this.ble_clientUrl = 'ws://localhost:8766'; // Python 服务端的 WebSocket 地址（替换为你的实际地址）
        this.reconnectInterval = 3000; // 重连间隔（3秒）
        this.maxReconnectTimes = 3;
        this.currentReconnectTimes = 0; // 当前重连次数
        this.reconnectTimer = null; // 重连计时器

        this.connectTimeoutTimer = null;
        this.emg_packet_count=0;
        this.emg_5_packets_count=0;
        this.emg_interval = 0.001; //1ms


        // ===== 新增：storage_server zetomq连接配置 =====
        this.storage_server_socket = new zmq.Request();
        this.storage_server_host = '127.0.0.1';
        this.storage_server_port = 5555;
        this.file_id = 1;
        this.write_enable = 0;    //1 用于表示当前正在打开文件中

        // ===== 新增：taskManager 发来的指令（stage start 信号， stage end信号，prompt信号）
        this.storage_start_flag = 0;
        this.storage_end_flag = 0;
        this.prompt_flag = 0;
        this.buttomname = 0;

        this.prompt_name = null;
        this.prompt_time = 0;

        this.stage_name = null;
        this.stage_start = 0;
        this.stage_end = 0;
    }

    // 0.0 启动 realtimeEngine 实时引擎模块
    start(port = 8080) {
        return new Promise((resolve, reject) => {
            try {
                /**
                 * ===== 1. 连接ble_server，接受数据  =====
                 */
                this.connectTimeoutTimer = setTimeout(() => {
                    this.ble_server_connect();
                }, 1000); // 延迟 1000 毫秒（1秒）


                /**
                 * ===== 2. 启动realtimeEngine >>> index.html websocket广播服务器， 实时显示 =====
                 */ 
                this.websocket_server = new WebSocket.Server({ port });

                //收到index.html client连接请求
                this.websocket_server.on('connection', (ws) => {
                    console.log('[realtimeEngine] 前端client连接已建立');
                    this.clients.add(ws);
                    
                    // ACK to client : connect_established
                    const connectMsg = JSON.stringify({
                        type: 'connection_established',
                        message: '实时数据连接已建立',
                        timestamp: Date.now()
                    });

                    //console.log(`[realtimeEngine] [${new Date().toISOString()}] 发送连接确认给前端:`, JSON.parse(connectMsg));
                    ws.send(connectMsg);


                    ws.on('close', () => {
                        console.log('[realtimeEngine] 前端WebSocket连接已关闭');
                        this.clients.delete(ws);
                    });

                    ws.on('error', (error) => {
                        console.error('[realtimeEngine] WebSocket错误:', error);
                        this.clients.delete(ws);
                    });
                });

                this.websocket_server.on('listening', () => {
                    console.log(`实时引擎启动成功，WebSocket服务运行在端口 ${port}`);
                    this.isRunning = true;
                    
                    resolve();
                });

                this.websocket_server.on('error', (error) => {
                    console.error('启动WebSocket服务器失败:', error);
                    reject(error);
                });


                /**
                 * ===== 3. 连接storage_server ， 数据存储 =====
                 */
                this.storage_server_connect();


            } catch (error) {
                console.error('[realtimeEngine] 启动实时引擎失败:', error);
                reject(error);
            }
        });
    }


    // 0.1 websocket广播
    broadcastToClients(dataPacket) {
        const message = JSON.stringify(dataPacket);
        
        this.clients.forEach((client) => {
            if (client.readyState === WebSocket.OPEN) {
                try {
                    client.send(message);
                } catch (error) {
                    console.error('[realtimeEngine] 发送数据到客户端失败:', error);
                    this.clients.delete(client);
                }
            }
        });
    }

    // 0.2 获取realtimeEngine引擎状态
    getStatus() {
        return {
            isRunning: this.isRunning,
            clientCount: this.clients.size,
            bufferSize: this.dataBuffer.length,
            maxBufferSize: this.maxBufferSize,
            port: this.websocket_server ? this.websocket_server.address().port : null
        };
    }

    // 0.3 停止实时引擎
    stop() {
        return new Promise((resolve) => {
            this.isRunning = false;
            //this.stopDataBroadcast();

            // 强制关闭所有客户端（设置code=1001表示正常退出，避免等待）
            this.clients.forEach(client => {
                if (client.readyState === WebSocket.OPEN) {
                    client.close(1001, '服务器关闭'); // 带状态码的强制关闭
                }
            });
            this.clients.clear(); // 立即清空集合，避免残留

            // 关闭服务器时设置超时，避免无限等待
            if (this.websocket_server) {
                const closeTimeout = setTimeout(() => {
                    console.warn('服务器关闭超时，强制退出');
                    resolve();
                }, 3000); // 3秒超时

                this.websocket_server.close(() => {
                    clearTimeout(closeTimeout); // 成功关闭则清除超时
                    console.log('【实时引擎已停止】');
                    resolve();
                });
            } else {
                resolve();
            }

            // 关闭目标 Client 连接（主动关闭，不触发重连）
            if (this.targetClient) {
                this.targetClient.close(1000, '代理关闭');
                this.targetClient = null;
            }
            // 清除重连计时器
            clearTimeout(this.reconnectTimer);
        });
    }




    /**
     * 1. ble_server 数据传输部分
     * 
     */

    // 1.1 连接ble_server 服务器
    ble_server_connect() {
        //关闭当前已有的连接
        if (this.ble_client) {
                this.ble_client.close();
                this.ble_client = null;
            }
        try {
            // 创建新连接
            this.ble_client = new WebSocket(this.ble_clientUrl);
            console.log("[realtimeEngine] create ble_client successful");

            // 连接成功回调
            this.ble_client.onopen = () => {
                console.log(`[realtimeEngine] ble_server连接成功`);
                this.currentReconnectTimes = 0; // 重置重连次数
                clearTimeout(this.reconnectTimer);
                clearTimeout(this.connectTimeoutTimer);
            };

            // 消息接收处理
            this.ble_client.onmessage = (event) => {
                try {
                    const packet = JSON.parse(event.data);
                    
                    // 处理连接确认消息（与后端realtimeEngine的connection_established对应）
                    if (packet.type === 'emg') {
                        //console.log(`[realtimeEngine] 来自ble_server的emg 大包，大包timestamp : ${packet.timestamp}`);
                        this.attributeEMGData(packet);
                        return;
                    }
                } catch (error) {
                    console.error('[realtimeEngine] ble_server的emg消息解析失败:', error);
                }
            };

            // 错误处理
            this.ble_client.onerror = (error) => {
                console.error('[realtimeEngine] 错误:', error);
                this.handleReconnect(); // 触发重连
            };

            // 关闭处理
            this.ble_client.onclose = (event) => {
                //clearTimeout(connectionTimeout);
                console.log(`[realtimeEngine] ble_server 连接关闭：code=${code}, reason=${reason.toString()}`);
                // 非主动关闭（code !== 1000）且未达到最大重连次数时重连
                if (code !== 1000) {
                    this.handleReconnect();
                }
            };

        } catch (error) {
            console.error('[realtimeEngine] realtimeEngine创建失败:', error);
            this.handleReconnect('创建连接失败');
        }
    }

    // 1.2 接受来自ble_server 的大包数据（5*32）数据，并即时广播出去
    async attributeEMGData(emgData) {
        if (!this.isRunning) return;

        try {
            // 获取 raw_data，假设它是一个包含5个长度64字符串的数组
            let rawData = emgData.raw_data;

            // 确保 rawData 是数组类型，且包含 5 组数据
            if (!Array.isArray(rawData)) {
                console.error("[realtimeEngine] rawData 不是一个数组");
                return;
            }

            // 确保是 5 组数据
            if (rawData.length !== 5) {
                console.error('[realtimeEngine] emg 数据组数不匹配，应该是 5 组');
                return;
            }

            // 统计小包数量 + 5
            this.emg_packet_count += rawData.length;
            this.emg_5_packets_count++;

            // 根据大包的时间戳，计算每个小包的时间戳
            let timestamp_array = [emgData.timestamp,
                            emgData.timestamp + this.emg_interval * 1,
                            emgData.timestamp + this.emg_interval * 2,
                            emgData.timestamp + this.emg_interval * 3,
                            emgData.timestamp + this.emg_interval * 4];

            /**
             * 实时显示广播数据包
             */

            const dataPacket = {
                type: 'emg_data',
                data: {
                    big_bag_raw_data: rawData,  // [string64, string64, string64, string64, string64]//大包的rawData[5]数组放入 big_bag_raw_data
                    timestamp: timestamp_array, // [.9f, .9f, .9f, .9f, .9f] // 计算好大包内的5组的时间戳
                    packetCount: this.emg_packet_count,
                    interval: null // 现在不需要interval，可以以后加
                }
            };

            // 广播出去
            this.broadcastToClients(dataPacket);
            //console.log('realtimeEngine.js 发送一个大包，大包统计：小包统计：', this.emg_5_packets_count,this.emg_packet_count);

            /**
             * 存储 数据包发送逻辑
             */
            await this.storage_manager(rawData, timestamp_array);


        } catch (error) {
            console.error('[realtimeEngine] 处理EMG数据时发生错误:', error);
        }
    }

    

    // 1.2.1 判断存储逻辑
    async storage_manager(rawData_array, timestamp_array)
    {
        if(this.storage_start_flag == 1)
        {
            this.storage_start_flag = 0;
            await this.storage_server_create_new_hdf5_file();
            return;
        }

        if(this.storage_end_flag == 1)
        {
            this.storage_end_flag = 0;
            await this.storage_server_close_hdf5_file();
            return;
        }

        let prompt_name_temp = null;
        let prompt_time_temp = 0;
        if(this.prompt_flag == 1)
        {
            this.prompt_flag = 0;
            prompt_name_temp = this.prompt_name;
            prompt_time_temp = this.prompt_time;
            this.prompt_name = null;
            this.prompt_time = 0;
        }

        
        const dataPacket_storage = {
            data: {
                task: 'discrete_gesture',     //采集任务（discrete/continual1/con2）

                // data
                big_bag_raw_data: rawData_array,  // [string64, string64, string64, string64, string64]//大包的rawData[5]数组放入 big_bag_raw_data
                timestamp: timestamp_array, // [.9f, .9f, .9f, .9f, .9f] // 计算好大包内的5组的时间戳

                // prompt
                prompt_name: prompt_name_temp,
                prompt_time: prompt_time_temp,

                // stage
                stage_name: null,
                stage_start: 0,
                stage_end: 0
            }
        };

        await this.storage_server_append_hdf5_data(dataPacket_storage);
        return;
    }

    // 1.3 断线重连逻辑
    handleReconnect() {
        // 检查是否达到最大重连次数
        if (this.maxReconnectTimes > 0 && this.currentReconnectTimes >= this.maxReconnectTimes) {
        console.error(`[realtimeEngine] ❌ 已达到最大重连次数（${this.maxReconnectTimes}），停止重连`);
        return;
        }

        this.currentReconnectTimes++;
        console.log(`[realtimeEngine] 🔄 正在进行第 ${this.currentReconnectTimes} 次重连目标服务器...`);

        // 延迟重连（避免频繁连接）
        this.reconnectTimer = setTimeout(() => {
        this.connectTargetServer();
        }, this.reconnectInterval);
    }






    // 2.1 连接storage_server
    storage_server_connect() {
        try {
            this.storage_server_socket.connect(`tcp://${this.storage_server_host}:${this.storage_server_port}`);
            console.log(`已连接到 HDF5 存储服务：${this.storage_server_host}:${this.storage_server_port}`);
        } catch (err) {
            throw new Error(`连接失败：${err.message}`);
        }
    }

    /**
     * 发送指令到 Python 服务端（适配新版 API）
     * @param {string} cmd 指令类型：create/write/close
     * @param {object} params 指令参数
     * @returns {Promise<object>} 服务端响应
     */
    // 2.2 向storage_server 发送储存指令指令
    storage_server_sendCommand(cmd, params = {}) {
        try {
            // 构造请求数据（JSON 序列化）
            const request = JSON.stringify({ cmd, params });
            // 发送数据（新版 send 支持字符串，自动转 Buffer）
            this.storage_server_socket.send(request);

            // 接收响应（新版需用 iterator 接收，且响应是 Buffer 数组）
            const [responseBuffer] = this.storage_server_socket.receive();
            const response = JSON.parse(responseBuffer.toString('utf8'));
            return response;
        } catch (err) {
            throw new Error(`指令发送失败（${cmd}）：${err.message}`);
        }
    } 


    // 2.3 请求storage_server 创建新的文件
    // 注意：函数必须声明为 async，因为 sendCommand 是异步函数
    async storage_server_create_new_hdf5_file() {
        try {
            if(this.write_enable == 1)
            {
                throw new Error(`已经有正在写的文件`);
            }

            // 1. 生成系统时间戳（毫秒级，避免重复；也可改用秒级：Math.floor(Date.now()/1000)）
            const timestamp = Date.now(); 
            // 2. 拼接文件名：hdf5_ + file_id + 时间戳 + .h5
            const fileName = `./storage/hdf5_${this.file_id}_${timestamp}.h5`;

            // 3. 创建 HDF5 文件
            console.log('\n=== 第一步：创建 HDF5 文件 ===, id = ', this.file_id);
            // 关键：异步函数必须加 await，否则 createResponse 是 Promise 对象
            const createResponse = await this.sendCommand('create', {
                file_name: fileName, // 使用拼接后的文件名
                group_name: 'emg_data'
            });
            console.log("创建响应：", createResponse);

            if (createResponse.status !== 'success') {
                throw new Error(`创建文件失败：${createResponse.msg}`);
            }

            // 可选：返回创建的文件名，方便后续使用
            this.write_enable = 1;
            
            return fileName;
            
        } catch (error) {
            console.error(`创建HDF5文件失败（file_id: ${this.file_id}）：`, error.message);
            throw error; // 向上抛出错误，让调用方处理
        }
    }

    // 2.4 关闭保存
    async storage_server_close_hdf5_file() {
        try {
            if(this.write_enable == 0)
            {
                throw new Error(`当前没有文件在写，无需关闭`);
            }

            // 1. 打印日志（关联 fileId，方便定位）
            console.log(`\n=== 关闭 HDF5 文件 ===, file_id = ${this.file_id}`);

            // 2. 异步发送关闭指令（await 等待响应）
            const closeResponse = await this.sendCommand('close');

            // 3. 打印响应结果
            console.log(`文件 ${this.file_id} 关闭响应：`, closeResponse);

            // 4. 校验关闭结果，失败则主动抛出错误
            if (closeResponse.status !== 'success' && closeResponse.status !== 'warning') {
                throw new Error(`文件 ${this.file_id} 关闭失败：${closeResponse.msg}`);
            }

            // 5. 成功关闭，返回响应结果（供外层调用）
            this.write_enable = 0;
            this.file_id++;
            return closeResponse;

        } catch (error) {
            // 6. 捕获所有错误（网络超时/指令失败等），打印日志后重新抛出
            console.error(`关闭 HDF5 文件失败（file_id: ${this.file_id}）：`, error.message);
            throw error; // 向上抛出，让调用方感知错误（可选择是否抛出）
        }
    }


        /**
     * 异步写入单批次传感器数据到 HDF5 文件
     * @param {string} fileId - 文件ID（用于日志定位，关联对应的HDF5文件）
     * @param {Array} sensorData - 单批次传感器数据（如 [25.1, 25.2, 25.3]）
     * @param {Object} [options] - 可选配置项
     * @param {string} [options.datasetName='temp_sensor_1'] - 数据集名称
     * @param {string} [options.dtype='float64'] - 数据类型（float64/uint8/int32等）
     * @returns {Promise<object>} 写入响应结果（包含status/msg/total_count等）
     * @throws {Error} 参数错误/写入失败时抛出错误
     */
    //2.5 写一次数据
    async storage_server_append_hdf5_data(dataPacket, options = {}) {
        // 1. 默认配置（可通过options覆盖）
        try {
            // 2. 核心参数校验（提前拦截无效调用）
            if (!this.file_id || this.write_enable == 0) {
                // throw new Error('写入失败：fileId 不能为空（需关联具体的HDF5文件）');
                return;
            }
            if (!Array.isArray(dataPacket.data.big_bag_raw_data) || dataPacket.data.big_bag_raw_data.length == 0) {
                throw new Error(`文件 ${this.file_id} 写入失败：传感器数据必须是非空数组`);
            }

            // 3. 打印写入日志（关联fileId，方便溯源）
            //console.log(`\n=== 写入 HDF5 数据 ===, file_id = ${this.file_id}`);
            //console.log(`数据集：${datasetName} | 数据类型：${dtype} | 数据条数：${dataPacket.length}`);
            //console.log(`写入数据：`, dataPacket);



            // 4. 异步发送写入指令（await 等待服务端响应）
            const writeResponse = await this.sendCommand('append', {
                data: dataPacket.data
            });



            // 5. 校验写入结果，失败则主动抛出错误
            if (writeResponse.status !== 'success') {
                throw new Error(`文件 ${this.file_id} 写入失败：${writeResponse.msg || '未知错误'}`);
            }

            // 6. 打印成功日志并返回响应结果
            //console.log(`文件 ${this.file_id} 写入成功 | 累计数据条数：${writeResponse.total_count}`);
            return writeResponse;

        } catch (error) {
            // 7. 捕获所有错误，打印详情后重新抛出（让调用方感知）
            console.error(`[写入错误] file_id = ${this.file_id}：`, error.message);
            throw error;
        }
    }

    /**
     * 发送指令到 Python 服务端（适配新版 API）
     * @param {string} cmd 指令类型：create/write/close
     * @param {object} params 指令参数
     * @returns {Promise<object>} 服务端响应
     */
    // 2.6 发送指令到服务器
    async sendCommand(cmd, params = {}) {
        try {
            // 构造请求数据（JSON 序列化）
            const request = JSON.stringify({ cmd, params });
            // 发送数据（新版 send 支持字符串，自动转 Buffer）
            await this.storage_server_socket.send(request);

            // 接收响应（新版需用 iterator 接收，且响应是 Buffer 数组）
            const [responseBuffer] = await this.storage_server_socket.receive();
            const response = JSON.parse(responseBuffer.toString('utf8'));
            return response;
        } catch (err) {
            throw new Error(`指令发送失败（${cmd}）：${err.message}`);
        }
    }




    /**
     * 
     *  3.1 接收taskManager (lab.js) 的控制储存起始结束按钮
     */

    taskManager_get_command(buttomname)
    {
        //console.log(`realtimeEngine 收到按钮点击`, buttomname);
        this.buttomname = buttomname;
        switch (this.buttomname) {
            case 'start':
                this.taskManager_send_start();
                break;
            case 'stop':
                this.taskManager_send_end();
                break;
            case 'prompt1':
                this.taskManager_send_prompt(0);
                break;
            case 'prompt2':
                this.taskManager_send_prompt(1);
                break;
            case 'prompt3':
                this.taskManager_send_prompt(2);
                break;
            case 'prompt4':
                this.taskManager_send_prompt(3);
                break;
            case 'prompt5':
                this.taskManager_send_prompt(4);
                break;
            default:
                break;
        }


    }
    taskManager_send_start()
    {
        //console.log("realtimeEngine storage start");
        this.storage_start_flag = 1;
        //this.storage_server_create_new_hdf5_file();
    }

    taskManager_send_end()
    {
        //console.log("realtimeEngine storage end");
        this.storage_end_flag = 1;
        //this.storage_server_close_hdf5_file();
    }

    taskManager_send_prompt(i)
    {
        //console.log("realtimeEngine storage prompt = ", discrete_gesture_prompt_name[i]);
        this.prompt_flag = 1;
        this.prompt_name = discrete_gesture_prompt_name[i];
        this.prompt_time = getSysTimeNode();
        
    }


}

// 创建单例实例
const realtimeEngine = new RealtimeEngine();

module.exports = realtimeEngine;
