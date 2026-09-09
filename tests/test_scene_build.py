"""Tests for ``wraithguard.scene.build`` -- baking placements into meshes.

The mesh loader is injected, so no NIF/VFS IO: a fake loader returns known
meshes and we check the reference transform is baked into the vertices and that
each model is loaded once however many times it is placed.
"""

from __future__ import annotations

from wraithguard.nif.geometry import Mesh, Transform
from wraithguard.scene.build import build_scene
from wraithguard.scene.resolve import Placement


def _mesh() -> Mesh:
    return Mesh(name="s", vertices=[(1.0, 0.0, 0.0)], triangles=[(0, 0, 0)])


def _placed(model: str, translation: tuple[float, float, float]) -> Placement:
    return Placement(
        ref_id="r",
        model=model,
        transform=Transform(translation=translation),
        kind="placed",
    )


def test_the_reference_transform_is_baked_into_vertices() -> None:
    scene = build_scene([_placed("rock.nif", (10.0, 0.0, 0.0))], lambda _p: [_mesh()])
    assert scene.drawn == 1
    assert scene.meshes[0].vertices == [(11.0, 0.0, 0.0)]  # 1 + 10


def test_a_repeated_model_is_loaded_once() -> None:
    calls: list[str] = []

    def loader(path: str) -> list[Mesh]:
        calls.append(path)
        return [_mesh()]

    placements = [_placed("rock.nif", (0.0, 0.0, 0.0)), _placed("rock.nif", (5.0, 0.0, 0.0))]
    scene = build_scene(placements, loader)
    assert calls == ["rock.nif"]  # cached: one load for two placements
    assert scene.drawn == 2
    assert len(scene.meshes) == 2


def test_an_unloadable_model_is_recorded_not_fatal() -> None:
    scene = build_scene([_placed("gone.nif", (0.0, 0.0, 0.0))], lambda _p: None)
    assert scene.drawn == 0
    assert scene.missing_models == ["gone.nif"]
    assert scene.meshes == []


def test_non_drawable_and_empty_model_placements_are_skipped() -> None:
    called: list[str] = []
    placements = [
        Placement(ref_id="a", model="", transform=Transform(), kind="no_mesh_record"),
        Placement(ref_id="b", model="x.nif", transform=Transform(), kind="actor"),
        Placement(ref_id="c", model="", transform=Transform(), kind="placed"),
    ]
    scene = build_scene(placements, lambda p: called.append(p) or [_mesh()])
    assert called == []  # nothing drawable, so the loader is never touched
    assert scene.drawn == 0
