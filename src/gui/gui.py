import gi
import os
import json
import time
import subprocess
import threading
import webbrowser
from datetime import datetime, timezone, timedelta

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Pango, Gdk

import sys
import os

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# SQLite DB helper
try:
    from src.core.db import (
        get_conn, init_db, load_db, save_db, delete_jobs_by_rank,
        clear_all_jobs, update_job, get_company_count, get_all_companies,
        delete_company, get_company_stats, save_company,
        clear_all_companies, clear_everything,
        extract_companies_from_existing_jobs, seed_starter_companies
    )
    from src.scrapers.ats_parser import parse_ats_info
except ImportError:
    from db import (
        get_conn, init_db, load_db, save_db, delete_jobs_by_rank,
        clear_all_jobs, update_job, get_company_count, get_all_companies,
        delete_company, get_company_stats, save_company,
        clear_all_companies, clear_everything,
        extract_companies_from_existing_jobs, seed_starter_companies
    )
    from ats_parser import parse_ats_info


DB_FILE = os.path.join(_ROOT_DIR, "jobs_db.sqlite")
STATUSES = ["New", "Applied", "Ongoing", "Rejected", "NA"]
RANKS = ["HIGH", "MEDIUM", "LOW", "IGNORE", "ERROR", "UNKNOWN"]
NA_EXPIRY_HOURS = 24

# Color mapping for ranks
RANK_COLORS = {
    "HIGH": "#1a531b",   # green
    "MEDIUM": "#7a6b10", # amber/yellow
    "LOW": "#8c4412",    # orange
    "IGNORE": "#404040", # dark grey
    "ERROR": "#591313",  # red
    "UNKNOWN": "#3b3b3b" # grey
}

# How many rows to render per idle tick (keeps UI responsive)
BATCH_SIZE = 20


def resolve_script_path(script_filename):
    """Find script in src/scrapers, src/ranking, root dir, or cwd."""
    candidates = [
        os.path.join(_ROOT_DIR, "src", "scrapers", script_filename),
        os.path.join(_ROOT_DIR, "src", "ranking", script_filename),
        os.path.join(_ROOT_DIR, script_filename),
        os.path.join(os.getcwd(), "src", "scrapers", script_filename),
        os.path.join(os.getcwd(), "src", "ranking", script_filename),
        os.path.join(os.getcwd(), script_filename),
        os.path.join(os.getcwd(), "scrapers", script_filename),
        os.path.join(os.getcwd(), "ranker", script_filename),
    ]
    for p in candidates:
        if os.path.exists(p):
            return os.path.abspath(p)
    return script_filename


class JobAppWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Job Scraper & Ranker")
        self.set_default_size(1300, 750)
        
        # Ensure we clean up any child processes when closed
        self.connect("close-request", self.on_close_request)

        # Ensure DB exists with correct schema
        init_db()

        # Setup CSS
        self.setup_css()

        # Load DB — returns dict[url -> data] same as before
        self.jobs_db = load_db()
        self.rendered_jobs = set()

        # Track pending batch render
        self._batch_queue = []
        self._batch_source_id = None
        # Track whether priority tab needs refresh
        self._prio_dirty = True
        # Search state
        self.search_query = ""
        self._search_timeout_id = None
        # Pagination
        self.page_size = 50
        self.page_offsets = {
            "New": self.page_size,
            "Applied": self.page_size,
            "Ongoing": self.page_size,
            "Rejected": self.page_size,
            "NA": self.page_size,
            "Priority Sorting": self.page_size,
        }

        # Main layout
        self.vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.vbox.set_margin_top(10)
        self.vbox.set_margin_bottom(10)
        self.vbox.set_margin_start(10)
        self.vbox.set_margin_end(10)
        self.set_child(self.vbox)

        # --- HEADER / GLOBAL ACTIONS ---
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header_box.set_size_request(-1, 38)
        self.vbox.append(header_box)

        self.scrape_btn = Gtk.Button(label="Scrape Jobs")
        self.scrape_btn.add_css_class("suggested-action")
        self.scrape_btn.connect("clicked", self.on_scrape_clicked)
        header_box.append(self.scrape_btn)

        self.scrape_ats_btn = Gtk.Button(label="🏢 Scrape Companies")
        self.scrape_ats_btn.add_css_class("suggested-action")
        self.scrape_ats_btn.connect("clicked", self.on_scrape_ats_clicked)
        header_box.append(self.scrape_ats_btn)

        self.view_companies_btn = Gtk.Button(label="📋 Saved Companies")
        self.view_companies_btn.connect("clicked", self.on_view_companies_clicked)
        header_box.append(self.view_companies_btn)

        self.refresh_btn = Gtk.Button(label="Refresh Everything")
        self.refresh_btn.connect("clicked", self.on_refresh_clicked)
        header_box.append(self.refresh_btn)

        self.fast_filter_btn = Gtk.Button(label="⚡ Smart Tier Filter")
        self.fast_filter_btn.connect("clicked", self.on_fast_filter_clicked)
        header_box.append(self.fast_filter_btn)

        self.limits_btn = Gtk.Button(label="🔑 API Limits")
        self.limits_btn.connect("clicked", self.on_check_limits_clicked)
        header_box.append(self.limits_btn)



        # Delete IGNORE jobs button
        self.del_ignore_btn = Gtk.Button(label="🗑 Delete IGNORE")
        self.del_ignore_btn.add_css_class("destructive-action")
        self.del_ignore_btn.connect("clicked", self.on_delete_ignore_clicked)
        header_box.append(self.del_ignore_btn)

        self.clear_db_btn = Gtk.Button(label="💣 Clear DB")
        self.clear_db_btn.add_css_class("destructive-action")
        self.clear_db_btn.connect("clicked", self.on_clear_db_clicked)
        header_box.append(self.clear_db_btn)

        self.status_label = Gtk.Label(label="Ready.")
        self.status_label.set_hexpand(True)
        self.status_label.set_halign(Gtk.Align.FILL)
        self.status_label.set_valign(Gtk.Align.CENTER)
        self.status_label.set_justify(Gtk.Justification.RIGHT)
        self.status_label.set_wrap(True)
        self.status_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.status_label.set_lines(2)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_label.add_css_class("status-header-label")
        self.status_label.set_tooltip_text("Ready.")
        self.status_label.connect("notify::label", lambda lbl, pspec: lbl.set_tooltip_text(lbl.get_text()))
        header_box.append(self.status_label)

        # Notebook for Tabs
        self.notebook = Gtk.Notebook()
        self.notebook.set_vexpand(True)
        self.notebook.set_hexpand(True)
        self.vbox.append(self.notebook)

        # --- TAB 1: Fresh Jobs (New) ---
        fresh_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)

        # Filter bar
        self.filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.filter_box.set_margin_top(8)
        self.filter_box.set_margin_start(10)
        self.filter_box.set_margin_end(10)
        filter_label = Gtk.Label(label="Filter by Rank:")
        self.filter_box.append(filter_label)

        self.rank_filter_options = ["ALL"] + RANKS
        filter_model = Gtk.StringList.new(self.rank_filter_options)
        self.rank_filter_drop = Gtk.DropDown(model=filter_model)
        self.rank_filter_drop.set_selected(0)  # Default: ALL
        self.rank_filter_drop.connect("notify::selected", self.on_rank_filter_changed)
        self.filter_box.append(self.rank_filter_drop)

        # Result count badge/label
        self.filter_stats_label = Gtk.Label(label="")
        self.filter_stats_label.add_css_class("dim-label")
        self.filter_box.append(self.filter_stats_label)

        # Spacer pushing search to right part
        filter_spacer = Gtk.Box()
        filter_spacer.set_hexpand(True)
        self.filter_box.append(filter_spacer)

        # Search Entry at right part
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("🔍 Search keywords (e.g. intern, python, company)...")
        self.search_entry.set_size_request(350, -1)
        self.search_entry.set_halign(Gtk.Align.END)
        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_entry.connect("stop-search", lambda e: e.set_text(""))
        self.filter_box.append(self.search_entry)

        fresh_vbox.append(self.filter_box)

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
        priority_header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        priority_vbox.append(priority_header)

        # Row 1: 3-Stage Pipeline Workflow + Full Auto Pipeline
        prio_row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        priority_header.append(prio_row1)

        self.extract_btn = Gtk.Button(label="📥 1. Extract JDs")
        self.extract_btn.connect("clicked", self.on_extract_clicked)
        prio_row1.append(self.extract_btn)

        self.prio_deep_filter_btn = Gtk.Button(label="🎯 2. Deep JD Filter")
        self.prio_deep_filter_btn.connect("clicked", self.on_deep_filter_clicked)
        prio_row1.append(self.prio_deep_filter_btn)

        self.ai_rank_btn = Gtk.Button(label="⭐ 3. AI Rank Clean Jobs")
        self.ai_rank_btn.add_css_class("suggested-action")
        self.ai_rank_btn.connect("clicked", self.on_ai_rank_clicked)
        prio_row1.append(self.ai_rank_btn)

        self.full_pipeline_btn = Gtk.Button(label="🚀 Full Pipeline (1➔2➔3)")
        self.full_pipeline_btn.connect("clicked", self.on_full_pipeline_clicked)
        prio_row1.append(self.full_pipeline_btn)

        self.stop_sort_btn = Gtk.Button(label="🛑 Stop")
        self.stop_sort_btn.add_css_class("destructive-action")
        self.stop_sort_btn.connect("clicked", self.on_stop_sort_clicked)
        self.stop_sort_btn.set_sensitive(False)
        prio_row1.append(self.stop_sort_btn)

        # Row 2: Secondary Utilities, Pre-Filter, Limits, and Live Status
        prio_row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        prio_row2.set_size_request(-1, 38)
        priority_header.append(prio_row2)

        self.prio_filter_btn = Gtk.Button(label="⚡ Pre-Filter Titles")
        self.prio_filter_btn.connect("clicked", self.on_fast_filter_clicked)
        prio_row2.append(self.prio_filter_btn)

        self.prio_limits_btn = Gtk.Button(label="🔑 API Limits (7 Keys)")
        self.prio_limits_btn.connect("clicked", self.on_check_limits_clicked)
        prio_row2.append(self.prio_limits_btn)

        self.reset_rank_btn = Gtk.Button(label="🔄 Reset Ranks")
        self.reset_rank_btn.add_css_class("destructive-action")
        self.reset_rank_btn.connect("clicked", self.on_reset_rank_clicked)
        prio_row2.append(self.reset_rank_btn)

        self.sort_status_label = Gtk.Label(label="")
        self.sort_status_label.set_hexpand(True)
        self.sort_status_label.set_halign(Gtk.Align.START)
        self.sort_status_label.set_valign(Gtk.Align.CENTER)
        self.sort_status_label.set_wrap(True)
        self.sort_status_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.sort_status_label.set_lines(2)
        self.sort_status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.sort_status_label.add_css_class("status-header-label")
        self.sort_status_label.connect("notify::label", lambda lbl, pspec: lbl.set_tooltip_text(lbl.get_text()))
        prio_row2.append(self.sort_status_label)

        # Backwards compatibility alias
        self.sort_btn = self.ai_rank_btn

        # Priority Listbox
        self.prio_scroll = Gtk.ScrolledWindow()
        self.prio_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.prio_scroll.set_vexpand(True)
        self.prio_listbox = Gtk.ListBox()
        self.prio_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.prio_listbox.add_css_class("boxed-list")
        self.prio_scroll.set_child(self.prio_listbox)
        priority_vbox.append(self.prio_scroll)

        self.priority_page_num = self.notebook.append_page(priority_vbox, Gtk.Label(label="Priority Sorting"))

        # Connect tab change signal
        self.notebook.connect("switch-page", self.on_tab_switched)

        # Initial Load
        self.backfill_added_at()
        self.expire_stale_jobs()
        self.refresh_ui()

    def on_close_request(self, window):
        """Called when the window is closed to ensure background tasks are killed."""
        if hasattr(self, 'rank_process') and self.rank_process:
            self.rank_process.terminate()
            self.rank_process = None
        
        # Clean up stop.flag if it was left around
        import os
        if os.path.exists("stop.flag"):
            try:
                os.remove("stop.flag")
            except:
                pass
        return False  # Propagate the close event

    def setup_css(self):
        css_provider = Gtk.CssProvider()
        css = b"""
            .status-header-label {
                font-size: 12px;
                opacity: 0.9;
            }
            .rank-label {
                padding: 4px 8px;
                border-radius: 4px;
                color: white;
                font-weight: bold;
                margin-right: 10px;
            }
            .rank-HIGH { background-color: #1a531b; }
            .rank-MEDIUM { background-color: #7a6b10; }
            .rank-LOW { background-color: #8c4412; }
            .rank-IGNORE { background-color: #404040; }
            .rank-ERROR { background-color: #591313; }
            .rank-UNKNOWN { background-color: #3b3b3b; }
            .platform-pill {
                padding: 2px 7px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
                color: white;
            }
            .platform-greenhouse { background-color: #0b7a42; }
            .platform-lever { background-color: #1f6feb; }
            .platform-ashby { background-color: #8250df; }
            .platform-smartrecruiters { background-color: #b07204; }
            .platform-workday { background-color: #cf222e; }
            .platform-breezy { background-color: #0969da; }
            .platform-workable { background-color: #1a7f37; }
            .platform-bamboohr { background-color: #2da44e; }
            .platform-jobvite { background-color: #9a6700; }
            .platform-recruitee { background-color: #545d68; }
            .platform-ats { background-color: #444c56; }
            .stats-banner {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                padding: 8px 12px;
            }
            .company-row {
                padding: 6px 10px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            }
            .company-title {
                font-weight: bold;
                font-size: 14px;
            }
            .metric-card {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 10px 14px;
            }
            .dim-label {
                opacity: 0.7;
                font-size: 12px;
            }
            .quick-remove-btn {
                background-color: rgba(229, 83, 75, 0.15);
                color: #ff7b72;
                border: 1px solid rgba(229, 83, 75, 0.35);
                border-radius: 6px;
                padding: 4px 10px;
                font-weight: bold;
                font-size: 13px;
            }
            .quick-remove-btn:hover {
                background-color: #cf222e;
                color: #ffffff;
                border-color: #cf222e;
            }
        """
        css_provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def get_db_file(self):
        return DB_FILE

    def load_db(self):
        """Load the whole DB as a dict — same interface as before."""
        return load_db()

    def save_db(self):
        """Persist the in-memory dict back to SQLite."""
        save_db(self.jobs_db)

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
        """Auto-expire 'New' jobs older than 24 hours to 'NA' using direct fast SQLite query."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=NA_EXPIRY_HOURS)
        cutoff_iso = cutoff.isoformat()

        conn = get_conn()
        cur = conn.execute(
            "UPDATE jobs SET status='NA', rank='IGNORE', reason='[Expired] Stale job (>24h)' "
            "WHERE status='New' AND added_at < ?",
            (cutoff_iso,)
        )
        expired = cur.rowcount
        conn.commit()
        conn.close()

        if expired > 0:
            for link, data in self.jobs_db.items():
                if data.get("status") == "New":
                    added_at_str = data.get("added_at")
                    if added_at_str and added_at_str < cutoff_iso:
                        data["status"] = "NA"
                        data["rank"] = "IGNORE"
                        data["reason"] = "[Expired] Stale job (>24h)"
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

    # ──────────────────────────────────────────────
    #  Batched rendering helpers
    # ──────────────────────────────────────────────
    def _cancel_pending_batch(self):
        """Cancel any in-flight batch rendering."""
        if self._batch_source_id is not None:
            GLib.source_remove(self._batch_source_id)
            self._batch_source_id = None
        self._batch_queue.clear()

    def _render_batch_tick(self):
        """Render the next BATCH_SIZE items from the queue. Returns True to keep running."""
        if not self._batch_queue:
            self._batch_source_id = None
            return False  # stop the idle source

        chunk = self._batch_queue[:BATCH_SIZE]
        self._batch_queue = self._batch_queue[BATCH_SIZE:]

        for render_fn, args in chunk:
            render_fn(*args)

        if not self._batch_queue:
            self._batch_source_id = None
            return False
        return True  # keep calling

    def _enqueue_rows(self, items):
        """Add items to the batch queue and start processing if not already running."""
        self._batch_queue.extend(items)
        if self._batch_source_id is None and self._batch_queue:
            self._batch_source_id = GLib.idle_add(self._render_batch_tick)

    # ──────────────────────────────────────────────
    #  Main refresh — now with pagination + batched rendering
    # ──────────────────────────────────────────────
    def refresh_ui(self):
        self._cancel_pending_batch()

        self.clear_listbox(self.fresh_listbox)
        self.clear_listbox(self.applied_listbox)
        self.clear_listbox(self.ongoing_listbox)
        self.clear_listbox(self.rejected_listbox)
        self.clear_listbox(self.na_listbox)
        self.clear_listbox(self.prio_listbox)
        self.rendered_jobs.clear()

        # Run expiry check before displaying
        self.expire_stale_jobs()

        counts = {"New": 0, "Applied": 0, "Ongoing": 0, "Rejected": 0, "NA": 0}
        grouped_jobs = {"New": [], "Applied": [], "Ongoing": [], "Rejected": [], "NA": []}

        for link, data in self.jobs_db.items():
            status = data.get("status", "New")
            counts[status] = counts.get(status, 0) + 1
            if status in grouped_jobs:
                grouped_jobs[status].append((link, data))

        self.status_label.set_text(
            f"Fresh: {counts['New']} | Applied: {counts['Applied']} | "
            f"Ongoing: {counts['Ongoing']} | Rejected: {counts['Rejected']} | NA: {counts['NA']}"
        )

        # Build batch render queue
        render_queue = []

        for status, items in grouped_jobs.items():
            if status == "New":
                active_filter = self.get_active_rank_filter()
                if active_filter != "ALL":
                    items = [(l, d) for l, d in items if d.get("rank", "UNKNOWN") == active_filter]
                if self.search_query:
                    items = [(l, d) for l, d in items if self._job_matches_search(d, l, self.search_query)]

                total_fresh = counts["New"]
                if hasattr(self, "filter_stats_label"):
                    if self.search_query or active_filter != "ALL":
                        self.filter_stats_label.set_text(f"({len(items)} matching of {total_fresh})")
                    else:
                        self.filter_stats_label.set_text(f"({total_fresh} jobs)")

            limit = self.page_offsets.get(status, self.page_size)
            for link, data in items[:limit]:
                render_queue.append((self._create_main_job_row, (link, data)))

            if len(items) > limit:
                render_queue.append((self._create_load_more_btn, (status, len(items) - limit)))
            elif not items and status == "New" and self.search_query:
                render_queue.append((self._create_empty_search_row, (self.search_query,)))

        # Only render priority tab if it's currently visible
        current_page = self.notebook.get_current_page()
        if current_page == self.priority_page_num:
            self._build_priority_queue(render_queue)
            self._prio_dirty = False
        else:
            self._prio_dirty = True

        self._enqueue_rows(render_queue)

    def _build_priority_queue(self, render_queue):
        """Append priority-sorted rows to the render queue."""
        def get_rank_weight(rank_str):
            weights = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3, "ERROR": 4, "IGNORE": 5}
            return weights.get(rank_str, 3)

        now = datetime.now(timezone.utc)
        recent_jobs = {}
        for link, data in self.jobs_db.items():
            if data.get("status") != "New":
                continue
            added_at_str = data.get("added_at")
            if added_at_str:
                try:
                    added_at = datetime.fromisoformat(added_at_str)
                    if (now - added_at) <= timedelta(hours=NA_EXPIRY_HOURS):
                        recent_jobs[link] = data
                except ValueError:
                    recent_jobs[link] = data
            else:
                recent_jobs[link] = data

        sorted_jobs = sorted(recent_jobs.items(), key=lambda x: get_rank_weight(x[1].get("rank", "UNKNOWN")))
        if self.search_query:
            sorted_jobs = [(l, d) for l, d in sorted_jobs if self._job_matches_search(d, l, self.search_query)]
        prio_limit = self.page_offsets.get("Priority Sorting", self.page_size)

        for link, data in sorted_jobs[:prio_limit]:
            render_queue.append((self._create_priority_row, (link, data)))

        if len(sorted_jobs) > prio_limit:
            render_queue.append((self._create_prio_load_more_btn, (len(sorted_jobs) - prio_limit,)))

    def _create_load_more_btn(self, status, remaining):
        target = self.get_listbox_for_status(status)
        btn_row = Gtk.ListBoxRow()
        btn = Gtk.Button(label=f"Load More ({remaining} remaining)")
        btn.add_css_class("pill")
        btn.set_margin_top(10)
        btn.set_margin_bottom(10)
        btn.connect("clicked", lambda b, s=status: self.load_more(s))
        btn_row.set_child(btn)
        target.append(btn_row)

    def _create_prio_load_more_btn(self, remaining):
        btn_row = Gtk.ListBoxRow()
        btn = Gtk.Button(label=f"Load More ({remaining} remaining)")
        btn.add_css_class("pill")
        btn.set_margin_top(10)
        btn.set_margin_bottom(10)
        btn.connect("clicked", lambda b: self.load_more("Priority Sorting"))
        btn_row.set_child(btn)
        self.prio_listbox.append(btn_row)

    def load_more(self, status):
        self.page_offsets[status] += self.page_size
        if status == "New":
            self._repopulate_fresh_jobs()
        elif status == "Priority Sorting":
            self._cancel_pending_batch()
            self.clear_listbox(self.prio_listbox)
            render_queue = []
            self._build_priority_queue(render_queue)
            self._enqueue_rows(render_queue)
        else:
            self.refresh_ui()

    def _create_main_job_row(self, link, data):
        """Create and append a single main job row."""
        title = data.get("title", "Unknown")
        status = data.get("status", "New")
        rank = data.get("rank", "UNKNOWN")
        reason = data.get("reason", "")

        # Apply rank filter for Fresh Jobs only
        if status == "New":
            active_filter = self.get_active_rank_filter()
            if active_filter != "ALL" and rank != active_filter:
                return
            if getattr(self, "search_query", None) and not self._job_matches_search(data, link, self.search_query):
                return

        self.rendered_jobs.add(link)

        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
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
        tooltip_text = f"URL: {link}"
        if reason:
            tooltip_text += f"\n\nAI REASON:\n{reason}"

        details = data.get("details")
        if details and isinstance(details, dict):
            tooltip_text += "\n\nEXTRACTED DETAILS:"
            for key, val in details.items():
                if val is not None and val != "" and val != []:
                    tooltip_text += f"\n• {key.replace('_', ' ').title()}: {val}"

        title_label.set_tooltip_text(tooltip_text)

        # Dropdown for status
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

        # Quick Cross (Remove) button: marks job as NA with a single click
        if status != "NA":
            quick_na_btn = Gtk.Button(label="✕")
            quick_na_btn.add_css_class("quick-remove-btn")
            quick_na_btn.set_valign(Gtk.Align.CENTER)
            quick_na_btn.set_tooltip_text("Quick Remove: Mark as NA")
            quick_na_btn.connect("clicked", self.on_quick_na_clicked, link, row, status_drop)
            box.append(quick_na_btn)

        row.set_child(box)

        # Add click event to open details popup
        click_gesture = Gtk.GestureClick()
        click_gesture.set_button(0)
        click_gesture.connect("released", self.on_job_row_clicked, data, link)
        row.add_controller(click_gesture)

        target_listbox = self.get_listbox_for_status(status)
        target_listbox.append(row)

    # Keep old name as alias for compatibility
    def add_main_job_row(self, link, data):
        self._create_main_job_row(link, data)

    def _create_priority_row(self, link, data):
        """Create and append a single priority row."""
        title = data.get("title", "Unknown")
        rank = data.get("rank", "UNKNOWN")
        reason = data.get("reason", "No reason recorded.")
        status = data.get("status", "New")

        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
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

        if status != "NA":
            prio_na_btn = Gtk.Button(label="✕")
            prio_na_btn.add_css_class("quick-remove-btn")
            prio_na_btn.set_valign(Gtk.Align.CENTER)
            prio_na_btn.set_tooltip_text("Quick Remove: Mark as NA")
            prio_na_btn.connect("clicked", self.on_quick_prio_na_clicked, link, row)
            top_box.append(prio_na_btn)

        reason_label = Gtk.Label(label=reason)
        reason_label.set_halign(Gtk.Align.START)
        reason_label.set_wrap(True)
        reason_label.set_max_width_chars(100)
        reason_label.add_css_class("dim-label")

        box.append(top_box)
        box.append(reason_label)

        row.set_child(box)

        click_gesture = Gtk.GestureClick()
        click_gesture.set_button(0)
        click_gesture.connect("released", self.on_job_row_clicked, data, link)
        row.add_controller(click_gesture)

        self.prio_listbox.append(row)

    # Keep old name as alias for compatibility
    def add_priority_row(self, link, data):
        self._create_priority_row(link, data)

    def on_job_row_clicked(self, gesture, n_press, x, y, data, link):
        """Open a popup window displaying structured job details."""
        if time.time() - getattr(self, '_last_btn_click_time', 0) < 0.25:
            return

        details = data.get("details", {})
        if not isinstance(details, dict):
            details = {}

        title = data.get("title", 'Job Details')

        dialog = Gtk.Window(title="Job Extract Details", transient_for=self, modal=True)
        dialog.set_default_size(950, 650)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        main_box.set_margin_top(20)
        main_box.set_margin_bottom(20)
        main_box.set_margin_start(20)
        main_box.set_margin_end(20)

        # Header Box
        header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        title_label = Gtk.Label(label=f"<span size='x-large' weight='bold'>{title}</span>")
        title_label.set_use_markup(True)
        title_label.set_halign(Gtk.Align.START)
        title_label.set_wrap(True)
        title_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title_label.set_max_width_chars(60)
        title_label.set_selectable(True)
        header_box.append(title_label)

        company = details.get("company")
        location = details.get("location")
        jtype = details.get("job_type")
        salary = details.get("salary")

        info_str = []
        if company: info_str.append(f"<b>Company:</b> {company}")
        if location: info_str.append(f"<b>Location:</b> {location}")
        if jtype: info_str.append(f"<b>Type:</b> {jtype}")
        if salary: info_str.append(f"<b>Salary:</b> {salary}")

        if info_str:
            info_label = Gtk.Label(label=" | ".join(info_str))
            info_label.set_use_markup(True)
            info_label.set_halign(Gtk.Align.START)
            info_label.set_wrap(True)
            info_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            info_label.set_max_width_chars(60)
            info_label.set_selectable(True)
            header_box.append(info_label)

        import html
        escaped_link = html.escape(link)

        # Show Rank Badge and Reason
        rank = data.get("rank", "UNKNOWN")
        reason = data.get("reason", "")
        if rank:
            rank_markup = f"<b>Rank:</b> <span weight='bold'>{rank}</span>"
            if reason:
                rank_markup += f"  —  <i>{html.escape(reason)}</i>"
            rank_meta_label = Gtk.Label(label=rank_markup)
            rank_meta_label.set_use_markup(True)
            rank_meta_label.set_halign(Gtk.Align.START)
            rank_meta_label.set_wrap(True)
            header_box.append(rank_meta_label)

        url_label = Gtk.Label(label=f"<a href='{escaped_link}'>Click here to open Original Job Posting</a>")
        url_label.set_use_markup(True)
        url_label.set_halign(Gtk.Align.START)
        header_box.append(url_label)

        main_box.append(header_box)
        main_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content_box.set_margin_top(10)
        content_box.set_margin_bottom(10)
        content_box.set_margin_start(5)
        content_box.set_margin_end(15)

        if details:
            skip_keys = ["job_name", "company", "location", "job_type", "salary"]
            key_order = ["about_job", "responsibilities", "requirements", "skills_required", "skills_preferred", "nice_to_have"]

            all_keys = list(details.keys())
            ordered_keys = [k for k in key_order if k in all_keys] + [k for k in all_keys if k not in key_order]

            for key in ordered_keys:
                if key in skip_keys: continue
                val = details[key]
                if val is not None and val != "" and val != []:
                    sec_title = Gtk.Label(label=f"<span size='large' weight='bold'>{key.replace('_', ' ').title()}</span>")
                    sec_title.set_use_markup(True)
                    sec_title.set_halign(Gtk.Align.START)
                    content_box.append(sec_title)

                    if isinstance(val, list):
                        val_str = "• " + "\n• ".join(str(v) for v in val)
                    else:
                        val_str = str(val)

                    val_label = Gtk.Label(label=val_str)
                    val_label.set_halign(Gtk.Align.START)
                    val_label.set_wrap(True)
                    val_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    val_label.set_max_width_chars(80)
                    val_label.set_selectable(True)
                    content_box.append(val_label)
        else:
            no_data = Gtk.Label(label="No structured AI extractions available for this job yet.")
            no_data.set_halign(Gtk.Align.START)
            content_box.append(no_data)

        scrolled.set_child(content_box)
        main_box.append(scrolled)

        dialog.set_child(main_box)
        dialog.present()

    def on_status_changed(self, dropdown, pspec, link, row):
        selected_item = dropdown.get_selected_item()
        if not selected_item: return
        new_status = selected_item.get_string()

        old_status = self.jobs_db[link].get("status", "New")
        if new_status == old_status: return

        self.jobs_db[link]["status"] = new_status

        # Write only the changed field directly to SQLite — no full dict dump
        conn = get_conn()
        if new_status == "NA":
            if self.jobs_db[link].get("rank") in ("UNKNOWN", "", None):
                self.jobs_db[link]["rank"] = "IGNORE"
                self.jobs_db[link]["reason"] = "[Manual] Dismissed by user"
            update_job(
                link, conn,
                status=new_status,
                rank=self.jobs_db[link].get("rank"),
                reason=self.jobs_db[link].get("reason")
            )
        else:
            update_job(link, conn, status=new_status)
        conn.commit()
        conn.close()

        if old_status != new_status:
            GLib.idle_add(self.move_main_row, row, new_status)
            self._prio_dirty = True

    def move_main_row(self, row, new_status):
        parent = row.get_parent()
        if parent: parent.remove(row)
        target_listbox = self.get_listbox_for_status(new_status)
        target_listbox.append(row)
        self.update_counts()
        return False

    def update_counts(self):
        counts = {"New": 0, "Applied": 0, "Ongoing": 0, "Rejected": 0, "NA": 0}
        for data in self.jobs_db.values():
            st = data.get("status", "New")
            counts[st] = counts.get(st, 0) + 1
        self.status_label.set_text(
            f"Fresh: {counts['New']} | Applied: {counts['Applied']} | "
            f"Ongoing: {counts['Ongoing']} | Rejected: {counts['Rejected']} | NA: {counts['NA']}"
        )
        if hasattr(self, "filter_stats_label"):
            active_filter = self.get_active_rank_filter()
            matching = [
                d for d in self.jobs_db.values()
                if d.get("status", "New") == "New"
                and (active_filter == "ALL" or d.get("rank", "UNKNOWN") == active_filter)
                and (not getattr(self, "search_query", None) or self._job_matches_search(d, "", self.search_query))
            ]
            if getattr(self, "search_query", None) or active_filter != "ALL":
                self.filter_stats_label.set_text(f"({len(matching)} matching of {counts['New']})")
            else:
                self.filter_stats_label.set_text(f"({counts['New']} jobs)")

    def on_apply_clicked(self, button, link):
        self._last_btn_click_time = time.time()
        try: webbrowser.open(link)
        except Exception as e: print(f"Failed to open URI: {e}")

    def on_quick_na_clicked(self, button, link, row, status_drop=None):
        """Immediately marks the job as NA and moves the row to NA tab."""
        self._last_btn_click_time = time.time()
        if link not in self.jobs_db:
            return

        old_status = self.jobs_db[link].get("status", "New")
        if old_status == "NA":
            return

        self.jobs_db[link]["status"] = "NA"
        if self.jobs_db[link].get("rank") in ["UNKNOWN", None, ""]:
            self.jobs_db[link]["rank"] = "IGNORE"
            self.jobs_db[link]["reason"] = "[Manual] Dismissed by user"

        conn = get_conn()
        update_job(
            link,
            conn,
            status="NA",
            rank=self.jobs_db[link].get("rank"),
            reason=self.jobs_db[link].get("reason")
        )
        conn.commit()
        conn.close()

        if status_drop:
            try:
                na_idx = STATUSES.index("NA") if "NA" in STATUSES else 4
                status_drop.set_selected(na_idx)
            except Exception:
                pass

        GLib.idle_add(self.move_main_row, row, "NA")
        self._prio_dirty = True

    def on_quick_prio_na_clicked(self, button, link, row):
        """Immediately marks priority job as NA and removes it from priority view."""
        self._last_btn_click_time = time.time()
        if link not in self.jobs_db:
            return

        self.jobs_db[link]["status"] = "NA"
        if self.jobs_db[link].get("rank") in ["UNKNOWN", None, ""]:
            self.jobs_db[link]["rank"] = "IGNORE"
            self.jobs_db[link]["reason"] = "[Manual] Dismissed by user"

        conn = get_conn()
        update_job(
            link,
            conn,
            status="NA",
            rank=self.jobs_db[link].get("rank"),
            reason=self.jobs_db[link].get("reason")
        )
        conn.commit()
        conn.close()

        parent = row.get_parent()
        if parent:
            parent.remove(row)
        self.update_counts()

    def get_active_rank_filter(self):
        """Return the currently selected rank filter string."""
        idx = self.rank_filter_drop.get_selected()
        return self.rank_filter_options[idx] if idx < len(self.rank_filter_options) else "ALL"

    def _job_matches_search(self, data, link, query):
        """Check if job matches the search query (supports comma OR and space AND)."""
        if not query:
            return True
        title = data.get("title") or ""
        company = data.get("company") or ""
        ats = data.get("ats_platform") or ""
        reason = data.get("reason") or ""
        details = data.get("details") or ""
        if isinstance(details, (dict, list)):
            try:
                details_str = json.dumps(details)
            except Exception:
                details_str = str(details)
        else:
            details_str = str(details)

        target_str = f"{title} {company} {ats} {link} {details_str} {reason}".lower()

        or_clauses = [clause.strip() for clause in query.lower().split(",") if clause.strip()]
        if not or_clauses:
            return True

        for clause in or_clauses:
            and_tokens = [tok.strip() for tok in clause.split() if tok.strip()]
            if and_tokens and all(token in target_str for token in and_tokens):
                return True
        return False

    def _create_empty_search_row(self, query):
        """Row shown when no jobs match the search query."""
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(40)
        box.set_margin_bottom(40)
        box.set_halign(Gtk.Align.CENTER)

        icon = Gtk.Label(label="🔍")
        box.append(icon)

        lbl = Gtk.Label(label=f"No fresh jobs found matching '{query}'")
        lbl.add_css_class("dim-label")
        box.append(lbl)

        row.set_child(box)
        self.fresh_listbox.append(row)

    def on_search_changed(self, entry):
        """Debounce search input and filter jobs in real-time."""
        if self._search_timeout_id is not None:
            GLib.source_remove(self._search_timeout_id)
            self._search_timeout_id = None
        self._search_timeout_id = GLib.timeout_add(150, self._apply_search)

    def _apply_search(self):
        """Callback to execute search filtering."""
        self._search_timeout_id = None
        self.search_query = self.search_entry.get_text().strip()
        self.page_offsets["New"] = self.page_size
        self.page_offsets["Priority Sorting"] = self.page_size
        self._prio_dirty = True
        self._repopulate_fresh_jobs()
        if self.notebook.get_current_page() == self.priority_page_num:
            self._cancel_pending_batch()
            self.clear_listbox(self.prio_listbox)
            render_queue = []
            self._build_priority_queue(render_queue)
            self._enqueue_rows(render_queue)
            self._prio_dirty = False
        return False

    def _repopulate_fresh_jobs(self):
        """Re-populate only the Fresh Jobs listbox with current rank filter and search query."""
        self._cancel_pending_batch()
        self.clear_listbox(self.fresh_listbox)

        active_filter = self.get_active_rank_filter()
        items = [(l, d) for l, d in self.jobs_db.items() if d.get("status", "New") == "New"]
        if active_filter != "ALL":
            items = [(l, d) for l, d in items if d.get("rank", "UNKNOWN") == active_filter]
        if self.search_query:
            items = [(l, d) for l, d in items if self._job_matches_search(d, l, self.search_query)]

        total_fresh = sum(1 for d in self.jobs_db.values() if d.get("status", "New") == "New")
        if hasattr(self, "filter_stats_label"):
            if self.search_query or active_filter != "ALL":
                self.filter_stats_label.set_text(f"({len(items)} matching of {total_fresh})")
            else:
                self.filter_stats_label.set_text(f"({total_fresh} jobs)")

        limit = self.page_offsets.get("New", self.page_size)
        render_queue = []
        for link, data in items[:limit]:
            render_queue.append((self._create_main_job_row, (link, data)))
        if len(items) > limit:
            render_queue.append((self._create_load_more_btn, ("New", len(items) - limit)))
        elif not items and self.search_query:
            render_queue.append((self._create_empty_search_row, (self.search_query,)))
        self._enqueue_rows(render_queue)

    def on_rank_filter_changed(self, dropdown, pspec):
        """Re-populate only the Fresh Jobs listbox when filter changes."""
        self.page_offsets["New"] = self.page_size
        self._repopulate_fresh_jobs()



    def on_delete_ignore_clicked(self, button):
        """Remove all IGNORE-ranked jobs from the database."""
        ignore_links = [link for link, data in self.jobs_db.items() if data.get("rank") == "IGNORE"]
        for link in ignore_links:
            del self.jobs_db[link]
        if ignore_links:
            deleted = delete_jobs_by_rank("IGNORE")
            self.refresh_ui()
            self.status_label.set_text(f"🗑 Deleted {deleted} IGNORE jobs.")
        else:
            self.status_label.set_text("No IGNORE jobs to delete.")

    def on_clear_db_clicked(self, button):
        """Show clear database options window."""
        win = ClearDbWindow(self)
        win.present()

    def on_refresh_clicked(self, button):
        # Reload DB from SQLite
        self.jobs_db = load_db()
        self.refresh_ui()

    def on_tab_switched(self, notebook, page, page_num):
        if page_num == self.priority_page_num and self._prio_dirty:
            # Only rebuild priority list, not everything
            self._cancel_pending_batch()
            self.clear_listbox(self.prio_listbox)
            render_queue = []
            self._build_priority_queue(render_queue)
            self._enqueue_rows(render_queue)
            self._prio_dirty = False

    def on_scrape_clicked(self, button):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            message_type=Gtk.MessageType.QUESTION,
            text="Select Scraping Speed",
            secondary_text="Choose the scraper speed. 'Fast' may encounter more CAPTCHAs requiring manual solving."
        )
        dialog.add_button("⚡ Fast", 1)
        dialog.add_button("🚶 Medium (Default)", 2)
        dialog.add_button("🌙 Overnight", 3)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.connect("response", self.on_scrape_dialog_response)
        dialog.present()

    def on_scrape_dialog_response(self, dialog, response_id):
        dialog.close()
        if response_id == Gtk.ResponseType.CANCEL or response_id < 0:
            return

        script_name = "intern_scraper.py"
        if response_id == 1:
            script_name = "intern_scraper_fast.py"
        elif response_id == 3:
            script_name = "intern_scraper_overnight.py"

        self.status_label.set_text(f"Scraping started ({script_name})... (Check terminal output for progress)")
        self.scrape_btn.set_sensitive(False)
        thread = threading.Thread(target=self.run_scraper, args=(script_name,))
        thread.daemon = True
        thread.start()

    def run_scraper(self, script_name="intern_scraper.py"):
        try:
            venv_python = os.path.join(os.getcwd(), "scraper_env", "bin", "python")
            if not os.path.exists(venv_python):
                venv_python = "python3"

            process = subprocess.Popen([venv_python, resolve_script_path(script_name)], cwd=os.getcwd())
            process.wait()
            GLib.idle_add(self.on_scrape_finished, process.returncode == 0)
        except Exception as e:
            print(f"Error running scraper: {e}")
            GLib.idle_add(self.on_scrape_finished, False)

    def on_scrape_finished(self, success):
        self.scrape_btn.set_sensitive(True)
        if success:
            self.status_label.set_text("✅ Scraping finished! Click 'Refresh Everything' to view new jobs.")
        else:
            self.status_label.set_text("⚠️ Scraping stopped or failed.")

    def on_scrape_ats_clicked(self, button):
        self.status_label.set_text("Scraping saved company ATS boards... (Check terminal output for progress)")
        self.scrape_ats_btn.set_sensitive(False)
        thread = threading.Thread(target=self.run_ats_scraper_thread)
        thread.daemon = True
        thread.start()

    def run_ats_scraper_thread(self):
        try:
            venv_python = os.path.join(os.getcwd(), "scraper_env", "bin", "python")
            if not os.path.exists(venv_python):
                venv_python = "python3"

            process = subprocess.Popen([venv_python, resolve_script_path("ats_scraper.py")], cwd=os.getcwd())
            process.wait()
            GLib.idle_add(self.on_ats_scrape_finished, process.returncode == 0)
        except Exception as e:
            print(f"Error running ATS scraper: {e}")
            GLib.idle_add(self.on_ats_scrape_finished, False)

    def on_ats_scrape_finished(self, success):
        self.scrape_ats_btn.set_sensitive(True)
        if success:
            comp_cnt = get_company_count()
            self.status_label.set_text(f"✅ ATS Scraping finished ({comp_cnt} companies)! Click 'Refresh Everything' to view new jobs.")
        else:
            self.status_label.set_text("⚠️ ATS Scraping stopped or failed.")

    def on_view_companies_clicked(self, button):
        if not hasattr(self, "companies_win") or self.companies_win is None:
            self.companies_win = CompaniesWindow(self)
        self.companies_win.present()

    # --- DETERMINISTIC PRE-FILTER LOGIC (ZERO TOKENS) ---
    def on_fast_filter_clicked(self, button):
        self.status_label.set_text("⚡ Running Smart Tier Deterministic Filter (0 AI tokens)...")
        self.fast_filter_btn.set_sensitive(False)
        if hasattr(self, 'prio_filter_btn'):
            self.prio_filter_btn.set_sensitive(False)

        def worker():
            try:
                try:
                    from src.ranking.job_filter import apply_deterministic_filter_to_db
                except ImportError:
                    from job_filter import apply_deterministic_filter_to_db
                stats = apply_deterministic_filter_to_db(only_unknown=False, move_to_na=True)
                def update_gui():
                    self.fast_filter_btn.set_sensitive(True)
                    if hasattr(self, 'prio_filter_btn'):
                        self.prio_filter_btn.set_sensitive(True)
                    tier1_count = stats.get('tier1_early_career', 0)
                    tier2_count = stats.get('tier2_general_swe', 0)
                    total_ignored = stats.get('total_ignored', 0)
                    msg = (f"⚡ Smart Tier: {tier1_count} Early Career (HIGH) | {tier2_count} General SWE | "
                           f"Ignored {total_ignored} -> NA | Click 'Refresh Everything' to view updated lists")
                    self.status_label.set_text(msg)
                    if hasattr(self, 'sort_status_label'):
                        self.sort_status_label.set_text(msg)
                GLib.idle_add(update_gui)
            except Exception as e:
                print(f"Error in deterministic filter worker: {e}")
                def on_err():
                    self.fast_filter_btn.set_sensitive(True)
                    if hasattr(self, 'prio_filter_btn'):
                        self.prio_filter_btn.set_sensitive(True)
                    self.status_label.set_text(f"⚠️ Filter error: {e}")
                GLib.idle_add(on_err)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def on_deep_filter_clicked(self, button):
        self.status_label.set_text("🎯 Running Deep JD Filter (Experience, Senior, Non-Tech)...")
        if hasattr(self, 'prio_deep_filter_btn'):
            self.prio_deep_filter_btn.set_sensitive(False)

        def worker():
            try:
                try:
                    from src.ranking.job_filter import apply_deep_jd_filter_to_db
                except ImportError:
                    from job_filter import apply_deep_jd_filter_to_db
                stats = apply_deep_jd_filter_to_db(move_to_na=True)
                def update_gui():
                    if hasattr(self, 'prio_deep_filter_btn'):
                        self.prio_deep_filter_btn.set_sensitive(True)
                    msg = (f"🎯 Deep JD Filter: {stats['total_ignored']} Ignored ({stats['exp_ignored']} exp>=1yr, "
                           f"{stats['senior_ignored']} senior, {stats['non_tech_ignored']} non-tech, {stats['us_ignored']} US, {stats['canada_ignored']} Canada) | "
                           f"Clean Candidates for AI: {stats['passed']} | Click 'Refresh Everything' to view updated lists")
                    self.status_label.set_text(msg)
                    if hasattr(self, 'sort_status_label'):
                        self.sort_status_label.set_text(msg)
                GLib.idle_add(update_gui)
            except Exception as e:
                print(f"Error in deep filter worker: {e}")
                def on_err():
                    if hasattr(self, 'prio_deep_filter_btn'):
                        self.prio_deep_filter_btn.set_sensitive(True)
                    self.status_label.set_text(f"⚠️ Deep filter error: {e}")
                GLib.idle_add(on_err)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def on_check_limits_clicked(self, button):
        win = GroqLimitsWindowGTK(self)
        win.present()

    # --- LLM RANKING LOGIC ---
    def on_reset_rank_clicked(self, button):
        count = 0
        for link, data in self.jobs_db.items():
            if data.get("status") != "Applied":
                data["rank"] = "UNKNOWN"
                data["reason"] = "Reset for re-evaluation"
                count += 1

        if count > 0:
            self.save_db()
            self.refresh_ui()
            self.sort_status_label.set_text(f"Reset {count} jobs to UNKNOWN rank.")
        else:
            self.sort_status_label.set_text("No jobs to reset.")

    def on_stop_sort_clicked(self, button=None):
        with open("stop.flag", "w") as f:
            f.write("stop")
        if hasattr(self, 'rank_process') and self.rank_process:
            try:
                self.rank_process.terminate()
            except Exception:
                pass
        self.sort_status_label.set_text("🛑 Stopping... Please wait.")
        self.stop_sort_btn.set_sensitive(False)

    def set_pipeline_buttons_sensitive(self, sensitive: bool):
        for btn in [getattr(self, 'extract_btn', None),
                    getattr(self, 'ai_rank_btn', None),
                    getattr(self, 'full_pipeline_btn', None),
                    getattr(self, 'prio_deep_filter_btn', None),
                    getattr(self, 'sort_btn', None)]:
            if btn:
                try:
                    btn.set_sensitive(sensitive)
                except Exception:
                    pass

    def run_pipeline(self, mode: str, label_name: str):
        if not os.path.exists(DB_FILE):
            self.sort_status_label.set_text(f"No {DB_FILE}. Scrape first!")
            return

        key_count = sum(1 for i in range(1, 26) if os.environ.get(f"GROQ_API_KEY_{i}"))
        if key_count == 0 and os.environ.get("GROQ_API_KEY"):
            key_count = 1
        key_info = f"({key_count} Groq API keys in circular rotation)" if key_count > 0 else ""

        self.sort_status_label.set_text(f"Starting {label_name} {key_info}... Check terminal!")
        self.set_pipeline_buttons_sensitive(False)
        self.stop_sort_btn.set_sensitive(True)
        thread = threading.Thread(target=self._execute_pipeline_process, args=(mode, label_name), daemon=True)
        thread.start()

    def _execute_pipeline_process(self, mode: str, label_name: str):
        try:
            venv_python = os.path.join(os.getcwd(), "scraper_env", "bin", "python")
            if not os.path.exists(venv_python):
                venv_python = "python3"

            cmd = [venv_python, resolve_script_path("rank_internships.py"), f"--{mode}"]
            self.rank_process = subprocess.Popen(cmd, cwd=os.getcwd())
            self.rank_process.wait()
            self.rank_process = None
            GLib.idle_add(self.on_pipeline_finished, True, label_name)
        except Exception as e:
            print(f"Error running pipeline ({label_name}): {e}")
            GLib.idle_add(self.on_pipeline_finished, False, label_name)

    def on_pipeline_finished(self, success: bool, label_name: str):
        self.set_pipeline_buttons_sensitive(True)
        self.stop_sort_btn.set_sensitive(False)
        if success:
            msg = f"✅ {label_name} finished! Click 'Refresh Everything' to view updated jobs."
        else:
            msg = f"⚠️ {label_name} stopped or encountered an error."
        self.sort_status_label.set_text(msg)
        self.status_label.set_text(msg)

    def on_extract_clicked(self, button=None):
        self.run_pipeline("extract", "Stage 1: JD Extraction")

    def on_ai_rank_clicked(self, button=None):
        self.run_pipeline("rank", "Stage 3: AI Rank Clean Jobs")

    def on_full_pipeline_clicked(self, button=None):
        self.run_pipeline("full", "Full Pipeline (1➔2➔3)")

    def on_sort_clicked(self, button=None):
        # Backwards compatibility
        self.on_full_pipeline_clicked()


class ClearDbWindow(Gtk.Window):
    def __init__(self, parent_win):
        super().__init__(title="Clear Database Options")
        self.set_default_size(520, 380)
        self.set_transient_for(parent_win)
        self.set_modal(True)
        self.parent_win = parent_win

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        self.set_child(vbox)

        # Title
        title_lbl = Gtk.Label()
        title_lbl.set_markup("<b><span size='large'>Clear Database Options</span></b>")
        title_lbl.set_halign(Gtk.Align.START)
        vbox.append(title_lbl)

        sub_lbl = Gtk.Label(label="Choose what you want to remove from your database:")
        sub_lbl.set_halign(Gtk.Align.START)
        sub_lbl.add_css_class("dim-label")
        vbox.append(sub_lbl)

        # Option 1: Delete all saved companies only
        opt1_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        btn_companies = Gtk.Button(label="🏢 Delete All Saved Companies Only")
        btn_companies.connect("clicked", self.on_clear_companies)
        opt1_box.append(btn_companies)
        lbl1 = Gtk.Label(label="Removes all saved companies from ATS pool. Scraped jobs and rank data are kept.")
        lbl1.add_css_class("dim-label")
        lbl1.set_halign(Gtk.Align.START)
        lbl1.set_wrap(True)
        opt1_box.append(lbl1)
        vbox.append(opt1_box)

        # Option 2: Delete all scraped jobs only
        opt2_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        btn_jobs = Gtk.Button(label="💼 Delete All Scraped Jobs Only")
        btn_jobs.connect("clicked", self.on_clear_jobs)
        opt2_box.append(btn_jobs)
        lbl2 = Gtk.Label(label="Removes all scraped job postings and rankings. Saved companies pool is kept.")
        lbl2.add_css_class("dim-label")
        lbl2.set_halign(Gtk.Align.START)
        lbl2.set_wrap(True)
        opt2_box.append(lbl2)
        vbox.append(opt2_box)

        # Option 3: Delete everything
        opt3_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        btn_all = Gtk.Button(label="💣 Delete Everything (Jobs + Companies + ATS Data)")
        btn_all.add_css_class("destructive-action")
        btn_all.connect("clicked", self.on_clear_everything)
        opt3_box.append(btn_all)
        lbl3 = Gtk.Label(label="Complete wipe: deletes all jobs, all saved companies, and archived URL history.")
        lbl3.add_css_class("dim-label")
        lbl3.set_halign(Gtk.Align.START)
        lbl3.set_wrap(True)
        opt3_box.append(lbl3)
        vbox.append(opt3_box)

        # Cancel button
        cancel_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        cancel_box.set_halign(Gtk.Align.END)
        cancel_box.set_margin_top(10)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: self.close())
        cancel_box.append(cancel_btn)
        vbox.append(cancel_box)

    def on_clear_companies(self, button):
        deleted = clear_all_companies()
        if hasattr(self.parent_win, "companies_win") and self.parent_win.companies_win:
            self.parent_win.companies_win.load_data()
        self.parent_win.status_label.set_text(
            f"🏢 Cleared all {deleted} saved companies from ATS pool. Jobs kept."
        )
        self.close()

    def on_clear_jobs(self, button):
        deleted = clear_all_jobs()
        self.parent_win.jobs_db.clear()
        self.parent_win.refresh_ui()
        self.parent_win.status_label.set_text(
            f"💼 Cleared {deleted} jobs from database. Saved companies kept."
        )
        self.close()

    def on_clear_everything(self, button):
        res = clear_everything()
        self.parent_win.jobs_db.clear()
        self.parent_win.refresh_ui()
        if hasattr(self.parent_win, "companies_win") and self.parent_win.companies_win:
            self.parent_win.companies_win.load_data()
        self.parent_win.status_label.set_text(
            f"💣 Wiped database! ({res['jobs']} jobs, {res['companies']} companies, {res['deleted_urls']} archived URLs)"
        )
        self.close()


class AddCompanyWindow(Gtk.Window):
    def __init__(self, parent_win):
        super().__init__(title="➕ Add ATS Career Board")
        self.set_default_size(520, 260)
        self.set_transient_for(parent_win)
        self.set_modal(True)
        self.parent_win = parent_win

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)
        self.set_child(vbox)

        header = Gtk.Label()
        header.set_markup("<b><span size='large'>Add Company to ATS Scraping Pool</span></b>")
        header.set_halign(Gtk.Align.START)
        vbox.append(header)

        sub_label = Gtk.Label(
            label="Paste any job URL or career page from Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Breezy, BambooHR, Workable, Jobvite, etc."
        )
        sub_label.set_wrap(True)
        sub_label.set_max_width_chars(55)
        sub_label.set_halign(Gtk.Align.START)
        sub_label.add_css_class("dim-label")
        vbox.append(sub_label)

        # URL Entry
        url_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        url_lbl = Gtk.Label(label="Job or Career Board URL:")
        url_lbl.set_halign(Gtk.Align.START)
        url_box.append(url_lbl)
        self.url_entry = Gtk.Entry()
        self.url_entry.set_placeholder_text("https://boards.greenhouse.io/stripe or job posting URL")
        self.url_entry.connect("activate", lambda e: self.on_save())
        url_box.append(self.url_entry)
        vbox.append(url_box)

        # Optional Name Entry
        name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        name_lbl = Gtk.Label(label="Company Name (Optional):")
        name_lbl.set_halign(Gtk.Align.START)
        name_box.append(name_lbl)
        self.name_entry = Gtk.Entry()
        self.name_entry.set_placeholder_text("Leave blank to detect automatically")
        self.name_entry.connect("activate", lambda e: self.on_save())
        name_box.append(self.name_entry)
        vbox.append(name_box)

        # Status Label
        self.status_label = Gtk.Label(label="")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_wrap(True)
        vbox.append(self.status_label)

        # Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_halign(Gtk.Align.END)
        btn_box.set_margin_top(8)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: self.close())
        btn_box.append(cancel_btn)

        save_btn = Gtk.Button(label="Save & Track")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", lambda b: self.on_save())
        btn_box.append(save_btn)

        vbox.append(btn_box)

    def on_save(self):
        url = self.url_entry.get_text().strip()
        custom_name = self.name_entry.get_text().strip()
        if not url:
            self.status_label.set_markup("<span color='#cf222e'>Please enter a URL.</span>")
            return

        res = parse_ats_info(url, resolve_google=False)
        if not res:
            self.status_label.set_markup(
                "<span color='#cf222e'>❌ Could not detect ATS platform.<br/>Supported: Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Breezy, BambooHR, Workable, Jobvite, Recruitee</span>"
            )
            return

        ats = res["ats_platform"]
        ident = res["identifier"]
        career_url = res["career_url"]
        comp_name = custom_name or res.get("company_name") or res.get("name") or ident.title()

        saved = save_company(comp_name, ats, ident, career_url)
        if saved:
            self.parent_win.load_data()
            self.close()
        else:
            self.status_label.set_markup(
                f"<span color='#d29922'>⚠️ Company <b>@{ident}</b> ({ats.upper()}) already exists in your pool.</span>"
            )


class CompaniesWindow(Gtk.Window):
    def __init__(self, parent):
        super().__init__(title="Saved Companies (ATS Scraping Pool)")
        self.set_default_size(950, 680)
        self.set_transient_for(parent)
        self.parent_window = parent

        self.connect("close-request", self.on_close_request)

        self.all_companies = []
        self.filtered_companies = []

        # Root box
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        self.set_child(vbox)

        # Header controls
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        vbox.append(header_box)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("🔍 Search company name, ATS platform, or handle...")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self.on_search_changed)
        header_box.append(self.search_entry)

        platform_lbl = Gtk.Label(label="Platform:")
        header_box.append(platform_lbl)

        self.platform_options = [
            "ALL", "greenhouse", "lever", "ashby", "smartrecruiters",
            "workday", "breezy", "workable", "bamboohr", "jobvite", "recruitee"
        ]
        self.platform_model = Gtk.StringList.new([p.upper() for p in self.platform_options])
        self.platform_drop = Gtk.DropDown(model=self.platform_model)
        self.platform_drop.set_selected(0)
        self.platform_drop.connect("notify::selected", self.on_platform_changed)
        header_box.append(self.platform_drop)

        self.add_btn = Gtk.Button(label="➕ Add")
        self.add_btn.add_css_class("suggested-action")
        self.add_btn.connect("clicked", self.on_add_company_clicked)
        header_box.append(self.add_btn)

        self.extract_btn = Gtk.Button(label="📥 Extract from Jobs")
        self.extract_btn.set_tooltip_text("Scan existing jobs in DB to discover and extract company ATS career websites")
        self.extract_btn.connect("clicked", self.on_extract_clicked)
        header_box.append(self.extract_btn)

        self.seed_btn = Gtk.Button(label="🌱 Seed Top 45+")
        self.seed_btn.add_css_class("suggested-action")
        self.seed_btn.set_tooltip_text("Instantly load 45+ top tech company ATS career boards into your pool")
        self.seed_btn.connect("clicked", self.on_seed_clicked)
        header_box.append(self.seed_btn)

        self.refresh_btn = Gtk.Button(label="🔄 Refresh")
        self.refresh_btn.connect("clicked", self.on_refresh_clicked)
        header_box.append(self.refresh_btn)

        self.clear_all_companies_btn = Gtk.Button(label="🗑 Clear All")
        self.clear_all_companies_btn.add_css_class("destructive-action")
        self.clear_all_companies_btn.set_tooltip_text("Remove all saved companies from ATS scraping pool")
        self.clear_all_companies_btn.connect("clicked", self.on_clear_all_companies_clicked)
        header_box.append(self.clear_all_companies_btn)


        # Stats Banner
        self.stats_banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.stats_banner.add_css_class("stats-banner")
        self.stats_label = Gtk.Label(label="Loading stats...")
        self.stats_label.set_hexpand(True)
        self.stats_label.set_halign(Gtk.Align.START)
        self.stats_banner.append(self.stats_label)
        vbox.append(self.stats_banner)

        # Scrolled List
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_vexpand(True)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.scroll.set_child(self.listbox)
        vbox.append(self.scroll)

        # Initial Load
        self.load_data()

    def on_close_request(self, window):
        if hasattr(self.parent_window, "companies_win"):
            self.parent_window.companies_win = None
        return False

    def load_data(self):
        self.all_companies = get_all_companies()
        stats = get_company_stats()

        total_c = stats.get("total_companies", 0)
        total_j = stats.get("total_jobs", 0)
        plat_strs = [f"{p.upper()}: {cnt}" for p, cnt in stats.get("platforms", {}).items()]
        breakdown = "  •  ".join(plat_strs)

        self.stats_label.set_markup(
            f"<b>{total_c}</b> Companies in Pool  •  <b>{total_j}</b> Jobs Scraped   |   {breakdown}"
        )
        self.apply_filter()

    def on_search_changed(self, entry):
        self.apply_filter()

    def on_platform_changed(self, dropdown, pspec):
        self.apply_filter()

    def on_refresh_clicked(self, button):
        self.load_data()

    def on_clear_all_companies_clicked(self, button):
        count = len(self.all_companies)
        if count == 0:
            return
        dialog = Gtk.MessageDialog(
            transient_for=self,
            message_type=Gtk.MessageType.WARNING,
            text="Delete ALL Saved Companies?",
            secondary_text=f"This will permanently delete all {count} companies from your ATS scraping pool.\nExisting scraped jobs will remain in your database."
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("🗑 Yes, Delete All", Gtk.ResponseType.OK)

        def on_response(diag, resp):
            diag.close()
            if resp == Gtk.ResponseType.OK:
                deleted = clear_all_companies()
                self.load_data()
                if hasattr(self.parent_window, "status_label"):
                    self.parent_window.status_label.set_text(
                        f"🗑 Cleared all {deleted} companies from scraping pool. Jobs kept."
                    )

        dialog.connect("response", on_response)
        dialog.present()

    def on_add_company_clicked(self, button):
        add_win = AddCompanyWindow(self)
        add_win.present()

    def on_extract_clicked(self, button):
        conn = get_conn()
        job_count = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
        conn.close()

        if job_count == 0:
            self.stats_label.set_markup("⚠️ <span foreground='#d29922'>No jobs in DB yet. Run 'Scrape Jobs' first or use '🌱 Seed Top 45+'!</span>")
            return

        self.extract_btn.set_sensitive(False)
        self.stats_label.set_markup(f"🔄 <i>Scanning {job_count} jobs to extract company ATS career websites... Please wait...</i>")

        def worker():
            try:
                total_c = extract_companies_from_existing_jobs(max_workers=20)
                GLib.idle_add(self._on_extract_done, total_c)
            except Exception as e:
                GLib.idle_add(self._on_extract_error, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_extract_done(self, total_c):
        self.extract_btn.set_sensitive(True)
        self.load_data()
        self.stats_label.set_markup(f"✅ <b>Extraction Complete!</b> Total companies in pool: <b>{total_c}</b>")
        if hasattr(self.parent_window, "status_label"):
            self.parent_window.status_label.set_text(f"✅ Extracted ATS companies from jobs. Pool: {total_c} companies.")
        return False

    def _on_extract_error(self, err_msg):
        self.extract_btn.set_sensitive(True)
        self.stats_label.set_markup(f"❌ <span foreground='#cf222e'>Extraction error: {err_msg}</span>")
        return False

    def on_seed_clicked(self, button):
        added = seed_starter_companies()
        self.load_data()
        total = get_company_count()
        self.stats_label.set_markup(f"🌱 <b>Added {added} top tech companies!</b> Total pool: <b>{total}</b> companies.")
        if hasattr(self.parent_window, "status_label"):
            self.parent_window.status_label.set_text(f"🌱 Seeded {added} top tech ATS companies. Pool: {total} companies.")


    def apply_filter(self):
        query = self.search_entry.get_text().strip().lower()
        selected_idx = self.platform_drop.get_selected()
        selected_platform = self.platform_options[selected_idx].lower()

        filtered = []
        for c in self.all_companies:
            name = (c.get("name") or "").lower()
            ats = (c.get("ats_platform") or "").lower()
            ident = (c.get("identifier") or "").lower()
            url = (c.get("career_url") or "").lower()

            if selected_platform != "all" and ats != selected_platform:
                continue

            if query and query not in name and query not in ats and query not in ident and query not in url:
                continue

            filtered.append(c)

        self.filtered_companies = filtered
        self.render_companies()

    def render_companies(self):
        # Clear existing rows
        while True:
            child = self.listbox.get_first_child()
            if child is None:
                break
            self.listbox.remove(child)

        if not self.filtered_companies:
            empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            empty_box.set_margin_top(40)
            empty_box.set_margin_bottom(40)
            empty_box.set_valign(Gtk.Align.CENTER)
            empty_box.set_halign(Gtk.Align.CENTER)
            empty_lbl = Gtk.Label()
            empty_lbl.set_markup("<span size='large' color='#8b949e'>No saved companies match your search or filter.</span>")
            empty_box.append(empty_lbl)
            self.listbox.append(empty_box)
            return

        for c in self.filtered_companies:
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row_box.add_css_class("company-row")

            # Left/Info column
            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            info_box.set_hexpand(True)

            top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name_text = c.get("name") or c.get("identifier", "").title()
            name_lbl = Gtk.Label(label=name_text)
            name_lbl.add_css_class("company-title")
            name_lbl.set_halign(Gtk.Align.START)
            top_box.append(name_lbl)

            ats = (c.get("ats_platform") or "ats").lower()
            badge = Gtk.Label(label=ats.upper())
            badge.add_css_class("platform-pill")
            badge.add_css_class(f"platform-{ats}")
            top_box.append(badge)

            ident_lbl = Gtk.Label(label=f"@{c.get('identifier', '')}")
            ident_lbl.add_css_class("dim-label")
            top_box.append(ident_lbl)

            info_box.append(top_box)

            bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            career_url = c.get("career_url") or ""
            url_lbl = Gtk.Label(label=career_url)
            url_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            url_lbl.set_max_width_chars(65)
            url_lbl.set_halign(Gtk.Align.START)
            url_lbl.add_css_class("dim-label")
            bottom_box.append(url_lbl)

            last_scraped = c.get("last_scraped_at")
            if last_scraped:
                try:
                    dt = datetime.fromisoformat(last_scraped)
                    scraped_str = f"Scraped: {dt.strftime('%b %d, %H:%M')}"
                except Exception:
                    scraped_str = f"Scraped: {last_scraped[:16]}"
            else:
                scraped_str = "Never scraped"

            job_cnt = c.get("job_count", 0) or 0
            meta_lbl = Gtk.Label(label=f"•  {scraped_str}  •  {job_cnt} active jobs")
            meta_lbl.add_css_class("dim-label")
            bottom_box.append(meta_lbl)

            info_box.append(bottom_box)
            row_box.append(info_box)

            # Action buttons on right
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            btn_box.set_halign(Gtk.Align.END)
            btn_box.set_valign(Gtk.Align.CENTER)

            open_btn = Gtk.Button(label="🌐 Open Board")
            open_btn.connect("clicked", lambda b, u=career_url: webbrowser.open(u))
            btn_box.append(open_btn)

            del_btn = Gtk.Button(label="🗑 Remove")
            del_btn.add_css_class("destructive-action")
            del_btn.set_tooltip_text("Delete company from pool")
            del_btn.connect("clicked", lambda b, comp=c: self.confirm_delete_company(comp))
            btn_box.append(del_btn)

            row_box.append(btn_box)
            self.listbox.append(row_box)

    def confirm_delete_company(self, comp):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            message_type=Gtk.MessageType.WARNING,
            text=f"Delete '{comp.get('name')}'?",
            secondary_text=f"Remove this company ({comp.get('ats_platform', '').upper()}) from your ATS scraping pool? Existing scraped jobs will remain in your database."
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("🗑 Delete", Gtk.ResponseType.OK)

        def on_response(diag, resp):
            diag.close()
            if resp == Gtk.ResponseType.OK:
                delete_company(comp["id"])
                self.load_data()
                if hasattr(self.parent_window, "status_label"):
                    cnt = get_company_count()
                    self.parent_window.status_label.set_text(
                        f"Removed '{comp.get('name')}'. ({cnt} companies remaining)"
                    )

        dialog.connect("response", on_response)
        dialog.present()


class GroqLimitsWindowGTK(Gtk.Window):
    """Modern modal dialog showing live Groq API limits & token status across all keys."""
    def __init__(self, parent_window):
        super().__init__(title="Groq API Limits & Quota", transient_for=parent_window, modal=True)
        self.parent_window = parent_window
        self.set_default_size(780, 580)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        main_box.set_margin_top(18)
        main_box.set_margin_bottom(18)
        main_box.set_margin_start(20)
        main_box.set_margin_end(20)
        self.set_child(main_box)

        # Header Title
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title_lbl = Gtk.Label(label="<span size='x-large' weight='bold'>🔑 Groq API Quotas &amp; Health</span>")
        title_lbl.set_use_markup(True)
        title_lbl.set_halign(Gtk.Align.START)
        title_box.append(title_lbl)

        sub_lbl = Gtk.Label(label="Live rate limits, daily requests remaining, and TPM token buffers queried across all keys in .env.")
        sub_lbl.set_halign(Gtk.Align.START)
        sub_lbl.add_css_class("dim-label")
        title_box.append(sub_lbl)
        main_box.append(title_box)

        # Summary Metrics Box
        self.stats_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        main_box.append(self.stats_box)

        # Status / Progress indicator
        self.status_label = Gtk.Label(label="⏳ Querying Groq API keys...")
        self.status_label.set_halign(Gtk.Align.START)
        main_box.append(self.status_label)

        # Scrolled Keys List
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_vexpand(True)
        self.keys_listbox = Gtk.ListBox()
        self.keys_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.keys_listbox.add_css_class("boxed-list")
        self.scroll.set_child(self.keys_listbox)
        main_box.append(self.scroll)

        # Explainer / Guide Notes
        guide_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        g1 = Gtk.Label(label="<small>• <b>RPD (Requests Per Day):</b> 1,000 req/day per key. Resets once daily at 00:00 UTC (~midnight).</small>")
        g1.set_use_markup(True)
        g1.set_halign(Gtk.Align.START)
        guide_box.append(g1)
        g2 = Gtk.Label(label="<small>• <b>TPM (Tokens Per Minute):</b> 8,000 tokens/key/min. Rolling 60s window that clears in seconds.</small>")
        g2.set_use_markup(True)
        g2.set_halign(Gtk.Align.START)
        guide_box.append(g2)
        g3 = Gtk.Label(label="<small>• <b>Add More Capacity:</b> Add <tt>GROQ_API_KEY_8</tt>, <tt>GROQ_API_KEY_9</tt>, etc. into <tt>.env</tt> (+500 jobs/day per key).</small>")
        g3.set_use_markup(True)
        g3.set_halign(Gtk.Align.START)
        guide_box.append(g3)
        main_box.append(guide_box)

        # Bottom Action Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_halign(Gtk.Align.END)
        self.refresh_btn = Gtk.Button(label="🔄 Refresh Limits")
        self.refresh_btn.connect("clicked", lambda w: self.fetch_limits())
        btn_box.append(self.refresh_btn)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda w: self.close())
        btn_box.append(close_btn)
        main_box.append(btn_box)

        self.fetch_limits()

    def fetch_limits(self):
        self.refresh_btn.set_sensitive(False)
        self.status_label.set_text("⏳ Querying Groq API keys...")

        def worker():
            try:
                from src.ranking.groq_limits import check_all_groq_limits
                data = check_all_groq_limits()
                GLib.idle_add(self._on_limits_loaded, data)
            except Exception as e:
                GLib.idle_add(self._on_limits_error, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_limits_error(self, err_msg):
        self.refresh_btn.set_sensitive(True)
        self.status_label.set_markup(f"❌ <span foreground='#cf222e'>Error: {err_msg}</span>")
        return False

    def _on_limits_loaded(self, data):
        self.refresh_btn.set_sensitive(True)
        if data.get("error"):
            self.status_label.set_markup(f"❌ <span foreground='#cf222e'>{data['error']}</span>")
            return False

        self.status_label.set_markup(f"✅ Checked <b>{data['total_keys']} keys</b> | Daily reset in <b>{data['daily_reset_in']}</b> (at 00:00 UTC)")

        # Clear stats_box
        while True:
            c = self.stats_box.get_first_child()
            if not c:
                break
            self.stats_box.remove(c)

        # Build Summary Cards
        def make_card(title, value, subtext):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            box.add_css_class("metric-card")
            box.set_hexpand(True)
            t = Gtk.Label(label=f"<small>{title}</small>")
            t.set_use_markup(True)
            t.set_halign(Gtk.Align.START)
            t.add_css_class("dim-label")
            box.append(t)
            v = Gtk.Label(label=f"<span size='large' weight='bold'>{value}</span>")
            v.set_use_markup(True)
            v.set_halign(Gtk.Align.START)
            box.append(v)
            s = Gtk.Label(label=f"<small>{subtext}</small>")
            s.set_use_markup(True)
            s.set_halign(Gtk.Align.START)
            s.add_css_class("dim-label")
            box.append(s)
            return box

        tok_pct = (data['total_remaining_tokens'] / (data['total_daily_tokens'] or 1)) * 100
        self.stats_box.append(make_card("DAILY TOKEN QUOTA", f"~{data['total_remaining_tokens']:,} tokens", f"{tok_pct:.1f}% left of {data['total_daily_tokens']:,}"))
        self.stats_box.append(make_card("REAL JOB CAPACITY", f"~{data['token_based_capacity']:,} jobs", "at ~1,800 tokens/job"))
        self.stats_box.append(make_card("FALLBACK CAPACITY", f"+{data['fallback_capacity']:,} jobs", "openai/gpt-oss-20b"))
        self.stats_box.append(make_card("DAILY RESET", f"{data['daily_reset_in']}", "Resets at 00:00 UTC"))

        # Clear keys listbox
        while True:
            c = self.keys_listbox.get_first_child()
            if not c:
                break
            self.keys_listbox.remove(c)

        for k in data["keys"]:
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
            row_box.set_margin_top(8)
            row_box.set_margin_bottom(8)
            row_box.set_margin_start(12)
            row_box.set_margin_end(12)

            # Left: Key info + badge
            key_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            key_box.set_size_request(180, -1)
            idx_lbl = Gtk.Label(label=f"<b>Key #{k['index']}</b>")
            idx_lbl.set_use_markup(True)
            key_box.append(idx_lbl)

            mask_lbl = Gtk.Label(label=f"<tt>{k['masked_key']}</tt>")
            mask_lbl.set_use_markup(True)
            mask_lbl.add_css_class("dim-label")
            key_box.append(mask_lbl)
            row_box.append(key_box)

            # Status pill
            status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            status_box.set_size_request(150, -1)
            pill = Gtk.Label(label=k["status_label"])
            pill.set_halign(Gtk.Align.START)
            status_box.append(pill)
            row_box.append(status_box)

            # Daily Tokens
            tpd_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            tpd_box.set_hexpand(True)
            rem_k = k['est_remaining_daily_tokens'] // 1000
            lim_k = k['limit_daily_tokens'] // 1000
            tpd_lbl = Gtk.Label(label=f"<b>Daily Tokens:</b> ~{rem_k}k / {lim_k}k ({k['daily_token_pct']:.0f}%)")
            tpd_lbl.set_use_markup(True)
            tpd_lbl.set_halign(Gtk.Align.START)
            tpd_box.append(tpd_lbl)
            row_box.append(tpd_box)

            # TPM Buffer
            tpm_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            tpm_box.set_size_request(160, -1)
            tpm_lbl = Gtk.Label(label=f"<b>TPM Buffer:</b> {k['remaining_tokens']:,} / {k['limit_tokens']:,}")
            tpm_lbl.set_use_markup(True)
            tpm_lbl.set_halign(Gtk.Align.START)
            tpm_box.append(tpm_lbl)
            row_box.append(tpm_box)

            # Reset timer
            rst_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            rst_box.set_size_request(110, -1)
            rst_lbl = Gtk.Label(label=f"<small>Reset: {k['reset_requests']}</small>")
            rst_lbl.set_use_markup(True)
            rst_lbl.set_halign(Gtk.Align.END)
            rst_lbl.add_css_class("dim-label")
            rst_box.append(rst_lbl)
            row_box.append(rst_box)

            self.keys_listbox.append(row_box)

        return False


class JobApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.github.jobscraper")

    def do_activate(self):
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
