# ML LocalService Home Assistant Integration

[![HACS validation](https://github.com/morelinks/ml-home-assistant/actions/workflows/validate.yaml/badge.svg)](https://github.com/morelinks/ml-home-assistant/actions/workflows/validate.yaml)

ML LocalService is a local Home Assistant integration for MoreLinks ML
gateways. It is distributed under the [Apache-2.0 license](LICENSE).

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

### HACS

After this repository is accepted by HACS, search for **ML LocalService** in
HACS and install it from the Integrations section. Until it is listed in the
HACS default repository, add
`morelinks/ml-home-assistant` as a HACS Custom repository with category
**Integration**.

### Manual installation

1. Copy `custom_components/ml_localservice` into the Home Assistant
   `config/custom_components/` directory.
2. Restart Home Assistant.
3. Open **Settings → Devices & services**. Confirm the discovered ML gateway,
or choose **Add integration → ML LocalService** and enter the gateway IPv4
address manually.
4. Verify that the Config Entry becomes loaded and that supported entities are
   attached to their corresponding ML devices.

### Uninstallation

Remove the **ML LocalService** config entry first, then remove the
`custom_components/ml_localservice` directory and restart Home Assistant.

The mDNS guide `id` is the stable Config Entry identity. A later mDNS
announcement for the same guide is registration-checked before updating the
displayed IP address and reloading the entry without creating a duplicate
gateway. If a gateway is still waiting for confirmation, a newer mDNS
announcement replaces the old discovery card so it does not retain a historical
IP. Multiple gateways in the same ML Home are configured independently; the
Home fingerprint verifies registration and never merges different gateways.
Manual IPv4 configuration uses an endpoint identity until mDNS identifies that
same verified endpoint. The entity identity is retained during this association
and subsequent address updates. The raw Home ID and registration token are not
stored.

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

Each gateway's options include **Use fallback values when device state is
missing**, enabled by default unless explicitly disabled. A synchronized gateway can keep
an entity usable while its device is still listed but its required telemetry
is absent. The entity uses its last reported state from the current loaded
session; without a previous report, lights and switches show off and curtains
show closed (position 0% when position is supported). Other entities without a
previous report show unknown. Missing sensor measurements always show unknown;
cached climate states omit current temperature and humidity. Missing optional
fields do not replace an otherwise valid mapped state.

The `state_source` attribute distinguishes `reported`, `last_known`, `default`
and `unknown`. Defaults and retained history also set HA's `assumed_state` flag;
automations may still use these displayed states. Fallbacks do not send commands,
change raw ML state or satisfy control confirmation. Invalid required values,
disconnected gateways and removed devices retain their existing unavailable
behavior. History survives reconnects, but resets when the entity is reloaded,
HA restarts, or its mapping/schema changes.

Curtain position inversion is also enabled by default for gateways without a
saved curtain selection: HA position is `100 - ML position` in both directions.
The gateway options initially select all curtains with a position capability.
Saving a selection overrides this default; deselect a device to use its original
position convention, or clear the selection to disable inversion for all devices
on that gateway. Existing saved selections, including empty ones, are preserved.
This affects position only; open/close/stop commands and tilt remain unchanged.

The diagnostics download redacts the gateway host and includes directory shape,
mapping classifications and state counts, but not raw ML state values.

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

## Support

For usage questions and reproducible bugs, open an issue in the
[GitHub issue tracker](https://github.com/morelinks/ml-home-assistant/issues).
Please include the Home Assistant version, integration version, relevant
diagnostics with secrets removed, and steps to reproduce. Do not include
gateway tokens, Home IDs, passwords, or private network details.

## Security

Please do not report security vulnerabilities in a public issue. Use GitHub's
private vulnerability reporting for this repository. If private reporting is
not available in your GitHub view, contact the maintainer account
[@morelinkslian](https://github.com/morelinkslian) privately before publishing
details. Do not include registration tokens, gateway identifiers, or decrypted
protocol payloads in a public report.
