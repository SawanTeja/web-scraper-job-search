import os
import json
import subprocess
import threading
import webbrowser
import html
import tkinter as tk
from datetime import datetime, timezone, timedelta
import customtkinter as ctk

import sys
import os

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

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


PLATFORM_COLORS = {
    "greenhouse": "#0b7a42",
    "lever": "#1f6feb",
    "ashby": "#8250df",
    "smartrecruiters": "#b07204",
    "workday": "#cf222e",
    "breezy": "#0969da",
    "workable": "#1a7f37",
    "bamboohr": "#2da44e",
    "jobvite": "#9a6700",
    "recruitee": "#545d68",
    "ats": "#444c56",
}

class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip_window = None
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)

    def enter(self, event=None):
        if not self.text: return
        x = event.x_root + 15 if event else self.widget.winfo_rootx() + 25
        y = event.y_root + 10 if event else self.widget.winfo_rooty() + 20
        self.tooltip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify='left',
                         background="#2b2b2b", foreground="white", relief='solid', borderwidth=1,
                         font=("Arial", 10, "normal"))
        label.pack(ipadx=4, ipady=4)

    def leave(self, event=None):
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None

DB_FILE = os.path.join(_ROOT_DIR, "jobs_db.sqlite")
STATUSES = ["New", "Applied", "Ongoing", "Rejected", "NA"]
RANKS = ["HIGH", "MEDIUM", "LOW", "IGNORE", "ERROR", "UNKNOWN"]

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
NA_EXPIRY_HOURS = 24

RANK_COLORS = {
    "HIGH": "#1a531b",   # green
    "MEDIUM": "#7a6b10", # amber/yellow
    "LOW": "#8c4412",    # orange
    "IGNORE": "#404040", # dark grey
    "ERROR": "#591313",  # red
    "UNKNOWN": "#3b3b3b" # grey
}

# How many rows to render per batch tick (keeps UI responsive)
BATCH_SIZE = 15
BATCH_DELAY_MS = 1  # ms between batches


class JobAppWindow(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Job Scraper & Tracker (Windows)")
        self.geometry("950x750")
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        init_db()
        self.jobs_db = load_db()
        self.rendered_jobs = set()

        # Track which rows are currently displayed so we can skip re-rendering
        # Maps link -> (status, rank, widget_reference)
        self._row_widgets = {}
        # Pending batch render state
        self._batch_queue = []
        self._batch_after_id = None
        # Track whether priority tab needs refresh
        self._prio_dirty = True
        # Track whether a full refresh is already scheduled
        self._refresh_scheduled = False

        # Header Box
        self.header_frame = ctk.CTkFrame(self)
        self.header_frame.pack(fill="x", padx=10, pady=10)

        self.scrape_btn = ctk.CTkButton(self.header_frame, text="Scrape Jobs", command=self.on_scrape_clicked, fg_color="#2b6b3e", hover_color="#215230")
        self.scrape_btn.pack(side="left", padx=5)

        self.scrape_ats_btn = ctk.CTkButton(self.header_frame, text="🏢 Scrape Companies", command=self.on_scrape_ats_clicked, fg_color="#2b6b3e", hover_color="#215230")
        self.scrape_ats_btn.pack(side="left", padx=5)

        self.view_companies_btn = ctk.CTkButton(self.header_frame, text="📋 Saved Companies", command=self.on_view_companies_clicked)
        self.view_companies_btn.pack(side="left", padx=5)

        self.refresh_btn = ctk.CTkButton(self.header_frame, text="Refresh Everything", command=self.on_refresh_clicked)
        self.refresh_btn.pack(side="left", padx=5)

        self.fast_filter_btn = ctk.CTkButton(self.header_frame, text="⚡ Smart Tier Filter", command=self.on_fast_filter_clicked, fg_color="#1f538d", hover_color="#14375e")
        self.fast_filter_btn.pack(side="left", padx=5)

        self.del_ignore_btn = ctk.CTkButton(self.header_frame, text="🗑 Delete IGNORE", command=self.on_delete_ignore_clicked, fg_color="#b32929", hover_color="#8a1f1f")
        self.del_ignore_btn.pack(side="left", padx=5)

        self.clear_db_btn = ctk.CTkButton(self.header_frame, text="💣 Clear DB", command=self.on_clear_db_clicked, fg_color="#b32929", hover_color="#8a1f1f")
        self.clear_db_btn.pack(side="left", padx=5)

        self.status_label = ctk.CTkLabel(self.header_frame, text="Ready.")
        self.status_label.pack(side="right", padx=5)

        # Tabs
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=10, pady=10)

        self.tab_names = ["Fresh Jobs", "Applied", "Ongoing", "Rejected", "NA", "Priority Sorting"]
        for tab_name in self.tab_names:
            self.tabview.add(tab_name)

        # Tab: Fresh Jobs
        self.fresh_tab = self.tabview.tab("Fresh Jobs")
        self.filter_frame = ctk.CTkFrame(self.fresh_tab)
        self.filter_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(self.filter_frame, text="Filter by Rank:").pack(side="left", padx=5)
        self.rank_filter_options = ["ALL"] + RANKS
        self.rank_filter_drop = ctk.CTkOptionMenu(self.filter_frame, values=self.rank_filter_options, command=self.on_rank_filter_changed)
        self.rank_filter_drop.set("ALL")
        self.rank_filter_drop.pack(side="left", padx=5)
        
        self.listboxes = {
            "New": ctk.CTkScrollableFrame(self.fresh_tab),
            "Applied": ctk.CTkScrollableFrame(self.tabview.tab("Applied")),
            "Ongoing": ctk.CTkScrollableFrame(self.tabview.tab("Ongoing")),
            "Rejected": ctk.CTkScrollableFrame(self.tabview.tab("Rejected")),
            "NA": ctk.CTkScrollableFrame(self.tabview.tab("NA")),
        }
        for s in self.listboxes.values():
            s.pack(fill="both", expand=True, padx=5, pady=5)

        # Tab: Priority Sorting
        self.prio_tab = self.tabview.tab("Priority Sorting")
        self.prio_header = ctk.CTkFrame(self.prio_tab)
        self.prio_header.pack(fill="x", pady=5)
        
        self.sort_btn = ctk.CTkButton(self.prio_header, text="AI Rank/Sort Jobs", command=self.on_sort_clicked, fg_color="#2b6b3e", hover_color="#215230")
        self.sort_btn.pack(side="left", padx=5)
        
        self.stop_sort_btn = ctk.CTkButton(self.prio_header, text="🛑 Stop Ranking", command=self.on_stop_sort_clicked, fg_color="#b32929", hover_color="#8a1f1f")
        self.stop_sort_btn.pack(side="left", padx=5)
        self.stop_sort_btn.configure(state="disabled")

        self.reset_rank_btn = ctk.CTkButton(self.prio_header, text="Reset Ranks", command=self.on_reset_rank_clicked, fg_color="#b32929", hover_color="#8a1f1f")
        self.reset_rank_btn.pack(side="left", padx=5)

        self.prio_filter_btn = ctk.CTkButton(self.prio_header, text="⚡ Smart Tier Filter", command=self.on_fast_filter_clicked, fg_color="#1f538d", hover_color="#14375e")
        self.prio_filter_btn.pack(side="left", padx=5)

        self.sort_status_label = ctk.CTkLabel(self.prio_header, text="")
        self.sort_status_label.pack(side="left", padx=5)
        
        self.prio_listbox = ctk.CTkScrollableFrame(self.prio_tab)
        self.prio_listbox.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.page_size = 50
        self.page_offsets = {
            "New": self.page_size,
            "Applied": self.page_size,
            "Ongoing": self.page_size,
            "Rejected": self.page_size,
            "NA": self.page_size,
            "Priority Sorting": self.page_size,
        }
        
        self.tabview.configure(command=self.on_tab_switched)

        self.backfill_added_at()
        self.expire_stale_jobs()
        self.refresh_ui()

    def on_closing(self):
        if hasattr(self, 'rank_process') and self.rank_process:
            self.rank_process.terminate()
            self.rank_process = None
        
        if os.path.exists("stop.flag"):
            try:
                os.remove("stop.flag")
            except:
                pass
        self.destroy()

    def save_db(self):
        save_db(self.jobs_db)
        
    def backfill_added_at(self):
        updated = False
        for link, data in self.jobs_db.items():
            if "added_at" not in data:
                data["added_at"] = datetime.now(timezone.utc).isoformat()
                updated = True
            if data.get("status") == "Ongoing/Waiting":
                data["status"] = "Ongoing"
                updated = True
        if updated:
            self.save_db()

    def expire_stale_jobs(self):
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
        for widget in listbox.winfo_children():
            widget.destroy()

    def get_listbox_for_status(self, status):
        return self.listboxes.get(status, self.listboxes["New"])

    # ──────────────────────────────────────────────
    #  Batched rendering helpers
    # ──────────────────────────────────────────────
    def _cancel_pending_batch(self):
        """Cancel any in-flight batch rendering."""
        if self._batch_after_id is not None:
            self.after_cancel(self._batch_after_id)
            self._batch_after_id = None
        self._batch_queue.clear()

    def _render_batch_tick(self):
        """Render the next BATCH_SIZE items from the queue."""
        if not self._batch_queue:
            self._batch_after_id = None
            return

        chunk = self._batch_queue[:BATCH_SIZE]
        self._batch_queue = self._batch_queue[BATCH_SIZE:]

        for render_fn, args in chunk:
            render_fn(*args)

        if self._batch_queue:
            self._batch_after_id = self.after(BATCH_DELAY_MS, self._render_batch_tick)
        else:
            self._batch_after_id = None

    def _enqueue_rows(self, items):
        """Add items to the batch queue and start processing if not already running."""
        self._batch_queue.extend(items)
        if self._batch_after_id is None and self._batch_queue:
            self._batch_after_id = self.after(BATCH_DELAY_MS, self._render_batch_tick)

    # ──────────────────────────────────────────────
    #  Main refresh — now with batched rendering
    # ──────────────────────────────────────────────
    def refresh_ui(self):
        # Cancel any in-progress batch rendering from a previous refresh
        self._cancel_pending_batch()

        # Destroy all existing row widgets
        for listbox in self.listboxes.values():
            self.clear_listbox(listbox)
        self.clear_listbox(self.prio_listbox)
        self._row_widgets.clear()
        self.rendered_jobs.clear()

        self.expire_stale_jobs()

        counts = {"New": 0, "Applied": 0, "Ongoing": 0, "Rejected": 0, "NA": 0}
        grouped_jobs = {"New": [], "Applied": [], "Ongoing": [], "Rejected": [], "NA": []}

        for link, data in self.jobs_db.items():
            status = data.get("status", "New")
            counts[status] = counts.get(status, 0) + 1
            if status in grouped_jobs:
                grouped_jobs[status].append((link, data))

        self.status_label.configure(text=f"Fresh: {counts['New']} | Applied: {counts['Applied']} | Ongoing: {counts['Ongoing']} | Rejected: {counts['Rejected']} | NA: {counts['NA']}")

        # Build a flat list of (render_fn, args) for batch rendering
        render_queue = []

        for status, items in grouped_jobs.items():
            if status == "New":
                active_filter = self.rank_filter_drop.get()
                if active_filter != "ALL":
                    items = [(l, d) for l, d in items if d.get("rank", "UNKNOWN") == active_filter]
                    
            limit = self.page_offsets.get(status, self.page_size)
            for link, data in items[:limit]:
                render_queue.append((self._create_main_job_row, (link, data)))
                
            if len(items) > limit:
                render_queue.append((self._create_load_more_btn, (status, len(items) - limit)))

        # Only render priority tab if it's currently visible
        current_tab = self.tabview.get()
        if current_tab == "Priority Sorting":
            self._build_priority_queue(render_queue)
            self._prio_dirty = False
        else:
            self._prio_dirty = True

        # Kick off batched rendering
        self._enqueue_rows(render_queue)

    def _build_priority_queue(self, render_queue):
        """Append priority-sorted rows to the render queue."""
        def get_rank_weight(rank_str):
            weights = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3, "ERROR": 4, "IGNORE": 5}
            return weights.get(rank_str, 3)

        now = datetime.now(timezone.utc)
        recent_jobs = {}
        for link, data in self.jobs_db.items():
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
        prio_limit = self.page_offsets.get("Priority Sorting", self.page_size)
        
        for link, data in sorted_jobs[:prio_limit]:
            render_queue.append((self._create_priority_row, (link, data)))
            
        if len(sorted_jobs) > prio_limit:
            render_queue.append((self._create_prio_load_more_btn, (len(sorted_jobs) - prio_limit,)))

    def _create_load_more_btn(self, status, remaining):
        btn = ctk.CTkButton(
            self.get_listbox_for_status(status),
            text=f"Load More ({remaining} remaining)",
            command=lambda s=status: self.load_more(s),
            fg_color="#3e3e3e",
            hover_color="#555555"
        )
        btn.pack(pady=10)

    def _create_prio_load_more_btn(self, remaining):
        btn = ctk.CTkButton(
            self.prio_listbox,
            text=f"Load More ({remaining} remaining)",
            command=lambda: self.load_more("Priority Sorting"),
            fg_color="#3e3e3e",
            hover_color="#555555"
        )
        btn.pack(pady=10)

    def _create_main_job_row(self, link, data):
        """Create and pack a single main job row widget."""
        self.rendered_jobs.add(link)
        title = data.get("title", "Unknown")
        status = data.get("status", "New")
        rank = data.get("rank", "UNKNOWN")
        reason = data.get("reason", "")

        target_listbox = self.get_listbox_for_status(status)

        row_frame = ctk.CTkFrame(target_listbox, corner_radius=5)
        row_frame.pack(fill="x", pady=2, padx=2)

        color = RANK_COLORS.get(rank, RANK_COLORS["UNKNOWN"])
        rank_label = ctk.CTkLabel(row_frame, text=f" {rank} ", fg_color=color, corner_radius=5, font=("Arial", 12, "bold"))
        rank_label.pack(side="left", padx=10, pady=8)

        title_btn = ctk.CTkButton(row_frame, text=title, fg_color="transparent", hover_color="#2b2b2b", anchor="w", command=lambda: self.on_job_row_clicked(data, link))
        title_btn.pack(side="left", fill="x", expand=True, padx=5)

        tooltip_text = f"URL: {link}"
        if reason:
            tooltip_text += f"\n\nAI REASON:\n{reason}"
        details = data.get("details")
        if details and isinstance(details, dict):
            tooltip_text += "\n\nEXTRACTED DETAILS:"
            for key, val in details.items():
                if val is not None and val != "" and val != []:
                    tooltip_text += f"\n• {key.replace('_', ' ').title()}: {val}"
        ToolTip(title_btn, tooltip_text)

        status_drop = ctk.CTkOptionMenu(row_frame, values=STATUSES, command=lambda v, l=link, r=row_frame: self.on_status_changed(v, l, r))
        status_drop.set(status)
        status_drop.pack(side="left", padx=10)

        apply_btn = ctk.CTkButton(row_frame, text="Apply", command=lambda: self.on_apply_clicked(link), width=60)
        apply_btn.pack(side="left", padx=10)

        self._row_widgets[link] = (status, rank, row_frame)

    # Keep old name as alias for compatibility
    def add_main_job_row(self, link, data):
        self._create_main_job_row(link, data)

    def _create_priority_row(self, link, data):
        """Create and pack a single priority row widget."""
        title = data.get("title", "Unknown")
        rank = data.get("rank", "UNKNOWN")
        reason = data.get("reason", "No reason recorded.")
        status = data.get("status", "New")

        row_frame = ctk.CTkFrame(self.prio_listbox, corner_radius=5)
        row_frame.pack(fill="x", pady=2, padx=2)

        top_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
        top_frame.pack(fill="x", padx=10, pady=(8, 0))

        color = RANK_COLORS.get(rank, RANK_COLORS["UNKNOWN"])
        ctk.CTkLabel(top_frame, text=f" {rank} ", fg_color=color, corner_radius=5, font=("Arial", 12, "bold")).pack(side="left", padx=(0, 10))

        ctk.CTkButton(top_frame, text=title, fg_color="transparent", hover_color="#2b2b2b", anchor="w", command=lambda: self.on_job_row_clicked(data, link)).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(top_frame, text=f"Status: {status}").pack(side="right")

        ctk.CTkLabel(row_frame, text=reason, justify="left", wraplength=800, text_color="gray").pack(fill="x", padx=10, pady=(3, 8))

    # Keep old name as alias for compatibility
    def add_priority_row(self, link, data):
        self._create_priority_row(link, data)

    def on_job_row_clicked(self, data, link):
        details = data.get("details", {})
        if not isinstance(details, dict):
            details = {}

        title = data.get("title", 'Job Details')

        popup = ctk.CTkToplevel(self)
        popup.title("Job Extract Details")
        popup.geometry("900x600")
        popup.attributes("-topmost", True)
        popup.after(100, lambda: popup.attributes("-topmost", False))
        
        main_box = ctk.CTkScrollableFrame(popup)
        main_box.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(main_box, text=title, font=("Arial", 20, "bold"), justify="left", wraplength=800).pack(anchor="w", pady=(0, 10))
        
        company = details.get("company")
        location = details.get("location")
        jtype = details.get("job_type")
        salary = details.get("salary")
        
        info_str = []
        if company: info_str.append(f"Company: {company}")
        if location: info_str.append(f"Location: {location}")
        if jtype: info_str.append(f"Type: {jtype}")
        if salary: info_str.append(f"Salary: {salary}")
        if info_str:
            ctk.CTkLabel(main_box, text=" | ".join(info_str), font=("Arial", 14), justify="left", wraplength=800).pack(anchor="w", pady=(0, 10))

        # Show Rank Badge and Reason
        rank = data.get("rank", "UNKNOWN")
        reason = data.get("reason", "")
        if rank:
            rank_color = RANK_COLORS.get(rank, RANK_COLORS["UNKNOWN"])
            badge_text = f" {rank} " + (f"  —  {reason}" if reason else "")
            ctk.CTkLabel(main_box, text=badge_text, fg_color=rank_color, corner_radius=5, font=("Arial", 13, "bold"), text_color="white").pack(anchor="w", pady=(0, 10))

        link_btn = ctk.CTkButton(main_box, text="Click here to open Original Job Posting", command=lambda: webbrowser.open(link))
        link_btn.pack(anchor="w", pady=(0, 20))
        
        if details:
            skip_keys = ["job_name", "company", "location", "job_type", "salary"]
            key_order = ["about_job", "responsibilities", "requirements", "skills_required", "skills_preferred", "nice_to_have"]
            all_keys = list(details.keys())
            ordered_keys = [k for k in key_order if k in all_keys] + [k for k in all_keys if k not in key_order]
            
            for key in ordered_keys:
                if key in skip_keys: continue
                val = details[key]
                if val is not None and val != "" and val != []:
                    ctk.CTkLabel(main_box, text=key.replace('_', ' ').title(), font=("Arial", 16, "bold")).pack(anchor="w", pady=(10, 5))
                    if isinstance(val, list):
                        val_str = "• " + "\n• ".join(str(v) for v in val)
                    else:
                        val_str = str(val)
                    ctk.CTkLabel(main_box, text=val_str, justify="left", wraplength=800).pack(anchor="w", pady=(0, 5))
        else:
            ctk.CTkLabel(main_box, text="No structured AI extractions available for this job yet.").pack(anchor="w")

    def on_status_changed(self, new_status, link, row_frame):
        old_status = self.jobs_db[link].get("status", "New")
        if new_status == old_status: return

        self.jobs_db[link]["status"] = new_status
        conn = get_conn()
        update_job(link, conn, status=new_status)
        conn.commit()
        conn.close()

        # Just move the row instead of full refresh
        row_frame.pack_forget()
        target_listbox = self.get_listbox_for_status(new_status)
        row_frame.master = target_listbox
        # Destroy and re-create just this one row in the target listbox
        row_frame.destroy()
        self._create_main_job_row(link, self.jobs_db[link])
        self.update_counts()
        self._prio_dirty = True

    def update_counts(self):
        counts = {"New": 0, "Applied": 0, "Ongoing": 0, "Rejected": 0, "NA": 0}
        for data in self.jobs_db.values():
            st = data.get("status", "New")
            counts[st] = counts.get(st, 0) + 1
        self.status_label.configure(text=f"Fresh: {counts['New']} | Applied: {counts['Applied']} | Ongoing: {counts['Ongoing']} | Rejected: {counts['Rejected']} | NA: {counts['NA']}")

    def on_apply_clicked(self, link):
        try: webbrowser.open(link)
        except Exception as e: print(f"Failed to open URI: {e}")

    def on_rank_filter_changed(self, value):
        self.page_offsets["New"] = self.page_size
        # Only rebuild the Fresh Jobs listbox, not everything
        self._cancel_pending_batch()
        self.clear_listbox(self.listboxes["New"])

        active_filter = value
        items = [(l, d) for l, d in self.jobs_db.items() if d.get("status", "New") == "New"]
        if active_filter != "ALL":
            items = [(l, d) for l, d in items if d.get("rank", "UNKNOWN") == active_filter]

        limit = self.page_offsets.get("New", self.page_size)
        render_queue = []
        for link, data in items[:limit]:
            render_queue.append((self._create_main_job_row, (link, data)))
        if len(items) > limit:
            render_queue.append((self._create_load_more_btn, ("New", len(items) - limit)))
        self._enqueue_rows(render_queue)
        
    def load_more(self, status):
        old_limit = self.page_offsets[status]
        self.page_offsets[status] += self.page_size
        new_limit = self.page_offsets[status]

        items = []
        if status == "Priority Sorting":
            now = datetime.now(timezone.utc)
            recent_jobs = {}
            for link, data in self.jobs_db.items():
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

            def get_rank_weight(rank_str):
                weights = {"HIGH": 0, "LOW": 1, "UNKNOWN": 2, "ERROR": 3, "IGNORE": 4}
                return weights.get(rank_str, 3)

            items = sorted(recent_jobs.items(), key=lambda x: get_rank_weight(x[1].get("rank", "UNKNOWN")))
        else:
            active_filter = "ALL"
            if status == "New":
                active_filter = self.rank_filter_drop.get()

            for link, data in self.jobs_db.items():
                if data.get("status", "New") == status:
                    if status == "New" and active_filter != "ALL":
                        if data.get("rank", "UNKNOWN") != active_filter:
                            continue
                    items.append((link, data))

        target_listbox = self.prio_listbox if status == "Priority Sorting" else self.get_listbox_for_status(status)
        for child in reversed(target_listbox.winfo_children()):
            if isinstance(child, ctk.CTkButton) and "Load More" in child.cget("text"):
                child.destroy()
                break

        render_queue = []
        for link, data in items[old_limit:new_limit]:
            if status == "Priority Sorting":
                render_queue.append((self._create_priority_row, (link, data)))
            else:
                render_queue.append((self._create_main_job_row, (link, data)))

        if len(items) > new_limit:
            if status == "Priority Sorting":
                render_queue.append((self._create_prio_load_more_btn, (len(items) - new_limit,)))
            else:
                render_queue.append((self._create_load_more_btn, (status, len(items) - new_limit)))

        self._enqueue_rows(render_queue)

    def on_delete_ignore_clicked(self):
        ignore_links = [link for link, data in self.jobs_db.items() if data.get("rank") == "IGNORE"]
        for link in ignore_links:
            del self.jobs_db[link]
        if ignore_links:
            deleted = delete_jobs_by_rank("IGNORE")
            self.refresh_ui()
            self.status_label.configure(text=f"🗑 Deleted {deleted} IGNORE jobs.")
        else:
            self.status_label.configure(text="No IGNORE jobs to delete.")

    def on_clear_db_clicked(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Clear Database Options")
        dialog.geometry("520x400")
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)

        frame = ctk.CTkFrame(dialog, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=25, pady=20)

        ctk.CTkLabel(frame, text="Clear Database Options", font=("Arial", 16, "bold")).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(frame, text="Choose what you want to remove from your database:", font=("Arial", 11), text_color="gray").pack(anchor="w", pady=(0, 15))

        # Option 1: Delete all saved companies only
        def clear_companies():
            cnt = clear_all_companies()
            if hasattr(self, "companies_win") and self.companies_win and self.companies_win.winfo_exists():
                self.companies_win.load_data()
            self.status_label.configure(text=f"🏢 Cleared all {cnt} companies from pool. Jobs kept.")
            dialog.destroy()

        ctk.CTkButton(frame, text="🏢 Delete All Saved Companies Only", command=clear_companies).pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(frame, text="Deletes all saved companies from ATS pool. Scraped jobs and rank data are kept.", font=("Arial", 10), text_color="gray", justify="left").pack(anchor="w", pady=(0, 12))

        # Option 2: Delete all scraped jobs only
        def clear_jobs():
            cnt = clear_all_jobs()
            self.jobs_db.clear()
            self.refresh_ui()
            self.status_label.configure(text=f"💼 Cleared {cnt} jobs from database. Saved companies kept.")
            dialog.destroy()

        ctk.CTkButton(frame, text="💼 Delete All Scraped Jobs Only", command=clear_jobs).pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(frame, text="Deletes all scraped job postings and rankings. Saved companies pool is kept.", font=("Arial", 10), text_color="gray", justify="left").pack(anchor="w", pady=(0, 12))

        # Option 3: Delete everything
        def clear_all():
            res = clear_everything()
            self.jobs_db.clear()
            self.refresh_ui()
            if hasattr(self, "companies_win") and self.companies_win and self.companies_win.winfo_exists():
                self.companies_win.load_data()
            self.status_label.configure(text=f"💣 Wiped database! ({res['jobs']} jobs, {res['companies']} companies, {res['deleted_urls']} archived URLs)")
            dialog.destroy()

        ctk.CTkButton(frame, text="💣 Delete Everything (Jobs + Companies + ATS Data)", fg_color="#b32929", hover_color="#8a1f1f", command=clear_all).pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(frame, text="Complete wipe: deletes all jobs, saved companies, and archived URL history.", font=("Arial", 10), text_color="gray", justify="left").pack(anchor="w", pady=(0, 15))

        ctk.CTkButton(frame, text="Cancel", width=100, command=dialog.destroy).pack(anchor="e")

    def on_refresh_clicked(self):
        self.jobs_db = load_db()
        self.refresh_ui()

    def on_tab_switched(self):
        if self.tabview.get() == "Priority Sorting" and self._prio_dirty:
            # Only rebuild priority list, not everything
            self._cancel_pending_batch()
            self.clear_listbox(self.prio_listbox)
            render_queue = []
            self._build_priority_queue(render_queue)
            self._enqueue_rows(render_queue)
            self._prio_dirty = False

    def on_scrape_clicked(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Select Scraping Speed")
        dialog.geometry("350x200")
        dialog.attributes("-topmost", True)
        
        ctk.CTkLabel(dialog, text="Choose the scraper speed.").pack(pady=(20, 10))
        
        def start(speed):
            dialog.destroy()
            script_name = "intern_scraper.py"
            if speed == 1: script_name = "intern_scraper_fast.py"
            elif speed == 3: script_name = "intern_scraper_overnight.py"
            
            self.status_label.configure(text=f"Scraping started ({script_name})...")
            self.scrape_btn.configure(state="disabled")
            
            thread = threading.Thread(target=self.run_scraper, args=(script_name,))
            thread.daemon = True
            thread.start()
            
        ctk.CTkButton(dialog, text="⚡ Fast", command=lambda: start(1)).pack(pady=5)
        ctk.CTkButton(dialog, text="🚶 Medium (Default)", command=lambda: start(2)).pack(pady=5)
        ctk.CTkButton(dialog, text="🌙 Overnight", command=lambda: start(3)).pack(pady=5)

    def run_scraper(self, script_name="intern_scraper.py"):
        import time
        import sys
        try:
            venv_python = None
            possible_paths = ["Scripts/python.exe"] if os.name == "nt" else ["bin/python"]
            for path in possible_paths:
                full_path = os.path.join(os.getcwd(), "scraper_env", *path.split("/"))
                if os.path.exists(full_path):
                    venv_python = full_path
                    break
            if not venv_python:
                venv_python = sys.executable
                
            process = subprocess.Popen([venv_python, resolve_script_path(script_name)], cwd=os.getcwd())
            
            while process.poll() is None:
                time.sleep(30)
                self.after(0, self.periodic_sync_and_rank)
                
            self.after(0, lambda: self.on_scrape_finished(process.returncode == 0))
        except Exception as e:
            print(f"Error running scraper: {e}")
            self.after(0, lambda: self.on_scrape_finished(False))

    def periodic_sync_and_rank(self):
        new_db = load_db()
        added_count = 0
        render_queue = []
        for link, data in new_db.items():
            if link not in self.jobs_db:
                self.jobs_db[link] = data
            if link not in self.rendered_jobs:
                render_queue.append((self._create_main_job_row, (link, data)))
                added_count += 1
        if render_queue:
            self._enqueue_rows(render_queue)
        if added_count > 0:
            self.status_label.configure(text=f"Scraping in progress... discovered {added_count} new jobs live!")
            self._prio_dirty = True
            
    def on_scrape_finished(self, success):
        self.scrape_btn.configure(state="normal")
        if success:
            self.jobs_db = load_db()
            self.refresh_ui()
            self.status_label.configure(text="Scraping finished!")
        else:
            self.status_label.configure(text="Scraping failed.")

    def on_scrape_ats_clicked(self):
        self.status_label.configure(text="Scraping saved company ATS boards...")
        self.scrape_ats_btn.configure(state="disabled")
        thread = threading.Thread(target=self.run_ats_scraper_thread)
        thread.daemon = True
        thread.start()

    def run_ats_scraper_thread(self):
        import time
        import sys
        try:
            venv_python = None
            possible_paths = ["Scripts/python.exe"] if os.name == "nt" else ["bin/python"]
            for path in possible_paths:
                full_path = os.path.join(os.getcwd(), "scraper_env", *path.split("/"))
                if os.path.exists(full_path):
                    venv_python = full_path
                    break
            if not venv_python:
                venv_python = sys.executable

            process = subprocess.Popen([venv_python, resolve_script_path("ats_scraper.py")], cwd=os.getcwd())

            while process.poll() is None:
                time.sleep(5)
                self.after(0, self.periodic_sync_and_rank)

            self.after(0, lambda: self.on_ats_scrape_finished(process.returncode == 0))
        except Exception as e:
            print(f"Error running ATS scraper: {e}")
            self.after(0, lambda: self.on_ats_scrape_finished(False))

    def on_ats_scrape_finished(self, success):
        self.scrape_ats_btn.configure(state="normal")
        if success:
            self.jobs_db = load_db()
            self.refresh_ui()
            comp_cnt = get_company_count()
            self.status_label.configure(text=f"ATS Scraping finished! ({comp_cnt} companies saved)")

    def on_view_companies_clicked(self):
        if not hasattr(self, "companies_win") or self.companies_win is None or not self.companies_win.winfo_exists():
            self.companies_win = CompaniesWindowWin(self)
        else:
            self.companies_win.focus()

    # --- DETERMINISTIC PRE-FILTER LOGIC (ZERO TOKENS) ---
    def on_fast_filter_clicked(self):
        self.status_label.configure(text="⚡ Running Smart Tier Deterministic Filter (0 AI tokens)...")
        self.fast_filter_btn.configure(state="disabled")
        if hasattr(self, 'prio_filter_btn'):
            self.prio_filter_btn.configure(state="disabled")

        def worker():
            try:
                try:
                    from src.ranking.job_filter import apply_deterministic_filter_to_db
                except ImportError:
                    from job_filter import apply_deterministic_filter_to_db
                stats = apply_deterministic_filter_to_db(only_unknown=False, move_to_na=True)
                def update_gui():
                    self.jobs_db = load_db()
                    self.page_offsets["New"] = self.page_size
                    self.refresh_ui()
                    self.fast_filter_btn.configure(state="normal")
                    if hasattr(self, 'prio_filter_btn'):
                        self.prio_filter_btn.configure(state="normal")
                    fresh_count = sum(1 for d in self.jobs_db.values() if d.get('status') == 'New')
                    tier1_count = stats.get('tier1_early_career', 0)
                    tier2_count = stats.get('tier2_general_swe', 0)
                    total_ignored = stats.get('total_ignored', 0)
                    msg = (f"⚡ Smart Tier: {tier1_count} Early Career (HIGH) | {tier2_count} General SWE | "
                           f"Ignored {total_ignored} (Senior/Helpdesk/US/Hubs) -> NA | Fresh Active: {fresh_count}")
                    self.status_label.configure(text=msg)
                    if hasattr(self, 'sort_status_label'):
                        self.sort_status_label.configure(text=msg)
                self.after(0, update_gui)
            except Exception as e:
                print(f"Error in deterministic filter worker: {e}")
                def on_err():
                    self.fast_filter_btn.configure(state="normal")
                    if hasattr(self, 'prio_filter_btn'):
                        self.prio_filter_btn.configure(state="normal")
                    self.status_label.configure(text=f"⚠️ Filter error: {e}")
                self.after(0, on_err)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def on_reset_rank_clicked(self):
        count = 0
        for link, data in self.jobs_db.items():
            if data.get("status") != "Applied":
                data["rank"] = "UNKNOWN"
                data["reason"] = "Reset for re-evaluation"
                count += 1
        if count > 0:
            self.save_db()
            self.refresh_ui()
            self.sort_status_label.configure(text=f"Reset {count} jobs to UNKNOWN rank.")
        else:
            self.sort_status_label.configure(text="No jobs to reset.")

    def on_stop_sort_clicked(self):
        with open("stop.flag", "w") as f:
            f.write("stop")
        self.sort_status_label.configure(text="🛑 Stopping... Please wait for current job to finish.")
        self.stop_sort_btn.configure(state="disabled")

    def on_sort_clicked(self):
        if not os.path.exists(DB_FILE):
            self.sort_status_label.configure(text=f"No {DB_FILE}. Scrape first!")
            return

        # Check configured Groq keys
        key_count = sum(1 for i in range(1, 26) if os.environ.get(f"GROQ_API_KEY_{i}"))
        if key_count == 0 and os.environ.get("GROQ_API_KEY"):
            key_count = 1
        key_info = f"({key_count} Groq API keys in circular rotation)" if key_count > 0 else ""

        self.sort_status_label.configure(text=f"Initializing AI analysis {key_info}... Check terminal!")
        self.sort_btn.configure(state="disabled")
        self.stop_sort_btn.configure(state="normal")
        thread = threading.Thread(target=self.run_sorter)
        thread.daemon = True
        thread.start()

    def run_sorter(self):
        import sys
        try:
            venv_python = None
            possible_paths = ["Scripts/python.exe"] if os.name == "nt" else ["bin/python"]
            for path in possible_paths:
                full_path = os.path.join(os.getcwd(), "scraper_env", *path.split("/"))
                if os.path.exists(full_path):
                    venv_python = full_path
                    break
            if not venv_python:
                venv_python = sys.executable
                
            self.rank_process = subprocess.Popen([venv_python, resolve_script_path("rank_internships.py")], cwd=os.getcwd())
            self.rank_process.wait()
            self.rank_process = None
            self.after(0, lambda: self.on_sort_finished(True))
        except Exception as e:
            print(f"Error running sorter: {e}")
            self.after(0, lambda: self.on_sort_finished(False))

    def on_sort_finished(self, success):
        self.sort_btn.configure(state="normal")
        self.stop_sort_btn.configure(state="disabled")
        if success:
            self.sort_status_label.configure(text="Ranking finished!")
            self.jobs_db = load_db()
            self.refresh_ui()
        else:
            self.sort_status_label.configure(text="Ranking script failed or was stopped.")
            self.jobs_db = load_db()
            self.refresh_ui()


class AddCompanyWindowWin(ctk.CTkToplevel):
    def __init__(self, parent_win):
        super().__init__(parent_win)
        self.parent_win = parent_win
        self.title("➕ Add ATS Career Board")
        self.geometry("520x300")
        self.attributes("-topmost", True)
        self.resizable(False, False)

        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        title_lbl = ctk.CTkLabel(frame, text="Add Company to ATS Scraping Pool", font=("Arial", 16, "bold"))
        title_lbl.pack(anchor="w", pady=(0, 4))

        sub_lbl = ctk.CTkLabel(
            frame,
            text="Paste any job URL or career board (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Breezy, etc.)",
            font=("Arial", 11),
            text_color="gray",
            wraplength=460,
            justify="left"
        )
        sub_lbl.pack(anchor="w", pady=(0, 15))

        self.url_entry = ctk.CTkEntry(frame, placeholder_text="Career Board or Job URL (e.g. boards.greenhouse.io/stripe)", width=460)
        self.url_entry.pack(fill="x", pady=(0, 10))

        self.name_entry = ctk.CTkEntry(frame, placeholder_text="Company Name (Optional, auto-detected if blank)", width=460)
        self.name_entry.pack(fill="x", pady=(0, 10))

        self.status_lbl = ctk.CTkLabel(frame, text="", font=("Arial", 11), text_color="#cf222e", wraplength=460, justify="left")
        self.status_lbl.pack(anchor="w", pady=(0, 10))

        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(5, 0))

        ctk.CTkButton(btn_frame, text="Cancel", width=100, command=self.destroy).pack(side="left")
        ctk.CTkButton(
            btn_frame,
            text="Save & Track",
            width=130,
            fg_color="#2b6b3e",
            hover_color="#215230",
            command=self.on_save
        ).pack(side="right")

    def on_save(self):
        url = self.url_entry.get().strip()
        custom_name = self.name_entry.get().strip()
        if not url:
            self.status_lbl.configure(text="Please enter a career board or job URL.", text_color="#cf222e")
            return

        res = parse_ats_info(url, resolve_google=False)
        if not res:
            self.status_lbl.configure(
                text="❌ Could not detect ATS platform from URL.\nSupported: Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Breezy, BambooHR, Workable, Jobvite, Recruitee.",
                text_color="#cf222e"
            )
            return

        ats = res["ats_platform"]
        ident = res["identifier"]
        career_url = res["career_url"]
        comp_name = custom_name or res.get("company_name") or res.get("name") or ident.title()

        saved = save_company(comp_name, ats, ident, career_url)
        if saved:
            self.parent_win.load_data()
            self.destroy()
        else:
            self.status_lbl.configure(
                text=f"⚠️ Company @{ident} ({ats.upper()}) already exists in your pool.",
                text_color="#d29922"
            )


class CompaniesWindowWin(ctk.CTkToplevel):
    def __init__(self, parent_win):
        super().__init__(parent_win)
        self.parent_win = parent_win
        self.title("Saved Companies (ATS Scraping Pool)")
        self.geometry("1120x720")
        self.attributes("-topmost", True)

        self.all_companies = []
        self.filtered_companies = []

        # Top Action Bar
        top_bar = ctk.CTkFrame(self)
        top_bar.pack(fill="x", padx=15, pady=(15, 8))

        self.search_entry = ctk.CTkEntry(
            top_bar,
            placeholder_text="🔍 Search company name, ATS, or handle...",
            width=230
        )
        self.search_entry.pack(side="left", padx=(5, 6), pady=8)
        self.search_entry.bind("<KeyRelease>", lambda e: self.apply_filter())

        ctk.CTkLabel(top_bar, text="Platform:").pack(side="left", padx=(3, 2))
        self.platform_options = [
            "ALL", "greenhouse", "lever", "ashby", "smartrecruiters",
            "workday", "breezy", "workable", "bamboohr", "jobvite", "recruitee"
        ]
        self.platform_drop = ctk.CTkOptionMenu(
            top_bar,
            values=[p.upper() for p in self.platform_options],
            command=lambda v: self.apply_filter(),
            width=110
        )
        self.platform_drop.pack(side="left", padx=4)

        self.add_btn = ctk.CTkButton(
            top_bar,
            text="➕ Add",
            fg_color="#2b6b3e",
            hover_color="#215230",
            command=self.on_add_company_clicked,
            width=70
        )
        self.add_btn.pack(side="left", padx=3)

        self.extract_btn = ctk.CTkButton(
            top_bar,
            text="📥 Extract from Jobs",
            fg_color="#1f538d",
            hover_color="#14375e",
            command=self.on_extract_clicked,
            width=135
        )
        self.extract_btn.pack(side="left", padx=3)

        self.seed_btn = ctk.CTkButton(
            top_bar,
            text="🌱 Seed Top 45+",
            fg_color="#2b6b3e",
            hover_color="#215230",
            command=self.on_seed_clicked,
            width=120
        )
        self.seed_btn.pack(side="left", padx=3)

        self.refresh_btn = ctk.CTkButton(
            top_bar,
            text="🔄 Refresh",
            command=self.load_data,
            width=75
        )
        self.refresh_btn.pack(side="left", padx=3)

        self.clear_all_btn = ctk.CTkButton(
            top_bar,
            text="🗑 Clear All",
            fg_color="#b32929",
            hover_color="#8a1f1f",
            command=self.on_clear_all_clicked,
            width=80
        )
        self.clear_all_btn.pack(side="left", padx=3)


        # Stats Banner Frame
        self.stats_frame = ctk.CTkFrame(self, fg_color=("gray85", "gray20"))
        self.stats_frame.pack(fill="x", padx=15, pady=(0, 10))

        self.stats_label = ctk.CTkLabel(
            self.stats_frame,
            text="Loading stats...",
            font=("Arial", 12),
            anchor="w",
            padx=12,
            pady=8
        )
        self.stats_label.pack(fill="x")

        # Scrollable Frame for Company Cards
        self.scrollable = ctk.CTkScrollableFrame(self)
        self.scrollable.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        self.load_data()

    def load_data(self):
        self.all_companies = get_all_companies()
        stats = get_company_stats()

        total_c = stats.get("total_companies", 0)
        total_j = stats.get("total_jobs", 0)
        plat_strs = [f"{p.upper()}: {cnt}" for p, cnt in stats.get("platforms", {}).items()]
        breakdown = "  •  ".join(plat_strs)

        self.stats_label.configure(
            text=f"{total_c} Companies in Pool  •  {total_j} Jobs Scraped   |   {breakdown}"
        )
        self.apply_filter()

    def apply_filter(self):
        query = self.search_entry.get().strip().lower()
        selected_plat = self.platform_drop.get().strip().lower()

        filtered = []
        for c in self.all_companies:
            name = (c.get("name") or "").lower()
            ats = (c.get("ats_platform") or "").lower()
            ident = (c.get("identifier") or "").lower()
            url = (c.get("career_url") or "").lower()

            if selected_plat != "all" and ats != selected_plat:
                continue

            if query and query not in name and query not in ats and query not in ident and query not in url:
                continue

            filtered.append(c)

        self.filtered_companies = filtered
        self.render_companies()

    def render_companies(self):
        for widget in self.scrollable.winfo_children():
            widget.destroy()

        if not self.filtered_companies:
            empty_lbl = ctk.CTkLabel(
                self.scrollable,
                text="No saved companies match your search or filter.",
                font=("Arial", 14),
                text_color="gray"
            )
            empty_lbl.pack(pady=40)
            return

        for c in self.filtered_companies:
            row_frame = ctk.CTkFrame(self.scrollable, fg_color=("gray90", "gray17"))
            row_frame.pack(fill="x", pady=4, padx=5)

            # Left/Info box
            info_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
            info_frame.pack(side="left", fill="both", expand=True, padx=12, pady=8)

            # Top Row: Name + Badge + Handle
            top_line = ctk.CTkFrame(info_frame, fg_color="transparent")
            top_line.pack(fill="x", anchor="w")

            name_text = c.get("name") or c.get("identifier", "").title()
            name_lbl = ctk.CTkLabel(top_line, text=name_text, font=("Arial", 14, "bold"), anchor="w")
            name_lbl.pack(side="left", padx=(0, 8))

            ats = (c.get("ats_platform") or "ats").lower()
            badge_color = PLATFORM_COLORS.get(ats, "#444c56")
            badge = ctk.CTkLabel(
                top_line,
                text=f" {ats.upper()} ",
                fg_color=badge_color,
                text_color="white",
                corner_radius=4,
                font=("Arial", 10, "bold")
            )
            badge.pack(side="left", padx=(0, 8))

            ident_lbl = ctk.CTkLabel(top_line, text=f"@{c.get('identifier', '')}", text_color="gray", font=("Arial", 11))
            ident_lbl.pack(side="left")

            # Bottom Row: Career URL + Scraped metadata
            career_url = c.get("career_url") or ""
            disp_url = (career_url[:65] + "...") if len(career_url) > 65 else career_url

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
            meta_text = f"{disp_url}   •   {scraped_str}   •   {job_cnt} active jobs"
            meta_lbl = ctk.CTkLabel(info_frame, text=meta_text, text_color="gray", font=("Arial", 11), anchor="w")
            meta_lbl.pack(fill="x", anchor="w", pady=(3, 0))

            # Right action buttons
            btn_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
            btn_frame.pack(side="right", padx=12, pady=8)

            open_btn = ctk.CTkButton(
                btn_frame,
                text="🌐 Open Board",
                width=110,
                command=lambda u=career_url: webbrowser.open(u)
            )
            open_btn.pack(side="left", padx=4)

            del_btn = ctk.CTkButton(
                btn_frame,
                text="🗑 Remove",
                width=80,
                fg_color="#b32929",
                hover_color="#8a1f1f",
                command=lambda comp=c: self.confirm_delete_company(comp)
            )
            del_btn.pack(side="left", padx=4)

    def on_add_company_clicked(self):
        AddCompanyWindowWin(self)

    def on_extract_clicked(self):
        conn = get_conn()
        job_count = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
        conn.close()

        if job_count == 0:
            self.stats_label.configure(
                text="⚠️ No jobs in DB yet. Run 'Scrape Jobs' first or use '🌱 Seed Top 45+'!"
            )
            return

        self.extract_btn.configure(state="disabled")
        self.stats_label.configure(
            text=f"🔄 Scanning {job_count} jobs to extract company ATS career boards... Please wait..."
        )

        def worker():
            try:
                total_c = extract_companies_from_existing_jobs(max_workers=20)
                self.after(0, lambda: self._on_extract_done(total_c))
            except Exception as e:
                self.after(0, lambda: self._on_extract_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_extract_done(self, total_c):
        self.extract_btn.configure(state="normal")
        self.load_data()
        self.stats_label.configure(
            text=f"✅ Extraction complete! Total companies in pool: {total_c}"
        )
        if hasattr(self.parent_win, "status_label"):
            self.parent_win.status_label.configure(
                text=f"✅ Extracted ATS companies from jobs. Pool: {total_c} companies."
            )

    def _on_extract_error(self, err_msg):
        self.extract_btn.configure(state="normal")
        self.stats_label.configure(text=f"❌ Extraction error: {err_msg}")

    def on_seed_clicked(self):
        added = seed_starter_companies()
        self.load_data()
        total = get_company_count()
        self.stats_label.configure(
            text=f"🌱 Added {added} top tech ATS companies! Total pool: {total} companies."
        )
        if hasattr(self.parent_win, "status_label"):
            self.parent_win.status_label.configure(
                text=f"🌱 Seeded {added} top tech companies. Total pool: {total} companies."
            )


    def on_clear_all_clicked(self):
        count = len(self.all_companies)
        if count == 0:
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Confirm Delete All Companies")
        dialog.geometry("400x170")
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)

        ctk.CTkLabel(
            dialog,
            text=f"Delete ALL {count} saved companies from scraping pool?\nExisting scraped jobs will remain untouched.",
            wraplength=360
        ).pack(pady=20)

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20)

        def do_delete_all():
            deleted = clear_all_companies()
            self.load_data()
            if hasattr(self.parent_win, "status_label"):
                self.parent_win.status_label.configure(
                    text=f"🗑 Cleared all {deleted} companies from scraping pool. Jobs kept."
                )
            dialog.destroy()

        ctk.CTkButton(btn_frame, text="Cancel", width=100, command=dialog.destroy).pack(side="left", expand=True)
        ctk.CTkButton(btn_frame, text="🗑 Delete All", width=110, fg_color="#b32929", hover_color="#8a1f1f", command=do_delete_all).pack(side="right", expand=True)

    def confirm_delete_company(self, comp):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Confirm Delete")
        dialog.geometry("380x160")
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)

        name = comp.get("name") or comp.get("identifier")
        ats = (comp.get("ats_platform") or "").upper()
        ctk.CTkLabel(
            dialog,
            text=f"Remove '{name}' ({ats})?\nThis deletes the company from your scraping pool.",
            wraplength=340
        ).pack(pady=20)

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20)

        def do_delete():
            delete_company(comp["id"])
            self.load_data()
            if hasattr(self.parent_win, "status_label"):
                cnt = get_company_count()
                self.parent_win.status_label.configure(
                    text=f"Removed '{name}'. ({cnt} companies remaining)"
                )
            dialog.destroy()

        ctk.CTkButton(btn_frame, text="Cancel", width=100, command=dialog.destroy).pack(side="left", expand=True)
        ctk.CTkButton(btn_frame, text="🗑 Delete", width=100, fg_color="#b32929", hover_color="#8a1f1f", command=do_delete).pack(side="right", expand=True)


if __name__ == "__main__":
    app = JobAppWindow()
    app.mainloop()
