document.addEventListener("DOMContentLoaded", function () {
  const filter = document.getElementById("topic-filter");
  if (filter) {
    filter.addEventListener("input", function () {
      const query = this.value.trim().toLowerCase();
      document.querySelectorAll(".topic-card").forEach(function (card) {
        const haystack = card.dataset.search || "";
        card.style.display = !query || haystack.includes(query) ? "" : "none";
      });
    });
  }

  document.querySelectorAll(".toggle-replies").forEach(function (button) {
    button.addEventListener("click", function () {
      const replies = this.closest(".replies");
      if (!replies) {
        return;
      }

      const hidden = replies.querySelectorAll(".reply-hidden");
      const isExpanded = button.dataset.expanded === "true";
      hidden.forEach(function (item) {
        item.classList.toggle("reply-hidden", isExpanded);
      });
      button.dataset.expanded = isExpanded ? "false" : "true";
      button.textContent = isExpanded
        ? `Show all ${button.dataset.total || hidden.length} replies`
        : "Show less";
    });
  });
});
