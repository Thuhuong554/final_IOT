# AI-Enhanced Smart Irrigation System 🌱💧

## Overview
This project presents an **AI-driven smart irrigation system** designed for precision agriculture.  
Unlike traditional timer-based irrigation, the system integrates **Vapor Pressure Deficit (VPD)**, **machine learning forecasting**, and **real-time diagnostics** to make proactive and adaptive watering decisions.

The system follows a **closed-loop, cloud–edge hybrid architecture**, where safety-critical logic runs on the ESP32 edge device, while predictive intelligence is handled by a cloud-based backend.

---

## Key Features
- **ESP32-based IoT edge node** with safety-first firmware logic  
- **Random Forest regression model** forecasting soil moisture 3 hours ahead  
- **VPD-based dynamic irrigation threshold** (environment-adaptive policy)  
- **Three-layer decision architecture** (Prediction → Threshold → Controller)  
- **Firebase Realtime Database** for real-time synchronization  
- **Web dashboard** for monitoring, diagnostics, and AI decision explanation  
- **Built-in system diagnostics** for pump and sensor failure detection  

---

## System Architecture
The architecture is divided into four layers:

### 1. IoT Layer (Edge)
- **Device**: ESP32-WROOM-32  
- **Sensors**:
  - DHT11 (Temperature & Humidity)
  - Capacitive Soil Moisture Sensor v1.2  
- **Actuator**: Relay-controlled water pump  
- **Responsibilities**:
  - Sensor data acquisition
  - Local safety overrides
  - Pump control
  - Offline failsafe operation

### 2. Cloud Layer (Data Bus)
- **Platform**: Firebase Realtime Database  
- **Role**:
  - Store live telemetry
  - Maintain historical logs
  - Exchange AI decisions between edge and backend

### 3. Intelligence Layer (Backend)
- **Framework**: Python FastAPI  
- **Functions**:
  - Data preprocessing and feature engineering
  - VPD calculation
  - Machine learning inference
  - Decision generation (IRRIGATE / WAIT)

### 4. Presentation Layer (Frontend)
- **Technologies**: HTML5, Bootstrap 5, Chart.js  
- **Capabilities**:
  - Real-time sensor visualization
  - VPD stress indication
  - AI decision explanation
  - Historical data analysis

---

## Machine Learning Pipeline
### Model
- **Algorithm**: Random Forest Regressor  
- **Task**: Predict soil moisture 3 hours into the future  
- **Features**:
  - Soil moisture lags (1h, 3h, 6h, 12h, 24h, 48h)
  - Soil moisture deltas
  - Rolling statistics
  - Temperature, humidity, VPD
  - Time encoding (sin/cos hour-of-day)
  - Irrigation history  

### Performance
- **MAE** ≈ 0.0079  
- **RMSE** ≈ 0.015  
- **Prediction accuracy** ≈ 95%

> The AI model **only forecasts** future soil conditions — it does not make final decisions.

---

## Three-Layer Decision Architecture
1. **Layer 1 — AI Prediction**  
   Forecasts soil moisture 3 hours ahead.

2. **Layer 2 — Dynamic Threshold (VPD-based)**  
   Adjusts target soil moisture depending on atmospheric dryness:
   - Low VPD → lower threshold
   - High VPD → higher threshold (early irrigation)

3. **Layer 3 — Decision Controller**  
   Combines:
   - Current soil moisture
   - Forecasted soil moisture
   - Dynamic threshold
   - Cooldown constraints  

   Produces final actions:
   - `IRRIGATE`
   - `WAIT`

---

## Firmware Safety Logic (Defense-in-Depth)
The ESP32 firmware prioritizes crop survival:

1. **Critical Safety Layer**  
   - If soil moisture < 40% → Force irrigation (bypass AI & network)

2. **AI-Controlled Layer**  
   - Uses Firebase AI decisions during normal operation

3. **Offline Failsafe Layer**  
   - Hysteresis control when network or backend is unavailable

---

## System Diagnostics
The system includes self-diagnostic logic:
- **Pump failure detection**
- **Sensor anomaly / leak detection**

Detected issues are visualized on the frontend dashboard as alerts.

---

## Results
- **Controller decision consistency**: ~95%  
- **Water efficiency**: Reduced waste during low-VPD periods  
- **Reliability**: Maintained operation during simulated network failures  
- **Classification F1-score**: 0.96  
- **Recall (dry events)**: 0.94  

---

## Limitations
- Limited dataset size (seasonal effects not fully captured)
- Power instability when pump and LCD operate simultaneously
- VPD thresholds currently hardcoded

---

## Future Work
- Adaptive threshold learning (PID-based or reinforcement learning)
- Larger and seasonal datasets
- Improved power management hardware
- Crop-specific model specialization

---

## Authors
- **Dương Thu Hương**  
- **Trần Đặng Hải Quân**

University of Danang – VN-UK Institute for Research and Executive Education  
Course: *Internet of Things and Applications*

---

## License
This project is developed for academic purposes.
