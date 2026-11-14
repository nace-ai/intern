"""Director: an LLM on DeepInfra turns any idea into a scene spec the Drex engine can paint.

The director never draws. It only decides *what* to paint (background, up to 3 sprites, how each moves)
and rewrites the idea into simple, iconic subjects Drex paints well at 16x16.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from pixeldrex.llm import DeepInfra
from pixeldrex.pixels import PALETTE, SpriteSpec

SYSTEM = f"""You are the director of a tiny pixel-art animation studio. A painter that only answers
multiple-choice questions will paint your scene pixel by pixel on a 64x64 canvas (sprites are 32x32), so you must keep everything
SIMPLE and ICONIC, like a classic 8-bit video game.

Turn the user's idea into a JSON scene spec:
{{
  "title": "short title, max 5 words",
  "scene": "one sentence describing the whole animation",
  "background": "the scenery only, as 2-3 large flat color areas with positions, e.g. 'a light blue sky over green grass that fills the bottom quarter'",
  "sprites": [
    {{"name": "one or two words", "look": "what it looks like: shape and colors",
      "parts": [{{"name": "one or two words", "where": "its shape and where it sits in the sprite, e.g. 'the pointed tip at the very top'"}}],
      "symmetric": true,
      "poses": [{{"name": "short pose name", "parts": [{{"name": "...", "where": "..."}}]}}],
      "motion": "how it moves across the canvas, using words like far left, far right, top, bottom, center, rises, falls, grows, shrinks"}}
  ]
}}

Rules:
- 1 or 2 sprites (3 only if the idea truly needs it). Each sprite is ONE simple object (a cat, a star, a car),
  never a group or a crowd. Effects like a light beam, smoke or a splash belong to a sprite's parts, not to a
  separate sprite.
- Prefer subjects and views that read well as a FRONT-FACING, left-right symmetric icon: rockets, UFOs, hot air
  balloons, robots, ghosts, jellyfish, trees, houses, hearts, snowmen, faces. Show animals from the front (face
  and body facing the viewer) and mark them symmetric, unless the idea truly needs a side view.
- Describe sprites with bold shapes and 2-3 colors from this palette only: {", ".join(PALETTE)}.
- Give every sprite 2 to 5 parts that together make its shape (e.g. a rocket: nose cone, body, window, fins,
  flame). Each part's "where" says its shape and position inside the sprite (top, bottom, left and right side,
  center). Parts are what the painter places pixel by pixel, so make them big and distinct.
- If the sprite CHANGES SHAPE during the animation (wings flapping, a flame flickering, legs running, a sapling
  growing into a tree, a flower opening, a candle burning down, a mouth opening and closing), give it 2 to 4
  "poses" in order. Each pose has its own "parts" list using the SAME part names (and colors) as "parts", with
  "where" describing that part in that pose (e.g. pose "wings up": wings raised above the body; pose "wings down":
  wings lowered below the body). Leave "poses" empty for sprites that only move. Prefer ideas with a clear change.
- "symmetric" is true when the sprite looks the same mirrored left-to-right (front views: rockets, UFOs, faces,
  trees, houses, hearts) and false for side views (a running cat, a fish swimming, a car driving).
- The background contains no characters or objects that move; put anything that moves in a sprite.
- Name each background area with a DIFFERENT palette color word (e.g. "a lightgray wall over a brown floor"),
  never two shades of the same color, so the areas are clearly distinct.
- Make sprites contrast with the background behind them (no white sprite on a white sky).
- Never include text, letters, numbers or logos.
- Motion must be something a single object can do: move between positions, rise, fall, bounce, grow, shrink,
  or stay still. Say where it starts and where it ends.
- If the idea is abstract (an emotion, a company, a news headline), pick a concrete visual metaphor for it.
- Reply with the JSON object only."""


@dataclass
class SceneSpec:
    title: str
    scene: str
    background: str
    sprites: list[SpriteSpec] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "scene": self.scene,
            "background": self.background,
            "sprites": [asdict(s) for s in self.sprites],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SceneSpec":
        sprites = [
            SpriteSpec(
                str(s.get("name", "sprite"))[:30],
                str(s.get("look", s.get("name", ""))),
                str(s.get("motion", "stays in the center")),
                parts=[p for p in (s.get("parts") or []) if isinstance(p, dict) and p.get("name")][:6],
                poses=[p for p in (s.get("poses") or []) if isinstance(p, dict) and p.get("parts")][:4],
                symmetric=bool(s.get("symmetric", True)),
            )
            for s in (d.get("sprites") or [])[:3]
            if isinstance(s, dict)
        ]
        if not sprites:
            raise ValueError("scene spec has no sprites")
        return cls(
            title=str(d.get("title") or "Untitled")[:60],
            scene=str(d.get("scene") or ""),
            background=str(d.get("background") or "a plain light blue background"),
            sprites=sprites,
        )


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


async def direct(di: DeepInfra, idea: str, *, model: str | None = None, retries: int = 2) -> SceneSpec:
    """Ask the director LLM for a scene spec; retry on malformed output."""
    last_err: Exception | None = None
    prompt = f"Idea: {idea}"
    for _ in range(retries + 1):
        text = await di.chat(prompt, system=SYSTEM, model=model, max_tokens=600, temperature=0.4, json_mode=True)
        try:
            return SceneSpec.from_dict(_parse_json(text))
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            prompt = f"Idea: {idea}\n\nYour previous reply was not a valid scene spec ({e}). Reply with the JSON object only."
    raise RuntimeError(f"director failed to produce a scene spec: {last_err}")
