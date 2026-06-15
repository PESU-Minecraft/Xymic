from flask import Flask
import logging
import os

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(name)s: %(message)s'
)

@app.route("/")
def home():
    return {
        "status": "online",
        "commit": os.getenv("RENDER_GIT_COMMIT"),
        "branch": os.getenv("RENDER_GIT_BRANCH"),
    }

@app.route("/health")
def health():
    return {"status": "healthy"}, 200
