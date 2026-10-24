// Whether this browser has opened (or dismissed) the "Getting started" guide -
// the dashboard shows its "New here?" banner until then. A per-browser
// convenience only: storage may be unavailable, which just shows the banner.

const KEY = "sdai_guide_seen";

export function guideSeen(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function markGuideSeen(): void {
  try {
    localStorage.setItem(KEY, "1");
  } catch {
    // storage blocked - the banner simply comes back
  }
}
