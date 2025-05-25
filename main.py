import os
import io
import time
import openai
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle
import config
import re

# Set up OpenAI
openai.api_key = config.openai_apikey

# Google Drive API setup
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
CREDENTIALS_FILE = config.google_credentials_file
TOKEN_PICKLE = 'token.pickle'

def get_drive_service():
    creds = None
    if os.path.exists(TOKEN_PICKLE):
        with open(TOKEN_PICKLE, 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PICKLE, 'wb') as token:
            pickle.dump(creds, token)
    return build('drive', 'v3', credentials=creds)

def extract_folder_id(link):
    match = re.search(r'/folders/([a-zA-Z0-9_-]+)', link)
    if match:
        return match.group(1)
    else:
        raise ValueError("Invalid folder link")


def list_pdfs_recursive(service, folder_id):
    pdfs = []

    # List PDFs in the current folder
    query = f"mimeType='application/pdf' and '{folder_id}' in parents"
    page_token = None
    while True:
        response = service.files().list(
            q=query,
            pageSize=1000,
            fields="nextPageToken, files(id, name)",
            pageToken=page_token
        ).execute()
        pdfs.extend(response.get('files', []))
        page_token = response.get('nextPageToken', None)
        if page_token is None:
            break

    # List subfolders in the current folder
    folder_query = f"mimeType='application/vnd.google-apps.folder' and '{folder_id}' in parents"
    page_token = None
    subfolders = []
    while True:
        response = service.files().list(
            q=folder_query,
            pageSize=1000,
            fields="nextPageToken, files(id, name)",
            pageToken=page_token
        ).execute()
        subfolders.extend(response.get('files', []))
        page_token = response.get('nextPageToken', None)
        if page_token is None:
            break

    # Recursively search each subfolder
    for folder in subfolders:
        pdfs.extend(list_pdfs_recursive(service, folder['id']))

    return pdfs


def download_pdf(service, file_id, file_name, dest_folder='pdfs'):
    os.makedirs(dest_folder, exist_ok=True)
    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(os.path.join(dest_folder, file_name), 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.close()
    return os.path.join(dest_folder, file_name)

def upload_to_openai(file_path):
    with open(file_path, "rb") as f:
        response = openai.files.create(
            file=f,
            purpose="assistants"
        )
    return response.id

def create_assistant(file_ids):
    assistant = openai.beta.assistants.create(
        name="PDF Query Assistant",
        instructions="You are a helpful assistant. Use the attached PDFs to answer questions.",
        tools=[{"type": "retrieval"}],
        file_ids=file_ids
    )
    return assistant.id

def query_assistant(assistant_id, question):
    # Create a thread
    thread = openai.beta.threads.create()
    # Add user message
    openai.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=question
    )
    # Run the assistant
    run = openai.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant_id
    )
    # Wait for completion
    while True:
        run_status = openai.beta.threads.runs.retrieve(
            thread_id=thread.id,
            run_id=run.id
        )
        if run_status.status in ["completed", "failed"]:
            break
        time.sleep(2)
    # Get assistant's response
    messages = openai.beta.threads.messages.list(thread_id=thread.id)
    for msg in messages.data:
        if msg.role == "assistant":
            print("Assistant:", msg.content[0].text.value)

def main():
    # Step 1: Download PDFs from Google Drive
    service = get_drive_service()
    file_link = extract_folder_id(config.google_filelink)
    pdf_files = list_pdfs_recursive(service,file_link)
    print(f"Found {len(pdf_files)} PDF files.")

    file_ids = []
    for file in pdf_files:
        print(f"Downloading: {file['name']}")
        local_path = download_pdf(service, file['id'], file['name'])
        # print(f"Uploading {file['name']} to OpenAI...")
        # file_id = upload_to_openai(local_path)
        # print(f"Uploaded: {file_id}")
        # file_ids.append(file_id)

    if not file_ids:
        print("No PDFs found or uploaded.")
        return

    # Step 2: Create Assistant with uploaded PDFs
    # assistant_id = create_assistant(file_ids)
    # print(f"Assistant created with ID: {assistant_id}")

    # # Step 3: Query the Assistant
    # question = input("Enter your question about the PDFs: ")
    # query_assistant(assistant_id, question)

if __name__ == "__main__":
    main()
