(function starfield() {
  const canvas = document.querySelector('#starfield');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let stars = [];
  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect();
    canvas.width = rect.width * devicePixelRatio;
    canvas.height = rect.height * devicePixelRatio;
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;
    const count = Math.round((rect.width * rect.height) / 3200);
    stars = Array.from({ length: count }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      r: (Math.random() * 1.1 + 0.3) * devicePixelRatio,
      phase: Math.random() * Math.PI * 2,
      speed: Math.random() * 0.015 + 0.006,
    }));
  }
  function draw(time) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const star of stars) {
      const twinkle = reduceMotion ? 0.75 : 0.45 + 0.55 * Math.abs(Math.sin(star.phase + time * star.speed));
      ctx.globalAlpha = twinkle;
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(star.x, star.y, star.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    if (!reduceMotion) requestAnimationFrame(draw);
  }
  resize();
  window.addEventListener('resize', resize);
  requestAnimationFrame(draw);
})();

const form = document.querySelector('#requestForm');
const startInput = document.querySelector('#start');
const exportBtn = document.querySelector('#exportBtn');
const csvBtn = document.querySelector('#csvBtn');
const packageBtn = document.querySelector('#packageBtn');
const modeInput = document.querySelector('#mode');
const cutoffLabel = document.querySelector('#cutoffLabel');
const cutoffInput = document.querySelector('#cutoff');
let latestResult = null;
const severityLabels = { info: 'информационно', not_detected: 'не выявлено', low: 'ограничение выполнено', moderate: 'требует внимания', high: 'высокий', unknown: 'нет данных' };
const sourceStatusLabels = { live: 'актуален', fresh: 'свежие данные', cached: 'из кеша', stale: 'устарел', unavailable: 'недоступен', archived: 'архив', archive: 'архив', disabled: 'отключён', unparseable: 'ошибка формата' };
const evidenceKindLabels = { observation: 'наблюдение', external_forecast: 'внешний прогноз', derived_signal: 'расчёт команды' };
const modeLabels = { current: 'текущая обстановка', historical: 'исторический разбор', replay: 'прогноз из прошлого', current_monitoring: 'текущая обстановка', historical_analysis: 'исторический разбор', replay_strict: 'прогноз из прошлого' };
const operationalLabels = { HOLD_DATA: 'Стоп: нет критичных данных', HOLD_POLICY: 'Ожидает ввода обязательных данных', HOLD_DOSIMETRY: 'Стоп: превышен дозиметрический порог', NO_GO_REVIEW: 'Стоп: радиационный пересмотр', HOLD_RADIATION: 'Пауза: радиационная проверка', REVIEW: 'Требуется проверка специалистом', CONDITIONAL: 'Условно пригодно для дальнейшего анализа' };
const domainStatusLabels = { critical: 'стоп', review: 'проверка', monitor: 'мониторинг', verified: 'данные полные' };

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}
function safeUrl(value) {
  try { const url = new URL(value); return ['http:','https:'].includes(url.protocol) ? url.href : null; } catch { return null; }
}

function utcInputValue(date) {
  return date.toISOString().slice(0, 16);
}
startInput.value = utcInputValue(new Date());
function syncMode() {
  cutoffLabel.hidden = modeInput.value !== 'replay';
  cutoffInput.required = modeInput.value === 'replay';
  if (modeInput.value === 'replay' && !cutoffInput.value) cutoffInput.value = startInput.value;
}
modeInput.addEventListener('change', syncMode);
syncMode();

function timeLabel(value) {
  return new Intl.DateTimeFormat('ru-RU', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short', year: 'numeric' }).format(new Date(value));
}

function windowExplanation(window, best) {
  if (window.score === best.score) return window.start === best.start ? 'Минимальный индекс среди доступных вариантов.' : 'Равноценно лучшему по доступным данным.';
  const rank = { unknown: 3, high: 2, moderate: 1, low: 0, not_detected: 0, info: 0 };
  const relevant = window.factors.filter(factor => factor.decision_relevant);
  const worstRank = Math.max(...relevant.map(factor => rank[factor.severity] ?? 3));
  const factor = relevant.filter(item => (rank[item.severity] ?? 3) === worstRank).sort((a,b) => b.overlap_minutes-a.overlap_minutes)[0];
  const duration = Math.max(1, (new Date(window.end)-new Date(window.start))/60000);
  const share = Math.round((factor?.overlap_minutes || 0) / duration * 100);
  return factor ? `Хуже из-за фактора «${factor.name}»: ${severityLabels[factor.severity] || factor.severity}, перекрытие ${share}% окна.` : 'Индекс выше по совокупности доступных факторов.';
}

function renderLighting(result) {
  const panel = document.querySelector('#lightingTimeline');
  const intervals = result.trajectory.shadow_intervals || [];
  const start = new Date(result.request.start).getTime();
  const end = start + (Number(result.request.search_hours) + Number(result.request.duration_hours)) * 3600000;
  if (!result.trajectory.points) {
    panel.className = 'lighting-panel empty';
    panel.textContent = 'Нет подходящих орбитальных элементов: тень не интерпретируется как свет.';
    return;
  }
  const segments = intervals.map(item => {
    const left = Math.max(0, (new Date(item.start).getTime()-start)/(end-start)*100);
    const right = Math.min(100, (new Date(item.end).getTime()-start)/(end-start)*100);
    return `<span class="shadow-segment" style="left:${left}%;width:${Math.max(0,right-left)}%" title="Тень: ${timeLabel(item.start)} – ${timeLabel(item.end)} UTC"></span>`;
  }).join('');
  panel.className = 'lighting-panel';
  panel.innerHTML = `<div class="lighting-legend"><span><i class="light-key"></i>Свет</span><span><i class="shadow-key"></i>Тень</span></div><div class="lighting-track" aria-label="Интервалы света и модельной тени">${segments}</div><div class="lighting-axis"><span>${timeLabel(new Date(start).toISOString())}</span><span>${timeLabel(new Date(end).toISOString())}</span></div>`;
}

function render(result) {
  latestResult = result;
  const bestScore = Math.min(...result.windows.map(item => item.score));
  const bestWindows = result.windows.filter(item => item.score === bestScore);
  const firstBest = bestWindows[0];
  exportBtn.disabled = false;
  csvBtn.disabled = false;
  packageBtn.disabled = false;
  document.querySelector('#recommendation').textContent = result.recommendation.startsWith('Предпочтительное начало:')
    ? `Предпочтительное окно: ${timeLabel(firstBest.start)} — ${timeLabel(firstBest.end)} UTC`
    : result.recommendation;
  document.querySelector('#dataNotice').textContent = `${result.data_notice} ID расчёта: ${result.result_id || 'не сохранён'}`;
  const status = document.querySelector('#operatorStatus');
  status.textContent = operationalLabels[result.operational_status] || result.operational_status;
  status.dataset.state = result.operational_status;
  document.querySelector('#operatorAction').textContent = result.operator_action;
  document.querySelector('#riskDomains').innerHTML = result.risk_domains.map(domain => `<article class="risk-domain ${escapeHtml(domain.status)}"><div><strong>${escapeHtml(domain.name)}</strong><span>${escapeHtml(domainStatusLabels[domain.status] || domain.status)}</span></div><small>${escapeHtml(domain.basis)}</small><p>${escapeHtml(domain.limitation)}</p></article>`).join('');
  document.querySelector('#decisionConfidence').textContent = result.decision_confidence === 'high' ? 'Все обязательные условия закрыты' : 'Часть условий ожидает заполнения';
  document.querySelector('#decisionGates').innerHTML = result.decision_gates.map(gate => `<div class="decision-gate ${gate.passed ? 'passed' : 'failed'}"><span aria-hidden="true">${gate.passed ? '✓' : '—'}</span><strong>${escapeHtml(gate.label)}</strong></div>`).join('');
  document.querySelector('#sources').innerHTML = result.source_status.map(source => `<div class="source-item"><strong>${escapeHtml(source.name)}</strong><span class="source-state ${escapeHtml(source.status)}">${escapeHtml(sourceStatusLabels[source.status] || source.status)}</span><br>${source.last_success ? `Ответ получен: ${timeLabel(source.last_success)} UTC · ${source.age_minutes} мин назад` : 'Успешное получение не зафиксировано'}${source.product_timestamp ? `<br>Данные относятся к: ${timeLabel(source.product_timestamp)} UTC · возраст продукта ${source.product_age_minutes} мин` : ''}<br>Охват: ${escapeHtml(source.coverage)}</div>`).join('');
  document.querySelector('#rationale').innerHTML = result.rationale.map(item => `<p>${escapeHtml(item)}</p>`).join('');
  document.querySelector('#rationale').innerHTML += `<details class="technical"><summary>Метод и технические параметры</summary><p><strong>Режим:</strong> ${escapeHtml(modeLabels[result.evaluation_mode] || result.evaluation_mode)}; <strong>cutoff:</strong> ${result.historical_cutoff ? timeLabel(result.historical_cutoff) + ' UTC' : 'не применяется'}</p><p><strong>Траектория:</strong> ${escapeHtml(result.trajectory.source)}; эпоха: ${result.trajectory.epoch ? timeLabel(result.trajectory.epoch) + ' UTC' : 'не подтверждена'}; модель: ${escapeHtml(result.trajectory.propagator)}</p><p><strong>Индекс:</strong> категория худшего критичного фактора + максимальная доля пересечения одного фактора. Меньше — предпочтительнее; разные механизмы не суммируются.</p></details>`;
  const completeness = result.windows.some(item => item.completeness === 'частичное покрытие') ? 'Частичное покрытие' : 'Источники обработаны';
  document.querySelector('#summary').innerHTML = `<div><span>Кандидатов</span><strong>${result.windows.length}</strong></div><div><span>Минимальный индекс</span><strong>${bestScore}</strong></div><div><span>Равноценных</span><strong>${bestWindows.length}</strong></div><div><span>Охват данных</span><strong>${completeness}</strong></div><div><span>Высота ISS</span><strong>${result.trajectory.min_altitude_km != null ? `${result.trajectory.min_altitude_km}–${result.trajectory.max_altitude_km} км` : 'нет данных'}</strong></div>`;
  const baseline = result.windows[0];
  const improvement = baseline.score - bestScore;
  const uniqueBest = bestWindows.length === 1;
  document.querySelector('#baseline').innerHTML = `<div><span>Исходное окно</span><strong>${timeLabel(baseline.start)} UTC</strong><small>индекс ${baseline.score}</small></div><div class="baseline-arrow" aria-hidden="true">${uniqueBest ? '→' : '='}</div><div><span>${uniqueBest ? 'Предпочтительное окно' : 'Автоматический выбор'}</span><strong>${uniqueBest ? `${timeLabel(firstBest.start)} UTC` : 'не выполнен'}</strong><small>${uniqueBest ? `индекс ${bestScore}` : `${bestWindows.length} вариантов равноценны`}</small></div><div class="improvement ${improvement > 0 && uniqueBest ? 'positive' : ''}"><span>Изменение индекса</span><strong>${uniqueBest && improvement > 0 ? `−${improvement}` : 'нет'}</strong><small>Эвристика, не вероятность травмы</small></div>`;
  const preferred = [...result.windows].sort((a,b) => a.score-b.score || new Date(a.start)-new Date(b.start)).slice(0,3);
  document.querySelector('#preferredWindows').innerHTML = preferred.map((item,index) => `<article><span class="preference-rank">${index+1}</span><div><strong>${timeLabel(item.start)} UTC</strong><small>Индекс ${item.score} · ${escapeHtml(severityLabels[item.severity] || item.severity)}</small><p>${escapeHtml(windowExplanation(item, firstBest))}</p></div></article>`).join('');
  const groups = result.windows.reduce((items, item) => { const last=items.at(-1); if(last && last.score===item.score && last.severity===item.severity){last.end=item.end;last.count+=1;}else items.push({start:item.start,end:item.end,count:1,score:item.score,severity:item.severity}); return items; }, []);
  document.querySelector('#timeline').classList.remove('empty');
  document.querySelector('#timeline').innerHTML = groups.map(item => `<div class="slot ${item.severity} ${uniqueBest && item.score === bestScore ? 'best' : ''}"><strong>${timeLabel(item.start)}${item.count>1 ? ` – ${timeLabel(item.end)}` : ''}</strong><br>${escapeHtml(severityLabels[item.severity] || item.severity)}<br>индекс ${item.score}${item.count>1 ? ` · ${item.count} окон` : ''}</div>`).join('');
  renderLighting(result);
  document.querySelector('#windows').innerHTML = `<table><thead><tr><th>Выбор</th><th>Начало UTC</th><th>Окончание</th><th>Уровень</th><th>Почему лучше / хуже</th><th>Охват</th><th>Индекс</th></tr></thead><tbody>${result.windows.map((item,index) => `<tr tabindex="0" role="button" aria-selected="false" data-index="${index}" class="${uniqueBest && item.score === bestScore ? 'recommended' : ''}"><td data-label="Выбор" class="selection-mark">○</td><td data-label="Начало UTC">${timeLabel(item.start)}</td><td data-label="Окончание UTC">${timeLabel(item.end)}</td><td data-label="Уровень"><span class="badge ${item.severity}">${escapeHtml(severityLabels[item.severity] || item.severity)}</span></td><td data-label="Объяснение" class="why-cell">${escapeHtml(windowExplanation(item, firstBest))}</td><td data-label="Охват">${item.completeness === 'частичное покрытие' ? 'частичный' : 'обработан'}</td><td data-label="Индекс"><strong>${item.score}</strong></td></tr>`).join('')}</tbody></table>`;
  const factorIds=[...new Set(result.windows.flatMap(item=>item.factors.filter(f=>f.decision_relevant).map(f=>f.factor_id)))];
  const factorNames=Object.fromEntries(result.windows.flatMap(item=>item.factors.map(f=>[f.factor_id,f.name])));
  document.querySelector('#factorMatrix').innerHTML=`<table class="factor-matrix"><thead><tr><th>Начало UTC</th>${factorIds.map(id=>`<th>${escapeHtml(factorNames[id])}</th>`).join('')}<th>Индекс</th></tr></thead><tbody>${result.windows.map(item=>`<tr><td data-label="Начало UTC">${timeLabel(item.start)}</td>${factorIds.map(id=>{const f=item.factors.find(x=>x.factor_id===id);return `<td data-label="${escapeHtml(factorNames[id])}"><span class="badge ${f?.severity||'unknown'}">${escapeHtml(severityLabels[f?.severity]||f?.severity||'нет данных')}</span>${f?.overlap_minutes?` <small>${f.overlap_minutes} мин</small>`:''}</td>`}).join('')}<td data-label="Индекс"><strong>${item.score}</strong></td></tr>`).join('')}</tbody></table>`;
  document.querySelectorAll('#windows tbody tr').forEach(row => { const select=()=>renderEvidence(result.windows[Number(row.dataset.index)]); row.addEventListener('click',select); row.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();select();}}); });
  const selected = uniqueBest ? firstBest : result.windows[0];
  renderEvidence(selected);
  const verificationSection=document.querySelector('#verificationSection'); verificationSection.hidden=!result.verification;
  if(result.verification) document.querySelector('#verification').innerHTML=`<strong>${result.verification.observation ? 'Проверка выполнена' : 'Наблюдение недоступно'}</strong><p>${escapeHtml(result.verification.forecast)}</p>${result.verification.observation?`<p><b>Последующее наблюдение:</b> ${escapeHtml(result.verification.observation)}</p>`:''}<p>${escapeHtml(result.verification.note)}</p>`;
}

function renderEvidence(selected) {
  document.querySelectorAll('#windows tbody tr').forEach(row => { const active=latestResult?.windows[Number(row.dataset.index)]?.start === selected.start; row.classList.toggle('selected',active); row.setAttribute('aria-selected',String(active)); row.querySelector('.selection-mark').textContent=active?'●':'○'; });
  document.querySelector('#selectedWindow').textContent = `${timeLabel(selected.start)} — ${timeLabel(selected.end)} UTC`;
  document.querySelector('#evidence').innerHTML = selected.factors.map(factor => { const evidence=factor.evidence[0]; const url=safeUrl(evidence?.source_url); return `<article class="factor ${factor.severity}"><h4>${escapeHtml(factor.name)}</h4><span class="badge ${factor.severity}">${escapeHtml(severityLabels[factor.severity] || factor.severity)}</span><span class="confidence">Уверенность: ${escapeHtml(factor.confidence)}</span><p>${escapeHtml(factor.summary)}</p>${url ? `<a class="primary-source" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Первоисточник</a>` : ''}<details><summary>Правило, доказательства и ограничения</summary><p><strong>Механизм:</strong> ${escapeHtml(factor.mechanism)}</p><p><strong>Применённое правило:</strong> ${escapeHtml(factor.applied_rule)}</p><p><strong>Основание уверенности:</strong> ${escapeHtml(factor.confidence_basis)}</p>${factor.evidence.map(item => { const itemUrl=safeUrl(item.source_url); return `<div class="source"><span class="evidence-kind">${escapeHtml(evidenceKindLabels[item.kind] || item.kind)}</span><strong>${escapeHtml(item.title)}</strong><br>${escapeHtml(item.value)}${item.unit ? ` ${escapeHtml(item.unit)}` : ''}<br>${item.observed_at ? `Начало воздействия: ${timeLabel(item.observed_at)} UTC<br>` : ''}${item.published_at ? `Опубликовано: ${timeLabel(item.published_at)} UTC<br>` : ''}ID записи: ${escapeHtml(item.record_id)}${item.record_sha256?`<br>SHA-256 записи: <code>${escapeHtml(item.record_sha256)}</code>`:''}<br>${escapeHtml(item.note)}${itemUrl ? `<br><a href="${escapeHtml(itemUrl)}" target="_blank" rel="noopener noreferrer">Открыть первоисточник</a>` : ''}</div>`; }).join('')}<p>${escapeHtml(factor.limitations.join(' '))}</p></details></article>`; }).join('');
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = 'Расчёт...';
  const progress = document.querySelector('#jobProgress');
  progress.hidden = false; progress.className = 'job-progress running';
  document.querySelector('#jobStatus').textContent = modeInput.value === 'current' ? 'Шаг 1 из 3 · получение источников…' : 'Шаг 1 из 3 · чтение датированного архива…';
  const statusTimer = setTimeout(() => { document.querySelector('#jobStatus').textContent = 'Шаг 2 из 3 · расчёт траектории и сравнение окон…'; }, 450);
  try {
    const disabled = [];
    if (document.querySelector('#disableNoaa').checked) disabled.push('noaa');
    if (document.querySelector('#disableOrbit').checked) disabled.push('orbit');
    const doseRate=document.querySelector('#doseRate').value; const doseLimit=document.querySelector('#doseLimit').value; const doseTimestamp=document.querySelector('#doseTimestamp').value;
    const payload = { mode: modeInput.value, start: `${startInput.value}:00Z`, cutoff: cutoffInput.value ? `${cutoffInput.value}:00Z` : null, duration_hours: Number(document.querySelector('#duration').value), search_hours: Number(document.querySelector('#search').value), step_minutes: Number(document.querySelector('#step').value), disabled_sources: disabled, continuous_lighting_required: document.querySelector('#lightingRequired').checked, force_refresh: document.querySelector('#forceRefresh').checked, operation_profile: document.querySelector('#operationProfile').value, policy_version: document.querySelector('#policyVersion').value, crew_dose_rate_usv_h: doseRate ? Number(doseRate) : null, crew_dose_limit_usv_h: doseLimit ? Number(doseLimit) : null, dosimetry_timestamp: doseTimestamp ? `${doseTimestamp}:00Z` : null };
    const response = await fetch('/api/assess', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (!response.ok) {
      const problem = await response.json().catch(() => ({}));
      const detail = Array.isArray(problem.detail) ? problem.detail.map(item => item.msg).join('; ') : problem.detail;
      throw new Error(detail || `сервер вернул HTTP ${response.status}`);
    }
    render(await response.json());
    document.querySelector('#jobStatus').textContent = `Шаг 3 из 3 · расчёт сохранён на сервере: ${latestResult.result_id}`;
    progress.classList.remove('running'); progress.classList.add('complete');
  } catch (error) {
    document.querySelector('#recommendation').textContent = `Ошибка расчёта: ${error.message}`;
    document.querySelector('#jobStatus').textContent = 'Расчёт не завершён. Проверьте параметры и доступность источников.';
    progress.classList.remove('running'); progress.classList.add('failed');
  } finally {
    clearTimeout(statusTimer);
    button.disabled = false;
    button.textContent = 'Рассчитать окна';
  }
});

exportBtn.addEventListener('click', () => {
  if (latestResult?.result_id) window.location.href = `/api/results/${latestResult.result_id}/result.json`;
});
packageBtn.addEventListener('click', () => {
  if (latestResult?.result_id) window.location.href = `/api/results/${latestResult.result_id}/export`;
});
csvBtn.addEventListener('click', () => {
  if (latestResult?.result_id) window.location.href = `/api/results/${latestResult.result_id}/windows.csv`;
});

fetch('/api/health').then(response => response.json()).then(data => {
  void data;
}).catch(() => {});
form.requestSubmit();


/* ---------- модальное окно параметров ---------- */
(function paramsDialog() {
  const dialog = document.querySelector('#paramsDialog');
  const openBtn = document.querySelector('#openParams');
  const closeBtn = document.querySelector('#closeParams');
  if (!dialog || !openBtn) return;

  openBtn.addEventListener('click', () => dialog.showModal());
  closeBtn?.addEventListener('click', () => dialog.close());

  /* Клик по подложке за пределами окна закрывает его. */
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    const outside = event.clientX < box.left || event.clientX > box.right
      || event.clientY < box.top || event.clientY > box.bottom;
    if (outside) dialog.close();
  });

  /* После запуска расчёта окно закрывается: прогресс виден на самой странице. */
  document.querySelector('#requestForm')?.addEventListener('submit', () => dialog.close());
})();


/* ---------- бургер: оглавление по разделам отчёта ---------- */
(function sectionNav() {
  const toggle = document.querySelector('#navToggle');
  const nav = document.querySelector('#sectionNav');
  const scrim = document.querySelector('#navScrim');
  if (!toggle || !nav || !scrim) return;

  /* Список строится из самой страницы, поэтому не расходится с разметкой. */
  function build() {
    const sections = document.querySelectorAll('.workspace > section:not(.operator-decision)');
    nav.innerHTML = '<h2>Разделы отчёта</h2>';
    for (const section of sections) {
      if (section.hidden) continue;
      const title = section.querySelector('h3');
      if (!title) continue;
      if (!title.id) title.id = `section-${Math.random().toString(36).slice(2, 8)}`;
      const link = document.createElement('a');
      link.href = `#${title.id}`;
      link.textContent = title.textContent.trim();
      nav.appendChild(link);
    }
  }

  function open() {
    build();
    nav.hidden = false;
    scrim.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
  }
  function close() {
    nav.hidden = true;
    scrim.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
  }

  toggle.addEventListener('click', () => (nav.hidden ? open() : close()));
  scrim.addEventListener('click', close);
  nav.addEventListener('click', event => { if (event.target.tagName === 'A') close(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !nav.hidden) close(); });
})();

/* ---------- оформление разделов: звёздный фон и звёздочка в заголовке ---------- */
(function decorateSections() {
  /* Те же контурные фигуры, что на главной. */
  const SPARKLE = "M50 3 C53.5 38 62 46.5 97 50 C62 53.5 53.5 62 50 97 C46.5 62 38 53.5 3 50 C38 46.5 46.5 38 50 3 Z";
  const BURST = (() => {
    const points = [];
    for (let i = 0; i < 10; i += 1) {
      const radius = i % 2 === 0 ? 47 : 19;
      const angle = (Math.PI * i) / 5 - Math.PI / 2;
      points.push(`${(50 + radius * Math.cos(angle)).toFixed(1)},${(50 + radius * Math.sin(angle)).toFixed(1)}`);
    }
    return `<polygon points="${points.join(" ")}" />`;
  })();

  const shapes = [`<path d="${SPARKLE}" />`, BURST];
  const sections = document.querySelectorAll(".workspace > section:not(.operator-decision)");

  sections.forEach((section, index) => {
    const title = section.querySelector("h3");
    if (title && !title.querySelector(".head-star")) {
      const star = document.createElement("span");
      /* Цвет чередуется: розовая, чёрная, розовая… */
      star.className = `head-star head-star--${index % 2 === 0 ? "pink" : "ink"}`;
      star.setAttribute("aria-hidden", "true");
      star.innerHTML = `<svg viewBox="0 0 100 100" focusable="false">${shapes[index % shapes.length]}</svg>`;
      title.prepend(star);
    }

    /* Содержимое раздела переезжает на звёздную подложку; заголовок остаётся снаружи. */
    if (section.querySelector(":scope > .section-sky")) return;
    const content = [...section.children].filter(node => !node.matches(".section-head, h3"));
    if (!content.length) return;
    const sky = document.createElement("div");
    sky.className = "section-sky";
    section.insertBefore(sky, content[0]);
    for (const node of content) sky.appendChild(node);
  });
})();

/* Время измерения должно быть свежее 30 минут — подставляем текущее UTC одной кнопкой. */
(function doseNowButton() {
  const button = document.querySelector("#doseNow");
  const field = document.querySelector("#doseTimestamp");
  if (!button || !field) return;
  button.addEventListener("click", () => {
    /* Поле трактуется как UTC (на отправке к нему дописывается Z), поэтому берём UTC-части. */
    field.value = new Date().toISOString().slice(0, 16);
  });
})();
