// Web Crypto's randomUUID() is only available in secure contexts (HTTPS)
// or on localhost.  Plain-HTTP LAN access (a common dev setup) doesn't get
// it, so we need a fallback so the UI doesn't crash.
//
// Don't use this where cryptographic randomness matters — it's only for
// client-side IDs (conversation IDs, fallback exchange IDs, etc.).

export function randomId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
