# Vincent character assets

These assets derive from the team's approved Seedance greeting video for Talk with Vincent. They depict an AI interpretation of Vincent van Gogh. The original AI-generation mark is retained. The original reference illustration and source video are kept outside the repository; only the approved web derivatives are included here.

| File | Purpose | Format | Bytes |
| --- | --- | --- | ---: |
| `greeting.mp4` | One greeting per page entry | 834 × 1112, 3:4, H.264 / yuv420p, 24 fps, 4 seconds, no audio track | 437,990 |
| `greeting-poster.webp` | First frame displayed while the video loads | 834 × 1112, WebP | 134,004 |
| `listening.webp` | Stable pose after the greeting and during recording or processing | 834 × 1112, WebP | 139,270 |

The source video's SHA-256 is `61b8d7f4a17789bffe0f1e5ce5ce275aac5cba27623e0689547435e528811960`. The four-second derivative removes its audio and reduces the video size by approximately 93%. The ending intentionally retains the accepted composition, including fingertips extending beyond the bottom edge. Both images come from the compressed video, so framing and colors match at the transitions. Use `object-fit: contain` for the video and portrait.

## Reproduction

FFmpeg 7.0.2 with libx264 and libwebp produced these files. It is an asset preparation tool, not an application dependency. Run these commands in a separate output directory with the approved source supplied as `source.mp4`:

```bash
ffmpeg -i source.mp4 -map 0:v:0 -an -t 4 -vf fps=24 \
  -c:v libx264 -preset medium -crf 25 -pix_fmt yuv420p \
  -movflags +faststart -map_metadata -1 greeting.mp4
ffmpeg -i greeting.mp4 -vf 'select=eq(n\,0)' -frames:v 1 \
  -c:v libwebp -quality 90 greeting-poster.webp
ffmpeg -i greeting.mp4 -vf 'select=eq(n\,95)' -frames:v 1 \
  -c:v libwebp -quality 90 listening.webp
```

`+faststart` places playback metadata before the video data. The browser also sets muted inline playback. Neither the video nor its completion event starts, stops or replaces welcome speech. Real voice is controlled separately through the conversation's voice controller.

The deployed files use SHA-256:

```text
33ef491ed1d1f580a9611aeb2dc4fb3f47c3400c44cc5e79d2e8887161474247  greeting.mp4
a72738634aa85730ec2f4951573e9218394adf43a97a8ecc84df32caff589d9d  greeting-poster.webp
32cc47e8c5f24a7286e5eaac58bdb2294d4146e0b23a6f9dc842335ad21b32ef  listening.webp
```
