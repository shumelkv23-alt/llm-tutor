/* Ползунок между уроком и чатом: доля левой панели 45–80 %, по умолчанию 70 %.
 * На странице проекта левая панель — чат, по умолчанию 62 % (data-split-default).
 *
 * Мышь и палец (pointer events), клавиатура (←/→ шаг 2 %, Home/End — пределы),
 * двойной клик — снова 70/30. Чат не уже 280 px. Доля запоминается в
 * localStorage; без хранилища страница просто начинает с 70/30.
 */
(() => {
  const MIN = 45;
  const MAX = 80;
  const STEP = 2;
  const CHAT_MIN = 280;
  const HANDLE = 12;

  const split = document.getElementById("split");
  // Тот же ползунок работает на странице проекта (срез 100): контейнер зовётся #project.
  const lesson = document.getElementById("lesson") || document.getElementById("project");
  const onProject = lesson.id === "project";
  // На странице проекта своя доля: слева чат с наставником (срез 104).
  const KEY = onProject ? "llmTutor.splitProject" : "llmTutor.split";
  const DEFAULT = Number(lesson.dataset.splitDefault) || 70;
  const left = onProject ? "Наставник" : "Урок";
  const right = onProject ? "проект" : "чат";
  const handle = document.getElementById("splitter");
  let value = DEFAULT;
  // Что выбрал ученик: узкое окно ограничивает долю, но выбор не забывается.
  let wanted = DEFAULT;

  function read() {
    try {
      const stored = Number(localStorage.getItem(KEY));
      return Number.isFinite(stored) && stored > 0 ? stored : DEFAULT;
    } catch {
      return DEFAULT;
    }
  }

  function save() {
    try {
      localStorage.setItem(KEY, String(wanted));
    } catch {
      /* без хранилища — просто не запоминаем */
    }
  }

  /** Верхний предел с учётом ширины: чату остаётся не меньше CHAT_MIN. */
  function maxFor(width) {
    if (!width) return MAX;
    return Math.max(MIN, Math.min(MAX, 100 - ((CHAT_MIN + HANDLE) / width) * 100));
  }

  function clamp(next) {
    const max = maxFor(split.getBoundingClientRect().width);
    return Math.round(Math.min(max, Math.max(MIN, next)) * 10) / 10;
  }

  function apply(next, { remember = true } = {}) {
    value = clamp(next);
    if (remember) wanted = value;
    lesson.style.setProperty("--split", `${value}%`);
    const maxRounded = Math.round(maxFor(split.getBoundingClientRect().width));
    const rounded = Math.min(Math.round(value), maxRounded);
    handle.setAttribute("aria-valuenow", String(rounded));
    handle.setAttribute("aria-valuemax", String(maxRounded));
    handle.setAttribute("aria-valuetext", `${left} ${rounded}%, ${right} ${100 - rounded}%`);
  }

  function fromPointer(event) {
    const rect = split.getBoundingClientRect();
    return ((event.clientX - rect.left) / rect.width) * 100;
  }

  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    split.classList.add("dragging");
    handle.focus();
  });

  handle.addEventListener("pointermove", (event) => {
    if (!handle.hasPointerCapture(event.pointerId)) return;
    apply(fromPointer(event));
  });

  function stop(event) {
    if (!handle.hasPointerCapture(event.pointerId)) return;
    handle.releasePointerCapture(event.pointerId);
    split.classList.remove("dragging");
    save();
  }

  handle.addEventListener("pointerup", stop);
  handle.addEventListener("pointercancel", stop);

  handle.addEventListener("keydown", (event) => {
    const moves = {
      ArrowLeft: value - STEP,
      ArrowRight: value + STEP,
      Home: MIN,
      End: MAX,
    };
    if (!(event.key in moves)) return;
    event.preventDefault();
    apply(moves[event.key]);
    save();
  });

  handle.addEventListener("dblclick", () => {
    apply(DEFAULT);
    save();
  });

  window.addEventListener("resize", () => apply(wanted, { remember: false }));

  wanted = Math.min(MAX, Math.max(MIN, read()));
  apply(wanted, { remember: false });
})();
