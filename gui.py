import gi
import csv
import os
import json
import subprocess
import threading
import webbrowser

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Pango

CSV_FILE = "fresh_internships.csv"
DB_FILE = "jobs_db.json"
STATUSES = ["New", "Applied", "Rejected", "Ongoing/Waiting"]

class JobAppWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Job Scraper & Tracker", default_width=900, default_height=700)
        
        # Load DB
        self.jobs_db = self.load_db()
        
        self.vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.vbox.set_margin_top(10)
        self.vbox.set_margin_bottom(10)
        self.vbox.set_margin_start(10)
        self.vbox.set_margin_end(10)
        self.set_child(self.vbox)
        
        # Header Box
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.vbox.append(header_box)
        
        self.scrape_btn = Gtk.Button(label="Scrape Jobs")
        self.scrape_btn.add_css_class("suggested-action")
        self.scrape_btn.connect("clicked", self.on_scrape_clicked)
        header_box.append(self.scrape_btn)
        
        self.refresh_btn = Gtk.Button(label="Refresh List")
        self.refresh_btn.connect("clicked", self.on_refresh_clicked)
        header_box.append(self.refresh_btn)
        
        self.status_label = Gtk.Label(label="Ready.")
        header_box.append(self.status_label)
        
        # Notebook for Tabs
        self.notebook = Gtk.Notebook()
        self.notebook.set_vexpand(True)
        self.notebook.set_hexpand(True)
        self.vbox.append(self.notebook)
        
        # Fresh Jobs Tab
        self.fresh_scroll = Gtk.ScrolledWindow()
        self.fresh_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.fresh_listbox = Gtk.ListBox()
        self.fresh_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.fresh_listbox.add_css_class("boxed-list")
        self.fresh_scroll.set_child(self.fresh_listbox)
        self.notebook.append_page(self.fresh_scroll, Gtk.Label(label="Fresh Jobs"))
        
        # Old Jobs Tab
        self.old_scroll = Gtk.ScrolledWindow()
        self.old_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.old_listbox = Gtk.ListBox()
        self.old_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.old_listbox.add_css_class("boxed-list")
        self.old_scroll.set_child(self.old_listbox)
        self.notebook.append_page(self.old_scroll, Gtk.Label(label="Old Jobs"))
        
        self.sync_csv_to_db()
        self.refresh_ui()

    def load_db(self):
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_db(self):
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.jobs_db, f, indent=4)

    def sync_csv_to_db(self):
        if not os.path.exists(CSV_FILE):
            return
            
        added = 0
        try:
            with open(CSV_FILE, mode='r', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader, None)
                for row in reader:
                    if len(row) >= 2:
                        title, link = row[0], row[1]
                        if link not in self.jobs_db:
                            self.jobs_db[link] = {
                                "title": title,
                                "status": "New"
                            }
                            added += 1
            if added > 0:
                self.save_db()
                self.status_label.set_text(f"Imported {added} new jobs from CSV.")
        except Exception as e:
            self.status_label.set_text(f"Error reading CSV: {e}")

    def refresh_ui(self):
        # Clear lists
        for lb in [self.fresh_listbox, self.old_listbox]:
            while True:
                child = lb.get_first_child()
                if not child:
                    break
                lb.remove(child)
                
        # Populate
        fresh_count = 0
        old_count = 0
        
        for link, data in self.jobs_db.items():
            status = data.get("status", "New")
            self.add_job_row(link, data)
            if status == "New":
                fresh_count += 1
            else:
                old_count += 1
                
        self.status_label.set_text(f"Loaded {fresh_count} Fresh, {old_count} Old jobs.")

    def add_job_row(self, link, data):
        title = data.get("title", "Unknown")
        status = data.get("status", "New")
        
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        
        title_label = Gtk.Label(label=title)
        title_label.set_hexpand(True)
        title_label.set_halign(Gtk.Align.START)
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        title_label.set_tooltip_text(link)
        
        # Combo box for status
        status_combo = Gtk.ComboBoxText()
        for s in STATUSES:
            status_combo.append_text(s)
            
        active_idx = STATUSES.index(status) if status in STATUSES else 0
        status_combo.set_active(active_idx)
        status_combo.set_valign(Gtk.Align.CENTER)
        status_combo.connect("changed", self.on_status_changed, link, row)
        
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.add_css_class("pill")
        apply_btn.set_valign(Gtk.Align.CENTER)
        apply_btn.connect("clicked", self.on_apply_clicked, link)
        
        box.append(title_label)
        box.append(status_combo)
        box.append(apply_btn)
        row.set_child(box)
        
        if status == "New":
            self.fresh_listbox.append(row)
        else:
            self.old_listbox.append(row)

    def on_status_changed(self, combo, link, row):
        new_status = combo.get_active_text()
        if not new_status:
            return
            
        old_status = self.jobs_db[link].get("status", "New")
        if new_status == old_status:
            return
            
        self.jobs_db[link]["status"] = new_status
        self.save_db()
        
        # Move row if category changed between Fresh/Old
        was_fresh = (old_status == "New")
        is_fresh = (new_status == "New")
        
        if was_fresh != is_fresh:
            # We must delay the UI removal slightly or just do it in idle because we are inside the 'changed' signal
            GLib.idle_add(self.move_row, row, is_fresh)
            
    def move_row(self, row, is_fresh):
        parent = row.get_parent()
        if parent:
            parent.remove(row)
        if is_fresh:
            self.fresh_listbox.append(row)
        else:
            self.old_listbox.append(row)
        return False

    def on_apply_clicked(self, button, link):
        try:
            webbrowser.open(link)
        except Exception as e:
            print(f"Failed to open URI: {e}")

    def on_refresh_clicked(self, button):
        self.sync_csv_to_db()
        self.refresh_ui()

    def on_scrape_clicked(self, button):
        self.status_label.set_text("Scraping started... Please check terminal/browser output.")
        self.scrape_btn.set_sensitive(False)
        thread = threading.Thread(target=self.run_scraper)
        thread.daemon = True
        thread.start()
        
    def run_scraper(self):
        try:
            process = subprocess.Popen(["python3", "intern_scraper.py"], cwd=os.getcwd())
            process.wait()
            GLib.idle_add(self.on_scrape_finished, True)
        except Exception as e:
            print(f"Error running scraper: {e}")
            GLib.idle_add(self.on_scrape_finished, False)

    def on_scrape_finished(self, success):
        self.scrape_btn.set_sensitive(True)
        if success:
            self.status_label.set_text("Scraping finished!")
            self.sync_csv_to_db()
            self.refresh_ui()
        else:
            self.status_label.set_text("Scraping failed.")

class JobApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.github.jobscraper")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = JobAppWindow(self)
        win.present()

if __name__ == "__main__":
    import sys
    app = JobApp()
    app.run(sys.argv)
