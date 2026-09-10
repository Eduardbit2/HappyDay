"use strict";
document.addEventListener("submit", (event) => {
  if (event.target.method.toLowerCase() !== "post") return;
  event.target.setAttribute("aria-busy", "true");
  event.target.querySelectorAll("button[type=submit]").forEach((button) => {
    button.dataset.originalText = button.textContent;
    button.textContent = "Подождите…";
    button.disabled = true;
  });
});
window.addEventListener("pageshow", () => {
  document.querySelectorAll("form[aria-busy]").forEach((form) => form.removeAttribute("aria-busy"));
  document.querySelectorAll("button[data-original-text]").forEach((button) => {
    button.textContent = button.dataset.originalText; button.disabled = false;
  });
});
if (window.visualViewport) {
  const updateKeyboard = () => {
    const editing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    document.body.classList.toggle("keyboard-open", editing && visualViewport.height < window.innerHeight * .78);
  };
  visualViewport.addEventListener("resize", updateKeyboard);
  document.addEventListener("focusin", updateKeyboard);
  document.addEventListener("focusout", () => document.body.classList.remove("keyboard-open"));
}

const csvFile = document.getElementById("csv-file");
if (csvFile) {
  let selection = 0;
  csvFile.addEventListener("change", async () => {
    const current = ++selection;
    const content = document.getElementById("csv");
    const error = document.getElementById("csv-file-error");
    const submit = csvFile.form.querySelector("button[type=submit]");
    content.value = "";
    error.textContent = "";
    const file = csvFile.files[0];
    submit.disabled = false;
    if (!file) return;
    submit.disabled = true;
    try {
      if (file.size > 262144) throw new Error("CSV должен быть не больше 256 КБ.");
      const buffer = await file.arrayBuffer();
      const text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
      if (current === selection) content.value = text;
    } catch (failure) {
      if (current === selection) error.textContent = failure instanceof TypeError
        ? "Не удалось прочитать UTF-8. Сохраните файл как CSV UTF-8."
        : failure.message;
    } finally {
      if (current === selection) submit.disabled = false;
    }
  });
}
