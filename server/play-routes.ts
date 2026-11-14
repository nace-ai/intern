import { Router } from "express";
import { PlayService } from "./play-service";
import { makeRuntime } from "./byok";
import { groqQuota } from "./quota";
export const playRouter = Router();
// Read configuration lazily: dotenv is loaded by index before any request.
let service: PlayService | undefined;
playRouter.use(async (req, res) => {
  service ??= new PlayService(
    (key, documents) =>
      makeRuntime(key, documents, {
        groqKey: process.env.GROQ_API_KEY,
        offline: process.env.HEIST_OFFLINE === "true",
        reserveGroq: (units) => {
          groqQuota.reserve(units);
        },
      }),
    !!process.env.GROQ_API_KEY && process.env.HEIST_OFFLINE !== "true",
  );
  const headers = new Headers();
  for (const name of ["cookie", "origin", "content-type"])
    if (typeof req.headers[name] === "string")
      headers.set(name, req.headers[name]);
  const localOrigin =
    typeof req.headers.origin === "string" &&
    ["http://127.0.0.1:5173", "http://localhost:5173"].includes(
      req.headers.origin,
    )
      ? req.headers.origin
      : `http://${req.headers.host}`;
  const request = new Request(`${localOrigin}${req.originalUrl}`, {
    method: req.method,
    headers,
    ...(req.method !== "GET" ? { body: JSON.stringify(req.body) } : {}),
  });
  const result = await service.handle(request, req.ip || "local");
  res.status(result.status);
  result.headers.forEach((value, key) => res.setHeader(key, value));
  res.send(await result.text());
});
const janitor = setInterval(() => service?.sweep(), 30000);
janitor.unref();
