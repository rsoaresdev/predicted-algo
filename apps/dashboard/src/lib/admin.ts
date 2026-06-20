/**
 * Single-admin credential verification. All three factors are evaluated
 * before returning, so the boolean AND does not short-circuit and leak timing
 * about which factor failed. Node-only (bcrypt + node:crypto + TOTP).
 */
import "server-only";
import bcrypt from "bcryptjs";
import { timingSafeEqual } from "node:crypto";
import { verifyTotp } from "./totp";

function constantTimeEquals(a: string, b: string): boolean {
  const ab = Buffer.from(a, "utf8");
  const bb = Buffer.from(b, "utf8");
  if (ab.length !== bb.length) {
    // Compare against self to keep timing roughly constant, then fail.
    timingSafeEqual(ab, ab);
    return false;
  }
  return timingSafeEqual(ab, bb);
}

export async function verifyAdmin(
  username: string,
  password: string,
  token: string,
): Promise<boolean> {
  const expectedUser = process.env.ADMIN_USERNAME;
  // The bcrypt hash is stored base64-encoded: a raw bcrypt hash contains '$',
  // which dotenv-expand (used by Next via @next/env) would mangle. Base64 has no
  // '$', so it survives every env loader (dotenv, systemd, Docker) intact.
  const passwordHashB64 = process.env.ADMIN_PASSWORD_HASH;
  if (!expectedUser || !passwordHashB64) return false;
  const passwordHash = Buffer.from(passwordHashB64, "base64").toString("utf8");

  const userOk = constantTimeEquals(username, expectedUser);
  const passOk = await bcrypt.compare(password, passwordHash).catch(() => false);
  const totpOk = verifyTotp(token);

  return userOk && passOk && totpOk;
}
