/* Session entry — spec section 5.
 *
 * The make-or-break screen: a full 12-race session in under 90 seconds, keyboard
 * only. Tab moves across fields, Enter moves to the next race.
 *
 * Track matching lives on the server, not here, deliberately. The ranking rules are
 * what keep `BC` off Boo Cinema and `SP` off Peach Stadium, and a second copy of
 * those rules in JS is a second copy that can drift. A LAN round-trip is a few
 * milliseconds; a divergent ranking is a corrupted dataset.
 */
(() => {
  const root = document.getElementById('session-root');
  if (!root) return;
  const SESSION = root.dataset.session;

  /* ------------------------------------------------------------- save state */
  let inFlight = 0;
  const stateEl = document.getElementById('save-state');
  const labelEl = document.getElementById('save-label');

  function renderSaveState(errored) {
    if (!stateEl) return;
    if (inFlight > 0) {
      stateEl.classList.remove('hidden');
      stateEl.classList.add('flex');
      labelEl.textContent = 'saving…';
    } else if (errored) {
      stateEl.classList.remove('hidden');
      stateEl.classList.add('flex');
      labelEl.textContent = 'not saved';
    } else {
      stateEl.classList.add('hidden');
      stateEl.classList.remove('flex');
    }
  }

  // Entry is ~99% on the LAN where per-field posts do not meaningfully drop, so
  // this guard replaces an offline queue: it is the thing that satisfies "never
  // lose a partial session to a closed tab" at a fraction of the complexity.
  window.addEventListener('beforeunload', (e) => {
    if (inFlight > 0 || document.querySelector('.row-dot.error')) {
      e.preventDefault();
      e.returnValue = '';
    }
  });

  function dot(el) {
    return el.closest('.race-row')?.querySelector('.row-dot') || null;
  }

  async function post(url, body, method = 'POST') {
    inFlight += 1;
    renderSaveState();
    try {
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!res.ok) {
        let detail = res.statusText;
        try { detail = (await res.json()).detail || detail; } catch (_) {}
        throw new Error(detail);
      }
      return await res.json();
    } finally {
      inFlight -= 1;
      renderSaveState(!!document.querySelector('.row-dot.error'));
    }
  }

  function applyStats(stats) {
    if (!stats) return;
    const avg = document.getElementById('running-avg');
    if (avg) {
      avg.textContent = stats.avg_placement == null ? '—' : stats.avg_placement.toFixed(2);
      avg.classList.remove('text-good-400', 'text-warn-400', 'text-ink-600');
      avg.classList.add(stats.avg_placement == null ? 'text-ink-600'
        : stats.avg_placement <= 6.5 ? 'text-good-400' : 'text-warn-400');
    }
    const logged = document.getElementById('logged-count');
    if (logged) logged.textContent = `${stats.placements_recorded}/${stats.expected_races}`;
    const chip = document.getElementById('complete-chip');
    if (chip) {
      chip.className = stats.is_complete ? 'chip-good' : 'chip-warn';
      chip.textContent = stats.is_complete ? 'complete' : `${stats.missing} missing`;
    }
  }

  /* ------------------------------------------------------------ field saves */
  async function saveRaceField(el, value) {
    const row = el.closest('.race-row');
    const raceNum = row.dataset.race;
    const field = el.dataset.field;
    const d = dot(el);
    if (d) { d.classList.add('dirty'); d.classList.remove('error', 'saved'); }
    try {
      const out = await post(`/api/sessions/${SESSION}/races/${raceNum}/field`,
                             { field, value });
      if (d) { d.classList.remove('dirty', 'error'); d.classList.add('saved'); }
      applyStats(out.stats);
      return out;
    } catch (err) {
      if (d) { d.classList.remove('dirty', 'saved'); d.classList.add('error'); }
      d && (d.title = String(err.message || err));
      renderSaveState(true);
      return null;
    }
  }

  async function saveSessionField(el, value) {
    try {
      const out = await post(`/api/sessions/${SESSION}/field`,
                             { field: el.dataset.sfield, value });
      el.classList.remove('field-error');
      applyStats(out.stats);
      // expected_races adds or trims trailing blank rows; reload to show them.
      if (el.dataset.sfield === 'expected_races' &&
          out.n_rows !== document.querySelectorAll('.race-row').length) {
        location.reload();
      }
      if (el.dataset.sfield === 'format') location.reload();  // team columns
      return out;
    } catch (err) {
      el.classList.add('field-error');
      el.title = String(err.message || err);
      renderSaveState(true);
      return null;
    }
  }

  /* MMR after is derived rather than stored: before + delta. Keeping the derived
   * field out of the schema prevents three persisted values from disagreeing. */
  const mmrBefore = document.getElementById('mmr-before');
  const mmrDelta = document.getElementById('mmr-delta');
  const mmrAfter = document.getElementById('mmr-after');

  function mmrNumber(el) {
    const raw = el?.value.trim().replaceAll(',', '');
    if (!raw || !/^[+-]?\d+$/.test(raw)) return null;
    return Number(raw);
  }

  function fillMmrAfter() {
    const before = mmrNumber(mmrBefore);
    const delta = mmrNumber(mmrDelta);
    mmrAfter.value = before == null || delta == null ? '' : String(before + delta);
    mmrAfter.classList.remove('field-error');
    mmrAfter.title = '';
  }

  async function deriveMmrDelta() {
    const before = mmrNumber(mmrBefore);
    const after = mmrNumber(mmrAfter);
    if (mmrAfter.value.trim() === '') {
      mmrDelta.value = '';
      await saveSessionField(mmrDelta, '');
      return;
    }
    if (before == null || after == null) {
      mmrAfter.classList.add('field-error');
      mmrAfter.title = before == null
        ? 'Enter MMR before to calculate the change'
        : 'MMR after must be a whole number';
      return;
    }
    mmrAfter.classList.remove('field-error');
    mmrAfter.title = '';
    mmrDelta.value = String(after - before);
    await saveSessionField(mmrDelta, mmrDelta.value);
  }

  root.addEventListener('change', (e) => {
    const el = e.target;
    if (el === mmrAfter) {
      deriveMmrDelta();
    } else if (el.dataset.sfield) {
      const v = el.type === 'checkbox' ? el.checked : el.value;
      saveSessionField(el, v);
      if (el === mmrBefore || el === mmrDelta) fillMmrAfter();
    } else if (el.dataset.field && !el.classList.contains('track-input')) {
      saveRaceField(el, el.value);
    }
  });

  /* -------------------------------------------------------------- typeahead */
  const menus = new WeakMap();   // input -> {items, active, seq}

  function menuEl(input) { return input.parentElement.querySelector('.track-menu'); }

  function closeMenu(input) {
    const m = menuEl(input);
    m.classList.add('hidden');
    m.innerHTML = '';
    menus.set(input, { items: [], active: -1, seq: (menus.get(input)?.seq || 0) });
  }

  function renderMenu(input, data) {
    const m = menuEl(input);
    const state = menus.get(input) || { seq: 0 };
    state.items = data.results;
    state.active = data.results.length ? 0 : -1;
    menus.set(input, state);

    if (!data.results.length) {
      m.innerHTML = `<div class="track-menu-empty">no match — keep typing, or
        <span class="text-ink-400">Esc</span> to clear</div>`;
      m.classList.remove('hidden');
      return;
    }
    m.innerHTML = data.results.map((r, i) => `
      <div class="track-opt ${i === 0 ? 'active' : ''}" data-i="${i}" data-id="${r.id}">
        <span class="track-opt-code">${r.code}</span>
        <span class="track-opt-name">${r.full_name}</span>
        ${r.exact ? '<span class="track-opt-tag">exact</span>' : ''}
        ${r.has_gate ? '<span class="track-opt-gate" title="gate track">cut</span>' : ''}
      </div>`).join('')
      // An unknown string offers "add as alias for..." rather than being rejected.
      + `<div class="track-menu-foot">
           <kbd class="kbd">Alt</kbd>+<kbd class="kbd">↵</kbd>
           add “<span class="text-ink-300">${escapeHtml(input.value.trim())}</span>” as an alias
         </div>`;
    m.classList.remove('hidden');
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function setActive(input, delta) {
    const state = menus.get(input);
    if (!state || !state.items.length) return;
    state.active = (state.active + delta + state.items.length) % state.items.length;
    const m = menuEl(input);
    m.querySelectorAll('.track-opt').forEach((el, i) =>
      el.classList.toggle('active', i === state.active));
    m.querySelectorAll('.track-opt')[state.active]?.scrollIntoView({ block: 'nearest' });
  }

  async function commitTrack(input, match, advance = true) {
    input.value = match.code;
    input.dataset.trackId = match.id;
    input.dataset.committedCode = match.code;
    closeMenu(input);
    const row = input.closest('.race-row');
    // Move on before the round-trip resolves — the next thing typed is almost
    // always the placement, and waiting on the network to hand over focus is what
    // would put a keystroke in the wrong box.
    if (advance) row.querySelector('[data-field="placement"]')?.focus();
    const out = await saveRaceField(input, String(match.id));
    row.querySelector('.track-name').textContent = match.full_name || '';
    // The cut field exists only on gate tracks, so it has to appear (or vanish) at
    // the moment the track is set.
    const cut = row.querySelector('.cut-cell');
    const hasGate = out?.race ? out.race.has_gate : match.has_gate;
    cut.classList.toggle('invisible', !hasGate);
    cut.querySelector('select').disabled = !hasGate;
    return out;
  }

  async function lookup(input) {
    const q = input.value.trim();
    const state = menus.get(input) || { seq: 0 };
    if (!q) { closeMenu(input); return null; }
    const seq = (state.seq || 0) + 1;
    state.seq = seq;
    menus.set(input, state);

    const res = await fetch(`/api/tracks/search?q=${encodeURIComponent(q)}`);
    const data = await res.json();
    // Drop responses that arrived after a later keystroke.
    if ((menus.get(input) || {}).seq !== seq) return null;
    if (input.value.trim() !== q) return null;

    renderMenu(input, data);
    // Auto-commit only on an exact code/alias hit that nothing else shadows.
    // `bc` does not auto-commit because `bci` exists; it highlights and waits.
    if (data.auto_commit && data.results.length) {
      await commitTrack(input, data.results[0]);
      return 'committed';
    }
    return null;
  }

  async function addAliasFromInput(input) {
    const state = menus.get(input);
    const chosen = state?.items?.[state.active];
    const alias = input.value.trim();
    if (!chosen || !alias) return;
    try {
      await post(`/api/tracks/${chosen.id}/aliases`, { alias });
      await commitTrack(input, chosen);
    } catch (err) {
      const m = menuEl(input);
      m.innerHTML = `<div class="track-menu-empty text-bad-400">${escapeHtml(String(err.message || err))}</div>`;
      m.classList.remove('hidden');
    }
  }

  /* -------------------------------------------------------------- keyboard */
  function rows() { return [...document.querySelectorAll('.race-row')]; }

  function focusRow(i) {
    const list = rows();
    if (i < 0 || i >= list.length) {
      // Past the last row: land on the session notes rather than nowhere.
      document.querySelector('[data-sfield="notes"]')?.focus();
      return;
    }
    const input = list[i].querySelector('.track-input');
    input.focus();
    input.select();
  }

  function rowIndex(el) {
    return rows().indexOf(el.closest('.race-row'));
  }

  root.addEventListener('keydown', async (e) => {
    const el = e.target;
    const row = el.closest('.race-row');

    // Alt+I toggles the variant without ever costing a tab stop.
    if (row && e.altKey && (e.key === 'i' || e.key === 'I')) {
      e.preventDefault();
      const sel = row.querySelector('.variant-select');
      sel.value = sel.value === '3lap' ? 'intermission' : '3lap';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
      row.dataset.variant = sel.value;
      return;
    }

    if (!el.classList.contains('track-input')) {
      if (e.key === 'Enter' && row) {
        e.preventDefault();
        el.dispatchEvent(new Event('change', { bubbles: true }));
        focusRow(rowIndex(el) + 1);
      }
      return;
    }

    /* --- track input --- */
    const state = menus.get(el) || { items: [], active: -1 };
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(el, +1); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); setActive(el, -1); return; }
    if (e.key === 'Escape') {
      e.preventDefault();
      closeMenu(el);
      el.value = el.dataset.trackId ? el.value : '';
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      if (e.altKey) { await addAliasFromInput(el); return; }
      const chosen = state.items?.[state.active];
      if (chosen) {
        // Highlighted but not auto-committed — this is the confirmation step for
        // anything ambiguous, `bc` above all.
        commitTrack(el, chosen);
      } else if (!el.value.trim()) {
        // An empty track field is a legitimate row (the doc has bare `12.`).
        if (el.dataset.trackId) {
          el.dataset.trackId = '';
          row.querySelector('.track-name').textContent = '';
          await saveRaceField(el, null);
        }
        focusRow(rowIndex(el) + 1);
      } else if (el.dataset.trackId) {
        // Already committed (an auto-commit, or coming back to an entered row):
        // Enter carries on to the placement rather than doing nothing.
        row.querySelector('[data-field="placement"]')?.focus();
      }
      // Text typed that resolved to nothing: stay put so it can be fixed.
    }
  });

  let debounce;
  root.addEventListener('input', (e) => {
    if (!e.target.classList.contains('track-input')) return;
    clearTimeout(debounce);
    debounce = setTimeout(() => lookup(e.target), 45);
  });

  root.addEventListener('mousedown', (e) => {
    const opt = e.target.closest('.track-opt');
    if (!opt) return;
    e.preventDefault();          // keep focus in the input
    const input = opt.closest('.relative').querySelector('.track-input');
    const state = menus.get(input);
    const chosen = state?.items?.[Number(opt.dataset.i)];
    if (chosen) commitTrack(input, chosen);
  });

  // Free text is never accepted as a track: an unresolved input reverts.
  root.addEventListener('focusout', (e) => {
    const el = e.target;
    if (!el.classList.contains('track-input')) return;
    setTimeout(() => {
      closeMenu(el);
      const row = el.closest('.race-row');
      const name = row.querySelector('.track-name').textContent.trim();
      if (!el.dataset.trackId) { el.value = ''; return; }
      // Reverting to the committed code is what stops a half-typed string sticking.
      const committed = el.dataset.committedCode || '';
      if (name && el.value.trim().toLowerCase() !== committed.toLowerCase()) {
        el.value = committed || el.value;
      }
    }, 120);
  });

  document.querySelectorAll('.track-input').forEach((el) => {
    el.dataset.committedCode = el.value;
    menus.set(el, { items: [], active: -1, seq: 0 });
  });

  /* ------------------------------------------------------------- row edits */
  document.getElementById('add-race')?.addEventListener('click', async () => {
    await post(`/api/sessions/${SESSION}/races`, {});
    location.reload();
  });
  document.getElementById('drop-race')?.addEventListener('click', async () => {
    try {
      await post(`/api/sessions/${SESSION}/races/last`, undefined, 'DELETE');
      location.reload();
    } catch (err) {
      alert(err.message || err);
    }
  });

  // Land on the first empty track field so a fresh session is one keystroke from
  // being logged.
  const first = rows().find((r) => !r.querySelector('.track-input').dataset.trackId);
  if (first && !/Mobi|Android/i.test(navigator.userAgent)) {
    first.querySelector('.track-input').focus();
  }
})();
