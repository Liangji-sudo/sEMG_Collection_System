
// 页面切换逻辑
const pages = document.querySelectorAll('.page');
const initialPage = document.getElementById('initial-page');
const collectPage = document.getElementById('collect-page');
const backendPage = document.getElementById('backend-page');

// 初始界面按钮
document.getElementById('collect-btn').addEventListener('click', () => {
    switchPage(collectPage);
});

document.getElementById('backend-btn').addEventListener('click', () => {
    switchPage(backendPage);
    // 加载文件列表
    loadFileList();
});

// 返回按钮
document.getElementById('back-to-initial').addEventListener('click', () => {
    switchPage(initialPage);
    resetCollectPage();
});

document.getElementById('back-to-initial-2').addEventListener('click', () => {
    switchPage(initialPage);
});

// 页面切换函数
function switchPage(targetPage) {
    pages.forEach(page => {
    page.classList.add('hidden');
    page.classList.remove('active');
    });
    targetPage.classList.remove('hidden');
    targetPage.classList.add('active', 'animate-fade');
}
