// A stale tab may export its inputs, but must not overwrite a newer library.
export function createProfileStorage(key, onConflict, onError) {
  let previous = null, stale = false;
  function conflict() {
    if (!stale) {stale = true; onConflict();}
  }
  window.addEventListener('storage', event => {
    if ((event.key === key || event.key === null) && event.newValue !== previous) conflict();
  });
  return {
    get outOfDate() {return stale;},
    load() {
      try {previous = localStorage.getItem(key); return previous;}
      catch {onError(); return null;}
    },
    save(value) {
      if (stale) return false;
      try {
        // Also catch a change whose storage event has not reached this tab yet.
        if (localStorage.getItem(key) !== previous) {conflict(); return false;}
        localStorage.setItem(key, value); previous = value;
        return true;
      } catch {onError(); return false;}
    },
  };
}
