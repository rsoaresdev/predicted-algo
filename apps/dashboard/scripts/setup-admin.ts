/**
 * One-time admin enrollment. Generates every secret the dashboard needs and
 * prints an env block to paste into .env.local (dev) or /etc/dashboard/dashboard.env
 * (prod). Nothing is written to disk by this script.
 *
 *   npm run setup:admin                 # interactive (hidden password prompt)
 *   npm run setup:admin -- --user admin # username via flag, prompt for password
 *
 * Produces: AUTH_SECRET, ADMIN_USERNAME, ADMIN_PASSWORD_HASH,
 *           TOTP_ENCRYPTION_KEY, TOTP_SECRET_ENC  (+ a QR code for your phone).
 */
import { generateSecret, generateURI } from "otplib";
import bcrypt from "bcryptjs";
import qrcode from "qrcode-terminal";
import { randomBytes } from "node:crypto";
import readline from "node:readline";
import { encryptWithKey, generateKeyB64 } from "../src/lib/crypto";

const ISSUER = "Trading Dashboard";
const BCRYPT_COST = 12;

function parseArgs(): { user?: string; password?: string } {
  const a = process.argv.slice(2);
  const out: { user?: string; password?: string } = {};
  for (let i = 0; i < a.length; i++) {
    if (a[i] === "--user") out.user = a[++i];
    else if (a[i] === "--password") out.password = a[++i];
  }
  return out;
}

function ask(prompt: string, hidden = false): Promise<string> {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: true,
  }) as readline.Interface & { stdoutMuted?: boolean; output?: NodeJS.WriteStream };

  // Echo "*" for hidden input instead of the typed characters.
  // @ts-expect-error patching the private writer is the standard mute trick
  rl._writeToOutput = (s: string) => {
    if (rl.stdoutMuted) process.stdout.write("*");
    else process.stdout.write(s);
  };

  return new Promise((resolve) => {
    rl.question(prompt, (answer) => {
      rl.close();
      if (hidden) process.stdout.write("\n");
      resolve(answer.trim());
    });
    rl.stdoutMuted = hidden;
  });
}

async function main() {
  const args = parseArgs();

  const username = args.user || (await ask("Admin username [admin]: ")) || "admin";
  let password = args.password;
  if (!password) {
    password = await ask("Admin password (hidden): ", true);
    const confirm = await ask("Confirm password (hidden): ", true);
    if (password !== confirm) {
      console.error("\n✗ Passwords do not match. Aborting.");
      process.exit(1);
    }
  }
  if (!password || password.length < 8) {
    console.error("\n✗ Password must be at least 8 characters. Aborting.");
    process.exit(1);
  }

  const passwordHash = await bcrypt.hash(password, BCRYPT_COST);
  const authSecret = randomBytes(32).toString("base64");
  const totpEncryptionKey = generateKeyB64();
  const totpSecret = generateSecret(); // base32
  const totpSecretEnc = encryptWithKey(totpSecret, totpEncryptionKey);
  const otpauth = generateURI({ issuer: ISSUER, label: username, secret: totpSecret });

  console.log("\n────────────────────────────────────────────────────────────");
  console.log(" Scan this QR with Google Authenticator / Authy:\n");
  qrcode.generate(otpauth, { small: true });
  console.log("\n If you cannot scan, add this key manually:");
  console.log(`   ${totpSecret}`);
  console.log("────────────────────────────────────────────────────────────");
  console.log("\n Paste the following into .env.local (dev) or");
  console.log(" /etc/dashboard/dashboard.env (prod) — then delete this output:\n");
  // The bcrypt hash is base64-encoded so the '$' characters never reach an env
  // loader (dotenv-expand/@next/env would otherwise mangle them). admin.ts
  // base64-decodes it before bcrypt.compare.
  const passwordHashB64 = Buffer.from(passwordHash, "utf8").toString("base64");

  console.log(`AUTH_SECRET=${authSecret}`);
  console.log(`ADMIN_USERNAME=${username}`);
  console.log(`ADMIN_PASSWORD_HASH=${passwordHashB64}`);
  console.log(`TOTP_ENCRYPTION_KEY=${totpEncryptionKey}`);
  console.log(`TOTP_SECRET_ENC=${totpSecretEnc}`);
  console.log("");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
