/**
 * Copy text to the clipboard, with a textarea+execCommand fallback for
 * non-secure contexts. The Wolf dev server runs over plain HTTP on a LAN
 * IP, where `navigator.clipboard.writeText` is undefined or throws —
 * without the fallback, copy actions silently do nothing.
 *
 * Once Phase 5.4 (native HTTPS via `wolf-cert`) lands the secure-context
 * branch will always be taken; the fallback can stay as a belt-and-braces.
 */
export async function copyText(text: string): Promise<boolean> {
  if (
    typeof navigator !== "undefined" &&
    navigator.clipboard &&
    window.isSecureContext
  ) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* fall through to the textarea fallback */
    }
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.left = "0";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
