// Token View - 主应用逻辑

// 状态管理
const state = {
    cards: {},
    compact: false,
    dock: false,
    topMode: false,
    topWidth: null,
    manualWidth: null,
    onTop: true,
    refreshInterval: 60000,
    timer: null,
    opacity: 0.92,
    theme: 'dark',
    fitToken: 0,
    dockMode: false,
    dockHidden: false
};

const PANEL_WIDTH = 420;
const COMPACT_PANEL_WIDTH = 280;
const WINDOW_HEIGHT_PADDING = 0;

// 颜色配置
const BADGE_COLORS = {
    zhipu: { bg: '#3b82f6', text: '智' },
    kimi: { bg: '#10b981', text: 'Ki' },
    opencode: { bg: '#a855f7', text: 'OC' },
    mimo: { bg: '#ff6900', text: 'Mi' },
    volcengine: { bg: '#e11d48', text: '火' }
};

// 工具函数
function colorForPercent(pct) {
    if (pct >= 90) return 'danger';
    if (pct >= 70) return 'warning';
    return '';
}

function formatReset(note) {
    return note || '';
}

function shortUsageLabel(label) {
    const text = String(label || '').trim().toLowerCase();
    if (text.includes('5') || text.includes('rolling')) return '5h';
    if (text.includes('7d') || text.includes('7天')) return '周';
    if (text.includes('week') || text.includes('周')) return '周';
    if (text.includes('month') || text.includes('月')) return '月';
    if (text.includes('mcp')) return 'MCP';
    return String(label || '').trim().slice(0, 3) || '用量';
}

// ---- 重置倒计时圆环 ----
// 根据 label 推断窗口时长（秒），用于把 reset_at 换算成剩余比例
function windowSecondsForLabel(label) {
    const text = String(label || '').trim().toLowerCase();
    if (text.includes('5') || text.includes('rolling')) return 5 * 3600;
    if (text.includes('7d') || text.includes('7天') || text.includes('week') || text.includes('周')) return 7 * 86400;
    if (text.includes('month') || text.includes('月')) return 30 * 86400;
    return 0;
}

function formatResetCountdown(resetAt) {
    const remain = Math.max(0, (Number(resetAt) || 0) - Date.now() / 1000);
    if (remain <= 0) return '即将重置';
    const d = Math.floor(remain / 86400);
    const h = Math.floor((remain % 86400) / 3600);
    const m = Math.ceil((remain % 3600) / 60);
    if (d > 0) return `${d}天${h}小时后重置`;
    if (h > 0) return `${h}小时${m}分后重置`;
    return `${m}分钟后重置`;
}

const RESET_RING_C = 2 * Math.PI * 5;  // r=5 的周长

function resetRingHTML(item) {
    const resetAt = Number(item.reset_at) || 0;
    if (!resetAt) return '';
    const windowSec = windowSecondsForLabel(item.label);
    if (!windowSec) return '';
    const frac = Math.max(0, Math.min(1, (resetAt - Date.now() / 1000) / windowSec));
    const offset = (RESET_RING_C * (1 - frac)).toFixed(2);
    return `<span class="reset-ring" data-reset-at="${resetAt}" data-window="${windowSec}"
                  title="${formatResetCountdown(resetAt)}">
        <svg viewBox="0 0 14 14">
            <circle class="ring-bg" cx="7" cy="7" r="5"></circle>
            <circle class="ring-fg" cx="7" cy="7" r="5"
                    stroke-dasharray="${RESET_RING_C.toFixed(3)}"
                    stroke-dashoffset="${offset}"
                    transform="rotate(-90 7 7)"></circle>
        </svg>
    </span>`;
}

// 每 30s 更新一次圆环，不重新请求数据
function tickResetRings() {
    const nowSec = Date.now() / 1000;
    document.querySelectorAll('.reset-ring').forEach(el => {
        const resetAt = Number(el.dataset.resetAt) || 0;
        const windowSec = Number(el.dataset.window) || 0;
        if (!resetAt || !windowSec) return;
        const frac = Math.max(0, Math.min(1, (resetAt - nowSec) / windowSec));
        const fg = el.querySelector('.ring-fg');
        if (fg) fg.setAttribute('stroke-dashoffset', (RESET_RING_C * (1 - frac)).toFixed(2));
        el.title = formatResetCountdown(resetAt);
    });
}

/* 主题切换 —— 供 pywebview evaluate_js 调用 */
function setTheme(theme) {
    theme = theme === 'light' ? 'light' : 'dark';
    state.theme = theme;
    document.body.classList.remove('theme-dark', 'theme-light');
    document.body.classList.add('theme-' + theme);
}

/* 根据所有 provider 的最低配额更新极光等级 */
function updateAuroraTier(providers) {
    const container = document.querySelector('.container');
    if (!container) return;
    container.classList.remove('quota-tier--healthy', 'quota-tier--caution', 'quota-tier--critical');

    var minPercent = 100;
    (providers || []).forEach(function(p) {
        (p.items || []).forEach(function(item) {
            var pct = Number(item.percent);
            if (!isNaN(pct) && pct < minPercent) minPercent = pct;
        });
    });

    if (minPercent >= 100) return;
    if (minPercent >= 50) container.classList.add('quota-tier--healthy');
    else if (minPercent >= 10) container.classList.add('quota-tier--caution');
    else container.classList.add('quota-tier--critical');
}

function normalizedCompactItems(items) {
    const slots = [
        { key: '5h', label: '5h', percent: 0, reset_at: null },
        { key: 'week', label: '周', percent: 0, reset_at: null },
        { key: 'month', label: '月', percent: 0, reset_at: null }
    ];
    (items || []).forEach(item => {
        const shortLabel = shortUsageLabel(item.label);
        const slot = shortLabel === '5h'
            ? slots[0]
            : shortLabel === '周'
                ? slots[1]
                : shortLabel === '月'
                    ? slots[2]
                    : null;
        if (slot) {
            slot.percent = Number(item.percent || 0);
            slot.reset_at = item.reset_at || null;
        }
    });
    return slots;
}

function desiredPanelWidth() {
    if (state.topMode) {
        if (state.topWidth) {
            return state.topWidth;
        }
        return Math.max(window.innerWidth || PANEL_WIDTH, PANEL_WIDTH);
    }
    if (state.manualWidth) {
        return state.manualWidth;
    }
    return state.compact ? COMPACT_PANEL_WIDTH : PANEL_WIDTH;
}

function applyPanelWidth() {
    document.documentElement.style.setProperty('--panel-width', `${desiredPanelWidth()}px`);
}

function nativeWidthForCss(cssWidth) {
    if (state.topMode) {
        return 0;
    }
    return Math.round(cssWidth);
}

function measurePanelSize() {
    applyPanelWidth();
    const container = document.querySelector('.container');
    const cards = elements.container;
    const cssWidth = desiredPanelWidth();
    const titleHeight = Math.ceil(elements.titlebar.getBoundingClientRect().height);

    // 临时解除 cards-container 的高度约束和滚动，让 scrollHeight 只反映
    // 卡片真实内容高度，避免被窗口当前尺寸污染（Windows WebView2 下
    // overflow:auto 的元素 scrollHeight 会跟着窗口增长，导致反馈循环）。
    const prevMaxHeight = cards.style.maxHeight;
    const prevHeight = cards.style.height;
    const prevOverflow = cards.style.overflowY;
    cards.style.maxHeight = 'none';
    cards.style.height = 'auto';
    cards.style.overflowY = 'visible';
    const cardsHeight = Math.ceil(cards.scrollHeight);
    cards.style.maxHeight = prevMaxHeight;
    cards.style.height = prevHeight;
    cards.style.overflowY = prevOverflow;

    const emptyHeight = elements.emptyTip.style.display === 'none' ? 0 : elements.emptyTip.scrollHeight;
    const loadingHeight = elements.loading.classList.contains('active') ? elements.loading.scrollHeight : 0;
    const height = Math.ceil(titleHeight + cardsHeight + emptyHeight + loadingHeight + WINDOW_HEIGHT_PADDING);
    const measuredWidth = Math.round(container.getBoundingClientRect().width || cssWidth);
    return {
        width: nativeWidthForCss(measuredWidth),
        height: Math.max(80, height)
    };
}

function fitWindowOnce(delay = 0, token = state.fitToken) {
    window.setTimeout(() => {
        requestAnimationFrame(() => {
            requestAnimationFrame(async () => {
                if (token !== state.fitToken) {
                    return;
                }
                if (!window.pywebview || !window.pywebview.api) {
                    return;
                }
                const size = measurePanelSize();
                await window.pywebview.api.resize_window_to_content(size.width, size.height);
                // resize 后窗口真实高度变了，dock 隐藏的 y 坐标需要重新计算
                // 否则 set_dock_hidden 用的是 fit 前的旧 h，会算错 y 导致完全隐藏
                if (state.dockMode && state.dockHidden) {
                    window.pywebview.api.set_dock_hidden(true);
                }
            });
        });
    }, delay);
}

function scheduleWindowFit(delay = 0) {
    state.fitToken = (state.fitToken || 0) + 1;
    const token = state.fitToken;
    fitWindowOnce(delay, token);
    [90, 220, 420].forEach(extraDelay => {
        window.setTimeout(() => fitWindowOnce(0, token), delay + extraDelay);
    });
}

// DOM 元素
const elements = {
    container: document.getElementById('cards-container'),
    emptyTip: document.getElementById('empty-tip'),
    loading: document.getElementById('loading'),
    btnRefresh: document.getElementById('btn-refresh'),
    btnTop: document.getElementById('btn-top'),
    btnMode: document.getElementById('btn-mode'),
    btnPin: document.getElementById('btn-pin'),
    btnSettings: document.getElementById('btn-settings'),
    btnClose: document.getElementById('btn-close'),
    titlebar: document.getElementById('titlebar')
};

// 创建卡片 HTML
function createCardHTML(provider) {
    const badge = BADGE_COLORS[provider.type] || { bg: '#666', text: '?' };

    let itemsHTML = '';
    if (provider.status === 'error') {
        itemsHTML = `<div class="error-message">⚠ ${(provider.error || '').substring(0, 50)}</div>`;
    } else if (provider.items && provider.items.length > 0) {
        const sourceItems = state.compact ? normalizedCompactItems(provider.items) : provider.items;
        itemsHTML = sourceItems.map(item => {
            const itemColorClass = colorForPercent(item.percent);
            const shortLabel = shortUsageLabel(item.label);
            return `
                <div class="usage-item">
                    <div class="usage-row">
                        <span class="usage-label" data-short="${shortLabel}">${item.label}</span>
                        <div class="progress-bar">
                            <div class="progress-fill ${itemColorClass}" 
                                 style="width: ${item.percent}%"></div>
                        </div>
                        ${resetRingHTML(item)}
                        <span class="usage-percent ${itemColorClass}">${item.percent.toFixed(0)}%</span>
                    </div>
                    ${item.note ? `<div class="usage-note">${item.note}</div>` : ''}
                </div>
            `;
        }).join('');
    } else {
        itemsHTML = '<div class="error-message">暂无数据</div>';
    }

    const statusClass = provider.status === 'ok' ? 'ok' : 
                       provider.status === 'error' ? 'error' : 
                       provider.status === 'loading' ? 'loading' : 'empty';

    return `
        <div class="card" data-id="${provider.id}">
            <div class="card-header">
                <div class="badge badge-${provider.type}">${badge.text}</div>
                <span class="card-title">${provider.name || provider.type}</span>
                ${provider.level ? `<span class="card-level">${provider.level}</span>` : ''}
                <span class="status-dot status-${statusClass}"></span>
            </div>
            <div class="card-body">
                ${itemsHTML}
            </div>
        </div>
    `;
}

// 渲染卡片
function renderCards(providers) {
    if (!providers || providers.length === 0) {
        elements.container.innerHTML = '';
        elements.emptyTip.style.display = 'block';
        scheduleWindowFit();
        return;
    }

    elements.emptyTip.style.display = 'none';
    elements.container.innerHTML = providers.map(createCardHTML).join('');

    // 保存状态
    providers.forEach(p => {
        state.cards[p.id] = p;
    });
    updateAuroraTier(providers);
    scheduleWindowFit();
}

function applyProviderUpdates(providers) {
    if (!providers || providers.length === 0) {
        elements.container.innerHTML = '';
        state.cards = {};
        elements.emptyTip.style.display = 'block';
        updateAuroraTier([]);
        scheduleWindowFit();
        return;
    }

    elements.emptyTip.style.display = 'none';
    const incomingIds = new Set();
    providers.forEach(provider => {
        incomingIds.add(provider.id);
        updateCard(provider, false);
    });

    elements.container.querySelectorAll('.card').forEach(card => {
        const id = card.dataset.id;
        if (!incomingIds.has(id)) {
            card.remove();
            delete state.cards[id];
        }
    });
    updateAuroraTier(providers);
}

// 更新单个卡片
function updateCard(provider, fit = true) {
    const existingCard = elements.container.querySelector(`[data-id="${provider.id}"]`);
    if (existingCard) {
        existingCard.outerHTML = createCardHTML(provider);
    } else {
        elements.container.insertAdjacentHTML('beforeend', createCardHTML(provider));
    }
    state.cards[provider.id] = provider;
    if (fit) {
        scheduleWindowFit();
    }
}

// 刷新数据
async function refresh() {
    const hasCards = Object.keys(state.cards).length > 0;
    elements.btnRefresh.disabled = true;
    elements.btnRefresh.classList.add('active');
    if (!hasCards) {
        elements.loading.classList.add('active');
    }

    try {
        // 只重新请求 provider 数据，回来后按卡片局部更新，不做整窗重绘。
        if (window.pywebview && window.pywebview.api) {
            const providers = await window.pywebview.api.get_usage();
            if (hasCards) {
                applyProviderUpdates(providers);
            } else {
                renderCards(providers);
            }
        }
    } catch (error) {
        console.error('刷新失败:', error);
    } finally {
        elements.btnRefresh.disabled = false;
        elements.btnRefresh.classList.remove('active');
        elements.loading.classList.remove('active');
    }
}

// 切换简单/复杂模式
async function toggleCompact() {
    state.compact = !state.compact;
    state.topMode = false;
    state.topWidth = null;
    state.manualWidth = null;
    state.widthScale = 1;
    document.body.classList.toggle('compact', state.compact);
    document.body.classList.remove('top-mode');
    applyPanelWidth();
    elements.btnMode.classList.toggle('active', state.compact);
    elements.btnMode.textContent = state.compact ? '⤢' : '⤡';
    
    if (window.pywebview && window.pywebview.api) {
        await window.pywebview.api.set_top_mode(false);
        await window.pywebview.api.set_compact(state.compact);
    }
    scheduleWindowFit();
}

// 放大并移动到当前屏幕顶部
async function moveToTop() {
    if (window.pywebview && window.pywebview.api) {
        state.topMode = true;
        state.topWidth = null;
        state.manualWidth = null;
        state.widthScale = 1;
        applyPanelWidth();
        document.body.classList.add('top-mode');

        // 等一帧让浏览器按 top-mode 布局 reflow 完再测量高度
        await new Promise(r => requestAnimationFrame(r));
        const size = measurePanelSize();

        // 顶部模式宽度固定，不随供应商数量变化（供应商上下堆叠，变的是高度）
        const targetWidth = state.compact ? 620 : 2460;
        const maxScreenW = window.screen.availWidth || 2000;
        const clampedWidth = Math.min(targetWidth, maxScreenW);

        const result = await window.pywebview.api.move_window_to_top(clampedWidth, size.height);
        const ok = typeof result === 'object' ? result.ok : result;
        elements.btnTop.classList.toggle('active', ok);
        setTimeout(() => elements.btnTop.classList.remove('active'), 700);
        if (ok) {
            if (typeof result === 'object' && result.width) {
                state.topWidth = result.width;
                applyPanelWidth();
            }
            // 延迟等 WebView2 完成重布局
            scheduleWindowFit(220);
        } else {
            state.topMode = false;
            state.topWidth = null;
            document.body.classList.remove('top-mode');
            applyPanelWidth();
            scheduleWindowFit(80);
        }
    }
}

// 切换 auto-hide dock 模式（替换原"顶部模式"按钮行为）
// 启用：移到屏幕顶部 + 鼠标离开窗口自动滑出（露 4px 缝）
// 关闭：窗口回到原位（恢复 pre-dock geometry）
async function toggleDock() {
    if (!window.pywebview || !window.pywebview.api) return;
    const willEnable = !state.dockMode;
    if (willEnable) {
        await moveToTop();
        state.dockMode = true;
        document.body.classList.add('dock-mode');
        document.body.classList.remove('dock-hidden');  // 初始先显示
        elements.btnTop.classList.add('active');
        scheduleWindowFit(220);
    } else {
        state.dockMode = false;
        document.body.classList.remove('dock-mode');
        document.body.classList.remove('dock-hidden');
        elements.btnTop.classList.remove('active');
        // 恢复窗口到原位
        await window.pywebview.api.restore_window();
        state.topMode = false;
        state.topWidth = null;
        state.manualWidth = null;
        document.body.classList.remove('top-mode');
        await window.pywebview.api.set_top_mode(false);
        applyPanelWidth();
        scheduleWindowFit(80);
    }
}

// auto-hide 行为：mouseenter 立即显示，mouseleave 走 200ms 延迟
// （避免 WebView2 在 set_dock_hidden 改 y 后重派 mouseenter/mouseleave
// 造成死循环）。
// 物理位置通过 api.set_dock_hidden(true/false) 切，CSS 只负责 cursor 指示。
const DOCK_HIDE_MARGIN = 20;  // 鼠标离开内容后多少像素内不触发隐藏
function setupDockAutoHide() {
    let leaveTimer = null;
    const setHidden = (hidden) => {
        if (!state.dockMode) return;
        if (state.dockHidden === hidden) return;       // 去重
        state.dockHidden = hidden;
        document.body.classList.toggle('dock-hidden', hidden);
        if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.set_dock_hidden(hidden);
        }
    };
    document.addEventListener('mouseenter', () => {
        if (leaveTimer) { clearTimeout(leaveTimer); leaveTimer = null; }
        setHidden(false);
    }, true);

    // 用 mousemove 检测鼠标是否离开内容区域 + 缓冲区间，
    // 代替 mouseleave（mouseleave 在视口边界就触发，没有缓冲）。
    document.addEventListener('mousemove', (e) => {
        if (!state.dockMode || resizeState.dragging) return;

        // 鼠标在视口内的位置，判断是否在边缘缓冲区外
        const w = window.innerWidth;
        const h = window.innerHeight;
        const cx = e.clientX;
        const cy = e.clientY;
        const inContent = cx >= 0 && cx <= w && cy >= 0 && cy <= h;
        const inMargin = cx >= -DOCK_HIDE_MARGIN && cx <= w + DOCK_HIDE_MARGIN
                      && cy >= -DOCK_HIDE_MARGIN && cy <= h + DOCK_HIDE_MARGIN;

        if (inContent) {
            // 鼠标在窗口内，取消隐藏定时
            if (leaveTimer) { clearTimeout(leaveTimer); leaveTimer = null; }
            setHidden(false);
        } else if (inMargin) {
            // 鼠标在缓冲区间内，不触发隐藏（取消已有定时）
            if (leaveTimer) { clearTimeout(leaveTimer); leaveTimer = null; }
        } else {
            // 鼠标超出缓冲区间，延迟隐藏
            if (!leaveTimer) {
                leaveTimer = setTimeout(() => {
                    leaveTimer = null;
                    setHidden(true);
                }, 200);
            }
        }

        // auto-hide 状态下鼠标接近屏幕顶部时恢复显示
        if (state.dockHidden && e.screenY < 4) {
            setHidden(false);
        }
    });

    // mouseleave 兜底：鼠标完全离开 WebView 且不在缓冲区
    document.addEventListener('mouseleave', () => {
        if (resizeState.dragging) return;
        if (!leaveTimer) {
            leaveTimer = setTimeout(() => {
                leaveTimer = null;
                setHidden(true);
            }, 200);
        }
    }, true);
}

// 假透明度：改 --opacity-primary CSS 变量，背景透、文字/进度条保持清晰。
function applyWindowOpacity(alpha) {
    const a = Math.max(0.3, Math.min(1.0, Number(alpha) || 0.92));
    document.documentElement.style.setProperty('--opacity-primary', String(a));
}

// 切换置顶
async function toggleOnTop() {
    if (window.pywebview && window.pywebview.api) {
        state.onTop = await window.pywebview.api.toggle_on_top();
        elements.btnPin.classList.toggle('active', state.onTop);
    }
}

// 打开设置
function openSettings() {
    // 使用 pywebview API 打开设置窗口
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.open_settings_window();
    } else {
        // 备用方案：在新窗口打开
        window.open('settings.html', 'settings', 'width=500,height=600');
    }
}

// 启动定时刷新
function startAutoRefresh(interval) {
    state.refreshInterval = interval || 60000;
    if (state.timer) {
        clearInterval(state.timer);
    }
    state.timer = setInterval(refresh, state.refreshInterval);
}

// 关闭窗口
async function closeWindow() {
    if (window.pywebview && window.pywebview.api) {
        await window.pywebview.api.quit_app();
    } else {
        window.close();
    }
}

// ---- 手动拖拽缩放 ----
// 无边框窗口需要 JS 手动处理 8 个方向的缩放
const resizeState = { dragging: false, dir: '', startX: 0, startY: 0, startW: 0, startH: 0, startWinX: 0, startWinY: 0 };

function startResize(e, dir) {
    e.preventDefault();
    e.stopPropagation();
    resizeState.dragging = true;
    resizeState.dir = dir;
    resizeState.startX = e.screenX;
    resizeState.startY = e.screenY;
    resizeState.startW = window.innerWidth;
    resizeState.startH = window.innerHeight;
    resizeState.startWinX = window.screenX;
    resizeState.startWinY = window.screenY;
    document.addEventListener('mousemove', onResizeMove);
    document.addEventListener('mouseup', onResizeEnd);
}

function onResizeMove(e) {
    if (!resizeState.dragging) return;
    const dx = e.screenX - resizeState.startX;
    const dy = e.screenY - resizeState.startY;
    const d = resizeState.dir;
    let newW = resizeState.startW;
    let newH = resizeState.startH;
    let newX = resizeState.startWinX;
    let newY = resizeState.startWinY;

    if (d.includes('e')) newW = resizeState.startW + dx;
    if (d.includes('s')) newH = resizeState.startH + dy;
    if (d.includes('w')) { newW = resizeState.startW - dx; newX = resizeState.startWinX + dx; }
    if (d.includes('n')) { newH = resizeState.startH - dy; newY = resizeState.startWinY + dy; }

    newW = Math.max(260, Math.min(newW, 2000));
    newH = Math.max(80, Math.min(newH, 1200));

    // 同步更新 CSS --panel-width，让内容跟着窗口宽度 reflow
    state.manualWidth = newW;
    applyPanelWidth();

    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.resize_window_to_content(newW, newH);
        if (newX !== resizeState.startWinX || newY !== resizeState.startWinY) {
            window.pywebview.api.move_window(newX, newY);
        }
    }
}

function onResizeEnd() {
    resizeState.dragging = false;
    document.removeEventListener('mousemove', onResizeMove);
    document.removeEventListener('mouseup', onResizeEnd);
    // 不调 scheduleWindowFit —— 手动缩放后保留用户设定的尺寸，
    // manualWidth 已在 onResizeMove 中设置，desiredPanelWidth 会优先使用它
    // dock 模式下手动 resize 后重算 auto-hide 的 y 坐标
    if (state.dockMode && state.dockHidden) {
        if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.set_dock_hidden(true);
        }
    }
}

function initResizeHandles() {
    const handles = [
        ['resize-grip', 'se'],
        ['resize-e', 'e'],
        ['resize-s', 's'],
        ['resize-w', 'w'],
        ['resize-n', 'n'],
        ['resize-ne', 'ne'],
        ['resize-nw', 'nw'],
        ['resize-sw', 'sw'],
    ];
    handles.forEach(([id, dir]) => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('mousedown', (e) => startResize(e, dir));
        }
    });
}

// 初始化
async function init() {
    // 检测平台，Windows 上禁用透明
    if (navigator.platform.indexOf('Win') !== -1 || navigator.userAgent.indexOf('Windows') !== -1) {
        document.body.classList.add('no-transparent');
    }
    
    // 绑定按钮事件
    elements.btnRefresh.addEventListener('click', refresh);
    elements.btnTop.addEventListener('click', toggleDock);
    elements.btnMode.addEventListener('click', toggleCompact);
    elements.btnPin.addEventListener('click', toggleOnTop);
    elements.btnSettings.addEventListener('click', openSettings);
    elements.btnClose.addEventListener('click', closeWindow);

    // 绑定缩放手柄
    initResizeHandles();

    // 启动 auto-hide dock 行为监听
    setupDockAutoHide();

    // 重置倒计时圆环每 30s 走一次（不重新请求数据）
    setInterval(tickResetRings, 30000);

    // 加载配置
    if (window.pywebview && window.pywebview.api) {
        try {
            const cfg = await window.pywebview.api.get_config();
            state.compact = cfg.compact ?? false;
            state.dock = false;
            state.topMode = false;
            state.dockMode = false;
            state.dockHidden = false;
            state.topWidth = null;
            state.onTop = cfg.always_on_top !== false;
            state.opacity = cfg.opacity || 0.92;
            applyWindowOpacity(state.opacity);
            await window.pywebview.api.set_top_mode(false);

            // 主题
            setTheme(cfg.theme || 'dark');

            // 设置模式
            document.body.classList.toggle('compact', state.compact);
            document.body.classList.remove('dock-mode');
            document.body.classList.remove('top-mode');
            applyPanelWidth();
            elements.btnMode.classList.toggle('active', state.compact);
            elements.btnMode.textContent = state.compact ? '⤢' : '⤡';
            elements.btnPin.classList.toggle('active', state.onTop);

            // 启动定时刷新
            startAutoRefresh(cfg.refresh_interval * 1000 || 60000);
        } catch (error) {
            console.error('加载配置失败:', error);
        }
    }

    // 首次刷新（异步：窗口立即可见，数据到后 renderCards 内部自动 fit）
    refresh();
}

// 等待 pywebview 就绪
window.addEventListener('pywebviewready', init);
