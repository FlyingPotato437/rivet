from pathlib import Path
from dotenv import load_dotenv
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "apps/web/.env.local")
load_dotenv(ROOT / "apps/web/.env")
load_dotenv(ROOT / ".env")
DATABASE_URL = os.getenv(
    "RIVET_DATABASE_URL", "postgresql+psycopg://localhost:55432/rivet"
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
DATA = ROOT / ".data"
BLOBS = DATA / "blobs"
BLOBS.mkdir(parents=True, exist_ok=True)
ORG = "9f0e7bbc-7fd1-43ca-b552-dae668fc82e1"
ACTOR = "Local estimator"
