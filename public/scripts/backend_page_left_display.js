
// 添加CSS样式
const signalStyles = `
.channel-chart {
    transition: all 0.3s ease;
}
.channel-chart:hover {
    background-color: #f8f9fa;
    transform: translateY(-1px);
}
.value-display {
    font-family: 'Courier New', monospace;
    background: #165DFF;
    color: white;
    padding: 2px 6px;
    border-radius: 3px;
    min-width: 60px;
    text-align: center;
}
.min-value, .max-value {
    font-family: 'Courier New', monospace;
    color: #6b7280;
}
`;

// 注入样式
if (!document.querySelector('#signal-styles')) {
const styleSheet = document.createElement('style');
styleSheet.id = 'signal-styles';
styleSheet.textContent = signalStyles;
document.head.appendChild(styleSheet);
}

// 3. 后台界面 - 加载文件列表和预览数据
async function loadFileList() {
    try {
    const response = await fetch('/api/files');
    const files = await response.json();
    const fileListEl = document.getElementById('file-list');
    
    // 清空现有列表
    fileListEl.innerHTML = '';
    
    // 填充文件列表
    files.forEach((file, index) => {
        const fileItem = document.createElement('div');
        fileItem.className = `file-item flex items-center justify-between p-3 rounded-lg ${index === 0 ? 'bg-primary/10 border border-primary' : 'hover:bg-light-2'} cursor-pointer transition-colors`;
        fileItem.innerHTML = `
        <div class="flex items-center">
            <i class="fa fa-file-code-o text-primary mr-3 text-xl"></i>
            <div>
            <div class="font-medium">${file.name}</div>
            <div class="text-xs text-dark-2">${file.createTime} | ${file.size}</div>
            </div>
        </div>
        <button class="preview-btn btn-hover bg-primary text-white px-3 py-1 rounded text-sm" data-filename="${file.name}">
            预览
        </button>
        `;
        fileListEl.appendChild(fileItem);
    });
    
    // 绑定预览按钮事件
    document.querySelectorAll('.preview-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
        const filename = e.target.dataset.filename;
        await previewFile(filename);
        });
    });
    } catch (error) {
    console.error('加载文件列表失败：', error);
    }
}

// 预览HDF5文件
async function previewFile(filename) {
    try {
    const response = await fetch(`/api/preview-file/${filename}`);
    const fileData = await response.json();
    
    // 更新预览区域信息
    document.getElementById('preview-filename').textContent = fileData.filename;
    document.getElementById('preview-createTime').textContent = `创建时间: ${fileData.createTime}`;
    document.getElementById('preview-size').textContent = `文件大小: ${fileData.size}`;
    
    // 更新统计信息
    document.getElementById('stats-semgRange').textContent = fileData.dataStats.semgRange;
    document.getElementById('stats-imuAccRange').textContent = fileData.dataStats.imuAccRange;
    document.getElementById('stats-imuGyroRange').textContent = fileData.dataStats.imuGyroRange;
    document.getElementById('stats-completeness').textContent = fileData.dataStats.completeness;
    
    // 显示预览区域
    document.getElementById('file-preview').classList.add('hidden');
    document.getElementById('data-preview').classList.remove('hidden');
    document.getElementById('data-preview').classList.add('animate-slide');
    
    // 绘制信号图表
    drawSignalCharts(fileData.semgData, fileData.imuData);
    
    // 更新文件列表选中状态
    document.querySelectorAll('.file-item').forEach(item => {
        item.classList.remove('bg-primary/10', 'border-primary');
        item.classList.add('hover:bg-light-2');
        if (item.querySelector('.font-medium').textContent === filename) {
        item.classList.add('bg-primary/10', 'border-primary');
        item.classList.remove('hover:bg-light-2');
        }
    });
    } catch (error) {
    console.error('预览文件失败：', error);
    }
}

// 绘制多通道信号图表
function drawSignalCharts(semgData, imuData) {
    // 销毁已存在的图表
    if (window.semgChart) window.semgChart.destroy();
    if (window.imuChart) window.imuChart.destroy();

    const labels = Array.from({ length: 100 }, (_, i) => i);

    // sEMG信号图表（显示前4个通道）
    const semgCtx = document.getElementById('semg-chart').getContext('2d');
    window.semgChart = new Chart(semgCtx, {
    type: 'line',
    data: {
        labels: labels,
        datasets: semgData.slice(0, 4).map((data, index) => ({
        label: `通道 ${index + 1}`,
        data: data,
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.4,
        borderColor: `hsl(${index * 90}, 70%, 50%)`,
        fill: false
        }))
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
        y: {
            beginAtZero: false,
            title: { display: true, text: '幅值 (mV)' }
        },
        x: { title: { display: true, text: '采样点' } }
        },
        plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } }
        }
    }
    });

    // IMU信号图表（加速度计+陀螺仪各1通道）
    const imuCtx = document.getElementById('imu-chart').getContext('2d');
    window.imuChart = new Chart(imuCtx, {
    type: 'line',
    data: {
        labels: labels,
        datasets: [
        {
            label: '加速度计 X',
            data: imuData[0],
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.4,
            borderColor: '#165DFF',
            fill: false
        },
        {
            label: '加速度计 Y',
            data: imuData[1],
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.4,
            borderColor: '#00B42A',
            fill: false
        },
        {
            label: '加速度计 Z',
            data: imuData[2],
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.4,
            borderColor: '#FF7D00',
            fill: false
        },
        {
            label: '陀螺仪 X',
            data: imuData[3].map(v => v / 10), // 缩放显示
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.4,
            borderColor: '#F53F3F',
            fill: false
        }
        ]
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
        y: { beginAtZero: false, title: { display: true, text: '幅值' } },
        x: { title: { display: true, text: '采样点' } }
        },
        plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } }
        }
    }
    });
}

// 更新当前时间
function updateCurrentTime() {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    const hour = String(now.getHours()).padStart(2, '0');
    const minute = String(now.getMinutes()).padStart(2, '0');
    const second = String(now.getSeconds()).padStart(2, '0');
    
    document.getElementById('collect-time').textContent = 
    `${year}-${month}-${day} ${hour}:${minute}:${second}`;
}

// 每秒更新时间
setInterval(updateCurrentTime, 1000);
updateCurrentTime();

