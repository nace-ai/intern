"""Offline tests: no API keys or network needed. They cover everything that doesn't ask Drex a question."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pixeldrex.cli import main, slugify
from pixeldrex.director import SceneSpec
from pixeldrex.life import World, neighbourhood
from pixeldrex.parts import EMPTY, PartsSprite, PoseSprite, tidy, tidy_group
from pixeldrex.pixels import PALETTE, Painting, Track, compose, smooth

ROOT = Path(__file__).resolve().parents[1]
WORLDS = sorted((ROOT / "worlds").glob("*.json"))


def blob(n: int, r0: int, r1: int, c0: int, c1: int) -> PartsSprite:
    grid = [[("body" if r0 <= r <= r1 and c0 <= c <= c1 else EMPTY) for c in range(n)] for r in range(n)]
    return PartsSprite("test blob", grid, {"body": "red"})


# ------------------------------------------------------------------ Drex Life


@pytest.mark.parametrize("path", WORLDS, ids=lambda p: p.stem)
def test_saved_worlds_load(path):
    world = World.from_dict(json.loads(path.read_text()))
    assert len(world.names) >= 2
    assert world.rules.strip()
    assert World.from_dict(world.to_dict()).names == world.names


def test_saved_world_titles_are_unique():
    titles = [json.loads(p.read_text())["title"] for p in WORLDS]
    assert len(titles) == len(set(titles))


def test_neighbourhood_has_eight_cells():
    grid = [["a", "b", "c"], ["d", "e", "f"], ["g", "h", "i"]]
    assert sorted(neighbourhood(grid, 1, 1)) == ["a", "b", "c", "d", "f", "g", "h", "i"]


# ------------------------------------------------------------------ Scenes


def test_parts_sprite_renders_with_outline():
    img = blob(8, 2, 5, 2, 5).image(outline=True)
    assert img.size == (10, 10)
    assert img.getpixel((0, 0))[3] == 0  # corners stay transparent


def test_tidy_removes_stray_pixels():
    s = blob(8, 2, 5, 2, 5)
    s.grid[0][7] = "body"  # a lone stray pixel
    assert sum(row.count("body") for row in tidy(s).grid) == 16


def test_pose_sprites_share_one_frame():
    small, big = blob(16, 10, 13, 6, 9), blob(16, 2, 13, 2, 13)
    poses = tidy_group([small, big])
    assert poses[0].n == poses[1].n
    sprite = PoseSprite(poses, ["sapling", "tree"])
    assert sprite.native(0).size == sprite.native(1).size
    assert sprite.native(99).size == sprite.native(1).size  # out-of-range poses clamp


def test_compose_applies_motion_tilt_squash_and_poses():
    color = next(iter(PALETTE))
    background = Painting([[color] * 16 for _ in range(16)])
    sprite = PoseSprite(tidy_group([blob(16, 4, 11, 4, 11), blob(16, 2, 13, 6, 9)]), ["a", "b"])
    frames = 4
    track = Track(
        xs=[10, 20, 30, 40],
        ys=[32] * 4,
        sizes=[16] * 4,
        tilts=[0, 10, -10, 0],
        squash=[0, 1, -1, 0],
        poses=[0, 1, 0, 1],
    )
    out = compose(background, [(sprite, track)], frames, 8, canvas=64)
    assert len(out) == frames
    assert len({im.size for im in out}) == 1
    assert len({im.tobytes() for im in out}) == frames  # every frame differs


def test_smooth_keeps_length_and_endpoints_bounded():
    values = [0, 10, 0, 10, 0]
    out = smooth(values)
    assert len(out) == len(values)
    assert min(values) <= min(out) and max(out) <= max(values)


def test_scene_spec_parses_poses():
    spec = SceneSpec.from_dict(
        {
            "title": "Sapling",
            "background": "a meadow",
            "sprites": [
                {
                    "name": "tree",
                    "look": "a small tree",
                    "motion": "grows",
                    "parts": [{"name": "trunk", "where": "bottom"}, {"name": "leaves", "where": "top"}],
                    "poses": [
                        {"name": "sapling", "parts": [{"name": "trunk", "where": "short"}]},
                        {"name": "tree", "parts": [{"name": "trunk", "where": "tall"}]},
                    ],
                }
            ],
        }
    )
    assert [p["name"] for p in spec.sprites[0].poses] == ["sapling", "tree"]
    assert SceneSpec.from_dict(spec.to_dict()).sprites[0].poses == spec.sprites[0].poses


def test_scene_spec_without_sprites_is_rejected():
    with pytest.raises(ValueError):
        SceneSpec.from_dict({"title": "empty", "sprites": []})


# ------------------------------------------------------------------ CLI and web app


def test_slugify():
    assert slugify("A Rocket, Launching at Night!") == "a-rocket-launching-at-night"
    assert slugify("!!!") == "scene"


@pytest.mark.parametrize("argv", [["--help"], ["life", "--help"], ["god", "--help"]])
def test_cli_help(argv):
    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    assert exit_info.value.code == 0


def test_web_app_routes():
    from pixeldrex.web import app

    client = TestClient(app)
    assert client.get("/").status_code == 200
    worlds = client.get("/api/worlds")
    assert worlds.status_code == 200 and len(worlds.json()) == len(WORLDS)
    assert client.get("/api/paint", params={"idea": "x", "res": 20}).status_code == 422
