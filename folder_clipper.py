from moviepy.editor import VideoFileClip
import numpy as np
import os
import shutil

# ===== USER SETTINGS =====
input_folder = "videos"
clip_limit = 3
seconds_before = 7
seconds_after = 12
window_size = 3
spike_multiplier = 1.25

supported_extensions = (".mp4", ".mov", ".avi", ".mkv")


def analyze_video(video_path):
    print("\n" + "=" * 60)
    print(f"Processing video: {video_path}")

    video = VideoFileClip(video_path)
    audio = video.audio

    if audio is None:
        print("Skipped: no audio found.")
        return

    volumes = []
    seconds = int(video.duration)

    print("Analyzing audio chunks...")

    for i in range(seconds):
        chunk = audio.subclip(i, i + 1)
        sound = chunk.to_soundarray(fps=22050)
        volume = float(np.mean(np.abs(sound)))
        volumes.append(volume)

    if not volumes:
        print("Skipped: could not analyze audio.")
        return

    average_volume = float(np.mean(volumes))
    print("Average volume:", average_volume)

    highlight_data = []

    for i in range(window_size, len(volumes) - window_size):
        local_window = volumes[i - window_size:i + window_size + 1]
        local_average = float(np.mean(local_window))

        if volumes[i] > local_average * spike_multiplier and volumes[i] > average_volume * 1.05:
            score = float(volumes[i] - local_average)
            highlight_data.append((i, score))

    print("Raw highlight data:", highlight_data)

    merged_highlights = []

    for second, score in highlight_data:
        if not merged_highlights:
            merged_highlights.append((second, score))
        else:
            last_second, last_score = merged_highlights[-1]

            if second - last_second <= 8:
                if score > last_score:
                    merged_highlights[-1] = (second, score)
            else:
                merged_highlights.append((second, score))

    print("Merged highlights:", merged_highlights)

    merged_highlights = sorted(merged_highlights, key=lambda x: x[1], reverse=True)
    top_highlights = merged_highlights[:clip_limit]
    top_highlights = sorted(top_highlights, key=lambda x: x[0])

    print("Top highlights:", top_highlights)

    video_name = os.path.splitext(os.path.basename(video_path))[0]
    output_folder = os.path.join(input_folder, f"{video_name}_clips")

    if os.path.exists(output_folder):
        shutil.rmtree(output_folder)

    os.makedirs(output_folder, exist_ok=True)

    report_lines = []
    report_lines.append("HIGHLIGHT REPORT")
    report_lines.append("=" * 40)
    report_lines.append(f"Video file: {video_path}")
    report_lines.append(f"Video duration: {video.duration:.2f} seconds")
    report_lines.append(f"Average volume: {average_volume:.4f}")
    report_lines.append(f"Total top highlights: {len(top_highlights)}")
    report_lines.append("")

    clip_count = 1

    for second, score in top_highlights:
        start_time = max(0, second - seconds_before)
        end_time = min(video.duration, second + seconds_after)

        highlight_clip = video.subclip(start_time, end_time)
        output_path = os.path.join(output_folder, f"clip_{clip_count}.mp4")

        print(f"Saving clip {clip_count}: {start_time} to {end_time} | score={score:.4f}")
        highlight_clip.write_videofile(output_path, codec="libx264", audio_codec="aac")

        report_lines.append(f"Clip {clip_count}")
        report_lines.append(f"  Highlight second: {second}")
        report_lines.append(f"  Score: {score:.4f}")
        report_lines.append(f"  Clip start: {start_time}")
        report_lines.append(f"  Clip end: {end_time}")
        report_lines.append(f"  File: clip_{clip_count}.mp4")
        report_lines.append("")

        clip_count += 1

    report_path = os.path.join(output_folder, "highlight_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    print(f"Done! Saved clips for {video_name} in: {output_folder}")


def main():
    if not os.path.exists(input_folder):
        os.makedirs(input_folder)
        print(f"Created folder '{input_folder}'. Put your videos in there and run again.")
        return

    video_files = [
        f for f in os.listdir(input_folder)
        if f.lower().endswith(supported_extensions)
    ]

    if not video_files:
        print(f"No video files found in '{input_folder}'.")
        return

    print(f"Found {len(video_files)} video(s).")

    for filename in video_files:
        video_path = os.path.join(input_folder, filename)
        try:
            analyze_video(video_path)
        except Exception as e:
            print(f"Error processing {filename}: {e}")

    print("\nAll videos finished.")


if __name__ == "__main__":
    main()