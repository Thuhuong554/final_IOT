#include <WiFi.h>
#include <Firebase_ESP_Client.h>
#include "addons/TokenHelper.h" 
#include "addons/RTDBHelper.h"
#include <Wire.h>
#include <LiquidCrystal_PCF8574.h>
#include <DHT.h>
#include "time.h" 

// ============================================================
// 1. CẤU HÌNH KẾT NỐI (Đã cập nhật WiFi mới)
// ============================================================
#define WIFI_SSID       "VNUK4-10"       
#define WIFI_PASSWORD   "Z@q12wsx"       
#define API_KEY         "AIzaSyD3oazhHgLWYJcHDwas6QxnULdG0v7YFK0"
#define DATABASE_URL    "https://smart-agriculture-f2f56-default-rtdb.asia-southeast1.firebasedatabase.app"
#define USER_EMAIL      "quan.tran220401@vnuk.edu.vn"
#define USER_PASSWORD   "test12345678"                    

// ============================================================
// 2. CẤU HÌNH PHẦN CỨNG
// ============================================================
LiquidCrystal_PCF8574 lcd(0x27); 

#define DHTPIN 25
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

// --- CHÂN CẢM BIẾN ---
#define SOIL_ANALOG_PIN  33  // A0
#define SOIL_DIGITAL_PIN 32  // D0

#define RELAY_PIN       13
#define RELAY_ON        HIGH 
#define RELAY_OFF       LOW 

// ============================================================
// 3. THIẾT LẬP LOGIC (ĐÃ SỬA: TỐI THIỂU 40%)
// ============================================================

// A. NGƯỠNG CẤP CỨU (CRITICAL):
// Nếu đất khô dưới 40% -> BẬT BƠM NGAY LẬP TỨC (AI không được phép cản)
const int CRITICAL_DRY_LEVEL = 40; 

// B. Ngưỡng Failsafe (Khi mất mạng):
const int FAILSAFE_DRY = 40; 
const int FAILSAFE_WET = 80; 

const char* ntpServer = "pool.ntp.org";
const long  gmtOffset_sec = 7 * 3600; 
const int   daylightOffset_sec = 0;

unsigned long lastDashboardUpdate = 0;
const unsigned long DASHBOARD_INTERVAL = 2000; 

unsigned long lastHistoryLog = 0;
const unsigned long HISTORY_INTERVAL = 900000;  

// Biến Firebase
FirebaseData fbdo;
FirebaseAuth auth;
FirebaseConfig config;

// Biến trạng thái
bool pumpState = false;      
float t = 0;
float h = 0;
int soilPercent = 0;
int soilDigitalState = 1;

// Biến AI
String aiDecision = "OFFLINE"; 
float aiThreshold = 0.0;       
float predictedSoil = 0.0;     

// ============================================================
// 4. CÁC HÀM HỖ TRỢ
// ============================================================

String getCurrentTime() {
  struct tm timeinfo;
  if(!getLocalTime(&timeinfo)) return "N/A";
  char timeBuffer[30];
  strftime(timeBuffer, sizeof(timeBuffer), "%Y-%m-%d %H:%M:%S", &timeinfo);
  return String(timeBuffer);
}

void pushHistoryLog(float t, float h, int s, bool p) {
  if (!Firebase.ready()) return;
  String timeStr = getCurrentTime();
  if (timeStr == "N/A") return; 

  FirebaseJson json;
  json.set("temperature", t);
  json.set("humidity", h);
  json.set("soilPercent", s);
  json.set("pumpState", p ? 1 : 0); 
  json.set("timestamp", timeStr);
  Firebase.RTDB.pushJSON(&fbdo, "/sensors/greenhouse_1/history_logs", &json);
}

void pushPumpEvent(bool state) {
  if (!Firebase.ready()) return;
  FirebaseJson json;
  json.set("event", state ? "PUMP_ON" : "PUMP_OFF");
  json.set("timestamp", getCurrentTime());
  Firebase.RTDB.pushJSON(&fbdo, "/sensors/greenhouse_1/pump_events", &json);
}

void fetchAIData() {
  if (Firebase.ready()) {
    if (Firebase.RTDB.getJSON(&fbdo, "/sensors/greenhouse_1/live_status")) {
      FirebaseJson &json = fbdo.jsonObject();
      FirebaseJsonData result;

      json.get(result, "ai_last_decision");
      if (result.success) aiDecision = result.to<String>();

      json.get(result, "ai_dynamic_threshold");
      if (result.success) {
        float val = result.to<float>();
        if (val <= 1.0) val *= 100;
        aiThreshold = val;
      }
      
      Serial.println("SYNC: " + aiDecision + " | Thr: " + String(aiThreshold));
    }
  } else {
    aiDecision = "OFFLINE";
  }
}

// ============================================================
// 5. SETUP
// ============================================================
void setup() {
  Serial.begin(115200);
  analogReadResolution(12); 
  
  pinMode(SOIL_ANALOG_PIN, INPUT);
  pinMode(SOIL_DIGITAL_PIN, INPUT);
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, RELAY_OFF);

  Wire.begin(26, 27); 
  lcd.begin(16, 2);
  lcd.setBacklight(255);
  lcd.print("System Booting..");

  dht.begin();
  
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 10000) {
    delay(300); Serial.print(".");
  }
  
  configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);

  config.api_key = API_KEY;
  config.database_url = DATABASE_URL;
  auth.user.email = USER_EMAIL;
  auth.user.password = USER_PASSWORD;
  config.token_status_callback = tokenStatusCallback; 
  
  Firebase.begin(&config, &auth);
  Firebase.reconnectWiFi(true);
  
  // --- MỚI: RESET TRẠNG THÁI AI KHI KHỞI ĐỘNG ---
  // Để tránh việc ESP32 nhận lệnh "WAIT" cũ gây tắt bơm khi vừa bật
  if (Firebase.ready()) {
      FirebaseJson jsonReset;
      jsonReset.set("ai_last_decision", "OFFLINE");
      jsonReset.set("ai_dynamic_threshold", 0);
      Firebase.RTDB.updateNode(&fbdo, "/sensors/greenhouse_1/live_status", &jsonReset);
      aiDecision = "OFFLINE"; 
      Serial.println(">> AI Status Reset to OFFLINE");
  }
  
  lcd.clear(); // Xóa màn hình một lần duy nhất lúc khởi động
}

// ============================================================
// 6. LOOP
// ============================================================
void loop() {
  // --- 1. ĐỌC CẢM BIẾN ---
  float newT = dht.readTemperature();
  float newH = dht.readHumidity();
  if (!isnan(newT)) t = newT;
  if (!isnan(newH)) h = newH;

  long totalADC = 0;
  for (int i = 0; i < 10; i++) {
    totalADC += analogRead(SOIL_ANALOG_PIN); delay(5);
  }
  // Map ADC (Thực tế: 3800 khô, 1500 ướt)
  int soilPercentRaw = map(totalADC / 10, 3800, 1500, 0, 100); 
  soilPercent = constrain(soilPercentRaw, 0, 100); 
  soilDigitalState = digitalRead(SOIL_DIGITAL_PIN);

  // --- 2. LOGIC ĐIỀU KHIỂN (ƯU TIÊN BẢO VỆ CÂY) ---
  bool requestOn = false;
  bool requestOff = false;

  // LỚP 1: BẢO VỆ TUYỆT ĐỐI (Dưới 40% -> TƯỚI NGAY)
  // Logic này chạy trước, đè lên mọi lệnh của AI
  if (soilPercent < CRITICAL_DRY_LEVEL) {
    requestOn = true;
    Serial.println("!!! LOW SOIL (<40%) -> FORCE PUMP ON !!!");
  } 
  // LỚP 2: AI HOẶC WIFI
  else {
    // Chỉ khi đất > 40% thì mới nghe lệnh AI
    if (aiDecision == "IRRIGATE") {
      requestOn = true; 
    } 
    else if (aiDecision == "WAIT") {
      requestOff = true; 
    } 
    else { 
      // Mất mạng / Chưa có lệnh AI (OFFLINE)
      // Dưới 40% đã được hứng ở Lớp 1, nên ở đây chỉ lo việc tắt
      if (soilPercent > FAILSAFE_WET) requestOff = true;
    }
  }

  // --- 3. THỰC THI RELAY ---
  if (requestOn && !pumpState) {
    digitalWrite(RELAY_PIN, RELAY_ON);
    pumpState = true;
    pushPumpEvent(true);
    Serial.println("-> PUMP ON");
  } 
  else if (requestOff && pumpState) {
    // Chỉ tắt khi đất đã an toàn (>40%)
    if (soilPercent >= CRITICAL_DRY_LEVEL) {
        digitalWrite(RELAY_PIN, RELAY_OFF);
        pumpState = false;
        pushPumpEvent(false);
        Serial.println("-> PUMP OFF");
    }
  }

  // --- 4. GIAO DIỆN LCD (KHÔNG DÙNG CLEAR ĐỂ TRÁNH NHÁY) ---
  unsigned long currentMillis = millis();
  if (currentMillis - lastDashboardUpdate > DASHBOARD_INTERVAL) {
    lastDashboardUpdate = currentMillis;

    // Dòng 1: H:60 T:30 S:40%
    lcd.setCursor(0, 0);
    // Cộng thêm khoảng trắng cuối chuỗi để xóa ký tự thừa cũ mà không cần lcd.clear()
    String line1 = "H:" + String(h, 0) + " T:" + String(t, 0) + " S:" + String(soilPercent) + "%   ";
    lcd.print(line1);
    
    // Dòng 2: Trạng thái
    lcd.setCursor(0, 1);
    String pStr = pumpState ? "ON " : "OFF";
    String line2 = "";

    if (soilPercent < CRITICAL_DRY_LEVEL) {
        // Báo hiệu đang ở chế độ cấp cứu (dưới 40%)
        line2 = "P:" + pStr + " [LOW <40%] "; 
    } else if (aiThreshold > 0) {
        // Báo hiệu đang chạy theo ngưỡng AI
        line2 = "P:" + pStr + " AI:" + String(aiThreshold, 0) + "% ";
    } else {
        // Báo hiệu trạng thái chờ hoặc mất mạng
        String status = (aiDecision == "OFFLINE") ? "LOST" : "WAIT";
        line2 = "P:" + pStr + " AI:" + status + "   ";
    }
    lcd.print(line2);

    // Gửi dữ liệu Firebase
    if (Firebase.ready()) {
       FirebaseJson jsonLive;
       jsonLive.set("temperature", t);
       jsonLive.set("humidity", h);
       jsonLive.set("soilPercent", soilPercent);
       jsonLive.set("soilDigital", soilDigitalState);
       jsonLive.set("pumpState", pumpState ? 1 : 0);
       jsonLive.set("timestamp", getCurrentTime());
       
       Firebase.RTDB.updateNode(&fbdo, "/sensors/greenhouse_1/live_status", &jsonLive);
       fetchAIData(); 
    }
    // Debug
    Serial.printf("S: %d%% | D0: %d | Pump: %d\n", soilPercent, soilDigitalState, pumpState);
  }

  // --- 5. LOG LỊCH SỬ ---
  if (currentMillis - lastHistoryLog > HISTORY_INTERVAL) {
    lastHistoryLog = currentMillis;
    pushHistoryLog(t, h, soilPercent, pumpState);
  }
}
