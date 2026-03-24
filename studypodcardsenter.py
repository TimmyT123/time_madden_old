# goto studypod.app and sign in with google
# goto https://studypod.app/decks/181138/manage
# run this click on front card and this will enter the cards for you

import pyautogui
import time

# time.sleep(5)
# print(pyautogui.position())
# quit()

print("Click into the FRONT field...")
time.sleep(5)

FRONT_X = 2221
FRONT_Y = 405

# 👇 Adjust these coordinates to your Save button
SAVE_BUTTON_X = 2959
SAVE_BUTTON_Y = 667

FILE_PATH = "cards.txt"

# ===== LOAD CARDS =====
cards = []

with open(FILE_PATH, "r", encoding="utf-8") as f:
    for line in f:
        if "::" in line:
            front, back = line.strip().split("::", 1)
            cards.append((front, back))

for i, (front, back) in enumerate(cards, start=1):
    print(f"Adding card {i}/{len(cards)}")

    # Make sure we're in the front field
    pyautogui.click(FRONT_X, FRONT_Y)
    time.sleep(0.2)

    pyautogui.write(front, interval=0.01)
    pyautogui.press('tab')
    pyautogui.write(back, interval=0.01)

    # Click Save instead of pressing Enter
    pyautogui.click(SAVE_BUTTON_X, SAVE_BUTTON_Y)
    # button = pyautogui.locateOnScreen('save_button.png')
    # pyautogui.click(button)

    time.sleep(7)

print("Done!")