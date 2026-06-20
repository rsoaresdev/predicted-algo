/**
 * TOTP verification (Google Authenticator / Authy compatible).
 *
 * The shared secret is decrypted from TOTP_SECRET_ENC once and cached in memory.
 * A small replay guard rejects a code that was already accepted, so a captured
 * code cannot be reused inside its validity window. Node-only.
 */
import { verifySync } from "otplib";
import { decryptSecret } from "./crypto";

// Allow ±30s (one time step) for clock skew between server and phone.
const EPOCH_TOLERANCE_SECONDS = 30;

let cachedSecret: string | null = null;

function getSecret(): string {
  if (cachedSecret) return cachedSecret;
  const enc = process.env.TOTP_SECRET_ENC;
  if (!enc) throw new Error("TOTP_SECRET_ENC is not set.");
  cachedSecret = decryptSecret(enc);
  return cachedSecret;
}

let lastAccepted: string | null = null;

export function verifyTotp(rawToken: string): boolean {
  const token = (rawToken ?? "").replace(/\s+/g, "");
  if (!/^\d{6}$/.test(token)) return false;
  // Reject immediate replay of a code we already accepted.
  if (token === lastAccepted) return false;
  let ok = false;
  try {
    ok = verifySync({
      secret: getSecret(),
      token,
      epochTolerance: EPOCH_TOLERANCE_SECONDS,
    }).valid;
  } catch {
    return false;
  }
  if (ok) lastAccepted = token;
  return ok;
}
