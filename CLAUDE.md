# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**NAKSHATRA-KAVACH** (Sanskrit: "Star Shield") is a real-time, AI-powered Space Weather Impact Intelligence Platform for ISRO Asset Protection. It monitors solar activity, predicts geomagnetic storm intensity (Kp index), assesses vulnerability of India's satellite fleet, evaluates India's power grid GIC risk, and generates automated mission-control advisories via Groq LLM.

**Key Numbers:** 45-minute warning window, 24-hour prediction horizon, 50+ ISRO satellites monitored, 3-day forecast capability, 8-layer ML pipeline.

## Build & Run Commands

### Prerequisites
- Python 3.11+
- Node.js 18+
- npm or yarn

### Backend Setup
```bash
cd backend
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
python run.py                   # Starts Flask at http://localhost:5000
```

### Frontend Setup
```bash
cd frontend
npm install
npm run dev                     # Dashboard at http://localhost:5173
```

### Docker (Full Stack)
```bash
docker-compose up --build       # Dashboard: localhost:3000, API: localhost:5000
```

### ML Training (Optional — checkpoints included)
```bash
cd ml_training
python 01_data_download.py     # Downloads ~500MB OMNI + Kp data
python 03_train_xgboost.py      # ~5-10 min on CPU
python 04_train_lstm.py         # ~30-60 min on CPU
python 05_evaluate_models.py
```

## Architecture

### 8-Layer Intelligence Pipeline

The system is a layered pipeline where each layer has a defined input/output contract:

```
Layer 1: Real-Time Data Ingestion (APScheduler every 60s)
    → NOAA SWPC, NASA DONKI, GOES XRS, Celestrak TLE
    → Normalized solar wind dataframe

Layer 2: Feature Engineering (45 ML-ready features)
    → Bz duration metrics, Epsilon coupling, Dynamic pressure
    → Rolling windows (30min/1hr/3hr/6hr), CME metadata

Layer 3: Kp Prediction Engine (XGBoost + LSTM Hybrid)
    → 3hr/6hr: 70% XGBoost + 30% LSTM
    → 12hr/24hr: 20% XGBoost + 80% LSTM
    → Monte Carlo Dropout for uncertainty quantification

Layer 4: Satellite Vulnerability Scorer
    → 3 kill mechanisms: Atmospheric drag (LEO), Surface charging (GEO), Radiation SEU
    → Per-satellite composite risk score (0-100)

Layer 5: India Power Grid GIC Risk Engine
    → Viljanen-Pirjola GIC estimation model
    → 6+ EHV transmission corridors (765kV/400kV)

Layer 6: LLM Mission Advisory (Groq LLaMA-3.3-70B)
    → ISRO/NDMA communication style with Hindi translation
    → Rule-based fallback for 100% uptime

Layer 7: Historical Storm Replay Engine
    → Pre-loaded: 1989 Quebec, 2003 Halloween, 2022 Starlink, 2024 G5
    → Replay speeds: 1x / 60x / 3600x

Layer 8: Mission Control Dashboard (React + Three.js)
```

### Data Flow
```
NOAA/NASA APIs → APScheduler (60s poll) → Feature Engineering → ML Inference
→ Satellite Scorer → Grid Risk Engine → LLM Advisory → SQLite Cache
→ WebSocket Push → React Dashboard
```

### Tech Stack

**Frontend:** React 18+, Three.js r155+, Recharts, react-leaflet, Tailwind CSS 3+, Socket.IO Client, Framer Motion, GSAP, Zustand, TanStack Query

**Backend:** Python 3.11+, Flask 3.x, Flask-SocketIO, APScheduler, SQLite/PostgreSQL, Celery + Redis

**ML/Science:** TensorFlow/Keras (LSTM), XGBoost (tabular), scikit-learn, SHAP, Skyfield (satellite TLE), Poliastro (orbital mechanics), Astropy, SpacePy

**External APIs:** NOAA SWPC, NASA DONKI, DSCOVR/RTSW, GFZ Potsdam, Celestrak, Groq (LLaMA-3.3-70B)

### Key API Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/solar/live` | Current solar wind (Bz, Bt, speed, density) |
| GET | `/api/kp/forecast` | Kp predictions 3/6/12/24hr with uncertainty |
| GET | `/api/satellites/risk` | Risk scores for all ISRO satellites |
| GET | `/api/grid/risk` | GIC risk per transmission corridor |
| GET | `/api/advisory/latest` | Latest LLM-generated advisory |
| GET | `/api/history/{storm_id}` | Historical storm data for replay |
| WS | `/realtime` | WebSocket: continuous push every 60s |

## Project Structure

```
nakshatra-kavach/
├── backend/
│   ├── app/
│   │   ├── routes/           # Flask API endpoints
│   │   │   ├── solar.py      # /api/solar/*
│   │   │   ├── satellites.py # /api/satellites/*
│   │   │   ├── grid.py       # /api/grid/*
│   │   │   ├── advisory.py   # /api/advisory/*
│   │   │   └── replay.py     # /api/history/*
│   │   ├── services/         # Core business logic (8 layers)
│   │   ├── models/           # Trained ML model files (.pkl, .h5)
│   │   └── data/             # Static JSON configs + historical CSVs
│   └── run.py
├── frontend/
│   ├── src/
│   │   ├── pages/            # Dashboard, StormSim, Satellites, GridMap, Replay, Advisory
│   │   ├── components/
│   │   │   ├── earth/        # Three.js Earth globe + satellite orbits + storm cone
│   │   │   ├── solar/        # Telemetry strip (6 live metrics)
│   │   │   ├── forecast/     # Kp forecast chart with confidence bands
│   │   │   ├── satellites/   # Risk cards panel
│   │   │   ├── grid/         # India GIC risk map (Leaflet)
│   │   │   ├── advisory/     # LLM advisory panel
│   │   │   ├── alerts/       # Storm overlay + edge glow animations
│   │   │   └── ui/           # Shared components
│   │   ├── store/            # Zustand global state
│   │   ├── hooks/            # Custom React hooks
│   │   └── mock/             # Mock data for development
│   └── public/textures/     # NASA Earth texture maps
├── ml_training/
│   ├── 01-05_*.py            # Data download, feature engineering, training scripts
│   └── notebooks/            # EDA and validation Jupyter notebooks
└── docs/
```

## Important Implementation Notes

### L1 Warning Window Physics
DSCOVR spacecraft orbits the Sun-Earth L1 Lagrange point (1.5M km from Earth). Solar wind transit time = 1,500,000 / solar_wind_speed_km_s / 60 minutes. This gives the physical warning window.

### Kp Prediction Strategy
- **XGBoost:** Tabular features, dominates short-term (3-6hr), fast on CPU
- **LSTM:** 24-hour sequence, dominates long-term (12-24hr), Monte Carlo Dropout for uncertainty
- **Fusion:** Weighted average that shifts from XGBoost to LSTM as horizon increases
- **Training Data:** NASA OMNI2 (1970-present) + GFZ Potsdam Kp (2005-2024), ~175K hourly samples
- **Test Set:** 2024 data held out — May 2024 G5 storm is the primary demo case

### Satellite Kill Mechanisms
1. **Atmospheric Drag (LEO):** Storm heats thermosphere → 3-100x density increase at 500km → orbit decay
2. **Surface Charging (GEO):** High-energy electrons cause ESD arcs → solar panel damage, electronics shorts
3. **Radiation SEUs (All Orbits):** SEP particles cause bit flips → software crashes, attitude control loss

### GIC Risk Model
GIC_amps = E_geo * corridor_length_km * sin(corridor_angle_from_NS) / line_resistance
Where E_geo = 10 * (Kp / 5)^2 V/km during storms

### Tiered Satellite Coverage
- **Tier 1 (12 satellites):** Deep individual profiling with 3 kill mechanism analysis
- **Tier 2 (40 satellites):** Auto-scored via Celestrak TLE orbital parameters
- **Tier 3 (80+ satellites):** Globe visualization only

## Environment Variables

```env
GROQ_API_KEY=your_groq_api_key_here    # Free at console.groq.com
FLASK_ENV=development
DATABASE_URL=sqlite:///nakshatra.db
VITE_API_URL=http://localhost:5000
VITE_USE_MOCK_DATA=true                # Set false for live backend
```

## Key Data Sources (All Free, All Government)

| Source | Data | Update |
|--------|------|--------|
| NOAA SWPC | Real-time Kp, solar wind | 1 min |
| NASA DONKI | CME catalog, flares | Event-driven |
| NOAA GOES XRS | X-ray flux (flare class) | 1 min |
| GFZ Potsdam | Historical Kp (1932-present) | 3 hr |
| NASA OMNI | Historical solar wind (ML training) | 1 hr |
| Celestrak | Satellite TLE elements | Daily |

## Design Principles

1. **No API keys required** except Groq (free tier available)
2. **No hardware dependency** — runs on standard laptop
3. **Graceful degradation** — shows cached data with staleness indicator if APIs fail
4. **Rule-based fallback** ensures 100% advisory availability if LLM is down
5. **Hackathon-friendly** — full MVP buildable in 24 hours, ML training optional (checkpoints provided)