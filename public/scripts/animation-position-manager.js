/**
 * animation-position-manager.js - 采集引导区域拖拽管理模块
 *
 * 管理两个可拖拽浮层的位置：
 * 1. #gestureAnimationPanel - 引导动画区域
 * 2. #gestureGifContainer - GIF示范窗口
 *
 * 位置限制在 .animation-area 内，使用 ratio 保存以适应窗口缩放。
 */
(function() {
    'use strict';

    const STORAGE_KEY_PANEL = 'emg_animation_panel_position';
    const STORAGE_KEY_GIF = 'emg_gif_panel_position';

    class PositionManager {
        constructor() {
            this._dragging = null;
            this._onPointerMove = this._onPointerMove.bind(this);
            this._onPointerUp = this._onPointerUp.bind(this);
            this._onResize = this._onResize.bind(this);
        }

        init() {
            this._container = document.querySelector('.animation-area');
            if (!this._container) {
                console.warn('[PositionManager] .animation-area 未找到，跳过初始化');
                return;
            }

            this._panel = document.getElementById('gestureAnimationPanel');
            this._gif = document.getElementById('gestureGifContainer');

            if (this._panel) {
                this._setupDraggable(this._panel, '[data-drag-handle="animation"]', STORAGE_KEY_PANEL);
            }
            if (this._gif) {
                this._setupDraggable(this._gif, '.gesture-gif-header', STORAGE_KEY_GIF);
            }

            window.addEventListener('resize', this._onResize);

            console.log('[PositionManager] 初始化完成');
        }

        _setupDraggable(el, handleSelector, storageKey) {
            const handle = el.querySelector(handleSelector);
            if (!handle) return;

            handle.style.cursor = 'grab';
            handle.style.userSelect = 'none';

            let restored = false;

            if (storageKey) {
                restored = this._restorePosition(el, storageKey);
            }

            if (!restored) {
                this._setDefaultPosition(el, storageKey);
            }

            handle.addEventListener('pointerdown', (e) => {
                if (e.button !== 0) return;
                e.preventDefault();
                e.stopPropagation();

                const containerRect = this._container.getBoundingClientRect();
                const elRect = el.getBoundingClientRect();

                // 优先读 inline style，fallback 到 computed position
                const inlineLeft = parseFloat(el.style.left);
                const inlineTop = parseFloat(el.style.top);
                const startLeft = !isNaN(inlineLeft) ? inlineLeft : (elRect.left - containerRect.left);
                const startTop = !isNaN(inlineTop) ? inlineTop : (elRect.top - containerRect.top);

                // 将初始位置写入 inline style，确保后续使用 top/left
                el.style.left = startLeft + 'px';
                el.style.top = startTop + 'px';
                el.style.bottom = 'auto';

                this._dragging = {
                    el,
                    storageKey,
                    startX: e.clientX,
                    startY: e.clientY,
                    startLeft,
                    startTop
                };

                handle.style.cursor = 'grabbing';
                el.style.userSelect = 'none';
                el.style.transition = 'none';

                document.addEventListener('pointermove', this._onPointerMove);
                document.addEventListener('pointerup', this._onPointerUp);
            });
        }

        _onPointerMove(e) {
            if (!this._dragging) return;

            const d = this._dragging;
            const dx = e.clientX - d.startX;
            const dy = e.clientY - d.startY;

            let newLeft = d.startLeft + dx;
            let newTop = d.startTop + dy;

            const containerRect = this._container.getBoundingClientRect();
            const elRect = d.el.getBoundingClientRect();
            const elWidth = elRect.width;
            const elHeight = elRect.height;

            newLeft = Math.max(0, Math.min(newLeft, containerRect.width - elWidth));
            newTop = Math.max(0, Math.min(newTop, containerRect.height - elHeight));

            d.el.style.left = newLeft + 'px';
            d.el.style.top = newTop + 'px';
        }

        _onPointerUp(e) {
            if (!this._dragging) return;

            document.removeEventListener('pointermove', this._onPointerMove);
            document.removeEventListener('pointerup', this._onPointerUp);

            const d = this._dragging;
            const handle = d.el.querySelector('[data-drag-handle]') || d.el.querySelector('.gesture-gif-header');
            if (handle) {
                handle.style.cursor = 'grab';
            }
            d.el.style.userSelect = '';

            this._savePosition(d.el, d.storageKey);
            this._triggerResize();

            this._dragging = null;
        }

        _savePosition(el, storageKey) {
            if (!storageKey || !this._container) return;

            const containerRect = this._container.getBoundingClientRect();
            if (containerRect.width <= 0 || containerRect.height <= 0) return;

            const left = parseFloat(el.style.left) || 0;
            const top = parseFloat(el.style.top) || 0;

            const data = {
                leftRatio: left / containerRect.width,
                topRatio: top / containerRect.height
            };

            try {
                localStorage.setItem(storageKey, JSON.stringify(data));
            } catch (e) {
                console.warn('[PositionManager] localStorage 写入失败:', e);
            }
        }

        _restorePosition(el, storageKey) {
            if (!this._container) return false;

            try {
                const raw = localStorage.getItem(storageKey);
                if (!raw) return false;

                const data = JSON.parse(raw);
                if (data.leftRatio == null || data.topRatio == null) return false;

                const containerRect = this._container.getBoundingClientRect();
                const elRect = el.getBoundingClientRect();

                let left = data.leftRatio * containerRect.width;
                let top = data.topRatio * containerRect.height;

                left = Math.max(0, Math.min(left, containerRect.width - elRect.width));
                top = Math.max(0, Math.min(top, containerRect.height - elRect.height));

                el.style.left = left + 'px';
                el.style.top = top + 'px';
                if (storageKey === STORAGE_KEY_GIF) {
                    el.style.bottom = 'auto';
                }

                return true;
            } catch (e) {
                return false;
            }
        }

        _clampToContainer(el) {
            if (!this._container) return;

            const containerRect = this._container.getBoundingClientRect();
            const elRect = el.getBoundingClientRect();

            let left = parseFloat(el.style.left) || 0;
            let top = parseFloat(el.style.top) || 0;

            if (isNaN(left)) left = 0;
            if (isNaN(top)) top = 0;

            left = Math.max(0, Math.min(left, containerRect.width - elRect.width));
            top = Math.max(0, Math.min(top, containerRect.height - elRect.height));

            el.style.left = left + 'px';
            el.style.top = top + 'px';
        }

        _setDefaultPosition(el, storageKey) {
            if (!el || !this._container) return;

            const containerRect = this._container.getBoundingClientRect();
            const elRect = el.getBoundingClientRect();

            // 元素不可见时（rect为0），跳过；CSS 默认定位生效
            if (elRect.width <= 0 || elRect.height <= 0) return;

            if (storageKey === STORAGE_KEY_PANEL) {
                const left = Math.max(0, (containerRect.width - elRect.width) / 2);
                const top = Math.max(0, containerRect.height * 0.05);
                el.style.left = left + 'px';
                el.style.top = top + 'px';
            } else if (storageKey === STORAGE_KEY_GIF) {
                const top = Math.max(0, containerRect.height - elRect.height - 70);
                el.style.left = '20px';
                el.style.top = top + 'px';
                el.style.bottom = 'auto';
            } else {
                el.style.left = '0px';
                el.style.top = '0px';
            }
        }

        _onResize() {
            if (this._dragging) return;

            const items = [
                { el: this._panel, key: STORAGE_KEY_PANEL },
                { el: this._gif, key: STORAGE_KEY_GIF }
            ];

            items.forEach(({ el, key }) => {
                if (!el) return;
                if (!this._restorePosition(el, key)) {
                    this._clampToContainer(el);
                }
            });
        }

        _triggerResize() {
            const canvases = [
                window.discreteGestureAnimation,
                window.continualGesture1Animation,
                window.continualGesture2Animation,
                window.continualGesture3Animation
            ];

            canvases.forEach(anim => {
                if (anim && typeof anim.resizeCanvas === 'function') {
                    try { anim.resizeCanvas(); } catch (e) {}
                }
            });
        }

        showAnimationPanel() {
            if (!this._panel) return;

            this._panel.classList.add('active');

            // 面板刚变为可见，如果没有有效 inline left/top，设置默认位置
            const left = parseFloat(this._panel.style.left);
            const top = parseFloat(this._panel.style.top);
            if (isNaN(left) || isNaN(top)) {
                if (!this._restorePosition(this._panel, STORAGE_KEY_PANEL)) {
                    this._setDefaultPosition(this._panel, STORAGE_KEY_PANEL);
                }
            }
            this._clampToContainer(this._panel);
            this._triggerResize();
        }

        hideAnimationPanel() {
            if (this._panel) {
                this._panel.classList.remove('active');
            }
        }

        resetPositions() {
            [STORAGE_KEY_PANEL, STORAGE_KEY_GIF].forEach(key => {
                try { localStorage.removeItem(key); } catch (e) {}
            });

            // 清除 inline style
            if (this._panel) {
                this._panel.style.left = '';
                this._panel.style.top = '';
            }
            if (this._gif) {
                this._gif.style.left = '';
                this._gif.style.top = '';
                this._gif.style.bottom = '';
            }

            // 仅对当前可见的元素立即恢复默认位置；
            // 隐藏元素由下次 showAnimationPanel() / 首次拖拽时处理
            const panelActive = this._panel && this._panel.classList.contains('active');
            const gifActive = this._gif && this._gif.classList.contains('active');

            if (panelActive) {
                this._setDefaultPosition(this._panel, STORAGE_KEY_PANEL);
                this._clampToContainer(this._panel);
            }
            if (gifActive) {
                this._setDefaultPosition(this._gif, STORAGE_KEY_GIF);
                this._clampToContainer(this._gif);
            }
            this._triggerResize();

            console.log('[PositionManager] 位置已重置为默认');
        }
    }

    const manager = new PositionManager();
    window.animationPositionManager = manager;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => manager.init());
    } else {
        manager.init();
    }
})();
