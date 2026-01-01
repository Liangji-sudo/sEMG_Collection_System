/**
 * collection-constants.js - 采集系统常量配置（重构版v2）
 * 
 * 概念说明：
 * - Task（任务）: 如 discrete_gesture（离散手势采集）
 * - Stage（阶段）: 任务中的一个采集阶段，如 "手心朝上" 姿势
 * - Prompt（提示）: Stage内的具体手势动作，如 "拇指上滑"、"食指点击" 等
 * 
 * 每个Stage会按顺序播放多个Prompt动画
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
 * ==================== 连续手势1采集配置（手指） ====================
 */
const CONTINUAL_GESTURE_1_CONFIG = {
    NAME: '连续手势采集1（手指）',
    
    PROMPT_LIBRARY: {
        'finger_spread_open': { label: '手指张开', icon: '🖐️', color: '#10b981' },
        'finger_spread_close': { label: '手指合拢', icon: '✊', color: '#10b981' },
        'finger_wave': { label: '手指波浪', icon: '👋', color: '#10b981' },
        'finger_tap_seq': { label: '手指依次敲击', icon: '🎹', color: '#10b981' },
        'finger_pinch_release': { label: '捏合松开', icon: '🤏', color: '#10b981' },
        'thumb_circle': { label: '拇指画圈', icon: '⭕', color: '#10b981' },
        'index_circle': { label: '食指画圈', icon: '⭕', color: '#10b981' },
        'ok_gesture': { label: 'OK手势', icon: '👌', color: '#10b981' },
        'victory_gesture': { label: '胜利手势', icon: '✌️', color: '#10b981' },
        'rock_gesture': { label: '摇滚手势', icon: '🤘', color: '#10b981' },
    },

    STAGES: {
        finger_spread: {
            name: 'finger_spread',
            label: '手指张合',
            instruction: '请跟随提示进行手指张合动作',
            icon: '✋',
            color: '#10b981',
            promptSequence: [
                'finger_spread_open',
                'finger_spread_close',
                'finger_spread_open',
                'finger_spread_close',
                'finger_pinch_release',
                'finger_pinch_release',
                'finger_wave',
                'finger_wave',
                'ok_gesture',
                'victory_gesture',
                'rock_gesture',
                'finger_spread_open'
            ]
        },
        finger_tap: {
            name: 'finger_tap',
            label: '手指点击',
            instruction: '请跟随提示进行手指点击动作',
            icon: '👆',
            color: '#10b981',
            promptSequence: [
                'finger_tap_seq',
                'finger_tap_seq',
                'finger_tap_seq',
                'thumb_circle',
                'index_circle',
                'finger_wave',
                'finger_pinch_release',
                'ok_gesture',
                'victory_gesture',
                'rock_gesture',
                'finger_tap_seq',
                'finger_spread_open'
            ]
        },
        finger_extend: {
            name: 'finger_extend',
            label: '手指伸展',
            instruction: '请跟随提示进行手指伸展动作',
            icon: '🖐',
            color: '#10b981',
            promptSequence: [
                'finger_spread_open',
                'victory_gesture',
                'rock_gesture',
                'ok_gesture',
                'finger_wave',
                'finger_tap_seq',
                'thumb_circle',
                'index_circle',
                'finger_spread_close',
                'finger_spread_open',
                'finger_pinch_release',
                'finger_spread_close'
            ]
        },
        finger_curl: {
            name: 'finger_curl',
            label: '手指弯曲',
            instruction: '请跟随提示进行手指弯曲动作',
            icon: '✊',
            color: '#10b981',
            promptSequence: [
                'finger_spread_close',
                'finger_pinch_release',
                'finger_spread_close',
                'ok_gesture',
                'rock_gesture',
                'finger_wave',
                'finger_tap_seq',
                'finger_spread_open',
                'finger_spread_close',
                'thumb_circle',
                'index_circle',
                'finger_spread_close'
            ]
        }
    },

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
            normal: '#10b981',
            indicator: '#ef4444'
        }
    }
};

/**
 * ==================== 连续手势2采集配置（手腕） ====================
 */
const CONTINUAL_GESTURE_2_CONFIG = {
    NAME: '连续手势采集2（手腕）',
    
    PROMPT_LIBRARY: {
        'wrist_flex_up': { label: '手腕上屈', icon: '⬆️', color: '#f59e0b' },
        'wrist_flex_down': { label: '手腕下屈', icon: '⬇️', color: '#f59e0b' },
        'wrist_left': { label: '手腕左偏', icon: '⬅️', color: '#f59e0b' },
        'wrist_right': { label: '手腕右偏', icon: '➡️', color: '#f59e0b' },
        'wrist_rotate_in': { label: '手腕内旋', icon: '🔄', color: '#f59e0b' },
        'wrist_rotate_out': { label: '手腕外旋', icon: '🔃', color: '#f59e0b' },
        'wrist_circle_cw': { label: '手腕顺时针绕圈', icon: '⭕', color: '#f59e0b' },
        'wrist_circle_ccw': { label: '手腕逆时针绕圈', icon: '⭕', color: '#f59e0b' },
        'fist_rotate_in': { label: '握拳内旋', icon: '👊', color: '#f59e0b' },
        'fist_rotate_out': { label: '握拳外旋', icon: '👊', color: '#f59e0b' },
        'fist_pump': { label: '握拳上下', icon: '💪', color: '#f59e0b' },
        'wrist_shake': { label: '手腕抖动', icon: '👋', color: '#f59e0b' },
    },

    STAGES: {
        wrist_rotation: {
            name: 'wrist_rotation',
            label: '手腕旋转',
            instruction: '请跟随提示进行手腕旋转动作',
            icon: '🔄',
            color: '#f59e0b',
            promptSequence: [
                'wrist_rotate_in',
                'wrist_rotate_out',
                'wrist_rotate_in',
                'wrist_rotate_out',
                'wrist_circle_cw',
                'wrist_circle_ccw',
                'wrist_circle_cw',
                'wrist_circle_ccw',
                'fist_rotate_in',
                'fist_rotate_out',
                'wrist_shake',
                'wrist_rotate_in'
            ]
        },
        wrist_updown: {
            name: 'wrist_updown',
            label: '手腕上下',
            instruction: '请跟随提示进行手腕上下摆动',
            icon: '↕',
            color: '#f59e0b',
            promptSequence: [
                'wrist_flex_up',
                'wrist_flex_down',
                'wrist_flex_up',
                'wrist_flex_down',
                'wrist_flex_up',
                'wrist_flex_down',
                'fist_pump',
                'fist_pump',
                'wrist_shake',
                'wrist_flex_up',
                'wrist_flex_down',
                'wrist_flex_up'
            ]
        },
        wrist_leftright: {
            name: 'wrist_leftright',
            label: '手腕左右',
            instruction: '请跟随提示进行手腕左右摆动',
            icon: '↔',
            color: '#f59e0b',
            promptSequence: [
                'wrist_left',
                'wrist_right',
                'wrist_left',
                'wrist_right',
                'wrist_left',
                'wrist_right',
                'wrist_circle_cw',
                'wrist_circle_ccw',
                'wrist_shake',
                'wrist_left',
                'wrist_right',
                'wrist_left'
            ]
        },
        fist_rotation: {
            name: 'fist_rotation',
            label: '握拳旋转',
            instruction: '请握拳并跟随提示旋转',
            icon: '👊',
            color: '#f59e0b',
            promptSequence: [
                'fist_rotate_in',
                'fist_rotate_out',
                'fist_rotate_in',
                'fist_rotate_out',
                'fist_pump',
                'fist_pump',
                'wrist_circle_cw',
                'wrist_circle_ccw',
                'fist_rotate_in',
                'fist_rotate_out',
                'wrist_shake',
                'fist_rotate_in'
            ]
        }
    },

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
            normal: '#f59e0b',
            indicator: '#ef4444'
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
    
    // 获取Stage配置
    getStageConfig(taskId, stageName) {
        const taskConfig = this.getTaskConfig(taskId);
        return taskConfig.STAGES[stageName] || null;
    },
    
    // 获取Stage的Prompt序列
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
        return taskConfig.PROMPT_LIBRARY[promptName] || { 
            label: promptName, 
            icon: '●', 
            color: '#6b7280' 
        };
    },
    
    // 获取动画配置
    getAnimationConfig(taskId) {
        const taskConfig = this.getTaskConfig(taskId);
        return taskConfig.ANIMATION || DISCRETE_GESTURE_CONFIG.ANIMATION;
    },
    
    // 获取Stage的Prompt数量
    getPromptCount(taskId, stageName) {
        const sequence = this.getPromptSequence(taskId, stageName);
        return sequence.length;
    },
    
    // 估算Stage时长（基于Prompt数量）
    estimateStageDuration(taskId, stageName) {
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

// 打印加载信息
console.log('[Constants] 采集常量已加载（v2）');
console.log('[Constants] 离散手势 Stage数:', Object.keys(DISCRETE_GESTURE_CONFIG.STAGES).length);
console.log('[Constants] 离散手势 Prompt库:', Object.keys(DISCRETE_GESTURE_CONFIG.PROMPT_LIBRARY).length, '个动作');
console.log('[Constants] 连续手势1 Stage数:', Object.keys(CONTINUAL_GESTURE_1_CONFIG.STAGES).length);
console.log('[Constants] 连续手势2 Stage数:', Object.keys(CONTINUAL_GESTURE_2_CONFIG.STAGES).length);