import { useEffect, useState } from "react";

/** Delays reflecting a fast-changing value (typically a search input) by `delayMs` -- lets a
 * page debounce a server-side search request instead of firing one on every keystroke. Shared by
 * every dashboard's search box (Citizen/Worker/Admin/My Area) now that complaint search is a real
 * backend query, not a client-side filter over an already-fetched list. */
export function useDebouncedValue<T>(value: T, delayMs = 350): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}
