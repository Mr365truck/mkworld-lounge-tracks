(() => {
  "use strict";

  function initTrackSorting() {
    const table = document.getElementById("track-table");
    if (!table || !table.tBodies.length) return;

    const headers = Array.from(table.tHead.rows[0].cells);
    const body = table.tBodies[0];

    headers.forEach((header, column) => {
      const button = header.querySelector("button[data-sort-type]");
      if (!button) return;

      button.addEventListener("click", () => {
        const type = button.dataset.sortType;
        const current = header.getAttribute("aria-sort");
        const direction = current === "ascending"
          ? "descending"
          : current === "descending"
            ? "ascending"
            : type === "text" ? "ascending" : "descending";
        const sign = direction === "ascending" ? 1 : -1;

        const rows = Array.from(body.rows).map((row, originalIndex) => ({ row, originalIndex }));
        rows.sort((a, b) => {
          const av = a.row.cells[column]?.dataset.sortValue ?? "";
          const bv = b.row.cells[column]?.dataset.sortValue ?? "";
          const aMissing = av === "" || (type === "number" && !Number.isFinite(Number(av)));
          const bMissing = bv === "" || (type === "number" && !Number.isFinite(Number(bv)));
          if (aMissing !== bMissing) return aMissing ? 1 : -1;
          if (aMissing && bMissing) return a.originalIndex - b.originalIndex;

          let comparison;
          if (type === "number") {
            comparison = Number(av) - Number(bv);
          } else {
            comparison = av.localeCompare(bv, undefined, { numeric: true, sensitivity: "base" });
          }
          return comparison === 0 ? a.originalIndex - b.originalIndex : comparison * sign;
        });

        rows.forEach(({ row }) => body.appendChild(row));
        headers.forEach((other) => {
          other.setAttribute("aria-sort", "none");
          const indicator = other.querySelector("[data-sort-indicator]");
          if (indicator) indicator.textContent = "";
        });
        header.setAttribute("aria-sort", direction);
        const indicator = header.querySelector("[data-sort-indicator]");
        if (indicator) indicator.textContent = direction === "ascending" ? "▲" : "▼";
      });
    });
  }

  function initScoreChart() {
    const canvas = document.getElementById("score-chart");
    const source = document.getElementById("score-chart-data");
    const readout = document.getElementById("score-chart-readout");
    if (!canvas || !source || !readout) return;

    const points = Array.from(source.children).map((element) => ({
      sessionId: Number(element.dataset.sessionId),
      label: element.dataset.label,
      raw: Number(element.dataset.score),
      weighted: element.dataset.adjusted === "" ? null : Number(element.dataset.adjusted),
      roomAvg: element.dataset.roomAvg === "" ? null : Number(element.dataset.roomAvg),
    }));
    const context = canvas.getContext("2d");
    const controls = Array.from(document.querySelectorAll("[data-score-mode]"));
    let mode = "raw";
    let selectedIndex = Math.max(0, points.length - 1);
    let geometry = [];

    function valueFor(point) {
      return mode === "raw" ? point.raw : point.weighted;
    }

    function updateReadout(point) {
      readout.replaceChildren();
      if (!point || valueFor(point) === null) {
        readout.textContent = "No room-weighted scores are available. Add room average MMR to a scored session.";
        return;
      }
      const value = valueFor(point);
      const label = mode === "raw" ? "points" : "room-weighted points";
      readout.append(`${point.label}: ${value.toFixed(mode === "raw" ? 0 : 1)} ${label}`);
      if (point.roomAvg !== null) readout.append(` · room avg ${point.roomAvg.toLocaleString()}`);
      readout.append(" · ");
      const link = document.createElement("a");
      link.href = `/sessions/${point.sessionId}`;
      link.className = "text-accent-400 underline underline-offset-2";
      link.textContent = "Open session";
      readout.append(link);
    }

    function draw() {
      const rect = canvas.getBoundingClientRect();
      const width = Math.max(320, rect.width);
      const height = 300;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);

      const margin = { top: 18, right: 18, bottom: 42, left: 48 };
      const plotWidth = width - margin.left - margin.right;
      const plotHeight = height - margin.top - margin.bottom;
      const values = points.map(valueFor).filter((value) => value !== null && Number.isFinite(value));
      geometry = [];

      if (!values.length) {
        context.fillStyle = "#55616f";
        context.font = "12px system-ui, sans-serif";
        context.textAlign = "center";
        context.fillText("No sessions in this view have both score and room average MMR.", width / 2, height / 2);
        updateReadout(null);
        return;
      }

      const maximum = Math.max(...values);
      const yMax = Math.max(10, Math.ceil(maximum * 1.1 / 10) * 10);
      const xAt = (index) => points.length === 1
        ? margin.left + plotWidth / 2
        : margin.left + index * plotWidth / (points.length - 1);
      const yAt = (value) => margin.top + plotHeight - value / yMax * plotHeight;

      context.font = "11px system-ui, sans-serif";
      context.lineWidth = 1;
      context.textAlign = "right";
      context.textBaseline = "middle";
      for (let step = 0; step <= 4; step += 1) {
        const value = yMax * step / 4;
        const y = yAt(value);
        context.strokeStyle = "#1f262e";
        context.beginPath();
        context.moveTo(margin.left, y);
        context.lineTo(width - margin.right, y);
        context.stroke();
        context.fillStyle = "#55616f";
        context.fillText(Math.round(value).toString(), margin.left - 8, y);
      }

      context.strokeStyle = mode === "raw" ? "#38bdf8" : "#fbbf24";
      context.lineWidth = 2;
      context.beginPath();
      let segmentOpen = false;
      points.forEach((point, index) => {
        const value = valueFor(point);
        if (value === null || !Number.isFinite(value)) {
          segmentOpen = false;
          return;
        }
        const x = xAt(index);
        const y = yAt(value);
        if (!segmentOpen) context.moveTo(x, y);
        else context.lineTo(x, y);
        segmentOpen = true;
        geometry.push({ point, index, x, y });
      });
      context.stroke();

      geometry.forEach(({ index, x, y }) => {
        const selected = index === selectedIndex;
        context.beginPath();
        context.fillStyle = selected ? "#e3e8ee" : mode === "raw" ? "#38bdf8" : "#fbbf24";
        context.arc(x, y, selected ? 5 : 3.5, 0, Math.PI * 2);
        context.fill();
      });

      const labelIndexes = points.length <= 6
        ? points.map((_, index) => index)
        : [0, Math.floor((points.length - 1) / 2), points.length - 1];
      context.fillStyle = "#55616f";
      context.textAlign = "center";
      context.textBaseline = "top";
      labelIndexes.forEach((index) => {
        const shortLabel = points[index].label.split(",")[0];
        context.fillText(shortLabel, xAt(index), height - margin.bottom + 12);
      });

      const selected = points[selectedIndex];
      if (selected && valueFor(selected) !== null) updateReadout(selected);
      else if (geometry.length) {
        selectedIndex = geometry[geometry.length - 1].index;
        updateReadout(points[selectedIndex]);
      }
      canvas.setAttribute(
        "aria-label",
        `${mode === "raw" ? "Raw" : "Room-weighted"} session score over time, ${geometry.length} points`,
      );
    }

    function selectNearest(clientX) {
      if (!geometry.length) return;
      const x = clientX - canvas.getBoundingClientRect().left;
      const nearest = geometry.reduce((best, item) =>
        Math.abs(item.x - x) < Math.abs(best.x - x) ? item : best,
      );
      selectedIndex = nearest.index;
      draw();
    }

    canvas.addEventListener("pointermove", (event) => selectNearest(event.clientX));
    canvas.addEventListener("pointerdown", (event) => selectNearest(event.clientX));
    controls.forEach((button) => {
      button.addEventListener("click", () => {
        mode = button.dataset.scoreMode;
        controls.forEach((control) => {
          const active = control === button;
          control.setAttribute("aria-pressed", active ? "true" : "false");
          control.classList.toggle("bg-ink-800", active);
          control.classList.toggle("text-ink-100", active);
        });
        draw();
      });
    });

    if (window.ResizeObserver) new ResizeObserver(draw).observe(canvas);
    else window.addEventListener("resize", draw);
    draw();
  }

  initTrackSorting();
  initScoreChart();
})();
