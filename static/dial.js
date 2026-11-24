'use strict';

const el = id => document.getElementById(id);
const defaultMode = 'reading';
const fields = ['low_value', 'high_value', 'unit', 'clockwise', 'band_in', 'band_out', 'dark_cut', 'bright_cut'];
const state = {panel: null, settings: null, archived: null, tool: 'pointer', lastProbe: null, request: 0,
               suggestion: null, evidence: null, evidenceDismissed: false};

function status(message, error = false) {
  el('status').textContent = message;
  el('status').toggleAttribute('data-error', error);
}

function guard(handler) {
  return async event => {
    try {
      await handler(event);
    } catch (error) {
      status(error.message, true);
    }
  };
}

function bind(id, name, handler) {
  el(id).addEventListener(name, guard(handler));
}

async function api(path, method = 'GET', payload) {
  const options = {method};
  if (payload instanceof FormData) {
    options.body = payload;
  } else if (payload !== undefined) {
    options.headers = {'Content-Type': 'application/json'};
    options.body = JSON.stringify(payload);
  }
  const response = await fetch('/dial' + path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The inspection could not be completed.');
  return result;
}

function route() {
  return '/panels/' + state.panel.id;
}

function shown() {
  return state.archived?.settings || state.settings;
}

function formSettings() {
  const values = {...state.settings};
  for (const field of fields) {
    const value = el(field).value;
    if (field === 'clockwise') values[field] = value === 'true';
    else values[field] = field === 'unit' || value === '' ? value : Number(value);
  }
  return values;
}

function selectTool(tool) {
  state.tool = tool;
  document.querySelectorAll('[data-tool]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.tool === tool));
  });
}

function item(label, current, callback) {
  const button = document.createElement('button');
  button.textContent = label;
  if (current) button.setAttribute('aria-current', 'true');
  button.onclick = guard(callback);
  return button;
}

async function listPanels() {
  const entries = await api('/panels');
  el('panels').replaceChildren(...entries.map(entry =>
    item(entry.title, entry.id === state.panel?.id, () => openPanel(entry.id))));
  return entries;
}

function archives() {
  const entries = state.panel.reports.map(entry =>
    item(entry.heading + ' · ' + new Date(entry.created).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}),
         entry.id === state.archived?.id, () => openReport(entry.id)));
  el('archives').replaceChildren(...(entries.length ? entries : [document.createTextNode('No saved inspections.')]));
}

function updateView() {
  const {panel, archived} = state;
  const config = shown();
  const files = archived?.sources || panel.sources;
  el('empty').hidden = true;
  el('photo-wrap').hidden = false;
  el('coordinates').hidden = false;
  el('coordinates').elements.x.max = panel.width - 1;
  el('coordinates').elements.y.max = panel.height - 1;
  el('photo-name').textContent = panel.title;
  el('photo-meta').textContent = panel.width + ' × ' + panel.height + (panel.synthetic ? ' · Synthetic sample' : '');
  el('prep-link').href = '/prepare?panel=' + panel.id;
  const picker = el('source');
  picker.replaceChildren(...Object.entries(files).map(([role, file]) =>
    new Option((role === 'a' ? 'Primary' : 'Reference') + ' · ' + file.name, role)));
  picker.value = config.source;
  const imageUrl = '/dial' + route() + '/source/' + config.source + '.png';
  const query = archived ? '?report=' + archived.id : '?revision=' + panel.revision;
  el('photo').src = imageUrl + query;
  el('source-link').href = imageUrl + query + '&download=1';
  el('source-link').hidden = false;
  for (const field of fields) el(field).value = config[field];
  for (const id of ['mode', 'analyze', 'save', 'settings', 'source']) el(id).disabled = Boolean(archived);
  document.querySelectorAll('[data-tool]').forEach(button => {
    button.disabled = Boolean(archived && button.dataset.tool !== 'probe');
  });
  el('return').hidden = !archived;
  el('downloads').hidden = !archived;
  if (archived) {
    el('mode').value = archived.mode;
    const base = '/dial' + route() + '/reports/' + archived.id;
    el('csv').href = base + '/report.csv';
    el('overlay').href = base + '/overlay.png?download=1';
  }
  el('probe-result').textContent = '';
  el('cursor').textContent = 'Original image coordinates';
  archives();
  paint();
}

async function openPanel(id, supplied) {
  const ticket = ++state.request;
  const next = supplied || await api('/panels/' + id);
  if (ticket !== state.request) return;
  state.panel = next;
  localStorage.setItem('dialbench-panel', id);
  state.settings = {...next.settings};
  state.archived = null;
  state.lastProbe = null;
  state.suggestion = null;
  state.evidence = null;
  state.evidenceDismissed = false;
  selectTool('pointer');
  el('mode').value = defaultMode;
  updateView();
  status('Photo ready.');
  await Promise.all([listPanels(), inspect()]);
}

async function openReport(id) {
  const ticket = ++state.request;
  const result = await api(route() + '/reports/' + id);
  if (ticket !== state.request) return;
  state.archived = result;
  state.lastProbe = null;
  state.suggestion = null;
  state.evidence = null;
  state.evidenceDismissed = false;
  selectTool('probe');
  updateView();
  render(result);
  status('Saved inspection opened.');
}

function paint() {
  if (!state.panel) return;
  const canvas = el('marks');
  const {width, height} = state.panel;
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  const config = shown();
  const center = config.center;
  const unit = Math.max(1, Math.min(width, height) / 280);
  ctx.lineWidth = unit;
  const tone = '#9df4dd';
  const evidence = state.evidence;
  if (evidence && evidence.length) {
    ctx.lineCap = 'round';
    for (const segment of evidence) {
      ctx.strokeStyle = '#0f1f2b';
      ctx.lineWidth = unit * 4.6;
      ctx.beginPath();
      ctx.moveTo(segment.x1, segment.y1);
      ctx.lineTo(segment.x2, segment.y2);
      ctx.stroke();
      ctx.strokeStyle = '#8ad8ff';
      ctx.lineWidth = unit * 2.3;
      ctx.beginPath();
      ctx.moveTo(segment.x1, segment.y1);
      ctx.lineTo(segment.x2, segment.y2);
      ctx.stroke();
    }
    ctx.lineCap = 'butt';
    if (state.suggestion) {
      const [tipX, tipY] = state.suggestion.point;
      ctx.strokeStyle = '#8ad8ff';
      ctx.lineWidth = unit * 1.2;
      ctx.beginPath();
      ctx.arc(tipX, tipY, unit * 4, 0, Math.PI * 2);
      ctx.stroke();
    }
  }
  const traversed = config.pointer;
  if (traversed) {
    ctx.strokeStyle = '#ffcb76';
    ctx.setLineDash([unit * 3, unit * 2]);
    ctx.beginPath();
    ctx.moveTo(...center);
    ctx.lineTo(...traversed);
    ctx.stroke();
    ctx.setLineDash([]);
  }
  const points = [['center', 'C'], ['low_mark', 'LOW'], ['high_mark', 'HIGH'], ['pointer', 'P']];
  state.lastProbe && points.push(['probe', '•']);
  for (const [key, label] of points) {
    const point = key === 'probe' ? state.lastProbe : config[key];
    if (!point) continue;
    const [x, y] = point;
    ctx.strokeStyle = key === 'pointer' ? '#ffcb76' : tone;
    ctx.fillStyle = '#18261f';
    ctx.beginPath();
    ctx.arc(x, y, unit * 3.15, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.font = 'bold ' + Math.max(9, unit * 6) + 'px Arial';
    ctx.fillStyle = '#17271f';
    ctx.fillRect(x + unit * 4, y - unit * 7, ctx.measureText(label).width + unit * 2, unit * 8);
    ctx.fillStyle = key === 'pointer' ? '#ffcb76' : tone;
    ctx.fillText(label, x + unit * 5, y - unit);
  }
}

function plot(rows) {
  const graph = document.createElement('canvas');
  graph.width = 460;
  graph.height = 180;
  graph.className = 'plot';
  graph.setAttribute('aria-label', 'Luminance line graph');
  const ctx = graph.getContext('2d');
  ctx.strokeStyle = '#456257';
  ctx.beginPath();
  for (const level of [25, 70, 115, 160]) {
    ctx.moveTo(15, level);
    ctx.lineTo(445, level);
  }
  ctx.stroke();
  ctx.strokeStyle = '#abdccc';
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (const [index, row] of rows.entries()) {
    const x = 15 + 430 * index / Math.max(1, rows.length - 1);
    const y = 160 - 135 * row.luma / 255;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
  graph.onclick = guard(async event => {
    const bounds = graph.getBoundingClientRect();
    const normalized = (event.clientX - bounds.left) / bounds.width;
    const index = Math.min(rows.length - 1, Math.max(0, Math.round((normalized - 15 / 460) * (rows.length - 1) / (430 / 460))));
    const row = rows[index];
    await probe([row.x, row.y]);
  });
  return graph;
}

const columns = {
  radial: [['radius_px', 'Radius'], ['luma', 'Luma']],
  arc: [['angle_deg', 'Angle'], ['reading', 'Scale'], ['luma', 'Luma']],
  lighting: [['zone', 'Zone'], ['shadow_pct', 'Dark %'], ['highlight_pct', 'Bright %']],
  contrast: [['zone', 'Zone'], ['median', 'Median'], ['spread', 'P90–P10']],
};

function angleDelta(a, b) {
  return Math.abs(((a - b + 180) % 360 + 360) % 360 - 180);
}

function renderSuggestion(area, suggestion, row) {
  state.suggestion = null;
  if (!suggestion) {
    state.evidence = null;
    return;
  }
  const card = document.createElement('div');
  card.className = 'suggest';
  const label = document.createElement('p');
  label.className = 'label';
  label.textContent = 'NEEDLE SUGGESTION';
  card.append(label);
  if (suggestion.state === 'ready') {
    state.suggestion = suggestion;
    if (!state.evidenceDismissed) state.evidence = suggestion.segments;
    const value = document.createElement('p');
    value.className = 'suggest-value';
    value.textContent = suggestion.reading === null
      ? suggestion.angle_deg.toFixed(2) + '° · outside the calibrated sweep'
      : suggestion.reading.toFixed(2) + ' ' + row.unit + ' · ' + suggestion.angle_deg.toFixed(2) + '°';
    const evidence = document.createElement('p');
    evidence.className = 'muted';
    evidence.textContent = 'Needle body visible for ' + Math.round(suggestion.visible_px) + ' of ' +
      Math.round(suggestion.window_px) + ' px scanned. ' +
      (state.evidenceDismissed
        ? 'Use Locate needle body to highlight the visible runs on the photo; missing parts stay blank.'
        : 'The visible runs are highlighted on the photo; missing parts stay blank.');
    card.append(value, evidence);
    const matches = row.angle_deg !== null && row.angle_deg !== undefined &&
      angleDelta(row.angle_deg, suggestion.angle_deg) < 0.75;
    if (matches) {
      const note = document.createElement('p');
      note.className = 'muted';
      note.textContent = 'The current pointer already follows this axis.';
      card.append(note);
    }
    const actions = document.createElement('div');
    actions.className = 'actions';
    if (!state.archived && !matches) {
      const adopt = document.createElement('button');
      adopt.textContent = 'Adopt suggestion';
      adopt.onclick = guard(adoptSuggestion);
      actions.append(adopt);
    }
    const locate = document.createElement('button');
    locate.className = 'outline';
    locate.textContent = 'Locate needle body';
    locate.onclick = guard(locateEvidence);
    actions.append(locate);
    card.append(actions);
  } else {
    state.evidence = null;
    const note = document.createElement('p');
    if (suggestion.state === 'uncertain') {
      const list = suggestion.candidates.map(candidate => candidate.angle_deg.toFixed(1) + '°').join(' and ');
      note.textContent = 'The dark traces support more than one needle axis (' + list +
        '). Choose the needle point by hand — the current reading is unchanged.';
    } else if (suggestion.state === 'no_pointer') {
      note.textContent = 'No clear needle body stands out between the hub and the tick band. ' +
        'Choose the needle point by hand — the current reading is unchanged.';
    } else {
      note.textContent = 'The calibrated geometry leaves no band to scan for the needle.';
    }
    card.append(note);
  }
  area.append(card);
}

async function adoptSuggestion() {
  const suggestion = state.suggestion;
  if (!suggestion || state.archived) return;
  state.settings.pointer = [...suggestion.point];
  state.evidence = suggestion.segments;
  state.evidenceDismissed = false;
  paint();
  await inspect();
  status('Suggested needle axis adopted at ' + suggestion.angle_deg.toFixed(2) + '°.');
}

async function locateEvidence() {
  const segments = state.suggestion?.segments || state.evidence;
  if (!segments || !segments.length) return;
  state.evidence = segments;
  state.evidenceDismissed = false;
  let longest = segments[0];
  for (const segment of segments) {
    if (segment.r2 - segment.r1 > longest.r2 - longest.r1) longest = segment;
  }
  paint();
  el('marks').scrollIntoView({behavior: 'smooth', block: 'nearest'});
  await probe([(longest.x1 + longest.x2) / 2, (longest.y1 + longest.y2) / 2]);
  status('Needle body segment marked on the photo.');
}

function render(report) {
  const area = el('results');
  const {mode, rows} = report;
  area.replaceChildren();
  if (mode === 'reading') {
    const row = rows[0];
    if (row.state === 'in_range') {
      const value = document.createElement('p');
      value.className = 'value';
      value.append(row.reading.toFixed(2));
      const unit = document.createElement('small');
      unit.textContent = row.unit;
      value.append(unit);
      const details = document.createElement('p');
      details.textContent = row.angle_deg.toFixed(2) + '° · pixel ' + row.pointer_x + ', ' + row.pointer_y;
      area.append(value, details);
    } else {
      const messages = {choose_pointer: 'Select a point on the needle to take a reading.',
                        at_pivot: 'Choose a needle point away from the center.',
                        outside_scale: 'The selected needle direction is outside the calibrated sweep.'};
      const note = document.createElement('p');
      note.textContent = messages[row.state];
      area.append(note);
    }
    renderSuggestion(area, report.suggestion, row);
    paint();
    return;
  }
  if (!rows.length) {
    area.textContent = 'Select a valid pointer direction before scanning its radius.';
    return;
  }
  if (mode === 'radial' || mode === 'arc') area.append(plot(rows));
  const wrap = document.createElement('div');
  wrap.className = 'table-wrap';
  const table = document.createElement('table');
  const header = table.createTHead().insertRow();
  for (const [, title] of columns[mode]) {
    const cell = document.createElement('th');
    cell.scope = 'col';
    cell.textContent = title;
    header.append(cell);
  }
  if (rows[0].x !== undefined) header.append(Object.assign(document.createElement('th'), {textContent: 'Photo'}));
  const body = table.createTBody();
  for (const row of rows) {
    const cells = body.insertRow();
    for (const [key] of columns[mode]) cells.insertCell().textContent = row[key] ?? '—';
    if (row.x !== undefined) {
      const jump = document.createElement('button');
      jump.textContent = 'Probe';
      jump.setAttribute('aria-label', 'Probe pixel ' + row.x + ', ' + row.y);
      jump.onclick = guard(() => probe([row.x, row.y]));
      cells.insertCell().append(jump);
    }
  }
  wrap.append(table);
  area.append(wrap);
}

async function inspect() {
  if (!state.panel || state.archived) return;
  const ticket = ++state.request;
  const result = await api(route() + '/preview', 'POST', {mode: el('mode').value, settings: formSettings()});
  if (ticket !== state.request) return;
  render(result);
  status('Inspection current.');
}

async function probe(point) {
  const ticket = state.request;
  const result = await api(route() + '/point', 'POST', {point, settings: formSettings(), report: state.archived?.id});
  if (ticket !== state.request) return;
  state.lastProbe = [result.x, result.y];
  el('probe-result').textContent = 'Photo ' + result.source.toUpperCase() + ' · x ' + result.x +
    ', y ' + result.y + ' · RGB ' + result.rgb.join('/') + ' · luma ' + result.luma + ' · r ' + result.radius_px + ' px';
  paint();
}

function cursorPoint(event) {
  const bounds = el('marks').getBoundingClientRect();
  const x = Math.min(state.panel.width - 1, Math.max(0, (event.clientX - bounds.left) * state.panel.width / bounds.width));
  const y = Math.min(state.panel.height - 1, Math.max(0, (event.clientY - bounds.top) * state.panel.height / bounds.height));
  return [Number(x.toFixed(2)), Number(y.toFixed(2))];
}

async function place(point) {
  if (state.tool === 'probe' || state.archived) return probe(point);
  state.settings[state.tool] = point;
  state.evidence = null;
  state.evidenceDismissed = state.tool === 'pointer';
  paint();
  await inspect();
}

document.querySelectorAll('[data-tool]').forEach(button => {
  button.disabled = true;
  button.onclick = () => selectTool(button.dataset.tool);
});
bind('marks', 'pointermove', event => {
  if (!state.panel) return;
  const [x, y] = cursorPoint(event);
  el('cursor').textContent = 'Original x ' + x + ' · y ' + y;
});
bind('marks', 'click', event => place(cursorPoint(event)));
bind('coordinates', 'submit', async event => {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  await place([Number(data.get('x')), Number(data.get('y'))]);
});
bind('demo', 'click', async () => {
  const next = await api('/demo', 'POST', {});
  await openPanel(next.id, next);
});
bind('upload', 'submit', async event => {
  event.preventDefault();
  const next = await api('/panels', 'POST', new FormData(event.currentTarget));
  el('upload').reset();
  await openPanel(next.id, next);
});
bind('source', 'change', async event => {
  const values = formSettings();
  state.settings = {...values, source: event.target.value};
  state.lastProbe = null;
  state.suggestion = null;
  state.evidence = null;
  state.evidenceDismissed = false;
  updateView();
  await inspect();
});
bind('mode', 'change', inspect);
bind('analyze', 'click', inspect);
bind('persist', 'click', async () => {
  const current = state.panel.id;
  const next = await api(route() + '/settings', 'PUT', {revision: state.panel.revision, settings: formSettings()});
  if (current !== state.panel.id) return;
  state.panel = next;
  state.settings = {...next.settings};
  updateView();
  await inspect();
  status('Calibration saved.');
});
bind('save', 'click', async () => {
  const current = state.panel.id;
  const result = await api(route() + '/reports', 'POST',
                           {revision: state.panel.revision, mode: el('mode').value, settings: formSettings()});
  if (current !== state.panel.id) return;
  state.settings = {...result.report.settings};
  state.panel = result.panel;
  await openReport(result.report.id);
});
bind('return', 'click', async () => {
  state.archived = null;
  state.lastProbe = null;
  state.suggestion = null;
  state.evidence = null;
  state.evidenceDismissed = false;
  selectTool('pointer');
  updateView();
  await inspect();
});
guard(async () => {
  const entries = await listPanels();
  const latest = localStorage.getItem('dialbench-panel');
  if (entries.some(entry => entry.id === latest)) await openPanel(latest);
})();
