import { writeFileSync } from "node:fs";
import type { Message, Mood, Proposal, Insider } from "../src/insider";
import { groqQuota } from "./quota";
export const candidates = ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"];
export type CharacterReply = { reply: string; mood: Mood; proposal: Proposal };
export interface DialogueProvider {
  respond(
    history: Message[],
    state: Pick<Insider, "heat" | "attempts" | "layer">,
  ): Promise<{ character: CharacterReply; evidence: unknown }>;
}
export { characterPrompt, validateCharacter } from "./character";
import { characterPrompt, validateCharacter } from "./character";
export class GroqProvider implements DialogueProvider {
  constructor(
    readonly model = process.env.GROQ_MODEL || "openai/gpt-oss-20b",
  ) {}
  async respond(
    history: Message[],
    state: Pick<Insider, "heat" | "attempts" | "layer">,
  ) {
    if (!process.env.GROQ_API_KEY)
      throw Error("Groq key unavailable on server.");
    if (!candidates.includes(this.model))
      throw Error("Model outside free-plan allowlist.");
    const messages = [
      {
        role: "system",
        content:
          characterPrompt +
          "\nAuthoritative state: " +
          JSON.stringify({
            heat: state.heat,
            attempts: state.attempts,
            layer: state.layer,
          }),
      },
      ...history,
    ];
    const text = JSON.stringify(messages);
    if (text.length > 14000) throw Error("Conversation context limit reached.");
    const n = groqQuota.reserve(text.length + 600);
    const start = Date.now();
    const res = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + process.env.GROQ_API_KEY,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: this.model,
        messages,
        response_format: {
          type: "json_schema",
          json_schema: {
            name: "sentinel_reply",
            strict: true,
            schema: {
              type: "object",
              properties: {
                reply: { type: "string" },
                mood: {
                  type: "string",
                  enum: [
                    "watchful",
                    "curious",
                    "suspicious",
                    "amused",
                    "alarmed",
                  ],
                },
                proposal: {
                  type: "string",
                  enum: ["TALK", "CHALLENGE", "FLAG", "UNLOCK_LAYER"],
                },
              },
              required: ["reply", "mood", "proposal"],
              additionalProperties: false,
            },
          },
        },
        temperature: 0.8,
        max_completion_tokens: 600,
        ...(this.model === "openai/gpt-oss-20b"
          ? { reasoning_effort: "low" }
          : { reasoning_effort: "none" }),
      }),
      signal: AbortSignal.timeout(25000),
    });
    if (!res.ok) {
      const failure = (await res.json().catch(() => ({}))) as any;
      const errorCode =
        typeof failure?.error?.code === "string"
          ? failure.error.code.slice(0, 80)
          : undefined;
      writeFileSync(
        `.private/groq-${n}.json`,
        JSON.stringify({
          model: this.model,
          status: res.status,
          errorCode,
          wall_ms: Date.now() - start,
        }),
        { mode: 0o600 },
      );
      throw Error(`Groq request failed (${res.status}). No retry made.`);
    }
    const data = (await res.json()) as any;
    const evidence = {
      model: data.model,
      id: data.id,
      usage: data.usage,
      wall_ms: Date.now() - start,
    };
    writeFileSync(
      `.private/groq-${n}.json`,
      JSON.stringify(evidence, null, 2),
      { mode: 0o600 },
    );
    let parsed;
    try {
      parsed = JSON.parse(data.choices?.[0]?.message?.content ?? "");
    } catch {
      throw Error("Invalid character response. No action executed.");
    }
    return { character: validateCharacter(parsed), evidence };
  }
}
