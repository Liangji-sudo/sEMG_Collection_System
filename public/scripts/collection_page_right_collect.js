
// 2. 采集界面逻辑
const taskIntro = document.getElementById('task-intro');
const gestureAnimation = document.getElementById('gesture-animation');
const countdownArea = document.getElementById('countdown-area');
const collectingArea = document.getElementById('collecting-area');
const startCollectBtn = document.getElementById('start-collect');
//const skipAnimationBtn = document.getElementById('skip-animation');
//const prevGestureBtn = document.getElementById('prev-gesture');
//const nextGestureBtn = document.getElementById('next-gesture');
const restartCollectBtn = document.getElementById('restart-collect');
const countdownEl = document.getElementById('countdown');
const currentAnimationTitle = document.getElementById('current-animation-title');
const animationInstruction = document.getElementById('animation-instruction');
const currentTaskEl = document.getElementById('current-task');
const currentGestureEl = document.getElementById('current-gesture');
const taskProgressEl = document.getElementById('task-progress');

// 手势动画序列
const gestures = [
    {
    title: '离散手势：拇指上滑',
    instruction: '请跟随动画演示，做出拇指向上滑动的动作，保持动作清晰、连贯',
    animation: () => {
        document.getElementById('thumb').style.transform = 'translateX(-50%) translateY(-20px)';
    }
    },
    {
    title: '离散手势：拇指下滑',
    instruction: '请跟随动画演示，做出拇指向下滑动的动作，保持动作平稳',
    animation: () => {
        document.getElementById('thumb').style.transform = 'translateX(-50%) translateY(10px)';
    }
    },
    {
    title: '离散手势：拇指左滑',
    instruction: '请跟随动画演示，做出拇指向左滑动的动作，动作幅度适中',
    animation: () => {
        document.getElementById('thumb').style.transform = 'translateX(-70px) translateY(-4px)';
    }
    },
    {
    title: '离散手势：拇指右滑',
    instruction: '请跟随动画演示，做出拇指向右滑动的动作，动作幅度适中',
    animation: () => {
        document.getElementById('thumb').style.transform = 'translateX(20px) translateY(-4px)';
    }
    },
    {
    title: '连续手势-1：拇指食指捏合',
    instruction: '请跟随动画演示，拇指与食指从张开到捏合的连续动作，保持匀速',
    animation: () => {
        document.getElementById('thumb').style.transform = 'translateX(-30px) translateY(-4px)';
        document.getElementById('index-finger').style.transform = 'translateX(-10px) translateY(-4px)';
    }
    },
    {
    title: '连续手势-2：食指抬起',
    instruction: '请跟随动画演示，食指从弯曲到完全抬起的连续动作，保持平稳',
    animation: () => {
        document.getElementById('index-finger').style.transform = 'translateX(4px) translateY(-20px)';
    }
    }
];

let currentGestureIndex = 0;
let collectProgress = 0;
let currentTask = 1;
const progressBars = [
    document.getElementById('progress-bar-1'),
    document.getElementById('progress-bar-2')
];
const taskCircles = document.querySelectorAll('.rounded-full.bg-light-1');

// 开始采集流程
startCollectBtn.addEventListener('click', () => {
    taskIntro.classList.add('hidden');
    gestureAnimation.classList.remove('hidden');
    //skipAnimationBtn.classList.remove('hidden');
    startCollectBtn.classList.add('hidden');
    updateGestureAnimation(0);
});

// 跳过动画
/*
skipAnimationBtn.addEventListener('click', () => {
    gestureAnimation.classList.add('hidden');
    skipAnimationBtn.classList.add('hidden');
    countdownArea.classList.remove('hidden');
    startCountdown();
});
*/

// 上一个手势
/*
prevGestureBtn.addEventListener('click', () => {
    currentGestureIndex = Math.max(0, currentGestureIndex - 1);
    updateGestureAnimation(currentGestureIndex);
});
*/

// 下一个手势
nextGestureBtn.addEventListener('click', () => {
    currentGestureIndex = Math.min(gestures.length - 1, currentGestureIndex + 1);
    
    if (currentGestureIndex === gestures.length - 1) {
    gestureAnimation.classList.add('hidden');
    //skipAnimationBtn.classList.add('hidden');
    countdownArea.classList.remove('hidden');
    startCountdown();
    } else {
    updateGestureAnimation(currentGestureIndex);
    }
});

// 重新开始采集
restartCollectBtn.addEventListener('click', resetCollectPage);

// 更新手势动画
function updateGestureAnimation(index) {
    const gesture = gestures[index];
    currentGestureIndex = index;
    
    currentAnimationTitle.textContent = gesture.title;
    animationInstruction.textContent = gesture.instruction;
    
    // 重置所有手指位置
    document.getElementById('thumb').style.transform = 'translateX(-50%) translateY(-4px)';
    document.getElementById('index-finger').style.transform = 'translateX(4px) translateY(-4px)';
    
    // 执行当前手势动画
    setTimeout(gesture.animation, 300);
}

// 倒计时功能
function startCountdown() {
    let countdown = 3;
    countdownEl.textContent = countdown;
    
    const countdownInterval = setInterval(() => {
    countdown--;
    countdownEl.textContent = countdown;
    countdownEl.classList.add('animate-count');
    
    if (countdown <= 0) {
        clearInterval(countdownInterval);
        countdownArea.classList.add('hidden');
        collectingArea.classList.remove('hidden');
        startCollecting();
    } else {
        setTimeout(() => {
        countdownEl.classList.remove('animate-count');
        }, 500);
    }
    }, 1000);
}

// 模拟采集过程
function startCollecting() {
    let taskProgress = 0;
    const totalDuration = 15; // 模拟每个任务15秒
    const updateInterval = 100;
    const steps = totalDuration * 10;
    const stepProgress = 100 / steps;

    // 设置当前任务信息
    const taskNames = ['离散手势采集', '连续手势采集-1', '连续手势采集-2'];
    currentTaskEl.textContent = taskNames[currentTask - 1];
    currentGestureEl.textContent = gestures[currentGestureIndex].title.split('：')[1];

    const collectInterval = setInterval(() => {
    taskProgress += stepProgress;
    taskProgressEl.style.width = `${taskProgress}%`;
    
    // 更新整体进度
    if (currentTask === 1) {
        collectProgress = taskProgress / 3;
        progressBars[0].style.width = `${collectProgress * 3}%`;
    } else if (currentTask === 2) {
        collectProgress = 33.33 + (taskProgress / 3);
        progressBars[0].style.width = '100%';
        progressBars[1].style.width = `${(collectProgress - 33.33) * 3}%`;
    } else if (currentTask === 3) {
        collectProgress = 66.66 + (taskProgress / 3);
        progressBars[0].style.width = '100%';
        progressBars[1].style.width = '100%';
    }

    if (taskProgress >= 100) {
        clearInterval(collectInterval);
        taskProgressEl.style.width = '100%';
        
        // 切换到下一个任务
        if (currentTask < 3) {
        currentTask++;
        taskCircles[currentTask - 2].classList.remove('bg-light-1', 'text-dark-2');
        taskCircles[currentTask - 2].classList.add('bg-primary', 'text-white');
        
        // 重置采集区域
        collectingArea.classList.add('hidden');
        countdownArea.classList.remove('hidden');
        countdownEl.textContent = 3;
        currentGestureIndex = (currentTask === 2) ? 4 : 5;
        startCountdown();
        } else {
        // 所有任务完成
        taskCircles[2].classList.remove('bg-light-1', 'text-dark-2');
        taskCircles[2].classList.add('bg-primary', 'text-white');
        currentTaskEl.textContent = '采集完成';
        currentGestureEl.textContent = '所有任务已完成';
        collectingArea.querySelector('.text-success').textContent = '采集完成！数据已自动保存';
        }
    }
    }, updateInterval);
}

// 重置采集界面
function resetCollectPage() {
    taskIntro.classList.remove('hidden');
    gestureAnimation.classList.add('hidden');
    countdownArea.classList.add('hidden');
    collectingArea.classList.add('hidden');
    startCollectBtn.classList.remove('hidden');
    //skipAnimationBtn.classList.add('hidden');

    EMGDisplayModule.cleanup();
    
    // 重置进度
    collectProgress = 0;
    currentTask = 1;
    currentGestureIndex = 0;
    progressBars.forEach(bar => bar.style.width = '0%');
    taskCircles.forEach((circle, index) => {
    if (index > 0) {
        circle.classList.remove('bg-primary', 'text-white');
        circle.classList.add('bg-light-1', 'text-dark-2');
    }
    });
    
    // 重置按钮状态和手势位置
    startCollectBtn.disabled = false;
    startCollectBtn.innerHTML = '<i class="fa fa-play-circle mr-2"></i>开始采集';
    taskProgressEl.style.width = '0%';
    document.getElementById('thumb').style.transform = 'translateX(-50%) translateY(-4px)';
    document.getElementById('index-finger').style.transform = 'translateX(4px) translateY(-4px)';
}
