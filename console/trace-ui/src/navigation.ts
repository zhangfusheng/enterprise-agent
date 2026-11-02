export function subscribeToLocation(callback: () => void) {
  window.addEventListener("popstate", callback);
  return () => window.removeEventListener("popstate", callback);
}

export function navigate(
  updates: Record<string, string | null>,
  replace = false,
  cursorHistory?: string[],
) {
  const url = new URL(window.location.href);
  Object.entries(updates).forEach(([key, value]) =>
    value ? url.searchParams.set(key, value) : url.searchParams.delete(key),
  );
  const state = { ...window.history.state };
  if ("cursor" in updates && !updates.cursor) state.traceCursors = [];
  if (cursorHistory) state.traceCursors = cursorHistory;
  window.history[replace ? "replaceState" : "pushState"](state, "", url);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function cursorTrail(): string[] {
  const value: unknown = window.history.state?.traceCursors;
  return Array.isArray(value) && value.every((item) => typeof item === "string")
    ? value
    : [];
}
