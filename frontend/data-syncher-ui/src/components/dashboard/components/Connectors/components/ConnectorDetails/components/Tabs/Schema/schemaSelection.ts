/** Backend / JSON may expose `selected` as bool, 0/1, or legacy strings — avoid truthy pitfalls. */
export function isConnectorTableMarkedSelected(selected: unknown): boolean {
  if (selected === true || selected === 1) return true;
  if (
    selected === false ||
    selected === 0 ||
    selected === null ||
    selected === undefined
  )
    return false;
  if (typeof selected === "string") {
    const s = selected.trim().toLowerCase();
    return s === "true" || s === "1" || s === "yes";
  }
  return Boolean(selected);
}
