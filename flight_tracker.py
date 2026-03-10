import json
import re
from playwright.sync_api import sync_playwright

URL = "https://www.southwest.com/air/booking/select.html?originationAirportCode=PHX&destinationAirportCode=MSY&departureDate=2026-10-10&returnDate=2026-10-14&passengerType=ADULT&adultPassengersCount=1"

PRICE_FILE = "lowest_price.json"


def get_price():

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        print("Opening Southwest page...")

        page.goto("https://www.southwest.com")

        # wait for page
        page.wait_for_timeout(3000)

        # handle cookie popup
        try:
            page.get_by_role("button", name="Dismiss").click(timeout=5000)
            print("Cookie popup dismissed")
        except:
            print("No cookie popup")

        # ORIGIN
        origin = page.locator("input[aria-describedby*='depart*']")
        origin.click()
        origin.fill("PHX")
        page.keyboard.press("Enter")

        page.wait_for_timeout(1000)
        page.keyboard.press("Enter")

        # DESTINATION
        dest = page.locator("input[aria-describedby*='arrive']")
        dest.click()
        dest.fill("MSY")
        page.keyboard.press("Enter")

        page.wait_for_timeout(1000)
        page.keyboard.press("Enter")

        # DEPART DATE
        depart = page.locator("input[name='departureDate']").first
        depart.click()
        depart.fill("10/10/2026")

        # RETURN DATE
        ret = page.locator("input[name='returnDate']").first
        ret.click()
        ret.fill("10/14/2026")

        print("Flight fields filled")

        # press search
        search_btn = page.locator("button:has-text('Search')")
        search_btn.scroll_into_view_if_needed()
        search_btn.click()

        print("Search pressed")

        # wait for fares to load
        page.wait_for_selector("text=$")

        html = page.content()

        prices = re.findall(r"\$(\d+)", html)

        browser.close()

        valid_prices = [int(p) for p in prices if int(p) > 50]

        if valid_prices:
            return min(valid_prices)
        else:
            return None

def load_lowest():

    try:
        with open(PRICE_FILE) as f:
            return json.load(f)["lowest"]
    except:
        return 9999


def save_lowest(price):

    with open(PRICE_FILE, "w") as f:
        json.dump({"lowest": price}, f)


def main():

    current_price = get_price()

    if current_price is None:
        print("Price not found")
        return

    lowest_price = load_lowest()

    print("Current price:", current_price)
    print("Lowest recorded:", lowest_price)

    if current_price < lowest_price:

        print("NEW LOW PRICE FOUND!")

        save_lowest(current_price)

    else:

        print("No new low price.")


if __name__ == "__main__":
    main()