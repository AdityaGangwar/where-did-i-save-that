from __future__ import annotations

import json
import os
import queue
import sqlite3
import sys
import threading
import time
import ctypes
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from visual_search import IMAGE_EXTENSIONS, ImageSearchEngine

APP_NAME = "Where Did I Save That?"
APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "WhereDidISaveThat"
DB_PATH = APP_DIR / "index.sqlite3"
CONFIG_PATH = APP_DIR / "settings.json"


def get_pictures_directory() -> Path:
    if os.name == "nt":
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x27, None, 0, buffer)
            if result == 0 and buffer.value:
                return Path(buffer.value)
        except (AttributeError, OSError):
            pass
    return Path.home() / "Pictures"


PHOTOS_DIR = get_pictures_directory() / "Photos"
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 1_000_000
PHOTOS_SHORTCUT_NAME = "Photos - Where Did I Save That.lnk"
SUPPORTED = {
    ".txt", ".md", ".rst", ".csv", ".tsv", ".json", ".xml", ".html", ".htm",
    ".log", ".py", ".js", ".ts", ".css", ".yaml", ".yml", ".ini", ".toml",
    ".pdf", ".docx", ".xlsx", ".pptx",
}


def asset_path(relative_path: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / relative_path


def load_config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {"folders": data.get("folders", []), "excluded": data.get("excluded", [])}
    except (OSError, json.JSONDecodeError, AttributeError):
        return {"folders": [], "excluded": []}


def save_config(config: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def ensure_photos_folder_and_shortcut() -> None:
    """Create the standard photo folder and a desktop shortcut to it."""
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        desktop = Path(shell.SpecialFolders("Desktop"))
        shortcut_path = desktop / PHOTOS_SHORTCUT_NAME
        if shortcut_path.exists():
            return
        shortcut = shell.CreateShortcut(str(shortcut_path))
        shortcut.TargetPath = str(PHOTOS_DIR)
        shortcut.WorkingDirectory = str(PHOTOS_DIR)
        shortcut.Description = "Open the photo folder searched by Where Did I Save That?"
        shortcut.IconLocation = f"{os.environ.get('SystemRoot', r'C:\Windows')}\\system32\\imageres.dll,4"
        shortcut.Save()
    finally:
        pythoncom.CoUninitialize()


def connect_db() -> sqlite3.Connection:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY, name TEXT NOT NULL, extension TEXT NOT NULL,
        modified REAL NOT NULL, size INTEGER NOT NULL, content TEXT NOT NULL,
        indexed_at REAL NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_files_name ON files(name COLLATE NOCASE)")
    db.execute("""CREATE TABLE IF NOT EXISTS images (
        path TEXT PRIMARY KEY, modified REAL NOT NULL, size INTEGER NOT NULL,
        embedding BLOB NOT NULL, indexed_at REAL NOT NULL)""")
    return db


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in {".pdf"}:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            chunks = []
            for page in reader.pages[:300]:
                chunks.append(page.extract_text() or "")
            return "\n".join(chunks)[:MAX_TEXT_CHARS]
        if suffix == ".docx":
            from docx import Document
            doc = Document(str(path))
            chunks = [p.text for p in doc.paragraphs]
            for table in doc.tables:
                for row in table.rows:
                    chunks.append(" ".join(cell.text for cell in row.cells))
            return "\n".join(chunks)[:MAX_TEXT_CHARS]
        if suffix == ".xlsx":
            from openpyxl import load_workbook
            book = load_workbook(path, read_only=True, data_only=True)
            chunks = []
            for sheet in book.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    values = [str(value) for value in row if value is not None]
                    if values:
                        chunks.append(" ".join(values))
                    if sum(map(len, chunks)) >= MAX_TEXT_CHARS:
                        break
                if sum(map(len, chunks)) >= MAX_TEXT_CHARS:
                    break
            book.close()
            return "\n".join(chunks)[:MAX_TEXT_CHARS]
        if suffix == ".pptx":
            from pptx import Presentation
            presentation = Presentation(str(path))
            chunks = []
            for slide in presentation.slides:
                for shape in slide.shapes:
                    if getattr(shape, "has_text_frame", False):
                        chunks.append(shape.text)
            return "\n".join(chunks)[:MAX_TEXT_CHARS]
        if suffix in SUPPORTED:
            return path.read_text(encoding="utf-8", errors="replace")[:MAX_TEXT_CHARS]
    except Exception:
        return ""
    return ""


class SearchApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")
        self.title(APP_NAME)
        try:
            self.iconbitmap(default=str(asset_path("assets/app.ico")))
        except tk.TclError:
            pass
        self.geometry("1050x700")
        self.minsize(820, 560)
        self.config_data = load_config()
        # Give new users a ready-to-use place for photos and keep it searchable by default.
        self.photos_setup_error = None
        try:
            ensure_photos_folder_and_shortcut()
        except Exception as exc:
            self.photos_setup_error = str(exc)
        photos_path = str(PHOTOS_DIR.resolve())
        if photos_path not in self.config_data["folders"]:
            self.config_data["folders"].append(photos_path)
            save_config(self.config_data)
        self.events: queue.Queue = queue.Queue()
        self.index_thread: threading.Thread | None = None
        self.search_thread: threading.Thread | None = None
        self.cancel_scan = threading.Event()
        self._build_ui()
        self._refresh_folder_list()
        self.after(150, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        header = ctk.CTkFrame(self, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text=APP_NAME, font=ctk.CTkFont(size=25, weight="bold")).grid(
            row=0, column=0, padx=24, pady=(20, 2), sticky="w")
        ctk.CTkLabel(header, text="Search the folders you choose. Your index stays on this PC.",
                     text_color=("#526071", "#aeb9c7")).grid(row=1, column=0, padx=25, pady=(0, 18), sticky="w")

        search = ctk.CTkFrame(self, fg_color="transparent")
        search.grid(row=1, column=0, padx=22, pady=(18, 8), sticky="ew")
        search.grid_columnconfigure(0, weight=1)
        self.query = ctk.CTkEntry(search, height=42, placeholder_text="Search names and file contents…",
                                  font=ctk.CTkFont(size=15))
        self.query.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.query.bind("<Return>", lambda _e: self.search())
        self.search_button = ctk.CTkButton(search, text="Search", width=110, height=42, command=self.search)
        self.search_button.grid(row=0, column=1)
        self.image_mode = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(search, text="Search pictures by description (one-time model download)",
                        variable=self.image_mode, command=self.toggle_image_mode).grid(
                            row=1, column=0, sticky="w", pady=(8, 0))

        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.grid(row=2, column=0, padx=22, pady=(0, 9), sticky="ew")
        self.status = ctk.CTkLabel(tools, text="Add folders, then build your local index.", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(tools, text="Open Photos", width=105, command=self.open_photos_folder).pack(side="left", padx=4)
        ctk.CTkButton(tools, text="Add folder", width=105, command=self.add_folder).pack(side="left", padx=4)
        ctk.CTkButton(tools, text="Remove selected", width=130, fg_color="transparent",
                      border_width=1, command=self.remove_folder).pack(side="left", padx=4)
        self.index_button = ctk.CTkButton(tools, text="Index folders", width=120, command=self.start_index)
        self.index_button.pack(side="left", padx=(4, 0))

        results = ctk.CTkFrame(self, corner_radius=10)
        results.grid(row=3, column=0, padx=22, pady=(0, 8), sticky="nsew")
        results.grid_columnconfigure(0, weight=1)
        results.grid_rowconfigure(0, weight=1)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=31, font=("Segoe UI", 10), borderwidth=0)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#245a91")], foreground=[("selected", "white")])
        columns = ("name", "type", "modified", "location")
        self.tree = ttk.Treeview(results, columns=columns, show="headings", selectmode="browse")
        for key, label, width in [("name", "Name", 260), ("type", "Type", 80),
                                  ("modified", "Modified", 145), ("location", "Location", 450)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=70, stretch=(key == "location"))
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)
        ybar = ttk.Scrollbar(results, orient="vertical", command=self.tree.yview)
        ybar.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=10)
        self.tree.configure(yscrollcommand=ybar.set)
        self.tree.bind("<Double-1>", lambda _e: self.open_selected())
        self.tree.bind("<Return>", lambda _e: self.open_selected())

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, padx=22, pady=(0, 14), sticky="ew")
        self.count_label = ctk.CTkLabel(footer, text="Double-click a result to open it.", anchor="w")
        self.count_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(footer, text="Open result", width=115, command=self.open_selected).pack(side="right")

        self.folder_window = None

    def open_photos_folder(self):
        try:
            PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(PHOTOS_DIR))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Windows could not open the Photos folder.\n\n{exc}")

    def _refresh_folder_list(self):
        count = len(self.config_data["folders"])
        if count:
            self.status.configure(text=f"{count} folder{'s' if count != 1 else ''} selected · index stored privately on this PC")
        else:
            self.status.configure(text="Add folders, then build your local index.")
        if self.photos_setup_error:
            self.status.configure(text="Photos folder is ready · desktop shortcut could not be created; use Open Photos")

    def toggle_image_mode(self):
        if self.image_mode.get():
            self.query.configure(placeholder_text="Describe the picture you want to find…")
            self.status.configure(text="Index folders that contain pictures before searching them.")
        else:
            self.query.configure(placeholder_text="Search names and file contents…")
            self._refresh_folder_list()

    def add_folder(self):
        selected = filedialog.askdirectory(title="Choose a folder to include in search", mustexist=True)
        if selected:
            normalized = str(Path(selected).resolve())
            if normalized not in self.config_data["folders"]:
                self.config_data["folders"].append(normalized)
                save_config(self.config_data)
                self._refresh_folder_list()
                self.status.configure(text=f"Added {Path(normalized).name}. Choose Index folders to scan it.")

    def remove_folder(self):
        if not self.config_data["folders"]:
            messagebox.showinfo(APP_NAME, "There are no selected folders to remove.")
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Selected folders")
        dialog.geometry("620x310")
        dialog.transient(self)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text="Choose a folder to stop searching:", anchor="w").pack(fill="x", padx=16, pady=(16, 8))
        box = tk.Listbox(dialog, height=9, font=("Segoe UI", 10), selectmode="browse")
        for item in self.config_data["folders"]:
            box.insert("end", item)
        box.pack(fill="both", expand=True, padx=16, pady=4)
        def remove():
            chosen = box.curselection()
            if not chosen:
                return
            path = self.config_data["folders"].pop(chosen[0])
            save_config(self.config_data)
            db = connect_db()
            for table in ("files", "images"):
                db.execute(f"DELETE FROM {table} WHERE path = ? OR path LIKE ?",
                           (path, path.rstrip("\\/") + os.sep + "%"))
            db.commit()
            db.close()
            dialog.destroy()
            self._refresh_folder_list()
        ctk.CTkButton(dialog, text="Remove folder and its indexed results", command=remove).pack(pady=12)

    def start_index(self):
        if self.index_thread and self.index_thread.is_alive():
            self.cancel_scan.set()
            self.index_button.configure(text="Stopping…", state="disabled")
            return
        folders = [Path(p) for p in self.config_data["folders"] if Path(p).is_dir()]
        if not folders:
            messagebox.showinfo(APP_NAME, "Add at least one folder before indexing.")
            return
        self.cancel_scan.clear()
        self.index_button.configure(text="Stop indexing", state="normal")
        self.status.configure(text="Preparing to scan selected folders…")
        self.index_thread = threading.Thread(target=self._index_worker, args=(folders,), daemon=True)
        self.index_thread.start()

    def _index_worker(self, folders: list[Path]):
        db = connect_db()
        found: set[str] = set()
        found_images: set[str] = set()
        image_engine = None
        count = 0
        try:
            for root in folders:
                for directory, dirs, files in os.walk(root):
                    if self.cancel_scan.is_set():
                        break
                    dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv", "venv"}]
                    for filename in files:
                        if self.cancel_scan.is_set():
                            break
                        path = Path(directory) / filename
                        suffix = path.suffix.lower()
                        if suffix not in SUPPORTED and suffix not in IMAGE_EXTENSIONS:
                            continue
                        try:
                            stat = path.stat()
                            if stat.st_size > MAX_FILE_BYTES or not path.is_file():
                                continue
                            key = str(path.resolve())
                            if suffix in IMAGE_EXTENSIONS:
                                found_images.add(key)
                                old = db.execute("SELECT modified, size FROM images WHERE path=?", (key,)).fetchone()
                                if old and old == (stat.st_mtime, stat.st_size):
                                    count += 1
                                    continue
                                if image_engine is None:
                                    self.events.put(("model_status", "Preparing picture search model…"))
                                    image_engine = ImageSearchEngine(
                                        APP_DIR / "models" / "clip-vit-base-patch32",
                                        progress=lambda name, done, total: self.events.put(
                                            ("model_progress", name, done, total)))
                                    self.events.put(("model_status", "Picture model ready · indexing images…"))
                                try:
                                    vector = image_engine.embed_image(path)
                                except Exception:
                                    self.events.put(("progress", count, f"Skipped unreadable picture: {path.name}"))
                                    continue
                                db.execute("""INSERT OR REPLACE INTO images
                                    (path,modified,size,embedding,indexed_at) VALUES(?,?,?,?,?)""",
                                           (key, stat.st_mtime, stat.st_size, vector.tobytes(), time.time()))
                                count += 1
                                if count % 25 == 0:
                                    db.commit()
                                    self.events.put(("progress", count, str(path)))
                                continue
                            found.add(key)
                            old = db.execute("SELECT modified, size FROM files WHERE path=?", (key,)).fetchone()
                            if old and old == (stat.st_mtime, stat.st_size):
                                count += 1
                                continue
                            content = extract_text(path)
                            db.execute("""INSERT OR REPLACE INTO files
                                (path,name,extension,modified,size,content,indexed_at) VALUES(?,?,?,?,?,?,?)""",
                                       (key, filename, path.suffix.lower(), stat.st_mtime, stat.st_size, content, time.time()))
                            count += 1
                            if count % 100 == 0:
                                db.commit()
                                self.events.put(("progress", count, str(path)))
                        except (OSError, PermissionError, sqlite3.Error):
                            continue
                if self.cancel_scan.is_set():
                    break
            if not self.cancel_scan.is_set():
                # Remove stale entries only within the folders scanned.
                for root in folders:
                    prefix = str(root.resolve()).rstrip("\\/") + os.sep
                    rows = db.execute("SELECT path FROM files WHERE path LIKE ?", (prefix + "%",)).fetchall()
                    stale = [(row[0],) for row in rows if row[0] not in found]
                    if stale:
                        db.executemany("DELETE FROM files WHERE path=?", stale)
                    rows = db.execute("SELECT path FROM images WHERE path LIKE ?", (prefix + "%",)).fetchall()
                    stale = [(row[0],) for row in rows if row[0] not in found_images]
                    if stale:
                        db.executemany("DELETE FROM images WHERE path=?", stale)
            db.commit()
            self.events.put(("done", count, self.cancel_scan.is_set()))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            db.close()

    def search(self):
        query = self.query.get().strip()
        for row in self.tree.get_children():
            self.tree.delete(row)
        if not query:
            self.count_label.configure(text="Describe a picture or type a word to search.")
            return
        if self.image_mode.get():
            if self.index_thread and self.index_thread.is_alive():
                self.status.configure(text="Wait for indexing to finish before searching pictures.")
                return
            if self.search_thread and self.search_thread.is_alive():
                self.status.configure(text="Picture search is already running…")
                return
            db = connect_db()
            total = db.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            db.close()
            if total == 0:
                self.count_label.configure(text="No pictures are indexed yet.")
                self.status.configure(text="Choose a folder with pictures and select Index folders first.")
                return
            self.count_label.configure(text="Comparing your description with indexed pictures…")
            self.status.configure(text="Picture matching runs locally on this PC.")
            self.search_button.configure(state="disabled")
            self.search_thread = threading.Thread(target=self._image_search_worker, args=(query,), daemon=True)
            self.search_thread.start()
            return
        terms = [term for term in query.split() if len(term) > 1]
        if not terms:
            terms = [query]
        where = " AND ".join("(name LIKE ? OR content LIKE ? OR path LIKE ?)" for _ in terms)
        params = []
        for term in terms:
            pattern = f"%{term}%"
            params.extend([pattern, pattern, pattern])
        db = connect_db()
        rows = db.execute(f"SELECT path,name,extension,modified FROM files WHERE {where} ORDER BY modified DESC LIMIT 500", params).fetchall()
        db.close()
        for path, name, ext, modified in rows:
            try:
                date = time.strftime("%b %d, %Y", time.localtime(modified))
            except (ValueError, OSError):
                date = ""
            self.tree.insert("", "end", values=(name, ext or "File", date, str(Path(path).parent)), tags=(path,))
        self.count_label.configure(text=f"{len(rows)} result{'s' if len(rows) != 1 else ''}" + (" · showing first 500" if len(rows) == 500 else ""))

    def _image_search_worker(self, query: str):
        try:
            import numpy as np
            db = connect_db()
            rows = db.execute("SELECT path, modified, embedding FROM images").fetchall()
            db.close()
            engine = ImageSearchEngine(APP_DIR / "models" / "clip-vit-base-patch32",
                                       progress=lambda name, done, total: self.events.put(
                                           ("model_progress", name, done, total)))
            query_vector = engine.embed_text(query)
            matches = []
            for path, modified, packed in rows:
                if not Path(path).is_file():
                    continue
                vector = np.frombuffer(packed, dtype=np.float32)
                if vector.shape == query_vector.shape:
                    matches.append((float(np.dot(query_vector, vector)), path, modified))
            matches.sort(key=lambda item: item[0], reverse=True)
            self.events.put(("image_results", [(path, modified) for _score, path, modified in matches[:500]], len(matches)))
        except Exception as exc:
            self.events.put(("image_search_error", str(exc)))

    def open_selected(self):
        selected = self.tree.selection()
        if not selected:
            return
        row = self.tree.item(selected[0])
        path = Path(row["values"][3]) / row["values"][0]
        if not path.exists():
            messagebox.showwarning(APP_NAME, "This file may have been moved or deleted. Re-index the folder to update results.")
            return
        try:
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Windows could not open this file.\n\n{exc}")

    def _drain_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    self.status.configure(text=f"Indexed or checked {event[1]:,} files…")
                elif event[0] == "model_status":
                    self.status.configure(text=event[1])
                elif event[0] == "model_progress":
                    percent = min(100, int(event[2] * 100 / max(event[3], 1)))
                    filename = "visual model" if "model_quantized.onnx" in event[1] else "text tokenizer"
                    self.status.configure(text=f"Downloading {filename} · {percent}% (first use only)…")
                elif event[0] == "done":
                    self.index_button.configure(text="Index folders", state="normal")
                    self.status.configure(text=f"Index ready · {event[1]:,} files checked" + (" · stopped" if event[2] else ""))
                elif event[0] == "image_results":
                    for path, modified in event[1]:
                        try:
                            date = time.strftime("%b %d, %Y", time.localtime(modified))
                        except (ValueError, OSError):
                            date = ""
                        self.tree.insert("", "end", values=(Path(path).name, "Picture", date, str(Path(path).parent)), tags=(path,))
                    count = len(event[1])
                    self.count_label.configure(text=f"{count} picture match{'es' if count != 1 else ''} · ordered by similarity" +
                                                (" · showing first 500" if event[2] > 500 else ""))
                    self.status.configure(text="Picture search complete · processed locally")
                    self.search_button.configure(state="normal")
                elif event[0] == "image_search_error":
                    self.count_label.configure(text="Picture search could not complete.")
                    self.status.configure(text="Check your internet connection if the model is still downloading.")
                    self.search_button.configure(state="normal")
                    messagebox.showerror(APP_NAME, f"Picture search could not run:\n\n{event[1]}")
                elif event[0] == "error":
                    self.index_button.configure(text="Index folders", state="normal")
                    messagebox.showerror(APP_NAME, f"Indexing stopped because of an error:\n\n{event[1]}")
        except queue.Empty:
            pass
        self.after(150, self._drain_events)

    def _close(self):
        if self.index_thread and self.index_thread.is_alive():
            self.cancel_scan.set()
        self.destroy()


def main():
    SearchApp().mainloop()


if __name__ == "__main__":
    main()
