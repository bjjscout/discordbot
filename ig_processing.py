import os
import subprocess
import json
import requests
import traceback
import sys
import io
import base64 # Potentially needed if Apify returns images/data differently

# Assuming transcribe_with_whisper is available (e.g., from ytsum)
try:
    from ytsum import transcribe_with_whisper
except ImportError:
    print("Warning: ytsum.transcribe_with_whisper not found. Transcription will fail.", file=sys.stderr)
    async def transcribe_with_whisper(*args, **kwargs): return "Import Error", "Import Error"

# --- yt-dlp Download Function ---
def download_instagram_video(url, output_base_path):
    """
    Downloads Instagram video, thumbnail, and description using yt-dlp.
    Uses explicit output paths and --print description.
    Returns paths to video, thumbnail (if found), description text, and any error.
    """
    video_path = None
    thumbnail_path = None
    description = None
    error = None

    output_dir = os.path.dirname(output_base_path)
    os.makedirs(output_dir, exist_ok=True)

    # Define output templates
    video_template = f"{output_base_path}.%(ext)s"
    # Define specific thumbnail template for --ppa to write to
    thumb_template = f"{output_base_path}_thumb.%(ext)s"
    # Expected final thumbnail path after conversion
    thumb_final_path = f"{output_base_path}_thumb.jpg"

    cmd = [
        'yt-dlp',
        '-S', "proto,ext:mp4:m4a,res,br", # Format selection
        '--write-thumbnail',
        '--convert-thumbnails', 'jpg',
        '--print', 'description', # Print description to stdout
        '-o', video_template, # Explicit video output path
        # Use PPA to control thumbnail output name *before* conversion
        # The converted file will replace the original extension with .jpg
        '--ppa', f"ThumbnailsConvertor:-o {thumb_template}",
        url
    ]

    print(f"Running yt-dlp command: {' '.join(cmd)}")
    
    try:
        # Run yt-dlp, capture stdout (description) and stderr
        process = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8')
        
        # Description is printed to stdout
        description = process.stdout.strip() if process.stdout else None
        if description: print("yt-dlp Description captured.")
        else: print("Warning: No description captured from yt-dlp stdout.")

        # Find the downloaded video file by iterating through common extensions
        found_vid = False
        for ext in ['mp4', 'm4a', 'avi', 'mov', 'mkv', 'webm']: # Added webm
             potential_video_path = f"{output_base_path}.{ext}"
             if os.path.exists(potential_video_path):
                 video_path = potential_video_path
                 print(f"Video file found: {video_path}")
                 found_vid = True
                 break
        if not found_vid:
             # This is likely an error if the process didn't fail
             error_msg = f"yt-dlp finished but video file not found at expected base path: {output_base_path}"
             print(f"Warning: {error_msg}")
             # Don't set error yet, maybe only thumbnail was downloaded? But unlikely for IG.
             # error = error_msg # Re-enable if this should halt execution

        # Check for the final converted thumbnail path
        if os.path.exists(thumb_final_path):
            thumbnail_path = thumb_final_path
            print(f"Thumbnail file found: {thumbnail_path}")
        else:
             print(f"Warning: Could not find converted thumbnail file: {thumb_final_path}")
             # Check if the *original* template path exists (before conversion)
             # This might happen if conversion failed silently
             found_orig_thumb = False
             for ext in ['jpg', 'png', 'webp']: # Common image extensions
                 potential_thumb_path = f"{output_base_path}_thumb.{ext}"
                 if os.path.exists(potential_thumb_path):
                      thumbnail_path = potential_thumb_path
                      print(f"Found original (unconverted?) thumbnail: {thumbnail_path}")
                      found_orig_thumb = True
                      break
             if not found_orig_thumb:
                  print("No thumbnail file found at expected paths.")


    except subprocess.CalledProcessError as e:
        error = f"yt-dlp failed with exit code {e.returncode}."
        print(f"{error}\nStderr: {e.stderr}", file=sys.stderr)
        # Clear potentially incorrect paths if yt-dlp failed
        video_path = None
        thumbnail_path = None
        description = None
    except Exception as e:
        error = f"An unexpected error occurred during yt-dlp execution: {str(e)}"
        print(error, file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        video_path = None
        thumbnail_path = None
        description = None

    # Return the found paths and description
    return video_path, thumbnail_path, description, error

# --- Transcription Function ---
def transcribe_ig_video(video_path):
    """Transcribes the downloaded Instagram video using Whisper."""
    if not video_path or not os.path.exists(video_path):
        return None, "Video path invalid or file does not exist."
    
    try:
        # Assuming transcribe_with_whisper takes a file path
        # It should return (plain_text, srt_text) or similar
        plain_transcript, srt_transcript = transcribe_with_whisper(video_path) 
        
        if not plain_transcript or len(plain_transcript.strip()) == 0:
             return "Transcription resulted in empty text.", None # Return error message as first element
             
        return plain_transcript, None # Return transcript and no error
    except Exception as e:
        error_msg = f"Error during transcription: {str(e)}"
        print(error_msg, file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return None, error_msg # Return None transcript and error message

# --- Apify Comment Fetching and Formatting ---
async def fetch_and_format_ig_comments(post_url, api_token):
    """Fetches comments from Apify and formats them."""
    api_endpoint = f"https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items?token={api_token}"
    payload = {
        "addParentData": False,
        "directUrls": [post_url], # Use the actual post URL
        "enhanceUserSearchWithFacebookPage": False,
        "isUserReelFeedURL": False,
        "isUserTaggedFeedURL": False,
        "onlyPostsNewerThan": "0 days", # Fetch all comments regardless of age for the specific post
        "resultsLimit": 200, # As specified
        "resultsType": "comments",
        "searchLimit": 1, # Limit search to the 1 URL provided
        "searchType": "hashtag" # Revert to 'hashtag' as per original example JSON
    }
    
    formatted_comments = ""
    error = None
    
    try:
        print(f"Calling Apify API for comments: {post_url}")
        response = requests.post(api_endpoint, json=payload, timeout=120) # Add timeout
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        comments_data = response.json()
        
        if not comments_data:
            print("Apify returned no comments.")
            return "", None # No comments found is not an error

        print(f"Received {len(comments_data)} comments/replies from Apify.")

        # Process top-level comments (ignore replies for now as per format)
        comment_list = []
        for item in comments_data:
             # Basic check for expected fields
             if 'ownerUsername' in item and 'text' in item:
                 username = item.get('ownerUsername', 'Unknown User')
                 text = item.get('text', '').strip()
                 likes = item.get('likesCount', 0) 
                 
                 if text: # Only include comments with text
                    comment_list.append(f"{username}\n{text}\nlikes: {likes}")
             else:
                  print(f"Skipping comment item due to missing fields: {item.get('id', 'N/A')}")

        formatted_comments = "\n\n".join(comment_list)
        print("Comments formatted successfully.")
        
    except requests.exceptions.Timeout:
        error = "Apify API request timed out."
        print(error, file=sys.stderr)
    except requests.exceptions.RequestException as e:
        error = f"Apify API request failed: {e}"
        print(error, file=sys.stderr)
        if hasattr(e, 'response') and e.response is not None:
            print(f"Apify Response Status: {e.response.status_code}", file=sys.stderr)
            print(f"Apify Response Body: {e.response.text}", file=sys.stderr)
    except json.JSONDecodeError:
        error = "Failed to parse JSON response from Apify."
        print(error, file=sys.stderr)
        # response object might not be available here if parsing failed early
    except Exception as e:
        error = f"An unexpected error occurred fetching comments: {str(e)}"
        print(error, file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        
    return formatted_comments, error

# --- Claude Prompt Formatting ---
def format_ig_claude_prompt(custom_title, plain_transcript, ig_description=None, additional_context=None, formatted_comments=None):
    """Formats the prompt for Claude API based on available data."""
    prompt_parts = []
    error = None

    if not custom_title:
        error = "Custom title is required for article generation."
        return None, error
        
    prompt_parts.append(f"Write a short blog post for calfkicker.com titled '{custom_title}'.")
    
    if additional_context:
        prompt_parts.append(f"Use the following context: {additional_context}")

    if ig_description:
        prompt_parts.append(f"\nThe Instagram post description was:\n```\n{ig_description}\n```")

    if plain_transcript:
        prompt_parts.append(f"\nBase the article on the following video transcript:\n```\n{plain_transcript}\n```")
    else:
        # If no transcript, maybe we shouldn't generate? Or adjust prompt?
        # For now, let's allow it but it might produce poor results.
         prompt_parts.append("\n(No video transcript was available)")


    if formatted_comments:
        prompt_parts.append(f"\nInclude reactions based on these comments from the post:\n```\n{formatted_comments}\n```")

    prompt_parts.append("\nEnsure the article is engaging and suitable for the website.")
    
    final_prompt = "\n".join(prompt_parts)
    
    return final_prompt, error