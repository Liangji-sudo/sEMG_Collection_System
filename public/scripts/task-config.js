/**
 * task-config.js - 采集任务配置文件
 * 
 * 这个文件定义了所有采集任务的stage配置
 * 你可以在这里自定义任务类型和每个任务包含的stage
 * 
 * 配置说明：
 * - id: 任务唯一标识符
 * - name: 任务显示名称
 * - description: 任务描述
 * - icon: FontAwesome图标类名
 * - taskType: 任务类型 ('prompt_sequence' | 'wheel_cursor')
 *   - prompt_sequence: 离散手势，按Prompt序列采集
 *   - wheel_cursor: 滚轮光标任务，目标追踪
 * - stages: stage数组，每个stage包含：
 *   - name: stage唯一标识符（英文，用于数据标记）
 *   - label: stage显示名称（中文）
 *   - instruction: stage指导说明（可选）
 *   - animation: 该stage使用的动画ID（可选，默认使用通用动画）
 *   - maxTrials: 滚轮任务的目标数量（仅wheel_cursor类型）
 *   - timeout: 滚轮任务的超时时间ms（仅wheel_cursor类型）
 */

const TASK_DEFINITIONS = {
    // ==================== 离散手势采集任务 ====================
    discrete_gesture: {
        id: 'discrete_gesture',
        name: '离散手势',
        description: '4种单独手势采集',
        icon: 'fa-hand-paper',
        taskType: 'prompt_sequence',  // 使用Prompt序列模式
        stages: [
            { 
                name: 'palm_up', 
                label: '手心向上',
                instruction: '请将手掌向上平放',
                animation: 'palm_up_anim'
            },
            { 
                name: 'palm_inward', 
                label: '手心向内',
                instruction: '请将手心朝向身体',
                animation: 'palm_inward_anim'
            },
            { 
                name: 'hand_on_knee', 
                label: '手放膝盖',
                instruction: '请将手自然放在膝盖上',
                animation: 'hand_on_knee_anim'
            },
            { 
                name: 'hand_on_desk', 
                label: '手放桌上',
                instruction: '请将手自然放在桌面上',
                animation: 'hand_on_desk_anim'
            }
        ]
    },

    // ==================== 连续手势1采集任务（滚轮光标） ====================
    continual_gesture_1: {
        id: 'continual_gesture_1',
        name: '连续手势1',
        description: '滚轮控制光标任务',
        icon: 'fa-hand-point-up',
        taskType: 'wheel_cursor',  // 使用滚轮光标模式
        stages: [
            { 
                name: 'wheel_task_1', 
                label: '滚轮控制任务1',
                instruction: '滚动滚轮将光标移动到蓝色目标区域，保持500ms命中',
                icon: '🎯',
                color: '#10b981',
                maxTrials: 10,      // 10个目标
                timeout: 120000     // 120秒超时
            },
            { 
                name: 'wheel_task_2', 
                label: '滚轮控制任务2',
                instruction: '滚动滚轮将光标移动到蓝色目标区域，保持500ms命中',
                icon: '🎯',
                color: '#10b981',
                maxTrials: 10,
                timeout: 120000
            },
            { 
                name: 'wheel_task_3', 
                label: '滚轮控制任务3',
                instruction: '滚动滚轮将光标移动到蓝色目标区域，保持500ms命中',
                icon: '🎯',
                color: '#10b981',
                maxTrials: 10,
                timeout: 120000
            },
            { 
                name: 'wheel_task_4', 
                label: '滚轮控制任务4',
                instruction: '滚动滚轮将光标移动到蓝色目标区域，保持500ms命中',
                icon: '🎯',
                color: '#10b981',
                maxTrials: 10,
                timeout: 120000
            }
        ]
    },

    // ==================== 连续手势2采集任务（滚轮光标） ====================
    continual_gesture_2: {
        id: 'continual_gesture_2',
        name: '连续手势2',
        description: '手腕控制光标任务',
        icon: 'fa-hand-peace',
        taskType: 'wheel_cursor',  // 使用滚轮光标模式
        stages: [
            { 
                name: 'wrist_control_1', 
                label: '手腕控制任务1',
                instruction: '用手腕动作控制光标移动到橙色目标区域',
                icon: '🔄',
                color: '#f59e0b',
                maxTrials: 10,
                timeout: 120000
            },
            { 
                name: 'wrist_control_2', 
                label: '手腕控制任务2',
                instruction: '用手腕动作控制光标移动到橙色目标区域',
                icon: '🔄',
                color: '#f59e0b',
                maxTrials: 10,
                timeout: 120000
            },
            { 
                name: 'wrist_control_3', 
                label: '手腕控制任务3',
                instruction: '用手腕动作控制光标移动到橙色目标区域',
                icon: '🔄',
                color: '#f59e0b',
                maxTrials: 10,
                timeout: 120000
            },
            { 
                name: 'wrist_control_4', 
                label: '手腕控制任务4',
                instruction: '用手腕动作控制光标移动到橙色目标区域',
                icon: '🔄',
                color: '#f59e0b',
                maxTrials: 10,
                timeout: 120000
            }
        ]
    }
};

// HTML中的data-task属性到内部ID的映射
const TASK_ID_MAP = {
    'discrete': 'discrete_gesture',
    'continuous1': 'continual_gesture_1',
    'continuous2': 'continual_gesture_2'
};

// 获取所有任务ID列表
function getTaskIds() {
    return Object.keys(TASK_DEFINITIONS);
}

// 获取指定任务的配置
function getTaskConfig(taskId) {
    return TASK_DEFINITIONS[taskId] || null;
}

// 获取指定任务的stage列表
function getTaskStages(taskId) {
    const config = TASK_DEFINITIONS[taskId];
    return config ? config.stages : [];
}

// 获取任务类型
function getTaskType(taskId) {
    const config = TASK_DEFINITIONS[taskId];
    return config ? (config.taskType || 'prompt_sequence') : 'prompt_sequence';
}

// 判断是否是滚轮光标任务
function isWheelTask(taskId) {
    return getTaskType(taskId) === 'wheel_cursor';
}

// 添加新任务（运行时动态添加）
function addTaskDefinition(taskId, taskConfig) {
    if (TASK_DEFINITIONS[taskId]) {
        console.warn(`[TaskConfig] 任务 ${taskId} 已存在，将被覆盖`);
    }
    TASK_DEFINITIONS[taskId] = taskConfig;
    console.log(`[TaskConfig] 已添加任务: ${taskId}`);
}

// 导出到全局
window.TaskConfig = {
    DEFINITIONS: TASK_DEFINITIONS,
    ID_MAP: TASK_ID_MAP,
    getTaskIds,
    getTaskConfig,
    getTaskStages,
    getTaskType,
    isWheelTask,
    addTaskDefinition
};

console.log('[TaskConfig] 任务配置已加载，共', Object.keys(TASK_DEFINITIONS).length, '个任务');
console.log('[TaskConfig] 滚轮任务:', Object.keys(TASK_DEFINITIONS).filter(id => TASK_DEFINITIONS[id].taskType === 'wheel_cursor').join(', '));
