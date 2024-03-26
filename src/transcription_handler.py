# transcription_handler.py

import time
import logging
import re
import asyncio
import json
import os

# Toggle this to use the full description or a snippet.
USE_SNIPPET_FOR_DESCRIPTION = False

# If we're using a snippet of the description, maximum number of lines to include
DESCRIPTION_MAX_LINES = 30

# Output directory for transcriptions; create if doesn't exist
output_dir = "transcriptions"
os.makedirs(output_dir, exist_ok=True)

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

async def download_audio(url, output_path):
    logger.info(f"Attempting to download audio from: {url}")
    command = ["yt-dlp", "--extract-audio", "--audio-format", "mp3", url, "-o", output_path]
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await process.communicate()
    
    if os.path.exists(output_path):
        logger.info(f"Audio downloaded successfully: {output_path}")
    else:
        logger.error(f"Failed to download audio: {output_path}")

# transcription logic
async def transcribe_audio(audio_path, output_dir, youtube_url):
    logger.info(f"Starting transcription for: {audio_path}")
    
    transcription_command = ["whisper", audio_path, "--output_dir", output_dir]
    # transcription_command = ["whisper", audio_path, "--model", "medium-v3", "--output_dir", output_dir]

    process = await asyncio.create_subprocess_exec(
        *transcription_command, 
        stdout=asyncio.subprocess.PIPE, 
        stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await process.communicate()

    # Check if Whisper process encountered an error
    if process.returncode != 0:
        logger.error(f"Whisper process failed with return code {process.returncode}")
        logger.error(f"Whisper STDERR: {stderr.decode()}")
    else:
        logger.info(f"Whisper transcription completed for: {audio_path}")

    # Verify and log the generated files
    base_filename = os.path.splitext(os.path.basename(audio_path))[0]
    transcript_files = {
        'txt': f"{output_dir}/{base_filename}.txt",
        'srt': f"{output_dir}/{base_filename}.srt",
        'vtt': f"{output_dir}/{base_filename}.vtt",
    }

    created_files = {}
    for fmt, path in transcript_files.items():
        if os.path.exists(path) and os.path.getsize(path) > 0:
            logger.info(f"Transcription file created: {path}")
            created_files[fmt] = path
        else:
            logger.warning(f"Expected transcription file not found or empty: {path}")

    return created_files

# Process the message's URL and keep the user informed
async def process_url_message(message_text, bot, update):
    urls = re.findall(r'(https?://\S+)', message_text)
    for url in urls:
        if not re.match(YOUTUBE_REGEX, url):
            await bot.send_message(chat_id=update.effective_chat.id, text="Skipping non-YouTube URL.")
            continue

        # Inform user that URL has been recognized and processing will begin
        await bot.send_message(chat_id=update.effective_chat.id, text="Processing YouTube URL...")

        video_id = extract_youtube_video_id(url)
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Fetch YouTube video details and inform the user
        await bot.send_message(chat_id=update.effective_chat.id, text="Fetching YouTube video details...")
        details = await fetch_youtube_details(youtube_url)

        if not details:
            await bot.send_message(chat_id=update.effective_chat.id, text="Failed to fetch video details.")
            continue

        # Inform user about the video being processed
        title = details.get('title', 'No title available')
        await bot.send_message(chat_id=update.effective_chat.id, text=f"Title: {title}\nDownloading audio for transcription...")

        audio_path = f"{video_id}.mp3"
        output_dir = "transcriptions"
        await download_audio(youtube_url, audio_path)

        if not os.path.exists(audio_path):
            await bot.send_message(chat_id=update.effective_chat.id, text="Audio file could not be downloaded.")
            continue

        # Inform user that transcription has started
        await bot.send_message(chat_id=update.effective_chat.id, text="Transcribing audio...")
        transcription_paths = await transcribe_audio(audio_path, output_dir, youtube_url)

        if not transcription_paths:
            await bot.send_message(chat_id=update.effective_chat.id, text="Failed to transcribe audio.")
            os.remove(audio_path)
            continue

        # Inform user that transcription files are being sent
        await bot.send_message(chat_id=update.effective_chat.id, text="Sending transcription files...")
        for fmt, path in transcription_paths.items():
            await bot.send_document(chat_id=update.effective_chat.id, document=open(path, 'rb'))
        
        os.remove(audio_path)  # Clean up the audio file after sending the files

        # The closing message
        await bot.send_message(chat_id=update.effective_chat.id, text="There ya go, have a nice day! :-)")

# Helper function to format duration from seconds to H:M:S
def format_duration(duration):
    if not duration:
        return 'No duration available'
    hours, remainder = divmod(duration, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    else:
        return f"{minutes}m {seconds}s"

# i.e. for youtube videos
async def fetch_youtube_details(url, max_retries=3, base_delay=5):
    command = ["yt-dlp", "--user-agent",
               "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
               "--dump-json", url]

    for attempt in range(max_retries):
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if stderr and process.returncode != 0:
            logger.warning(f"Attempt {attempt + 1} failed: {stderr.decode()}")
            if attempt < max_retries - 1:
                wait_time = base_delay * (2 ** attempt)  # Exponential backoff
                logger.info(f"Retrying after {wait_time} seconds...")
                await asyncio.sleep(wait_time)
            else:
                logger.error("All retry attempts failed.")
        else:
            try:
                video_details = json.loads(stdout.decode())
                duration_formatted = format_duration(video_details.get('duration'))                

                if USE_SNIPPET_FOR_DESCRIPTION:
                    # Get the snippet if the flag is set to True.
                    description_text = get_description_snippet(video_details.get('description', 'No description available'))
                else:
                    # Use the full description if the flag is set to False.
                    description_text = video_details.get('description', 'No description available')

                filtered_details = {
                    'title': video_details.get('title', 'No title available'),
                    # 'duration': video_details.get('duration', 'No duration available'),
                    'duration': duration_formatted,                    
                    'channel': video_details.get('uploader', 'No channel information available'),
                    'upload_date': video_details.get('upload_date', 'No upload date available'),
                    'views': video_details.get('view_count', 'No views available'),
                    'likes': video_details.get('like_count', 'No likes available'),
                    'average_rating': video_details.get('average_rating', 'No rating available'),
                    'comment_count': video_details.get('comment_count', 'No comment count available'),
                    'channel_id': video_details.get('channel_id', 'No channel ID available'),
                    'video_id': video_details.get('id', 'No video ID available'),
                    'tags': video_details.get('tags', ['No tags available']),
                    'description': description_text,
                }

                logger.info(f"Fetched YouTube details successfully for URL: {url}")
                return filtered_details
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding JSON from yt-dlp output: {e}")
                return None
    return None

# Helper function to get up to n lines from the description
def get_description_snippet(description, max_lines=DESCRIPTION_MAX_LINES):
    lines = description.split('\n')
    snippet = '\n'.join(lines[:max_lines])
    return snippet

# Regular expression for extracting the YouTube video ID
YOUTUBE_REGEX = (
    r'(https?://)?(www\.)?'
    '(youtube|youtu|youtube-nocookie)\.(com|be)/'
    '(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})')

def extract_youtube_video_id(url):
    match = re.match(YOUTUBE_REGEX, url)
    if not match:
        raise ValueError("Invalid YouTube URL")
    return match.group(6)