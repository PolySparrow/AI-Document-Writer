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
import os
import io
from googleapiclient.http import MediaIoBaseDownload
import google.auth
from googleapiclient.errors import HttpError
import json

# Set up OpenAI
openai.api_key = config.openai_apikey

# Google Drive API setup
DRIVE_SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
CREDENTIALS_FILE = config.google_credentials_file
DRIVE_TOKEN_PICKLE = 'token.pickle'

DOCS_SCOPES = ['https://www.googleapis.com/auth/documents']
DOCS_TOKEN_PICKLE = 'token_docs.pickle'

def get_docs_service():
    creds = None
    if os.path.exists(DOCS_TOKEN_PICKLE):
        with open(DOCS_TOKEN_PICKLE, 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, DOCS_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(DOCS_TOKEN_PICKLE, 'wb') as token:
            pickle.dump(creds, token)
    return build('docs', 'v1', credentials=creds)

def get_drive_service():
    creds = None
    if os.path.exists(DRIVE_TOKEN_PICKLE):
        with open(DRIVE_TOKEN_PICKLE, 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, DRIVE_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(DRIVE_TOKEN_PICKLE, 'wb') as token:
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

    # List all items in the current folder
    query = f"'{folder_id}' in parents"
    page_token = None
    while True:
        response = service.files().list(
            q=query,
            pageSize=1000,
            fields="nextPageToken, files(id, name, mimeType)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            pageToken=page_token
        ).execute()
        items = response.get('files', [])
        page_token = response.get('nextPageToken', None)

        # Add only PDFs to the output
        for item in items:
            if item['mimeType'] == 'application/pdf':
                pdfs.append(item)
            # If it's a folder, recurse into it
            elif item['mimeType'] == 'application/vnd.google-apps.folder':
                pdfs.extend(list_pdfs_recursive(service, item['id']))
        if page_token is None:
            break

    return pdfs


    # Recursively search each subfolder
    for folder in subfolders:
        pdfs.extend(list_pdfs_recursive(service, folder['id']))

    return pdfs





def download_file(service, file_id, file_name, mime_type, dest_folder='pdfs'):
    import os
    import io
    from googleapiclient.http import MediaIoBaseDownload

    os.makedirs(dest_folder, exist_ok=True)
    file_path = os.path.join(dest_folder, file_name)

    # Google Workspace file types and their export MIME types
    export_mime_types = {
        'application/vnd.google-apps.document': 'application/pdf',
        'application/vnd.google-apps.spreadsheet': 'application/pdf',
        'application/vnd.google-apps.presentation': 'application/pdf',
    }

    if mime_type in export_mime_types:
        # Export Google Workspace files as PDF
        request = service.files().export_media(
            fileId=file_id,
            mimeType=export_mime_types[mime_type]
        )
        # Ensure file has .pdf extension
        if not file_path.lower().endswith('.pdf'):
            file_path += '.pdf'
    else:
        # Download regular files as-is
        request = service.files().get_media(fileId=file_id)

    fh = io.FileIO(file_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.close()
    print(f"Downloaded: {file_path}")
    return file_path

from googleapiclient.discovery import build

def create_and_write_google_doc(service, title, content):
    """
    Creates a new Google Doc with the given title and writes the provided content.
    :param service: Authenticated Google Docs API service object
    :param title: Title of the new document
    :param content: Text content to write into the document
    :return: The document ID and URL
    """
    # 1. Create the document
    doc = service.documents().create(body={"title": title}).execute()
    doc_id = doc.get('documentId')
    print(f"Created document with ID: {doc_id}")

    # 2. Write content to the document
    requests = [
        {
            'insertText': {
                'location': {
                    'index': 1,
                },
                'text': content
            }
        }
    ]
    service.documents().batchUpdate(
        documentId=doc_id,
        body={'requests': requests}
    ).execute()

    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
    print(f"Document URL: {doc_url}")
    return doc_id, doc_url


def upload_to_openai(file_path):
    with open(file_path, "rb") as f:
        response = openai.files.create(
            file=f,
            purpose="assistants"
        )
    return response.id

def create_assistant():
    assistant = openai.beta.assistants.create(
        name="PDF Query Assistant",
        instructions="You are a helpful assistant. Analyze the style of the pdfs and write based on the style. The user will provide you the prompt at which to write your topic about",
        tools=[{"type": "file_search"}],
        model=config.model,  # e.g., "gpt-3.5-turbo" or "gpt-4"
    )
    return assistant.id

def query_assistant(assistant_id, question,file_ids):
    # Create a thread
    thread = openai.beta.threads.create()
    # Add user message
    openai.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=question,
        attachments=file_ids
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
        print(f"Run status: {run_status.status}")
        if run_status.status in ["completed", "failed"]:
            break
        time.sleep(2)

    if run_status.status == "failed":
        print("Run failed:", run_status.last_error)
        return
    # Get assistant's response
    messages = openai.beta.threads.messages.list(thread_id=thread.id)
    #print(messages)
    for msg in messages.data:
        #print(msg.content[0].text.value)
        if msg.role == "assistant":
            print("Assistant:", msg.content[0].text.value)
            return msg.content[0].text.value

def main():
    # Step 1: Download PDFs from Google Drive
    drive_service = get_drive_service()
    doc_service=get_docs_service()
    file_link = extract_folder_id(config.google_filelink)
    #print(file_link)
    pdf_files = list_pdfs_recursive(drive_service,file_link)
    print(f"Found {len(pdf_files)} PDF files.")
    #print(pdf_files)
    file_ids = []
    for file in pdf_files:
        print(f"Downloading: {file['name']}")
        local_path = download_file(service=drive_service,
        file_id=file['id'],
        file_name=file['name'],
        mime_type=file['mimeType'],
        dest_folder='pdfs'  # or any folder you want
    )
        print(f"Uploading {file['name']} to OpenAI...")
        file_id = upload_to_openai(local_path)
        print(f"Uploaded: {file_id}")
        file_ids.append(file_id)
    #print(file_ids)
    for fid in file_ids:
        attachments = [
        {"file_id": fid, "tools": [{"type": "file_search"}]} 
        ]
    #print(json.dumps(attachments, indent=2))
    if not file_ids:
        print("No PDFs found or uploaded.")
        return

    # Step 2: Create Assistant with uploaded PDFs
    assistant_id = create_assistant()
    print(f"Assistant created with ID: {assistant_id}")

    # # Step 3: Query the Assistant
    question = input("Enter your question about the PDFs: ")
    #question = 'Say hello world'
    openai_result=query_assistant(assistant_id, question,attachments)
    create_and_write_google_doc(doc_service, "PDF Summary", openai_result)
if __name__ == "__main__":
    main()
