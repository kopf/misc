#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pygame",
#     "numpy",
# ]
# ///

import os
import sys
import math
import random
import argparse
import numpy as np
import pygame
from dataclasses import dataclass
from typing import List, Optional

# Original Pollock palette from XScreensaver substrate.c
POLLOCK_PALETTE = [
    "#201F21", "#262C2E", "#352626", "#372B27", "#302C2E", "#392B2D", "#323229", "#3F3229",
    "#38322E", "#2E333D", "#333A3D", "#473329", "#403433", "#4A362D", "#3B3831", "#3E3B33",
    "#4D373B", "#423E3E", "#503E31", "#444133", "#454436", "#50433E", "#4C4640", "#47494A",
    "#59483C", "#5C4B4B", "#525043", "#56524A", "#5B5552", "#66503E", "#645543", "#68584B",
    "#625C51", "#6A5E57", "#6C6552", "#716352", "#776752", "#73695B", "#796C61", "#6D727A",
    "#767571", "#7B776E", "#83745D", "#897664", "#847B6A", "#8D7F73", "#828374", "#8B8679",
    "#95856F", "#9A8C7A", "#96917B", "#9A9586", "#A19888", "#9099A2", "#A29F91", "#AA9F8C",
    "#B0A391", "#B6A795", "#A6A99D", "#B3AE9D", "#BCB39E", "#BDB7AB", "#C6BBA8", "#C7C2B4",
    "#BDC3C9", "#CBCFD2", "#D2C9B9", "#D9D2C3", "#DAD7C9", "#E1DDD2", "#EAE6DA", "#F2EEE4",
    "#F8F2C7", "#EFEFD0"
]

EMPTY_GRID = 10001.0

@dataclass
class Crack:
    x: float
    y: float
    angle: float
    sand_color: pygame.Color
    is_circular: bool = False
    radius: float = 0.0
    sandp: float = 0.0
    sandg: float = 0.0
    is_alive: bool = True

class Substrate:
    def __init__(self, width: int, height: int, args):
        self.width = width
        self.height = height
        self.args = args
        
        self.screen = pygame.display.set_mode((width, height))
        pygame.display.set_caption("Substrate Screensaver")
        
        self.bg_color = pygame.Color(args.background)
        self.fg_color = pygame.Color(args.foreground)
        
        # Grid to store crack angles for collision detection
        self.grid = np.full((width, height), EMPTY_GRID, dtype=np.float32)
        
        # Persistent surface for all drawing (needs alpha for sand painting)
        self.draw_surface = pygame.Surface((width, height), pygame.SRCALPHA)
        self.draw_surface.fill((0, 0, 0, 0))
        
        self.cracks: List[Crack] = []
        self.palette = [pygame.Color(c) for c in POLLOCK_PALETTE]
        
        self.cycles = 0
        self.reset()

    def reset(self):
        self.screen.fill(self.bg_color)
        self.draw_surface.fill((0, 0, 0, 0))
        self.grid.fill(EMPTY_GRID)
        self.cracks = []
        self.cycles = 0
        
        for _ in range(self.args.initial_cracks):
            self.add_crack()

    def add_crack(self):
        if len(self.cracks) >= self.args.max_cracks:
            # Re-use dead crack slots if possible
            for i, c in enumerate(self.cracks):
                if not c.is_alive:
                    self.cracks[i] = self._create_new_crack_data()
                    return
            return

        self.cracks.append(self._create_new_crack_data())

    def _create_new_crack_data(self) -> Crack:
        # Try to find a starting point that respects min_dist from other active cracks
        best_x, best_y = random.uniform(0, self.width), random.uniform(0, self.height)
        best_angle = random.uniform(0, 360)
        
        # We try multiple times to find a good starting point
        for attempt in range(100):
            curr_x, curr_y = random.uniform(0, self.width), random.uniform(0, self.height)
            curr_angle = random.uniform(0, 360)
            found_on_grid = False
            
            # 1. Try to find a point on the grid (branching)
            # Sample fewer times per attempt to keep it fast but effective
            for _ in range(100):
                px, py = random.randint(0, self.width - 1), random.randint(0, self.height - 1)
                grid_val = self.grid[px, py]
                if grid_val < 10000:
                    curr_x, curr_y = float(px), float(py)
                    branch_dir = random.choice([-90.0, 90.0])
                    curr_angle = (grid_val + branch_dir + random.uniform(-2.0, 2.0)) % 360.0
                    found_on_grid = True
                    break
            
            # 2. Check distance from other active cracks
            if self.args.min_dist > 0:
                too_close = False
                for other in self.cracks:
                    if other.is_alive:
                        dist_sq = (curr_x - other.x)**2 + (curr_y - other.y)**2
                        if dist_sq < self.args.min_dist**2:
                            too_close = True
                            break
                if too_close:
                    continue # Try another attempt
            
            # If we got here, it's a valid point (either far enough or we don't care about min_dist)
            best_x, best_y, best_angle = curr_x, curr_y, curr_angle
            break

        is_circular = random.randint(0, 100) < self.args.circle_percent
        radius = 0.0
        if is_circular:
            radius = 10.0 + random.uniform(0, (self.width + self.height) / 2.0)
            if random.random() < 0.5:
                radius *= -1.0
        
        return Crack(
            x=best_x,
            y=best_y,
            angle=best_angle,
            sand_color=random.choice(self.palette),
            is_circular=is_circular,
            radius=radius,
            sandg=random.uniform(0.0, 1.0)
        )

    def step(self):
        if self.cycles >= self.args.max_cycles:
            self.reset()
            return

        new_cracks_needed = 0
        
        for crack in self.cracks:
            if not crack.is_alive:
                continue

            # Move crack
            old_x, old_y = crack.x, crack.y
            
            step_size = 0.42
            if crack.is_circular:
                radian_inc = step_size / crack.radius
                crack.angle = (crack.angle + math.degrees(radian_inc)) % 360.0
            
            crack.x += step_size * math.cos(math.radians(crack.angle))
            crack.y += step_size * math.sin(math.radians(crack.angle))
            
            # Check bounds
            if not (0 <= crack.x < self.width and 0 <= crack.y < self.height):
                crack.is_alive = False
                new_cracks_needed += 2
                continue
            
            # Check collision
            ix, iy = int(crack.x), int(crack.y)
            grid_angle = self.grid[ix, iy]
            if not self.args.no_stop and grid_angle < 10000:
                # Collision if angle is different enough (handles wrap-around)
                diff = abs(grid_angle - crack.angle) % 360
                if diff > 180:
                    diff = 360 - diff
                
                if diff > 2.0:
                    crack.is_alive = False
                    new_cracks_needed += 2
                    continue
            
            # Record current angle in grid
            self.grid[ix, iy] = crack.angle
            
            # Sand painting (coloring) - Draw sand first so crack is on top
            if not self.args.wireframe:
                self.paint_sand(crack)

            # Draw crack line
            pygame.draw.line(self.draw_surface, self.fg_color, (old_x, old_y), (crack.x, crack.y), 1)
        
        # Add new cracks requested by collisions/deaths
        for _ in range(new_cracks_needed):
            self.add_crack()
            
        self.cycles += 1

    def paint_sand(self, crack: Crack):
        # Scan perpendicular to find nearest collision
        angle_rad = math.radians(crack.angle + 90)
        dx = math.cos(angle_rad)
        dy = math.sin(angle_rad)
        
        # Raycast
        rx, ry = crack.x, crack.y
        hit_x, hit_y = rx, ry
        
        # Max search distance to prevent infinite loops and for performance
        max_dist = 200 
        for dist in range(1, max_dist):
            tx, ty = int(crack.x + dx * dist), int(crack.y + dy * dist)
            if not (0 <= tx < self.width and 0 <= ty < self.height):
                hit_x, hit_y = crack.x + dx * dist, crack.y + dy * dist
                break
            if self.grid[tx, ty] < 10000:
                hit_x, hit_y = float(tx), float(ty)
                break
        else:
            hit_x, hit_y = crack.x + dx * max_dist, crack.y + dy * max_dist

        # Draw grains
        grains = self.args.sand_grains
        w = crack.sandg / (grains - 1) if grains > 1 else 0
        
        for i in range(grains):
            # Math from original: crack_x + (hit_x - crack_x) * sin(sandp + sin(i * w))
            t = math.sin(crack.sandp + math.sin(i * w))
            gx = crack.x + (hit_x - crack.x) * t
            gy = crack.y + (hit_y - crack.y) * t
            
            if 0 <= gx < self.width and 0 <= gy < self.height:
                # Alpha in original is 0.1 down to 0.0
                alpha_val = max(0, int(30 - (i / grains) * 30))
                
                # Draw small pixel on surface
                color = list(crack.sand_color[:3]) + [alpha_val]
                self.draw_surface.set_at((int(gx), int(gy)), color)

    def run(self):
        clock = pygame.time.Clock()
        running = True
        
        # Attempt to focus window
        pygame.event.pump()
        pygame.display.flip()
        
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_r:
                        self.reset()

            self.step()
            
            # Draw background then the persistent drawing surface
            self.screen.fill(self.bg_color)
            self.screen.blit(self.draw_surface, (0, 0))
            
            pygame.display.flip()
            
            if self.args.growth_delay > 0:
                # growth_delay is in microseconds, convert to ms
                pygame.time.delay(self.args.growth_delay // 1000)

def main():
    parser = argparse.ArgumentParser(description="Python version of XScreensaver Substrate")
    parser.add_argument("--initial-cracks", type=int, default=3, help="Starting number of cracks")
    parser.add_argument("--max-cracks", type=int, default=100, help="Maximum concurrent cracks")
    parser.add_argument("--min-dist", type=float, default=10.0, help="Minimum distance between new crack starts and existing cracks")
    parser.add_argument("--sand-grains", type=int, default=64, help="Number of grains for coloring")
    parser.add_argument("--circle-percent", type=int, default=33, help="Percentage of circular cracks")
    parser.add_argument("--max-cycles", type=int, default=10000, help="Steps before reset")
    parser.add_argument("--growth-delay", type=int, default=18000, help="Delay between steps (microseconds)")
    parser.add_argument("--wireframe", action="store_true", help="Draw only lines, no coloring")
    parser.add_argument("--no-stop", action="store_true", help="Cracks do not stop when hitting other cracks")
    parser.add_argument("--background", type=str, default="white", help="Background color")
    parser.add_argument("--foreground", type=str, default="black", help="Foreground (crack) color")
    parser.add_argument("--width", type=int, default=1024, help="Screen width")
    parser.add_argument("--height", type=int, default=768, help="Screen height")
    
    args = parser.parse_args()
    
    pygame.init()
    substrate = Substrate(args.width, args.height, args)
    substrate.run()
    pygame.quit()

if __name__ == "__main__":
    main()
