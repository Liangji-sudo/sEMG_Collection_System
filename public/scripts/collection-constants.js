/**
 * collection-constants.js - 采集系统常量配置（重构版v3）
 * 
 * 概念说明：
 * - Task（任务）: 如 discrete_gesture（离散手势采集）、continual_gesture_1（连续手势1）
 * - Stage（阶段）: 任务中的一个采集阶段
 * - Prompt（提示）: 离散手势Stage内的具体手势动作
 * 
 * v3更新：
 * - 连续手势1/2采用滚轮光标任务模式，而不是Prompt序列
 * - 每个Stage是一个独立的滚轮控制任务
 */

const COLLECTION_CONSTANTS = {
    // ==================== 开场动画配置 ====================
    INTRO: {
        DURATION: 10000,        // 10秒
        TYPE: 'countdown',      // 'countdown' | 'video' | 'custom'
        VIDEO_URL: '',
    },

    // ==================== Stage间准备配置 ====================
    STAGE_PREPARE: {
        COUNTDOWN_SECONDS: 3,
        COUNTDOWN_INTERVAL: 1000,
    },

    // ==================== 通用UI配置 ====================
    UI: {
        PROGRESS_TRANSITION: 300,
        TOAST_DURATION: 3000,
        STATUS_DEBOUNCE: 100,
    },

    // ==================== 调试配置 ====================
    DEBUG: {
        ENABLED: true,
        FAST_MODE: false,
        FAST_MODE_RATIO: 0.1,
    }
};

/**
 * ==================== 离散手势采集配置 ====================
 * 
 * 在离散手势采集中：
 * - 每个Stage代表一个手部姿态（如手心朝上、手心朝内等）
 * - 每个Stage内会播放一系列Prompt（具体的手势动作）
 * - 用户需要在保持Stage姿态的同时，跟随Prompt完成具体动作
 */
const DISCRETE_GESTURE_CONFIG = {
    NAME: '离散手势采集',
    
    // ==================== Prompt定义库 ====================
    // 所有可用的手势Prompt定义
    PROMPT_LIBRARY: {
        // 拇指动作
        'thumb_up': { label: '拇指上滑', icon: '👆', color: '#3b82f6' },
        'thumb_down': { label: '拇指下滑', icon: '👇', color: '#3b82f6' },
        'thumb_left': { label: '拇指左滑', icon: '👈', color: '#3b82f6' },
        'thumb_right': { label: '拇指右滑', icon: '👉', color: '#3b82f6' },
        'thumb_press': { label: '拇指按压', icon: '👍', color: '#3b82f6' },
        
        // 食指动作
        'index_tap': { label: '食指点击', icon: '☝️', color: '#10b981' },
        'index_double_tap': { label: '食指双击', icon: '✌️', color: '#10b981' },
        'index_swipe': { label: '食指滑动', icon: '👆', color: '#10b981' },
        
        // 中指动作
        'middle_tap': { label: '中指点击', icon: '🖕', color: '#f59e0b' },
        'middle_swipe': { label: '中指滑动', icon: '🖕', color: '#f59e0b' },
        
        // 多指动作
        'pinch': { label: '捏合', icon: '🤏', color: '#8b5cf6' },
        'spread': { label: '张开', icon: '🖐️', color: '#8b5cf6' },
        'fist': { label: '握拳', icon: '✊', color: '#8b5cf6' },
        'release': { label: '松开', icon: '✋', color: '#8b5cf6' },
        
        // 手腕动作
        'wrist_up': { label: '手腕上抬', icon: '⬆️', color: '#ec4899' },
        'wrist_down': { label: '手腕下压', icon: '⬇️', color: '#ec4899' },
        'wrist_rotate_cw': { label: '手腕顺时针', icon: '🔃', color: '#ec4899' },
        'wrist_rotate_ccw': { label: '手腕逆时针', icon: '🔄', color: '#ec4899' },
        
        // 休息/空动作
        'rest': { label: '保持', icon: '⏸️', color: '#6b7280' },
        'ready': { label: '准备', icon: '✅', color: '#6b7280' },
    },

    // ==================== Stage定义 ====================
    STAGES: {
        // Stage 1: 手心朝上
        palm_up: {
            name: 'palm_up',
            label: '手心朝上',
            instruction: '请保持手心朝上的姿势，跟随提示完成动作',
            icon: '🤲',
            color: '#3b82f6',
            
            // 该Stage内的Prompt序列 - 可以自定义顺序和内容
            promptSequence: [
                'thumb_up',
                'thumb_down',
                'thumb_up',
                'thumb_up',
                'thumb_up',
                'thumb_left',
                'thumb_right',
                'index_tap',
                'index_double_tap',
                'pinch',
                'spread',
                'fist',
                'release'
            ]
        },
        
        // Stage 2: 手心朝内
        palm_inward: {
            name: 'palm_inward',
            label: '手心朝内',
            instruction: '请保持手心朝内的姿势，跟随提示完成动作',
            icon: '🫲',
            color: '#10b981',
            
            promptSequence: [
                'thumb_up',
                'thumb_down',
                'index_tap',
                'middle_tap',
                'pinch',
                'spread',
                'wrist_up',
                'wrist_down',
                'fist',
                'release'
            ]
        },
        
        // Stage 3: 手放膝盖
        hand_on_knee: {
            name: 'hand_on_knee',
            label: '手放膝盖',
            instruction: '请将手放在膝盖上，跟随提示完成动作',
            icon: '🦵',
            color: '#f59e0b',
            
            promptSequence: [
                'thumb_press',
                'index_tap',
                'index_double_tap',
                'middle_tap',
                'pinch',
                'spread',
                'fist',
                'release',
                'rest',
                'ready'
            ]
        },
        
        // Stage 4: 手放桌上
        hand_on_desk: {
            name: 'hand_on_desk',
            label: '手放桌上',
            instruction: '请将手放在桌面上，跟随提示完成动作',
            icon: '🖥️',
            color: '#8b5cf6',
            
            promptSequence: [
                'index_tap',
                'index_double_tap',
                'middle_tap',
                'thumb_left',
                'thumb_right',
                'pinch',
                'spread',
                'wrist_rotate_cw',
                'wrist_rotate_ccw',
                'rest'
            ]
        }
    },

    // ==================== 动画配置 ====================
    ANIMATION: {
        SCROLL_SPEED: 2,
        PROMPT_SPACING: 120,
        INDICATOR_POSITION: 0.3,
        PROMPT_LENGTH: 60,
        PROMPT_THICKNESS: 10,
        LABEL_OFFSET: 80,
        
        COLORS: {
            active: '#10b981',
            passed: '#9ca3af',
            normal: '#3b82f6',
            indicator: '#ef4444'
        }
    }
};

/**
 * ==================== 连续手势1采集配置（滚轮光标任务） ====================
 * 
 * 在连续手势1采集中：
 * - 每个Stage是一个滚轮控制光标任务
 * - 用户通过滚轮控制光标移动到目标位置
 * - 光标停留在目标区域500ms视为命中
 * - 完成指定数量的目标或超时后进入下一个Stage
 * - Stage内不向realtimeEngine发送prompt消息
 */
const CONTINUAL_GESTURE_1_CONFIG = {
    NAME: '连续手势采集1（滚轮控制）',
    
    // ==================== 任务类型标识 ====================
    TASK_TYPE: 'wheel_cursor',  // 滚轮光标任务
    
    // ==================== 滚轮任务配置 ====================
    WHEEL_TASK: {
        MAX_TRIALS: 10,          // 每个Stage的目标数量
        STAGE_TIMEOUT: 120000,   // Stage超时时间（120秒）
        DWELL_MS: 500,           // 停留时间阈值（ms）
        TARGET_FRAC: 0.12,       // 目标高度占轨道的比例
        MIN_TARGET_DISTANCE: 0.20, // 相邻目标的最小距离
    },
    
    // ==================== Stage定义 ====================
    // 连续手势的Stage不使用promptSequence，而是使用滚轮任务
    STAGES: {
        wheel_task_1: {
            name: 'wheel_task_1',
            label: '滚轮控制任务1',
            instruction: '滚动滚轮将光标移动到蓝色目标区域，保持500ms命中',
            icon: '🎯',
            color: '#10b981',
            // 可选的Stage特定配置
            maxTrials: 10,
            timeout: 120000,
        },
        wheel_task_2: {
            name: 'wheel_task_2',
            label: '滚轮控制任务2',
            instruction: '滚动滚轮将光标移动到蓝色目标区域，保持500ms命中',
            icon: '🎯',
            color: '#10b981',
            maxTrials: 10,
            timeout: 120000,
        },
        wheel_task_3: {
            name: 'wheel_task_3',
            label: '滚轮控制任务3',
            instruction: '滚动滚轮将光标移动到蓝色目标区域，保持500ms命中',
            icon: '🎯',
            color: '#10b981',
            maxTrials: 10,
            timeout: 120000,
        },
        wheel_task_4: {
            name: 'wheel_task_4',
            label: '滚轮控制任务4',
            instruction: '滚动滚轮将光标移动到蓝色目标区域，保持500ms命中',
            icon: '🎯',
            color: '#10b981',
            maxTrials: 10,
            timeout: 120000,
        }
    },

    // ==================== 动画配置（保留兼容性） ====================
    ANIMATION: {
        COLORS: {
            track: 'rgba(15, 23, 42, 0.06)',
            trackBorder: 'rgba(15, 23, 42, 0.15)',
            target: 'rgba(59, 130, 246, 0.25)',
            targetActive: 'rgba(59, 130, 246, 0.4)',
            cursor: '#ef4444',
            cursorBorder: '#991b1b',
            success: '#10b981',
            warning: '#f59e0b'
        }
    }
};

/**
 * ==================== 连续手势2采集配置（手腕） ====================
 * 
 * 连续手势2也采用滚轮光标任务模式
 */
const CONTINUAL_GESTURE_2_CONFIG = {
    NAME: '连续手势采集2（手腕控制）',
    
    // ==================== 任务类型标识 ====================
    TASK_TYPE: 'wheel_cursor',
    
    // ==================== 滚轮任务配置 ====================
    WHEEL_TASK: {
        MAX_TRIALS: 10,
        STAGE_TIMEOUT: 120000,
        DWELL_MS: 500,
        TARGET_FRAC: 0.12,
        MIN_TARGET_DISTANCE: 0.20,
    },
    
    // ==================== Stage定义 ====================
    STAGES: {
        wrist_control_1: {
            name: 'wrist_control_1',
            label: '手腕控制任务1',
            instruction: '用手腕动作控制光标移动到目标区域',
            icon: '🔄',
            color: '#f59e0b',
            maxTrials: 10,
            timeout: 120000,
        },
        wrist_control_2: {
            name: 'wrist_control_2',
            label: '手腕控制任务2',
            instruction: '用手腕动作控制光标移动到目标区域',
            icon: '🔄',
            color: '#f59e0b',
            maxTrials: 10,
            timeout: 120000,
        },
        wrist_control_3: {
            name: 'wrist_control_3',
            label: '手腕控制任务3',
            instruction: '用手腕动作控制光标移动到目标区域',
            icon: '🔄',
            color: '#f59e0b',
            maxTrials: 10,
            timeout: 120000,
        },
        wrist_control_4: {
            name: 'wrist_control_4',
            label: '手腕控制任务4',
            instruction: '用手腕动作控制光标移动到目标区域',
            icon: '🔄',
            color: '#f59e0b',
            maxTrials: 10,
            timeout: 120000,
        }
    },

    // ==================== 动画配置 ====================
    ANIMATION: {
        COLORS: {
            track: 'rgba(15, 23, 42, 0.06)',
            trackBorder: 'rgba(15, 23, 42, 0.15)',
            target: 'rgba(245, 158, 11, 0.25)',
            targetActive: 'rgba(245, 158, 11, 0.4)',
            cursor: '#ef4444',
            cursorBorder: '#991b1b',
            success: '#10b981',
            warning: '#f59e0b'
        }
    }
};

// ==================== 挂载到全局 ====================
window.COLLECTION_CONSTANTS = COLLECTION_CONSTANTS;
window.DISCRETE_GESTURE_CONFIG = DISCRETE_GESTURE_CONFIG;
window.CONTINUAL_GESTURE_1_CONFIG = CONTINUAL_GESTURE_1_CONFIG;
window.CONTINUAL_GESTURE_2_CONFIG = CONTINUAL_GESTURE_2_CONFIG;

/**
 * ==================== 便捷访问对象 ====================
 */
const CollectionTiming = {
    getIntroDuration() {
        let duration = COLLECTION_CONSTANTS.INTRO.DURATION;
        if (COLLECTION_CONSTANTS.DEBUG.FAST_MODE) {
            duration = Math.max(duration * COLLECTION_CONSTANTS.DEBUG.FAST_MODE_RATIO, 1000);
        }
        return duration;
    },
    
    getPrepareCountdown() {
        let seconds = COLLECTION_CONSTANTS.STAGE_PREPARE.COUNTDOWN_SECONDS;
        if (COLLECTION_CONSTANTS.DEBUG.FAST_MODE) {
            seconds = Math.max(Math.floor(seconds * COLLECTION_CONSTANTS.DEBUG.FAST_MODE_RATIO), 1);
        }
        return seconds;
    },
    
    // 获取任务配置
    getTaskConfig(taskId) {
        const configMap = {
            'discrete_gesture': DISCRETE_GESTURE_CONFIG,
            'continual_gesture_1': CONTINUAL_GESTURE_1_CONFIG,
            'continual_gesture_2': CONTINUAL_GESTURE_2_CONFIG
        };
        return configMap[taskId] || DISCRETE_GESTURE_CONFIG;
    },
    
    // 获取任务类型
    getTaskType(taskId) {
        const config = this.getTaskConfig(taskId);
        return config.TASK_TYPE || 'prompt_sequence'; // 默认是prompt序列类型
    },
    
    // 判断是否是滚轮任务
    isWheelTask(taskId) {
        return this.getTaskType(taskId) === 'wheel_cursor';
    },
    
    // 获取Stage配置
    getStageConfig(taskId, stageName) {
        const taskConfig = this.getTaskConfig(taskId);
        return taskConfig.STAGES[stageName] || null;
    },
    
    // 获取Stage的Prompt序列（仅对离散手势有效）
    getPromptSequence(taskId, stageName) {
        const stageConfig = this.getStageConfig(taskId, stageName);
        if (stageConfig && stageConfig.promptSequence) {
            let sequence = [...stageConfig.promptSequence];
            if (COLLECTION_CONSTANTS.DEBUG.FAST_MODE) {
                // 快速模式下只取前3个
                sequence = sequence.slice(0, Math.max(3, Math.floor(sequence.length * COLLECTION_CONSTANTS.DEBUG.FAST_MODE_RATIO)));
            }
            return sequence;
        }
        return [];
    },
    
    // 获取Prompt定义
    getPromptDefinition(taskId, promptName) {
        const taskConfig = this.getTaskConfig(taskId);
        if (taskConfig.PROMPT_LIBRARY) {
            return taskConfig.PROMPT_LIBRARY[promptName] || { 
                label: promptName, 
                icon: '●', 
                color: '#6b7280' 
            };
        }
        return { label: promptName, icon: '●', color: '#6b7280' };
    },
    
    // 获取动画配置
    getAnimationConfig(taskId) {
        const taskConfig = this.getTaskConfig(taskId);
        return taskConfig.ANIMATION || DISCRETE_GESTURE_CONFIG.ANIMATION;
    },
    
    // 获取滚轮任务配置
    getWheelTaskConfig(taskId) {
        const taskConfig = this.getTaskConfig(taskId);
        return taskConfig.WHEEL_TASK || {
            MAX_TRIALS: 10,
            STAGE_TIMEOUT: 120000,
            DWELL_MS: 500,
            TARGET_FRAC: 0.12
        };
    },
    
    // 获取Stage的Prompt数量（离散手势）或Trial数量（滚轮任务）
    getPromptCount(taskId, stageName) {
        if (this.isWheelTask(taskId)) {
            const stageConfig = this.getStageConfig(taskId, stageName);
            const wheelConfig = this.getWheelTaskConfig(taskId);
            return stageConfig?.maxTrials || wheelConfig.MAX_TRIALS;
        }
        const sequence = this.getPromptSequence(taskId, stageName);
        return sequence.length;
    },
    
    // 估算Stage时长
    estimateStageDuration(taskId, stageName) {
        if (this.isWheelTask(taskId)) {
            const stageConfig = this.getStageConfig(taskId, stageName);
            const wheelConfig = this.getWheelTaskConfig(taskId);
            return stageConfig?.timeout || wheelConfig.STAGE_TIMEOUT;
        }
        const promptCount = this.getPromptCount(taskId, stageName);
        return (promptCount + 2) * 1000; // 每个prompt约1秒，加2秒缓冲
    },
    
    isDebugMode() {
        return COLLECTION_CONSTANTS.DEBUG.ENABLED;
    },
    
    isFastMode() {
        return COLLECTION_CONSTANTS.DEBUG.FAST_MODE;
    }
};

window.CollectionTiming = CollectionTiming;

// ==================== TaskConfig 整合（原task-config.js） ====================
/**
 * 任务配置 - 整合到 collection-constants.js
 * 这些定义主要用于UI显示和任务管理
 */
const TASK_DEFINITIONS = {
    discrete_gesture: {
        id: 'discrete_gesture',
        name: '离散手势',
        description: '4种单独手势采集',
        icon: 'fa-hand-paper',
        taskType: 'prompt_sequence',
        stages: [
            { name: 'palm_up', label: '手心向上', instruction: '请将手掌向上平放' },
            { name: 'palm_inward', label: '手心向内', instruction: '请将手心朝向身体' },
            { name: 'hand_on_knee', label: '手放膝盖', instruction: '请将手自然放在膝盖上' },
            { name: 'hand_on_desk', label: '手放桌上', instruction: '请将手自然放在桌面上' }
        ]
    },
    continual_gesture_1: {
        id: 'continual_gesture_1',
        name: '连续手势1',
        description: '滚轮控制光标任务',
        icon: 'fa-hand-point-up',
        taskType: 'wheel_cursor',
        stages: [
            { name: 'wheel_task_1', label: '滚轮控制任务1', instruction: '滚动滚轮将光标移动到目标区域', icon: '🎯', color: '#10b981', maxTrials: 10, timeout: 120000 },
            { name: 'wheel_task_2', label: '滚轮控制任务2', instruction: '滚动滚轮将光标移动到目标区域', icon: '🎯', color: '#10b981', maxTrials: 10, timeout: 120000 },
            { name: 'wheel_task_3', label: '滚轮控制任务3', instruction: '滚动滚轮将光标移动到目标区域', icon: '🎯', color: '#10b981', maxTrials: 10, timeout: 120000 },
            { name: 'wheel_task_4', label: '滚轮控制任务4', instruction: '滚动滚轮将光标移动到目标区域', icon: '🎯', color: '#10b981', maxTrials: 10, timeout: 120000 }
        ]
    },
    continual_gesture_2: {
        id: 'continual_gesture_2',
        name: '连续手势2',
        description: '手腕控制光标任务',
        icon: 'fa-hand-peace',
        taskType: 'wheel_cursor',
        stages: [
            { name: 'wrist_control_1', label: '手腕控制任务1', instruction: '用手腕动作控制光标移动到目标区域', icon: '🔄', color: '#f59e0b', maxTrials: 10, timeout: 120000 },
            { name: 'wrist_control_2', label: '手腕控制任务2', instruction: '用手腕动作控制光标移动到目标区域', icon: '🔄', color: '#f59e0b', maxTrials: 10, timeout: 120000 },
            { name: 'wrist_control_3', label: '手腕控制任务3', instruction: '用手腕动作控制光标移动到目标区域', icon: '🔄', color: '#f59e0b', maxTrials: 10, timeout: 120000 },
            { name: 'wrist_control_4', label: '手腕控制任务4', instruction: '用手腕动作控制光标移动到目标区域', icon: '🔄', color: '#f59e0b', maxTrials: 10, timeout: 120000 }
        ]
    }
};

// HTML中的data-task属性到内部ID的映射
const TASK_ID_MAP = {
    'discrete': 'discrete_gesture',
    'continuous1': 'continual_gesture_1',
    'continuous2': 'continual_gesture_2'
};

// TaskConfig API（兼容原task-config.js的接口）
window.TaskConfig = {
    DEFINITIONS: TASK_DEFINITIONS,
    ID_MAP: TASK_ID_MAP,
    
    getTaskIds() {
        return Object.keys(TASK_DEFINITIONS);
    },
    
    getTaskConfig(taskId) {
        return TASK_DEFINITIONS[taskId] || null;
    },
    
    getTaskStages(taskId) {
        const config = TASK_DEFINITIONS[taskId];
        return config ? config.stages : [];
    },
    
    getTaskType(taskId) {
        const config = TASK_DEFINITIONS[taskId];
        return config ? (config.taskType || 'prompt_sequence') : 'prompt_sequence';
    },
    
    isWheelTask(taskId) {
        return this.getTaskType(taskId) === 'wheel_cursor';
    },
    
    addTaskDefinition(taskId, taskConfig) {
        if (TASK_DEFINITIONS[taskId]) {
            console.warn(`[TaskConfig] 任务 ${taskId} 已存在，将被覆盖`);
        }
        TASK_DEFINITIONS[taskId] = taskConfig;
        console.log(`[TaskConfig] 已添加任务: ${taskId}`);
    }
};

// 打印加载信息
console.log('[Constants] 采集常量已加载（v4 - 整合TaskConfig + 支持外部配置）');
console.log('[Constants] 离散手势 Stage数:', Object.keys(DISCRETE_GESTURE_CONFIG.STAGES).length);
console.log('[Constants] 离散手势 Prompt库:', Object.keys(DISCRETE_GESTURE_CONFIG.PROMPT_LIBRARY).length, '个动作');
console.log('[Constants] 连续手势1 Stage数:', Object.keys(CONTINUAL_GESTURE_1_CONFIG.STAGES).length, '(滚轮任务)');
console.log('[Constants] 连续手势2 Stage数:', Object.keys(CONTINUAL_GESTURE_2_CONFIG.STAGES).length, '(滚轮任务)');
console.log('[Constants] TaskConfig已整合，共', Object.keys(TASK_DEFINITIONS).length, '个任务定义');
