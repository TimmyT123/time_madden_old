import json
from tkinter import *
from tkinter import ttk, messagebox
from pathlib import Path
from datetime import datetime

DATA_FILE = Path("commish_logs.json")


def load_logs():
    if not DATA_FILE.exists():
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_logs(logs):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2)


logs = load_logs()


# ---------- UI ----------
root = Tk()
root.title("WURD Commish Logs")
root.geometry("900x500")

# Top Frame
top = Frame(root)
top.pack(fill=X, padx=10, pady=5)

Label(top, text="Search").pack(side=LEFT)
search_var = StringVar()
Entry(top, textvariable=search_var, width=30).pack(side=LEFT, padx=5)


def refresh():
    tree.delete(*tree.get_children())
    q = search_var.get().lower()

    for e in logs:
        blob = json.dumps(e).lower()
        if q in blob:
            tree.insert("", END, values=(
                e["id"], e["season"], e["date"], e["week"], ",".join(e["teams"]),
                e["issue"], e["ruling"]
            ))


Button(top, text="Search", command=refresh).pack(side=LEFT, padx=5)
Button(top, text="Clear", command=lambda: search_var.set("")).pack(side=LEFT)

# Table
cols = ("ID", "Season", "Date", "Week", "Teams", "Issue", "Ruling")
tree = ttk.Treeview(root, columns=cols, show="headings")

for c in cols:
    tree.heading(c, text=c)
    tree.column(c, width=140)

tree.pack(fill=BOTH, expand=True, padx=10, pady=10)


# ---------- Buttons ----------
btns = Frame(root)
btns.pack(pady=5)

def add_entry():
    win = Toplevel(root)
    win.title("Add Entry")

    fields = ["Season", "Week", "Teams", "Users", "Issue", "Details", "Result", "Ruling", "Rule", "Commissioner", "Notes"]
    entries = {}

    for i, f in enumerate(fields):
        Label(win, text=f).grid(row=i, column=0)
        e = Entry(win, width=40)
        e.grid(row=i, column=1)
        entries[f] = e

    def save():
        date = datetime.now().strftime("%Y-%m-%d")
        entry = {
            "id": f"{date}-{len(logs) + 1:03d}",
            "season": entries["Season"].get(),
            "date": date,
            "week": entries["Week"].get(),
            "teams": entries["Teams"].get().split(","),
            "users": entries["Users"].get().split(","),
            "issue": entries["Issue"].get(),
            "details": entries["Details"].get(),
            "result": entries["Result"].get(),
            "ruling": entries["Ruling"].get(),
            "rule": entries["Rule"].get(),
            "commissioner": entries["Commissioner"].get(),
            "notes": entries["Notes"].get()
        }

        logs.append(entry)
        save_logs(logs)
        refresh()
        win.destroy()

    Button(win, text="Save", command=save).grid(row=len(fields), column=1)


def delete_entry():
    selected = tree.focus()
    if not selected:
        return

    vals = tree.item(selected)["values"]
    log_id = vals[0]

    if messagebox.askyesno("Delete", "Delete this log?"):
        global logs
        logs = [e for e in logs if e["id"] != log_id]
        save_logs(logs)
        refresh()


Button(btns, text="Add Log", command=add_entry).pack(side=LEFT, padx=10)
Button(btns, text="Delete Selected", command=delete_entry).pack(side=LEFT)

refresh()
root.mainloop()
