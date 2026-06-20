import tkinter as tk
import math

class Spinner(tk.Canvas):
    def __init__(self, parent, size=24, bg="#1e1e2e", accent_color="#7c5cff", muted_color="#272739", **kwargs):
        super().__init__(
            parent,
            width=size,
            height=size,
            bg=bg,
            highlightthickness=0,
            bd=0,
            **kwargs
        )
        self.size = size
        self.accent_color = accent_color
        self.muted_color = muted_color
        self.is_spinning = False
        self.step = 0
        self.num_dots = 8
        self.radius = (size // 2) - 3
        self.center = size // 2
        
        # Precompute dot coordinates
        self.dots = []
        for i in range(self.num_dots):
            angle = i * (2 * math.pi / self.num_dots)
            x = self.center + self.radius * math.cos(angle)
            y = self.center + self.radius * math.sin(angle)
            self.dots.append((x, y))

    def _draw(self):
        self.delete("all")
        if not self.is_spinning:
            return

        for i in range(self.num_dots):
            # Calculate position relative to the moving head
            pos = (i - self.step) % self.num_dots
            x, y = self.dots[i]

            # Fading effect logic:
            if pos == 0:
                r = 3.5
                color = self.accent_color
            elif pos == 1:
                r = 3.0
                color = "#9277ff"  # Faded accent
            elif pos == 2:
                r = 2.5
                color = "#6c53d1"  # Mid-tone
            elif pos == 3:
                r = 2.0
                color = "#513c9e"  # Darker mid-tone
            else:
                r = 1.5
                color = self.muted_color

            self.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="")

    def start(self):
        if not self.is_spinning:
            self.is_spinning = True
            self._animate()

    def stop(self):
        self.is_spinning = False
        self.delete("all")

    def _animate(self):
        if self.is_spinning:
            self._draw()
            self.step = (self.step + 1) % self.num_dots
            self.after(80, self._animate)
