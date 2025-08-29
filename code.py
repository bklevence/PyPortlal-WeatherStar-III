# code.py - combo version (terminalio fonts, scrolling forecast, SD save)
import time
import board
import displayio
import terminalio
from adafruit_display_text import label
import busio
from digitalio import DigitalInOut
from adafruit_esp32spi import adafruit_esp32spi
import adafruit_connection_manager
import adafruit_requests
from os import getenv
import rtc
import gc
import storage
import adafruit_sdcard

# ---------------- CONFIG ----------------
USER_AGENT = "PyPortalWeather/1.0 (your_email@example.com)"
LAT = CHANGEME
LON = CHANGEME
MAX_RETRIES = 3
TIMEOUT = 20
REFRESH_SECONDS = 60  # refresh every minute for testing
SCROLL_SPEED = 2      # pixels per update for forecast
COLORS = {
    'bg': 0x0B3D91,       # deep blue main background
    'bar': 0x051529,      # dark navy bars
    'temp': 0x00FFFF,     # cyan temp
    'location': 0xFF6B35, # orange location
    'time': 0xFF1493,     # magenta time/date
    'headers': 0xFFD700,  # yellow headers
    'wind': 0x32CD32,     # lime wind
    'data': 0xFFFFFF,     # white data
    'forecast': 0xF0E68C, # khaki forecast
    'status': 0x00FFFF,   # cyan status
    'error': 0xFF0000,    # red
    'text': 0xFFFFFF
}
WIND_DIRS = ("N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW")
# ----------------------------------------

class WeatherApp:
    def __init__(self):
        gc.collect()
        self.display = board.DISPLAY
        self.display.brightness = 1.0

        # network / sd
        self.spi = None
        self.esp = None
        self.requests = None
        self.sd = None
        self.vfs = None
        self.sd_available = False

        # data store (simple dict)
        self.data = {
            "Time": "",
            "Location": "Fetching...",
            "Temp": "-- °F",
            "Conditions": "Fetching...",
            "Wind": "--",
            "Humidity": "--%",
            "Dewpoint": "-- °F",
            "Pressure": "-- \"",
            "Visibility": "-- mi",
            "Forecast": "Fetching...",
        }
        self.data_loaded = False
        self.data_error = False

        # scrolling
        self.forecast_x = 320
        self.forecast_text_width = 0

        # status management
        self.show_status_bar = True
        self.status_hide_timer = 0

        # display elements
        self._setup_display()
        gc.collect()

    def log(self, *args):
        print(*args)
        gc.collect()

    def _setup_display(self):
        # root group
        self.root = displayio.Group()
        self.display.root_group = self.root

        # background main area (320 x 170 at y=30)
        bg = displayio.Bitmap(320, 170, 1)
        pal_bg = displayio.Palette(1)
        pal_bg[0] = COLORS['bg']
        self.root.append(displayio.TileGrid(bg, pixel_shader=pal_bg, x=0, y=30))

        # top bar (30) and bottom bar (40)
        top = displayio.Bitmap(320, 30, 1)
        bottom = displayio.Bitmap(320, 40, 1)
        pal_bar = displayio.Palette(1)
        pal_bar[0] = COLORS['bar']
        self.root.append(displayio.TileGrid(top, pixel_shader=pal_bar, x=0, y=0))
        self.root.append(displayio.TileGrid(bottom, pixel_shader=pal_bar, x=0, y=200))

        # Top bar: time, date, location, SD indicator
        self.time_label = label.Label(terminalio.FONT, text="", color=COLORS['time'])
        self.time_label.x = 5
        self.time_label.y = 18
        self.root.append(self.time_label)

        self.date_label = label.Label(terminalio.FONT, text="", color=COLORS['time'])
        self.date_label.x = 85
        self.date_label.y = 18
        self.root.append(self.date_label)

        self.location_label = label.Label(terminalio.FONT, text="", color=COLORS['location'])
        self.location_label.x = 160
        self.location_label.y = 18
        self.root.append(self.location_label)

        # SD status indicator in top right
        self.sd_status_label = label.Label(terminalio.FONT, text="", color=COLORS['error'])
        self.sd_status_label.x = 280
        self.sd_status_label.y = 18
        self.root.append(self.sd_status_label)

        # Bottom bar status (hideable) + scrolling forecast
        self.status_label = label.Label(terminalio.FONT, text="", color=COLORS['status'])
        self.status_label.x = 10
        self.status_label.y = 215
        self.root.append(self.status_label)

        self.forecast_label = label.Label(terminalio.FONT, text="", color=COLORS['forecast'])
        self.forecast_label.x = self.forecast_x
        self.forecast_label.y = 220
        self.root.append(self.forecast_label)

        # Main content area (170px tall)
        self.header_label = label.Label(terminalio.FONT, text="CURRENT CONDITIONS", color=COLORS['headers'])
        self.header_label.x = 80
        self.header_label.y = 55
        self.root.append(self.header_label)

        # Temperature & description (left)
        self.temp_label = label.Label(terminalio.FONT, text="", color=COLORS['temp'])
        self.temp_label.x = 15
        self.temp_label.y = 90
        self.root.append(self.temp_label)

        self.desc_label = label.Label(terminalio.FONT, text="", color=COLORS['data'])
        self.desc_label.x = 15
        self.desc_label.y = 115
        self.root.append(self.desc_label)

        # Wind left two lines
        self.wind_label = label.Label(terminalio.FONT, text="", color=COLORS['wind'])
        self.wind_label.x = 15
        self.wind_label.y = 135
        self.root.append(self.wind_label)

        self.wind_label2 = label.Label(terminalio.FONT, text="", color=COLORS['wind'])
        self.wind_label2.x = 15
        self.wind_label2.y = 155
        self.root.append(self.wind_label2)

        # Right column data
        self.humidity_label = label.Label(terminalio.FONT, text="", color=COLORS['data'])
        self.humidity_label.x = 150
        self.humidity_label.y = 90
        self.root.append(self.humidity_label)

        self.dewpoint_label = label.Label(terminalio.FONT, text="", color=COLORS['data'])
        self.dewpoint_label.x = 150
        self.dewpoint_label.y = 110
        self.root.append(self.dewpoint_label)

        self.pressure_label = label.Label(terminalio.FONT, text="", color=COLORS['data'])
        self.pressure_label.x = 150
        self.pressure_label.y = 130
        self.root.append(self.pressure_label)

        self.visibility_label = label.Label(terminalio.FONT, text="", color=COLORS['data'])
        self.visibility_label.x = 150
        self.visibility_label.y = 150
        self.root.append(self.visibility_label)

    # ---------- status / sd / wifi ----------
    def update_sd_status(self, status_type):
        """Update SD status indicator: error (red), working (yellow), saved (green), hidden"""
        if status_type == "error":
            self.sd_status_label.text = "SD"
            self.sd_status_label.color = COLORS['error']  # Red
        elif status_type == "working":
            self.sd_status_label.text = "SD"
            self.sd_status_label.color = 0xFFFF00  # Yellow
        elif status_type == "saved":
            self.sd_status_label.text = "SD"
            self.sd_status_label.color = 0x00FF00  # Green
        else:  # hidden
            self.sd_status_label.text = ""

    def update_status(self, text, is_error=False, temporary=True):
        """Update main status bar with auto-hide option"""
        self.log(text)
        if self.show_status_bar:
            self.status_label.text = text[:40]
            if is_error:
                self.status_label.color = COLORS['error']
            else:
                self.status_label.color = COLORS['status']

        # Set timer to hide status after a few seconds (except for errors)
        if temporary and not is_error:
            self.status_hide_timer = time.monotonic() + 5  # Hide after 5 seconds

    def hide_status_if_needed(self):
        """Hide status bar after timer expires"""
        if self.status_hide_timer > 0 and time.monotonic() > self.status_hide_timer:
            self.status_label.text = ""
            self.status_hide_timer = 0

    def setup_shared_spi(self):
        try:
            self.log("Setting up SPI...")
            self.spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
            time.sleep(0.05)
            return True
        except Exception as e:
            self.log("SPI setup error:", e)
            return False

    def setup_sd(self):
        try:
            self.update_sd_status("working")
            cs = DigitalInOut(board.SD_CS)
            self.sd = adafruit_sdcard.SDCard(self.spi, cs)
            self.vfs = storage.VfsFat(self.sd)
            storage.mount(self.vfs, "/sd")
            self.sd_available = True
            self.update_sd_status("saved")
            gc.collect()
            return True
        except Exception as e:
            self.log("SD setup error:", e)
            self.sd_available = False
            self.update_sd_status("error")
            return False

    def save_to_sd(self):
        if not self.sd_available:
            return False
        try:
            self.update_sd_status("working")
            with open("/sd/weather.txt", "w") as f:
                for k, v in self.data.items():
                    f.write("{}={}\n".format(k, v))
                f.write("timestamp={}\n".format(time.time()))
            self.update_sd_status("saved")
            return True
        except Exception as e:
            self.log("SD write error:", e)
            self.update_sd_status("error")
            return False

    def setup_wifi(self):
        try:
            self.update_status("WiFi setup...")
            ssid = getenv("CIRCUITPY_WIFI_SSID")
            password = getenv("CIRCUITPY_WIFI_PASSWORD")
            if not ssid or not password:
                self.update_status("No WiFi creds", is_error=True, temporary=False)
                return False
            esp32_cs = DigitalInOut(board.ESP_CS)
            esp32_ready = DigitalInOut(board.ESP_BUSY)
            esp32_reset = DigitalInOut(board.ESP_RESET)
            self.esp = adafruit_esp32spi.ESP_SPIcontrol(self.spi, esp32_cs, esp32_ready, esp32_reset)
            for attempt in range(MAX_RETRIES):
                try:
                    self.update_status("Connecting WiFi...")
                    self.esp.connect_AP(ssid, password)
                    break
                except Exception as e:
                    self.log("WiFi attempt failed:", e)
                    time.sleep(2)
            if not getattr(self.esp, "is_connected", False):
                self.update_status("WiFi failed", is_error=True, temporary=False)
                return False
            pool = adafruit_connection_manager.get_radio_socketpool(self.esp)
            ssl_context = adafruit_connection_manager.get_radio_ssl_context(self.esp)
            self.requests = adafruit_requests.Session(pool, ssl_context)
            self.update_status("WiFi connected")
            gc.collect()
            return True
        except Exception as e:
            self.log("WiFi setup error:", e)
            self.update_status("WiFi setup error", is_error=True, temporary=False)
            return False

    def sync_time(self):
        response = None
        try:
            self.update_status("Syncing time...")
            timezone = getenv("TIMEZONE", "America/New_York")
            url = f"http://worldtimeapi.org/api/timezone/{timezone}"
            response = self.requests.get(url, timeout=TIMEOUT)
            if response.status_code == 200:
                import json
                data = response.json()
                utc_time = data["unixtime"] + data["raw_offset"] + data["dst_offset"]
                rtc.RTC().datetime = time.localtime(utc_time)
                self.update_status("Time synced")
                del json, data
            else:
                self.update_status("Time sync failed", is_error=True)
        except Exception as e:
            self.log("Time sync error:", e)
            self.update_status("Time sync error", is_error=True)
        finally:
            if response:
                response.close()
            gc.collect()

    def fetch_weather(self):
        """Simplified single-station fetch using NWS points -> forecast + first station latest obs."""
        if not self.requests:
            self.update_status("No internet", is_error=True, temporary=False)
            return False
        gc.collect()
        response = None
        try:
            self.update_status("Getting location...")
            url = f"https://api.weather.gov/points/{LAT},{LON}"
            response = self.requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            if response.status_code != 200:
                self.log("Points API status:", response.status_code)
                return False
            import json
            data = response.json()
            rel = data["properties"]["relativeLocation"]["properties"]
            self.data["Location"] = "{}, {}".format(rel.get("city",""), rel.get("state",""))
            forecast_url = data["properties"]["forecast"]
            stations_url = data["properties"]["observationStations"]
            del data, rel, json
            response.close()
            gc.collect()

            # forecast
            self.update_status("Getting forecast...")
            response = self.requests.get(forecast_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            if response.status_code == 200:
                import json
                data = response.json()
                period = data["properties"]["periods"][0]
                self.data["Temp"] = "{} °F".format(period.get("temperature","--"))
                self.data["Conditions"] = period.get("shortForecast","--")
                self.data["Forecast"] = "TODAY: " + period.get("detailedForecast","")
                del data, period, json
            else:
                self.log("Forecast error:", response.status_code)
                response.close()
                return False
            response.close()
            gc.collect()

            # stations -> first station -> latest observation
            self.update_status("Getting conditions...")
            response = self.requests.get(stations_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            if response.status_code == 200:
                import json
                data = response.json()
                features = data.get("features", [])
                if features:
                    station_url = features[0]["id"] + "/observations/latest"
                    response.close()
                    gc.collect()
                    response = self.requests.get(station_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
                    if response.status_code == 200:
                        obs = response.json().get("properties", {})
                        # wind
                        try:
                            if obs.get("windSpeed") and obs["windSpeed"].get("value") is not None:
                                ws = obs["windSpeed"]["value"]
                                self.data["Wind"] = "{} mph".format(int(ws * 0.621371))
                        except Exception:
                            pass
                        # humidity
                        try:
                            if obs.get("relativeHumidity") and obs["relativeHumidity"].get("value") is not None:
                                self.data["Humidity"] = "{}%".format(int(obs["relativeHumidity"]["value"]))
                        except Exception:
                            pass
                        # dewpoint
                        try:
                            if obs.get("dewpoint") and obs["dewpoint"].get("value") is not None:
                                dp = obs["dewpoint"]["value"]
                                self.data["Dewpoint"] = "{} °F".format(int(dp * 9/5 + 32))
                        except Exception:
                            pass
                        # pressure
                        try:
                            if obs.get("barometricPressure") and obs["barometricPressure"].get("value") is not None:
                                p = obs["barometricPressure"]["value"]
                                self.data["Pressure"] = "{:.2f} inHg".format(p * 0.0002953)
                        except Exception:
                            pass
                        # visibility
                        try:
                            if obs.get("visibility") and obs["visibility"].get("value") is not None:
                                v = obs["visibility"]["value"] / 1609.34
                                self.data["Visibility"] = "{:.1f} mi".format(v)
                        except Exception:
                            pass
                    # else: ignore obs failure, keep defaults
                del data, json
            else:
                self.log("Stations list error:", response.status_code)

        except Exception as e:
            self.log("Weather fetch error:", e)
            return False
        finally:
            if response:
                response.close()
            gc.collect()

        self.data_loaded = True
        self.data_error = False
        self.update_status("Weather loaded!")
        return True

    # ---------------- display updates ----------------
    def update_display_from_data(self):
        # Time
        now = time.localtime()
        tstr = "{:02d}:{:02d}:{:02d}".format(now.tm_hour, now.tm_min, now.tm_sec)
        self.data["Time"] = tstr
        self.time_label.text = tstr
        self.date_label.text = "{} {:02d}{}".format(("MON","TUE","WED","THU","FRI","SAT","SUN")[now.tm_wday],
                                                   now.tm_mday, ("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC")[now.tm_mon-1])

        # Location - keep space for SD indicator
        loc = self.data.get("Location","")
        max_chars = 14  # Leave room for SD indicator
        if len(loc) > max_chars:
            loc = loc[:max_chars-3] + "..."
        self.location_label.text = loc

        # left column
        self.temp_label.text = self.data.get("Temp","")
        self.desc_label.text = (self.data.get("Conditions","") or "").upper()

        if "CALM" in self.data.get("Wind",""):
            self.wind_label.text = "WIND: CALM"
            self.wind_label2.text = ""
        else:
            self.wind_label.text = "WIND: " + (self.data.get("Wind","--"))
            self.wind_label2.text = ""

        # right column
        self.humidity_label.text = "HUMIDITY: {}".format(self.data.get("Humidity","--"))
        self.dewpoint_label.text = "DEWPOINT: {}".format(self.data.get("Dewpoint","--"))
        self.pressure_label.text = "PRESSURE: {}".format(self.data.get("Pressure","--"))
        self.visibility_label.text = "VISIBILITY: {}".format(self.data.get("Visibility","--"))

        # forecast scrolling setup
        ftxt = (self.data.get("Forecast","") or "").upper()
        self.forecast_label.text = ftxt
        # approximate pixel width (chars * 7.5)
        self.forecast_text_width = int(len(ftxt) * 8)
        if self.forecast_x > 319:
            self.forecast_x = 320
        self.forecast_label.x = int(self.forecast_x)
        gc.collect()

    def update_scrolling(self):
        if not self.data_loaded or not self.forecast_label.text:
            return
        self.forecast_x -= SCROLL_SPEED
        self.forecast_label.x = int(self.forecast_x)
        if self.forecast_x < -self.forecast_text_width:
            self.forecast_x = 320

    # ---------------- main loop ----------------
    def run(self):
        self.log("WeatherApp starting...")
        if not self.setup_shared_spi():
            self.log("SPI failed; abort")
            return

        # SD mount optional but try it early
        self.setup_sd()

        # WiFi required for initial fetch
        if not self.setup_wifi():
            self.update_status("No internet, exiting", is_error=True, temporary=False)
            return

        # sync time (best effort)
        try:
            self.sync_time()
        except Exception:
            pass

        # initial fetch
        success = self.fetch_weather()
        if success:
            self.update_display_from_data()
            # save to sd if available
            if self.sd_available:
                self.save_to_sd()
        else:
            self.update_status("Initial fetch failed", is_error=True, temporary=False)

        last_refresh = time.monotonic()

        # main loop
        while True:
            try:
                # update local time display every loop
                self.update_display_from_data()
                # update scrolling
                self.update_scrolling()
                # hide status bar if timer expired
                self.hide_status_if_needed()

                # periodic refresh (every REFRESH_SECONDS)
                if time.monotonic() - last_refresh >= REFRESH_SECONDS:
                    self.log("Scheduled refresh...")
                    # re-sync time and fetch new weather
                    try:
                        self.sync_time()
                    except Exception:
                        pass
                    if self.fetch_weather():
                        self.update_display_from_data()
                        if self.sd_available:
                            self.save_to_sd()
                    else:
                        self.update_status("Refresh failed", is_error=True, temporary=False)
                    last_refresh = time.monotonic()
                    gc.collect()

                time.sleep(0.1)

            except Exception as e:
                self.log("Main loop exception:", e)
                self.update_status("System error", is_error=True, temporary=False)
                gc.collect()
                time.sleep(2)

if __name__ == "__main__":
    WeatherApp().run()
