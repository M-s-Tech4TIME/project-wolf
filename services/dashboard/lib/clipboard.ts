/**
 * Copy text to the clipboard, with a textarea+execCommand fallback for
 * non-secure contexts.
 *
 * Phase 5.4 update: when Wolf is running with self-signed certs minted
 * by `wolf-cert init`, the dev server serves HTTPS and the browser
 * sees a secure context, so the `navigator.clipboard.writeText` branch
 * is taken on every modern browser. The execCommand fallback is now
 * the HTTP-fallback path — exercised only when the operator hasn't
 * run `wolf-cert init` yet (or has revoked the certs deliberately).
 * Kept as belt-and-braces so plain-HTTP dev keeps working without a
 * forced HTTPS dependency.
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
