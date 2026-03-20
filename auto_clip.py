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
    if not fps or fps <= 0:
        fps = 1

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
            motion_scores.append(float(motion))
            prev_frame = gray

        frame_count += 1

    cap.release()
    return motion_scores


def generate_highlight_clips(video_path, clip_limit=3, output_root="generated_clips"):
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    clip_limit = int(clip_limit)
    if clip_limit < 1:
        clip_limit = 1

    print("Opening video...")
    video = VideoFileClip(video_path)
    audio = video.audio

    if audio is None:
        raise ValueError("This video has no audio track.")

    print("Detecting motion...")
    motion_scores = detect_motion(video_path)

    print("Analyzing audio chunks...")
    volumes = []
    seconds = int(video.duration)

    for i in range(seconds):
        chunk_end = min(i + 1, video.duration)
        chunk = audio.subclip(i, chunk_end)
        sound = chunk.to_soundarray(fps=22050)

        if sound.size == 0:
            volume = 0.0
        else:
            volume = float(np.mean(np.abs(sound)))

        volumes.append(volume)

    average_volume = float(np.mean(volumes)) if volumes else 0.0

    highlight_data = []
    window_size = 3
    spike_multiplier = 1.25

    motion_average = float(np.mean(motion_scores)) if motion_scores else 0.0
    motion_max = float(max(motion_scores)) if motion_scores and max(motion_scores) > 0 else 1.0
    audio_max = float(max(volumes)) if volumes and max(volumes) > 0 else 1.0

    max_index = min(len(volumes), len(motion_scores))

    for i in range(window_size, max_index - window_size):
        local_window = volumes[i - window_size:i + window_size + 1]
        local_average = float(np.mean(local_window)) if local_window else 0.0

        raw_audio_score = 0.0
        raw_motion_score = 0.0

        if volumes[i] > local_average * spike_multiplier and volumes[i] > average_volume * 1.05:
            raw_audio_score = float(volumes[i] - local_average)

        if motion_scores[i] > motion_average * 1.2:
            raw_motion_score = float(motion_scores[i] - motion_average)

        normalized_audio = raw_audio_score / audio_max if audio_max > 0 else 0.0
        normalized_motion = raw_motion_score / motion_max if motion_max > 0 else 0.0
        combined_score = (normalized_audio * 0.7) + (normalized_motion * 0.3)

        if combined_score > 0:
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
    output_folder = os.path.join(output_root, f"{video_name}_clips")

    if os.path.exists(output_folder):
        shutil.rmtree(output_folder)

    os.makedirs(output_folder, exist_ok=True)

    clip_count = 1
    seconds_before = 7
    seconds_after = 12

    report_lines = [
        "HIGHLIGHT REPORT",
        "=" * 40,
        f"Video file: {video_path}",
        f"Video duration: {video.duration:.2f} seconds",
        f"Average volume: {average_volume:.4f}",
        f"Total top highlights: {len(top_highlights)}",
        ""
    ]

    clips = []

    for second, score, audio_score, motion_score in top_highlights:
        start_time = max(0, second - seconds_before)
        end_time = min(video.duration, second + seconds_after)

        highlight_clip = video.subclip(start_time, end_time)
        output_filename = f"clip_{clip_count}.mp4"
        output_path = os.path.join(output_folder, output_filename)

        print(
            f"Saving clip {clip_count}: {start_time} to {end_time} | "
            f"total={score:.4f} | audio={audio_score:.4f} | motion={motion_score:.4f}"
        )

        highlight_clip.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac"
        )

        clips.append({
            "clip_number": clip_count,
            "start_time": round(start_time, 2),
            "end_time": round(end_time, 2),
            "filename": output_filename,
            "file_path": output_path
        })

        report_lines.append(f"Clip {clip_count}")
        report_lines.append(f"  Highlight second: {second}")
        report_lines.append(f"  Total score: {score:.4f}")
        report_lines.append(f"  Audio score: {audio_score:.4f}")
        report_lines.append(f"  Motion score: {motion_score:.4f}")
        report_lines.append(f"  Clip start: {start_time}")
        report_lines.append(f"  Clip end: {end_time}")
        report_lines.append(f"  File: {output_filename}")
        report_lines.append("")

        clip_count += 1

    report_path = os.path.join(output_folder, "highlight_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    video.close()

    return {
        "output_folder": output_folder,
        "report_path": report_path,
        "clips": clips
    }


if __name__ == "__main__":
    video_path = input("Enter your video filename (example: video.mp4): ").strip()
    clip_limit = input("How many highlight clips do you want? (example: 3): ").strip()

    if clip_limit.isdigit():
        clip_limit = int(clip_limit)
    else:
        clip_limit = 3

    result = generate_highlight_clips(video_path, clip_limit)
    print(f"Report saved: {result['report_path']}")
    print(f"Done! Best clips saved in the '{result['output_folder']}' folder.")