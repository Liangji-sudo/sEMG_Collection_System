# 数据格式说明文档

## 数据流架构

```
┌─────────────────┐      ┌─────────────────────┐      ┌─────────────────┐
│  ble_server.py  │ ───► │  realtimeEngine.js  │ ───► │   waveform.js   │
│    (端口8766)    │      │     (端口8080)       │      │   (前端显示)     │
└─────────────────┘      └─────────────────────┘      └─────────────────┘
     数据格式A                  数据格式B                   渲染数据
```

---

## 一、ble_server.py → realtimeEngine.js (端口8766)

### 数据包格式 (type: "data")

```json
{
    "type": "data",
    "ts": 1704067200.123456,
    "dev1": { ... },
    "dev2": { ... },
    "active": [1, 2]
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 固定为 `"data"` |
| `ts` | float | 发送时间戳（Unix时间戳，秒） |
| `dev1` | object \| null | 设备1的数据包，未连接/未采集时为 `null` |
| `dev2` | object \| null | 设备2的数据包，未连接/未采集时为 `null` |
| `active` | array | 当前正在采集的设备ID列表，如 `[1]`, `[2]`, `[1,2]` |

### 单设备数据包格式 (dev1/dev2)

```json
{
    "f": 12345,
    "n": 9,
    "raw": [
        [ch0, ch1, ch2, ..., ch15],
        [ch0, ch1, ch2, ..., ch15],
        ...
    ],
    "uv": [
        [ch0, ch1, ch2, ..., ch15],
        [ch0, ch1, ch2, ..., ch15],
        ...
    ],
    "emg_t": [t0, t1, t2, ..., t8],
    "imu": [
        [[ax, ay, az], [gx, gy, gz], [mx, my, mz]],
        [[ax, ay, az], [gx, gy, gz], [mx, my, mz]]
    ],
    "imu_t": [t0, t1],
    "s": [total_frames, lost_frames]
}
```

### 单设备字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `f` | int | 起始帧索引 |
| `n` | int | 本包帧数（通常为9） |
| `raw` | array[n][16] | EMG原始ADC值，n帧×16通道 |
| `uv` | array[n][16] | EMG微伏值，n帧×16通道 |
| `emg_t` | array[n] | **EMG时间戳数组，每帧一个时间戳** |
| `imu` | array[2][3][3] | IMU数据，2组×(加速度计/陀螺仪/磁力计)×3轴 |
| `imu_t` | array[2] | **IMU时间戳数组，每组一个时间戳** |
| `s` | array[2] | 统计信息 [总帧数, 丢帧数] |

### EMG数据维度说明

```
uv[帧索引][通道索引]

uv = [
    [frame0_ch0, frame0_ch1, ..., frame0_ch15],  // 第0帧，16通道
    [frame1_ch0, frame1_ch1, ..., frame1_ch15],  // 第1帧，16通道
    ...
    [frame8_ch0, frame8_ch1, ..., frame8_ch15],  // 第8帧，16通道
]

维度: [9][16] = 9帧 × 16通道

emg_t = [t0, t1, t2, t3, t4, t5, t6, t7, t8]  // 9个时间戳，对应9帧
时间间隔: 1ms (1kHz采样率)
```

### IMU数据维度说明

```
imu[组索引][传感器索引][轴索引]

imu = [
    [                           // 第0组
        [ax, ay, az],           // 加速度计 (g)
        [gx, gy, gz],           // 陀螺仪 (deg/s)
        [mx, my, mz]            // 磁力计 (μT)
    ],
    [                           // 第1组
        [ax, ay, az],
        [gx, gy, gz],
        [mx, my, mz]
    ]
]

维度: [2][3][3] = 2组 × 3传感器 × 3轴

imu_t = [t0, t1]  // 2个时间戳，对应2组
时间间隔: 5ms (200Hz采样率)
```

---

## 二、realtimeEngine.js → waveform.js (端口8080)

### 数据包格式 (type: "realtime_data")

```json
{
    "type": "realtime_data",
    "data": {
        "emg1": [[...], [...], ...],
        "emg2": [[...], [...], ...],
        "emg1_t": [t0, t1, ..., t8],
        "emg2_t": [t0, t1, ..., t8],
        "imu1": { "acc": [...], "gyr": [...], "mag": [...] },
        "imu2": { "acc": [...], "gyr": [...], "mag": [...] },
        "imu1_t": [t0, t1],
        "imu2_t": [t0, t1],
        "timestamp": 1704067200.123456,
        "packetCount": 12345,
        "framesInPacket": 9,
        "stats1": { "total": 10000, "lost": 5 },
        "stats2": { "total": 9800, "lost": 3 },
        "activeDevices": [1, 2]
    }
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 固定为 `"realtime_data"` |
| `data` | object | 数据内容 |

### data 内部字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `emg1` | array[16][n] \| null | 设备1 EMG数据，**已转置**为 16通道×n帧 |
| `emg2` | array[16][n] \| null | 设备2 EMG数据，**已转置**为 16通道×n帧 |
| `emg1_t` | array[n] \| null | **设备1 EMG时间戳数组，每帧一个** |
| `emg2_t` | array[n] \| null | **设备2 EMG时间戳数组，每帧一个** |
| `imu1` | object \| null | 设备1 IMU数据 |
| `imu2` | object \| null | 设备2 IMU数据 |
| `imu1_t` | array[2] \| null | **设备1 IMU时间戳数组，每组一个** |
| `imu2_t` | array[2] \| null | **设备2 IMU时间戳数组，每组一个** |
| `timestamp` | float | 包级别时间戳（保留兼容） |
| `packetCount` | int | 累计包数 |
| `framesInPacket` | int | 本包帧数（通常为9） |
| `stats1` | object \| null | 设备1统计 |
| `stats2` | object \| null | 设备2统计 |
| `activeDevices` | array | 活跃设备列表 |

### EMG数据格式（已转置，方便渲染）

```
emg1[通道索引][帧索引]

emg1 = [
    [ch0_frame0, ch0_frame1, ..., ch0_frame8],  // 通道0，9帧数据
    [ch1_frame0, ch1_frame1, ..., ch1_frame8],  // 通道1，9帧数据
    ...
    [ch15_frame0, ch15_frame1, ..., ch15_frame8], // 通道15，9帧数据
]

维度: [16][9] = 16通道 × 9帧

emg1_t = [t0, t1, t2, t3, t4, t5, t6, t7, t8]  // 9个时间戳
emg1[ch][i] 对应的时间戳是 emg1_t[i]
```

**注意**：这里的维度与 ble_server 发送的是**转置**关系！
- ble_server: `[帧][通道]` = `[9][16]`
- realtimeEngine → 前端: `[通道][帧]` = `[16][9]`

### IMU数据格式

```json
{
    "acc": [ax, ay, az],
    "gyr": [gx, gy, gz],
    "mag": [mx, my, mz]
}
```

| 字段 | 类型 | 单位 | 说明 |
|------|------|------|------|
| `acc` | array[3] | g | 加速度计 X/Y/Z |
| `gyr` | array[3] | deg/s | 陀螺仪 X/Y/Z |
| `mag` | array[3] | μT | 磁力计 X/Y/Z |

### IMU时间戳说明

```
imu1_t = [t0, t1]  // 2个时间戳，对应2组IMU数据
imu1 的数据对应 imu1_t[0] 时刻（使用第一组）
```

### stats 统计对象格式

```json
{
    "total": 10000,
    "lost": 5
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `total` | int | 累计接收帧数 |
| `lost` | int | 累计丢帧数 |

---

## 三、数据转换示意

### EMG数据转置过程

```
ble_server 发送 (uv):          realtimeEngine 转置后 (emg1/emg2):
┌─────────────────────┐        ┌─────────────────────┐
│ Frame0: [c0,c1...c15]│        │ Ch0:  [f0,f1...f8]  │
│ Frame1: [c0,c1...c15]│   ──►  │ Ch1:  [f0,f1...f8]  │
│ ...                  │        │ ...                 │
│ Frame8: [c0,c1...c15]│        │ Ch15: [f0,f1...f8]  │
└─────────────────────┘        └─────────────────────┘
     [9][16]                         [16][9]
```

### 转置函数 (realtimeEngine.js)

```javascript
transposeEMG(uvData) {
    // uvData: [帧][通道] = [9][16]
    // 返回:   [通道][帧] = [16][9]
    
    const numFrames = uvData.length;      // 9
    const numChannels = uvData[0].length; // 16
    
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
```

---

## 四、完整示例

### ble_server.py 发送的数据包示例

```json
{
    "type": "data",
    "ts": 1704067200.123,
    "dev1": {
        "f": 1000,
        "n": 9,
        "raw": [
            [100, 102, 98, 105, 101, 99, 103, 97, 104, 100, 102, 98, 105, 101, 99, 103],
            [101, 103, 99, 106, 102, 100, 104, 98, 105, 101, 103, 99, 106, 102, 100, 104],
            ...
        ],
        "uv": [
            [50.5, 51.2, 49.8, 52.1, 50.9, 50.0, 51.8, 49.2, 52.5, 50.5, 51.2, 49.8, 52.1, 50.9, 50.0, 51.8],
            [51.0, 51.8, 50.2, 52.6, 51.4, 50.5, 52.3, 49.7, 53.0, 51.0, 51.8, 50.2, 52.6, 51.4, 50.5, 52.3],
            ...
        ],
        "emg_t": [
            1704067200.120000,
            1704067200.121000,
            1704067200.122000,
            1704067200.123000,
            1704067200.124000,
            1704067200.125000,
            1704067200.126000,
            1704067200.127000,
            1704067200.128000
        ],
        "imu": [
            [[0.02, -0.01, 1.01], [1.5, -0.8, 0.3], [25.2, 3.1, -42.5]],
            [[0.03, -0.02, 1.00], [1.6, -0.7, 0.4], [25.3, 3.0, -42.4]]
        ],
        "imu_t": [
            1704067200.120000,
            1704067200.125000
        ],
        "s": [1009, 2]
    },
    "dev2": null,
    "active": [1]
}
```

### realtimeEngine.js 发送给前端的数据包示例

```json
{
    "type": "realtime_data",
    "data": {
        "emg1": [
            [50.5, 51.0, 51.5, 50.8, 51.2, 50.9, 51.3, 50.7, 51.1],
            [51.2, 51.8, 52.3, 51.6, 52.0, 51.7, 52.1, 51.5, 51.9],
            ...
        ],
        "emg2": null,
        "emg1_t": [
            1704067200.120000,
            1704067200.121000,
            1704067200.122000,
            1704067200.123000,
            1704067200.124000,
            1704067200.125000,
            1704067200.126000,
            1704067200.127000,
            1704067200.128000
        ],
        "emg2_t": null,
        "imu1": {
            "acc": [0.02, -0.01, 1.01],
            "gyr": [1.5, -0.8, 0.3],
            "mag": [25.2, 3.1, -42.5]
        },
        "imu2": null,
        "imu1_t": [1704067200.120000, 1704067200.125000],
        "imu2_t": null,
        "timestamp": 1704067200.123,
        "packetCount": 1009,
        "framesInPacket": 9,
        "stats1": { "total": 1009, "lost": 2 },
        "stats2": null,
        "activeDevices": [1]
    }
}
```

---

## 五、不同连接状态下的数据

### 只连接设备1

```json
{
    "emg1": [[...], [...], ...],  // 有数据
    "emg2": null,                  // 无数据
    "imu1": { ... },               // 有数据
    "imu2": null,                  // 无数据
    "activeDevices": [1]
}
```

### 只连接设备2

```json
{
    "emg1": null,                  // 无数据
    "emg2": [[...], [...], ...],  // 有数据
    "imu1": null,                  // 无数据
    "imu2": { ... },               // 有数据
    "activeDevices": [2]
}
```

### 两个设备都连接

```json
{
    "emg1": [[...], [...], ...],  // 有数据
    "emg2": [[...], [...], ...],  // 有数据
    "imu1": { ... },               // 有数据
    "imu2": { ... },               // 有数据
    "activeDevices": [1, 2]
}
```

---

## 六、控制消息格式

### ble_server.py 控制端口 (8764) 消息

#### 请求格式
```json
{
    "action": "scan" | "connect1" | "connect2" | "disconnect1" | "disconnect2" | 
              "start1" | "start2" | "stop1" | "stop2" | "start_all" | "stop_all" | "status",
    "mac": "AA:BB:CC:DD:EE:FF"  // connect时需要
}
```

#### 响应格式
```json
{
    "type": "response",
    "action": "scan",
    "success": true,
    "devices": [
        { "name": "ESP32S3_EMG_001", "mac": "AA:BB:CC:DD:EE:01", "rssi": -45 },
        ...
    ]
}
```
