#!/usr/bin/env python3
import gi, os, sys, pathlib, subprocess, threading

# GTK Initialization
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk

STATUS_COL = 2
COLOR_MAP = {
    "NULL": "#cccccc",
    "PASS": "#a8f0a8",
    "FAIL": "#f0a8a8",
}

DSRC_DIR = "design_src"
SSRC_DIR = "sim_src"
SRES_DIR = "sim_result"
GOLDEN_DIR = "golden"

class TeeStream:
    def __init__(self, gui_callback, orig_stream, sync_filter_func):
        self.gui_callback = gui_callback
        self.orig_stream = orig_stream
        self.buffer = ""
        self.sync_filter_func = sync_filter_func

    def write(self, data):
        self.orig_stream.write(data)
        self.orig_stream.flush()
        self.buffer += data
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            GLib.idle_add(self.sync_filter_func, line + "\n")

    def flush(self):
        self.orig_stream.flush()

class playV(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="tw.nycu.playv.v3_0")
        root = os.environ.get("LABSROOT")
        if not root:
            raise SystemExit("❌ Please set LABSROOT")
        self.labs_root = pathlib.Path(root).expanduser()
        if not self.labs_root.is_dir():
            raise SystemExit(f"LABSROOT is not a directory: {self.labs_root}")

        # Initialize but defer content setting until do_activate
        self.subdirs = []
        self.child_map = {}
        self.store = Gtk.ListStore(str, str, str)
        self.row_map = {}
        self._busy = False
        self._combo_ignore = False
        self.sync_output = False
        self.current_lab = None
        self.current_prob = None
        self.sim_running = False
        self.sim_terminal_buffer = ""
        self.sim_student_can_see = False

    def do_activate(self):
        print("✅ GUI starting...")

        # Rebuild directory structure
        self.subdirs = sorted(
            [p for p in self.labs_root.iterdir() if p.is_dir() and not p.name.startswith('.')]
        )

        if not self.subdirs:
            dialog = Gtk.MessageDialog(
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CLOSE,
                text="⚠️ No lab folders found in LABSROOT."
            )
            dialog.set_modal(True)
            dialog.run()
            dialog.destroy()
            self.quit()
            return

        # Set child_map only for valid directories
        self.child_map = {}
        for lab in self.subdirs:
            try:
                children = [c for c in lab.iterdir() if c.is_dir() and not c.name.startswith('.')]
                self.child_map[lab] = sorted(children)
            except Exception as e:
                print(f"[Warning] Failed to read subdirectories of {lab}: {e}", file=sys.stderr)
                self.child_map[lab] = []

        print(f"✅ LABSROOT: {self.labs_root}")
        print(f"✅ labs: {[p.name for p in self.subdirs]}")

        # Prevent proceeding if all subdirectories are also empty (e.g. lab1 has no content)
        if all(len(children) == 0 for children in self.child_map.values()):
            dialog = Gtk.MessageDialog(
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CLOSE,
                text="⚠️ No problems found in any lab folders."
            )
            dialog.set_modal(True)
            dialog.run()
            dialog.destroy()
            self.quit()
            return

        # TODO: Connect to GUI setup logic (e.g., create main window, components, etc.)
        print("✅ Directory scan and validation complete. Proceed to GUI setup.")
        win = Gtk.ApplicationWindow(application=self)
        win.set_wmclass("playV", "playV")
        win.set_title("playV v3.0")

        screen = win.get_screen()
        width = screen.get_width()
        height = screen.get_height()
        win.set_default_size(width, height)

        hpaned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        win.add(hpaned)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main_box.set_border_width(20)
        hpaned.add1(main_box)

        self.lbl_cwd = Gtk.Label(xalign=0)
        main_box.pack_start(self.lbl_cwd, False, False, 0)

        self.combo_parent = Gtk.ComboBoxText()
        for p in self.subdirs:
            self.combo_parent.append_text(p.name)
        self.combo_parent.set_active(0)
        self.combo_parent.connect("changed", self.on_parent_changed)
        self.combo_parent.hide()
        self.combo_child = Gtk.ComboBoxText()
        self.combo_child.connect("changed", lambda *_: self.switch_to_selected())
        self.combo_child.hide()

        # 第一行按鈕區
        hbox = Gtk.Box(spacing=10)
        main_box.pack_start(hbox, False, False, 0)

        self.btn_code  = Gtk.Button(label="Code Editor")
        self.btn_test  = Gtk.Button(label="Simulation")
        self.btn_wave  = Gtk.Button(label="Waveform (yours)")

        self.btn_code.connect("clicked", self.open_vscode)
        self.btn_test.connect("clicked", lambda *_: self.run_make_async("test"))
        self.btn_wave.connect("clicked", self.open_gtkwave)

        hbox.pack_start(self.btn_code, True, True, 0)
        hbox.pack_start(self.btn_test, True, True, 0)
        hbox.pack_start(self.btn_wave, True, True, 0)

        # 第二行單獨放 golden 波形按鈕
        hbox_golden = Gtk.Box(spacing=10)
        main_box.pack_start(hbox_golden, False, False, 0)

        self.btn_show_golden_log = Gtk.Button(label="Result (golden)")
        self.btn_wave_golden = Gtk.Button(label="Waveform (golden)")

        self.btn_show_golden_log.connect("clicked", self.show_golden_log)
        self.btn_wave_golden.connect("clicked", self.open_gtkwave_golden)

        hbox_golden.pack_start(self.btn_show_golden_log, True, True, 0)
        hbox_golden.pack_start(self.btn_wave_golden, True, True, 0)

        self.all_buttons = [self.btn_code, self.btn_test, self.btn_wave, self.btn_show_golden_log, self.btn_wave_golden]

        tree = Gtk.TreeView(model=self.store)
        tree.set_headers_visible(True)
        tree.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        tree.connect("button-press-event", self.on_tree_click)
        renderer = Gtk.CellRendererText()
        col_lab = Gtk.TreeViewColumn("lab", renderer, text=0)
        col_lab.set_min_width(150)
        col_lab.set_expand(True)
        tree.append_column(col_lab)
        col_prob = Gtk.TreeViewColumn("problem", renderer, text=1)
        col_prob.set_min_width(150)
        col_prob.set_expand(True)
        tree.append_column(col_prob)
        renderer_status = Gtk.CellRendererText()
        col_status = Gtk.TreeViewColumn("status", renderer_status, text=STATUS_COL)
        col_status.set_min_width(90)
        col_status.set_expand(True)
        col_status.set_cell_data_func(renderer_status, self._status_color_func)
        tree.append_column(col_status)
        tree.get_selection().connect("changed", self.on_tree_selected)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.add(tree)
        frame = Gtk.Frame(label="score board")
        frame.add(scroller)
        main_box.pack_start(frame, True, True, 0)
        hbox2 = Gtk.Box(spacing=10)
        main_box.pack_start(hbox2, False, False, 0)
        self.btn_refresh_status = Gtk.Button(label="Refresh Status")
        self.btn_refresh_status.connect("clicked", self.on_refresh_status_clicked)
        hbox2.pack_start(self.btn_refresh_status, True, True, 0)
        self.all_buttons.insert(0, self.btn_refresh_status)
        self.btn_reset_all = Gtk.Button(label="Reset Simulation All")
        self.btn_reset_all.connect("clicked", self.on_reset_all_clicked)
        hbox2.pack_start(self.btn_reset_all, True, True, 0)
        self.btn_test_all = Gtk.Button(label="Simulation All")
        self.btn_test_all.connect("clicked", self.on_test_all_clicked)
        hbox2.pack_start(self.btn_test_all, True, True, 0)
        self.all_buttons += [self.btn_reset_all, self.btn_test_all]
        self.refresh_child_options()
        self.switch_to_selected()
        self.tree = tree
        self.set_buttons_sensitive(False)

        term_frame = Gtk.Frame(label="Simulation Result")
        hpaned.add2(term_frame)

        danger_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        danger_label = Gtk.Label()
        danger_label.set_markup('<span foreground="red"><b>Dangerous Zone</b></span>')
        danger_box.pack_start(danger_label, False, False, 0)

        btn_reset_all_design = Gtk.Button(label="Reset Every Design")
        btn_reset_all_design.connect("clicked", self.on_reset_all_design_clicked)
        danger_box.pack_start(btn_reset_all_design, False, False, 0)

        main_box.pack_end(danger_box, False, False, 0)

        # === 右半部 VBox 包含 Clear 按鈕與 terminal 區塊 ===
        term_right_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        term_frame.add(term_right_vbox)

        # Clear 按鈕
        self.btn_clear_terminal = Gtk.Button(label="Clear")
        self.btn_clear_terminal.connect("clicked", self.on_clear_terminal_clicked)
        term_right_vbox.pack_start(self.btn_clear_terminal, False, False, 0)

        # TextView + overlay
        self.term_view = Gtk.TextView()
        self.term_view.set_editable(False)
        self.term_view.set_cursor_visible(False)
        self.term_view.set_monospace(True)
        self.term_buffer = self.term_view.get_buffer()
        self.term_view.set_wrap_mode(Gtk.WrapMode.CHAR)
        term_scroll = Gtk.ScrolledWindow()
        term_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
        term_scroll.set_vexpand(True)
        term_scroll.add(self.term_view)

        self.term_overlay = Gtk.Overlay()
        self.term_overlay.add(term_scroll)

        overlay_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        overlay_box.set_halign(Gtk.Align.CENTER)
        overlay_box.set_valign(Gtk.Align.CENTER)
        overlay_box.set_spacing(8)
        overlay_box.set_size_request(160, 80)
        overlay_box.set_margin_top(24)
        overlay_box.set_margin_bottom(24)

        self.sim_spinner = Gtk.Spinner()
        self.sim_spinner.set_size_request(36, 36)
        overlay_box.pack_start(self.sim_spinner, False, False, 0)
        self.sim_label = Gtk.Label(label="simulation...")
        overlay_box.pack_start(self.sim_label, False, False, 0)

        self.sim_mask = Gtk.EventBox()
        self.sim_mask.set_visible_window(True)
        rgba = Gdk.RGBA(0.5, 0.5, 0.5, 0.45)  # 半透明灰
        self.sim_mask.override_background_color(Gtk.StateFlags.NORMAL, rgba)
        self.sim_mask.add(overlay_box)
        self.term_overlay.add_overlay(self.sim_mask)

        term_right_vbox.pack_start(self.term_overlay, True, True, 0)

        win.show_all()
        self.sim_mask.hide()
        hpaned.set_position(int(width * 1/3))
        sys.stdout = TeeStream(self.gui_sync_output, sys.__stdout__, self.sync_filter)
        sys.stderr = TeeStream(self.gui_sync_output, sys.__stderr__, self.sync_filter)

    def append_to_terminal(self, text):
        end_iter = self.term_buffer.get_end_iter()
        self.term_buffer.insert(end_iter, text)
        mark = self.term_buffer.create_mark(None, self.term_buffer.get_end_iter(), False)
        self.term_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
        return False

    def gui_sync_output(self, text):
        # for simulation error dialog (v3.0)
        if getattr(self, "sim_running", False):
            self.sim_terminal_buffer += text + "\n"
            if "##SEC_STUDENT_CAN_SEE" in text:
                self.sim_student_can_see = True

        if text.endswith("\n"):
            text = text[:-1]
        if "##SEC_STUDENT_CAN_SEE" in text:
            self.sync_output = True
            prob_name = self.current_prob or "(unnamed)"
            self.append_to_terminal(f"【{prob_name}】\n")
            return False
        elif "##END_STUDENT_CAN_SEE" in text:
            self.sync_output = False
            return False
        elif self.sync_output:
            self.append_to_terminal(text + "\n")
        return False

    def sync_filter(self, text):
        return self.gui_sync_output(text)

    def _populate_store(self):
        for lab in self.subdirs:
            probs = self.child_map[lab] or [""]
            for prob in probs:
                p_name = prob.name if isinstance(prob, pathlib.Path) else prob
                it = self.store.append([lab.name, p_name, "NULL"])
                self.row_map[(lab.name, p_name)] = it

    def _status_color_func(self, column, cell, model, it, _):
        status = model[it][STATUS_COL].upper()
        cell.set_property("cell-background", COLOR_MAP.get(status, "#ffffff"))

    def on_tree_click(self, tree, event):
        if event.button != 1:
            return False
        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        sel = tree.get_selection()
        if hit is None:
            sel.unselect_all()
            self.set_buttons_sensitive(False)
            return False
        path, col, cellx, celly = hit
        model  = tree.get_model()
        it = model.get_iter(path)
        if sel.iter_is_selected(it):
            sel.unselect_iter(it)
            self.set_buttons_sensitive(False)
            return True
        return False

    def on_tree_selected(self, selection):
        if self._busy:
            return
        model, it = selection.get_selected()
        self.set_buttons_sensitive(it is not None)
        if not it:
            self.current_lab = None
            self.current_prob = None
            return
        lab, prob = model[it][0], model[it][1]
        self.current_lab = lab
        self.current_prob = prob
        self._combo_ignore = True
        self.combo_parent.set_active(
            next(i for i, p in enumerate(self.subdirs) if p.name == lab)
        )
        self.refresh_child_options()
        if prob:
            clist = self.child_map[self.subdirs[self.combo_parent.get_active()]]
            for idx, c in enumerate(clist):
                if c.name == prob:
                    self.combo_child.set_active(idx)
                    break
        else:
            self.combo_child.set_active(-1)
        self._combo_ignore = False
        self.switch_to_selected()

    def on_parent_changed(self, *_):
        if self._busy or self._combo_ignore:
            return
        self.refresh_child_options()
        self.switch_to_selected()

    def refresh_child_options(self):
        self.combo_child.remove_all()
        parent = self.subdirs[self.combo_parent.get_active()]
        for c in self.child_map[parent]:
            self.combo_child.append_text(c.name)
        if self.combo_child.get_active() < 0 and self.combo_child.get_model():
            self.combo_child.set_active(0)

    def switch_to_selected(self):
        if self._busy:
            return
        lab  = self.subdirs[self.combo_parent.get_active()].name
        prob = self.combo_child.get_active_text() or ""
        target = self.labs_root / lab / prob if prob else self.labs_root / lab
        try:
            os.chdir(target)
            self.lbl_cwd.set_text(f"Current CWD: {target}")
        except Exception as e:
            print(f"[playV] 切換失敗: {e}", file=sys.stderr)

    def run_make_async(self, target):
        if self._busy:
            return
        # -- v3.0 simulation mode tracking --
        self.sim_terminal_buffer = ""
        self.sim_student_can_see = False
        self.sim_running = ("test" in target)
        self.set_busy(True)
        threading.Thread(target=self._run_make, args=(target,), daemon=True).start()

    def _run_make(self, target):
        lab  = self.subdirs[self.combo_parent.get_active()].name
        prob = self.combo_child.get_active_text() or ""
        try:
            self._run_and_log(["make"] + target.split())
        finally:
            if "test" in target:
                status = self._read_status()
                GLib.idle_add(self._update_status, lab, prob, status)
                # v3.0: show dialog if never enter student can see mode
                if not self.sim_student_can_see:
                    buffer_copy = self.sim_terminal_buffer.strip()
                    GLib.idle_add(self.show_sim_error_popup, buffer_copy)
            elif "clean" in target:
                GLib.idle_add(self._update_status, lab, prob, "NULL")
            GLib.idle_add(self.set_busy, False)
            self.sim_running = False

    def show_sim_error_popup(self, message):
        dialog = Gtk.Dialog(title="Error Message", parent=None, modal=True)
        dialog.set_modal(True)
        dialog.set_deletable(False)
        dialog.set_resizable(True)
        dialog.add_button("OK", Gtk.ResponseType.OK)
        content_area = dialog.get_content_area()
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_width(1200)
        sw.set_min_content_height(400)
        textview = Gtk.TextView()
        textview.set_editable(False)
        textview.set_cursor_visible(False)
        textview.set_monospace(True)
        # 修正：只加非空行
        lines = [line.rstrip() for line in message.splitlines() if line.strip()]
        msg_fixed = "\n".join(lines)
        textview.get_buffer().set_text(msg_fixed or "(No output)")
        sw.add(textview)
        content_area.pack_start(sw, True, True, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def _run_and_log(self, cmd, cwd=None):
        try:
            p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in iter(p.stdout.readline, ''):
                sys.__stdout__.write(line)
                sys.__stdout__.flush()
                GLib.idle_add(self.gui_sync_output, line)
            p.stdout.close()
            p.wait()
        except Exception as e:
            err = f"[playV] 指令失敗: {' '.join(cmd)}: {e}\n"
            sys.__stderr__.write(err)
            GLib.idle_add(self.gui_sync_output, err)

    @staticmethod
    def _read_status():
        try:
            txt = (pathlib.Path.cwd() / SRES_DIR / "result.txt").read_text().strip().lower()
            return "PASS" if txt == "pass" else "FAIL"
        except Exception:
            return "FAIL"

    def _update_status(self, lab, prob, status):
        it = self.row_map.get((lab, prob))
        if it:
            self.store.set(it, (STATUS_COL,), (status,))

    def open_vscode(self, *_):
        cwd = pathlib.Path.cwd() / DSRC_DIR
        v_files = sorted(str(p) for p in cwd.glob("*.v"))
        cmd = ["code", "-n", str(cwd)] + v_files
        threading.Thread(target=self._run_and_log, args=(cmd,), daemon=True).start()

    def open_gtkwave(self, *_):
        wave = pathlib.Path.cwd() / SRES_DIR / "wave.vcd"
        if wave.is_file():
            threading.Thread(target=self._run_and_log, args=(["gtkwave", str(wave)],), daemon=True).start()
        else:
            msg = "[playV] wave.vcd 不存在\n"
            sys.stderr.write(msg)
            GLib.idle_add(self.gui_sync_output, msg)

    def show_golden_log(self, *_):
        golden_log = pathlib.Path.cwd() / GOLDEN_DIR / "golden_log.txt"
        prob_name = self.current_prob or "(unnamed)"
        self.append_to_terminal(f"【{prob_name}】 golden\n")
        try:
            content = golden_log.read_text().strip()
        except Exception as e:
            content = f"[playV] 無法讀取 golden_log.txt: {e}"
        for line in content.splitlines():
            if line.strip().startswith("##"):
                continue
            self.append_to_terminal(line + "\n")

    def open_gtkwave_golden(self, *_):
        wave = pathlib.Path.cwd() / GOLDEN_DIR / "golden_wave.vcd"
        if wave.is_file():
            threading.Thread(target=self._run_and_log, args=(["gtkwave", str(wave)],), daemon=True).start()
        else:
            msg = "[playV] golden_wave.vcd 不存在\n"
            sys.stderr.write(msg)
            GLib.idle_add(self.gui_sync_output, msg)

    def set_busy(self, flag):
        self._busy = flag
        for w in self.all_buttons:
            w.set_sensitive(not flag)
        self.tree.set_sensitive(not flag)    # 只有指令執行時才同步鎖定選單
        if flag:
            self.sim_mask.show()
            self.sim_spinner.start()
        else:
            self.sim_spinner.stop()
            self.sim_mask.hide()
        return False

    def set_buttons_sensitive(self, flag):
        if self._busy:
            for w in self.all_buttons:
                w.set_sensitive(False)
        else:
            for w in self.all_buttons:
                w.set_sensitive(flag)

            self.btn_refresh_status.set_sensitive(True)
            self.btn_reset_all.set_sensitive(True)
            self.btn_test_all.set_sensitive(True)

    def _show_cwd(self, dirpath):
        try:
            os.chdir(dirpath)
            self.lbl_cwd.set_text(f"Current CWD: {dirpath}")
        except Exception as e:
            msg = f"[playV] CWD 切換失敗: {dirpath}: {e}\n"
            sys.stderr.write(msg)
            GLib.idle_add(self.gui_sync_output, msg)
        return False

    def _restore_selected_cwd(self):
        if self.current_lab is not None:
            if self.current_prob:
                target = self.labs_root / self.current_lab / self.current_prob
            else:
                target = self.labs_root / self.current_lab
            os.chdir(target)
            self.lbl_cwd.set_text(f"Current CWD: {target}")
    
    def on_refresh_status_clicked(self, *_):
        if self._busy:
            return
        self.set_busy(True)
        threading.Thread(target=self._refresh_all_status, daemon=True).start()

    def _refresh_all_status(self):
        self.subdirs = sorted(
            p for p in self.labs_root.iterdir()
            if p.is_dir() and not p.name.startswith('.')
        )
        self.child_map = {
            p: sorted(
                c for c in p.iterdir()
                if c.is_dir() and not c.name.startswith('.')
            )
            for p in self.subdirs
        }

        GLib.idle_add(self._refresh_store_and_status)

    def _refresh_store_and_status(self):
        self.store.clear()
        self.row_map.clear()
        for lab in self.subdirs:
            probs = self.child_map[lab] or [""]
            for prob in probs:
                p_name = prob.name if isinstance(prob, pathlib.Path) else prob
                it = self.store.append([lab.name, p_name, "NULL"])
                self.row_map[(lab.name, p_name)] = it
                dirpath = lab / p_name if p_name else lab
                result_file = dirpath / SRES_DIR / "result.txt"
                try:
                    txt = result_file.read_text().strip().lower()
                    status = "PASS" if txt == "pass" else "FAIL"
                except Exception:
                    status = "NULL"
                self.store.set(it, (STATUS_COL,), (status,))
        self.refresh_child_options()
        self.switch_to_selected()
        self.set_busy(False)

    def _reload_lab_structure(self):
        self.subdirs = sorted(
            p for p in self.labs_root.iterdir()
            if p.is_dir() and not p.name.startswith('.')
        )
        self.child_map = {
            p: sorted(
                c for c in p.iterdir()
                if c.is_dir() and not c.name.startswith('.')
            )
            for p in self.subdirs
        }

    def on_reset_all_clicked(self, *_):
        if self._busy:
            return
        self.set_busy(True)
        threading.Thread(target=self._reset_all, daemon=True).start()

    def _reset_all(self):
        self._reload_lab_structure()
        for lab in self.subdirs:
            for prob in self.child_map[lab] or [None]:
                if prob:
                    dirpath = lab / prob.name
                    prob_name = prob.name
                else:
                    dirpath = lab
                    prob_name = ""
                GLib.idle_add(self._show_cwd, dirpath)
                self._run_and_log(["make", "clean"], cwd=dirpath)
                GLib.idle_add(self._update_status, lab.name, prob_name, "NULL")
        GLib.idle_add(self.set_busy, False)
        GLib.idle_add(self._restore_selected_cwd)

    def on_test_all_clicked(self, *_):
        if self._busy:
            return
        self.set_busy(True)
        threading.Thread(target=self._test_all, daemon=True).start()

    def on_clear_terminal_clicked(self, *_):
        self.term_buffer.set_text("")

    def _test_all(self):
        self._reload_lab_structure()
        for lab in self.subdirs:
            for prob in self.child_map[lab] or [None]:
                if prob:
                    dirpath = lab / prob.name
                    prob_name = prob.name
                else:
                    dirpath = lab
                    prob_name = ""
                self.current_lab = lab.name
                self.current_prob = prob.name if prob else ""
                GLib.idle_add(self._show_cwd, dirpath)
                self._run_and_log(["make", "test"], cwd=dirpath)
                status = "FAIL"
                try:
                    txt = (dirpath / SRES_DIR / "result.txt").read_text().strip().lower()
                    if txt == "pass":
                        status = "PASS"
                except Exception:
                    status = "FAIL"
                GLib.idle_add(self._update_status, lab.name, prob_name, status)
        GLib.idle_add(self.set_busy, False)
        GLib.idle_add(self._restore_selected_cwd)

    def on_reset_all_design_clicked(self, *_):
        dialog = Gtk.MessageDialog(
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Will erase all of your Verilog code. Do you want to continue?"
        )
        dialog.set_modal(True)
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.YES:
            dev_root = os.environ.get("LABS_DEV_ROOT")
            pub_root = os.environ.get("LABS_PUBLIC_ROOT")
            if not dev_root or not pub_root:
                self.append_to_terminal("[playV] LABS_DEV_ROOT or LABS_PUBLIC_ROOT not set.\n")
                return
            cmd = f'''
                    git -C "{dev_root}" pull
                    rm -rf "{pub_root}"/lab*
                    cp -r "{dev_root}"/lab*/ "{pub_root}"/
                    '''
            threading.Thread(target=self._run_and_log, args=(["bash", "-c", cmd],), daemon=True).start()
            threading.Thread(target=self._refresh_all_status, daemon=True).start()

if __name__ == "__main__":
    playV().run()
