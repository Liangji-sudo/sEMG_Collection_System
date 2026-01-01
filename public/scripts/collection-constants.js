/**
 * collection-constants.js - 采集系统常量配置（重构版）
 * 
 * 重构说明：
 * - 每种采集任务（discrete_gesture, continual_gesture_1, continual_gesture_2）
 *   都有独立的细节配置
 * - 不再使用统一的倒计时，改为用prompt数量来控制每个stage的时长
 * - 每个prompt约1秒滚过指示线
 * 
 * 时间单位：毫秒 (ms)
 */

const COLLECTION_CONSTANTS = {
    // ==================== 开场动画配置 ====================
    INTRO: {
        // 开场动画持续时间（点击开始后，第一个stage之前播放）
        DURATION: 10000,        // 10秒
        
        // 开场动画类型: 'countdown' | 'video' | 'custom'
        TYPE: 'countdown',
        
        // 如果是视频，视频URL
        VIDEO_URL: '',
    },

    // ==================== 通用UI配置 ====================
    UI: {
        // 进度条动画过渡时间
        PROGRESS_TRANSITION: 300, // 300ms
        
        // Toast提示显示时间
        TOAST_DURATION: 3000,     // 3秒
        
        // 状态更新防抖时间
        STATUS_DEBOUNCE: 100,     // 100ms
    },

    // ==================== Stage间准备配置 ====================
    STAGE_PREPARE: {
        // stage之间的准备倒计时（秒数）
        COUNTDOWN_SECONDS: 3,
        
        // 每个倒计时数字显示的间隔
        COUNTDOWN_INTERVAL: 1000, // 1秒
    },

    // ==================== 离散手势采集配置 ====================
    DISCRETE_GESTURE: {
        // 任务名称
        NAME: '离散手势采集',
        
        // 每个stage的prompt数量（每个prompt约1秒）
        PROMPTS_PER_STAGE: 10,
        
        // prompt滚动速度（像素/帧，60fps）
        SCROLL_SPEED: 2,
        
        // prompt之间的间距（像素）
        PROMPT_SPACING: 120,
        
        // 各个stage的配置
        STAGES: {
            palm_up: {
                name: 'palm_up',
                label: '手心向上',
                promptCount: 10,      // 这个stage需要采集10个prompt
                icon: '↑',
                color: '#3b82f6',
                instruction: '请保持手心向上的姿势'
            },
            palm_inward: {
                name: 'palm_inward',
                label: '手心向内',
                promptCount: 10,
                icon: '→',
                color: '#3b82f6',
                instruction: '请保持手心向内的姿势'
            },
            hand_on_knee: {
                name: 'hand_on_knee',
                label: '手放膝盖',
                promptCount: 10,
                icon: '↓',
                color: '#3b82f6',
                instruction: '请将手放在膝盖上'
            },
            hand_on_desk: {
                name: 'hand_on_desk',
                label: '手放桌上',
                promptCount: 10,
                icon: '◐',
                color: '#3b82f6',
                instruction: '请将手放在桌上'
            }
        },
        
        // 动画配置
        ANIMATION: {
            // 指示线位置（相对于画布宽度的比例）
            INDICATOR_POSITION: 0.3,
            
            // 提示竖线长度
            PROMPT_LENGTH: 60,
            
            // 提示竖线粗细
            PROMPT_THICKNESS: 10,
            
            // 标签偏移
            LABEL_OFFSET: 80,
            
            // 颜色配置
            COLORS: {
                active: '#10b981',    // 激活颜色（绿色）
                passed: '#9ca3af',    // 已过颜色（灰色）
                normal: '#3b82f6',    // 普通颜色（蓝色）
                indicator: '#ef4444'  // 指示线颜色（红色）
            }
        }
    },

    // ==================== 连续手势1采集配置 ====================
    CONTINUAL_GESTURE_1: {
        NAME: '连续手势采集1（手指）',
        
        PROMPTS_PER_STAGE: 12,
        SCROLL_SPEED: 2,
        PROMPT_SPACING: 120,
        
        STAGES: {
            finger_spread: {
                name: 'finger_spread',
                label: '手指张合',
                promptCount: 12,
                icon: '✋',
                color: '#10b981',
                instruction: '请进行手指张合动作'
            },
            finger_tap: {
                name: 'finger_tap',
                label: '手指点击',
                promptCount: 12,
                icon: '👆',
                color: '#10b981',
                instruction: '请进行手指点击动作'
            },
            finger_extend: {
                name: 'finger_extend',
                label: '手指伸展',
                promptCount: 12,
                icon: '🖐',
                color: '#10b981',
                instruction: '请进行手指伸展动作'
            },
            finger_curl: {
                name: 'finger_curl',
                label: '手指弯曲',
                promptCount: 12,
                icon: '✊',
                color: '#10b981',
                instruction: '请进行手指弯曲动作'
            }
        },
        
        ANIMATION: {
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
    },

    // ==================== 连续手势2采集配置 ====================
    CONTINUAL_GESTURE_2: {
        NAME: '连续手势采集2（手腕）',
        
        PROMPTS_PER_STAGE: 12,
        SCROLL_SPEED: 2,
        PROMPT_SPACING: 120,
        
        STAGES: {
            wrist_rotation: {
                name: 'wrist_rotation',
                label: '手腕旋转',
                promptCount: 12,
                icon: '🔄',
                color: '#f59e0b',
                instruction: '请进行手腕旋转动作'
            },
            wrist_updown: {
                name: 'wrist_updown',
                label: '手腕上下',
                promptCount: 12,
                icon: '↕',
                color: '#f59e0b',
                instruction: '请进行手腕上下摆动'
            },
            wrist_leftright: {
                name: 'wrist_leftright',
                label: '手腕左右',
                promptCount: 12,
                icon: '↔',
                color: '#f59e0b',
                instruction: '请进行手腕左右摆动'
            },
            fist_rotation: {
                name: 'fist_rotation',
                label: '握拳旋转',
                promptCount: 12,
                icon: '👊',
                color: '#f59e0b',
                instruction: '请握拳并旋转'
            }
        },
        
        ANIMATION: {
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
    },

    // ==================== 调试配置 ====================
    DEBUG: {
        // 是否启用调试模式（启用后会有更多日志输出）
        ENABLED: true,
        
        // 是否使用快速模式（测试时缩短所有时间）
        FAST_MODE: false,
        
        // 快速模式下的时间倍率（0.1 = 原来的10%时间）
        FAST_MODE_RATIO: 0.1,
    }
};

/**
 * 便捷访问对象 - CollectionTiming
 * 提供各种时间和配置的快捷获取方法
 */
const CollectionTiming = {
    // 获取开场动画时长
    getIntroDuration() {
        let duration = COLLECTION_CONSTANTS.INTRO.DURATION;
        if (COLLECTION_CONSTANTS.DEBUG.FAST_MODE) {
            duration = Math.max(duration * COLLECTION_CONSTANTS.DEBUG.FAST_MODE_RATIO, 1000);
        }
        return duration;
    },
    
    // 获取准备倒计时秒数
    getPrepareCountdown() {
        let seconds = COLLECTION_CONSTANTS.STAGE_PREPARE.COUNTDOWN_SECONDS;
        if (COLLECTION_CONSTANTS.DEBUG.FAST_MODE) {
            seconds = Math.max(Math.floor(seconds * COLLECTION_CONSTANTS.DEBUG.FAST_MODE_RATIO), 1);
        }
        return seconds;
    },
    
    // 获取指定任务的配置
    getTaskConfig(taskId) {
        const configMap = {
            'discrete_gesture': COLLECTION_CONSTANTS.DISCRETE_GESTURE,
            'continual_gesture_1': COLLECTION_CONSTANTS.CONTINUAL_GESTURE_1,
            'continual_gesture_2': COLLECTION_CONSTANTS.CONTINUAL_GESTURE_2
        };
        return configMap[taskId] || COLLECTION_CONSTANTS.DISCRETE_GESTURE;
    },
    
    // 获取指定任务的stage配置
    getStageConfig(taskId, stageName) {
        const taskConfig = this.getTaskConfig(taskId);
        return taskConfig.STAGES[stageName] || null;
    },
    
    // 获取指定stage的prompt数量
    getPromptCount(taskId, stageName) {
        const stageConfig = this.getStageConfig(taskId, stageName);
        if (stageConfig && stageConfig.promptCount) {
            let count = stageConfig.promptCount;
            if (COLLECTION_CONSTANTS.DEBUG.FAST_MODE) {
                count = Math.max(Math.floor(count * COLLECTION_CONSTANTS.DEBUG.FAST_MODE_RATIO), 3);
            }
            return count;
        }
        // 默认值
        const taskConfig = this.getTaskConfig(taskId);
        let count = taskConfig.PROMPTS_PER_STAGE || 10;
        if (COLLECTION_CONSTANTS.DEBUG.FAST_MODE) {
            count = Math.max(Math.floor(count * COLLECTION_CONSTANTS.DEBUG.FAST_MODE_RATIO), 3);
        }
        return count;
    },
    
    // 获取动画配置
    getAnimationConfig(taskId) {
        const taskConfig = this.getTaskConfig(taskId);
        return taskConfig.ANIMATION || COLLECTION_CONSTANTS.DISCRETE_GESTURE.ANIMATION;
    },
    
    // 获取滚动速度
    getScrollSpeed(taskId) {
        const taskConfig = this.getTaskConfig(taskId);
        return taskConfig.SCROLL_SPEED || 2;
    },
    
    // 获取prompt间距
    getPromptSpacing(taskId) {
        const taskConfig = this.getTaskConfig(taskId);
        return taskConfig.PROMPT_SPACING || 120;
    },
    
    // 是否调试模式
    isDebugMode() {
        return COLLECTION_CONSTANTS.DEBUG.ENABLED;
    },
    
    // 是否快速模式
    isFastMode() {
        return COLLECTION_CONSTANTS.DEBUG.FAST_MODE;
    },
    
    /**
     * 根据prompt数量计算预估的stage时长（毫秒）
     * 每个prompt约1秒（60fps, 2像素/帧, 120像素间距 = 1秒）
     */
    estimateStageDuration(taskId, stageName) {
        const promptCount = this.getPromptCount(taskId, stageName);
        // 每个prompt约1秒，加上一些缓冲时间
        return (promptCount + 2) * 1000;
    }
};

// 导出到全局
window.COLLECTION_CONSTANTS = COLLECTION_CONSTANTS;
window.CollectionTiming = CollectionTiming;

// 打印加载信息
console.log('[Constants] 采集常量已加载（重构版）');
console.log('[Constants] 开场动画:', COLLECTION_CONSTANTS.INTRO.DURATION / 1000, '秒');
console.log('[Constants] 准备倒计时:', COLLECTION_CONSTANTS.STAGE_PREPARE.COUNTDOWN_SECONDS, '秒');
console.log('[Constants] 离散手势每stage prompts:', COLLECTION_CONSTANTS.DISCRETE_GESTURE.PROMPTS_PER_STAGE);
console.log('[Constants] 连续手势1每stage prompts:', COLLECTION_CONSTANTS.CONTINUAL_GESTURE_1.PROMPTS_PER_STAGE);
console.log('[Constants] 连续手势2每stage prompts:', COLLECTION_CONSTANTS.CONTINUAL_GESTURE_2.PROMPTS_PER_STAGE);
console.log('[Constants] 快速模式:', COLLECTION_CONSTANTS.DEBUG.FAST_MODE ? '开启' : '关闭');
