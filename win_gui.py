import os
import json
import subprocess
import threading
import webbrowser
import html
from datetime import datetime, timezone, timedelta
import customtkinter as ctk

from db import get_conn, init_db, load_db, save_db, delete_jobs_by_rank, clear_all_jobs, update_job

DB_FILE = "jobs_db.sqlite"
STATUSES = ["New", "Applied", "Ongoing", "Rejected", "NA"]
RANKS = ["HIGH", "LOW", "IGNORE", "ERROR", "UNKNOWN"]
NA_EXPIRY_HOURS = 24

RANK_COLORS = {
    "HIGH": "#1a531b",   # green
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

        self.refresh_btn = ctk.CTkButton(self.header_frame, text="Refresh Everything", command=self.on_refresh_clicked)
        self.refresh_btn.pack(side="left", padx=5)

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
        
        self.reset_rank_btn = ctk.CTkButton(self.prio_header, text="Reset Ranks", command=self.on_reset_rank_clicked, fg_color="#b32929", hover_color="#8a1f1f")
        self.reset_rank_btn.pack(side="left", padx=5)
        
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
            weights = {"HIGH": 0, "LOW": 1, "UNKNOWN": 2, "ERROR": 3, "IGNORE": 4}
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

        target_listbox = self.get_listbox_for_status(status)

        row_frame = ctk.CTkFrame(target_listbox, corner_radius=5)
        row_frame.pack(fill="x", pady=2, padx=2)

        color = RANK_COLORS.get(rank, RANK_COLORS["UNKNOWN"])
        rank_label = ctk.CTkLabel(row_frame, text=f" {rank} ", fg_color=color, corner_radius=5, font=("Arial", 12, "bold"))
        rank_label.pack(side="left", padx=10, pady=8)

        title_btn = ctk.CTkButton(row_frame, text=title, fg_color="transparent", hover_color="#2b2b2b", anchor="w", command=lambda: self.on_job_row_clicked(data, link))
        title_btn.pack(side="left", fill="x", expand=True, padx=5)

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
        self.page_offsets[status] += self.page_size
        self.refresh_ui()

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
        dialog.title("Confirm")
        dialog.geometry("300x150")
        dialog.attributes("-topmost", True)
        
        ctk.CTkLabel(dialog, text="Clear Entire Database?\nThis cannot be undone.").pack(pady=20)
        
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x")
        
        def confirm():
            deleted = clear_all_jobs()
            self.jobs_db.clear()
            self.refresh_ui()
            self.status_label.configure(text=f"💣 Cleared {deleted} jobs from the database.")
            dialog.destroy()
            
        ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy).pack(side="left", padx=10, expand=True)
        ctk.CTkButton(btn_frame, text="Clear", fg_color="#b32929", command=confirm).pack(side="right", padx=10, expand=True)

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
        try:
            venv_python = os.path.join(os.getcwd(), "scraper_env", "Scripts", "python.exe")
            if not os.path.exists(venv_python):
                venv_python = "python"
                
            process = subprocess.Popen([venv_python, script_name], cwd=os.getcwd())
            
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

    def on_sort_clicked(self):
        if not os.path.exists(DB_FILE):
            self.sort_status_label.configure(text=f"No {DB_FILE}. Scrape first!")
            return
            
        self.sort_status_label.configure(text="Initializing Groq AI analysis... Check terminal!")
        self.sort_btn.configure(state="disabled")
        thread = threading.Thread(target=self.run_sorter)
        thread.daemon = True
        thread.start()

    def run_sorter(self):
        try:
            venv_python = os.path.join(os.getcwd(), "scraper_env", "Scripts", "python.exe")
            if not os.path.exists(venv_python):
                venv_python = "python"
                
            process = subprocess.Popen([venv_python, "rank_internships.py"], cwd=os.getcwd())
            process.wait()
            self.after(0, lambda: self.on_sort_finished(True))
        except Exception as e:
            print(f"Error running sorter: {e}")
            self.after(0, lambda: self.on_sort_finished(False))

    def on_sort_finished(self, success):
        self.sort_btn.configure(state="normal")
        if success:
            self.sort_status_label.configure(text="Ranking finished!")
            self.jobs_db = load_db()
            self.refresh_ui()
        else:
            self.sort_status_label.configure(text="Ranking script failed.")

if __name__ == "__main__":
    app = JobAppWindow()
    app.mainloop()
