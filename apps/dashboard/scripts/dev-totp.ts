/**
 * DEV ONLY — print the current 2FA code (and a QR to enroll it once) from the
 * TOTP secret already in .env.local. Lets you log into the local dev dashboard
 * without having scanned the QR during setup.
 *
 *   npm run dev:totp
 */
import { config } from "dotenv";
config({ path: ".env.local" });
import { generateSync, generateURI } from "otplib";
import qrcode from "qrcode-terminal";
import { decryptSecret } from "../src/lib/crypto";

const secret = decryptSecret(process.env.TOTP_SECRET_ENC!);
const user = process.env.ADMIN_USERNAME ?? "admin";

console.log(`\nCurrent 2FA code for "${user}":  \x1b[1m${generateSync({ secret })}\x1b[0m`);
console.log("(valid ~30s — re-run for a fresh one)\n");
console.log("Scan once to add it to Google Authenticator / Authy:\n");
qrcode.generate(generateURI({ issuer: "Algo Desk (dev)", label: user, secret }), {
  small: true,
});
