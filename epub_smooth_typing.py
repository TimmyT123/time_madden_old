import tkinter as tk
from tkinter import simpledialog, messagebox
from ebooklib import epub, ITEM_DOCUMENT
from bs4 import BeautifulSoup
import json
import os
import re
import time

"""
EPUB Smooth Typing Reader — now with on-the-fly quizzes.

What’s new:
- After each "screen" (MAX_LINES * CHARS_PER_LINE), you’ll be quizzed on the last screen’s content.
- Press "q" anytime to quiz yourself on what’s currently visible.
- Simple cloze-style questions (fill in the blank). Instant feedback + score summary.
- Quiz history saved in quiz_history.json per book.

Lightweight: no extra NLP libraries; just regex + heuristics.
"""

# === Config ===
BOOK_ID = "How_to_Win_Friends_and_Influence_People"
EPUB_PATH = "How_to_Win_Friends_and_Influence_People.epub"
SAVE_FILE = "save_state.json"
QUIZ_HISTORY_FILE = "quiz_history.json"
MAX_LINES = 15
CHARS_PER_LINE = 90

# Typing delays (in ms)
delay_speaking = 50
delay_typing   = 150
delay_reading  = 25

# Quiz behavior
QUESTIONS_PER_SCREEN = 3  # how many questions after each screenful
MIN_SENTENCE_LEN = 60     # ignore very short sentences for questions

# --- Helpers for "pages" (screenfuls) ---
SCREEN_CHARS = MAX_LINES * CHARS_PER_LINE


def index_to_page(idx: int) -> int:
    return max(1, idx // SCREEN_CHARS + 1)


def page_to_index(page: int) -> int:
    return max(0, (page - 1) * SCREEN_CHARS)


# === Extract Text from EPUB ===
def extract_text_from_epub(file_path):
    book = epub.read_epub(file_path)
    full_text = ""
    for item in book.get_items():
        if item.get_type() == ITEM_DOCUMENT:
            soup = BeautifulSoup(item.get_content(), 'html.parser')
            # Remove scripts/styles
            for bad in soup(["script", "style"]):
                bad.decompose()
            text = soup.get_text(" ").strip()
            # normalize whitespace
            text = re.sub(r"\s+", " ", text)
            if len(text) > 50:
                full_text += text + " "
    return full_text.strip()


# === Save/Load Progress ===
def load_saved_index():
    try:
        with open(SAVE_FILE, "r") as f:
            data = json.load(f)
            return data.get(BOOK_ID, 0)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0


def save_current_index(index):
    try:
        data = {}
        if os.path.exists(SAVE_FILE):
            with open(SAVE_FILE, "r") as f:
                data = json.load(f)
        data[BOOK_ID] = index
        with open(SAVE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Error saving state: {e}")


# === Quiz Helpers ===
_sentence_splitter = re.compile(r"(?<=[.!?])\s+")
_word_re = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")


def split_sentences(text: str):
    # basic sentence split; keeps punctuation with sentence
    parts = _sentence_splitter.split(text.strip()) if text else []
    # restore trailing punctuation if split removed it
    return [s.strip() for s in parts if s and len(s.strip()) > 0]


def pick_blank_word(sentence: str):
    """Pick a good word to blank out. Prefer capitalized non-first words; fallback to longest word."""
    words = list(_word_re.finditer(sentence))
    if not words:
        return None

    # Prefer capitalized, non-first word that isn't at the very beginning of a sentence
    for m in words[1:]:
        w = m.group(0)
        if w[0].isupper() and len(w) > 3:
            return m

    # otherwise choose the longest word (>=5 chars)
    words_sorted = sorted(words, key=lambda m: len(m.group(0)), reverse=True)
    for m in words_sorted:
        if len(m.group(0)) >= 5:
            return m

    return words_sorted[0]


def make_cloze_question(sentence: str):
    m = pick_blank_word(sentence)
    if not m:
        return None
    answer = m.group(0)
    # create a blank with same length, use underscores
    blank = "_" * max(4, min(len(answer), 14))
    s = sentence[:m.start()] + blank + sentence[m.end():]
    return {"question": s, "answer": answer}


def generate_questions_from_text(chunk: str, count: int):
    # Choose sentences of reasonable length and variety
    sentences = [s for s in split_sentences(chunk) if len(s) >= MIN_SENTENCE_LEN]
    # de-duplicate similar sentences by first 40 chars
    seen = set()
    filtered = []
    for s in sentences:
        key = s[:40].lower()
        if key not in seen:
            seen.add(key)
            filtered.append(s)

    # Prefer later sentences (recency) but include mix
    pool = filtered[-(count * 4 + 8):] if filtered else []

    questions = []
    for s in pool:
        q = make_cloze_question(s)
        if q:
            questions.append(q)
        if len(questions) >= count:
            break

    # Fallback: if not enough, loosen constraints
    if len(questions) < count:
        for s in sentences:
            if any(q["question"] == s for q in questions):
                continue
            q = make_cloze_question(s)
            if q:
                questions.append(q)
            if len(questions) >= count:
                break

    return questions[:count]


def save_quiz_history(book_id: str, page_from: int, page_to: int, results: list):
    entry = {
        "timestamp": int(time.time()),
        "book_id": book_id,
        "page_from": page_from,
        "page_to": page_to,
        "results": results,  # list of {question, answer, user, correct}
    }
    try:
        history = []
        if os.path.exists(QUIZ_HISTORY_FILE):
            with open(QUIZ_HISTORY_FILE, "r") as f:
                history = json.load(f)
        history.append(entry)
        with open(QUIZ_HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"Error saving quiz history: {e}")


# === Typing GUI ===
class TypingEPUBReader:
    def __init__(self, root, text, speed_delay_ms, start_index=0):
        self.root = root
        self.text = text
        self.index = start_index
        self.speed_delay = speed_delay_ms
        self.display_text = ""
        self.paused = False
        self.last_screen_text = ""  # text from the previous full screen
        self.screen_start_index = start_index

        self.display_label = tk.Label(
            root, text="", font=("Courier", 20), wraplength=1400, justify="left", anchor="nw"
        )
        self.display_label.pack(padx=20, pady=20, fill="both", expand=True)

        self.typing_entry = tk.Entry(root, font=("Courier", 16), width=120)
        self.typing_entry.pack(pady=10)

        # Bind spacebar for pause/resume, 'q' for quiz-now
        self.root.bind("<space>", self.toggle_pause)
        self.root.bind("q", self.quiz_now)

        self.root.protocol("WM_DELETE_WINDOW", self.on_quit)

        if self.text and self.index < len(self.text):
            self.root.after(100, self.show_next_letter)
        else:
            self.display_label.config(text="No readable content found or end of book reached.")

    def toggle_pause(self, event=None):
        self.paused = not self.paused

    def quiz_now(self, event=None):
        # quiz on what's currently on screen
        if not self.display_text.strip():
            return
        self._run_quiz(self.display_text, index_to_page(self.screen_start_index), index_to_page(self.index))

    def _run_quiz(self, chunk: str, page_from: int, page_to: int):
        self.paused = True
        questions = generate_questions_from_text(chunk, QUESTIONS_PER_SCREEN)
        if not questions:
            messagebox.showinfo("Quiz", "Not enough material yet to generate questions.")
            self.paused = False
            return

        results = []
        correct = 0
        for i, q in enumerate(questions, 1):
            prompt = f"Q{i}: Fill in the blank\n\n{q['question']}\n\nYour answer:"
            user = simpledialog.askstring("Quiz", prompt, parent=self.root)
            if user is None:
                user = ""  # treat cancel as blank
            is_ok = user.strip().lower() == q["answer"].strip().lower()
            if is_ok:
                correct += 1
                messagebox.showinfo("Correct!", f"Nice! Answer: {q['answer']}")
            else:
                messagebox.showwarning("Answer", f"Answer: {q['answer']}\nYou wrote: {user}")
            results.append({"question": q["question"], "answer": q["answer"], "user": user, "correct": is_ok})

        save_quiz_history(BOOK_ID, page_from, page_to, results)
        messagebox.showinfo("Quiz Summary", f"You got {correct} / {len(questions)} correct.")
        self.paused = False

    def show_next_letter(self):
        if self.paused:
            self.root.after(100, self.show_next_letter)
            return

        if self.index < len(self.text):
            letter = self.text[self.index]
            self.display_text += letter
            self.index += 1

            # if we've filled a screen, quiz on it and then clear
            if len(self.display_text) // CHARS_PER_LINE >= MAX_LINES:
                chunk = self.display_text
                page_from = index_to_page(self.screen_start_index)
                page_to = index_to_page(self.index)
                self._run_quiz(chunk, page_from, page_to)
                # prepare next screen
                self.display_text = ""
                self.screen_start_index = self.index

            self.display_label.config(text=self.display_text)
            self.root.after(self.speed_delay, self.show_next_letter)
        else:
            self.display_label.config(text=self.display_text + "\n\n[End of book reached]")
            # Final end-of-book quiz on remaining text
            if self.display_text.strip():
                self._run_quiz(self.display_text, index_to_page(self.screen_start_index), index_to_page(self.index))

    def on_quit(self):
        save_current_index(self.index)
        self.root.destroy()


# === Run the App ===
if __name__ == "__main__":
    # Invisible root for dialogs
    dialog_root = tk.Tk()
    dialog_root.withdraw()

    # Mode picker
    choice = simpledialog.askstring(
        title="Select Mode",
        prompt="Press '1' for Typing (slower), '2' for Speaking, or '3' for Reading (fast):",
        parent=dialog_root
    )
    if choice == '2':
        delay = delay_speaking; mode_name = 'Speaking'
    elif choice == '3':
        delay = delay_reading;  mode_name = 'Reading'
    else:
        delay = delay_typing;   mode_name = 'Typing'

    # Load text and compute pages
    text = extract_text_from_epub(EPUB_PATH)
    saved_idx = load_saved_index()
    default_page = index_to_page(saved_idx)
    total_pages = max(1, (len(text) + SCREEN_CHARS - 1) // SCREEN_CHARS)

    # Ask what page to start on (Cancel = resume)
    start_page = simpledialog.askinteger(
        title="Start Page",
        prompt=f"Enter page to start (1–{total_pages}).\nClick Cancel to resume at page {default_page}.",
        parent=dialog_root,
        minvalue=1, maxvalue=total_pages, initialvalue=default_page
    )
    if start_page is None:
        start_index = saved_idx  # resume
    else:
        start_index = min(page_to_index(start_page), len(text) - 1)

    dialog_root.destroy()

    root = tk.Tk()
    root.title(f"EPUB Reader - {mode_name} Mode")
    root.geometry("1500x700")
    app = TypingEPUBReader(root, text, delay, start_index=start_index)
    root.mainloop()
