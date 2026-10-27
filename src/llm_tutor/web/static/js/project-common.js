/* Общее для страниц проекта: сборка узлов и карточка описания проекта.
 *
 * Всё, что пришло с сервера как данные (названия, ссылки, тексты ученика),
 * попадает в страницу через textContent. Ссылки — только http(s).
 */
window.ProjectUI = (() => {
  /** Узел: el("div", {class: "x", "data-id": 1}, "текст", дочерний_узел). */
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (value === undefined || value === null || value === false) continue;
      if (name === "text") node.textContent = value;
      else node.setAttribute(name, value === true ? "" : value);
    }
    for (const child of children) {
      if (child === undefined || child === null || child === false) continue;
      node.append(child);
    }
    return node;
  }

  function safeLink(url) {
    return /^https?:\/\//i.test(url || "");
  }

  /** Описание проекта курса: суть, результат, занятия и примеры данных. */
  function briefBody(brief) {
    const body = el("div", { class: "brief-body" });
    body.append(el("p", { class: "brief-summary", text: brief.summary }));
    body.append(
      el("p", { class: "brief-result" }, el("strong", { text: "Что получится: " }), brief.result)
    );
    if (brief.lessons.length) {
      body.append(
        el("p", { class: "brief-lessons" }, el("strong", { text: "Пригодятся занятия: " }), brief.lessons.join(" · "))
      );
    }
    const links = brief.datasets.filter((dataset) => safeLink(dataset.url));
    if (links.length) {
      const list = el("ul", { class: "brief-datasets" });
      for (const dataset of links) {
        list.append(
          el(
            "li",
            {},
            el("a", { href: dataset.url, target: "_blank", rel: "noopener noreferrer", text: dataset.title })
          )
        );
      }
      body.append(el("p", { class: "brief-lessons" }, el("strong", { text: "Примеры данных:" })), list);
    }
    return body;
  }

  /** Карточка выбора: описание и кнопка «Выбрать» (onChoose получает описание). */
  function briefCard(brief, onChoose) {
    const button = el("button", { class: "btn primary", type: "button" }, "Выбрать");
    button.setAttribute("aria-label", `Выбрать проект «${brief.title}»`);
    button.addEventListener("click", () => onChoose(brief, button));
    return el(
      "article",
      { class: "brief-card panel", "data-brief": brief.id },
      el("h3", { text: brief.title }),
      briefBody(brief),
      el("div", { class: "brief-actions" }, button)
    );
  }

  return { el, safeLink, briefBody, briefCard };
})();
