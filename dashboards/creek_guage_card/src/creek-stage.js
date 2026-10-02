// Reference updater for the Creek Stage SVGs — the exact mapping Claude Code should port
// into the custom card's render(). Pure DOM; no HA dependencies.
(function () {
  const STATUS = [
    { key: 'major', label: 'Major', color: 'var(--prism-purple,#8a6fd6)' },
    { key: 'flood', label: 'Flood', color: 'var(--prism-bad,#c64b4b)' },
    { key: 'action', label: 'Action', color: 'var(--prism-warn,#e0922e)' },
  ];
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  // Piecewise: bed→bank (crest) fills the channel; bank→max rises over the banks.
  // `bank` = stage at the top of the creek bank; defaults to flood stage when unset.
  function stageToY(stage, c, g) {
    const bank = c.bank != null ? c.bank : c.flood;
    const max = Math.max(c.max, bank + 0.01);
    const s = clamp(stage, c.bed, max);
    if (s <= bank) return g.bed - ((s - c.bed) / ((bank - c.bed) || 1)) * (g.bed - g.bank);
    return g.bank - ((s - bank) / ((max - bank) || 1)) * (g.bank - g.top);
  }
  // No reading is its own state, not "Normal": the gateway blanks stage to unknown while the
  // creek node is offline, and a green pill over a missing number reads as all-clear.
  const noData = (v) => v == null || isNaN(v);
  function status(stage, c) {
    if (noData(stage)) return { key: 'nodata', label: 'No data', color: 'var(--prism-text-secondary,#6b7280)' };
    for (const s of STATUS) if (c[s.key] != null && stage >= c[s.key]) return s;
    return { key: 'normal', label: 'Normal', color: 'var(--prism-good,#3aa76d)' };
  }
  const fmt = (v, d = 1) => (v == null || isNaN(v) ? '—' : Number(v).toFixed(d));
  const trendText = (t, unit) => (t == null || isNaN(t) ? '' : (Math.abs(t) < 0.005 ? '→ steady' : (t > 0 ? '▲ ' : '▼ ') + fmt(Math.abs(t), 1) + ' ' + unit + '/h'));
  // Rising water is "worse" (invert_trend): up = bad, down = good.
  const trendColor = (t) => (t == null || Math.abs(t) < 0.005 ? 'var(--prism-text-secondary,#6b7280)' : t > 0 ? 'var(--prism-bad,#c64b4b)' : 'var(--prism-good,#3aa76d)');
  const set = (root, sel, txt) => { const el = root.querySelector(sel); if (el) el.textContent = txt; return el; };

  function fitPill(root, bgSel, textSel, rightX, pad) {
    const t = root.querySelector(textSel), bg = root.querySelector(bgSel);
    if (!t || !bg || !t.getComputedTextLength) return;
    const w = Math.ceil(t.getComputedTextLength()) + pad * 2;
    bg.setAttribute('width', w); bg.setAttribute('x', rightX - w);
    t.setAttribute('x', rightX - w / 2);
  }
  function fitChip(root, bgSel, x, pad) {
    const bg = root.querySelector(bgSel); if (!bg) return;
    const t = bg.nextElementSibling; if (!t || !t.getComputedTextLength) return;
    bg.setAttribute('width', Math.ceil(t.getComputedTextLength()) + pad * 2);
  }

  // cfg: { stage, unit, bed, bank, action, flood, major, max, trend, flow, flowUnit, peak, updated, title }
  window.updateCreekCard = function (svg, cfg) {
    const c = Object.assign({ unit: 'ft', bed: 0, max: 12 }, cfg);
    if (c.max == null || c.max <= (c.bank ?? c.flood)) c.max = (c.bank ?? c.flood) * 1.4;
    const G = { bed: 204, bank: 121, top: 92 };
    const st = status(c.stage, c);
    const water = svg.querySelector('#water');
    water.style.display = noData(c.stage) ? 'none' : '';   // no level to draw, not an empty channel
    if (!noData(c.stage)) water.setAttribute('transform', 'translate(0 ' + stageToY(c.stage, c, G).toFixed(1) + ')');
    [['action', '.th-action'], ['flood', '.th-flood'], ['major', '.th-major']].forEach(([k, sel]) => {
      const g = svg.querySelector(sel); if (!g) return;
      if (c[k] == null || (k === 'flood' && c.bank != null && c.flood === c.bank)) { g.style.display = 'none'; return; }
      g.style.display = '';
      g.setAttribute('transform', 'translate(0 ' + stageToY(c[k], c, G).toFixed(1) + ')');
      set(g, 'text', k.toUpperCase() + ' ' + fmt(c[k], 1) + ' ' + c.unit);
      const tw = g.querySelector('text').getComputedTextLength ? g.querySelector('text').getComputedTextLength() : 56;
      const r = g.querySelector('rect'); r.setAttribute('width', Math.ceil(tw) + 8); r.setAttribute('x', 378 - Math.ceil(tw) - 8);
    });
    const crest = svg.querySelector('#bank-crest-label');
    if (crest) crest.textContent = 'BANK ' + fmt(c.bank != null ? c.bank : c.flood, 1) + ' ' + c.unit;
    if (c.title) set(svg, '#stage-title', c.title);
    set(svg, '#stage-value', fmt(c.stage, 1));
    set(svg, '#stage-unit', c.unit);
    const tr = set(svg, '#stage-trend', trendText(c.trend, c.unit)); if (tr) tr.setAttribute('fill', trendColor(c.trend));
    set(svg, '#stage-status-text', st.label);
    svg.querySelector('#stage-status-bg').setAttribute('fill', st.color);
    fitPill(svg, '#stage-status-bg', '#stage-status-text', 384, 11);
    set(svg, '#chip-flow', c.flow == null ? '—' : Math.round(c.flow) + ' ' + (c.flowUnit || 'cfs'));
    set(svg, '#chip-peak', fmt(c.peak, 1) + ' ' + c.unit);
    fitChip(svg, '#chip-flow-bg', 16, 10);
    const peakBg = svg.querySelector('#chip-peak-bg'), flowBg = svg.querySelector('#chip-flow-bg');
    if (peakBg && flowBg) { const x = 16 + Number(flowBg.getAttribute('width')) + 6; peakBg.setAttribute('x', x); peakBg.nextElementSibling.setAttribute('x', x + 10); fitChip(svg, '#chip-peak-bg', x, 10); }
    if (c.updated) set(svg, '#stage-updated', c.updated);
    svg.setAttribute('aria-label', 'Creek stage ' + fmt(c.stage, 1) + ' ' + c.unit + ', ' + st.label.toLowerCase());
  };

  window.updateCreekFeature = function (svg, cfg) {
    const c = Object.assign({ unit: 'ft', bed: 0, max: 12 }, cfg);
    const st = status(c.stage, c);
    const fWater = svg.querySelector('#f-water');
    fWater.style.display = noData(c.stage) ? 'none' : '';
    if (!noData(c.stage)) fWater.setAttribute('transform', 'translate(0 ' + stageToY(c.stage, c, { bed: 36, bank: 13, top: 4 }).toFixed(1) + ')');
    set(svg, '#f-value', fmt(c.stage, 1));
    set(svg, '#f-unit', c.unit);
    const s = set(svg, '#f-status', st.label); if (s) s.setAttribute('fill', st.color);
    const tr = set(svg, '#f-trend', trendText(c.trend, c.unit)); if (tr) tr.setAttribute('fill', trendColor(c.trend));
  };
})();
