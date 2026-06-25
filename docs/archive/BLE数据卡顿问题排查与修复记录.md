# BLE 数据采集卡顿问题排查与修复记录




**日期**: 2026-03-13

**项目**: sEMG Collection System

**问题**: ESP32 BLE 数据传输在 STOP→START 循环后出现卡顿/冻结




---

## 1. 问题现象

### 1.1 初始症状
- 在实时显示界面，未点击"开始采集"，仅进行实时数据显示时，数据就会出现卡顿
- 日志显示丢包数量逐渐增多
- 完全冻结后无法恢复，必须重新连接设备

### 1.2 关键观察
| 测试场景 | 结果 |
|---------|------|
| 单独运行 `ble_test_client.py` 测试 `ble_server.py` | **正常** - 1分钟内0丢包 |
| 完整系统 `npm start` | **异常** - 累计丢包，最终冻结 |
| 供应商原版上位机 | **相同问题** - 连接→开始→停止→再开始→冻结 |

### 1.3 日志分析
```
05:34:47 - 开始采集
05:34:49 - 首次出现回调间隔异常 (107.9ms, 正常应 <40ms)
05:35:09 - 丢包增加到 36
05:35:13 - 数据超时 - 完全冻结
```

---

## 2. 排查过程

### 2.1 第一阶段：软件层面排查

#### 怀疑点1: ZMQ 存储服务阻塞
- **分析**: `realtimeEngine.js` 使用 ZMQ REQ/REP 模式发送数据到 `storage_server.py`，REP 模式是阻塞的
- **优化**: 改为 PUSH/PULL 模式，非阻塞发送
- **结果**: 问题依然存在

#### 怀疑点2: WebSocket 发送频率过高
- **分析**: 每个 BLE 数据包都立即通过 WebSocket 发送给前端
- **优化**: 实现批量发送，每3个包或50ms发送一次
- **结果**: 问题依然存在

#### 怀疑点3: 前端渲染压力
- **分析**: 检查 `waveform.js` 的渲染逻辑
- **优化**: 适配批量消息处理
- **结果**: 问题依然存在

### 2.2 第二阶段：硬件/固件层面排查

#### 关键线索
供应商的原版上位机也出现相同问题！这说明：
- **不是我们软件的问题**
- **问题在 ESP32 固件层面**

#### 固件代码分析 (`gatts_demo_imu-v3.1-260128.c`)

**发现的问题**:

1. **DRDY 中断未正确重置** (根本原因)
   - `gpio_isr_handler_add()` 只在 `data_task` 启动时执行一次
   - STOP 后中断状态未清理，START 时可能处于不稳定状态
   - 中断可能在硬件初始化过程中被触发，导致竞态条件

2. **STOP 延迟不足**
   - 原代码只有 50ms 延迟
   - 可能不足以等待当前 SPI 操作完成

3. **DISCONNECT 时未正确处理中断**
   - 断开连接时直接调用 `ads_stop_conversion()`
   - 没有先禁用中断

---

## 3. 解决方案

### 3.1 修复 START 命令 (0xA0)

**修改前**:
```c
else if (value == 0xA0) { // START
    if (!g_is_streaming) {
        esp_pm_lock_acquire(s_pm_cpu_lock);
        imu_set_power_mode(true);
        g_header_written = false;
        ads_power_up_reset();
        ads_config_global();
        ads_start_conversion();
        g_is_streaming = true;
    }
}
```

**修改后**:
```c
else if (value == 0xA0) { // START
    bool usb_busy = tinyusb_msc_storage_in_use_by_usb_host();

    if (usb_busy) {
        ESP_LOGW(GATTS_TAG, "Start Failed: USB MSC is active & Cable connected");
    }
    else {
        if (!g_is_streaming) {
            // 【修复】1. 先禁用 DRDY 中断，清除残留状态
            gpio_intr_disable(ADS_DRDY_PIN);

            // 2. 获取 CPU 锁
            esp_pm_lock_acquire(s_pm_cpu_lock);

            // 3. 唤醒 IMU
            imu_set_power_mode(true);

            // 4. 唤醒 ADS
            g_header_written = false;
            ads_power_up_reset();
            ads_config_global();
            ads_start_conversion();

            // 【修复】5. 等待硬件稳定后再启用中断
            vTaskDelay(pdMS_TO_TICKS(20));
            gpio_intr_enable(ADS_DRDY_PIN);

            g_is_streaming = true;
            ESP_LOGI(GATTS_TAG, "System Started (IRQ re-enabled)");
        }
    }
}
```

### 3.2 修复 STOP 命令 (0xA1)

**修改前**:
```c
} else if (value == 0xA1) { // STOP
    if (g_is_streaming) {
        g_is_streaming = false;
        vTaskDelay(pdMS_TO_TICKS(50));
        ads_stop_conversion();
        imu_set_power_mode(false);
        esp_pm_lock_release(s_pm_cpu_lock);
    }
}
```

**修改后**:
```c
} else if (value == 0xA1) { // STOP
    if (g_is_streaming) {
        // 【修复】1. 先禁用中断，防止在停止过程中触发
        gpio_intr_disable(ADS_DRDY_PIN);

        g_is_streaming = false;
        vTaskDelay(pdMS_TO_TICKS(100));  // 【修复】增加延迟到100ms

        // 2. 停止 ADS
        ads_stop_conversion();

        // 3. 休眠 IMU
        imu_set_power_mode(false);

        // 4. 释放 CPU 锁
        esp_pm_lock_release(s_pm_cpu_lock);

        ESP_LOGI(GATTS_TAG, "System Stopped (Low Power Mode, IRQ disabled)");
    }
}
```

### 3.3 修复 DISCONNECT 事件

**修改前**:
```c
case ESP_GATTS_DISCONNECT_EVT:
    ESP_LOGI(GATTS_TAG, "DISCONNECTED");
    emg_conn_id = 0xFFFF;
    notify_enabled = false;
    g_ble_connected = false;
    g_is_streaming = false;
    g_is_advertising = true;
    ads_stop_conversion();
    esp_ble_gap_start_advertising(&adv_params);
    break;
```

**修改后**:
```c
case ESP_GATTS_DISCONNECT_EVT:
    ESP_LOGI(GATTS_TAG, "DISCONNECTED");
    emg_conn_id = 0xFFFF;
    notify_enabled = false;

    // 【修复】断开连接时正确停止采集
    if (g_is_streaming) {
        gpio_intr_disable(ADS_DRDY_PIN);  // 先禁用中断
        g_is_streaming = false;
        vTaskDelay(pdMS_TO_TICKS(50));    // 等待当前操作完成
    }

    g_ble_connected = false;
    g_is_advertising = true;

    ads_stop_conversion();
    esp_ble_gap_start_advertising(&adv_params);
    break;
```

---

## 4. 修复原理

### 4.1 问题根因分析

```
┌─────────────────────────────────────────────────────────────┐
│                    原始代码执行流程                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  START:                                                     │
│    ads_power_up_reset()  ──┐                                │
│    ads_config_global()    │  ← DRDY 中断可能在此期间触发    │
│    ads_start_conversion() ─┘    导致读取无效数据            │
│                                                             │
│  STOP:                                                      │
│    g_is_streaming = false                                   │
│    vTaskDelay(50ms)       ← 中断仍在触发！                  │
│    ads_stop_conversion()  ← 可能与中断处理产生竞态          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

```
┌─────────────────────────────────────────────────────────────┐
│                    修复后执行流程                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  START:                                                     │
│    gpio_intr_disable()    ← 先禁用中断                      │
│    ads_power_up_reset()                                     │
│    ads_config_global()     安全的硬件初始化                  │
│    ads_start_conversion()                                   │
│    vTaskDelay(20ms)       ← 等待硬件稳定                    │
│    gpio_intr_enable()     ← 再启用中断                      │
│                                                             │
│  STOP:                                                      │
│    gpio_intr_disable()    ← 先禁用中断                      │
│    g_is_streaming = false                                   │
│    vTaskDelay(100ms)      ← 充足的等待时间                  │
│    ads_stop_conversion()   安全停止                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 关键 API 说明

| 函数 | 作用 |
|-----|------|
| `gpio_intr_disable(pin)` | 禁用指定 GPIO 的中断 |
| `gpio_intr_enable(pin)` | 启用指定 GPIO 的中断 |
| `vTaskDelay(pdMS_TO_TICKS(ms))` | FreeRTOS 延迟函数 |

---

## 5. 验证结果

修复后测试：
- ✅ 连接 → 开始采集 → 停止 → 再开始：**正常**
- ✅ 多次循环 STOP/START：**正常**
- ✅ 长时间实时显示：**0丢包**
- ✅ 断开重连后采集：**正常**

---

## 6. 经验总结

### 6.1 排查方法论

1. **分层排查**: 从上层软件逐步排查到底层硬件
2. **对比测试**: 单独测试各组件，定位问题层级
3. **寻找共性**: 发现供应商软件也有相同问题，说明问题在固件层

### 6.2 嵌入式开发要点

1. **中断管理**:
   - 硬件初始化/停止时必须正确管理中断状态
   - 避免在硬件状态不稳定时触发中断

2. **竞态条件**:
   - ISR 与主程序之间的资源共享需要仔细处理
   - 使用 `disable/enable` 包围关键操作

3. **时序要求**:
   - 给硬件足够的稳定时间
   - 停止操作需要等待当前操作完成

---

## 7. 相关文件

| 文件 | 修改内容 |
|-----|---------|
| `gatts_demo_imu-v3.1-260128.c` | ESP32 固件核心修复 |
| `realtimeEngine.js` | ZMQ PUSH/PULL 优化、批量发送 |
| `storage_server.py` | PUSH/PULL 模式支持 |
| `waveform.js` | 批量消息处理 |

---

## 8. 附录：软件层优化（附带实现）

虽然软件层优化不是根本原因，但这些优化仍然有价值：

### 8.1 ZMQ PUSH/PULL 模式

```javascript
// realtimeEngine.js
this.storage_push_socket = new zmq.Push();
await this.storage_push_socket.connect(`tcp://127.0.0.1:5556`);

// 发送数据 - 非阻塞
await this.storage_push_socket.send(JSON.stringify({ cmd: 'append', params: { data } }));
```

```python
# storage_server.py
self.data_socket = self.context.socket(zmq.PULL)
self.data_socket.bind(f"tcp://{host}:5556")
```

### 8.2 WebSocket 批量发送

```javascript
// 缓冲配置
this.realtimeDataBuffer = [];
this.realtimeDataBufferLimit = 3;  // 每3包发送一次
this.realtimeDataMaxDelay = 50;    // 最大延迟50ms

// 批量发送
flushRealtimeDataBuffer() {
    this.broadcastToClients({
        type: 'realtime_data_batch',
        batch: this.realtimeDataBuffer
    });
    this.realtimeDataBuffer = [];
}
```
