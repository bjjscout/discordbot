import os
from googleapiclient.discovery import build
from google.oauth2 import service_account
from dotenv import load_dotenv
from ytsum import get_video_id, fetch_transcript, call_claude_api
from app4 import upload_to_r2
import io
import requests
from urllib.parse import urlparse
import openai # Added for OpenAI and DeepSeek APIs
import google.generativeai as genai # Added for Gemini API
import argparse # Added for command-line arguments
import traceback # Added for error details

# Load environment variables
load_dotenv()

# MAKE.COM Webhook URL 
WEBHOOK_URL = "https://hook.us1.make.com/43tt8qin7xypeemwchomdd4ujyeid8yy"

# N8N Webhook URL to insert internal links 
#WEBHOOK_URL = "https://n8n.jeffrey-epstein.com/webhook/7bb32413-0c48-4732-89ff-29dff1913f0b"

# Set up Google Sheets API
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
SHEET_NAME = 'YT Script Rewriter'
RANGE_NAME = f"'{SHEET_NAME}'!A2:H"
creds = service_account.Credentials.from_service_account_file(
    r'C:\Vidmaker\analog-context-429106-f0-29949c2ce03a.json',
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
service = build('sheets', 'v4', credentials=creds)

def send_webhook_request(blog, title, body, row):
    data = {
        "blog": blog,
        "Title": title,
        "Body": body,
        "row": row,
        "source": "ytwriter"
    }
    response = requests.post(WEBHOOK_URL, json=data)
    return response.status_code == 200

def get_sheet_data():
    sheet = service.spreadsheets()
    result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME).execute()
    return result.get('values', [])

def update_sheet_cell(row, col, value):
    range_name = f"'{SHEET_NAME}'!{col}{row}"
    body = {'values': [[value]]}
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID, range=range_name,
        valueInputOption='RAW', body=body).execute()

def get_transcript(link):
    parsed_url = urlparse(link)
    
    if parsed_url.path.endswith('.txt'):
        # Direct download link for transcript
        try:
            response = requests.get(link)
            response.raise_for_status()  # Raises an HTTPError for bad responses
            return response.text
        except requests.RequestException as e:
            print(f"Failed to download transcript from {link}. Error: {e}")
            return None
    else:
        # Assume it's a YouTube link
        video_id = get_video_id(link)
        if not video_id:
            print(f"Invalid YouTube URL: {link}")
            return None
        _, plain_transcript, _ = fetch_transcript(video_id)
        return plain_transcript
# Function to call OpenAI API (Copied from aiwriter.py)
def call_openai_api(prompt_text, model_name="gpt-4.1-mini"):
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
def call_gemini_api(prompt_text, model_name="gemini-2.5-flash-preview-05-20"):
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

def process_row(row_number, row_data, ai_provider="claude", progress_callback=None):
    link = row_data[0]
    site = row_data[1].lower() if len(row_data) > 1 else ''
    title = row_data[2] if len(row_data) > 2 else ''
    prompt = row_data[3] if len(row_data) > 3 else ''

    if progress_callback:
        progress_callback(f"Processing row {row_number}: {link}")

    # Skip if column E is not empty
    if len(row_data) > 4 and row_data[4]:
        if progress_callback:
            progress_callback(f"Skipping row {row_number} as column E is not empty")
        return

    # Get transcript
    transcript = get_transcript(link)
    if not transcript:
        print(f"Failed to get transcript for {link}")
        return

    # Generate AI response based on provider
    ai_response = None
    ai_model_used = ""

    # This prompt is used for all providers
    ai_prompt = f"""
    You have been provided with a transcript where you are to write a 300 to 500 word article corresponding to this title: {title}.

    {prompt}

    Transcript:
    {transcript}

   Do not give me word counts. Do not repeat my instructions back to me. Only give me your output. Do not invent, paraphase or change the wording of quotes. Do not deviate and cover other topics in subheadings. Avoid the use of M dashes. Clean up grammar in quotes, don't change wording. Eliminate all ai cliche phrases and sentences. Make all weight in lbs but leave kilogram value in ()s. ALL OUTPUT ONLY IN ENGLISH.

    Avoid using any of the following words outside of quotes Addicting/Addiction Bomb Booze/Boozy Cheat/Cheating Click Crime Die/Died/Dead Download [Drug name] Drunk/Drunken Execute/Execution Explode/Explosion Extreme Gun Hangover/Hungover Hate/Hatred Hell Insane Jerk Kill/Killer Naked [Profanity/Expletive] Shoot Shot/Shots Sober Stream Stupid Substance Suffer Torture Victim Shocking Brutal
"""

    try:
        if ai_provider == "openai":
            if progress_callback: progress_callback(f"Calling OpenAI API for row {row_number} using model gpt-4.1-mini...")
            ai_response = call_openai_api(ai_prompt, model_name="gpt-4.1-mini")
            ai_model_used = "OpenAI (gpt-4.1-mini)"
        elif ai_provider == "gemini":
            if progress_callback: progress_callback(f"Calling Gemini API for row {row_number} using model gemini-2.5-flash-preview-05-20...")
            ai_response = call_gemini_api(ai_prompt, model_name="gemini-2.5-flash-preview-05-20")
            ai_model_used = "Gemini (gemini-2.5-flash-preview-05-20)"
        elif ai_provider == "deepseek":
            if progress_callback: progress_callback(f"Calling DeepSeek API for row {row_number} using model deepseek-reasoner...")
            ai_response = call_deepseek_api(ai_prompt, model_name="deepseek-reasoner")
            ai_model_used = "DeepSeek (deepseek-reasoner)"
        elif ai_provider == "claude":
            if progress_callback: progress_callback(f"Calling Claude API for row {row_number} using model claude-4.5...")
            claude_response = call_claude_api(ai_prompt, model="sonnet")
            # call_claude_api might return dict or string, ensure we get the text
            if isinstance(claude_response, dict):
                ai_response = claude_response.get('text', '')
            elif isinstance(claude_response, str):
                ai_response = claude_response
            else:
                 print(f"Unexpected Claude API response format for row {row_number}: {type(claude_response)}")
                 if progress_callback: progress_callback(f"Error: Unexpected Claude API response format for row {row_number}")
                 update_sheet_cell(row_number, 'G', f"Claude API bad response for row {row_number}")
                 return # Exit if response is bad
            ai_model_used = "Claude (sonnet)"
        else:
            print(f"Unknown AI provider: {ai_provider} for row {row_number}")
            if progress_callback: progress_callback(f"Error: Unknown AI provider: {ai_provider}")
            update_sheet_cell(row_number, 'G', f"Unknown AI provider: {ai_provider}")
            return # Exit if provider is unknown

        if not ai_response:
             print(f"{ai_model_used} call failed or returned no content for row {row_number}")
             if progress_callback: progress_callback(f"Error: {ai_model_used} call failed or returned no content for row {row_number}")
             update_sheet_cell(row_number, 'G', f"{ai_model_used} call failed for row {row_number}")
             return # Exit if no response

        # Update sheet with AI's response
        update_sheet_cell(row_number, 'E', ai_response)
        if progress_callback: progress_callback(f"Updated cell E{row_number} with {ai_model_used}'s response")

    except Exception as e:
        print(f"Error processing {ai_provider} output for row {row_number}: {str(e)}")
        print(traceback.format_exc())
        if progress_callback: progress_callback(f"Error processing {ai_provider} output for row {row_number}: {str(e)}")
        update_sheet_cell(row_number, 'G', f"Error in {ai_provider} processing for row {row_number}: {str(e)}")
        return # Exit on error

    # Upload transcript to R2
    transcript_file = io.BytesIO(transcript.encode('utf-8'))
    file_name = f"transcript_{get_video_id(link) or 'direct'}"
    r2_link = upload_to_r2(transcript_file, file_name, 'txt')
    
    if r2_link:
        # Update sheet with R2 link
        update_sheet_cell(row_number, 'F', r2_link)
    else:
        print(f"Failed to upload transcript to R2 for row {row_number}")

    # Send webhook request if site is 'calf', 'doc', 'bred', 'vult', or 'kz'
    if site in ['calf', 'doc', 'bred', 'vult', 'kz']:
        success = send_webhook_request(site, title, ai_response, row_number) # Use ai_response
        if success:
            print(f"Successfully sent webhook request for row {row_number}")
            if progress_callback: progress_callback(f"Successfully sent webhook request for row {row_number}")
            update_sheet_cell(row_number, 'G', "Webhook sent successfully")
        else:
            print(f"Failed to send webhook request for row {row_number}")
            if progress_callback: progress_callback(f"Failed to send webhook request for row {row_number}")
            update_sheet_cell(row_number, 'G', "Webhook send failed")
    elif site:
        print(f"Invalid site '{site}' specified for row {row_number}. Skipping webhook.")
    else:
        print(f"No site specified for row {row_number}. Skipping webhook.")

def main(ai_provider="claude", progress_callback=None): # Added ai_provider parameter
    sheet_data = get_sheet_data()
    for row_number, row in enumerate(sheet_data, start=2):  # Start from row 2
        # Check if column E is already filled (index 4)
        if len(row) > 4 and row[4].strip():
            if progress_callback: progress_callback(f"Skipping row {row_number} as column E is not empty")
            continue # Skip if column E is not empty

        if len(row) > 3 and row[0] and row[3]:  # Check if link and prompt exist (columns A and D)
            process_row(row_number, row, ai_provider=ai_provider, progress_callback=progress_callback) # Pass ai_provider
        elif progress_callback:
             progress_callback(f"Skipping row {row_number} due to insufficient data (missing link or prompt)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process YouTube transcripts using AI.")
    parser.add_argument(
        "--ai_provider",
        type=str,
        default="claude",
        choices=["claude", "openai", "gemini", "deepseek"], # Added choices
        help="Specify the AI provider to use (claude, openai, gemini, or deepseek). Default is claude."
    )
    args = parser.parse_args()

    # Check for API keys based on selected provider
    if args.ai_provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            print("Error: OPENAI_API_KEY not found in environment variables. Please set it in your .env file to use the OpenAI provider.")
            exit(1)
    elif args.ai_provider == "gemini":
        if not os.getenv("GEMINI_API_KEY"):
            print("Error: GEMINI_API_KEY not found in environment variables. Please set it in your .env file to use the Gemini provider.")
            exit(1)
    elif args.ai_provider == "deepseek":
        if not os.getenv("DEEPSEEK_API_KEY"):
            print("Error: DEEPSEEK_API_KEY not found in environment variables. Please set it in your .env file to use the DeepSeek provider.")
            exit(1)

    print(f"Starting to process sheet using {args.ai_provider}...")
    main(ai_provider=args.ai_provider) # Pass the provider
    print("Finished processing sheet.")