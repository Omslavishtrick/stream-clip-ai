import os
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox

import cv2
import numpy as np
from moviepy.editor import VideoFileClip


def detect_motion(video_path):
    cap = cv2.VideoCapture(video_path)
    motion_scores = []
    prev_frame = None

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 30

    frame_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_count % int(fps) != 0:
            frame_count += 1
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        if prev_frame is None:
            prev_frame = gray
            motion_scores.append(0)
        else:
            diff = cv2.absdiff(prev_frame, gray)
            motion = float(diff.mean())
            motion_scores.append(motion)
            prev_frame = gray

        frame_count += 1

    cap.release()
    return motion_scores


def process_video(video_path, clip_limit, status_callback):
    status_callback("Opening video...")
    video = VideoFileClip(video_path)
    audio = video.audio

    if audio is None:
        raise ValueError("This video has no audio track.")

    status_callback("Detecting motion...")
    motion_scores = detect_motion(video_path)

    status_callback("Analyzing audio chunks...")
    volumes = []
    seconds = int(video.duration)

    for i in range(seconds):
        chunk = audio.subclip(i, i + 1)
        sound = chunk.to_soundarray(fps=22050)
        volume = float(np.mean(np.abs(sound)))
        volumes.append(volume)

    average_volume = float(np.mean(volumes)) if volumes else 0.0

    highlight_data = []
    window_size = 3
    spike_multiplier = 1.25
    min_highlight_score = 0.12

    motion_average = 0.0
    motion_max = 1.0

    if motion_scores:
        motion_average = float(np.mean(motion_scores))
        motion_max = float(max(motion_scores)) if max(motion_scores) > 0 else 1.0

    audio_max = float(max(volumes)) if volumes and max(volumes) > 0 else 1.0

    max_index = min(len(volumes), len(motion_scores))

    for i in range(window_size, max_index - window_size):
        local_window = volumes[i - window_size:i + window_size + 1]
        local_average = float(np.mean(local_window))

        raw_audio_score = 0.0
        raw_motion_score = 0.0

        if volumes[i] > local_average * spike_multiplier and volumes[i] > average_volume * 1.05:
            raw_audio_score = float(volumes[i] - local_average)

        if motion_scores[i] > motion_average * 1.2:
            raw_motion_score = float(motion_scores[i] - motion_average)

        normalized_audio = raw_audio_score / audio_max
        normalized_motion = raw_motion_score / motion_max
        combined_score = (normalized_audio * 0.7) + (normalized_motion * 0.3)

        if combined_score > min_highlight_score:
            highlight_data.append((i, combined_score, normalized_audio, normalized_motion))

    merged_highlights = []

    for second, score, audio_score, motion_score in highlight_data:
        if not merged_highlights:
            merged_highlights.append((second, score, audio_score, motion_score))
        else:
            last_second, last_score, _, _ = merged_highlights[-1]

            if second - last_second <= 8:
                if score > last_score:
                    merged_highlights[-1] = (second, score, audio_score, motion_score)
            else:
                merged_highlights.append((second, score, audio_score, motion_score))

    merged_highlights = sorted(merged_highlights, key=lambda x: x[1], reverse=True)
    top_highlights = merged_highlights[:clip_limit]
    top_highlights = sorted(top_highlights, key=lambda x: x[0])

    video_name = os.path.splitext(os.path.basename(video_path))[0]
    output_folder = f"{video_name}_clips"

    if os.path.exists(output_folder):
        shutil.rmtree(output_folder)

    os.makedirs(output_folder, exist_ok=True)

    seconds_before = 7
    seconds_after = 12
    clip_count = 1

    report_lines = []
    report_lines.append("HIGHLIGHT REPORT")
    report_lines.append("=" * 40)
    report_lines.append(f"Video file: {video_path}")
    report_lines.append(f"Video duration: {video.duration:.2f} seconds")
    report_lines.append(f"Average volume: {average_volume:.4f}")
    report_lines.append(f"Average motion: {motion_average:.4f}")
    report_lines.append(f"Total highlights saved: {len(top_highlights)}")
    report_lines.append("")

    for second, score, audio_score, motion_score in top_highlights:
        start_time = max(0, second - seconds_before)
        end_time = min(video.duration, second + seconds_after)

        output_path = os.path.join(output_folder, f"clip_{clip_count}.mp4")
        status_callback(
            f"Saving clip {clip_count}: {start_time} to {end_time} | total={score:.4f}"
        )

        highlight_clip = video.subclip(start_time, end_time)
        highlight_clip.write_videofile(output_path, codec="libx264", audio_codec="aac")

        report_lines.append(f"Clip {clip_count}")
        report_lines.append(f"  Highlight second: {second}")
        report_lines.append(f"  Total score: {score:.4f}")
        report_lines.append(f"  Audio score: {audio_score:.4f}")
        report_lines.append(f"  Motion score: {motion_score:.4f}")
        report_lines.append(f"  Clip start: {start_time}")
        report_lines.append(f"  Clip end: {end_time}")
        report_lines.append(f"  File: clip_{clip_count}.mp4")
        report_lines.append("")

        clip_count += 1

    report_path = os.path.join(output_folder, "highlight_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    video.close()
    return output_folder


class ClipApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Stream Clip AI")
        self.root.geometry("600x320")

        self.video_path = tk.StringVar()
        self.clip_limit = tk.StringVar(value="3")
        self.status_text = tk.StringVar(value="Choose a video to begin.")

        tk.Label(root, text="Stream Clip AI", font=("Arial", 18, "bold")).pack(pady=10)

        tk.Label(root, text="Selected video:").pack(anchor="w", padx=20)
        tk.Entry(root, textvariable=self.video_path, width=70).pack(padx=20, pady=5)

        tk.Button(root, text="Browse Video", command=self.browse_video).pack(pady=5)

        tk.Label(root, text="How many clips to generate:").pack(anchor="w", padx=20, pady=(15, 0))
        tk.Entry(root, textvariable=self.clip_limit, width=10).pack(anchor="w", padx=20)

        tk.Button(root, text="Generate Highlights", command=self.run_processing, height=2, width=25).pack(pady=20)

        tk.Label(root, text="Status:").pack(anchor="w", padx=20)
        tk.Label(root, textvariable=self.status_text, wraplength=540, justify="left").pack(anchor="w", padx=20, pady=5)

    def browse_video(self):
        file_path = filedialog.askopenfilename(
            title="Choose a video file",
            filetypes=[
                ("Video Files", "*.mp4 *.mov *.avi *.mkv"),
                ("All Files", "*.*")
            ]
        )
        if file_path:
            self.video_path.set(file_path)
            self.status_text.set("Video selected. Ready to generate highlights.")

    def update_status(self, message):
        self.status_text.set(message)
        self.root.update_idletasks()

    def run_processing(self):
        video_path = self.video_path.get().strip()
        clip_limit_text = self.clip_limit.get().strip()

        if not video_path:
            messagebox.showerror("Error", "Please choose a video file first.")
            return

        if not os.path.exists(video_path):
            messagebox.showerror("Error", "That video file does not exist.")
            return

        if not clip_limit_text.isdigit():
            messagebox.showerror("Error", "Clip count must be a whole number.")
            return

        clip_limit = int(clip_limit_text)
        if clip_limit <= 0:
            messagebox.showerror("Error", "Clip count must be at least 1.")
            return

        try:
            output_folder = process_video(video_path, clip_limit, self.update_status)
            self.status_text.set(f"Done! Clips saved in: {output_folder}")
            messagebox.showinfo("Success", f"Highlights created successfully.\n\nSaved in: {output_folder}")
        except Exception as e:
            messagebox.showerror("Error", str(e))
            self.status_text.set("Something went wrong. Check the error message.")


root = tk.Tk()
app = ClipApp(root)
root.mainloop()
