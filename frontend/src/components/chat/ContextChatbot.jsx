import React, { memo, useMemo, useState } from "react";
import { useStormStore } from "../../store/useStormStore";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:5000";

function topBy(items, key, count = 5) {
  return [...(items || [])]
    .sort((a, b) => Number(b?.[key] || 0) - Number(a?.[key] || 0))
    .slice(0, count);
}

function compactContext(state) {
  const forecast = state.kpForecast || {};
  const shap = (state.shapExplain?.features || []).slice(0, 10);
  return {
    solar: {
      kp_current: state.solarWind?.kp_current ?? null,
      kp_forecast_3h: forecast.kp_3hr?.value ?? null,
      storm_class: state.solarWind?.storm_class || forecast.peak_storm_class || "UNKNOWN",
      bz_gsm: state.solarWind?.bz_gsm,
      sw_speed: state.solarWind?.sw_speed,
      proton_density: state.solarWind?.proton_density,
      data_quality: state.solarWind?.data_quality || state.solarWind?.quality || "UNKNOWN",
      source: state.solarWind?.is_historical
        ? `Replay ${state.solarWind?.replay_data_type || ""}`
        : (state.solarWind?.data_source || "Live backend"),
    },
    kp_forecast: forecast,
    shap,
    top_satellites: topBy(state.satellites, "composite_risk", 8).map((sat) => ({
      name: sat.name,
      tier: sat.tier,
      orbit: sat.type,
      risk_level: sat.risk_level,
      composite_risk: sat.composite_risk,
      action: sat.action,
      safe_mode_minutes: sat.safe_mode_minutes,
    })),
    top_grid_corridors: topBy(state.gridCorridors, "risk_percent", 6).map((grid) => ({
      name: grid.name,
      gic_amps: grid.gic_amps,
      risk_percent: grid.risk_percent,
      action: grid.action,
    })),
    advisory: {
      title: state.advisory?.content?.advisory_title || state.advisory?.advisory_title,
      source: state.advisory?.source,
      priority_action: state.advisory?.content?.priority_action,
      hindi_summary: state.advisory?.hindi_summary || state.advisory?.content?.hindi_summary,
    },
  };
}

async function streamGroqAnswer({ message, language, context, history, onToken }) {
  const response = await fetch(`${API_BASE}/api/advisory/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, language, context, history }),
  });

  if (!response.ok || !response.body) {
    let detail = "";
    try {
      const payload = await response.json();
      detail = payload.error || JSON.stringify(payload);
    } catch {
      detail = await response.text();
    }
    throw new Error(detail || `Groq chat failed (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const eventText of events) {
      const dataLine = eventText.split("\n").find((line) => line.startsWith("data:"));
      if (!dataLine) continue;
      const payload = JSON.parse(dataLine.slice(5).trim() || "{}");
      if (payload.token) onToken(payload.token);
      if (payload.error) throw new Error(payload.error);
    }
  }
}

export const ContextChatbot = memo(() => {
  const state = useStormStore();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [language, setLanguage] = useState("en");
  const [streaming, setStreaming] = useState(false);
  const context = useMemo(() => compactContext(state), [
    state.solarWind,
    state.kpForecast,
    state.satellites,
    state.gridCorridors,
    state.shapExplain,
    state.advisory,
  ]);
  const [messages, setMessages] = useState([
    {
      id: "welcome",
      role: "assistant",
      text: "I am powered by Groq LLaMA 4 Scout. Ask about Kp, SHAP drivers, satellites, grid risk, replay/live source, or ask in Hindi.",
      source: "GROQ",
    },
  ]);

  const send = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    const assistantId = `assistant-${Date.now()}`;
    const userMessage = { id: `user-${Date.now()}`, role: "user", text };
    const assistantMessage = { id: assistantId, role: "assistant", text: "", source: "GROQ", streaming: true };
    const history = messages
      .filter((item) => item.role === "user" || item.role === "assistant")
      .slice(-8)
      .map(({ role, text: content }) => ({ role, content }));

    setMessages((items) => [...items, userMessage, assistantMessage]);
    setInput("");
    setStreaming(true);

    try {
      await streamGroqAnswer({
        message: text,
        language,
        context,
        history,
        onToken: (token) => {
          setMessages((items) => items.map((item) =>
            item.id === assistantId ? { ...item, text: item.text + token } : item
          ));
        },
      });
      setMessages((items) => items.map((item) =>
        item.id === assistantId ? { ...item, streaming: false } : item
      ));
    } catch (error) {
      setMessages((items) => items.map((item) =>
        item.id === assistantId
          ? { ...item, streaming: false, text: item.text || `Groq response unavailable: ${error.message}` }
          : item
      ));
    } finally {
      setStreaming(false);
    }
  };

  return (
    <div style={{ position: "fixed", right: 18, bottom: 18, zIndex: 2100, fontFamily: "Space Grotesk, sans-serif" }}>
      {!open && (
        <button onClick={() => setOpen(true)} style={fabStyle} title="Open Groq mission chat">
          AI
        </button>
      )}
      {open && (
        <div style={panelStyle}>
          <div style={headerStyle}>
            <div>
              <div style={titleStyle}>Groq Mission Chat</div>
              <div style={subStyle}>LLaMA 4 Scout with live Kp, SHAP, satellite and grid context</div>
            </div>
            <button onClick={() => setOpen(false)} style={closeStyle}>x</button>
          </div>

          <div style={modeRowStyle}>
            <button onClick={() => setLanguage("en")} style={language === "en" ? activeModeStyle : modeStyle}>EN</button>
            <button onClick={() => setLanguage("hi")} style={language === "hi" ? activeModeStyle : modeStyle}>हिंदी</button>
            <span style={{ marginLeft: "auto", color: streaming ? "#FFD54F" : "#607D8B", fontSize: 10 }}>
              {streaming ? "Streaming from Groq" : "Groq ready"}
            </span>
          </div>

          <div style={messagesStyle}>
            {messages.map((message) => (
              <div key={message.id} style={message.role === "user" ? userBubbleStyle : botBubbleStyle}>
                {message.text || (message.streaming ? "Thinking..." : "")}
              </div>
            ))}
          </div>

          <div style={inputRowStyle}>
            <input
              value={input}
              disabled={streaming}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") send(); }}
              placeholder={language === "hi" ? "हिंदी या English में पूछें..." : "Ask about Kp, SHAP, grid..."}
              style={inputStyle}
            />
            <button onClick={send} disabled={streaming} style={{ ...sendStyle, opacity: streaming ? 0.55 : 1 }}>
              Send
            </button>
          </div>
        </div>
      )}
    </div>
  );
});

const fabStyle = {
  width: 62,
  height: 62,
  borderRadius: "50%",
  border: "1px solid rgba(0,212,255,0.55)",
  background: "linear-gradient(145deg, rgba(2,8,23,0.98), rgba(7,27,52,0.98))",
  color: "#00D4FF",
  fontFamily: "Orbitron, sans-serif",
  fontWeight: 900,
  boxShadow: "0 0 28px rgba(0,212,255,0.26), inset 0 0 18px rgba(0,212,255,0.08)",
  cursor: "pointer",
};

const panelStyle = {
  width: "min(430px, calc(100vw - 28px))",
  height: "min(610px, calc(100vh - 112px))",
  background: "linear-gradient(180deg, rgba(2,8,23,0.98), rgba(6,18,39,0.98))",
  border: "1px solid rgba(0,212,255,0.24)",
  borderRadius: 10,
  boxShadow: "0 24px 70px rgba(0,0,0,0.55), 0 0 34px rgba(0,212,255,0.1)",
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

const headerStyle = {
  display: "flex",
  justifyContent: "space-between",
  gap: 12,
  padding: "14px 14px 10px",
  borderBottom: "1px solid rgba(0,212,255,0.12)",
};

const titleStyle = { color: "#E8F4FD", fontFamily: "Orbitron, sans-serif", fontWeight: 800, fontSize: 13 };
const subStyle = { color: "#78909C", fontSize: 11, marginTop: 3, lineHeight: 1.35 };
const closeStyle = { width: 28, height: 28, borderRadius: 6, background: "rgba(255,255,255,0.04)", color: "#90A4AE", border: "1px solid rgba(255,255,255,0.12)", cursor: "pointer" };
const modeRowStyle = { display: "flex", alignItems: "center", gap: 6, padding: "8px 12px", borderBottom: "1px solid rgba(0,212,255,0.09)" };
const modeStyle = { border: "1px solid rgba(255,255,255,0.1)", background: "rgba(255,255,255,0.03)", color: "#78909C", borderRadius: 6, padding: "5px 9px", fontSize: 11, cursor: "pointer" };
const activeModeStyle = { ...modeStyle, border: "1px solid rgba(0,212,255,0.4)", background: "rgba(0,212,255,0.12)", color: "#00D4FF" };
const messagesStyle = { flex: 1, overflow: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 8 };
const botBubbleStyle = { alignSelf: "flex-start", maxWidth: "92%", background: "rgba(0,212,255,0.08)", border: "1px solid rgba(0,212,255,0.14)", color: "#C8D7E1", borderRadius: 8, padding: "8px 10px", fontSize: 12, lineHeight: 1.5, whiteSpace: "pre-wrap" };
const userBubbleStyle = { ...botBubbleStyle, alignSelf: "flex-end", background: "rgba(255,152,0,0.11)", border: "1px solid rgba(255,152,0,0.2)", color: "#FFE0B2" };
const inputRowStyle = { display: "flex", gap: 8, padding: 12, borderTop: "1px solid rgba(0,212,255,0.12)" };
const inputStyle = { flex: 1, minWidth: 0, borderRadius: 8, border: "1px solid rgba(0,212,255,0.18)", background: "rgba(255,255,255,0.05)", color: "#E8F4FD", padding: "0 10px", fontSize: 12 };
const sendStyle = { borderRadius: 8, border: "1px solid rgba(0,212,255,0.42)", background: "rgba(0,212,255,0.14)", color: "#00D4FF", padding: "0 12px", fontFamily: "Orbitron, sans-serif", fontSize: 10, fontWeight: 800, cursor: "pointer" };
