/* Progressive enhancement only. Release metadata never enables installation. */
"use strict";
for (const button of document.querySelectorAll("[data-copy-target]")) {
  const field = document.getElementById(button.dataset.copyTarget);
  const status = document.getElementById("copy-status");
  if (!field || !status) continue;
  button.hidden = false;
  button.addEventListener("click", async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(field.value);
      status.textContent = "Readiness prompt copied. Review it in your chosen agent before sending.";
    } catch {
      field.focus();
      field.select();
      status.textContent = "Copy was unavailable. The prompt is selected; use your device’s copy action.";
    }
  });
}
