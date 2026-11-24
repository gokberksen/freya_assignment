const amount = new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
const tick = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 2 });
const dateFormat = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: "America/New_York" });
const timeFormat = new Intl.DateTimeFormat("tr-TR", { hour: "2-digit", minute: "2-digit", timeZone: "America/New_York" });

export function createChart(canvas, tooltip) {
  const ctx = canvas.getContext("2d");
  let points = [];
  let period = "1D";
  let active = null;
  let layout;

  function draw() {
    const { width, height } = canvas.getBoundingClientRect();
    if (!ctx || !width || !height) return;
    const scale = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    if (points.length < 2) return;

    const prices = points.map(point => point[1]);
    const low = Math.min(...prices);
    const high = Math.max(...prices);
    const pad = Math.max((high - low) * 0.13, high * 0.001);
    const min = low - pad;
    const max = high + pad;
    const labelWidth = Math.max(48, tick.format(max).length * 8 + 9);
    const left = 8;
    const right = width - labelWidth;
    const top = 16;
    const bottom = height - 27;
    const x = index => left + (right - left) * index / (points.length - 1);
    const y = price => bottom - (bottom - top) * (price - min) / (max - min);
    layout = { left, right, width };

    ctx.font = '12px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    ctx.fillStyle = "#655b73";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.lineWidth = 1;
    ctx.strokeStyle = "#ece8f4";
    for (let i = 0; i < 5; i += 1) {
      const value = min + (max - min) * i / 4;
      const at = y(value);
      ctx.beginPath();
      ctx.moveTo(left, at);
      ctx.lineTo(right, at);
      ctx.stroke();
      ctx.fillText(tick.format(value), right + 7, at);
    }

    const ticks = Math.min(width < 350 ? 3 : 4, points.length);
    for (let i = 0; i < ticks; i += 1) {
      const index = Math.round(i * (points.length - 1) / (ticks - 1));
      ctx.textAlign = i === 0 ? "left" : i === ticks - 1 ? "right" : "center";
      ctx.fillText((period === "1D" ? timeFormat : dateFormat).format(points[index][0]), x(index), height - 9);
    }

    function line() {
      ctx.beginPath();
      points.forEach((point, index) => {
        if (index === 0) ctx.moveTo(x(index), y(point[1]));
        else ctx.lineTo(x(index), y(point[1]));
      });
    }

    const gradient = ctx.createLinearGradient(0, top, 0, bottom);
    gradient.addColorStop(0, "#c5b4fd88");
    gradient.addColorStop(1, "#eae1ff16");
    line();
    ctx.lineTo(right, bottom);
    ctx.lineTo(left, bottom);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();
    line();
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.strokeStyle = "#6844d9";
    ctx.stroke();

    const current = active ?? points.length - 1;
    const currentX = x(current);
    const currentY = y(points[current][1]);
    if (active !== null) {
      ctx.beginPath();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = "#927acb";
      ctx.moveTo(currentX, top);
      ctx.lineTo(currentX, bottom);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.beginPath();
    ctx.arc(currentX, currentY, 4, 0, 2 * Math.PI);
    ctx.fillStyle = "#6844d9";
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.stroke();

    if (active !== null) {
      const point = points[active];
      const stamp = (period === "1D" ? timeFormat : dateFormat).format(point[0]);
      const price = amount.format(point[1]);
      const value = document.createElement("strong");
      value.textContent = price + " USD";
      tooltip.replaceChildren(document.createTextNode(stamp), value);
      tooltip.hidden = false;
      tooltip.style.left = Math.max(4, Math.min(width - tooltip.offsetWidth - 4, currentX + 9)) + "px";
      tooltip.style.top = Math.max(3, currentY - 48) + "px";
      canvas.setAttribute("aria-label", stamp + ", " + price + " dolar");
    } else {
      tooltip.hidden = true;
      canvas.setAttribute("aria-label", "Fiyat grafiği. Noktaları incelemek için yön tuşlarını kullanın.");
    }
  }

  function clearActive() {
    if (active === null) return;
    active = null;
    draw();
  }

  canvas.addEventListener("pointermove", event => {
    if (!layout || points.length < 2) return;
    const { left, right } = layout;
    const px = event.clientX - canvas.getBoundingClientRect().left;
    const index = Math.round((px - left) / (right - left) * (points.length - 1));
    const next = Math.max(0, Math.min(points.length - 1, index));
    if (next !== active) {
      active = next;
      draw();
    }
  });
  canvas.addEventListener("pointerleave", clearActive);
  canvas.addEventListener("blur", clearActive);
  canvas.addEventListener("keydown", event => {
    if (points.length < 2 || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const delta = event.key === "ArrowLeft" ? -1 : 1;
    active = Math.max(0, Math.min(points.length - 1, (active ?? points.length - 1) + delta));
    draw();
  });
  new ResizeObserver(draw).observe(canvas);

  return (nextPoints, nextPeriod) => {
    points = nextPoints;
    period = nextPeriod;
    active = null;
    tooltip.hidden = true;
    draw();
  };
}
