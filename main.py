import os
import joblib
import datetime
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import firebase_admin
from firebase_admin import credentials, db

# 1. CONFIGURATION & SETUP
load_dotenv()
DB_URL = os.getenv("FIREBASE_DB_URL")
CRED_PATH = os.getenv("FIREBASE_CRED_PATH")
MODEL_DIR = os.getenv("MODEL_DIR")
MODEL_NAME = os.getenv("MODEL_NAME")

# --- DYNAMIC IRRIGATION POLICY CONFIGURATION ---
POLICY_CONFIG = {
    "thr_lo": 0.65,   # Low threshold
    "thr_mid": 0.70,  # Mid threshold
    "thr_hi": 0.72,   # High threshold
    "margin": 0.005,  # Safety margin
    "vpd_low": 0.75,
    "vpd_high": 1.1
}

if not DB_URL or not CRED_PATH:
    raise RuntimeError("Error: Missing Firebase configuration variables in .env file.")

app = FastAPI(title="Smart Garden Control API", version="3.4.2 (Smart Logging)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Firebase
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(CRED_PATH)
        firebase_admin.initialize_app(cred, {'databaseURL': DB_URL})
        print(f"System: Connected to Firebase at {DB_URL}")
    except Exception as e:
        print(f"System Error: Firebase connection failed - {e}")

# 2. AI MODEL LOADER
ai_resources = {}

def load_ai_models():
    """Loads the .pkl model and auto-detects features."""
    try:
        pkl_path = os.path.join(MODEL_DIR, MODEL_NAME if MODEL_NAME else "soil_model_v1.pkl")
        model = joblib.load(pkl_path)
        ai_resources['model'] = model
        
        if hasattr(model, "feature_names_in_"):
            ai_resources['features'] = list(model.feature_names_in_)
            print(f"Model loaded. Auto-detected features: {ai_resources['features']}")
        else:
            print("Model does not store feature names. Using default fallback.")
            ai_resources['features'] = ["VPD_kPa", "soil_moisture_frac", "temperature_C", "humidity_RH"] 

        print("System: AI Models loaded successfully.")
    except Exception as e:
        print(f"System Error: Failed to load .pkl model - {e}")

load_ai_models()

# 3. HELPER FUNCTIONS

def calculate_vpd(temp_c, rh_percent):
    """Calculates Vapor Pressure Deficit (VPD) in kPa."""
    if temp_c is None or rh_percent is None: return 0.0
    svp = 0.6108 * np.exp((17.27 * temp_c) / (temp_c + 237.3))
    vpd = svp * (1 - (rh_percent / 100.0))
    return max(0.0, vpd)

def compute_dynamic_threshold(vpd, config):
    """Determines the irrigation threshold based on current VPD."""
    if vpd < config["vpd_low"]: return config["thr_lo"]
    elif vpd < config["vpd_high"]: return config["thr_mid"]
    else: return config["thr_hi"]

def process_historical_data(hist_df: pd.DataFrame, feature_list: list) -> pd.DataFrame:
    """Preprocesses data to match exactly what the .pkl model expects."""
    df = hist_df.copy()
    rename_map = {'temperature': 'temperature_C', 'humidity': 'humidity_RH', 'temp': 'temperature_C', 'humid': 'humidity_RH'}
    df.rename(columns=rename_map, inplace=True)
    
    if 'soil_moisture_frac' not in df.columns:
        if 'soilPercent' in df.columns:
            df['soil_moisture_frac'] = df['soilPercent'] / 100.0
        elif 'soil_moisture_percent' in df.columns:
            df['soil_moisture_frac'] = df['soil_moisture_percent']

    if 'timestamp' not in df.columns: df = df.reset_index()
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp')

    df['VPD_kPa'] = df.apply(lambda row: calculate_vpd(row.get('temperature_C'), row.get('humidity_RH')), axis=1)

    df["hour"] = df["timestamp"].dt.hour
    df["sin_hour"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["cos_hour"] = np.cos(2 * np.pi * df["hour"] / 24)

    for k in [1, 3, 6, 12, 24, 48]:
        df[f"sm_lag{k}"] = df["soil_moisture_frac"].shift(k)
    for w in [3, 6, 12, 24]:
        df[f"sm_roll{w}"] = df["soil_moisture_frac"].rolling(w).mean()
    df["sm_diff1"] = df["soil_moisture_frac"].diff(1)
    df["sm_diff3"] = df["soil_moisture_frac"].diff(3)

    try:
        last_row = df.iloc[[-1]][feature_list]
    except KeyError:
        missing_cols = list(set(feature_list) - set(df.columns))
        for col in missing_cols: df[col] = 0
        last_row = df.iloc[[-1]][feature_list]

    if last_row.isna().any().any():
        last_row = last_row.fillna(method='ffill').fillna(0)
    return last_row

def validate_data_freshness(data_dict):
    """Checks if data is recent (< 120 seconds)."""
    if not data_dict or 'timestamp' not in data_dict: return False, None
    try:
        last_update = pd.to_datetime(data_dict['timestamp'])
        diff = (datetime.datetime.now() - last_update).total_seconds()
        return (True, diff) if diff < 120 else (False, diff)
    except: return False, 9999

# --- AI DOCTOR MODULE ---
def evaluate_system_health(current_soil_frac, pump_state, ai_pred_frac):
    """
    Compares real-time data against AI predictions to detect anomalies.
    """
    alerts = []
    status = "NORMAL"
    MARGIN_ERROR = 0.10 # 10% Margin
    
    deviation = (current_soil_frac - ai_pred_frac) * 100

    if pump_state:
        # PUMP ON: Soil should be wetting
        if current_soil_frac <= (ai_pred_frac - MARGIN_ERROR):
             alerts.append(f"Critical: Pump ON but soil dry ({current_soil_frac*100:.1f}% vs Pred {ai_pred_frac*100:.1f}%)")
             status = "PUMP_FAIL"
    else:
        # PUMP OFF: Soil drying too fast?
        if (ai_pred_frac - current_soil_frac) > MARGIN_ERROR:
             alerts.append(f"Warning: High evaporation/Leak detected (Dev: {deviation:.1f}%)")
             status = "SENSOR_FAIL"
    
    return status, alerts, deviation

# 4. API ENDPOINTS

@app.get("/api/v1/sensors/live")
def get_live_status():
    """Returns live sensor data with CONNECTIVITY CHECK."""
    try:
        data = db.reference('/sensors/greenhouse_1/live_status').get()
        
        is_fresh, diff = validate_data_freshness(data)
        if not is_fresh:
            return {
                "status": "OFFLINE",
                "message": "Device disconnected",
                "last_seen_seconds": int(diff) if diff else -1
            }
        
        return data if data else {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/sensors/history")
def get_history(limit: int = 20):
    try:
        ref = db.reference('/sensors/greenhouse_1/history_logs')
        snapshot = ref.order_by_key().limit_to_last(limit).get()
        if not snapshot: return []
        data = [dict(v, id=k) for k, v in snapshot.items()]
        return data
    except Exception as e:
        return []

@app.get("/api/v1/system/diagnostics")
def get_system_diagnostics():
    """NEW API: AI Doctor."""
    if 'model' not in ai_resources:
        raise HTTPException(status_code=503, detail="AI Model service not initialized")

    try:
        ref_hist = db.reference('/sensors/greenhouse_1/history_logs')
        snapshot = ref_hist.order_by_key().limit_to_last(60).get()
        live_data = db.reference('/sensors/greenhouse_1/live_status').get() or {}

        is_fresh, _ = validate_data_freshness(live_data)
        if not is_fresh:
             return {"health_status": "OFFLINE", "alerts": ["System offline"], "deviation_percent": 0}

        if not snapshot or len(snapshot) < 10:
             return {"health_status": "WAITING_DATA", "alerts": []}

        df = pd.DataFrame(list(snapshot.values()))
        input_row = process_historical_data(df, ai_resources['features'])
        y_pred = float(ai_resources['model'].predict(input_row)[0])

        curr_soil = float(live_data.get('soilPercent', 0)) / 100.0
        curr_pump = bool(live_data.get('pumpState', 0))

        status, alerts, deviation = evaluate_system_health(curr_soil, curr_pump, y_pred)

        return {
            "health_status": status,
            "alerts": alerts,
            "deviation_percent": round(deviation, 2),
            "ai_expected_soil": round(y_pred * 100, 2),
            "real_current_soil": round(curr_soil * 100, 2),
            # FORMAT DATE HERE: YYYY-MM-DD HH:MM:SS
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    except Exception as e:
        print(f"Diagnostic Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/irrigation/decision")
def determine_irrigation_action():
    if 'model' not in ai_resources:
        raise HTTPException(status_code=503, detail="AI Model service not initialized")

    try:
        ref_hist = db.reference('/sensors/greenhouse_1/history_logs')
        snapshot = ref_hist.order_by_key().limit_to_last(60).get()
        live_data = db.reference('/sensors/greenhouse_1/live_status').get() or {}

        is_fresh, _ = validate_data_freshness(live_data)
        if not is_fresh:
            return {"decision": "OFFLINE", "reason": "Lost connection to device"}

        if not snapshot or len(snapshot) < 10:
             return {"decision": "WAIT", "reason": "Collecting data..."}

        df = pd.DataFrame(list(snapshot.values()))
        input_row = process_historical_data(df, ai_resources['features'])
        
        curr_temp = float(live_data.get('temperature', 25))
        curr_humid = float(live_data.get('humidity', 70))
        curr_soil = float(live_data.get('soilPercent', 0)) / 100.0
        
        current_vpd = calculate_vpd(curr_temp, curr_humid)
        dynamic_thr = compute_dynamic_threshold(current_vpd, POLICY_CONFIG)
        margin = POLICY_CONFIG["margin"]
        
        y_pred = float(ai_resources['model'].predict(input_row)[0])
        
        effective_threshold = dynamic_thr - margin
        
        # --- HUMAN READABLE REASONING (FRIENDLY ENGLISH) ---
        pred_pct = int(y_pred * 100)
        thr_pct = int(effective_threshold * 100)
        
        decision = "WAIT"
        # Friendly Reason
        reason = f"Moisture Stable (Forecast {pred_pct}% >= Limit {thr_pct}%)"

        if y_pred < effective_threshold:
            decision = "IRRIGATE"
            reason = f"Soil Drying Out (Forecast {pred_pct}% < Limit {thr_pct}%)"
        
        if curr_soil < 0.50:
            decision = "IRRIGATE"
            reason = f"Emergency: Soil Critically Dry ({int(curr_soil*100)}%)"

        update_payload = {
            "ai_vpd_kpa": round(current_vpd, 3),
            "ai_dynamic_threshold": round(dynamic_thr, 3),
            "ai_forecast_soil": round(y_pred, 3),
            "ai_last_decision": decision,
            # FORMAT DATE HERE: YYYY-MM-DD HH:MM:SS
            "ai_last_update": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # 1. ALWAYS UPDATE LIVE STATUS (For Dashboard)
        db.reference('/sensors/greenhouse_1/live_status').update(update_payload)

        # 2. SMART LOGGING: Only push to logs if Decision or Forecast changes
        prev_decision = live_data.get('ai_last_decision')
        prev_forecast = live_data.get('ai_forecast_soil')
        
        # Handle case where prev_forecast is None (first run)
        try:
            prev_forecast_val = float(prev_forecast) if prev_forecast is not None else -1.0
        except (ValueError, TypeError):
            prev_forecast_val = -1.0

        # Check: Did decision change? OR Did forecast change significantly?
        # We compare rounded values to match what we store
        if (decision != prev_decision) or (round(y_pred, 3) != prev_forecast_val):
            db.reference('/sensors/greenhouse_1/decision_logs').push(update_payload)
        
        return {
            "decision": decision,
            "vpd_kpa": round(current_vpd, 3),
            "dynamic_threshold": round(dynamic_thr, 3),
            "forecast_soil": round(y_pred, 3),
            "reason": reason,
            "margin": margin
        }

    except Exception as e:
        print(f"System Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))