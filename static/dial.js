'use strict';

const el = id => document.getElementById(id);
const defaultMode = 'reading';
const fields = ['low_value', 'high_value', 'unit', 'clockwise', 'band_in', 'band_out', 'dark_cut', 'bright_cut'];
const state = {panel: null, settings: null, archived: null, tool: 'pointer', lastProbe: null,
               request: 0, report: null, offerPlan: false, display: null};

function roleName(role) {
  return role === 'a' ? 'Primary' : 'Reference';
}

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
  refreshPhoto();
  el('source-link').hidden = false;
  for (const field of fields) el(field).value = config[field];
  for (const id of ['mode', 'analyze', 'save', 'settings', 'source']) el(id).disabled = Boolean(archived);
  const pairing = Object.keys(files).length > 1;
  el('suggest').hidden = !pairing || Boolean(archived) || (archived ? archived.mode : el('mode').value) !== 'arc';
  el('suggest').disabled = Boolean(archived);
  el('single-actions').hidden = true;
  el('split-actions').hidden = true;
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
  allocation(state.report || {mode: el('mode').value, stitched: false});
  paint();
}

async function selectSource(role) {
  if (state.archived) return;
  state.offerPlan = false;
  state.report = null;
  state.display = null;
  state.settings = {...formSettings(), source: role, band_split: false};
  state.lastProbe = null;
  updateView();
  await inspect();
}

function showPhoto(role) {
  if (role === (state.display || shown().source)) return;
  state.display = role;
  state.lastProbe = null;
  refreshPhoto();
  paint();
}

function refreshPhoto() {
  const {panel, archived} = state;
  const role = state.display && (archived?.sources || panel.sources)[state.display]
    ? state.display : shown().source;
  const query = archived ? '?report=' + archived.id : '?revision=' + panel.revision;
  const imageUrl = '/dial' + route() + '/source/' + role + '.png';
  el('source').value = role;
  el('photo').src = imageUrl + query;
  el('source-link').href = imageUrl + query + '&download=1';
}

async function suggestSplit() {
  state.offerPlan = true;
  await inspect();
}

async function adoptSplit() {
  state.offerPlan = false;
  state.report = null;
  state.settings = {...formSettings(), band_split: true};
  state.lastProbe = null;
  updateView();
  paint();
  await inspect();
  status('Photo split adopted. Saving keeps this arc.');
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
  state.report = null;
  state.display = null;
  state.offerPlan = false;
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
  state.report = result;
  state.display = null;
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
  const at = index => 15 + 430 * index / Math.max(1, rows.length - 1);
  ctx.lineWidth = 2;
  ctx.strokeStyle = '#abdccc';
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    const x = at(index);
    if (row.gap || row.luma == null) {
      ctx.strokeStyle = '#6c5140';
      ctx.beginPath();
      ctx.moveTo(x, 160);
      ctx.lineTo(x, 25);
      ctx.stroke();
      ctx.strokeStyle = '#abdccc';
      continue;
    }
    const y = 160 - 135 * row.luma / 255;
    const previous = rows[index - 1];
    if (!previous || previous.gap || previous.luma == null) {
      ctx.beginPath();
      ctx.arc(x, y, 1.8, 0, Math.PI * 2);
      ctx.fillStyle = '#abdccc';
      ctx.fill();
    } else {
      ctx.beginPath();
      ctx.moveTo(at(index - 1), 160 - 135 * previous.luma / 255);
      ctx.lineTo(x, y);
      ctx.stroke();
    }
  }
  graph.onclick = guard(async event => {
    const bounds = graph.getBoundingClientRect();
    const normalized = (event.clientX - bounds.left) / bounds.width;
    const index = Math.min(rows.length - 1, Math.max(0, Math.round((normalized - 15 / 460) * (rows.length - 1) / (430 / 460))));
    const row = rows[index];
    if (row.gap || row.x == null) return status('No photo shows this stretch of the scale — mark it as missing.', true);
    await probe([row.x, row.y], row.source);
  });
  return graph;
}

const columns = {
  radial: [['radius_px', 'Radius'], ['luma', 'Luma']],
  arc: [['angle_deg', 'Angle'], ['reading', 'Scale'], ['luma', 'Luma']],
  lighting: [['zone', 'Zone'], ['shadow_pct', 'Dark %'], ['highlight_pct', 'Bright %']],
  contrast: [['zone', 'Zone'], ['median', 'Median'], ['spread', 'P90–P10']],
};

function allocation(report) {
  const area = el('allocation');
  const useful = report.mode === 'arc' && report.stitched && report.segments?.length
    && (report.segments.length > 1 || (report.missing?.length ?? 0) > 0);
  if (!useful) {
    area.replaceChildren();
    area.hidden = true;
    el('split-actions').hidden = true;
    return;
  }
  area.hidden = false;
  const role = value => value ? roleName(value) : 'No photo';
  const summary = document.createElement('p');
  summary.className = 'allocation-summary';
  const missing = report.missing?.length ?? 0;
  const proposal = report.proposal && !state.settings.band_split;
  summary.textContent = (proposal ? 'Suggested: ' : '') + (missing
    ? missing + ' stretch' + (missing === 1 ? ' needs' : 'es need') + ' a reshoot.'
    : 'Every scale step has a usable photo.');
  const list = document.createElement('ol');
  list.className = 'allocation-list';
  for (const segment of report.segments) {
    const entry = document.createElement('li');
    const label = document.createElement('span');
    label.className = 'allocation-source';
    label.dataset.source = segment.source || 'gap';
    label.textContent = role(segment.source);
    const range = document.createElement('span');
    const span = segment.reading_start == null || segment.reading_end == null
      ? 'offset ' + segment.start + '°–' + segment.end + '°'
      : segment.reading_start.toFixed(2) + '–' + segment.reading_end.toFixed(2);
    range.textContent = ' · ' + span + ' · ' + segment.samples + ' steps';
    entry.append(label, range);
    if (segment.source) {
      const jump = document.createElement('button');
      jump.className = 'link';
      jump.textContent = 'Show photo';
      jump.onclick = guard(() => showPhoto(segment.source));
      entry.append(jump);
    }
    list.append(entry);
  }
  area.replaceChildren(summary, list);
}

function render(report) {
  const area = el('results');
  const {mode, rows} = report;
  area.replaceChildren();
  state.report = report;
  allocation(report);
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
    if (row.gap) cells.className = 'gap-row';
    for (const [key] of columns[mode]) {
      const value = row[key];
      cells.insertCell().textContent = value == null ? (row.gap ? 'missing' : '—') : value;
    }
    if (row.x !== undefined) {
      if (row.gap || row.x == null) {
        cells.insertCell().textContent = '—';
        continue;
      }
      const jump = document.createElement('button');
      jump.textContent = report.stitched ? roleName(row.source) : 'Probe';
      jump.setAttribute('aria-label', 'Probe pixel ' + row.x + ', ' + row.y + ' on ' + roleName(row.source));
      jump.onclick = guard(() => probe([row.x, row.y], row.source));
      cells.insertCell().append(jump);
    }
  }
  wrap.append(table);
  area.append(wrap);
}

async function inspect() {
  if (!state.panel || state.archived) return;
  const ticket = ++state.request;
  const mode = el('mode').value;
  const offer = state.offerPlan && mode === 'arc' && !state.settings.band_split
    && Object.keys(state.panel.sources).length > 1;
  const result = await api(route() + '/preview', 'POST',
                           {mode, settings: formSettings(), plan: offer});
  if (ticket !== state.request) return;
  state.offerPlan = Boolean(result.proposal);
  render(result);
  const split = state.settings.band_split;
  const useful = result.proposal && !split && result.segments?.length > 1;
  el('split-actions').hidden = !useful || Boolean(state.archived);
  el('single-actions').hidden = !(split && result.stitched) || Boolean(state.archived);
  if (result.proposal && !useful) {
    status('Both photos read the same stretches — one is enough here.');
    return;
  }
  status(result.proposal ? 'Suggested split from both photos.' : 'Inspection current.');
}

async function probe(point, source) {
  const ticket = state.request;
  const payload = {point, settings: formSettings(), report: state.archived?.id};
  if (source) payload.source = source;
  const result = await api(route() + '/point', 'POST', payload);
  if (ticket !== state.request) return;
  state.lastProbe = [result.x, result.y];
  const label = roleName(result.source);
  el('probe-result').textContent = 'Photo ' + label + ' · x ' + result.x +
    ', y ' + result.y + ' · RGB ' + result.rgb.join('/') + ' · luma ' + result.luma + ' · r ' + result.radius_px + ' px';
  if (source && result.source !== (state.display || shown().source)) {
    state.display = result.source;
    refreshPhoto();
  }
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
  const role = event.target.value;
  if (state.settings.band_split) return showPhoto(role);
  await selectSource(role);
});
bind('mode', 'change', async () => {
  state.offerPlan = false;
  state.report = null;
  updateView();
  await inspect();
});
bind('analyze', 'click', inspect);
bind('suggest', 'click', suggestSplit);
bind('adopt', 'click', adoptSplit);
bind('single', 'click', async () => {
  await selectSource(state.display || state.settings.source);
  status('Scanning one photo only.');
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
  state.report = null;
  state.display = null;
  selectTool('pointer');
  updateView();
  await inspect();
});
guard(async () => {
  const entries = await listPanels();
  const latest = localStorage.getItem('dialbench-panel');
  if (entries.some(entry => entry.id === latest)) await openPanel(latest);
})();
