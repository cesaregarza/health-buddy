/* Optional, session-only exercise ranking for the published workout. */
const TrainingFast = (() => {
  let generation = 0;
  let activeController = null;
  const remembered = new Map();
  const MAX_STEPS = 32;
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };

  function mount(host, {snapshot, revision, identity, rows, enabled = false} = {}) {
    activeController?.abort();
    activeController = new AbortController();
    const ownGeneration = ++generation;
    if (!host) return;
    host.textContent = '';
    if (!enabled) { host.hidden = true; return; }
    const recommendation = snapshot?.recommendation;
    const exercises = recommendation?.template?.exercises || [];
    const date = recommendation?.date || '';
    const list = rows || document.querySelector('#training-next .rx-list');
    if (!exercises.length || !list) {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    const planKey = JSON.stringify([identity?.installationId,identity?.datasetId,identity?.restoreEpoch,date,revision || '',recommendation.template]);

    const card = el('section', 'fast-mode');
    const header = el('div', 'fast-mode-head');
    header.append(el('strong', '', 'Workout priorities'));
    const chooser = el('div', 'seg fast-mode-chooser');
    chooser.setAttribute('role', 'group');
    chooser.setAttribute('aria-label', 'Workout planning mode');
    const fullButton = el('button', '', 'Full workout');
    const fastButton = el('button', '', 'Fast mode');
    for (const button of [fullButton, fastButton]) button.type = 'button';
    chooser.append(fullButton, fastButton);
    header.append(chooser);
    card.append(header);
    const panel = el('div', 'fast-mode-panel');
    panel.setAttribute('aria-live', 'polite');
    card.append(panel);
    host.append(card);

    const previous = remembered.get(planKey);
    let currentState = previous?.state || null;
    let running = false;
    let paused = false;
    let mode = previous?.mode === 'fast' ? 'fast' : 'full';
    let modeEpoch = 0;
    let busy = false;
    const remember = () => remembered.set(planKey, {mode, state: currentState});
    const requestIsCurrent = epoch => identityIsCurrent() && mode === 'fast' && epoch === modeEpoch;
    const timedFetch = async (url, options = {}) => {
      const controller = new AbortController();
      const parentSignal = activeController.signal;
      const cancel = () => controller.abort();
      if (parentSignal.aborted) cancel();
      else parentSignal.addEventListener('abort', cancel, {once: true});
      const timeout = setTimeout(cancel, 90000);
      try { return await fetch(url, {...options, signal: controller.signal}); }
      finally { clearTimeout(timeout); parentSignal.removeEventListener('abort', cancel); }
    };

    const allRows = () => [...list.querySelectorAll('.rx-row')];
    const rowByIndex = () => new Map(allRows().map((row, fallback) => {
      const value = Number(row.dataset.exerciseIndex);
      return [Number.isInteger(value) ? value : fallback, row];
    }));
    const originalOrder = () => {
      const map = rowByIndex();
      [...map.entries()].sort((a, b) => a[0] - b[0]).forEach(([, row]) => list.append(row));
      list.querySelectorAll('.fast-priority').forEach(badge => badge.remove());
    };
    const identityIsCurrent = () => ownGeneration === generation && host.isConnected;
    const validState = (state, requestedStep = null) => {
      if (!state || state.schema_version !== 1 || state.date !== date || state.source_revision !== revision) return false;
      if (!['not_started', 'ranking', 'threshold', 'complete'].includes(state.phase)) return false;
      if (!Number.isInteger(state.step) || state.step < 0 || state.step > MAX_STEPS) return false;
      if (!Array.isArray(state.ranked) || !Array.isArray(state.remaining) || !state.scores || typeof state.scores !== 'object') return false;
      const seen = new Set();
      for (const item of state.ranked) {
        if (!item || !Number.isInteger(item.index) || item.index < 0 || item.index >= exercises.length || item.id !== `e${item.index}` || seen.has(item.id)) return false;
        if (!Number.isFinite(item.score) || item.score < 0 || item.score > 4) return false;
        seen.add(item.id);
      }
      for (const id of state.remaining) {
        if (typeof id !== 'string' || !/^e\d+$/.test(id) || id !== `e${Number(id.slice(1))}` || Number(id.slice(1)) >= exercises.length || seen.has(id)) return false;
        seen.add(id);
      }
      if (seen.size !== exercises.length || typeof state.plan_id !== 'string' || !state.plan_id) return false;
      if (['threshold','complete'].includes(state.phase) && state.remaining.length) return false;
      if (state.phase === 'complete' && (typeof state.threshold_uncertain !== 'boolean' ||
          (state.must_count !== null && (!Number.isInteger(state.must_count) || state.must_count < 0 || state.must_count > exercises.length)))) return false;
      if (currentState && (state.plan_id !== currentState.plan_id || currentState.ranked.some((item,i)=>state.ranked[i]?.id!==item.id))) return false;
      if (requestedStep != null && state.phase !== 'complete' && state.step <= requestedStep) return false;
      return true;
    };
    const renderStatus = () => {
      panel.textContent = '';
      if (!currentState) {
        panel.append(el('p', 'fast-mode-copy', 'Build a suggested priority order for a shorter session. This order is a guide; it does not require a particular workout execution order.'));
        panel.append(el('p', 'prov', 'Jev receives this workout’s exercises, equipment, work prescription and minimum-session guidance. No health readings or workout log evidence are sent.'));
        const build = el('button', 'action', 'Build with Jev');
        build.type = 'button'; build.disabled = busy;
        build.addEventListener('click', () => buildSequence(false));
        panel.append(build);
        return;
      }

      const ranked = Array.isArray(currentState.ranked) ? currentState.ranked : [];
      const count = exercises.length;
      const scores = currentState.scores || {};
      const scoreCount = Object.keys(scores).length;
      panel.append(el('p', 'fast-mode-progress', `Scored ${scoreCount} of ${count} exercises · priority order ${ranked.length} of ${count}`));
      panel.append(el('p', 'prov', 'Jev receives this workout’s exercises, equipment, work prescription and minimum-session guidance. No health readings or workout log evidence are sent.'));
      const complete = currentState.phase === 'complete';
      const uncertain = complete && (currentState.threshold_uncertain === true || !Number.isInteger(currentState.must_count));
      if (!complete) {
        const stepText = currentState.phase === 'threshold'
          ? 'Evaluating the minimum-session cutoff after the full priority list.'
          : 'Each next choice considers all remaining exercises and the locked priority list.';
        panel.append(el('p', 'fast-mode-copy', `${stepText} Jev’s scores are estimates, not measures of correctness. No “Absolute must” labels appear until the full list and cutoff are complete.`));
        const controls = el('div', 'fast-mode-actions');
        const label = running ? 'Pause after this choice' : currentState.phase === 'not_started' ? 'Build with Jev' : 'Resume with Jev';
        const control = el('button', 'action', label);
        control.type = 'button'; control.disabled = busy && !running;
        control.addEventListener('click', () => {
          if (running) { paused = true; renderStatus(); }
          else buildSequence(true);
        });
        controls.append(control);
        panel.append(controls);
      } else if (uncertain) {
        panel.append(el('p', 'fast-mode-copy', 'Jev could not set a clear minimum-session cutoff, so this ranking makes no “Absolute must” claims. Jev’s scores are estimates, not measures of correctness.'));
      } else {
        panel.append(el('p', 'fast-mode-copy', 'These are suggested priorities for a shorter session. You can still do the exercises in any order. Jev’s scores are estimates, not measures of correctness.'));
      }
      for (const [position, item] of ranked.entries()) {
        const row = rowByIndex().get(item.index);
        if (!row) continue;
        let badge = row.querySelector('.fast-priority');
        if (!badge) { badge = el('span', 'chip fast-priority'); row.append(badge); }
        const score = Number(scores[item.id]?.score ?? item.score);
        let label = `Priority ${position + 1}`;
        if (complete && !uncertain) label = position < currentState.must_count ? 'Absolute must' : 'If time allows';
        const scoreText = Number.isFinite(score) ? (Math.round(score * 10) / 10).toFixed(1) : 'not available';
        badge.textContent = Number.isFinite(score) ? `${label} · score ${scoreText}/4` : label;
        badge.setAttribute('aria-label', `${label}; Jev exercise score ${scoreText} out of 4`);
      }
      const map = rowByIndex();
      ranked.forEach(item => { const row = map.get(item.index); if (row) list.append(row); });
      const already = new Set(ranked.map(item => item.index));
      [...map.entries()].sort((a, b) => a[0] - b[0]).forEach(([index, row]) => { if (!already.has(index)) list.append(row); });

    };

    async function loadState() {
      if (busy) return;
      busy = true; renderStatus();
      const epoch = modeEpoch;
      try {
        const url = `/v1/training/fast?date=${encodeURIComponent(date)}&revision=${encodeURIComponent(revision || '')}`;
        const response = await timedFetch(url, {method: 'GET', headers: {'Accept': 'application/json'}});
        if (!requestIsCurrent(epoch)) return;
        const body = (await HealthAPI.envelope(response,identity)).data;
        if (!requestIsCurrent(epoch)) return;
        if (!validState(body)) throw new Error('The saved ranking did not match this workout. Retry to refresh it.');
        currentState = body; remember();
        busy = false;
        renderStatus();
      } catch (error) {
        if (requestIsCurrent(epoch)) {
          panel.textContent = '';
          panel.append(el('p', 'prov', error.message || 'Could not load the saved ranking.'));
          const retry = el('button', 'action', 'Retry / resume'); retry.type = 'button'; retry.addEventListener('click', loadState); panel.append(retry);
        }
      } finally {
        if (epoch === modeEpoch) busy = false;
      }
    }

    async function buildSequence(resume) {
      if (busy || running || !identityIsCurrent()) return;
      paused = false; running = true; busy = true;
      const epoch = modeEpoch;
      let failed = false;
      if (resume && !currentState) { running = false; busy = false; await loadState(); return; }
      renderStatus();
      try {
        for (let n = 0; n < MAX_STEPS; n++) {
          if (paused || !identityIsCurrent() || mode !== 'fast') break;
          const step = Number.isInteger(currentState?.step) ? currentState.step : 0;
          const response = await timedFetch('/v1/training/fast', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'Accept': 'application/json', 'X-Health-Action': 'rank-training'},
            body: JSON.stringify({date, revision, step}),
          });
          const body = (await HealthAPI.envelope(response,identity)).data;
          if (!requestIsCurrent(epoch)) return;
          if (!validState(body, step)) throw new Error('Jev returned an invalid or unchanged ranking step. Your saved progress is still available; retry to resume.');
          currentState = body; remember();
          renderStatus();
          if (body.phase === 'complete') break;
        }
      } catch (error) {
        failed = true;
        if (requestIsCurrent(epoch)) {
          panel.textContent = '';
          panel.append(el('p', 'prov', `${error.message || 'Fast mode stopped.'} Your original workout is still available.`));
          const retry = el('button', 'action', 'Retry / resume'); retry.type = 'button'; retry.addEventListener('click', () => buildSequence(true)); panel.append(retry);
        }
      } finally {
        if (epoch === modeEpoch) {
          running = false; busy = false;
          if (requestIsCurrent(epoch) && !failed) {
            renderStatus();
            if (!paused && currentState?.phase !== 'complete') panel.append(el('p', 'prov', 'Fast mode paused at its step limit. Select Resume with Jev to continue.'));
          }
        }
      }
    }

    function selectMode(next) {
      if (mode === next) return;
      modeEpoch++;
      activeController.abort();
      activeController = new AbortController();
      mode = next;
      busy = false; running = false;
      remember();
      fullButton.setAttribute('aria-pressed', String(mode === 'full'));
      fastButton.setAttribute('aria-pressed', String(mode === 'fast'));
      originalOrder();
      if (mode === 'full') { panel.textContent = ''; return; }
      if (currentState) renderStatus();
      else loadState();
    }
    fullButton.addEventListener('click', () => selectMode('full'));
    fastButton.addEventListener('click', () => selectMode('fast'));
    fullButton.setAttribute('aria-pressed', String(mode === 'full'));
    fastButton.setAttribute('aria-pressed', String(mode === 'fast'));
    originalOrder();
    if (mode === 'fast') renderStatus();
  }

  return {mount};
})();
