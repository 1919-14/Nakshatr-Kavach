import React, { memo, useEffect, useRef } from "react";
import { MapContainer, TileLayer, Polyline, Popup, useMap } from "react-leaflet";
import { motion } from "framer-motion";
import { useStormStore } from "../../store/useStormStore";
import { MOCK_GRID_CORRIDORS } from "../../mock/mockData";
import { getRiskColor } from "../../utils/riskColorMapper";
import { SectionLabel } from "../ui/index";
import "leaflet/dist/leaflet.css";

// ── Custom zoom control ───────────────────────────────────────────────────────
function CustomZoom() {
  const map = useMap();
  return (
    <div style={{
      position: "absolute", right: 10, bottom: 40,
      zIndex: 1000, display: "flex", flexDirection: "column", gap: 4,
    }}>
      {["+", "−"].map((label, i) => (
        <button
          key={label}
          onClick={() => i === 0 ? map.zoomIn() : map.zoomOut()}
          style={{
            width: 28, height: 28, borderRadius: 6,
            background: "rgba(13,27,62,0.9)",
            border: "1px solid rgba(0,212,255,0.3)",
            color: "#00D4FF", fontSize: 16, cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontFamily: "Orbitron, sans-serif",
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

// ── Corridor popup content ────────────────────────────────────────────────────
function CorridorPopup({ corridor }) {
  const color = getRiskColor(corridor.risk_percent);
  return (
    <div style={{
      fontFamily: "Space Grotesk, sans-serif",
      minWidth:   200,
      color:      "#E8F4FD",
    }}>
      <div style={{
        fontFamily: "Orbitron, sans-serif",
        fontSize: 11, fontWeight: 700,
        color: "#00D4FF", marginBottom: 8,
        letterSpacing: "0.08em",
      }}>
        {corridor.name}
      </div>
      <div style={{ fontSize: 11, color: "#546E7A", marginBottom: 6 }}>
        {corridor.voltage} · {corridor.states}
      </div>

      {/* GIC value */}
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ fontSize: 11, color: "#546E7A" }}>GIC Amplitude</span>
        <span style={{
          fontFamily: "Orbitron, sans-serif",
          fontSize: 14, fontWeight: 700, color,
        }}>
          {corridor.gic_amps}A
        </span>
      </div>

      {/* Risk bar */}
      <div style={{ marginBottom: 8 }}>
        <div style={{ height: 4, background: "rgba(255,255,255,0.1)", borderRadius: 2 }}>
          <div style={{
            height: "100%", width: `${corridor.risk_percent}%`,
            background: color, borderRadius: 2,
            transition: "width 0.6s ease",
          }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 2 }}>
          <span style={{ fontSize: 10, color: "#546E7A" }}>Risk</span>
          <span style={{ fontSize: 10, color }}>{corridor.risk_percent}%</span>
        </div>
      </div>

      {/* Stats */}
      {[
        ["Economic risk",    `₹${corridor.impact_crore}Cr`],
        ["Population",       `${corridor.population_millions}M people`],
      ].map(([l, v]) => (
        <div key={l} style={{
          display: "flex", justifyContent: "space-between",
          fontSize: 11, marginBottom: 3,
        }}>
          <span style={{ color: "#546E7A" }}>{l}</span>
          <span style={{ color: "#90A4AE" }}>{v}</span>
        </div>
      ))}

      {/* Action */}
      <div style={{
        marginTop: 8, padding: "6px 8px",
        background: "rgba(255,143,0,0.1)",
        border: "1px solid rgba(255,143,0,0.3)",
        borderRadius: 5, fontSize: 11,
        color: "#FF9800", lineHeight: 1.5,
      }}>
        ▸ {corridor.action}
      </div>
    </div>
  );
}

// ── India Grid Map ────────────────────────────────────────────────────────────
export const IndiaGridMap = memo(() => {
  const { gridCorridors } = useStormStore();
  const corridors = gridCorridors?.length ? gridCorridors : MOCK_GRID_CORRIDORS;

  return (
    <div style={{
      height:       "100%",
      background:   "var(--color-bg-card)",
      borderRadius: 12,
      border:       "1px solid rgba(0,212,255,0.12)",
      padding:      "14px 14px 8px",
      display:      "flex",
      flexDirection:"column",
      gap:          8,
      overflow:     "hidden",
    }}>
      <SectionLabel>India Grid Risk Map</SectionLabel>

      <div style={{ flex: 1, borderRadius: 8, overflow: "hidden", position: "relative" }}>
        <MapContainer
          center={[22.5, 80.0]}
          zoom={4}
          style={{ height: "100%", width: "100%", background: "#020817" }}
          zoomControl={false}
          attributionControl={false}
        >
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            className="dark-tiles"
          />

          <CustomZoom />

          {corridors.map(corridor => {
            const color  = getRiskColor(corridor.risk_percent);
            const weight = corridor.risk_percent > 60 ? 4 : corridor.risk_percent > 30 ? 3 : 2;
            const dashArray = corridor.risk_percent > 60 ? "8 4" : undefined;

            return (
              <Polyline
                key={corridor.id}
                positions={corridor.coords}
                pathOptions={{
                  color,
                  weight,
                  opacity:   0.85,
                  dashArray,
                }}
              >
                <Popup>
                  <CorridorPopup corridor={corridor} />
                </Popup>
              </Polyline>
            );
          })}
        </MapContainer>

        {/* Legend */}
        <div style={{
          position:      "absolute",
          bottom:        10, left: 10,
          zIndex:        1000,
          background:    "rgba(13,27,62,0.92)",
          border:        "1px solid rgba(0,212,255,0.2)",
          borderRadius:  8,
          padding:       "8px 10px",
          backdropFilter:"blur(10px)",
        }}>
          <div style={{
            fontSize: 9, color: "#546E7A",
            fontFamily: "Orbitron, sans-serif",
            letterSpacing: "0.1em", marginBottom: 6,
          }}>
            GIC RISK
          </div>
          {[
            ["Critical", "#EF5350"],
            ["High",     "#FF8F00"],
            ["Moderate", "#FDD835"],
            ["Low",      "#43A047"],
          ].map(([label, color]) => (
            <div key={label} style={{
              display: "flex", alignItems: "center", gap: 6,
              marginBottom: 3, fontSize: 10,
              color: "#90A4AE", fontFamily: "Space Grotesk, sans-serif",
            }}>
              <div style={{
                width: 20, height: 3, borderRadius: 2,
                background: color,
              }} />
              {label}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
});
