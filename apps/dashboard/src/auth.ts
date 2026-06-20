/**
 * Full Auth.js setup (Node runtime). Credentials provider verifies the single
 * admin (username + bcrypt password + TOTP). JWT sessions — no database writes,
 * so the dashboard's DB connection stays strictly read-only.
 */
import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import { authConfig } from "./auth.config";
import { loginSchema } from "./lib/schemas";
import { verifyAdmin } from "./lib/admin";

export const { handlers, auth, signIn, signOut } = NextAuth({
  ...authConfig,
  providers: [
    Credentials({
      credentials: {
        username: { label: "Username", type: "text" },
        password: { label: "Password", type: "password" },
        token: { label: "2FA code", type: "text" },
      },
      authorize: async (raw) => {
        const parsed = loginSchema.safeParse(raw);
        if (!parsed.success) return null;
        const { username, password, token } = parsed.data;
        const ok = await verifyAdmin(username, password, token);
        if (!ok) return null;
        // Minimal principal — no PII, single admin.
        return { id: "admin", name: username };
      },
    }),
  ],
});
