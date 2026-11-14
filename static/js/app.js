document.addEventListener("submit", function (event) {
  const form = event.target.closest(".confirm-submit");
  if (!form) {
    return;
  }

  const message = form.dataset.confirm || "Are you sure you want to continue?";
  if (!window.confirm(message)) {
    event.preventDefault();
    return;
  }

  const button = form.querySelector('button[type="submit"], input[type="submit"]');
  if (button) {
    button.disabled = true;
    if (button.tagName === "BUTTON") {
      button.dataset.originalText = button.innerHTML;
      button.innerHTML = "Please wait...";
    }
  }
});
