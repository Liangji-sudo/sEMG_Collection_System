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
 * - stages: stage数组，每个stage包含：
 *   - name: stage唯一标识符（英文，用于数据标记）
 *   - label: stage显示名称（中文）
 *   - instruction: stage指导说明（可选）
 *   - animation: 该stage使用的动画ID（可选，默认使用通用动画）
 */

const TASK_DEFINITIONS = {
    // ==================== 离散手势采集任务 ====================
    discrete_gesture: {
        id: 'discrete_gesture',
        name: '离散手势',
        description: '4种单独手势采集',
        icon: 'fa-hand-paper',
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

    // ==================== 连续手势1采集任务 ====================
    continual_gesture_1: {
        id: 'continual_gesture_1',
        name: '连续手势1',
        description: '手指连续运动',
        icon: 'fa-hand-point-up',
        stages: [
            { 
                name: 'finger_spread', 
                label: '手指张合',
                instruction: '请反复张开和握拢手指',
                animation: 'finger_spread_anim'
            },
            { 
                name: 'finger_tap', 
                label: '手指交替点击',
                instruction: '请用手指交替点击桌面',
                animation: 'finger_tap_anim'
            },
            { 
                name: 'finger_extend', 
                label: '手指伸展',
                instruction: '请依次伸展每根手指',
                animation: 'finger_extend_anim'
            },
            { 
                name: 'finger_curl', 
                label: '手指弯曲',
                instruction: '请依次弯曲每根手指',
                animation: 'finger_curl_anim'
            }
        ]
    },

    // ==================== 连续手势2采集任务 ====================
    continual_gesture_2: {
        id: 'continual_gesture_2',
        name: '连续手势2',
        description: '手腕连续运动',
        icon: 'fa-hand-peace',
        stages: [
            { 
                name: 'wrist_rotation', 
                label: '手腕旋转',
                instruction: '请缓慢旋转手腕',
                animation: 'wrist_rotation_anim'
            },
            { 
                name: 'wrist_updown', 
                label: '手腕上下',
                instruction: '请上下摆动手腕',
                animation: 'wrist_updown_anim'
            },
            { 
                name: 'wrist_leftright', 
                label: '手腕左右',
                instruction: '请左右摆动手腕',
                animation: 'wrist_leftright_anim'
            },
            { 
                name: 'fist_rotation', 
                label: '握拳旋转',
                instruction: '请握拳并旋转手腕',
                animation: 'fist_rotation_anim'
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
    addTaskDefinition
};

console.log('[TaskConfig] 任务配置已加载，共', Object.keys(TASK_DEFINITIONS).length, '个任务');
