# AGENTS.md

## Goal
Adapt this Home Assistant integration to support an additional Harvia API specification (documented at `https://harvia.io/api`) while keeping existing MyHarvia cloud support working.

## Source Documents (Use These as Contract)
- `https://harvia.io/assets/api-docs/harvia/api-overview.md`
- `https://harvia.io/assets/api-docs/harvia/device-service.md`
- `https://harvia.io/assets/api-docs/harvia/events-service.md`
- `https://harvia.io/assets/api-docs/harvia/data-service.md`

Key documented flow:
- Fetch runtime endpoints from `https://api.harvia.io/endpoints`
- Authenticate via `POST {endpoints.RestApi.generics.https}/auth/token`
- Refresh via `POST {endpoints.RestApi.generics.https}/auth/refresh`
- Use `Authorization: Bearer <idToken>` for REST and GraphQL

## Working Assumptions
- This repository currently uses the MyHarvia cloud GraphQL/AppSync API (`custom_components/harvia_sauna/api.py` + `websocket.py`).
- The additional API is expected to be very similar to the current API.
- Backward compatibility is required for existing users.

## Implementation Plan

### 1. Capture and Freeze the New API Contract
1. Save the four source markdown files under `docs/harvia_api_spec/`:
   - `api-overview.md`
   - `device-service.md`
   - `events-service.md`
   - `data-service.md`
2. From `api-overview.md`, capture current endpoint configuration shape from `https://api.harvia.io/endpoints`:
   - `endpoints.RestApi.generics.https`
   - `endpoints.RestApi.device.https`
   - `endpoints.RestApi.data.https`
   - `endpoints.GraphQL.device.https`
   - `endpoints.GraphQL.data.https`
   - `endpoints.GraphQL.events.https`
3. Record auth contract:
   - `/auth/token` returns `idToken`, `accessToken`, `refreshToken`, `expiresIn`
   - `/auth/refresh` renews token set
   - `/auth/revoke` invalidates refresh token
4. Create `docs/api_mapping.md` with a two-column mapping:
   - existing integration capability -> new API endpoint(s)/payload.

Exit criteria:
- A stable local copy of the API contract exists.
- Endpoint-level mapping document is complete.

### 2. Compare APIs Before Designing Abstraction
1. Build `docs/api_comparison.md` with a side-by-side comparison:
   - endpoint discovery shape (`/endpoints` payload keys)
   - auth endpoints and token fields (`idToken`, `refreshToken`, `expiresIn`)
   - device list/state operations and payload structure
   - telemetry latest/history operations and payload structure
   - command/control semantics (`SAUNA`, `LIGHTS`, `FAN`, target updates)
   - GraphQL subscription topics and message envelope format
2. Mark each capability as:
   - `IDENTICAL` (same contract)
   - `COMPATIBLE` (small mapping only)
   - `DIVERGENT` (different flow/payload)
3. Decide implementation strategy based on score:
   - If almost all capabilities are `IDENTICAL`/`COMPATIBLE`: use a similarity-first approach (shared transport/helpers + lightweight provider adapter).
   - If several critical capabilities are `DIVERGENT`: proceed with strict provider abstraction.
4. Freeze this decision in `docs/api_mapping.md` before coding.

Exit criteria:
- Comparison file completed with `IDENTICAL/COMPATIBLE/DIVERGENT` labels.
- Chosen architecture is justified from comparison evidence.

### 3. Introduce an API Abstraction Layer (Only If Needed)
1. Add a provider-neutral interface in `custom_components/harvia_sauna/api_base.py`:
   - `async_authenticate()`
   - `async_get_user_data()`
   - `async_get_devices()`
   - `async_get_device_state(device_id)`
   - `async_get_latest_device_data(device_id)`
   - `async_request_state_change(device_id, payload)`
   - optional realtime hooks (`async_start_push`, `async_stop_push`)
2. Keep all return values normalized to one internal schema (same keys used by coordinator/entities today).
3. Refactor current `HarviaApiClient` in `api.py` to implement this interface (no behavior change).

Exit criteria:
- Coordinator can consume the interface rather than a concrete MyHarvia class.

### 4. Add a Second API Provider
1. Create `custom_components/harvia_sauna/api_harviaio.py` implementing the same interface.
2. Implement endpoint discovery first (`GET https://api.harvia.io/endpoints`), then cache service base URLs.
3. Implement auth/token lifecycle using `generics/auth/token` and `generics/auth/refresh`.
4. Implement endpoint calls and map responses to existing internal schema.
5. If the new API supports push updates, add `custom_components/harvia_sauna/websocket_harviaio.py` (or `sse_harviaio.py`) and wire it via the provider interface.
6. If no push exists, use polling only and tune `SCAN_INTERVAL_FALLBACK` for acceptable responsiveness.

Exit criteria:
- New provider can fully read and control devices with normalized data output.

### 5. Wire Provider Selection Into Config and Setup
1. In `custom_components/harvia_sauna/const.py`, add:
   - `CONF_API_PROVIDER`
   - provider values (for example `myharvia_graphql`, `harviaio_openapi`).
2. In `custom_components/harvia_sauna/config_flow.py`:
   - add API provider selection step,
   - collect provider-specific credentials/settings,
   - validate by performing a lightweight auth + device list check.
3. In `custom_components/harvia_sauna/__init__.py`:
   - instantiate provider based on config entry,
   - pass interface instance to coordinator.
4. Keep existing entries defaulted to current provider to avoid breaking upgrades.

Exit criteria:
- New installs can choose provider.
- Existing users continue working without reconfiguration.

### 6. Keep Entity Layer Stable
1. Ensure coordinator output shape remains unchanged (`HarviaDeviceData` fields still populated).
2. Only touch entity files (`climate.py`, `switch.py`, `sensor.py`, `number.py`, `binary_sensor.py`) if new API semantics require it.
3. If the new API exposes additional capabilities, gate them behind feature checks so unsupported devices/providers do not break.

Exit criteria:
- Entity IDs and behavior remain stable for existing users.

### 7. Add Tests Before Final Merge
1. Add tests under `tests/`:
   - provider selection tests,
   - auth failure and token refresh tests per provider,
   - payload normalization tests,
   - coordinator update path tests (poll + push/fallback),
   - regression tests for existing MyHarvia provider.
2. Add fixture payloads for both APIs in `tests/fixtures/`.
3. Mock network calls; avoid live API dependencies in CI.

Exit criteria:
- Both providers covered by automated tests.
- Existing behavior regressions are prevented.

### 8. Documentation, Diagnostics, and Migration Notes
1. Update `README.md`:
   - mention dual-provider support,
   - explain provider selection,
   - document provider-specific limitations.
2. Update diagnostics (`diagnostics.py`) to include provider type and sanitized provider metadata.
3. Update translations (`translations/*.json`) and `strings.json` for new config fields.
4. Add `docs/migration.md`:
   - how existing users remain on current provider,
   - how to switch providers safely.

Exit criteria:
- Users can configure and troubleshoot both providers.

## Similarity-First Implementation Rule
If API comparison shows high parity, prefer:
- shared endpoint discovery/auth/token-refresh helpers,
- shared request builders for common REST calls,
- thin provider-specific mappers only where field names differ.

Avoid introducing deep abstraction layers unless `DIVERGENT` areas make it necessary.

## Data Normalization Requirements
Both providers must normalize to current coordinator/entity expectations:
- device identity: stable `device_id`, `display_name`, firmware/version
- session state: `active`, `remaining_time`, `target_temp`, `current_temp`
- controls: light/fan/steam/aroma/dehumidifier flags and levels
- telemetry: humidity, RSSI, timestamps, status bits
- derived values: energy/session tracking still computed in coordinator

If a field is missing in the new API, use safe defaults and mark unavailable rather than throwing.

## Minimum Endpoint Mapping for This Integration
Use REST first for parity with current entities; add GraphQL subscriptions for push updates.

- Auth bootstrap:
  - `GET https://api.harvia.io/endpoints`
  - `POST {RestApi.generics}/auth/token`
  - `POST {RestApi.generics}/auth/refresh`
- Device discovery:
  - `GET {RestApi.device}/devices`
- Device state:
  - `GET {RestApi.device}/devices/state?deviceId=...&subId=C1`
- Latest telemetry:
  - `GET {RestApi.data}/data/latest-data?deviceId=...&cabinId=C1`
- Telemetry history (diagnostics/future features):
  - `GET {RestApi.data}/data/telemetry-history?...`
- Core controls:
  - Power on/off -> `POST {RestApi.device}/devices/command` with `command.type=SAUNA`
  - Light on/off -> `POST .../devices/command` with `command.type=LIGHTS`
  - Fan on/off -> `POST .../devices/command` with `command.type=FAN`
  - Target temp/humidity -> `PATCH {RestApi.device}/devices/target`
  - Profile changes (optional) -> `PATCH {RestApi.device}/devices/profile`

Push updates (recommended):
- Data feed subscription: `devicesMeasurementsUpdateFeed(receiver: <cognito:username>)` on `endpoints.GraphQL.data.https`
- Events feed subscription: `eventsFeed(receiver: <cognito:username>)` on `endpoints.GraphQL.events.https`

GraphQL fallbacks for state/control (optional):
- Query: `devicesStatesGet`
- Mutation: `devicesCommandsSend`, `devicesStatesUpdate`

## Rollout Strategy
1. Ship behind provider selection (default current provider).
2. Beta test with users on new provider.
3. Watch diagnostics/logs for mapping mismatches.
4. Promote new provider from experimental after stable releases.

## Definition of Done
- Two API providers implemented behind one interface.
- Existing MyHarvia users unaffected after upgrade.
- New API users can authenticate, read state, control devices, and receive timely updates.
- Tests pass for both providers.
- Docs and diagnostics updated.

## Implementation Status (Current)
- Completed:
  - API comparison and mapping docs (`docs/api_comparison.md`, `docs/api_mapping.md`)
  - Provider-neutral client interface and provider factory
  - Harvia REST/GraphQL provider with endpoint discovery + token login/refresh
  - Provider selection in config flow (default stays legacy provider)
  - Coordinator/provider wiring and diagnostics provider visibility
  - Push parity:
    - legacy provider push retained
    - Harvia provider subscriptions implemented for:
      - `devicesStatesUpdateFeed`
      - `devicesMeasurementsUpdateFeed`
- Added unit tests:
  - `tests/test_harviaio_push.py`
  - Coverage includes:
    - state/telemetry normalization mapping
    - subscription payload translation to coordinator update format
    - provider push lifecycle (start/stop manager hooks)

## CI Workflow
- Unit test workflow file: `.github/workflows/unit-tests.yaml`
- Triggers:
  - `push`
  - `pull_request`
  - `workflow_dispatch` (manual run)
- Test command:
  - `python -m unittest discover -s tests -p "test_*.py" -v`

## Workflow Maintenance Rule
- Any code addition in this integration that introduces or changes behavior requiring automated verification must include the corresponding CI workflow update in the same change set.
- Do not defer workflow updates to a later PR; add them automatically when implementing new behavior.

## Decisions from Recent Iterations (Must Keep)
- API naming in config flow/provider labels:
  - Original API: `myHarvia (Xenio controller)`
  - New API: `myHarvia 2 - harvia.io (Fenix controller)`
- Config flow text requirements:
  - Explain `heater_model` is used for naming in Home Assistant UI.
  - Explain `heater_power` is used for approximate absolute power calculation in watts (W).
- Credential management requirements:
  - Reconfigure flow must allow changing username and password after setup.
  - Password must never be prefilled in forms.
  - Existing password must remain unchanged unless user explicitly provides a new one.
  - If username/email changes, require a new password and validate credentials before saving.
  - Sensitive fields must stay redacted in diagnostics/logging.
  - Password-at-rest policy:
    - Do not store one-way hashed passwords for outbound API authentication flows (hashes cannot be used to log in).
    - Current implementation stores credentials in config-entry data per Home Assistant defaults.
    - Password fields must never be prefilled in forms and must stay redacted in diagnostics/logging.
- Localization consistency:
  - German translations must use consistent German wording (`Ofenmodell`, `Ofenleistung`) and avoid mixed Finnish labels.
- Discovery robustness:
  - Device discovery must handle multiple payload shapes and avoid hard failure when fallback GraphQL discovery is unauthorized.
  - Setup should not fail solely due to optional fallback discovery path errors.
- CI requirements:
  - Unit-test workflow with `push`, `pull_request`, and `workflow_dispatch` must remain in place.
  - New behavior changes require corresponding CI/test updates in the same PR automatically.
