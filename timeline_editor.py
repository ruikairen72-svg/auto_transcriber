"""
Build a self-contained ELAN-style multi-tier timeline editor page.

The returned HTML page is rendered inside a Gradio ``gr.HTML`` iframe.
It exposes ``window.getEditedSegments()`` so the Gradio JS hook on the
generate button can read the current state.

Layout: video (top-left) + control panel (top-right) + multi-tier timeline (bottom).

Enhancements (2026-08-09):
  1. Auto-scroll keeps playhead centred during playback.
  2. Click empty area / ruler seeks only when paused (ruler always seeks).
  3. Double-click empty area creates segment at click position.
  4. Undo / Redo (Ctrl+Z / Ctrl+Shift+Z) with full history stack.
  5. Tier hierarchy display (parent-child indentation from EAF PARENT_REF).
"""

from __future__ import annotations

import html
import json


def build_timeline_html(
    segments: list[dict],
    speakers: list[str],
    tier_names: list[str],
    video_path: str,
    audio_duration: float,
    tier_hierarchy: dict | None = None,
) -> str:
    """Return a complete HTML page with an ELAN-style timeline editor."""
    tier_options = tier_names if tier_names else speakers
    hierarchy = tier_hierarchy or {}

    # Ensure every segment has a unique id and tierName
    for i, seg in enumerate(segments):
        seg.setdefault("id", f"seg_{i}")
        seg.setdefault("tierName", seg.get("speaker", "UNKNOWN"))

    data_json = json.dumps(
        {
            "segments": segments,
            "speakers": speakers,
            "tierOptions": tier_options,
            "videoUrl": f"/file={video_path}",
            "audioDuration": audio_duration,
            "tierHierarchy": hierarchy,
        },
        ensure_ascii=False,
    )

    inner_html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ELAN Timeline Editor</title>
<style>
/* ============================================================
   Reset & Base
   ============================================================ */
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    background:#1a1a2e;color:#e0e0e0;overflow:hidden;height:100vh;
    display:flex;flex-direction:column;user-select:none;
}}
input,select,textarea,button{{font-family:inherit;font-size:12px;outline:none;}}
button{{cursor:pointer;}}
::-webkit-scrollbar{{width:6px;height:6px;}}
::-webkit-scrollbar-track{{background:#1a1a2e;}}
::-webkit-scrollbar-thumb{{background:#444;border-radius:3px;}}

/* ============================================================
   Error Bar
   ============================================================ */
.error-bar{{
    position:fixed;top:0;left:0;right:0;z-index:1000;
    background:#d32f2f;color:#fff;padding:10px 40px 10px 16px;
    font-size:13px;line-height:1.5;display:none;
    box-shadow:0 2px 12px rgba(0,0,0,0.4);
    white-space:pre-wrap;word-break:break-word;
    max-height:120px;overflow-y:auto;
}}
.error-bar.show{{display:block;}}
.error-bar .err-close{{
    position:absolute;top:6px;right:12px;
    background:none;border:none;color:#fff;font-size:18px;
    cursor:pointer;opacity:0.8;padding:0;line-height:1;
}}
.error-bar .err-close:hover{{opacity:1;}}

/* ============================================================
   Top Panel: Video + Controls
   ============================================================ */
.top-panel{{
    display:flex;flex:0 0 240px;min-height:200px;border-bottom:2px solid #2a2a4a;
}}
.video-panel{{
    flex:3;background:#000;display:flex;align-items:center;justify-content:center;
    padding:8px;min-width:0;
}}
.video-panel video{{
    max-width:100%;max-height:100%;border-radius:4px;
}}
.control-panel{{
    flex:2;background:#16213e;padding:10px 12px;overflow-y:auto;min-width:240px;
    display:flex;flex-direction:column;gap:7px;border-left:2px solid #2a2a4a;
}}
.control-panel h3{{font-size:12px;color:#888;margin-bottom:1px;border-bottom:1px solid #2a2a4a;padding-bottom:3px;}}
.control-panel label{{font-size:11px;color:#999;display:flex;flex-direction:column;gap:2px;}}
.control-panel input[type=text],.control-panel input[type=number],.control-panel select,.control-panel textarea{{
    background:#1a1a2e;color:#e0e0e0;border:1px solid #2a2a4a;border-radius:4px;padding:4px 8px;
}}
.control-panel textarea{{resize:vertical;min-height:40px;font-size:12px;}}
.control-panel select{{min-width:100px;}}
.control-panel button{{
    border:none;border-radius:4px;padding:5px 10px;font-size:12px;white-space:nowrap;
}}
.btn-primary{{background:#0f6030;color:#fff;}}
.btn-primary:hover{{background:#1a7a4a;}}
.btn-danger{{background:#5a1a1a;color:#fff;}}
.btn-danger:hover{{background:#7a2a2a;}}
.btn-secondary{{background:#0f3460;color:#ccc;}}
.btn-secondary:hover{{background:#1a4a7a;}}
.btn-secondary:disabled{{opacity:0.4;cursor:not-allowed;}}
.btn-secondary:disabled:hover{{background:#0f3460;}}

.tier-list{{display:flex;flex-direction:column;gap:2px;max-height:110px;overflow-y:auto;}}
.tier-item{{display:flex;align-items:center;gap:6px;font-size:12px;padding:2px 0;}}
.tier-item input[type=checkbox]{{accent-color:#2196F3;flex-shrink:0;}}
.tier-color{{width:12px;height:12px;border-radius:3px;flex-shrink:0;}}
.tree-indent{{display:inline-block;flex-shrink:0;}}

.add-tier-row{{display:flex;gap:4px;}}
.add-tier-row input{{flex:1;}}

.stat-row{{display:flex;justify-content:space-between;font-size:11px;color:#888;}}
.search-row{{display:flex;gap:4px;}}
.search-row input{{flex:1;}}

.edit-section{{display:none;flex-direction:column;gap:6px;}}
.edit-section.active{{display:flex;}}
.edit-section .btn-row{{display:flex;gap:6px;}}

.no-selection{{color:#555;font-size:12px;text-align:center;padding:8px;}}

.undo-redo-row{{display:flex;gap:6px;}}
.undo-redo-row button{{flex:1;}}

/* ============================================================
   Timeline Area
   ============================================================ */
.timeline-container{{flex:1;display:flex;min-height:0;position:relative;}}
.tier-labels{{
    flex:0 0 110px;background:#16213e;border-right:2px solid #2a2a4a;
    overflow-y:auto;overflow-x:hidden;z-index:5;
}}
.tier-labels::-webkit-scrollbar{{width:0;}}
.label-spacer{{
    height:28px;border-bottom:1px solid #2a2a4a;display:flex;align-items:center;
    justify-content:center;font-size:10px;color:#666;
}}
.tier-label{{
    height:44px;display:flex;align-items:center;padding:0 6px;font-size:11px;font-weight:600;
    color:#ccc;border-bottom:1px solid #1a1a3e;gap:6px;cursor:pointer;white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis;
}}
.tier-label.hidden-tier{{opacity:0.35;}}
.tier-label .tier-color{{width:10px;height:10px;border-radius:2px;flex-shrink:0;}}
.timeline-scroll{{flex:1;overflow:auto;position:relative;scroll-behavior:auto;}}
.timeline-inner{{position:relative;}}
.timeline-ruler{{
    position:sticky;top:0;z-index:6;height:28px;cursor:pointer;
    border-bottom:1px solid #2a2a4a;
}}
.timeline-ruler canvas{{display:block;}}

.tier-row{{
    position:relative;height:44px;border-bottom:1px solid #1a1a3e;cursor:pointer;
}}
.tier-row:nth-child(even){{background:rgba(255,255,255,0.02);}}
.tier-row.hidden-tier{{display:none;}}
.tier-row.drop-target{{background:rgba(233,69,96,0.15);}}

.segment{{
    position:absolute;top:3px;bottom:3px;border-radius:4px;cursor:grab;
    display:flex;align-items:center;padding:0 8px;overflow:hidden;
    border:2px solid transparent;transition:box-shadow 0.1s;min-width:16px;
    z-index:2;
}}
.segment:hover{{box-shadow:0 0 8px rgba(255,255,255,0.12);z-index:3;}}
.segment.selected{{
    border-color:#ffd700!important;
    box-shadow:0 0 14px rgba(255,215,0,0.35);z-index:4;
}}
.segment.dimmed{{opacity:0.18;}}
.segment.search-hit{{box-shadow:0 0 10px rgba(33,150,243,0.6);}}
.segment.dragging{{opacity:0.7;z-index:20;cursor:grabbing;}}
.segment .seg-text{{
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
    font-size:11px;pointer-events:none;line-height:1.2;
}}
.segment .handle{{
    position:absolute;top:0;bottom:0;width:7px;z-index:1;
}}
.segment .handle.left{{left:0;cursor:col-resize;}}
.segment .handle.right{{right:0;cursor:col-resize;}}

/* Clone for cross-tier drag */
.drag-clone{{
    position:fixed;pointer-events:none;z-index:100;opacity:0.75;
    border-radius:4px;display:flex;align-items:center;padding:0 8px;
    font-size:11px;white-space:nowrap;border:2px dashed #ffd700;
}}

/* Playhead */
.playhead{{
    position:absolute;top:0;width:2px;background:#e94560;z-index:10;
    pointer-events:none;transition:none;
}}
.playhead::after{{
    content:'';position:absolute;top:28px;left:-5px;width:12px;height:12px;
    background:#e94560;border-radius:2px;transform:rotate(45deg);
}}

/* ============================================================
   Empty / initial state
   ============================================================ */
.empty-state{{
    display:flex;align-items:center;justify-content:center;height:100%;
    color:#555;font-size:14px;
}}
</style>
</head>
<body>

<div class="error-bar" id="errorBar">
    <span id="errorMsg"></span>
    <button class="err-close" onclick="hideErrorBar()">&times;</button>
</div>

<!-- ============================================================
     Top Panel
     ============================================================ -->
<div class="top-panel" id="topPanel">
    <div class="video-panel">
        <video id="videoPlayer" controls preload="metadata"></video>
    </div>
    <div class="control-panel" id="controlPanel">
        <h3>🔄 历史记录</h3>
        <div class="undo-redo-row">
            <button class="btn-secondary" id="btnUndo" disabled>↩ 撤销</button>
            <button class="btn-secondary" id="btnRedo" disabled>↪ 重做</button>
        </div>

        <h3>📋 层管理</h3>
        <div class="tier-list" id="tierList"></div>
        <div class="add-tier-row">
            <input type="text" id="newTierInput" placeholder="新层名称...">
            <button class="btn-secondary" id="btnAddTier">+ 添加层</button>
        </div>
        <button class="btn-danger" id="btnDeleteTier" style="width:100%;">🗑 删除当前层</button>

        <h3>🔍 搜索与统计</h3>
        <div class="search-row">
            <input type="text" id="searchInput" placeholder="搜索标注文本...">
            <button class="btn-secondary" id="btnClearSearch">✕</button>
        </div>
        <div class="stat-row">
            <span>标注总数: <b id="statCount">0</b></span>
            <span>搜索结果: <b id="statHits">-</b></span>
        </div>

        <h3>✏️ 编辑选中标注</h3>
        <div class="no-selection" id="noSelection">单击时间轴上的色块进行编辑</div>
        <div class="edit-section" id="editSection">
            <label>层归属
                <select id="editTier"></select>
            </label>
            <label>开始时间 (秒)
                <input type="number" id="editStart" step="0.1" min="0">
            </label>
            <label>结束时间 (秒)
                <input type="number" id="editEnd" step="0.1" min="0">
            </label>
            <label>文本内容
                <textarea id="editText" rows="2" placeholder="标注文本..."></textarea>
            </label>
            <div class="btn-row">
                <button class="btn-primary" id="btnApplyEdit">✓ 应用</button>
                <button class="btn-danger" id="btnDeleteSeg">🗑 删除</button>
            </div>
        </div>

        </div>
    </div>
</div>

<!-- ============================================================
     Timeline
     ============================================================ -->
<div class="timeline-container" id="timelineContainer">
    <div class="tier-labels" id="tierLabels">
        <div class="label-spacer">⏱</div>
    </div>
    <div class="timeline-scroll" id="timelineScroll">
        <div class="timeline-inner" id="timelineInner">
            <div class="timeline-ruler" id="timelineRuler">
                <canvas id="rulerCanvas"></canvas>
            </div>
        </div>
        <div class="playhead" id="playhead"></div>
    </div>
</div>

<script>
// ============================================================
// Embedded data
// ============================================================
const EDITOR_DATA = {data_json};

// ============================================================
// Constants
// ============================================================
const TIER_COLORS = [
    '#4CAF50','#2196F3','#FF9800','#9C27B0','#F44336',
    '#00BCD4','#FFEB3B','#FF5722','#607D8B','#8BC34A',
];
const MIN_ZOOM = 10; const MAX_ZOOM = 500; const DEFAULT_ZOOM = 50;
const SNAP = 0.1; const ROW_H = 44;
const MAX_HISTORY = 50;
const TIER_HIERARCHY = EDITOR_DATA.tierHierarchy || {{}};
let _segCounter = 0;

// ============================================================
// State
// ============================================================
let segments = [];
let tiers = [];
let hiddenTiers = new Set();
let collapsedTiers = new Set(); // Tiers collapsed by user in tree view
let selectedId = null;
let pixelsPerSec = DEFAULT_ZOOM;
let dragState = null;
let audioDuration = EDITOR_DATA.audioDuration || 60;
let undoStack = [];
let redoStack = [];
let textEditDirty = false;

// ============================================================
// DOM refs
// ============================================================
const $=id=>document.getElementById(id);
const videoPlayer = $('videoPlayer');
const timelineScroll = $('timelineScroll');
const timelineInner = $('timelineInner');
const timelineRuler = $('timelineRuler');
const rulerCanvas = $('rulerCanvas');
const tierLabels = $('tierLabels');
const playhead = $('playhead');
const tierList = $('tierList');
const editSection = $('editSection');
const noSelection = $('noSelection');

// ============================================================
// Undo / Redo
// ============================================================
function deepCopySegments(arr) {{
    return arr.map(function(s) {{
        return {{id:s.id,tier:s.tier,start:s.start,end:s.end,text:s.text||'',speaker:s.speaker||'',words:(s.words||[]).slice()}};
    }});
}}

function saveUndoState() {{
    redoStack = [];
    undoStack.push({{
        segments: deepCopySegments(segments),
        tiers: tiers.slice(),
        hiddenTiers: new Set(hiddenTiers),
        collapsedTiers: new Set(collapsedTiers),
    }});
    if (undoStack.length > MAX_HISTORY) undoStack.shift();
    updateUndoButtons();
}}

function undo() {{
    if (undoStack.length === 0) return;
    redoStack.push({{
        segments: deepCopySegments(segments),
        tiers: tiers.slice(),
        hiddenTiers: new Set(hiddenTiers),
        collapsedTiers: new Set(collapsedTiers),
    }});
    var state = undoStack.pop();
    segments = state.segments;
    tiers = state.tiers;
    hiddenTiers = new Set(state.hiddenTiers || []);
    collapsedTiers = new Set(state.collapsedTiers || []);
    deselectAll();
    renderAll();
    syncToParent();
    updateUndoButtons();
}}

function redo() {{
    if (redoStack.length === 0) return;
    undoStack.push({{
        segments: deepCopySegments(segments),
        tiers: tiers.slice(),
        hiddenTiers: new Set(hiddenTiers),
        collapsedTiers: new Set(collapsedTiers),
    }});
    var state = redoStack.pop();
    segments = state.segments;
    tiers = state.tiers;
    hiddenTiers = new Set(state.hiddenTiers || []);
    collapsedTiers = new Set(state.collapsedTiers || []);
    deselectAll();
    renderAll();
    syncToParent();
    updateUndoButtons();
}}

function updateUndoButtons() {{
    var ub = $('btnUndo'), rb = $('btnRedo');
    if (ub) {{ ub.disabled = undoStack.length === 0; ub.textContent = '↩ 撤销' + (undoStack.length ? ' ('+undoStack.length+')' : ''); }}
    if (rb) {{ rb.disabled = redoStack.length === 0; rb.textContent = '↪ 重做' + (redoStack.length ? ' ('+redoStack.length+')' : ''); }}
}}

// ============================================================
// Tier hierarchy helpers
// ============================================================
function hasChildren(tierName) {{
    return tiers.some(function(t) {{ return TIER_HIERARCHY[t] === tierName; }});
}}

function getChildren(tierName) {{
    return tiers.filter(function(t) {{ return TIER_HIERARCHY[t] === tierName; }});
}}

function getRootTiers() {{
    return tiers.filter(function(t) {{ return TIER_HIERARCHY[t] == null; }});
}}

function toggleTierCollapse(tierName) {{
    if (collapsedTiers.has(tierName)) {{
        collapsedTiers.delete(tierName);
    }} else {{
        collapsedTiers.add(tierName);
    }}
    renderAll();
}}

// ============================================================
// Recursive tier rendering helpers
// ============================================================
// Render one tier row + its children (if expanded) into the timeline
function renderTimelineRow(tierName, level, sortIndex) {{
    if (hiddenTiers.has(tierName)) return;
    var w = totalWidth();
    var row = document.createElement('div');
    row.className = 'tier-row';
    row.dataset.tier = tierName;
    row.dataset.tierIdx = sortIndex;
    row.style.width = w + 'px';
    row.style.height = ROW_H + 'px';
    timelineInner.appendChild(row);
    var nextIdx = sortIndex + 1;

    // Render segments belonging to this tier
    segments.forEach(function(s) {{
        if (s.tier === tierName) renderSegment(s.id);
    }});

    // Recursively render children if expanded
    if (!collapsedTiers.has(tierName)) {{
        var children = getChildren(tierName);
        children.forEach(function(child) {{
            nextIdx = renderTimelineRow(child, level + 1, nextIdx);
        }});
    }}
    return nextIdx;
}}

// Render one tier label + its children (if expanded) into the left sidebar
function renderLabelNode(tierName, level, colorIdx) {{
    if (hiddenTiers.has(tierName)) return;
    var indentPx = 8 + level * 18;
    var div = document.createElement('div');
    div.className = 'tier-label';
    div.dataset.tier = tierName;
    div.style.paddingLeft = indentPx + 'px';

    var toggleHtml = '';
    if (hasChildren(tierName)) {{
        toggleHtml = '<span class=\"tier-toggle\" data-tier=\"' + tierName + '\" style=\"cursor:pointer;flex-shrink:0;width:14px;text-align:center;font-size:10px;\">' +
            (collapsedTiers.has(tierName) ? '▶' : '▼') + '</span>';
    }} else {{
        toggleHtml = '<span style=\"flex-shrink:0;width:14px;\"></span>';
    }}
    var prefix = level > 0 ? '<span style=\"color:#555;flex-shrink:0;\">└</span>' : '';
    div.innerHTML = toggleHtml + prefix + '<span class=\"tier-color\" style=\"background:' + tierColor(colorIdx) + '\"></span>' + tierName;

    div.addEventListener('click', function(e) {{
        if (e.target.classList.contains('tier-toggle')) return;
        if (hasChildren(tierName)) {{
            toggleTierCollapse(tierName);
        }} else {{
            var segs = segments.filter(function(s) {{ return s.tier === tierName; }});
            if (segs.length > 0) selectSegment(segs[0].id);
        }}
    }});
    tierLabels.appendChild(div);

    // Recursively render children if expanded
    if (!collapsedTiers.has(tierName)) {{
        var children = getChildren(tierName);
        children.forEach(function(child) {{
            renderLabelNode(child, level + 1, colorIdx);
        }});
    }}
}}

// Render one tier checkbox item + its children (if expanded) into the control panel
function renderCheckboxNode(tierName, level, colorIdx) {{
    if (hiddenTiers.has(tierName)) return;
    var indent = level * 18;
    var prefix = '';
    if (level > 0) {{
        prefix = '<span class=\"tree-indent\" style=\"width:' + (level*16) + 'px;display:inline-block;flex-shrink:0;\"></span>' +
                 '<span style=\"color:#555;flex-shrink:0;\">└</span>';
    }}
    var toggleHtml = '';
    if (hasChildren(tierName)) {{
        toggleHtml = '<span class=\"tier-toggle-list\" data-tier=\"' + tierName + '\" style=\"cursor:pointer;flex-shrink:0;width:14px;text-align:center;font-size:10px;\">' +
            (collapsedTiers.has(tierName) ? '▶' : '▼') + '</span>';
    }} else {{
        toggleHtml = '<span style=\"flex-shrink:0;width:14px;\"></span>';
    }}
    var checkbox = '<input type=\"checkbox\" data-tier=\"' + tierName + '\" ' + (hiddenTiers.has(tierName) ? '' : 'checked') + '>';
    tierList.insertAdjacentHTML('beforeend',
        '<div class=\"tier-item\">' + toggleHtml + prefix + checkbox +
        '<span class=\"tier-color\" style=\"background:' + tierColor(colorIdx) + '\"></span>' +
        '<span style=\"flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;\">' + tierName + '</span>' +
        '<span style=\"color:#666;font-size:10px;\">' + tierSegCount(tierName) + '</span>' +
        '</div>');

    // Recursively render children if expanded
    if (!collapsedTiers.has(tierName)) {{
        var children = getChildren(tierName);
        children.forEach(function(child) {{
            renderCheckboxNode(child, level + 1, colorIdx);
        }});
    }}
}}

// ============================================================
// Error display
// ============================================================
function showErrorBar(msg) {{
    var bar = $('errorBar');
    var span = $('errorMsg');
    span.textContent = msg;
    bar.classList.add('show');
    console.error('[Timeline Editor]', msg);
}}

function hideErrorBar() {{
    var bar = $('errorBar');
    bar.classList.remove('show');
}}

// ============================================================
// Init
// ============================================================
function init() {{
    videoPlayer.src = EDITOR_DATA.videoUrl;
    tiers = EDITOR_DATA.tierOptions.slice();
    // Ensure all segment tierNames are in tiers
    EDITOR_DATA.segments.forEach(function(s) {{
        var tn = s.tierName || s.speaker || 'UNKNOWN';
        if (!tiers.includes(tn)) tiers.push(tn);
    }});
    // Load segments with unique ids
    segments = EDITOR_DATA.segments.map(function(s, i) {{
        return {{
            id: s.id || 'seg_' + (i + 1),
            tier: s.tierName || s.speaker || tiers[0] || 'UNKNOWN',
            start: s.start || 0,
            end: s.end || (s.start + 1),
            text: s.text || '',
            speaker: s.speaker || s.tierName || '',
            words: s.words || [],
        }};
    }});
    _segCounter = segments.length + 1;
    updateTierCheckboxes();
    updateTierDropdown();
    updateStats();
    renderAll();
    bindEvents();
    updateUndoButtons();
}}

// ============================================================
// Tier management
// ============================================================
function updateTierCheckboxes() {{
    tierList.innerHTML = '';
    var roots = getRootTiers();
    roots.forEach(function(root, idx) {{
        renderCheckboxNode(root, 0, idx);
    }});
    // Bind checkbox events
    tierList.querySelectorAll('input[type=checkbox]').forEach(function(cb) {{
        cb.addEventListener('change', function() {{
            var t = cb.dataset.tier;
            if (cb.checked) hiddenTiers.delete(t);
            else hiddenTiers.add(t);
            renderAll();
        }});
    }});
    // Bind toggle icon clicks in tier list
    tierList.querySelectorAll('.tier-toggle-list').forEach(function(el) {{
        el.addEventListener('click', function(e) {{
            e.stopPropagation();
            toggleTierCollapse(el.dataset.tier);
        }});
    }});
}}

function updateTierDropdown() {{
    var sel = $('editTier');
    var cur = sel.value;
    sel.innerHTML = tiers.map(function(t) {{
        return '<option value="' + t + '"' + (t===cur?' selected':'') + '>' + t + '</option>';
    }}).join('');
}}

function updateTierLabels() {{
    // Keep spacer, rebuild labels recursively
    var spacer = tierLabels.querySelector('.label-spacer');
    tierLabels.innerHTML = '';
    tierLabels.appendChild(spacer);
    var roots = getRootTiers();
    roots.forEach(function(root, idx) {{
        renderLabelNode(root, 0, idx);
    }});
    // Bind toggle icon clicks (stop propagation to avoid double-fire)
    tierLabels.querySelectorAll('.tier-toggle').forEach(function(el) {{
        el.addEventListener('click', function(e) {{
            e.stopPropagation();
            toggleTierCollapse(el.dataset.tier);
        }});
    }});
}}

function tierColor(idx) {{ return TIER_COLORS[idx % TIER_COLORS.length]; }}
function tierColorForName(name) {{ return TIER_COLORS[tiers.indexOf(name) % TIER_COLORS.length]; }}
function tierIndex(name) {{
    // Find DOM row index — used for color assignment and other lookup tasks
    var rows = timelineInner.querySelectorAll('.tier-row');
    for (var i = 0; i < rows.length; i++) {{
        if (rows[i].dataset.tier === name) return i;
    }}
    return tiers.indexOf(name); // fallback
}}
function tierSegCount(name) {{ return segments.filter(function(s) {{ return s.tier === name; }}).length; }}

// ============================================================
// Rendering
// ============================================================
function totalWidth() {{ return Math.max(audioDuration * pixelsPerSec, 800); }}
function t2px(t) {{ return t * pixelsPerSec; }}
function px2t(px) {{ return px / pixelsPerSec; }}
function snap(t) {{ return Math.round(t / SNAP) * SNAP; }}
function fmtTime(sec) {{
    var m = Math.floor(sec / 60);
    var s = (sec % 60).toFixed(1);
    return m + ':' + (s < 10 ? '0' : '') + s;
}}

function renderAll() {{
    renderTimeline();
    renderPlayhead();
    updateTierCheckboxes();
    updateTierDropdown();
    updateTierLabels();
    updateStats();
}}

function renderTimeline() {{
    var w = totalWidth();
    timelineInner.style.width = w + 'px';
    timelineRuler.style.width = w + 'px';

    // Ruler canvas
    var dpr = window.devicePixelRatio || 1;
    rulerCanvas.width = w * dpr;
    rulerCanvas.height = 28 * dpr;
    rulerCanvas.style.width = w + 'px';
    rulerCanvas.style.height = '28px';
    var ctx = rulerCanvas.getContext('2d');
    ctx.setTransform(1,0,0,1,0,0);
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, 28);
    ctx.fillStyle = '#16213e';
    ctx.fillRect(0, 0, w, 28);

    // Ticks
    var major, minor;
    if (pixelsPerSec >= 80) {{ major = 1; minor = 0.5; }}
    else if (pixelsPerSec >= 40) {{ major = 2; minor = 0.5; }}
    else if (pixelsPerSec >= 20) {{ major = 5; minor = 1; }}
    else {{ major = 10; minor = 2; }}

    var endT = Math.max(audioDuration, px2t(w));
    ctx.strokeStyle = '#444'; ctx.lineWidth = 1;
    for (var t = 0; t <= endT; t += minor) {{
        var x = t2px(t);
        ctx.beginPath(); ctx.moveTo(x, 20); ctx.lineTo(x, 28); ctx.stroke();
    }}
    ctx.fillStyle = '#aaa'; ctx.font = '10px sans-serif';
    for (var t = 0; t <= endT; t += major) {{
        var x = t2px(t);
        ctx.beginPath(); ctx.moveTo(x, 12); ctx.lineTo(x, 28); ctx.stroke();
        ctx.fillText(fmtTime(t), x + 3, 12);
    }}

    // Tier rows — recursive tree rendering
    var existingRows = timelineInner.querySelectorAll('.tier-row');
    existingRows.forEach(function(r) {{ r.remove(); }});

    var roots = getRootTiers();
    var rowCount = 0;
    roots.forEach(function(root) {{
        rowCount = renderTimelineRow(root, 0, rowCount);
    }});

    // Update timeline height based on actual rendered rows
    var actualRows = timelineInner.querySelectorAll('.tier-row').length;
    timelineInner.style.minHeight = (actualRows * ROW_H + 28) + 'px';
    playhead.style.height = (actualRows * ROW_H + 28) + 'px';
    playhead.style.top = '0px';

    // Segments already rendered during recursive renderTimelineRow
}}

function renderSegment(sid) {{
    var seg = segments.find(function(s) {{ return s.id === sid; }});
    if (!seg) return;
    var ti = tierIndex(seg.tier);
    var rows = timelineInner.querySelectorAll('.tier-row');
    var row = rows[ti];
    if (!row) return;

    var el = row.querySelector('[data-sid="' + sid + '"]');
    if (!el) {{
        el = document.createElement('div');
        el.className = 'segment';
        el.dataset.sid = sid;
        el.innerHTML = '<div class="handle left"></div><span class="seg-text"></span><div class="handle right"></div>';
        row.appendChild(el);
    }}

    var left = t2px(seg.start);
    var w = Math.max(t2px(seg.end - seg.start), 18);
    el.style.left = left + 'px';
    el.style.width = w + 'px';
    el.style.backgroundColor = tierColor(ti) + '99';
    el.style.borderColor = tierColor(ti);

    el.classList.toggle('selected', seg.id === selectedId);

    // Search highlighting
    var query = ($('searchInput').value || '').toLowerCase().trim();
    if (query) {{
        var match = seg.text.toLowerCase().includes(query);
        el.classList.toggle('search-hit', match);
        el.classList.toggle('dimmed', !match);
    }} else {{
        el.classList.remove('search-hit', 'dimmed');
    }}

    el.querySelector('.seg-text').textContent = seg.text || '(空)';
    el.title = '#' + seg.id + ': ' + seg.text + '\\n' + seg.start.toFixed(2) + 's – ' + seg.end.toFixed(2) + 's\\n层: ' + seg.tier;
}}

function renderPlayhead() {{
    var t = videoPlayer.currentTime || 0;
    var x = t2px(t);
    playhead.style.left = x + 'px';
    playhead.style.display = 'block';

    // ================================================================
    // Enhancement #1: Auto-scroll to keep playhead centred during playback
    // ================================================================
    if (!videoPlayer.paused) {{
        var cw = timelineScroll.clientWidth;
        timelineScroll.scrollLeft = Math.max(0, x - cw / 2);
    }}
}}

// ============================================================
// Segment selection & edit panel
// ============================================================
function selectSegment(sid) {{
    if (dragState) return; // Don't change selection during drag
    selectedId = sid;
    textEditDirty = false;
    var seg = segments.find(function(s) {{ return s.id === sid; }});
    if (!seg) {{ deselectAll(); return; }}

    noSelection.style.display = 'none';
    editSection.classList.add('active');
    $('editTier').value = seg.tier;
    $('editStart').value = seg.start.toFixed(2);
    $('editEnd').value = seg.end.toFixed(2);
    $('editText').value = seg.text;

    // Scroll to make segment visible
    var el = timelineInner.querySelector('[data-sid="' + sid + '"]');
    if (el) el.scrollIntoView({{ behavior: 'smooth', block: 'nearest', inline: 'nearest' }});

    segments.forEach(function(s) {{ renderSegment(s.id); }});
}}

function deselectAll() {{
    selectedId = null;
    textEditDirty = false;
    noSelection.style.display = '';
    editSection.classList.remove('active');
    segments.forEach(function(s) {{ renderSegment(s.id); }});
}}

function applyEdit() {{
    if (!selectedId) return;
    saveUndoState();
    textEditDirty = false;
    var seg = segments.find(function(s) {{ return s.id === selectedId; }});
    if (!seg) return;
    var newTier = $('editTier').value;
    seg.tier = newTier;
    seg.start = snap(parseFloat($('editStart').value) || seg.start);
    seg.end = snap(parseFloat($('editEnd').value) || seg.end);
    if (seg.end <= seg.start) seg.end = seg.start + SNAP;
    seg.text = $('editText').value;
    // Ensure tier exists
    if (!tiers.includes(newTier)) {{
        tiers.push(newTier);
    }}
    renderAll();
    selectSegment(selectedId);
    syncToParent();
}}

function deleteSegment(sid) {{
    var idx = segments.findIndex(function(s) {{ return s.id === sid; }});
    if (idx < 0) return;
    saveUndoState();
    segments.splice(idx, 1);
    if (selectedId === sid) deselectAll();
    renderAll();
    syncToParent();
}}

function addSegment(t, tier) {{
    saveUndoState();
    textEditDirty = false;
    var st = snap(t);
    var en = snap(st + 1.0);
    var id = 'seg_' + (_segCounter++);
    var useTier = tier || (tiers.length > 0 ? tiers[0] : 'default');
    segments.push({{ id: id, tier: useTier, start: st, end: en, text: '新标注', speaker: useTier, words: [] }});
    segments.sort(function(a, b) {{ return a.start - b.start; }});
    if (!tiers.includes(useTier)) tiers.push(useTier);
    renderAll();
    selectSegment(id);
    syncToParent();
    setTimeout(function() {{ $('editText').focus(); }}, 80);
}}

// ============================================================
// Stats
// ============================================================
function updateStats() {{
    $('statCount').textContent = segments.length;
    var q = ($('searchInput').value || '').toLowerCase().trim();
    if (q) {{
        var hits = segments.filter(function(s) {{ return s.text.toLowerCase().includes(q); }}).length;
        $('statHits').textContent = hits;
    }} else {{
        $('statHits').textContent = '-';
    }}
}}

// ============================================================
// Event binding
// ============================================================
function bindEvents() {{
    // Video sync
    videoPlayer.addEventListener('timeupdate', renderPlayhead);
    videoPlayer.addEventListener('loadedmetadata', function() {{
        if (videoPlayer.duration) audioDuration = Math.max(audioDuration, videoPlayer.duration);
        renderAll();
    }});

    // ================================================================
    // Enhancement #2: Ruler always seeks (navigation control).
    // Empty tier-row area seeks only when paused.
    // ================================================================
    timelineRuler.addEventListener('click', function(e) {{
        // Use timelineScroll (fixed container) rect, NOT timelineRuler (moves with scroll)
        var rect = timelineScroll.getBoundingClientRect();
        var x = e.clientX - rect.left + timelineScroll.scrollLeft;
        videoPlayer.currentTime = Math.max(0, Math.min(px2t(x), videoPlayer.duration || audioDuration));
    }});

    // Segment click → select (not seek)
    timelineInner.addEventListener('click', function(e) {{
        var segEl = e.target.closest('.segment');
        if (segEl) {{
            if (e.target.closest('.handle')) return; // Handled by pointer events
            selectSegment(segEl.dataset.sid);
        }}
    }});

    // Click tier row empty area → seek only when paused
    timelineInner.addEventListener('click', function(e) {{
        if (e.target.closest('.segment') || e.target.closest('.timeline-ruler')) return;
        if (e.target.classList.contains('tier-row') || e.target.closest('.tier-row')) {{
            if (!videoPlayer.paused) return; // Enhancement #2: seek only when paused
            // Use timelineScroll rect (fixed container), NOT timelineInner (moves with scroll)
            var rect = timelineScroll.getBoundingClientRect();
            var x = e.clientX - rect.left + timelineScroll.scrollLeft;
            videoPlayer.currentTime = Math.max(0, Math.min(px2t(x), videoPlayer.duration || audioDuration));
        }}
    }});

    // ============================================================
    // Enhancement #3: Double-click empty area → create segment at click position
    // (click position is computed from event coordinates, NOT playhead time)
    // ============================================================
    timelineInner.addEventListener('dblclick', function(e) {{
        if (e.target.closest('.segment') || e.target.closest('.timeline-ruler')) return;
        var row = e.target.closest('.tier-row');
        var tier = row ? row.dataset.tier : (tiers[0] || 'default');
        // Use timelineScroll rect (fixed container), NOT timelineInner (moves with scroll)
        var rect = timelineScroll.getBoundingClientRect();
        var x = e.clientX - rect.left + timelineScroll.scrollLeft;
        var t = px2t(x);
        if (t >= 0) addSegment(t, tier);
    }});

    // Pointer events for drag
    timelineInner.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('pointermove', onPointerMove);
    document.addEventListener('pointerup', onPointerUp);

    // Control panel buttons
    $('btnApplyEdit').addEventListener('click', applyEdit);
    $('btnDeleteSeg').addEventListener('click', function() {{
        if (selectedId) {{ deleteSegment(selectedId); }}
    }});
    $('btnAddTier').addEventListener('click', function() {{
        var name = $('newTierInput').value.trim();
        if (name && !tiers.includes(name)) {{
            saveUndoState();
            tiers.push(name);
            $('newTierInput').value = '';
            renderAll();
        }}
    }});
    $('btnDeleteTier').addEventListener('click', function() {{
        if (!selectedId) {{ alert('请先选择一个标注以指定要删除的层'); return; }}
        var seg = segments.find(function(s) {{ return s.id === selectedId; }});
        if (!seg) return;
        var tier = seg.tier;
        if (!confirm('确定删除层 "' + tier + '" 及其所有 ' + tierSegCount(tier) + ' 个标注吗？')) return;
        saveUndoState();
        // Remove segments in this tier
        segments = segments.filter(function(s) {{ return s.tier !== tier; }});
        tiers = tiers.filter(function(t) {{ return t !== tier; }});
        hiddenTiers.delete(tier);
        deselectAll();
        renderAll();
    }});
    $('newTierInput').addEventListener('keydown', function(e) {{
        if (e.key === 'Enter') $('btnAddTier').click();
    }});

    // Undo / Redo buttons
    $('btnUndo').addEventListener('click', undo);
    $('btnRedo').addEventListener('click', redo);

    // Search
    var searchTimer;
    $('searchInput').addEventListener('input', function() {{
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function() {{
            segments.forEach(function(s) {{ renderSegment(s.id); }});
            updateStats();
        }}, 200);
    }});
    $('btnClearSearch').addEventListener('click', function() {{
        $('searchInput').value = '';
        segments.forEach(function(s) {{ renderSegment(s.id); }});
        updateStats();
    }});

    // Edit panel: tier / start / end change → apply immediately after saving undo state
    ['editStart','editEnd'].forEach(function(id) {{
        $(id).addEventListener('change', function() {{
            applyEdit();
        }});
    }});
    $('editTier').addEventListener('change', function() {{
        applyEdit();
    }});

    // Edit text: save undo state on first keystroke, then live-update segment
    $('editText').addEventListener('input', function() {{
        if (!selectedId) return;
        if (!textEditDirty) {{
            saveUndoState();
            textEditDirty = true;
        }}
        var seg = segments.find(function(s) {{ return s.id === selectedId; }});
        if (seg) {{ seg.text = $('editText').value; renderSegment(seg.id); syncToParent(); }}
    }});

    // ============================================================
    // Keyboard shortcuts
    // ============================================================
    document.addEventListener('keydown', function(e) {{
        // Undo / Redo — always captured, even when focus is in inputs
        if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {{
            e.preventDefault();
            undo();
            return;
        }}
        if ((e.ctrlKey || e.metaKey) && (e.key === 'Z' || (e.key === 'z' && e.shiftKey))) {{
            e.preventDefault();
            redo();
            return;
        }}

        // Other shortcuts only when NOT in a text input
        if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;
        if (e.key === 'Delete' || e.key === 'Backspace') {{
            if (selectedId) {{ e.preventDefault(); deleteSegment(selectedId); }}
        }}
        if (e.key === 'Escape') deselectAll();
        if (e.key === ' ') {{
            e.preventDefault();
            if (videoPlayer.paused) videoPlayer.play(); else videoPlayer.pause();
        }}
    }});

    // Resize
    window.addEventListener('resize', function() {{ renderAll(); }});

    // Zoom with Ctrl+scroll
    timelineScroll.addEventListener('wheel', function(e) {{
        if (e.ctrlKey || e.metaKey) {{
            e.preventDefault();
            var factor = e.deltaY < 0 ? 1.25 : 0.8;
            pixelsPerSec = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, pixelsPerSec * factor));
            renderAll();
        }}
    }});

    // Sync vertical scroll between timeline and tier labels
    var scrollSyncing = false;
    timelineScroll.addEventListener('scroll', function() {{
        if (scrollSyncing) return;
        scrollSyncing = true;
        tierLabels.scrollTop = timelineScroll.scrollTop;
        scrollSyncing = false;
    }});
    tierLabels.addEventListener('scroll', function() {{
        if (scrollSyncing) return;
        scrollSyncing = true;
        timelineScroll.scrollTop = tierLabels.scrollTop;
        scrollSyncing = false;
    }});
}}

// ============================================================
// Drag handlers
// ============================================================
function onPointerDown(e) {{
    var handle = e.target.closest('.handle');
    var segEl = e.target.closest('.segment');
    if (!segEl) return;
    var sid = segEl.dataset.sid;
    var seg = segments.find(function(s) {{ return s.id === sid; }});
    if (!seg) return;

    e.preventDefault();
    segEl.setPointerCapture(e.pointerId);

    if (sid !== selectedId) selectSegment(sid);

    var mode = handle
        ? (handle.classList.contains('left') ? 'resize-left' : 'resize-right')
        : 'move';

    // Save undo state before any drag modification
    saveUndoState();
    textEditDirty = false;

    dragState = {{
        segId: sid, mode: mode, startX: e.clientX, startY: e.clientY,
        origStart: seg.start, origEnd: seg.end, origTier: seg.tier,
        pointerId: e.pointerId,
    }};

    segEl.classList.add('dragging');
}}

function onPointerMove(e) {{
    if (!dragState || e.pointerId !== dragState.pointerId) return;

    var seg = segments.find(function(s) {{ return s.id === dragState.segId; }});
    if (!seg) return;

    var dx = e.clientX - dragState.startX;
    var dy = e.clientY - dragState.startY;
    var dt = snap(px2t(dx));

    if (dragState.mode === 'move') {{
        var dur = dragState.origEnd - dragState.origStart;
        var ns = snap(dragState.origStart + dt);
        if (ns < 0) ns = 0;
        seg.start = ns;
        seg.end = snap(ns + dur);

        // Cross-tier drag detection
        if (Math.abs(dy) > ROW_H * 0.6) {{
            var rowRects = [];
            timelineInner.querySelectorAll('.tier-row').forEach(function(r) {{
                if (!r.classList.contains('hidden-tier')) rowRects.push({{ tier: r.dataset.tier, rect: r.getBoundingClientRect() }});
            }});
            for (var i = 0; i < rowRects.length; i++) {{
                var item = rowRects[i];
                if (e.clientY >= item.rect.top && e.clientY <= item.rect.bottom) {{
                    if (item.tier !== seg.tier) {{
                        seg.tier = item.tier;
                        dragState.origTier = item.tier;
                        dragState.startY = e.clientY;
                        if (!tiers.includes(item.tier)) tiers.push(item.tier);
                        renderAll();
                        selectSegment(seg.id);
                    }}
                    break;
                }}
            }}
        }}
    }} else if (dragState.mode === 'resize-left') {{
        var ns = snap(dragState.origStart + dt);
        if (ns < 0) ns = 0;
        if (ns >= seg.end - SNAP) ns = seg.end - SNAP;
        seg.start = ns;
    }} else if (dragState.mode === 'resize-right') {{
        var ne = snap(dragState.origEnd + dt);
        if (ne <= seg.start + SNAP) ne = seg.start + SNAP;
        seg.end = ne;
    }}

    renderSegment(seg.id);
    if (seg.id === selectedId) {{
        $('editStart').value = seg.start.toFixed(2);
        $('editEnd').value = seg.end.toFixed(2);
        $('editTier').value = seg.tier;
    }}
}}

function onPointerUp(e) {{
    if (!dragState || e.pointerId !== dragState.pointerId) return;
    var seg = segments.find(function(s) {{ return s.id === dragState.segId; }});
    if (seg) {{
        seg.start = snap(seg.start);
        seg.end = snap(seg.end);
    }}
    dragState = null;
    if (seg) {{
        renderSegment(seg.id);
        if (seg.id === selectedId) {{
            $('editStart').value = seg.start.toFixed(2);
            $('editEnd').value = seg.end.toFixed(2);
        }}
        syncToParent();
    }}
}}

// ============================================================
// Public API
// ============================================================
window.getEditedSegments = function() {{
    var clean = segments.map(function(s) {{
        return {{
            id: s.id,
            start: snap(s.start),
            end: snap(s.end),
            text: s.text || '',
            speaker: s.tier,
            tierName: s.tier,
            words: s.words || [],
        }};
    }});
    return JSON.stringify({{
        segments: clean,
        speakers: tiers.slice(),
        tierOptions: tiers.slice(),
        audioDuration: audioDuration,
    }});
}};

// Continuously sync edited data to the parent page's hidden textarea
// so Gradio always reads the latest edits when the export button is clicked.
function syncToParent() {{
    var json = window.getEditedSegments();
    var segCount = JSON.parse(json).segments.length;
    var synced = false;

    // Method 1: direct DOM access (works when iframe has allow-same-origin)
    try {{
        var hiddenEl = parent.document.querySelector('#hidden-json-input textarea, #hidden-json-input input');
        if (hiddenEl) {{
            hiddenEl.value = json;
            hiddenEl.dispatchEvent(new Event('input', {{bubbles: true}}));
            synced = true;
            console.log('[syncToParent] Wrote ' + segCount + ' segments to parent DOM');
        }}
    }} catch(e) {{
        console.warn('[syncToParent] Direct DOM access failed, trying postMessage:', e.message);
    }}

    // Method 2: postMessage fallback (works cross-origin or when sandbox restricts DOM)
    if (!synced) {{
        try {{
            parent.postMessage({{
                type: 'TIMELINE_SYNC',
                json: json,
                segCount: segCount,
            }}, '*');
            console.log('[syncToParent] Sent ' + segCount + ' segments via postMessage');
            synced = true;
        }} catch(e2) {{
            console.error('[syncToParent] postMessage also failed:', e2.message);
        }}
    }}

    if (!synced) {{
        console.error('[syncToParent] FAILED to sync ' + segCount + ' segments — export will use stale data!');
    }}
}}

window.getOriginalSegments = function() {{
    return JSON.stringify(EDITOR_DATA);
}};

// ============================================================
// Start
// ============================================================
init();
</script>
</body>
</html>"""
    escaped = html.escape(inner_html, quote=True)
    return f'<iframe sandbox="allow-scripts allow-same-origin" style="width:100%;height:85vh;border:none;display:block;" srcdoc="{escaped}"></iframe>'
