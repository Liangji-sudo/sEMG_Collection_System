# 后台数据统计提示异常调查 — 2026-05-31

**分支**：fix_hint  

---

## 1. 数据流

```
showBackend() → backendManager.onPageShow()
  ├─ loadLastStats()          // localStorage key: emg_backend_last_stats
  ├─ renderLoadingState()
  └─ loadStorageFiles()       // fetch /api/storage/files
       └─ parseAndAnalyze()   // 解析文件名、统计 subjects/tasks
       └─ render()            // renderStats() + renderFileList()
            └─ calculateChanges(lastStats, current)  // 增量计算
            └─ renderChangeBadge(changes.*)          // 绿色气泡
       └─ showToast("已加载 N 个文件")

离开后台：
  MutationObserver: backend-page class="hidden"
    └─ saveCurrentStats()     // 写入 emg_backend_last_stats
```

## 2. 根因分析

### Bug 1: Toast 文案不区分场景 + 异步竞态

**`loadStorageFiles()` line 150**：
```javascript
this.showToast(`已加载 ${data.count} 个文件`, 'success');
```

问题：
- 首次加载、刷新无变化、刷新有变化都是同一个文案
- 没有 `requestId`/`loadToken` 防止旧请求 toast 覆盖新请求
- 快速点击刷新多次时，异步请求返回顺序不确定，最后一个 toast 可能来自较早的请求

### Bug 2: saveCurrentStats 仅在离开时保存

`MutationObserver` 在 `backend-page` 获得 `hidden` class 时调用 `saveCurrentStats()`。用户在后台点击刷新后，如果未离开页面，snapshot 不会更新。虽然这对增量气泡影响不大（气泡显示的是相对上次离开的快照），但如果用户长时间停留在后台并多次刷新，气泡会一直显示相同增量。

### Bug 3: 增量气泡逻辑基本正确（非 bug）

- 首次访问无 `lastStats` → changes 全部 null → 不显示气泡 ✅
- 再次访问时比较当前 vs 上次保存的快照 ✅
- 负数检测 + 目录切换保护 ✅

## 3. 修复方案

1. **Toast 修复**：`loadStorageFiles()` 增加 `_loadRequestId` 计数器，返回后比对，避免旧请求 toast；区分首次/刷新无变化/刷新有变化文案。
2. **Snapshot 保存时机**：在 `render()` 完成后也调用 `saveCurrentStats()`，确保刷新后更新基准。
3. **无需修改 storage_server.py**：`/api/storage/files` 接口返回正确。

## 4. 验证

- 首次进入后台：toast "已加载 N 个文件"，无增量气泡
- 刷新无新增：toast "已刷新，暂无新增文件"
- 刷新有新增：toast "已加载 N 个文件，新增 M 个"
- 旧请求不覆盖新 toast
- 离开后再进入，增量正确
