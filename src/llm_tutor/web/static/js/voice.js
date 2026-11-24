/* Голосовой ввод: речь распознаёт сам браузер (Web Speech API).
 *
 * Звук никуда с нашего сервера не уходит: распознавание делает браузер
 * (в Chrome/Edge — их служба), поэтому модуль работает только в защищённом
 * контексте (https или localhost/127.0.0.1) и только там, где браузер знает
 * SpeechRecognition. Движка нет — кнопки микрофона просто не появляются.
 *
 * Поле ввода — обычное: распознанный текст дописывается в позицию курсора,
 * а `input`-событие подхватывают автовысота чата и черновики «Практики».
 */
window.Voice = (() => {
  const { icon, notify } = window.App;
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const available = Boolean(Recognition) && window.isSecureContext;

  // Говорит один ученик: запуск нового поля останавливает прежнее.
  let current = null;

  const ERRORS = {
    "not-allowed": "Нет доступа к микрофону. Разрешите его в настройках браузера.",
    "service-not-allowed": "Браузер запретил распознавание речи.",
    "audio-capture": "Микрофон не найден.",
    network: "Браузер не смог распознать речь — проверьте интернет.",
  };

  // Склейка кусков речи без лишних пробелов перед знаками препинания.
  const NO_SPACE_AFTER = /[\s(«"'—–-]$/;
  const NO_SPACE_BEFORE = /^[\s.,;:!?)»"']/;

  function spaced(left, right) {
    if (!left) return right;
    if (!right) return left;
    if (NO_SPACE_AFTER.test(left) || NO_SPACE_BEFORE.test(right)) return left + right;
    return `${left} ${right}`;
  }

  /**
   * Кнопка микрофона для поля ввода.
   *
   * Возвращает `{button, status, start, stop, setEnabled, listening}`. Кнопку
   * и `status` (скрытое объявление для диктора) вставляет вызывающий: в чате —
   * рядом с «Отправить», в «Практике» — в ряд действий.
   */
  function create(field, { lang = "ru-RU", label = "" } = {}) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = label ? "btn ghost voice-btn" : "btn ghost icon-only voice-btn";
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-label", "Надиктовать голосом");
    button.title = "Надиктовать голосом (речь распознаёт браузер)";
    button.append(icon("mic"));
    const caption = label ? document.createElement("span") : null;
    if (caption) {
      caption.textContent = label;
      button.append(caption);
    }

    const status = document.createElement("span");
    status.className = "visually-hidden";
    status.setAttribute("role", "status");

    let recognition = null;
    let listening = false;
    // Сеанс завершён и текст зафиксирован: поздние события его не трогают.
    // Без этого хвост распознавания мог дописать текст в уже очищенное поле
    // после отправки.
    let settled = false;
    // Текст поля до диктовки и после курсора: распознанное вставляется между ними.
    let base = "";
    let tail = "";
    let spoken = "";
    let interim = "";

    function compose() {
      const speech = spaced(spoken, interim);
      if (!speech) return base + tail;
      return spaced(spaced(base, speech), tail);
    }

    function updateField() {
      let text = compose();
      // Программная запись обходит maxlength — держим тот же предел, что у набора.
      if (field.maxLength > 0) text = text.slice(0, field.maxLength);
      field.value = text;
      // Автовысота чата и черновики «Практики» слушают обычный input.
      field.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function setUi() {
      button.classList.toggle("listening", listening);
      button.setAttribute("aria-pressed", String(listening));
      button.setAttribute("aria-label", listening ? "Остановить запись" : "Надиктовать голосом");
      if (caption) caption.textContent = listening ? "Остановить" : label;
      status.textContent = listening ? "Слушаю. Говорите — текст появится в поле." : "";
    }

    /** Зафиксировать сказанное: хвост нефинального текста остаётся в поле. */
    function settle() {
      if (settled) return;
      settled = true;
      // Ученик это сказал — значит, это часть ответа, даже если слово ещё не
      // финальное. После отправки поле уже очищено, но ответ собран раньше.
      if (interim) {
        spoken = spaced(spoken, interim);
        interim = "";
        updateField();
      }
      recognition = null;
      if (current === controller) current = null;
      setUi();
    }

    function fail(message) {
      notify(message, { error: true });
      stop();
    }

    function stop() {
      if (!listening) return;
      listening = false;
      const session = recognition;
      // Синхронно: отправитель следом очищает поле, и поздний onend не должен
      // вернуть текст обратно.
      settle();
      try {
        session?.stop();
      } catch {
        // Сеанса уже нет — текст и состояние уже зафиксированы.
      }
      setUi();
    }

    function start() {
      if (!available || button.disabled) return;
      if (listening) {
        stop();
        return;
      }
      if (current && current !== controller) current.stop();
      current = controller;

      const value = field.value || "";
      const caret = field.selectionStart ?? value.length;
      base = value.slice(0, caret);
      tail = value.slice(caret);
      spoken = "";
      interim = "";
      settled = false;
      listening = true;
      recognition = new Recognition();
      recognition.lang = lang;
      recognition.continuous = true;
      recognition.interimResults = true;

      recognition.onresult = (event) => {
        // После «Остановить» или отправки поздние результаты не пишем: поле
        // могло уже очиститься.
        if (!listening) return;
        let interimText = "";
        for (let index = event.resultIndex; index < event.results.length; index += 1) {
          const result = event.results[index];
          const text = (result[0]?.transcript || "").trim();
          if (!text) continue;
          if (result.isFinal) spoken = spaced(spoken, text);
          else interimText = spaced(interimText, text);
        }
        interim = interimText;
        updateField();
      };

      recognition.onerror = (event) => {
        // Молчание и отмена — не ошибка: ждём дальше или уже остановились.
        if (event.error === "no-speech" || event.error === "aborted") return;
        fail(ERRORS[event.error] || "Не удалось распознать речь.");
      };

      recognition.onend = () => {
        // Chrome заканчивает сеанс на паузе; продолжаем, пока ученик не нажал
        // «Остановить». Ошибка уже сняла listening и показала причину.
        if (!listening) {
          settle();
          return;
        }
        try {
          recognition.start();
        } catch {
          listening = false;
          settle();
        }
      };

      try {
        recognition.start();
      } catch {
        listening = false;
        settle();
        notify("Не удалось включить микрофон — попробуйте ещё раз.", { error: true });
        return;
      }
      setUi();
    }

    button.addEventListener("click", () => {
      if (!listening) {
        start();
        return;
      }
      stop();
      // После остановки фокус — в поле: Enter отправит ответ, а не начнёт
      // запись заново.
      if (!field.disabled) field.focus();
    });
    setUi();

    const controller = {
      button,
      status,
      start,
      stop,
      setEnabled(enabled) {
        button.disabled = !enabled;
        if (!enabled) stop();
      },
      get listening() {
        return listening;
      },
    };
    return controller;
  }

  return { available, create };
})();
