## Feasibility assessment

- **Configuration & options flows** – Home Assistant’s data entry flow framework supports multi-step config and options dialogs driven by voluptuous schemas, making it practical to collect the endpoint, exposure lists, throttles, and routing policies described in the PRD.

- **Conversation agent registration** – Custom agents implement `AbstractConversationAgent.async_process` and register with the conversation manager, which keeps a per-agent registry keyed by config entry ID. This allows one agent per config entry, supports reload/unload, and feeds Assist with `IntentResponse` objects as required by the PRD.

- **Assist pipeline compatibility** – Assist pipelines drive utterance handling over the WebSocket API and surface device/pipeline metadata that can be forwarded to the AI runtime, so routing Assist speech/UI turns directly into the custom agent aligns with pipeline behavior.

- **State/event streaming** – Both the server-side helper `async_track_state_change_event` and the WebSocket `subscribe_events` command deliver full `state_changed` payloads (old/new state, context) for curated entities/domains, letting the integration normalize and forward updates as described while maintaining audit context.

- **Service execution & helper services** – Custom domains can register new services and, when invoked, call arbitrary Home Assistant services via `hass.services.async_call`, including returning structured responses or surfacing errors—covering the outbound action execution and helper wrappers in the PRD.

- **Audit surface & notifications** – The WebSocket API (and corresponding event bus primitives) supports firing custom events with full context, enabling `aiembodied.update_forwarded`/`aiembodied.action_executed` audit trails and persistent notifications for operators.

- **Conversation entities & responses** – The conversation platform shows how responses, speech, and chat logs are exposed to the UI, ensuring the AI’s replies and directives can be rendered and traced for supervisors as required.


No blockers were found in the documentation: each PRD requirement maps cleanly to supported Home Assistant APIs.

## Implementation plan

1. **Scaffold the integration**
   - Create `custom_components/aiembodied/manifest.json`, constants, and an aiohttp-based API client module for the upstream AI endpoint (respecting secrets). Wire up an async setup entry that instantiates per-entry runtime state and stores it in `hass.data`.
2. **Config & options flows**
   - Implement `ConfigFlow`/`OptionsFlow` handlers capturing endpoint credentials, curated exposure lists, throttles, and Assist routing policies using voluptuous selectors per the data entry flow patterns.

3. **Conversation agent implementation**
   - Define a class implementing `AbstractConversationAgent` that packages Assist requests (utterance, metadata) into upstream payloads and returns `ConversationResult`/`IntentResponse` values, registering/unregistering with `get_agent_manager(hass).async_set_agent(entry.entry_id, agent)` during setup/teardown.

4. **Assist routing helpers**
   - Add a helper service (e.g., `aiembodied.send_conversation_turn`) that scripts/automations can call to emit synthetic “speech” into the agent, reusing the `ConversationInput` plumbing.
5. **Event & script exposure**
   - Build a subscription controller that attaches `async_track_state_change_event` listeners for curated entities/domains, optional registry/area watchers, and script hooks that normalize payloads (entity, friendly name, area, context) before dispatching to the AI client. Emit `aiembodied.update_forwarded` via `hass.bus.async_fire` for auditability.

6. **Outbound action execution**
   - Register `aiembodied.invoke_service` plus convenience wrappers; validate targets, enforce policy, execute via `hass.services.async_call(..., blocking=True, return_response=True)` and forward results/errors back upstream. Fire `aiembodied.action_executed` events with correlation IDs.

7. **Autonomy controls & diagnostics**
   - Expose binary sensor, switch, and sensor entities driven by the integration state; manage pause/resume behavior by suspending listeners/action handlers. Raise persistent notifications on repeated upstream failures using existing HA services (via `async_call`). Tie verbose logging toggles to options flow settings.
8. **Networking & resilience**
   - Use the shared `aiohttp_client.async_get_clientsession` for outbound calls, add capped backoff retry logic, and drop events when unreachable while surfacing diagnostics.
9. **Observability**
   - Thread correlation IDs through inbound updates, agent decisions, and service executions; log INFO/DEBUG entries accordingly and optionally append trace data to HA’s conversation trace facility.
10. **Testing & examples**
    - Add unit tests for message normalization, agent request/response handling, throttling, and service validation using pytest fixtures. Provide pytest-based integration tests exercising config flow paths, event forwarding, action execution (success/failure), and autonomy toggles. Supply example YAML automations demonstrating script notifications and audit event consumption.

## Testing plan

### Automated coverage

- Unit tests (pytest) for:
  - Config/option flow validation and migration paths.
  - Event filtering, throttling, and normalization logic.
  - Conversation agent request/response translation, including error paths.
  - Service invocation policy enforcement and diagnostics entity updates.
- Integration tests using the Home Assistant pytest harness for:
  - Config entry setup/unload and option updates.
  - Simulated state changes triggering upstream notifications.
  - AI-issued service calls (allowed/denied) and audit event emission.
  - Pause/resume switch behavior impacting event forwarding and service execution.
- Static checks: mypy (if typing strict), ruff/flake8, and formatting to match repo standards.

### Manual verification on a Home Assistant server

1. Install the custom component under `<config>/custom_components/aiembodied`.
2. Restart Home Assistant, add an “Embodied AI” config entry with curated entities and options.
3. Confirm Assist pipelines list the new agent and route utterances through it; observe responses and audit events in the Logbook.
4. Trigger curated entity changes to verify updates reach the AI runtime and generate expected human-facing notifications.
5. Issue remote actions from the AI (e.g., light toggle) and confirm `aiembodied.action_executed` events plus policy enforcement.
6. Toggle `switch.aiembodied_autonomy` and simulate upstream outages to observe diagnostics entities and persistent notifications.
7. Review example automations to ensure script-injected context appears in the AI logs.

### Testing
⚠️ Tests not run (read-only QA review).
