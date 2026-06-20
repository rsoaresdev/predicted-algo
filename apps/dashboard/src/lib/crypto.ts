/**
 * AES-256-GCM helpers for encrypting the TOTP secret at rest.
 *
 * The secret is never stored in plaintext: it lives encrypted in
 * TOTP_SECRET_ENC and is only decrypted in memory inside the Node runtime
 * (login verification + the setup script). The 32-byte key lives in
 * TOTP_ENCRYPTION_KEY (base64). Node-only — must never be imported by the edge
 * middleware.
 */
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";

const ALG = "aes-256-gcm";
const IV_LEN = 12;
const TAG_LEN = 16;

function keyFromBase64(b64: string): Buffer {
  const key = Buffer.from(b64, "base64");
  if (key.length !== 32) {
    throw new Error("TOTP_ENCRYPTION_KEY must decode to exactly 32 bytes (base64).");
  }
  return key;
}

function envKey(): Buffer {
  const b64 = process.env.TOTP_ENCRYPTION_KEY;
  if (!b64) throw new Error("TOTP_ENCRYPTION_KEY is not set.");
  return keyFromBase64(b64);
}

/** Encrypt with an explicit key (used by the setup script). */
export function encryptWithKey(plain: string, keyB64: string): string {
  const iv = randomBytes(IV_LEN);
  const cipher = createCipheriv(ALG, keyFromBase64(keyB64), iv);
  const ct = Buffer.concat([cipher.update(plain, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, ct]).toString("base64");
}

/** Decrypt using the key from TOTP_ENCRYPTION_KEY. */
export function decryptSecret(blob: string): string {
  const raw = Buffer.from(blob, "base64");
  const iv = raw.subarray(0, IV_LEN);
  const tag = raw.subarray(IV_LEN, IV_LEN + TAG_LEN);
  const ct = raw.subarray(IV_LEN + TAG_LEN);
  const decipher = createDecipheriv(ALG, envKey(), iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(ct), decipher.final()]).toString("utf8");
}

/** Generate a fresh base64 32-byte key (setup script). */
export function generateKeyB64(): string {
  return randomBytes(32).toString("base64");
}
