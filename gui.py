import gi
import csv
import os
import json
import subprocess
import threading
import webbrowser
from datetime import datetime, timezone, timedelta

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Pango


DB_FILE = "jobs_db.json"
STATUSES = ["New", "Applied", "Ongoing", "Rejected", "NA"]
RANKS = ["HIGH", "MEDIUM", "LOW", "IGNORE", "ERROR", "UNKNOWN"]
NA_EXPIRY_HOURS = 24

# Color mapping for ranks
RANK_COLORS = {
    "HIGH": "#1a531b",   # green
    "MEDIUM": "#8b7e12", # yellow
    "LOW": "#8c4412",    # orange
    "IGNORE": "#404040", # dark grey
    "ERROR": "#591313",  # red
    "UNKNOWN": "#3b3b3b" # grey
}

class JobAppWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Job Scraper & Tracker", default_width=950, default_height=750)
        
        # Setup CSS
        self.setup_css()
        
        # Load DB
        self.jobs_db = self.load_db()
        
        # Main layout
        self.vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.vbox.set_margin_top(10)
        self.vbox.set_margin_bottom(10)
        self.vbox.set_margin_start(10)
        self.vbox.set_margin_end(10)
        self.set_child(self.vbox)
        
        # --- HEADER / GLOBAL ACTIONS ---
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.vbox.append(header_box)
        
        self.scrape_btn = Gtk.Button(label="Scrape Jobs")
        self.scrape_btn.add_css_class("suggested-action")
        self.scrape_btn.connect("clicked", self.on_scrape_clicked)
        header_box.append(self.scrape_btn)
        
        self.refresh_btn = Gtk.Button(label="Refresh Everything")
        self.refresh_btn.connect("clicked", self.on_refresh_clicked)
        header_box.append(self.refresh_btn)
        
        # Ollama GPU toggle
        self.ollama_btn = Gtk.Button()
        self.ollama_btn.connect("clicked", self.on_ollama_toggle)
        header_box.append(self.ollama_btn)
        self.update_ollama_btn_label()
        
        # Delete IGNORE jobs button
        self.del_ignore_btn = Gtk.Button(label="🗑 Delete IGNORE")
        self.del_ignore_btn.add_css_class("destructive-action")
        self.del_ignore_btn.connect("clicked", self.on_delete_ignore_clicked)
        header_box.append(self.del_ignore_btn)
        
        self.status_label = Gtk.Label(label="Ready.")
        self.status_label.set_hexpand(True)
        self.status_label.set_halign(Gtk.Align.END)
        header_box.append(self.status_label)
        
        # Notebook for Tabs
        self.notebook = Gtk.Notebook()
        self.notebook.set_vexpand(True)
        self.notebook.set_hexpand(True)
        self.vbox.append(self.notebook)
        
        # --- TAB 1: Fresh Jobs (New) ---
        fresh_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        
        # Filter bar
        filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        filter_box.set_margin_top(8)
        filter_box.set_margin_start(10)
        filter_box.set_margin_end(10)
        filter_label = Gtk.Label(label="Filter by Rank:")
        filter_box.append(filter_label)
        
        self.rank_filter_options = ["ALL"] + RANKS
        filter_model = Gtk.StringList.new(self.rank_filter_options)
        self.rank_filter_drop = Gtk.DropDown(model=filter_model)
        self.rank_filter_drop.set_selected(0)  # Default: ALL
        self.rank_filter_drop.connect("notify::selected", self.on_rank_filter_changed)
        filter_box.append(self.rank_filter_drop)
        
        fresh_vbox.append(filter_box)
        
        self.fresh_scroll = Gtk.ScrolledWindow()
        self.fresh_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.fresh_scroll.set_vexpand(True)
        self.fresh_listbox = Gtk.ListBox()
        self.fresh_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.fresh_listbox.add_css_class("boxed-list")
        self.fresh_scroll.set_child(self.fresh_listbox)
        fresh_vbox.append(self.fresh_scroll)
        
        self.notebook.append_page(fresh_vbox, Gtk.Label(label="Fresh Jobs"))
        
        # --- TAB 2: Applied ---
        self.applied_scroll = Gtk.ScrolledWindow()
        self.applied_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.applied_listbox = Gtk.ListBox()
        self.applied_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.applied_listbox.add_css_class("boxed-list")
        self.applied_scroll.set_child(self.applied_listbox)
        self.notebook.append_page(self.applied_scroll, Gtk.Label(label="Applied"))
        
        # --- TAB 3: Ongoing ---
        self.ongoing_scroll = Gtk.ScrolledWindow()
        self.ongoing_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.ongoing_listbox = Gtk.ListBox()
        self.ongoing_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.ongoing_listbox.add_css_class("boxed-list")
        self.ongoing_scroll.set_child(self.ongoing_listbox)
        self.notebook.append_page(self.ongoing_scroll, Gtk.Label(label="Ongoing"))
        
        # --- TAB 4: Rejected ---
        self.rejected_scroll = Gtk.ScrolledWindow()
        self.rejected_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.rejected_listbox = Gtk.ListBox()
        self.rejected_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.rejected_listbox.add_css_class("boxed-list")
        self.rejected_scroll.set_child(self.rejected_listbox)
        self.notebook.append_page(self.rejected_scroll, Gtk.Label(label="Rejected"))
        
        # --- TAB 5: NA (Expired / Not Acted Upon) ---
        self.na_scroll = Gtk.ScrolledWindow()
        self.na_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.na_listbox = Gtk.ListBox()
        self.na_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.na_listbox.add_css_class("boxed-list")
        self.na_scroll.set_child(self.na_listbox)
        self.notebook.append_page(self.na_scroll, Gtk.Label(label="NA"))
        
        # --- TAB 6: Priority Ranking Screen ---
        priority_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        priority_vbox.set_margin_top(15)
        priority_vbox.set_margin_bottom(15)
        priority_vbox.set_margin_start(15)
        priority_vbox.set_margin_end(15)
        
        # Priority Header
        priority_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        priority_vbox.append(priority_header)
        
        self.sort_btn = Gtk.Button(label="AI Rank/Sort Jobs")
        self.sort_btn.add_css_class("suggested-action")
        self.sort_btn.connect("clicked", self.on_sort_clicked)
        priority_header.append(self.sort_btn)
        
        self.sort_status_label = Gtk.Label(label="")
        priority_header.append(self.sort_status_label)
        
        # Priority Listbox
        self.prio_scroll = Gtk.ScrolledWindow()
        self.prio_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.prio_scroll.set_vexpand(True)
        self.prio_listbox = Gtk.ListBox()
        self.prio_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.prio_listbox.add_css_class("boxed-list")
        self.prio_scroll.set_child(self.prio_listbox)
        priority_vbox.append(self.prio_scroll)
        
        self.notebook.append_page(priority_vbox, Gtk.Label(label="Priority Sorting"))
        
        # Connect tab change signal to trigger special updates if needed
        self.notebook.connect("switch-page", self.on_tab_switched)

        # Initial Load
        self.backfill_added_at()
        self.expire_stale_jobs()
        self.refresh_ui()

    def setup_css(self):
        css_provider = Gtk.CssProvider()
        css = b"""
            .rank-label {
                padding: 4px 8px;
                border-radius: 4px;
                color: white;
                font-weight: bold;
                margin-right: 10px;
            }
            .rank-HIGH { background-color: #1a531b; }
            .rank-MEDIUM { background-color: #8b7e12; }
            .rank-LOW { background-color: #8c4412; }
            .rank-IGNORE { background-color: #404040; }
            .rank-ERROR { background-color: #591313; }
            .rank-UNKNOWN { background-color: #3b3b3b; }
        """
        css_provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def load_db(self):
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception: pass
        return {}

    def save_db(self):
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.jobs_db, f, indent=4)


    def backfill_added_at(self):
        """Add added_at timestamp to existing jobs and migrate old statuses."""
        updated = False
        for link, data in self.jobs_db.items():
            if "added_at" not in data:
                data["added_at"] = datetime.now(timezone.utc).isoformat()
                updated = True
            # Migrate old "Ongoing/Waiting" status to "Ongoing"
            if data.get("status") == "Ongoing/Waiting":
                data["status"] = "Ongoing"
                updated = True
        if updated:
            self.save_db()

    def expire_stale_jobs(self):
        """Auto-expire 'New' jobs older than 24 hours to 'NA'."""
        now = datetime.now(timezone.utc)
        expired = 0
        for link, data in self.jobs_db.items():
            if data.get("status") == "New":
                added_at_str = data.get("added_at")
                if added_at_str:
                    try:
                        added_at = datetime.fromisoformat(added_at_str)
                        if (now - added_at) > timedelta(hours=NA_EXPIRY_HOURS):
                            data["status"] = "NA"
                            expired += 1
                    except ValueError:
                        pass
        if expired > 0:
            self.save_db()
            print(f"⏰ Auto-expired {expired} stale jobs to NA.")


    def clear_listbox(self, listbox):
        while True:
            child = listbox.get_first_child()
            if not child: break
            listbox.remove(child)

    def get_listbox_for_status(self, status):
        """Return the correct listbox for a given job status."""
        mapping = {
            "New": self.fresh_listbox,
            "Applied": self.applied_listbox,
            "Ongoing": self.ongoing_listbox,
            "Rejected": self.rejected_listbox,
            "NA": self.na_listbox,
        }
        return mapping.get(status, self.fresh_listbox)

    def refresh_ui(self):
        self.clear_listbox(self.fresh_listbox)
        self.clear_listbox(self.applied_listbox)
        self.clear_listbox(self.ongoing_listbox)
        self.clear_listbox(self.rejected_listbox)
        self.clear_listbox(self.na_listbox)
        self.clear_listbox(self.prio_listbox)
        
        # Run expiry check before displaying
        self.expire_stale_jobs()
        
        counts = {"New": 0, "Applied": 0, "Ongoing": 0, "Rejected": 0, "NA": 0}
        
        # --- UI for all status tabs ---
        for link, data in self.jobs_db.items():
            status = data.get("status", "New")
            self.add_main_job_row(link, data)
            counts[status] = counts.get(status, 0) + 1
            
        self.status_label.set_text(
            f"Fresh: {counts['New']} | Applied: {counts['Applied']} | "
            f"Ongoing: {counts['Ongoing']} | Rejected: {counts['Rejected']} | NA: {counts['NA']}"
        )
        
        # --- UI for Priority (Sorted) ---
        # Sort logic: HIGH -> MEDIUM -> LOW -> UNKNOWN -> ERROR -> IGNORE
        def get_rank_weight(rank_str):
            weights = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3, "ERROR": 4, "IGNORE": 5}
            return weights.get(rank_str, 3)

        sorted_jobs = sorted(self.jobs_db.items(), key=lambda x: get_rank_weight(x[1].get("rank", "UNKNOWN")))
        for link, data in sorted_jobs:
            self.add_priority_row(link, data)

    def add_main_job_row(self, link, data):
        title = data.get("title", "Unknown")
        status = data.get("status", "New")
        rank = data.get("rank", "UNKNOWN")
        reason = data.get("reason", "")
        
        # Apply rank filter for Fresh Jobs only
        if status == "New":
            active_filter = self.get_active_rank_filter()
            if active_filter != "ALL" and rank != active_filter:
                return
        
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        
        # Rank pill
        rank_label = Gtk.Label(label=rank)
        rank_label.add_css_class("rank-label")
        rank_label.add_css_class(f"rank-{rank}")
        rank_label.set_valign(Gtk.Align.CENTER)
        
        # Title text
        title_label = Gtk.Label(label=title)
        title_label.set_hexpand(True)
        title_label.set_halign(Gtk.Align.START)
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        # Detailed tooltip showing URL + AI Reason
        tooltip_text = f"URL: {link}"
        if reason:
            tooltip_text += f"\n\nAI REASON:\n{reason}"
        title_label.set_tooltip_text(tooltip_text)
        
        # Dropdown for status (Modern GTK4)
        status_model = Gtk.StringList.new(STATUSES)
        status_drop = Gtk.DropDown(model=status_model)
        
        active_idx = STATUSES.index(status) if status in STATUSES else 0
        status_drop.set_selected(active_idx)
        status_drop.set_valign(Gtk.Align.CENTER)
        status_drop.connect("notify::selected", self.on_status_changed, link, row)
        
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.add_css_class("pill")
        apply_btn.set_valign(Gtk.Align.CENTER)
        apply_btn.connect("clicked", self.on_apply_clicked, link)
        
        box.append(rank_label)
        box.append(title_label)
        box.append(status_drop)
        box.append(apply_btn)
        row.set_child(box)
        
        target_listbox = self.get_listbox_for_status(status)
        target_listbox.append(row)

    def add_priority_row(self, link, data):
        title = data.get("title", "Unknown")
        rank = data.get("rank", "UNKNOWN")
        reason = data.get("reason", "No reason recorded.")
        status = data.get("status", "New")
        
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        
        top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        
        rank_label = Gtk.Label(label=rank)
        rank_label.add_css_class("rank-label")
        rank_label.add_css_class(f"rank-{rank}")
        
        title_label = Gtk.Label(label=title)
        title_label.set_hexpand(True)
        title_label.set_halign(Gtk.Align.START)
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        
        status_label = Gtk.Label(label=f"Status: {status}")
        status_label.set_valign(Gtk.Align.CENTER)
        
        top_box.append(rank_label)
        top_box.append(title_label)
        top_box.append(status_label)
        
        reason_label = Gtk.Label(label=reason)
        reason_label.set_halign(Gtk.Align.START)
        reason_label.set_wrap(True)
        reason_label.set_max_width_chars(100)
        reason_label.add_css_class("dim-label")
        
        box.append(top_box)
        box.append(reason_label)
        
        row.set_child(box)
        self.prio_listbox.append(row)

    def on_status_changed(self, dropdown, pspec, link, row):
        selected_item = dropdown.get_selected_item()
        if not selected_item: return
        new_status = selected_item.get_string()
            
        old_status = self.jobs_db[link].get("status", "New")
        if new_status == old_status: return
            
        self.jobs_db[link]["status"] = new_status
        self.save_db()
        
        # Move row to the correct tab
        if old_status != new_status:
            GLib.idle_add(self.move_main_row, row, new_status)
            
    def move_main_row(self, row, new_status):
        parent = row.get_parent()
        if parent: parent.remove(row)
        target_listbox = self.get_listbox_for_status(new_status)
        target_listbox.append(row)
        return False

    def on_apply_clicked(self, button, link):
        try: webbrowser.open(link)
        except Exception as e: print(f"Failed to open URI: {e}")

    def get_active_rank_filter(self):
        """Return the currently selected rank filter string."""
        idx = self.rank_filter_drop.get_selected()
        return self.rank_filter_options[idx] if idx < len(self.rank_filter_options) else "ALL"

    def on_rank_filter_changed(self, dropdown, pspec):
        """Re-populate Fresh Jobs when filter changes."""
        self.refresh_ui()

    def is_ollama_running(self):
        """Check if Ollama systemd service is active."""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "ollama"],
                capture_output=True, text=True, timeout=3
            )
            return result.stdout.strip() == "active"
        except Exception:
            return False

    def update_ollama_btn_label(self):
        """Update the Ollama button text based on service status."""
        if self.is_ollama_running():
            self.ollama_btn.set_label("🟢 Ollama ON")
            self.ollama_btn.remove_css_class("destructive-action")
            self.ollama_btn.add_css_class("suggested-action")
        else:
            self.ollama_btn.set_label("🔴 Ollama OFF")
            self.ollama_btn.remove_css_class("suggested-action")
            self.ollama_btn.add_css_class("destructive-action")

    def on_ollama_toggle(self, button):
        """Start or stop the Ollama service."""
        self.ollama_btn.set_sensitive(False)
        thread = threading.Thread(target=self.run_ollama_toggle)
        thread.daemon = True
        thread.start()

    def run_ollama_toggle(self):
        try:
            if self.is_ollama_running():
                subprocess.run(["pkexec", "systemctl", "stop", "ollama"], timeout=30)
            else:
                subprocess.run(["pkexec", "systemctl", "start", "ollama"], timeout=30)
        except Exception as e:
            print(f"Ollama toggle error: {e}")
        GLib.idle_add(self.on_ollama_toggle_done)

    def on_ollama_toggle_done(self):
        self.ollama_btn.set_sensitive(True)
        self.update_ollama_btn_label()
        return False

    def on_delete_ignore_clicked(self, button):
        """Remove all IGNORE-ranked jobs from the database."""
        ignore_links = [link for link, data in self.jobs_db.items() if data.get("rank") == "IGNORE"]
        for link in ignore_links:
            del self.jobs_db[link]
        if ignore_links:
            self.save_db()
            self.refresh_ui()
            self.status_label.set_text(f"🗑 Deleted {len(ignore_links)} IGNORE jobs.")
        else:
            self.status_label.set_text("No IGNORE jobs to delete.")

    def on_refresh_clicked(self, button):
        # Reload DB from disk
        self.jobs_db = self.load_db()
        self.refresh_ui()
        
    def on_tab_switched(self, notebook, page, page_num):
        # Refresh UI just in case DB changed between tabs (e.g. status changes impacting Priority tab)
        if page_num == 5: # Priority page (tab index 5 now)
            # Redrawing Priority page to update Status text on it
            self.refresh_ui()

    def on_scrape_clicked(self, button):
        self.status_label.set_text("Scraping started... (Check terminal output for progress)")
        self.scrape_btn.set_sensitive(False)
        thread = threading.Thread(target=self.run_scraper)
        thread.daemon = True
        thread.start()
        
    def run_scraper(self):
        import time
        try:
            # Use the virtual environment's Python explicitly
            venv_python = os.path.join(os.getcwd(), "scraper_env", "bin", "python")
            if not os.path.exists(venv_python):
                venv_python = "python3" # Fallback
            process = subprocess.Popen([venv_python, "intern_scraper.py"], cwd=os.getcwd())
            
            # While scraper runs, periodically sync CSV→DB and trigger ranking
            while process.poll() is None:
                time.sleep(30)  # Check every 30 seconds
                GLib.idle_add(self.periodic_sync_and_rank)
            
            GLib.idle_add(self.on_scrape_finished, process.returncode == 0)
        except Exception as e:
            print(f"Error running scraper: {e}")
            GLib.idle_add(self.on_scrape_finished, False)

    def periodic_sync_and_rank(self):
        """Reload DB from disk and refresh UI while scraper is still running."""
        self.jobs_db = self.load_db()
        self.refresh_ui()
        self.status_label.set_text("Scraping in progress... syncing new jobs live!")
        return False

    def on_scrape_finished(self, success):
        self.scrape_btn.set_sensitive(True)
        if success:
            # Final reload to catch any stragglers
            self.jobs_db = self.load_db()
            self.refresh_ui()
            self.status_label.set_text("Scraping finished!")
        else:
            self.status_label.set_text("Scraping failed.")

    # --- LLM RANKING LOGIC ---
    def on_sort_clicked(self, button):
        if not os.path.exists(DB_FILE):
            self.sort_status_label.set_text(f"No {DB_FILE}. Scrape first!")
            return
            
        self.sort_status_label.set_text("Initializing Llama 3.1 analysis... Check terminal!")
        self.sort_btn.set_sensitive(False)
        thread = threading.Thread(target=self.run_sorter)
        thread.daemon = True
        thread.start()
        
    def run_sorter(self):
        try:
            # Use the virtual environment's Python explicitly
            venv_python = os.path.join(os.getcwd(), "scraper_env", "bin", "python")
            if not os.path.exists(venv_python):
                venv_python = "python3" # Fallback
            process = subprocess.Popen([venv_python, "rank_internships.py"], cwd=os.getcwd())
            process.wait()
            GLib.idle_add(self.on_sort_finished, True)
        except Exception as e:
            print(f"Error running sorter: {e}")
            GLib.idle_add(self.on_sort_finished, False)

    def on_sort_finished(self, success):
        self.sort_btn.set_sensitive(True)
        if success:
            self.sort_status_label.set_text("Ranking finished!")
            self.jobs_db = self.load_db()
            self.refresh_ui()
        else:
            self.sort_status_label.set_text("Ranking script failed.")

class JobApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.github.jobscraper")

    def do_activate(self):
        # Provide Gdk namespace into PyGObject to load Custom CSS provider
        from gi.repository import Gdk
        globals()['Gdk'] = Gdk

        win = self.props.active_window
        if not win:
            win = JobAppWindow(self)
        win.present()

if __name__ == "__main__":
    import sys
    app = JobApp()
    app.run(sys.argv)
