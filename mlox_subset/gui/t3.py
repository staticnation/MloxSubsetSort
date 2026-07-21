"""tes3cmd front-end: the window, file staging, clean/sync workers.

Split out of the ``App`` class in ``mlox_subset_sort_gui.py`` as a mixin
(CODE_REVIEW.md §16/§9.2, 3.0). Method bodies are verbatim; ``App`` inherits
this class, so ``self`` is the running ``App`` instance and every attribute
reference resolves exactly as it did when the methods lived there.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import traceback
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, ClassVar

import mlox_subset_sort as core
from mlox_subset.gui import app_base_dir, trace_first_fire
from mlox_subset.gui.theme import DARK, style_plain_widget
from mlox_subset.gui.widgets import QueueWriter, add_tooltip, attach_typeahead
from mlox_subset.i18n import gettext as _, ngettext


class Tes3cmdMixin:
    """The tes3cmd window and its workers (mixed into ``App``)."""

    if TYPE_CHECKING:
        # The host contract. These live on ``App``, not here -- a mixin is
        # only half a class, and without declaring what it expects from its
        # host, mypy cannot check the half that IS here. Declared rather than
        # silenced so the coupling is explicit and reviewable: this is exactly
        # the surface ``App`` must keep providing.
        T3_COMMANDS: ClassVar[tuple[tuple[str, str], ...]]
        T3_NEVER_CLEAN: ClassVar[set[str]]
        root: tk.Misc
        log_queue: queue.Queue
        status_var: tk.StringVar
        sort_button: ttk.Button
        plugin_order_yml_var: tk.StringVar
        _current_plan: dict | None
        _tes3cmd_override: str | None
        _tes3conv_override: str | None
        worker_running: bool

        def _cfg_dir(self) -> str | None: ...
        def _plan_scan_dirs(self) -> list[str]: ...

    def _tes3conv_json_dir(self) -> Path:
        return app_base_dir() / "tes3conv_json"

    def on_tes3cmd_window(self) -> None:
        """Open (or raise) the tes3cmd window."""
        win = getattr(self, "_t3_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            return
        win = tk.Toplevel(self.root)
        self._t3_win = win
        win.title("tes3cmd")
        win.configure(bg=DARK["bg"])
        win.geometry("760x520")
        top = ttk.Frame(win, padding=10)
        top.pack(fill="both", expand=True)
        top.columnconfigure(1, weight=1)

        # tes3cmd binary path (auto-detected; MOMW Tools Pack ships tes3cmd.exe)
        ttk.Label(top, text=_("tes3cmd:")).grid(row=0, column=0, sticky="w")
        self._t3_path_var = tk.StringVar(value=self._tes3cmd_override or "")
        ent = ttk.Entry(top, textvariable=self._t3_path_var)
        ent.grid(row=0, column=1, sticky="ew", padx=6)
        add_tooltip(
            ent,
            _(
                "Path to tes3cmd. Leave empty to auto-detect (PATH, next to this app, "
                "next to openmw.cfg). End users normally have the compiled tes3cmd.exe "
                "from the MOMW Tools Pack; the pure-perl script works too if perl is "
                "installed."
            ),
        )

        def _browse() -> None:
            p = filedialog.askopenfilename(
                title=_("Locate tes3cmd"),
                filetypes=(("tes3cmd", "tes3cmd*"), ("Executables", "*.exe"), ("All files", "*.*")),
            )
            if p:
                self._t3_path_var.set(p)

        ttk.Button(top, text=_("Browse..."), command=_browse).grid(row=0, column=2, padx=(0, 0))

        detected = core.find_tes3cmd(explicit=None, extra_dirs=[self._cfg_dir()])
        note = (
            f"auto-detected: {detected}"
            if detected
            else "not found -- install the MOMW Tools Pack or Browse to it"
        )
        ttk.Label(top, text=note, foreground=DARK["fg_dim"]).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=6, pady=(2, 6)
        )

        # plugin list
        lf = ttk.LabelFrame(top, text=_("Plugins to run on"))
        lf.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(4, 6))
        top.rowconfigure(2, weight=1)
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)
        self._t3_list = tk.Listbox(
            lf, selectmode="extended", exportselection=False, activestyle="dotbox"
        )
        style_plain_widget(self._t3_list)
        attach_typeahead(self._t3_list)
        self._t3_list.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        sc = ttk.Scrollbar(lf, orient="vertical", command=self._t3_list.yview)
        sc.grid(row=0, column=1, sticky="ns", pady=8)
        self._t3_list.configure(yscrollcommand=sc.set)
        self._t3_files: list[str] = []  # full paths, parallel to listbox rows

        btns = ttk.Frame(lf)
        btns.grid(row=0, column=2, sticky="n", padx=8, pady=8)
        b1 = ttk.Button(btns, text=_("My mods (last sort)"), command=self._t3_add_from_plan)
        b1.pack(fill="x", pady=2)
        add_tooltip(
            b1,
            _(
                "Add every custom mod from the last Sort whose file could be located "
                "(curated plugins are the list's job, not yours)."
            ),
        )
        b1b = ttk.Button(btns, text=_("MOMW needs-cleaning"), command=self._t3_add_needs_cleaning)
        b1b.pack(fill="x", pady=2)
        add_tooltip(
            b1b,
            _(
                "Add every ACTIVE plugin that plugin-order.yml flags as "
                "needs_cleaning (MOMW's own 'clean this one' list), resolved across "
                "the data folders. Requires the plugin-order.yml path on the main tab."
            ),
        )
        b2 = ttk.Button(btns, text=_("Add file(s)..."), command=self._t3_add_files)
        b2.pack(fill="x", pady=2)
        b3 = ttk.Button(btns, text=_("Remove selected"), command=self._t3_remove_selected)
        b3.pack(fill="x", pady=2)
        b4 = ttk.Button(btns, text=_("Clear"), command=lambda: self._t3_set_files([]))
        b4.pack(fill="x", pady=2)

        # command choice
        cf = ttk.LabelFrame(top, text=_("Command"))
        cf.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        self._t3_cmd_var = tk.StringVar(value="sync")
        for i, (val, label) in enumerate(self.T3_COMMANDS):
            ttk.Radiobutton(cf, text=label, value=val, variable=self._t3_cmd_var).grid(
                row=i, column=0, sticky="w", padx=8, pady=1
            )
        xf = ttk.Frame(cf)
        xf.grid(row=len(self.T3_COMMANDS), column=0, sticky="ew", padx=8, pady=(4, 6))
        ttk.Label(xf, text=_("extra arguments:")).pack(side="left")
        self._t3_extra_var = tk.StringVar()
        xent = ttk.Entry(xf, textvariable=self._t3_extra_var, width=48)
        xent.pack(side="left", padx=6, fill="x", expand=True)
        add_tooltip(
            xent,
            _(
                "Optional extra tes3cmd switches, e.g. --instances for clean, "
                "--author/--description for header. Passed through verbatim."
            ),
        )

        row = ttk.Frame(top)
        row.grid(row=4, column=0, columnspan=3, sticky="ew")
        self._t3_run_btn = ttk.Button(row, text=_("Run"), command=self._t3_run)
        self._t3_run_btn.pack(side="left")
        add_tooltip(
            self._t3_run_btn,
            _(
                "Output streams to the main Log. Modifying commands ask "
                "for confirmation first; tes3cmd makes its own backups."
            ),
        )
        ttk.Button(row, text=_("Close"), command=win.destroy).pack(side="right")

    def _t3_set_files(self, paths: Sequence[str]) -> None:
        self._t3_files = list(paths)
        self._t3_list.delete(0, "end")
        for p in self._t3_files:
            self._t3_list.insert("end", f"{Path(p).name}    ({Path(p).parent})")

    def _t3_remove_selected(self) -> None:
        trace_first_fire("tes3cmd Remove selected")
        before = len(self._t3_files)
        keep = [
            p for i, p in enumerate(self._t3_files) if i not in set(self._t3_list.curselection())
        ]
        core.trace(f"[smoke] tes3cmd Remove selected: {before} -> {len(keep)} file(s)")
        self._t3_set_files(keep)

    def _t3_add_from_plan(self) -> None:
        plan = self._current_plan or {}
        subset = plan.get("subset") or []
        if not subset:
            self.status_var.set(_("Run '1. Sort' first so I know which mods are yours."))
            return
        index = core.PluginFileIndex(self._plan_scan_dirs())
        found, missing = [], 0
        have = {str(p).lower() for p in self._t3_files}
        for n in subset:
            if str(n).lower().endswith(".omwscripts"):
                continue  # not a TES3 file; nothing for tes3cmd to do
            p = index.find(n)
            if p is None:
                missing += 1
            elif str(p).lower() not in have:
                found.append(str(p))
        self._t3_set_files(self._t3_files + found)
        msg = _("tes3cmd: added %(count)d of your mods") % {"count": len(found)}
        if missing:
            msg += ngettext(
                " (%(count)d file not found in the data folders)",
                " (%(count)d files not found in the data folders)",
                missing,
            ) % {"count": missing}
        self.status_var.set(msg + ".")

    def _t3_add_needs_cleaning(self) -> None:
        """Add active plugins that MOMW's plugin-order.yml flags needs_cleaning."""
        yml = self.plugin_order_yml_var.get().strip()
        if not yml:
            self.status_var.set(_("Set the plugin-order.yml path on the main tab first."))
            return
        try:
            entries = core.parse_plugin_order_yml(Path(yml))
            nc = core.needs_cleaning_set(entries)
        except Exception as e:  # noqa: BLE001
            # untrusted plugin-order.yml; status line carries the failure
            self.status_var.set(_("Couldn't read plugin-order.yml: %(error)s") % {"error": e})
            return
        plan = self._current_plan or {}
        active = plan.get("final_order") or plan.get("base_order_names") or []
        if not active:
            self.status_var.set(_("Run '1. Sort' first so I know the active load order."))
            return
        index = core.PluginFileIndex(self._plan_scan_dirs())
        have = {str(p).lower() for p in self._t3_files}
        found, unfound = [], 0
        for n in active:
            if n.lower() not in nc or n.lower() in self.T3_NEVER_CLEAN:
                continue
            p = index.find(n)
            if p is None:
                unfound += 1
            elif str(p).lower() not in have:
                found.append(str(p))
        self._t3_set_files(self._t3_files + found)
        msg = ngettext(
            "tes3cmd: added %(count)d needs-cleaning plugin",
            "tes3cmd: added %(count)d needs-cleaning plugins",
            len(found),
        ) % {"count": len(found)}
        if unfound:
            msg += f" ({unfound} not found in the data folders)"
        self.status_var.set(msg + ".")

    def _t3_add_files(self) -> None:
        ps = filedialog.askopenfilenames(
            title=_("Choose plugin file(s)"),
            filetypes=(("TES3 plugins", "*.esp *.esm *.omwaddon *.omwgame"), ("All files", "*.*")),
        )
        if ps:
            have = {str(p).lower() for p in self._t3_files}
            self._t3_set_files(self._t3_files + [p for p in ps if str(p).lower() not in have])

    def _t3_run(self) -> None:
        if self.worker_running:
            return
        cmd = self._t3_cmd_var.get()
        extra = self._t3_extra_var.get().split()
        files = list(self._t3_files)

        # "sync" runs entirely in-app (VFS-aware) -- tes3cmd not needed, and
        # its own --synchronize writes EMPTY master sizes when the masters
        # aren't in the plugin's folder (OpenMW multi-folder layouts).
        if cmd == "sync":
            if not files:
                messagebox.showinfo(
                    _("tes3cmd"), _("Add at least one plugin file first."), parent=self._t3_win
                )
                return
            if not messagebox.askyesno(
                _("Resync master sizes"),
                ngettext(
                    "Rewrite the recorded master sizes in %(count)d plugin file to "
                    "match the installed masters?\n\nOnly the 8-byte size fields change; "
                    "a one-time .masterfix.bak copy of each file is kept.",
                    "Rewrite the recorded master sizes in %(count)d plugin files to "
                    "match the installed masters?\n\nOnly the 8-byte size fields change; "
                    "a one-time .masterfix.bak copy of each file is kept.",
                    len(files),
                )
                % {"count": len(files)},
                parent=self._t3_win,
            ):
                return
            self.worker_running = True
            self._t3_run_btn.configure(state="disabled")
            self.sort_button.configure(state="disabled")
            self.status_var.set(_("Resyncing master sizes..."))
            threading.Thread(target=self._t3_sync_worker, args=(files,), daemon=True).start()
            return

        # A manually-entered path must WIN or fail loudly -- never silently
        # fall back to some other tes3cmd found on the system.
        explicit = self._t3_path_var.get().strip() or None
        if explicit:
            if not Path(explicit).is_file():
                messagebox.showerror(
                    _("tes3cmd"),
                    _("'%(path)s' does not exist. Fix the path or clear it to auto-detect.")
                    % {"path": explicit},
                    parent=self._t3_win,
                )
                return
            exe: str | None = explicit
        else:
            exe = core.find_tes3cmd(extra_dirs=[self._cfg_dir()])
        if not exe:
            messagebox.showerror(
                _("tes3cmd"),
                _("tes3cmd not found. Browse to the compiled " "tes3cmd.exe (MOMW Tools Pack)."),
                parent=self._t3_win,
            )
            return
        argv, err = core.tes3cmd_invocation(exe)
        if err:
            messagebox.showerror(_("tes3cmd"), err, parent=self._t3_win)
            return
        self._tes3cmd_override = explicit  # persist a manual path in settings
        if not files:
            messagebox.showinfo(
                _("tes3cmd"), _("Add at least one plugin file first."), parent=self._t3_win
            )
            return
        if cmd == "clean":
            guarded = [f for f in files if Path(f).name.lower() in self.T3_NEVER_CLEAN]
            if guarded:
                files = [f for f in files if Path(f).name.lower() not in self.T3_NEVER_CLEAN]
                messagebox.showwarning(
                    _("tes3cmd"),
                    "Skipping the vanilla master(s):\n\n  "
                    + "\n  ".join(Path(f).name for f in guarded)
                    + "\n\nMorrowind.esm, Tribunal.esm and Bloodmoon.esm are never cleaned -- "
                    "even a careful GMST-preserving clean rewrites bytes that other content "
                    "depends on and causes in-game failures.",
                    parent=self._t3_win,
                )
                if not files:
                    return
            # masters before dependents: cleaning changes the master a
            # dependent is compared against, so sequence by the sorted load
            # order (fallback: masters first, then name)
            order = {
                n.lower(): i
                for i, n in enumerate((self._current_plan or {}).get("final_order") or [])
            }
            files.sort(
                key=lambda f: (
                    order.get(Path(f).name.lower(), 1 << 30),
                    not Path(f).name.lower().endswith((".esm", ".omwgame")),
                    Path(f).name.lower(),
                )
            )
            if not messagebox.askyesno(
                _("tes3cmd clean"),
                ngettext(
                    "Clean %(count)d plugin file?\n\nEach is staged into a private "
                    "'Data Files' with its masters so tes3cmd sees the full VFS; plugins "
                    "whose masters can't be found are skipped. A one-time .preclean.bak "
                    "copy of each modified file is kept.",
                    "Clean %(count)d plugin files?\n\nEach is staged into a private "
                    "'Data Files' with its masters so tes3cmd sees the full VFS; plugins "
                    "whose masters can't be found are skipped. A one-time .preclean.bak "
                    "copy of each modified file is kept.",
                    len(files),
                )
                % {"count": len(files)},
                parent=self._t3_win,
            ):
                return
        self.worker_running = True
        self._t3_run_btn.configure(state="disabled")
        self.sort_button.configure(state="disabled")
        self.status_var.set(_("tes3cmd %(command)s running...") % {"command": cmd})
        threading.Thread(
            target=self._t3_worker, args=(argv, cmd, extra, files), daemon=True
        ).start()

    def _t3_staging_dir(self) -> Path:
        """Return the persistent staging root (next to the app).

        Like the tes3conv dump: hardlinked/copied masters are reused across
        runs, so staging a plugin costs almost nothing after the first time.
        """
        return app_base_dir() / "tes3cmd_staging"

    def _t3_worker(self, argv: list[str], cmd: str, extra: list[str], files: Sequence[str]) -> None:
        import subprocess

        writer = QueueWriter(self.log_queue)
        ok = fail = skipped = changed = 0
        # clean: --replace makes tes3cmd overwrite its input (we work on a
        # staged COPY and copy back ourselves); --hide-backups keeps its own
        # backup clutter inside the disposable staging dir
        sub = {"clean": ["clean", "--replace", "--hide-backups"], "header": ["header"]}[cmd]

        def _run_t3(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            env = dict(os.environ)
            env["PWD"] = str(cwd)  # tes3cmd trusts $PWD over getcwd when set
            # check=False: the caller inspects returncode itself and reports it
            # in the log rather than raising.
            return subprocess.run(
                args,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=900,
                cwd=str(cwd),
                env=env,
                check=False,
                **core._no_window_kwargs(),
            )

        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                print("\n" + "=" * 70)
                print(
                    f" TES3CMD {' '.join(sub).upper()}"
                    + (" (staged, VFS-aware)" if cmd == "clean" else "")
                )
                print("=" * 70)
                print(_("  Engine: %(command)s") % {"command": " ".join(argv)})
                index = None
                staging = None
                if cmd == "clean":
                    dirs = self._plan_scan_dirs()
                    dirs += [str(Path(f).parent) for f in files]
                    index = core.PluginFileIndex(list(dict.fromkeys(dirs)))
                    staging = self._t3_staging_dir()
                    print(_("  Staging dir: %(path)s") % {"path": staging})
                for f in files:
                    name = Path(f).name
                    print(f"\n--- {' '.join(sub)}: {f}")
                    try:
                        if cmd == "header":
                            r = _run_t3(argv + sub + extra + [name], Path(f).parent)
                            print(
                                ((r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")).strip()
                                or "(no output)"
                            )
                            ok += 1 if r.returncode == 0 else 0
                            fail += 0 if r.returncode == 0 else 1
                            continue
                        # clean: stage plugin + masters, run there, copy back
                        assert staging is not None  # set on the clean path  # noqa: S101
                        staged, missing = core.stage_for_tes3cmd(staging, f, index)
                        if staged is None:
                            skipped += 1
                            print(_("  SKIPPED: could not stage '%(name)s'.") % {"name": name})
                            continue
                        if missing:
                            skipped += 1
                            print(
                                _(
                                    "  SKIPPED: master(s) not found in any data folder: "
                                    "%(names)s -- cleaning without the masters "
                                    "present gives wrong results."
                                )
                                % {"names": ", ".join(missing)}
                            )
                            continue
                        before = staged.read_bytes()
                        r = _run_t3(argv + sub + extra + [staged.name], staged.parent)
                        print(
                            ((r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")).strip()
                            or "(no output)"
                        )
                        if r.returncode != 0:
                            fail += 1
                            print(
                                _("  (exit code %(code)d) -- original NOT touched")
                                % {"code": r.returncode}
                            )
                            continue
                        after = staged.read_bytes()
                        if after == before:
                            ok += 1
                            print(_("  no changes -- already clean"))
                            continue
                        # copy the cleaned result back over the original,
                        # keeping a one-time backup of the original
                        import shutil as _sh

                        bak = Path(f).with_name(name + ".preclean.bak")
                        if not bak.exists():
                            _sh.copy2(f, bak)
                        Path(f).write_bytes(after)
                        ok += 1
                        changed += 1
                        print(
                            _("  cleaned: %(before)d -> %(after)d bytes (backup: %(backup)s)")
                            % {"before": len(before), "after": len(after), "backup": bak.name}
                        )
                    except Exception as e:  # noqa: BLE001
                        # per-file isolation: one unclean plugin must not abort the batch
                        fail += 1
                        print(_("  ERROR: %(error)s") % {"error": e})
            if cmd == "clean":
                status = _(
                    "tes3cmd clean: %(changed)d cleaned, %(clean)d already clean, "
                    "%(skipped)d skipped (missing masters), %(failed)d failed."
                ) % {
                    "changed": changed,
                    "clean": ok - changed,
                    "skipped": skipped,
                    "failed": fail,
                }
                if changed:
                    status += _("  Re-run '1. Sort' to refresh checks.")
            else:
                status = _("tes3cmd %(command)s: %(ok)d ok, %(failed)d failed. See the log.") % {
                    "command": cmd,
                    "ok": ok,
                    "failed": fail,
                }
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: tes3cmd run failed:\n" + traceback.format_exc())
            status = "tes3cmd run failed -- see log."
        finally:
            self.root.after(0, self._t3_finished, status)

    def _t3_sync_worker(self, files: Sequence[str]) -> None:
        """In-app master-size resync (see core.sync_plugin_master_sizes)."""
        writer = QueueWriter(self.log_queue)
        ok = fixed = fail = 0
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                print("\n" + "=" * 70)
                print(_(" MASTER-SIZE RESYNC (in-app, VFS-aware)"))
                print("=" * 70)
                dirs = self._plan_scan_dirs()
                extra = [str(Path(f).parent) for f in files]
                index = core.PluginFileIndex(list(dict.fromkeys(dirs + extra)))
                for f in files:
                    name = Path(f).name
                    updated, unresolved, err = core.sync_plugin_master_sizes(f, index)
                    if err:
                        fail += 1
                        print(_("  %(name)s: ERROR: %(error)s") % {"name": name, "error": err})
                        continue
                    ok += 1
                    if updated:
                        fixed += 1
                        for m, old, new in updated:
                            print(f"  {name}: '{m}' {old} -> {new}")
                    else:
                        print(_("  %(name)s: already in sync") % {"name": name})
                    for m in unresolved:
                        print(
                            _(
                                "  %(name)s: WARNING: master '%(master)s' not found in any "
                                "data folder -- its size field left untouched"
                            )
                            % {"name": name, "master": m}
                        )
            status = _(
                "Resync: %(fixed)d plugin(s) updated, %(ok)d already in sync, "
                "%(failed)d error(s)."
            ) % {"fixed": fixed, "ok": ok - fixed, "failed": fail}
            if fixed:
                status += _("  Re-run '1. Sort' to refresh the master check.")
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: resync failed:\n" + traceback.format_exc())
            status = "Resync failed -- see log."
        finally:
            self.root.after(0, self._t3_finished, status)

    def _t3_finished(self, status: str) -> None:
        self.worker_running = False
        self.sort_button.configure(state="normal")
        try:
            if self._t3_win and self._t3_win.winfo_exists():
                self._t3_run_btn.configure(state="normal")
        except tk.TclError:  # the tes3cmd window may already be gone
            pass
        self.status_var.set(status)

    def _set_tes3conv(self) -> None:
        p = filedialog.askopenfilename(
            title=_("Locate the tes3conv executable"), filetypes=(("All files", "*.*"),)
        )
        if not p:
            return
        self._tes3conv_override = p
        self.status_var.set(
            _("tes3conv set — click 'Check Conflicts' again to re-scan with field diffs.")
        )
        messagebox.showinfo(
            _("tes3conv set"),
            _(
                "tes3conv location saved.\n\nClick 'Check Conflicts' again to re-scan; the "
                "field comparison will then populate when you select a record."
            ),
        )
