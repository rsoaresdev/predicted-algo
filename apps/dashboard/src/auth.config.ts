/**
 * Edge-safe Auth.js config. Contains NO Node-only code (no bcrypt / node:crypto
 * / db) so it can run inside middleware on the edge runtime. The real Credentials
 * provider with its Node-only `authorize` lives in auth.ts.
 */
import type { NextAuthConfig } from "next-auth";

const useSecureCookies = process.env.NODE_ENV === "production";

export const authConfig = {
  // Behind nginx/Caddy we terminate TLS at the proxy; trust the forwarded host.
  trustHost: true,
  pages: { signIn: "/login" },
  session: {
    strategy: "jwt",
    maxAge: 60 * 60 * 8, // 8 hours
  },
  cookies: {
    sessionToken: {
      name: `${useSecureCookies ? "__Secure-" : ""}authjs.session-token`,
      options: {
        httpOnly: true,
        sameSite: "strict",
        path: "/",
        secure: useSecureCookies,
      },
    },
  },
  callbacks: {
    // The global gate: every matched route requires a logged-in user.
    // Returning false makes Auth.js redirect to `pages.signIn` (/login).
    authorized({ auth }) {
      return !!auth?.user;
    },
  },
  providers: [], // real provider added in auth.ts
} satisfies NextAuthConfig;
