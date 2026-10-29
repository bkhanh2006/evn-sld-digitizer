/* SVG tương tác (SDD 8.4.1): bật/tắt layer, click thiết bị đổi trạng thái, tô vùng mang điện.
 *
 * energize() cài cùng thuật toán với Energizer trong src/core/net_builder.py (SDD 8.3.3);
 * tests/test_svg_interactive_js.py chạy bản này bằng Node và so với bản Python.
 * Tên lớp ký hiệu, hành vi điện và màu đều đọc từ khối JSON "sld-meta" do Python ghi,
 * không viết cứng ở đây.
 */
(function (root) {
  'use strict';

  function refKey(r) {
    return r.kind === 'port' ? 'port|' + r.deviceId + '|' + r.portId : r.kind + '|' + r.id;
  }

  function netIndex(graph) {
    const index = {};
    for (const net of graph.nets || []) {
      for (const m of net.members) index[refKey(m)] = net.id;
    }
    return index;
  }

  function sortedUnique(arr) {
    return Array.from(new Set(arr)).sort();
  }

  function deviceState(d) {
    return d.operationalState || d.drawingState || 'unknown';
  }

  function defaultSources(graph, meta) {
    const index = netIndex(graph);
    const kvOf = {};
    for (const n of graph.nets || []) kvOf[n.id] = n.voltageKv === undefined ? null : n.voltageKv;
    const terminals = [];
    for (const d of graph.devices || []) {
      if (meta.symbols[d.detectorClass].type !== meta.sourceDeviceType) continue;
      for (const p of d.ports) terminals.push(index[refKey({kind: 'port', deviceId: d.id, portId: p.id})]);
    }
    const known = terminals.map((n) => kvOf[n]).filter((kv) => kv !== null && kv !== undefined);
    if (known.length === 0) return sortedUnique(terminals);
    const top = Math.max.apply(null, known);
    return sortedUnique(terminals.filter((n) => kvOf[n] === top));
  }

  function energize(graph, meta, sources) {
    const index = netIndex(graph);
    if (sources === undefined || sources === null) sources = defaultSources(graph, meta);
    const nonConducting = new Set(meta.nonConductingPortRoles);

    const edges = [];
    const earthed = new Set();
    for (const d of graph.devices || []) {
      const sym = meta.symbols[d.detectorClass];
      const portNets = d.ports.filter((p) => !nonConducting.has(p.role))
        .map((p) => index[refKey({kind: 'port', deviceId: d.id, portId: p.id})]);
      const state = deviceState(d);
      if (sym.conduction === 'earthing_by_state' && state === 'closed') portNets.forEach((n) => earthed.add(n));
      if (sym.netBehavior !== 'split' || portNets.length < 2) continue;
      if (sym.conduction === 'always') edges.push([portNets, true]);
      else if (sym.conduction === 'by_state') {
        edges.push([portNets, state === 'closed' ? true : (state === 'open' ? false : null)]);
      }
    }
    for (const n of graph.nets || []) if (n.kind === 'ground') earthed.add(n.id);

    const adjacency = {};
    for (const e of edges) {
      for (const n of e[0]) (adjacency[n] = adjacency[n] || []).push(e);
    }

    function reach(start, allowUnknown) {
      const seen = new Set(start);
      const queue = start.slice();
      while (queue.length) {
        const cur = queue.shift();
        for (const [netsOfDev, conducts] of adjacency[cur] || []) {
          if (conducts === true || (allowUnknown && conducts === null)) {
            for (const nxt of netsOfDev) {
              if (!seen.has(nxt)) { seen.add(nxt); queue.push(nxt); }
            }
          }
        }
      }
      return seen;
    }

    const definite = reach(sources, false);
    const maybe = reach(sources, true);
    const states = {};
    const conflicts = [];
    for (const n of graph.nets || []) {
      const energized = definite.has(n.id) ? true : (maybe.has(n.id) ? null : false);
      states[n.id] = {energized: energized, earthed: earthed.has(n.id)};
      if (energized === true && earthed.has(n.id)) conflicts.push(n.id);
    }
    return {states: states, conflicts: conflicts};
  }

  function isSwitchable(meta, cls) {
    const c = meta.symbols[cls].conduction;
    return c === 'by_state' || c === 'earthing_by_state';
  }

  function nextState(state) {
    return state === 'closed' ? 'open' : 'closed';
  }

  /* Lớp ký hiệu cùng loại thiết bị có drawingState = state (đổi hình đóng/mở); null nếu không có. */
  function variantClass(meta, cls, state) {
    const type = meta.symbols[cls].type;
    for (const c of Object.keys(meta.symbols)) {
      if (meta.symbols[c].type === type && meta.symbols[c].drawingState === state) return c;
    }
    return null;
  }

  function netColors(graph, meta, result) {
    const colors = {};
    for (const n of graph.nets || []) {
      const st = result.states[n.id];
      let color = meta.colors.deenergized;
      let dash = null;
      if (st && st.energized === null) { color = meta.colors.unknown; dash = meta.colors.unknownDash; }
      else if (st && st.energized === true) {
        const kv = n.voltageKv === undefined || n.voltageKv === null ? null : String(n.voltageKv);
        color = (kv !== null && meta.colors.voltage[kv]) || meta.colors.unknownVoltage;
      }
      if (st && st.earthed) color = meta.colors.earthed;
      colors[n.id] = {color: color, dash: dash};
    }
    return colors;
  }

  const api = {refKey, netIndex, defaultSources, energize, isSwitchable, nextState, variantClass, netColors};
  root.SLD = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;

  // ------------------------------------------------------------------ phần chạy trong trình duyệt
  if (typeof document === 'undefined' || !document.getElementById('graph-data')) return;

  const graph = JSON.parse(document.getElementById('graph-data').textContent);
  const meta = JSON.parse(document.getElementById('sld-meta').textContent);
  const devById = {};
  for (const d of graph.devices || []) devById[d.id] = d;
  const index = netIndex(graph);

  function recolor() {
    const colors = netColors(graph, meta, energize(graph, meta, meta.sources));
    for (const el of document.querySelectorAll('#layer-busbars [data-net], #layer-wires [data-net]')) {
      const c = colors[el.getAttribute('data-net')];
      if (!c) continue;
      el.setAttribute('stroke', c.color);
      if (c.dash) el.setAttribute('stroke-dasharray', c.dash); else el.removeAttribute('stroke-dasharray');
    }
    for (const el of document.querySelectorAll('#layer-nodes circle[data-net]')) {
      const c = colors[el.getAttribute('data-net')];
      if (c) el.setAttribute('fill', c.color);
    }
    const nonConducting = new Set(meta.nonConductingPortRoles);
    for (const el of document.querySelectorAll('#layer-devices [data-id]')) {
      const d = devById[el.getAttribute('data-id')];
      const cols = d.ports.filter((p) => !nonConducting.has(p.role))
        .map((p) => colors[index[refKey({kind: 'port', deviceId: d.id, portId: p.id})]])
        .filter((c) => c).map((c) => c.color);
      const live = cols.filter((c) => c !== meta.colors.deenergized);
      el.setAttribute('color', live.length ? live[0] : (cols[0] || '#000000'));
    }
  }

  function onDeviceClick(evt) {
    const el = evt.target.closest ? evt.target.closest('[data-id]') : null;
    if (!el) return;
    const d = devById[el.getAttribute('data-id')];
    if (!d || !isSwitchable(meta, d.detectorClass)) return;
    const state = nextState(deviceState(d));
    d.operationalState = state;
    el.setAttribute('data-state', state);
    const cls = variantClass(meta, d.detectorClass, state);
    if (cls) {
      el.setAttribute('href', '#sym-' + cls);
      el.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:href', '#sym-' + cls);
    }
    recolor();
  }

  function onLayerToggle(evt) {
    const el = evt.currentTarget;
    const layer = document.getElementById('layer-' + el.getAttribute('data-layer'));
    if (!layer) return;
    const hidden = layer.getAttribute('display') === 'none';
    layer.setAttribute('display', hidden ? 'inline' : 'none');
    el.textContent = (hidden ? '■ ' : '□ ') + el.textContent.slice(2);
  }

  document.getElementById('layer-devices').addEventListener('click', onDeviceClick);
  for (const el of document.querySelectorAll('#ui-controls [data-layer]')) el.addEventListener('click', onLayerToggle);
  recolor();
  root.sld = {graph: graph, meta: meta, recolor: recolor};
})(typeof globalThis !== 'undefined' ? globalThis : this);
