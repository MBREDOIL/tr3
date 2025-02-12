import logging
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
import requests
import hashlib
import json
import os
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime
import requests.utils as requests_utils

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

USER_DATA_FILE = 'user_data.json'

def get_domain(url):
    """Extract domain from URL"""
    parsed_uri = urlparse(url)
    return f"{parsed_uri.netloc}"

def load_user_data():
    """Load user data from file"""
    try:
        with open(USER_DATA_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_user_data(user_data):
    """Save user data to file"""
    with open(USER_DATA_FILE, 'w') as f:
        json.dump(user_data, f, indent=4)

def fetch_url_content(url):
    """Fetch website content"""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None

def extract_documents(html_content, base_url):
    """Extract document links from HTML"""
    soup = BeautifulSoup(html_content, 'lxml')
    document_extensions = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt']
    documents = []

    for link in soup.find_all('a', href=True):
        href = link['href']
        # Proper URL encoding handling
        encoded_href = requests_utils.requote_uri(href)
        absolute_url = urljoin(base_url, encoded_href)
        link_text = link.text.strip()

        if any(absolute_url.lower().endswith(ext) for ext in document_extensions):
            # Use link text or filename as document name
            if not link_text:
                filename = os.path.basename(absolute_url)
                link_text = os.path.splitext(filename)[0]
            documents.append({
                'name': link_text,
                'url': absolute_url
            })

    # Remove duplicates
    return list({doc['url']: doc for doc in documents}.values())

async def create_document_file(url, documents):
    """Create TXT file with documents list"""
    domain = get_domain(url)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{domain}_documents_{timestamp}.txt"

    with open(filename, 'w', encoding='utf-8') as f:
        for doc in documents:
            f.write(f"{doc['name']} {doc['url']}\n\n")

    return filename

async def check_website_updates(client):
    """Check for website updates"""
    user_data = load_user_data()
    for user_id, data in user_data.items():
        for url_info in data['tracked_urls']:
            url = url_info['url']
            stored_hash = url_info['hash']
            stored_documents = url_info['documents']

            current_content = fetch_url_content(url)
            if not current_content:
                continue

            current_hash = hashlib.sha256(current_content.encode()).hexdigest()
            current_documents = extract_documents(current_content, url)

            if current_hash != stored_hash:
                try:
                    # General change notification
                    await client.send_message(
                        chat_id=user_id,
                        text=f"🚨 Website changed! {url}"
                    )
                except Exception as e:
                    logger.error(f"Error sending update to {user_id}: {e}")

                # Check for new documents
                new_docs = [doc for doc in current_documents
                            if doc not in stored_documents]

                if new_docs:
                    try:
                        # Create and send TXT file
                        txt_file = await create_document_file(url, new_docs)
                        await client.send_document(
                            chat_id=user_id,
                            document=txt_file,
                            caption=f"📄 New documents found at {url} ({len(new_docs)})"
                        )
                        os.remove(txt_file)
                    except Exception as e:
                        logger.error(f"Error sending document to {user_id}: {e}")

                    # Update stored data
                    url_info['documents'] = current_documents
                    url_info['hash'] = current_hash

    save_user_data(user_data)

async def start(client, message):
    """Handle /start command"""
    await message.reply_text(
        'Welcome to Website Tracker Bot!\n\n'
        'Commands:\n'
        '/track <url> - Track a website\n'
        '/untrack <url> - Stop tracking\n'
        '/list - List tracked websites\n'
        '/documents <url> - Get documents list'
    )

async def track(client, message):
    """Handle /track command"""
    user_id = str(message.from_user.id)
    url = ' '.join(message.command[1:]).strip()

    if not url.startswith(('http://', 'https://')):
        await message.reply_text("⚠ Please enter a valid URL (with http/https)")
        return

    user_data = load_user_data()
    if user_id not in user_data:
        user_data[user_id] = {'tracked_urls': []}

    if any(u['url'] == url for u in user_data[user_id]['tracked_urls']):
        await message.reply_text("❌ This URL is already being tracked")
        return

    content = fetch_url_content(url)
    if not content:
        await message.reply_text("❌ Could not access URL")
        return

    current_hash = hashlib.sha256(content.encode()).hexdigest()
    current_documents = extract_documents(content, url)

    user_data[user_id]['tracked_urls'].append({
        'url': url,
        'hash': current_hash,
        'documents': current_documents
    })

    save_user_data(user_data)
    await message.reply_text(f"✅ Tracking started: {url}\nFound documents: {len(current_documents)}")

async def untrack(client, message):
    """Handle /untrack command"""
    user_id = str(message.from_user.id)
    url = ' '.join(message.command[1:]).strip()

    user_data = load_user_data()
    if user_id not in user_data:
        await message.reply_text("❌ No tracked URLs found")
        return

    original_count = len(user_data[user_id]['tracked_urls'])
    user_data[user_id]['tracked_urls'] = [
        u for u in user_data[user_id]['tracked_urls']
        if u['url'] != url
    ]

    if len(user_data[user_id]['tracked_urls']) < original_count:
        save_user_data(user_data)
        await message.reply_text(f"❎ Tracking stopped: {url}")
    else:
        await message.reply_text("❌ URL not found")

async def list_urls(client, message):
    """Handle /list command"""
    user_id = str(message.from_user.id)
    user_data = load_user_data()

    if user_id not in user_data or not user_data[user_id]['tracked_urls']:
        await message.reply_text("📭 You're not tracking any URLs")
        return

    urls = "\n".join([u['url'] for u in user_data[user_id]['tracked_urls']])
    await message.reply_text(f"📜 Tracked URLs:\n\n{urls}")

async def list_documents(client, message):
    """Handle /documents command"""
    user_id = str(message.from_user.id)
    url = ' '.join(message.command[1:]).strip()

    user_data = load_user_data()
    if user_id not in user_data or not user_data[user_id]['tracked_urls']:
        await message.reply_text("❌ You're not tracking any URLs")
        return

    url_info = next((u for u in user_data[user_id]['tracked_urls'] if u['url'] == url), None)
    if not url_info:
        await message.reply_text("❌ This URL is not being tracked")
        return

    documents = url_info.get('documents', [])
    if not documents:
        await message.reply_text(f"ℹ️ No documents found at {url}")
    else:
        try:
            txt_file = await create_document_file(url, documents)
            await client.send_document(
                chat_id=user_id,
                document=txt_file,
                caption=f"📑 Documents at {url} ({len(documents)})"
            )
            os.remove(txt_file)
        except Exception as e:
            logger.error(f"Error sending documents list: {e}")
            await message.reply_text("❌ Error sending documents")

def main():
    """Main application"""
    app = Client(
        "my_bot",
        api_id="",
        api_hash="",
        bot_token=""
    )

    # Add command handlers
    handlers = [
        MessageHandler(start, filters.command("start")),
        MessageHandler(track, filters.command("track")),
        MessageHandler(untrack, filters.command("untrack")),
        MessageHandler(list_urls, filters.command("list")),
        MessageHandler(list_documents, filters.command("documents"))
    ]

    for handler in handlers:
        app.add_handler(handler)

    # Setup scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_website_updates, 'interval', minutes=30, args=[app])
    scheduler.start()

    try:
        app.run()
    except Exception as e:
        logger.error(f"Error running bot: {e}")

if __name__ == '__main__':
    main()
