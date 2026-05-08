/**
 * chat-upload plugin — v1.4.0
 *
 * Live chat with tool-aware ephemeral status indicator.
 * Uses the dashboard Plugin SDK (window.__HERMES_PLUGIN_SDK__).
 * No build step — plain IIFE using SDK globals.
 *
 * Status vocabulary:
 *   thinking…    LLM call in flight
 *   searching…   web_search / web_extract
 *   running…     terminal / process
 *   browsing…    browser_*
 *   delegating…  delegate_task
 *   writing…     write_file / patch
 *   reading…     read_file / search_files
 *   responding…  final text streaming
 *   working…     everything else
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var React = SDK.React;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useRef = SDK.hooks.useRef;
  var useCallback = SDK.hooks.useCallback;

  var Card = SDK.components.Card;
  var CardContent = SDK.components.CardContent;
  var CardHeader = SDK.components.CardHeader;
  var CardTitle = SDK.components.CardTitle;
  var Button = SDK.components.Button;
  var Input = SDK.components.Input;
  var Badge = SDK.components.Badge;
  var cn = SDK.utils.cn;

  var PLUGIN_VERSION = "1.4.0";

  // ─── Helpers ────────────────────────────────────────────────────────────────

  function getToken() {
    return window.__HERMES_SESSION_TOKEN__ || "";
  }

  function buildWsUrl(path) {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var base = window.__HERMES_BASE_PATH__ || "";
    return proto + "//" + location.host + base + "/api/plugins/chat-upload" + path + "?token=" + encodeURIComponent(getToken());
  }

  // ─── Status indicator ───────────────────────────────────────────────────────

  function StatusIndicator(props) {
    // label: string | null  (null = hidden)
    var label = props.label;
    if (!label) return null;

    return React.createElement(
      "div",
      {
        style: {
          display: "flex",
          alignItems: "center",
          gap: "6px",
          padding: "6px 12px",
          opacity: 0.7,
          fontStyle: "italic",
          fontSize: "0.82rem",
          color: "hsl(var(--muted-foreground))",
          userSelect: "none",
          minHeight: "28px",
        },
      },
      React.createElement(SpinnerDots, null),
      label + "\u2026"
    );
  }

  function SpinnerDots() {
    // CSS-animated three dots via keyframes injected once.
    useEffect(function () {
      var id = "chat-upload-spinner-style";
      if (document.getElementById(id)) return;
      var style = document.createElement("style");
      style.id = id;
      style.textContent =
        "@keyframes cu-blink {" +
        "0%,80%,100%{opacity:0.2}40%{opacity:1}" +
        "}" +
        ".cu-dot{display:inline-block;width:4px;height:4px;border-radius:50%;" +
        "background:currentColor;animation:cu-blink 1.2s infinite;}" +
        ".cu-dot:nth-child(2){animation-delay:.2s}" +
        ".cu-dot:nth-child(3){animation-delay:.4s}";
      document.head.appendChild(style);
    }, []);

    return React.createElement(
      "span",
      { style: { display: "inline-flex", gap: "3px", alignItems: "center" } },
      React.createElement("span", { className: "cu-dot" }),
      React.createElement("span", { className: "cu-dot" }),
      React.createElement("span", { className: "cu-dot" })
    );
  }

  // ─── Message bubble ─────────────────────────────────────────────────────────

  function MessageBubble(props) {
    var msg = props.msg;
    var isUser = msg.role === "user";

    return React.createElement(
      "div",
      {
        style: {
          display: "flex",
          justifyContent: isUser ? "flex-end" : "flex-start",
          marginBottom: "12px",
        },
      },
      React.createElement(
        "div",
        {
          style: {
            maxWidth: "78%",
            padding: "8px 12px",
            borderRadius: "8px",
            fontSize: "0.875rem",
            lineHeight: "1.5",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            background: isUser
              ? "hsl(var(--primary))"
              : "hsl(var(--muted))",
            color: isUser
              ? "hsl(var(--primary-foreground))"
              : "hsl(var(--foreground))",
          },
        },
        msg.text
      )
    );
  }

  // ─── Main chat component ─────────────────────────────────────────────────────

  function ChatUploadPage() {
    var _s = useState([]);
    var messages = _s[0], setMessages = _s[1];

    var _st = useState(null);
    var statusLabel = _st[0], setStatusLabel = _st[1];

    var _inp = useState("");
    var inputText = _inp[0], setInputText = _inp[1];

    var _busy = useState(false);
    var busy = _busy[0], setBusy = _busy[1];

    var _err = useState(null);
    var error = _err[0], setError = _err[1];

    var scrollRef = useRef(null);
    var wsRef = useRef(null);
    var sessionIdRef = useRef(null);

    // Auto-scroll on new messages
    useEffect(function () {
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      }
    }, [messages, statusLabel]);

    function appendMessage(role, text) {
      setMessages(function (prev) {
        return prev.concat([{ id: Date.now() + Math.random(), role: role, text: text }]);
      });
    }

    var sendMessage = useCallback(function () {
      var text = inputText.trim();
      if (!text || busy) return;

      setInputText("");
      setError(null);
      setBusy(true);
      setStatusLabel("thinking");
      appendMessage("user", text);

      // Accumulate streamed delta chunks
      var deltaBuffer = [];

      var ws = new WebSocket(buildWsUrl("/stream"));
      wsRef.current = ws;

      ws.onopen = function () {
        ws.send(JSON.stringify({
          type: "message",
          text: text,
          session_id: sessionIdRef.current || null,
        }));
      };

      ws.onmessage = function (evt) {
        var frame;
        try { frame = JSON.parse(evt.data); } catch (_e) { return; }

        switch (frame.type) {
          case "status":
            setStatusLabel(frame.label || null);
            break;

          case "delta":
            deltaBuffer.push(frame.text || "");
            break;

          case "done":
            // "done" carries the full response. Prefer that over assembled deltas
            // in case deltas were out of order or partial.
            var finalText = frame.text || deltaBuffer.join("");
            setStatusLabel(null);
            appendMessage("assistant", finalText);
            deltaBuffer = [];
            break;

          case "clear":
            setStatusLabel(null);
            break;

          case "error":
            setStatusLabel(null);
            setError(frame.text || "Agent error");
            break;

          default:
            break;
        }
      };

      ws.onerror = function () {
        setStatusLabel(null);
        setError("Connection error. Is the dashboard restarted?");
        setBusy(false);
      };

      ws.onclose = function () {
        setStatusLabel(null);
        setBusy(false);
        wsRef.current = null;
      };
    }, [inputText, busy]);

    function handleKeyDown(e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    }

    function clearChat() {
      setMessages([]);
      setStatusLabel(null);
      setError(null);
      sessionIdRef.current = null;
    }

    return React.createElement(
      "div",
      {
        style: {
          display: "flex",
          flexDirection: "column",
          height: "calc(100vh - 120px)",
          gap: "0",
        },
      },

      // Header
      React.createElement(
        Card,
        { style: { marginBottom: "12px", flexShrink: 0 } },
        React.createElement(
          CardHeader,
          { style: { paddingBottom: "8px" } },
          React.createElement(
            "div",
            { style: { display: "flex", alignItems: "center", justifyContent: "space-between" } },
            React.createElement(
              "div",
              { style: { display: "flex", alignItems: "center", gap: "8px" } },
              React.createElement(CardTitle, { className: "text-lg" }, "Chat"),
              React.createElement(Badge, { variant: "outline" }, "v" + PLUGIN_VERSION)
            ),
            React.createElement(
              Button,
              {
                onClick: clearChat,
                style: { fontSize: "0.75rem", padding: "4px 10px", cursor: "pointer" },
              },
              "Clear"
            )
          )
        )
      ),

      // Message list
      React.createElement(
        Card,
        { style: { flex: 1, minHeight: 0, display: "flex", flexDirection: "column" } },
        React.createElement(
          CardContent,
          {
            ref: scrollRef,
            style: {
              flex: 1,
              overflowY: "auto",
              padding: "16px",
              display: "flex",
              flexDirection: "column",
            },
          },

          messages.length === 0
            ? React.createElement(
                "div",
                {
                  style: {
                    flex: 1,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "hsl(var(--muted-foreground))",
                    fontSize: "0.875rem",
                    fontStyle: "italic",
                  },
                },
                "Send a message to start chatting."
              )
            : messages.map(function (m) {
                return React.createElement(MessageBubble, { key: m.id, msg: m });
              }),

          // Ephemeral status indicator — NOT part of message history
          React.createElement(StatusIndicator, { label: statusLabel })
        )
      ),

      // Error banner
      error
        ? React.createElement(
            "div",
            {
              style: {
                margin: "8px 0",
                padding: "8px 12px",
                background: "hsl(var(--destructive) / 0.12)",
                color: "hsl(var(--destructive))",
                borderRadius: "6px",
                fontSize: "0.8rem",
                flexShrink: 0,
              },
            },
            error
          )
        : null,

      // Input row
      React.createElement(
        Card,
        { style: { marginTop: "12px", flexShrink: 0 } },
        React.createElement(
          CardContent,
          { style: { padding: "12px", display: "flex", gap: "8px", alignItems: "flex-end" } },
          React.createElement(Input, {
            value: inputText,
            onChange: function (e) { setInputText(e.target.value); },
            onKeyDown: handleKeyDown,
            placeholder: busy ? "Agent is working\u2026" : "Type a message and press Enter",
            disabled: busy,
            style: { flex: 1, resize: "none" },
          }),
          React.createElement(
            Button,
            {
              onClick: sendMessage,
              disabled: busy || !inputText.trim(),
              style: { cursor: busy ? "not-allowed" : "pointer", flexShrink: 0 },
            },
            busy ? "Working\u2026" : "Send"
          )
        )
      )
    );
  }

  // Register the plugin tab.
  window.__HERMES_PLUGINS__.register("chat-upload", ChatUploadPage);
})();
