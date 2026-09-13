#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#     "faster-whisper",
# ]
# ///

import os
from pathlib import Path
from faster_whisper import WhisperModel

# Directory containing your video files
VIDEO_DIR = Path("./videos")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}

def process_videos():
    # Load model optimized for CPU execution using 8-bit quantization
    # Options: "tiny.en", "base.en", "small.en", "medium.en"
    print("Loading Whisper model...")
    model = WhisperModel(
        model_size_or_path="small.en", 
        device="cpu", 
        compute_type="int8",
        cpu_threads=os.cpu_count()  # Maximize CPU utilization
    )

    for file_path in VIDEO_DIR.iterdir():
        if file_path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue

        output_txt_path = file_path.with_suffix(".txt")
        if output_txt_path.exists():
            print(f"Skipping (already transcribed): {file_path.name}")
            continue

        print(f"Transcribing: {file_path.name}...")
        
        # Transcribe with parameters tuned for single-speaker English
        segments, info = model.transcribe(
            str(file_path),
            language="en",
            beam_size=5,
            vad_filter=True,  # Filters out silent regions to avoid hallucinated text
            vad_parameters=dict(min_silence_duration_ms=500)
        )

        # Collect text from generator
        full_text = " ".join([segment.text.strip() for segment in segments])

        # Save transcript next to the video
        with open(output_txt_path, "w", encoding="utf-8") as f:
            f.write(full_text + "\n")
            
        print(f"Done -> {output_txt_path.name}")

if __name__ == "__main__":
    process_videos()
