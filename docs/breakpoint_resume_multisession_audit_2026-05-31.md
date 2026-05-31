# 多轮采集断点续采专项审计 — 2026-05-31

**审计范围**：全部轮次（all-sessions）模式下异常中断 → 断点续采 → 后续轮次自动继续的完整链路。  
**审计方法**：代码走读 + 场景推演，不改代码。  
**分支**：feat_breakpoint  

---

## 1. 断点保存审计

### 场景 A：sessionCount=3，全部轮次模式，从 session 1（第 2 轮）gestureIndex=18 异常中断

#### 1.1 `abortTask()` 创建的 `_pendingAbortSnapshot`

| 字段 | 值 | 源码位置 |
|------|-----|---------|
| `progress.currentSessionIndex` | `1` | [abortTask():1330](public/scripts/collection-controller.js#L1330) |
| `progress.sessionCount` | `3` | [abortTask():1338](public/scripts/collection-controller.js#L1338) |
| `progress.isAllSessionsMode` | `true` | [abortTask():1337](public/scripts/collection-controller.js#L1337) |
| `progress.currentStageIndex` | `this.currentStageIndex` | [abortTask():1331](public/scripts/collection-controller.js#L1331) |
| `progress.currentGestureIndex` | `18` | [abortTask():1332](public/scripts/collection-controller.js#L1332) |
| `gesturesSnapshot` | 完整序列 | [abortTask():1342-1352](public/scripts/collection-controller.js#L1342-L1352) |
| `recordingSessionId` | `this._recordingSessionId` | [abortTask():1364](public/scripts/collection-controller.js#L1364) |
| `currentSegmentIndex` | `1`（首次中断） | [abortTask():1354](public/scripts/collection-controller.js#L1354) |
| `isAllSessionsMode` | `true` | [abortTask():1365](public/scripts/collection-controller.js#L1365) |

**✅ 结论**：全部必要字段已保存。

#### 1.2 `_executeAbort()` 写入 localStorage 的 `breakpointState`

使用 `snap.progress.*` 和 `snap.isAllSessionsMode` 构建：

```javascript
breakpointState = {
    currentSessionIndex: snap.progress.currentSessionIndex,  // = 1
    isAllSessionsMode: snap.progress.isAllSessionsMode,      // = true  ✅
    sessionCount: snap.progress.sessionCount,                // = 3
    currentGestureIndex: snap.progress.currentGestureIndex,  // = 18
    recordingSessionId: snap.recordingSessionId,             // preserved
    segmentIndex: snap.currentSegmentIndex,                  // = 1
}
```

**✅ 结论**：localStorage 包含完整的多轮进度信息。

---

## 2. 断点恢复审计

### 2.1 `resumeBreakpoint()`（page-switch.js）

| 恢复项 | 方法 | 源码 |
|--------|------|------|
| `window.currentCollectionConfig` | 直接赋值 | [resumeBreakpoint():638](public/scripts/page-switch.js) |
| `localStorage emg_current_collection_config` | `JSON.stringify` | [resumeBreakpoint():639](public/scripts/page-switch.js) |
| `emg_current_user` | 从 `collectionConfig.subject` | [resumeBreakpoint():630](public/scripts/page-switch.js) |
| `showCollection()` | 进入采集页 | [resumeBreakpoint():653](public/scripts/page-switch.js) |
| `selectTask()` | 任务类型映射 | [resumeBreakpoint():658-663](public/scripts/page-switch.js) |
| `loadBreakpointState(state)` | 恢复全部进度 | [resumeBreakpoint():664](public/scripts/page-switch.js) |

**✅ 结论**：恢复入口正确。

### 2.2 `loadBreakpointState(state)`（collection-controller.js）

| 恢复字段 | 值 | 源码 |
|----------|-----|------|
| `currentSessionIndex` | `state.currentSessionIndex ?? 0` → **1** | [loadBreakpointState:3004](public/scripts/collection-controller.js#L3004) |
| `sessionCount` | `state.sessionCount \|\| 3` → **3** | [loadBreakpointState:3010](public/scripts/collection-controller.js#L3010) |
| `_isAllSessionsMode` | `state.isAllSessionsMode \|\| false` → **true** | [loadBreakpointState:3011](public/scripts/collection-controller.js#L3011) |
| `currentStageIndex` | `state.currentStageIndex ?? 0` | [loadBreakpointState:3003](public/scripts/collection-controller.js#L3003) |
| `currentGestureIndex` | `state.currentGestureIndex ?? 0` → **18** | [loadBreakpointState:3005](public/scripts/collection-controller.js#L3005) |
| `gestures` | `state.gesturesSnapshot`（优先快照） | [loadBreakpointState:3021](public/scripts/collection-controller.js#L3021) |
| `_recordingSessionId` | `state.recordingSessionId \|\| null` | [loadBreakpointState:3030](public/scripts/collection-controller.js#L3030) |
| `_isResumeMode` | `true` | [loadBreakpointState:3033](public/scripts/collection-controller.js#L3033) |
| `_resumeSegmentIndex` | `(state.segmentIndex \|\| 1) + 1` → **2** | [loadBreakpointState:3036](public/scripts/collection-controller.js#L3036) |

**✅ 结论**：所有多轮进度字段正确恢复。UI 应显示"第 2/3 轮"。

### 2.3 `_updateResumeReadyUI()`

- `startAllSessionsBtn.disabled = true`（不允许再点击"全部轮次"）→ ✅
- `testModeBtn.disabled = true` → ✅
- `startTaskBtn` 文案改为"开始续采" → ✅
- **`_isAllSessionsMode` 仍为 `true`**，但不通过 `startAllSessions()` 启动 → 通过 `startTask(false)` + `isMultiSession: true` + `onAllGesturesComplete` 驱动多轮循环 → ✅

**✅ 结论**：UI 正确区分续采和普通模式，用户只能通过"开始续采"启动。

---

## 3. 续采启动审计

### 3.1 `startTask(isTestMode=false)` — resume mode

| 行为 | Resume Mode | 源码 |
|------|------------|------|
| 不重置 `currentGestureIndex` | ✅ 保留 18 | [startTask:986-990](public/scripts/collection-controller.js#L986-L990) |
| 不调 `loadCollectionConfig()` | ✅ 调 `_reloadExecutionParams()` | [startTask:918-928](public/scripts/collection-controller.js#L918-L928) |
| 保留 `recordingSessionId` | ✅ 复用断点值 | [startTask:1023](public/scripts/collection-controller.js#L1023) |
| `sessionIndex` | `this.currentSessionIndex` → **1** | [startTask:1032](public/scripts/collection-controller.js#L1032) |
| `sessionNumber` | `this.currentSessionIndex + 1` → **2** | [startTask:1033](public/scripts/collection-controller.js#L1033) |
| `sessionCount` | `this.sessionCount` → **3** | [startTask:1034](public/scripts/collection-controller.js#L1034) |
| `isMultiSession` | `this._isAllSessionsMode` → **true** | [startTask:1042](public/scripts/collection-controller.js#L1042) |
| `isResume` | `true` | [startTask:1050](public/scripts/collection-controller.js#L1050) |
| `resumeSegmentIndex` | `this._resumeSegmentIndex` → **2** | [startTask:1053](public/scripts/collection-controller.js#L1053) |

**✅ 结论**：续采启动 payload 正确，realtimeEngine 收到正确的多轮 + resume 元数据。

---

## 4. 续采 Stage 完成后的多轮继续审计

### 4.1 `onAllGesturesComplete()` — 关键路径

```javascript
onAllGesturesComplete() {
    // ... 
    this.sendToRealtimeEngine('collection_stop', { completed: true });
    this._isRunning = false;

    // 【Phase 2】resumed Stage 正常完成 → 清除断点状态
    this._clearBreakpointState();       // ← 清除 _isResumeMode + localStorage

    // 【新增】全部轮次模式：检查是否需要继续下一轮
    if (this._isAllSessionsMode) {      // ← 仍为 true（exitResumeMode 不碰它）
        const hasMoreSessions = this.currentSessionIndex < this.sessionCount - 1;
        if (hasMoreSessions) {
            this.showRestCountdownAndContinue();  // ← 进入多轮循环
            return;
        } else {
            // 所有轮次完成
            this._isAllSessionsMode = false;
            // ...显示完成弹窗...
            return;
        }
    }
    // 单轮模式...
}
```

### 4.2 状态流转推演

**起点**：`currentSessionIndex=1`, `_isAllSessionsMode=true`, `_isResumeMode=true`, `sessionCount=3`

**Step 1** — `_clearBreakpointState()`:
- `localStorage.removeItem('emg_breakpoint_state')` ✅
- `localStorage.setItem('emg_breakpoint_exists', 'false')` ✅
- `_isResumeMode = false` ✅
- `_resumeState = null` ✅
- `_isAllSessionsMode` — **未修改**（仍为 `true`）✅

**Step 2** — `_isAllSessionsMode` 检查:
- `this.currentSessionIndex < this.sessionCount - 1` → `1 < 2` → `true` ✅
- → `showRestCountdownAndContinue()` ✅

**Step 3** — `showRestCountdownAndContinue()`:
- `nextSessionIndex = this.currentSessionIndex + 1` → **2** ✅
- 休息倒计时...
- `this.currentSessionIndex = nextSessionIndex` → **2** ✅
- `this.currentGestureIndex = 0` ✅（新轮次从头开始）
- `startTask(false)` ✅

**Step 4** — 第二次 `startTask(false)`（普通模式，非 resume）:
- `loadCollectionConfig()` → 重新加载配置 ✅
- `currentGestureIndex = 0` ✅
- `isMultiSession: true` ✅
- `sessionIndex: 2, sessionNumber: 3, sessionCount: 3` ✅
- 正常采集 session 2 ✅

**Step 5** — session 2 完成后:
- `onAllGesturesComplete()` → `_isAllSessionsMode` 仍为 `true`
- `hasMoreSessions = 2 < 2` → `false`
- `_isAllSessionsMode = false`
- 显示"全部轮次完成"弹窗 ✅

**✅ 结论**：多轮续采流程正确。
- 第 1 轮（session 0）不重采 ✅
- 第 2 轮（session 1）从 gesture 18 继续 ✅
- 第 2 轮完成后自动进入第 3 轮（session 2）✅
- 第 3 轮完成后显示完成弹窗 ✅

---

## 5. H5 元数据审计

### 5.1 续采 segment H5（create_file）

| Attrs | 如何传递 | 值 |
|-------|---------|-----|
| `session_index` | realtimeEngine → `openStageFile()` params → storage | **1** |
| `session_number` | 同上 | **2** |
| `session_count` | 同上 | **3** |
| `is_multi_session` | 同上 | **true** ✅ |
| `recording_session_id` | 同上 | 复用断点值 ✅ |
| `is_resumed` | 同上（Phase 2 新增） | **true** ✅ |
| `segment_index` | 同上 | **2** ✅ |
| `resume_from_interrupted_at` | 同上 | ISO 时间 ✅ |
| `resume_reason` | 同上 | 中断原因 ✅ |
| `resume_parent_recording_session_id` | 同上 | 父录像ID ✅ |

**✅ 结论**：续采 H5 包含完整的多轮和 resume 元数据。

### 5.2 异常中断 segment H5（close_file）

| Attrs | 值 |
|-------|-----|
| `collection_status` | `"abnormal_interrupted"` ✅ |
| `interrupted_at` | ISO 时间 ✅ |
| `interrupt_reason` | 用户选择的原因 ✅ |
| `resume_progress` | JSON 进度快照 ✅ |
| `sync_status` | `"pending"`（不变） ✅ |

### 5.3 后续 session 的 H5（非 resume）

session 2（第 3 轮）的 H5 由 `startTask(false)`（普通模式）创建：
- `is_resumed: false`
- `segment_index: 1`（普通 segment）
- `session_index: 2`, `session_number: 3`, `session_count: 3`
- `is_multi_session: true`
- `recording_session_id`: 与前面相同（多轮共享）✅

**✅ 结论**：只有续采的 segment 标记 `is_resumed=true`，后续轮次作为普通 segment。

---

## 6. 发现的问题分级

### 阻塞（0 个）

无。多轮续采核心流程经代码走读验证正确。

### 高风险（1 个）

**H-1：`_clearBreakpointState()` 在 `onAllGesturesComplete()` 中先于多轮检查执行**

- **现象**：`_clearBreakpointState()` → `exitResumeMode()` 清除 `_isResumeMode`，但保留了 `_isAllSessionsMode`，当前逻辑依赖此行为。
- **风险**：如果未来有人在 `exitResumeMode()` 中增加 `this._isAllSessionsMode = false`，会导致多轮循环中断。
- **位置**：[onAllGesturesComplete():2145-2146](public/scripts/collection-controller.js#L2145-L2146)
- **建议**：给 `exitResumeMode()` 加注释："注意：不修改 _isAllSessionsMode，多轮续采依赖此行为"。当前不改代码。

### 中风险（1 个）

**M-1：轮次间休息期间无法异常中断**

- **现象**：`showRestCountdownAndContinue()` 期间 `_isRunning = false`，`abortTask()` 直接返回"采集未运行"。
- **影响**：休息倒计时期间如遇设备问题，用户无法中断保存进度，只能等倒计时结束进入下轮后再点中断。
- **位置**：[showRestCountdownAndContinue()](public/scripts/collection-controller.js#L1123)
- **建议**：不作为本次修复范围。后续可考虑在 rest 期间保持 `_isRunning=true` 并允许 abort，但需确认不会破坏倒计时逻辑。

### 低风险（2 个）

**L-1：resume 模式下 `startAllSessionsBtn` 被禁用，用户无法直接从续采准备态启动全部轮次**

- 当前设计中，用户必须点击"开始续采"（单 session）来续采当前中断的 session，然后由 `onAllGesturesComplete` 的多轮检查自动驱动后续 session。行为符合预期。
- 文档已说明。

**L-2：页面刷新后 `window.__showBreakpointResumeAfterAbort` 丢失，按钮不显示**

- 这是设计意图（详见 2026-05-31 UX 修复），避免非中断路径看到续采按钮。

---

## 7. 连续手势多轮续采

`onContinualStageComplete()` 中同样有 `_clearBreakpointState()` 和 `_isAllSessionsMode` 检查，与离散手势逻辑对称。代码走读未发现差异。

**⚠️ 注意**：连续手势的 `continualTrialCount` 在 resume 模式下不重置，但动画从 trial 0 重新开始（已知限制，已在 loadBreakpointState 中 toast 提示）。

---

## 8. 验收场景推演

### 场景 A：3 轮全部轮次，session 1 gestureIndex=18 异常中断

| 步骤 | 预期 | 代码支持 |
|------|------|---------|
| 中断时 breakpointState.currentSessionIndex | 1 | ✅ |
| 中断时 breakpointState.isAllSessionsMode | true | ✅ |
| 中断时 breakpointState.currentGestureIndex | 18 | ✅ |
| 续采准备态 UI 显示 | "第 2/3 轮" | ✅ |
| startPayload.sessionIndex | 1 | ✅ |
| startPayload.sessionNumber | 2 | ✅ |
| startPayload.isMultiSession | true | ✅ |
| startPayload.isResume | true | ✅ |
| startPayload.resumeSegmentIndex | 2 | ✅ |
| H5 attrs: is_resumed | true | ✅ |
| H5 attrs: segment_index | 2 | ✅ |
| H5 attrs: session_index | 1 | ✅ |
| 当前 stage 完成后进入 session 2 | 自动休息倒计时 → 开始第 3 轮 | ✅ |
| session 2 H5: is_resumed | false | ✅ |
| session 2 H5: segment_index | 1 | ✅ |

### 场景 B：中断后再次中断（二次中断）

| 步骤 | 预期 | 代码支持 |
|------|------|---------|
| 第一次 resume 后在 session 1 gesture 25 再次中断 | 创建新 breakpoint | ✅ |
| 新 breakpoint segmentIndex | 2（从 `_resumeSegmentIndex` 取） | ✅ |
| 新 breakpoint isAllSessionsMode | true | ✅ |
| 再次 resume 的 segment_index | 3 | ✅ |
| H5 attrs: segment_index | 3 | ✅ |

---

## 9. 总结

**多轮采集断点续采：支持** ✅

- 已中断的 session 从断点 gesture 继续 ✅
- 已完成的前面 session 不重采 ✅
- 当前 session 完成后自动进入后续 session ✅
- H5 元数据正确记录 session/index/segment/resume 信息 ✅
- 无阻塞性 bug ✅

**不需要改代码**。当前实现完整支持多轮续采场景。

**需关注的风险点**：
1. H-1：`exitResumeMode()` 增加了 `this._isAllSessionsMode = false` 会导致多轮中断（当前不存在此代码，仅作为未来修改的提醒）
2. M-1：轮次间休息期间无法异常中断（UX 限制，不影响数据完整性）
