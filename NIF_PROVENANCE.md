# How the NIF reader was written, and where every fact in it came from

This document exists so that the origin of `wraithguard/nif/` can be examined
by someone who did not write it, including someone who assumes the worst. It
records what was consulted, what was deliberately not consulted, what method
produced each field layout, and what evidence was required before a layout was
accepted.

`CREDITS.md` records the *licence decision* - why the reader is written rather
than imported, and which libraries were ruled out. This document records the
*method*. The two are meant to be read together.

**This is not legal advice, and no part of it should be relied on as such.** It
is a factual record of engineering practice, written by the people who did the
work, so that a lawyer or a maintainer has something concrete to evaluate
instead of a claim.

## What this is, and what it is not

It is worth being exact, because the phrase "clean room" is a term of art and
this project does not meet its strict definition.

A textbook clean-room implementation uses **two isolated teams**. One examines
the original work and writes a functional specification containing no
expression from it; the other, having never seen the original, implements from
that specification alone. The isolation is the point: it produces a documented
chain showing the implementers *could not* have copied, because they never had
access.

**That is not what happened here.** There is one author. There were no two
teams and no wall.

What this project did is narrower and should be described as what it is: an
**independent implementation derived from public documentation and from direct
observation of files the user lawfully owns**, with a deliberate and recorded
policy of not reading the source code of existing implementations whose
licences are incompatible with this project's.

Claiming more than that would be the wrong thing to do in a document whose only
value is that it can be trusted. The protections this practice actually relies
on are:

- **A file format is not itself a copyrightable work.** What is protected is
  the *expression* in a particular implementation - its code, its comments, its
  structure - not the fact that a 32-bit integer sits at a given offset.
  Reimplementing a format from facts is ordinarily lawful; copying someone's
  code that reads it is not.
- **Facts observed from a file you lawfully possess are your own
  observations.** Every byte offset in this reader was confirmed against
  Morrowind meshes on the user's own installed copy of the game.
- **The policy of not reading incompatible sources** means the question of
  copying does not arise on the merits, rather than being defended after the
  fact.

These are general principles, they vary by jurisdiction, and they are not a
substitute for advice from a lawyer.

## Sources that were used

| Source | Licence / status | What was taken |
| --- | --- | --- |
| *Notes for Modmakers* (`morrowind-nif.github.io`) | Community documentation, published as prose for modders | Which block types the Morrowind engine supports; what blocks are *for*; the enumeration used as the coverage denominator |
| The user's installed Morrowind meshes (7,343 files) | Lawfully owned copy of the game | Every byte offset and field width, by direct observation |
| The *Notes for Modmakers* attachment packages (see below) | Distributed by the same community project as teaching material | Files exercising specific block types in isolation |
| NifSkope, as an **instrument** | GPL-3.0 program, used but not read | Confirmation that a parse result matches what a known-good viewer shows |

## Sources that were deliberately not used

Recorded so that the absence is visible, not merely asserted. See `CREDITS.md`
for the licence analysis behind each.

- **`nif.xml`** (NifTools' machine-readable format description). Not used as a
  source for any field layout. It lives in a GPL-3.0 repository whose own
  licence status is [disputed upstream](https://github.com/niftools/nifxml/issues/86),
  and an unresolved licence is worse than one that clearly says no.
- **NifSkope's source code**, **nifly**, **NiflySharp**, **io_scene_mw**,
  **OpenMW**. All GPL-family. None read for field layouts.
- **pyFFI**. LGPL; ruled out for distribution reasons rather than reading
  reasons, but likewise not read for layouts.
- **niflib** is BSD-3 and would be a permissible reference. It has not been
  needed and has not been consulted. If it ever is, that use should be recorded
  here at the time, not reconstructed afterwards.

### The specific line drawn around NifSkope

NifSkope is GPL-3.0 and this project is MIT, so the distinction matters and is
applied consistently:

- **Permitted - NifSkope as an oracle.** Opening a file the user owns and
  observing *what the file contains*: how many blocks, in what order, of what
  types, with what values. These are facts about the user's file. Running a
  program does not place its licence on your observations.
- **Not permitted - NifSkope as a source.** Transcribing the field names,
  types and orderings it *displays* for a block this project has not yet
  implemented. That display is generated from `nif.xml`, so copying it would
  import exactly the artefact ruled out above, by a longer route.

In practice this means NifSkope answers "is this parse right?" and never "what
are the fields?". The one time it was used during this work - settling whether
`c/amulet_common_1.nif` contains one `NiMaterialProperty` block or two - was
purely the first kind: counting blocks in a block list.

## The method

A layout is a hypothesis, and a hypothesis about a binary format is testable in
a way that leaves very little room for opinion. NIF 4.0.0.2 blocks carry **no
length field**, and each is preceded by a length-prefixed type name. So a
correct layout consumes exactly the right number of bytes and the cursor lands
precisely on the next type name; a layout wrong by even one byte lands on
something that is not a type name at all.

That property is the entire method, and it is unusually strong: it means a
guess cannot quietly half-work.

1. **State the hypothesis from public documentation and from the shape of the
   data.** What fields a block plausibly holds, in what order.
2. **Walk real files.** `tools/check_nif_layouts.py` reads a folder and reports
   how far it got in each file.
3. **Require the walk to land exactly.** If the next four bytes read as a
   length `n` and the following `n` bytes read as a well-formed type name, the
   layout consumed the right number of bytes. If they do not, it did not.
4. **Cross-check the whole file against a scan that shares no code with the
   reader.** `wraithguard/nif/scan.py` recovers a file's block list *without
   using any field layout*, so it cannot fail the way the reader fails. It also
   reconciles against the block count the file's own header declares, and
   disqualifies itself when it does not. `--verify` reports the first index at
   which the two disagree.
5. **Only then accept the layout**, and record how it was derived in a comment
   at the layout itself, not only here.

### The evidence standard, stated as a rule

> A field is added, removed or resized only when doing so makes the reader land
> exactly on the following type name in real files, and the layout-free scan
> agrees with the reader across the whole file.
>
> Adding fields speculatively and in bulk is not permitted, because a layout
> guessed wrong does not fail where the guess was, and a batch of them makes
> the next survey unreadable. One at a time, each validated.
>
> **The files that already parse are evidence too.** A derivation that explains
> every failure in front of you is not thereby correct, and the working files
> are the ones nobody thinks to re-check. See the bounding box below, where
> exactly this went wrong.

## Worked examples

These are recorded in full because the reasoning is the evidence. Anyone can
re-run them.

### `NiGeomMorpherController` - one byte longer than its siblings

Every file that lost alignment stopped with a type name of `\x00NiMorphData`:
a leading NUL followed by the correct name. That is precisely what a cursor one
byte early looks like - it read the last byte of the true length prefix as the
first byte of the name.

Inspecting the bytes at the stop point in `b_n_nord_f_head_01.nif`:

```
bytes at the failure point: b'\x00\x0b\x00\x00\x00NiMorphData\x03\x00\x00\x00'
u32 at q+1 = 11 -> name: b'NiMorphData'
```

Consuming one more byte in the preceding block makes the next `u32` read 11 and
the next 11 bytes read `NiMorphData`. All 10 alignment failures in the sample
had `NiGeomMorpherController` as the preceding block, and the byte was `0` in
every one. After the fix, alignment failures across all 7,319 vanilla meshes
went to **zero**.

Because the corpus shows only the value `0`, its *meaning* is not determinable
from the evidence. It is therefore named `trailing_flag` - a name that records
where it sits and declines to invent what it does. Naming it after a guess
would have been the moment this stopped being observation.

### `NiBillboardNode` - a field that is not there

An earlier layout carried a speculative `billboard_mode` `u16`, reasoning from
later NIF versions. `--explain` on `BM_Snow_01.NIF` showed the reader finishing
two bytes past the next type string. The field was removed. Morrowind's
billboarding has no mode to select, which is consistent with the observation
but was *not* the reason for the change - the byte count was.

### `NiKeyframeData` - a field that is

The reverse case: a float was missing before the three XYZ key groups. With it
consumed, the walk lands exactly on `NiTextureEffect`.

### `vertices` and `normals` - a bug caused by inferring rather than recording

Both fields are the same *kind*, and the reader originally inferred the
presence-gate from the kind. That meant normals were read whenever vertices
were present, desynchronising every mesh that had one without the other. Gates
are now written out explicitly per field. This is recorded because it shows the
failure mode the method is designed around: the bug did not fail where the bug
was.

### The particle controller - a fixed head, found by five counts agreeing

`NiParticleSystemController` block bodies measured 40154, 6154 and 16154
bytes. Each is 154 bytes plus a multiple of 40, and the multiplier - 1000, 150,
400 - appears in the block as a `u16` at offset 137. Across 51 fixtures with
five distinct counts, every body is exactly `154 + count * 40`.

Two conclusions follow from the arithmetic rather than from any document.
First, the record size is 40 bytes: five different counts fitting exactly
leaves no room for coincidence. Second, **nothing follows the array**, because
a trailing field of any size would offset all 51 by that amount.

111 of the head bytes are emitter parameters that were not individually
identified. They are stepped over as a measured span called
`emitter_parameters`, not given invented names. The width is what the rest of
the file depends on; a plausible-looking wrong field name is worse than an
admitted gap, because it would be believed and repeated.

### Particle rotations - an optional array found by subtraction

Two `NiRotatingParticlesData` fixtures both declare 1000 particles. One block
is 32052 bytes and the other 48052. The difference is exactly 16000 - sixteen
bytes per particle - which identified an optional array behind its own flag.
Six fixtures spanning 12 to 1000 particles and every combination of the
optional vertex, color, size and rotation arrays then reconcile to the byte.

### `NiTextureEffect` - counted entries that are not links

The block's tail is `4 + 4n + 91` bytes, where `n` is the leading count: the
four observed shapes are 95, 99, 111 and 115 bytes for counts of 0, 1, 4 and 5.

The counted entries hold values such as `0x0b741950`. Those are not block
indices - they are memory addresses the exporter left in the file. They are
counted and stepped over rather than exposed as links, because a caller
following them would be following pointers into a process that exited two
decades ago. This is recorded because the honest reading of the bytes and the
convenient one differ here, and the honest one was taken.

### The bounding box - where this method was applied badly, and what it cost

Three mod meshes failed inside an optional bounding box. Bounding the block
between its start and the next type name gave one span that landed exactly, in
both files it could be computed for: **20 bytes**. That was written into the
reader as established, with the derivation spelled out in a comment.

It was wrong. Twenty bytes broke thirteen files that the previous width read
correctly - files that had never been re-examined because they were not
failing.

The box is *typed*. The word after the presence flag selects the size: type 1
carries a translation, a 3x3 rotation and an extents triple, and type 0 carries
sixteen bytes. Across every block in the corpus that sets the flag **and
parses**, the type word is 1 in all 27; in the meshes that would not parse it
is 0 in every one. No single width can be right for both populations, and
averaging them produces a number that is right for neither.

This is recorded at length because the failure was in *method*, not in
arithmetic. Every measurement was correct. The step that was skipped was the
one the standard above already required: re-running the whole corpus before
accepting the change. An unrecognised box type is now refused outright rather
than guessed, because an unknown width does not fail where the guess was.

### Property block counts - where an external reference was wrong

A per-file block census shipped with one of the documentation packages was used
as a reference and reported far fewer property blocks than the reader found.
Three independent methods were applied:

- the layout reader: 2 `NiMaterialProperty` in `c/amulet_common_1.nif`;
- the layout-free scan: 2;
- NifSkope's block list: blocks 4 and 11, both `NiMaterialProperty`.

The census said 1. It undercounts shared property blocks specifically, while
matching on every other type. The reader was correct and the reference was not,
which is why the project no longer depends on that file: `--verify` generates
its own reference by scanning.

## What the method produced

Stated so the claim can be checked rather than taken on trust. Against the
7,343 meshes of a vanilla Morrowind install, using `--verify`, which compares
the layout reader against the layout-free scan:

```
identical     : 7339
unverifiable  :    4
No divergence: every block the reader named matched the scan exactly.
```

Zero files stopped early and zero diverged. The four marked unverifiable are
ones where the *scan* found more blocks than the header declares - its known
false-positive mode, where a node happens to be named like a type - so they are
excluded from the comparison and checked against the header count instead.

The reader implements 54 block types, which covers every type occurring in
those meshes. Every layout in it was derived by the method
above, and the four blocks whose fields could not all be identified carry
measured spans with names that say so (`emitter_parameters`,
`unidentified_tail`, `path_parameters`, `projection`) rather than guesses.

### Where the method stops: a file that is simply wrong

The exact-landing test assumes the file is well-formed. One mesh in the sample
is not, and it is worth recording because it marks the method's boundary.

`dbs_meatstick.nif` declares 26 blocks and the layout-free scan finds exactly
26 type names, so every block *boundary* is where it should be. Block 10 then
reads a property count of `0xFFFFFFFF`. Opened in NifSkope - as an oracle, per
the rule above - it shows orphaned blocks. The boundaries are sound and the
contents are not.

No layout can be derived from it, because there is no consistent layout to
find: the bytes do not describe a valid block. The correct response is to
refuse the file and say so, not to widen a layout until it swallows the
nonsense. A layout invented to fit one broken file breaks the sound ones, which
is exactly what happened once already with the bounding box above.

**So a failure to parse is evidence about the reader only when the file is
sound.** Distinguishing the two needs something outside the bytes - here, a
person opening it in a viewer.

## The same method, applied to textures

`wraithguard/images/` decodes every texture format this game and its mods use,
under the same rules and for the same reasons. The block formats are arithmetic
-- a DXT1 block is two 16-bit colors and sixteen 2-bit selectors, and the
decode is the interpolation the format defines -- so they were implemented from
the public description and checked against real files.

### BC7 is a different scale of problem, and was treated as one

BC1 fits in a paragraph. BC7 does not: a 16-byte block carries one of eight
modes, and the mode decides how many subsets the block is cut into, how wide
the endpoints are, whether alpha exists at all, whether a second index set
exists, and whether a color channel was rotated into alpha before encoding.
Nothing after the mode bits sits at a fixed offset.

Its definition is **roughly six hundred numbers**: an eight-row mode table, two
64-entry partition tables and three 64-entry anchor tables. These were
transcribed from the **published format specification** -- Khronos's OpenGL
BPTC specification and Microsoft's Direct3D 11 BC7 documentation, both public
descriptions of the format rather than implementations of it. No decoder source
was read. The licence reasoning is identical to the NIF reader's.

**Why transcription needed a different kind of check.** A single wrong table
entry does not fail loudly. It produces a correct-looking image with a handful
of wrong 4x4 blocks, in whichever partition shapes the encoder happened to
choose. A unit test cannot find that, because the test and the table were
written by the same person from the same reading.

So `tools/check_bc7.py` does not test against expectations at all. It generates
blocks that *force every table entry to be used* -- all eight modes crossed with
all 64 partitions -- and compares against Pillow's unrelated implementation.
**19,380 blocks, byte-for-byte identical.** Random bits are valid input here:
every 128-bit pattern is a legal BC7 block except the one reserved value, so
noise exercises endpoints, P-bits, index packing and anchor widths far harder
than any real texture would.

### One thing Pillow cannot adjudicate

BC5 stores two channels; blue is **reconstructed** by whoever decodes it.
There is no right answer to compare against, only a convention -- so comparing
our blue against Pillow's would be comparing two conventions rather than
testing a decoder. The cross-check therefore compares red and green only, and
the reconstruction is checked separately **by geometry**: a decoded normal
should be a unit vector, and a flat normal should come back pointing straight
out of the surface.

That check was itself wrong on the first attempt. It asserted every pixel of a
random block was unit length, passed on one seed and failed on the next --
because random bytes are not normals, and where x² + y² exceeds one the
reconstruction correctly clamps z to zero. It now checks both branches
separately. Worth recording: the error was found only by rerunning the check,
not by reasoning about it.

The cross-check here was **Pillow**, used as an instrument and not read: all 50
textures in the local corpus decode byte-for-byte identically to it, and every
PNG the encoder writes reads back through it with the exact pixels it was
given. Pillow is not a dependency of this project. The distinction is the same
one drawn around NifSkope above -- comparing our output against another
program's output is an observation, not a copy.

## What a modded corpus changed

The figures above are for a vanilla install. They were also, for a while, the
basis of a claim that four block types were not worth implementing because
vanilla never uses them.

A run over 80,197 meshes from a mod collection disproved that, and is recorded
here because it bears on how coverage claims in this document should be read:
**vanilla is not the population this tool serves.** The reader now accepts NIF
4.0.0.0 as well, which was established the same way as every layout -- 40 such
files had their version word alone rewritten and every one then parsed
identically to the layout-free scan, so the header differs and the layouts do
not.

## The corpus

Two distinct packages are present, from the same community documentation
project but distributed separately. They are **not the same set** and are
easily confused, so the difference is recorded:

| | `NifCorpus/` (outer) | `NifCorpus/Nif_files_Examples/` |
| --- | --- | --- |
| `.nif` files | 365 | 191 |
| Also contains | 139 `.flv` and 30 `.mp4` tutorial videos, 51 `.max` scenes, 50 `.dds` textures, 15 `.txt`, 7 `.gif`, 1 `.kf` | 4 `.txt`, 4 `.png`, 2 `.kf`, 1 `.gif` |
| Organisation | Tutorial material; **not every folder contains a `.nif`** | Folders named for the block type they demonstrate |
| Total `.nif` recursively | **556** (includes the 191 below) | 191 |

Two practical consequences, both of which have already caused bugs:

- **556 is the recursive total**, so a count taken over `NifCorpus/` already
  includes `Nif_files_Examples/`. Reporting them as 556 + 191 would double-count.
- **318 of the 556 use an uppercase `.NIF` extension.** File discovery must be
  case-insensitive; a `rglob("*.nif")` silently missed them on Linux.

Neither package, nor any sampled game asset, is redistributed by this project.
`tools/check_nif_layouts.py --collect` copies real meshes out of the user's game
install for local analysis, and both that destination and the generated reports
are listed in `.gitignore`. The game files are the user's own; they stay on the
user's machine.

## Rules for anyone continuing this work

1. Do not read `nif.xml`, NifSkope, nifly, NiflySharp, io_scene_mw or OpenMW for
   field layouts. If you have read them, say so, and do not write layouts.
2. Use NifSkope to check answers, never to obtain them.
3. Derive from bytes in files you lawfully own, and require the exact-landing
   test plus scan agreement before accepting a layout.
4. Name a field after what the evidence supports. `trailing_flag` is a better
   name than a confident guess, because the name is part of the record.
5. Record the derivation in a comment at the layout, and add a worked example
   here when the reasoning is not obvious from the comment.
6. If a permissively-licensed reference such as niflib is ever consulted, record
   it here **at the time**.


## The licence boundary, dated

Everything in this document above this line was derived **before 28 July 2026**
and **without access to any GPL-licensed NIF implementation**. That is the claim
the whole document exists to support, and it is fixed as of that date. Nothing
written later can change how the existing layouts were obtained, and nothing
later should be allowed to blur the record of it.

### What changed

On 28 July 2026, Greatness7 - the author of `io_scene_mw`, the Blender Morrowind
plugin - offered publicly to relicense its NIF library under MIT, and pointed at
`Greatness7/tes3`, a Rust library that is **already MIT** and covers reading and
writing of `.esp`, `.esm`, `.nif` and `.kf`.

That offer, if executed, removes the constraint that shaped this entire reader.

### What has and has not actually happened

| | Licence, as verified 28 July 2026 | Usable? |
| --- | --- | --- |
| `Greatness7/tes3` (Rust) | **MIT**, repository root `LICENSE` | Yes |
| `Greatness7/io_scene_mw`, **`lib/es3/` only** | **MIT**, granted 28 July 2026 | Yes |
| `Greatness7/io_scene_mw`, everything else | **GPL-3.0** | **No** |

### The grant, precisely

Commit
[`cbe18b5`](https://github.com/Greatness7/io_scene_mw/commit/cbe18b558299e14ecd959183e3cf9ea096fe95df),
*"mark `es3` library explicitly as MIT license"*, adds `lib/es3/LICENSE`:
21 lines of MIT text, `Copyright (c) 2026 Greatness7`. One file changed.

That is a grant. The chat offer that preceded it was not, and this project
waited for the commit - the standard being a licence file in the repository,
because that is the thing still true in two years when nobody remembers the
conversation.

**The grant covers `lib/es3/` and nothing else.** The rest of the repository -
`nif_import.py`, `nif_export.py`, `nif_shader.py`, `nif_utils.py`, `operators/`,
`panels/`, `properties/` - remains GPL-3.0, because Blender requires its plugins
to be. That boundary is a directory path, and it is easy to slide from
"io_scene_mw is MIT now" into reading `nif_import.py`, which it is not. **Only
`lib/es3/`.**

Rule 1 above is amended accordingly, for that directory only and from that date
only. Every other project named in it - `nif.xml`, NifSkope, nifly, NiflySharp,
OpenMW - is untouched and still off limits.

### Keep the two kinds of fact apart in the code

A layout derived from bytes and a layout taken from a permissively-licensed
reference are different kinds of fact, with different reasons to be trusted and
different things that would falsify them. A reader six months from now cannot
tell them apart unless the comment says which is which - so say which.

Where a reference **confirms** an existing derivation, that is the strongest
state and worth marking as such: two independent routes to the same answer.
`_BOUNDING_BOX_TAILS` is the model for how to write it.

### First results from the MIT library, 28 July 2026

`Greatness7/tes3` is MIT **in the repository**, so it was read. Two results
within the first half hour, which is the argument for cross-checking in one
paragraph.

**It confirmed the hardest derivation in this project.** Its `BoundType` is
`Sphere = 0, Box = 1`; `NiBound` is a centre and a radius, 16 bytes; `NiBoxBV`
is a centre, a 3x3 axis matrix and an extents triple, 60 bytes. Both type
numbers and both widths match `_BOUNDING_BOX_TAILS` exactly. That is the
derivation recorded above where a confident conclusion drawn from two files -
"20 bytes, solved not guessed" - broke thirteen meshes that already worked, and
the right answer only appeared once the two populations were separated. It is
now confirmed by an implementation that shares none of this project's
assumptions.

**It found a real gap.** `tes3` also handles `NiUnionBV`, bound type 4, which
is *not a width*: it is a count followed by that many complete bounding
volumes, each carrying its own type word, and a union may contain unions. A
width table cannot express that shape, so this reader would have stopped on any
such file with "unknown bounding box type 4". Now implemented, with a depth
limit, since the format permits recursion a corrupt file could abuse.

No file in either corpus has ever carried one - which is exactly why no test
and no measurement here could have found it. **A gap in coverage is invisible
to a corpus that does not exercise it**, and the only instrument that sees it is
a second implementation.

Types 2 (Capsule), 3 (Lozenge) and 5 (Halfspace) are named by the format and
are still refused. `tes3` declines them too, so there is no width to take and
nothing to derive one from.

### Nineteen block types, taken rather than derived

Running this reader over the categorised NIF **sample archive** - not vanilla,
not the mod corpus - showed 624 of 768 files parsing. The stops were all named
types, and `tes3` had every one of them. Taking those layouts brought the
sample archive to **723 of 768 (94.1%)**, and the reader from 66 known block
types to 85:

| | |
| --- | --- |
| Nodes | `NiBSPNode`, `NiCollisionSwitch`, `NiFltAnimationNode`, `NiSortAdjustNode` |
| Lights | `NiAmbientLight`, `NiDirectionalLight`, `NiPointLight`, `NiSpotLight` |
| Controllers | `NiRollController`, `NiLookAtController`, `NiLightColorController` |
| Geometry | `NiTriStrips`, `NiTriStripsData` |
| Other | `NiFogProperty`, `NiBltSource`, `NiSequenceStreamHelper`, and the three accumulators |

**Each is marked in `blocks.py` as taken from `tes3` rather than derived.**
That distinction is not bookkeeping. A derived layout has survived the
exact-landing test across thousands of files; a taken one is a transcription
confirmed against however many sample files happen to carry that type - for
several of these, exactly one. They are both true and they are not equally
well-evidenced, and the comment says which is which so that a future failure is
debugged in the right place.

They did all pass the exact-landing test on the files that exercise them, which
is the same standard applied to everything else here. It is simply a much
smaller sample.

Two of these were worth the reading rather than the guessing:

* **`NiBltSource` has no name, extra data or controller.** Its base is
  `NiObject`, not `NiObjectNET`, unlike almost every other block in the format.
  A reasonable assumption would have consumed twelve bytes that are not there.
* **`NiTriStripsData`'s final run has no stored length.** It is the *sum* of
  the strip-length array immediately before it. That shape cannot be expressed
  as a gated run, so it needed a new field kind rather than a new entry.

The 45 files still failing are the honest remainder: `NiLines`,
`NiParticleBomb`, `NiParticlesData`, `NiSphericalCollider` and a handful of
others, plus two files that are not NIFs at all and two that are genuinely
malformed (a properties count of 4,294,967,295).

**None of this changes the vanilla or mod figures**, which were already 100%
and ~99%. Every type here was absent from both corpora. That is the point:
these were invisible to every measurement this project had, and a second
implementation made them visible in an afternoon.

### The mod corpus, re-verified

`--verify` over 80,197 mod meshes, 28 July 2026:

| | |
| --- | --- |
| Cross-checked against the layout-free scan, agreed on every block | **79,102** |
| **Diverged** | **0** |
| Reader stopped early | 2 |
| Reader did not read every declared block | 7 |
| Scan could not produce a reference at all | 1,086 |

**Read the buckets carefully, because the obvious reading is wrong.**
`unverifiable` counts files where *the scan* failed to reconcile with the
header - it is a limitation of the cross-check, not a failure of the reader,
and the reader is still run against the header's own block count for those. So
the reader's actual failures are the 2 stops plus the 7 incompletes: **9 files
in 80,197, or 0.011%**.

The line that matters is **zero divergence**. A divergence means a field width
is wrong in a block *before* the point of disagreement, and such a file may
still walk to the end and look perfectly healthy - it is invisible to any
survey that only counts what parsed. Zero across 79,102 independently verified
files is the strongest evidence this reader has.

Both remaining stops are now settled rather than open:

* **`NiBSParticleNode`** (1 file) - the tool's own message hedges between "a
  layout bug here" and "a malformed file". `tes3` declares the type as a bare
  `NiNode`, identical to this reader's layout, so the layout is not the
  problem. `dbs_meatstick.nif` carries a property count of 0xFFFFFFFF and was
  inspected in NifSkope. A finding about the mod.
* **`NiTextureProperty`** (1 file) - a genuine coverage gap, and **neither
  `tes3` nor `io_scene_mw/lib/es3` implements it either.** Worth recording
  plainly: with three implementations in hand, one file in 80,197 references a
  type none of them knows.

### One thing worth not losing

If the reader is eventually replaced, the replacement should be held to the
measurement this one already meets: **100% of 7,343 vanilla meshes and ~99% of
80,197 mod meshes**, with the remainder categorised. That figure is not a boast,
it is a regression baseline - the only way to know a replacement is an
improvement rather than a change.

The comparison also has independent value. Two implementations of the same
format, diffed over 87,000 real files, is the strongest check available for
either of them, and stronger than either project's own tests. Every error found
in this project - the typed bounding box, the BSA data offset, the texture
extension comparison, BC5's clamped normals - came from a cross-check against
something that did not share its assumptions.

## Closing the corpus, and a writer, 31 August 2026

Nine block types the corpus contained but this reader lacked were added, and
each is marked in `blocks.py` with which kind of fact it rests on:

- **From `es3` (MIT, `lib/es3/` only):** `NiPalette`, `NiLinesData`,
  `NiSkinPartition` (and its `NiPartition` sub-record), `NiKeyframeManager`
  (and `NiSequence`), and `BSMirroredNode`. These are transcriptions of the
  reference's own `load` order, the permitted MIT source.
- **Derived from bytes, no code read:** `NiTextureProperty`,
  `NiParticleMeshes`, `NiRendererSpecificProperty` and `NiParticleMeshModifier`.
  Neither `tes3` nor `es3` models these, so they were reverse-engineered from
  the corpus's own single demo mesh of each, bounded by the next block's type
  string, and cross-checked only against the **public inheritance facts** the
  UESP Construction Set wiki documents (`NiProperty → NiTextureProperty`,
  `NiGeometry → NiParticles → NiParticleMeshes`) - facts about Bethesda's
  format, not anyone's implementation.

**pyffi was not used.** The pyffi library is BSD, but the layouts it reads from
live in `nif.xml`, which is GPL and on the wrong side of the boundary Rule 1
draws. It was neither imported (it does not run on Python 3.10) nor read; the
four blocks above came from bytes instead, the same method as everything derived
before 28 July 2026.

**The last block, and how a wrong base hid it.**
`NiParticleMeshesData` is the one block no permissively-licensed reference
models, and the corpus holds exactly one example (43 particles, kept as the
regression fixture `testdata/nif/NiParticleMeshes.nif`).
For a while it was left unmodeled, because measured against `NiParticlesData` --
the base a first reading assumed -- its 1432-byte body left ~696 unexplained
bytes that looked like an undecodable per-particle trailer. The base was wrong.
The block inherits `NiRotatingParticlesData`, which is `NiParticlesData` *plus*
an optional per-particle rotation array; measured against that base, the body
consumes 1428 of its 1432 bytes and the "trailer" collapses to **four**: a
single `Ref`. The rotation array is what the 696 bytes were -- and it is
provably a rotation array, not a coincidental fit, because all 43 of its
quaternions are exact unit quaternions (`|q| = 1.0`), which random bytes are
not. The remaining `Ref` resolves to the master mesh `NiNode`, and the block
ends exactly on the next block's type string. So the layout is
`NiRotatingParticlesData` followed by one link -- derived from bytes, checked by
the unit-length rotations and the closing offset, needing no `nif.xml` and no
second file. With it, this single corpus example parses whole (all 26 blocks,
no stop) and round-trips byte-exact. The lesson is the document's thesis again:
the bytes were always derivable; what had been missing was the right base to
measure them against.

**pyffi was tried as a sample source, and ruled out on the merits, not the
licence.** The idea was sound: a GPL tool's *output* is data, not a derivative of
its source, so generating meshes with pyffi and deriving from their bytes would
have been as clean as reading tes3conv's output or any modded mesh. pyffi even
runs, given a one-line `time.clock` shim for Python 3. It was ruled out because
it does not work on the files that matter: pointed at this corpus, pyffi read
**1 of 120** real Morrowind meshes before desynchronising -- typically back at
`NiTriShapeData`, a block this reader parses in thousands of files. Its
`nif.xml`-derived layouts diverge from what Morrowind's own exporters actually
wrote, so it can neither be ground truth for the real file nor generate samples
that would match one. That 1-of-120, against this reader's 761-of-765 (the four
remaining are malformed or unreadable files, not missing block types -- every
block type the corpus contains is now modeled), is the whole document's argument
in miniature: the generic multi-version model is wrong for Morrowind
specifically, which is why the layouts here were measured from Morrowind's bytes
rather than copied from a reference.

**A writer, and what makes it a parity claim.** `write_nif` is the inverse of
the reader. Because the reader keeps element counts rather than the elements, a
field-by-field writer could not reproduce bulk arrays; instead a
`retain=True` read keeps each block's body verbatim and the file's header and
footer, and the writer reassembles the framing around them. The test that it is
right is a **byte-exact round trip over all 761 fully-parsed corpus files** -
every file the reader understands, it writes back unchanged. The block walk stops
at the declared block count and never reads the trailing root-object list, so the
footer is carried across separately; that eight-byte gap was the whole of the
first round-trip's error and is the kind of thing only a full-corpus byte
comparison surfaces.
