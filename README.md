# ML LocalService Home Assistant Integration

`ml_localservice` discovers MoreLinks ML gateways over mDNS and connects to the
gateway's local `appwsserver`. Each
Config Entry owns one persistent WebSocket client and one coordinator. Entities
consume coordinator snapshots and never access the socket directly.

## Requirements

- Home Assistant with support for custom integrations;
- the HA host and ML gateway on a network where `_ml-gwconnect._tcp.local.`
  multicast discovery is visible;
- TCP 8820 and UDP 8810 reachable from Home Assistant to the gateway.

The integration listens only for `_ml-gwconnect._tcp.local.` mDNS
announcements. It uses the discovered address and fixed TCP 8820 appwsserver
port; no UDP broadcast discovery is performed. UDP 8810 is contacted directly
only when appwsserver requires its one-shot registration token.

The discovered SRV port is the gateway-discovery service (normally 10009). The
integration intentionally uses the separate, fixed appwsserver port 8820.

## Installation and configuration

1. Copy `custom_components/ml_localservice` into the Home Assistant
   `config/custom_components/` directory.
2. Restart Home Assistant.
3. Open **Settings → Devices & services**. Confirm the discovered ML gateway,
or choose **Add integration → ML LocalService** and enter the gateway IPv4
address manually.
4. Verify that the Config Entry becomes loaded and that supported entities are
   attached to their corresponding ML devices.

The mDNS guide `id` is the stable Config Entry identity. A later mDNS
announcement for the same guide is registration-checked before updating the
displayed IP address and reloading the entry without creating a duplicate
gateway. If a gateway is still waiting for confirmation, a newer mDNS
announcement replaces the old discovery card so it does not retain a historical
IP. The raw Home ID and registration token are not stored.

## Runtime behavior

- Setup connects, registers, synchronizes the device directory and states, then
  forwards the `light` and `switch` platforms.
- Push notifications update entity state. A bounded full-state read confirms a
  control if its push notification is missed.
- Disconnects mark entities unavailable. Reconnection repeats registration and
  both full synchronizations before making entities available again.
- Directory refreshes dynamically add newly supported entities. Removed or
  retyped projections are retired without deleting their Entity Registry
  identity, so the same stable unique ID can recover later.
- Unload closes the client and cancels the coordinator and platform-owned tasks.

The diagnostics download redacts the gateway host and includes directory shape,
mapping classifications and state counts, but not raw ML state values.

## Current mappings and extension

Relay containers (`1811592189610`), placeholder DO devices (`4991543809610`)
and unknown types remain zero-entity records. The documented category pairs are
mapped only after their advertised function schema confirms the relevant
capability:

- `10/10100001`–`10100004`: switching lights (including an advertised
  one-shot flash), dimmable, colour-temperature and RGB lights, respectively.
  RGB lights also expose their advertised brightness point;
- `2/10200005`: electric door lock (`status=true` means locked);
- `15/11500001`: physical click, double-click and long-press panel actions,
  exposed as one passive HA event entity with three event types.
- `15/11500002`: air-conditioner panels, exposed as a separate HA climate
  device with their advertised power, mode, temperature, fan, and telemetry;
- `15/11500003`: floor-heating panels, exposed as a separate HA climate device
  with off/heat, temperature, and advertised temperature/humidity telemetry;
- `15/11500004`: ventilation panels, exposed as a separate HA fan with their
  advertised power and low/medium/high presets;
- `15/11500005`: curtain panels, exposed as a separate HA cover with its
  advertised position and open/close/stop controls.
- `3/10300002`–`10300023`: documented security, presence and environmental
  sensors, exposed as HA binary sensors, sensors, and the person detector's
  mode switch when its advertised function schema supports them.
- `6/10600001`: documented air conditioners, exposed as one HA climate entity
  for power, HVAC mode, target temperature, fan/swing modes, and environmental
  readings; their mute mode and three-state auxiliary heat setting are exposed
  as a switch and select entity, respectively.
- `7/10700001`: documented floor-heating devices, exposed as one HA climate
  entity with `off`/`heat` modes, target temperature, and current temperature.
- `8/10800001` and `8/10800004`: documented ventilation devices, exposed as
  HA fans with on/off and three preset speeds; the first type also exposes its
  advertised indoor temperature and humidity as sensors.
- `12/11200001`–`11200009`: documented curtains, exposed as HA covers with
  open/close/stop and advertised position or tilt controls. Day/night curtains
  become independent day and night cover entities; rolling-curtain jog actions
  are exposed as buttons when advertised.

The existing basic-light entity retains its `power` mapping key, so enabling
new lighting capabilities does not change its entity identity.

New device support is added by implementing the mapper protocol in
`custom_components/ml_localservice/mapping/base.py` and registering the exact
type key in `mapping/registry.py`. A mapper returns device metadata, entity
 specifications, mapped states and translated control commands. Adding a
 mapper does not require changes to discovery, transport or coordinator
 ownership.

## Development and testing

Run the dependency-free suite from the repository root:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q custom_components tests
```

Config Flow, lifecycle, entity and diagnostics tests automatically run when the
Python environment includes Home Assistant. To exercise those paths against the
same HA release used for deployment, copy the repository to a disposable path
inside the HA container and run the same unittest command there.

For live validation, first confirm the mDNS TXT `id`, then verify registration,
device/state synchronization, mapped entity state, control confirmation,
reconnection and unload cleanup. Restore any physical output changed by the
test. Never store host-search tokens; they are one-shot registration material.
