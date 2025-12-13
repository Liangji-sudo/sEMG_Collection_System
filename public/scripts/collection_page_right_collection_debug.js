const API_URL = 'http://localhost:3000/button-click';

/**
 * 发送按钮点击事件到后端
 * @param {string} buttonName 按钮名称
 */
async function sendButtonClick(buttonName) {
    try {
        // 发送POST请求
        const response = await fetch(API_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json' // JSON格式
            },
            body: JSON.stringify({ buttonName }) // 传递按钮名称
        });

        // 解析后端响应
        const result = await response.json();
        // 显示响应结果到页面
        document.getElementById('response').textContent = result.msg;
    } catch (error) {
        // 错误处理
        document.getElementById('response').textContent = `请求失败：${error.message}`;
        console.error('请求错误：', error);
    }
}