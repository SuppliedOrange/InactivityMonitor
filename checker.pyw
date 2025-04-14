import threading
import logging
import sys
from time import sleep, time
import win32gui
import win32api
import pygetwindow as gw
from multiprocessing import Process
import pynput
import tkinter as tk
from tkextrafont import Font
from random import randint
from PIL import Image, ImageTk
import math
import psutil
import pystray
import pywintypes
import os
import subprocess
import json

# --------------------- Logging Configuration ---------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("inactivity_monitor.log", mode="w"),
        logging.StreamHandler(sys.stdout)
    ],
)
# -----------------------------------------------------------------

# --------------------- Configuration Constants ---------------------
# How long to wait between each iteration (in seconds)
# Keep this low, because it affects how long it takes to close the app
ITERATION_DELAY = 1
# How many seconds to wait before locking 
IDLE_LIMIT = 120

# Internal counter for idle time      
CURRENT_IDLE = 0
# Internal character memory for unlock combination
CHARACTER_MEMORY = []

# Modules to enable/disable
KEYBOARD_BLOCK = True
MOUSE_BLOCK = True
TASK_MANAGER_KILLER = True
KOMOREBI_INTEGRATION_ENABLED = True

# Options

# If True, the program will not lock the computer if the windows OS is locked
IGNORE_WHEN_WINDOWS_LOCKED = True

""" Komorebi specific options """

# When there are full-screen applications, do not assume komorebi idling
IGNORE_FULLSCREEN_APPLICATIONS = True 
# For the above parameter, this also requires it to be focused
ONLY_IGNORE_FOCUSED_FULLSCREEN_APPLICATIONS = True

# Applications that aren't considered full-screen even if they are
IGNORED_FULLSCREEN_TITLES = [
    "NVIDIA GeForce Overlay DT",
    "Task Switching",
] # You can determine which ones to ignore by running `fullscreen_apps.py`

"""
Make sure your unlock combination works! See the pynput.Key objects for special keys.
Use NON_LETHAL mode to test that your unlock combination actually works before deploying
"""
# Unlock combination and mode
UNLOCK_COMBINATION = ["o", "p", "p", "o"]
NON_LETHAL = False
# -----------------------------------------------------------------

def is_windows_locked():
    """ Check if the windows OS is locked """
    for proc in psutil.process_iter():
        if(proc.name() == "LogonUI.exe"):
            return True
    return False
def is_komorebi_running():
    """Check if komorebi.exe is running."""
    for process in psutil.process_iter(['name']):
        if process.info['name'] == 'komorebi.exe':
            return True
    return False

def is_komorebic_in_path():
    """Checks if 'komorebic' is in the system's PATH."""
    return any(
        os.path.exists(os.path.join(path, 'komorebic.exe')) for path in os.environ["PATH"].split(os.pathsep)
    )

def has_fullscreen_applications_running(ensure_focused:bool = ONLY_IGNORE_FOCUSED_FULLSCREEN_APPLICATIONS):
    """
    Returns a list of fullscreen applications that are optionally in focus.
    """

    if not IGNORE_FULLSCREEN_APPLICATIONS: return []

    screen_width = win32api.GetSystemMetrics(0)  # Screen width
    screen_height = win32api.GetSystemMetrics(1)  # Screen height

    fullscreen_applications = []

    # Get the handle of the currently focused window
    focused_window = win32gui.GetForegroundWindow()

    for window in gw.getAllWindows():

        # Check if the window matches the screen size
        if window.isMaximized or (window.width == screen_width and window.height == screen_height):

            if (
                    # Window is in focus?
                    ensure_focused and
                    window._hWnd == focused_window
                ) and (
                    # Window title is not empty?
                    window.title
                ) and (
                    # Window title is not in the ignored list?
                    all(ignored not in window.title for ignored in IGNORED_FULLSCREEN_TITLES)
                ):
                
                # Valid fullscreen application
                fullscreen_applications.append(window.title)

    return fullscreen_applications

def is_komorebi_workspace_idle():
    """
    Checks if the current komorebi workspace is idle (no windows).
    Returns:
        True if the workspace is idle, False otherwise.
    """

    komorebi_status = is_komorebi_running()
    komorebic_status = is_komorebic_in_path()
    
    if not komorebi_status or not komorebic_status:

        komorebi_message = f"komorebi is { 'running' if komorebi_status else 'not running' }"
        komorebic_message = f"komorebic is { 'in' if komorebic_status else 'not in' } PATH"
        
        error_message = f"{komorebi_message} and {komorebic_message}. Idle state cannot be determined."
                
        logging.error(error_message)
        return False  # komorebi not running, thus not idle

    try:

        result = subprocess.run(['komorebic', 'state'], capture_output=True, text=True, check=True)
        state = json.loads(result.stdout)

        monitors = state.get("monitors", {}).get("elements", [])

        if monitors:

            focused_monitor_index = state.get("monitors", {}).get("focused")
            focused_monitor = monitors[focused_monitor_index]
            workspaces = focused_monitor.get("workspaces", {}).get("elements", [])

            if workspaces:

                focused_workspace_index = focused_monitor.get("workspaces", {}).get("focused")
                focused_workspace = workspaces[focused_workspace_index]
                window_count = len(focused_workspace.get("containers", {}).get("elements", []))
                return window_count == 0

        return False  # Handle cases where the JSON structure is unexpected

    except FileNotFoundError as e:
        logging.error(f"Error: komorebic not found. Full Error:\n{e}")
        return False

    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        logging.error(f"Error getting komorebi state: {e}")
        return False

    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        return False

class TaskManagerKiller:

    def __init__(self):

        self.process = None

    def start_subprocess(self):

        if self.process: self.stop_subprocess()

        self.process = Process(
            target=self.run_loop
        )

        self.process.start()

    def stop_subprocess(self):

        if not self.process: return
        self.process.terminate()
        self.process = None

    def run_loop(self):

        if not TASK_MANAGER_KILLER or NON_LETHAL: return

        while True:

            sleep(ITERATION_DELAY)

            # Keep looking for the task manager process and kill it.

            for process in psutil.process_iter(['name']):
                
                if process.info['name'] and process.info['name'].lower() == "taskmgr.exe":
                    process.kill()
                    logging.info("Task Manager killed.")

class InputHandler:
    """
    Handles input blocking and listens for the unlock combination.
    """

    def __init__(self, onUnblock):

        self.devicesEnabled = True
        self.onUnblock = onUnblock

        self.keyboardListener = None
        self.mouseListener = None

        logging.debug("InputHandler initialized.")

    def handleInput(self, event):
        if self.devicesEnabled:
            return

        # Find out the string name of the key pressed
        key_name = ""
        if hasattr(event, "char") and event.char:
            key_name = event.char.lower()
        elif hasattr(event, "_name_") and event._name_:
            key_name = event._name_.lower()

        # If there's too many characters in memory (more than the unlock combination), remove oldest one
        if len(CHARACTER_MEMORY) >= len(UNLOCK_COMBINATION):
            CHARACTER_MEMORY.pop(0)

        # Add key to memory
        CHARACTER_MEMORY.append(key_name)
        logging.debug(f"Character Memory: [{', '.join(CHARACTER_MEMORY)}]")

        # If everything the user has typed so far is not the unlock combo, exit
        if len(CHARACTER_MEMORY) < len(UNLOCK_COMBINATION): return
        if UNLOCK_COMBINATION != CHARACTER_MEMORY[-len(UNLOCK_COMBINATION):]: return

        # Correct combination entered if not returned by now.

        logging.info("Correct combination entered. Unlocking computer.")

        self.unblock()
        self.onUnblock()

    def block(self):

        logging.debug("Unblocking inputs before blocking to avoid issues.")
        self.unblock()

        if KEYBOARD_BLOCK:

            self.keyboardListener = pynput.keyboard.Listener(
                suppress = not NON_LETHAL,
                on_press = self.handleInput
            )
            self.keyboardListener.start()
            logging.info("Keyboard blocked.")

        if MOUSE_BLOCK:

            self.mouseListener = pynput.mouse.Listener(
                suppress = not NON_LETHAL,
            )
            self.mouseListener.start()
            logging.info("Mouse blocked.")

        self.devicesEnabled = False


    def unblock(self):

        self.devicesEnabled = True

        if self.keyboardListener is not None:

            self.keyboardListener.stop()
            self.keyboardListener = None

            logging.info("Keyboard unblocked.")

        if self.mouseListener is not None:

            self.mouseListener.stop()
            self.mouseListener = None
            
            logging.info("Mouse unblocked.")

class LockMessage:
    """
    Displays a lock screen message using Tkinter.
    """

    def __init__(self):
        self.process = None
        logging.debug("LockMessage initialized.")

    def drawMessage(self):
        root = tk.Tk()
        screenWidth = root.winfo_screenwidth()
        screenHeight = root.winfo_screenheight()
        root.geometry(f"{screenWidth}x{screenHeight}+0+0")

        try:
            img = Image.open("./assets/lockedscreen.png")
            img = img.resize((screenWidth // 5, math.floor(screenWidth // 4)))
            img = ImageTk.PhotoImage(img)
            root.image = img

            label = tk.Label(root, image=root.image, bg='green')
            root.overrideredirect(True)
            root.lift()
            root.configure(bg='green')

            root.wm_attributes("-topmost", True)
            root.wm_attributes("-disabled", True)
            
            # Basically makes a greenscreen. Not very effective but I don't mind.
            # Colour everything you want transparent green.
            root.wm_attributes("-transparentcolor", "green")

            # Find a random spot on the screen. I don't know what I did here, but it works.
            random_x = screenWidth // 2 - (screenWidth // randint(2,10)) // 2
            random_y = screenHeight // 2 - (screenWidth // randint(2,10)) // 2

            label.place(x=random_x, y=random_y)

            elapsed_time_label = tk.Label(
                root, 
                text="00:00:00", 
                font=Font(file="./assets/VCR_OSD_MONO.ttf", family="VCR OSD Mono", size=screenWidth//50), 
                fg='white', 
                bg='black'
            )
            elapsed_time_label.place(x=random_x + screenHeight // 16, y=random_y + screenWidth // 5)

            start_time = time()
            
            # Update the time every second
            def update_elapsed_time():

                elapsed_time = time() - start_time

                minutes, seconds = divmod(int(elapsed_time), 60)
                hours, minutes = divmod(minutes, 60)

                time_string = f"{hours:02}:{minutes:02}:{seconds:02}"

                elapsed_time_label.config(text=time_string)

                root.after(1000, update_elapsed_time)

            update_elapsed_time()

            root.mainloop()

        except Exception as e:

            logging.error(f"Error in LockMessage.drawMessage: {e}")

    def start(self):

        self.stop()

        p = Process(target=self.drawMessage)
        p.start()
        self.process = p
        
        logging.debug("LockMessage process started.")

    def stop(self):

        if self.process:

            self.process.terminate()
            self.process = None

            logging.debug("LockMessage process terminated.")

class InactivityMonitor(threading.Thread):
    """
    Monitors user inactivity and locks the computer after a specified idle time.
    """

    def __init__(self, timeout:int = 300):

        super().__init__()

        self.timeout = timeout
        self.lockMessage = LockMessage()
        self.inputHandler = InputHandler(self.unlock)
        self.taskManagerKiller = TaskManagerKiller()
        self.isCurrentlyLocked = False

        self.enabled = threading.Event()
        self.enabled.set()

        self.running = threading.Event()
        self.running.set()

        logging.info("InactivityMonitor initialized with timeout %s seconds", self.timeout)

    def isDesktopActive(self):
        try:
            windowClassName = win32gui.GetClassName(win32gui.GetForegroundWindow())
        except pywintypes.error:
            # In case of invalid (non-existent) window handle
            return False
        return windowClassName == "WorkerW"

    def run(self):

        global CURRENT_IDLE
        logging.info("InactivityMonitor started.")

        while self.running.is_set():

            sleep(ITERATION_DELAY)

            if self.enabled.is_set() and not self.isCurrentlyLocked:

                if self.isDesktopActive() or \
                (
                    KOMOREBI_INTEGRATION_ENABLED and is_komorebi_workspace_idle()
                    and not has_fullscreen_applications_running()
                ) and \
                (
                    IGNORE_WHEN_WINDOWS_LOCKED and not is_windows_locked()
                ):

                    CURRENT_IDLE += ITERATION_DELAY
                    logging.debug(f"Desktop active for {CURRENT_IDLE} seconds.")

                    if CURRENT_IDLE > IDLE_LIMIT:
                        CURRENT_IDLE = 0
                        logging.info("Idle time exceeded limit. Locking computer.")
                        self.lock()

                else:
                    CURRENT_IDLE = 0

        logging.info("InactivityMonitor stopped.")

    def lock(self):
        
        self.isCurrentlyLocked = True
        logging.debug("Locking the computer.")

        self.lockMessage.start()
        self.inputHandler.block()
        self.taskManagerKiller.start_subprocess()

    def unlock(self):

        self.isCurrentlyLocked = False
        logging.debug("Unlocking the computer.")

        self.lockMessage.stop()
        self.inputHandler.unblock()
        self.taskManagerKiller.stop_subprocess()

    def enable(self):
        if not self.enabled.is_set():
            self.enabled.set()
            logging.debug("InactivityMonitor enabled.")

    def disable(self):
        if self.enabled.is_set():
            self.enabled.clear()
            logging.debug("InactivityMonitor disabled.")

    def stop(self):
        logging.info("Stopping InactivityMonitor.")
        self.running.clear()

class TrayIcon:

    def __init__(self, monitor: InactivityMonitor):
        """
        Creates and runs the system tray icon with menu options.
        """

        def on_enable(icon, item):
            monitor.enable()
            logging.info("Tray Menu: Enabled Inactivity Monitor.")

        def on_disable(icon, item):
            monitor.disable()
            logging.info("Tray Menu: Disabled Inactivity Monitor.")

        def on_exit(icon, item):
            logging.info("Tray Menu: Exiting application.")
            monitor.stop()
            icon.stop()
        
        def on_lock(icon, item):
            monitor.lock()
            logging.info("Tray Menu: Locked Manually.")

        # Load icon image
        try:
            icon_image = Image.open("./assets/icon.png")
        except Exception as e:
            logging.error(f"Failed to load tray icon image: {e}")
            # Fallback icon, red square
            icon_image = Image.new('RGB', (64, 64), color = 'red')

        # Create menu
        menu = pystray.Menu(

            # Lock manually
            pystray.MenuItem('Lock', on_lock),

            # Enable the monitor
            pystray.MenuItem(
                'Enable Monitor', 
                on_enable,
                checked = lambda item: monitor.enabled.is_set()),

            # Disable the monitor
            pystray.MenuItem(
                'Disable Monitor',
                on_disable, 
                checked = lambda item: not monitor.enabled.is_set()
            ),

            # Exit the monitor (kill this process)
            pystray.MenuItem('Exit', on_exit)
        )

        self.icon = pystray.Icon("InactivityMonitor", icon_image, "Inactivity Monitor", menu)
        logging.debug("System tray icon created.")

    def start(self):

        logging.info("System tray icon running.")
        self.icon.run()
    
    

if __name__ == "__main__":

    # Initialize InactivityMonitor
    monitor = InactivityMonitor(timeout=IDLE_LIMIT)
    
    # Start the InactivityMonitor thread
    monitor.start()
    logging.debug("InactivityMonitor thread started.")

    # Create and run the system tray icon
    try:

        logging.debug("Starting tray icon..")
        TrayIcon(monitor).start()

    except Exception as e:

        logging.error(f"Error running tray icon: {e}")
        monitor.stop()
        exit(1)