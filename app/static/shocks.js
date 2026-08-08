(() => {
  "use strict";

  const raw = document.getElementById("shock-data");
  if (!raw) return;

  const stored = JSON.parse(raw.textContent);
  const cards = [];
  let lapFilter = 0;

  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

  function responseError(response, fallback) {
    return response.json().then(body => {
      throw new Error(body.detail || fallback);
    }).catch(error => {
      if (error instanceof SyntaxError) throw new Error(fallback);
      throw error;
    });
  }

  function filteredEvents(card) {
    return lapFilter ? card.events.filter(event => event.lap === lapFilter) : card.events;
  }

  function draw(card) {
    const rect = card.map.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const pixelWidth = Math.round(rect.width * dpr);
    const pixelHeight = Math.round(rect.height * dpr);
    if (card.canvas.width !== pixelWidth || card.canvas.height !== pixelHeight) {
      card.canvas.width = pixelWidth;
      card.canvas.height = pixelHeight;
    }

    const ctx = card.canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    ctx.globalCompositeOperation = "lighter";

    const radius = clamp(Math.min(rect.width, rect.height) * 0.12, 16, 38);
    for (const event of filteredEvents(card)) {
      const x = event.x * rect.width;
      const y = event.y * rect.height;
      const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
      gradient.addColorStop(0, "rgba(255, 244, 92, 0.78)");
      gradient.addColorStop(0.28, "rgba(251, 146, 60, 0.52)");
      gradient.addColorStop(0.62, "rgba(239, 68, 68, 0.30)");
      gradient.addColorStop(1, "rgba(239, 68, 68, 0)");
      ctx.fillStyle = gradient;
      ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2);
    }

    const shown = filteredEvents(card).length;
    card.count.textContent = lapFilter
      ? `${shown} / ${card.events.length} shocks`
      : `${card.events.length} ${card.events.length === 1 ? "shock" : "shocks"}`;
  }

  function clearPending(card) {
    card.pending = null;
    card.marker.classList.add("hidden");
    card.prompt.classList.add("hidden");
    card.status.classList.remove("hidden", "text-bad-400", "text-good-400");
  }

  function setStatus(card, message, kind = "normal") {
    card.status.textContent = message;
    card.status.classList.remove("text-bad-400", "text-good-400");
    if (kind === "error") card.status.classList.add("text-bad-400");
    if (kind === "saved") card.status.classList.add("text-good-400");
  }

  function choosePoint(card, pointerEvent) {
    const rect = card.map.getBoundingClientRect();
    const hasPointer = pointerEvent.clientX || pointerEvent.clientY;
    const x = hasPointer ? clamp((pointerEvent.clientX - rect.left) / rect.width, 0, 1) : 0.5;
    const y = hasPointer ? clamp((pointerEvent.clientY - rect.top) / rect.height, 0, 1) : 0.5;
    card.pending = {x, y};
    card.marker.style.left = `${x * 100}%`;
    card.marker.style.top = `${y * 100}%`;
    card.marker.classList.remove("hidden");
    card.prompt.classList.remove("hidden");
    card.status.classList.add("hidden");
  }

  async function save(card, lap, button) {
    if (!card.pending) return;
    const point = card.pending;
    button.disabled = true;
    try {
      const response = await fetch("/api/shocks", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({track_id: card.trackId, x: point.x, y: point.y, lap}),
      });
      if (!response.ok) await responseError(response, "Could not save shock");
      const body = await response.json();
      card.events.push(body.event);
      clearPending(card);
      setStatus(card, `Lap ${lap} shock saved`, "saved");
      card.undo.disabled = false;
      draw(card);
    } catch (error) {
      clearPending(card);
      setStatus(card, error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function undo(card) {
    const latest = card.events.reduce((best, event) => !best || event.id > best.id ? event : best, null);
    if (!latest) return;
    card.undo.disabled = true;
    try {
      const response = await fetch(`/api/shocks/${latest.id}`, {method: "DELETE"});
      if (!response.ok) await responseError(response, "Could not remove shock");
      card.events = card.events.filter(event => event.id !== latest.id);
      setStatus(card, `Removed latest shock (lap ${latest.lap})`);
      draw(card);
    } catch (error) {
      setStatus(card, error.message, "error");
    } finally {
      card.undo.disabled = card.events.length === 0;
    }
  }

  document.querySelectorAll(".shock-card").forEach(element => {
    const trackId = Number(element.dataset.trackId);
    const card = {
      element,
      trackId,
      events: stored[String(trackId)] || [],
      map: element.querySelector(".shock-map"),
      canvas: element.querySelector("canvas"),
      target: element.querySelector(".shock-map-target"),
      marker: element.querySelector(".shock-pending-marker"),
      prompt: element.querySelector(".shock-prompt"),
      status: element.querySelector(".shock-status"),
      count: element.querySelector(".shock-count"),
      undo: element.querySelector(".shock-undo"),
      pending: null,
    };
    cards.push(card);

    card.target.addEventListener("click", event => choosePoint(card, event));
    card.prompt.querySelectorAll("[data-save-lap]").forEach(button => {
      button.addEventListener("click", () => save(card, Number(button.dataset.saveLap), button));
    });
    card.prompt.querySelector(".shock-cancel").addEventListener("click", () => {
      clearPending(card);
      setStatus(card, "Click the map to add a shock");
    });
    card.undo.addEventListener("click", () => undo(card));

    new ResizeObserver(() => draw(card)).observe(card.map);
  });

  document.querySelectorAll(".shock-filter").forEach(button => {
    button.addEventListener("click", () => {
      lapFilter = Number(button.dataset.lap);
      document.querySelectorAll(".shock-filter").forEach(candidate => {
        const active = candidate === button;
        candidate.classList.toggle("active", active);
        candidate.setAttribute("aria-pressed", String(active));
      });
      cards.forEach(draw);
    });
  });

  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    for (const card of cards) {
      if (card.pending) {
        clearPending(card);
        setStatus(card, "Click the map to add a shock");
      }
    }
  });
})();
