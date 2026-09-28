import { Netlify } from "@netlify/functions"

export async function reply(message: string, provider = "gemini") {
  const text = String(message || "").slice(0, 12_000)
  if (!text) throw new Error("message required")
  if (provider === "openrouter") {
    const key = Netlify.env.get("OPENROUTER_API_KEY")
    if (!key) throw new Error("OpenRouter is not configured")
    const response = await fetch("https://openrouter.ai/api/v1/chat/completions", { method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" }, body: JSON.stringify({ model: Netlify.env.get("OPENROUTER_MODEL") || "openrouter/auto", messages: [{ role: "user", content: text }] }) })
    if (!response.ok) throw new Error("OpenRouter request failed")
    const data = await response.json() as { choices?: Array<{ message?: { content?: string } }> }
    return data.choices?.[0]?.message?.content || "No response."
  }
  const key = Netlify.env.get("GEMINI_API_KEY")
  if (!key) throw new Error("Gemini is not configured")
  const model = Netlify.env.get("GEMINI_CLOUD_MODEL") || "gemini-3.8-flash"
  const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${encodeURIComponent(key)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ contents: [{ parts: [{ text }] }] }) })
  if (!response.ok) throw new Error("Gemini request failed")
  const data = await response.json() as { candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }> }
  return data.candidates?.[0]?.content?.parts?.map(part => part.text || "").join("") || "No response."
}

export async function research(query: string) {
  const key = Netlify.env.get("GEMINI_API_KEY")
  if (!key) throw new Error("Gemini is not configured")
  const model = Netlify.env.get("GEMINI_CLOUD_MODEL") || "gemini-3.8-flash"
  const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${encodeURIComponent(key)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ contents: [{ parts: [{ text: `Research this current question and cite the sources used: ${String(query).slice(0, 12_000)}` }] }], tools: [{ google_search: {} }] }) })
  if (!response.ok) throw new Error("Gemini research request failed")
  const data = await response.json() as { candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }> }
  return data.candidates?.[0]?.content?.parts?.map(part => part.text || "").join("") || "No response."
}
