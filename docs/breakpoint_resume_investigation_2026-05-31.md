# 异常中断后断点续采 — 架构调查与实现方案

**日期**：2026-05-31  
**分支**：feat_breakpoint  
**状态**：Phase 1 — 只读代码、写文档，不改业务代码  

---

## 1. 当前采集链路梳理

### 1.1 完整数据流

```
[腕带 BLE] → ble_server.py (WS:8764/8766)
                ↓
         realtimeEngine.js (WS:8080)
                ↓              ↓
         [前端 H5]    storage_server.py (ZMQ REP:5555 / PULL:5556)
                              ↓
                         HDF5 文件
```

### 1.2 前端页面流转

```
welcomeScreen (index.html)
  │
  ├─ 点击"采集" ──→ collection-selector.js (分步选择弹窗)
  │                    ├─ 分类0: 采集任务
  │                    ├─ 分类1: 大类
  │                    ├─ 分类2: 大场景
  │                    ├─ 分类4: 人群
  │                    └─ 分类5: 受试者信息
  │                    ↓ .complete()
  │                    window.currentCollectionConfig = config
  │                    localStorage: emg_current_collection_config
  │                    ↓
  │                  collectionScreen (采集页)
  │                    ↓
  │                  collection-controller.js
  │                    ├─ selectTask(taskId)
  │                    └─ startTask(isTestMode)
  │
  └─ 点击"后台" ──→ backend-page (后台管理页)
```

### 1.3 采集启动 → 数据写入链路

```
collection-controller.startTask()
  ├─ sendToRealtimeEngine('collection_start', {...})
  │     ↓ WS → realtimeEngine.onCollectionStart()
  │        保存: taskId, userId, config, sessionIndex, isTestMode,
  │              recordingSessionId, sd_filenames, device_names
  │
  ├─ startDiscreteGestureCollection() / startContinualGestureCollection()
  │     ↓
  │   sendToRealtimeEngine('stage_start', {stageName, stageIndex, needMocap})
  │     ↓ WS → realtimeEngine.onStageStart()
  │         → openStageFile(stageName, stageIndex)
  │           ↓ ZMQ REP → storage_server.create_file()
  │              创建 H5 文件:
  │              dir: storage/{task}/{cat1}/{cat2}/{cat4}/{userId}/
  │              file: {userId}_{cat2}_{stage}_session{N}_{date}_{time}.h5
  │              写入 attrs: task_id, user_id, session_index, sd_bin_dev1/2,
  │                         ble_device_dev1/2, recording_session_id...
  │              创建 datasets: emg1/2_250hz_adc, imu1a/1b/2a/2b_ble,
  │                           mocap_L/R, prompts, ...
  │
  ├─ BLE 数据到达 → realtimeEngine.handleBleDataPacket()
  │     → saveDataToStorage() → ZMQ PUSH → storage_server.append_data()
  │       → _append_emg / _append_imu / _append_mocap_batch
  │
  ├─ 手势 prompt → sendToRealtimeEngine('prompt', {name, stageName, ts})
  │     → realtimeEngine.onPrompt()
  │       → sendStorageCommand('append', {prompt_name, prompt_time, prompt_stage})
  │
  └─ 采集结束 →
      ├─ sendToRealtimeEngine('stage_end', {stageName})
      │     → closeStageFile() → storage_server.close_file()
      │         写入 attrs: closed_at, total_emg1/2_frames, total_prompts, ...
      │
      └─ sendToRealtimeEngine('collection_stop', {completed: true/false})
            → 重置 isCollecting, isTestMode, collectionPaused
```

### 1.4 前端按钮布局

采集页面控制按钮 ([index.html:5458-5471](public/index.html#L5458-L5471))：

```html
<div class="control-buttons">
    <button id="startAllSessionsBtn"> 开始采集（全部轮次）</button>
    <button id="startTaskBtn">        开始采集（单轮）</button>
    <button id="testModeBtn">         测试</button>
    <button id="stopTaskBtn" disabled>停止</button>
</div>
```

当前只有 4 个按钮：全轮、单轮、测试、停止。**没有"异常中断"按钮**。

欢迎页操作按钮 ([index.html:4975-4986](public/index.html#L4975-L4986))：

```html
<div class="action-buttons">
    <input id="sessionIdInput" placeholder="如 S001">
    <button id="startCollectionBtn">采集</button>
    <button id="backendBtn">       后台</button>
</div>
```

当前只有 2 个按钮：采集、后台。**没有"断点续采"按钮**。

---

## 2. 当前 H5 文件生命周期

### 2.1 H5 文件 = 一个 Stage × 一个 Session

- **创建时机**：`stage_start` 命令（每个 Stage 开始时）
- **关闭时机**：`stage_end` 命令（Stage 完成时）或 `collection_stop`（异常停止时）
- **一个完整采集会话**产生 N 个 H5 文件（N = sessionCount × stageCount）
- 例如 3 轮 × 2 个 Stage = 6 个 H5 文件

### 2.2 H5 文件当前属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `task_id` | string | 采集任务（如 "离散手势采集"） |
| `user_id` | string | 受试者编号 |
| `stage_name` | string | 子场景名 |
| `category1/2/4` | string | 分类标签 |
| `session_index/number/count` | int | 轮次信息 |
| `sd_bin_dev1/2` | string | SD 卡 bin 前缀 |
| `ble_device_dev1/2` | string | BLE 设备名 |
| `sync_status` | string | `pending` / `synced` / `sync_failed` |
| `created_at` | ISO time | 文件创建时间 |
| `closed_at` | ISO time | 文件关闭时间 |
| `recording_session_id` | string | 录像会话 ID |

### 2.3 关键发现：缺失中断状态标记

当前 H5 属性中**完全没有**以下字段：
- `collection_status` — 不知道这次采集是正常完成还是被中断
- `interrupted_at` — 不知道中断发生在哪个 gesture/trial
- `is_resumed` / `resume_parent_id` — 无法追溯续采关系

---

## 3. 当前任务进度状态在哪里维护

### 3.1 唯一状态存储：collection-controller.js 内存变量

```javascript
// collection-controller.js — 全部是内存变量，没有任何持久化
this.currentSessionIndex = 0;     // 第几轮
this.currentStageIndex = 0;       // 第几个 Stage
this.currentGestureIndex = 0;     // 第几个手势（离散）
this.gestureRepeatCount = 0;      // 当前手势重复次数
this.continualTrialCount = 0;     // 当前试次计数（连续）
this._shuffleMode = false;        // 是否乱序模式
this._isRunning = false;          // 是否正在采集
this.currentPhase = null;         // 'prepare' | 'gesture' | 'rest' | 'continual' | 'complete'
```

### 3.2 采集配置存储

```javascript
// 两个地方存储（都是同一份数据）：
window.currentCollectionConfig   // 内存（页面刷新后丢失）
localStorage: 'emg_current_collection_config'  // 持久化（页面刷新后还在）

// 配置内容（collection-selector.js:562-584）：
{
    task_id, task,                          // 任务类型
    category1/2/4_id, category1/2/4,        // 分类选择
    category3List: [...],                   // Stage 列表
    sessionConfig: { count: 3, ... },       // Session 配置
    execution: {...},                       // 执行参数（repeat/trials/dwell...）
    gestures: {...},                        // 手势库配置
    subject: {...},                         // 受试者信息
    templateName, timestamp
}
```

### 3.3 问题总结

| 数据 | 内存 | localStorage | H5 attrs | 重启后 |
|------|------|-------------|----------|--------|
| 采集配置 | ✅ window. | ✅ | ❌ | ✅ 可恢复 |
| 进度状态 | ✅ this. | ❌ | ❌ | ❌ 丢失 |
| sd_bin_dev1/2 | ✅ realtimeEngine | ❌ | ✅ | ✅ 可从 H5 读 |
| device_names | ✅ realtimeEngine | ❌ | ✅ | ✅ 可从 H5 读 |

**结论：中断后如果需要恢复，必须先把 `collection-controller` 的状态刷到持久化存储。**

---

## 4. 当前 bin 文件名如何进入 H5 或 realtimeEngine

### 4.1 数据流

```
ble_server.py
  └─ start_all 成功 → WS 发送 event: sd_filenames_updated
       { sd_filenames: {dev1: "S001_L_260312_143025", dev2: "S001_R_260312_143025"},
         device_names: {dev1: "WristBand_3A76", dev2: "WristBand_5B12"} }
       ↓
realtimeEngine.js
  └─ onSdFilenamesUpdated(sd_filenames, device_names)
       保存到 this.sd_filenames, this.device_names
       ↓
  └─ openStageFile() 时
       传递 sd_bin_dev1, sd_bin_dev2, ble_dev1, ble_dev2 到 storage_server
       ↓
storage_server.py
  └─ create_file() → 写入 H5 attrs: sd_bin_dev1, sd_bin_dev2,
                                     ble_device_dev1, ble_device_dev2
```

### 4.2 bin_sync_tool 如何使用

```python
# hdf5_tool.py SyncWorker._find_bin_files()
attr_name = f'sd_bin_dev{device_id}'
bin_prefix = f.attrs.get(attr_name)  # 例如 "S001_L_260312_143025"
emg_bin_name = f"{bin_prefix}_emg.bin"  # 例如 "S001_L_260312_143025_emg.bin"
# 在 bin_dir 中查找
```

**关键依赖**：`sd_bin_dev1/2` 属性必须准确对应 SD 卡 bin 文件名前缀。如果换设备，前缀会变。

---

## 5. 断点续采的核心难点和风险

### 难点 1：进度状态无持久化
当前所有进度状态都在 `collection-controller.js` 内存变量中，刷新页面或重启系统全部丢失。必须在中断时刷到持久化存储。

### 难点 2：H5 文件不完整
中断时 H5 文件已写入部分数据（EMG/IMU/mocap/prompts），但：
- `sync_status = "pending"`（未同步）
- 没有 `collection_status` 标记
- 后续 bin_sync_tool 无法区分"正常完成但未同步"和"中断未完成"

### 难点 3：设备可能更换
异常中断后，工作人员可能：
- 更换没电的腕带 → dev1/dev2 MAC 地址变化
- 新腕带产生新的 bin 文件 → sd_bin_dev1/2 前缀变化
- 不能假设同一个设备贯穿整个恢复任务

### 难点 4：bin 文件时序一致性
bin_sync_tool 使用 `frame_id × 8 + j` 映射 SD 帧号到 H5 的 BLE 帧号。如果续采时：
- 使用同一 bin 文件：frame_id 不连续（中间有 gap）
- 使用不同 bin 文件：frame_id 从 0 重新开始，可能和前面的数据混淆

### 难点 5：乱序模式状态复杂
`_shuffleMode` 时，手势序列已被 Fisher-Yates 打乱 + 部分顺序段。中断后恢复需要知道：
- 当前 shuffle 序列中执行到第几个实例
- 每个实例的 `_shuffleSegment`（ordered/shuffled）
- 顺序段的边界位置

### 难点 6：storage_server 单文件模式
当前 `HDF5StorageServer` 一次只管理一个 H5 文件（`self.f`）。如果需要在同一个 Stage 内续写，需要重新打开已有文件并定位写入位置。

---

## 6. 推荐架构方案比较

### 6.1 方案 A：续写同一个 H5

**做法**：中断后，`stage_start` 不再创建新 H5，而是重新打开上一个未完成的 H5 继续追加。

**优势**：
- 一个 Stage 只产生一个 H5 文件，数据最完整
- bin_sync_tool 逻辑不变，只需处理一个文件

**风险（高）**：
1. **文件损坏风险**：中断时 H5 可能处于不一致状态（buffer 未刷盘），重新打开后可能无法追加
2. **h5py 追加限制**：resize 操作在 maxshape=(None,) 的 gzip 压缩数据集上可行，但 reopen 后在末尾追加会触发 metadata rewrite，大文件时很慢
3. **设备更换冲突**：如果换设备，`sd_bin_dev1` 属性需要覆盖 → 同一文件出现两个 bin 前缀，bin_sync_tool 逻辑复杂化
4. **frame_id 连续性**：新设备的 BLE frame_id 从 0 开始，和旧数据拼接后会看到 frame_id 回退，bin_sync_tool 的 `validate_frame_ids()` 严格校验会报 gap
5. **并发写入风险**：如果程序崩溃时正在 `ds.resize()`，文件可能永久损坏

**结论：❌ 不推荐。风险太高，尤其在设备更换场景下几乎不可行。**

### 6.2 方案 B：每次异常后新建 segment H5 + resume manifest（推荐）

**做法**：每次异常中断 → 关闭当前 H5（标记为 interrupted）→ 重新连接设备 → 点击"断点续采" → 创建新 H5 继续 → 所有 H5 由一个 JSON manifest 文件串联。

**优势**：
1. **隔离性好**：每个 segment 是独立的完整 H5 文件，一个损坏不影响其他
2. **设备更换友好**：每个 segment 有自己的 `sd_bin_dev1/2` 和 `ble_device_dev1/2`
3. **bin_sync_tool 无需改动**：每个文件独立同步，校验逻辑不变
4. **实现简单**：不修改现有的 `create_file` / `close_file` 路径
5. **审计友好**：manifest 记录每次中断和恢复的时间线

**劣势**：
1. 文件数量增多（同一 Stage 可能有多个 H5）
2. 需要 manifest 层来合并视图
3. 跨 segment 的 frame 连续性分析需要 manifest 辅助

**分段命名策略**：
```
S001_坐姿_手心朝上_session1_20260531_141100.h5       # 正常完成
S001_坐姿_手心朝上_session1_20260531_141500.h5       # 中断
S001_坐姿_手心朝上_session1_20260531_142000_seg2.h5  # 续采-继续
S001_坐姿_手心朝上_session1_20260531_143000_seg3.h5  # 再次中断后恢复
```

### 6.3 方案 C：单 H5 内部多 group/segment

**做法**：一个 H5 文件内部用 group 隔离不同 segment（如 `/segment_1/`, `/segment_2/`），每个 group 下有独立的数据集。

**优势**：
1. 文件数量少
2. 设备信息可按 segment group 独立存储

**劣势**：
1. **和方案 A 相同的文件损坏风险**：一个 segment 写入出错可能损坏整个文件
2. h5py 在 groups 间 resize 复杂度更高
3. **bin_sync_tool 需要大改**：需要遍历 groups 分别同步
4. 实现复杂度介于 A 和 B 之间，但收益不明显

**结论：⚠️ 不推荐。比 A 稍好但没有根本性解决问题，复杂度反而更高。**

---

## 7. 推荐 MVP 方案：方案 B + localStorage 状态缓存

### 7.1 核心设计

```
┌─────────────────────────────────────────────────────────┐
│                   Resume Manifest (JSON)                 │
│ 存储位置: storage/{userId}/_resume_manifests/            │
│ 或在 localStorage + 文件系统双写                          │
├─────────────────────────────────────────────────────────┤
│ {                                                       │
│   "manifest_id": "resume_S001_20260531_141500",         │
│   "collection_status": "abnormal_interrupted",           │
│   "created_at": "2026-05-31T14:10:00",                  │
│   "updated_at": "2026-05-31T14:20:00",                  │
│   "collection_config": { ... },      // 同 emg_current_collection_config │
│   "segments": [                                          │
│     {                                                    │
│       "segment_index": 1,                                │
│       "h5_file": "storage/.../S001_..._141100.h5",      │
│       "status": "interrupted",                           │
│       "sd_bin_dev1": "S001_L_260531_141000",            │
│       "sd_bin_dev2": "S001_R_260531_141000",            │
│       "ble_device_dev1": "WristBand_3A76",              │
│       "ble_device_dev2": "WristBand_5B12",              │
│       "frame_id_range_dev1": [0, 3499],                 │
│       "frame_id_range_dev2": [0, 3499],                 │
│       "start_time": "...",                               │
│       "end_time": "..."                                  │
│     },                                                   │
│     {                                                    │
│       "segment_index": 2,                                │
│       "h5_file": "storage/.../S001_..._142000_seg2.h5", │
│       "status": "completed",                             │
│       ...                                                │
│     }                                                    │
│   ],                                                     │
│   "resume_progress": {          // 断点续采恢复进度      │
│     "session_index": 0,                                  │
│     "session_number": 1,                                 │
│     "stage_index": 1,                                    │
│     "gesture_index": 2,                                  │
│     "gesture_repeat_count": 3,                           │
│     "continual_trial_count": 5,                          │
│     "shuffle_mode": false,                               │
│     "shuffle_sequence_snapshot": null,                   │
│     "interrupted_at": "2026-05-31T14:20:00",            │
│     "interrupt_reason": "设备掉电"                        │
│   }                                                      │
│ }                                                        │
└─────────────────────────────────────────────────────────┘
```

### 7.2 各系统职责

| 系统 | 新增职责 |
|------|---------|
| **collection-controller.js** | 中断时保存进度到 localStorage + 后端 manifest；续采时恢复进度 |
| **realtimeEngine.js** | 处理 `abnormal_interrupt` 命令；记录 frame_id range；管理 manifest 写入 |
| **storage_server.py** | H5 attrs 新增 `collection_status`, `segment_index`；close 时记录 frame_id range |
| **hdf5_tool.py** | 显示 `collection_status`；支持 manifest 合并视图 |

---

## 8. 分阶段实现计划

### Phase 1：异常中断按钮 + 状态缓存 + H5 标记（最小改动）

**目标**：点击"异常中断" → 保存当前进度 → 关闭 H5 → 标记 interrupted

**前端改动**：
1. [index.html](public/index.html) — 采集页控制区增加"异常中断"按钮（红色/橙色，与停止按钮区分）
2. [collection-controller.js](public/scripts/collection-controller.js) — 新增 `abortTask()` 方法：
   - 保存当前进度到 localStorage key: `emg_breakpoint_state`
   - 调用 `sendToRealtimeEngine('abnormal_interrupt', {reason, progress})`
   - 停止所有动画和定时器
   - 设置按钮状态
3. [page-switch.js](public/scripts/page-switch.js) — `backToWelcome()` 中检查是否有中断状态，如有则不走 `confirm('确定要返回吗？')` 的普通停止流程

**后端改动**：
1. [realtimeEngine.js](realtimeEngine.js) — 新增 `onAbnormalInterrupt()` 处理：
   - 记录 `interrupt_reason`, `interrupted_at`, 当前 `frame_id` range
   - 调用 `closeStageFile()` 正常关闭 H5
   - 将中断信息写入 H5 attrs（通过 `close` 命令扩展参数）
2. [storage_server.py](storage_server.py) — `close_file()` 接受扩展参数：
   - `collection_status`: `"abnormal_interrupted"` | `"completed"` | `"abandoned"`
   - `interrupted_at`: ISO 时间
   - `segment_index`: segment 序号
   - 写入 H5 attrs

**不修改**：ble_server.py, bin_sync_tool.py

**代码改动量估算**：
- collection-controller.js: +60 行
- page-switch.js: +15 行
- index.html: +5 行（按钮 HTML + CSS）
- realtimeEngine.js: +30 行
- storage_server.py: +15 行

### Phase 2：首页断点续采入口 + 恢复进度

**目标**：首页显示"断点续采"按钮 → 点击后恢复中断的采集任务

**前端改动**：
1. [index.html](public/index.html) — 欢迎页增加"断点续采"按钮（仅在 `emg_breakpoint_state` 存在时显示）
2. [collection-selector.js](public/scripts/collection-selector.js) — 新增"跳过选择，直接恢复"模式
3. [collection-controller.js](public/scripts/collection-controller.js) — 新增 `resumeTask()` 方法：
   - 从 localStorage 恢复 `collectionConfig` 和进度状态
   - 跳过已完成的手势/试次
   - 从断点位置继续采集

**后端改动**：
1. [realtimeEngine.js](realtimeEngine.js) — 新增 `onResumeCollection()` 处理：
   - 创建新 segment H5（`_segN` 后缀）
   - 传递 `resume_from_manifest_id` 关联到上一段
2. [storage_server.py](storage_server.py) — `create_file()` 接受：
   - `resume_manifest_id` / `parent_segment_h5`
   - 新 H5 的 `segment_index = parent.segment_index + 1`

### Phase 3：segment/bin 元数据写入

**目标**：每个 segment H5 记录完整的设备/bin 信息，manifest 串联所有 segment

**改动**：
1. [realtimeEngine.js](realtimeEngine.js) — 每次 `collection_start` 时检查是否有中断状态，自动初始化 manifest
2. [storage_server.py](storage_server.py) — 每个 H5 attrs 新增：
   - `collection_status`
   - `segment_index`
   - `resume_manifest_id`
   - `frame_id_start_dev1/2`, `frame_id_end_dev1/2`
   - `sd_bin_dev1/2`（已有，确认中断后也能正确记录）

### Phase 4：hdf5_tool 展示与同步工具适配

**目标**：hdf5_tool 能展示中断/续采状态，bin_sync_tool 支持 segment 批量同步

**改动**：
1. [hdf5_tool.py](tools/hdf5_tool.py) — 统计面板新增显示：
   - `collection_status`（completed / manual_stopped / abnormal_interrupted / resumed / abandoned）
   - `segment_index` / `resume_manifest_id`
   - 文件颜色标记（红色=中断，黄色=续采中，绿色=完成）
2. [bin_sync_tool.py](tools/bin_sync_tool.py) — 可选改动：
   - 支持根据 manifest 批量同步所有 segment
   - 多 segment 输出合并视图

---

## 9. Phase 1 最小代码改动范围

### 文件清单（优先级从高到低）

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `public/scripts/collection-controller.js` | **核心** | 新增 `abortTask()`, 保存中断状态到 localStorage |
| `public/index.html` | UI | 新增"异常中断"按钮 HTML + CSS |
| `realtimeEngine.js` | 后端 | 新增 `onAbnormalInterrupt()` action handler |
| `storage_server.py` | 后端 | `close_file()` 接受扩展参数写入 attrs |
| `public/scripts/page-switch.js` | 流程 | 离开采集页时检查中断状态 |

### 不修改的文件（Phase 1）
- ❌ `ble_server.py` — 不涉及
- ❌ `ble_server_sim_v2.py` — 不涉及
- ❌ `bin_sync_tool.py` — Phase 4 才考虑
- ❌ `hdf5_tool.py` — Phase 4 才考虑
- ❌ `deviceSync.js` — 不涉及
- ❌ `waveform-renderer.js` — 不涉及

### localStorage 新增 key

| Key | 内容 | 写入时机 |
|-----|------|---------|
| `emg_breakpoint_state` | `{collectionConfig, progress, interruptedAt, reason}` | 点击"异常中断"时 |
| `emg_breakpoint_exists` | `true/false` | 中断时设 true，恢复/清除时设 false |

### H5 新增 attrs

| 属性 | 类型 | 示例值 |
|------|------|--------|
| `collection_status` | string | `"completed"` / `"manual_stopped"` / `"abnormal_interrupted"` / `"resumed"` / `"abandoned"` |
| `interrupted_at` | ISO time | `"2026-05-31T14:20:00"`（仅 abnormal_interrupted） |
| `interrupt_reason` | string | `"设备掉电"` / `"掉包严重"` / `"双设备不同步"`（仅 abnormal_interrupted） |
| `segment_index` | int | `1` (第一个 segment) |
| `resume_progress` | JSON string | `{"session_index":0,"stage_index":1,"gesture_index":2,...}`（仅 abnormal_interrupted） |

---

## 10. 需要主管审批后才能写代码的点

以下决策点需要主管确认后再进入 Phase 1 编码：

### 审批点 1：方案选择
- [ ] 确认采用**方案 B（segment H5 + manifest）** 而不是续写同一个 H5
- [ ] 确认接受"一个 Stage 可能产生多个 H5 文件"的后果

### 审批点 2：异常中断按钮位置和样式
- [ ] 确认按钮放在采集页控制区（与"停止"按钮并列）
- [ ] 确认按钮颜色/图标（建议橙红色，区别于红色的"停止"）
- [ ] 确认按钮文案："异常中断"还是"紧急中断"还是"标记异常"

### 审批点 3：中断后的流程
- [ ] 确认中断后是否需要弹窗让工作人员选择中断原因（掉电/掉包/不同步/其他）
- [ ] 确认中断后是否自动返回首页
- [ ] 确认"断点续采"按钮放在首页什么位置（建议放在"采集"按钮旁边）

### 审批点 4：续采恢复粒度
- [ ] 确认续采从哪个层级恢复：Session → Stage → Gesture → Repeat/Trial
- [ ] 如果中断在乱序模式的中间，是否支持恢复（实现复杂度较高）
- [ ] 如果更换了设备，是否允许继续（需要新 bin 文件前缀）

### 审批点 5：manifest 存储位置
- [ ] 确认 manifest 只存 localStorage（重启后可能丢失，但可重建）
- [ ] 还是同时写文件系统（`storage/{userId}/_manifests/`，需要新增文件写入能力）
- [ ] 建议：Phase 1 只存 localStorage + H5 attrs，Phase 3 再加文件系统备份

### 审批点 6：测试模式下的中断
- [ ] 确认测试模式（`isTestMode=true`，不创建 H5）是否需要支持中断/续采
- [ ] 建议：Phase 1 只在正式采集模式下支持

---

## 附录 A：关键文件索引

| 文件 | 路径 | 核心职责 |
|------|------|---------|
| 采集控制器 | [collection-controller.js](public/scripts/collection-controller.js) | Session/Stage/Gesture 流转，Prompt 发送 |
| 采集选择器 | [collection-selector.js](public/scripts/collection-selector.js) | 分步选择，配置保存 |
| 页面切换 | [page-switch.js](public/scripts/page-switch.js) | 页面路由，用户管理 |
| 实时引擎 | [realtimeEngine.js](realtimeEngine.js) | WS 中继，BLE 数据分发，H5 生命周期 |
| 存储服务 | [storage_server.py](storage_server.py) | HDF5 创建/写入/关闭 |
| 设备同步 | [deviceSync.js](deviceSync.js) | Python 进程管理 |
| 前端页面 | [index.html](public/index.html) | 完整 UI（欢迎页 + 采集页 + 后台页） |
| 同步工具 | [bin_sync_tool.py](tools/bin_sync_tool.py) | 250Hz → 2kHz 同步 |
| H5 工具 | [hdf5_tool.py](tools/hdf5_tool.py) | H5 查看 + 批量同步 |

## 附录 B：中断/恢复时序图

```
正常采集 → 异常中断 → 恢复采集

[采集页]                          [realtimeEngine]              [storage_server]
   |                                    |                            |
   |-- collection_start ──────────────→ |                            |
   |-- stage_start ───────────────────→ |-- create ────────────────→ | 创建 H5#1
   |                                    |                            |
   |  BLE 数据流...                     |-- append ────────────────→ | 写入 H5#1
   |                                    |                            |
   |== 工作人员点击"异常中断" ========== |                            |
   |                                    |                            |
   |-- abnormal_interrupt ────────────→ |                            |
   |   {reason, progress}               |-- close(collection_status= |
   |                                    |   "abnormal_interrupted",  |
   |                                    |    interrupted_at,          |
   |                                    |    resume_progress) ──────→ | 关闭 H5#1
   |                                    |                            | 写入 attrs
   |  保存到 localStorage:              |                            |
   |    emg_breakpoint_state            |                            |
   |    emg_breakpoint_exists=true      |                            |
   |                                    |                            |
   |== 返回首页 ====================================================|
   |                                    |                            |
[首页] 显示"断点续采"按钮               |                            |
   |                                    |                            |
   |== 工作人员点击"断点续采" ========== |                            |
   |                                    |                            |
   |-- 恢复 collectionConfig + progress |                            |
   |                                    |                            |
   |-- collection_start ──────────────→ |                            |
   |   {is_resume: true,                |                            |
   |    manifest_id: "..."}             |                            |
   |                                    |                            |
   |-- stage_start ───────────────────→ |-- create(segment_index=2,  |
   |                                    |    resume_from=H5#1) ────→ | 创建 H5#2
   |                                    |                            |
   |  从断点继续采集...                  |-- append ────────────────→ | 写入 H5#2
   |                                    |                            |
   |-- stage_end ─────────────────────→ |-- close(completed) ──────→ | 关闭 H5#2
   |                                    |                            |
   |  清除 localStorage:                |                            |
   |    emg_breakpoint_state = null     |                            |
   |    emg_breakpoint_exists = false   |                            |
```

---

## 11. Phase 1 实现结果（2026-05-31）

### 11.1 修改文件列表

| 文件 | 改动行数 | 说明 |
|------|---------|------|
| [storage_server.py](storage_server.py) | +40 行 | `close_file()` 接受扩展参数，写入 `collection_status`、`interrupted_at`、`interrupt_reason`、`resume_progress`、`segment_index` 到 H5 attrs |
| [realtimeEngine.js](realtimeEngine.js) | +40 行 | 新增 `abnormal_interrupt` action 分发 + `onAbnormalInterrupt()` 方法；`closeStageFile()` 接受 `extraParams` |
| [collection-controller.js](public/scripts/collection-controller.js) | +220 行 | 新增 `abortTask(reason)`、`_showAbortReasonDialog(callback)`、`_executeAbort(reason)`；`updateControlButtons()` 处理 abort 按钮状态 |
| [index.html](public/index.html) | +10 行 | 新增"异常中断"按钮 HTML + CSS（橙红色 `#f97316`，位于停止按钮旁） |
| [page-switch.js](public/scripts/page-switch.js) | +30 行 | `showWelcome()` 调用 `_checkBreakpointState()` 检测断点状态并输出 console 日志 |

### 11.2 异常中断按钮

- **位置**：[index.html:5471](public/index.html) 采集页控制区，停止按钮右侧
- **颜色**：橙红色 (`background: #f97316`)，hover 时 `#ea580c`
- **图标**：⚠️ `fa-exclamation-triangle`
- **状态规则**：
  - 未采集时：`disabled`
  - 采集进行中（`_isRunning && !_isTestMode`）：`enabled`
  - 测试模式（`_isTestMode`）：`disabled`（点击时 toast 提示"测试模式无需保存断点"）

### 11.3 异常中断流程

```
点击"异常中断" → abortTask()
  ├─ ✓ 检查 _isRunning === true 且 !_isTestMode
  ├─ 弹出原因选择对话框（设备没电/丢包严重/双设备不同步/信号异常/其他）
  ├─ 用户选择原因 → _executeAbort(reason)
  │   ├─ 采集进度快照 → localStorage
  │   │    key: emg_breakpoint_state (version:1, 完整序列化)
  │   │    key: emg_breakpoint_exists = "true"
  │   ├─ WS → realtimeEngine: abnormal_interrupt {reason, interruptedAt, progress}
  │   │    └─ onAbnormalInterrupt() → closeStageFile({collection_status: "abnormal_interrupted", ...})
  │   │         └─ storage_server.close_file(params) → H5 attrs 写入
  │   └─ 前端清理（动画/定时器/空格监听/GIF），_isRunning=false
  └─ 取消 → 不做任何事
```

### 11.4 localStorage Schema

**Key: `emg_breakpoint_state`**

```json
{
  "version": 1,
  "status": "abnormal_interrupted",
  "interruptedAt": "2026-05-31T14:20:00.000Z",
  "interruptReason": "设备没电",
  "collectionConfig": { /* emg_current_collection_config 完整内容 */ },
  "currentTaskId": "discrete_gesture",
  "currentSessionIndex": 0,
  "currentStageIndex": 1,
  "currentGestureIndex": 3,
  "gestureRepeatCount": 2,
  "continualTrialCount": 0,
  "currentPhase": "gesture",
  "_shuffleMode": false,
  "isAllSessionsMode": true,
  "sessionCount": 3,
  "gesturesSnapshot": [
    { "id": "pinch", "name": "捏合", "icon": "🤏", "gifFile": null,
      "_shuffled": false, "_shuffleSegment": null,
      "_baseGestureIndex": null, "_repeatIndex": null, "_repeatTotal": null }
  ],
  "recordingSessionId": "rec_20260531_141000_3",
  "stages": [{"id":"palm_up","name":"手心朝上"}, {"id":"palm_inward","name":"手心朝内"}]
}
```

**Key: `emg_breakpoint_exists`** — 字符串 `"true"` 或不存在。

### 11.5 H5 Attrs（异常中断时写入）

| 属性 | 值 | 写入条件 |
|------|-----|---------|
| `collection_status` | `"abnormal_interrupted"` | 中断时强制写入 |
| `interrupted_at` | `"2026-05-31T14:20:00.000Z"` | 中断时写入 |
| `interrupt_reason` | `"设备没电"` | 中断时写入 |
| `resume_progress` | JSON 字符串（进度快照） | 中断时写入 |
| `segment_index` | `1` | 始终写入，默认 1 |
| `sync_status` | `"pending"`（不变） | 不受影响，只由 bin_sync_tool 修改 |

正常关闭时 — 根据前端命令区分状态：

| 触发条件 | `collection_status` |
|----------|---------------------|
| Stage 正常完成（`collection_stop({completed: true})`） | `"completed"` |
| 手动点击停止（`collection_stop({completed: false})`） | `"manual_stopped"` |
| 异常中断（`abnormal_interrupt`） | `"abnormal_interrupted"` |
| 兼容旧调用（未传 collection_status） | `"completed"`（默认） |

关键区别：
- `"manual_stopped"` 不生成 localStorage breakpoint → 不可续采
- `"abnormal_interrupted"` 生成 localStorage breakpoint → 可续采

### 11.6 验证结果

| 验证项 | 状态 |
|--------|------|
| `node --check collection-controller.js` | ✅ 通过 |
| `node --check page-switch.js` | ✅ 通过 |
| `node --check realtimeEngine.js` | ✅ 通过 |
| `python -m py_compile storage_server.py` | ✅ 通过 |
| localStorage schema 结构验证 | ✅ ~798 bytes，可序列化 |
| 正常完成 `collection_stop(true)` → `"completed"` | ✅ `onCollectionStop(true)` 显式传 `collection_status: "completed"` |
| 手动停止 `collection_stop(false)` → `"manual_stopped"` | ✅ `onCollectionStop(false)` 显式传 `collection_status: "manual_stopped"` |
| 异常中断 `abnormal_interrupt` → `"abnormal_interrupted"` | ✅ `onAbnormalInterrupt()` 保持不变 |
| `sync_status` 不受影响 | ✅ 始终 `"pending"`，只由 `bin_sync_tool` 修改 |
| 测试模式不支持中断 | ✅ 按钮 disabled + toast 提示 |
| 中断后不覆盖 breakpoint | ✅ `backToWelcome()` 不清 localStorage |
| `manual_stopped` 不生成 breakpoint | ✅ 只有 `abortTask()` 写 localStorage |

### 11.7 Phase 1 未实现内容

以下内容在 Phase 2/3/4 实现：

- ❌ 首页"断点续采"按钮（Phase 2）
- ❌ 恢复采集执行 `resumeTask()`（Phase 2）
- ❌ 跳过已完成手势/试次（Phase 2）
- ❌ 新建 segment H5（`_segN` 后缀）（Phase 2）
- ❌ `resume_manifest_id` / `parent_segment_h5` 关联（Phase 2）
- ❌ Manifest JSON 文件落盘（Phase 3）
- ❌ hdf5_tool 展示 `collection_status` 标记（Phase 4）
- ❌ bin_sync_tool 批量 segment 同步（Phase 4）
