import time
import tkinter as tk
from tkinter import ttk
import cv2
import numpy as np
from PIL import Image, ImageTk
from .engine import Engine
from .guidance import GuidanceOutput
from .overlays import draw_hand_observation
from .presentation import display_state

BG = '#0c1422'
PANEL = '#172439'
TEXT = '#f3f7ff'
MUTED = '#b6c7df'
ACCENT = '#91e6c0'
WARNING = '#ffcf80'


class Window:
    def __init__(self, config, preview=False):
        self.config = config
        self.root = tk.Tk()
        self.root.title('VisionOS | Hand guidance')
        width = min(1440, self.root.winfo_screenwidth() - 60)
        height = min(920, self.root.winfo_screenheight() - 90)
        self.root.geometry(f'{width}x{height}')
        self.root.minsize(min(980, width), min(660, height))
        self.root.configure(bg=BG)
        self.root.columnconfigure(0, weight=7)
        self.root.columnconfigure(1, weight=3, minsize=350)
        self.root.rowconfigure(1, weight=1)
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('VisionOS.TButton', font=('Segoe UI', 12), padding=(12, 10),
                        foreground=TEXT, background='#2a405d', borderwidth=1)
        style.map('VisionOS.TButton', background=[('active', '#365679')],
                  foreground=[('disabled', MUTED)])
        header = tk.Frame(self.root, bg=BG, padx=24, pady=16)
        header.grid(row=0, column=0, columnspan=2, sticky='ew')
        tk.Label(header, text='VisionOS', bg=BG, fg=TEXT,
                 font=('Segoe UI', 23, 'bold')).pack(side='left')
        tk.Label(header, text='HAND GUIDANCE', bg=BG, fg=MUTED,
                 font=('Segoe UI', 12)).pack(side='left', padx=22)
        ttk.Button(header, text='Fullscreen', style='VisionOS.TButton',
                   command=self.full).pack(side='right')
        self.camera = tk.Label(self.root, bg='#050a12', fg=MUTED,
                               text='Connecting to camera…', font=('Segoe UI', 18))
        self.camera.grid(row=1, column=0, sticky='nsew', padx=16, pady=4)
        panel = tk.Frame(self.root, bg=PANEL, padx=20, pady=20)
        panel.grid(row=1, column=1, sticky='nsew', padx=(0, 16), pady=4)

        def label(text, size=12, color=TEXT, bold=False):
            widget = tk.Label(panel, text=text, font=('Segoe UI', size, 'bold' if bold else 'normal'),
                              bg=PANEL, fg=color, anchor='w', justify='left', wraplength=310)
            widget.pack(fill='x', pady=(0, 10))
            return widget
        self.status_chip = label('CONNECTING', 15, WARNING, True)
        label('GUIDANCE', 11, MUTED, True)
        self.direction = label('STOP', 36, WARNING, True)
        label('FINGERTIP TO TARGET', 11, MUTED, True)
        self.distance = label('Unavailable', 30, TEXT, True)
        self.directions = label('SIDE       —\nHEIGHT   —\nREACH    —', 15)
        ttk.Separator(panel).pack(fill='x', pady=(6, 18))
        self.states = label('Hand: LOST\nDepth: UNAVAILABLE\nTarget: SEARCHING', 12)
        self.message = label('Waiting for current hand and target observations.', 12, WARNING)
        self.performance = label('', 11, MUTED)
        actions = tk.Frame(self.root, bg=BG, padx=16, pady=14)
        actions.grid(row=2, column=0, columnspan=2, sticky='ew')
        ttk.Button(actions, text='Find again', style='VisionOS.TButton', command=self.reset).pack(side='left')
        ttk.Button(actions, text='Pause guidance', style='VisionOS.TButton',
                   command=self.pause).pack(side='left', padx=10)
        ttk.Button(actions, text='Rotate camera', style='VisionOS.TButton',
                   command=self.rotate).pack(side='left')
        self.guidance = GuidanceOutput()
        self.engine = Engine(config, self.guidance, preview)
        self.fullscreen = False
        self.last_render = None
        self.closed = False
        for key, action in [('r', self.reset), ('o', self.rotate), ('p', self.pause),
                            ('<F11>', self.full), ('<Escape>', self.escape)]:
            self.root.bind(key, lambda event, fn=action: fn())
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.engine.start()
        self.tick()

    def reset(self):
        self.engine.reset()

    def pause(self):
        self.engine.pause()

    def full(self):
        self.fullscreen = not self.fullscreen
        self.root.attributes('-fullscreen', self.fullscreen)

    def rotate(self):
        self.engine.rotate()
        self.last_render = None

    def escape(self):
        self.fullscreen = False
        self.root.attributes('-fullscreen', False)

    def tick(self):
        if self.closed:
            return
        result = self.engine.result
        error = self.engine.error
        self.message.config(text=error or 'Connecting to camera…')
        if result:
            frame, metrics = result['frame'], result['metrics']
            now = time.monotonic()
            view = display_state(result, now, self.config['max_frame_age_ms'], self.engine.completed, error)
            age = now - frame.received + frame.relative_lag
            direction = self.guidance.current().direction if view.active else 'STOP'
            self.status_chip.config(text=view.status, fg=ACCENT if view.active else WARNING)
            self.direction.config(text=direction, fg=WARNING if direction == 'STOP' else ACCENT)
            self.distance.config(text=view.distance)
            self.directions.config(text=view.directions)
            self.states.config(text=f'Hand: {view.hand}\nDepth: {view.depth}\nTarget: {view.target}')
            self.message.config(text=view.message)
            self.performance.config(text=f'{metrics["processing_fps"]:.1f} FPS · Frame age {age * 1000:.0f} ms')
            width, height = max(1, self.camera.winfo_width()), max(1, self.camera.winfo_height())
            key = (frame.epoch, frame.id, width, height, False, view.stale, bool(error), view.active)
            if self.last_render != key:
                self.last_render = key
                image = frame.color.copy()
                if not view.stale and not error:
                    box = result['object_box']
                    if box is not None:
                        a, b, c, d = np.clip(box, [0, 0, 0, 0], [image.shape[1] - 1, image.shape[0] - 1] * 2).astype(int)
                        color = (192, 230, 145) if view.target in ('VISIBLE', 'TRACKED') else (128, 207, 255)
                        cv2.rectangle(image, (a, b), (c, d), color, 2)
                        cv2.putText(image, 'Target', (a, max(18, b - 7)), 0, .5, color, 1, cv2.LINE_AA)
                    if view.active and result['surface_uv'] is not None and result['finger_uv'] is not None:
                        a = tuple(np.rint(result['finger_uv']).astype(int))
                        b = tuple(np.rint(result['surface_uv']).astype(int))
                        cv2.line(image, a, b, (192, 230, 145), 2, cv2.LINE_AA)
                draw_hand_observation(image, result.get('hand_landmarks'), result['finger_uv'], view.stale or bool(error))
                pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
                pil.thumbnail((width, height))
                self.photo = ImageTk.PhotoImage(pil)
                self.camera.config(image=self.photo, text='')
        elif self.engine.error:
            self.camera.config(text='Camera unavailable.\nCheck the connection and restart.')
        self.root.after(33, self.tick)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.engine.close()
        self.root.destroy()


def main():
    from .cli import main as run
    run()
