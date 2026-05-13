# NAKSHATRA-KAVACH — Backend Connection Guide
## For the backend developer

---

## Step 1 — Enable real data

Open `.env` in the frontend project and change:
```
VITE_USE_MOCK_DATA=false
VITE_API_BASE=http://localhost:8000
```

---

## Step 2 — Add CORS to your FastAPI

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## Step 3 — Build these endpoints

### GET /api/solar-wind
```json
{
  "timestamp": "2024-05-10T14:32:00Z",
  "bz_gsm": -18.4,
  "bt_total": 28.7,
  "sw_speed": 720,
  "proton_density": 12.3,
  "xray_class": "M1.5",
  "kp_current": 7.2,
  "storm_class": "G3",
  "storm_active": true
}
```

### GET /api/kp-forecast
```json
{
  "kp_3hr":  { "value": 7.8, "uncertainty": 0.6 },
  "kp_6hr":  { "value": 8.2, "uncertainty": 0.9 },
  "kp_12hr": { "value": 7.1, "uncertainty": 1.2 },
  "kp_24hr": { "value": 5.3, "uncertainty": 1.8 },
  "peak_arrival_minutes": 42,
  "storm_probability": 0.87
}
```

### GET /api/satellite-risk
```json
[
  {
    "id": "insat-3dr",
    "name": "INSAT-3DR",
    "shortName": "INSAT",
    "type": "GEO",
    "altitude": 35786,
    "inclination": 1.5,
    "mission": "Weather forecasting",
    "drag_risk": 0,
    "charging_risk": 78,
    "radiation_risk": 45,
    "composite_risk": 74,
    "risk_level": "HIGH",
    "action": "Initiate safe mode in 35 minutes",
    "safe_mode_minutes": 35
  }
]
```

### Satellite IDs (use exactly these):
- `insat-3dr`
- `navic-irnss1i`
- `cartosat-3`
- `risat-2b`
- `eos-01`
- `gsat-30`
- `eos-06`
- `aditya-l1`

### GET /api/grid-risk
```json
[
  {
    "id": "rj-gj",
    "name": "Rajasthan-Gujarat 400kV",
    "states": "RJ-GJ",
    "voltage": "400kV",
    "coords": [[26.9, 73.9], [23.0, 72.6]],
    "gic_amps": 68,
    "risk_percent": 74,
    "impact_crore": 240,
    "population_millions": 4.2,
    "action": "Reduce load by 15%"
  }
]
```

### GET /api/advisory
```json
{
  "generated_at": "2024-05-10T14:35:00Z",
  "source": "AI_GENERATED",
  "sections": [
    {
      "title": "THREAT ASSESSMENT",
      "content": "Your LLM advisory text here..."
    }
  ],
  "hindi_summary": "हिंदी सारांश यहाँ..."
}
```

---

## Step 4 — Test connection

Run your backend then open: `http://localhost:5173`

The frontend polls every 60 seconds automatically.
