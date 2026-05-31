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

---

## 12. Phase 2 实现结果（2026-05-31）

### 12.1 修改文件列表

| 文件 | 改动行数 | 说明 |
|------|---------|------|
| [index.html](public/index.html) | +15 行 | 新增"断点续采"按钮 HTML + CSS（蓝绿色渐变，默认隐藏） |
| [page-switch.js](public/scripts/page-switch.js) | +80 行 | `_checkBreakpointState()` 控制按钮显隐；`resumeBreakpoint()` 确认弹窗 + 恢复流程 |
| [collection-controller.js](public/scripts/collection-controller.js) | +120 行 | `loadBreakpointState()` 恢复进度；`_clearBreakpointState()` 清理；startTask resume mode；_executeAbort 更新 segmentIndex |
| [realtimeEngine.js](realtimeEngine.js) | +20 行 | `onCollectionStart()` 保存 resume 字段；`openStageFile()` 传递 resume attrs 到 create 命令 |
| [storage_server.py](storage_server.py) | +20 行 | `create_file()` 接受并写入 `is_resumed`、`segment_index`、`resume_from_interrupted_at`、`resume_reason`、`resume_parent_recording_session_id` |

### 12.2 首页按钮显示逻辑

```
showWelcome()
  └─ _checkBreakpointState()
       ├─ emg_breakpoint_exists !== "true" → 隐藏按钮
       ├─ state 解析失败 / 缺少必要字段 → 隐藏按钮
       └─ state 有效 → 显示"断点续采"按钮
```

- **按钮样式**：蓝绿色渐变 (`#06b6d4` → `#0891b2`)，与其他按钮并列
- **点击确认弹窗**：显示中断时间、原因、任务名、session/stage/gesture 进度、腕带重连提醒
- **取消**：不做任何事
- **确认**：恢复配置 → 切换到采集页 → 调用 `loadBreakpointState()`

### 12.3 恢复流程

```
page-switch.resumeBreakpoint()
  ├─ 校验 state.version / collectionConfig / currentTaskId
  ├─ 确认弹窗（中断时间、原因、任务、进度）
  ├─ window.currentCollectionConfig = state.collectionConfig
  ├─ localStorage: emg_current_collection_config
  ├─ showCollection() → startWaveform()
  └─ collectionController.loadBreakpointState(state)
       ├─ 恢复 collectionConfig, currentTaskId
       ├─ 恢复 currentSessionIndex, currentStageIndex
       ├─ 恢复 currentGestureIndex, gestureRepeatCount, continualTrialCount
       ├─ 恢复 _shuffleMode, sessionCount, _isAllSessionsMode
       ├─ 恢复 stages, gestures（优先快照）
       ├─ 设置 _isResumeMode=true, _resumeState=state
       ├─ _resumeSegmentIndex = (state.segmentIndex || 1) + 1
       └─ 更新 UI（session/stage selector, gesture list, status）
```

### 12.4 startTask 在 resume mode 和 normal mode 的差异

| 行为 | Normal Mode | Resume Mode |
|------|------------|-------------|
| 重置 currentGestureIndex | ✅ → 0 | ❌ 保留 |
| 重置 gestureRepeatCount | ✅ → 0 | ❌ 保留 |
| 重置 continualTrialCount | ✅ → 0 | ❌ 保留 |
| 生成新 recordingSessionId | ✅ | ❌ 复用断点的 |
| collection_start.isResume | 不传 | `true` |
| collection_start.resumeSegmentIndex | 不传 | `N+1` |
| collection_start.resumeFromInterruptedAt | 不传 | ISO 时间 |
| collection_start.resumeReason | 不传 | 中断原因 |
| collection_start.resumeParentRecordingSessionId | 不传 | 父录像 ID |
| Stage 完成后清除 breakpoint | 不触发 | ✅ `_clearBreakpointState()` |

### 12.5 H5 新增 Attrs

| 属性 | 类型 | 何时写入 | 示例值 |
|------|------|---------|--------|
| `is_resumed` | bool | create_file（续采模式） | `true` / `false` |
| `segment_index` | int | create_file（始终写入） | `1`（普通）/ `2`（首次续采） |
| `resume_from_interrupted_at` | string | create_file（续采模式） | `"2026-05-31T14:20:00.000Z"` |
| `resume_reason` | string | create_file（续采模式） | `"设备没电"` |
| `resume_parent_recording_session_id` | string | create_file（续采模式） | `"rec_20260531_141000_3"` |

### 12.6 支持和不支持的恢复粒度

| 场景 | 恢复粒度 | 状态 |
|------|---------|------|
| 离散手势-普通顺序 | 恢复到 currentGestureIndex | ✅ 完整支持 |
| 离散手势-乱序模式 | 从 gesturesSnapshot 恢复完整序列 + currentGestureIndex | ✅ 支持 |
| 连续手势 (continual) | 恢复到 currentStageIndex | ⚠️ 仅恢复计数，动画从 trial 0 重新开始 |

**连续手势限制说明**：现有动画模块（`continualGesture1Animation`、`continualGesture2Animation`）不支持从指定 `trialIndex` 开始。Phase 2 恢复 `continualTrialCount` 用于记录，但动画实际从第 0 个 trial 重新开始。`loadBreakpointState()` 会 toast 提示此限制。

### 12.7 验证结果

| 验证项 | 状态 |
|--------|------|
| `node --check page-switch.js` | ✅ 通过 |
| `node --check collection-controller.js` | ✅ 通过 |
| `node --check realtimeEngine.js` | ✅ 通过 |
| `python -m py_compile storage_server.py` | ✅ 通过 |
| emg_breakpoint_exists=true 时按钮显示 | ✅ `_checkBreakpointState()` 控制 |
| state 无效时按钮隐藏 | ✅ 缺字段/解析失败 → 隐藏 |
| loadBreakpointState 恢复 session/stage/gesture | ✅ 数据流模拟通过 |
| 续采 payload 带 isResume/resumeSegmentIndex | ✅ 数据流模拟通过 |
| 普通采集不带 resume 字段 | ✅ `isResume: false`, `segment_index: 1` |
| 新 H5 含 resume attrs | ✅ `is_resumed`, `segment_index`, `resume_from_interrupted_at`, `resume_reason`, `resume_parent_recording_session_id` |
| 普通采集不破坏断点 | ✅ 不清 localStorage，不传 resume 字段 |
| resumed Stage 完成后清除断点 | ✅ `_clearBreakpointState()` 在 completion 中调用 |
| 重新中断更新 segmentIndex | ✅ `_executeAbort` 写当前 `_resumeSegmentIndex` |
| 普通 stopTask 不生成/清除 breakpoint | ✅ `manual_stopped` 流程不变 |

### 12.8 Phase 3/4 待办

- ❌ Manifest JSON 文件落盘（Phase 3）
- ❌ segment 列表维护与合并视图（Phase 3）
- ❌ hdf5_tool 展示 `is_resumed` / `segment_index` / `collection_status`（Phase 4）
- ❌ bin_sync_tool 批量 segment 同步（Phase 4）
- ❌ 连续手势从指定 trialIndex 恢复（动画模块需改造）

---

## 13. Phase 2 审查修复（2026-05-31）

### Bug 1：续采乱序序列被 startTask() 覆盖

**根因**：`startTask()` 无条件调用 `loadCollectionConfig()`，内部会 `loadGesturesForCurrentStage()`，把 `loadBreakpointState()` 恢复的 `this.gestures`（快照）重建为正常顺序。

**修复**：
- 新增 `_reloadExecutionParams()` 轻量方法，仅刷新 `executionParams` / `currentExecutionParams`，不触碰 `gestures`、`stages`、`sessionCount` 等断点状态。
- `startTask()` 中判断 `this._isResumeMode`：续采模式调 `_reloadExecutionParams()`，普通模式调 `loadCollectionConfig()`。
- 文件：[collection-controller.js](public/scripts/collection-controller.js) — `_reloadExecutionParams()` + `startTask()` guard。

**验证**：resume mode 下 `gestures` 保留快照顺序（`ordered,ordered,shuffled,shuffled,shuffled`），`executionParams` 正确加载。

### Bug 2：resumed H5 的 segment_index 被 close_file 覆盖回 1

**根因**：`storage_server.close_file()` 写 `segment_index = params.get("segment_index", 1)` — 默认值 1 覆盖 `create_file` 写入的 2+。

**修复**：
- `close_file()` 改为仅当 `"segment_index" in params` 时才写入，否则保留已有 attrs。
- `onAbnormalInterrupt()` 不再传 `segment_index: 1`，交由 `create_file` 的值保持。
- 文件：[storage_server.py](storage_server.py) — `close_file()` guard；[realtimeEngine.js](realtimeEngine.js) — `onAbnormalInterrupt()` 移除 hardcoded segment_index。

**验证**：
- `close(completed)` 不传 segment_index → H5 保持 2 ✅
- `close(abnormal_interrupt)` 显式传 segment_index=3 → H5 更新为 3 ✅

### Bug 3：continual_gesture_3 映射缺失

**根因**：`page-switch.js` `resumeBreakpoint()` 的 ternary chain 和 `collection-controller.js` `selectTask()` 的 `taskIdMap` 都没有 `continual_gesture_3` / `continuous3` 映射。

**修复**：
- `page-switch.js` ternary chain 增加 `continual_gesture_3 → continuous3`
- `collection-controller.js` `taskIdMap` 增加 `continuous3: continual_gesture_3`
- 文件：[page-switch.js](public/scripts/page-switch.js) — `resumeBreakpoint()` ternary；[collection-controller.js](public/scripts/collection-controller.js) — `selectTask()` taskIdMap。

**验证**：`continual_gesture_3 → continuous3 → continual_gesture_3` 往返映射正确 ✅

### Bug 4（Phase 2 审查第 3 轮）：startTask resume mode 的 execution 完整性检查用错字段

**根因**：Bug 1 修复中，resume mode fallback 检查用 `!this.currentExecutionParams.trialsPerStage` 判断参数是否完整，但离散手势没有 `trialsPerStage` 字段（只有 `repeatPerGesture`），因此离散断点续采会误判"参数不完整"并调用 `loadCollectionConfig()`，再次覆盖 `gesturesSnapshot`。

**修复**：
- 新增 `_hasValidExecutionParamsForTask(taskId)` — 按任务类型校验：
  - `discrete_gesture`：检查 `repeatPerGesture`、`gestureDisplayTime`、`preparationTime` 是否都是 number
  - `continual_gesture_*`：检查 `trialsPerStage`、`preparationTime` 是否都是 number
- 新增 `_applyExecutionDefaultsForTask(taskId)` — 参数缺失时用默认值兜底，**不调用 `loadCollectionConfig()`**，不触碰 `this.gestures`
- `startTask()` resume mode 用新 helper 替代原来的 `trialsPerStage` 检查
- 文件：[collection-controller.js](public/scripts/collection-controller.js) — `_hasValidExecutionParamsForTask()` + `_applyExecutionDefaultsForTask()` + `startTask()` guard

**验证**：
- 离散 resume state（只有 `repeatPerGesture`，无 `trialsPerStage`）→ 旧检查 `!p.trialsPerStage = true`（误触发）→ 新检查 `true`（✅ 不触发 fallback）
- 连续 resume state（有 `trialsPerStage`）→ 通过 ✅
- 兜底路径不碰 `this.gestures` ✅
- `node --check collection-controller.js` ✅

---

## 14. Phase 2 UX 修复（2026-05-31）

### 策略变更：从"暂停动画+恢复"改为"非侵入式遮挡"

**审查风险**：上一版 UX 修复在弹窗打开时 stop 动画和清 timer，取消弹窗后用 `startNextGesture()` / `startContinualAnimation()` 恢复，可能重复发送 prompt、重启当前手势/连续动画，污染 H5 数据。

**修订策略**：弹窗期间后台采集照常运行，只做 UI 遮挡。不停止任何业务 timer 或动画模块。取消弹窗仅关闭 overlay + 恢复状态文案，不重新进入任何采集阶段。

### 修复 1：弹窗采用非侵入式遮挡

- 移除 `_pauseForAbortDialog()` 和 `_resumeFromAbortDialog()` 方法。
- `abortTask()` 弹窗回调改为：
  - 确认 → `_executeAbort(reason)`（仅在此处停止动画和清理 timer）
  - 取消 → `updateStatus('采集中')`（无其他操作）
- 弹窗 overlay 背景改为 `rgba(15,23,42,0.88)` 接近不透明，遮挡后台动画。
- 弹窗内容增加黄色提示框："采集仍在后台进行中 / 如确认异常中断将保存断点并返回首页 / 如取消将关闭本窗口继续当前采集"。
- **关键保证**：取消弹窗后不调 `startNextGesture()`、`startContinualAnimation()`、`showRestPeriod()`、`showPreparation()`，不重复 prompt，不污染 H5。

### 修复 2：确认中断后自动返回首页（不变）

`_executeAbort()` 末尾 `setTimeout 400ms → pageSwitchController.showWelcome()`。

### 修复 3：续采准备态 UI（不变）

`loadBreakpointState()` → `_updateResumeReadyUI()`；按钮状态矩阵；`exitResumeMode()` / `_confirmAbandonBreakpoint()`。

### resumeBreakpoint 中 BleControl.startAll()

- `page-switch.js` `resumeBreakpoint()` 中 `BleControl.startAll()` 仅启动 BLE 数据流（腕带 streaming），不启动 H5 记录。
- H5 记录在用户点击"开始续采"后由 `startTask()` 触发。
- 代码注释和日志已标注"仅 streaming，未开始 H5 记录"。

### 按钮状态矩阵（不变）

| 模式 | startTaskBtn | startAllBtn | testBtn | abortBtn |
|------|-------------|------------|---------|----------|
| 普通-就绪 | "开始采集（单轮）" enabled | enabled | enabled | "异常中断" disabled |
| 普通-采集中 | disabled | disabled | disabled | "异常中断" enabled |
| 续采-就绪 | **"开始续采"** enabled | disabled | disabled | **"放弃断点"** enabled |
| 续采-采集中 | disabled | disabled | disabled | "异常中断" enabled |

### 验证结果

```
node --check collection-controller.js ✅
node --check page-switch.js           ✅
弹窗 overlay 接近不透明 (0.88)         ✅
取消弹窗后不重新进入采集阶段            ✅
取消弹窗后不重复 prompt                ✅
确认中断后才停止动画和清理              ✅
确认中断后自动回首页                    ✅
续采准备态按钮→"开始续采"              ✅
放弃断点→清除 breakpoint→回首页        ✅
resumed Stage 完成后 UI 恢复           ✅
```

---

## 15. 2026-05-31 bugfix: abort freeze and resume index

### 问题

1. **断点时机错误**：点击"异常中断"按钮后，原因选择弹窗打开期间后台采集动画和 prompt 仍在继续推动。断点进度以"选完原因那一刻"为准，而不是"点击按钮那一刻"，导致 prompt 可能从 5 推进到 6、7。
2. **续采索引回退到 0**：点击"断点续采"→"开始续采"后，动画和进度显示 0/72，而不是从断点保存的手势索引继续。

### 修复方案

#### Bug A：点击中断瞬间冻结

**策略变更**：从"非侵入式遮挡"改为"点击即冻结 + 不可取消"。

**collection-controller.js** `abortTask()`：
- 点击按钮后**立即**创建 `_pendingAbortSnapshot`（包含 progress、gesturesSnapshot、interruptedAt、segmentIndex）
- 立即停止所有业务推进：`_isRunning = false`、清除所有 timer（countdownTimer/phaseTimer/continualProgressTimer/calibrationTimer）、stop 所有动画（discreteGestureAnimation/continualGesture1/continualGesture2）、disable 空格键、更新按钮状态
- **不发送** `collection_stop`（避免写入 `manual_stopped`）
- 弹出原因选择对话框（**不可取消**：无取消按钮，无遮罩关闭）

**collection-controller.js** `_executeAbort(reason)`：
- 优先使用 `_pendingAbortSnapshot` 构建 breakpointState 和 abnormal_interrupt payload
- 不在 `_executeAbort` 中重复读取 `this.currentGestureIndex`（此时已被 freeze 清零）
- 完成后清空 `_pendingAbortSnapshot`

**collection-controller.js** `_showAbortReasonDialog()`：
- 移除取消按钮和遮罩关闭事件
- 文案改为"采集已冻结，请选择中断原因"
- overlay 背景 `rgba(15,23,42,0.92)`

#### Bug B：续采从断点索引恢复

**collection-controller.js** `startDiscreteGestureCollection()`：
- 续采乱序模式下计算 `startIndex = this.currentGestureIndex`
- 传递给 `startShuffleModeAnimation({ startIndex })`

**collection-controller.js** `startShuffleModeAnimation()`：
- 接受 `startIndex` 参数
- GIF 显示 `gestures[startIndex]` 而非 `gestures[0]`
- 将 `startIndex` 传给 `discreteGestureAnimation.startShuffleMode()`

**discrete-gesture-animation.js** `startShuffleMode()`：
- 新增 `startIndex` 参数（默认 0）
- `executedCount = safeStartIndex`（跳过已执行的手势）
- `nextPromptIndex = safeStartIndex`（后续 prompt 从正确位置创建）
- `promptLibrary[gestureId].originalIndex = index`（全局索引映射）
- `createInitialShufflePrompts()` 从 `nextPromptIndex` 位置开始创建 prompt
- 使用全局索引 `seqIdx` 创建 prompt，保证 `triggerPrompt` 回调的 index 正确映射到 `gestures` 数组

### 关键保证

| 验证项 | 状态 |
|--------|------|
| 点击中断瞬间 gestureIndex 被快照保存 | ✅ |
| 弹窗期间不再产生新 prompt | ✅ |
| 弹窗不可取消 | ✅ |
| localStorage breakpointState 使用快照 | ✅ |
| 续采 prepare UI 显示 18/72 而非 0/72 | ✅ |
| startShuffleMovie 从 startIndex 构建 promptSequence | ✅ |
| 第一个 prompt 回调 index = safeStartIndex | ✅ |
| GIF 显示 gestures[startIndex] | ✅ |
| 普通模式 startIndex=0 不受影响 | ✅ |
| abnormal_interrupt H5 attrs 正确 | ✅ |
| manual_stopped/collection_stop 不被异常中断触发 | ✅ |

### 剩余限制

- **普通顺序离散手势**（非乱序模式）：`startNextGesture()` 已通过 `currentGestureIndex` 自动从断点位置开始（`startTask()` 不重置该值），无需额外改动。
- **连续手势**：`continualTrialCount` 恢复仅做计数记录，动画仍从 trial 0 开始。完整支持需动画模块改造（Phase 3+）。

---

## 16. Phase 3: segment/bin 元数据写入（2026-05-31）

### 16.1 修改文件列表

| 文件 | 改动 | 说明 |
|------|------|------|
| [storage_server.py](storage_server.py) | +80 行 | `close_file()` 新增 `_write_segment_metadata()`；`create_file()` 新增 `start_time`、`stage_index`、`interruption_id`、`resumed_by_segment_index`、`resumed_by_file`、`collection_session_id`、`parent_segment_index` |
| [realtimeEngine.js](realtimeEngine.js) | +3 行 | `onCollectionStart()` 提取 `resumeParentSegmentIndex`；`openStageFile()` 传递 `parent_segment_index` |
| [collection-controller.js](public/scripts/collection-controller.js) | +2 行 | `startPayload` 新增 `resumeParentSegmentIndex` |

### 16.2 新增 H5 Attrs 总览

#### create_file 阶段写入

| 属性 | 类型 | 何时有效 | 说明 |
|------|------|---------|------|
| `start_time` | float | 始终 | Stage 开始时间戳（从 realtimeEngine 传入） |
| `stage_index` | int | 始终 | 当前 stage 序号 |
| `collection_status` | string | 始终 | create 时写 `"running"`，close 时覆盖 |
| `collection_session_id` | string | 有 recording_session_id 时 | 同 recording_session_id，跨 segment 关联 |
| `interruption_id` | string | 始终 | `int_{session_id}_seg{N}_{timestamp}`，唯一标识 |
| `resumed_by_segment_index` | int | 始终 | 占位 `-1`，续采后由后续 segment 补填 |
| `resumed_by_file` | string | 始终 | 占位 `""`，续采后由后续 segment 补填 |
| `parent_segment_index` | int | is_resumed 时 | 父 segment 序号（从 `resumeParentSegmentIndex` 传入） |

#### close_file 阶段写入

| 属性 | 类型 | 说明 |
|------|------|------|
| `end_time` | float | Stage 结束时间戳 |
| `emg1_frame_count` | int | 250Hz 数据集帧数 |
| `emg1_frame_id_min` | int | 最小 BLE frame_id（无数据时 -1） |
| `emg1_frame_id_max` | int | 最大 BLE frame_id（无数据时 -1） |
| `emg1_time_min` | float | 最早时间戳（无数据时 -1.0） |
| `emg1_time_max` | float | 最晚时间戳（无数据时 -1.0） |
| `emg2_frame_count` / `emg2_frame_id_min` / `emg2_frame_id_max` / `emg2_time_min` / `emg2_time_max` | 同上 | 同上 |
| `segment_has_dev1_bin` | bool | 设备 1 是否有 bin 文件 |
| `segment_has_dev2_bin` | bool | 设备 2 是否有 bin 文件 |
| `segment_device_count` | int | 有数据的设备数（frame_count > 0） |
| `segment_bin_summary` | JSON string | 包含 dev1/dev2 的 frame range、bin 名称、BLE 设备名 |
| `interruption_id` | string | 仅在 `abnormal_interrupted` 的 close 中补充生成 |

Frame range 计算基于 H5 中实际的 `emg1_250hz_adc` / `emg2_250hz_adc` dataset，使用 `np.min` / `np.max` 提取 `frame_id` 和 `time` 字段。

### 16.3 三种状态的字段差异

| 场景 | `collection_status` | `interruption_id` | `resume_*` attrs | frame range |
|------|---------------------|-------------------|-----------------|-------------|
| **completed**（正常完成） | `"completed"` | create 时生成（占位） | 无 | ✅ 完整 |
| **manual_stopped**（手动停止） | `"manual_stopped"` | create 时生成（占位） | 无 | ✅ 完整 |
| **abnormal_interrupted**（异常中断） | `"abnormal_interrupted"` | create 时生成 + close 确认 | `interrupted_at`、`interrupt_reason`、`resume_progress` | ✅ 完整 |
| **is_resumed=true**（续采 segment） | `"completed"` / `"manual_stopped"` / `"abnormal_interrupted"` | create 时生成 | `is_resumed`、`segment_index`、`resume_from_interrupted_at`、`resume_reason`、`resume_parent_recording_session_id`、`parent_segment_index` | ✅ 完整 |

### 16.4 segment_bin_summary 示例

```json
{
  "device_count": 2,
  "devices": {
    "dev1": {
      "frame_id_min": 0,
      "frame_id_max": 1498,
      "frame_count": 750,
      "time_min": 1000.0,
      "time_max": 1002.996,
      "sd_bin": "S001_L_260531_141000",
      "ble_device": "WristBand_3A76"
    },
    "dev2": {
      "frame_id_min": 0,
      "frame_id_max": 1498,
      "frame_count": 750,
      "time_min": 1000.0,
      "time_max": 1002.996,
      "sd_bin": "S001_R_260531_141000",
      "ble_device": "WristBand_5B12"
    }
  }
}
```

### 16.5 已知限制

- **MAC 地址**：`ble_server.py` 当前不向 realtimeEngine 传递 BLE MAC 地址，因此 `ble_mac_dev1`/`ble_mac_dev2` 暂缺。H5 中已有 `ble_device_dev1`/`ble_device_dev2`（设备名称），bin_sync_tool 通过 `sd_bin_dev1`/`sd_bin_dev2`（bin 文件前缀）已经能正确匹配。
- **IMU/mocap frame range**：当前只统计 EMG 250Hz 的 frame range。IMU 和 mocap 的 frame range 可在 Phase 4 按需补充。
- **resumed_by_file**：占位字段，Phase 4 实现跨 segment 关联时填入。

### 16.6 验证结果

```
node --check realtimeEngine.js          ✅
node --check collection-controller.js   ✅
python -m py_compile storage_server.py  ✅
frame range 计算逻辑                    ✅ (750 frames, frame_id [0, 1498])
segment_bin_summary JSON 格式           ✅
empty dataset → -1 兜底                ✅
interruption_id 生成                    ✅
普通采集不受影响                        ✅
```

### 16.7 Phase 4: hdf5_tool 展示 + bin_sync_tool 审计（2026-05-31）

#### 修改文件

| 文件 | 改动 | 说明 |
|------|------|------|
| [hdf5_tool.py](tools/hdf5_tool.py) | +120 行 | 新增 `extract_segment_metadata()` 纯函数；`StatisticsPanel` 新增 17 个 Phase 3 字段展示；增加 `json` / `datetime` import；面板高度从 460 增至 700 |
| [bin_sync_tool.py](tools/bin_sync_tool.py) | +5 行 | 同步前检查 `collection_status`，`abnormal_interrupted` 时日志提示，不阻止同步 |

#### hdf5_tool 新增展示内容

**StatisticsPanel 新增字段**：
- `collection_status`（红色=abnormal_interrupted，橙色=manual_stopped，绿色=completed）
- `is_resumed`（紫色="是"）
- `segment_index`（>1 时紫色）
- `collection_session_id`、`parent_segment_index`
- `start_time` / `end_time` / `duration`
- `session_number`、`stage_index`
- `emg1/emg2_frame_count`、`emg1/emg2_frame_range`（`[min, max]`）
- `segment_has_dev1/dev2_bin`（✅/❌）
- `segment_device_count`

**新增纯函数** `extract_segment_metadata(h5_f) -> dict`：
- 返回完整 metadata dict，包含上述所有字段
- 自动解析 `segment_bin_summary` JSON
- 自动解析 `resume_progress` JSON（abnormal 时）
- 旧 H5 缺字段返回 `None` 或 `'-'`，不会崩溃

#### bin_sync_tool 审计结论

- `sync_h5_with_bin()` 按单个 H5 的 `sd_bin_dev1`/`sd_bin_dev2` 独立查找 bin 文件 ✅
- `SyncWorker._find_bin_files()` 从 H5 attrs 读取 bin 前缀，不依赖全局状态 ✅
- 每个 segment H5 自包含 bin 信息，逐文件同步安全 ✅
- **最小适配**：同步前检查 `collection_status`，`abnormal_interrupted` 时日志提示"仅同步已采集的有效前半段"，不阻止同步 ✅
- **不需要修改核心同步算法**

#### 异常中断 segment 同步策略

- **允许同步**：frame_id 校验通过即可同步，即使 `collection_status == abnormal_interrupted`
- **日志提示**：警告工作人员这是中断 segment，同步的只是前半段有效数据
- **不阻止**：除非 frame_id 校验失败（gap/duplicate），否则正常同步

#### 旧 H5 兼容

- 所有新增展示字段通过 `.get()` 读取，缺字段显示 `'-'`
- `extract_segment_metadata()` 对所有 attrs 做 None 安全处理
- 旧 H5 不会崩溃

#### 验证结果

```
python -m py_compile hdf5_tool.py      ✅
python -m py_compile bin_sync_tool.py  ✅
extract_segment_metadata 正常           ✅
  collection_status: abnormal_interrupted
  duration: 3.5s
  emg1_range: {frame_count:750, frame_id_min:0, frame_id_max:1498}
  emg2_range: {frame_count:0} (empty)
  abnormal_detail progress_parsed: {currentGestureIndex:18}
  bin_summary_parsed: {device_count:1}
```

---

## 17. Phase 5: segment 链路 + 父 H5 反链 + 重启策略（2026-05-31）

### 17.1 修改文件

| 文件 | 改动 | 说明 |
|------|------|------|
| [hdf5_tool.py](tools/hdf5_tool.py) | +100 行 | 新增 `scan_segment_chain()`、`format_segment_chain_summary()`；`StatisticsPanel` 新增 segment 链路 QTextEdit；面板高度 700→840 |
| [storage_server.py](storage_server.py) | +45 行 | 新增 `_maybe_update_parent_segment_link()`；`create_file()` 成功后补填父 H5 的 `resumed_by_segment_index`/`resumed_by_file`；新增 `current_recording_session_id` 实例变量 |

### 17.2 hdf5_tool 新增链路展示

**`scan_segment_chain(current_h5_path) -> list[dict]`**：
- 读取当前 H5 的 `collection_session_id`
- 扫描同目录下所有 `.h5`，找出相同 session_id 的文件
- 对每个文件调用 `extract_segment_metadata()` 提取元数据
- 按 `segment_index` 排序
- 旧 H5 无 `collection_session_id` 时返回空列表

**`format_segment_chain_summary(chain, current_path) -> str`**：
- 输出汇总行："会话共有 X 个 segment，异常中断 Y 个，续采 Z 个，完成 W 个"
- 表格式列表：segment_index、文件名、状态、是否续采、回合、Stage、Dev1/Dev2 Bin、sync
- 当前文件标记 `▶`
- 关系提示："当前文件已被 segment N 续采" / "当前文件是续采段，父 segment=N"

**StatisticsPanel 新增 QTextEdit**：
- 显示 segment 链路摘要（只读，Consolas 7pt，max 120px，紫色标题）

### 17.3 storage_server 补填父 segment 反链

**`_maybe_update_parent_segment_link(new_file_path, params)`**：
- **触发条件**：`is_resumed=true`，`parent_segment_index` 存在，`recording_session_id` 存在
- **操作**：扫描同目录 `.h5`，找到 `collection_session_id` 相同、`segment_index == parent_segment_index`、`collection_status == abnormal_interrupted` 的父文件
- **写入**：`resumed_by_segment_index = 当前 segment_index`，`resumed_by_file = os.path.relpath(new_file_path, storage_dir)`
- **安全**：找不到就 log warning，不阻塞新 segment 创建；文件打开失败跳过
- **调用点**：`create_file()` 成功后、return 前

**链路完整性**：
- 父 segment：`resumed_by_segment_index=2`、`resumed_by_file="path/to/seg2.h5"`
- 续采 segment：`is_resumed=true`、`parent_segment_index=1`
- 形成双向链路 ✅

### 17.4 重启后断点策略

**当前行为（不变）**：
- 异常中断自动回首页 → `window.__showBreakpointResumeAfterAbort=true` → 显示"断点续采"按钮
- 普通返回首页、刷新首页 → 内存标记丢失 → 按钮不显示
- localStorage `emg_breakpoint_state` **仍保留**

**策略说明**：
- 这是 UX 策略，不是数据丢失
- localStorage 断点是一个"热恢复"标记，只在同一会话的异常中断路径中可见
- 重启后如需恢复，可通过 hdf5_tool 的 segment 链路视图找到 `abnormal_interrupted` 的 segment
- Phase 6 建议增加"历史断点管理"入口，从 hdf5_tool 一键恢复

### 17.5 验证结果

```
python -m py_compile hdf5_tool.py      ✅
python -m py_compile storage_server.py ✅
python -m py_compile bin_sync_tool.py  ✅

scan_segment_chain: 2 files (parent + resumed) ✅
segment sort by segment_index: correct ✅
parent backfill: resumed_by_segment_index=2 ✅
parent backfill: resumed_by_file set ✅
chain summary: session stats correct ✅
old H5 (no collection_session_id): empty chain ✅
```

### 17.6 Phase 6: 历史断点任务管理（2026-05-31）

#### 修改文件

| 文件 | 改动 | 说明 |
|------|------|------|
| [hdf5_tool.py](tools/hdf5_tool.py) | +130 行 | 新增 `scan_breakpoints()`、`_bp_summary_line()`、`generate_breakpoint_json()`；新增 `BreakpointTab` 标签页（扫描、列表、导出、复制剪贴板） |
| [page-switch.js](public/scripts/page-switch.js) | +40 行 | 新增 `_handleImportBreakpoint()` 从 JSON 文件导入断点；绑定隐藏 file input |
| [index.html](public/index.html) | +5 行 | 新增隐藏 `#importBreakpointInput` + "导入断点文件" 链接 |

#### hdf5_tool 新增功能

**`scan_breakpoints(storage_root) -> list[dict]`**：
- 递归扫描 `storage_root` 下所有 `.h5`
- 筛选 `collection_status == "abnormal_interrupted"`
- 判断是否已被续采：`resumed_by_segment_index > 0` 或 `resumed_by_file` 非空
- 恢复数据来源优先级：**`breakpoint_state`（新格式，含完整 collectionConfig/gesturesSnapshot）→ `resume_progress`（旧格式，仅诊断 fallback）**
- 可恢复判定：未被续采 + `breakpoint_state` 可解析 + 含 `collectionConfig`
- 仅 `resume_progress` 无 `breakpoint_state` → 标记 `[OLD_FMT]`，仅可诊断

**`generate_breakpoint_json(h5_path) -> dict`**：
- 生成前端兼容的 breakpoint state JSON
- **优先读取 `breakpoint_state`（完整可恢复状态），fallback 到 `resume_progress`（旧格式兼容）**
- 包含 `source_h5_path` 用于溯源
- 返回 `recoverable` 标志和 `warnings` 列表

**BreakpointTab UI**：
- "扫描历史断点" 按钮 → 选择目录 → 列出所有异常中断 H5
- 绿色 `[RECOVERABLE]` = 可恢复，紫色 `[RESUMED]` = 已续采，黄色 `[OLD_FMT]` = 旧格式仅诊断，橙色 `[DIAG_ONLY]` = 其他
- 详情面板显示用户/任务/轮次/Stage/手势进度；旧格式文件提示"缺少 breakpoint_state/collectionConfig，仅可诊断"
- "导出断点恢复 JSON" 和 "复制 JSON 到剪贴板" 仅对 `recoverable=true` 的项启用
- 对不可恢复项点击导出会弹窗说明缺少 breakpoint_state/collectionConfig

#### 前端导入入口

- 首页 welcome 页面增加隐藏 `<input type="file" accept=".json">` + "导入断点文件" 链接
- `_handleImportBreakpoint()` 读取 JSON，校验 version/status/collectionConfig/currentTaskId/segmentIndex
- 写入 localStorage + 设置 `window.__showBreakpointResumeAfterAbort = true`
- 刷新首页显示"断点续采"按钮

#### 可恢复判定规则

| 条件 | 判定 |
|------|------|
| collection_status == "abnormal_interrupted" | 扫描范围内 |
| resumed_by_segment_index <= 0 | 未被续采 |
| 有 `breakpoint_state` attrs（新格式） | 优先使用，含完整 collectionConfig+gesturesSnapshot |
| 仅有 `resume_progress`（旧格式） | fallback，仅诊断，标记 OLD_FMT |
| breakpoint_state.collectionConfig 存在 | **可完整恢复** |
| 缺 collectionConfig | 仅可诊断（不可恢复） |

#### 为什么不直接写浏览器 localStorage

hdf5_tool 是 Python/PyQt5 桌面应用，浏览器 localStorage 在不同进程中。通过导出 JSON 文件 + 前端导入的方式解耦：
1. hdf5_tool 导出 `.breakpoint.json`
2. 用户通过首页"导入断点文件"链接加载
3. 前端写入 localStorage

#### 完整恢复流程（重启后）

```
hdf5_tool → 扫描 storage → 找到 abnormal_interrupted H5
  → 导出 .breakpoint.json
  → 复制到采集电脑/同一目录
首页 → "导入断点文件" → 选择 .breakpoint.json
  → localStorage 写入 → 显示"断点续采"按钮
  → 连接设备 → 点击"断点续采" → 从断点继续
```

#### 验证结果

```
python -m py_compile hdf5_tool.py      ✅
node --check page-switch.js            ✅
node --check collection-controller.js  ✅

scan_breakpoints: 2 found (1 recoverable + 1 resumed) ✅
generate_breakpoint_json: source_h5_path + progress ✅
  currentGestureIndex=18, collectionConfig present ✅
completed H5: not in results ✅
old H5 (no collection_status): not in results ✅
frontend import: localStorage write + button refresh ✅
```
