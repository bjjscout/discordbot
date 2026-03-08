import os
import sys
import asyncio
import gspread
from google.oauth2.service_account import Credentials
import subprocess
import uuid
import io
import json
import requests
from concurrent.futures import ThreadPoolExecutor
import traceback
import time # Needed for sleep
import openai # Added for OpenAI and DeepSeek APIs
import google.generativeai as genai # Added for Gemini API
import argparse # Added for command-line arguments

# --- Environment Setup ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from dotenv import load_dotenv
load_dotenv()

# --- Imports from other project modules ---
# Add robust error handling for imports - try multiple sources
upload_to_r2 = None

try:
    from app4 import upload_to_r2
except ImportError:
    try:
        from r2upload import upload_file_to_r2 as upload_to_r2
        print("INFO: Using upload_file_to_r2 from r2upload as fallback")
    except ImportError:
        print("WARNING: Could not import upload_to_r2. R2 uploads may not work.", file=sys.stderr)
        # Define a stub function so imports don't crash
        def upload_to_r2(*args, **kwargs):
            print("ERROR: upload_to_r2 not available - app4 and r2upload not found")
            return None

try:
    from ytsum import call_claude_api
except ImportError:
    print("WARNING: Could not import call_claude_api from ytsum. Article generation may not work.", file=sys.stderr)
    # Define a stub function
    def call_claude_api(*args, **kwargs):
        print("ERROR: call_claude_api not available")
        return None

try:
    from tweet_processing import (
        fetch_tweet_data,
        transcribe_video,
        fetch_and_format_replies,
        format_claude_prompt,
        get_tweet_id_from_url
    )
except ImportError:
    print("WARNING: Could not import functions from tweet_processing.", file=sys.stderr)
    # Define stubs
    def fetch_tweet_data(*args, **kwargs): return None
    def transcribe_video(*args, **kwargs): return None
    def fetch_and_format_replies(*args, **kwargs): return None
    def format_claude_prompt(*args, **kwargs): return None
    def get_tweet_id_from_url(*args, **kwargs): return None

# --- Constants ---
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = r'C:\Vidmaker\analog-context-429106-f0-29949c2ce03a.json'
SPREADSHEET_ID = '1IM8l0QA2hs9iJMacUQAeyr46OvX5ORNVEYy6GKHMN2g'
WORKSHEET_NAME = 'tweetscan' # Adjust if the sheet name is different
TWEET_WEBHOOK_URL = os.getenv('TWEET_WEBHOOK_URL', "https://n8n.jeffrey-epstein.com/webhook/fde498fe-a99c-4e73-8440-4b42baae09b1")

# Column indices (0-based for list access)
COL_URL = 1         # Column B
COL_CONVERT = 9     # Column J
COL_TITLE = 10      # Column K
COL_ADD_CONTEXT = 11 # Column L
COL_INC_REPLIES = 12 # Column M
COL_CONVERT_DONE = 13 # Column N

# ThreadPoolExecutor for background tasks
executor = ThreadPoolExecutor(max_workers=5)

# --- Google Sheets Functions ---
def get_google_sheet():
    """Authenticates and returns the Google Sheet worksheet."""
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(WORKSHEET_NAME)
        print("Successfully connected to Google Sheet.")
        return sheet
    except FileNotFoundError:
        print(f"ERROR: Service account file not found at {SERVICE_ACCOUNT_FILE}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error connecting to Google Sheets: {e}", file=sys.stderr)
        return None
# Function to call OpenAI API (Copied from aiwriter.py)
def call_openai_api(prompt_text, model_name="gpt-4.1"):
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("Error: OPENAI_API_KEY not found in environment variables. Cannot call OpenAI.")
            return None
        
        client = openai.OpenAI(api_key=api_key)
        
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "user", "content": prompt_text}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error calling OpenAI API (model: {model_name}): {str(e)}")
        print(traceback.format_exc())
        return None

# Function to call Gemini API (Copied from aiwriter.py)
def call_gemini_api(prompt_text, model_name="gemini-2.5-pro-exp-03-25"):
    try:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print("Error: GEMINI_API_KEY not found in environment variables. Cannot call Gemini.")
            return None
        
        genai.configure(api_key=api_key)
        
        model = genai.GenerativeModel(model_name) 
        response = model.generate_content(prompt_text)
        
        if not response.candidates:
            print(f"Gemini API call (model: {model_name}) returned no candidates. This might be due to safety filters or an issue with the prompt.")
            if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                print(f"Prompt Feedback: {response.prompt_feedback}")
            return None

        if response.candidates[0].content and response.candidates[0].content.parts:
            return "".join(part.text for part in response.candidates[0].content.parts if hasattr(part, 'text'))
        else:
            print(f"Gemini API response (model: {model_name}) did not contain expected text parts.")
            return None

    except Exception as e:
        print(f"Error calling Gemini API (model: {model_name}): {str(e)}")
        print(traceback.format_exc())
        return None

# Function to call DeepSeek API (using OpenAI compatible client) (Copied from aiwriter.py)
def call_deepseek_api(prompt_text, model_name="deepseek-reasoner"):
    try:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            print("Error: DEEPSEEK_API_KEY not found in environment variables. Cannot call DeepSeek.")
            return None
        
        client = openai.OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com" # DeepSeek API endpoint
        )
        
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "user", "content": prompt_text}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error calling DeepSeek API (model: {model_name}): {str(e)}")
        print(traceback.format_exc())
        return None

# --- Webhook Function ---
async def send_tweetsheet_webhook(results, row_number, user_id):
    """Sends the processed data to the webhook, including the row number."""
    if not results.get("article_r2_url") or not results.get("embed_code") or not results.get("custom_title"):
        print(f"Row {row_number}: Skipping webhook - missing article_r2_url, embed_code, or custom_title.")
        return False, "Missing data for webhook"

    # Ensure article content exists, even if empty, to avoid KeyError
    article_body = results.get("article_content", "")
    if results.get("embed_code"): # Append embed code if it exists
         article_body += f"\n\n{results['embed_code']}"

    payload = {
        "blog": "calf", # Hardcoded to calf based on sheet name
        "Title": str(results["custom_title"]),
        "Body": str(article_body),
        "image": str(results.get("thumbnail_url", "")),
        "user_id": str(user_id),
        "row": row_number # Add the row number
    }

    print(f"Row {row_number}: Sending webhook payload: {{ Title: '{payload['Title']}', Blog: '{payload['blog']}', Row: {payload['row']}, ... }}")

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            executor,
            lambda: requests.post(TWEET_WEBHOOK_URL, json=payload, timeout=45) # Increased timeout
        )
        response.raise_for_status()
        print(f"Row {row_number}: Successfully sent webhook request. Status: {response.status_code}, Response: {response.text[:100]}...")
        return True, response.text
    except requests.exceptions.RequestException as e:
        error_msg = f"Failed to send webhook request: {str(e)}"
        print(f"Row {row_number}: {error_msg}", file=sys.stderr)
        if hasattr(e, 'response') and e.response is not None:
            print(f"Row {row_number}: Error response status code: {e.response.status_code}", file=sys.stderr)
            print(f"Row {row_number}: Error response body: {e.response.text}", file=sys.stderr)
            error_msg += f" - Status: {e.response.status_code}, Body: {e.response.text[:100]}..."
        return False, error_msg

# --- Main Processing Logic ---
async def process_single_row(row_number, row_data, sheet, ai_provider="claude", progress_callback=None, user_id=None): # Added ai_provider and made user_id optional
    """Processes a single row from the Google Sheet."""
    loop = asyncio.get_event_loop()
    results = {} # Store results like URLs, embed code, article content
    article_content = None # Initialize article_content

    try:
        # --- 1. Extract Data ---
        url = row_data[COL_URL]
        custom_title = row_data[COL_TITLE] if len(row_data) > COL_TITLE else None
        additional_context = row_data[COL_ADD_CONTEXT] if len(row_data) > COL_ADD_CONTEXT else None
        fetch_replies_flag = str(row_data[COL_INC_REPLIES]).strip().lower() == 'y' if len(row_data) > COL_INC_REPLIES else False

        if not url or not custom_title:
            await progress_callback(f"Row {row_number}: Skipping - Missing URL (Col B) or Title (Col K).")
            return

        await progress_callback(f"Row {row_number}: Processing URL: {url} | Title: '{custom_title}'")
        results["custom_title"] = custom_title

        # --- 2. Fetch Tweet Info ---
        await progress_callback(f"Row {row_number}: Fetching tweet info...")
        api_key = os.getenv('RAPIDAPI_KEY')
        if not api_key: raise ValueError("RAPIDAPI_KEY not set")

        tweet_data = await loop.run_in_executor(executor, lambda: fetch_tweet_data(url, api_key))
        if tweet_data.get("error"): raise Exception(f"Fetch Tweet Data Error: {tweet_data['error']}")

        highest_quality_url = tweet_data["highest_quality_url"]
        tweet_title = tweet_data["tweet_title"] # Original tweet title for context
        results["thumbnail_url"] = tweet_data["thumbnail_url"]
        if not highest_quality_url: raise Exception("Could not extract video URL.")
        await progress_callback(f"Row {row_number}: Tweet info fetched.")

        # --- 3. Transcribe Video ---
        await progress_callback(f"Row {row_number}: Transcribing video...")
        plain_transcript, _, transcribe_error = await loop.run_in_executor(executor, lambda: transcribe_video(highest_quality_url))
        if transcribe_error: await progress_callback(f"Row {row_number}: Transcription Warning: {transcribe_error}")
        if not plain_transcript or plain_transcript == "Transcription failed due to an error.":
            plain_transcript = "Transcription not available."
            await progress_callback(f"Row {row_number}: Transcription failed or unavailable.")
        else:
            await progress_callback(f"Row {row_number}: Transcription successful.")

        # --- 4. Upload Transcript ---
        transcript_r2_url = None
        if plain_transcript != "Transcription not available.":
            await progress_callback(f"Row {row_number}: Uploading transcript...")
            try:
                transcript_filename = f"sheet_transcript_{row_number}_{uuid.uuid4()}"
                transcript_r2_url = await loop.run_in_executor(
                    executor, lambda: upload_to_r2(io.BytesIO(plain_transcript.encode('utf-8')), transcript_filename, 'txt')
                )
                await progress_callback(f"Row {row_number}: Transcript uploaded: {transcript_r2_url}")
                results["transcript_r2_url"] = transcript_r2_url # Store for potential debugging/logging
            except Exception as e:
                await progress_callback(f"Row {row_number}: Error uploading transcript: {e}")

        # --- 5. Fetch/Upload Replies ---
        tweet_replies = None
        replies_r2_url = None
        if fetch_replies_flag:
            await progress_callback(f"Row {row_number}: Fetching replies...")
            tweet_id = get_tweet_id_from_url(url)
            if tweet_id:
                replies_api_key = "86bbdd1d0e7d4fe695f9848eeb513965"
                formatted_replies, replies_error = await loop.run_in_executor(
                    executor, lambda: fetch_and_format_replies(tweet_id, replies_api_key)
                )
                if replies_error:
                    await progress_callback(f"Row {row_number}: Error fetching replies: {replies_error}")
                elif formatted_replies:
                    tweet_replies = formatted_replies
                    await progress_callback(f"Row {row_number}: Replies fetched. Uploading...")
                    try:
                        replies_filename = f"sheet_replies_{row_number}_{uuid.uuid4()}"
                        replies_r2_url = await loop.run_in_executor(
                            executor, lambda: upload_to_r2(io.BytesIO(tweet_replies.encode('utf-8')), replies_filename, 'txt')
                        )
                        await progress_callback(f"Row {row_number}: Replies uploaded: {replies_r2_url}")
                        results["replies_r2_url"] = replies_r2_url
                    except Exception as e:
                        await progress_callback(f"Row {row_number}: Error uploading replies: {e}")
                else:
                     await progress_callback(f"Row {row_number}: No replies found or formatted.")
            else:
                await progress_callback(f"Row {row_number}: Could not get Tweet ID for replies.")

        # --- 6. Upload to Raptive (Calf) ---
        embed_code = None
        await progress_callback(f"Row {row_number}: Uploading video to Calf Raptive via calfupload.py...")
        try:
            raptive_title = custom_title # Use the title from the sheet
            script_path = os.path.join(current_dir, "calfupload.py")
            if not os.path.exists(script_path):
                 raise FileNotFoundError(f"Raptive script not found: {script_path}")

            process = await asyncio.create_subprocess_exec(
                sys.executable, script_path, highest_quality_url, "-title", raptive_title,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            exit_code = process.returncode
            output = stdout.decode(errors='ignore').strip()
            stderr_output = stderr.decode(errors='ignore').strip()

            if exit_code == 0:
                # Search all output lines for embed code pattern
                output_lines = output.splitlines()
                embed_code = None
                for line in reversed(output_lines):  # Search from bottom up
                    if line.startswith('[adthrive-in-post-video-player'):
                        embed_code = line
                        break
                
                if embed_code:
                    await progress_callback(f"Row {row_number}: Raptive upload successful. Embed: {embed_code}")
                    results["embed_code"] = embed_code
                else:
                    await progress_callback(f"Row {row_number}: Raptive script ran but no embed code found in output. Full output:\n```\n{output[:500]}...\n```")

            else:
                await progress_callback(f"Row {row_number}: Raptive upload script failed (Exit Code: {exit_code}). Stderr: {stderr_output}")
        except Exception as e:
            await progress_callback(f"Row {row_number}: Error running Raptive upload script: {e}")
            traceback.print_exc(file=sys.stderr)


        # --- 7. Generate Article ---
        article_r2_url = None
        article_content = None
        if not embed_code:
            await progress_callback(f"Row {row_number}: Skipping article generation - no embed code.")
        elif plain_transcript == "Transcription not available.":
             await progress_callback(f"Row {row_number}: Skipping article generation - no transcript.")
        else:
            await progress_callback(f"Row {row_number}: Generating article...")
            try:
                # Generate AI response based on provider
                ai_response = None
                ai_model_used = ""

                # This prompt is used for all providers
                ai_prompt = format_claude_prompt(custom_title, plain_transcript, tweet_title, additional_context, tweet_replies)

                try:
                    if ai_provider == "openai":
                        await progress_callback(f"Row {row_number}: Calling OpenAI API using model gpt-4.1...")
                        ai_response = await loop.run_in_executor(executor, lambda: call_openai_api(ai_prompt, model_name="gpt-4.1"))
                        ai_model_used = "OpenAI (gpt-4.1)"
                    elif ai_provider == "gemini":
                        await progress_callback(f"Row {row_number}: Calling Gemini API using model gemini-2.5-pro-exp-03-25...")
                        ai_response = await loop.run_in_executor(executor, lambda: call_gemini_api(ai_prompt, model_name="gemini-2.5-pro-exp-03-25"))
                        ai_model_used = "Gemini (gemini-2.5-pro-exp-03-25)"
                    elif ai_provider == "deepseek":
                        await progress_callback(f"Row {row_number}: Calling DeepSeek API using model deepseek-reasoner...")
                        ai_response = await loop.run_in_executor(executor, lambda: call_deepseek_api(ai_prompt, model_name="deepseek-reasoner"))
                        ai_model_used = "DeepSeek (deepseek-reasoner)"
                    elif ai_provider == "claude":
                        await progress_callback(f"Row {row_number}: Calling Claude API using model sonnet...")
                        claude_response = await loop.run_in_executor(executor, lambda: call_claude_api(ai_prompt, model="sonnet"))
                        # call_claude_api might return dict or string, ensure we get the text
                        if isinstance(claude_response, dict):
                            ai_response = claude_response.get('text', '')
                        elif isinstance(claude_response, str):
                            ai_response = claude_response
                        else:
                            await progress_callback(f"Row {row_number}: Unexpected Claude API response format: {type(claude_response)}")
                            update_sheet_cell(row_number, COL_CONVERT_DONE + 1, f"Claude API bad response for row {row_number}") # Mark as failed
                            return # Exit if response is bad
                        ai_model_used = "Claude (sonnet)"
                    else:
                        await progress_callback(f"Row {row_number}: Unknown AI provider: {ai_provider}")
                        update_sheet_cell(row_number, COL_CONVERT_DONE + 1, f"Unknown AI provider: {ai_provider}") # Mark as failed
                        return # Exit if provider is unknown

                    if not ai_response or len(ai_response.strip()) == 0:
                        await progress_callback(f"Row {row_number}: {ai_model_used} call failed or returned empty content.")
                        update_sheet_cell(row_number, COL_CONVERT_DONE + 1, f"{ai_model_used} call failed for row {row_number}") # Mark as failed
                        return # Exit if no response

                    await progress_callback(f"Row {row_number}: Article generated using {ai_model_used}. Uploading...")
                    article_content = ai_response # Store raw content
                    results["article_content"] = article_content
                    article_to_upload = article_content + f"\n\n{embed_code}" # Append embed code for upload
                    
                    try:
                        article_filename = f"sheet_article_{row_number}_{uuid.uuid4()}"
                        article_r2_url = await loop.run_in_executor(
                            executor, lambda: upload_to_r2(io.BytesIO(article_to_upload.encode('utf-8')), article_filename, 'txt')
                        )
                        results["article_r2_url"] = article_r2_url
                        await progress_callback(f"Row {row_number}: Article uploaded: {article_r2_url}")
                    except Exception as e:
                        await progress_callback(f"Row {row_number}: Error uploading article: {e}")
                        update_sheet_cell(row_number, COL_CONVERT_DONE + 1, f"Article upload failed for row {row_number}: {e}") # Mark as failed
                        return # Exit on upload error

                except Exception as e:
                    await progress_callback(f"Row {row_number}: Error during AI article generation: {e}")
                    traceback.print_exc(file=sys.stderr)
                    update_sheet_cell(row_number, COL_CONVERT_DONE + 1, f"AI generation failed for row {row_number}: {e}") # Mark as failed
                    return # Exit on generation error
            except Exception as e:
                await progress_callback(f"Row {row_number}: Error during article generation: {e}")
                traceback.print_exc(file=sys.stderr)

        # --- 8. Send Webhook & Update Sheet ---
        webhook_success, webhook_response = await send_tweetsheet_webhook(results, row_number, user_id)

        if webhook_success:
            try:
                # Use 1-based indexing for gspread cell update
                sheet.update_cell(row_number, COL_CONVERT_DONE + 1, 'y')
                await progress_callback(f"Row {row_number}: Marked as 'Done' in sheet.")
            except Exception as e:
                await progress_callback(f"Row {row_number}: Webhook sent, but FAILED to update 'Convert Done' column: {e}")
        else:
             await progress_callback(f"Row {row_number}: Webhook failed. Sheet not updated. Reason: {webhook_response}")

        await progress_callback(f"--- Row {row_number} Processing Finished ---")

    except Exception as e:
        await progress_callback(f"Row {row_number}: **** FAILED PROCESSING ROW **** Error: {e}")
        traceback.print_exc(file=sys.stderr) # Log full traceback for debugging

async def main(ai_provider="claude", progress_callback=None, user_id=None): # Added ai_provider and made user_id optional
    """Main function to process the Google Sheet."""
    if progress_callback: await progress_callback("Connecting to Google Sheet...")
    sheet = get_google_sheet()
    if not sheet:
        if progress_callback: await progress_callback("Failed to connect to Google Sheet. Aborting.")
        return

    await progress_callback("Fetching all sheet data...")
    try:
        # Consider adding retry logic here if sheet access is flaky
        all_data = sheet.get_all_values()
        await progress_callback(f"Found {len(all_data) - 1} data rows to check.")
    except Exception as e:
         await progress_callback(f"Error reading sheet data: {e}")
         return

    processed_count = 0
    # Start from row 2 (index 1) to skip header
    for i, row in enumerate(all_data[1:], start=2):
        try:
            # Basic validation and flag checking
            if len(row) <= max(COL_CONVERT, COL_CONVERT_DONE):
                continue # Skip rows that are too short

            convert_flag = str(row[COL_CONVERT]).strip().lower()
            done_flag = str(row[COL_CONVERT_DONE]).strip().lower()

            # Skip if already processed (Column N not empty) or not marked for conversion
            if done_flag:  # Column N is not empty
                continue
            elif convert_flag == 'y':
                if progress_callback: await progress_callback(f"--- Found row {i} marked for conversion ---")
                # Pass the ai_provider to process_single_row
                await process_single_row(i, row, sheet, ai_provider=ai_provider, progress_callback=progress_callback, user_id=user_id)
                processed_count += 1
                await asyncio.sleep(5) # Delay between processing rows to avoid rate limits
            # else:
            #     print(f"Row {i}: Skipping (Convert='{convert_flag}', Done='{done_flag}')")

        except Exception as e:
             # Catch errors processing a specific row's flags/data before calling process_single_row
             await progress_callback(f"Row {i}: Error checking row flags: {e}")
             traceback.print_exc(file=sys.stderr)


    await progress_callback(f"Sheet processing finished. Processed {processed_count} rows marked with 'y'.")

# --- Allow standalone execution for testing ---
if __name__ == "__main__":
    async def cli_progress(message):
        print(f"PROGRESS: {message}")

    print("Running tweetsheet.py standalone for testing...")
    test_user_id = "standalone_test"
    # Ensure event loop is handled correctly for standalone run
    try:
        asyncio.run(main(cli_progress, test_user_id))
    except KeyboardInterrupt:
        print("\nStandalone execution interrupted.")
    except Exception as e:
        print(f"\nError during standalone execution: {e}")
        traceback.print_exc()
    finally:
        # Ensure executor shuts down cleanly
        executor.shutdown(wait=True)
        print("Executor shutdown.")
    print("Standalone execution finished.")