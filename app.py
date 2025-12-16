import os
import requests
from flask import Flask, render_template, request, jsonify, session
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_default")

# Configuration
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
BOT_USERNAME = os.getenv("BOT_USERNAME")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")

USER_AGENT = "DepictsEditor/1.0 (https://github.com/example/depicts-editor; tool_maintainer@example.com)"

def get_commons_session():
    """Authenticates and returns a session with a CSRF token."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})

    # 1. Login
    if BOT_USERNAME and BOT_PASSWORD:
        login_token = s.get(COMMONS_API, params={
            "action": "query",
            "meta": "tokens",
            "type": "login",
            "format": "json"
        }).json()["query"]["tokens"]["logintoken"]
        
        login_req = s.post(COMMONS_API, data={
            "action": "login",
            "lgname": BOT_USERNAME,
            "lgpassword": BOT_PASSWORD,
            "lgtoken": login_token,
            "format": "json"
        })
        
        # Check if login was successful
        # (For this prototype, we'll proceed, but in prod we should check result)

    return s

def get_csrf_token(s):
    """Gets a CSRF token for edit actions."""
    tokens = s.get(COMMONS_API, params={
        "action": "query",
        "meta": "tokens",
        "type": "csrf",
        "format": "json"
    }).json()
    return tokens["query"]["tokens"]["csrftoken"]

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/search")
def search():
    """
    1. Search Wikidata for the QID of the query.
    2. Search Commons for images with that QID in P180.
    """
    query = request.args.get("q")
    if not query:
        return jsonify({"error": "No query provided"}), 400

    # 1. Search Wikidata for QID
    wd_params = {
        "action": "wbsearchentities",
        "search": query,
        "language": "en",
        "format": "json",
        "limit": 1
    }
    wd_resp = requests.get(WIKIDATA_API, params=wd_params, headers={"User-Agent": USER_AGENT}).json()
    
    if not wd_resp.get("search"):
        return jsonify({"results": [], "found_entity": None})
        
    entity = wd_resp["search"][0]
    qid = entity["id"]
    label = entity.get("label", query)
    description = entity.get("description", "")

    # 2. Search Commons
    # We use generator=search with haswbstatement
    # Note: 'haswbstatement' search usage on Commons: "haswbstatement:P180=Q146"
    commons_query = f"haswbstatement:P180={qid}"
    
    commons_params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": commons_query,
        "gsrnamespace": 6, # File namespace
        "gsrlimit": 20,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "format": "json"
    }
    
    c_resp = requests.get(COMMONS_API, params=commons_params, headers={"User-Agent": USER_AGENT}).json()
    
    files = []
    if "query" in c_resp and "pages" in c_resp["query"]:
        for page_id, page_data in c_resp["query"]["pages"].items():
            if "imageinfo" in page_data:
                info = page_data["imageinfo"][0]
                files.append({
                    "pageid": page_id,
                    "title": page_data["title"],
                    "url": info["url"],
                    "thumb_url": info.get("thumburl", info["url"]), # Fallback if no thumb (should request iiurlwidth for real thumbs)
                    "description": info["extmetadata"].get("ImageDescription", {}).get("value", "No description")
                })
                
    return jsonify({
        "results": files,
        "found_entity": {
            "id": qid,
            "label": label,
            "description": description
        }
    })

@app.route("/api/file/<pageid>")
def file_details(pageid):
    """
    Get file details and SDC (Structured Data on Commons)
    """
    # Get M-ID (MediaInfo ID). Usually M + pageid for Commons files.
    # But let's fetch it properly via wbgetentities? 
    # Actually, simpler to look up by pageid directly on Commons API to get the M-ID concept or just use the pageid to find the entity.
    # SDC entities are conceptualized as 'M' + page_id.
    
    mid = f"M{pageid}"
    
    params = {
        "action": "wbgetentities",
        "ids": mid,
        "format": "json"
    }
    
    resp = requests.get(COMMONS_API, params=params, headers={"User-Agent": USER_AGENT}).json()
    
    if "entities" not in resp or mid not in resp["entities"]:
         return jsonify({"error": "Entity not found"}), 404
         
    entity = resp["entities"][mid]
    statements = entity.get("statements", {})
    
    depicts = []
    if "P180" in statements:
        p180_claims = statements["P180"]
        
        # Get all QIDs
        qids = []
        for claim in p180_claims:
            main_snak = claim.get("mainsnak", {})
            if main_snak.get("snaktype") == "value":
                datavalue = main_snak.get("datavalue", {})
                if datavalue.get("type") == "wikibase-entityid":
                    qids.append(datavalue["value"]["id"])
            
        # Bulk resolve QID labels from Wikidata
        if qids:
            chunk_size = 50
            labels = {}
            for i in range(0, len(qids), chunk_size):
                chunk = qids[i:i+chunk_size]
                wd_params = {
                    "action": "wbgetentities",
                    "ids": "|".join(chunk),
                    "props": "labels",
                    "languages": "en",
                    "format": "json"
                }
                wd_resp = requests.get(WIKIDATA_API, params=wd_params, headers={"User-Agent": USER_AGENT}).json()
                if "entities" in wd_resp:
                     for qid, q_data in wd_resp["entities"].items():
                         labels[qid] = q_data.get("labels", {}).get("en", {}).get("value", qid)
            
            for qid in qids:
                depicts.append({
                    "id": qid,
                    "label": labels.get(qid, qid)
                })

    return jsonify({
        "mid": mid,
        "depicts": depicts
    })

@app.route("/api/wikidata_search")
def wikidata_search():
    query = request.args.get("q")
    if not query:
        return jsonify([])
        
    wd_params = {
        "action": "wbsearchentities",
        "search": query,
        "language": "en",
        "format": "json",
        "limit": 10
    }
    resp = requests.get(WIKIDATA_API, params=wd_params, headers={"User-Agent": USER_AGENT}).json()
    results = []
    if "search" in resp:
        for item in resp["search"]:
            results.append({
                "id": item["id"],
                "label": item.get("label", item["id"]),
                "description": item.get("description", "")
            })
    return jsonify(results)

@app.route("/api/add_claim", methods=["POST"])
def add_claim():
    """
    Adds a P180 statement to a MediaInfo entity.
    """
    data = request.json
    mid = data.get("mid")
    qid = data.get("qid")
    
    if not mid or not qid:
        return jsonify({"error": "Missing info"}), 400
        
    if not BOT_USERNAME or not BOT_PASSWORD:
        return jsonify({"error": "Server not configured with bot credentials"}), 500

    try:
        session = get_commons_session()
        csrf_token = get_csrf_token(session)
        
        # Determine value snak
        value = {
            "entity-type": "item",
            "numeric-id": int(qid.replace("Q", ""))
        }
        
        # https://www.mediawiki.org/wiki/Wikibase/API#wbcreateclaim
        params = {
            "action": "wbcreateclaim",
            "entity": mid,
            "property": "P180",
            "snaktype": "value",
            "value": str(jsonify(value).data, 'utf-8') if isinstance(value, str) else str(value).replace("'", '"'), # Tricky part, requests handles dicts usually but wb APIs can be finicky with JSON encoding of values
            "bot": 1,
            "token": csrf_token,
            "format": "json"
        }
        
        # Actually requests handles json value encoding if passed as string
        # Correct format for 'value' in wbcreateclaim is a JSON string stringifying the datavalue content object?
        # No, for 'value' parameter in `wbcreateclaim`:
        # "The value to set the snak to. JSON encoding of the value as expected by the datatype ... For 'wikibase-item': '{"entity-type":"item","numeric-id":1}'"
        
        import json
        json_value = json.dumps(value)
        
        post_data = {
            "action": "wbcreateclaim",
            "entity": mid,
            "property": "P180",
            "snaktype": "value",
            "value": json_value,
            "bot": 1,
            "token": csrf_token,
            "format": "json"
        }
        
        api_resp = session.post(COMMONS_API, data=post_data)
        resp_json = api_resp.json()
        
        if "error" in resp_json:
            return jsonify({"error": resp_json["error"]["info"]}), 400
            
        return jsonify({"success": True, "data": resp_json})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
