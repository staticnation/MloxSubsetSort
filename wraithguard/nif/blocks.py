"""Field layouts for the NIF blocks Morrowind actually ships.

Each layout is a flat tuple of ``(name, kind)`` pairs, read in order. Kinds that
depend on an earlier field name that field, so the walker can look the count or
the flag up in what it has already read -- which is the whole of the grammar
needed here, and deliberately no more. NIF's real schema has conditionals,
version ranges and inheritance; encoding all of that would be re-implementing
``nif.xml``, and the point of restricting to one game version is that the
resulting layouts are flat.

**Inheritance is expanded, not modelled.** ``NiNode`` is written out as
``NiAVObject``'s fields followed by its own, because a block is a byte stream
and the reader only ever walks forward. Sharing is done with tuple
concatenation, which keeps the duplication out of the data without introducing
a class hierarchy that would have to be resolved at parse time.

**Coverage is measured against the engine, not against a wish list.** The
Morrowind engine supports a fixed, enumerated set of NIF objects -- the
"Supported by Engine" table in the community's *Notes for Modmakers*
(``morrowind-nif.github.io``) lists them. Anything outside it cannot appear in
a mesh the game will load, so that table is the denominator: this reader is
"done" when it covers the objects that actually occur, not all 112.

Coverage is now complete for the game as shipped. Against the 7,343 meshes of
a vanilla install, ``tools/check_nif_layouts.py --verify`` reports **7,339
identical, 0 stopped early and 0 diverged**; the remaining 4 are files where
the cross-checking scan cannot reconcile with the header and so cannot act as
a reference, not files the reader struggles with.

Getting there took 21 further layouts, added one at a time, each confirmed by
landing exactly on the following type name and by agreeing with a scan that
uses no layout knowledge at all. That discipline was not ceremony: it caught a
one-byte error in ``NiGeomMorpherController`` that had been reported as a
missing block type, and it is the reason a batch of guesses was never
committed. A layout guessed wrong does not fail where the guess was.

Four blocks contain fields that could not be identified from the bytes. Those
are stepped over as *measured spans* with names that admit it --
``emitter_parameters``, ``unidentified_tail``, ``path_parameters``,
``projection`` -- because the width is what the rest of the file depends on,
and an invented field name is worse than an admitted gap: it gets believed.

**These layouts want verification against real files.** They are written from
the publicly documented format; a wrong field width does not raise, it
desynchronises the rest of the file, and the reader will usually surface that as
an unknown block type several blocks later. :func:`~wraithguard.nif.read_nif`
reports how far it got for exactly that reason.
"""

from __future__ import annotations

from typing import Final, Literal

#: How one field is stored. Everything is little-endian.
#:
#: * ``u8`` / ``u16`` / ``u32`` / ``i32`` -- plain integers.
#: * ``f32`` -- a float.
#: * ``bool32`` -- Morrowind stores booleans as a 32-bit word. Later NIF
#:   versions narrowed it to a byte, which is precisely the sort of difference
#:   that makes a general reader expensive and a single-version one cheap.
#: * ``string`` -- ``u32`` length followed by that many bytes, not terminated.
#: * ``link`` -- ``i32`` index of another block, ``-1`` for none.
#: * ``ref_list`` -- ``u32`` count followed by that many links.
#: * ``matrix33`` / ``vector3`` / ``color4`` -- fixed runs of floats.
FieldKind = Literal[
    "u8",
    "u16",
    "u32",
    "i32",
    "f32",
    "bool32",
    "string",
    "link",
    "ref_list",
    "matrix33",
    "vector3",
    "color4",
]

#: Fixed byte widths, for the kinds that have one. Sizes the walker cannot infer
#: from the stream are absent and handled explicitly.
FIXED_WIDTHS: Final[dict[str, int]] = {
    "u8": 1,
    "u16": 2,
    "u32": 4,
    "i32": 4,
    "f32": 4,
    "bool32": 4,
    "link": 4,
    "vector3": 12,
    "color4": 16,
    "matrix33": 36,
}

#: One field: ``(name, kind)``, or ``(name, kind, gate)`` when the field is
#: present only if an earlier boolean in the same block is set. The gate is
#: written out rather than inferred from the field name, because inferring it
#: is exactly the bug this table shipped with: ``vertices`` and ``normals`` are
#: the same *kind*, so a single hardcoded gate read normals whenever vertices
#: were present and desynchronised every mesh that had one without the other.
Field = tuple[str, str] | tuple[str, str, str]

Layout = tuple[Field, ...]

#: Every named object: a name, and links to its first extra-data block and its
#: first controller. Both are ``-1`` when absent.
_NI_OBJECT_NET: Final[Layout] = (
    ("name", "string"),
    ("extra_data", "link"),
    ("controller", "link"),
)

#: Anything with a place in the scene: transform, property list, optional
#: bounding box. The bounding box is the one conditional here -- its presence is
#: a flag in the stream, so the walker has to branch on a value it just read.
_NI_AV_OBJECT: Final[Layout] = (
    *_NI_OBJECT_NET,
    ("flags", "u16"),
    ("translation", "vector3"),
    ("rotation", "matrix33"),
    ("scale", "f32"),
    ("velocity", "vector3"),
    ("properties", "ref_list"),
    ("has_bounding_box", "bool32"),
    ("bounding_box", "bounding_box"),
)

#: A property block's own preamble.
_NI_PROPERTY: Final[Layout] = (*_NI_OBJECT_NET, ("flags", "u16"))

#: A node: children and effects on top of a scene object. ``RootCollisionNode``
#: and ``AvoidNode`` are the same shape, which is why collision detection is a
#: question about the block *type* rather than about any field.
_NI_NODE: Final[Layout] = (
    *_NI_AV_OBJECT,
    ("children", "ref_list"),
    ("effects", "ref_list"),
)

#: Every controller's preamble: where it sits in the controller chain, when it
#: runs, and what it drives.
#: A scene object that projects something onto the nodes it affects. Taken
#: from tes3: an AV object followed by the list of nodes it applies to.
_NI_DYNAMIC_EFFECT: Final[Layout] = (
    *_NI_AV_OBJECT,
    ("affected_nodes", "ref_list"),
)

#: Every light's shared body: a dimmer and three colors. Taken from tes3.
#: The concrete light types differ only in what follows this.
_NI_LIGHT: Final[Layout] = (
    *_NI_DYNAMIC_EFFECT,
    ("dimmer", "f32"),
    ("ambient_color", "vector3"),
    ("diffuse_color", "vector3"),
    ("specular_color", "vector3"),
)

_NI_TIME_CONTROLLER: Final[Layout] = (
    ("next_controller", "link"),
    ("flags", "u16"),
    ("frequency", "f32"),
    ("phase", "f32"),
    ("start_time", "f32"),
    ("stop_time", "f32"),
    ("target", "link"),
)

#: Particle geometry: the same vertex, normal, color and UV block a mesh
#: carries, followed by the per-particle extras. Derived by reconciling block
#: lengths across six fixtures spanning 12 to 1000 particles and every
#: combination of the optional arrays -- 240, 704, 2288, 660, 32052 and 48052
#: bytes, each accounted for exactly. Two fixtures with 1000 particles differ
#: by precisely 16000 bytes, which is what identified the rotation array below
#: as sixteen bytes per particle behind its own flag.
_NI_PARTICLES_DATA: Final[Layout] = (
    ("num_vertices", "u16"),
    ("has_vertices", "bool32"),
    ("vertices", "vec3_array", "has_vertices"),
    ("has_normals", "bool32"),
    ("normals", "vec3_array", "has_normals"),
    ("center", "vector3"),
    ("radius", "f32"),
    ("has_vertex_colors", "bool32"),
    ("vertex_colors", "color4_array", "has_vertex_colors"),
    ("num_uv_sets", "u16"),
    ("has_uv", "bool32"),
    ("uv_sets", "uv_array", "num_uv_sets"),
    ("num_particles", "u16"),
    ("particle_radius", "f32"),
    ("num_active", "u16"),
    ("has_sizes", "bool32"),
    ("sizes", "opt_float_array", "has_sizes"),
)

#: ``NiRotatingParticlesData`` is ``NiParticlesData`` followed by an optional
#: per-particle rotation array. Shared so ``NiParticleMeshesData`` -- which is
#: this same body plus one trailing link -- can build on it without repeating
#: the fields or the reasoning behind them.
_NI_ROTATING_PARTICLES_DATA: Final[Layout] = (
    *_NI_PARTICLES_DATA,
    ("has_rotations", "bool32"),
    ("rotations", "quat_array", "has_rotations"),
)

#: Every particle modifier's preamble: the next modifier in the chain and the
#: controller that owns it. Both are ``-1`` when absent, and both were read off
#: the fixtures directly -- the values are block indices or -1 in every file.
_NI_PARTICLE_MODIFIER: Final[Layout] = (
    ("next_modifier", "link"),
    ("controller", "link"),
)

#: Extra data blocks chain to one another and declare their own length.
_NI_EXTRA_DATA: Final[Layout] = (("next_extra_data", "link"), ("bytes_remaining", "u32"))

#: The geometry-data base every mesh-data block shares: optional vertex, normal,
#: colour and UV arrays around a bounding sphere. ``NiTriShapeData`` and
#: ``NiTriStripsData`` inline these same fields and then add their own topology;
#: ``NiLinesData`` reuses them and adds only per-vertex connectivity flags.
_NI_GEOMETRY_DATA: Final[Layout] = (
    ("num_vertices", "u16"),
    ("has_vertices", "bool32"),
    ("vertices", "vec3_array", "has_vertices"),
    ("has_normals", "bool32"),
    ("normals", "vec3_array", "has_normals"),
    ("center", "vector3"),
    ("radius", "f32"),
    ("has_vertex_colors", "bool32"),
    ("vertex_colors", "color4_array", "has_vertex_colors"),
    ("num_uv_sets", "u16"),
    ("has_uv", "bool32"),
    ("uv_sets", "uv_array", "num_uv_sets"),
)

#: The blocks this reader understands, by the type string written before each
#: one in the file. Anything absent stops the read rather than being skipped:
#: no block carries a length, so there is nothing to skip *by*.
BLOCK_LAYOUTS: Final[dict[str, Layout]] = {
    # -- scene graph ------------------------------------------------------
    "NiNode": _NI_NODE,
    "RootCollisionNode": _NI_NODE,
    "AvoidNode": _NI_NODE,
    # Known-malformed file, recorded so it is not re-investigated as a layout
    # gap: dbs_meatstick.nif stops inside one of these with a property count of
    # 0xFFFFFFFF. Its 26 block type names reconcile exactly with the header, so
    # the block *boundaries* are sound and the block's *contents* are not.
    # Inspected in NifSkope, which tolerates it and shows orphaned blocks.
    # Refusing it is the right behaviour: the alternative is inventing a layout
    # to fit one broken file and breaking the sound ones.
    #
    # **Settled on 28 July 2026.** `--verify` over 80,197 mod meshes reports
    # exactly one stop inside this type, and its own message hedges: "usually a
    # layout bug here; sometimes a malformed file". That hedge is now resolved.
    # tes3 declares NiBSParticleNode as a bare NiNode -- identical to this --
    # so the layout is not the problem and the file is. Two independent
    # implementations agreeing is what turns "probably malformed" into a
    # finding about the mod.
    "NiBSParticleNode": _NI_NODE,
    "NiBSAnimationNode": _NI_NODE,
    # No billboard_mode field: later NIF versions carry one, 4.0.0.2 does not.
    # Verified against BM_Snow_01.NIF, where the speculative u16 left the
    # reader two bytes past the next type string. Morrowind's billboarding has
    # no mode to choose -- the node always faces the camera.
    "NiBillboardNode": _NI_NODE,
    # A node plus one word. Four fixtures, all 4 bytes past the node's shape.
    # Absent from vanilla entirely, and the single commonest reason a modded
    # mesh stopped: 2,127 files in one mod collection.
    "NiSwitchNode": (*_NI_NODE, ("active_child", "u32")),
    # A node, a sorting mode and a link to the sub-sorter. Taken from tes3.
    "NiSortAdjustNode": (*_NI_NODE, ("sorting_mode", "u32"), ("sub_sorter", "link")),
    # -- taken from tes3, not derived ------------------------------------
    #
    # The five layouts below come from Greatness7's `tes3` (MIT), read on
    # 28 July 2026 under the licence recorded in NIF_PROVENANCE.md. They are
    # marked because they are a *different kind of fact* from the rest of this
    # table: everything else here was derived from bytes and survives the
    # exact-landing test, while these are transcriptions that have been
    # confirmed against only the handful of sample files that carry them.
    #
    # None of these types appears in vanilla Morrowind or in the 80,197-mesh
    # mod corpus. They stopped the reader on files from the categorised NIF
    # sample archive, which is how they were found at all -- a gap no corpus
    # measurement could reveal, because a type absent from the corpus produces
    # no failure to count.
    #
    # A switch node plus a period.
    "NiFltAnimationNode": (*_NI_NODE, ("active_child", "u32"), ("period", "f32")),
    # A plain node; the collision behaviour is carried in the node's flags.
    "NiCollisionSwitch": _NI_NODE,
    # A node plus a plane: a normal and a distance.
    "NiBSPNode": (
        *_NI_NODE,
        # A plane: a normal and a distance from the origin.
        ("plane_normal", "vector3"),
        ("plane_distance", "f32"),
    ),
    # A node, 16 bytes, then a count and eight bytes per level. Solved from
    # the counts rather than assumed: a fixture with 4 levels leaves 52 bytes
    # past the node's shape and one with 1 level leaves 28, and 16 + 4 + n*8
    # accounts for both exactly. The count in the stream also equals the
    # child count in every fixture, which is what a level-of-detail node
    # should look like -- one range per child.
    "NiLODNode": (*_NI_NODE, ("lod_center", "skip:16"), ("lod_levels", "lod_level_array")),
    # -- geometry ---------------------------------------------------------
    "NiTriShape": (*_NI_AV_OBJECT, ("data", "link"), ("skin_instance", "link")),
    # -- lights, taken from tes3 ------------------------------------------
    #
    # None appears in vanilla or the mod corpus -- Morrowind lights cells
    # rather than meshes -- but they occur in the sample archive, and a mesh
    # carrying one stopped this reader outright.
    "NiAmbientLight": _NI_LIGHT,
    "NiDirectionalLight": _NI_LIGHT,
    "NiPointLight": (
        *_NI_LIGHT,
        ("constant_attenuation", "f32"),
        ("linear_attenuation", "f32"),
        ("quadratic_attenuation", "f32"),
    ),
    # A point light plus a cone. Note the base: a spot light is a *point*
    # light, so it carries the attenuation triple before its own two fields.
    "NiSpotLight": (
        *_NI_LIGHT,
        ("constant_attenuation", "f32"),
        ("linear_attenuation", "f32"),
        ("quadratic_attenuation", "f32"),
        ("outer_spot_angle", "f32"),
        ("exponent", "f32"),
    ),
    # -- triangle strips, taken from tes3 ---------------------------------
    #
    # The same shape as NiTriShape: geometry data and an optional skin.
    "NiTriStrips": (*_NI_AV_OBJECT, ("data", "link"), ("skin_instance", "link")),
    # Line geometry, which shares the geometry shape and then stores one
    # connectivity byte per vertex rather than faces. Taken from tes3.
    "NiLines": (*_NI_AV_OBJECT, ("data", "link"), ("skin_instance", "link")),
    # The geometry-data base, then one connectivity-flag byte per vertex.
    "NiLinesData": (*_NI_GEOMETRY_DATA, ("vertex_connectivity_flags", "connectivity_flags")),
    # A byte-indexed colour table: a has-alpha flag, then a count of RGBA quads.
    "NiPalette": (("has_alpha", "u8"), ("palettes", "palette_array")),
    # A count of skin partitions, each a self-describing header plus its arrays.
    "NiSkinPartition": (("partitions", "skin_partition_array"),),
    # A time controller, then a count of named animation sequences.
    "NiKeyframeManager": (*_NI_TIME_CONTROLLER, ("sequences", "sequence_array")),
    # The three blocks neither tes3 nor es3 models, derived from the corpus's own
    # demo meshes (see NIF_PROVENANCE.md) rather than any GPL format table:
    # a bare property; a property plus its image link (the legacy single-texture
    # property); and a particle-mesh geometry, which is a plain geometry base.
    "NiRendererSpecificProperty": _NI_PROPERTY,
    "NiTextureProperty": (*_NI_PROPERTY, ("image", "link")),
    "NiParticleMeshes": (*_NI_AV_OBJECT, ("data", "link"), ("skin_instance", "link")),
    # A mirrored node is a plain node (es3); a mesh modifier is the particle
    # modifier base (next, controller) plus its list of mesh references, derived
    # from the demo mesh's bytes since neither reference library models it.
    "BSMirroredNode": _NI_NODE,
    "NiParticleMeshModifier": (*_NI_PARTICLE_MODIFIER, ("meshes", "ref_list")),
    "NiTriShapeData": (
        ("num_vertices", "u16"),
        ("has_vertices", "bool32"),
        ("vertices", "vec3_array", "has_vertices"),
        ("has_normals", "bool32"),
        ("normals", "vec3_array", "has_normals"),
        ("center", "vector3"),
        ("radius", "f32"),
        ("has_vertex_colors", "bool32"),
        ("vertex_colors", "color4_array", "has_vertex_colors"),
        ("num_uv_sets", "u16"),
        # The UV array is gated on the *count*, not on ``has_uv``. Three mod
        # meshes carry num_uv_sets=1 with has_uv=0 and the UV data present
        # anyway; reading the flag desynchronised the rest of the block. With
        # the count as the gate, num_triangle_points comes out as exactly
        # three times num_triangles in all three, which is the invariant that
        # says the triangle list was found where it really is.
        ("has_uv", "bool32"),
        ("uv_sets", "uv_array", "num_uv_sets"),
        ("num_triangles", "u16"),
        ("num_triangle_points", "u32"),
        ("triangles", "triangle_array"),
        ("match_groups", "match_group_array"),
    ),
    # Taken from tes3. The same geometry body as NiTriShapeData, then strips
    # instead of a triangle list: a triangle count, a count of strips, one
    # length per strip, and finally that many indices *in total*.
    #
    # The last field is why this needs its own kind rather than a repeat
    # count: its length is the **sum of the preceding array**, not a number
    # stored anywhere in the file.
    "NiTriStripsData": (
        ("num_vertices", "u16"),
        ("has_vertices", "bool32"),
        ("vertices", "vec3_array", "has_vertices"),
        ("has_normals", "bool32"),
        ("normals", "vec3_array", "has_normals"),
        ("center", "vector3"),
        ("radius", "f32"),
        ("has_vertex_colors", "bool32"),
        ("vertex_colors", "color4_array", "has_vertex_colors"),
        ("num_uv_sets", "u16"),
        ("has_uv", "bool32"),
        ("uv_sets", "uv_array", "num_uv_sets"),
        ("num_triangles", "u16"),
        ("strips", "strip_array"),
    ),
    # A scene object with 52 bytes of projection parameters. Only one fixture
    # carries this block, so the span is measured rather than corroborated --
    # the exact-landing check still validates it, but a second example would
    # be worth more than the comment.
    # An embedded texture. The 3,145,788-byte fixture is the one that pins the
    # shape down: 1024*1024*3 bytes of pixels leaves exactly 60, which is the
    # 44-byte head plus one 12-byte mipmap entry plus the 4-byte pixel length.
    "NiPixelData": (
        ("pixel_format", "u32"),
        ("red_mask", "u32"),
        ("green_mask", "u32"),
        ("blue_mask", "u32"),
        ("alpha_mask", "u32"),
        ("bits_per_pixel", "u32"),
        ("unidentified_pair", "skip:8"),
        ("palette", "link"),
        ("num_mipmaps", "u32"),
        ("bytes_per_pixel", "u32"),
        ("mipmaps", "mipmap_array"),
        ("pixels", "byte_run"),
    ),
    "NiCamera": (*_NI_AV_OBJECT, ("projection", "skip:52")),
    # Tail is 4 + 4*n + 91 bytes across all four observed shapes, where n is
    # the leading count: 95, 99, 111 and 115 for counts of 0, 1, 4 and 5.
    #
    # The counted entries are *not* block indices -- they hold values like
    # 0x0b741950, which is a memory address left in the file by the exporter,
    # not a link. They are counted and stepped over rather than exposed as
    # links, because a caller following them would be following pointers into
    # a process that exited twenty years ago.
    "NiTextureEffect": (
        *_NI_AV_OBJECT,
        ("affected_node_pointers", "ref_list"),
        ("effect_parameters", "skip:91"),
    ),
    # -- particles --------------------------------------------------------
    # Particle systems are geometry: they carry the scene-object preamble and
    # then the same data-and-skin pair a NiTriShape does. Confirmed by byte
    # count on three fixtures -- 108, 122 and 120 bytes, each exactly the
    # preamble plus 1, 5 and 4 property links.
    "NiRotatingParticles": (*_NI_AV_OBJECT, ("data", "link"), ("skin_instance", "link")),
    "NiAutoNormalParticles": (*_NI_AV_OBJECT, ("data", "link"), ("skin_instance", "link")),
    "NiParticles": (*_NI_AV_OBJECT, ("data", "link"), ("skin_instance", "link")),
    # Particle modifiers chain to one another, so the first two links are what
    # matter structurally; the tails are emitter maths. Every one of these is a
    # single fixed size across all its fixtures, which is what makes the split
    # between "named links" and "measured span" safe here.
    "NiAutoNormalParticlesData": _NI_PARTICLES_DATA,
    # The base name for the same body. Taken from tes3: this reader already
    # had the layout under two derived names and simply never had this one,
    # which is the cheapest kind of gap and the least visible.
    "NiParticlesData": _NI_PARTICLES_DATA,
    "NiRotatingParticlesData": _NI_ROTATING_PARTICLES_DATA,
    # ``NiParticleMeshesData`` is a ``NiRotatingParticlesData`` body followed by
    # a single ``Ref``. Measured from the one corpus sample (NifCorpus's
    # ``NiParticleMeshes.NIF``, 43 particles): the rotating-particles base
    # consumes 1428 of the block's 1432 bytes -- all 43 rotations are exact unit
    # quaternions, so the base is right, not merely a size that happens to fit --
    # and the remaining 4 bytes are one link that resolves to the master mesh
    # ``NiNode``. The link closing exactly on the next block's type string is the
    # check. Earlier this block was left unmodeled because the trailer was
    # measured against ``NiParticlesData`` (no rotation array), which left 696
    # unexplained bytes; the rotations are what those bytes are.
    "NiParticleMeshesData": (*_NI_ROTATING_PARTICLES_DATA, ("modifier", "link")),
    "NiColorData": (("keys", "color_key_group"),),
    "NiParticleColorModifier": (*_NI_PARTICLE_MODIFIER, ("color_data", "link")),
    # -- taken from tes3 ---------------------------------------------------
    #
    # A collider is a modifier plus a bounce coefficient; the spherical one
    # adds where it is and how big.
    "NiSphericalCollider": (
        *_NI_PARTICLE_MODIFIER,
        ("bounce", "f32"),
        ("radius", "f32"),
        ("position", "vector3"),
    ),
    "NiParticleBomb": (
        *_NI_PARTICLE_MODIFIER,
        ("decay", "f32"),
        ("duration", "f32"),
        ("delta_v", "f32"),
        ("start_time", "f32"),
        ("decay_type", "u32"),
        ("symmetry_type", "u32"),
        ("position", "vector3"),
        ("direction", "vector3"),
    ),
    "NiParticleGrowFade": (*_NI_PARTICLE_MODIFIER, ("grow", "f32"), ("fade", "f32")),
    "NiParticleRotation": (
        *_NI_PARTICLE_MODIFIER,
        ("random_initial_axis", "u8"),
        ("rotation", "skip:16"),
    ),
    "NiGravity": (*_NI_PARTICLE_MODIFIER, ("field", "skip:36")),
    "NiPlanarCollider": (*_NI_PARTICLE_MODIFIER, ("plane", "skip:64")),
    # The head is a fixed 154 bytes and the array is exactly ``num_particles``
    # 40-byte records, with nothing after it. Verified on 51 fixtures spanning
    # five distinct counts, all fitting exactly.
    #
    # 111 of those head bytes are emitter parameters -- speed, spread, lifetime
    # and so on -- that have not been individually identified. They are skipped
    # as a measured span rather than given invented names: the width is what
    # the rest of the file depends on, and a plausible-looking wrong name is
    # worse than an honest gap, because it would be believed.
    # NiBSPArrayController is the same block under another name: both fixtures
    # are 154 bytes of head plus 30 and 72 forty-byte records exactly.
    "NiBSPArrayController": (
        *_NI_TIME_CONTROLLER,
        ("emitter_parameters", "skip:111"),
        ("num_particles", "u16"),
        ("num_live_particles", "u16"),
        ("unidentified_tail", "skip:13"),
        ("particles", "particle_array"),
    ),
    "NiParticleSystemController": (
        *_NI_TIME_CONTROLLER,
        ("emitter_parameters", "skip:111"),
        ("num_particles", "u16"),
        ("num_live_particles", "u16"),
        ("unidentified_tail", "skip:13"),
        ("particles", "particle_array"),
    ),
    # -- skinning ---------------------------------------------------------
    # Derived by arithmetic, not assumption: the block in
    # NiSkinInstance&NiSkinPartition.nif is exactly 36 bytes and reads as nine
    # i32 -- 41, 34, then 6 followed by precisely six bone links. A data link,
    # a skeleton root and a counted bone list account for it with nothing over.
    # Note there is no skin-partition link: that belongs to later NIF
    # versions, and the byte count leaves no room for one.
    "NiSkinInstance": (
        ("data", "link"),
        ("skeleton_root", "link"),
        ("bones", "ref_list"),
    ),
    # The bone count here always equals the instance's, which is what makes
    # the two verifiable together. The skin-partition link is -1 in ordinary
    # meshes and a real index only in the partition fixture -- present in the
    # layout because the byte count requires a field there either way.
    "NiSkinData": (
        ("transform_rotation", "matrix33"),
        ("transform_translation", "vector3"),
        ("transform_scale", "f32"),
        ("bone_count", "u32"),
        ("skin_partition", "link"),
        ("bones", "skin_bone_array"),
    ),
    # -- properties -------------------------------------------------------
    "NiMaterialProperty": (
        *_NI_PROPERTY,
        ("ambient", "vector3"),
        ("diffuse", "vector3"),
        ("specular", "vector3"),
        ("emissive", "vector3"),
        ("glossiness", "f32"),
        ("alpha", "f32"),
    ),
    "NiTexturingProperty": (
        *_NI_PROPERTY,
        ("apply_mode", "u32"),
        ("texture_count", "u32"),
        ("textures", "texture_slots"),
    ),
    "NiAlphaProperty": (*_NI_PROPERTY, ("threshold", "u8")),
    # Taken from tes3, not derived -- see the note above NiFltAnimationNode.
    # A property, a fog depth and an RGB color.
    "NiFogProperty": (*_NI_PROPERTY, ("fog_depth", "f32"), ("fog_color", "vector3")),
    "NiZBufferProperty": _NI_PROPERTY,
    "NiShadeProperty": _NI_PROPERTY,
    "NiWireframeProperty": _NI_PROPERTY,
    "NiDitherProperty": _NI_PROPERTY,
    "NiSpecularProperty": _NI_PROPERTY,
    "NiVertexColorProperty": (
        *_NI_PROPERTY,
        ("vertex_mode", "u32"),
        ("lighting_mode", "u32"),
    ),
    "NiStencilProperty": (
        *_NI_PROPERTY,
        ("enabled", "u8"),
        ("stencil_function", "u32"),
        ("stencil_ref", "u32"),
        ("stencil_mask", "u32"),
        ("fail_action", "u32"),
        ("z_fail_action", "u32"),
        ("pass_action", "u32"),
        ("draw_mode", "u32"),
    ),
    # -- textures ---------------------------------------------------------
    "NiSourceTexture": (
        *_NI_OBJECT_NET,
        ("use_external", "u8"),
        ("external_or_internal", "source_texture_body"),
        ("pixel_layout", "u32"),
        ("use_mipmaps", "u32"),
        ("alpha_format", "u32"),
        ("is_static", "u8"),
    ),
    # Taken from tes3, not derived. The same texture-source shape as
    # NiSourceTexture -- a flag, then either a path or a link to embedded
    # pixels -- but with **no name, extra data or controller in front of it**:
    # its base is NiObject rather than NiObjectNET. Assuming the usual
    # preamble here would consume twelve bytes that are not there.
    "NiBltSource": (
        ("use_external", "u8"),
        ("external_or_internal", "source_texture_body"),
    ),
    # Taken from tes3. Accumulators decide draw order and carry **no fields
    # at all** -- their whole content is their type name. An empty layout is
    # the correct answer here, not a placeholder: the reader must consume
    # nothing and move straight to the next block.
    "NiAccumulator": (),
    "NiClusterAccumulator": (),
    "NiAlphaAccumulator": (),
    # Taken from tes3. A name, extra data and a controller, and nothing else:
    # the helper exists to hang an animation's controllers off.
    "NiSequenceStreamHelper": _NI_OBJECT_NET,
    # -- extra data -------------------------------------------------------
    "NiStringExtraData": (*_NI_EXTRA_DATA, ("string_data", "string")),
    "NiTextKeyExtraData": (*_NI_EXTRA_DATA, ("text_keys", "text_key_array")),
    "NiVertWeightsExtraData": (
        *_NI_EXTRA_DATA,
        ("num_vertices", "u16"),
        ("weights", "float_array"),
    ),
    # -- animation --------------------------------------------------------
    # Every controller shares NiTimeController's preamble and then adds its
    # own data link. Morrowind meshes carry these for doors, banners, lights
    # and anything else that moves without a creature attached.
    "NiKeyframeController": (*_NI_TIME_CONTROLLER, ("data", "link")),
    "NiVisController": (*_NI_TIME_CONTROLLER, ("data", "link")),
    "NiAlphaController": (*_NI_TIME_CONTROLLER, ("data", "link")),
    # Taken from tes3, not derived. A float controller: the standard preamble
    # and a link to its data. Confirmed by the exact-landing test on the one
    # sample file that carries it.
    "NiRollController": (*_NI_TIME_CONTROLLER, ("data", "link")),
    # Taken from tes3. A time controller and the object being looked at.
    "NiLookAtController": (*_NI_TIME_CONTROLLER, ("look_at", "link")),
    # Taken from tes3. A time controller and its position data.
    "NiLightColorController": (*_NI_TIME_CONTROLLER, ("data", "link")),
    # One byte longer than the other data-link controllers. Found by byte
    # inspection, not by guessing: in all ten alignment failures in the
    # sampled corpus the reader landed exactly one byte early, reading a type
    # name of "\x00NiMorphData" -- a leading NUL followed by the correct name,
    # which is what being one short looks like. With the byte consumed, the
    # next u32 reads 11 and the next 11 bytes read "NiMorphData" exactly.
    # It is 0 in every observed file, so its meaning is not determinable from
    # the corpus and the name says only where it sits.
    "NiGeomMorpherController": (
        *_NI_TIME_CONTROLLER,
        ("data", "link"),
        ("trailing_flag", "u8"),
    ),
    "NiFlipController": (
        *_NI_TIME_CONTROLLER,
        ("texture_slot", "u32"),
        ("unknown_int", "u32"),
        ("delta", "f32"),
        ("sources", "ref_list"),
    ),
    "NiUVController": (*_NI_TIME_CONTROLLER, ("unknown_short", "u16"), ("data", "link")),
    "NiMaterialColorController": (*_NI_TIME_CONTROLLER, ("data", "link")),
    # Fixed at 48 bytes across nine fixtures: the controller preamble and 22
    # bytes of path parameters that have not been individually identified.
    "NiPathController": (*_NI_TIME_CONTROLLER, ("path_parameters", "skip:22")),
    # Four float key groups -- U, V and their two scales. The first fixture is
    # 8 bytes of head plus two quadratic keys, then three empty groups at four
    # bytes each: 8 + 32 + 12 = 52, exactly the block length.
    "NiUVData": (
        ("u_keys", "float_key_group"),
        ("v_keys", "float_key_group"),
        ("u_scale_keys", "float_key_group"),
        ("v_scale_keys", "float_key_group"),
    ),
    "NiKeyframeData": (("keyframe_data", "keyframe_data"),),
    # Each target holds a whole vertex set, not a delta, so the vertex count is
    # declared once here rather than per target. ``relative_targets`` is a
    # single byte and is 1 in every observed file.
    "NiMorphData": (
        ("num_morphs", "u32"),
        ("num_vertices", "u32"),
        ("relative_targets", "u8"),
        ("morphs", "morph_array"),
    ),
    "NiVisData": (("vis_keys", "vis_key_array"),),
    "NiFloatData": (("keys", "float_key_group"),),
    "NiPosData": (("keys", "vector_key_group"),),
}


def block_layout(block_type: str) -> Layout | None:
    """Look up the field layout for a block type.

    Args:
        block_type: The type string written before the block in the file.

    Returns:
        The layout, or ``None`` when the type is not one this reader knows.
    """
    return BLOCK_LAYOUTS.get(block_type)
