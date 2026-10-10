/* Вкладка «Теория»: конспект текущей темы (готовый HTML с сервера). */
(() => {
  const panel = document.getElementById("tab-theory");

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
    if (window.Lesson.code) {
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
      title.textContent = "Тема не выбрана";
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
      article.append(body);
    } else {
      const note = document.createElement("p");
      note.className = "muted";
      note.textContent = "Конспект этой темы скоро появится. Пока спросите тьютора в чате — он объяснит и покажет пример.";
      article.append(note);
    }
    const next = document.createElement("button");
    next.type = "button";
    next.className = "btn primary theory-next";
    next.textContent = "Перейти к практике";
    next.addEventListener("click", () => window.Lesson.selectTab("practice", { focus: true }));
    article.append(next);
    panel.append(article);
  }

  window.Lesson.onState(render);
})();
