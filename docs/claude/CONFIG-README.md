# 采集任务配置系统使用说明

## 概述

本配置系统允许您通过JSON配置文件来自定义数据采集任务。

**纯浏览器支持**：`npm start` 打开网页即可使用，无需 Electron。

## 文件说明

```
scripts/
├── collection-constants.js   # 采集常量（已整合TaskConfig，可删除原task-config.js）
└── config-manager.js         # 配置管理器

config/
└── default-collection-config.json   # 示例配置文件

index.html   # 主页面（已添加配置管理UI）
```

## 使用方法

### 1. 在初始界面加载配置

1. 在初始界面底部状态栏找到"配置管理"卡片
2. 点击"**加载**"按钮 → 弹出文件选择对话框
3. 选择本地的 JSON 配置文件
4. 加载成功后显示提示，配置立即生效
5. 点击"**预览**"按钮可以查看当前配置详情

### 2. 配置文件格式

配置文件使用 JSON 格式，主要结构：

```json
{
  "configVersion": "1.0.0",
  "configName": "我的配置",
  
  "globalSettings": {
    "intro": { "duration": 10000 },
    "stagePrepare": { "countdownSeconds": 3 },
    "debug": { "enabled": true, "fastMode": false }
  },
  
  "promptLibrary": {
    "thumb_up": { "label": "拇指上滑", "icon": "👆", "color": "#3b82f6" }
  },
  
  "tasks": {
    "discrete_gesture": {
      "taskType": "prompt_sequence",
      "stages": [
        {
          "name": "palm_up",
          "label": "手心朝上",
          "promptSequence": ["thumb_up", "thumb_down"]
        }
      ]
    }
  }
}
```

### 3. 常见修改场景

**增加Stage的Prompt数量**：
```json
"promptSequence": ["thumb_up", "thumb_down", "新增的prompt"]
```

**新增一个Stage**：
```json
"stages": [
  // 现有stages...
  { "name": "new_stage", "label": "新Stage", "promptSequence": [...] }
]
```

**修改滚轮任务的目标数量**：
```json
{ "name": "wheel_task_1", "maxTrials": 15, "timeout": 180000 }
```

## 注意事项

1. 配置文件使用 **UTF-8 编码**
2. 加载新配置后**立即生效**，无需刷新页面
3. 可以删除原来的 `task-config.js`（功能已整合到 `collection-constants.js`）
4. 配置验证失败会显示具体错误信息

## 配置验证规则

- 必须包含 `tasks` 对象
- 必须包含 `globalSettings` 对象  
- 每个任务必须有 `stages` 数组
- 每个任务必须有 `taskType`（`prompt_sequence` 或 `wheel_cursor`）
