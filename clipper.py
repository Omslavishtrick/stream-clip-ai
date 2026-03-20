from moviepy.editor import VideoFileClip

video = VideoFileClip("video.mp4")

# automatically get the last 5 seconds
start = max(0, video.duration - 5)
clip = video.subclip(start, video.duration)

clip.write_videofile("clip.mp4")
