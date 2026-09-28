import { acceptInvite, confirmEmail, getUser, login, logout, verifyRequestOrigin } from "@netlify/identity"
import type { Config } from "@netlify/functions"
import { bad, json } from "./_shared/http.js"

const session = async () => {
  const user = await getUser()
  return user ? json({ authenticated: true, email: user.email, id: user.id }) : json({ authenticated: false }, 401)
}

export default async (req: Request) => {
  const action = new URL(req.url).pathname.split("/").pop()
  try {
    if (action === "session" && req.method === "GET") return session()
    if (req.method !== "POST") return bad("method not allowed", 405)
    verifyRequestOrigin(req)
    const body = await req.json()
    if (action === "login") {
      await login(String(body.email || ""), String(body.password || ""))
      return session()
    }
    if (action === "invite") {
      await acceptInvite(String(body.token || ""), String(body.password || ""))
      return session()
    }
    if (action === "confirm") {
      await confirmEmail(String(body.token || ""))
      return session()
    }
    if (action === "logout") {
      await logout()
      return json({ authenticated: false })
    }
    return bad("not found", 404)
  } catch (error) {
    return bad(error instanceof Error ? error.message : "authentication failed", 401)
  }
}

export const config: Config = { path: ["/api/auth/session", "/api/auth/login", "/api/auth/invite", "/api/auth/confirm", "/api/auth/logout"] }
