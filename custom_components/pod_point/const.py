"""Constants for pod_point."""

from homeassistant.const import Platform
from podpointclient.version import __version__ as pod_point_client_version

from .version import __version__ as integration_version

# Base component constants
NAME = "Pod Point"
DOMAIN = "pod_point"
VERSION = integration_version
ATTRIBUTION = "Data provided by https://pod-point.com/"
ISSUE_URL = "https://github.com/NikNakk/pod-point-home-assistant-component/issues"

# Icons
ICON_1C = "mdi:ev-plug-type1"
ICON_2C = "mdi:ev-plug-type2"
ICON = ICON_2C
ICON_EV_STATION = "mdi:ev-station"

SWITCH_ICON = ICON_EV_STATION

# Platforms
BINARY_SENSOR = Platform.BINARY_SENSOR
SENSOR = Platform.SENSOR
NUMBER = Platform.NUMBER
SELECT = Platform.SELECT
SWITCH = Platform.SWITCH
UPDATE = Platform.UPDATE
PLATFORMS = [BINARY_SENSOR, SENSOR, NUMBER, SELECT, SWITCH, UPDATE]

SERVICE_CHARGE_NOW = "charge_now"
SERVICE_STOP_CHARGE_NOW = "stop_charge_now"
SERVICE_SET_SCHEDULE = "set_schedule"

# Configuration and options
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 300
CONF_HTTP_DEBUG = "http_debug"
DEFAULT_HTTP_DEBUG = False
CONF_CURRENCY = "currency"
DEFAULT_CURRENCY = "GBP"

# State attributes
ATTR_STATE = "state"

ATTR_STATE_AVAILABLE = "available"
ATTR_STATE_UNAVAILABLE = "unavailable"
ATTR_STATE_CHARGING = "charging"
ATTR_STATE_IDLE = "idle"
ATTR_STATE_SUSPENDED_EV = "suspended-ev"
ATTR_STATE_SUSPENDED_EVSE = "suspended-evse"
ATTR_STATE_PENDING = "pending"
ATTR_STATE_OUT_OF_SERVICE = "out-of-service"
ATTR_STATE_WAITING = "waiting-for-schedule"
ATTR_STATE_CONNECTED_WAITING = "connected-waiting-for-schedule"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_DEVICE_ID = "device_id"
ATTR_HOURS = "hours"
ATTR_MINUTES = "minutes"
ATTR_SECONDS = "seconds"
ATTR_SCHEDULES = "schedules"
ATTR_START_DAY = "start_day"
ATTR_START_TIME = "start_time"
ATTR_END_DAY = "end_day"
ATTR_END_TIME = "end_time"
ATTR_IS_ACTIVE = "is_active"

DEFAULT_CHARGE_NOW_DURATION = 60

# Flags
CHARGING_FLAG = ATTR_STATE_CHARGING

# Image serving
APP_IMAGE_URL_BASE = f"/api/{DOMAIN}/static"

STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
Version: {VERSION} (podpointclient={pod_point_client_version})
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""
