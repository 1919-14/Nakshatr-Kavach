import { create } from "zustand";
import {
  MOCK_SOLAR_WIND, MOCK_KP_FORECAST, MOCK_KP_CHART_DATA,
  MOCK_SATELLITES, MOCK_GRID_CORRIDORS, MOCK_ADVISORY,
} from "../mock/mockData";

const USE_MOCK = import.meta.env.VITE_USE_MOCK_DATA === "true";

export const useStormStore = create((set, get) => ({
  // ── Data ──────────────────────────────────────────────────────────────────
  solarWind:     USE_MOCK ? MOCK_SOLAR_WIND     : null,
  kpForecast:    USE_MOCK ? MOCK_KP_FORECAST    : null,
  kpChartData:   USE_MOCK ? MOCK_KP_CHART_DATA  : [],
  satellites:    USE_MOCK ? MOCK_SATELLITES      : [],
  gridCorridors: USE_MOCK ? MOCK_GRID_CORRIDORS  : [],
  advisory:      USE_MOCK ? MOCK_ADVISORY        : null,
  shapExplain:   null,

  // ── System status ─────────────────────────────────────────────────────────
  systemStatus: {
    noaa:   "online",   // "online" | "degraded" | "offline"
    ml:     "online",
    llm:    "online",
  },

  // ── UI state ──────────────────────────────────────────────────────────────
  selectedSatellite:     null,
  alertOverlayVisible:   false,
  alertDismissed:        false,
  replayMode:            false,
  replayState:           "IDLE",
  replayProgress:        0,
  replayCurrentFrame:    0,
  replayTotalFrames:     0,
  replayStormName:       null,
  isReplayMode:          false,
  replayStormId:         null,
  replayFrameIndex:      0,
  replayPlaying:         false,
  replaySpeed:           60,    // 1× | 60× | 3600×
  advisoryExpanded:      false,
  selectedNavTab:        "dashboard",

  // ── Derived ───────────────────────────────────────────────────────────────
  get currentKp() {
    return get().solarWind?.kp_current ?? 0;
  },
  get stormActive() {
    return (get().solarWind?.kp_current ?? 0) >= 5;
  },

  // ── Actions ───────────────────────────────────────────────────────────────
  setSolarWind: (d) => set(state => ({ solarWind: typeof d === "function" ? d(state.solarWind) : d })),
  setKpForecast:    (d)  => set({ kpForecast: d }),
  setKpChartData:   (d)  => set({ kpChartData: d }),
  setSatellites:    (d)  => set({ satellites: d }),
  setGridCorridors: (d)  => set({ gridCorridors: d }),
  setAdvisory:      (d)  => set({ advisory: d }),
  setShapExplain:   (d)  => set({ shapExplain: d }),
  setSystemStatus:  (d)  => set(state => ({ systemStatus: typeof d === "function" ? d(state.systemStatus) : d })),

  selectSatellite:  (id) => set({ selectedSatellite: id }),
  clearSatellite:   ()   => set({ selectedSatellite: null }),

  showAlertOverlay: ()   => set({ alertOverlayVisible: true,  alertDismissed: false }),
  hideAlertOverlay: ()   => set({ alertOverlayVisible: false, alertDismissed: true  }),

  setReplayMode:    (v)  => set({ replayMode: Boolean(v), isReplayMode: Boolean(v) }),
  setReplayStorm:   (id) => set({
    replayStormId: id,
    replayFrameIndex: 0,
    replayCurrentFrame: 0,
    replayProgress: 0,
  }),
  setReplayStatus:  (status = {}) => set((state) => {
    const nextState = status.state ?? status.new_state ?? state.replayState;
    const currentFrame =
      status.current_frame ?? status.frame ?? status.frame_index ?? state.replayCurrentFrame;
    const totalFrames = status.total_frames ?? status.total ?? state.replayTotalFrames;
    const progress = status.progress_pct ??
      (totalFrames ? Math.round((currentFrame / totalFrames) * 1000) / 10 : state.replayProgress);
    const active = nextState && nextState !== "IDLE";
    return {
      replayMode: Boolean(active),
      isReplayMode: Boolean(active),
      replayState: nextState,
      replaySpeed: status.speed ?? state.replaySpeed,
      replayProgress: progress,
      replayCurrentFrame: currentFrame,
      replayFrameIndex: currentFrame,
      replayTotalFrames: totalFrames,
      replayStormName: status.storm_name ?? status.replayStormName ?? state.replayStormName,
      replayPlaying: nextState === "PLAYING" ? true : nextState === "PAUSED" || nextState === "IDLE" ? false : state.replayPlaying,
    };
  }),
  setReplayFrame:   (frame)  => set((state) => {
    if (typeof frame === "number") {
      return { replayFrameIndex: frame, replayCurrentFrame: frame };
    }
    const frameIndex = frame?.frame_index ?? state.replayCurrentFrame;
    const totalFrames = frame?.total_frames ?? state.replayTotalFrames;
    return {
      replayMode: Boolean(frame?.is_historical ?? true),
      isReplayMode: Boolean(frame?.is_historical ?? true),
      replayState: frame?.state ?? state.replayState,
      replayProgress: frame?.progress_pct ?? state.replayProgress,
      replayCurrentFrame: frameIndex,
      replayFrameIndex: frameIndex,
      replayTotalFrames: totalFrames,
      replaySpeed: frame?.speed ?? state.replaySpeed,
    };
  }),
  setReplayPlaying: (v)  => set({ replayPlaying: v }),
  setReplaySpeed:   (v)  => set({ replaySpeed: v }),

  setAdvisoryExpanded: (v) => set({ advisoryExpanded: v }),
  setNavTab:           (v) => set({ selectedNavTab: v }),

  // Simulate live random-walk data updates
  tickMockData: () => {
    const sw = get().solarWind;
    if (!sw || !USE_MOCK) return;
    set({
      solarWind: {
        ...sw,
        bz_gsm:        parseFloat((sw.bz_gsm + (Math.random()-0.5)*0.8).toFixed(1)),
        sw_speed:      Math.round(sw.sw_speed + (Math.random()-0.5)*12),
        proton_density:parseFloat((sw.proton_density + (Math.random()-0.5)*0.4).toFixed(1)),
        kp_current:    parseFloat(Math.min(9, Math.max(0, sw.kp_current + (Math.random()-0.5)*0.15)).toFixed(1)),
        timestamp:     new Date().toISOString(),
      },
    });
  },
}));
