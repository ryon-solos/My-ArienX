export const json = (body: unknown, status = 200) => Response.json(body, { status, headers: { "Cache-Control": "no-store" } })
export const bad = (message: string, status = 400) => json({ error: message }, status)
