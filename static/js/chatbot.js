(() => {
  const root = document.getElementById("chatbot");
  if (!root) {
    return;
  }

  const toggle = document.getElementById("chatbot-toggle");
  const win = document.getElementById("chatbot-window");
  const messages = document.getElementById("chatbot-messages");
  const form = document.getElementById("chatbot-form");
  const input = document.getElementById("chatbot-input");
  const clearButton = document.getElementById("chatbot-clear");
  const closeButton = document.getElementById("chatbot-close");
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  let historyLoaded = false;
  let sending = false;

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatAssistantContent(value) {
    let html = escapeHtml(value);
    html = html.replace(/```([\s\S]*?)```/g, (_match, code) => `<pre><code>${code.trim()}</code></pre>`);
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\n/g, "<br>");
    return html;
  }

  function addMessage(role, content, createdAt = "") {
    const item = document.createElement("div");
    item.className = `chat-message ${role}`;

    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    bubble.innerHTML = role === "assistant" ? formatAssistantContent(content) : escapeHtml(content).replaceAll("\n", "<br>");
    item.appendChild(bubble);

    if (createdAt) {
      const time = document.createElement("small");
      time.className = "chat-time";
      time.textContent = createdAt;
      item.appendChild(time);
    }

    messages.appendChild(item);
    scrollToBottom();
    return item;
  }

  function addTyping() {
    const item = document.createElement("div");
    item.className = "chat-message assistant typing";
    item.innerHTML = '<div class="chat-bubble"><span></span><span></span><span></span></div>';
    messages.appendChild(item);
    scrollToBottom();
    return item;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        ...(options.headers || {}),
      },
      ...options,
    });
    let data = {};
    try {
      data = await response.json();
    } catch (_error) {
      data = {};
    }
    if (!response.ok) {
      throw new Error(data.error || "Request failed. Please try again.");
    }
    return data;
  }

  async function loadHistory() {
    if (historyLoaded) {
      return;
    }
    try {
      const data = await api("/api/chat/history");
      messages.innerHTML = "";
      if (data.messages && data.messages.length) {
        data.messages.forEach((message) => addMessage(message.role, message.content, message.created_at));
      } else {
        addMessage("assistant", "Hi! I can answer questions about the forum or help with anything else. What would you like to know?");
      }
      historyLoaded = true;
    } catch (error) {
      addMessage("assistant", error.message);
    }
  }

  function setOpen(open) {
    win.classList.toggle("open", open);
    win.setAttribute("aria-hidden", String(!open));
    toggle.setAttribute("aria-expanded", String(open));
    if (open) {
      loadHistory();
      input.focus();
    }
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  }

  async function sendMessage() {
    const message = input.value.trim();
    if (!message || sending) {
      return;
    }

    sending = true;
    addMessage("user", message);
    input.value = "";
    autosize();
    const typing = addTyping();

    try {
      const data = await api("/api/chat", {
        method: "POST",
        body: JSON.stringify({ message }),
      });
      typing.remove();
      addMessage("assistant", data.reply || "I could not generate a response.");
    } catch (error) {
      typing.remove();
      addMessage("assistant", error.message);
    } finally {
      sending = false;
      input.focus();
    }
  }

  toggle.addEventListener("click", () => setOpen(!win.classList.contains("open")));
  closeButton.addEventListener("click", () => setOpen(false));

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
  });

  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });

  document.querySelectorAll("[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      input.value = button.dataset.prompt || "";
      sendMessage();
    });
  });

  clearButton.addEventListener("click", async () => {
    try {
      await api("/api/chat/clear", { method: "POST" });
      messages.innerHTML = "";
      addMessage("assistant", "Chat cleared. How can I help you now?");
      historyLoaded = true;
    } catch (error) {
      addMessage("assistant", error.message);
    }
  });

  autosize();
})();
