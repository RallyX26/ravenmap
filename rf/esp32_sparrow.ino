/*
 * SparrowMap RF sensor for ESP32 — a $5 always-on scanner.
 *
 * Scans the wifi it can see, keeps ONLY names that look like surveillance
 * cameras (Flock/ALPR) or police in-car gear (Axon, etc.), and uploads those to
 * SparrowMap. Every other network — homes, phones, ordinary cars — is counted
 * and forgotten on the chip. Nothing private ever leaves the device. Same rule
 * SparrowMap uses for licence plates.
 *
 * Nothing is published automatically: uploads land in a review pen and a human
 * confirms them before anything hits the public map.
 *
 * ── FLASH IT ────────────────────────────────────────────────────────────────
 * 1. Arduino IDE → Boards Manager → install "esp32" (Espressif).
 * 2. Select your board (e.g. "ESP32 Dev Module").
 * 3. Fill in the CONFIG block below (wifi to upload through + your node token).
 * 4. Get a node id + token first, from any computer:
 *      curl -X POST https://map.sparrowmap.com/api/enroll \
 *        -H "Content-Type: application/json" \
 *        -d '{"name":"My ESP32","lat":XX.XXXX,"lon":-YY.YYYY,"kind":"rf"}'
 * 5. Upload. Open Serial Monitor at 115200 to watch it work.
 *
 * Needs internet to upload, so it connects to a wifi network (home wifi or a
 * phone hotspot) AND scans the airwaves around it at the same time. For a
 * moving sensor, tether it to a phone hotspot.
 */

#include <WiFi.h>
#include <HTTPClient.h>

// ── CONFIG ───────────────────────────────────────────────────────────────────
const char* WIFI_SSID = "your-wifi";        // network to upload THROUGH
const char* WIFI_PASS = "your-wifi-password";
const char* HUB       = "https://map.sparrowmap.com";
const char* NODE_ID   = "n_xxxxxxxx";        // from /api/enroll
const char* TOKEN     = "your-node-token";   // from /api/enroll (shown once)
const float FIXED_LAT = 0.0;                 // 0 = use the node's enrolled spot
const float FIXED_LON = 0.0;                 // set these if the sensor is fixed
const unsigned SCAN_EVERY_MS = 30000;        // scan every 30s

// Names we keep (case-insensitive substring of the SSID). This is the whole
// "is it surveillance or police gear" test. Grows as real hardware is confirmed.
const char* SURVEILLANCE[] = {
  "flock", "falcon", "vigilant", "alpr", "verkada", "avigilon", "genetec"
};
// Police in-car gear — corroborates a visual police sighting (see /rfbeta).
const char* POLICE[] = {
  "axon", "bodyworn", "watchguard", "digitalally"
};
// Drones broadcast Remote ID over 2.4/5GHz — same band this scans.
const char* DRONE[] = {
  "dji", "mavic", "skydio", "autel", "anafi", "drone", "remoteid"
};

// ── helpers ──────────────────────────────────────────────────────────────────
String lower(String s) { s.toLowerCase(); return s; }

// returns "" (skip), "surveillance", "police", or "drone"
String classify(const String& ssid) {
  String s = lower(ssid);
  for (auto p : POLICE)        if (s.indexOf(p) >= 0) return "police";
  for (auto p : DRONE)         if (s.indexOf(p) >= 0) return "drone";
  for (auto p : SURVEILLANCE)  if (s.indexOf(p) >= 0) return "surveillance";
  return "";
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("connecting wifi");
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(500); Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " ok" : " FAILED (will retry)");
}

void postCandidates(const String& body) {
  if (WiFi.status() != WL_CONNECTED) { connectWifi(); return; }
  HTTPClient http;
  http.begin(String(HUB) + "/api/rf");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", String("Bearer ") + TOKEN);
  int code = http.POST(body);
  Serial.printf("  uploaded -> HTTP %d\n", code);
  http.end();
}

// ── main ─────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\nSparrowMap ESP32 RF sensor");
  connectWifi();
}

void loop() {
  int n = WiFi.scanNetworks(false, true);   // sync scan, include hidden
  int kept = 0, dropped = 0;
  String cands = "";
  for (int i = 0; i < n; i++) {
    String ssid = WiFi.SSID(i);
    String kind = classify(ssid);
    if (kind == "") { dropped++; continue; }   // private device: forget it now
    kept++;
    // dev_id: a stable hash of the BSSID (public infrastructure, fair to keep).
    String bssid = WiFi.BSSIDstr(i);
    uint32_t h = 2166136261u;
    for (char c : bssid) { h ^= (uint8_t)c; h *= 16777619u; }
    char devid[9]; snprintf(devid, sizeof(devid), "%08x", h);
    String police_conf = (kind == "police") ? "weak" : "";
    String is_drone = (kind == "drone") ? "true" : "false";
    if (cands.length()) cands += ",";
    cands += "{\"dev_id\":\"" + String(devid) + "\",";
    cands += "\"ssid\":\"" + ssid + "\",";
    cands += "\"vendor_reason\":\"ssid-match\",";
    cands += "\"rssi\":" + String(WiFi.RSSI(i)) + ",";
    cands += "\"police_conf\":\"" + police_conf + "\",";
    cands += "\"is_drone\":" + is_drone + ",";
    if (FIXED_LAT != 0.0) { cands += "\"lat\":" + String(FIXED_LAT, 5) + ",\"lon\":" + String(FIXED_LON, 5) + ","; }
    cands += "\"ts\":" + String((uint32_t)(millis() / 1000)) + "}";
  }
  WiFi.scanDelete();
  Serial.printf("scanned %d -> kept %d surveillance/police, dropped %d private\n", n, kept, dropped);
  if (kept > 0) {
    String body = "{\"node_id\":\"" + String(NODE_ID) + "\",\"candidates\":[" + cands + "]}";
    postCandidates(body);
  }
  delay(SCAN_EVERY_MS);
}
