/* Карта знаний в профиле (срез 75): граф курса в духе Obsidian.
 *
 * Данные — GET /api/courses/{id}/graph. Раскладка силовая: занятия
 * отталкиваются, связи «нужно до» тянут их друг к другу, всё слегка тянется к
 * центру. Рисуем на <canvas>; для клавиатуры и диктора тот же выбор — списком
 * под картой. Нажатие открывает карточку занятия с переходом к нему — тем же
 * POST /lesson/switch, что и «Маршрут».
 */
(() => {
  const { $, request, confirm, notify } = window.App;
  const root = $("#knowledge");
  if (!root) return;
  const course = root.dataset.course;
  const lessonHref = root.dataset.href;
  const canvas = $("#kg-canvas");
  const stage = $("#kg-stage");
  const tip = $("#kg-tip");
  const statusLine = $("#kg-status");
  const card = $("#kg-card");
  const list = $("#kg-list");
  const hud = root.querySelector(".kg-hud");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const STATUS = {
    current: "сейчас",
    closed: "закрыто",
    claimed: "знакомо по анкете — проверим",
    available: "доступно",
    ahead: "впереди",
  };
  const MODULE_COLORS = 6;

  let data = null;
  let nodes = [];
  let links = [];
  let byId = {};
  let neighbours = {};
  let palette = {};
  const view = { x: 0, y: 0, s: 1 };
  let hover = null;
  let selected = null;
  let alpha = 1;
  let animating = false;

  const el = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };

  function readPalette() {
    const css = getComputedStyle(document.documentElement);
    const get = (name) => css.getPropertyValue(name).trim();
    palette = {
      canvas: get("--graph-canvas"),
      link: get("--graph-link"),
      linkHl: get("--graph-link-hl"),
      label: get("--text"),
      accent: get("--accent"),
      font: get("--font-text") || "sans-serif",
      modules: Array.from({ length: MODULE_COLORS }, (_, i) => get(`--graph-m${i + 1}`)),
    };
  }

  const colorOf = (node) => palette.modules[(node.module - 1) % MODULE_COLORS];
  const radius = (node) => 4 + Math.sqrt(node.degree) * 2.3;
  const statusText = (node) => (node.weak ? "закрыто слабо" : STATUS[node.status] || node.status);
  const graphPath = `/api/courses/${encodeURIComponent(course)}/graph`;
  const names = (ids) => ids.map((id) => byId[id].name);
  const moduleTitle = (number) => data.modules.find((m) => m.number === number)?.title || "";

  function build(payload) {
    data = payload;
    byId = {};
    neighbours = {};
    const count = data.modules.length || 1;
    nodes = data.nodes.map((node, i) => {
      // Старт — по кругу модулями: силы быстрее собирают модули в скопления.
      const angle = ((node.module - 1) / count) * Math.PI * 2 + i * 0.35;
      const item = { ...node, x: Math.cos(angle) * 170, y: Math.sin(angle) * 170, vx: 0, vy: 0, degree: 0, fixed: false };
      byId[item.id] = item;
      neighbours[item.id] = new Set();
      return item;
    });
    links = data.edges
      .filter((edge) => byId[edge.from] && byId[edge.to])
      .map((edge) => ({ s: byId[edge.from], t: byId[edge.to], hard: edge.hard }));
    links.forEach((link) => {
      link.s.degree += 1;
      link.t.degree += 1;
      neighbours[link.s.id].add(link.t.id);
      neighbours[link.t.id].add(link.s.id);
    });
  }

  // --- физика ---

  function tick() {
    const k = alpha;
    for (let i = 0; i < nodes.length; i += 1) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j += 1) {
        const b = nodes[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        const d2 = dx * dx + dy * dy || 0.01;
        const d = Math.sqrt(d2);
        const force = (1100 / d2) * k;
        dx /= d;
        dy /= d;
        a.vx -= dx * force;
        a.vy -= dy * force;
        b.vx += dx * force;
        b.vy += dy * force;
      }
    }
    for (const link of links) {
      const dx = link.t.x - link.s.x;
      const dy = link.t.y - link.s.y;
      const d = Math.hypot(dx, dy) || 0.01;
      const target = link.hard ? 70 : 105;
      const force = ((d - target) / d) * (link.hard ? 0.06 : 0.025) * k;
      link.s.vx += dx * force;
      link.s.vy += dy * force;
      link.t.vx -= dx * force;
      link.t.vy -= dy * force;
    }
    for (const node of nodes) {
      node.vx -= node.x * 0.004 * k;
      node.vy -= node.y * 0.004 * k;
      if (node.fixed) {
        node.vx = 0;
        node.vy = 0;
        continue;
      }
      node.vx *= 0.82;
      node.vy *= 0.82;
      node.x += node.vx;
      node.y += node.vy;
    }
    alpha = Math.max(alpha * 0.985, 0.002);
  }

  // --- отрисовка ---

  function draw() {
    const ratio = window.devicePixelRatio || 1;
    const { width, height } = canvas.getBoundingClientRect();
    if (!width || !height) return;
    // Буфер холста — только при смене размера: иначе он пересоздавался бы
    // на каждом кадре и движении мыши (аудит 74–75).
    const w = Math.round(width * ratio);
    const h = Math.round(height * ratio);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.fillStyle = palette.canvas;
    ctx.fillRect(0, 0, width, height);
    ctx.save();
    ctx.translate(width / 2 + view.x, height / 2 + view.y);
    ctx.scale(view.s, view.s);

    const focus = hover || selected;
    const lit = focus ? new Set([focus.id, ...neighbours[focus.id]]) : null;
    for (const link of links) {
      const on = lit && (link.s === focus || link.t === focus);
      ctx.globalAlpha = lit && !on ? 0.15 : 1;
      ctx.strokeStyle = on ? palette.linkHl : palette.link;
      ctx.lineWidth = (on ? 1.8 : link.hard ? 1.2 : 0.8) / view.s;
      ctx.setLineDash(link.hard ? [] : [3 / view.s, 3 / view.s]);
      ctx.beginPath();
      ctx.moveTo(link.s.x, link.s.y);
      ctx.lineTo(link.t.x, link.t.y);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    for (const node of nodes) {
      const color = colorOf(node);
      const r = radius(node);
      const faded = lit && !lit.has(node.id);
      // «Впереди» отличается тонким пунктиром, а не бледностью: при 0.45
      // контур не дотягивал до 3:1 к фону (WCAG 1.4.11, аудит 74–75).
      const base = 1;
      ctx.globalAlpha = faded ? 0.18 : base;
      if (node.status === "current") {
        ctx.beginPath();
        ctx.arc(node.x, node.y, r + 9, 0, Math.PI * 2);
        ctx.fillStyle = palette.accent;
        ctx.globalAlpha = (faded ? 0.18 : 1) * 0.22;
        ctx.fill();
        ctx.globalAlpha = faded ? 0.18 : 1;
      }
      ctx.beginPath();
      ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
      if (node.status === "closed" && !node.weak) {
        ctx.fillStyle = color;
        ctx.fill();
      } else if (node.status === "current") {
        ctx.fillStyle = color;
        ctx.fill();
        ctx.strokeStyle = palette.accent;
        ctx.lineWidth = 2.5;
        ctx.stroke();
      } else {
        ctx.fillStyle = palette.canvas;
        ctx.fill();
        ctx.strokeStyle = color;
        ctx.lineWidth = node.weak ? 2 : node.status === "ahead" ? 1.3 : 1.8;
        if (node.weak) ctx.setLineDash([2.5, 2]);
        else if (node.status === "ahead") ctx.setLineDash([1.5, 2.5]);
        ctx.stroke();
        ctx.setLineDash([]);
        if (node.status === "claimed") {
          ctx.beginPath();
          ctx.arc(node.x, node.y, Math.max(2, r / 3), 0, Math.PI * 2);
          ctx.fillStyle = color;
          ctx.fill();
        }
      }
      if (node === selected) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, r + 4, 0, Math.PI * 2);
        ctx.strokeStyle = palette.linkHl;
        ctx.lineWidth = 2 / view.s;
        ctx.stroke();
      }
      const label = node.degree >= 7 || view.s > 1.8 || node.status === "current" || (lit && lit.has(node.id));
      if (label) {
        ctx.globalAlpha = faded ? 0.15 : 1;
        ctx.fillStyle = palette.label;
        const size = 11.5 / Math.max(view.s, 0.8);
        ctx.font = `${node === focus || node.status === "current" ? 700 : 500} ${size}px ${palette.font}`;
        ctx.textAlign = "center";
        ctx.fillText(node.name, node.x, node.y + r + 13 / view.s);
      }
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  function frame() {
    tick();
    draw();
    if (alpha > 0.004 || dragged) {
      requestAnimationFrame(frame);
    } else {
      animating = false;
    }
  }

  function kick(value = 0.4) {
    alpha = Math.max(alpha, value);
    if (reduced) {
      for (let i = 0; i < 300; i += 1) tick();
      draw();
      return;
    }
    if (!animating) {
      animating = true;
      requestAnimationFrame(frame);
    }
  }

  function fit() {
    const { width, height } = canvas.getBoundingClientRect();
    if (!nodes.length || !width) return;
    const xs = nodes.map((n) => n.x);
    const ys = nodes.map((n) => n.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    view.s = Math.min(width / (maxX - minX + 110), height / (maxY - minY + 110), 2);
    view.x = -((minX + maxX) / 2) * view.s;
    view.y = -((minY + maxY) / 2) * view.s;
    draw();
  }

  function centerOn(node, scale = 1.3) {
    view.s = scale;
    view.x = -node.x * view.s;
    view.y = -node.y * view.s;
    draw();
  }

  // --- карточка занятия ---

  /** Обязательные (жёсткие) пререквизиты, ещё не пройденные: тем же правилом
   * сервер считает статус «доступно», и карточка ему не противоречит. */
  function missingBefore(id) {
    const seen = new Set();
    const stack = [id];
    while (stack.length) {
      const current = stack.pop();
      for (const link of links) {
        if (link.hard && link.t.id === current && !seen.has(link.s.id)) {
          seen.add(link.s.id);
          stack.push(link.s.id);
        }
      }
    }
    return [...seen].filter((x) => !["closed", "claimed"].includes(byId[x].status));
  }

  let going = false;

  async function go(node, button) {
    // Второе нажатие, пока идёт первое, не шлёт второй переход (аудит 74–75).
    if (going) return;
    going = true;
    button.disabled = true;
    try {
      // Задание могли взять в другой вкладке — спрашиваем по свежему состоянию.
      const fresh = await request(graphPath);
      if (fresh.pending_item) {
        const ok = await confirm("Перейти к другому занятию?", "Задание, которое ждёт ответа, будет снято.", { label: "Перейти" });
        if (!ok) return;
      }
      await request(`/api/courses/${encodeURIComponent(course)}/lesson/switch`, { method: "POST", body: { node_id: node.id } });
      window.location.assign(lessonHref);
      return;
    } catch (problem) {
      notify(problem.message, { error: true });
    } finally {
      going = false;
      button.disabled = false;
    }
  }

  function renderCard() {
    list.querySelectorAll("button[data-node]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.node === selected?.id)));
    if (!selected) {
      card.hidden = true;
      return;
    }
    const node = selected;
    card.replaceChildren();
    card.append(el("h3", "", node.name));
    card.append(el("p", "kg-meta", `Модуль ${node.module} · ${moduleTitle(node.module)}`));
    const facts = [`Статус: ${statusText(node)}`, `владение ${node.mastery}%${node.confident ? "" : " (мало данных)"}`];
    const missing = ["closed", "claimed", "current", "available"].includes(node.status) ? [] : missingBefore(node.id);
    facts.push(missing.length ? `обязательных занятий до него не пройдено: ${missing.length}` : "можно проходить");
    card.append(el("p", "kg-meta", facts.join(" · ")));
    const before = links.filter((l) => l.t === node).map((l) => l.s.id);
    const after = links.filter((l) => l.s === node).map((l) => l.t.id);
    if (before.length) card.append(el("p", "kg-meta", `Нужно до: ${names(before).join(", ")}`));
    if (after.length) card.append(el("p", "kg-meta", `Открывает: ${names(after).join(", ")}`));
    const actions = el("div", "kg-actions");
    if (node.status === "current") {
      const link = el("a", "btn primary", "Продолжить занятие");
      link.href = lessonHref;
      actions.append(link);
    } else if (data.survey_done && data.pending_survey !== null && data.pending_survey !== undefined && node.needs_survey) {
      // Сервер такой переход отклонит (409): сначала висящая анкета.
      actions.append(el("p", "kg-meta", `Сначала анкета модуля ${data.pending_survey}: она открыта на странице занятия.`));
      const link = el("a", "btn primary", "К анкете");
      link.href = lessonHref;
      actions.append(link);
    } else if (data.survey_done) {
      const button = el("button", "btn primary", "Перейти к занятию");
      button.type = "button";
      button.addEventListener("click", () => go(node, button));
      actions.append(button);
    } else {
      actions.append(el("p", "kg-meta", "Перейти к занятию можно после анкеты курса."));
      const link = el("a", "btn", "К анкете");
      link.href = lessonHref;
      actions.append(link);
    }
    const close = el("button", "btn ghost", "Закрыть");
    close.type = "button";
    close.addEventListener("click", () => select(null));
    actions.append(close);
    card.append(actions);
    card.hidden = false;
  }

  function select(node, { announce = false } = {}) {
    selected = node;
    renderCard();
    draw();
    if (node && announce) {
      // С клавиатуры и в диктор: фокус на заголовок карточки, она в зоне видимости.
      const heading = card.querySelector("h3");
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
    if (node) card.scrollIntoView({ block: "nearest", behavior: reduced ? "auto" : "smooth" });
  }

  function renderList() {
    list.replaceChildren();
    for (const module of data.modules) {
      list.append(el("h4", "", `Модуль ${module.number}. ${module.title}`));
      const items = el("ul");
      for (const node of nodes.filter((n) => n.module === module.number)) {
        const button = el("button", "", node.name);
        button.type = "button";
        button.dataset.node = node.id;
        button.setAttribute("aria-pressed", "false");
        button.setAttribute("aria-label", `${node.name}: ${statusText(node)}`);
        button.addEventListener("click", () => {
          centerOn(node);
          select(node, { announce: true });
        });
        const li = el("li");
        li.append(button);
        items.append(li);
      }
      list.append(items);
    }
  }

  // --- мышь, касания, колесо ---

  let dragged = null;
  let pan = null;
  let moved = false;
  let downAt = null;
  // Сдвиг меньше порога — ещё нажатие, а не перетаскивание: палец дрожит.
  const DRAG_THRESHOLD = 5;
  const pointers = new Map();
  let pinch = null;

  function toWorld(event) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left - rect.width / 2 - view.x) / view.s,
      y: (event.clientY - rect.top - rect.height / 2 - view.y) / view.s,
      sx: event.clientX - rect.left,
      sy: event.clientY - rect.top,
      width: rect.width,
    };
  }
  const pick = (p) => nodes.find((node) => Math.hypot(node.x - p.x, node.y - p.y) < radius(node) + 6 / view.s);

  function showTip(node, p) {
    if (!node) {
      tip.hidden = true;
      return;
    }
    tip.replaceChildren(el("b", "", node.name), el("span", "", `Модуль ${node.module} · ${statusText(node)}`));
    tip.hidden = false;
    tip.style.left = `${Math.max(8, Math.min(p.sx + 14, p.width - 250))}px`;
    tip.style.top = `${Math.max(p.sy - 52, 8)}px`;
  }

  canvas.addEventListener("pointerdown", (event) => {
    if (!data) return;
    canvas.setPointerCapture(event.pointerId);
    pointers.set(event.pointerId, event);
    moved = false;
    downAt = { x: event.clientX, y: event.clientY };
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinch = { distance: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY), scale: view.s };
      if (dragged) dragged.fixed = false; // иначе точка навсегда выпала бы из физики
      dragged = null;
      pan = null;
      return;
    }
    const p = toWorld(event);
    const node = pick(p);
    if (node) {
      dragged = node;
      node.fixed = true;
    } else {
      pan = { x: event.clientX - view.x, y: event.clientY - view.y };
    }
    canvas.classList.add("dragging");
  });

  canvas.addEventListener("pointermove", (event) => {
    if (!data) return;
    if (pointers.has(event.pointerId)) pointers.set(event.pointerId, event);
    if (pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const distance = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      view.s = Math.min(4, Math.max(0.3, (pinch.scale * distance) / pinch.distance));
      draw();
      return;
    }
    const p = toWorld(event);
    if (downAt && !moved && Math.hypot(event.clientX - downAt.x, event.clientY - downAt.y) > DRAG_THRESHOLD) moved = true;
    if (dragged && moved) {
      dragged.x = p.x;
      dragged.y = p.y;
      kick(0.3);
    } else if (pan && moved) {
      view.x = event.clientX - pan.x;
      view.y = event.clientY - pan.y;
      draw();
    }
    const node = dragged || pick(p);
    if (node !== hover) {
      hover = node || null;
      draw();
    }
    showTip(hover, p);
  });

  function release(event) {
    pointers.delete(event.pointerId);
    if (pointers.size < 2) pinch = null;
    if (dragged) {
      dragged.fixed = false;
      if (!moved) select(dragged);
    } else if (pan && !moved) {
      select(null);
    }
    dragged = null;
    pan = null;
    downAt = null;
    canvas.classList.remove("dragging");
  }
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);
  canvas.addEventListener("pointerleave", () => {
    if (dragged) return;
    hover = null;
    tip.hidden = true;
    draw();
  });
  canvas.addEventListener(
    "wheel",
    (event) => {
      if (!data) return;
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mx = event.clientX - rect.left - rect.width / 2;
      const my = event.clientY - rect.top - rect.height / 2;
      const next = Math.min(4, Math.max(0.3, view.s * Math.exp(-event.deltaY * 0.0015)));
      view.x = mx - ((mx - view.x) * next) / view.s;
      view.y = my - ((my - view.y) * next) / view.s;
      view.s = next;
      draw();
    },
    { passive: false }
  );

  $("#kg-fit").addEventListener("click", fit);
  $("#kg-current").addEventListener("click", () => {
    const node = data && byId[data.current];
    if (node) {
      select(node);
      centerOn(node);
    } else if (data && data.pending_survey) {
      notify(`Сейчас идёт анкета модуля ${data.pending_survey}: она на странице занятия.`);
    } else if (data && data.survey_done) {
      notify("Текущего занятия нет: выберите любое на карте.");
    } else {
      notify("Текущего занятия пока нет: курс начнётся после анкеты.");
    }
  });

  // --- тема и размер ---

  const repaint = () => {
    readPalette();
    draw();
  };
  new MutationObserver(repaint).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", repaint);
  window.addEventListener("resize", () => draw());

  // --- загрузка ---

  async function load() {
    try {
      const payload = await request(graphPath);
      if (!payload.nodes.length) {
        statusLine.textContent = "В курсе пока нет открытых занятий.";
        hud.hidden = true;
        return;
      }
      readPalette();
      build(payload);
      statusLine.hidden = true;
      renderList();
      // Раскладка до первого кадра: граф появляется уже собранным.
      for (let i = 0; i < 220; i += 1) tick();
      fit();
      root.dataset.ready = "true";
      if (!reduced) kick(0.15);
    } catch (problem) {
      statusLine.textContent = problem.message;
      hud.hidden = true;
    }
  }
  load();
})();
