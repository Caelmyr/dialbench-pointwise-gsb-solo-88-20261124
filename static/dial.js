'use strict';

const el = id => document.getElementById(id);
const defaultMode = 'reading';
const fields = ['low_value', 'high_value', 'unit', 'clockwise', 'band_in', 'band_out', 'dark_cut', 'bright_cut'];
const state = {panel: null, settings: null, archived: null, tool: 'pointer', lastProbe: null,
  request: 0, suggestion: null, suggesting: false};

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
  const locked = Boolean(archived);
  for (const id of ['mode', 'analyze', 'save', 'settings', 'source', 'suggest']) el(id).disabled = locked;
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
  referenceRow();
  el('probe-result').textContent = '';
  el('cursor').textContent = 'Original image coordinates';
  archives();
  paint();
}

function referenceRow() {
  const config = shown();
  const radius = config.scale_radius;
  const present = radius != null;
  el('reference-row').hidden = state.archived !== null || !present;
  if (present) {
    el('reference-readout').textContent = 'Scan radius from suggestion · ' + Number(radius).toFixed(1) + ' px';
  }
  el('reference-clear').hidden = state.archived !== null;
}

function clearSuggestion() {
  state.suggestion = null;
  el('suggestion').hidden = true;
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
  clearSuggestion();
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
  if (state.suggestion) suggestionRings(ctx, state.suggestion, unit);
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

function suggestionRings(ctx, proposal, unit) {
  if (proposal.status !== 'ok' || !proposal.preview) return;
  const {center, inner_px, outer_px, reference_px, rays} = proposal.preview;
  ctx.save();
  ctx.setLineDash([unit * 4, unit * 3]);
  ctx.strokeStyle = '#ffcb76';
  ctx.lineWidth = unit * 1.4;
  for (const radius of [inner_px, outer_px]) {
    if (radius > 2) {
      ctx.beginPath();
      ctx.arc(center[0], center[1], radius, 0, Math.PI * 2);
      ctx.stroke();
    }
  }
  ctx.setLineDash([]);
  ctx.strokeStyle = '#ffcb76';
  ctx.lineWidth = unit * 1.2;
  for (const [start, end] of rays || []) {
    ctx.beginPath();
    ctx.moveTo(...start);
    ctx.lineTo(...end);
    ctx.stroke();
  }
  ctx.fillStyle = '#ffcb76';
  ctx.strokeStyle = '#18261f';
  ctx.beginPath();
  ctx.arc(center[0], center[1], unit * 4.2, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(center[0] - unit * 8, center[1]);
  ctx.lineTo(center[0] + unit * 8, center[1]);
  ctx.moveTo(center[0], center[1] - unit * 8);
  ctx.lineTo(center[0], center[1] + unit * 8);
  ctx.stroke();
  ctx.font = 'bold ' + Math.max(9, unit * 6) + 'px Arial';
  ctx.fillStyle = '#17271f';
  const label = 'SUGGESTED · ' + reference_px.toFixed(1) + ' px';
  ctx.fillRect(center[0] + unit * 10, center[1] - unit * 9,
    ctx.measureText(label).width + unit * 2, unit * 8);
  ctx.fillStyle = '#ffcb76';
  ctx.fillText(label, center[0] + unit * 11, center[1] - unit * 3);
  ctx.restore();
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

function render(report) {
  const area = el('results');
  const {mode, rows} = report;
  area.replaceChildren();
  if (mode === 'reading') {
    const row = rows[0];
    if (row.state !== 'in_range') {
      const messages = {choose_pointer: 'Select a point on the needle to take a reading.',
                        at_pivot: 'Choose a needle point away from the center.',
                        outside_scale: 'The selected needle direction is outside the calibrated sweep.'};
      area.textContent = messages[row.state];
      return;
    }
    const value = document.createElement('p');
    value.className = 'value';
    value.append(row.reading.toFixed(2));
    const unit = document.createElement('small');
    unit.textContent = row.unit;
    value.append(unit);
    const details = document.createElement('p');
    details.textContent = row.angle_deg.toFixed(2) + '° · pixel ' + row.pointer_x + ', ' + row.pointer_y;
    area.append(value, details);
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
  if (state.tool === 'center' || state.tool === 'low_mark' || state.tool === 'high_mark') {
    // A hand-placed mark invalidates any suggestion drawn over the old geometry.
    // The adopted reference radius stands until it is cleared, so re-clicking a
    // mark never widens the scan band on its own.
    clearSuggestion();
  }
  paint();
  await inspect();
}

function suggestionSummary(proposal) {
  if (proposal.status !== 'ok') return proposal.message;
  const band = proposal.band;
  const center = proposal.center.map(value => value.toFixed(1)).join(', ');
  return 'Suggested centre ' + center + ' · scan radius ' + band.reference_px.toFixed(1) +
    ' px (' + band.tick_inner_px.toFixed(1) + '–' + band.tick_outer_px.toFixed(1) +
    ' px tick ends) · ' + proposal.confidence + ' confidence.';
}

function showSuggestion(proposal) {
  state.suggestion = proposal;
  const box = el('suggestion');
  box.hidden = false;
  el('suggestion-summary').textContent = suggestionSummary(proposal);
  const notes = (proposal.notes || []).concat(proposal.reasons || []);
  el('suggestion-notes').replaceChildren(...notes.map(note => {
    const line = document.createElement('li');
    line.textContent = note;
    return line;
  }));
  const adoptable = proposal.status === 'ok' && proposal.proposal;
  el('adopt').disabled = !adoptable;
  el('adopt').hidden = !adoptable;
  paint();
}

async function requestSuggestion() {
  if (!state.panel || state.archived || state.suggesting) return;
  state.suggesting = true;
  el('suggest').disabled = true;
  try {
    const result = await api(route() + '/calibrate', 'POST', {settings: formSettings()});
    showSuggestion(result);
    if (result.status === 'ok') {
      status('Suggestion shown on the original photo. Adopt it or keep the manual marks.');
    } else {
      status(result.message, true);
    }
  } finally {
    state.suggesting = false;
    el('suggest').disabled = Boolean(state.archived);
  }
}

async function adoptSuggestion() {
  const proposal = state.suggestion;
  if (!proposal || proposal.status !== 'ok' || !proposal.proposal) return;
  const current = state.panel.id;
  const draft = proposal.proposal;
  // Send the calibration exactly as it stands in the form: only the centre,
  // reference radius and scan band change, so the clicked marks and readings stay.
  const next = await api(route() + '/calibrate/adopt', 'POST', {
    revision: state.panel.revision,
    settings: formSettings(),
    center: draft.center,
    ref_radius: draft.ref_radius,
    band_in: draft.band_in,
    band_out: draft.band_out,
  });
  if (current !== state.panel.id) return;
  state.panel = next;
  state.settings = {...next.settings};
  clearSuggestion();
  updateView();
  await inspect();
  status('Suggestion adopted. Your low and high marks and values are unchanged.');
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
  updateView();
  await inspect();
});
bind('mode', 'change', inspect);
bind('analyze', 'click', inspect);
bind('suggest', 'click', requestSuggestion);
bind('dismiss', 'click', () => {
  clearSuggestion();
  status('Kept the current manual calibration.');
});
bind('adopt', 'click', adoptSuggestion);
bind('reference-clear', 'click', async () => {
  if (!state.panel || state.archived) return;
  const current = state.panel.id;
  // An explicit null tells the server to drop the adopted radius; omitting it
  // would keep the stored value.
  const next = await api(route() + '/settings', 'PUT',
    {revision: state.panel.revision, settings: {...formSettings(), scale_radius: null}});
  if (current !== state.panel.id) return;
  state.panel = next;
  state.settings = {...next.settings};
  updateView();
  await inspect();
  status('The scan now follows the clicked marks again.');
});
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
  selectTool('pointer');
  updateView();
  await inspect();
});
guard(async () => {
  const entries = await listPanels();
  const latest = localStorage.getItem('dialbench-panel');
  if (entries.some(entry => entry.id === latest)) await openPanel(latest);
})();
