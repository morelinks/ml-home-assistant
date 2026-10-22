"""Constants shared by the ML LocalService integration."""

from typing import Final

DOMAIN: Final = "ml_localservice"
INTEGRATION_VERSION: Final = "0.1.0"

# Each platform below has a concrete entity implementation. Other domains
# remain an explicit extension boundary for later documented ML categories.
PLATFORMS: Final = (
    "light",
    "switch",
    "lock",
    "button",
    "event",
    "sensor",
    "binary_sensor",
    "climate",
    "select",
    "fan",
    "cover",
)
RESERVED_PLATFORMS: Final = ()

CONF_GATEWAY_ID: Final = "gateway_id"
CONF_HOST: Final = "host"
CONF_HOSTNAME: Final = "hostname"
CONF_PORT: Final = "port"
CONF_PV: Final = "pv"
CONF_SV: Final = "sv"
CONF_HV: Final = "hv"
CONF_HOME_FINGERPRINT: Final = "home_fingerprint"
CONF_INVERTED_POSITION_DEVICE_IDS: Final = "inverted_position_device_ids"
MDNS_GATEWAY_ID: Final = "id"

# The mDNS service advertises the gwconnect service port (10009).  The
# appwsserver WebSocket is a separate, fixed service on 8820.
APPWSSERVER_PORT: Final = 8820
GATEWAY_DISCOVERY_PORT: Final = 10009
HOST_SEARCH_PORT: Final = 8810
ZEROCONF_TYPE: Final = "_ml-gwconnect._tcp.local."
ZEROCONF_MODE: Final = "1"

# These values are used only for the one-shot registration check in the
# Config Flow. Task 1-3 will own the long-lived client lifecycle.
APP_ID: Final = "homeassistant"
PROTOCOL_VERSION: Final = "1.0.0"
# appwsserver encrypts ``morelinkssearchResult`` with this app-specific
# authorization material.  It is protocol material, not a user credential.
APP_AUTH_TOKEN: Final = "O0YhBi6iBZ8yluNLr"
APP_AUTH_AES_KEY: Final = "BFtXNc4RRwTDpjUbF0h0Dvzc8SVrlUnSOkWLamhLfF8"
CONNECTION_TIMEOUT: Final = 10.0

# Persistent appwsserver client bounds.  The server shares one four-second
# LocalService fetch throttle across all synchronization commands.
REQUEST_TIMEOUT: Final = 10.0
# Some gateways take longer than the ordinary request timeout to fetch a full
# device or state snapshot from LocalService.
SYNC_REQUEST_TIMEOUT: Final = 20.0
SYNC_INTERVAL: Final = 4.1
SYNC_RETRIES: Final = 1
WEBSOCKET_HEARTBEAT: Final = 30.0
# appwsserver records activity only when it receives an application message.
# Keep the registered connection alive before its one-minute idle cleanup.
APPLICATION_HEARTBEAT_INTERVAL: Final = 25.0
MAX_MESSAGE_BYTES: Final = 1024 * 1024
MAX_PENDING_REQUESTS: Final = 64
MAX_EVENT_QUEUE_SIZE: Final = 256
MAX_DEVICES: Final = 4096
MAX_FUNCTIONS_PER_DEVICE: Final = 256
MAX_STATES: Final = 16384

# Coordinator lifecycle defaults. Reconnect attempts continue indefinitely,
# while every individual delay is bounded by the final value.
RECONNECT_DELAYS: Final = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
DEVICE_REFRESH_INTERVAL: Final = 15.0 * 60.0
CONTROL_CONFIRMATION_TIMEOUT: Final = 5.0

ABORT_CANNOT_CONNECT: Final = "cannot_connect"
ABORT_INVALID_MODE: Final = "invalid_mode"
ABORT_MISSING_ADDRESS: Final = "missing_address"
ABORT_MISSING_GATEWAY_ID: Final = "missing_gateway_id"
