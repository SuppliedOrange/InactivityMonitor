import pygetwindow as gw
from time import sleep
import win32api
import win32gui

ONLY_SHOW_IF_FOCUSED = False

def get_fullscreen_applications():
    """
    Returns a list of fullscreen applications that are in focus.
    """

    screen_width = win32api.GetSystemMetrics(0)
    screen_height = win32api.GetSystemMetrics(1)

    fullscreen_applications = []

    focused_window = win32gui.GetForegroundWindow()

    for window in gw.getAllWindows():

        if window.isMaximized or (window.width == screen_width and window.height == screen_height):

            if (
                ONLY_SHOW_IF_FOCUSED and window._hWnd == focused_window
            ) and window.title:

                fullscreen_applications.append(window.title)

    return fullscreen_applications

if __name__ == "__main__":
    
    while True:
        print(get_fullscreen_applications())
        sleep(1)