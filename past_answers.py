import os
import csv
from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=False)
        page = browser.new_page()
        page.goto("https://www.rockpapershotgun.com/wordle-past-answers")
        items = page.locator("ul.inline > li")
        filename = "past_answers.csv"

        if not os.path.exists(filename):
            with open(filename, mode="w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["word"])  # wrap header in a list!

                for i in range(items.count()):
                    word = items.nth(i).text_content()
                    writer.writerow([word])  # wrap row in a list too

        browser.close()

main()
