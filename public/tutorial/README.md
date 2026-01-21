# Tutorial 资源目录

## 目录结构

```
tutorial/
├── video/                      # 教程视频
│   ├── discrete_gesture.mp4    # 离散手势采集教程
│   ├── continual_gesture_1.mp4 # 连续手势1采集教程
│   ├── continual_gesture_2.mp4 # 连续手势2采集教程
│   └── continual_gesture_3.mp4 # 连续手势3采集教程
│
└── gestures/                   # 动作示范 GIF
    ├── discrete/               # 离散手势 GIF
    │   └── [手势gifFile].gif   # 与后台配置的 gifFile 字段对应
    ├── continual_1/            # 连续手势1 GIF
    │   └── action.gif          # 整个任务使用同一个 GIF
    ├── continual_2/            # 连续手势2 GIF
    │   └── action.gif
    └── continual_3/            # 连续手势3 GIF
        └── action.gif
```

## 使用说明

### 教程视频
- 格式：MP4
- 命名：固定名称，替换时保持同名覆盖即可
- 在采集界面点击"教程"按钮播放

### 动作示范 GIF
- 格式：GIF
- 离散手势：每个手势一个 GIF，文件名在后台配置的 gifFile 字段中指定
- 连续手势：每个任务类型一个 GIF，固定命名为 action.gif
- 采集过程中在左下角显示

## 占位图
如果 GIF 文件不存在，会显示占位图提示缺少资源。
