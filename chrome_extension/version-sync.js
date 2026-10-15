const WCA_EXTENSION_PROTOCOL_VERSION = 1;
const WCA_EXTENSION_RELOAD_GUARD_KEY = "wcaExtensionReloadGuard";

function decideExtensionVersionAction(command, currentVersion, storedGuard) {
  if (command.action === "reload_extension") {
    const guard = {
      fromVersion: currentVersion,
      toVersion: command.expected_extension_version,
      protocolVersion: command.protocol_version
    };
    const alreadyAttempted = Boolean(
      storedGuard
      && storedGuard.fromVersion === guard.fromVersion
      && storedGuard.toVersion === guard.toVersion
      && storedGuard.protocolVersion === guard.protocolVersion
    );
    return { blockTasks: true, reload: !alreadyAttempted, guard, clearGuard: false };
  }
  if (command.action === "manual_reload_required" || command.action === "incompatible_extension") {
    return { blockTasks: true, reload: false, guard: null, clearGuard: false };
  }
  return {
    blockTasks: false,
    reload: false,
    guard: null,
    clearGuard: Boolean(storedGuard)
  };
}
