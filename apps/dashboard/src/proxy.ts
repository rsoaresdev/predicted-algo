/**
 * Global authentication gate — Next.js 16 "proxy" convention (replaces the
 * deprecated middleware.ts). Proxy runs on the Node.js runtime.
 *
 * Built from the edge-safe authConfig (no bcrypt / node:crypto here); the
 * `authorized` callback redirects any request without a valid session to /login.
 *
 * Runs on EVERY route except:
 *   - /api/auth/*   (Auth.js sign-in/out endpoints)
 *   - /login        (the only public page)
 *   - Next.js internals and static asset files
 */
import NextAuth from "next-auth";
import { authConfig } from "@/auth.config";

const { auth } = NextAuth(authConfig);

export default auth;

export const config = {
  matcher: [
    "/((?!api/auth|login|_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
