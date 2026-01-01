/**
 * collection-constants.js - 采集系统常量配置
 * 
 * 这个文件集中管理所有与采集流程相关的常量
 * 修改这里的值可以调整整个采集流程的时间参数
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

    // ==================== Stage间隔配置 ====================
    STAGE: {
        // 每个stage的内容动画持续时间
        CONTENT_DURATION: 5000,   // 5秒
        
        // stage之间的准备倒计时
        PREPARE_COUNTDOWN: 3,     // 3秒（这是秒数，不是毫秒）
        
        // 每个倒计时数字显示的间隔
        COUNTDOWN_INTERVAL: 1000, // 1秒
    },

    // ==================== 倒计时显示配置 ====================
    COUNTDOWN: {
        // 倒计时数字的动画效果持续时间
        ANIMATION_DURATION: 300,  // 300ms
        
        // 倒计时完成后到下一步的延迟
        COMPLETE_DELAY: 500,      // 500ms
    },

    // ==================== UI更新配置 ====================
    UI: {
        // 进度条动画过渡时间
        PROGRESS_TRANSITION: 300, // 300ms
        
        // Toast提示显示时间
        TOAST_DURATION: 3000,     // 3秒
        
        // 状态更新防抖时间
        STATUS_DEBOUNCE: 100,     // 100ms
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

// 获取实际使用的时间值（考虑快速模式）
function getActualTime(baseTime) {
    if (COLLECTION_CONSTANTS.DEBUG.FAST_MODE) {
        return Math.max(baseTime * COLLECTION_CONSTANTS.DEBUG.FAST_MODE_RATIO, 500);
    }
    return baseTime;
}

// 便捷访问函数
const CollectionTiming = {
    // 获取开场动画时长
    getIntroDuration() {
        return getActualTime(COLLECTION_CONSTANTS.INTRO.DURATION);
    },
    
    // 获取stage内容时长
    getStageDuration() {
        return getActualTime(COLLECTION_CONSTANTS.STAGE.CONTENT_DURATION);
    },
    
    // 获取准备倒计时秒数
    getPrepareCountdown() {
        // 倒计时秒数在快速模式下最少1秒
        if (COLLECTION_CONSTANTS.DEBUG.FAST_MODE) {
            return Math.max(Math.floor(COLLECTION_CONSTANTS.STAGE.PREPARE_COUNTDOWN * COLLECTION_CONSTANTS.DEBUG.FAST_MODE_RATIO), 1);
        }
        return COLLECTION_CONSTANTS.STAGE.PREPARE_COUNTDOWN;
    },
    
    // 获取倒计时间隔
    getCountdownInterval() {
        return getActualTime(COLLECTION_CONSTANTS.STAGE.COUNTDOWN_INTERVAL);
    },
    
    // 是否调试模式
    isDebugMode() {
        return COLLECTION_CONSTANTS.DEBUG.ENABLED;
    },
    
    // 是否快速模式
    isFastMode() {
        return COLLECTION_CONSTANTS.DEBUG.FAST_MODE;
    }
};

// 导出到全局
window.COLLECTION_CONSTANTS = COLLECTION_CONSTANTS;
window.CollectionTiming = CollectionTiming;

console.log('[Constants] 采集常量已加载');
console.log('[Constants] 开场动画:', COLLECTION_CONSTANTS.INTRO.DURATION / 1000, '秒');
console.log('[Constants] Stage内容:', COLLECTION_CONSTANTS.STAGE.CONTENT_DURATION / 1000, '秒');
console.log('[Constants] 准备倒计时:', COLLECTION_CONSTANTS.STAGE.PREPARE_COUNTDOWN, '秒');
console.log('[Constants] 快速模式:', COLLECTION_CONSTANTS.DEBUG.FAST_MODE ? '开启' : '关闭');
