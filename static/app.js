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
  document.addEventListener("focusout", () => document.body.classList.remove("keyboard-open"));
}
