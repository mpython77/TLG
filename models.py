from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, JSON, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

Base = declarative_base()

class Account(Base):
    __tablename__ = 'accounts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    api_id = Column(String(255), nullable=False)
    api_hash = Column(String(255), nullable=False)
    phone = Column(String(50), unique=True, nullable=False)
    source_channel = Column(String(255), nullable=False)
    target_channels = Column(JSON, nullable=False)  # Store as JSON array
    status = Column(String(50), default='Added')
    session_file = Column(String(255), nullable=False)
    session_string = Column(Text, nullable=True)  # Store session data for persistence
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'api_id': self.api_id,
            'api_hash': self.api_hash,
            'phone': self.phone,
            'source_channel': self.source_channel,
            'target_channels': self.target_channels,
            'status': self.status,
            'session_file': self.session_file,
            'session_string': self.session_string
        }


class ScheduledPost(Base):
    __tablename__ = 'scheduled_posts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    post = Column(String(255), nullable=False)  # Message ID
    target_datetime = Column(DateTime, nullable=False)
    channels = Column(JSON, nullable=False)  # Store as JSON: {phone: [channels]}
    status = Column(String(50), default='Pending')
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'post': self.post,
            'datetime': self.target_datetime,
            'channels': self.channels,
            'status': self.status,
            'created': self.created_at
        }


class DatabaseManager:
    def __init__(self, database_url=None):
        if database_url is None:
            # Get from environment variable, fallback to SQLite for local testing
            database_url = os.environ.get('DATABASE_URL')
            if database_url and database_url.startswith('postgres://'):
                # Fix for Heroku/Railway - they use postgres:// but SQLAlchemy needs postgresql://
                database_url = database_url.replace('postgres://', 'postgresql://', 1)
            elif not database_url:
                # Fallback to SQLite for local development
                database_url = 'sqlite:///telegram_forwarder.db'

        self.engine = create_engine(database_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def get_session(self):
        return self.Session()
