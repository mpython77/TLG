import asyncio
import json
import os
import threading
import time
import random
from datetime import datetime, timedelta, timezone
import logging
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_socketio import SocketIO, emit
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, FloodWaitError, ChannelPrivateError, UserBannedInChannelError
from telethon.tl.types import PeerChannel, PeerChat, PeerUser
import re
import hashlib
from models import DatabaseManager, Account, ScheduledPost
from sqlalchemy.exc import IntegrityError

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-here-change-this-in-production')
app.permanent_session_lifetime = timedelta(hours=24)

socketio = SocketIO(app, 
                   cors_allowed_origins="*",
                   logger=False,
                   engineio_logger=False,
                   ping_timeout=60,
                   ping_interval=25,
                   async_mode='threading')

class AuthManager:
    def __init__(self):
        self.password_file = 'password.txt'
        print(f"Looking for password file at: {os.path.abspath(self.password_file)}")
        self.create_default_password_file()
    
    def create_default_password_file(self):
        pass
    
    def load_credentials(self):
        try:
            abs_path = os.path.abspath(self.password_file)
            print(f"Checking password file: {abs_path}")
            print(f"File exists: {os.path.exists(self.password_file)}")
            
            if os.path.exists(self.password_file):
                with open(self.password_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                print(f"Raw file content: {repr(content)}")
                content = content.strip()
                print(f"Stripped content: {repr(content)}")
                
                login = None
                password = None
                
                lines = content.split('\n')
                print(f"Lines: {lines}")
                
                for i, line in enumerate(lines):
                    line = line.strip()
                    print(f"Processing line {i}: {repr(line)}")
                    if line.startswith('Login:'):
                        login = line.replace('Login:', '').strip()
                        print(f"Found login: {repr(login)}")
                    elif line.startswith('Password:'):
                        password = line.replace('Password:', '').strip()
                        print(f"Found password: {repr(password)}")
                
                print(f"Final parsed credentials: login={repr(login)}, password={repr(password)}")
                
                if login and password:
                    return {'login': login, 'password': password}
                else:
                    print("ERROR: Missing login or password in file")
                    return None
            else:
                print(f"ERROR: Password file not found at: {abs_path}")
                return None
        except Exception as e:
            print(f"ERROR loading credentials: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def verify_credentials(self, username, password):
        print(f"\n=== CREDENTIAL VERIFICATION ===")
        print(f"Input username: {repr(username)}")
        print(f"Input password: {repr(password)}")
        
        credentials = self.load_credentials()
        
        if credentials is None:
            print("ERROR: No credentials loaded from file")
            return False
        
        print(f"File username: {repr(credentials['login'])}")
        print(f"File password: {repr(credentials['password'])}")
        
        username_match = username == credentials['login']
        password_match = password == credentials['password']
        
        print(f"Username match: {username_match}")
        print(f"Password match: {password_match}")
        
        result = username_match and password_match
        print(f"Final result: {result}")
        print("=== END VERIFICATION ===\n")
        
        return result

class WebTelegramForwarder:
    def __init__(self):
        # Initialize database
        self.db_manager = DatabaseManager()

        # Old JSON config file - keep for migration
        self.config_file = 'accounts_config.json'

        # Migrate old data if exists
        self.migrate_from_json()

        self.clients = {}
        self.running = False
        self.message_monitoring = False

        self.min_delay = 7
        self.max_delay = 12
        self.last_forward_time = {}

        self.connection_queue = []
        self.current_connecting_phone = None
        self.connection_in_progress = False
        self.connection_paused = False

        self.scheduler_running = False

        self.active_tasks = set()
        self.connection_semaphore = None

        self.loop = None
        self.loop_thread = None

        self.pending_auth = {}

        self.log_history = []
        self.monitor_history = []
        self.max_history_size = 500

        self.entity_cache = {}

        logging.basicConfig(
            format='%(asctime)s - %(levelname)s - %(message)s',
            level=logging.WARNING,
            handlers=[
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

        self.start_async_loop()
        
    def start_async_loop(self):
        if self.loop_thread is None or not self.loop_thread.is_alive():
            self.loop_thread = threading.Thread(target=self.run_async_loop, daemon=True)
            self.loop_thread.start()
            
            for i in range(30):
                time.sleep(0.1)
                if self.loop is not None:
                    break
    
    def run_async_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def migrate_from_json(self):
        """Migrate existing data from JSON file to PostgreSQL"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    old_accounts = json.load(f)

                if old_accounts:
                    db = self.db_manager.get_session()
                    try:
                        # Check if we already have data in database
                        existing_count = db.query(Account).count()
                        if existing_count == 0:
                            # Migrate accounts
                            for acc in old_accounts:
                                # Check if account already exists
                                existing = db.query(Account).filter_by(phone=acc['phone']).first()
                                if not existing:
                                    new_account = Account(
                                        name=acc.get('name', acc['phone']),
                                        api_id=acc['api_id'],
                                        api_hash=acc['api_hash'],
                                        phone=acc['phone'],
                                        source_channel=acc['source_channel'],
                                        target_channels=acc['target_channels'],
                                        status=acc.get('status', 'Added'),
                                        session_file=acc['session_file']
                                    )
                                    db.add(new_account)
                            db.commit()
                            self.log_message(f"Migrated {len(old_accounts)} accounts from JSON to database")

                            # Rename old file to prevent re-migration
                            os.rename(self.config_file, f"{self.config_file}.backup")
                    except Exception as e:
                        db.rollback()
                        self.logger.error(f"Error during migration: {e}")
                    finally:
                        db.close()
            except Exception as e:
                self.logger.error(f"Error loading old config: {e}")
    
    def log_message(self, message, account_phone=None):
        utc_plus_2 = timezone(timedelta(hours=2))
        timestamp = datetime.now(utc_plus_2).strftime("%H:%M:%S")
        if account_phone:
            full_message = f"[{timestamp}] [{account_phone}] {message}"
        else:
            full_message = f"[{timestamp}] [SYSTEM] {message}"
        
        self.log_history.append(full_message)
        if len(self.log_history) > self.max_history_size:
            self.log_history = self.log_history[-self.max_history_size:]
        
        try:
            socketio.emit('log_message', {'message': full_message})
        except Exception as e:
            pass
        
        print(full_message)
    
    def monitor_message(self, message, account_phone=None, channel=None):
        utc_plus_2 = timezone(timedelta(hours=2))
        timestamp = datetime.now(utc_plus_2).strftime("%H:%M:%S")
        if account_phone and channel:
            full_message = f"[{timestamp}] [{account_phone}] [{channel}] {message}"
        else:
            full_message = f"[{timestamp}] [MONITOR] {message}"
        
        self.monitor_history.append(full_message)
        if len(self.monitor_history) > self.max_history_size:
            self.monitor_history = self.monitor_history[-self.max_history_size:]
        
        try:
            socketio.emit('monitor_message', {'message': full_message})
        except:
            pass
        
        print(full_message)
    
    def get_log_history(self):
        return '\n'.join(self.log_history) if self.log_history else 'System ready... Logs will appear here.'
    
    def get_monitor_history(self):
        return '\n'.join(self.monitor_history) if self.monitor_history else 'Monitor ready... Connect accounts and start monitoring to see message IDs.'
    
    def clear_log_history(self):
        self.log_history = []
        try:
            socketio.emit('clear_logs', {})
        except:
            pass
    
    def clear_monitor_history(self):
        self.monitor_history = []
        try:
            socketio.emit('clear_monitor', {})
        except:
            pass

    async def save_session_to_db(self, phone, session_string):
        """Save Telegram session string to database for persistence"""
        db = self.db_manager.get_session()
        try:
            account = db.query(Account).filter_by(phone=phone).first()
            if account:
                account.session_string = session_string
                db.commit()
                self.log_message(f"Session saved to database", phone)
        except Exception as e:
            db.rollback()
            self.logger.error(f"Error saving session to database: {e}")
        finally:
            db.close()

    async def get_entity_safe(self, client, entity_id, phone):
        try:
            cache_key = f"{phone}_{entity_id}"
            if cache_key in self.entity_cache:
                return self.entity_cache[cache_key]
            
            entity_id = int(entity_id)
            
            try:
                entity = await client.get_entity(entity_id)
                self.entity_cache[cache_key] = entity
                return entity
            except Exception as e:
                if "Could not find the input entity" in str(e) or "No user has" in str(e):
                    try:
                        entity = await client.get_entity(PeerChannel(-entity_id - 1000000000000))
                        self.entity_cache[cache_key] = entity
                        return entity
                    except:
                        pass
                    
                    try:
                        entity = await client.get_entity(PeerChat(-entity_id))
                        self.entity_cache[cache_key] = entity
                        return entity
                    except:
                        pass
                
                self.log_message(f"Entity not found or access denied: {entity_id}", phone)
                raise
                
        except Exception as e:
            self.log_message(f"Error getting entity {entity_id}: {str(e)}", phone)
            raise
    
    def add_account(self, api_id, api_hash, phone, source_channel, target_channels, name=None):
        try:
            int(source_channel)
        except ValueError:
            return {"success": False, "error": "Source channel must be a number (ID)!"}

        if not target_channels:
            return {"success": False, "error": "Add at least one target channel ID!"}

        for channel in target_channels:
            try:
                int(channel)
            except ValueError:
                return {"success": False, "error": f"Target channel '{channel}' must be a number (ID)!"}

        session_name = f"session_{phone.replace('+', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '')}"
        session_path = session_name

        db = self.db_manager.get_session()
        try:
            # Check if account already exists
            existing = db.query(Account).filter_by(phone=phone).first()
            if existing:
                db.close()
                return {"success": False, "error": "This phone number is already added!"}

            # Create new account
            new_account = Account(
                name=name if name else phone,
                api_id=api_id,
                api_hash=api_hash,
                phone=phone,
                source_channel=source_channel,
                target_channels=target_channels,
                status='Added',
                session_file=session_path
            )

            db.add(new_account)
            db.commit()

            account_display = name if name else phone
            self.log_message(f"New account added: {account_display} ({phone}) ({len(target_channels)} channels)")

            return {"success": True, "message": f"Account added! {len(target_channels)} target channels set."}

        except Exception as e:
            db.rollback()
            self.logger.error(f"Error adding account: {e}")
            return {"success": False, "error": f"Database error: {str(e)}"}
        finally:
            db.close()
    
    def remove_account(self, phone):
        db = self.db_manager.get_session()
        try:
            # Find account by phone (try different phone formats)
            phone_clean = phone.replace('+', '').replace(' ', '')
            account = db.query(Account).filter(
                (Account.phone == phone) |
                (Account.phone.like(f"%{phone_clean}%"))
            ).first()

            if not account:
                db.close()
                return {"success": False, "error": "Account not found!"}

            # Remove session file
            session_file = f"{account.session_file}.session"
            if os.path.exists(session_file):
                try:
                    os.remove(session_file)
                    self.log_message(f"Session file removed: {session_file}")
                except Exception as e:
                    self.log_message(f"Error removing session file: {str(e)}")

            # Disconnect client if connected
            for phone_variant in [account.phone, account.phone.replace('+', ''), f"+{account.phone}"]:
                if phone_variant in self.clients:
                    try:
                        if self.loop:
                            asyncio.run_coroutine_threadsafe(self.clients[phone_variant].disconnect(), self.loop)
                        del self.clients[phone_variant]
                        self.log_message(f"Client disconnected: {phone_variant}")
                        break
                    except Exception as e:
                        self.log_message(f"Error disconnecting client: {str(e)}")

            # Delete from database
            db.delete(account)
            db.commit()

            self.entity_cache.clear()

            self.log_message(f"Account removed: {account.phone}")
            return {"success": True, "message": f"{account.phone} account removed!"}

        except Exception as e:
            db.rollback()
            self.logger.error(f"Error removing account: {e}")
            return {"success": False, "error": f"Database error: {str(e)}"}
        finally:
            db.close()
    
    def remove_selected_channels(self, selected_channels):
        if not selected_channels:
            return {"success": False, "error": "No channels selected!"}

        db = self.db_manager.get_session()
        try:
            removed_count = 0

            for phone, channels_to_remove in selected_channels.items():
                account = db.query(Account).filter_by(phone=phone).first()
                if account:
                    original_count = len(account.target_channels)

                    # Remove selected channels
                    account.target_channels = [ch for ch in account.target_channels if ch not in channels_to_remove]

                    removed_from_this_account = original_count - len(account.target_channels)
                    removed_count += removed_from_this_account

                    self.log_message(f"Removed {removed_from_this_account} channels from {phone}")

            db.commit()
            self.log_message(f"Total channels removed: {removed_count}")

            try:
                socketio.emit('accounts_updated', self.get_accounts_data())
            except:
                pass

            return {"success": True, "message": f"Removed {removed_count} channels successfully!"}

        except Exception as e:
            db.rollback()
            self.logger.error(f"Error removing channels: {e}")
            return {"success": False, "error": f"Database error: {str(e)}"}
        finally:
            db.close()
    
    def get_accounts_data(self):
        db = self.db_manager.get_session()
        try:
            accounts = db.query(Account).all()
            accounts_data = []

            for account in accounts:
                status = account.status
                if account.phone in self.clients:
                    status = 'Connected'
                    # Update status in database
                    account.status = 'Connected'

                accounts_data.append({
                    'name': account.name,
                    'phone': account.phone,
                    'source_channel': account.source_channel,
                    'target_channels': account.target_channels,
                    'status': status
                })

            db.commit()

            return {
                'accounts': accounts_data,
                'total_accounts': len(accounts),
                'connected_accounts': len(self.clients)
            }

        except Exception as e:
            self.logger.error(f"Error getting accounts data: {e}")
            return {
                'accounts': [],
                'total_accounts': 0,
                'connected_accounts': 0
            }
        finally:
            db.close()
    
    def get_auth_status(self):
        if self.pending_auth:
            for phone, auth_data in self.pending_auth.items():
                return {
                    'auth_required': True,
                    'phone': phone,
                    'step': auth_data['step']
                }
        return {'auth_required': False}
    
    def connect_all_accounts(self):
        # Load accounts from database
        db = self.db_manager.get_session()
        try:
            accounts = db.query(Account).all()
            accounts_list = [acc.to_dict() for acc in accounts]
        finally:
            db.close()

        if not accounts_list:
            return {"success": False, "error": "No accounts available!"}

        if self.connection_in_progress:
            return {"success": False, "error": "Connection process is already in progress!"}

        if self.pending_auth:
            return {"success": False, "error": "Please complete authentication for the current account first!"}

        if self.loop and not self.loop.is_closed():
            self.connection_queue = accounts_list
            self.current_connecting_phone = None
            self.connection_in_progress = True
            self.connection_paused = False

            try:
                socketio.emit('connection_progress', {
                    'current': 0,
                    'total': len(accounts_list),
                    'status': 'Starting sequential connection...'
                })
            except:
                pass

            asyncio.run_coroutine_threadsafe(self.connect_accounts_sequentially(), self.loop)
            return {"success": True, "message": "Sequential connection started!"}
        else:
            return {"success": False, "error": "Async loop not available!"}
    
    async def connect_accounts_sequentially(self):
        connected_count = 0
        failed_count = 0
        
        self.log_message(f"Starting sequential connection for {len(self.connection_queue)} accounts")
        
        for index, account in enumerate(self.connection_queue):
            if not self.connection_in_progress:
                break
                
            phone = account['phone']
            self.current_connecting_phone = phone
            
            self.log_message(f"Processing account {index + 1}/{len(self.connection_queue)}: {phone}")
            
            try:
                socketio.emit('connection_progress', {
                    'current': index + 1,
                    'total': len(self.connection_queue),
                    'status': f"Connecting {phone}..."
                })
            except:
                pass
            
            try:
                result = await self.connect_single_account_sequential(account)
                
                if result == 'auth_required':
                    self.log_message(f"Authentication required for {phone}. Process paused.")
                    self.connection_paused = True
                    
                    try:
                        socketio.emit('connection_progress', {
                            'current': index + 1,
                            'total': len(self.connection_queue),
                            'status': f"Authentication required for {phone}. Process paused.",
                            'paused': True
                        })
                    except:
                        pass
                    return
                    
                elif result == 'success':
                    connected_count += 1
                    self.log_message(f"Successfully connected: {phone}")
                    
                else:
                    failed_count += 1
                    self.log_message(f"Failed to connect: {phone}")
                    
            except Exception as e:
                failed_count += 1
                self.log_message(f"Connection error for {phone}: {str(e)}")
                account['status'] = 'Error'
            
            try:
                socketio.emit('accounts_updated', self.get_accounts_data())
            except:
                pass
            
            if index < len(self.connection_queue) - 1:
                await asyncio.sleep(2)
        
        self.finish_connection_process(connected_count, failed_count)
    
    def finish_connection_process(self, connected_count, failed_count):
        self.connection_in_progress = False
        self.connection_paused = False
        self.current_connecting_phone = None
        total = len(self.connection_queue)

        self.log_message(f"Connection process completed: {connected_count} connected, {failed_count} failed")

        try:
            socketio.emit('connection_progress', {
                'current': total,
                'total': total,
                'status': f"Process completed: {connected_count} connected, {failed_count} failed",
                'finished': True
            })

            socketio.emit('accounts_updated', self.get_accounts_data())
        except:
            pass

        # Auto-start scheduler if there are pending posts and accounts are connected
        if connected_count > 0 and not self.scheduler_running:
            db = self.db_manager.get_session()
            try:
                pending_count = db.query(ScheduledPost).filter_by(status='Pending').count()
                if pending_count > 0:
                    self.log_message(f"Found {pending_count} pending posts - auto-starting scheduler")
                    self.scheduler_running = True
                    asyncio.run_coroutine_threadsafe(self.run_scheduler(), self.loop)
                    try:
                        socketio.emit('scheduler_status', {'running': True})
                    except:
                        pass
            except Exception as e:
                self.logger.error(f"Error checking pending posts: {e}")
            finally:
                db.close()
    
    async def connect_single_account_sequential(self, account):
        phone = account['phone']

        try:
            self.log_message(f"Initiating connection...", phone)
            account['status'] = 'Connecting...'

            if phone in self.clients:
                try:
                    old_client = self.clients[phone]
                    del self.clients[phone]
                    await asyncio.wait_for(old_client.disconnect(), timeout=3.0)
                    await asyncio.sleep(0.5)
                except Exception as cleanup_error:
                    self.log_message(f"Cleanup error (continuing): {str(cleanup_error)}", phone)

            # Load session from database and check if exists
            session_string = account.get('session_string', '')

            if session_string:
                self.log_message(f"✓ Session found in database - attempting to connect with saved session", phone)
            else:
                self.log_message(f"✗ No session in database - will request authorization code", phone)

            # Use StringSession for database persistence
            client = TelegramClient(
                StringSession(session_string) if session_string else StringSession(),
                int(account['api_id']),
                account['api_hash'],
                timeout=20,
                retry_delay=1,
                auto_reconnect=True,
                connection_retries=3
            )

            await asyncio.wait_for(client.connect(), timeout=15.0)

            if not await client.is_user_authorized():
                if session_string:
                    self.log_message(f"⚠ Saved session is invalid or expired - requesting new authorization code", phone)
                else:
                    self.log_message(f"Authorization required - sending code", phone)

                account['status'] = 'Waiting for code...'

                await client.send_code_request(phone)

                self.pending_auth[phone] = {
                    'client': client,
                    'account': account,
                    'step': 'code'
                }

                try:
                    socketio.emit('auth_required', {
                        'phone': phone,
                        'step': 'code'
                    })
                    socketio.emit('accounts_updated', self.get_accounts_data())
                except:
                    pass

                return 'auth_required'

            me = await client.get_me()
            self.clients[phone] = client
            account['status'] = 'Connected'

            if session_string:
                self.log_message(f"✓ Connected with saved session: {me.first_name} (no code required)", phone)
            else:
                self.log_message(f"Connected successfully: {me.first_name}", phone)

            # Save session string to database (update if changed)
            await self.save_session_to_db(phone, client.session.save())

            try:
                source_entity = await self.get_entity_safe(client, account['source_channel'], phone)
                self.log_message(f"Source channel verified: {source_entity.title if hasattr(source_entity, 'title') else 'Channel'}", phone)
                
                for target_id in account['target_channels']:
                    try:
                        target_entity = await self.get_entity_safe(client, target_id, phone)
                        self.log_message(f"Target channel {target_id} verified: {target_entity.title if hasattr(target_entity, 'title') else 'Channel'}", phone)
                    except Exception as e:
                        self.log_message(f"Warning: Target channel {target_id} not accessible: {str(e)}", phone)
                        
            except Exception as e:
                self.log_message(f"Warning: Could not verify channels: {str(e)}", phone)
            
            return 'success'
            
        except asyncio.TimeoutError:
            self.log_message(f"Connection timeout", phone)
            account['status'] = 'Timeout'
            return 'failed'
            
        except Exception as e:
            error_msg = str(e)
            if "database is locked" in error_msg.lower():
                self.log_message(f"Database locked, retrying...", phone)
                account['status'] = 'Retrying...'
                await asyncio.sleep(3)

                try:
                    # Load session from database for retry
                    session_string = account.get('session_string', '')

                    if session_string:
                        self.log_message(f"Retry: Using saved session from database", phone)
                    else:
                        self.log_message(f"Retry: No session available, will request code", phone)

                    retry_client = TelegramClient(
                        StringSession(session_string) if session_string else StringSession(),
                        int(account['api_id']),
                        account['api_hash'],
                        timeout=20,
                        retry_delay=1,
                        auto_reconnect=True,
                        connection_retries=3
                    )

                    await asyncio.wait_for(retry_client.connect(), timeout=15.0)

                    if await retry_client.is_user_authorized():
                        me = await retry_client.get_me()
                        self.clients[phone] = retry_client
                        account['status'] = 'Connected'

                        if session_string:
                            self.log_message(f"✓ Connected after retry with saved session: {me.first_name}", phone)
                        else:
                            self.log_message(f"Connected after retry: {me.first_name}", phone)

                        # Save session string to database
                        await self.save_session_to_db(phone, retry_client.session.save())

                        return 'success'
                    else:
                        account['status'] = 'Auth required after retry'

                        if session_string:
                            self.log_message(f"⚠ Saved session invalid on retry - requesting code", phone)
                        else:
                            self.log_message(f"Authorization required after retry", phone)

                        await retry_client.send_code_request(phone)

                        self.pending_auth[phone] = {
                            'client': retry_client,
                            'account': account,
                            'step': 'code'
                        }

                        try:
                            socketio.emit('auth_required', {
                                'phone': phone,
                                'step': 'code'
                            })
                        except:
                            pass
                        return 'auth_required'
                        
                except Exception as retry_error:
                    self.log_message(f"Retry failed: {str(retry_error)}", phone)
                    account['status'] = 'Retry failed'
                    return 'failed'
                    
            else:
                self.log_message(f"Connection error: {error_msg}", phone)
                account['status'] = 'Error'
                return 'failed'
    
    def submit_auth_code(self, phone, code):
        if phone not in self.pending_auth:
            return {"success": False, "error": "No pending authentication for this phone"}
        
        auth_data = self.pending_auth[phone]
        
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self.process_auth_code(auth_data['account'], auth_data['client'], code, phone),
                self.loop
            )
            return {"success": True, "message": "Code submitted"}
        
        return {"success": False, "error": "Async loop not available"}
    
    async def process_auth_code(self, account, client, code, phone):
        try:
            await client.sign_in(phone, code)

            me = await client.get_me()
            self.clients[phone] = client
            account['status'] = 'Connected'
            self.log_message(f"Authentication successful: {me.first_name}", phone)

            # Save session string to database
            await self.save_session_to_db(phone, client.session.save())

            if phone in self.pending_auth:
                del self.pending_auth[phone]

            try:
                socketio.emit('auth_success', {'phone': phone})
                socketio.emit('accounts_updated', self.get_accounts_data())
            except:
                pass

            if self.connection_paused and self.connection_in_progress:
                self.log_message("Resuming connection process...")
                await asyncio.sleep(1)
                await self.resume_connection_after_auth()
            
        except SessionPasswordNeededError:
            self.log_message("2FA password required", phone)
            
            self.pending_auth[phone]['step'] = 'password'
            
            try:
                socketio.emit('auth_required', {
                    'phone': phone,
                    'step': 'password'
                })
            except:
                pass
            
        except Exception as e:
            error_msg = str(e)
            self.log_message(f"Code authentication error: {error_msg}", phone)
            
            if phone in self.pending_auth:
                del self.pending_auth[phone]
            
            account['status'] = 'Auth error'
            
            try:
                socketio.emit('auth_error', {
                    'phone': phone,
                    'error': error_msg
                })
                socketio.emit('accounts_updated', self.get_accounts_data())
            except:
                pass
            
            if self.connection_paused and self.connection_in_progress:
                self.log_message("Resuming connection process after auth error...")
                await asyncio.sleep(1)
                await self.resume_connection_after_auth()
    
    def submit_auth_password(self, phone, password):
        if phone not in self.pending_auth:
            return {"success": False, "error": "No pending authentication for this phone"}
        
        auth_data = self.pending_auth[phone]
        
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self.process_auth_password(auth_data['account'], auth_data['client'], password, phone),
                self.loop
            )
            return {"success": True, "message": "Password submitted"}
        
        return {"success": False, "error": "Async loop not available"}
    
    async def process_auth_password(self, account, client, password, phone):
        try:
            await client.sign_in(password=password)

            me = await client.get_me()
            self.clients[phone] = client
            account['status'] = 'Connected'
            self.log_message(f"2FA authentication successful: {me.first_name}", phone)

            # Save session string to database
            await self.save_session_to_db(phone, client.session.save())

            if phone in self.pending_auth:
                del self.pending_auth[phone]

            try:
                socketio.emit('auth_success', {'phone': phone})
                socketio.emit('accounts_updated', self.get_accounts_data())
            except:
                pass

            if self.connection_paused and self.connection_in_progress:
                self.log_message("Resuming connection process...")
                await asyncio.sleep(1)
                await self.resume_connection_after_auth()
            
        except Exception as e:
            self.log_message(f"2FA password error: {str(e)}", phone)
            account['status'] = '2FA error'
            
            if phone in self.pending_auth:
                del self.pending_auth[phone]
            
            try:
                socketio.emit('auth_error', {
                    'phone': phone,
                    'error': str(e)
                })
                socketio.emit('accounts_updated', self.get_accounts_data())
            except:
                pass
            
            if self.connection_paused and self.connection_in_progress:
                self.log_message("Resuming connection process after auth error...")
                await asyncio.sleep(1)
                await self.resume_connection_after_auth()
    
    async def resume_connection_after_auth(self):
        if not self.connection_in_progress or not self.connection_paused:
            return
        
        self.connection_paused = False
        
        current_index = next((i for i, acc in enumerate(self.connection_queue) 
                            if acc['phone'] == self.current_connecting_phone), -1)
        
        if current_index == -1:
            self.finish_connection_process(len(self.clients), 0)
            return
        
        connected_count = len(self.clients)
        failed_count = 0
        
        for index in range(current_index + 1, len(self.connection_queue)):
            if not self.connection_in_progress:
                break
                
            account = self.connection_queue[index]
            phone = account['phone']
            self.current_connecting_phone = phone
            
            self.log_message(f"Continuing with account {index + 1}/{len(self.connection_queue)}: {phone}")
            
            try:
                socketio.emit('connection_progress', {
                    'current': index + 1,
                    'total': len(self.connection_queue),
                    'status': f"Connecting {phone}..."
                })
            except:
                pass
            
            try:
                result = await self.connect_single_account_sequential(account)
                
                if result == 'auth_required':
                    self.log_message(f"Authentication required for {phone}. Process paused again.")
                    self.connection_paused = True
                    
                    try:
                        socketio.emit('connection_progress', {
                            'current': index + 1,
                            'total': len(self.connection_queue),
                            'status': f"Authentication required for {phone}. Process paused.",
                            'paused': True
                        })
                    except:
                        pass
                    return
                    
                elif result == 'success':
                    connected_count += 1
                    self.log_message(f"Successfully connected: {phone}")
                    
                else:
                    failed_count += 1
                    self.log_message(f"Failed to connect: {phone}")
                    
            except Exception as e:
                failed_count += 1
                self.log_message(f"Connection error for {phone}: {str(e)}")
                account['status'] = 'Error'
            
            try:
                socketio.emit('accounts_updated', self.get_accounts_data())
            except:
                pass
            
            if index < len(self.connection_queue) - 1:
                await asyncio.sleep(2)
        
        total_connected = len(self.clients)
        total_failed = len(self.connection_queue) - total_connected
        self.finish_connection_process(total_connected, total_failed)
    
    def start_message_monitoring(self):
        if not self.clients:
            return {"success": False, "error": "Connect to accounts first!"}
        
        if self.message_monitoring:
            return {"success": False, "error": "Message monitoring is already running!"}
        
        self.message_monitoring = True
        
        if self.loop:
            asyncio.run_coroutine_threadsafe(self.setup_message_monitoring(), self.loop)
        
        self.monitor_message("Message ID monitoring started")
        self.log_message("Message ID monitoring started")
        
        try:
            socketio.emit('monitor_status', {'running': True})
        except:
            pass
        return {"success": True, "message": "Message monitoring started"}
    
    def stop_message_monitoring(self):
        if not self.message_monitoring:
            return {"success": False, "error": "Message monitoring is not running!"}
        
        self.message_monitoring = False
        
        if self.loop:
            asyncio.run_coroutine_threadsafe(self.clear_monitoring_handlers(), self.loop)
        
        self.monitor_message("Message ID monitoring stopped")
        self.log_message("Message ID monitoring stopped")
        
        try:
            socketio.emit('monitor_status', {'running': False})
        except:
            pass
        return {"success": True, "message": "Message monitoring stopped"}
    
    async def setup_message_monitoring(self):
        # Load accounts from database
        db = self.db_manager.get_session()
        try:
            accounts_dict = {acc.phone: acc.to_dict() for acc in db.query(Account).all()}
        finally:
            db.close()

        for phone, client in self.clients.items():
            try:
                if phone in accounts_dict:
                    account = accounts_dict[phone]
                    await self.setup_single_monitor_handler(account, client)
            except Exception as e:
                self.log_message(f"Monitor handler setup error: {str(e)}", phone)
    
    async def setup_single_monitor_handler(self, account, client):
        try:
            source_entity = await self.get_entity_safe(client, account['source_channel'], account['phone'])
            phone = account['phone']
            channel = account['source_channel']
            
            @client.on(events.NewMessage(chats=source_entity))
            async def monitor_handler(event):
                if not self.message_monitoring:
                    return
                
                try:
                    message_id = event.message.id
                    self.monitor_message(f"New Message ID: {message_id}", phone, channel)
                except Exception as e:
                    self.monitor_message(f"Monitor error: {str(e)}", phone, channel)
            
            @client.on(events.MessageEdited(chats=source_entity))
            async def edited_monitor_handler(event):
                if not self.message_monitoring:
                    return
                
                try:
                    message_id = event.message.id
                    self.monitor_message(f"Edited Message ID: {message_id}", phone, channel)
                except Exception as e:
                    self.monitor_message(f"Edited monitor error: {str(e)}", phone, channel)
            
            self.monitor_message(f"Monitor handler setup for {channel}", phone)
            self.log_message(f"Monitor handler setup: {account['source_channel']}", phone)
            
        except Exception as e:
            self.monitor_message(f"Monitor handler setup error: {str(e)}", account['phone'])
            self.log_message(f"Monitor handler setup error: {str(e)}", account['phone'])
    
    async def clear_monitoring_handlers(self):
        for phone, client in self.clients.items():
            try:
                client.list_event_handlers().clear()
                self.log_message("Monitor handlers cleared", phone)
            except Exception as e:
                self.log_message(f"Monitor handler clearing error: {str(e)}", phone)
    
    def add_scheduled_post(self, post_input, target_datetime, selected_channels):
        try:
            int(post_input)
        except ValueError:
            return {"success": False, "error": "Message ID must be a number!"}

        if not selected_channels:
            return {"success": False, "error": "Select at least one channel!"}

        utc_plus_2 = timezone(timedelta(hours=2))
        current_time = datetime.now(utc_plus_2)
        time_diff = (target_datetime - current_time).total_seconds()

        if time_diff < -60:
            return {"success": False, "error": f"Time must be in the future!"}

        # Save to database
        db = self.db_manager.get_session()
        try:
            new_post = ScheduledPost(
                post=post_input,
                target_datetime=target_datetime,
                channels=selected_channels,
                status='Pending'
            )
            db.add(new_post)
            db.commit()

            # Convert to local time for logging
            display_time = target_datetime.astimezone(utc_plus_2) if target_datetime.tzinfo else target_datetime
            total_channels = sum(len(channels) for channels in selected_channels.values())
            self.log_message(f"New post scheduled: Message ID {post_input} for {display_time.strftime('%d.%m.%Y %H:%M')} - {total_channels} channels")

            if not self.scheduler_running and self.loop and self.clients:
                self.log_message("Auto-starting scheduler for new post")
                self.scheduler_running = True
                asyncio.run_coroutine_threadsafe(self.run_scheduler(), self.loop)
                try:
                    socketio.emit('scheduler_status', {'running': True})
                except:
                    pass

            try:
                socketio.emit('scheduled_posts_updated', self.get_scheduled_posts_data())
            except:
                pass

            return {"success": True, "message": f"Post scheduled! Time: {display_time.strftime('%d.%m.%Y %H:%M')}, Channels: {total_channels}"}

        except Exception as e:
            db.rollback()
            self.logger.error(f"Error adding scheduled post: {e}")
            return {"success": False, "error": f"Database error: {str(e)}"}
        finally:
            db.close()
    
    def get_scheduled_posts_data(self):
        db = self.db_manager.get_session()
        try:
            posts = db.query(ScheduledPost).all()
            posts_data = []
            utc_plus_2 = timezone(timedelta(hours=2))

            for post in posts:
                total_channels = sum(len(channels) for channels in post.channels.values())
                accounts_info = f"{len(post.channels)} accounts, {total_channels} channels"

                # Convert from UTC to UTC+2 for display
                display_time = post.target_datetime
                if display_time.tzinfo is None:
                    # If naive datetime, assume it's UTC
                    display_time = display_time.replace(tzinfo=timezone.utc)
                # Convert to UTC+2
                display_time = display_time.astimezone(utc_plus_2)

                posts_data.append({
                    'id': post.id,
                    'time': display_time.strftime('%d.%m.%Y %H:%M'),
                    'post': post.post,
                    'accounts': accounts_info,
                    'status': post.status
                })

            return posts_data

        except Exception as e:
            self.logger.error(f"Error getting scheduled posts: {e}")
            return []
        finally:
            db.close()
    
    def remove_scheduled_post(self, post_id):
        db = self.db_manager.get_session()
        try:
            post = db.query(ScheduledPost).filter_by(id=post_id).first()
            if post:
                message_id = post.post
                status = post.status
                db.delete(post)
                db.commit()
                self.log_message(f"Scheduled post removed: ID {post_id} (Message ID: {message_id}, Status: {status})")
            else:
                self.log_message(f"Scheduled post not found: ID {post_id}")

            try:
                socketio.emit('scheduled_posts_updated', self.get_scheduled_posts_data())
            except:
                pass

            return {"success": True, "message": "Scheduled post removed"}

        except Exception as e:
            db.rollback()
            self.logger.error(f"Error removing scheduled post: {e}")
            return {"success": False, "error": f"Database error: {str(e)}"}
        finally:
            db.close()
    
    def start_scheduler(self):
        # Check if there are scheduled posts in database
        db = self.db_manager.get_session()
        try:
            posts_count = db.query(ScheduledPost).count()
        finally:
            db.close()

        if posts_count == 0:
            return {"success": False, "error": "No scheduled posts available!"}
        
        if not self.loop:
            return {"success": False, "error": "Async loop not available!"}
        
        if not self.clients:
            return {"success": False, "error": "No accounts connected!"}
        
        self.scheduler_running = True

        asyncio.run_coroutine_threadsafe(self.run_scheduler(), self.loop)

        # Count pending posts from database
        db = self.db_manager.get_session()
        try:
            pending_count = db.query(ScheduledPost).filter_by(status='Pending').count()
        finally:
            db.close()

        self.log_message(f"Scheduler started - {pending_count} pending posts")

        try:
            socketio.emit('scheduler_status', {'running': True})
        except:
            pass
        return {"success": True, "message": f"Scheduler started - {pending_count} pending posts"}
    
    async def run_scheduler(self):
        utc_plus_2 = timezone(timedelta(hours=2))
        self.log_message("Scheduler started - checking every 5 seconds for pending posts")

        while self.scheduler_running:
            try:
                current_time = datetime.now(utc_plus_2)

                # Load pending posts from database
                db = self.db_manager.get_session()
                try:
                    pending_posts = db.query(ScheduledPost).filter_by(status='Pending').all()
                    posts_to_send = []

                    for post in pending_posts:
                        post_time = post.target_datetime

                        # Convert database time (UTC) to local time (UTC+2) for comparison
                        if not hasattr(post_time, 'tzinfo') or post_time.tzinfo is None:
                            # If naive, assume it's UTC
                            post_time = post_time.replace(tzinfo=timezone.utc)
                        # Convert to UTC+2 for comparison
                        post_time = post_time.astimezone(utc_plus_2)

                        time_diff = (post_time - current_time).total_seconds()

                        if time_diff <= 0:
                            posts_to_send.append(post.to_dict())
                            self.log_message(f"Post {post.id} ready to send! (scheduled for {post_time.strftime('%H:%M:%S')})")
                finally:
                    db.close()

                if posts_to_send:
                    self.log_message(f"Found {len(posts_to_send)} posts ready to send")
                    posts_to_send.sort(key=lambda x: x['datetime'])

                    for post in posts_to_send:
                        if self.scheduler_running:
                            self.log_message(f"Sending post {post['id']} (Message ID: {post['post']})")
                            await self.send_scheduled_post(post)
                            if len(posts_to_send) > 1:
                                await asyncio.sleep(30)

                # Sleep for 5 seconds before next check
                await asyncio.sleep(5)
                
            except Exception as e:
                self.log_message(f"Scheduler error: {str(e)}")
                await asyncio.sleep(10)
        
        self.log_message("Scheduler stopped")
        try:
            socketio.emit('scheduler_status', {'running': False})
        except:
            pass
    
    async def send_scheduled_post(self, post):
        post_id = post['id']
        try:
            # Double-check: Verify post still exists in database before sending
            db = self.db_manager.get_session()
            try:
                post_obj = db.query(ScheduledPost).filter_by(id=post_id).first()
                if not post_obj:
                    # Post was deleted by user - skip sending
                    self.log_message(f"Post {post_id} was removed by user - skipping send")
                    return

                if post_obj.status != 'Pending':
                    # Post already processed or cancelled - skip
                    self.log_message(f"Post {post_id} status is '{post_obj.status}' - skipping send")
                    return

                # Update status to Sending
                post_obj.status = 'Sending'
                db.commit()
            finally:
                db.close()

            try:
                socketio.emit('scheduled_posts_updated', self.get_scheduled_posts_data())
            except:
                pass

            self.log_message(f"Starting to send scheduled post: Message ID {post['post']}")

            success_count = 0
            total_count = 0
            failed_details = []  # Track which channels failed and why

            for phone, channels in post['channels'].items():
                if phone not in self.clients:
                    self.log_message(f"⚠ Account not connected: {phone} - skipping {len(channels)} channels")
                    for channel in channels:
                        total_count += 1
                        failed_details.append(f"Channel {channel}: Account {phone} not connected")
                    continue

                client = self.clients[phone]
                self.log_message(f"Using account {phone} for {len(channels)} channels")

                for channel in channels:
                    total_count += 1
                    retry_count = 0
                    max_retries = 3
                    sent = False

                    while retry_count < max_retries and not sent:
                        try:
                            delay = random.uniform(self.min_delay, self.max_delay)
                            if retry_count == 0:
                                self.log_message(f"[{total_count}] Sending to channel {channel} (delay {delay:.1f}s)")
                            else:
                                self.log_message(f"[{total_count}] Retry #{retry_count} for channel {channel}")

                            await asyncio.sleep(delay)

                            await self.send_single_scheduled_post(client, post['post'], channel, phone)
                            success_count += 1
                            sent = True
                            self.log_message(f"✓ [{total_count}] Successfully sent to channel {channel} via {phone}")

                        except FloodWaitError as e:
                            retry_count += 1
                            if retry_count < max_retries:
                                self.log_message(f"⚠ Flood wait for channel {channel}: {e.seconds}s - will retry after waiting")
                                await asyncio.sleep(e.seconds + 2)
                            else:
                                failed_details.append(f"Channel {channel} ({phone}): FloodWait {e.seconds}s - max retries exceeded")
                                self.log_message(f"✗ Failed to send to channel {channel}: FloodWait - max retries")

                        except ChannelPrivateError:
                            failed_details.append(f"Channel {channel} ({phone}): Channel is private or not accessible")
                            self.log_message(f"✗ Failed to send to channel {channel}: Channel is private/not accessible")
                            break  # No point retrying

                        except UserBannedInChannelError:
                            failed_details.append(f"Channel {channel} ({phone}): User is banned in channel")
                            self.log_message(f"✗ Failed to send to channel {channel}: User banned")
                            break  # No point retrying

                        except ValueError as e:
                            # Message not found or account not found
                            failed_details.append(f"Channel {channel} ({phone}): {str(e)}")
                            self.log_message(f"✗ Failed to send to channel {channel}: {str(e)}")
                            break  # No point retrying

                        except Exception as e:
                            retry_count += 1
                            if retry_count < max_retries:
                                self.log_message(f"⚠ Error sending to channel {channel}: {type(e).__name__}: {str(e)} - retrying...")
                                await asyncio.sleep(5)
                            else:
                                failed_details.append(f"Channel {channel} ({phone}): {type(e).__name__}: {str(e)}")
                                self.log_message(f"✗ Failed to send to channel {channel} after {max_retries} attempts: {str(e)}")

            # Update final status in database
            db = self.db_manager.get_session()
            try:
                post_obj = db.query(ScheduledPost).filter_by(id=post_id).first()
                if post_obj:
                    if success_count == total_count and total_count > 0:
                        post_obj.status = 'Sent'
                        self.log_message(f"✓ Post {post_id} FULLY SENT: {success_count}/{total_count} successful")
                    elif success_count > 0:
                        post_obj.status = f'Partial ({success_count}/{total_count})'
                        self.log_message(f"⚠ Post {post_id} PARTIAL SEND: {success_count}/{total_count} successful")
                        # Log failed channels details
                        if failed_details:
                            self.log_message(f"Failed channels ({len(failed_details)}):")
                            for detail in failed_details:
                                self.log_message(f"  - {detail}")
                    else:
                        post_obj.status = 'Error'
                        self.log_message(f"✗ Post {post_id} FAILED: 0/{total_count} successful")
                        # Log all failed channels
                        if failed_details:
                            self.log_message(f"All channels failed ({len(failed_details)}):")
                            for detail in failed_details:
                                self.log_message(f"  - {detail}")
                    db.commit()
            finally:
                db.close()

            try:
                socketio.emit('scheduled_posts_updated', self.get_scheduled_posts_data())
            except:
                pass

        except Exception as e:
            # Update status to Error in database
            db = self.db_manager.get_session()
            try:
                post_obj = db.query(ScheduledPost).filter_by(id=post_id).first()
                if post_obj:
                    post_obj.status = 'Error'
                    db.commit()
            finally:
                db.close()

            self.log_message(f"Scheduled post general error: {str(e)}")
            try:
                socketio.emit('scheduled_posts_updated', self.get_scheduled_posts_data())
            except:
                pass
    
    async def send_single_scheduled_post(self, client, post_input, target_channel, phone):
        try:
            # Load account from database
            db = self.db_manager.get_session()
            try:
                account_obj = db.query(Account).filter_by(phone=phone).first()
                if not account_obj:
                    raise ValueError(f"Account not found in database: {phone}")
                account = account_obj.to_dict()
            finally:
                db.close()

            source_channel_id = int(account['source_channel'])
            message_id = int(post_input)
            target_channel_id = int(target_channel)

            # Load entities (source and target)
            try:
                source_entity = await self.get_entity_safe(client, source_channel_id, phone)
            except Exception as e:
                raise ValueError(f"Cannot access source channel {source_channel_id}: {type(e).__name__}: {str(e)}")

            try:
                target_entity = await self.get_entity_safe(client, target_channel_id, phone)
            except Exception as e:
                raise ValueError(f"Cannot access target channel {target_channel_id}: {type(e).__name__}: {str(e)}")

            # Get message from source
            try:
                message = await client.get_messages(source_entity, ids=message_id)
                if not message:
                    raise ValueError(f"Message {message_id} not found in source channel {source_channel_id}")
            except Exception as e:
                if "not found" in str(e).lower():
                    raise ValueError(f"Message {message_id} not found in source channel {source_channel_id}")
                raise

            # Forward message to target
            await client.forward_messages(
                target_entity,
                message,
                from_peer=source_entity,
                drop_author=True,
                silent=True
            )

        except FloodWaitError as e:
            # Telegram rate limiting - will be retried in parent function
            raise
        except ChannelPrivateError as e:
            # Channel is private or not accessible - cannot retry
            raise
        except UserBannedInChannelError as e:
            # User is banned - cannot retry
            raise
        except ValueError as e:
            # Our custom errors (entity not found, message not found, etc)
            raise
        except Exception as e:
            # Unexpected errors - log with details
            error_type = type(e).__name__
            raise Exception(f"{error_type}: {str(e)}")
    
    def disconnect_all(self):
        self.running = False
        self.message_monitoring = False
        self.scheduler_running = False
        self.connection_in_progress = False
        self.connection_paused = False
        
        if self.loop:
            asyncio.run_coroutine_threadsafe(self.async_disconnect_all(), self.loop)
        
        self.log_message("Disconnecting all connections...")
        
        try:
            socketio.emit('monitor_status', {'running': False})
            socketio.emit('scheduler_status', {'running': False})
        except:
            pass
        
        return {"success": True, "message": "Disconnecting all accounts..."}
    
    async def async_disconnect_all(self):
        disconnect_tasks = []
        
        for phone, client in list(self.clients.items()):
            try:
                task = asyncio.create_task(self.safe_disconnect_client(client, phone))
                disconnect_tasks.append(task)
            except Exception as e:
                self.log_message(f"Error creating disconnect task: {str(e)}", phone)
        
        if disconnect_tasks:
            await asyncio.gather(*disconnect_tasks, return_exceptions=True)
        
        self.clients.clear()
        self.pending_auth.clear()
        self.entity_cache.clear()
        
        try:
            socketio.emit('accounts_updated', self.get_accounts_data())
        except:
            pass
        self.log_message("All connections disconnected")
    
    async def safe_disconnect_client(self, client, phone):
        try:
            await asyncio.wait_for(client.disconnect(), timeout=5.0)
            self.log_message("Connection disconnected", phone)
        except asyncio.TimeoutError:
            self.log_message("Disconnect timeout - forcing close", phone)
        except Exception as e:
            self.log_message(f"Disconnect error: {str(e)}", phone)

auth_manager = AuthManager()
forwarder = WebTelegramForwarder()

def login_required(f):
    def decorated_function(*args, **kwargs):
        if 'authenticated' not in session or not session['authenticated']:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        print(f"Login attempt: username='{username}', password='{password}'")
        
        if not username or not password:
            print("Empty username or password")
            return render_template('login.html', error='Please fill in all fields')
        
        if auth_manager.verify_credentials(username, password):
            print("Login successful")
            session['authenticated'] = True
            session['username'] = username
            session.permanent = True
            return redirect(url_for('index'))
        else:
            print("Invalid credentials")
            return render_template('login.html', error='Invalid username or password')
    
    if 'authenticated' in session and session['authenticated']:
        return redirect(url_for('index'))
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/api/server-time')
@login_required
def get_server_time():
    utc_plus_2 = timezone(timedelta(hours=2))
    current_time = datetime.now(utc_plus_2)
    return jsonify({
        'time': current_time.strftime('%H:%M:%S'),
        'date': current_time.strftime('%Y-%m-%d'),
        'formatted_date': current_time.strftime('%d/%m/%Y'),
        'timestamp': current_time.timestamp(),
        'iso': current_time.isoformat()
    })

@app.route('/api/accounts', methods=['GET'])
@login_required
def get_accounts():
    return jsonify(forwarder.get_accounts_data())

@app.route('/api/accounts', methods=['POST'])
@login_required
def add_account():
    data = request.json
    result = forwarder.add_account(
        data['api_id'],
        data['api_hash'],
        data['phone'],
        data['source_channel'],
        data['target_channels'],
        data.get('name')
    )
    return jsonify(result)

@app.route('/api/accounts/<phone>', methods=['DELETE'])
@login_required
def remove_account(phone):
    result = forwarder.remove_account(phone)
    return jsonify(result)

@app.route('/api/channels/remove', methods=['POST'])
@login_required
def remove_channels():
    data = request.json
    selected_channels = data.get('channels', {})
    result = forwarder.remove_selected_channels(selected_channels)
    return jsonify(result)

@app.route('/api/connect', methods=['POST'])
@login_required
def connect_accounts():
    result = forwarder.connect_all_accounts()
    return jsonify(result)

@app.route('/api/disconnect', methods=['POST'])
@login_required
def disconnect_accounts():
    result = forwarder.disconnect_all()
    return jsonify(result)

@app.route('/api/auth/status', methods=['GET'])
@login_required
def get_auth_status():
    return jsonify(forwarder.get_auth_status())

@app.route('/api/auth/code', methods=['POST'])
@login_required
def submit_auth_code():
    data = request.json
    result = forwarder.submit_auth_code(data['phone'], data['code'])
    return jsonify(result)

@app.route('/api/auth/password', methods=['POST'])
@login_required
def submit_auth_password():
    data = request.json
    result = forwarder.submit_auth_password(data['phone'], data['password'])
    return jsonify(result)

@app.route('/api/monitor/start', methods=['POST'])
@login_required
def start_monitor():
    result = forwarder.start_message_monitoring()
    return jsonify(result)

@app.route('/api/monitor/stop', methods=['POST'])
@login_required
def stop_monitor():
    result = forwarder.stop_message_monitoring()
    return jsonify(result)

@app.route('/api/scheduled', methods=['GET'])
@login_required
def get_scheduled_posts():
    return jsonify(forwarder.get_scheduled_posts_data())

@app.route('/api/scheduled', methods=['POST'])
@login_required
def add_scheduled_post():
    data = request.json

    try:
        utc_plus_2 = timezone(timedelta(hours=2))
        target_datetime = datetime.strptime(data['datetime'], '%Y-%m-%dT%H:%M')
        target_datetime = target_datetime.replace(tzinfo=utc_plus_2)
    except:
        return jsonify({"success": False, "error": "Invalid datetime format"})
    
    result = forwarder.add_scheduled_post(
        data['post'],
        target_datetime,
        data['channels']
    )
    return jsonify(result)

@app.route('/api/scheduled/<int:post_id>', methods=['DELETE'])
@login_required
def remove_scheduled_post(post_id):
    result = forwarder.remove_scheduled_post(post_id)
    return jsonify(result)

@app.route('/api/scheduler/start', methods=['POST'])
@login_required
def start_scheduler():
    result = forwarder.start_scheduler()
    return jsonify(result)

@app.route('/api/logs/clear', methods=['POST'])
@login_required
def clear_logs():
    forwarder.clear_log_history()
    return jsonify({"success": True, "message": "Logs cleared"})

@app.route('/api/monitor/clear', methods=['POST'])
@login_required
def clear_monitor():
    forwarder.clear_monitor_history()
    return jsonify({"success": True, "message": "Monitor cleared"})

@app.route('/api/logs/history', methods=['GET'])
@login_required
def get_log_history():
    return jsonify({"history": forwarder.get_log_history()})

@app.route('/api/monitor/history', methods=['GET'])
@login_required
def get_monitor_history():
    return jsonify({"history": forwarder.get_monitor_history()})

@app.route('/health')
def health():
    return {"status": "healthy", "accounts": len(forwarder.accounts), "connected": len(forwarder.clients)}

@socketio.on('connect')
def handle_connect():
    if 'authenticated' not in session or not session['authenticated']:
        return False
    
    try:
        emit('accounts_updated', forwarder.get_accounts_data())
        emit('scheduled_posts_updated', forwarder.get_scheduled_posts_data())
        emit('monitor_status', {'running': forwarder.message_monitoring})
        emit('scheduler_status', {'running': forwarder.scheduler_running})
        
        emit('log_history', {'history': forwarder.get_log_history()})
        emit('monitor_history', {'history': forwarder.get_monitor_history()})
        
        print(f"Client connected: {request.sid}")
    except Exception as e:
        print(f"Error in socket connect: {str(e)}")

@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    
    print("="*50)
    print("STARTING TELEGRAM FORWARDER WITH AUTH")
    print("="*50)
    
    print(f"Current working directory: {os.getcwd()}")
    print(f"Files in directory: {os.listdir('.')}")
    
    auth_test = AuthManager()
    test_creds = auth_test.load_credentials()
    if test_creds:
        print(f"✓ Credentials loaded successfully")
        print(f"  Username: {test_creds['login']}")
        print(f"  Password: {test_creds['password']}")
    else:
        print("✗ Failed to load credentials")
    
    print("="*50)
    
    socketio.run(
        app, 
        host='0.0.0.0', 
        port=port, 
        debug=True,
        allow_unsafe_werkzeug=True,
        logger=False,
        engineio_logger=False
    )
