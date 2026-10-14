/* Вкладка «Теория»: конспект текущего занятия (готовый HTML с сервера).
 *
 * У занятия с разделами (срез 55) — оглавление с ✓/⚠️ и прокрутка к текущему
 * разделу, когда он сменился.
 */
(() => {
  const panel = document.getElementById("tab-theory");
  // seaborn и plotly в Pyodide нет: такой пример в редакторе не запустится.
  const NOT_IN_PYODIDE = /^\s*(import|from)\s+(seaborn|plotly)\b/m;
  const TOC_MARKS = { passed: "✓", weak: "⚠️", current: "▶", ahead: "·" };
  // Последний раздел, к которому прокручивали: прокрутка — только на смене.
  let scrolledTo = null;

  /** Блок кода: кнопки «Скопировать» и (если есть редактор) «Открыть в редакторе». */
  function decorateCode(pre) {
    const wrap = document.createElement("div");
    wrap.className = "code-block";
    pre.replaceWith(wrap);
    wrap.append(pre);
    const tools = document.createElement("div");
    tools.className = "code-tools";
    const code = pre.textContent;
    const copy = document.createElement("button");
    copy.type = "button";
    copy.textContent = "Скопировать";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(code);
        copy.textContent = "Скопировано";
      } catch {
        copy.textContent = "Не удалось";
      }
      setTimeout(() => {
        copy.textContent = "Скопировать";
      }, 1500);
    });
    tools.append(copy);
    if (window.Lesson.code && !NOT_IN_PYODIDE.test(code)) {
      const open = document.createElement("button");
      open.type = "button";
      open.textContent = "Открыть в редакторе";
      open.addEventListener("click", () => window.Lesson.code.open(code));
      tools.append(open);
    }
    wrap.append(tools);
  }

  function render(state) {
    panel.replaceChildren();
    if (state.survey) {
      const empty = document.createElement("div");
      empty.className = "empty";
      const title = document.createElement("strong");
      title.textContent = "Сначала короткая анкета";
      empty.append(title, "Ответьте на пару вопросов — и тьютор подберёт, с чего начать.");
      panel.append(empty);
      return;
    }
    if (!state.node) {
      const empty = document.createElement("div");
      empty.className = "empty";
      const title = document.createElement("strong");
      title.textContent = "Занятие не выбрано";
      empty.append(title, "Откройте «Маршрут» и выберите, с чего продолжить.");
      panel.append(empty);
      return;
    }
    const article = document.createElement("article");
    article.className = "theory";
    // Конспект начинается со своего заголовка — второй не рисуем.
    if (!state.node.theory_html) {
      const heading = document.createElement("h2");
      heading.textContent = state.node.name;
      article.append(heading);
    }
    if (state.node.description && !state.node.theory_html) {
      const lead = document.createElement("p");
      lead.className = "theory-lead";
      lead.textContent = state.node.description;
      article.append(lead);
    }
    if (state.node.theory_html) {
      const body = document.createElement("div");
      body.className = "theory-body";
      // Готовый HTML: конспект прошёл web.markdown.render на сервере.
      body.innerHTML = state.node.theory_html;
      body.querySelectorAll("pre").forEach(decorateCode);
      const lesson = state.node.lesson;
      if (lesson && lesson.sections.length) article.append(contents(lesson, body));
      article.append(body);
    } else {
      const note = document.createElement("p");
      note.className = "muted";
      note.textContent = "Конспект этого занятия скоро появится. Пока спросите тьютора в чате — он объяснит и покажет пример.";
      article.append(note);
    }
    const next = document.createElement("button");
    next.type = "button";
    next.className = "btn primary theory-next";
    next.textContent = "Перейти к практике";
    next.addEventListener("click", () => window.Lesson.selectTab("practice", { focus: true }));
    article.append(next);
    panel.append(article);
    scrollToCurrent(state);
  }

  /** Оглавление разделов; заголовки разделов в конспекте получают якоря. */
  function contents(lesson, body) {
    const headings = [...body.querySelectorAll("h2")];
    const nav = document.createElement("nav");
    nav.className = "theory-toc";
    nav.setAttribute("aria-label", "Разделы конспекта");
    const list = document.createElement("ol");
    lesson.sections.forEach((section) => {
      const heading = headings.find((h) => h.textContent.trim() === section.title);
      if (heading) heading.id = `section-${section.id}`;
      const item = document.createElement("li");
      item.className = `toc-item ${section.status}`;
      const link = document.createElement("button");
      link.type = "button";
      link.className = "toc-link";
      link.textContent = `${TOC_MARKS[section.status] || "·"} ${section.title}`;
      link.disabled = !heading;
      link.addEventListener("click", () => heading?.scrollIntoView({ block: "start" }));
      item.append(link);
      list.append(item);
    });
    nav.append(list);
    return nav;
  }

  function scrollToCurrent(state) {
    const lesson = state.node?.lesson;
    const current = lesson?.sections.find((section) => section.status === "current");
    const key = current ? `${state.node.id}:${current.id}` : null;
    if (!key || key === scrolledTo) return;
    const first = scrolledTo === null;
    scrolledTo = key;
    // Первый раздел — с начала конспекта (там «Зачем»); дальше — к разделу.
    if (first && current.id === lesson.sections[0].id) return;
    document.getElementById(`section-${current.id}`)?.scrollIntoView({ block: "start" });
  }

  window.Lesson.onState(render);
})();
