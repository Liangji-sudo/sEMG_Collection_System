/**
 * collection-controller-patch.js
 * 
 * 这个文件包含需要添加到 collection-controller.js 的标定功能代码
 * 
 * ============================================================
 * 使用方法：
 * 1. 在 constructor() 中添加标定相关属性
 * 2. 添加新的标定方法
 * 3. 修改 startContinualGestureCollection() 方法
 * ============================================================
 */

// ============================================================
// 第1部分：在 constructor() 中添加以下属性
// ============================================================

/*
在 constructor() 中添加（约在 this.continualProgressTimer 之后）：

            // ===== 标定相关状态 =====
            this.calibrationEnabled = true;     // 是否启用标定流程
            this.isCalibrating = false;         // 是否正在标定
            this.calibrationPhase = null;       // 'demo' | 'min' | 'max' | null
            this.calibrationTimer = null;       // 标定计时器
            this.skipCalibration = false;       // 是否跳过标定
*/


// ============================================================
// 第2部分：添加以下新方法
// ============================================================

// 将以下方法添加到 CollectionController 类中

/**
 * 启动标定流程
 */
startCalibrationFlow() {
    console.log('[Collection] ====== 启动标定流程 ======');
    
    this.isCalibrating = true;
    this.currentPhase = 'calibration';
    this.updateGestureList();
    
    const inputInterface = window.animationInputInterface;
    if (inputInterface) {
        inputInterface.setCurrentTask(this.currentTaskId);
    }
    
    this.sendToRealtimeEngine('task_change', { taskId: this.currentTaskId });
    this.showCalibrationDemo();
}

/**
 * 显示标定示范动画
 */
showCalibrationDemo() {
    console.log('[Collection] 播放标定示范动画');
    
    this.calibrationPhase = 'demo';
    
    const guideAnimation = window.calibrationGuideAnimation;
    if (guideAnimation) {
        guideAnimation.show(
            this.currentTaskId,
            'demo',
            () => { this.startMinCalibration(); },
            () => { this.startMinCalibration(); }  // 跳过
        );
    } else {
        this.startMinCalibration();
    }
}

/**
 * 开始最小值标定
 */
startMinCalibration() {
    console.log('[Collection] 开始最小值标定');
    
    this.calibrationPhase = 'min';
    
    const inputInterface = window.animationInputInterface;
    const guideAnimation = window.calibrationGuideAnimation;
    
    if (inputInterface) {
        inputInterface.startCalibration(this.currentTaskId, 'min');
    }
    
    if (guideAnimation) {
        guideAnimation.show(
            this.currentTaskId,
            'calibrate_min',
            () => { this.endMinCalibration(); }
        );
    } else {
        this.showSimpleCalibrationCountdown('最小值', () => { this.endMinCalibration(); });
    }
}

/**
 * 结束最小值标定
 */
endMinCalibration() {
    console.log('[Collection] 最小值标定完成');
    
    const inputInterface = window.animationInputInterface;
    if (inputInterface) {
        const result = inputInterface.endCalibration();
        console.log('[Collection] 最小值标定结果:', result);
    }
    
    setTimeout(() => { this.startMaxCalibration(); }, 500);
}

/**
 * 开始最大值标定
 */
startMaxCalibration() {
    console.log('[Collection] 开始最大值标定');
    
    this.calibrationPhase = 'max';
    
    const inputInterface = window.animationInputInterface;
    const guideAnimation = window.calibrationGuideAnimation;
    
    if (inputInterface) {
        inputInterface.startCalibration(this.currentTaskId, 'max');
    }
    
    if (guideAnimation) {
        guideAnimation.show(
            this.currentTaskId,
            'calibrate_max',
            () => { this.endMaxCalibration(); }
        );
    } else {
        this.showSimpleCalibrationCountdown('最大值', () => { this.endMaxCalibration(); });
    }
}

/**
 * 结束最大值标定
 */
endMaxCalibration() {
    console.log('[Collection] 最大值标定完成');
    
    const inputInterface = window.animationInputInterface;
    if (inputInterface) {
        const result = inputInterface.endCalibration();
        console.log('[Collection] 最大值标定结果:', result);
    }
    
    this.onCalibrationComplete();
}

/**
 * 标定流程完成
 */
onCalibrationComplete() {
    console.log('[Collection] ====== 标定流程完成 ======');
    
    this.isCalibrating = false;
    this.calibrationPhase = null;
    
    const inputInterface = window.animationInputInterface;
    if (inputInterface) {
        const status = inputInterface.getCalibrationStatus();
        console.log('[Collection] 标定状态:', status);
        
        if (status.isCalibrated) {
            this.showToast(`标定完成: min=${status.min?.toFixed(1)}, max=${status.max?.toFixed(1)}`, 'success');
        }
    }
    
    this.updateGestureDisplay({
        name: '标定完成',
        instruction: '即将开始正式采集...',
        showCountdown: false
    });
    
    setTimeout(() => {
        this.currentPhase = 'prepare';
        this.updateGestureList();
        this.showContinualPreparation(() => {
            this.startContinualAnimation();
        });
    }, 1500);
}

/**
 * 显示简单的标定倒计时（当没有guideAnimation时使用）
 */
showSimpleCalibrationCountdown(label, callback) {
    const duration = 4;
    
    this.updateGestureDisplay({
        name: `标定${label}`,
        instruction: `请保持${label}姿势...`,
        showCountdown: true,
        countdownValue: duration
    });
    
    let countdown = duration;
    const countdownEl = document.getElementById('countdown');
    
    this.calibrationTimer = setInterval(() => {
        countdown--;
        if (countdownEl) countdownEl.textContent = countdown;
        
        if (countdown <= 0) {
            clearInterval(this.calibrationTimer);
            this.calibrationTimer = null;
            if (countdownEl) countdownEl.style.display = 'none';
            callback();
        }
    }, 1000);
}

/**
 * 跳过标定流程
 */
skipCalibrationFlow() {
    console.log('[Collection] 跳过标定流程');
    
    if (this.calibrationTimer) {
        clearInterval(this.calibrationTimer);
        this.calibrationTimer = null;
    }
    
    const guideAnimation = window.calibrationGuideAnimation;
    if (guideAnimation) guideAnimation.hide();
    
    this.skipCalibration = true;
    this.isCalibrating = false;
    this.calibrationPhase = null;
    
    this.currentPhase = 'prepare';
    this.updateGestureList();
    this.showContinualPreparation(() => {
        this.startContinualAnimation();
    });
}

/**
 * 重置标定
 */
resetCalibration() {
    const inputInterface = window.animationInputInterface;
    if (inputInterface) {
        inputInterface.resetCalibration(this.currentTaskId);
        this.showToast('标定已重置', 'info');
    }
    this.skipCalibration = false;
}


// ============================================================
// 第3部分：修改 startContinualGestureCollection() 方法
// ============================================================

/*
将原来的 startContinualGestureCollection() 方法修改为：
*/

startContinualGestureCollection() {
    console.log('[Collection] 开始连续手势采集');
    console.log('[Collection] 任务类型:', this.currentTaskId);
    console.log('[Collection] ★★★ 执行参数 ★★★:', this.currentExecutionParams);
    
    const currentStage = this.stages[this.currentStageIndex];
    this.continualTrialCount = 0;
    
    // 【新增】检查是否需要标定
    const inputInterface = window.animationInputInterface;
    const needCalibration = this.calibrationEnabled !== false && 
                           inputInterface && 
                           !inputInterface.isCalibrated();
    
    if (needCalibration && !this.skipCalibration) {
        console.log('[Collection] 需要标定，启动标定流程');
        this.startCalibrationFlow();
    } else {
        console.log('[Collection] 跳过标定，直接开始采集');
        this.currentPhase = 'prepare';
        this.updateGestureList();
        
        this.showContinualPreparation(() => {
            this.startContinualAnimation();
        });
    }
}


// ============================================================
// 第4部分：在 stopTask() 中添加标定相关清理
// ============================================================

/*
在 stopTask() 方法中添加（在清除 continualProgressTimer 之后）：

            // 清理标定状态
            if (this.calibrationTimer) {
                clearInterval(this.calibrationTimer);
                this.calibrationTimer = null;
            }
            this.isCalibrating = false;
            this.calibrationPhase = null;
            
            // 隐藏标定指导动画
            if (window.calibrationGuideAnimation) {
                window.calibrationGuideAnimation.hide();
            }
*/


// ============================================================
// 第5部分：修改动画模块的 start() 方法
// ============================================================

/*
在 continual-gesture-X-animation.js 的 start() 方法开头添加：

            // 获取输入接口
            this.inputInterface = window.animationInputInterface || null;
            if (this.inputInterface) {
                this.inputInterface.setCurrentTask('continual_gesture_1');  // 或 2/3
            }
*/


// ============================================================
// 第6部分：修改动画模块的 animate() 方法
// ============================================================

/*
在 continual-gesture-1/2-animation.js 的 animate() 方法中，
在 updateGuideRadius() 之后添加：

            // 从输入接口更新用户控制的半径
            if (this.inputInterface && this.inputInterface.isCalibrated()) {
                const normalizedInput = this.inputInterface.getNormalizedInput();
                this.userRadius = normalizedInput * this.maxRadius;
            }

在 continual-gesture-3-animation.js 的 animate() 方法中，
在 updateGuidePosition() 之后添加：

            // 从输入接口更新光标位置
            if (this.inputInterface && this.inputInterface.isCalibrated()) {
                this.cursorPosition = this.inputInterface.getNormalizedInput();
            }
*/
