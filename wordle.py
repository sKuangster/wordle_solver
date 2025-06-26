from playwright.sync_api import sync_playwright
import csv
import time
import sys
from typing import List, Dict, Set, Tuple

# Constants
WORD_LENGTH = 5
MAX_ATTEMPTS = 6
STARTING_WORD = "slant"
WORD_FILE = "word_frequency.csv"
TIMEOUTS = {
    "page_load": 5000,
    "feedback": 15000,
    "row_ready_check": 10000,  # Max time to wait for row to be ready
    "initial_setup": 2000  # Initial wait after game setup
}

class WordleFilter:
    """Handles word filtering logic based on Wordle feedback."""
    
    def __init__(self):
        self.word_list = self._load_words()
    
    def _load_words(self) -> List[Dict[str, str]]:
        """Load and cache word list once."""
        with open(WORD_FILE, newline='') as f:
            return [row for row in csv.DictReader(f) if len(row["word"]) == WORD_LENGTH]
    
    def filter_words(self, feedback: List[Dict]) -> List[str]:
        """Filter words based on accumulated feedback."""
        if not feedback:
            return [row["word"].lower() for row in self.word_list]
        
        # Pre-process feedback for efficiency
        correct_pos = {f["pos"]: f["letter"] for f in feedback if f["status"] == "correct"}
        present_pairs = [(f["pos"], f["letter"]) for f in feedback if f["status"] == "present"]
        known_letters = {f["letter"] for f in feedback if f["status"] in ["correct", "present"]}
        absent_letters = {f["letter"] for f in feedback 
                         if f["status"] == "absent" and f["letter"] not in known_letters}
        
        filtered = []
        for row in self.word_list:
            word = row["word"].lower()
            
            # Check correct positions
            if any(word[pos] != letter for pos, letter in correct_pos.items()):
                continue
            
            # Check present letters (must be in word but not in guessed position)
            if not all(letter in word and word[pos] != letter 
                      for pos, letter in present_pairs):
                continue
            
            # Check absent letters
            if any(letter in word for letter in absent_letters):
                continue
            
            filtered.append(word)
        
        return list(set(filtered))
    
    def get_best_guess(self, candidates: List[str]) -> str:
        """Get highest frequency word from candidates."""
        if not candidates:
            return ""
        
        word_freq = {row["word"].lower(): float(row["frequency"]) 
                    for row in self.word_list 
                    if row["word"].lower() in candidates}
        
        return max(word_freq, key=word_freq.get) if word_freq else candidates[0]

class WordlePage:
    """Handles Wordle page interactions."""
    
    def __init__(self, page):
        self.page = page
    
    def setup_game(self):
        """Initialize the game page."""
        self.page.goto("https://www.nytimes.com/games/wordle/index.html")
        
        # Click play button with retry
        self._click_play_button()
        self._close_modal()
        
        # Wait for game to be fully loaded and ready
        print("Waiting for game to initialize...")
        self.page.wait_for_timeout(TIMEOUTS["initial_setup"])
    
    def _click_play_button(self):
        """Click the play button with fallback."""
        try:
            self.page.get_by_test_id("Play").click(timeout=TIMEOUTS["page_load"])
        except:
            print("Retrying play button click...")
            self.page.wait_for_timeout(1000)
            self.page.get_by_test_id("Play").click(timeout=TIMEOUTS["page_load"])
    
    def _close_modal(self):
        """Close any modal dialogs."""
        try:
            self.page.locator('svg[data-testid="icon-close"]').wait_for(timeout=TIMEOUTS["page_load"])
            self.page.click('svg[data-testid="icon-close"]')
        except:
            pass  # No modal present
    
    def make_guess(self, word: str, row: int) -> List[Dict]:
        """Make a guess and return feedback."""
        if len(word) != WORD_LENGTH:
            raise ValueError(f"Word must be {WORD_LENGTH} letters")
        
        # Wait for the row to be ready for input
        self._wait_for_row_ready(row)
        
        # Type the word (no delays between keystrokes)
        word = word.upper()
        print(f"Typing word: {word}")
        
        for letter in word:
            self.page.keyboard.press(f"Key{letter}")
        
        # Press Enter immediately
        print("Pressing Enter...")
        self.page.keyboard.press("Enter")
        
        # Wait for and extract feedback
        return self._get_row_feedback(row)
    
    def _wait_for_row_ready(self, row: int):
        """Dynamically wait for the specified row to be ready for input."""
        print(f"Checking if row {row} is ready for input...")
        start_time = time.time()
        
        while time.time() - start_time < TIMEOUTS["row_ready_check"] / 1000:
            try:
                # Check if the row exists and is ready
                row_locator = self.page.locator(f'//div[@aria-label="Row {row}"]')
                row_locator.wait_for(timeout=1000)
                
                # Check if row is empty (ready for input)
                tiles = row_locator.locator('div[data-testid*="tile"]')
                tile_count = tiles.count()
                
                if tile_count >= WORD_LENGTH:
                    # Check if tiles are empty (not filled)
                    empty_tiles = 0
                    for i in range(min(tile_count, WORD_LENGTH)):
                        tile = tiles.nth(i)
                        aria_label = tile.get_attribute("aria-label") or ""
                        data_state = tile.get_attribute("data-state") or ""
                        
                        # Tile is empty if it doesn't have letters or is in 'empty' state
                        if not aria_label or "empty" in data_state.lower() or len(aria_label.split()) <= 2:
                            empty_tiles += 1
                    
                    if empty_tiles >= WORD_LENGTH:
                        print(f"Row {row} is ready for input")
                        return True
                    else:
                        print(f"Row {row} appears to have content, waiting...")
                
                # If row 1 and we can't find proper tiles, try alternative detection
                if row == 1:
                    # For first row, just check if we can focus on the game area
                    game_area = self.page.locator('[data-testid="wordle-app-game"]')
                    if game_area.count() > 0:
                        print(f"Game area found, row {row} should be ready")
                        return True
                
            except Exception as e:
                print(f"Row readiness check attempt failed: {e}")
            
            time.sleep(0.5)  # Wait before next check
        
        print(f"Warning: Row {row} readiness timeout, proceeding anyway")
        return False
    
    def _get_row_feedback(self, row: int) -> List[Dict]:
        """Wait for and extract feedback from a row."""
        self._wait_for_animation(row)
        
        row_locator = self.page.locator(f'//div[@aria-label="Row {row}"]')
        results = []
        
        # Try multiple approaches to get tile feedback
        for i in range(WORD_LENGTH):
            tile_selectors = [
                f'[style*="animation-delay: {i * 100}ms"] > div',
                f'div[data-testid*="tile"]:nth-child({i + 1})',
                f'div:nth-child({i + 1})'
            ]
            
            for selector in tile_selectors:
                try:
                    tile = row_locator.locator(selector)
                    if tile.count() > 0:
                        aria_label = tile.evaluate("el => el.getAttribute('aria-label')")
                        
                        if aria_label:
                            parts = aria_label.split(", ")
                            if len(parts) >= 3:
                                results.append({
                                    "pos": i,
                                    "letter": parts[1].lower(),
                                    "status": parts[2].lower()
                                })
                                break  # Found valid feedback for this position
                except:
                    continue
        
        print(f"Row {row} feedback: {[(r['letter'], r['status']) for r in results]}")
        return results
        """Wait for and extract feedback from a row."""
        self._wait_for_animation(row)
        
        row_locator = self.page.locator(f'//div[@aria-label="Row {row}"]')
        results = []
        
        for i in range(WORD_LENGTH):
            tile = row_locator.locator(f'[style*="animation-delay: {i * 100}ms"] > div')
            aria_label = tile.evaluate("el => el.getAttribute('aria-label')")
            
            if aria_label:
                parts = aria_label.split(", ")
                if len(parts) >= 3:
                    results.append({
                        "pos": i,
                        "letter": parts[1].lower(),
                        "status": parts[2].lower()
                    })
        
        return results
    
    def _wait_for_animation(self, row: int, timeout: int = 15):
        """Wait for row animation to complete."""
        print(f"Waiting for row {row} animation and feedback...")
        start_time = time.time()
        row_locator = self.page.locator(f'//div[@aria-label="Row {row}"]')
        
        while time.time() - start_time < timeout:
            tiles_ready = 0
            all_correct = True
            
            for i in range(WORD_LENGTH):
                try:
                    # Try multiple ways to find the tile
                    tile_selectors = [
                        f'[style*="animation-delay: {i * 100}ms"] > div',
                        f'div[data-testid*="tile"]:nth-child({i + 1})',
                        f'div:nth-child({i + 1})'
                    ]
                    
                    tile = None
                    label = ""
                    
                    for selector in tile_selectors:
                        try:
                            tile = row_locator.locator(selector)
                            if tile.count() > 0:
                                label = tile.get_attribute("aria-label") or ""
                                if label:
                                    break
                        except:
                            continue
                    
                    if label and any(status in label for status in ["absent", "present", "correct"]):
                        tiles_ready += 1
                        if "correct" not in label:
                            all_correct = False
                    
                except Exception as e:
                    continue
            
            if tiles_ready == WORD_LENGTH:
                if all_correct:
                    # Extract the correct word from the tiles
                    correct_word = ""
                    for i in range(WORD_LENGTH):
                        try:
                            for selector in [f'[style*="animation-delay: {i * 100}ms"] > div',
                                           f'div[data-testid*="tile"]:nth-child({i + 1})',
                                           f'div:nth-child({i + 1})']:
                                try:
                                    tile = row_locator.locator(selector)
                                    if tile.count() > 0:
                                        label = tile.get_attribute("aria-label") or ""
                                        if label:
                                            parts = label.split(", ")
                                            if len(parts) >= 2:
                                                correct_word += parts[1].upper()
                                                break
                                except:
                                    continue
                        except:
                            continue
                    
                    print(f"🎉 PUZZLE SOLVED! The word was: {correct_word}")
                    print(f"✅ Solved in {row} attempt{'s' if row > 1 else ''}!")
                    self.page.wait_for_timeout(3000)  # Wait to see the celebration
                    sys.exit(0)
                print(f"Row {row} feedback complete ({tiles_ready} tiles ready)")
                return
            
            time.sleep(0.3)
        
        raise TimeoutError(f"Timeout waiting for row {row} feedback (only {tiles_ready} tiles ready)")

def main():
    """Main game loop."""
    word_filter = WordleFilter()
    all_feedback = []
    
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=False)
        page = browser.new_page()
        wordle_page = WordlePage(page)
        
        print("🎯 Starting Wordle solver...")
        wordle_page.setup_game()
        
        # First guess is always the starting word
        print(f"Making first guess: {STARTING_WORD}")
        feedback = wordle_page.make_guess(STARTING_WORD, 1)
        all_feedback.extend(feedback)
        
        # Subsequent guesses based on filtering
        for row in range(2, MAX_ATTEMPTS + 1):
            candidates = word_filter.filter_words(all_feedback)
            
            if not candidates:
                print(f"❌ No valid words remaining at row {row}")
                break
            
            best_word = word_filter.get_best_guess(candidates)
            print(f"Row {row}: {len(candidates)} candidates, guessing '{best_word}'")
            
            feedback = wordle_page.make_guess(best_word, row)
            all_feedback.extend(feedback)
            
            # Debug output
            print(f"Feedback: {[(f['letter'], f['status']) for f in feedback]}")
        
        print("🏁 Game completed. Waiting before closing...")
        page.wait_for_timeout(10000)
        browser.close()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⏹ Game interrupted by user")
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)