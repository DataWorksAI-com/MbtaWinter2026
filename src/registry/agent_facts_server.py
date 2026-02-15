from flask import Flask, request, jsonify
from pymongo import MongoClient
import os

app = Flask(__name__)

ATLAS_URL = os.getenv("ATLAS_URL") or os.getenv("MONGO_URI")
client = MongoClient(ATLAS_URL)
db = client.nanda_private_registry
facts = db.agent_facts

try:
    facts.create_index("agent_name", unique=True)
except Exception:
    pass


@app.post("/api/agent-facts")
def create_agent_facts():
    agent_facts = request.json
    try:
        result = facts.insert_one(agent_facts)
        return jsonify({"status": "success", "id": str(result.inserted_id)})
    except Exception as e:
        if "duplicate" in str(e):
            agent_name = agent_facts.get("agent_name")
            facts.update_one({"agent_name": agent_name}, {"$set": agent_facts})
            return jsonify({"status": "success", "message": "updated"})
        return jsonify({"error": str(e)}), 500


@app.get("/@<username>.json")
def get_agent_facts(username):
    fact = facts.find_one({"agent_name": username}, {"_id": 0})
    if not fact:
        return jsonify({"error": "Not found"}), 404
    return jsonify(fact)


@app.get("/list")
def list_agent_facts():
    all_facts = list(facts.find({}, {"_id": 0}))
    return jsonify({"agent_facts": all_facts, "count": len(all_facts)})


@app.get("/health")
def health_check():
    try:
        client.admin.command('ping')
        return jsonify({"status": "healthy", "mongodb": "connected"})
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)})


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8000))
    app.run(host="0.0.0.0", port=port)
