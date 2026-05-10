#!/usr/bin/env uv run
# /// script
# dependencies = [
#   "pandas",
#   "matplotlib",
# ]
# ///

import pandas as pd
import matplotlib.pyplot as plt
import sys
import os

def generate_graphs(csv_file):
    if not os.path.exists(csv_file):
        print(f"Error: {csv_file} not found.")
        return

    # Load data
    df = pd.read_csv(csv_file)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # Create a figure with 4 subplots
    fig, axes = plt.subplots(4, 1, figsize=(12, 16), sharex=True)
    fig.suptitle(f'Router Diagnostic Over Time\nSource: {csv_file}', fontsize=16)

    # 1. Memory Usage
    axes[0].plot(df['timestamp'], df['mem_free'], label='Free RAM (MB)', color='green')
    axes[0].plot(df['timestamp'], df['mem_cached'], label='Cached RAM (MB)', color='blue')
    axes[0].set_ylabel('Memory (MB)')
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    # 2. CPU Load & Connections
    ax2_twin = axes[1].twinx()
    axes[1].plot(df['timestamp'], df['load_1min'], label='CPU Load (1m)', color='red')
    ax2_twin.plot(df['timestamp'], df['conntrack_count'], label='Connections', color='purple', linestyle='--')
    axes[1].set_ylabel('CPU Load')
    ax2_twin.set_ylabel('Conn Count')
    axes[1].legend(loc='upper left')
    ax2_twin.legend(loc='upper right')
    axes[1].grid(True, alpha=0.3)

    # 3. WiFi Signal (Noise/Signal Strength)
    # Note: Noise is usually negative (e.g., -90), closer to 0 is worse
    axes[2].plot(df['timestamp'], df['wifi_noise'], label='Signal Strength (dBm)', color='orange')
    axes[2].set_ylabel('dBm')
    axes[2].legend()
    axes[2].grid(True, alpha=0.3)

    # 4. WiFi Bitrate
    axes[3].plot(df['timestamp'], df['wifi_bitrate'], label='TX Bitrate (Mbps)', color='brown')
    axes[3].set_ylabel('Mbps')
    axes[3].set_xlabel('Time')
    axes[3].legend()
    axes[3].grid(True, alpha=0.3)

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    
    # Save the output
    output_png = "router_analysis.png"
    plt.savefig(output_png)
    print(f"Analysis complete! Graph saved as: {output_png}")
    plt.show()

if __name__ == "__main__":
    file_path = sys.argv[1] if len(sys.argv) > 1 else "router_stats.csv"
    generate_graphs(file_path)