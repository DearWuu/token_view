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
    opacity: 0.92
};

const PANEL_WIDTH = 420;
const COMPACT_PANEL_WIDTH = 280;
const WINDOW_HEIGHT_PADDING = 0;

// 颜色配置
const BADGE_COLORS = {
    zhipu: { bg: '#3b82f6', text: '智' },
    opencode: { bg: '#a855f7', text: 'OC' },
    mimo: { bg: '#ff6900', text: 'Mi' }
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
    if (text.includes('week') || text.includes('周')) return '周';
    if (text.includes('month') || text.includes('月')) return '月';
    if (text.includes('mcp')) return 'MCP';
    return String(label || '').trim().slice(0, 3) || '用量';
}

function normalizedCompactItems(items) {
    const slots = [
        { key: '5h', label: '5h', percent: 0 },
        { key: 'week', label: '周', percent: 0 },
        { key: 'month', label: '月', percent: 0 }
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
    return Math.ceil(cssWidth);
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
    const summedHeight = titleHeight + cardsHeight + emptyHeight + loadingHeight;
    const containerHeight = Math.ceil(container.scrollHeight || 0);
    const height = Math.ceil(Math.max(summedHeight, containerHeight) + WINDOW_HEIGHT_PADDING);
    const measuredWidth = Math.ceil(container.getBoundingClientRect().width || cssWidth);
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
    scheduleWindowFit();
}

function applyProviderUpdates(providers) {
    if (!providers || providers.length === 0) {
        elements.container.innerHTML = '';
        state.cards = {};
        elements.emptyTip.style.display = 'block';
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

    scheduleWindowFit();
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
        const size = measurePanelSize();
        const result = await window.pywebview.api.move_window_to_top(0, size.height);
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

// 切换置顶
async function toggleOnTop() {
    if (window.pywebview && window.pywebview.api) {
        state.onTop = await window.pywebview.api.toggle_on_top();
        elements.btnPin.classList.toggle('active', state.onTop);
        scheduleWindowFit();
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
    elements.btnTop.addEventListener('click', moveToTop);
    elements.btnMode.addEventListener('click', toggleCompact);
    elements.btnPin.addEventListener('click', toggleOnTop);
    elements.btnSettings.addEventListener('click', openSettings);
    elements.btnClose.addEventListener('click', closeWindow);

    // 绑定缩放手柄
    initResizeHandles();

    // 加载配置
    if (window.pywebview && window.pywebview.api) {
        try {
            const cfg = await window.pywebview.api.get_config();
            state.compact = cfg.compact ?? false;
            state.dock = false;
            state.topMode = false;
            state.topWidth = null;
            state.onTop = cfg.always_on_top !== false;
            state.opacity = cfg.opacity || 0.92;
            await window.pywebview.api.set_top_mode(false);
            
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

    // 首次刷新
    await refresh();
    scheduleWindowFit(80);
}

// 等待 pywebview 就绪
window.addEventListener('pywebviewready', init);
