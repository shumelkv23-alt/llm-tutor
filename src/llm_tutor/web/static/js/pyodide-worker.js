/* Web Worker: Python (Pyodide) для вкладки «Код».
 *
 * Код ученика выполняется здесь, а не на сервере и не в основном потоке:
 * бесконечный цикл не вешает страницу — страница просто уничтожает воркер
 * по таймауту. Каждый запуск идёт в чистом пространстве имён.
 *
 * Протокол:
 *   → {type: "run", id, indexURL, setup, code, tests}
 *   ← {type: "status", text}            загрузка Python и пакетов
 *   ← {type: "started", id}             код начал выполняться (отсчёт таймаута)
 *   ← {type: "result", id, ok, stage, stdout, error, passed}
 *   ← {type: "fatal", text}             Python не загрузился
 */
let pyodide = null;
let loading = null;

function post(message) {
  self.postMessage(message);
}

async function ensurePyodide(indexURL) {
  if (pyodide) return pyodide;
  if (!loading) {
    loading = (async () => {
      post({ type: "status", text: "Загружаем Python… (первый раз — до минуты)" });
      importScripts(`${indexURL}pyodide.js`);
      const instance = await self.loadPyodide({ indexURL });
      pyodide = instance;
      return instance;
    })();
  }
  return loading;
}

/** Последняя содержательная строка трейсбека: «AssertionError: …», «NameError: …». */
function shortError(error) {
  const text = String(error && error.message ? error.message : error);
  const lines = text.trim().split("\n").filter((line) => line.trim());
  return lines.length ? lines[lines.length - 1].trim() : text;
}

self.onmessage = async (event) => {
  const message = event.data || {};
  if (message.type !== "run") return;
  const { id, indexURL, setup = "", code = "", tests = "" } = message;
  let instance;
  try {
    instance = await ensurePyodide(indexURL);
  } catch (error) {
    loading = null;
    post({ type: "fatal", text: `Не удалось загрузить Python: ${shortError(error)}` });
    return;
  }

  const output = [];
  instance.setStdout({ batched: (text) => output.push(text) });
  instance.setStderr({ batched: (text) => output.push(text) });

  try {
    post({ type: "status", text: "Загружаем библиотеки…" });
    // pandas и numpy подгружаются по импортам в коде (с того же адреса).
    await instance.loadPackagesFromImports(`${setup}\n${code}\n${tests}`);
  } catch (error) {
    post({ type: "result", id, ok: false, stage: "packages", stdout: output.join("\n"), error: shortError(error), passed: false });
    return;
  }

  const namespace = instance.globals.get("dict")();
  let stage = "setup";
  try {
    post({ type: "started", id });
    if (setup.trim()) await instance.runPythonAsync(setup, { globals: namespace });
    stage = "code";
    await instance.runPythonAsync(code, { globals: namespace });
    let passed = null;
    if (tests.trim()) {
      stage = "tests";
      await instance.runPythonAsync(tests, { globals: namespace });
      passed = true;
    }
    post({ type: "result", id, ok: true, stage, stdout: output.join("\n"), error: null, passed });
  } catch (error) {
    post({
      type: "result",
      id,
      ok: false,
      stage,
      stdout: output.join("\n"),
      error: shortError(error),
      passed: false,
    });
  } finally {
    namespace.destroy();
  }
};
