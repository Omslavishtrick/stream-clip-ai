from moviepy.editor import VideoFileClip
import numpy as np
import os
import shutil
import cv2
def detect_motion(video_path):
    cap = cv2.VideoCapture(video_path)

    motion_scores = []
    prev_frame = None

    fps = cap.get(cv2.CAP_PROP_FPS)
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
            motion = diff.mean()
            motion_scores.append(motion)
            prev_frame = gray

        frame_count += 1

    cap.release()

    return motion_scores
video_path = input("Enter your video filename (example: video.mp4): ").strip()

clip_limit = input("How many highlight clips do you want? (example: 3): ").strip()

if clip_limit.isdigit():
    clip_limit = int(clip_limit)
else:
    clip_limit = 3

if not os.path.exists(video_path):
    print("Error: file not found.")
    exit()

print("Opening video...")
video = VideoFileClip(video_path)
audio = video.audio
print("Detecting motion...")

motion_scores = detect_motion(video_path)

print("Analyzing audio chunks...")

volumes = []
seconds = int(video.duration)

for i in range(seconds):
    chunk = audio.subclip(i, i + 1)
    sound = chunk.to_soundarray(fps=22050)
    volume = float(np.mean(np.abs(sound)))
    volumes.append(volume)

print("First 20 volumes:", volumes[:20])

average_volume = float(np.mean(volumes))
print("Average volume:", average_volume)

highlight_data = []

window_size = 3
spike_multiplier = 1.25

motion_average = 0
motion_max = 1

if len(motion_scores) > 0:
    motion_average = float(np.mean(motion_scores))
    motion_max = float(max(motion_scores)) if max(motion_scores) > 0 else 1

audio_max = float(max(volumes)) if max(volumes) > 0 else 1

print("Average motion:", motion_average)
print("Max audio:", audio_max)
print("Max motion:", motion_max)

max_index = min(len(volumes), len(motion_scores))

for i in range(window_size, max_index - window_size):
    local_window = volumes[i - window_size:i + window_size + 1]
    local_average = float(np.mean(local_window))

    raw_audio_score = 0
    raw_motion_score = 0

    if volumes[i] > local_average * spike_multiplier and volumes[i] > average_volume * 1.05:
        raw_audio_score = float(volumes[i] - local_average)

    if motion_scores[i] > motion_average * 1.2:
        raw_motion_score = float(motion_scores[i] - motion_average)

    normalized_audio = raw_audio_score / audio_max
    normalized_motion = raw_motion_score / motion_max

    combined_score = (normalized_audio * 0.7) + (normalized_motion * 0.3)

    if combined_score > 0:
        highlight_data.append((i, combined_score, normalized_audio, normalized_motion))

print("Raw highlight data:", highlight_data)
merged_highlights = []

for second, score, audio_score, motion_score in highlight_data:
    if not merged_highlights:
        merged_highlights.append((second, score, audio_score, motion_score))
    else:
        last_second, last_score, last_audio_score, last_motion_score = merged_highlights[-1]

        if second - last_second <= 8:
            if score > last_score:
                merged_highlights[-1] = (second, score, audio_score, motion_score)
        else:
            merged_highlights.append((second, score, audio_score, motion_score))

print("Merged highlights:", merged_highlights)

merged_highlights = sorted(merged_highlights, key=lambda x: x[1], reverse=True)
top_highlights = merged_highlights[:clip_limit]
top_highlights = sorted(top_highlights, key=lambda x: x[0])

print("Top highlights:", top_highlights)

video_name = os.path.splitext(os.path.basename(video_path))[0]
output_folder = f"{video_name}_clips"

if os.path.exists(output_folder):
    shutil.rmtree(output_folder)

os.makedirs(output_folder, exist_ok=True)

clip_count = 1
seconds_before = 7
seconds_after = 12

report_lines = []
report_lines.append("HIGHLIGHT REPORT")
report_lines.append("=" * 40)
report_lines.append(f"Video file: {video_path}")
report_lines.append(f"Video duration: {video.duration:.2f} seconds")
report_lines.append(f"Average volume: {average_volume:.4f}")
report_lines.append(f"Total top highlights: {len(top_highlights)}")
report_lines.append("")

for second, score, audio_score, motion_score in top_highlights:
    start_time = max(0, second - seconds_before)
    end_time = min(video.duration, second + seconds_after)

    highlight_clip = video.subclip(start_time, end_time)
    output_path = os.path.join(output_folder, f"clip_{clip_count}.mp4")

    print(f"Saving clip {clip_count}: {start_time} to {end_time} | total={score:.4f} | audio={audio_score:.4f} | motion={motion_score:.4f}")
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

print(f"Report saved: {report_path}")
print(f"Done! Best clips saved in the '{output_folder}' folder.")