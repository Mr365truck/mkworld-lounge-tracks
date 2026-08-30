(() => {
  'use strict';

  const input = document.getElementById('player-search');
  const menu = document.getElementById('player-results');
  const status = document.getElementById('search-status');
  const spinner = document.getElementById('search-spinner');
  const refreshButton = document.getElementById('refresh-names');
  if (!input || !menu) return;
  const gameLabel = input.dataset.gameLabel || 'MKWorld 12P';

  let results = [];
  let active = -1;
  let sequence = 0;
  let debounce;

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);
  }

  function closeMenu() {
    results = [];
    active = -1;
    menu.innerHTML = '';
    menu.classList.add('hidden');
    input.setAttribute('aria-expanded', 'false');
  }

  function setActive(next) {
    if (!results.length) return;
    active = (next + results.length) % results.length;
    menu.querySelectorAll('[role="option"]').forEach((row, index) => {
      row.classList.toggle('bg-accent-500/15', index === active);
      row.setAttribute('aria-selected', index === active ? 'true' : 'false');
    });
    menu.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' });
  }

  function render(data) {
    results = data.results || [];
    active = results.length ? 0 : -1;
    if (!results.length) {
      menu.innerHTML = '<div class="px-3 py-3 text-sm text-ink-500">No matching Lounge players.</div>';
    } else {
      menu.innerHTML = results.map((player, index) => `
        <button type="button" role="option" aria-selected="${index === 0}"
                data-index="${index}" ${player.listed ? 'disabled' : ''}
                class="flex w-full items-center gap-3 border-b border-ink-800 px-3 py-2 text-left last:border-0 ${index === 0 ? 'bg-accent-500/15' : ''} ${player.listed ? 'cursor-default opacity-50' : 'hover:bg-ink-800'}">
          <span class="w-8 shrink-0 text-right text-xs tabular-nums text-ink-600">${player.rank == null ? '—' : `#${player.rank}`}</span>
          <span class="min-w-0 flex-1 truncate text-sm text-ink-100">${escapeHtml(player.name)}</span>
          <span class="shrink-0 font-mono text-[11px] text-ink-500">${escapeHtml(player.country_code || '—')}</span>
          <span class="w-14 shrink-0 text-right text-xs tabular-nums text-ink-400">${player.mmr == null ? '—' : player.mmr}</span>
          <span class="w-12 shrink-0 text-right text-xs ${player.listed ? 'text-ink-500' : 'text-accent-300'}">${player.listed ? 'Added' : 'Add'}</span>
        </button>`).join('');
    }
    menu.classList.remove('hidden');
    input.setAttribute('aria-expanded', 'true');
    status.textContent = data.total > results.length
      ? `Showing ${results.length} of ${data.total} matches · Lounge season ${data.season}`
      : `${data.total} ${data.total === 1 ? 'match' : 'matches'} · Lounge season ${data.season}`;
  }

  async function lookup() {
    const query = input.value.trim();
    const requestSequence = ++sequence;
    if (!query) {
      closeMenu();
      status.textContent = `Searches the current ${gameLabel} Lounge leaderboard.`;
      spinner.textContent = '';
      return;
    }
    spinner.textContent = '…';
    try {
      const response = await fetch(`/api/do-not-mogi/search?q=${encodeURIComponent(query)}`);
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || 'Could not search Lounge');
      if (requestSequence !== sequence || input.value.trim() !== query) return;
      render(body);
    } catch (error) {
      if (requestSequence !== sequence) return;
      closeMenu();
      status.textContent = error.message || 'Could not search Lounge';
      status.classList.add('text-bad-400');
    } finally {
      if (requestSequence === sequence) spinner.textContent = '';
    }
  }

  async function addPlayer(index) {
    const player = results[index];
    if (!player || player.listed) return;
    const row = menu.querySelector(`[data-index="${index}"]`);
    if (row) row.disabled = true;
    status.classList.remove('text-bad-400');
    status.textContent = `Adding ${player.name}…`;
    try {
      const response = await fetch('/api/do-not-mogi', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lounge_player_id: player.lounge_player_id }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || 'Could not add player');
      location.reload();
    } catch (error) {
      if (row) row.disabled = false;
      status.textContent = error.message || 'Could not add player';
      status.classList.add('text-bad-400');
    }
  }

  input.addEventListener('input', () => {
    clearTimeout(debounce);
    status.classList.remove('text-bad-400');
    debounce = setTimeout(lookup, 250);
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault(); setActive(active + 1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault(); setActive(active - 1);
    } else if (event.key === 'Enter') {
      event.preventDefault(); addPlayer(active);
    } else if (event.key === 'Escape') {
      closeMenu();
    }
  });
  menu.addEventListener('click', (event) => {
    const row = event.target.closest('[data-index]');
    if (row) addPlayer(Number(row.dataset.index));
  });
  document.addEventListener('click', (event) => {
    if (!menu.contains(event.target) && event.target !== input) closeMenu();
  });

  document.addEventListener('click', async (event) => {
    const button = event.target.closest('.remove-player');
    if (!button) return;
    const row = button.closest('[data-player-id]');
    const name = row.dataset.playerName;
    if (!confirm(`Remove ${name} from the Do Not Mogi list?`)) return;
    button.disabled = true;
    const response = await fetch(`/api/do-not-mogi/${row.dataset.playerId}`, {
      method: 'DELETE',
    });
    if (response.ok) location.reload();
    else button.disabled = false;
  });

  refreshButton?.addEventListener('click', async () => {
    refreshButton.disabled = true;
    refreshButton.textContent = 'Refreshing…';
    try {
      const response = await fetch('/api/do-not-mogi/refresh', { method: 'POST' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || 'Could not refresh names');
      location.reload();
    } catch (error) {
      status.textContent = error.message || 'Could not refresh names';
      status.classList.add('text-bad-400');
      refreshButton.disabled = false;
      refreshButton.textContent = 'Refresh names';
    }
  });
})();
