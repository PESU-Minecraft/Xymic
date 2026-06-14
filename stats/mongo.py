from pymongo import MongoClient
import os
import logging

logger = logging.getLogger(__name__)

MONGO_URI = os.getenv("STATS_MONGO_URI")
MONGO_DB = os.getenv("STATS_DB_NAME")

# Initialize variables to None by default so main.py imports don't fail (cuz rn, mc shit not ready)
client = None
db = None
server_metrics = None
players = None
duels_db = None

mc_ready = os.getenv("MC_READY", "false").lower() == "true"

if mc_ready:
    try:
        client = MongoClient(MONGO_URI)
        db = client[MONGO_DB]
        server_metrics = db.server_metrics
        players = db.players
        duels_db = db.duels
    except Exception as e:
        logger.warning(f"Could not connect to stats MongoDB: {e}")
